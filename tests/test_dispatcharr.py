"""Phase 7A M3A contract tests: optional Dispatcharr standard outputs, read contract, mappings and staged route switching.

Synthetic data and a fake transport only: no network, no Dispatcharr instance, no media request. A passing run proves the
configuration contract and the read-only classification logic; it does not prove D3 capacity safety, a pinned release's
schema, or any behaviour of a real Dispatcharr (see PHASE7A-M3A.md for the explicit limits).
"""
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import dispatcharr
import settings
import test_setup

BASE = 'https://dispatcharr.example.invalid:9191'
KEY = 'dk-synthetic-key-0001'
KEY2 = 'dk-synthetic-key-0002'
PLAYLIST_URL = BASE + '/output/m3u/sports-' + 'tokenabc123'
GUIDE_URL = BASE + '/output/epg/sports-' + 'tokenabc123'
SECRET_TEXTS = [KEY, KEY2, 'dispatcharr.example.invalid', 'tokenabc123', '0d8c6f2e-1111-4222-8333-444455556666']
NOW = 1_800_000_000

PROXIED = ('#EXTM3U\n'
           '#EXTINF:-1 tvg-id="espn.us" tvg-name="ESPN" group-title="Sports",ESPN\n' + BASE + '/proxy/ts/stream/0d8c6f2e-1111-4222-8333-444455556666\n'
           '#EXTINF:-1 tvg-id="fs1.us" tvg-name="FS1",FS1\n' + BASE + '/proxy/ts/stream/aaaaaaaa-1111-4222-8333-444455556666\n')
DIRECT = ('#EXTM3U\n#EXTINF:-1 tvg-id="espn.us",ESPN\nhttp://provider.example.invalid:8080/live/user/pass/1.ts\n')
MIXED = PROXIED + '#EXTINF:-1 tvg-id="x.us",X\nhttp://provider.example.invalid:8080/live/user/pass/2.ts\n'
GUIDE = b'<?xml version="1.0"?><tv><channel id="espn.us"><display-name>ESPN</display-name></channel><channel id="other.us"/></tv>'


def up(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


def delete(section, item_id):
    return {'op': 'delete', 'section': section, 'id': item_id}


def api_ok(version='0.32.0', **status):
    base = {dispatcharr.VERSION_PATH: (200, json.dumps({'version': version}).encode())}
    for name, path in dispatcharr.CAPABILITY_PATHS.items():
        base[path] = (status.get(name, 200), b'{"results": [{"url": "http://private.invalid/secret-stream"}]}')
    return {BASE + path: value for path, value in base.items()}


class Fake:
    """Records every request; anything not routed answers 404. A routed Exception is raised."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, dict(headers)))
        entry = self.routes.get(url, (404, b''))
        if isinstance(entry, Exception):
            raise entry
        return entry


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 's.sqlite'
        self.store = settings.SettingsStore(self.path, paired_devices={'cd' * 32: {'expires': 4_000_000_000}})

    def rev(self):
        return self.store.read()['revision']

    def build(self):
        """Direct and Dispatcharr-mediated imports of the same subscription, with an inactive mediated route."""
        self.store.update(self.rev(), [
            up('accounts', id='acct-a', name='Provider A', max_connections=1),
            up('accounts', id='acct-b', name='Provider B', max_connections=2),
            up('profiles', id='prof-household', preferences={'favorites': ['team.soccer.men.la-liga.fc-barcelona']}),
            up('integrations', id='int-disp', name='Dispatcharr', kind='dispatcharr', base_url=BASE, api_key=KEY),
            up('sources', id='src-direct', name='Direct', kind='m3u', location_type='url', location='https://iptv.example.invalid/get.php?u=1', account_id='acct-a'),
            up('sources', id='src-disp', name='Via Dispatcharr', kind='m3u', middleware='dispatcharr', integration_id='int-disp', location_type='url',
               location=PLAYLIST_URL, account_id='acct-a'),
            up('guide_sources', id='guide-disp', name='Dispatcharr guide', kind='xmltv', middleware='dispatcharr', integration_id='int-disp', location_type='url', location=GUIDE_URL, format='xml'),
            up('routes', id='route-direct', name='Direct', kind='direct', account_ids=['acct-a'], source_ids=['src-direct'], enabled=True),
            up('routes', id='route-disp', name='Mediated', kind='dispatcharr', integration_id='int-disp', account_ids=['acct-a'], source_ids=['src-disp'], enabled=False)])
        return self.store.read()

    def assertNoSecrets(self, value):
        text = json.dumps(value)
        for secret in SECRET_TEXTS:
            self.assertNotIn(secret, text)

    def item(self, section, item_id):
        return next(i for i in self.store.read()[section] if i['id'] == item_id)


class CleanAndMigration(Base):
    def test_clean_install_has_no_dispatcharr_and_core_paths_work(self):
        view = self.store.read()
        self.assertEqual(view['schema_version'], 4)
        for section in ('integrations', 'routes', 'channel_mappings', 'capability_snapshots'):
            self.assertEqual(view[section], [])
        self.assertEqual(self.store.dispatcharr_status(NOW)['integrations'], [])
        view = self.store.update(1, [up('sources', id='src-plain', name='Plain', kind='m3u', location_type='url', location='https://iptv.example.invalid/a.m3u')])
        self.assertTrue(view['sources'][0]['location_configured'])
        self.assertNotIn('delivery', self.store.setup_status()['sources'][0])

    def test_schema_2_database_migrates_without_data_loss(self):
        old = Path(self.temp.name) / 'old.sqlite'
        v2 = settings.SettingsStore(old, migrations={1: settings.MIGRATIONS[1], 2: settings.MIGRATIONS[2]})
        v2.update(1, [up('profiles', id='prof-keep', name='Keep', preferences={'favorites': ['team.soccer.men.la-liga.fc-barcelona']})])
        self.assertEqual(v2.schema_version(), 2)
        raw = sqlite3.connect(old)  # a genuine version-2 document predates the mapping section
        doc = json.loads(raw.execute('SELECT doc FROM config').fetchone()[0])
        doc.pop('channel_mappings')
        raw.execute('UPDATE config SET doc=?', (json.dumps(doc),))
        raw.commit()
        raw.close()
        new = settings.SettingsStore(old)
        view = new.read()
        self.assertEqual((new.schema_version(), view['revision']), (4, 2))
        self.assertEqual(view['channel_mappings'], [])
        self.assertEqual([p['id'] for p in view['profiles']], ['prof-household', 'prof-keep'])
        new.update(2, [up('integrations', name='D', kind='dispatcharr', base_url=BASE, api_key=KEY)])
        self.assertEqual(len(settings.SettingsStore(old).read()['integrations']), 1)


class GuideCheck(Base):
    def test_check_guide_reports_channels_without_importing_or_leaking_the_address(self):
        self.store.update(1, [up('guide_sources', id='guide-x', name='G', kind='xmltv', location_type='url', location=GUIDE_URL, format='xml')])
        body = dispatcharr.check_guide(self.store, 'guide-x', Fake({GUIDE_URL: (200, GUIDE)}))
        self.assertEqual(body['status'], 'ok')
        self.assertGreaterEqual(body['channels'], 1)
        self.assertNotIn(GUIDE_URL, json.dumps(body))
        self.assertEqual(dispatcharr.check_guide(self.store, 'guide-x', Fake({GUIDE_URL: (503, b'')})), {'status': 'unavailable', 'reason': 'guide_http_503'})
        self.assertEqual(dispatcharr.check_guide(self.store, 'guide-x', Fake({GUIDE_URL: (200, b'<!DOCTYPE x><tv/>')}))['status'], 'unreadable')


class StandardOutputMode(Base):
    def test_no_key_standard_output_is_classified_without_management_access_or_stream_requests(self):
        self.store.update(1, [up('accounts', id='acct-a', name='A', max_connections=1),
                              up('sources', id='src-disp', name='Via', kind='m3u', middleware='dispatcharr', location_type='url', location=PLAYLIST_URL, account_id='acct-a'),
                              up('guide_sources', id='guide-disp', name='G', kind='xmltv', middleware='dispatcharr', location_type='url', location=GUIDE_URL, format='xml')])
        self.assertEqual(self.store.read()['integrations'], [])
        fake = Fake({PLAYLIST_URL: (200, PROXIED.encode()), GUIDE_URL: (200, GUIDE)})
        body = dispatcharr.assess_source(self.store, 'src-disp', 'guide-disp', fake, NOW)
        self.assertEqual([c[0] for c in fake.calls], [PLAYLIST_URL, GUIDE_URL])  # metadata only: no /proxy/ stream URL was requested
        for _, headers in fake.calls:
            self.assertNotIn('X-API-Key', headers)
            self.assertNotIn('Authorization', headers)
        self.assertEqual((body['entries'], body['delivery_profile'], body['catalog']), (2, 'proxy_path', 'imported'))
        self.assertEqual(body['budget_assurance'], 'not_assured')
        self.assertIn('redirect_profile_not_detectable_from_playlist', body['assurance_reasons'])
        self.assertEqual(body['guide'], {'channels': 2, 'playlist_ids': 2, 'matched': 1, 'unmatched': 1})
        row = self.store.setup_status()['sources'][0]
        self.assertEqual(row['states'], {'configured': 'done', 'catalog_imported': 'done', 'guide_mapped': 'not_started', 'transport_tested': 'not_started', 'game_confirmed': 'not_started'})
        self.assertEqual(row['delivery']['budget_assurance'], 'not_assured')
        self.assertNoSecrets([self.store.read(), self.store.export(), self.store.setup_status()])

    def test_direct_mixed_empty_and_unparseable_outputs_never_claim_assurance(self):
        self.store.update(1, [up('sources', id='src-disp', name='Via', kind='m3u', middleware='dispatcharr', location_type='url', location=PLAYLIST_URL)])
        cases = [(DIRECT, 'direct_or_unknown', 'direct_url_present'), (MIXED, 'mixed', 'direct_url_present'), ('#EXTM3U\n', 'empty', 'no_entries'),
                 ('#EXTM3U\n#EXTINF:-1,X\nrtmp://x.invalid/a\n', 'direct_or_unknown', 'unparseable_entries')]
        for text, profile, reason in cases:
            body = dispatcharr.assess_source(self.store, 'src-disp', None, Fake({PLAYLIST_URL: (200, text.encode())}), NOW)
            self.assertEqual(body['delivery_profile'], profile, text)
            self.assertIn(reason, body['assurance_reasons'])
            self.assertEqual(body['budget_assurance'], 'not_assured')
        dispatcharr.assess_source(self.store, 'src-disp', None, Fake({PLAYLIST_URL: (200, b'#EXTM3U\n')}), NOW)
        self.assertEqual(self.store.setup_status()['sources'][0]['states']['catalog_imported'], 'not_started')  # an empty playlist is not an imported catalog
        self.assertNotEqual(dispatcharr.assess('', None)['catalog'], 'imported')

    def test_failed_fetch_is_visible_and_keeps_catalog_not_imported(self):
        self.store.update(1, [up('sources', id='src-disp', name='Via', kind='m3u', middleware='dispatcharr', location_type='url', location=PLAYLIST_URL)])
        for result in ((500, b''), dispatcharr.TransportError('timeout')):
            body = dispatcharr.assess_source(self.store, 'src-disp', None, Fake({PLAYLIST_URL: result}), NOW)
            self.assertEqual((body['status'], body['catalog']), ('unavailable', 'not_imported'))
        self.assertEqual(self.store.setup_status()['sources'][0]['states']['catalog_imported'], 'not_started')

    def test_guide_parsing_handles_gzip_and_refuses_entities(self):
        self.assertEqual(dispatcharr.guide_channel_ids(gzip.compress(GUIDE)), {'espn.us', 'other.us'})
        with self.assertRaises(ValueError):
            dispatcharr.guide_channel_ids(b'<?xml version="1.0"?><!DOCTYPE tv [<!ENTITY a "b">]><tv/>')
        with self.assertRaises(ValueError):
            dispatcharr.guide_channel_ids(b'<tv><channel')

    def test_non_url_location_cannot_be_assessed_and_changing_a_source_drops_its_old_assessment(self):
        self.store.update(1, [up('sources', id='src-file', name='F', kind='m3u', middleware='dispatcharr', location_type='file', location='out.m3u'),
                              up('sources', id='src-disp', name='Via', kind='m3u', middleware='dispatcharr', location_type='url', location=PLAYLIST_URL)])
        with self.assertRaises(settings.SettingsError):
            dispatcharr.assess_source(self.store, 'src-file', None, Fake({}), NOW)
        dispatcharr.assess_source(self.store, 'src-disp', None, Fake({PLAYLIST_URL: (200, PROXIED.encode())}), NOW)
        self.assertEqual(len(self.store.read()['capability_snapshots']), 1)
        self.store.update(self.rev(), [up('sources', id='src-disp', location=PLAYLIST_URL + '2')])
        self.assertEqual(self.store.read()['capability_snapshots'], [])  # a changed location is unverified again


class ConnectChangeDisconnect(Base):
    def test_connect_change_disconnect_preserve_user_state_and_identity(self):
        self.build()
        before = self.store.read()
        connected = self.item('integrations', 'int-disp')
        self.assertEqual((connected['base_url_configured'], connected['api_key_configured'], connected['generation']), (True, True, 1))
        self.assertNotIn('base_url', connected)
        self.assertNotIn('api_key', connected)
        # change: rotate key and URL on the same identity
        self.store.update(self.rev(), [up('integrations', id='int-disp', api_key=KEY2, base_url=BASE + '/prefix')])
        changed = self.item('integrations', 'int-disp')
        self.assertEqual((changed['id'], changed['generation'], changed['created_at']), ('int-disp', 2, connected['created_at']))
        creds, generation = self.store.integration_credentials('int-disp')
        self.assertEqual((creds['api_key'], creds['base_url'], generation), (KEY2, BASE + '/prefix', 2))
        # a blank change keeps the stored secrets
        self.store.update(self.rev(), [up('integrations', id='int-disp', name='Renamed')])
        self.assertEqual(self.store.integration_credentials('int-disp')[0]['api_key'], KEY2)
        # disconnect is refused while a mediated route is active
        self.store.update(self.rev(), [up('routes', id='route-direct', enabled=False), up('routes', id='route-disp', enabled=True)])
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.disconnect_integration(self.rev(), 'int-disp')
        self.assertEqual(raised.exception.errors[0]['error'], 'active_route_switch_required')
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False), up('routes', id='route-direct', enabled=True)])
        view = self.store.disconnect_integration(self.rev(), 'int-disp')
        self.assertEqual(view['integrations'], [])
        self.assertEqual([r['id'] for r in view['routes']], ['route-direct'])
        self.assertIsNone(next(s for s in view['sources'] if s['id'] == 'src-disp')['integration_id'])
        self.assertEqual(next(s for s in view['sources'] if s['id'] == 'src-disp')['middleware'], 'dispatcharr')  # still a usable standard-output import
        for section in ('profiles', 'accounts', 'devices'):
            self.assertEqual(view[section], before[section], section)
        with sqlite3.connect(self.path) as db:
            refs = {r[0] for r in db.execute('SELECT id FROM secrets')}
        self.assertEqual(len(refs), 3)  # two source/guide locations + the direct source; the API key and base URL are gone
        self.assertNoSecrets([view, self.store.export()])

    def test_invalid_connection_input_leaves_last_good_configuration_intact(self):
        self.build()
        good, creds = self.store.read(), self.store.integration_credentials('int-disp')
        for item in ({'base_url': 'ftp://dispatcharr.example.invalid'}, {'base_url': 'https://u:p@dispatcharr.example.invalid'}, {'base_url': BASE + '/?token=1'},
                     {'base_url': 'not a url'}, {'api_key': 'short'}, {'api_key': 'has space in key 123'}, {'auth_variant': 'bearer'}, {'credential': 'x'},
                     {'options': {'u': 'https://x.invalid'}}):
            with self.assertRaises(settings.SettingsError, msg=item):
                self.store.update(self.rev(), [up('integrations', id='int-disp', **item)])
        with self.assertRaises(settings.SettingsError):
            self.store.update(self.rev(), [up('integrations', id='int-disp', name='Half'), up('routes', id='route-bad', name='B', kind='dispatcharr', integration_id='int-none', account_ids=['acct-a'])])
        self.assertEqual(self.store.read(), good)
        self.assertEqual(self.store.integration_credentials('int-disp'), creds)

    def test_client_cannot_write_server_owned_fields(self):
        self.build()
        for key in ('generation', 'created_at', 'updated_at'):
            with self.assertRaises(settings.SettingsError):
                self.store.update(self.rev(), [up('integrations', id='int-disp', **{key: 9})])
        with self.assertRaises(settings.SettingsError):
            self.store.update(self.rev(), [{'op': 'upsert', 'section': 'capability_snapshots', 'item': {'name': 'x'}}])

    def test_other_integration_kinds_cannot_carry_dispatcharr_fields(self):
        with self.assertRaises(settings.SettingsError):
            self.store.update(1, [up('integrations', name='JF', kind='jellyfin', base_url=BASE)])

    def test_export_restore_round_trip_requires_separate_secrets_and_drops_observations(self):
        self.build()
        dispatcharr.probe_integration(self.store, 'int-disp', Fake(api_ok()), NOW)
        export = self.store.export()
        self.assertNotIn('capability_snapshots', export)
        self.assertNoSecrets(export)
        needed = {'%s/%s/%s' % (n['section'], n['id'], n['field']) for n in export['secrets_required']}
        self.assertIn('integrations/int-disp/api_key', needed)
        fresh = settings.SettingsStore(Path(self.temp.name) / 'fresh.sqlite')
        with self.assertRaises(settings.SettingsError):
            fresh.restore(1, export, {})
        supplied = {'integrations/int-disp/api_key': KEY, 'integrations/int-disp/base_url': BASE,
                    'sources/src-direct/location': 'https://iptv.example.invalid/get.php?u=1', 'sources/src-disp/location': PLAYLIST_URL, 'guide_sources/guide-disp/location': GUIDE_URL}
        view = fresh.restore(1, export, supplied)
        self.assertEqual([i['id'] for i in view['integrations']], ['int-disp'])
        self.assertEqual(fresh.integration_credentials('int-disp')[0]['api_key'], KEY)
        self.assertEqual(view['capability_snapshots'], [])


class MappingsAndGenerations(Base):
    def mapping(self, **override):
        item = dict(canonical_channel_id='channel.espn-us', source_id='src-disp', integration_id='int-disp', dispatcharr_channel='12', dispatcharr_stream='34', account_id='acct-a')
        item.update(override)
        return up('channel_mappings', **item)

    def test_mapping_ids_and_generations_are_stable_and_separate_from_game_ids(self):
        self.build()
        view = self.store.update(self.rev(), [self.mapping(id='cmap-espn')])
        created = next(m for m in view['channel_mappings'] if m['id'] == 'cmap-espn')
        self.assertEqual((created['generation'], created['dispatcharr_channel']), (1, '12'))
        self.store.update(self.rev(), [up('channel_mappings', id='cmap-espn', dispatcharr_stream='35')])
        changed = self.item('channel_mappings', 'cmap-espn')
        self.assertEqual((changed['id'], changed['generation'], changed['created_at'], changed['canonical_channel_id']), ('cmap-espn', 2, created['created_at'], 'channel.espn-us'))
        self.store.update(self.rev(), [up('channel_mappings', id='cmap-espn', dispatcharr_stream='35')])  # no content change: no new generation
        self.assertEqual(self.item('channel_mappings', 'cmap-espn')['generation'], 2)
        generated = self.store.update(self.rev(), [self.mapping(canonical_channel_id='channel.fs1-us', dispatcharr_channel='13')])
        self.assertTrue(all(m['id'].startswith('cmap-') for m in generated['channel_mappings']))
        # restart keeps ids
        self.assertEqual([m['id'] for m in settings.SettingsStore(self.path).read()['channel_mappings']], [m['id'] for m in generated['channel_mappings']])

    def test_mapping_validation(self):
        self.build()
        self.store.update(self.rev(), [self.mapping(id='cmap-espn')])
        bad = [self.mapping(canonical_channel_id='team.soccer.men.la-liga.fc-barcelona', dispatcharr_channel='99'),  # a team id is not a channel id
               self.mapping(canonical_channel_id='game.2026-10-17.x', dispatcharr_channel='98'),
               self.mapping(),  # same channel+source twice
               self.mapping(canonical_channel_id='channel.other', dispatcharr_channel='12'),  # one upstream channel, two canonical channels
               self.mapping(canonical_channel_id='channel.x1', dispatcharr_channel='1', source_id='src-direct'),  # source not linked to the integration
               self.mapping(canonical_channel_id='channel.x2', dispatcharr_channel='2', account_id='acct-b'),  # account not behind the source
               self.mapping(canonical_channel_id='channel.x3', dispatcharr_channel='http://x.invalid'),
               self.mapping(canonical_channel_id='channel.x4', dispatcharr_channel=None)]
        good = self.store.read()
        for op in bad:
            with self.assertRaises(settings.SettingsError, msg=op):
                self.store.update(self.rev(), [op])
        self.assertEqual(self.store.read(), good)

    def test_duplicate_imports_share_one_account_budget_and_mediation_never_copies_an_allowance(self):
        self.build()
        accounts = {a['account_id']: a for a in self.store.budgets()['accounts']}
        self.assertEqual(accounts['acct-a']['max_connections'], 1)
        self.assertEqual(sorted(accounts['acct-a']['source_ids']), ['src-direct', 'src-disp'])  # two imports, one budget
        self.assertEqual(len(accounts), 2)
        self.assertEqual(len(self.store.read()['accounts']), 2)
        # a Dispatcharr import with no explicit account gets no allowance of its own
        self.store.update(self.rev(), [up('sources', id='src-orphan', name='O', kind='m3u', middleware='dispatcharr', location_type='url', location=PLAYLIST_URL + 'x')])
        budgets = self.store.budgets()
        self.assertIn('src-orphan', budgets['unresolved_source_ids'])
        self.assertEqual(budgets['unresolved_allowance'], 0)
        # a mediated route must name accounts explicitly and cover every account behind its sources
        for bad in (up('routes', id='route-none', name='N', kind='dispatcharr', integration_id='int-disp', account_ids=[], source_ids=['src-disp'], enabled=False),
                    up('routes', id='route-gap', name='G', kind='dispatcharr', integration_id='int-disp', account_ids=['acct-b'], source_ids=['src-disp'], enabled=False)):
            with self.assertRaises(settings.SettingsError):
                self.store.update(self.rev(), [bad])


class StagedSwitch(Base):
    def flags(self):
        return {r['id']: r['enabled'] for r in self.store.read()['routes']}

    def test_both_routes_can_never_be_active(self):
        self.build()
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.update(self.rev(), [up('routes', id='route-disp', enabled=True)])
        self.assertIn('account_in_multiple_active_routes', [e['error'] for e in raised.exception.errors])
        self.assertEqual(self.flags(), {'route-direct': True, 'route-disp': False})

    def test_preview_commit_rollback(self):
        self.build()
        revision, before = self.rev(), self.store.read()
        plan = self.store.preview_switch('route-disp')
        self.assertTrue(plan['ready'])
        self.assertEqual([(f['route_id'], f['enabled']) for f in plan['flips']], [('route-direct', False), ('route-disp', True)])
        self.assertIn('budget_assurance_not_centralized', [w['code'] for w in plan['warnings']])
        self.assertEqual(self.store.read(), before)  # preview changes nothing
        committed = self.store.commit_switch(plan['plan_id'], revision)
        self.assertEqual(self.flags(), {'route-direct': False, 'route-disp': True})
        self.assertEqual(committed['revision'], revision + 1)
        self.assertEqual(next(a for a in self.store.budgets()['accounts'] if a['account_id'] == 'acct-a')['max_connections'], 1)  # no allowance copied
        self.assertEqual([r['id'] for r in committed['accounts']], ['acct-a', 'acct-b'])
        for section in ('profiles', 'devices'):
            self.assertEqual(committed[section], before[section])
        with self.assertRaises(settings.SettingsError):
            self.store.commit_switch(plan['plan_id'], self.rev())  # a plan commits once
        self.store.rollback_switch(plan['plan_id'])
        self.assertEqual(self.flags(), {'route-direct': True, 'route-disp': False})
        self.assertEqual([s['state'] for s in self.store.switches()], ['rolled_back'])
        with self.assertRaises(settings.SettingsError):
            self.store.rollback_switch(plan['plan_id'])

    def test_rollback_refuses_after_a_later_edit_and_sessions_block_both_directions(self):
        self.build()
        plan = self.store.preview_switch('route-disp', active_accounts={'acct-a'})
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['blockers'], [{'code': 'managed_sessions_active', 'account_id': 'acct-a'}])
        with self.assertRaises(settings.SettingsError):
            self.store.commit_switch(plan['plan_id'], self.rev(), active_accounts={'acct-a'})
        self.assertEqual(self.flags(), {'route-direct': True, 'route-disp': False})
        plan = self.store.preview_switch('route-disp')
        self.store.commit_switch(plan['plan_id'], self.rev())
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.rollback_switch(plan['plan_id'], active_accounts={'acct-a'})
        self.assertEqual(raised.exception.errors[0]['error'], 'switch_not_ready')
        self.store.update(self.rev(), [up('routes', id='route-disp', name='Renamed later')])
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.rollback_switch(plan['plan_id'])
        self.assertEqual(raised.exception.errors[0]['error'], 'rollback_blocked_by_later_edit')
        self.assertEqual(self.flags(), {'route-direct': False, 'route-disp': True})

    def test_stale_plan_conflicts_and_staged_plan_pauses_destructive_changes(self):
        self.build()
        plan = self.store.preview_switch('route-disp')
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.update(self.rev(), [delete('sources', 'src-direct')])
        self.assertEqual(raised.exception.errors[0]['error'], 'destructive_change_paused_while_switch_staged')
        self.store.update(self.rev(), [up('profiles', id='prof-household', name='Edited')])  # non-destructive edits are allowed
        with self.assertRaises(settings.Conflict):
            self.store.commit_switch(plan['plan_id'], self.rev())  # but the plan is now stale
        self.assertEqual(self.flags()['route-direct'], True)
        fresh = self.store.preview_switch('route-disp')
        self.assertEqual([s['state'] for s in self.store.switches()], ['staged', 'superseded'])
        self.store.cancel_switch(fresh['plan_id'])
        self.store.update(self.rev(), [delete('guide_sources', 'guide-disp')])  # destructive changes work again once the plan is cancelled
        self.assertEqual(self.store.switches()[0]['state'], 'cancelled')

    def test_mediated_route_without_accounts_cannot_be_switched_to(self):
        self.build()
        with self.assertRaises(settings.SettingsError):
            self.store.preview_switch('route-missing')


class ReadContract(Base):
    def probe(self, routes, **creds_change):
        self.build()
        if creds_change:
            self.store.update(self.rev(), [up('integrations', id='int-disp', **creds_change)])
        fake = Fake(routes)
        return dispatcharr.probe_integration(self.store, 'int-disp', fake, NOW), fake

    def test_healthy_probe_is_read_only_and_redacted(self):
        health, fake = self.probe(api_ok())
        self.assertEqual((health['status'], health['version'], health['stale'], health['pin_status']), ('ok', '0.32.0', False, 'pinned'))
        allowed = {BASE + p for p in [dispatcharr.VERSION_PATH] + list(dispatcharr.CAPABILITY_PATHS.values())}
        self.assertTrue({c[0] for c in fake.calls} <= allowed)
        self.assertTrue(all(h.get('X-API-Key') == KEY and 'Authorization' not in h for _, h in fake.calls))
        self.assertEqual(self.item('integrations', 'int-disp')['generation'], 1)
        self.assertNoSecrets([self.store.read(), self.store.export(), self.store.dispatcharr_status(NOW), health])
        self.assertNotIn('private.invalid', json.dumps(self.store.read()))

    def test_authorization_apikey_variant_is_explicit_and_unsupported_variants_refused(self):
        _, fake = self.probe(api_ok(), auth_variant='authorization-apikey')
        self.assertTrue(all(h.get('Authorization') == 'ApiKey ' + KEY and 'X-API-Key' not in h for _, h in fake.calls))
        body = dispatcharr.probe({'base_url': BASE, 'api_key': KEY, 'auth_variant': 'bearer'}, Fake({}), NOW)
        self.assertEqual((body['status'], body['reason']), ('capability_mismatch', 'unsupported_auth_variant'))

    def test_unavailable_revoked_version_and_capability_states_are_visible(self):
        cases = {
            'unavailable': (dispatcharr.TransportError('timeout'), 'timeout'),
            'unavailable ': ({BASE + dispatcharr.VERSION_PATH: (503, b'')}, 'http_503'),
            'credentials_rejected': (dict(api_ok(), **{BASE + dispatcharr.CAPABILITY_PATHS['channels']: (401, b'')}), 'key_not_authorized'),
            'credentials_rejected ': ({BASE + dispatcharr.VERSION_PATH: (403, b'')}, 'http_403'),
            'version_mismatch': (api_ok(version='0.3.0'), 'not_the_pinned_release'),
            'version_mismatch ': (api_ok(version='0.32.1'), 'not_the_pinned_release'),
            'version_mismatch  ': ({BASE + dispatcharr.VERSION_PATH: (200, b'not json')}, 'version_unreadable'),
            'capability_mismatch': (api_ok(streams=404), 'required_capability_missing'),
            'degraded': (api_ok(proxy_status=404), 'optional_capability_missing')}
        for expected, (routes, reason) in cases.items():
            self.setUp()
            if isinstance(routes, Exception):
                routes = {BASE + dispatcharr.VERSION_PATH: routes}
            health, _ = self.probe(routes)
            self.assertEqual((health['status'], health['reason']), (expected.strip(), reason), expected)
            self.assertEqual(self.store.dispatcharr_status(NOW)['integrations'][0]['health']['status'], expected.strip())

    def test_not_configured_makes_no_request(self):
        self.store.update(1, [up('integrations', id='int-bare', name='Bare', kind='dispatcharr')])
        fake = Fake(api_ok())
        health = dispatcharr.probe_integration(self.store, 'int-bare', fake, NOW)
        self.assertEqual((health['status'], fake.calls), ('not_configured', []))

    def test_stale_health_is_never_reported_as_ok(self):
        self.probe(api_ok())
        fresh = self.store.dispatcharr_status(NOW + 10)['integrations'][0]['health']
        late = self.store.dispatcharr_status(NOW + dispatcharr.HEALTH_STALE_SECONDS + 1)['integrations'][0]['health']
        self.assertEqual((fresh['status'], fresh['stale']), ('ok', False))
        self.assertEqual((late['status'], late['last_status'], late['stale']), ('stale', 'ok', True))
        self.assertEqual(dispatcharr.health(None, NOW)['status'], 'never_checked')

    def test_observations_do_not_touch_revision_and_follow_the_connection(self):
        self.probe(api_ok())
        revision, snapshots = self.rev(), self.store.snapshots()
        dispatcharr.probe_integration(self.store, 'int-disp', Fake(api_ok()), NOW + 60)
        self.assertEqual((self.rev(), self.store.snapshots()), (revision, snapshots))
        self.assertEqual(self.store.read()['capability_snapshots'][0]['generation'], 2)
        self.store.update(self.rev(), [up('integrations', id='int-disp', api_key=KEY2)])  # rotating the key invalidates the old observation
        self.assertEqual(self.store.read()['capability_snapshots'], [])
        with self.assertRaises(settings.SettingsError) as raised:  # an observation taken against an older generation is discarded
            self.store.record_observation('integration', 'int-disp', 1, {'status': 'ok'}, NOW)
        self.assertEqual(raised.exception.errors[0]['error'], 'stale_observation')

    def test_channel_and_stream_reads_keep_only_allowlisted_fields(self):
        self.build()
        creds, _ = self.store.integration_credentials('int-disp')
        payload = json.dumps({'results': [{'id': 12, 'uuid': 'u-1', 'name': 'ESPN', 'channel_number': 5.0, 'streams': [34], 'stream_profile_id': 1,
                                           'url': 'http://private.invalid/secret', 'stream_url': 'http://private.invalid/x'}]}).encode()
        url = BASE + '/api/channels/channels/?page_size=100&page=1'
        page = dispatcharr.read_page(creds, Fake({url: (200, payload)}), 'channels', 1)
        self.assertEqual(page['items'], [{'channel': '12', 'uuid': 'u-1', 'name': 'ESPN', 'number': 5.0, 'streams': [34], 'stream_profile_id': 1}])
        self.assertNotIn('private.invalid', json.dumps(page))
        stream = dispatcharr.sanitize_stream({'id': 34, 'name': 'S', 'm3u_account': 2, 'url': 'http://private.invalid/s'})
        self.assertEqual(stream, {'stream': '34', 'name': 'S', 'account': '2'})
        self.assertEqual(dispatcharr.read_page(creds, Fake({url: (401, b'')}), 'channels', 1), {'status': 'credentials_rejected', 'items': []})


class LoggingRedaction(Base):
    def test_no_secret_reaches_logs_or_exception_text(self):
        self.build()
        with self.assertLogs(level='DEBUG') as logs:
            import logging
            logging.getLogger('probe').debug('start')
            dispatcharr.probe_integration(self.store, 'int-disp', Fake({BASE + dispatcharr.VERSION_PATH: dispatcharr.TransportError('tls')}), NOW)
            with self.assertRaises(settings.SettingsError) as raised:
                self.store.update(self.rev(), [up('integrations', id='int-disp', base_url='ftp://dispatcharr.example.invalid', api_key=KEY2)])
        self.assertNoSecrets([logs.output, raised.exception.errors])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(app, 'DATA', Path(self.temp.name)), patch.object(app, 'active_account_ids', lambda: set())):
            patcher.start()
            self.addCleanup(patcher.stop)
        app.SETTINGS.clear()

    def post(self, sub, body, transport=None):
        raw = json.dumps(body).encode()
        with patch.object(app, 'DISPATCHARR_TRANSPORT', transport or Fake({})):
            return app.settings_route(True, sub, raw)

    def test_admin_routes_end_to_end(self):
        status, view = app.settings_route(False, [])
        ops = [up('accounts', id='acct-a', name='A', max_connections=1),
               up('integrations', id='int-disp', name='D', kind='dispatcharr', base_url=BASE, api_key=KEY),
               up('sources', id='src-disp', name='Via', kind='m3u', middleware='dispatcharr', integration_id='int-disp', location_type='url', location=PLAYLIST_URL, account_id='acct-a'),
               up('routes', id='route-disp', name='M', kind='dispatcharr', integration_id='int-disp', account_ids=['acct-a'], source_ids=['src-disp'], enabled=False)]
        self.assertEqual(self.post([], {'revision': view['revision'], 'ops': ops})[0], 200)
        status, health = self.post(['dispatcharr', 'probe'], {'integration_id': 'int-disp'}, Fake(api_ok()))
        self.assertEqual((status, health['status']), (200, 'ok'))
        status, body = self.post(['dispatcharr', 'assess'], {'source_id': 'src-disp'}, Fake({PLAYLIST_URL: (200, PROXIED.encode())}))
        self.assertEqual((status, body['budget_assurance']), (200, 'not_assured'))
        status, listing = app.settings_route(False, ['dispatcharr'])
        self.assertEqual((status, listing['integrations'][0]['health']['status']), (200, 'ok'))
        status, plan = self.post(['switch', 'preview'], {'activate_route_id': 'route-disp'})
        self.assertEqual((status, plan['ready']), (200, True))
        revision = app.settings_route(False, [])[1]['revision']
        self.assertEqual(self.post(['switch', 'commit'], {'plan_id': plan['plan_id'], 'revision': revision - 1})[0], 409)
        self.assertEqual(self.post(['switch', 'commit'], {'plan_id': plan['plan_id'], 'revision': revision})[0], 200)
        self.assertEqual(self.post(['switch', 'rollback'], {'plan_id': plan['plan_id']})[0], 200)
        self.assertEqual(app.settings_route(False, ['switch'])[1]['switches'][0]['state'], 'rolled_back')
        revision = app.settings_route(False, [])[1]['revision']
        status, view = self.post(['dispatcharr', 'disconnect'], {'revision': revision, 'integration_id': 'int-disp'})
        self.assertEqual((status, view['integrations']), (200, []))
        self.assertNoSecrets_all([health, body, listing, plan, view])

    def assertNoSecrets_all(self, values):
        text = json.dumps(values)
        for secret in SECRET_TEXTS:
            self.assertNotIn(secret, text)

    def test_bad_requests_are_classified_not_crashes(self):
        self.assertEqual(self.post(['dispatcharr', 'probe'], {'integration_id': 5})[0], 400)
        self.assertEqual(self.post(['dispatcharr', 'probe'], {'integration_id': 'int-none'})[0], 422)
        self.assertEqual(self.post(['dispatcharr', 'channels'], {'integration_id': 'int-none'})[0], 422)
        self.assertEqual(self.post(['switch', 'commit'], {'plan_id': 'sw-none', 'revision': 1})[0], 422)
        self.assertEqual(self.post(['dispatcharr', 'nope'], {})[0], 404)
        self.assertEqual(app.settings_route(False, ['dispatcharr', 'probe'])[0], 404)

    def test_new_routes_require_the_admin_token_through_the_http_handler(self):
        case = test_setup.RouteAndPageTests('test_setup_flow_through_http_handler_requires_token_and_never_leaks')
        case.setUp()
        self.addCleanup(case.tearDown)
        for method, path in (('GET', '/v1/settings/dispatcharr'), ('POST', '/v1/settings/dispatcharr/probe'), ('POST', '/v1/settings/switch/preview'), ('GET', '/v1/settings/switch')):
            status, _, _ = case.call(method, path, {} if method == 'POST' else None, token=None)
            self.assertEqual(status, 401, path)


if __name__ == '__main__':
    unittest.main()
