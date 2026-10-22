import contextlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
import dvr
import tv
import verification

PKT = b'\x47' + b'\x00' * 187


def live(espn='401', home='Tennessee Volunteers', away='Florida Gators', home_id='2633', away_id='57', **extra):
    return {'espn_id': espn, 'home': home, 'away': away, 'home_espn_id': home_id, 'away_espn_id': away_id,
            'state': 'in', 'verified_live': True, 'likely_replay': False, 'completed': False,
            'event_start': 1000, **extra}


def event(sport='Football', league='NCAA', title='Tennessee vs Florida', lv=None, **extra):
    base = {'id': 'g1', 'is_game': True, 'sport': sport, 'league': league, 'title': title,
            'matchup': title, 'start': 1000, 'event_phase': 'live', 'live': lv or live(),
            'feeds': [{'provider_id': 'providera', 'stream_id': '7', 'content_verified': True,
                       'transport_verified': True, 'status': 'verified'}]}
    base.update(extra)
    return base


def barca(title='FC Barcelona vs Sevilla', **extra):
    return event('Soccer', 'La Liga', title,
                 live(espn='700', home='Barcelona', away='Sevilla', home_id='83', away_id='243'), **extra)


class FakeCapacity:
    def __init__(self, free):
        self.value = free

    def free(self):
        return self.value


class FakeBudget:
    def __init__(self, ok=True):
        self.ok, self.log = ok, []

    def acquire(self, pid, owner):
        self.log.append(('acquire', pid, owner))
        return self.ok

    def release(self, pid, owner):
        self.log.append(('release', pid, owner))


def source_of(chunks, fail_after=None):
    @contextlib.contextmanager
    def source():
        def gen():
            for i, chunk in enumerate(chunks):
                if fail_after is not None and i >= fail_after:
                    raise OSError('boom')
                yield chunk
        yield gen()
    return source


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'dvr'
        self.free = FakeCapacity(100 << 30)
        self.clock = [1000]
        self.store = dvr.Store(self.root, capacity=self.free, max_bytes=1 << 20, min_free=1 << 20, clock=lambda: self.clock[0])

    def rec(self, ev, chunks=(PKT * 4,), status=None, budget=None, bucket=None, **kw):
        bucket = bucket or dvr.bucket_for(ev)[0]
        return dvr.record(self.store, bucket, ev, ev['feeds'][0], source_of(chunks, kw.pop('fail_after', None)),
                          budget or FakeBudget(), status or (lambda: 'final'), sleep=lambda s: None, **kw)


class EligibilityTests(unittest.TestCase):
    def test_exactly_three_supported_buckets(self):
        self.assertEqual(set(dvr.BUCKETS), {'tennessee-football', 'tennessee-softball', 'fc-barcelona'})
        self.assertEqual(dvr.bucket_for(event())[0], 'tennessee-football')
        soft = event('Softball', 'NCAA', 'Tennessee vs Florida', live(home='Tennessee Lady Volunteers', home_id='611'))
        self.assertEqual(dvr.bucket_for(soft)[0], 'tennessee-softball')
        self.assertEqual(dvr.bucket_for(barca())[0], 'fc-barcelona')

    def test_wrong_sport_or_team_is_refused(self):
        self.assertEqual(dvr.bucket_for(event('Softball', 'NCAA'))[1], 'unsupported_team_or_sport')  # football id on softball
        self.assertEqual(dvr.bucket_for(event('Baseball', 'MLB'))[1], 'unsupported_team_or_sport')
        other = event(lv=live(home='Alabama', home_id='333', away='Auburn', away_id='2'))
        self.assertEqual(dvr.bucket_for(other)[1], 'unsupported_team_or_sport')
        espanyol = event('Soccer', 'La Liga', 'RCD Espanyol vs Sevilla', live(espn='9', home='Espanyol', away='Sevilla', home_id='88', away_id='243'))
        self.assertIsNone(dvr.bucket_for(espanyol)[0])

    def test_womens_barcelona_refused(self):
        ev = barca(title='FC Barcelona Femeni vs Sevilla')
        self.assertIsNotNone(dvr.bucket_for(ev)[1])

    def test_replay_and_non_live_fail_closed(self):
        for patch_ in ({'event_phase': 'replay'}, {'event_phase': 'on_now'}, {'event_phase': 'pregame'}, {'repeat': True}, {'replay': True}):
            self.assertIsNone(dvr.bucket_for(event(**patch_))[0], patch_)
        self.assertIsNone(dvr.bucket_for(event(lv=live(likely_replay=True)))[0])
        self.assertIsNone(dvr.bucket_for(event(lv=live(verified_live=False)))[0])
        self.assertIsNone(dvr.bucket_for(event(lv=live(completed=True)))[0])
        self.assertEqual(dvr.bucket_for({'is_game': True, 'sport': 'Football', 'league': 'NCAA'})[1], 'not_verified_live')

    def test_missing_or_ambiguous_identity_refused(self):
        self.assertEqual(dvr.bucket_for(event(lv=live(espn=None)))[1], 'no_event_identity')
        # Guide names a different matchup from the one ESPN matched.
        self.assertEqual(dvr.bucket_for(event(title='Tennessee vs Georgia'))[1], 'ambiguous_identity')
        self.assertEqual(dvr.bucket_for(event(title='Tennessee football highlights'))[1], 'ambiguous_identity')
        self.assertIsNone(dvr.bucket_for(event(is_game=False))[0])

    def test_feed_requires_content_and_transport_verification(self):
        ev = event()
        ev['feeds'] = [{'provider_id': 'a', 'stream_id': '1', 'content_verified': False, 'transport_verified': True},
                       {'provider_id': 'b', 'stream_id': '2', 'content_verified': True, 'transport_verified': False},
                       {'provider_id': 'c', 'stream_id': '3', 'content_verified': True, 'transport_verified': True}]
        self.assertEqual(dvr.pick_feed(ev)['provider_id'], 'c')
        ev['feeds'] = ev['feeds'][:2]
        self.assertIsNone(dvr.pick_feed(ev))
        self.assertEqual(dvr.plan([ev]), [])

    def test_plan_dedups_merged_listings_by_event_identity(self):
        a, b = event(), event(id='g2')
        self.assertEqual(len(dvr.plan([a, b])), 1)
        self.assertEqual(len(dvr.plan([a, barca()])), 2)


class RecordTests(Base):
    def test_successful_recording_is_from_beginning_and_releases_budget(self):
        budget = FakeBudget()
        meta = self.rec(event(), budget=budget)
        self.assertEqual((meta['state'], meta['complete_start'], meta['complete_end'], meta['bytes']), ('ready', True, True, 188 * 4))
        self.assertEqual([a for a, *_ in budget.log], ['acquire', 'release'])
        self.assertEqual(self.store.media_path(meta['id']).stat().st_size, 188 * 4)
        self.assertNotIn('stream_id', json.dumps(self.store.listing()))

    def test_late_start_is_not_claimed_from_beginning(self):
        self.clock[0] = 1000 + 1      # even one second late is not "from the beginning"
        meta = self.rec(event())
        self.assertFalse(meta['complete_start'])

    def test_partial_chunks_are_packet_aligned(self):
        meta = self.rec(event(), chunks=(PKT[:100], PKT[100:] + PKT[:50]))
        self.assertEqual(meta['bytes'], 188)

    def test_provider_busy_fails_without_media_and_without_opening(self):
        opened = []
        budget = FakeBudget(ok=False)
        ev = event()
        out = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], lambda: opened.append(1), budget)
        self.assertEqual((out['state'], out['reason']), ('failed', 'provider_busy'))
        self.assertEqual(opened, [])
        self.assertFalse(any(a == 'release' for a, *_ in budget.log))

    def test_empty_source_fails_and_releases(self):
        budget = FakeBudget()
        meta = self.rec(event(), chunks=(), budget=budget, status=lambda: 'unknown')
        self.assertEqual((meta['state'], meta['reason']), ('failed', 'provider_unavailable'))
        self.assertEqual(budget.log[-1][0], 'release')

    def test_source_error_midway_keeps_playable_partial_and_releases(self):
        budget = FakeBudget()
        meta = self.rec(event(), chunks=(PKT, PKT, PKT), fail_after=2, budget=budget, status=lambda: 'unknown')
        self.assertEqual((meta['state'], meta['reason'], meta['complete_end'], meta['bytes']), ('ready', 'source_ended_early', False, 376))
        self.assertEqual(budget.log[-1][0], 'release')

    def test_reconnect_reuses_the_one_held_slot(self):
        budget, verdicts = FakeBudget(), iter(['live', 'live', 'final'])
        calls = []

        @contextlib.contextmanager
        def source():
            calls.append(1)
            yield iter([PKT])
        ev = event()
        meta = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], source, budget, lambda: next(verdicts), sleep=lambda s: None)
        self.assertEqual(len(calls), 3)
        self.assertEqual([a for a, *_ in budget.log], ['acquire', 'release'])
        self.assertTrue(meta['complete_end'])

    def test_cancellation_stops_and_releases(self):
        stop, budget = threading.Event(), FakeBudget()

        @contextlib.contextmanager
        def source():
            def gen():
                yield PKT
                stop.set()
                yield PKT
            yield gen()
        ev = event()
        meta = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], source, budget, lambda: 'live', stop, sleep=lambda s: None)
        self.assertEqual((meta['state'], meta['reason'], meta['complete_end']), ('ready', 'cancelled', False))
        self.assertEqual(budget.log[-1][0], 'release')

    def test_disk_write_error_ends_with_storage_pressure_and_releases(self):
        budget = FakeBudget()
        with patch('builtins.open', side_effect=OSError('full')):
            meta = self.rec(event(), budget=budget)
        self.assertEqual(meta['reason'], 'storage_pressure')
        self.assertEqual(budget.log[-1][0], 'release')


class RetentionTests(Base):
    def game(self, n, bucket='tennessee-football'):
        ev = event(lv=live(espn=str(n), event_start=1000 + n * 100))
        ev['start'] = 1000 + n * 100
        return ev

    def test_last_three_per_bucket_independent(self):
        self.clock[0] = 1000
        for n in range(1, 6):
            self.clock[0] = 1000 + n * 100
            self.rec(self.game(n))
        self.rec(barca())
        football = [m for m in self.store.bucket('tennessee-football') if m['state'] == 'ready']
        self.assertEqual([m['espn_id'] for m in football], ['5', '4', '3'])
        expired = [m for m in self.store.bucket('tennessee-football') if m['state'] == 'expired']
        self.assertTrue(all(m['reason'] == 'evicted' and not self.store.media_path(m['id']).exists() for m in expired))
        self.assertEqual(len(self.store.bucket('fc-barcelona')), 1)
        self.assertEqual(len(self.store.bucket('tennessee-softball')), 0)

    def test_eviction_is_deterministic_regardless_of_arrival_order(self):
        outcomes = []
        for order in ([1, 2, 3, 4], [4, 3, 2, 1], [2, 4, 1, 3]):
            with tempfile.TemporaryDirectory() as tmp:
                store = dvr.Store(Path(tmp), capacity=self.free, max_bytes=1 << 20, min_free=1 << 20, clock=lambda: 5)
                for n in order:
                    ev = self.game(n)
                    dvr.record(store, 'tennessee-football', ev, ev['feeds'][0], source_of([PKT]), FakeBudget(), lambda: 'final', sleep=lambda s: None)
                outcomes.append([m['espn_id'] for m in store.bucket('tennessee-football') if m['state'] == 'ready'])
        self.assertEqual(outcomes, [['4', '3', '2']] * 3)

    def test_oldest_survives_when_new_recording_never_gets_media(self):
        for n in (1, 2, 3):
            self.rec(self.game(n))
        self.rec(self.game(4), chunks=(), status=lambda: 'unknown')
        ready = [m['espn_id'] for m in self.store.bucket('tennessee-football') if m['state'] == 'ready']
        self.assertEqual(sorted(ready), ['1', '2', '3'])

    def test_same_event_not_recorded_twice(self):
        self.rec(self.game(1))
        again = self.rec(self.game(1))
        self.assertEqual((again['state'], again['reason']), ('failed', 'already_recorded'))
        self.assertEqual(self.store.read(again['id'])['state'], 'ready')


class StorageTests(Base):
    def test_low_space_refuses_without_opening_or_acquiring(self):
        self.free.value = 1 << 20      # nothing above the reserve
        budget = FakeBudget()
        meta = self.rec(event(), budget=budget)
        self.assertEqual(meta['reason'], 'storage_pressure')
        self.assertEqual(budget.log, [])
        self.assertEqual(self.store.listing()['storage'], 'pressure')

    def test_unknown_capacity_fails_closed(self):
        self.free.value = None
        self.assertEqual(self.rec(event())['reason'], 'capacity_unknown')
        self.assertEqual(self.store.listing()['storage'], 'unknown')

    def test_eviction_reclaim_counts_toward_admission(self):
        for n in (1, 2, 3):
            ev = event(lv=live(espn=str(n), event_start=1000 + n)); ev['start'] = 1000 + n
            self.rec(ev, chunks=(PKT * 100,))
        self.free.value = (2 << 20) - 18800 - 1          # one byte short even after reclaiming the oldest
        ev = event(lv=live(espn='9', event_start=2000)); ev['start'] = 2000
        self.assertEqual(self.store.admit('tennessee-football', dvr.recording_id('tennessee-football', ev)), (False, 'storage_pressure'))
        self.free.value = (2 << 20) - 18800              # exactly enough once the oldest is reclaimed
        self.assertEqual(self.store.admit('tennessee-football', dvr.recording_id('tennessee-football', ev)), (True, None))

    def test_pressure_midway_stops_with_playable_partial(self):
        meta_capacity = FakeCapacity(100 << 30)
        store = dvr.Store(self.root / 'x', capacity=meta_capacity, max_bytes=1 << 20, min_free=1 << 20, clock=lambda: 10_000)
        ev = event()
        times = iter(range(0, 10_000, 100))
        with patch('dvr.time.monotonic', side_effect=lambda: next(times)):
            def chunks():
                meta_capacity.value = 0
                return source_of([PKT, PKT, PKT])()
            meta = dvr.record(store, 'tennessee-football', ev, ev['feeds'][0], chunks, FakeBudget(), lambda: 'live', sleep=lambda s: None)
        self.assertEqual((meta['state'], meta['reason'], meta['complete_end']), ('ready', 'storage_pressure', False))
        self.assertTrue(meta['playable'])

    def test_per_recording_cap(self):
        store = dvr.Store(self.root / 'cap', capacity=self.free, max_bytes=188 * 2, min_free=0, clock=lambda: 10_000)
        ev = event()
        meta = dvr.record(store, 'tennessee-football', ev, ev['feeds'][0], source_of([PKT] * 10), FakeBudget(), lambda: 'live', sleep=lambda s: None)
        self.assertEqual(meta['reason'], 'storage_pressure')
        self.assertEqual(meta['bytes'], 188 * 2)


class RecoveryTests(Base):
    def seed(self, rid, state, data=b'', bucket='tennessee-football', start=1, **extra):
        meta = {'id': rid, 'bucket': bucket, 'state': state, 'event_start': start, 'created_at': start, 'bytes': len(data),
                'title': 't', 'espn_id': rid, 'playable': bool(data), **extra}
        self.store.write(meta)
        if data is not None:
            self.store.media_path(rid).write_bytes(data)

    def test_interrupted_recording_becomes_honest_partial(self):
        self.seed('a', 'recording', PKT * 2 + b'xx')
        self.seed('b', 'recording', b'')
        self.store.recover()
        a, b = self.store.read('a'), self.store.read('b')
        self.assertEqual((a['state'], a['reason'], a['bytes'], a['complete_end']), ('ready', 'interrupted', 376, False))
        self.assertEqual(self.store.media_path('a').stat().st_size, 376)
        self.assertEqual((b['state'], b['reason']), ('failed', 'interrupted'))

    def test_missing_media_is_expired_not_ready(self):
        self.seed('a', 'ready', PKT)
        self.store.media_path('a').unlink()
        self.store.recover()
        self.assertEqual((self.store.read('a')['state'], self.store.read('a')['reason']), ('expired', 'media_missing'))

    def test_orphans_and_temp_files_cleaned_and_recover_idempotent(self):
        (self.root / 'orphan').mkdir()
        (self.root / 'orphan' / 'media.ts').write_bytes(PKT)
        self.seed('a', 'ready', PKT)
        (self.root / 'a' / 'meta.json.tmp').write_text('{')
        self.store.recover()
        snapshot = {m['id']: dict(m, updated_at=0) for m in self.store.all()}
        self.store.recover()
        self.assertFalse((self.root / 'orphan').exists())
        self.assertFalse((self.root / 'a' / 'meta.json.tmp').exists())
        self.assertEqual(snapshot, {m['id']: dict(m, updated_at=0) for m in self.store.all()})

    def test_recover_applies_retention(self):
        for n in range(1, 6):
            self.seed(f'r{n}', 'ready', PKT, start=n)
        self.store.recover()
        self.assertEqual([m['id'] for m in self.store.bucket('tennessee-football') if m['state'] == 'ready'], ['r5', 'r4', 'r3'])

    def test_unsafe_ids_rejected(self):
        for rid in ('../x', 'a/b', '', '.hidden'):
            with self.assertRaises(ValueError):
                self.store.dir(rid)


class BudgetTests(unittest.TestCase):
    def test_single_hold_and_busy_provider_blocks(self):
        busy = {'providera': False}
        budget = dvr.Budget(threading.RLock(), lambda pid: busy.get(pid, False))
        self.assertTrue(budget.acquire('providera', 'a'))
        self.assertFalse(budget.acquire('providera', 'b'))
        self.assertTrue(budget.acquire('other', 'b'))
        budget.release('providera', 'a')
        busy['providera'] = True
        self.assertFalse(budget.acquire('providera', 'c'))
        busy['providera'] = False
        self.assertTrue(budget.acquire('providera', 'c'))
        budget.release('providera', 'a')            # releasing a non-owner changes nothing
        self.assertEqual(budget.held('providera'), 1)

    def test_concurrent_acquires_never_exceed_limit(self):
        budget = dvr.Budget(threading.RLock(), lambda pid: False)
        wins = []
        threads = [threading.Thread(target=lambda i=i: wins.append(budget.acquire('p', str(i)))) for i in range(20)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(sum(wins), 1)


class TvIntegrationTests(Base):
    def setUp(self):
        super().setUp()
        p = patch.object(app, 'DATA', self.root.parent)
        p.start(); self.addCleanup(p.stop)
        tv.DVR_STORE = self.store; tv.DVR_STOPS.clear(); tv.DVR_BUDGET.holds.clear()
        tv.PAIR.clear(); tv.ACTIVE.clear(); tv.VERIFYING.clear()
        tv.configure(app)

    def test_budget_sees_playback_and_verification(self):
        app.save_cache('tv-sessions', {'s': {'provider_id': 'providera', 'expires': tv.now() + 60, 'owner': 'o'}})
        self.assertFalse(tv.DVR_BUDGET.acquire('providera', 'x'))
        app.save_cache('tv-sessions', {})
        tv.VERIFYING.add('providera')
        self.assertFalse(tv.DVR_BUDGET.acquire('providera', 'x'))
        tv.VERIFYING.clear()
        self.assertTrue(tv.DVR_BUDGET.acquire('providera', 'x'))

    def test_playback_and_verification_refuse_while_dvr_holds_slot(self):
        self.assertTrue(tv.DVR_BUDGET.acquire('providera', 'dvr:x'))
        with self.assertRaises(verification.Busy):
            tv.sample_feed({}, {'provider_id': 'providera', 'stream_id': '1'})
        game = {'fixture': {'id': 't', 'kickoff': 0}, 'feeds': [{'provider_id': 'providera', 'stream_id': '42'}]}
        with patch.object(app, 'resolve', return_value=game), self.assertRaises(ValueError) as ctx:
            tv.begin('o', {'fixture_id': 't', 'provider_id': 'providera', 'stream_id': '42'})
        self.assertIn('recording', str(ctx.exception))

    def test_tick_disabled_by_default_and_never_starts_for_unverified(self):
        with patch.dict('os.environ', {}, clear=False):
            self.assertEqual(tv.dvr_tick(), [])

    def test_recordings_endpoint_lists_and_media_serves_from_start(self):
        ev = event()
        store = tv.dvr_store()
        meta = dvr.record(store, 'tennessee-football', ev, ev['feeds'][0], source_of([PKT * 3]), FakeBudget(), lambda: 'final', sleep=lambda s: None)
        handler = MagicMock(); handler.headers = {'Authorization': 'Bearer t'}
        with patch.object(tv, 'device', return_value='owner'):
            tv.handle(handler, '/tv/recordings', False)
        body = handler.send.call_args[0][1]
        self.assertFalse(body['enabled'])
        rows = {b['id']: b['recordings'] for b in body['buckets']}
        self.assertEqual(rows['tennessee-football'][0]['state'], 'ready')
        self.assertEqual(rows['fc-barcelona'], [])
        out = []
        handler2 = MagicMock(); handler2.wfile.write.side_effect = out.append
        tv.dvr_media(handler2, meta['id'])
        self.assertEqual(b''.join(out), PKT * 3)
        handler3 = MagicMock()
        tv.dvr_media(handler3, 'missing-id')
        self.assertEqual(handler3.send.call_args[0][0], 404)
        store.media_path(meta['id']).unlink()
        handler4 = MagicMock()
        tv.dvr_media(handler4, meta['id'])
        self.assertEqual(handler4.send.call_args[0][0], 404)
        self.assertEqual(store.read(meta['id'])['reason'], 'media_missing')



def pregame(**extra):
    ev = event(event_phase='pregame', lv=live(state='in', verified_live=False))
    ev['live'] = live(verified_live=False, state='pre', event_start=1000)
    ev.update(extra)
    ev['feeds'] = [{'provider_id': 'providera', 'stream_id': '7'}]
    return ev


class PreRollTests(Base):
    def setUp(self):
        super().setUp()
        for name, value in (('RECHECK_SECONDS', 0), ('VERIFY_MIN_BYTES', 188 * 2)):
            p = patch.object(dvr, name, value)
            p.start(); self.addCleanup(p.stop)
        self.clock[0] = 800                      # 200 s before the scheduled start

    def run_pre(self, truths, verdicts=('confirmed',), chunks=60, advance=60, **kw):
        """Provisional capture; the clock advances per chunk, truth and verdicts come from fixed scripts."""
        truth_iter, verdict_iter, budget = iter(truths), iter(verdicts), FakeBudget()
        last = {'t': truths[-1]}

        def status():
            last['t'] = next(truth_iter, last['t'])
            return last['t']

        def verify(path, offset, length):
            self.assertTrue(Path(path).exists())
            return next(verdict_iter, 'unknown')

        def gen():
            for _ in range(chunks):
                self.clock[0] += advance
                yield PKT * 4
        ev = pregame()

        @contextlib.contextmanager
        def source():
            yield gen()
        meta = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], source, budget, status,
                          sleep=lambda s: None, provisional=True, verify=verify, **kw)
        return meta, budget

    def test_only_pregame_inside_the_preroll_window_is_planned(self):
        ev = pregame()
        self.assertEqual(len(dvr.plan_preroll([ev], 1000 - dvr.PREROLL_SECONDS)), 1)
        self.assertEqual(dvr.plan_preroll([ev], 1000 - dvr.PREROLL_SECONDS - 1), [])
        self.assertEqual(dvr.plan_preroll([ev], 1001), [])
        self.assertEqual(dvr.plan_preroll([event()], 900), [])                    # live is not pre-roll
        self.assertEqual(dvr.plan_preroll([pregame(replay=True)], 900), [])
        self.assertEqual(dvr.plan_preroll([pregame(repeat=True)], 900), [])
        self.assertEqual(dvr.plan_preroll([pregame(broadcast_check={'status': 'disagreement'})], 900), [])
        other = pregame(); other['live'] = live(home='Alabama', home_id='333', verified_live=False, state='pre')
        self.assertEqual(dvr.plan_preroll([other], 900), [])
        bad = pregame(); bad['feeds'] = [{'provider_id': 'a', 'stream_id': '1', 'availability': 'probe_failed'}]
        self.assertEqual(dvr.plan_preroll([bad], 900), [])

    def test_confirmed_capture_is_promoted_and_claims_the_beginning(self):
        meta, budget = self.run_pre(['pre', 'pre', 'live', 'live', 'live', 'final'])
        self.assertEqual(meta['state'], 'ready')
        self.assertTrue(meta['complete_start'])
        self.assertLess(meta['started_at'], meta['event_start'])
        self.assertTrue(meta['playable'])
        self.assertTrue(self.store.check_media(meta, full=True)[0])
        self.assertEqual([a for a, *_ in budget.log], ['acquire', 'release'])

    def test_capture_is_hidden_and_not_retained_until_confirmed(self):
        for n in (1, 2, 3):
            ev = event(lv=live(espn=str(n), event_start=10 + n)); ev['start'] = 10 + n
            self.rec(ev)
        seen = []

        def verify_probe(path, offset, length):
            seen.append(([m['state'] for m in self.store.bucket('tennessee-football')],
                         [r['id'] for b in self.store.listing()['buckets'] for r in b['recordings']]))
            return 'confirmed'
        with patch.object(dvr, 'RECHECK_SECONDS', 0):
            self.run_pre_with(verify_probe)
        states, listed = seen[0]
        self.assertEqual(states.count('ready'), 3)           # nothing was evicted for an unconfirmed capture
        self.assertIn('provisional', states)
        self.assertEqual(len(listed), 3)                     # and it was never listed

    def run_pre_with(self, verify):
        ev = pregame(); budget = FakeBudget(); truths = iter(['pre', 'live', 'live', 'live'])

        @contextlib.contextmanager
        def source():
            def gen():
                for _ in range(20):
                    self.clock[0] += 60
                    yield PKT * 4
            yield gen()
        return dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], source, budget,
                          lambda: next(truths, 'live'), sleep=lambda s: None, provisional=True, verify=verify)

    def test_wrong_content_is_discarded_and_never_playable(self):
        meta, budget = self.run_pre(['pre', 'live'], verdicts=('wrong',))
        self.assertEqual((meta['state'], meta['reason'], meta['playable'], meta['bytes']), ('failed', 'wrong_content', False, 0))
        self.assertFalse(self.store.media_path(meta['id']).exists())
        self.assertEqual(budget.log[-1][0], 'release')
        self.assertEqual([m for m in self.store.bucket('tennessee-football') if self.store.holding_media(m)], [])

    def test_never_confirmed_is_discarded_as_unverified(self):
        meta, _ = self.run_pre(['pre', 'live'], verdicts=())
        self.assertEqual((meta['state'], meta['reason']), ('failed', 'unverified'))
        self.assertFalse(self.store.media_path(meta['id']).exists())

    def test_verification_deadline_bounds_a_live_but_unconfirmed_capture(self):
        meta, _ = self.run_pre(['pre', 'live'], verdicts=(), advance=dvr.VERIFY_DEADLINE, chunks=5)
        self.assertEqual(meta['reason'], 'unverified')

    def test_provisional_never_survives_cancel_eof_or_error(self):
        stop = threading.Event(); stop.set()
        meta, budget = self.run_pre(['pre'], stop=stop)
        self.assertEqual((meta['state'], meta['reason']), ('failed', 'cancelled'))
        self.assertEqual(budget.log[-1][0], 'release')
        meta, _ = self.run_pre(['pre'], chunks=1)             # source ends while still unconfirmed
        self.assertEqual((meta['state'], meta['playable']), ('failed', False))

    def test_schedule_moved_far_later_abandons_the_preroll(self):
        later = {'phase': 'pre', 'event_start': 800 + dvr.PREROLL_SECONDS + 600}
        meta, _ = self.run_pre([later])
        self.assertEqual((meta['state'], meta['reason']), ('failed', 'schedule_changed'))

    def test_postponed_or_never_started_is_discarded(self):
        meta, _ = self.run_pre(['postponed'])
        self.assertEqual(meta['reason'], 'schedule_changed')
        meta, _ = self.run_pre(['pre'], advance=dvr.SCHEDULE_GRACE + 1000, chunks=3)
        self.assertEqual(meta['reason'], 'not_started')

    def test_small_schedule_slip_keeps_the_beginning_but_earlier_start_does_not(self):
        slip = {'phase': 'pre', 'event_start': 1200}
        meta, _ = self.run_pre([slip, {'phase': 'live', 'event_start': 1200}])
        self.assertTrue(meta['complete_start'])

    def test_game_that_began_before_capture_never_claims_the_beginning(self):
        early = {'phase': 'live', 'event_start': 700}        # game really began before capture did
        meta, _ = self.run_pre([early])
        self.assertEqual(meta['state'], 'ready')
        self.assertFalse(meta['complete_start'])

    def test_post_roll_is_bounded(self):
        truths = ['pre', 'live', 'live', 'final']
        meta, budget = self.run_pre(truths, chunks=100000, advance=30)
        self.assertTrue(meta['complete_end'])
        # final was seen at some point; capture stopped POST_ROLL_SECONDS later, long before the 100000 chunks
        self.assertLess(meta['bytes'], 188 * 4 * 100)
        self.assertEqual(budget.log[-1][0], 'release')

    def test_post_roll_not_claimed_when_cut_short(self):
        stop = threading.Event()
        calls = []

        def status():
            calls.append(1)
            if len(calls) == 3:
                stop.set()
            return 'live'
        ev = event()

        @contextlib.contextmanager
        def source():
            yield iter([PKT] * 10)
        meta = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], source, FakeBudget(), status, stop, sleep=lambda s: None)
        self.assertFalse(meta['complete_end'])

    def test_one_provider_slot_shared_with_verification_and_playback(self):
        guard = threading.RLock()
        busy = {'v': False}
        budget = dvr.Budget(guard, lambda pid: busy['v'])
        meta, _ = self.run_pre(['pre'], chunks=1)             # unrelated: runs on a fake budget
        busy['v'] = True                                      # verification/playback owns the provider
        ev = pregame()
        out = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], lambda: None, budget, provisional=True)
        self.assertEqual((out['state'], out['reason']), ('failed', 'provider_busy'))
        busy['v'] = False
        self.assertTrue(budget.acquire('providera', 'dvr:other'))
        out = dvr.record(self.store, 'tennessee-football', ev, ev['feeds'][0], lambda: None, budget, provisional=True)
        self.assertEqual(out['reason'], 'provider_busy')      # and a second DVR hold is refused too


class IntegrityTests(Base):
    def test_ready_media_is_checked_after_capture_and_on_recovery(self):
        meta = self.rec(event())
        self.assertTrue(self.store.check_media(meta, full=True)[0])
        path = self.store.media_path(meta['id'])
        data = bytearray(path.read_bytes()); data[200] ^= 0xFF; path.write_bytes(bytes(data))
        self.assertEqual(self.store.check_media(meta, full=True), (False, 'integrity_failed'))
        self.assertTrue(self.store.check_media(meta)[0])      # cheap check cannot see a mid-file flip
        path.write_bytes(b'\x00' * 188 * 4)                    # lost sync bytes
        self.store.recover()
        self.assertEqual((self.store.read(meta['id'])['state'], self.store.read(meta['id'])['reason']), ('expired', 'integrity_failed'))
        self.assertFalse(path.exists())

    def test_recovery_discards_provisional_and_unplayable_recordings(self):
        base = {'bucket': 'tennessee-football', 'event_start': 5, 'created_at': 5, 'title': 't'}
        self.store.write({**base, 'id': 'p', 'state': 'provisional', 'bytes': 188, 'playable': False, 'espn_id': 'p'})
        self.store.media_path('p').write_bytes(PKT)
        self.store.write({**base, 'id': 'q', 'state': 'recording', 'bytes': 188, 'playable': False, 'espn_id': 'q'})
        self.store.media_path('q').write_bytes(PKT)
        self.store.write({**base, 'id': 'z', 'state': 'failed', 'bytes': 188, 'playable': False, 'espn_id': 'z'})
        self.store.media_path('z').write_bytes(PKT)
        self.store.recover()
        for rid in ('p', 'q', 'z'):
            self.assertEqual(self.store.read(rid)['state'], 'failed', rid)
            self.assertFalse(self.store.media_path(rid).exists(), rid)
        self.assertEqual(self.store.read('p')['reason'], 'interrupted')
        self.assertEqual([m for m in self.store.bucket('tennessee-football') if self.store.holding_media(m)], [])

    def test_retention_ignores_provisional_when_enforcing(self):
        for n in (1, 2, 3):
            ev = event(lv=live(espn=str(n), event_start=10 + n)); ev['start'] = 10 + n
            self.rec(ev)
        self.store.write({'id': 'prov', 'bucket': 'tennessee-football', 'state': 'provisional', 'event_start': 99, 'created_at': 99,
                          'bytes': 188, 'playable': False, 'espn_id': 'p', 'title': 't'})
        self.store.media_path('prov').write_bytes(PKT)
        self.store.enforce('tennessee-football')
        self.assertEqual(sorted(m['state'] for m in self.store.bucket('tennessee-football')), ['provisional', 'ready', 'ready', 'ready'])


if __name__ == '__main__':
    unittest.main()
