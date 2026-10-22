import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = patch.object(app, 'DATA', Path(self.temp.name))
        self.data.start()
        self.addCleanup(self.data.stop)

    def test_missing_credentials(self):
        with patch.object(app, 'SECRETS', Path(self.temp.name)):
            with self.assertRaisesRegex(app.ProviderError, 'credentials_required'):
                app.Xtream(app.PROVIDERS['providera'])

    def test_failover_and_account_redaction(self):
        (Path(self.temp.name) / 'providera.json').write_text('{"username":"secret","password":"secret"}')
        with patch.object(app, 'SECRETS', Path(self.temp.name)):
            adapter = app.Xtream(app.PROVIDERS['providera'])
        with patch.object(adapter, 'request', side_effect=[app.ProviderError('failed'), {'user_info': {'auth': 1, 'status': 'Active', 'password': 'secret'}}]):
            result = adapter.account()
        self.assertEqual(adapter.host, app.PROVIDERS['providera']['hosts'][1])
        self.assertNotIn('secret', json.dumps(result))
        with self.assertRaisesRegex(app.ProviderError, 'action_not_allowed'):
            adapter.request(adapter.host, 'play')

    def fixture(self):
        return {'checked_at': 100, 'max_age_seconds': 259200, 'fixtures': [{
            'id': 'test', 'kickoff': 200, 'kickoff_iso': '1970-01-01T00:03:20+00:00',
            'opponent': 'Feyenoord'}]}

    def cache(self):
        return {'updated_at': 100, 'epg_updated_at': 100, 'epg_coverage': {},
                'channels': [{'stream_id': '1', 'provider_id': 'providera',
                 'category': 'SPORTS', 'name': 'Barcelona vs Feyenoord 1970-01-01 3-0'}],
                'epg': [{'stream_id': '1', 'title': 'FC Barcelona vs Feyenoord 3-0',
                         'start': 190, 'end': 300, 'fetched_at': 100}]}

    def test_resolver_time_staleness_and_spoilers(self):
        app.save_cache('providera', self.cache())
        with patch.object(app, 'load_fixtures', return_value=self.fixture()):
            result = app.resolve(200)
            self.assertEqual(len(result['feeds']), 1)
            self.assertEqual(result['feeds'][0]['rank'], 100)
            self.assertNotIn('3-0', json.dumps(result))
            self.assertTrue(result['game_verified'])
            self.assertFalse(result['live_status_verified'])
            self.assertEqual(app.resolve(199)['feeds'], [])
            self.assertEqual(len(app.resolve(199, 'next')['feeds']), 1)
            self.assertEqual(app.resolve(11000)['feeds'], [])
            self.assertEqual(app.resolve(300000)['status'], 'fixture_data_required')

    def test_resolver_keeps_a_long_running_match_current_only_while_espn_says_so(self):
        # A match that goes to extra time/penalties, or just runs long, shouldn't fall out of
        # "current" the instant the fixed 3-hour window elapses -- same fix as the sports guide's
        # GUIDE_GRACE_SECONDS, applied here to Barcelona's own schedule. kickoff=200, so the
        # normal window ends at 11000; past that, only a verified-live ESPN match should extend it.
        fixture = self.fixture()
        with patch.object(app, 'load_fixtures', return_value=fixture):
            with patch('livescores.for_fixture', return_value={'verified_live': True}):
                self.assertEqual(app.resolve(11000)['fixture']['id'], 'test')
                self.assertEqual(app.resolve(11000, fixture_id='test')['fixture']['id'], 'test')
            with patch('livescores.for_fixture', return_value={'verified_live': False}):
                self.assertIsNone(app.resolve(11000)['fixture'])
            with patch('livescores.for_fixture', return_value=None):
                self.assertIsNone(app.resolve(11000)['fixture'])
            # Even a verified-live match doesn't extend forever -- past the grace ceiling it's
            # gone regardless, a safety bound against ESPN never correcting a stuck 'in' status.
            with patch('livescores.for_fixture', return_value={'verified_live': True}):
                far_past_grace = 200 + 10800 + app.FIXTURE_GRACE_SECONDS + 1
                self.assertIsNone(app.resolve(far_past_grace)['fixture'])

    def test_resolver_rejects_wrong_opponent_old_date_other_squad(self):
        cached = self.cache()
        cached['epg'] = []
        for name in ('Barcelona vs Levante 1970-01-01', 'Barcelona vs Feyenoord 1969-01-01',
                     'Barcelona vs Feyenoord Women 1970-01-01', 'Barcelona vs Feyenoord highlights 1970-01-01'):
            cached['channels'][0]['name'] = name
            app.save_cache('providera', cached)
            with patch.object(app, 'load_fixtures', return_value=self.fixture()):
                self.assertEqual(app.resolve(200)['feeds'], [])

    def next_game(self):
        return {'id': 'next-2633-1', 'team_name': 'Tennessee Volunteers', 'sport': 'Football',
                'league': 'NCAA', 'opponent': 'Florida Gators', 'home': True,
                'competition': 'NCAA', 'kickoff': 200}

    def test_resolve_next_game_matches_epg_and_redacts_score(self):
        cache = self.cache()
        cache['channels'][0]['name'] = 'SPORTS #1'
        cache['epg'] = [{'stream_id': '1', 'title': 'Tennessee Volunteers vs Florida Gators 21-14',
                          'start': 190, 'end': 300, 'fetched_at': 100}]
        app.save_cache('providera', cache)
        app.save_cache('next-games', {'updated_at': 100, 'games': [self.next_game()]})
        result = app.resolve_next_game('next-2633-1', now=200)
        self.assertEqual(len(result['feeds']), 1)
        self.assertEqual(result['feeds'][0]['rank'], 100)
        self.assertNotIn('21-14', json.dumps(result))
        self.assertTrue(result['game_verified'])
        self.assertEqual(result['status'], 'candidate_feeds')

    def test_resolve_next_game_uses_the_canonical_published_guide(self):
        app.save_cache('next-games', {'updated_at': 100, 'games': [self.next_game()]})
        published={'id':'canonical','feeds':[{'provider_id':'providera','stream_id':'42',
            'display_name':'ESPN','broadcast_kind':'main'}]}
        with patch('guide.for_fixture',return_value=published):
            result=app.resolve_next_game('next-2633-1',now=200)
        self.assertEqual([(f['provider_id'],f['stream_id']) for f in result['feeds']],[('providera','42')])
        self.assertEqual(result['feeds'][0]['evidence'],'published_guide_event')
        self.assertEqual(result['feeds'][0]['availability'],'scheduled_not_probed')

    def test_resolve_next_game_rejects_wrong_opponent_and_excluded_channels(self):
        cache = self.cache()
        cache['channels'][0]['name'] = 'SPORTS #1'
        app.save_cache('next-games', {'updated_at': 100, 'games': [self.next_game()]})
        for title in ('Tennessee Volunteers vs LSU Tigers', 'Tennessee Volunteers vs Florida Gators highlights',
                      'Tennessee Volunteers vs Florida Gators Women'):
            cache['epg'] = [{'stream_id': '1', 'title': title, 'start': 190, 'end': 300, 'fetched_at': 100}]
            app.save_cache('providera', cache)
            self.assertEqual(app.resolve_next_game('next-2633-1', now=200)['feeds'], [])

    def test_resolve_next_game_unknown_id_or_stale_cache(self):
        app.save_cache('next-games', {'updated_at': 100, 'games': [self.next_game()]})
        self.assertEqual(app.resolve_next_game('next-missing', now=200)['status'], 'fixture_data_required')
        self.assertEqual(app.resolve_next_game('next-2633-1', now=100000)['status'], 'fixture_data_required')

    def test_event_channel_fallback_and_stale_catalog(self):
        cached = self.cache()
        cached['epg'] = []
        app.save_cache('providera', cached)
        with patch.object(app, 'load_fixtures', return_value=self.fixture()):
            self.assertEqual(app.resolve(200)['feeds'][0]['rank'], 70)
            cached['updated_at'] = -30000
            app.save_cache('providera', cached)
            self.assertEqual(app.resolve(200)['feeds'], [])

    def test_adjacent_date_is_lower_confidence(self):
        cached = self.cache()
        cached['epg'] = []
        cached['channels'][0]['name'] = 'AU Barcelona v Feyenoord 1970-01-02 02:40'
        app.save_cache('providera', cached)
        with patch.object(app, 'load_fixtures', return_value=self.fixture()):
            feed = app.resolve(200)['feeds'][0]
            self.assertEqual(feed['rank'], 40)
            self.assertTrue(feed['requires_date_verification'])
            self.assertFalse(feed['provider_time_verified'])
        cached['channels'][0]['name'] = 'Barcelona v Feyenoord 1970-01-03'
        app.save_cache('providera', cached)
        with patch.object(app, 'load_fixtures', return_value=self.fixture()):
            self.assertEqual(app.resolve(200)['feeds'], [])

    def test_no_catalog_is_explicit(self):
        with patch.object(app, 'load_fixtures', return_value=self.fixture()):
            self.assertEqual(app.resolve(200)['coverage']['providera']['status'], 'catalog_required')

    def test_barcelona_event_is_sport(self):
        self.assertTrue(app.SPORTS.search('AMAZON PRIME Barcelona vs Feyenoord'))

    def test_decode_and_timestamps(self):
        self.assertEqual(app.decode('RkMgQmFyY2Vsb25h'), 'FC Barcelona')
        self.assertIsNone(app.epoch({'start': '2026-09-08 12:00:00'}, 'start'))

if __name__ == '__main__':
    unittest.main()
