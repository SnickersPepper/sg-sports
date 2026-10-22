import time
import unittest
from unittest.mock import MagicMock, patch
import livescores

def espn_event(id='1', home='Los Angeles Rams', away='San Francisco 49ers',
                home_score='0', away_score='0', state='pre', completed=False,
                detail='Scheduled', period=0, clock='0:00', season_type=2,
                season_slug=None, date=None, home_rank=None, away_rank=None):
    date = date or time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())
    def side(order, name, score, rank):
        team = {'displayName': name}
        competitor = {'homeAway': order, 'team': team, 'score': score}
        if rank is not None:
            competitor['curatedRank'] = {'current': rank}
        return competitor
    return {'id': id, 'date': date, 'season': {'type': season_type, 'slug': season_slug},
            'competitions': [{'status': {'period': period, 'displayClock': clock,
                'type': {'state': state, 'completed': completed, 'shortDetail': detail}},
                'competitors': [side('home', home, home_score, home_rank),
                                 side('away', away, away_score, away_rank)]}]}

def cache_with(*games, sport='Football', league='NFL', stale=False):
    normalized = [livescores._normalize(sport, league, g) for g in games]
    updated_at = int(time.time()) - (3600 if stale else 0)
    return {'updated_at': updated_at, 'games': normalized}

class LiveScoresTests(unittest.TestCase):
    def test_scoreboard_refresh_uses_one_supported_calendar_date(self):
        # site-v2 returns HTTP 400 for YYYYMMDD-YYYYMMDD ranges. A single date keeps ESPN live
        # verification working; unmatched current guide games fail closed as replays in the TV.
        value = livescores._scoreboard_date()
        self.assertRegex(value, r'^\d{8}$')

    def test_fetch_raises_the_result_cap_so_a_wide_date_range_cant_truncate_todays_games(self):
        # ESPN silently caps an unbounded request around 100 events — a real St. Louis
        # Cardinals game live right now was missing from the cache entirely because a busy
        # league (MLB) filled that cap with older games across the widened date range before
        # reaching today's. Without an explicit limit this regresses every time the range grows.
        with patch.object(livescores, '_get', return_value={'events': []}) as mock_get:
            livescores._fetch('baseball/mlb')
        url = mock_get.call_args[0][0]
        self.assertIn('limit=1000', url)

    def test_normalize_extracts_score_and_status(self):
        game = livescores._normalize('Football', 'NFL', espn_event(home_score='24', away_score='17', state='in', period=4, clock='2:15'))
        self.assertEqual(game['home']['score'], '24')
        self.assertEqual(game['away']['score'], '17')
        self.assertEqual(game['state'], 'in')
        self.assertEqual(game['period'], 4)

    def test_curated_rank_99_sentinel_is_cleaned_to_unranked(self):
        # ESPN reports curatedRank.current=99 for every unranked team (including every NFL team,
        # since the NFL has no top-25 poll at all) -- that's "not ranked", not "ranked 99th", and
        # must never surface as a real rank badge.
        nfl = livescores._normalize('Football', 'NFL', espn_event(home_rank=99, away_rank=99))
        self.assertIsNone(nfl['home']['rank'])
        self.assertIsNone(nfl['away']['rank'])
        ranked = livescores._normalize('Football', 'NCAA', espn_event(home_rank=8, away_rank=99))
        self.assertEqual(ranked['home']['rank'], 8)
        self.assertIsNone(ranked['away']['rank'])

    def test_for_event_matches_by_team_tokens_either_order(self):
        api = MagicMock()
        api.read_cache.return_value = cache_with(espn_event(home='Los Angeles Rams', away='San Francisco 49ers', state='in', home_score='10', away_score='7'))
        for title in ['NFL: San Francisco 49ers at Los Angeles Rams', 'NFL: Rams vs 49ers']:
            live = livescores.for_event(api, {'sport': 'Football', 'league': 'NFL', 'title': title, 'start': int(time.time())})
            self.assertIsNotNone(live, title)
            self.assertTrue(live['verified_live'])
            self.assertEqual(live['home_score'], '10')

    def test_best_match_prefers_known_time_over_unknown_when_both_match(self):
        # A candidate with no parseable start time must not look like a "perfect" (gap=0) match
        # against the expected kickoff -- it should only ever be picked over a genuinely
        # time-close candidate if it's the only one matching by team tokens at all.
        near = 1_000_000
        unknown_start = {'sport': 'Football', 'league': 'NFL', 'start': None, 'completed': False,
                          'home': {'tokens': ['rams']}, 'away': {'tokens': ['niners']}}
        known = {'sport': 'Football', 'league': 'NFL', 'start': near + 1000, 'completed': False,
                 'home': {'tokens': ['rams']}, 'away': {'tokens': ['niners']}}
        cache = {'updated_at': int(time.time()), 'games': [unknown_start, known]}
        result = livescores._best_match(cache, 'Football', 'NFL', {'rams'}, {'niners'}, near=near)
        self.assertIs(result, known)
        # But if it's the only match at all, it's still returned rather than nothing.
        cache_alone = {'updated_at': int(time.time()), 'games': [unknown_start]}
        self.assertIs(livescores._best_match(cache_alone, 'Football', 'NFL', {'rams'}, {'niners'}, near=near), unknown_start)

    def test_for_event_no_match_returns_none(self):
        api = MagicMock()
        api.read_cache.return_value = cache_with(espn_event(home='Los Angeles Rams', away='San Francisco 49ers'))
        live = livescores.for_event(api, {'sport': 'Football', 'league': 'NFL', 'title': 'NFL: Kansas City Chiefs at Buffalo Bills', 'start': int(time.time())})
        self.assertIsNone(live)

    def test_stale_cache_fails_closed(self):
        api = MagicMock()
        api.read_cache.return_value = cache_with(espn_event(home='Los Angeles Rams', away='San Francisco 49ers'), stale=True)
        live = livescores.for_event(api, {'sport': 'Football', 'league': 'NFL', 'title': 'Rams at 49ers', 'start': int(time.time())})
        self.assertIsNone(live)

    def test_replay_detected_when_guide_slot_airing_but_espn_says_final(self):
        api = MagicMock()
        # ESPN began the game hours before this guide slot did, so the slot is a rebroadcast.
        # (A game that began with its own slot and has just ended is final, not a replay: see test_truth.)
        began = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(int(time.time()) - 6 * 3600))
        api.read_cache.return_value = cache_with(espn_event(home='Los Angeles Rams', away='San Francisco 49ers', state='post', completed=True, detail='Final', date=began))
        now = int(time.time())
        live = livescores.for_event(api, {'sport': 'Football', 'league': 'NFL', 'title': 'Rams at 49ers', 'start': now - 3600, 'end': now + 3600})
        self.assertIsNotNone(live)
        self.assertFalse(live['verified_live'])
        self.assertTrue(live['likely_replay'])

    def test_replay_of_a_game_from_days_ago_is_still_matched(self):
        # The real bug: a channel replaying a game that happened days before the EPG slot
        # airs now. The game's own kickoff is far outside any reasonable "near now" window,
        # so a plain nearest-in-time match would find nothing at all.
        api = MagicMock()
        now = int(time.time())
        days_ago = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(now - 2 * 86400))
        api.read_cache.return_value = cache_with(
            espn_event(home='San Francisco Giants', away='St. Louis Cardinals', state='post',
                       completed=True, detail='Final', date=days_ago),
            sport='Baseball', league='MLB')
        event = {'sport': 'Baseball', 'league': 'MLB',
                 'title': 'MLB Baseball\nSt. Louis Cardinals at San Francisco Giants',
                 'start': now - 300, 'end': now + 3300}
        live = livescores.for_event(api, event)
        self.assertIsNotNone(live)
        self.assertFalse(live['verified_live'])
        self.assertTrue(live['likely_replay'])

    def test_multiple_past_completed_games_pick_the_most_recent(self):
        api = MagicMock()
        now = int(time.time())
        older = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(now - 4 * 86400))
        newer = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(now - 2 * 86400))
        cache = cache_with(
            espn_event(id='1', home='San Francisco Giants', away='St. Louis Cardinals', home_score='5', away_score='4',
                       state='post', completed=True, date=older),
            espn_event(id='2', home='San Francisco Giants', away='St. Louis Cardinals', home_score='2', away_score='1',
                       state='post', completed=True, date=newer),
            sport='Baseball', league='MLB')
        api.read_cache.return_value = cache
        event = {'sport': 'Baseball', 'league': 'MLB', 'title': 'St. Louis Cardinals at San Francisco Giants',
                 'start': now - 300, 'end': now + 3300}
        live = livescores.for_event(api, event)
        self.assertEqual(live['home_score'], '2')

    def test_ncaa_football_notable_requires_both_teams_ranked(self):
        api = MagicMock()
        ranked_vs_ranked = livescores._normalize('Football', 'NCAA', espn_event(home='Georgia Bulldogs', away='Texas Longhorns', home_rank=3, away_rank=5))
        ranked_vs_unranked = livescores._normalize('Football', 'NCAA', espn_event(home='Georgia Bulldogs', away='Furman Paladins', home_rank=3, away_rank=99))
        self.assertTrue(livescores.is_notable(api, ranked_vs_ranked))
        self.assertFalse(livescores.is_notable(api, ranked_vs_unranked))

    def test_nfl_mlb_notable_only_in_postseason(self):
        api = MagicMock()
        regular = livescores._normalize('Football', 'NFL', espn_event(season_type=2))
        playoff = livescores._normalize('Football', 'NFL', espn_event(season_type=3))
        self.assertFalse(livescores.is_notable(api, regular))
        self.assertTrue(livescores.is_notable(api, playoff))

    def test_champions_league_notable_from_knockout_rounds_only(self):
        api = MagicMock()
        league_phase = livescores._normalize('Soccer', 'UEFA Champions League', espn_event(season_slug='league-phase'))
        quarterfinal = livescores._normalize('Soccer', 'UEFA Champions League', espn_event(season_slug='quarterfinal'))
        final = livescores._normalize('Soccer', 'UEFA Champions League', espn_event(season_slug='final'))
        self.assertFalse(livescores.is_notable(api, league_phase))
        self.assertTrue(livescores.is_notable(api, quarterfinal))
        self.assertTrue(livescores.is_notable(api, final))

    def test_premier_league_and_la_liga_notable_requires_both_teams_top5(self):
        api = MagicMock()
        api.read_cache.return_value = {'updated_at': int(time.time()),
            'top5': {'Premier League': ['Manchester City', 'Arsenal', 'Hull City', 'Chelsea', 'Brentford'],
                     'La Liga': ['Real Madrid', 'Barcelona']}}
        top5_clash = livescores._normalize('Soccer', 'Premier League', espn_event(home='Manchester City', away='Arsenal'))
        one_team_only = livescores._normalize('Soccer', 'Premier League', espn_event(home='Manchester City', away='Everton'))
        other_league = livescores._normalize('Soccer', 'La Liga', espn_event(home='Real Madrid', away='Barcelona'))
        self.assertTrue(livescores.is_notable(api, top5_clash))
        self.assertFalse(livescores.is_notable(api, one_team_only))
        self.assertTrue(livescores.is_notable(api, other_league))

    def test_premier_league_notable_is_false_when_standings_cache_is_stale_or_missing(self):
        stale_api = MagicMock()
        stale_api.read_cache.return_value = {'updated_at': int(time.time()) - 90000,
            'top5': {'Premier League': ['Manchester City', 'Arsenal']}}
        missing_api = MagicMock()
        missing_api.read_cache.return_value = None
        game = livescores._normalize('Soccer', 'Premier League', espn_event(home='Manchester City', away='Arsenal'))
        self.assertFalse(livescores.is_notable(stale_api, game))
        self.assertFalse(livescores.is_notable(missing_api, game))

    def test_for_fixture_matches_barcelona_by_opponent(self):
        api = MagicMock()
        api.read_cache.return_value = cache_with(espn_event(home='Barcelona', away='Real Madrid', state='in', home_score='2', away_score='1'), sport='Soccer', league='La Liga')
        fixture = {'opponent': 'Real Madrid', 'competition': 'La Liga', 'kickoff': int(time.time())}
        live = livescores.for_fixture(api, fixture, 'Barcelona')
        self.assertIsNotNone(live)
        self.assertTrue(live['verified_live'])

def espn_team_schedule(team_espn_id, opponent_name, is_home, date=None, state='pre'):
    # refresh_next_games() fetches the real schedule (not team.nextEvent, which lags behind
    # real game state -- see the comment on that function) and picks the first event whose own
    # status is still 'pre'; this builds one such schedule event for tests to feed it.
    date = date or time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() + 86400))
    us = {'id': str(team_espn_id), 'displayName': 'Us'}
    them = {'id': '999999', 'displayName': opponent_name}
    home = {'homeAway': 'home', 'team': us if is_home else them}
    away = {'homeAway': 'away', 'team': them if is_home else us}
    return {'events': [{'id': 'evt1', 'date': date,
        'competitions': [{'competitors': [home, away], 'status': {'type': {'state': state}}}]}]}

class NextGamesTests(unittest.TestCase):
    def test_schedule_event_rejects_an_event_that_does_not_contain_the_favorite(self):
        event=espn_team_schedule('999998','Someone Else',is_home=True)['events'][0]
        opponent,is_home,start,state=livescores._schedule_event(event,'12')
        self.assertIsNone(opponent)
        self.assertIsNone(start)

    def test_espn_timestamp_accepts_offsets_and_fractional_seconds(self):
        self.assertEqual(livescores._timestamp('2026-09-15T20:15:00.000Z'),
                         livescores._timestamp('2026-09-15T16:15:00-04:00'))

    def test_refresh_next_games_identifies_home_away_by_espn_id_not_name(self):
        api = MagicMock()
        responses = {
            (t['path'], t['espn_id']): espn_team_schedule(t['espn_id'], 'Rival ' + t['name'], is_home=(i % 2 == 0))
            for i, t in enumerate(livescores.FAVORITE_TEAMS)
        }
        def fake_get(url):
            for (path, espn_id), payload in responses.items():
                if url == livescores.BASE + '/' + path + '/teams/' + str(espn_id) + '/schedule?fixture=true':
                    return payload
            raise AssertionError('unexpected url ' + url)
        with patch.object(livescores, '_get', side_effect=fake_get):
            summary = livescores.refresh_next_games(api)
        self.assertEqual(summary['games'], len(livescores.FAVORITE_TEAMS))
        self.assertEqual(summary['errors'], 0)
        saved = api.save_cache.call_args[0]
        self.assertEqual(saved[0], 'next-games')
        games = saved[1]['games']
        self.assertEqual([c['status'] for c in saved[1]['coverage']],['available']*len(livescores.FAVORITE_TEAMS))
        for team, game in zip(livescores.FAVORITE_TEAMS, games):
            self.assertEqual(game['team_name'], team['name'])
            self.assertEqual(game['opponent'], 'Rival ' + team['name'])
            self.assertEqual(game['sport'], team['sport'])
            self.assertEqual(game['league'], team['league'])

    def test_refresh_next_games_team_index_survives_a_skipped_team(self):
        # A gap earlier in FAVORITE_TEAMS (e.g. an off-season team with no nextEvent) must not
        # shift which favorite a later game's team_index points at — that caused a real bug
        # where the client matched next_games to favorites.json purely by array position.
        api = MagicMock()
        skip_index = 2
        def fake_get(url):
            for i, t in enumerate(livescores.FAVORITE_TEAMS):
                if url == livescores.BASE + '/' + t['path'] + '/teams/' + str(t['espn_id']) + '/schedule?fixture=true':
                    if i == skip_index:
                        return {'events': []}
                    return espn_team_schedule(t['espn_id'], 'Rival ' + t['name'], is_home=True)
            raise AssertionError('unexpected url ' + url)
        with patch.object(livescores, '_get', side_effect=fake_get):
            summary = livescores.refresh_next_games(api)
        self.assertEqual(summary['games'], len(livescores.FAVORITE_TEAMS) - 1)
        games = api.save_cache.call_args[0][1]['games']
        self.assertNotIn(skip_index, [g['team_index'] for g in games])
        for game in games:
            expected = livescores.FAVORITE_TEAMS[game['team_index']]
            self.assertEqual(game['team_name'], expected['name'])

    def test_refresh_next_games_skips_team_with_no_upcoming_event(self):
        api = MagicMock()
        def fake_get(url):
            return {'events': []}
        with patch.object(livescores, '_get', side_effect=fake_get):
            summary = livescores.refresh_next_games(api)
        self.assertEqual(summary['games'], 0)
        self.assertEqual(summary['errors'], 0)
        coverage=api.save_cache.call_args[0][1]['coverage']
        self.assertEqual({c['status'] for c in coverage},{'no_upcoming_event'})

    def test_refresh_next_games_skips_a_game_already_underway_for_a_later_one(self):
        # The real bug this covers: ESPN's team.nextEvent field kept pointing at a game that had
        # already started (state 'in') or was postponed (state 'post', never completed) for
        # hours after the fact -- ESPN just hasn't rolled its own pointer forward yet. That team
        # must not disappear from "next game"; it must fall through to the real next 'pre' game.
        api = MagicMock()
        team = livescores.FAVORITE_TEAMS[3]
        started = espn_team_schedule(team['espn_id'], 'Already Started Opponent', is_home=True,
            date=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() - 3600)), state='in')
        postponed = espn_team_schedule(team['espn_id'], 'Postponed Opponent', is_home=True,
            date=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() - 86400 * 30)), state='post')
        real_next = espn_team_schedule(team['espn_id'], 'Real Next Opponent', is_home=False,
            date=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() + 86400)), state='pre')
        combined = {'events': started['events'] + postponed['events'] + real_next['events']}
        def fake_get(url):
            if url == livescores.BASE + '/' + team['path'] + '/teams/' + str(team['espn_id']) + '/schedule?fixture=true':
                return combined
            raise AssertionError('unexpected url ' + url)
        with patch.object(livescores, '_get', side_effect=fake_get):
            summary = livescores.refresh_next_games(api)
        self.assertEqual(summary['games'], 1)
        game = api.save_cache.call_args[0][1]['games'][0]
        self.assertEqual(game['opponent'], 'Real Next Opponent')

    def test_refresh_next_games_counts_errors_without_failing(self):
        api = MagicMock()
        with patch.object(livescores, '_get', side_effect=Exception('network down')):
            summary = livescores.refresh_next_games(api)
        self.assertEqual(summary['games'], 0)
        self.assertEqual(summary['errors'], len(livescores.FAVORITE_TEAMS))
        coverage=api.save_cache.call_args[0][1]['coverage']
        self.assertEqual({c['status'] for c in coverage},{'unavailable'})

class FixtureIdentityTests(unittest.TestCase):
    def test_unknown_competition_does_not_silently_become_la_liga(self):
        api=MagicMock();api.read_cache.return_value={'updated_at':int(time.time()),'games':[]}
        fixture={'sport':'Basketball','league':'NBA','competition':'NBA','opponent':'Celtics','kickoff':100}
        self.assertIsNone(livescores.for_fixture(api,fixture,'Knicks'))

class TeamHubTests(unittest.TestCase):
    TEAM = {'name': 'Test Team', 'sport': 'Football', 'league': 'NFL', 'path': 'football/nfl', 'espn_id': 99}
    BASE_URL = livescores.BASE + '/football/nfl/teams/99'

    def _routed(self, routes):
        def fake_get(url):
            for suffix, payload in routes.items():
                if url == self.BASE_URL + suffix:
                    return payload
            raise AssertionError('unexpected url ' + url)
        return fake_get

    def test_team_hub_assembles_all_sections(self):
        routes = {
            '': {'team': {'record': {'items': [{'type': 'total', 'summary': '9-2'}]},
                          'rank': 12, 'standingSummary': '1st in AFC West'}},
            '/roster': {'athletes': [
                {'position': 'offense', 'items': [
                    {'displayName': 'Josh Star', 'jersey': '7', 'position': {'abbreviation': 'QB'},
                     'injuries': [{'status': 'Questionable'}]},
                ]},
            ]},
        }
        with patch.object(livescores, '_get', side_effect=self._routed(routes)):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(hub['record'], '9-2')
        self.assertEqual(hub['rank'], 12)
        self.assertEqual(hub['standing'], '1st in AFC West')
        self.assertEqual(hub['roster'][0], {'name': 'Josh Star', 'jersey': '7', 'position': 'QB'})
        self.assertEqual(hub['injuries'], [{'name': 'Josh Star', 'status': 'Questionable'}])
        self.assertEqual(hub['news'], [])  # not mocked above — must default to empty, not raise

    def test_team_hub_flat_soccer_roster_and_unranked_team(self):
        routes = {
            '': {'team': {'record': {'items': [{'type': 'total', 'summary': '4-0-0'}]},
                          'standingSummary': '1st in La Liga'}},
            '/roster': {'athletes': [{'displayName': 'A. Keeper', 'jersey': '1', 'position': {'abbreviation': 'G'}}]},
        }
        with patch.object(livescores, '_get', side_effect=self._routed(routes)):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertIsNone(hub['rank'])
        self.assertEqual(hub['standing'], '1st in La Liga')
        self.assertEqual(hub['roster'], [{'name': 'A. Keeper', 'jersey': '1', 'position': 'G'}])
        self.assertEqual(hub['injuries'], [])

    def test_team_hub_games_are_upcoming_only_sorted_and_capped_at_five(self):
        events = [espn_event(id=str(i), state='pre', completed=False,
                              date=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() + i * 86400)))
                  for i in [3, 1, 2, 4, 5, 6, 7]]
        events.append(espn_event(id='already-played', state='post', completed=True,
                                  date=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() - 86400))))
        routes = {'': {'team': {}}, '/schedule?fixture=true': {'events': events}}
        with patch.object(livescores, '_get', side_effect=self._routed(routes)):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual([g['espn_id'] for g in hub['games']], ['1', '2', '3', '4', '5'])

    def test_team_hub_events_cover_the_whole_schedule_without_changing_games(self):
        now = time.time()
        def at(days):
            return time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(now + days * 86400))
        events = [espn_event(id='next', state='pre', date=at(2)),
                  espn_event(id='played', state='post', completed=True, detail='Final', home_score='3', away_score='1', date=at(-3)),
                  espn_event(id='live', state='in', detail='3rd - 4:00', home_score='7', away_score='6', date=at(0)),
                  espn_event(id='tbd', state='pre'),
                  espn_event(id='postponed', state='post', completed=False, date=at(9))]
        events[3]['date'] = None   # espn_event() fills an empty date with 'now'; a TBD game has none
        events[4]['competitions'][0]['status']['type']['name'] = 'STATUS_POSTPONED'
        routes = {'': {'team': {}}, '/schedule?fixture=true': {'events': events}}
        with patch.object(livescores, '_get', side_effect=self._routed(routes)):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual([g['espn_id'] for g in hub['games']], ['next'])   # unchanged: upcoming only
        by_id = {e['espn_id']: e for e in hub['events']}
        self.assertEqual(set(by_id), {'next', 'played', 'live', 'tbd', 'postponed'})
        self.assertEqual([e['espn_id'] for e in hub['events']][:3], ['played', 'live', 'next'])   # chronological; no start sorts last
        self.assertEqual(hub['events'][-1]['espn_id'], 'tbd')
        self.assertIsNone(by_id['tbd']['start'])
        self.assertEqual((by_id['played']['state'], by_id['played']['completed'], by_id['played']['home']['score'], by_id['played']['away']['score']), ('post', True, '3', '1'))
        self.assertEqual(by_id['postponed']['status_name'], 'STATUS_POSTPONED')
        self.assertNotIn('tokens', by_id['played']['home'])   # compact: no matching internals leak to the TV

    def test_team_hub_events_are_capped_and_a_failed_schedule_has_none(self):
        many = [espn_event(id=str(i), state='pre', date=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(time.time() + i * 3600))) for i in range(livescores.HUB_EVENTS_CAP + 30)]
        routes = {'': {'team': {}}, '/schedule?fixture=true': {'events': many}}
        with patch.object(livescores, '_get', side_effect=self._routed(routes)):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(len(hub['events']), livescores.HUB_EVENTS_CAP)
        def fake_get(url):
            if url.endswith('/schedule?fixture=true'):
                raise Exception('schedule down')
            return {}
        with patch.object(livescores, '_get', side_effect=fake_get):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(hub['unavailable'], ['games'])
        self.assertNotIn('events', hub)

    def test_team_hub_news_carries_provider_id_link_and_attribution(self):
        routes = {'': {'team': {}}}
        with patch.object(livescores, '_get', side_effect=lambda url: {'articles': [
                {'id': 123, 'headline': 'H1', 'published': '2026-01-01T00:00:00Z', 'links': {'web': {'href': 'https://example.com/a'}}},
                {'headline': 'H2'}]} if url.endswith('/news?team=99') else {}):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(hub['news'][0], {'headline': 'H1', 'published': '2026-01-01T00:00:00Z', 'id': '123', 'url': 'https://example.com/a', 'source': 'ESPN'})
        self.assertEqual((hub['news'][1]['id'], hub['news'][1]['url']), ('', None))   # absent facts stay absent

    def test_team_hub_roster_is_not_capped(self):
        routes = {
            '': {'team': {}},
            '/roster': {'athletes': [{'displayName': f'Player {i}', 'jersey': str(i),
                'position': {'abbreviation': 'M'}} for i in range(1, 26)]},
        }
        with patch.object(livescores, '_get', side_effect=self._routed(routes)):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(len(hub['roster']), 25)

    def test_team_hub_each_section_fails_independently(self):
        def fake_get(url):
            if url == self.BASE_URL + '/roster':
                raise Exception('roster down')
            if url == self.BASE_URL:
                return {'team': {'record': {'items': [{'type': 'total', 'summary': '9-2'}]}}}
            return {}
        with patch.object(livescores, '_get', side_effect=fake_get):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(hub['record'], '9-2')
        self.assertEqual(hub['roster'], [])
        self.assertEqual(hub['injuries'], [])
        self.assertEqual(hub['unavailable'], ['roster'])

    def test_team_hub_all_sections_failing_is_unavailable_not_an_empty_success(self):
        with patch.object(livescores, '_get', side_effect=Exception('provider unreachable')):
            with self.assertRaises(livescores.HubUnavailable):
                livescores.team_hub(MagicMock(), self.TEAM)

    def test_team_hub_genuinely_empty_provider_data_is_still_a_success(self):
        with patch.object(livescores, '_get', return_value={}):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(hub['unavailable'], [])
        self.assertEqual(hub['roster'], [])

    def test_team_hub_sections_run_concurrently(self):
        import threading
        barrier = threading.Barrier(4, timeout=5)
        def fake_get(url):
            barrier.wait()  # only passes if all four section requests are in flight together
            return {}
        with patch.object(livescores, '_get', side_effect=fake_get):
            hub = livescores.team_hub(MagicMock(), self.TEAM)
        self.assertEqual(hub['unavailable'], [])

    def test_team_hub_for_every_favorite_uses_its_own_espn_team(self):
        for team in livescores.FAVORITE_TEAMS:
            seen = []
            with patch.object(livescores, '_get', side_effect=lambda url: seen.append(url) or {}):
                livescores.team_hub(MagicMock(), team)
            self.assertTrue(all('/' + team['path'] + '/' in u and str(team['espn_id']) in u for u in seen), team['team_id'])
            self.assertEqual(len(seen), 4)

def rel(api, sport, league, **kw):
    game = livescores._normalize(sport, league, espn_event(**kw))
    return livescores.evaluate_relevance(api, game)

def codes(result):
    return [r['code'] for r in result['relevance']['reasons']]

def live_kw(**kw):
    return dict(state='in', home_score='21', away_score='17', period=4, clock='5:00', **kw)

class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock()
        self.api.read_cache.return_value = None

    def test_default_payload_is_versioned_and_empty(self):
        result = rel(self.api, 'Football', 'NFL', season_type=2)
        self.assertEqual(result, {'notable': False, 'relevance': {'version': 1, 'score': 0, 'reasons': []}})

    def test_close_late_football_live(self):
        result = rel(self.api, 'Football', 'NFL', **live_kw())
        self.assertTrue(result['notable'])
        self.assertEqual(codes(result), ['close_late'])
        self.assertEqual(result['relevance']['score'], 30)
        self.assertEqual(result['relevance']['reasons'][0]['label'], 'One-score game late')

    def test_close_but_not_late_and_late_but_not_close(self):
        self.assertFalse(rel(self.api, 'Football', 'NFL', **{**live_kw(), 'period': 2})['notable'])
        self.assertFalse(rel(self.api, 'Football', 'NFL', **{**live_kw(), 'home_score': '35'})['notable'])

    def test_close_late_requires_verified_live(self):
        for state, completed in (('pre', False), ('post', True)):
            kw = {**live_kw(), 'state': state, 'completed': completed}
            self.assertFalse(rel(self.api, 'Football', 'NFL', **kw)['notable'], state)

    def test_close_late_malformed_inputs_contribute_zero(self):
        for bad in ({'home_score': None}, {'away_score': 'x'}, {'period': None}, {'period': 'late'}):
            self.assertFalse(rel(self.api, 'Football', 'NFL', **{**live_kw(), **bad})['notable'], bad)

    def test_baseball_and_softball_thresholds(self):
        self.assertTrue(rel(self.api, 'Baseball', 'MLB', **{**live_kw(), 'period': 7, 'home_score': '3', 'away_score': '2'})['notable'])
        self.assertFalse(rel(self.api, 'Baseball', 'MLB', **{**live_kw(), 'period': 6, 'home_score': '3', 'away_score': '2'})['notable'])
        self.assertFalse(rel(self.api, 'Baseball', 'MLB', **{**live_kw(), 'period': 8, 'home_score': '9', 'away_score': '2'})['notable'])
        self.assertTrue(rel(self.api, 'Softball', 'NCAA', **{**live_kw(), 'period': 6, 'home_score': '1', 'away_score': '1'})['notable'])

    def test_soccer_late_close_uses_minute_not_wall_clock(self):
        base = {**live_kw(), 'period': 2, 'home_score': '1', 'away_score': '1'}
        self.assertTrue(rel(self.api, 'Soccer', 'MLS', **{**base, 'clock': "82'"})['notable'])
        self.assertFalse(rel(self.api, 'Soccer', 'MLS', **{**base, 'clock': "60'"})['notable'])
        self.assertFalse(rel(self.api, 'Soccer', 'MLS', **{**base, 'clock': "82'", 'home_score': '3'})['notable'])
        self.assertFalse(rel(self.api, 'Soccer', 'MLS', **{**base, 'clock': 'garbage'})['notable'])
        self.assertFalse(rel(self.api, 'Soccer', 'MLS', **{**base, 'clock': "82'", 'period': 1})['notable'])

    def test_ranked_matchup_ncaa_and_standings(self):
        result = rel(self.api, 'Football', 'NCAA', home='Georgia Bulldogs', away='Texas Longhorns', home_rank=3, away_rank=5)
        self.assertEqual(codes(result), ['ranked_matchup'])
        self.assertEqual(result['relevance']['score'], 40)
        self.api.read_cache.return_value = {'updated_at': int(time.time()), 'top5': {'Premier League': ['Arsenal', 'Chelsea']}}
        self.assertEqual(codes(rel(self.api, 'Soccer', 'Premier League', home='Arsenal', away='Chelsea')), ['ranked_matchup'])
        self.api.read_cache.return_value = {'updated_at': int(time.time()) - 90000, 'top5': {'Premier League': ['Arsenal', 'Chelsea']}}
        self.assertFalse(rel(self.api, 'Soccer', 'Premier League', home='Arsenal', away='Chelsea')['notable'])

    def test_knockout_requires_explicit_metadata(self):
        self.assertEqual(codes(rel(self.api, 'Football', 'NFL', season_type=3)), ['knockout'])
        self.assertEqual(codes(rel(self.api, 'Soccer', 'UEFA Champions League', season_slug='semifinal')), ['knockout'])
        self.assertFalse(rel(self.api, 'Soccer', 'UEFA Champions League', season_slug=None)['notable'])
        self.assertFalse(rel(self.api, 'Soccer', 'UEFA Champions League', season_slug='league-phase')['notable'])

    def test_rivalry_exact_pair_reversal_and_negatives(self):
        self.assertEqual(codes(rel(self.api, 'Soccer', 'La Liga', home='Real Madrid', away='Barcelona')), ['rivalry'])
        self.assertEqual(codes(rel(self.api, 'Soccer', 'La Liga', home='Barcelona', away='Real Madrid')), ['rivalry'])
        # similarly named, not the pair
        self.assertFalse(rel(self.api, 'Soccer', 'La Liga', home='Real Madrid', away='Espanyol')['notable'])
        self.assertFalse(rel(self.api, 'Football', 'NCAA', home='Tennessee Tech Golden Eagles', away='Alabama Crimson Tide')['notable'])
        # cross-league collision: the pair is scoped to its own sport and league
        self.assertFalse(rel(self.api, 'Soccer', 'Serie A', home='Real Madrid', away='Barcelona')['notable'])
        self.assertEqual(codes(rel(self.api, 'Football', 'NCAA', home='Auburn Tigers', away='Alabama Crimson Tide')), ['rivalry'])

    def test_additive_cap_reason_limit_and_order(self):
        result = rel(self.api, 'Football', 'NCAA', home='Auburn Tigers', away='Alabama Crimson Tide',
                     home_rank=4, away_rank=2, **live_kw())
        self.assertEqual(codes(result), ['ranked_matchup', 'close_late'])
        self.assertEqual(result['relevance']['score'], 95)
        self.assertLessEqual(len(result['relevance']['reasons']), 2)
        capped = livescores._normalize('Football', 'NFL', espn_event(season_type=3, **live_kw()))
        with patch.object(livescores, '_rivalry', return_value=True), patch.object(livescores, 'RELEVANCE_MAX_SCORE', 50):
            self.assertEqual(livescores.evaluate_relevance(self.api, capped)['relevance']['score'], 50)
            self.assertEqual(len(livescores.evaluate_relevance(self.api, capped)['relevance']['reasons']), 2)

    def test_repeated_runs_are_equal_and_boolean_matches_legacy(self):
        game = livescores._normalize('Football', 'NCAA', espn_event(home='Auburn Tigers', away='Alabama Crimson Tide', home_rank=4, away_rank=2, **live_kw()))
        first = livescores.evaluate_relevance(self.api, game)
        self.assertEqual(first, livescores.evaluate_relevance(self.api, game))
        self.assertEqual(livescores.is_notable(self.api, game), first['notable'])

    def test_major_ufc_fails_closed_without_title_guessing(self):
        game = livescores._normalize('Football', 'NFL', espn_event(home='UFC 300 Main Event', away='Studio Show'))
        self.assertFalse(livescores._major_ufc(game))
        self.assertNotIn('major_ufc', codes(livescores.evaluate_relevance(self.api, game)))

    def test_evaluation_error_returns_safe_default(self):
        game = livescores._normalize('Football', 'NFL', espn_event(season_type=3))
        with patch.object(livescores, '_knockout', side_effect=RuntimeError('boom')):
            self.assertEqual(livescores.evaluate_relevance(self.api, game),
                             {'notable': False, 'relevance': {'version': 1, 'score': 0, 'reasons': []}})
        self.api.read_cache.side_effect = RuntimeError('cache down')
        soccer = livescores._normalize('Soccer', 'Premier League', espn_event())
        self.assertFalse(livescores.evaluate_relevance(self.api, soccer)['notable'])

    def test_present_payload_is_bounded_and_carries_no_private_data(self):
        game = livescores._normalize('Football', 'NFL', espn_event(**live_kw()))
        payload = livescores._present(self.api, game, None, None)
        self.assertEqual(set(payload['relevance']), {'version', 'score', 'reasons'})
        text = repr(payload['relevance'])
        for forbidden in ('21', '17', 'http', 'provider', 'account'):
            self.assertNotIn(forbidden, text)
        for reason in payload['relevance']['reasons']:
            self.assertEqual(set(reason), {'code', 'label'})

if __name__ == '__main__':
    unittest.main()
