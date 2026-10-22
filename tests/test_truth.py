"""Phase 3: canonical identity, ESPN state truth, replay/upcoming phase and channel corroboration.

Fixture data only; no ESPN or provider contact.
"""
import unittest
from unittest.mock import MagicMock, patch

import guide
import livescores
import truth
import tv

NOW = 1_000_000
SLOT = 10800

def espn_event(event_id, home, away, start, state='in', name='STATUS_IN_PROGRESS', completed=False,
               networks=('ESPN',), venue='Test Park'):
    def side(role, team):
        team, _, short = team.partition('|')   # 'Display Name|Short Name'
        return {'homeAway': role, 'score': '1', 'team': {'id': str(sum(map(ord, team))), 'displayName': team,
                'shortDisplayName': short or team,
                'location': team.split()[0], 'logo': 'https://img/' + team.replace(' ', '_') + '.png'}}
    import datetime as dt
    return {'id': event_id, 'date': dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
            'competitions': [{'venue': {'fullName': venue}, 'broadcasts': [{'market': 'national', 'names': list(networks)}],
                'status': {'period': 2, 'displayClock': '8:42',
                           'type': {'state': state, 'name': name, 'completed': completed, 'shortDetail': name}},
                'competitors': [side('home', home), side('away', away)]}]}

def cache_of(*events, league=('Soccer', 'La Liga')):
    games = [livescores._normalize(league[0], league[1], e) for e in events]
    return {'updated_at': NOW, 'games': games}

def guide_event(event_id, title, start, feeds=(), sport='Soccer', league='La Liga', repeat=False, is_game=True):
    competition, matchup = guide.split_title(title, '')
    return {'id': event_id, 'title': title, 'competition': competition, 'matchup': matchup, 'is_game': is_game,
            'sport': sport, 'league': league, 'conferences': [], 'start': start, 'end': start + SLOT,
            'feeds': [dict(f) for f in feeds], 'live_tag': False, 'repeat': repeat}

def feed(pid, sid, name, **extra):
    return {'provider_id': pid, 'stream_id': sid, 'display_name': name, **extra}

class IdentityTests(unittest.TestCase):
    def test_accent_folding_and_espanyol_alias_never_become_barcelona(self):
        for name in ('RCD Español de Barcelona', 'Espanyol', 'RCD Espanyol de Barcelona', 'Español'):
            self.assertEqual(guide.team_key(name), 'espanyol', name)
        self.assertEqual(guide.team_key('FC Barcelona'), 'barcelona')
        self.assertEqual(guide.team_key('Alavés'), guide.team_key('Alaves'))

    def test_participants_matchup_key_and_sides(self):
        event = guide_event('a', 'Soccer: RCD Español de Barcelona vs Getafe', 100)
        merged = guide.consolidate_events([event])[0]
        self.assertEqual(merged['participants'], ['espanyol', 'getafe'])
        self.assertEqual(merged['matchup_key'], 'espanyol|getafe')
        self.assertEqual(merged['sides'], ['espanyol', 'getafe'])
        flipped = guide.consolidate_events([guide_event('b', 'Soccer: Getafe at Espanyol', 100)])[0]
        self.assertEqual(flipped['matchup_key'], merged['matchup_key'])
        self.assertEqual(flipped['sides'], ['getafe', 'espanyol'])

    def test_espanyol_and_barcelona_listings_are_different_events(self):
        merged = guide.consolidate_events([
            guide_event('e', 'Soccer: RCD Español de Barcelona vs Getafe', 100, [feed('providera', '1', 'beIN')]),
            guide_event('b', 'Soccer: FC Barcelona vs Getafe', 100, [feed('providerb', '2', 'beIN')])])
        self.assertEqual(len(merged), 2)

    def test_accent_and_naming_variants_merge(self):
        merged = guide.consolidate_events([
            guide_event('a', 'Soccer: Atlético Madrid vs Alavés', 100, [feed('providera', '1', 'beIN')]),
            guide_event('b', 'Soccer: Atletico Madrid - Alaves', 400, [feed('providerb', '2', 'beIN')])])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['feeds']), 2)

    def test_repeated_replay_listings_collapse_but_never_join_the_live_game(self):
        live = guide_event('live', 'Soccer: Sevilla vs Valencia', 100, [feed('providera', '1', 'beIN')])
        r1 = guide_event('r1', 'Soccer: Sevilla vs Valencia', 100, [feed('providera', '2', 'beIN 2')], repeat=True)
        r2 = guide_event('r2', 'Soccer: Valencia vs Sevilla', 200, [feed('providerb', '3', 'beIN 3')], repeat=True)
        merged = guide.consolidate_events([live, r1, r2])
        self.assertEqual(sorted((bool(e['repeat']), len(e['source_programs'])) for e in merged), [(False, 1), (True, 2)])

    def test_same_day_doubleheader_stays_two_cards_with_distinct_espn_ids(self):
        g1 = guide_event('g1', 'Houston Astros at Texas Rangers', 100, [feed('providera', '1', 'MLB')], 'Baseball', 'MLB')
        g2 = guide_event('g2', 'Houston Astros at Texas Rangers', 100 + 6 * 3600, [feed('providera', '2', 'MLB')], 'Baseball', 'MLB')
        self.assertEqual(len(guide.consolidate_events([g1, g2])), 2)

class EspnMatchingTests(unittest.TestCase):
    def setUp(self):
        p = patch('time.time', return_value=NOW); p.start(); self.addCleanup(p.stop)
        self.api = MagicMock()

    def match(self, event, *espn):
        self.api.read_cache.return_value = cache_of(*espn)
        return livescores.for_event(self.api, event)

    def test_espanyol_listing_resolves_to_espanyol_not_barcelona(self):
        barca = espn_event('1', 'Barcelona', 'Getafe', NOW - 600)
        espanyol = espn_event('2', 'Espanyol', 'Getafe', NOW - 600)
        event = guide_event('e', 'Soccer: RCD Español de Barcelona vs Getafe', NOW - 600)
        self.assertEqual(self.match(event, barca, espanyol)['espn_id'], '2')
        self.assertIsNone(self.match(event, barca))
        # And Barcelona never resolves to Espanyol's game.
        event = guide_event('b', 'Soccer: FC Barcelona vs Getafe', NOW - 600)
        self.assertEqual(self.match(event, barca, espanyol)['espn_id'], '1')
        self.assertIsNone(self.match(event, espanyol))

    def test_exact_identity_beats_token_overlap(self):
        tech = espn_event('1', 'Tennessee Tech Golden Eagles|Tennessee Tech', 'Auburn Tigers|Auburn', NOW - 600)
        vols = espn_event('2', 'Tennessee Volunteers|Tennessee', 'Auburn Tigers|Auburn', NOW - 600)
        event = guide_event('e', 'College Football\nAuburn at Tennessee', NOW - 600, sport='Football', league='NCAA')
        api_games = [livescores._normalize('Football', 'NCAA', x) for x in (tech, vols)]
        self.api.read_cache.return_value = {'updated_at': NOW, 'games': api_games}
        self.assertEqual(livescores.for_event(self.api, event)['espn_id'], '2')

    def test_upcoming_event_is_not_pinned_to_an_older_finished_game(self):
        old = espn_event('1', 'Sevilla', 'Valencia', NOW - 5 * 86400, 'post', 'STATUS_FINAL', True)
        event = guide_event('e', 'Soccer: Sevilla vs Valencia', NOW + 3600)
        self.assertIsNone(self.match(event, old))

    def test_state_score_period_clock_venue_artwork_and_networks_are_exposed(self):
        game = espn_event('1', 'Sevilla', 'Valencia', NOW - 600, networks=('ESPN', 'ESPN2'), venue='Sánchez-Pizjuán')
        live = self.match(guide_event('e', 'Soccer: Sevilla vs Valencia', NOW - 600), game)
        self.assertEqual((live['state'], live['period'], live['display_clock'], live['verified_live']), ('in', 2, '8:42', True))
        self.assertEqual((live['home_score'], live['away_score']), ('1', '1'))
        self.assertEqual(live['venue'], 'Sánchez-Pizjuán')
        self.assertEqual(live['networks'], ['ESPN', 'ESPN2'])
        self.assertTrue(live['home_logo'].endswith('Sevilla.png') and live['away_logo'].endswith('Valencia.png'))

class PhaseTests(unittest.TestCase):
    def setUp(self):
        p = patch('time.time', return_value=NOW); p.start(); self.addCleanup(p.stop)
        self.api = MagicMock()

    def phase(self, event, *espn):
        self.api.read_cache.return_value = cache_of(*espn)
        live = livescores.for_event(self.api, event)
        return truth.annotate(event, live, NOW)

    def test_espn_live_final_postponed_and_canceled_truth(self):
        title = 'Soccer: Sevilla vs Valencia'
        started = NOW - 1800
        event = guide_event('e', title, started)
        self.assertEqual(self.phase(event, espn_event('1', 'Sevilla', 'Valencia', started))['event_phase'], 'live')
        final = espn_event('1', 'Sevilla', 'Valencia', started - 6000, 'post', 'STATUS_FINAL', True)
        # Game began at its slot start and just finished inside the slot: final, not a replay.
        justended = guide_event('e', title, started - 6000)
        self.assertEqual(self.phase(justended, final)['event_phase'], 'final')
        post = espn_event('1', 'Sevilla', 'Valencia', started, 'post', 'STATUS_POSTPONED', False)
        self.assertEqual(self.phase(event, post)['event_phase'], 'postponed')
        canc = espn_event('1', 'Sevilla', 'Valencia', started, 'post', 'STATUS_CANCELED', False)
        self.assertEqual(self.phase(event, canc)['event_phase'], 'canceled')

    def test_finished_game_aired_later_in_the_day_is_a_replay_never_live(self):
        earlier = espn_event('1', 'Sevilla', 'Valencia', NOW - 7200, 'post', 'STATUS_FINAL', True)
        replay_slot = guide_event('e', 'Soccer: Sevilla vs Valencia', NOW - 600)
        result = self.phase(replay_slot, earlier)
        self.assertEqual(result['event_phase'], 'replay')
        self.assertFalse(result['live']['verified_live'])

    def test_provider_repeat_flag_wins_even_over_espn_in_progress(self):
        event = guide_event('e', 'Soccer: Sevilla vs Valencia', NOW - 600, repeat=True)
        result = self.phase(event, espn_event('1', 'Sevilla', 'Valencia', NOW - 600))
        self.assertEqual(result['event_phase'], 'replay')
        self.assertFalse(result['live']['verified_live'])
        self.assertTrue(result['live']['likely_replay'])

    def test_upcoming_unmapped_events_are_not_replays(self):
        for lead, expected in ((16 * 60, 'pregame'), (30 * 60, 'pregame'), (31 * 60, 'upcoming'), (4 * 3600, 'upcoming')):
            event = guide_event('e', 'Soccer: Sevilla vs Valencia', NOW + lead)
            result = self.phase(event)
            self.assertEqual(result['event_phase'], expected, lead)
            self.assertEqual(result['espn_coverage'], 'unmapped')

    def test_started_but_unmapped_is_on_now_not_replay_or_live(self):
        result = self.phase(guide_event('e', 'Soccer: Sevilla vs Valencia', NOW - 600))
        self.assertEqual(result['event_phase'], 'on_now')

    def test_espn_pre_after_guide_start_is_still_pregame(self):
        pre = espn_event('1', 'Sevilla', 'Valencia', NOW + 600, 'pre', 'STATUS_SCHEDULED')
        self.assertEqual(self.phase(guide_event('e', 'Soccer: Sevilla vs Valencia', NOW - 60), pre)['event_phase'], 'pregame')

    def test_studio_show_is_a_show(self):
        self.assertEqual(truth.event_phase({'is_game': False, 'start': NOW - 5}, None, NOW), 'show')

class PregameWindowTests(unittest.TestCase):
    def test_fifteen_minute_boundary_before_at_and_after(self):
        event = {'id': 'game', 'start': 10000, 'end': 12000, 'title': 'Chiefs at Broncos'}
        api = MagicMock(); api.read_cache.return_value = {'updated_at': 9000, 'source_updated_at': 9000, 'programs': [event]}
        self.assertGreaterEqual(guide.PREGAME_TUNE_SECONDS, 15 * 60)
        self.assertTrue(guide.find(api, 'game', at=10000 - 16 * 60)['pregame'])   # before the 15-minute mark
        self.assertTrue(guide.find(api, 'game', at=10000 - 15 * 60)['pregame'])   # at the 15-minute mark
        self.assertTrue(guide.find(api, 'game', at=10000 - 14 * 60)['pregame'])   # after it
        self.assertFalse(guide.find(api, 'game', at=10000)['pregame'])             # kickoff
        self.assertIsNone(guide.find(api, 'game', at=10000 - guide.PREGAME_TUNE_SECONDS - 1))

class BroadcastCheckTests(unittest.TestCase):
    def check(self, networks, *feeds):
        live = {'networks': list(networks)} if networks is not None else None
        return truth.broadcast_check({'live': live, 'feeds': list(feeds)})

    def test_corroboration_with_normalized_channel_names(self):
        result = self.check(['ESPN2'], feed('providera', '1', 'US: ESPN 2 HD'), feed('providerb', '2', 'ESPN2 FHD'))
        self.assertEqual(result['status'], 'corroborated')
        self.assertFalse(result['needs_verification'])

    def test_disagreement_is_reported_not_resolved(self):
        result = self.check(['ESPN'], feed('providera', '1', 'ESPN HD'), feed('providerb', '2', 'FS1 HD'))
        self.assertEqual(result['status'], 'disagreement')
        self.assertTrue(result['needs_verification'])
        self.assertEqual(result['providers']['providera']['status'], 'corroborated')
        self.assertEqual(result['providers']['providerb']['status'], 'disagreement')

    def test_espn_vs_espn2_and_espn_deportes_do_not_count_as_agreement(self):
        self.assertEqual(self.check(['ESPN'], feed('providera', '1', 'ESPN2'))['status'], 'disagreement')
        self.assertEqual(self.check(['ESPN'], feed('providera', '1', 'ESPN Deportes'))['status'], 'disagreement')

    def test_unknown_without_espn_networks_or_events(self):
        self.assertEqual(self.check([], feed('providera', '1', 'ESPN'))['status'], 'unknown')
        self.assertEqual(self.check(None, feed('providera', '1', 'ESPN'))['status'], 'unknown')
        self.assertEqual(self.check(['ESPN'])['status'], 'unknown')

    def test_alternate_commentary_feeds_are_not_compared(self):
        result = self.check(['ESPN'], feed('providera', '1', 'ESPN'), feed('providerb', '2', 'ESPN2', broadcast_kind='alternate'))
        self.assertEqual(result['providers']['providerb']['status'], 'not_compared')
        self.assertEqual(result['status'], 'corroborated')

class AttachLiveEventsTests(unittest.TestCase):
    def setUp(self):
        p = patch('time.time', return_value=NOW); p.start(); self.addCleanup(p.stop)
        api = MagicMock(); api.read_cache.return_value = cache_of(
            espn_event('77', 'Sevilla', 'Valencia', NOW - 1800, networks=('ESPN',)))
        previous = tv.A
        tv.configure(api)
        self.addCleanup(tv.configure, previous)

    def test_naming_duplicates_with_one_espn_event_become_one_card_and_carry_the_comparison(self):
        a = guide_event('a', 'Soccer: Sevilla vs Valencia', NOW - 1800, [feed('providera', '1', 'ESPN HD')])
        b = guide_event('b', 'Soccer: Valencia - Sevilla', NOW - 5400, [feed('providerb', '2', 'FS1')])
        cards = tv.attach_live_events([a, b])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['event_phase'], 'live')
        self.assertEqual(cards[0]['broadcast_check']['status'], 'disagreement')
        self.assertEqual({f['provider_id'] for f in cards[0]['feeds']}, {'providera', 'providerb'})

    def test_unmapped_upcoming_listing_gets_server_phase_and_no_live_block(self):
        future = guide_event('c', 'Soccer: Getafe vs Osasuna', NOW + 1200, [feed('providera', '1', 'beIN')])
        card = tv.attach_live_events([future])[0]
        self.assertEqual(card['event_phase'], 'pregame')
        self.assertNotIn('live', card)


if __name__ == '__main__':
    unittest.main()
