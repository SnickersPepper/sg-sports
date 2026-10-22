"""Phase 7A M2B contract tests: least-privilege paired-TV preferences over the shared versioned settings store. Synthetic data only."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import adminfix
import settings
import tv

M3U_URL = 'https://iptv.example.invalid/get.php?username=demo&password=s3cret-m3u'
XTREAM = json.dumps({'host': 'https://xc.example.invalid', 'username': 'demo-user', 'password': 's3cret-xc'})
SECRET_TEXTS = ['s3cret-m3u', 's3cret-xc', 'demo-user', 'xc.example.invalid', 'iptv.example.invalid', 'admintoken']
FAVORITES = ['soccer.men.fc-barcelona', 'soccer.women.fc-barcelona'] + ['nfl.team-%d' % n for n in range(8)]


def up(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


class TvSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(app, 'DATA', Path(self.temp.name)),):
            patcher.start()
            self.addCleanup(patcher.stop)
        app.SETTINGS.clear()
        tv.PAIR.clear()
        self.outputs = []

    def call(self, method, path, body=None, token='admintoken', content_type='application/json'):
        sent = {}
        data = json.dumps(body).encode() if body is not None else b''
        headers = {'Content-Type': content_type, 'Content-Length': str(len(data))}
        if token == 'admintoken':
            headers.update(adminfix.admin_headers(app.DATA))
        elif token:
            headers['Authorization'] = 'Bearer ' + token

        class Fake(app.Handler):
            def __init__(inner):
                inner.path, inner.headers, inner.rfile, inner.wfile = path, headers, io.BytesIO(data), io.BytesIO()
            def send(inner, status, payload):
                sent['status'], sent['body'] = status, payload
        secrets_dir = Path(self.temp.name) / 'sec'
        secrets_dir.mkdir(exist_ok=True)
        with patch.object(app, 'SECRETS', secrets_dir):
            Fake().dispatch(method == 'POST')
        self.outputs.append(sent['body'])
        return sent['status'], sent['body']

    def admin(self, method, sub, body=None):
        return self.call(method, '/v1/settings' + sub, body)

    def pair(self):
        code = tv.new_pair()['code']
        token = tv.pair(code)['token']
        return token, 'dev-' + tv.digest(token)[:16]

    def tv_get(self, token):
        return self.call('GET', '/v1/tv/settings', token=token)

    def tv_put(self, token, revision, preferences, **extra):
        return self.call('POST', '/v1/tv/settings', dict({'revision': revision, 'preferences': preferences}, **extra), token=token)

    def assertNoLeak(self, token=None):
        text = json.dumps(self.outputs)
        for secret in SECRET_TEXTS[:-1] + ([token] if token else []):
            self.assertNotIn(secret, text)

    def test_tv_reads_only_its_assigned_profile_and_allowed_preferences(self):
        token, device = self.pair()
        self.admin('GET', '')  # registry sync
        self.admin('POST', '', {'revision': self.admin('GET', '')[1]['revision'], 'ops': [
            up('accounts', id='acct-main', name='Main', max_connections=1, credential=XTREAM),
            up('sources', id='src-direct', name='Direct', kind='m3u', location_type='url', location=M3U_URL, account_id='acct-main'),
            up('profiles', id='prof-other', name='Other', preferences={'favorites': ['nfl.other-secretish']}),
            up('profiles', id='prof-household', name='Household', preferences={'favorites': FAVORITES, 'time_zone': 'America/Chicago', 'language': 'es'})]})
        status, view = self.tv_get(token)
        self.assertEqual(status, 200)
        self.assertEqual(set(view), {'revision', 'profile_id', 'profile_name', 'preferences'})
        self.assertEqual(view['profile_id'], 'prof-household')
        self.assertEqual(view['preferences']['favorites'], FAVORITES)
        self.assertNotIn('time_zone', view['preferences'])  # administrator-only preference
        self.assertNotIn('nfl.other-secretish', json.dumps(view))  # other profile
        self.assertNoLeak(token)
        self.assertEqual(self.call('GET', '/v1/settings', token=token)[0], 401)  # a TV token is not an administrator token
        self.assertEqual(self.call('POST', '/v1/settings', {'revision': 1, 'ops': []}, token=token)[0], 401)
        self.assertEqual(self.call('GET', '/v1/tv/settings', token=None)[0], 401)
        self.assertEqual(self.call('GET', '/v1/tv/settings', token='admintoken')[0], 401)  # nor a TV token

    def test_browser_to_tv_and_tv_to_browser_converge_on_one_revision(self):
        token, device = self.pair()
        revision = self.admin('GET', '')[1]['revision']
        self.admin('POST', '', {'revision': revision, 'ops': [up('profiles', id='prof-household', preferences={'favorites': FAVORITES[:3], 'leagues': ['nfl']})]})
        status, view = self.tv_get(token)
        self.assertEqual(view['preferences']['favorites'], FAVORITES[:3])
        self.assertEqual(view['revision'], self.admin('GET', '')[1]['revision'])
        status, saved = self.tv_put(token, view['revision'], {'favorites': list(reversed(FAVORITES)), 'language': 'pt', 'spoilers': True,
                                                              'playback': {'start_from_beginning': True, 'quality': 'high'}, 'recording': {'enabled': True, 'keep_latest': 3}})
        self.assertEqual(status, 200)
        self.assertEqual(saved['revision'], view['revision'] + 1)
        profile = next(p for p in self.admin('GET', '')[1]['profiles'] if p['id'] == 'prof-household')
        self.assertEqual(profile['preferences']['favorites'], list(reversed(FAVORITES)))
        self.assertEqual(profile['preferences']['leagues'], ['nfl'])  # fields the TV did not name are untouched
        self.assertEqual(profile['preferences']['playback'], {'start_from_beginning': True, 'quality': 'high'})
        self.assertEqual(self.admin('GET', '')[1]['revision'], saved['revision'])
        self.assertNoLeak(token)

    def test_stale_simultaneous_edits_conflict_explicitly_and_lose_nothing(self):
        token, _ = self.pair()
        tv_rev = self.tv_get(token)[1]['revision']
        self.admin('POST', '', {'revision': tv_rev, 'ops': [up('profiles', id='prof-household', preferences={'favorites': ['nfl.team-1']})]})
        status, conflict = self.tv_put(token, tv_rev, {'favorites': ['nfl.team-9']})
        self.assertEqual((status, conflict['error']), (409, 'revision_conflict'))
        self.assertEqual(conflict['current']['preferences']['favorites'], ['nfl.team-1'])  # the TV can re-base on this
        self.assertEqual(conflict['revision'], conflict['current']['revision'])
        self.assertEqual(self.tv_get(token)[1]['preferences']['favorites'], ['nfl.team-1'])
        self.assertEqual(self.tv_put(token, conflict['revision'], {'favorites': ['nfl.team-1', 'nfl.team-9']})[0], 200)
        # the reverse race: the TV writes first, the browser's stale write conflicts
        status, stale = self.admin('POST', '', {'revision': conflict['revision'], 'ops': [up('profiles', id='prof-household', name='Renamed')]})
        self.assertEqual((status, stale['error']), (409, 'revision_conflict'))

    def test_profile_isolation_and_assignment_survive_routine_source_and_guide_changes(self):
        token_a, device_a = self.pair()
        token_b, device_b = self.pair()
        self.assertNotEqual(device_a, device_b)
        revision = self.admin('GET', '')[1]['revision']
        self.admin('POST', '', {'revision': revision, 'ops': [
            up('profiles', id='prof-guest', name='Guest', preferences={'favorites': ['nfl.guest-team']}),
            up('devices', id=device_b, profile_id='prof-guest')]})
        self.assertEqual(self.tv_get(token_a)[1]['profile_id'], 'prof-household')
        view_b = self.tv_get(token_b)[1]
        self.assertEqual((view_b['profile_id'], view_b['preferences']['favorites']), ('prof-guest', ['nfl.guest-team']))
        self.assertEqual(self.tv_put(token_b, view_b['revision'], {'favorites': []})[0], 200)  # empty list
        self.assertNotIn('favorites', self.tv_get(token_a)[1]['preferences'])
        self.assertEqual(self.tv_get(token_b)[1]['preferences']['favorites'], [])
        # routine source / guide edits and a full restore keep device identity and profile
        revision = self.admin('GET', '')[1]['revision']
        self.admin('POST', '', {'revision': revision, 'ops': [
            up('accounts', id='acct-main', name='Main', max_connections=1, credential=XTREAM),
            up('sources', id='src-direct', name='Direct', kind='m3u', location_type='url', location=M3U_URL, account_id='acct-main'),
            up('guide_sources', id='guide-main', name='Guide', kind='xmltv')]})
        revision = self.admin('GET', '')[1]['revision']
        self.admin('POST', '', {'revision': revision, 'ops': [{'op': 'delete', 'section': 'sources', 'id': 'src-direct'}, up('guide_sources', id='guide-main', name='Renamed guide')]})
        export = self.admin('GET', '/export')[1]
        revision = self.admin('GET', '')[1]['revision']
        status, _ = self.admin('POST', '/restore', {'revision': revision, 'export': export, 'secrets': {'accounts/acct-main/credential': XTREAM}})
        self.assertEqual(status, 200)
        devices = {d['id']: d for d in self.admin('GET', '')[1]['devices']}
        self.assertEqual(devices[device_b]['profile_id'], 'prof-guest')
        self.assertEqual(devices[device_a]['profile_id'], 'prof-household')
        self.assertEqual(self.tv_get(token_b)[1]['profile_id'], 'prof-guest')
        self.assertEqual(self.tv_get(token_a)[0], 200)
        self.assertNoLeak(token_a)
        self.assertNoLeak(token_b)

    def test_forbidden_fields_are_rejected_without_writing(self):
        token, _ = self.pair()
        view = self.tv_get(token)[1]
        revision = view['revision']
        for preferences in ({'time_zone': 'UTC'}, {'accounts': []}, {'profiles': []}, {'devices': []}, {'sources': [], 'favorites': []}, {'routes': []},
                            {'credential': 'x'}, {'location_ref': 'store:abcdef12'}, {'integrations': []}, {'secret': 'x'}):
            status, error = self.tv_put(token, revision, preferences)
            self.assertEqual((status, error['error']), (422, 'invalid_settings'), preferences)
        for extra in ({'ops': [up('accounts', name='Evil', max_connections=1)]}, {'section': 'accounts'}, {'profile_id': 'prof-other'}):
            status, error = self.tv_put(token, revision, {'favorites': []}, **extra)
            self.assertEqual((status, error['error']), (422, 'invalid_settings'), extra)
        for body in ({'preferences': {'favorites': []}}, {'revision': True, 'preferences': {'favorites': []}}, {'revision': revision, 'preferences': {}}, {'revision': revision, 'preferences': []}):
            self.assertEqual(self.call('POST', '/v1/tv/settings', body, token=token)[0], 422, body)
        self.assertEqual(self.call('POST', '/v1/tv/settings', [1], token=token)[0], 400)
        self.assertEqual(self.call('POST', '/v1/tv/settings', {'revision': revision, 'preferences': {'favorites': []}}, token=token, content_type='text/plain')[0], 415)
        self.assertEqual(self.admin('GET', '')[1]['revision'], revision)  # nothing was written
        self.assertEqual(self.admin('GET', '')[1]['accounts'], [])
        # no admin-section path reachable with the TV token
        for sub in ('', '/export', '/budgets', '/setup-status', '/client/../export'):
            self.assertIn(self.call('GET', '/v1/settings' + sub, token=token)[0], (401, 404), sub)

    def test_value_validation_for_lists_and_nested_preferences(self):
        token, _ = self.pair()
        revision = self.tv_get(token)[1]['revision']
        bad = [{'favorites': ['Has Space']}, {'favorites': ['a.b', 'a.b']}, {'favorites': 'nfl'}, {'favorites': [5]}, {'favorites': ['http://x.invalid']},
               {'favorites': ['a.b@c.d']}, {'favorites': ['x%d' % n for n in range(201)]}, {'leagues': ['UPPER']}, {'language': 'not a language'},
               {'language': 'http://x.invalid'}, {'spoilers': 'yes'}, {'playback': {'quality': 'ultra'}}, {'playback': {'url': 'x'}},
               {'recording': {'keep_latest': 0}}, {'recording': {'keep_latest': True}}, {'recording': {'path': '/etc'}}, {'playback': 'x'}]
        for preferences in bad:
            status, error = self.tv_put(token, revision, preferences)
            self.assertEqual((status, error['error']), (422, 'invalid_settings'), preferences)
        self.assertEqual(self.tv_get(token)[1]['revision'], revision)
        ok = {'favorites': ['soccer.women.fc-barcelona', 'soccer.men.fc-barcelona', 'basketball.women.tennessee', 'basketball.men.tennessee'], 'leagues': ['sec', 'nfl'],
              'language': 'pt-BR', 'spoilers': False, 'playback': {'audio_language': 'es', 'quality': 'auto'}, 'recording': {'enabled': False, 'favorites_only': True, 'keep_latest': 50}}
        status, saved = self.tv_put(token, revision, ok)
        self.assertEqual(status, 200)
        self.assertEqual(saved['preferences'], ok)  # sport/gender-distinct IDs stay distinct and ordered
        # reorder, remove one, remove all
        favorites = ok['favorites']
        status, saved = self.tv_put(token, saved['revision'], {'favorites': [favorites[2], favorites[0]]})
        self.assertEqual(saved['preferences']['favorites'], [favorites[2], favorites[0]])
        status, saved = self.tv_put(token, saved['revision'], {'favorites': []})
        self.assertEqual(saved['preferences']['favorites'], [])
        self.assertEqual(saved['preferences']['leagues'], ['sec', 'nfl'])
        long_list = ['nfl.team-%03d' % n for n in range(200)]
        self.assertEqual(self.tv_put(token, saved['revision'], {'favorites': long_list})[1]['preferences']['favorites'], long_list)

    def test_revocation_expiry_and_repair_never_reuse_credentials(self):
        token, device = self.pair()
        self.assertEqual(self.tv_get(token)[0], 200)
        # TV-initiated unpair
        self.assertEqual(self.call('POST', '/tv/unpair', {}, token=token)[0], 200)
        self.assertEqual(self.tv_get(token)[0], 401)
        self.assertEqual(self.tv_put(token, 1, {'favorites': []})[0], 401)
        self.assertEqual({d['id']: d['state'] for d in self.admin('GET', '')[1]['devices']}[device], 'unpaired')
        # a new pairing is a new identity: it does not inherit the old token or device record
        token2, device2 = self.pair()
        self.assertNotEqual(token, token2)
        self.assertNotEqual(device, device2)
        self.assertEqual(self.tv_get(token)[0], 401)
        self.assertEqual(self.tv_get(token2)[0], 200)
        # administrator revocation
        status, view = self.admin('POST', '/devices/%s/revoke' % device2, {})
        self.assertEqual(status, 200)
        self.assertEqual({d['id']: d['state'] for d in view['devices']}[device2], 'unpaired')
        self.assertEqual(self.tv_get(token2)[0], 401)
        self.assertEqual(self.admin('POST', '/devices/dev-0000000000000000/revoke', {})[0], 422)
        self.assertEqual(self.call('POST', '/v1/settings/devices/%s/revoke' % device2, {}, token=token2)[0], 401)
        # expiry
        token3, device3 = self.pair()
        self.assertEqual(self.tv_get(token3)[0], 200)
        with patch.object(tv, 'now', return_value=tv.now() + 31 * 86400):
            self.assertEqual(self.tv_get(token3)[0], 401)
        store = app.settings_store()
        store.sync_devices(app.read_cache('tv-devices'), now=tv.now() + 31 * 86400)
        with self.assertRaises(settings.Forbidden):
            store.tv_settings(device3)
        self.assertNoLeak(token3)

    def test_unassigned_unknown_or_non_tv_device_is_forbidden(self):
        token, device = self.pair()
        revision = self.admin('GET', '')[1]['revision']
        self.admin('POST', '', {'revision': revision, 'ops': [up('devices', id=device, profile_id=None)]})
        status, error = self.tv_get(token)
        self.assertEqual((status, error['reason']), (403, 'no_profile_assigned'))
        self.assertEqual(self.tv_put(token, self.admin('GET', '')[1]['revision'], {'favorites': []})[0], 403)
        revision = self.admin('GET', '')[1]['revision']
        self.admin('POST', '', {'revision': revision, 'ops': [up('devices', id=device, profile_id='prof-household', role='admin')]})
        self.assertEqual(self.tv_get(token)[1]['reason'], 'unknown_device')
        store = app.settings_store()
        with self.assertRaises(settings.Forbidden):
            store.tv_settings('dev-ffffffffffffffff')

    def test_restart_persistence_and_installed_state_migration(self):
        token, device = self.pair()
        revision = self.tv_get(token)[1]['revision']
        self.tv_put(token, revision, {'favorites': FAVORITES, 'language': 'fr'})
        app.SETTINGS.clear()  # process restart: the store and its pairing registry are rebuilt from disk
        status, view = self.tv_get(token)
        self.assertEqual((status, view['preferences']['favorites'], view['preferences']['language']), (200, FAVORITES, 'fr'))
        self.assertEqual(view['revision'], revision + 1)
        # an install paired before the registry existed is registered on first contact, assigned to the default profile
        app.SETTINGS.clear()
        (Path(self.temp.name) / 'settings.sqlite').unlink()
        for suffix in ('-wal', '-shm'):
            (Path(self.temp.name) / ('settings.sqlite' + suffix)).unlink(missing_ok=True)
        status, view = self.tv_get(token)
        self.assertEqual((status, view['profile_id']), (200, 'prof-household'))
        self.assertEqual([d['id'] for d in self.admin('GET', '')[1]['devices']], [device])
        self.assertNoLeak(token)

    def test_tv_cannot_widen_scope_through_other_tv_routes(self):
        token, _ = self.pair()
        for path in ('/v1/settings/export', '/v1/providers', '/v1/settings/budgets'):
            self.assertEqual(self.call('GET', path, token=token)[0], 401, path)
        self.assertEqual(self.call('GET', '/v1/tv/settings?profile=prof-other', token=token)[1]['profile_id'], 'prof-household')


if __name__ == '__main__':
    unittest.main()
