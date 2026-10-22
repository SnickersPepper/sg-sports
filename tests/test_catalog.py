"""Canonical favorite/league catalog contract: stable IDs, explicit recoverable migration, >7 favorites end-to-end. Synthetic data only."""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import adminfix
import catalog
import livescores
import settings
import tv

SEVEN_LEGACY = ['soccer.fc-barcelona', 'football.tennessee-volunteers', 'softball.lady-vols', 'baseball.st-louis-cardinals',
                'football.kansas-city-chiefs', 'baseball.houston-astros', 'football.missouri-tigers']
SEVEN_CANONICAL = ['team.soccer.men.la-liga.fc-barcelona', 'team.football.men.ncaa.tennessee-volunteers', 'team.softball.women.ncaa.tennessee-volunteers',
                   'team.baseball.men.mlb.st-louis-cardinals', 'team.football.men.nfl.kansas-city-chiefs', 'team.baseball.men.mlb.houston-astros',
                   'team.football.men.ncaa.missouri-tigers']


def up(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


class CatalogContractTests(unittest.TestCase):
    def test_catalog_is_well_formed_with_unique_stable_ids(self):
        self.assertEqual(catalog.validate(), [])
        self.assertEqual(len({e['id'] for e in catalog.ENTRIES}), len(catalog.ENTRIES))

    def test_more_than_seven_selectable_teams_and_all_sport_gender_distinctions(self):
        self.assertGreater(len(catalog.TEAMS), 7)
        self.assertGreater(len(catalog.TEAMS), 60)
        by = catalog.BY_ID
        men, women = by['team.soccer.men.la-liga.fc-barcelona'], by['team.soccer.women.liga-f.fc-barcelona']
        self.assertNotEqual(men['id'], women['id'])
        self.assertEqual((men['gender'], women['gender']), ('men', 'women'))
        football, softball = by['team.football.men.ncaa.tennessee-volunteers'], by['team.softball.women.ncaa.tennessee-volunteers']
        self.assertNotEqual(football['id'], softball['id'])
        self.assertEqual((football['sport'], softball['sport']), ('Football', 'Softball'))
        # same short name in two competitions stays two entries (ncaa football vs nfl; ncaa vs mlb leagues)
        self.assertNotEqual(by['league.football.ncaa']['id'], by['league.softball.ncaa']['id'])

    def test_ids_do_not_depend_on_display_name_or_list_position(self):
        original = {e['id']: e for e in catalog.ENTRIES}
        renamed = copy.deepcopy(catalog.ENTRIES)
        for e in renamed:
            e['name'] = e['name'].upper() + ' (renamed)'
        reordered = list(reversed(renamed))
        self.assertEqual({e['id'] for e in reordered}, set(original))
        with patch.object(catalog, 'TEAMS', [e for e in reordered if e['kind'] == 'team']), patch.object(catalog, 'LEAGUES', [e for e in reordered if e['kind'] == 'league']):
            view = catalog.view()
        self.assertEqual({t['id'] for t in view['teams']}, {e['id'] for e in catalog.TEAMS})
        # migration works from the explicit table, so renaming/reordering cannot change what a legacy ID becomes
        with patch.object(catalog, 'TEAMS', [e for e in reordered if e['kind'] == 'team']):
            self.assertEqual(catalog.migrate_ids('team', SEVEN_LEGACY), SEVEN_CANONICAL)

    def test_no_personal_defaults_and_no_implied_support(self):
        view = catalog.view()
        self.assertNotIn('default', json.dumps(view).lower())
        installed = set(SEVEN_CANONICAL)
        for team in view['teams']:
            if team['id'] not in installed:
                self.assertEqual(team['support'], {'schedule': False, 'program_matching': False}, team['id'])
            self.assertNotIn('dvr', team['support'])
        for league in view['leagues']:
            self.assertEqual(league['support'], {'schedule': False, 'program_matching': False})
        self.assertNotIn('legacy_ids', json.dumps(view))  # migration data is not part of the client contract

    def test_schedule_coverage_matches_the_catalog_exactly(self):
        covered = {t['team_id'] for t in livescores.FAVORITE_TEAMS}
        self.assertEqual(covered, {e['id'] for e in catalog.TEAMS if e['support']['schedule']})
        self.assertTrue(covered <= set(catalog.BY_ID))
        self.assertEqual(len(covered), len(livescores.FAVORITE_TEAMS))

    def test_favorite_team_lookup_by_id_without_positional_fallback(self):
        chiefs = livescores.favorite_team('team.football.men.nfl.kansas-city-chiefs')
        self.assertEqual(chiefs['espn_id'], 12)
        self.assertIsNone(livescores.favorite_team('team.football.men.nfl.seattle-seahawks'))
        self.assertIsNone(livescores.favorite_team('team.unknown.x.y.z', 0))  # a named id never falls back to an index
        self.assertIsNone(livescores.favorite_team(3))
        self.assertEqual(livescores.favorite_team(None, 0)['team_id'], SEVEN_CANONICAL[0])  # installed build only
        self.assertIsNone(livescores.favorite_team(None, 7))
        self.assertIsNone(livescores.favorite_team(None, True))

    def test_migrate_ids_translates_only_the_explicit_table(self):
        self.assertEqual(catalog.migrate_ids('team', SEVEN_LEGACY), SEVEN_CANONICAL)
        self.assertEqual(catalog.migrate_ids('team', []), [])
        # unknown IDs, already-canonical IDs and ambiguous legacy IDs are preserved; order kept; duplicates dropped
        mixed = ['soccer.fc-barcelona', 'basketball.some-team', SEVEN_CANONICAL[0], 'softball.tennessee-volunteers', 'softball.lady-vols']
        self.assertEqual(catalog.migrate_ids('team', mixed), [SEVEN_CANONICAL[0], 'basketball.some-team', SEVEN_CANONICAL[2]])
        self.assertEqual(catalog.migrate_ids('league', ['ncaa', 'nfl', 'mlb', 'future-league']), ['ncaa', 'league.football.nfl', 'league.baseball.mlb', 'future-league'])
        self.assertEqual(catalog.migrate_ids('team', ['favorite-0', 'favorite-1']), ['favorite-0', 'favorite-1'])  # array-position IDs are never remapped
        self.assertEqual(catalog.migrate_ids('team', ['nfl']), ['nfl'])  # a league legacy ID is not a team


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 's.sqlite'

    def v1_store(self, favorites, leagues=None):
        store = settings.SettingsStore(self.path, migrations={1: settings.MIGRATIONS[1]})
        prefs = {'favorites': favorites}
        if leagues is not None:
            prefs['leagues'] = leagues
        # legacy IDs are not canonical-looking on purpose; write them the way an earlier build did (dots, lowercase)
        store.update(1, [up('profiles', id='prof-household', preferences=prefs)])
        return store

    def test_seven_installed_favorites_migrate_explicitly_and_recoverably(self):
        self.v1_store(SEVEN_LEGACY, ['nfl', 'ncaa', 'uefa-champions-league'])
        store = settings.SettingsStore(self.path)
        view = store.read()
        prefs = view['profiles'][0]['preferences']
        self.assertEqual(prefs['favorites'], SEVEN_CANONICAL)
        self.assertEqual(prefs['leagues'], ['league.football.nfl', 'ncaa', 'league.soccer.uefa-champions-league'])  # ambiguous 'ncaa' kept as saved
        self.assertEqual((view['schema_version'], view['revision']), (4, 3))
        # recoverable: the exact pre-migration document is in the snapshot history
        self.assertIn(2, store.snapshots())
        with sqlite3.connect(self.path) as db:
            before = json.loads(db.execute('SELECT doc FROM snapshots WHERE revision=2').fetchone()[0])
        self.assertEqual(before['profiles'][0]['preferences']['favorites'], SEVEN_LEGACY)
        # idempotent: reopening does nothing further
        again = settings.SettingsStore(self.path).read()
        self.assertEqual((again['revision'], again['profiles'][0]['preferences']['favorites']), (3, SEVEN_CANONICAL))

    def test_unknown_empty_and_unset_favorites_are_preserved_without_a_revision_bump(self):
        self.v1_store(['basketball.future-team', 'team.custom.x.y.z'])
        prefs = settings.SettingsStore(self.path).read()['profiles'][0]['preferences']
        self.assertEqual(prefs['favorites'], ['basketball.future-team', 'team.custom.x.y.z'])
        path2 = Path(self.temp.name) / 'empty.sqlite'
        store = settings.SettingsStore(path2, migrations={1: settings.MIGRATIONS[1]})
        store.update(1, [up('profiles', id='prof-household', preferences={'favorites': []})])
        reopened = settings.SettingsStore(path2).read()
        self.assertEqual((reopened['profiles'][0]['preferences']['favorites'], reopened['revision']), ([], 2))
        path3 = Path(self.temp.name) / 'unset.sqlite'
        fresh = settings.SettingsStore(path3).read()
        self.assertEqual((fresh['profiles'][0]['preferences'], fresh['revision']), ({}, 1))  # clean install: no personal defaults

    def test_other_profiles_migrate_independently(self):
        store = settings.SettingsStore(self.path, migrations={1: settings.MIGRATIONS[1]})
        store.update(1, [up('profiles', id='prof-household', preferences={'favorites': ['soccer.fc-barcelona']}),
                         up('profiles', id='prof-guest', name='Guest', preferences={'favorites': ['football.kansas-city-chiefs'], 'language': 'es'})])
        profiles = {p['id']: p['preferences'] for p in settings.SettingsStore(self.path).read()['profiles']}
        self.assertEqual(profiles['prof-household']['favorites'], [SEVEN_CANONICAL[0]])
        self.assertEqual(profiles['prof-guest'], {'favorites': [SEVEN_CANONICAL[4]], 'language': 'es'})

    def test_version_one_export_restores_with_translated_ids(self):
        store = settings.SettingsStore(self.path)
        export = store.export()
        export['schema_version'] = 1
        export['profiles'] = [{'id': 'prof-household', 'name': 'Household', 'preferences': {'favorites': SEVEN_LEGACY[:2] + ['basketball.future-team']}}]
        store.restore(store.read()['revision'], export, {})
        prefs = store.read()['profiles'][0]['preferences']
        self.assertEqual(prefs['favorites'], SEVEN_CANONICAL[:2] + ['basketball.future-team'])


class HttpCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(app, 'DATA', Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        app.SETTINGS.clear()
        tv.PAIR.clear()

    def call(self, method, path, body=None, token='admintoken'):
        sent = {}
        data = json.dumps(body).encode() if body is not None else b''
        headers = {'Content-Type': 'application/json', 'Content-Length': str(len(data))}
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
        return sent['status'], sent['body']

    def pair(self):
        return tv.pair(tv.new_pair()['code'])['token']

    def test_catalog_is_served_to_the_administrator_and_a_paired_tv_only(self):
        status, admin = self.call('GET', '/v1/catalog/favorites')
        self.assertEqual((status, admin), (200, catalog.view()))
        self.assertEqual(self.call('GET', '/v1/catalog/favorites', token=None)[0], 401)
        token = self.pair()
        self.assertEqual(self.call('GET', '/v1/tv/catalog', token=token), (200, catalog.view()))
        self.assertEqual(self.call('GET', '/v1/tv/catalog', token=None)[0], 401)
        self.assertEqual(self.call('GET', '/v1/tv/catalog', token='admintoken')[0], 401)
        self.assertEqual(self.call('GET', '/v1/catalog/favorites', token=token)[0], 401)  # a TV token is not an administrator token

    def test_more_than_seven_catalog_favorites_reorder_and_empty_round_trip_between_browser_and_tv(self):
        token = self.pair()
        ids = [t['id'] for t in catalog.view()['teams']][:25]
        revision = self.call('GET', '/v1/settings')[1]['revision']
        self.assertEqual(self.call('POST', '/v1/settings', {'revision': revision, 'ops': [up('profiles', id='prof-household', preferences={'favorites': ids})]})[0], 200)
        status, view = self.call('GET', '/v1/tv/settings', token=token)
        self.assertEqual(view['preferences']['favorites'], ids)
        reordered = ids[::-1]
        self.assertEqual(self.call('POST', '/v1/tv/settings', {'revision': view['revision'], 'preferences': {'favorites': reordered}}, token=token)[0], 200)
        profile = self.call('GET', '/v1/settings')[1]['profiles'][0]
        self.assertEqual(profile['preferences']['favorites'], reordered)
        status, emptied = self.call('POST', '/v1/tv/settings', {'revision': self.call('GET', '/v1/settings')[1]['revision'], 'preferences': {'favorites': []}}, token=token)
        self.assertEqual((status, emptied['preferences']['favorites']), (200, []))
        # an unknown-but-well-formed ID is accepted and preserved verbatim; a malformed one (e.g. a display label) is refused
        revision = self.call('GET', '/v1/tv/settings', token=token)[1]['revision']
        self.assertEqual(self.call('POST', '/v1/tv/settings', {'revision': revision, 'preferences': {'favorites': [ids[0], 'team.future.x.y.z']}}, token=token)[0], 200)
        revision = self.call('GET', '/v1/tv/settings', token=token)[1]['revision']
        self.assertEqual(self.call('POST', '/v1/tv/settings', {'revision': revision, 'preferences': {'favorites': ['FC Barcelona']}}, token=token)[0], 422)
        self.assertEqual(self.call('GET', '/v1/tv/settings', token=token)[1]['preferences']['favorites'], [ids[0], 'team.future.x.y.z'])

    def test_team_hub_request_accepts_a_catalog_id_for_schedule_covered_teams_only(self):
        self.assertEqual(livescores.favorite_team(SEVEN_CANONICAL[5])['name'], 'Houston Astros')
        # a selectable-but-uncovered catalog entry has no schedule/hub source and must not resolve
        self.assertIsNone(livescores.favorite_team('team.baseball.men.mlb.seattle-mariners'))


if __name__ == '__main__':
    unittest.main()


class CacheCompatibilityTests(unittest.TestCase):
    def test_pre_catalog_next_games_cache_gets_canonical_ids_from_the_backend_list(self):
        rows = [{'team_index': 5, 'team_name': 'Houston Astros'}, {'team_index': 99}, {'team_index': 1, 'team_id': 'team.keep.me.x'}, 'junk']
        out = tv.with_team_ids(rows)
        self.assertEqual(out[0]['team_id'], 'team.baseball.men.mlb.houston-astros')
        self.assertNotIn('team_id', out[1])
        self.assertEqual(out[2]['team_id'], 'team.keep.me.x')
        self.assertEqual(out[3], 'junk')
        self.assertNotIn('team_id', rows[0])  # the cached rows themselves are untouched


class BrowserLogicTests(unittest.TestCase):
    def test_deterministic_js_checks_pass_when_node_is_available(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed in this environment; run: node tests/js/prefs-logic.test.js')
        root = Path(app.__file__).resolve().parent
        result = subprocess.run([node, 'tests/js/prefs-logic.test.js'], cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
