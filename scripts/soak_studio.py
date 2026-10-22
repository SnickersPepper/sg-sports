#!/usr/bin/env python3
"""Offline, synthetic long-session soak for the Studio backend seams. No network, no providers, no devices.

  python3 scripts/soak_studio.py [--cycles N] [--out artifacts/phase9/soak-summary.json]

One cycle models a few minutes of Studio use on a fake clock: settings edits, catalog/ops reads, a playback lease with a second
(split-screen) owner refused and then handed over, lease expiry after a "crash", a DVR capture killed mid-packet and recovered.
Invariants are asserted every cycle and thresholds at the end; exit status is non-zero on any violation.
"""
import argparse
import json
import socket
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

def _deny(*args, **kwargs):
    raise OSError('Outbound network is disabled in the soak harness')
socket.socket.connect = _deny
socket.socket.connect_ex = _deny

import catalog, dvr, lease, ops, settings  # noqa: E402

PKT = b'\x47' + b'\x00' * 187
THRESHOLDS = {'p95_ms_max': 100.0, 'heap_growth_bytes_max': 8 << 20, 'limiter_keys_max': 1024, 'snapshots_max': settings.SNAPSHOT_LIMIT, 'ready_per_bucket_max': dvr.KEEP}


def run(cycles):
    violations = []
    clock = [1000.0]
    now = lambda: clock[0]
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        store = settings.SettingsStore(scratch / 'settings.sqlite')
        leases = lease.LeaseAuthority(scratch / 'leases.sqlite', clock=now)
        metrics, limiter = ops.Metrics(), ops.RateLimiter(clock=now)
        latencies, revision = [], store.revision()
        tracemalloc.start()
        baseline = None
        for n in range(cycles):
            began = time.perf_counter()
            view = store.update(store.revision(), [{'op': 'upsert', 'section': 'profiles', 'item': {'id': 'prof-soak', 'name': 'Soak', 'preferences': {'language': 'en' if n % 2 else 'es'}}}])
            if view['revision'] <= revision:
                violations.append('revision_not_monotonic@%d' % n)
            revision = view['revision']
            catalog.view()
            ops.verify_backup(ops.make_backup(store, now()))
            # playback: owner A holds the single-slot provider; split-screen owner B is refused; A releases and B takes over
            a = leases.acquire('acct-soak', 'playback:a%d' % n, 'playback', ttl=90)
            if a is None or leases.acquire('acct-soak', 'playback:b%d' % n, 'playback', ttl=90) is not None:
                violations.append('lease_budget_broken@%d' % n)
            leases.release(a)
            b = leases.acquire('acct-soak', 'playback:b%d' % n, 'playback', ttl=90)
            if b is None:
                violations.append('lease_handover_failed@%d' % n)
            clock[0] += 120   # b's owner "crashed": its lease must expire rather than leak
            if leases.holders():
                violations.append('lease_leaked@%d' % n)
            # DVR: a capture killed mid-packet, then a restart
            rid = 'g%d' % n
            dstore = dvr.Store(scratch / 'dvr', clock=now)
            dstore.write({'id': rid, 'bucket': 'tennessee-football', 'state': 'recording', 'event_start': n + 1, 'created_at': n + 1, 'bytes': 0, 'title': 't', 'espn_id': rid, 'playable': True})
            dstore.media_path(rid).write_bytes(PKT * 4 + b'zz')
            dstore = dvr.Store(scratch / 'dvr', clock=now)
            dstore.recover()
            ready = [m for m in dstore.bucket('tennessee-football') if m['state'] == 'ready']
            if len(ready) > dvr.KEEP:
                violations.append('retention_exceeded@%d' % n)
            for client in ('c%d' % (n % 5000),):
                limiter.spend('auth_fail', client)
            elapsed = time.perf_counter() - began
            latencies.append(elapsed)
            metrics.observe('/v1/settings', 200, elapsed)
            if n == min(20, cycles - 1):
                baseline = tracemalloc.get_traced_memory()[0]
        growth = tracemalloc.get_traced_memory()[0] - (baseline or 0)
        tracemalloc.stop()
        ordered = sorted(latencies)
        p95 = ordered[max(0, int(len(ordered) * .95) - 1)] * 1000
        results = {'cycles': cycles, 'p95_ms': round(p95, 2), 'heap_growth_bytes': growth, 'settings_revision': revision,
                   'snapshots': len(store.snapshots()), 'limiter_keys': limiter.snapshot()['tracked_keys'], 'ready_per_bucket': len(ready),
                   'metrics_samples': metrics.snapshot()['routes']['settings']['latency_ms']['samples']}
        for key, value in (('p95_ms', THRESHOLDS['p95_ms_max']), ('heap_growth_bytes', THRESHOLDS['heap_growth_bytes_max']), ('limiter_keys', THRESHOLDS['limiter_keys_max']),
                           ('snapshots', THRESHOLDS['snapshots_max']), ('ready_per_bucket', THRESHOLDS['ready_per_bucket_max'])):
            if results[key] > value:
                violations.append('%s=%s>%s' % (key, results[key], value))
    return {'ok': not violations, 'violations': violations[:20], 'results': results, 'thresholds': THRESHOLDS, 'synthetic': True, 'network': 'denied'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cycles', type=int, default=300)
    parser.add_argument('--out', default=str(root / 'artifacts/phase9/soak-summary.json'))
    args = parser.parse_args()
    summary = run(args.cycles)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
