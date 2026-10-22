"""Static and logic checks of the pinned Dispatcharr e2e assets (no container, no sockets). They do not replace the isolated run."""
import py_compile
import re
import unittest
from pathlib import Path
from unittest.mock import patch

E2E = Path(__file__).resolve().parent / 'dispatcharr_e2e'
import sys
sys.path.insert(0, str(E2E))
import seed  # noqa: E402


class Assets(unittest.TestCase):
    def test_every_script_compiles(self):
        for name in ('provider_stub.py', 'seed.py', 'run_e2e.py'):
            py_compile.compile(str(E2E / name), cfile=str(Path('/tmp') / (name + 'c')), doraise=True)

    def test_images_are_pinned_by_reviewed_digests(self):
        compose = (E2E / 'docker-compose.yml').read_text()
        self.assertRegex(compose, r'image: ghcr\.io/dispatcharr/dispatcharr@sha256:b7d695c5[0-9a-f]{56}')
        self.assertIn('internal: true', compose)
        self.assertNotIn('ports:', compose)
        self.assertNotIn('docker.sock', compose)
        self.assertRegex((E2E / 'Dockerfile.stub').read_text(), r'FROM python:3\.11-slim@sha256:[0-9a-f]{64}\n')
        self.assertNotIn('REPLACE', (E2E / 'Dockerfile.stub').read_text())

    def test_runner_covers_every_required_verdict_and_never_passes_without_a_seed(self):
        text = (E2E / 'run_e2e.py').read_text()
        for name in ('auth_pinned_version', 'auth_wrong_key_rejected', 'proxy_profile_on_all_channels', 'fan_out_one_upstream',
                     'contention_second_channel_never_reaches_relay', 'cooldown_and_restart_hold_the_account', 'jellyfin_route_no_extra_upstream',
                     'output_is_secret_redacted'):
            self.assertIn("'%s'" % name, text)
        self.assertIn('BLOCKED', text)


class Seed(unittest.TestCase):
    def test_seed_sequence_against_a_scripted_public_api_and_secrets_stay_out_of_the_log(self):
        calls, log = [], []

        def fake(base, method, path, body=None, headers=None, expect=None):
            calls.append((method, path))
            if path == '/api/accounts/initialize-superuser/':
                return {'superuser_exists': False} if method == 'GET' else {'superuser_exists': True}
            if path == '/api/accounts/token/':
                return {'access': 'jwt-token'}
            if path == '/api/accounts/api-keys/generate/':
                return {'key': 'the-api-key'}
            if path == '/api/core/streamprofiles/':
                return [{'id': 7, 'name': 'Proxy', 'locked': True}, {'id': 8, 'name': 'Redirect', 'locked': True}]
            if path.startswith('/api/m3u/accounts/') and method == 'GET':
                return {'status': 'success'}
            if path == '/api/m3u/accounts/':
                return {'id': 1 if 'Provider A' in body['name'] else 2}
            if path.startswith('/api/m3u/refresh/'):
                return {}
            if path.startswith('/api/channels/streams/'):
                return {'results': [{'id': 11, 'url': 'http://pa/live/u/p/1.ts'}, {'id': 12, 'url': 'http://pa/live/u/p/2.ts'}, {'id': 21, 'url': 'http://pb/live/u/p/1.ts'}]}
            if path == '/api/channels/channels/':
                self.assertEqual(body['stream_profile_id'], 7)
                return {'uuid': '00000000-0000-4000-8000-%012d' % body['streams'][0]}
            raise AssertionError(path)
        with patch.object(seed, 'call', fake), patch.object(seed.time, 'sleep', lambda s: None):
            data = seed.seed('http://d', {'A': 'http://pa', 'B': 'http://pb'}, log.append)
        self.assertEqual(sorted(data['channels']), ['A1', 'A2', 'B1'])
        self.assertEqual(data['api_key'], 'the-api-key')
        self.assertTrue(all(data['api_key'] not in line and data['admin_password'] not in line for line in log))
        self.assertEqual(calls[0], ('GET', '/api/accounts/initialize-superuser/'))

    def test_a_missing_proxy_profile_or_existing_admin_blocks_instead_of_passing(self):
        def with_admin(*a, **k):
            return {'superuser_exists': True}
        with patch.object(seed, 'call', with_admin):
            with self.assertRaises(seed.Blocked):
                seed.seed('http://d', {'A': 'http://pa', 'B': 'http://pb'}, lambda m: None)


if __name__ == '__main__':
    unittest.main()
