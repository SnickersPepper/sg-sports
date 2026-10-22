import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import adminfix
import settings

PROVIDERS = [{'id': 'providera', 'name': 'Provider A', 'type': 'xtream', 'max_connections': 1, 'hosts': ['http://a.invalid']},
             {'id': 'providerb', 'name': 'Provider B', 'type': 'xtream', 'max_connections': 2, 'hosts': ['http://b.invalid']}]
SECRET_URL = 'http://user:hunter2@provider.invalid/get.php?username=user&password=hunter2'


def upsert(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.sqlite'
        self.store = settings.SettingsStore(self.path, providers=PROVIDERS, paired_devices={'ab' * 32: {'expires': 1}})

    def test_clean_creation_and_legacy_import(self):
        view = self.store.read()
        self.assertEqual((view['schema_version'], view['revision']), (4, 1))
        self.assertEqual([a['id'] for a in view['accounts']], ['acct-providera', 'acct-providerb'])
        self.assertEqual([a['max_connections'] for a in view['accounts']], [1, 2])
        self.assertEqual(view['sources'][0]['account_id'], 'acct-providera')
        self.assertEqual(view['devices'][0]['id'], 'dev-' + 'ab' * 8)
        self.assertEqual(view['profiles'][0]['id'], 'prof-household')
        self.assertTrue(view['accounts'][0]['credential_configured'])
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), '0o600')

    def test_restart_persistence_keeps_ids_and_does_not_reimport(self):
        view = self.store.update(1, [upsert('profiles', name='Guest', preferences={'language': 'es'})])
        guest = view['profiles'][1]['id']
        reopened = settings.SettingsStore(self.path, providers=[{'id': 'other', 'name': 'X', 'max_connections': 9}])
        again = reopened.read()
        self.assertEqual(again['revision'], 2)
        self.assertEqual(again['profiles'][1]['id'], guest)
        self.assertEqual([a['id'] for a in again['accounts']], ['acct-providera', 'acct-providerb'])

    def test_migration_runs_forward_and_failed_migration_rolls_back(self):
        def two(db, ctx):
            db.execute('CREATE TABLE extra (x)')
        store = settings.SettingsStore(self.path, migrations={**settings.MIGRATIONS, 5: two})
        self.assertEqual(store.schema_version(), 5)
        self.assertEqual(store.read()['revision'], 1)

        def bad(db, ctx):
            db.execute('CREATE TABLE half (x)')
            raise RuntimeError('boom')
        with self.assertRaises(RuntimeError):
            settings.SettingsStore(self.path, migrations={**settings.MIGRATIONS, 5: two, 6: bad})
        self.assertEqual(settings.SettingsStore(self.path, migrations={**settings.MIGRATIONS, 5: two}).schema_version(), 5)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='half'").fetchone())

    def test_newer_schema_is_refused(self):
        settings.SettingsStore(self.path, migrations={**settings.MIGRATIONS, 5: lambda db, ctx: None})
        with self.assertRaises(settings.SchemaTooNew):
            settings.SettingsStore(self.path)

    def test_logos_default_off_for_a_fresh_install_but_an_existing_install_keeps_espn(self):
        # SGH-314: a fresh install starts with logos Off; an installation that existed before (legacy providers or paired TVs) keeps ESPN, with no revision change.
        fresh = settings.SettingsStore(Path(self.temp.name) / 'fresh.sqlite')
        self.assertIsNone(fresh.artwork_config())
        self.assertEqual(fresh.read()['revision'], 1)
        self.assertEqual(self.store.artwork_config()['mode'], 'espn')
        self.assertEqual(self.store.read()['revision'], 1)
        self.store.update(1, [upsert('integrations', id='int-artwork', name='Team artwork', kind='artwork', enabled=True, options={'mode': 'off'})])
        self.assertEqual(self.store.artwork_config()['mode'], 'off')  # an explicit choice always wins over the inherited default
        import artwork
        self.assertEqual(artwork.DEFAULT_MODE, 'off')

    def test_stale_revision_conflicts_and_changes_nothing(self):
        self.store.update(1, [upsert('profiles', name='One')])
        with self.assertRaises(settings.Conflict) as caught:
            self.store.update(1, [upsert('profiles', name='Two')])
        self.assertEqual(caught.exception.revision, 2)
        self.assertEqual([p['name'] for p in self.store.read()['profiles']], ['Household', 'One'])

    def test_concurrent_writers_exactly_one_wins(self):
        results = []

        def writer(n):
            try:
                self.store.update(1, [upsert('profiles', name='P%d' % n)])
                results.append('ok')
            except settings.Conflict:
                results.append('conflict')
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ['conflict'] * 5 + ['ok'])
        self.assertEqual(self.store.read()['revision'], 2)

    def test_invalid_update_keeps_last_good_state_atomically(self):
        before = self.store.read()
        bad = [upsert('profiles', name='Valid but rolled back'),
               upsert('sources', name='Bad', kind='m3u', account_id='acct-missing')]
        with self.assertRaises(settings.SettingsError) as caught:
            self.store.update(1, bad)
        self.assertIn('unknown_account', json.dumps(caught.exception.errors))
        self.assertEqual(self.store.read(), before)
        for ops in ([upsert('accounts', name='A', max_connections=0)], [upsert('accounts', name='A', max_connections=True)],
                    [upsert('accounts', name='A', max_connections=1, bogus=1)], [{'op': 'delete', 'section': 'accounts', 'id': 'acct-providera'}],
                    [{'op': 'delete', 'section': 'profiles', 'id': 'prof-household'}], [upsert('nope', name='x')]):
            with self.assertRaises(settings.SettingsError):
                self.store.update(1, ops)
            self.assertEqual(self.store.read(), before)

    def test_stable_ids(self):
        view = self.store.update(1, [upsert('accounts', name='Third', max_connections=3)])
        new_id = view['accounts'][-1]['id']
        self.assertRegex(new_id, r'^acct-[0-9a-f]{12}$')
        view = self.store.update(2, [upsert('accounts', id=new_id, name='Renamed', max_connections=4)])
        account = [a for a in view['accounts'] if a['id'] == new_id]
        self.assertEqual((len(account), account[0]['name'], account[0]['max_connections']), (1, 'Renamed', 4))
        with self.assertRaises(settings.SettingsError):
            self.store.update(3, [upsert('sources', id=new_id, name='Clash', kind='m3u')])
        with self.assertRaises(settings.SettingsError):
            self.store.update(3, [upsert('profiles', id='Bad Id!', name='x')])

    def test_duplicate_source_aliases_share_one_account_budget(self):
        view = self.store.update(1, [
            upsert('sources', name='Imported copy', kind='m3u', account_id='acct-providera', location=SECRET_URL),
            upsert('sources', name='IPTVBoss layout', kind='iptvboss_export', account_id='acct-providera', location=SECRET_URL + '2'),
            upsert('sources', name='Unmapped merged list', kind='m3u', account_id=None)])
        budgets = self.store.budgets()
        providera = [a for a in budgets['accounts'] if a['account_id'] == 'acct-providera'][0]
        self.assertEqual(providera['max_connections'], 1)
        self.assertEqual(len(providera['source_ids']), 3)
        self.assertEqual(len(budgets['accounts']), 2)
        self.assertEqual(budgets['unresolved_source_ids'], [view['sources'][-1]['id']])

    def test_secret_redaction_in_every_view_and_gc(self):
        view = self.store.update(1, [upsert('sources', name='Direct', kind='m3u', account_id='acct-providera', location=SECRET_URL),
                                     upsert('integrations', name='Media server', kind='jellyfin', credential='SYNTHETIC-KEY-123', options={'enabled_features': ['dvr']})])
        source = view['sources'][-1]
        self.assertTrue(source['location_configured'])
        for blob in (json.dumps(self.store.read()), json.dumps(self.store.client_view()), json.dumps(self.store.export()), json.dumps(self.store.budgets())):
            for needle in ('hunter2', 'provider.invalid', 'SYNTHETIC-KEY-123', 'store:'):
                self.assertNotIn(needle, blob)
        self.assertEqual(self.store.export()['secrets_required'][-1]['field'], 'credential')
        with sqlite3.connect(self.path) as db:
            ref = json.loads(db.execute('SELECT doc FROM config').fetchone()[0])['sources'][-1]['location_ref']
        self.assertEqual(self.store.secret(ref), SECRET_URL)
        # rotate keeps the reference, clear removes the stored value
        self.store.update(2, [upsert('sources', id=source['id'], location='http://rotated.invalid/x')])
        self.assertEqual(self.store.secret(ref), 'http://rotated.invalid/x')
        self.store.update(3, [upsert('sources', id=source['id'], location=None)])
        self.assertIsNone(self.store.secret(ref))
        self.assertFalse(self.store.read()['sources'][-1]['location_configured'])
        # deleting an item garbage-collects its secret
        self.store.update(4, [upsert('integrations', id=view['integrations'][0]['id'], credential='ANOTHER')])
        self.store.update(5, [{'op': 'delete', 'section': 'integrations', 'id': view['integrations'][0]['id']}])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM secrets').fetchone()[0], 0)

    def test_plain_fields_reject_urls_and_client_cannot_write_references(self):
        for ops in ([upsert('sources', name=SECRET_URL, kind='m3u')],
                    [upsert('profiles', name='x', preferences={'language': 'http://x.invalid'})],
                    [upsert('sources', name='x', kind='m3u', location_ref='file:../etc/passwd')]):
            with self.assertRaises(settings.SettingsError):
                self.store.update(1, ops)
        self.assertEqual(self.store.read()['revision'], 1)

    def test_client_view_has_no_admin_sections_and_snapshots_are_bounded(self):
        self.assertEqual(set(self.store.client_view()), {'schema_version', 'revision', 'profiles'})
        for n in range(settings.SNAPSHOT_LIMIT + 5):
            self.store.update(n + 1, [upsert('profiles', id='prof-household', name='H%d' % n)])
        self.assertLessEqual(len(self.store.snapshots()), settings.SNAPSHOT_LIMIT)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(app, 'DATA', Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        app.SETTINGS.clear()

    def test_route_contract(self):
        status, view = app.settings_route(False, [])
        self.assertEqual((status, view['revision']), (200, 1))
        body = json.dumps({'revision': 1, 'ops': [upsert('profiles', name='Kid')]}).encode()
        self.assertEqual(app.settings_route(True, [], body)[0], 200)
        status, payload = app.settings_route(True, [], body)
        self.assertEqual((status, payload), (409, {'error': 'revision_conflict', 'revision': 2}))
        bad = json.dumps({'revision': 2, 'ops': [upsert('accounts', name='x', max_connections=0)]}).encode()
        status, payload = app.settings_route(True, [], bad)
        self.assertEqual((status, payload['error']), (422, 'invalid_settings'))
        self.assertEqual(app.settings_route(True, [], b'[]')[0], 400)
        self.assertEqual(app.settings_route(True, [], b'{')[0], 400)
        self.assertEqual(app.settings_route(False, ['nope'])[0], 404)
        self.assertEqual(app.settings_route(False, ['export'])[0], 200)
        self.assertEqual(app.settings_route(False, ['budgets'])[0], 200)

    def test_settings_requires_admin_session_and_device_gets_only_client_view(self):
        class Headers(dict):
            def get(self, key, default=''):
                return dict.get(self, key, default)
        sent = []

        class Fake(app.Handler):
            def __init__(self, path, headers):
                self.path, self.headers = path, Headers(headers)
                self.rfile = None
            def send(self, status, data):
                sent.append((status, data))
        secrets_dir = Path(self.temp.name) / 'secrets'
        secrets_dir.mkdir()
        admin = adminfix.admin_headers(app.DATA)
        with patch.object(app, 'SECRETS', secrets_dir):
            Fake('/v1/settings', {}).dispatch(False)
            Fake('/v1/settings', {'Authorization': 'Bearer wrong'}).dispatch(False)
            Fake('/v1/settings/client', {'Authorization': 'Bearer not-a-paired-device'}).dispatch(False)
            Fake('/v1/settings', admin).dispatch(False)
            with patch.object(app.tv, 'device', return_value='x'):
                Fake('/v1/settings/client', {'Authorization': 'Bearer pairedtoken'}).dispatch(False)
                Fake('/v1/settings', {'Authorization': 'Bearer pairedtoken'}).dispatch(False)
        self.assertEqual([s for s, _ in sent], [401, 401, 401, 200, 200, 401])
        self.assertIn('accounts', sent[3][1])
        self.assertEqual(set(sent[4][1]), {'schema_version', 'revision', 'profiles'})

    def test_legacy_import_is_marked_read_only_and_clean_install_has_no_providers(self):
        self.store = settings.SettingsStore(Path(self.temp.name) / 'legacy.sqlite', providers=PROVIDERS)
        view = self.store.read()
        self.assertTrue(all(a['provenance'] == 'legacy_import' and a['read_only'] for a in view['accounts']))
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.update(1, [upsert('accounts', id='acct-providera', name='x', max_connections=3)])
        self.assertEqual(raised.exception.errors[0]['error'], 'read_only_legacy_import')
        self.assertEqual(self.store.update(1, [{'op': 'delete', 'section': 'sources', 'id': 'src-providerb'}, {'op': 'delete', 'section': 'accounts', 'id': 'acct-providerb'}])['revision'], 2)
        clean = settings.SettingsStore(Path(self.temp.name) / 'clean.sqlite')
        view = clean.read()
        self.assertEqual((view['accounts'], view['sources'], view['devices']), ([], [], []))
        self.assertEqual(len(view['profiles']), 1)
        # app-level: no credential file => no provider imported; 'off' never imports
        secrets_dir = Path(self.temp.name) / 'sec'
        secrets_dir.mkdir()
        with patch.object(app, 'SECRETS', secrets_dir):
            self.assertEqual(app.legacy_providers(), [])
            (secrets_dir / 'providera.json').write_text('{}')
            self.assertEqual([p['id'] for p in app.legacy_providers()], ['providera'])
            with patch.dict(os.environ, {'SGSPORTS_LEGACY_IMPORT': 'off'}):
                self.assertEqual(app.legacy_providers(), [])

    def test_migration_ids_disambiguate_collisions_and_skip_invalid_keys(self):
        providers = [{'id': 'A.b', 'name': 'One'}, {'id': 'a-b', 'name': 'Two'}, {'id': 'A-B', 'name': 'Three'}, {'id': 'a-b', 'name': 'dup'},
                     {'id': '', 'name': 'empty'}, {'id': 7}, {'id': '../x', 'name': 'Bad', 'max_connections': 'many'}, {'id': 'z' * 90}]
        store = settings.SettingsStore(Path(self.temp.name) / 'ids.sqlite', providers=providers)
        view = store.read()
        ids = [a['id'] for a in view['accounts']]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        self.assertIn('acct-a-b', ids)
        self.assertTrue(all(settings.ID_RE.match(i) for i in ids))
        again = settings.SettingsStore(Path(self.temp.name) / 'ids2.sqlite', providers=providers).read()
        self.assertEqual(ids, [a['id'] for a in again['accounts']])
        bad = next(a for a in view['accounts'] if a['name'] == 'Bad')
        self.assertEqual(bad['max_connections'], 1)
        self.assertFalse(bad['credential_configured'])
        long = next(a for a in view['accounts'] if a['name'].startswith('z'))
        self.assertFalse(long['credential_configured'])

    def test_device_registry_follows_pairing_store(self):
        k1, k2 = 'ab' * 32, 'cd' * 32
        store = settings.SettingsStore(Path(self.temp.name) / 'dev.sqlite')
        self.assertEqual(store.read()['devices'], [])
        store.sync_devices({k1: {'expires': 2000}, 'not-a-digest': {'expires': 5}}, now=1000)  # pair after init; invalid key ignored
        devices = store.read()['devices']
        self.assertEqual([(d['id'], d['state'], d['expires']) for d in devices], [('dev-' + k1[:16], 'active', 2000)])
        revision = store.read()['revision']
        store.sync_devices({k1: {'expires': 2000}}, now=1000)
        self.assertEqual(store.read()['revision'], revision)  # idempotent
        store.update(revision, [upsert('devices', id='dev-' + k1[:16], name='Den TV')])
        store.sync_devices({k1: {'expires': 2000}, k2: {'expires': 3000}}, now=2500)  # k1 expired, k2 new
        by_id = {d['id']: d for d in store.read()['devices']}
        self.assertEqual((by_id['dev-' + k1[:16]]['state'], by_id['dev-' + k1[:16]]['name']), ('expired', 'Den TV'))
        self.assertEqual(by_id['dev-' + k2[:16]]['state'], 'active')
        store.sync_devices({k2: {'expires': 3000}}, now=2500)  # k1 unpaired
        self.assertEqual({d['id']: d['state'] for d in store.read()['devices']}, {'dev-' + k1[:16]: 'unpaired', 'dev-' + k2[:16]: 'active'})
        restarted = settings.SettingsStore(Path(self.temp.name) / 'dev.sqlite', paired_devices={'ef' * 32: {}})
        self.assertEqual(len(restarted.read()['devices']), 2)  # restart keeps registry; no re-import
        restarted.sync_devices({k2: {'expires': 3000}}, now=2500)
        text = json.dumps([restarted.read(), restarted.client_view(), restarted.export()])
        self.assertNotIn(k1, text)
        self.assertNotIn(k2, text)
        with self.assertRaises(settings.SettingsError):
            restarted.update(restarted.read()['revision'], [upsert('devices', id='dev-' + k2[:16], state='active')])

    def test_route_syncs_with_real_tv_devices_cache(self):
        key = 'ab' * 32
        with patch.object(app, 'read_cache', return_value={key: {'expires': 4102444800}}):
            devices = app.settings_route(False, [])[1]['devices']
        self.assertEqual([(d['id'], d['state']) for d in devices], [('dev-' + key[:16], 'active')])
        with patch.object(app, 'read_cache', return_value={}):
            devices = app.settings_route(False, [])[1]['devices']
        self.assertEqual([d['state'] for d in devices], ['unpaired'])

    def test_body_parsing_is_bounded_before_reading(self):
        class Headers(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)

        class Reader:
            def __init__(self, data):
                self.data, self.calls = data, []
            def read(self, n=-1):
                self.calls.append(n)
                return self.data[:n]
        good = json.dumps({'revision': 1, 'ops': []}).encode()
        j = 'application/json'
        cases = [({'Content-Type': j, 'Content-Length': '-1'}, 400), ({'Content-Type': j, 'Content-Length': 'abc'}, 400),
                 ({'Content-Type': j, 'Content-Length': '1.5'}, 400), ({'Content-Type': j}, 400), ({'Content-Type': j, 'Content-Length': '0'}, 400),
                 ({'Content-Type': j, 'Content-Length': str(app.SETTINGS_BODY_LIMIT + 1)}, 413),
                 ({'Content-Type': 'text/plain', 'Content-Length': str(len(good))}, 415), ({'Content-Length': str(len(good))}, 415)]
        secrets_dir = Path(self.temp.name) / 'secrets2'
        secrets_dir.mkdir()
        admin = adminfix.admin_headers(app.DATA)
        for headers, expected in cases:
            sent, reader = [], Reader(good)

            class Fake(app.Handler):
                def __init__(self):
                    self.path, self.headers, self.rfile = '/v1/settings', Headers({**headers, **admin}), reader
                def send(self, status, data):
                    sent.append(status)
            with patch.object(app, 'SECRETS', secrets_dir):
                Fake().dispatch(True)
            self.assertEqual(sent, [expected], headers)
            self.assertEqual(reader.calls, [], headers)  # nothing read, and never read(-1)
        reader = Reader(good)
        self.assertEqual(app.parse_settings_body(Headers({'Content-Type': 'application/json; charset=utf-8', 'Content-Length': str(len(good))}), reader), good)
        self.assertEqual(reader.calls, [len(good)])
        with self.assertRaises(ValueError):
            app.parse_settings_body(Headers({'Content-Type': j, 'Content-Length': str(len(good) + 5)}), Reader(good))


if __name__ == '__main__':
    unittest.main()
