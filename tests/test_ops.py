"""Phase 9 operational hardening: diagnostics privacy, rate control, storage, backup/restore/rollback, DVR restart, API performance."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
import adminfix
import catalog
import dvr
import ops
import settings

PKT = b'\x47' + b'\x00' * 187
PROVIDERS = [{'id': 'providera', 'name': 'Provider A', 'max_connections': 1}]
SECRET = 'sentinel-secret-password-9f3a'


def upsert(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SanitizeTests(unittest.TestCase):
    def test_only_safe_shapes_survive(self):
        out = ops.sanitize({'ok': True, 'n': 3, 'name': 'studio', 'url': 'https://u:p@host/x', 'free text': 'a b', 'bad key!': 1,
                            'nested': {'token': 'Bearer abc', 'list': ['fine', 'Has Space', 4]}, 'nan': float('nan')})
        self.assertEqual(out['ok'], True)
        self.assertEqual(out['url'], ops.REDACTED)
        self.assertEqual(out['nested'], {'token': ops.REDACTED, 'list': ['fine', ops.REDACTED, 4]})
        self.assertIsNone(out['nan'])
        self.assertNotIn('bad key!', out)
        self.assertNotIn('https', json.dumps(out))

    def test_route_labels_never_carry_ids_tokens_or_queries(self):
        for path in ('/v1/tv/sessions/abc123secret', '/v1/providers/providera/refresh', '/v1/settings/restore', '/random/' + SECRET):
            self.assertRegex(ops.route_label(path), r'^[a-z_]+$')
        self.assertEqual(ops.route_label('/random/' + SECRET), 'other')

    def test_release_channel_is_allowlisted(self):
        self.assertEqual(ops.release_channel({'SG_RELEASE_CHANNEL': 'studio'}), 'studio')
        self.assertEqual(ops.release_channel({'SG_RELEASE_CHANNEL': 'https://x:y@z'}), 'unspecified')
        self.assertEqual(ops.release_channel({}), 'unspecified')


class MetricsAndRateTests(unittest.TestCase):
    def test_metrics_bounded_windows_and_quantiles(self):
        m = ops.Metrics(window=10)
        for i in range(100):
            m.observe('/health', 200, i / 1000)
        m.observe('/v1/tv/pair/' + SECRET, 404, 0.001)
        snap = m.snapshot()
        self.assertEqual(snap['routes']['health']['requests'], 100)
        self.assertEqual(snap['routes']['health']['latency_ms']['samples'], 10)
        self.assertEqual(snap['routes']['tv']['status_4xx'], 1)
        self.assertNotIn(SECRET, json.dumps(snap))

    def test_token_bucket_refills_and_is_per_key(self):
        clock = Clock()
        limiter = ops.RateLimiter(clock=clock)
        burst = ops.RateLimiter.POLICIES['auth_fail'][0]
        for _ in range(burst):
            self.assertEqual(limiter.spend('auth_fail', 'a'), 0)
        self.assertGreater(limiter.spend('auth_fail', 'a'), 0)
        self.assertGreater(limiter.blocked('auth_fail', 'a'), 0)
        self.assertEqual(limiter.blocked('auth_fail', 'b'), 0)
        clock.now += 60
        self.assertEqual(limiter.blocked('auth_fail', 'a'), 0)

    def test_tracked_keys_are_capped(self):
        limiter = ops.RateLimiter(max_keys=50, clock=Clock())
        for n in range(5000):
            limiter.spend('auth_fail', 'client-%d' % n)
        self.assertLessEqual(limiter.snapshot()['tracked_keys'], 50)


class StorageTests(unittest.TestCase):
    def usage(self, free, total=1000):
        return lambda path: SimpleNamespace(free=free, total=total)

    def test_levels_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'f').write_bytes(b'x' * 10)
            self.assertEqual(ops.storage_report(d, usage=self.usage(500))['status'], 'ok')
            self.assertEqual(ops.storage_report(d, usage=self.usage(50))['status'], 'low')
            self.assertEqual(ops.storage_report(d, usage=self.usage(10))['status'], 'critical')

            def boom(path):
                raise OSError()
            report = ops.storage_report(d, usage=boom)
            self.assertEqual((report['status'], report['free_bytes']), ('unknown', None))
            self.assertEqual(report['data_bytes'], 10)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = settings.SettingsStore(Path(self.temp.name) / 's.sqlite')
        cred = json.dumps({'host': 'https://prov.example', 'username': 'user', 'password': SECRET})
        self.store.update(self.store.revision(), [upsert('accounts', id='acct-main', name='Main', max_connections=2, credential=cred),
                                                  upsert('profiles', id='prof-kid', name='Kid', preferences={'language': 'es'})])
        self.cred = cred

    def test_backup_is_versioned_checksummed_and_secret_free(self):
        bundle = ops.make_backup(self.store, now=5)
        text = json.dumps(bundle)
        self.assertNotIn(SECRET, text)
        self.assertNotIn('prov.example', text)
        self.assertEqual((bundle['format'], bundle['format_version'], bundle['secrets_included']), ('sg-sports-backup', 1, False))
        self.assertEqual(ops.verify_backup(bundle), [])
        self.assertEqual({'section': 'accounts', 'id': 'acct-main', 'field': 'credential'} in bundle['settings']['secrets_required'], True)

    def test_verify_rejects_tampering_newer_format_and_embedded_credentials(self):
        bundle = ops.make_backup(self.store)
        tampered = json.loads(json.dumps(bundle))
        tampered['settings']['profiles'][0]['name'] = 'Changed'
        self.assertEqual(ops.verify_backup(tampered)[0]['error'], 'checksum_mismatch')
        newer = dict(bundle, format_version=2)
        self.assertEqual(ops.verify_backup(newer)[0]['error'], 'incompatible_format_newer')
        leaked = json.loads(json.dumps(bundle))
        leaked['settings']['profiles'][0]['name'] = 'https://u:p@host/x'
        leaked['checksum'] = ops._checksum(leaked['settings'])
        self.assertTrue(any(p['error'] == 'url_or_credential_not_allowed_in_plain_settings' for p in ops.verify_backup(leaked)))
        self.assertEqual(ops.verify_backup([])[0]['error'], 'object_required')

    def test_verify_does_not_touch_the_live_store(self):
        before = self.store.revision()
        ops.verify_backup(ops.make_backup(self.store))
        self.assertEqual(self.store.revision(), before)

    def test_restore_then_rollback_demonstration(self):
        good = ops.make_backup(self.store)
        key = 'accounts/acct-main/credential'
        self.store.update(self.store.revision(), [upsert('profiles', id='prof-kid', name='Edited', preferences={'language': 'fr'})])
        result = ops.restore_backup(self.store, good, {key: self.cred})
        self.assertEqual([p['name'] for p in self.store.read()['profiles'] if p['id'] == 'prof-kid'], ['Kid'])
        self.assertTrue(self.store.read()['accounts'][0]['credential_configured'])
        # the rollback point captured the edited state; restoring it brings the edit back, and nothing else changes
        rolled = ops.restore_backup(self.store, result['rollback'], {key: self.cred})
        self.assertEqual([p['name'] for p in self.store.read()['profiles'] if p['id'] == 'prof-kid'], ['Edited'])
        self.assertGreater(rolled['revision'], result['revision'])

    def test_failed_restore_leaves_last_good_state(self):
        good = ops.make_backup(self.store)
        before = self.store.read()
        with self.assertRaises(settings.SettingsError):
            ops.restore_backup(self.store, good, {})   # secret not supplied
        after = self.store.read()
        self.assertEqual((before['revision'], before['profiles'], before['accounts']), (after['revision'], after['profiles'], after['accounts']))

    def test_legacy_schema_version_one_export_migrates_during_verify(self):
        legacy = next(v for (kind, v) in catalog.LEGACY_MAP if kind == 'team')
        doc = {'schema_version': 1, 'revision': 1, 'profiles': [{'id': 'prof-old', 'name': 'Old', 'preferences': {'favorites': [legacy]}}]}
        bundle = {'format': ops.BACKUP_FORMAT, 'format_version': 1, 'settings': doc, 'checksum': ops._checksum(doc)}
        self.assertEqual(ops.verify_backup(bundle), [])


class Headers(dict):
    def get(self, key, default=''):
        return dict.get(self, key, default)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'secrets').mkdir()
        for p in (patch.object(app, 'DATA', root), patch.object(app, 'SECRETS', root / 'secrets'),
                  patch.object(app, 'LIMITER', ops.RateLimiter()), patch.object(app, 'METRICS', ops.Metrics()),
                  patch.dict('os.environ', {'SG_RELEASE_CHANNEL': 'studio', 'SG_DVR': '', 'SG_JELLYFIN_ENABLED': ''})):
            p.start()
            self.addCleanup(p.stop)
        app.SETTINGS.clear()
        self.sent = []

    def call(self, path, headers=None, mutation=False, body=b''):
        sent = self.sent

        class Fake(app.Handler):
            def __init__(self):
                self.path, self.headers = path, Headers(headers or {})
                self.client_address = ('203.0.113.9', 1)

                class R:
                    def read(_, n):
                        return body[:n]
                self.rfile = R()

            def send(self, status, data):
                self._status = status
                sent.append((status, data))
        Fake().dispatch(mutation)
        return self.sent[-1]

    @property
    def AUTH(self):
        return adminfix.admin_headers(app.DATA)

    def test_health_reports_release_channel_without_auth(self):
        status, data = self.call('/health')
        self.assertEqual((status, data['release_channel']), (200, 'studio'))

    def test_ops_requires_admin_session(self):
        self.assertEqual(self.call('/v1/ops/diagnostics')[0], 401)
        self.assertEqual(self.call('/v1/ops/diagnostics', {'Cookie': 'sg_admin=nope'})[0], 401)

    def test_diagnostics_are_sanitised_and_secret_free(self):
        app.settings_store().update(1, [upsert('accounts', id='acct-main', name='Main', max_connections=1,
                                               credential=json.dumps({'host': 'https://prov.example', 'username': 'u', 'password': SECRET}))])
        self.call('/health')
        status, data = self.call('/v1/ops/diagnostics', self.AUTH)
        text = json.dumps(data)
        self.assertEqual((status, data['release_channel'], data['settings']['schema_version']), (200, 'studio', settings.SCHEMA_VERSION))
        self.assertIn('health', data['metrics']['routes'])
        for forbidden in (SECRET, 'prov.example', 'http'):
            self.assertNotIn(forbidden, text)
        self.assertIn(self.call('/v1/ops/metrics', self.AUTH)[0], (200,))
        self.assertEqual(self.call('/v1/ops/storage', self.AUTH)[1]['status'] in ('ok', 'low', 'critical', 'unknown'), True)

    def test_backup_endpoint_and_verify(self):
        status, bundle = self.call('/v1/ops/backup', self.AUTH)
        self.assertEqual((status, bundle['format']), (200, 'sg-sports-backup'))
        body = json.dumps(bundle).encode()
        headers = dict(self.AUTH, **{'Content-Type': 'application/json', 'Content-Length': str(len(body))})
        self.assertEqual(self.call('/v1/ops/backup/verify', headers, True, body), (200, {'ok': True, 'problems': []}))
        bad = json.dumps(dict(bundle, checksum='0' * 64)).encode()
        status, data = self.call('/v1/ops/backup/verify', dict(headers, **{'Content-Length': str(len(bad))}), True, bad)
        self.assertEqual((status, data['problems'][0]['error']), (422, 'checksum_mismatch'))

    def test_repeated_bad_sessions_are_rate_limited_even_for_the_right_session(self):
        statuses = [self.call('/v1/settings', {'Cookie': 'sg_admin=wrong'})[0] for _ in range(14)]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[10:], [429] * 4)
        self.assertEqual(self.call('/v1/settings', self.AUTH)[0], 429)   # blocked client stays blocked until the bucket refills

    def test_admin_writes_are_bounded(self):
        body = json.dumps({'revision': 1, 'ops': []}).encode()
        headers = dict(self.AUTH, **{'Content-Type': 'application/json', 'Content-Length': str(len(body))})
        statuses = {self.call('/v1/settings', headers, True, body)[0] for _ in range(40)}
        self.assertIn(429, statuses)

    def test_api_latency_regression_budget(self):
        """Loose thresholds that still catch an accidental O(n^2) or per-request sqlite migration; synthetic data, no network."""
        self.call('/v1/ops/diagnostics', self.AUTH)   # warm the store
        budgets = {'/health': 0.020, '/v1/settings': 0.100, '/v1/ops/diagnostics': 0.150, '/v1/ops/backup': 0.150}
        for path, limit in budgets.items():
            samples = []
            for _ in range(30):
                start = time.perf_counter()
                self.call(path, self.AUTH)
                samples.append(time.perf_counter() - start)
            samples.sort()
            self.assertLess(samples[int(len(samples) * .95) - 1], limit, path)


class DvrRestartTests(unittest.TestCase):
    def test_repeated_crash_restart_cycles_keep_last_three_and_ownership(self):
        with tempfile.TemporaryDirectory() as d:
            for cycle in range(5):
                store = dvr.Store(d)   # a fresh process each cycle
                store.recover()
                rid = 'g%d' % cycle
                store.write({'id': rid, 'bucket': 'tennessee-football', 'state': 'recording', 'event_start': cycle + 1, 'created_at': cycle + 1,
                             'bytes': 0, 'title': 't', 'espn_id': rid, 'playable': True})
                store.media_path(rid).write_bytes(PKT * 3 + b'partial')   # killed mid-packet
            store = dvr.Store(d)
            store.recover()
            store.recover()   # idempotent
            ready = [m['id'] for m in store.bucket('tennessee-football') if m['state'] == 'ready']
            self.assertEqual(ready, ['g4', 'g3', 'g2'])
            for rid in ready:
                self.assertEqual(store.read(rid)['bytes'] % 188, 0)
            report = ops.dvr_report(store)
            self.assertEqual((report['ready_per_bucket_max'], report['retention_limit']), (3, 3))
            self.assertNotIn('tennessee', json.dumps(ops.sanitize(report)).replace('recordings_by_state', ''))


if __name__ == '__main__':
    unittest.main()
