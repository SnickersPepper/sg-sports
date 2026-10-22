"""Phase 7A M2A contract tests: portable setup, direct inputs, shared budgets, atomic restore. Synthetic data only."""
import io
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import adminfix
import settings

M3U_URL = 'https://iptv.example.invalid/get.php?username=demo&password=s3cret-m3u'
XMLTV_URL = 'https://iptv.example.invalid/xmltv.php?username=demo&password=s3cret-xml'
XTREAM = json.dumps({'host': 'https://xc.example.invalid', 'username': 'demo-user', 'password': 's3cret-xc'})
SECRET_TEXTS = ['s3cret-m3u', 's3cret-xml', 's3cret-xc', 'demo-user', 'xc.example.invalid', 'iptv.example.invalid']
ROOT = Path(app.__file__).resolve().parent


def up(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


def configure(store):
    """Synthetic direct M3U + XMLTV, an Xtream account and an IPTVBoss export."""
    view = store.update(1, [
        up('accounts', id='acct-main', name='Main subscription', max_connections=2, credential=XTREAM),
        up('sources', id='src-direct', name='Direct M3U', kind='m3u', location_type='url', format='m3u', location=M3U_URL, account_id='acct-main'),
        up('sources', id='src-boss', name='IPTVBoss export', kind='iptvboss_export', location_type='file', location='layout.m3u', account_id='acct-main'),
        up('sources', id='src-xc', name='Xtream', kind='xtream', account_id='acct-main'),
        up('guide_sources', id='guide-main', name='Guide', kind='xmltv', location_type='url', format='xml.gz', location=XMLTV_URL),
        up('guide_mappings', id='gmap-one', guide_source_id='guide-main', source_id='src-direct', source_channel='1', guide_channel='espn.us')])
    return view


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 's.sqlite'
        self.store = settings.SettingsStore(self.path)

    def secrets_in(self, value):
        text = json.dumps(value)
        for secret in SECRET_TEXTS:
            self.assertNotIn(secret, text)

    def test_clean_install_has_no_provider_defaults_and_direct_inputs_round_trip(self):
        view = self.store.read()
        self.assertEqual([view[s] for s in ('accounts', 'sources', 'guide_sources', 'guide_mappings', 'integrations')], [[]] * 5)
        view = configure(self.store)
        by_id = {s['id']: s for s in view['sources']}
        self.assertTrue(by_id['src-direct']['location_configured'] and by_id['src-boss']['location_configured'])
        self.assertEqual(by_id['src-boss']['kind'], 'iptvboss_export')
        self.assertTrue(view['guide_sources'][0]['location_configured'])
        self.assertTrue(view['accounts'][0]['credential_configured'])
        self.secrets_in([view, self.store.export(), self.store.client_view(), self.store.budgets(), self.store.setup_status()])
        self.assertEqual(self.store.secret(self.store._load(sqlite3.connect(self.path))[1]['sources'][0]['location_ref']), M3U_URL)

    def test_add_edit_remove_keeps_secret_when_blank_and_clears_on_null(self):
        configure(self.store)
        rev = self.store.read()['revision']
        view = self.store.update(rev, [up('sources', id='src-direct', name='Renamed')])
        self.assertTrue(next(s for s in view['sources'] if s['id'] == 'src-direct')['location_configured'])
        view = self.store.update(rev + 1, [up('sources', id='src-direct', location=None)])
        self.assertFalse(next(s for s in view['sources'] if s['id'] == 'src-direct')['location_configured'])
        view = self.store.update(rev + 2, [{'op': 'delete', 'section': 'sources', 'id': 'src-boss'}])
        self.assertEqual([s['id'] for s in view['sources']], ['src-direct', 'src-xc'])
        with sqlite3.connect(self.path) as db:  # removed/cleared secret values are purged
            values = [r[0] for r in db.execute('SELECT value FROM secrets')]
        self.assertNotIn('layout.m3u', values)
        self.assertNotIn(M3U_URL, values)

    def test_location_validation_is_classified_and_never_echoes_values(self):
        bad = [(dict(location_type='url', location='ftp://x.invalid/a'), 'invalid_url'),
               (dict(location_type='url', location='not a url with secret-token'), 'invalid_url'),
               (dict(location_type='file', location='../../etc/passwd'), 'invalid_file_reference'),
               (dict(location_type='file', location='/etc/passwd'), 'invalid_file_reference'),
               (dict(location_type='file', location='list.txt'), 'invalid_file_reference'),
               (dict(location='list.m3u'), 'invalid_url')]
        rev = self.store.read()['revision']
        for fields, code in bad:
            with self.assertRaises(settings.SettingsError) as caught:
                self.store.update(rev, [up('sources', name='n', kind='m3u', **fields)])
            self.assertEqual(caught.exception.errors[0]['error'], code)
            self.assertNotIn(fields.get('location', 'x'), json.dumps(caught.exception.errors))
        with self.assertRaises(settings.SettingsError) as caught:
            self.store.update(rev, [up('guide_sources', name='g', kind='xmltv', location_type='file', location='guide.m3u')])
        self.assertEqual(caught.exception.errors[0]['error'], 'invalid_file_reference')
        self.store.update(rev, [up('guide_sources', name='g', kind='xmltv', location_type='file', location='guide.xml.gz', format='xml.gz')])
        with self.assertRaises(settings.SettingsError) as caught:
            self.store.update(rev + 1, [up('accounts', name='a', max_connections=1, credential='{"host":"https://h.invalid","username":"u"}')])
        self.assertEqual(caught.exception.errors[0]['error'], 'invalid_xtream_credential')
        self.assertEqual(self.store.read()['revision'], rev + 1)
        for kind in (dict(format='mp4'), dict(location_type='ssh')):
            with self.assertRaises(settings.SettingsError):
                self.store.update(rev + 1, [up('sources', name='n', kind='m3u', **kind)])
        with self.assertRaises(settings.SettingsError) as caught:
            self.store.update(rev + 1, [up('sources', name='n', kind='xtream', location_type='url', location='https://x.invalid/a')])
        self.assertEqual(caught.exception.errors[0]['error'], 'location_not_used_for_xtream')

    def test_shared_budget_and_unresolved_sources_are_conservative(self):
        view = configure(self.store)
        budgets = self.store.budgets()
        main = next(a for a in budgets['accounts'] if a['account_id'] == 'acct-main')
        self.assertEqual((main['max_connections'], sorted(main['source_ids'])), (2, ['src-boss', 'src-direct', 'src-xc']))  # one shared allowance
        self.assertEqual(budgets['unresolved_source_ids'], [])
        rev = view['revision']
        # a duplicate import with no mapping, and a merged export that names no accounts, stay unresolved; names never infer allowance
        self.store.update(rev, [up('sources', id='src-dup', name='Main subscription', kind='m3u'),
                                up('sources', id='src-merged', name='Merged layout', kind='iptvboss_export', merged=True, account_id='acct-main')])
        budgets = self.store.budgets()
        self.assertEqual(sorted(budgets['unresolved_source_ids']), ['src-dup', 'src-merged'])
        self.assertEqual((budgets['unresolved_allowance'], budgets['accounts'][0]['max_connections']), (0, 2))
        states = {r['source_id']: r for r in self.store.setup_status()['sources']}
        self.assertEqual((states['src-dup']['budget'], states['src-dup']['states']['configured']), ('unresolved', 'unresolved'))
        self.assertEqual(states['src-direct']['states'], {'configured': 'done', 'catalog_imported': 'not_started', 'guide_mapped': 'done',
                                                            'transport_tested': 'not_started', 'game_confirmed': 'not_started'})
        self.assertEqual(states['src-boss']['states']['guide_mapped'], 'not_started')
        # explicit complete mapping resolves the merged export onto both real accounts
        rev = self.store.read()['revision']
        self.store.update(rev, [up('accounts', id='acct-two', name='Second', max_connections=1),
                                up('sources', id='src-merged', account_ids=['acct-main', 'acct-two'])])
        budgets = self.store.budgets()
        self.assertNotIn('src-merged', budgets['unresolved_source_ids'])
        self.assertIn('src-merged', next(a for a in budgets['accounts'] if a['account_id'] == 'acct-two')['source_ids'])
        with self.assertRaises(settings.SettingsError):
            self.store.update(self.store.read()['revision'], [up('sources', id='src-merged', account_ids=['acct-missing'])])

    def test_favorites_reorder_remove_empty_distinct_ids_and_profile_isolation(self):
        favorites = ['soccer.men.fc-barcelona', 'soccer.women.fc-barcelona'] + ['nfl.team-%d' % n for n in range(8)]
        self.assertGreater(len(favorites), 7)
        view = self.store.update(1, [up('profiles', id='prof-guest', name='Guest', preferences={'favorites': favorites[:3]}),
                                     up('profiles', id='prof-household', name='Household', preferences={'favorites': favorites})])
        prefs = {p['id']: p['preferences']['favorites'] for p in view['profiles']}
        self.assertEqual(prefs['prof-household'], favorites)
        self.assertEqual(len(set(prefs['prof-household'])), 10)  # men's/women's canonical IDs stay distinct
        rev = view['revision']
        view = self.store.update(rev, [up('profiles', id='prof-household', preferences={'favorites': list(reversed(favorites))})])
        self.assertEqual({p['id']: p['preferences']['favorites'] for p in view['profiles']}['prof-household'][0], 'nfl.team-7')
        view = self.store.update(rev + 1, [up('profiles', id='prof-household', preferences={'favorites': favorites[2:]})])
        self.assertEqual({p['id']: p['preferences']['favorites'] for p in view['profiles']}['prof-guest'], favorites[:3])  # isolation
        view = self.store.update(rev + 2, [up('profiles', id='prof-household', preferences={'favorites': []})])
        self.assertEqual({p['id']: p['preferences']['favorites'] for p in view['profiles']}['prof-household'], [])
        # source changes do not touch profile data
        before = self.store.read()['profiles']
        configure_ops = [up('sources', name='Later', kind='m3u')]
        self.store.update(rev + 3, configure_ops)
        self.assertEqual(self.store.read()['profiles'], before)

    def test_stale_revision_conflict_and_rejected_write_preserve_last_good(self):
        configure(self.store)
        good = self.store.read()
        with self.assertRaises(settings.Conflict):
            self.store.update(1, [up('sources', id='src-direct', name='Stale')])
        with self.assertRaises(settings.SettingsError):
            self.store.update(good['revision'], [up('sources', id='src-direct', name='Half'), up('sources', id='src-boss', location_type='url', location='nope')])
        self.assertEqual(self.store.read(), good)
        self.assertEqual(self.store.secret(self.store._load(sqlite3.connect(self.path))[1]['sources'][0]['location_ref']), M3U_URL)

    def test_restart_persistence(self):
        configure(self.store)
        before = self.store.read()
        reopened = settings.SettingsStore(self.path)
        self.assertEqual(reopened.read(), before)
        self.assertEqual(reopened.budgets(), self.store.budgets())

    def test_export_restore_into_fresh_store_with_separate_secrets(self):
        configure(self.store)
        self.store.update(self.store.read()['revision'], [up('profiles', id='prof-guest', name='Guest', preferences={'favorites': ['soccer.women.fc-barcelona']}),
                                                          up('integrations', id='int-setup-progress', name='Setup progress', kind='setup_progress', options={'skipped': ['guide']})])
        export = self.store.export()
        self.secrets_in(export)
        needed = {'%s/%s/%s' % (n['section'], n['id'], n['field']) for n in export['secrets_required']}
        self.assertEqual(needed, {'accounts/acct-main/credential', 'sources/src-direct/location', 'sources/src-boss/location', 'guide_sources/guide-main/location'})
        supplied = {'accounts/acct-main/credential': XTREAM, 'sources/src-direct/location': M3U_URL, 'sources/src-boss/location': 'layout.m3u',
                    'guide_sources/guide-main/location': XMLTV_URL}
        fresh = settings.SettingsStore(Path(self.temp.name) / 'fresh.sqlite')
        restored = fresh.restore(1, json.loads(json.dumps(export)), supplied)
        self.assertEqual(restored['revision'], 2)
        strip = lambda v: {k: x for k, x in v.items() if k != 'revision'}
        self.assertEqual(strip(restored), strip(self.store.read()))
        self.assertEqual(fresh.budgets()['unresolved_source_ids'], [])
        row = fresh._load(sqlite3.connect(Path(self.temp.name) / 'fresh.sqlite'))[1]
        self.assertEqual(fresh.secret(row['guide_sources'][0]['location_ref']), XMLTV_URL)
        self.secrets_in(fresh.export())
        # restoring over an existing configuration replaces user-owned items and drops what the export lacks
        fresh.update(2, [up('sources', id='src-extra', name='Extra', kind='m3u')])
        again = fresh.restore(3, json.loads(json.dumps(export)), supplied)
        self.assertNotIn('src-extra', [s['id'] for s in again['sources']])

    def test_invalid_and_partial_restore_roll_back_everything(self):
        configure(self.store)
        export = self.store.export()
        supplied = {'accounts/acct-main/credential': XTREAM, 'sources/src-direct/location': M3U_URL, 'sources/src-boss/location': 'layout.m3u',
                    'guide_sources/guide-main/location': XMLTV_URL}
        target = settings.SettingsStore(Path(self.temp.name) / 't.sqlite')
        target.update(1, [up('sources', id='src-keep', name='Keep', kind='m3u', location_type='url', location='https://keep.example.invalid/k.m3u')])
        good = target.read()
        with sqlite3.connect(Path(self.temp.name) / 't.sqlite') as db:
            secrets_before = db.execute('SELECT id,value FROM secrets ORDER BY id').fetchall()
        copy = lambda **patch_: {**json.loads(json.dumps(export)), **patch_}
        newer = copy(schema_version=settings.SCHEMA_VERSION + 1)
        dup = json.loads(json.dumps(export)); dup['sources'].append(dup['sources'][0])
        dangling = json.loads(json.dumps(export)); dangling['sources'][0]['account_id'] = 'acct-nope'
        badfield = json.loads(json.dumps(export)); badfield['sources'][0]['bogus'] = 1
        badsecret = {**supplied, 'sources/src-direct/location': 'ftp://nope.invalid/x'}
        cases = [(newer, supplied, 'incompatible_schema_newer'), (copy(schema_version='1'), supplied, 'invalid_schema_version'),
                 (export, {k: v for k, v in supplied.items() if 'boss' not in k}, 'missing_secret'),
                 (export, {**supplied, 'accounts/acct-x/credential': 'v'}, 'unexpected_secret'),
                 (dup, supplied, 'duplicate_id'), (dangling, supplied, 'unknown_account'), (badfield, supplied, 'unknown_field'),
                 (export, badsecret, 'invalid_url'), (copy(mystery=[]), supplied, 'unknown_section'),
                 (copy(sources='nope'), supplied, 'list_required'), ([], supplied, 'object_required'), (export, [], 'object_required')]
        for payload, secrets_, code in cases:
            with self.assertRaises(settings.SettingsError) as caught:
                target.restore(2, payload, secrets_)
            self.assertIn(code, [e['error'] for e in caught.exception.errors], code)
            self.secrets_in(caught.exception.errors)
            self.assertEqual(target.read(), good, code)
            with sqlite3.connect(Path(self.temp.name) / 't.sqlite') as db:
                self.assertEqual(db.execute('SELECT id,value FROM secrets ORDER BY id').fetchall(), secrets_before, code)
        with self.assertRaises(settings.Conflict):
            target.restore(1, export, supplied)
        self.assertEqual(target.read(), good)

    def test_restore_preserves_pairing_and_legacy_records_and_does_not_import_them(self):
        key = 'ab' * 32
        legacy = settings.SettingsStore(Path(self.temp.name) / 'l.sqlite', providers=[{'id': 'providera', 'name': 'Legacy', 'max_connections': 1}], paired_devices={key: {'expires': 4102444800}})
        legacy.update(1, [up('profiles', id='prof-household', name='Household', preferences={'favorites': ['a.b']}), up('sources', id='src-new', name='New', kind='m3u', account_id='acct-providera')])
        export = legacy.export()
        legacy.restore(2, export, {})
        view = legacy.read()
        self.assertEqual([d['id'] for d in view['devices']], ['dev-' + key[:16]])
        self.assertEqual([a['id'] for a in view['accounts']], ['acct-providera'])
        self.assertTrue(view['accounts'][0]['read_only'])
        self.assertEqual(view['profiles'][0]['preferences']['favorites'], ['a.b'])
        self.assertIn('src-new', [s['id'] for s in view['sources']])
        # a restore into a store lacking that legacy account fails by reference, not silently
        with self.assertRaises(settings.SettingsError) as caught:
            self.store.restore(1, export, {})
        self.assertIn('unknown_account', [e['error'] for e in caught.exception.errors])

    def test_owned_dvr_data_is_not_touched_by_settings_changes(self):
        dvr_file = Path(self.temp.name) / 'dvr.json'
        dvr_file.write_text('{"rules":[{"id":"r1"}]}')
        configure(self.store)
        self.store.restore(self.store.read()['revision'], self.store.export(), {
            'accounts/acct-main/credential': XTREAM, 'sources/src-direct/location': M3U_URL, 'sources/src-boss/location': 'layout.m3u', 'guide_sources/guide-main/location': XMLTV_URL})
        self.assertEqual(dvr_file.read_text(), '{"rules":[{"id":"r1"}]}')
        self.assertNotIn('dvr', json.dumps(self.store.read()).lower())  # settings neither hold nor erase DVR rules


class RouteAndPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(app, 'DATA', Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        app.SETTINGS.clear()

    def call(self, method, path, body=None, token='admintoken'):
        sent = {}
        data = json.dumps(body).encode() if body is not None else b''

        class Headers(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)
        headers = Headers({'Content-Type': 'application/json', 'Content-Length': str(len(data))})
        if token == 'admintoken':
            headers.update(adminfix.admin_headers(app.DATA))
        elif token:
            headers['Authorization'] = 'Bearer ' + token

        class Fake(app.Handler):
            def __init__(inner):
                inner.path, inner.headers, inner.rfile, inner.wfile = path, headers, io.BytesIO(data), io.BytesIO()
            def send(inner, status, payload):
                sent['status'], sent['body'] = status, payload
            def send_response(inner, status):
                sent['status'] = status
            def send_header(inner, key, value):
                sent.setdefault('headers', {})[key] = value
            def end_headers(inner):
                pass
        secrets_dir = Path(self.temp.name) / 'sec'
        secrets_dir.mkdir(exist_ok=True)
        fake = None
        with patch.object(app, 'SECRETS', secrets_dir):
            fake = Fake()
            fake.dispatch(method == 'POST')
        if 'body' not in sent:
            sent['body'] = fake.wfile.getvalue()
        return sent['status'], sent['body'], sent.get('headers', {})

    def test_root_is_the_console_and_setup_is_a_compatibility_redirect(self):
        # SGH-314: the root address is the whole administration console; /setup (with or without a slash or query) only redirects to it.
        status, base, headers = self.call('GET', '/', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(base, (ROOT / 'web' / 'setup.html').read_bytes())
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        for path in ('/setup', '/setup/', '/setup?from=tv'):
            status, _, headers = self.call('GET', path, token=None)
            self.assertEqual((status, headers['Location']), (302, '/'), path)

    def test_root_shows_no_consumer_sports_content(self):
        page = self.call('GET', '/', token=None)[1].decode()
        for word in ('Barcelona', 'Feed candidates', 'KICKOFF', 'Game day', 'scoreboard'):
            self.assertNotIn(word, page)
        for gone in ('/screen.js', '/screen.css'):
            self.assertNotEqual(self.call('GET', gone, token=None)[0], 200, gone)

    def test_console_is_organised_by_task_with_required_and_optional_marked(self):
        page = (ROOT / 'web' / 'setup.html').read_text()
        for heading in ('Configuration home', 'Server identity and TV pairing', 'IPTV playlist sources', 'Guide sources (XMLTV)', 'IPTVBoss and Dispatcharr (optional)',
                        'Profiles and favorite teams', 'DVR (Jellyfin) and storage', 'Artwork (optional)', 'Backup, restore, import and export', 'Diagnostics and update information'):
            self.assertIn(heading, page)
        self.assertIn('<option value="off">None', page)  # artwork: the first, default choice is Off
        self.assertLess(page.index('value="off"'), page.index('value="espn"'))
        self.assertIn('Skip to content', page)

    def test_static_assets_are_secret_free_and_script_src_self_only(self):
        for path, kind in (('/', 'text/html'), ('/setup.js', 'text/javascript'), ('/prefs-logic.js', 'text/javascript'), ('/setup.css', 'text/css')):
            status, body, headers = self.call('GET', path, token=None)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers['Content-Type'].startswith(kind))
            self.assertEqual(body, (ROOT / 'web' / ('setup.html' if path == '/' else path[1:])).read_bytes())
            for secret in SECRET_TEXTS:
                self.assertNotIn(secret.encode(), body)
        html = (ROOT / 'web' / 'setup.html').read_text()
        js = (ROOT / 'web' / 'setup.js').read_text()
        self.assertEqual(re.findall(r'<script[^>]*>', html), ['<script src="/prefs-logic.js">', '<script src="/setup.js">'])  # no inline script
        self.assertNotRegex(html, r'\son[a-z]+=')
        self.assertIn("script-src 'self'", self.call('GET', '/', token=None)[2]['Content-Security-Policy'])
        logic = (ROOT / 'web' / 'prefs-logic.js').read_text()
        self.assertNotIn('innerHTML', js + logic)
        self.assertNotIn('localStorage', js)
        self.assertNotIn('sessionStorage', js)
        self.assertEqual(len(re.findall(r'type="password"', html)), 15)  # every secret input is write-only (incl. the logo-server base address; plus sign-in, migration and password-change fields)
        for needed in ('lang="en"', 'name="viewport"', 'aria-live', 'IPTVBoss export (recommended path)', 'Skip the rest for now', 'Transport tested', 'Game confirmed', 'Catalog imported', 'Guide mapped'):
            self.assertIn(needed, html)
        for element_id in set(re.findall(r"\$\('([a-z0-9-]+)'\)", js)):
            self.assertIn('id="%s"' % element_id, html, element_id)
        for ref in ('/v1/settings/restore', '/v1/settings/setup-status', '/v1/settings/export'):
            self.assertIn(ref, js)

    def test_setup_flow_through_http_handler_requires_token_and_never_leaks(self):
        self.assertEqual(self.call('GET', '/v1/settings/setup-status', token='wrong')[0], 401)
        self.assertEqual(self.call('POST', '/v1/settings/restore', {'revision': 1}, token=None)[0], 401)
        status, view, _ = self.call('GET', '/v1/settings')
        self.assertEqual((status, view['revision'], view['sources']), (200, 1, []))
        ops = [up('accounts', id='acct-main', name='Main', max_connections=1, credential=XTREAM),
               up('sources', id='src-direct', name='Direct', kind='m3u', location_type='url', location=M3U_URL, account_id='acct-main'),
               up('guide_sources', id='guide-main', name='Guide', kind='xmltv', location_type='url', location=XMLTV_URL)]
        status, saved, _ = self.call('POST', '/v1/settings', {'revision': 1, 'ops': ops})
        self.assertEqual(status, 200)
        self.assertEqual(self.call('POST', '/v1/settings', {'revision': 1, 'ops': ops})[0], 409)
        bad = self.call('POST', '/v1/settings', {'revision': 2, 'ops': [up('sources', id='src-direct', location_type='url', location='ftp://x.invalid/s3cret-m3u')]})
        self.assertEqual((bad[0], bad[1]['details'][0]['error']), (422, 'invalid_url'))
        outputs = [saved, bad[1]]
        for sub in ('', '/export', '/budgets', '/setup-status', '/client'):
            status, payload, _ = self.call('GET', '/v1/settings' + sub)
            self.assertEqual(status, 200, sub)
            outputs.append(payload)
        self.assertEqual(outputs[-3]['unresolved_source_ids'], [])
        status, export, _ = self.call('GET', '/v1/settings/export')
        secrets_ = {'accounts/acct-main/credential': XTREAM, 'sources/src-direct/location': M3U_URL, 'guide_sources/guide-main/location': XMLTV_URL}
        status, restored, _ = self.call('POST', '/v1/settings/restore', {'revision': 2, 'export': export, 'secrets': secrets_})
        self.assertEqual(status, 200)
        outputs.append(restored)
        status, failed, _ = self.call('POST', '/v1/settings/restore', {'revision': 3, 'export': export, 'secrets': {}})
        self.assertEqual((status, failed['error']), (422, 'invalid_settings'))
        outputs.append(failed)
        status, stale, _ = self.call('POST', '/v1/settings/restore', {'revision': 1, 'export': export, 'secrets': secrets_})
        self.assertEqual((status, stale['revision']), (409, 3))
        status, garbage, _ = self.call('POST', '/v1/settings/restore', {'revision': 3, 'export': 'x', 'secrets': secrets_})
        self.assertEqual(status, 422)
        for payload in outputs + [garbage, stale]:
            for secret in SECRET_TEXTS:
                self.assertNotIn(secret, json.dumps(payload))

    def test_no_filesystem_read_primitive_in_settings_code(self):
        for name in ('settings.py',):
            text = (ROOT / name).read_text()
            self.assertNotRegex(text, r'(?<![\w.])open\(|read_text|read_bytes|urlopen|requests\.|socket', name)


if __name__ == '__main__':
    unittest.main()
