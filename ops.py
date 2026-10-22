"""Phase 9 operational hardening: privacy-safe metrics, bounded rate control, storage monitoring and versioned backups.

Privacy is structural, not best-effort: metrics use a fixed route vocabulary (no ids, tokens or query strings ever become labels),
and every diagnostics payload passes through `sanitize`, which keeps only numbers, booleans and short lowercase identifier strings.
Anything else (URLs, credentials, free text) is replaced by a marker. Backups are built only from the settings export, which is
already secret-free, and are re-checked with the same URL/credential scan the settings store uses.
"""
import copy
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

import settings

RELEASE_CHANNELS = ('design', 'studio', 'livingroom', 'lab', 'unspecified')
BACKUP_FORMAT = 'sg-sports-backup'
BACKUP_VERSION = 1
SAFE_KEY = re.compile(r'^[a-z][a-z0-9_]{0,47}$')
SAFE_STR = re.compile(r'^[a-z0-9][a-z0-9_.:-]{0,63}$')
REDACTED = '[redacted]'

# Fixed vocabulary: first path segments that may appear as a metric label. Everything else is "other".
ROUTES = (('/health', 'health'), ('/screen-data', 'screen_data'), ('/v1/ops/', 'ops'), ('/v1/artwork', 'artwork'), ('/tv/artwork/', 'tv_artwork'), ('/v1/settings', 'settings'),
          ('/v1/tv/settings', 'tv_settings'), ('/v1/tv/catalog', 'tv_catalog'), ('/v1/tv/', 'tv'), ('/v1/catalog', 'catalog'),
          ('/v1/providers', 'providers'), ('/v1/teams', 'teams'))


def release_channel(environ=None):
    """The release channel this backend reports (SG_RELEASE_CHANNEL); anything unknown is labelled "unspecified"."""
    value = (environ if environ is not None else os.environ).get('SG_RELEASE_CHANNEL', '')
    return value if value in RELEASE_CHANNELS else 'unspecified'


def route_label(path):
    for prefix, label in ROUTES:
        if path == prefix or path.startswith(prefix):
            return label
    return 'other'


def sanitize(value, depth=0):
    """Reduce any structure to privacy-safe diagnostics: numbers, booleans, None and short identifier strings only."""
    if depth > 6:
        return REDACTED
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3) if math.isfinite(value) else None
    if isinstance(value, str):
        return value if SAFE_STR.match(value) and not settings.URLISH.search(value) else REDACTED
    if isinstance(value, dict):
        return {(k if isinstance(k, str) and SAFE_KEY.match(k) else REDACTED + str(i)): sanitize(v, depth + 1) for i, (k, v) in enumerate(value.items())}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in list(value)[:100]]
    return REDACTED


class Metrics:
    """Request counters and bounded latency windows, labelled by route vocabulary and status class only."""

    def __init__(self, window=256, clock=time.monotonic):
        self.window, self.clock, self.lock = window, clock, threading.Lock()
        self.started = clock()
        self.counts, self.latency = {}, {}

    def observe(self, path, status, seconds):
        label, klass = route_label(path), 'status_%dxx' % (int(status) // 100)
        with self.lock:
            self.counts[(label, klass)] = self.counts.get((label, klass), 0) + 1
            self.latency.setdefault(label, deque(maxlen=self.window)).append(max(0.0, float(seconds)))

    @staticmethod
    def _quantile(values, q):
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0

    def snapshot(self):
        with self.lock:
            counts = dict(self.counts)
            latency = {label: list(values) for label, values in self.latency.items()}
        routes = {}
        for (label, klass), n in counts.items():
            routes.setdefault(label, {'requests': 0, 'latency_ms': {}})['requests'] += n
            routes[label][klass] = n
        for label, values in latency.items():
            routes[label]['latency_ms'] = {'p50': round(self._quantile(values, .5) * 1000, 2), 'p95': round(self._quantile(values, .95) * 1000, 2),
                                           'max': round(max(values) * 1000, 2), 'samples': len(values)}
        return {'uptime_seconds': int(self.clock() - self.started), 'routes': routes}


class RateLimiter:
    """Token buckets with a hard cap on tracked keys (least recently used evicted), so memory is bounded under any key flood."""

    POLICIES = {'auth_fail': (10, 1 / 6.0), 'admin_write': (30, 1.0)}   # (burst, refill tokens per second)

    def __init__(self, max_keys=1024, clock=time.monotonic):
        self.max_keys, self.clock, self.lock = max_keys, clock, threading.Lock()
        self.buckets = OrderedDict()
        self.denied = {name: 0 for name in self.POLICIES}

    def _bucket(self, name, key):
        burst, rate = self.POLICIES[name]
        now = self.clock()
        tokens, last = self.buckets.pop((name, key), (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * rate)
        return tokens, now, rate

    def blocked(self, name, key):
        """True if the key has no token left (does not spend one)."""
        with self.lock:
            tokens, now, rate = self._bucket(name, key)
            self.buckets[(name, key)] = (tokens, now)
            self._trim()
            if tokens < 1.0:
                self.denied[name] += 1
                return int(math.ceil((1.0 - tokens) / rate))
            return 0

    def spend(self, name, key):
        """Spend one token; returns 0 if allowed, otherwise the seconds to wait."""
        with self.lock:
            tokens, now, rate = self._bucket(name, key)
            wait = 0 if tokens >= 1.0 else int(math.ceil((1.0 - tokens) / rate))
            if wait:
                self.denied[name] += 1
            else:
                tokens -= 1.0
            self.buckets[(name, key)] = (tokens, now)
            self._trim()
            return wait

    def _trim(self):
        while len(self.buckets) > self.max_keys:
            self.buckets.popitem(last=False)

    def snapshot(self):
        with self.lock:
            return {'tracked_keys': len(self.buckets), 'max_keys': self.max_keys, 'denied': dict(self.denied)}


def storage_report(data_dir, dvr_root=None, usage=shutil.disk_usage, low_fraction=0.10, critical_fraction=0.03):
    """Free space and DVR/data footprint. Unknown free space is reported as "unknown" (fail closed), never "ok"."""
    report = {'status': 'unknown', 'free_bytes': None, 'total_bytes': None, 'free_fraction': None}
    try:
        stat = usage(str(data_dir))
        free, total = int(stat.free), int(stat.total)
        fraction = free / total if total else 0.0
        report.update(free_bytes=free, total_bytes=total, free_fraction=round(fraction, 4),
                      status='critical' if fraction < critical_fraction else 'low' if fraction < low_fraction else 'ok')
    except Exception:
        pass
    report['data_bytes'] = _tree_bytes(data_dir, skip=dvr_root)
    if dvr_root is not None:
        report['dvr_bytes'] = _tree_bytes(dvr_root)
    return report


def _tree_bytes(root, skip=None):
    total = 0
    root = Path(root)
    try:
        for current, dirs, files in os.walk(root):
            if skip is not None and Path(current) == Path(skip):
                dirs[:] = []
                continue
            for name in files:
                try:
                    total += os.stat(os.path.join(current, name)).st_size
                except OSError:
                    pass
    except OSError:
        return None
    return total


def dvr_report(store):
    """Counts of recordings by state plus how many each bucket retains (never titles, ids or paths)."""
    states, buckets = {}, {}
    for meta in store.all():
        states[meta.get('state', 'unknown')] = states.get(meta.get('state', 'unknown'), 0) + 1
        if meta.get('state') == 'ready':
            buckets[meta.get('bucket', 'unknown')] = buckets.get(meta.get('bucket', 'unknown'), 0) + 1
    return {'recordings_by_state': states, 'ready_per_bucket_max': max(buckets.values(), default=0), 'retention_limit': store.keep}


# --- versioned backup / restore --------------------------------------------------------------

class BackupError(Exception):
    def __init__(self, problems):
        super().__init__('backup_invalid')
        self.problems = problems


def _checksum(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def make_backup(store, now=None):
    """Versioned, checksummed, secret-free bundle. Secret values are never read; the export carries only references."""
    document = store.export()
    problems = []
    settings._scan_urlish(document, 'settings', problems)
    if problems:
        raise BackupError(problems)   # a credential-bearing value reached the document: refuse to write a backup
    return {'format': BACKUP_FORMAT, 'format_version': BACKUP_VERSION, 'created_at': int(now if now is not None else time.time()),
            'settings_schema_version': settings.SCHEMA_VERSION, 'secrets_included': False,
            'settings': document, 'checksum': _checksum(document)}


def _placeholder(section, item, field):
    if section == 'accounts':
        return json.dumps({'host': 'https://placeholder.invalid', 'username': 'u', 'password': 'p'})
    if section == 'integrations':
        return 'https://placeholder.invalid' if field == 'base_url' else 'placeholder-key-0000'
    if item.get('location_type') == 'file':
        return 'placeholder.m3u' if section == 'sources' else 'placeholder.xml'
    return 'https://placeholder.invalid/x'


def verify_backup(bundle):
    """Return a list of problems ([] = restorable). Checks format, version, checksum and a real dry-run restore into a throwaway store."""
    if not isinstance(bundle, dict):
        return [{'path': 'backup', 'error': 'object_required'}]
    problems = []
    if bundle.get('format') != BACKUP_FORMAT:
        problems.append({'path': 'format', 'error': 'unknown_format'})
    version = bundle.get('format_version')
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append({'path': 'format_version', 'error': 'invalid_format_version'})
    elif version > BACKUP_VERSION:
        problems.append({'path': 'format_version', 'error': 'incompatible_format_newer', 'supported': BACKUP_VERSION})
    document = bundle.get('settings')
    if not isinstance(document, dict):
        return problems + [{'path': 'settings', 'error': 'object_required'}]
    if bundle.get('checksum') != _checksum(document):
        problems.append({'path': 'checksum', 'error': 'checksum_mismatch'})
    settings._scan_urlish(document, 'settings', problems)
    if problems:
        return problems
    needed = {'%s/%s/%s' % (n['section'], n['id'], n['field']): n for n in document.get('secrets_required', []) if isinstance(n, dict)}
    items = {(s, i.get('id')): i for s in settings.SECTIONS for i in document.get(s, []) if isinstance(i, dict)}
    supplied = {key: _placeholder(n['section'], items.get((n['section'], n['id']), {}), n['field']) for key, n in needed.items()}
    with tempfile.TemporaryDirectory() as scratch:
        probe = settings.SettingsStore(Path(scratch) / 'verify.sqlite')
        try:
            probe.restore(probe.revision(), document, supplied)
        except settings.SettingsError as error:
            problems.extend({'path': 'restore.' + str(e.get('path', ''))[:60], 'error': str(e.get('error', 'invalid'))[:40]} for e in error.errors)
        except settings.Conflict:
            problems.append({'path': 'restore', 'error': 'conflict'})
    return problems


def restore_backup(store, bundle, secrets_supplied, now=None):
    """Verify, take a pre-restore backup (the rollback point), then restore. Returns {'rollback': <backup>, 'view': ...}.

    Secret values must be supplied separately (as in /v1/settings/restore); they are never part of a bundle."""
    problems = verify_backup(bundle)
    if problems:
        raise BackupError(problems)
    rollback = make_backup(store, now)
    view = store.restore(store.revision(), bundle['settings'], secrets_supplied)
    return {'rollback': rollback, 'revision': view['revision'] if isinstance(view, dict) and 'revision' in view else store.revision()}


def diagnostics(metrics, limiter, data_dir, dvr_root=None, dvr_store=None, store=None, extra=None):
    """The single sanitised diagnostics document served by /v1/ops/diagnostics."""
    out = {'service': 'sgsports', 'release_channel': release_channel(), 'metadata_only': True, 'metrics': metrics.snapshot(),
           'rate_limits': limiter.snapshot(), 'storage': storage_report(data_dir, dvr_root)}
    if store is not None:
        out['settings'] = {'schema_version': store.schema_version(), 'revision': store.revision(), 'backup_format_version': BACKUP_VERSION}
    if dvr_store is not None:
        out['dvr'] = dvr_report(dvr_store)
    if extra:
        out.update(extra)
    return sanitize(out)
