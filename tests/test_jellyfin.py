import os
import stat
import tempfile
import unittest
from pathlib import Path

import jellyfin
from fake_jellyfin import FakeJellyfin, KEY

EXPORT = 'http://sg.test'


def cfg(**kw):
    return jellyfin.Config(True, 'http://jf.test:8096', KEY, EXPORT, 'relay-secret', **kw)


class ConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(jellyfin.load_config({}).enabled)
        self.assertEqual(jellyfin.Client(jellyfin.load_config({})).check()['state'], 'disabled')

    def env(self, **kw):
        return {'SG_JELLYFIN_ENABLED': '1', 'SG_JELLYFIN_URL': 'http://jellyfin:8096', 'SG_JELLYFIN_API_KEY': KEY,
                'SG_JELLYFIN_RELAY_KEY': 'r', 'SG_JELLYFIN_EXPORT_URL': EXPORT, **kw}

    def test_environment_config(self):
        c = jellyfin.load_config(self.env())
        self.assertTrue(c.enabled and not c.problem)
        self.assertNotIn(KEY, repr(c))

    def test_missing_pieces_are_not_configured(self):
        for drop in ('SG_JELLYFIN_URL', 'SG_JELLYFIN_API_KEY', 'SG_JELLYFIN_RELAY_KEY', 'SG_JELLYFIN_EXPORT_URL'):
            env = self.env()
            del env[drop]
            c = jellyfin.load_config(env, secrets_dir='/nonexistent')
            self.assertEqual(jellyfin.Client(c).check()['state'], 'not_configured', drop)

    def test_remote_http_rejected_private_http_allowed(self):
        self.assertEqual(jellyfin.load_config(self.env(SG_JELLYFIN_URL='http://media.example.com')).problem.state, 'not_configured')
        for url in ('https://media.example.com', 'http://192.168.1.5:8096', 'http://localhost:8096', 'http://jellyfin:8096'):
            self.assertIsNone(jellyfin.load_config(self.env(SG_JELLYFIN_URL=url)).problem, url)
        for bad in ('http://u:p@jf:8096', 'http://jf:8096/?api_key=x', 'ftp://jf'):
            self.assertIsNotNone(jellyfin.load_config(self.env(SG_JELLYFIN_URL=bad)).problem, bad)

    def test_secret_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'key'
            path.write_text(KEY + '\n')
            env = self.env()
            del env['SG_JELLYFIN_API_KEY']
            env['SG_JELLYFIN_API_KEY_FILE'] = str(path)
            os.chmod(path, 0o644)
            self.assertIsNotNone(jellyfin.load_config(env).problem)
            os.chmod(path, 0o600)
            c = jellyfin.load_config(env)
            self.assertIsNone(c.problem)
            self.assertEqual(c.api_key, KEY)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeJellyfin(export_url=EXPORT)
        self.fake.add_channel('SG Sports x')
        self.client = jellyfin.Client(cfg(), self.fake)

    def state(self):
        return self.client.check()['state']

    def test_ready(self):
        self.assertEqual(self.state(), 'ready')

    def test_each_failure_is_distinct(self):
        cases = [('reachable', False, 'unreachable'), ('key_ok', False, 'denied'), ('livetv_enabled', False, 'livetv_unavailable'),
                 ('tuners', False, 'tuner_missing'), ('guide', False, 'guide_missing'), ('recording_path', '', 'storage_misconfigured'),
                 ('path_ok', False, 'storage_misconfigured'), ('version', '9.0.1', 'incompatible'),
                 ('version', '10.11.0', 'incompatible'), ('version', '11.0.0', 'incompatible')]
        seen = set()
        for attr, value, expected in cases:
            fake = FakeJellyfin(export_url=EXPORT)
            fake.add_channel('c')
            setattr(fake, attr, value)
            state = jellyfin.Client(cfg(), fake).check()['state']
            self.assertEqual(state, expected, attr)
            seen.add(state)
        self.assertEqual(len(seen), 7)

    def test_mapping_missing_when_no_channels_or_foreign_tuner(self):
        fake = FakeJellyfin(export_url=EXPORT)
        self.assertEqual(jellyfin.Client(cfg(), fake).check()['state'], 'mapping_missing')
        fake.add_channel('c')
        fake.export_url = 'http://someone-else'
        self.assertEqual(jellyfin.Client(cfg(), fake).check()['state'], 'mapping_missing')

    def test_key_only_in_authorization_header_never_in_errors(self):
        self.fake.key_ok = False
        result = self.client.check()
        self.assertNotIn(KEY, str(result))
        for method, path, query, payload in self.fake.requests:
            self.assertNotIn(KEY, path + query)

    def test_server_error_is_unreachable_and_redirect_like_status_is_incompatible(self):
        c = jellyfin.Client(cfg(), lambda *a: (503, b''))
        self.assertEqual(c.check()['state'], 'unreachable')
        c = jellyfin.Client(cfg(), lambda *a: (302, b''))
        self.assertEqual(c.check()['state'], 'incompatible')


if __name__ == '__main__':
    unittest.main()
