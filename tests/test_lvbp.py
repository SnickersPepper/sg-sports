"""Phase 5A: LVBP schedule-first ingestion, assignment, mapping, overrides, coverage and API seams.

All offline. Fixtures under tests/fixtures/lvbp are public schedule fragments captured from
stats.lvbp.com on 2026-10-15; synthetic pages cover shapes the live source has not produced yet.
"""
import datetime as dt
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import app
import guide
import lvbp
import lvbp_broadcast as lb
import tv
import truth
import verification

FIX = Path(__file__).parent / 'fixtures' / 'lvbp'
CCS = ZoneInfo('America/Caracas')
NOW = int(dt.datetime(2026, 10, 15, 13, 0, tzinfo=CCS).timestamp())  # 17:00Z; the audit observations are earlier
DAY = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')
MONTH = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')


def fixture(name):
    return (FIX / name).read_text(encoding='utf-8')


def page(days):
    """Synthetic schedule fragment: days = [(date, [(away, home, time_text, venue, [labels])])]."""
    out = '<div><span class="game-date">test</span>'
    for date, rows in days:
        out += '<div class="col-12 fw-bold my-2">%s %d de %s</div>' % (DAY[date.weekday()], date.day, MONTH[date.month - 1])
        if not rows:
            out += '<h5>No hay juegos programados para esta fecha.</h5>'
        for away, home, time_text, venue, labels in rows:
            out += ('<div class="list-group-item"><span class="game"><img src="a"> %s @ <img src="b"> %s</span>'
                    '<span class="game-time">%s</span><span class="game-status">%s</span>%s</div>'
                    % (away, home, time_text, venue, ''.join('<img src="t" alt="%s" title="%s">' % (l, l) for l in labels)))
    return out + '</div>'


def fixture_fetch(day):
    path = FIX / ('schedule-%s.html' % day.isoformat())
    return path.read_text(encoding='utf-8') if path.exists() else page([(day, []), (day + dt.timedelta(days=1), [])])


def stored(**kw):
    base = {'id': 'lvbp-test', 'season': '2026-27', 'away_team_id': 'navegantes-del-magallanes', 'home_team_id': 'tigres-de-aragua',
            'venue': 'Estadio', 'start': NOW + 6 * 3600, 'local_date': '2026-10-15', 'source_timezone': 'America/Caracas',
            'status': 'scheduled', 'source_url': lvbp.SOURCE_URL, 'source': lvbp.SOURCE_ID, 'source_event_id': None,
            'fetched_at': NOW, 'game_number': 1, 'doubleheader': False, 'broadcasters_raw': ['Meridiano TV']}
    base.update(kw)
    return base


SOURCE_OK = {'state': 'fresh', 'checked_at': NOW}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(app, 'DATA', Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        verification.RUNNING.clear()

    def catalog(self, provider, *entries):
        app.save_cache(provider, {'channels': [{'stream_id': str(s), 'name': n, 'epg_channel_id': e} for s, n, e in entries]})

    def standard_catalogs(self):
        self.catalog('providera', (309879, '|LATIN| MERIDIANO', None), (232440, '|VE| Meridiano', None),
                     (73457, '|Venezuela| IVC', None), (232442, '|VE| Televen', None), (73461, '|Venezuela| Venevision', None))
        self.catalog('providerb', (264832, 'LAT - MERIDIANO', None), (1134438, 'LAT - TELEVEN', 'Televen.ve'), (1134422, 'LAT - VENEVISION', None))

    def isolate_health(self, observations=()):
        p = patch.dict(lvbp.DATA, {'observations': list(observations)})
        p.start()
        self.addCleanup(p.stop)


class TeamsAndAliases(Base):
    def test_eight_teams_and_aliases_including_accent_variants(self):
        self.assertEqual(len(lvbp.TEAMS), 8)
        for name, expected in [('Águilas del Zulia', 'aguilas-del-zulia'), ('Aguilas', 'aguilas-del-zulia'), ('ÁGUILAS', 'aguilas-del-zulia'),
                               ('Bravos', 'bravos-de-margarita'), ('Cardenales de Lara', 'cardenales-de-lara'),
                               ('Caribes de Anzoátegui', 'caribes-de-anzoategui'), ('caribes de anzoategui', 'caribes-de-anzoategui'),
                               ('Leones', 'leones-del-caracas'), ('Navegantes del Magallanes', 'navegantes-del-magallanes'),
                               ('Magallanes', 'navegantes-del-magallanes'), ('Tigres', 'tigres-de-aragua'),
                               ('Tiburones de La Guaira', 'tiburones-de-la-guaira')]:
            self.assertEqual(lvbp.team_id(name), expected, name)
        self.assertIsNone(lvbp.team_id('Yankees'))
        self.assertIsNone(lvbp.team_id(''))

    def test_two_teams_are_needed_to_claim_the_league(self):
        self.assertEqual(lvbp.team_league('Leones del Caracas vs Tiburones de La Guaira'), lvbp.LEAGUE)
        self.assertIsNone(lvbp.team_league('Leones highlights'))
        self.assertEqual(guide.classify('Baseball · Leones del Caracas vs Tiburones de La Guaira', []), ('Baseball', lvbp.LEAGUE))
        # A Venezuelan soccer fixture between same-named cities must not become baseball.
        self.assertIsNone(guide.classify('Soccer · Caracas vs Aragua', []))
        self.assertEqual(guide.classify('Chicago White Sox @ St. Louis Cardinals', ['Baseball']), ('Baseball', 'MLB'))


class ParsingAndIdentity(Base):
    def test_real_fixture_parses_to_eight_games_with_official_labels_and_venues(self):
        rows = lvbp.parse_page(fixture('schedule-2026-10-15.html'), dt.date(2026, 10, 15), NOW)
        self.assertEqual(len(rows), 8)
        events = lvbp.build_events(rows, NOW)
        self.assertEqual(len(events), 8)
        first = min(events.values(), key=lambda e: (e['local_date'], e['start']))
        self.assertEqual((first['away_team_id'], first['home_team_id']), ('bravos-de-margarita', 'leones-del-caracas'))
        self.assertEqual(first['venue'], 'Estadio Monumental Simón Bolívar')
        self.assertEqual(first['broadcasters_raw'], ['1Baseball Network'])
        self.assertEqual(first['source_timezone'], 'America/Caracas')
        # Same visitor/home pair on consecutive days is two games, not a duplicate.
        pair = [e for e in events.values() if e['away_team_id'] == 'bravos-de-margarita' and e['home_team_id'] == 'leones-del-caracas']
        self.assertEqual(len({e['id'] for e in pair}), 2)
        self.assertFalse(any(e['doubleheader'] for e in pair))

    def test_true_doubleheader_survives_and_exact_duplicates_collapse(self):
        d = dt.date(2026, 10, 24)
        row = ('Tigres', 'Leones', '1:00 PM', 'Estadio A', [])
        rows = lvbp.parse_page(page([(d, [row, ('Tigres', 'Leones', '6:00 PM', 'Estadio A', []), row]), (d + dt.timedelta(days=1), [])]), d, NOW)
        events = lvbp.build_events(rows, NOW)
        self.assertEqual(len(events), 2)
        ordered = sorted(events.values(), key=lambda e: e['start'])
        self.assertEqual([e['game_number'] for e in ordered], [1, 2])
        self.assertTrue(all(e['doubleheader'] for e in ordered))
        self.assertNotEqual(ordered[0]['id'], ordered[1]['id'])

    def test_identity_is_stable_when_first_pitch_moves_within_the_day(self):
        d = dt.date(2026, 10, 24)
        def build(time_text):
            return lvbp.build_events(lvbp.parse_page(page([(d, [('Tigres', 'Leones', time_text, 'V', [])]), (d + dt.timedelta(days=1), [])]), d, NOW), NOW)
        before, after = build('7:00 PM'), build('5:00 PM')
        self.assertEqual(set(before), set(after))
        self.assertNotEqual(next(iter(before.values()))['start'], next(iter(after.values()))['start'])

    def test_caracas_time_is_independent_of_viewer_zone_and_never_uses_dst(self):
        original = os.environ.get('TZ')
        try:
            for tz in ('Asia/Tokyo', 'America/New_York', 'UTC'):
                os.environ['TZ'] = tz
                time.tzset()
                rows = lvbp.parse_page(page([(dt.date(2026, 10, 15), [('Tigres', 'Leones', '7:00 PM', 'V', [])]), (dt.date(2026, 10, 16), [])]), dt.date(2026, 10, 15), NOW)
                self.assertEqual(rows[0]['start'], int(dt.datetime(2026, 10, 15, 23, 0, tzinfo=dt.timezone.utc).timestamp()), tz)
        finally:
            if original is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = original
            time.tzset()
        # Northern-summer and winter dates share UTC-4: no daylight-saving assumption.
        for month in (1, 7):
            d = dt.date(2027, month, 15)
            rows = lvbp.parse_page(page([(d, [('Tigres', 'Leones', '12:30 AM', 'V', [])]), (d + dt.timedelta(days=1), [])]), d, NOW)
            self.assertEqual(rows[0]['start'], int(dt.datetime(2027, month, 15, 4, 30, tzinfo=dt.timezone.utc).timestamp()))
        rows = lvbp.parse_page(page([(dt.date(2026, 10, 15), [('Tigres', 'Leones', '12:00 PM', 'V', [])]), (dt.date(2026, 10, 16), [])]), dt.date(2026, 10, 15), NOW)
        self.assertEqual(rows[0]['start'], int(dt.datetime(2026, 10, 15, 16, 0, tzinfo=dt.timezone.utc).timestamp()))

    def test_malformed_or_naive_times_are_rejected_not_guessed(self):
        d = dt.date(2026, 10, 15)
        for bad in ('25:00 PM', '13:00 PM', '0:30 PM'):
            with self.assertRaises(lvbp.SchemaError, msg=bad):
                lvbp.parse_page(page([(d, [('Tigres', 'Leones', bad, 'V', [])]), (d + dt.timedelta(days=1), [])]), d, NOW)
        rows = lvbp.parse_page(page([(d, [('Tigres', 'Leones', '19:00', 'V', [])]), (d + dt.timedelta(days=1), [])]), d, NOW)
        self.assertEqual((rows[0]['status'], rows[0]['start']), ('unknown', None))

    def test_status_words_final_and_missing_fields(self):
        d = dt.date(2026, 10, 15)
        cases = [('Pospuesto', 'postponed'), ('Suspendido', 'suspended'), ('Cancelado por lluvia', 'canceled'),
                 ('Reprogramado', 'rescheduled'), ('ZUL 8, CAR 0', 'final'), ('', 'unknown')]
        rows = lvbp.parse_page(page([(d, [('Tigres', 'Leones', t, '', []) for t, _ in cases]), (d + dt.timedelta(days=1), [])]), d, NOW)
        self.assertEqual([r['status'] for r in rows], [s for _, s in cases])
        events = lvbp.build_events(rows, NOW)
        self.assertTrue(all(e['venue'] == '' for e in events.values()))  # missing venue stays empty, never invented

    def test_real_results_page_marks_finals_and_hides_pitcher_text_as_venue(self):
        rows = lvbp.parse_page(fixture('schedule-2026-10-13.html'), dt.date(2026, 10, 13), NOW)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r['status'] == 'final' and r['venue'] == '' for r in rows))
        self.assertEqual(rows[0]['source_event_id'], '870090')

    def test_offseason_and_schema_changes_fail_closed(self):
        self.assertEqual(lvbp.parse_page(fixture('schedule-2027-02-20.html'), dt.date(2027, 2, 20), NOW), [])
        good = fixture('schedule-2026-10-15.html')
        for broken in (good.replace('game-date', 'fecha'), good.replace('Leones', 'Marlins', 1), good.replace('class="game-time"', 'class="hora"'),
                       good.replace('Jueves 15 de octubre', 'Quinceavo octubre'), '', '<html>maintenance</html>'):
            with self.assertRaises(lvbp.SchemaError):
                lvbp.parse_page(broken, dt.date(2026, 10, 15), NOW)
        with self.assertRaises(lvbp.SchemaError):  # an empty section without the source's "no games" notice
            lvbp.parse_page('<span class="game-date">x</span><div class="col-12 fw-bold my-2">Jueves 15 de octubre</div><div class="col-12 fw-bold my-2">Viernes 16 de octubre</div>', dt.date(2026, 10, 15), NOW)

    def test_reschedule_supersedes_instead_of_duplicating(self):
        d = dt.date(2026, 10, 20)
        old = lvbp.build_events(lvbp.parse_page(page([(d, [('Tigres', 'Leones', '7:00 PM', 'V', [])]), (d + dt.timedelta(days=1), [])]), d, NOW), NOW)
        moved = dt.date(2026, 10, 22)
        new = lvbp.build_events(lvbp.parse_page(page([(moved, [('Tigres', 'Leones', '7:00 PM', 'V', [])]), (moved + dt.timedelta(days=1), [])]), moved, NOW), NOW)
        merged, withdrawn = lvbp.reconcile(old, new, dt.date(2026, 10, 15), dt.date(2026, 10, 30))
        self.assertEqual(withdrawn, [])
        old_id, new_id = next(iter(old)), next(iter(new))
        self.assertEqual(merged[old_id]['status'], 'rescheduled')
        self.assertEqual(merged[old_id]['superseded_by'], new_id)
        self.assertEqual(merged[new_id]['supersedes'], old_id)
        # A vanished game with no replacement is withdrawn, not silently kept as scheduled.
        merged, withdrawn = lvbp.reconcile(old, {}, dt.date(2026, 10, 15), dt.date(2026, 10, 30))
        self.assertEqual(withdrawn, [old_id])
        self.assertNotIn(old_id, merged)


class RefreshAndFreshness(Base):
    def run_refresh(self, now=NOW, fetch=fixture_fetch, **kw):
        return lvbp.refresh(app, fetch=fetch, now=now, **kw)

    def test_refresh_stores_window_and_normal_cadence(self):
        result = self.run_refresh()
        self.assertEqual(result['state'], 'ok')
        cache = app.read_cache(lvbp.CACHE)
        self.assertEqual(cache['season'], '2026-27')
        self.assertEqual(cache['status']['next_attempt'], NOW + lvbp.REFRESH_SECONDS)
        self.assertGreater(len(cache['events']), 30)
        self.assertEqual(lvbp.freshness(app, NOW + 60)['state'], 'fresh')
        self.assertEqual(self.run_refresh(now=NOW + 60)['state'], 'backoff')  # not due again yet

    def test_failure_keeps_last_known_good_backs_off_and_becomes_visibly_stale(self):
        self.run_refresh()
        before = app.read_cache(lvbp.CACHE)['events']
        def boom(day):
            raise OSError('private url https://secret.example/token')
        for i, wait in enumerate((15 * 60, 30 * 60, 60 * 60)):
            at = NOW + lvbp.REFRESH_SECONDS + i * 4 * 3600
            result = self.run_refresh(now=at, fetch=boom)
            self.assertEqual(result['state'], 'fetch_failed')
            self.assertEqual(result['next_attempt'] - at, wait)
        cache = app.read_cache(lvbp.CACHE)
        self.assertEqual(cache['events'], before)
        self.assertNotIn('secret', json.dumps(cache))
        self.assertEqual(lvbp.freshness(app, NOW + 7 * 3600)['state'], 'fresh')
        self.assertEqual(lvbp.freshness(app, NOW + 9 * 3600)['state'], 'stale')
        self.assertTrue(lvbp.events(app, NOW + 9 * 3600))  # stale data is still shown, labeled by freshness
        self.assertEqual(lvbp.freshness(app, NOW + 8 * 86400)['state'], 'unavailable')
        self.assertEqual(lvbp.events(app, NOW + 8 * 86400), [])
        self.assertEqual(lvbp.freshness(app, NOW + 9 * 3600)['checked_at'], NOW)

    def test_backoff_is_capped_at_the_normal_cadence(self):
        for i in range(8):
            result = self.run_refresh(now=NOW + i * 7 * 3600, fetch=lambda d: (_ for _ in ()).throw(OSError()))
        self.assertEqual(result['next_attempt'] - (NOW + 7 * 7 * 3600), lvbp.REFRESH_SECONDS)

    def test_schema_change_fails_closed_and_keeps_last_known_good(self):
        self.run_refresh()
        before = app.read_cache(lvbp.CACHE)['events']
        later = NOW + lvbp.REFRESH_SECONDS + 1
        result = self.run_refresh(now=later, fetch=lambda d: '<html>redesigned</html>')
        self.assertEqual(result['state'], 'schema_changed')
        self.assertEqual(app.read_cache(lvbp.CACHE)['events'], before)

    def test_unexpectedly_empty_schedule_does_not_erase_upcoming_games(self):
        self.run_refresh()
        before = app.read_cache(lvbp.CACHE)['events']
        later = NOW + lvbp.REFRESH_SECONDS + 1
        result = self.run_refresh(now=later, fetch=lambda d: page([(d, []), (d + dt.timedelta(days=1), [])]))
        self.assertEqual(result['state'], 'schema_changed')
        self.assertEqual(app.read_cache(lvbp.CACHE)['events'], before)

    def test_offseason_is_an_ok_empty_schedule(self):
        feb = int(dt.datetime(2027, 2, 20, 12, tzinfo=CCS).timestamp())
        self.assertEqual(self.run_refresh(now=feb)['state'], 'ok')
        self.assertEqual(lvbp.events(app, feb), [])
        self.assertEqual(lvbp.freshness(app, feb)['state'], 'fresh')

    def test_previous_season_data_never_passes_as_current(self):
        self.run_refresh()
        later_season = int(dt.datetime(2027, 10, 20, 12, tzinfo=CCS).timestamp())
        self.assertEqual(lvbp.season_for(later_season), '2027-28')
        self.assertEqual(lvbp.freshness(app, later_season)['state'], 'unavailable')
        self.assertEqual(lvbp.events(app, later_season), [])

    def test_season_starts_in_october(self):
        def at(y, m, d):
            return int(dt.datetime(y, m, d, 12, tzinfo=CCS).timestamp())
        self.assertEqual(lvbp.season_for(at(2026, 9, 30)), '2025-26')
        self.assertEqual(lvbp.season_for(at(2026, 10, 1)), '2026-27')
        self.assertEqual(lvbp.season_for(at(2027, 1, 15)), '2026-27')

    def test_source_can_be_disabled_and_never_raises(self):
        with patch.dict(os.environ, {'SG_LVBP_SOURCE': 'off'}):
            self.assertEqual(self.run_refresh()['state'], 'disabled')
        self.assertIsNone(app.read_cache(lvbp.CACHE).get('events'))

    def test_still_scheduled_long_after_first_pitch_is_unresolved_not_offered(self):
        app.save_cache(lvbp.CACHE, {'season': '2026-27', 'fetched_at': NOW, 'status': {}, 'events': [stored(start=NOW - 7 * 3600), stored(id='b', start=NOW - 3600), stored(id='c', status='final', start=NOW + 100)]})
        self.assertEqual([e['id'] for e in lvbp.events(app, NOW)], ['b'])


class AssignmentsAndCoverageLevel(Base):
    def setUp(self):
        super().setUp()
        self.standard_catalogs()

    def evaluate(self, event=None, now=NOW, programs=(), overrides=(), guide_updated=None):
        return lb.evaluate(app, event or stored(), now, programs, guide_updated or now, overrides)

    def test_official_assignment_with_fresh_channel_health_is_confirmed(self):
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW - 600, 'ttl': 21600}])
        ev = self.evaluate()
        self.assertEqual((ev['coverage'], ev['assigned']), ('confirmed', ['meridiano']))
        item, _ = lb.to_event(app, stored(), NOW, SOURCE_OK)
        self.assertEqual(item['lvbp']['label'], 'Confirmed feed')
        self.assertEqual(item['lvbp']['broadcasters'], ['Meridiano TV'])

    def test_official_assignment_without_fresh_health_is_only_possible(self):
        self.isolate_health()
        self.assertEqual(self.evaluate()['coverage'], 'possible')
        # An expired observation cannot support Confirmed either.
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW - 30000, 'ttl': 21600}])
        self.assertEqual(self.evaluate()['coverage'], 'possible')

    def test_official_beats_conflicting_epg_and_conflict_is_reported(self):
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW - 60, 'ttl': 21600}])
        epg = {'league': lvbp.LEAGUE, 'is_game': True, 'start': NOW + 6 * 3600, 'sides': ['navegantes del magallanes', 'tigres de aragua'],
               'feeds': [{'provider_id': 'providera', 'stream_id': '232442', 'display_name': '|VE| Televen'}]}
        ev = self.evaluate(programs=[epg])
        self.assertEqual(ev['assigned'], ['meridiano'])
        self.assertEqual(ev['conflicts'], ['televen'])
        self.assertEqual(ev['coverage'], 'confirmed')
        # The disagreeing listing alone must not lift an unassigned game above None either.
        ev = self.evaluate(stored(broadcasters_raw=['ByM Sport']), programs=[epg])
        self.assertEqual(ev['coverage'], 'none')

    def test_epg_only_is_possible_and_expired_epg_is_not(self):
        self.isolate_health()
        epg = {'league': lvbp.LEAGUE, 'is_game': True, 'start': NOW + 6 * 3600, 'sides': ['navegantes del magallanes', 'tigres de aragua'],
               'feeds': [{'provider_id': 'providera', 'stream_id': '232442', 'display_name': '|VE| Televen'}]}
        event = stored(broadcasters_raw=[])
        self.assertEqual(self.evaluate(event, programs=[epg])['coverage'], 'possible')
        old = self.evaluate(event, programs=[epg], guide_updated=NOW - lb.EPG_TTL - 1)
        self.assertEqual((old['coverage'], old['epg'], old['feeds']), ('none', [], []))
        item, _ = lb.to_event(app, event, NOW, SOURCE_OK, [epg], NOW)
        self.assertIn('not confirm', item['lvbp']['detail'])

    def test_expired_official_assignment_loses_confirmation(self):
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW, 'ttl': 10 ** 7}])
        later = NOW + lb.ASSIGNMENT_TTL + 1
        ev = self.evaluate(now=later)
        self.assertEqual(ev['coverage'], 'none')
        self.assertTrue(ev['expired'])

    def test_generic_service_listing_is_not_a_linear_assignment(self):
        self.isolate_health()
        ev = self.evaluate(stored(broadcasters_raw=['BeisbolPlay']))
        self.assertEqual((ev['coverage'], ev['assigned'], ev['feeds']), ('none', [], []))
        item, _ = lb.to_event(app, stored(broadcasters_raw=['BeisbolPlay']), NOW, SOURCE_OK)
        self.assertEqual(item['lvbp']['label'], 'No verified feed')
        self.assertEqual(item['feeds'], [])

    def test_assigned_non_linear_or_unmapped_broadcaster_is_no_verified_feed_with_reason(self):
        self.isolate_health()
        for labels, phrase in ((['1Baseball Network'], '1Baseball Network'), (['ByM Sport'], 'ByM Sport'), (['Mystery Sports'], 'Mystery Sports')):
            item, ev = lb.to_event(app, stored(broadcasters_raw=labels), NOW, SOURCE_OK)
            self.assertEqual(item['lvbp']['coverage'], 'none')
            self.assertIn(phrase, item['lvbp']['detail'])
            self.assertEqual(item['feeds'], [])
        item, _ = lb.to_event(app, stored(broadcasters_raw=[]), NOW, SOURCE_OK)
        self.assertIn('No broadcaster is listed', item['lvbp']['detail'])

    def test_cancelled_or_postponed_games_never_claim_a_feed(self):
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW, 'ttl': 21600}])
        for status in ('canceled', 'postponed', 'suspended'):
            self.assertEqual(self.evaluate(stored(status=status))['coverage'], 'none')

    def test_phase_follows_schedule_status_before_time_inference(self):
        for status, phase in (('postponed', 'postponed'), ('canceled', 'canceled'), ('suspended', 'suspended'), ('rescheduled', 'postponed')):
            item, _ = lb.to_event(app, stored(status=status, start=NOW - 3600), NOW, SOURCE_OK)
            self.assertEqual(truth.annotate(item, None, NOW)['event_phase'], phase)
        item, _ = lb.to_event(app, stored(), NOW, SOURCE_OK)
        self.assertEqual(truth.annotate(item, None, NOW)['event_phase'], 'upcoming')
        item, _ = lb.to_event(app, stored(start=NOW - 600), NOW, SOURCE_OK)
        self.assertEqual(truth.annotate(item, None, NOW)['event_phase'], 'on_now')


class ChannelMapping(Base):
    def test_aliases_hd_and_provider_decorations_map_to_one_broadcaster(self):
        for label, expected in [('|VE| Venevision ᴴᴰ', 'venevision'), ('|LATIN| MERIDIANO', 'meridiano'), ('LAT - CANAL I', 'canal-i'),
                                ('LAT - TELEVEN', 'televen'), ('Televen HD', 'televen'), ('Venevisión', 'venevision'), ('IVC Networks', 'ivc'),
                                ('|Venezuela| TVES', 'tves'), ('ByM Sport', 'bym-sport'), ('1Baseball Network', '1baseball-network'),
                                ('LVBP YouTube', 'lvbp-youtube')]:
            self.assertEqual(lb.broadcaster_id(label), expected, label)
        for label in ('Televen Plus', '', None, 'ESPN Deportes', 'Venevision Cine'):
            self.assertIsNone(lb.broadcaster_id(label), label)

    def test_variants_collapse_per_provider_while_provider_alternatives_remain(self):
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW - 10, 'ttl': 3600},
                             {'provider': 'providera', 'stream_id': '232440', 'result': 'timeout', 'observed_at': NOW - 10, 'ttl': 3600}])
        self.catalog('providera', (309879, '|LATIN| MERIDIANO', None), (232440, '|VE| Meridiano', None))
        self.catalog('providerb', (264832, 'LAT - MERIDIANO', None))
        feeds = lb.candidates(app, {'meridiano'}, NOW)
        self.assertEqual([(f['provider_id'], f['stream_id']) for f in feeds], [('providera', '309879'), ('providerb', '264832')])
        self.assertTrue(all('url' not in f and 'password' not in json.dumps(f).lower() for f in feeds))

    def test_variant_choice_is_health_driven_not_name_driven(self):
        self.isolate_health([{'provider': 'providera', 'stream_id': '232440', 'result': 'ok', 'observed_at': NOW - 10, 'ttl': 3600},
                             {'provider': 'providera', 'stream_id': '309879', 'result': 'timeout', 'observed_at': NOW - 10, 'ttl': 3600}])
        self.catalog('providera', (309879, '|LATIN| MERIDIANO', None), (232440, '|VE| Meridiano', None))
        self.assertEqual([f['stream_id'] for f in lb.candidates(app, {'meridiano'}, NOW)], ['232440'])

    def test_missing_catalog_entry_and_wrong_guide_id_do_not_match(self):
        self.isolate_health()
        self.catalog('providera', (73457, '|Venezuela| IVC', None))
        self.assertEqual([f['stream_id'] for f in lb.candidates(app, {'ivc', 'meridiano'}, NOW)], ['73457'])  # Meridiano absent from catalog
        self.catalog('providerb', (1134438, 'LAT - TELEVEN', 'Wrong.id'))
        self.assertEqual(lb.candidates(app, {'televen'}, NOW), [])
        self.catalog('providerb', (1134438, 'LAT - TELEVEN', 'Televen.ve'))
        self.assertEqual([f['stream_id'] for f in lb.candidates(app, {'televen'}, NOW)], ['1134438'])
        self.catalog('providerb', (1134438, 'LAT - TELEVEN', None))  # empty guide ids never contradict
        self.assertEqual(len(lb.candidates(app, {'televen'}, NOW)), 1)

    def test_unassigned_games_get_no_guessed_provider_channels(self):
        self.standard_catalogs()
        item, _ = lb.to_event(app, stored(broadcasters_raw=[]), NOW, SOURCE_OK)
        self.assertEqual(item['feeds'], [])


class ProviderNeutralHealth(Base):
    def feeds(self):
        return [{'provider_id': 'providera', 'stream_id': '1', 'display_name': 'A'}, {'provider_id': 'providerb', 'stream_id': '2', 'display_name': 'A'}]

    def first_provider(self, event_id, now):
        out = verification.annotate(app, {'id': event_id, 'sport': 'Baseball', 'league': lvbp.LEAGUE, 'feeds': self.feeds()}, now=now)
        return out['feeds'][0]['provider_id']

    def test_providera_and_providerb_share_one_ranker_with_no_fixed_preference(self):
        firsts = {self.first_provider('lvbp-%d' % i, NOW) for i in range(40)}
        self.assertEqual(firsts, {'providera', 'providerb'})

    def test_access_error_is_an_expiring_observation_not_a_ban(self):
        records = {}
        for i in range(40):
            event = {'id': 'lvbp-%d' % i}
            records[verification.record_key(event, self.feeds()[1])] = {
                'analysis_version': verification.ANALYSIS_VERSION, 'status': 'unavailable', 'availability': 'probe_failed',
                'content_verified': False, 'transport_verified': False, 'shadow_verdict': 'unavailable',
                'verified_at': NOW, 'expires_at': NOW + verification.FAILURE_TTL}
        app.save_cache('stream-verification', records)
        during = {self.first_provider('lvbp-%d' % i, NOW + 60) for i in range(40)}
        self.assertEqual(during, {'providera'})
        after = {self.first_provider('lvbp-%d' % i, NOW + verification.FAILURE_TTL + 1) for i in range(40)}
        self.assertEqual(after, {'providera', 'providerb'})

    def test_healthy_observation_moves_a_provider_up_only_while_fresh(self):
        events = [{'id': 'lvbp-%d' % i} for i in range(40)]
        records = {}
        for event in events:
            records[verification.record_key(event, self.feeds()[1])] = {
                'analysis_version': verification.ANALYSIS_VERSION, 'status': 'healthy_unconfirmed', 'availability': 'stream_healthy',
                'content_verified': False, 'transport_verified': True, 'shadow_verdict': 'unknown', 'height': 720,
                'verified_at': NOW, 'expires_at': NOW + verification.RESULT_TTL}
        app.save_cache('stream-verification', records)
        self.assertEqual({self.first_provider(e['id'], NOW + 60) for e in events}, {'providerb'})
        self.assertEqual({self.first_provider(e['id'], NOW + verification.RESULT_TTL + 1) for e in events}, {'providera', 'providerb'})


class ContentVerificationInteraction(Base):
    def setUp(self):
        super().setUp()
        self.standard_catalogs()
        self.isolate_health([{'provider': 'providera', 'stream_id': '309879', 'result': 'ok', 'observed_at': NOW - 5, 'ttl': 21600}])

    def record(self, event, feed, verdict, **extra):
        records = app.read_cache('stream-verification') or {}
        records[verification.record_key(event, feed)] = {
            'analysis_version': verification.ANALYSIS_VERSION, 'shadow_verdict': verdict, 'transport_verified': True,
            'availability': 'stream_healthy', 'content_verified': False, 'verified_at': NOW, 'expires_at': NOW + 3600, **extra}
        app.save_cache('stream-verification', records)

    def lvbp_item(self):
        item, _ = lb.to_event(app, stored(broadcasters_raw=['Meridiano TV', 'Televen']), NOW, SOURCE_OK)
        return item

    def test_explicit_wrong_content_is_omitted_and_confirmation_is_withdrawn(self):
        item = self.lvbp_item()
        self.assertEqual(item['lvbp']['coverage'], 'confirmed')
        for feed in item['feeds']:
            self.record(item, feed, 'wrong_content')
        annotated = lb.after_verification(verification.annotate(app, truth.annotate(item, None, NOW), now=NOW))
        self.assertEqual(annotated['feeds'], [])
        self.assertEqual(annotated['verification']['omitted_wrong_content'], len(item['feeds']))
        self.assertEqual((annotated['lvbp']['coverage'], annotated['lvbp']['label']), ('none', 'No verified feed'))

    def test_unknown_verdict_is_retained_and_keeps_confirmation(self):
        item = self.lvbp_item()
        for feed in item['feeds']:
            self.record(item, feed, 'unknown')
        annotated = lb.after_verification(verification.annotate(app, truth.annotate(item, None, NOW), now=NOW))
        self.assertEqual(len(annotated['feeds']), len(item['feeds']))
        self.assertEqual(annotated['lvbp']['coverage'], 'confirmed')

    def test_one_surviving_supporting_channel_keeps_the_claim(self):
        item = self.lvbp_item()
        self.record(item, item['feeds'][0], 'wrong_content')
        annotated = lb.after_verification(verification.annotate(app, truth.annotate(item, None, NOW), now=NOW))
        self.assertEqual(len(annotated['feeds']), len(item['feeds']) - 1)
        self.assertEqual(annotated['lvbp']['coverage'], 'confirmed')

    def test_replay_evidence_is_inert(self):
        item = self.lvbp_item()
        item = {**item, 'event_phase': 'replay'}
        for feed in item['feeds']:
            self.record(item, feed, 'wrong_content')
        annotated = verification.annotate(app, item, now=NOW)
        self.assertEqual(len(annotated['feeds']), len(item['feeds']))
        self.assertEqual(annotated['verification']['omitted_wrong_content'], 0)

    def test_public_event_carries_no_stream_telemetry(self):
        item = self.lvbp_item()
        for feed in item['feeds']:
            self.record(item, feed, 'unknown', height=1080, fps=60, bitrate_mbps=8, open_latency_ms=120)
        annotated = verification.annotate(app, truth.annotate(item, None, NOW), now=NOW)
        blob = json.dumps(annotated)
        for forbidden in ('height', 'fps', 'bitrate', 'open_latency', 'reliability', '_rank', 'password', 'username'):
            self.assertNotIn(forbidden, blob)


def override(**kw):
    base = {'id': 'o1', 'target_type': 'event', 'target_id': 'lvbp-test', 'operation': 'set_assignment',
            'value': {'broadcaster_id': 'televen'}, 'reason': 'League tweet corrected the broadcaster', 'author': 'operator',
            'source_url': 'https://example.org/notice', 'created_at': '2026-10-15T12:00:00+00:00', 'expires_at': '2026-10-20T12:00:00+00:00'}
    base.update(kw)
    return base


class Overrides(Base):
    IDS = {'lvbp-test'}

    def test_valid_override_applies_and_outranks_epg(self):
        self.standard_catalogs()
        self.isolate_health()
        active, rejected = lb.validate_overrides([override()], self.IDS, NOW)
        self.assertEqual((len(active), rejected), (1, []))
        ev = lb.evaluate(app, stored(broadcasters_raw=[]), NOW, [], NOW, active)
        self.assertEqual(ev['assigned'], ['televen'])
        self.assertEqual(ev['official'][0]['evidence_type'], 'manual_override')
        self.assertEqual(ev['coverage'], 'possible')

    def test_remove_and_status_overrides(self):
        self.standard_catalogs()
        active, _ = lb.validate_overrides([override(id='r', operation='remove_assignment', value={'broadcaster_id': 'meridiano'}),
                                           override(id='s', operation='set_status', value={'status': 'postponed'})], self.IDS, NOW)
        ev = lb.evaluate(app, stored(), NOW, [], NOW, active)
        self.assertEqual((ev['assigned'], ev['status'], ev['coverage']), ([], 'postponed', 'none'))

    def test_invalid_overrides_fail_closed_with_reasons(self):
        cases = {
            'missing_field': override(reason=''),
            'source_url_required': override(source_url='http://plain.example'),
            'invalid_expiry': override(expires_at=None),
            'expired': override(expires_at='2026-10-15T12:30:00+00:00', created_at='2026-10-15T12:00:00+00:00'),
            'unknown_event': override(target_id='lvbp-nope'),
            'unknown_operation': override(operation='delete_everything'),
            'unknown_broadcaster': override(value={'broadcaster_id': 'nobody'}),
            'invalid_status': override(operation='set_status', value={'status': 'live'}),
            'invalid_value': override(value='televen'),
            'generic_service_is_not_an_assignment': override(value={'broadcaster_id': 'beisbolplay'}),
        }
        for reason, record in cases.items():
            active, rejected = lb.validate_overrides([record], self.IDS, NOW)
            self.assertEqual(active, [], reason)
            self.assertEqual([r['reason'] for r in rejected], [reason], reason)
        too_long = override(expires_at='2026-12-31T00:00:00+00:00')
        self.assertEqual(lb.validate_overrides([too_long], self.IDS, NOW)[1][0]['reason'], 'invalid_expiry')
        self.assertEqual(lb.validate_overrides(['x'], self.IDS, NOW)[1][0]['reason'], 'not_an_object')

    def test_conflicting_overrides_reject_both(self):
        a = override(id='a', value={'broadcaster_id': 'televen'}, operation='set_assignment')
        b = override(id='b', operation='remove_assignment', value={'broadcaster_id': 'televen'})
        active, rejected = lb.validate_overrides([a, b], self.IDS, NOW)
        self.assertEqual(active, [])
        self.assertEqual({r['id'] for r in rejected}, {'a', 'b'})
        s1 = override(id='s1', operation='set_status', value={'status': 'postponed'})
        s2 = override(id='s2', operation='set_status', value={'status': 'canceled'})
        self.assertEqual(lb.validate_overrides([s1, s2], self.IDS, NOW)[0], [])
        same = override(id='s3', operation='set_status', value={'status': 'postponed'})
        self.assertEqual(len(lb.validate_overrides([s1, same], self.IDS, NOW)[0]), 2)

    def test_expiry_returns_to_source_truth(self):
        self.standard_catalogs()
        active, _ = lb.validate_overrides([override(operation='set_status', value={'status': 'canceled'})], self.IDS, NOW)
        self.assertEqual(lb.evaluate(app, stored(), NOW, [], NOW, active)['status'], 'canceled')
        active, rejected = lb.validate_overrides([override(operation='set_status', value={'status': 'canceled'})], self.IDS, NOW + 6 * 86400)
        self.assertEqual((active, rejected[0]['reason']), ([], 'expired'))

    def test_unreadable_override_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'o.json'
            self.assertEqual(lb.load_overrides(path), ([], 'overrides_unreadable'))
            path.write_text('{not json')
            self.assertEqual(lb.load_overrides(path), ([], 'overrides_unreadable'))
            path.write_text(json.dumps({'version': 2, 'overrides': []}))
            self.assertEqual(lb.load_overrides(path), ([], 'overrides_unreadable'))
            path.write_text(json.dumps({'version': 1, 'overrides': [override()]}))
            self.assertEqual(lb.load_overrides(path)[1], None)
        self.assertEqual(lb.load_overrides(), ([], None))  # the shipped file is valid and empty


class Coverage(Base):
    def setUp(self):
        super().setUp()
        self.standard_catalogs()
        lvbp.refresh(app, fetch=fixture_fetch, now=NOW)

    def expected(self):
        window = [e for e in lvbp.events(app, NOW, days=7) if e['start'] <= NOW + 7 * 86400]
        return window

    def test_report_has_window_denominator_and_separate_sections(self):
        report = lb.coverage_report(app, NOW, window_hours=168, overrides=[])
        window = self.expected()
        self.assertEqual(report['window'], {'start': NOW, 'end': NOW + 168 * 3600, 'hours': 168})
        self.assertEqual(report['denominator'], len(window))
        self.assertGreater(report['denominator'], 20)
        self.assertEqual(report['confirmed'] + report['possible'] + report['no_verified_feed'], report['denominator'])
        mapped = {'meridiano', 'ivc', 'televen', 'venevision'}
        official_mapped = sum(any(lb.broadcaster_id(l) in mapped for l in e['broadcasters_raw']) for e in window)
        self.assertEqual(report['confirmed'], official_mapped)  # the four audit-verified Provider A channels are fresh at NOW
        self.assertEqual(report['mapped_candidates'], official_mapped)
        self.assertEqual(report['official_assignments'], sum(bool(e['broadcasters_raw']) for e in window))
        self.assertIn('ByM Sport', report['gaps']['broadcasters_without_channel'])
        self.assertIn('1Baseball Network', report['gaps']['broadcasters_without_channel'])
        self.assertEqual(report['gaps']['broadcasters_without_channel']['ByM Sport'],
                         sum('ByM Sport' in e['broadcasters_raw'] for e in window))
        self.assertEqual(report['gaps']['unmapped_broadcaster_labels'], {})
        self.assertEqual(report['playback_reliability']['ok'], 7)
        self.assertEqual(report['playback_reliability']['failed'], 10)
        self.assertEqual(report['playback_reliability']['by_result'].get('http_407'), 5)
        self.assertEqual(report['no_assignment'], sum(not e['broadcasters_raw'] for e in window))

    def test_denominator_excludes_postponed_and_canceled_but_reports_them(self):
        cache = app.read_cache(lvbp.CACHE)
        in_window = [e for e in cache['events'] if NOW <= e['start'] <= NOW + 7 * 86400]
        in_window[0]['status'] = 'postponed'
        in_window[1]['status'] = 'canceled'
        app.save_cache(lvbp.CACHE, cache)
        report = lb.coverage_report(app, NOW, overrides=[])
        self.assertEqual(report['denominator'], len(in_window) - 2)
        self.assertEqual(report['out_of_scope'], {'postponed': 1, 'canceled': 1})

    def test_narrower_window_changes_the_denominator_explicitly(self):
        week = lb.coverage_report(app, NOW, window_hours=168, overrides=[])
        two_days = lb.coverage_report(app, NOW, window_hours=48, overrides=[])
        self.assertLess(two_days['denominator'], week['denominator'])
        self.assertEqual(two_days['window']['hours'], 48)

    def test_stale_schedule_is_counted_as_stale_evidence(self):
        later = NOW + 9 * 3600
        report = lb.coverage_report(app, later, window_hours=24, overrides=[])
        self.assertEqual(report['schedule']['state'], 'stale')
        self.assertEqual(report['stale_evidence'], report['denominator'])
        self.assertEqual(report['confirmed'], 0)  # the six-hour audit observations have expired; assignments alone never confirm

    def test_playback_reliability_never_changes_guide_coverage_counts(self):
        a = lb.coverage_report(app, NOW, overrides=[])
        with patch.dict(lvbp.DATA, {'observations': []}):
            b = lb.coverage_report(app, NOW, overrides=[])
        self.assertEqual((a['denominator'], a['official_assignments'], a['mapped_candidates']), (b['denominator'], b['official_assignments'], b['mapped_candidates']))
        self.assertEqual(b['confirmed'], 0)  # without fresh channel health nothing is Confirmed
        self.assertEqual(b['playback_reliability']['ok'], 0)

    def test_override_problems_are_visible_in_the_report(self):
        report = lb.coverage_report(app, NOW, programs=[], overrides=None)
        self.assertEqual(report['overrides'], {'active': 0, 'rejected': [], 'error': None})


class GuideJoinAndApi(Base):
    def setUp(self):
        super().setUp()
        self.standard_catalogs()
        lvbp.refresh(app, fetch=fixture_fetch, now=NOW)
        self.events = lvbp.events(app, NOW)
        self.target = next(e for e in self.events if (e['away_team_id'], e['home_team_id'], e['local_date']) == ('navegantes-del-magallanes', 'tigres-de-aragua', '2026-10-15'))

    def program(self, **kw):
        base = {'id': 'g1', 'title': 'x', 'league': lvbp.LEAGUE, 'sport': 'Baseball', 'is_game': True, 'repeat': False, 'start': self.target['start'],
                'end': self.target['start'] + 10800, 'sides': ['navegantes del magallanes', 'tigres de aragua'],
                'feeds': [{'provider_id': 'providera', 'stream_id': '232442', 'display_name': '|VE| Televen'}]}
        base.update(kw)
        return base

    def test_matching_requires_identity_and_bounded_time_and_never_creates_games(self):
        stored_events = self.events
        hit = lb.match_programs(stored_events, [self.program()])
        self.assertEqual(list(hit), [self.target['id']])
        self.assertEqual(lb.match_programs(stored_events, [self.program(start=self.target['start'] + 3 * 3600)]), {})   # too far in time
        self.assertEqual(lb.match_programs(stored_events, [self.program(sides=['yankees', 'tigres de aragua'])]), {})   # unknown team
        self.assertEqual(lb.match_programs(stored_events, [self.program(repeat=True)]), {})                           # replays never join
        self.assertEqual(lb.match_programs(stored_events, [self.program(league='MLB')]), {})
        self.assertEqual(lb.match_programs(stored_events, [self.program(is_game=False)]), {})
        # A guide entry with no schedule counterpart creates nothing.
        self.assertEqual(lb.match_programs(stored_events, [self.program(sides=['leones del caracas', 'aguilas del zulia'])]), {})
        self.assertEqual(lb.match_programs([], [self.program()]), {})

    def test_doubleheader_program_joins_the_nearest_game_and_ties_stay_unmatched(self):
        d = dt.date(2026, 10, 24)
        rows = lvbp.parse_page(page([(d, [('Tigres', 'Leones', '1:00 PM', 'V', []), ('Tigres', 'Leones', '2:30 PM', 'V', [])]), (d + dt.timedelta(days=1), [])]), d, NOW)
        evs = list(lvbp.build_events(rows, NOW).values())
        early, late = sorted(evs, key=lambda e: e['start'])
        program = {'league': lvbp.LEAGUE, 'is_game': True, 'repeat': False, 'sides': ['tigres de aragua', 'leones del caracas'], 'feeds': [], 'start': late['start'] + 600}
        self.assertEqual(list(lb.match_programs(evs, [program])), [late['id']])
        mid = dict(program, start=(early['start'] + late['start']) // 2)
        self.assertEqual(lb.match_programs(evs, [mid]), {})  # equidistant: never guess

    def test_tv_lvbp_events_join_candidates_and_expose_only_public_fields(self):
        epg = self.program()
        app.save_cache('sports-guide', {'updated_at': NOW, 'source_updated_at': NOW, 'programs': [epg]})
        with patch.object(tv, 'A', app), patch.object(tv, 'now', return_value=NOW):
            events, source = tv.lvbp_events()
        self.assertEqual(source['state'], 'fresh')
        item = next(e for e in events if e['id'] == self.target['id'])
        keys = {(f['provider_id'], f['stream_id']) for f in item['feeds']}
        self.assertIn(('providera', '309879'), keys)       # official Meridiano candidate
        self.assertIn(('providera', '232442'), keys)       # guide-joined listing, disagreeing network
        self.assertEqual(item['lvbp']['broadcasters'], ['Meridiano TV'])
        self.assertEqual(item['league'], lvbp.LEAGUE)
        self.assertEqual(item['event_phase'], 'upcoming')
        other = next(e for e in events if e['id'] != self.target['id'] and 'Televen' in e['lvbp']['broadcasters'])
        self.assertEqual(other['feeds'][0]['availability'], 'scheduled_not_probed')
        self.assertTrue(all(isinstance(e['lvbp']['schedule']['checked_at'], int) for e in events))

    def test_guide_listing_alone_never_invents_a_game_or_appears_in_general_lists(self):
        app.save_cache(lvbp.CACHE, {'season': '2026-27', 'fetched_at': NOW, 'status': {}, 'events': []})
        epg = self.program(title='Baseball · Navegantes vs Tigres', end=NOW + 99999, start=NOW - 60)
        soon = self.program(id='g2', start=NOW + 3600, end=NOW + 9000)
        app.save_cache('sports-guide', {'updated_at': NOW, 'source_updated_at': NOW, 'programs': [epg, soon]})
        with patch.object(tv, 'A', app), patch.object(tv, 'now', return_value=NOW):
            self.assertEqual(tv.lvbp_events()[0], [])
            self.assertIsNone(tv.find_attached_event('g1'))
        with patch.object(tv, 'A', app), patch('time.time', return_value=NOW):
            self.assertEqual(guide.current(app)['events'], [])
            self.assertEqual(guide.later_today(app), [])
            self.assertIsNone(guide.find(app, 'g1', at=NOW))

    def test_find_attached_event_resolves_schedule_events_and_sports_endpoint_lists_them(self):
        app.save_cache('sports-guide', {'updated_at': NOW, 'source_updated_at': NOW, 'programs': []})
        with patch.object(tv, 'A', app), patch.object(tv, 'now', return_value=NOW):
            found = tv.find_attached_event(self.target['id'])
        self.assertEqual(found['id'], self.target['id'])
        self.assertIn('lvbp', found)
        handler = MagicMock()
        handler.headers = {}
        with patch.object(tv, 'A', app), patch.object(tv, 'device', return_value='owner'), patch.object(tv, 'now', return_value=NOW), patch('time.time', return_value=NOW):
            tv.handle(handler, '/tv/sports', False)
        result = handler.send.call_args[0][1]
        self.assertEqual(handler.send.call_args[0][0], 200)
        self.assertIn(lvbp.LEAGUE, result['sports']['Baseball'])
        self.assertTrue(result['lvbp'])
        self.assertEqual(result['lvbp_schedule'], {'state': 'fresh', 'checked_at': NOW})
        self.assertTrue(all(e['league'] == lvbp.LEAGUE for e in result['lvbp']))

    def test_stale_schedule_is_labeled_not_hidden(self):
        later = NOW + 10 * 3600
        with patch.object(tv, 'A', app), patch.object(tv, 'now', return_value=later):
            events, source = tv.lvbp_events()
        self.assertEqual(source['state'], 'stale')
        self.assertTrue(events)
        self.assertTrue(all(e['lvbp']['schedule']['state'] == 'stale' and e['lvbp']['schedule']['checked_at'] == NOW for e in events))

    def test_first_seen_status_rows_without_a_time_stay_visible(self):
        cache = app.read_cache(lvbp.CACHE)
        base = dict(self.target)
        for i, status in enumerate(('postponed', 'suspended', 'canceled', 'rescheduled')):
            cache['events'].append(dict(base, id='lvbp-status-%d' % i, status=status, start=None, local_date='2026-10-16', game_number=1,
                                        doubleheader=False, broadcasters_raw=[]))
        app.save_cache(lvbp.CACHE, cache)
        shown = {e['id']: e for e in lvbp.events(app, NOW) if e['id'].startswith('lvbp-status-')}
        self.assertEqual(len(shown), 4)
        self.assertTrue(all(e['start'] is None for e in shown.values()))  # no first pitch invented
        # a rescheduled row that already has a successor is still hidden
        cache['events'][-1]['superseded_by'] = self.target['id']
        app.save_cache(lvbp.CACHE, cache)
        self.assertNotIn('lvbp-status-3', {e['id'] for e in lvbp.events(app, NOW)})
        with patch.object(tv, 'A', app), patch.object(tv, 'now', return_value=NOW):
            items = {e['id']: e for e in tv.lvbp_events()[0]}
        for i, phase in enumerate(('postponed', 'suspended', 'canceled')):
            item = items['lvbp-status-%d' % i]
            self.assertEqual(item['event_phase'], phase)
            self.assertEqual(item['start'], 0)  # Android renders "Time not listed"
            self.assertEqual(item['lvbp']['coverage'], 'none')
            self.assertEqual(item['feeds'], [])
        report = lb.coverage_report(app, NOW, overrides=[])
        self.assertEqual(report['out_of_scope'], {'postponed': 1, 'suspended': 1, 'canceled': 1})

    def test_status_row_expires_after_its_calendar_day(self):
        cache = app.read_cache(lvbp.CACHE)
        cache['events'].append(dict(self.target, id='lvbp-old', status='postponed', start=None, local_date='2026-10-10', broadcasters_raw=[]))
        app.save_cache(lvbp.CACHE, cache)
        self.assertNotIn('lvbp-old', {e['id'] for e in lvbp.events(app, NOW)})


if __name__ == '__main__':
    unittest.main()
