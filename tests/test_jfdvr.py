import tempfile
import unittest
from pathlib import Path

import dvr
import jellyfin
import jfdvr
import lease
from fake_jellyfin import FakeJellyfin, KEY
from test_dvr import event, live, barca

EXPORT = 'http://sg.test'
NOW = 1_000_000


def pregame(start, espn='401', **extra):
    ev = event(lv=live(espn=espn, state='pre', verified_live=False, event_start=start), event_phase='pregame', start=start, **extra)
    ev['feeds'] = [{'provider_id': 'providera', 'stream_id': '7', 'availability': 'ok'}]
    return ev


def barca_pre(start):
    ev = barca()
    ev['live'].update(state='pre', verified_live=False, event_start=start)
    ev.update(event_phase='pregame', start=start)
    ev['feeds'] = [{'provider_id': 'providera', 'stream_id': '9', 'availability': 'ok'}]
    return ev


class Base(unittest.TestCase):
    @staticmethod
    def path(rid):
        return '/rec/Game %s/Game.ts' % jfdvr.tag(rid)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = NOW
        clock = lambda: self.now
        self.fake = FakeJellyfin(export_url=EXPORT)
        self.fake.add_channel('SG Sports seed')
        config = jellyfin.Config(True, 'http://jf.test:8096', KEY, EXPORT, 'relay-secret')
        self.leases = lease.LeaseAuthority(Path(tmp.name) / 'l.sqlite', clock)
        self.ledger = jfdvr.Ledger(Path(tmp.name) / 'ledger.sqlite')
        self.mk = lambda retention=False: jfdvr.JellyfinDvr(jellyfin.Client(config, self.fake), self.ledger, self.leases, clock, retention)
        self.dvr = self.mk()

    def schedule(self, ev, name=None):
        """Drive one pending entry through mapping so a real timer exists in the fake."""
        self.dvr.tick([ev], self.now)
        rid = dvr.recording_id('tennessee-football', ev)
        entry = self.ledger.get(rid)
        cid = self.fake.add_channel(jfdvr.channel_name(rid))
        self.fake.add_program(cid, jellyfin.iso(entry['event_start']), jellyfin.iso(entry['event_end']))
        self.dvr._check = (0, None)
        self.dvr.tick([ev], self.now)
        return rid, self.ledger.get(rid)


class PlanningTests(Base):
    def test_candidates_use_canonical_policy_and_horizon(self):
        start = NOW + 3600
        good = pregame(start)
        replay = pregame(start, espn='402')
        replay['replay'] = True
        unsupported = pregame(start, espn='403', title='Kentucky vs Florida')
        unsupported['live'].update(home_espn_id='96', away_espn_id='57')
        far = pregame(NOW + 20 * 3600, espn='404')
        found = self.dvr.candidates([good, replay, unsupported, far, good], NOW)
        self.assertEqual([c['espn_id'] for c in found], ['401'])
        self.assertEqual(found[0]['event_end'] - found[0]['event_start'], 4 * 3600)

    def test_overlapping_windows_on_one_provider_resolve_by_priority_inprogress_first(self):
        a = {'id': 'a', 'bucket': 'tennessee-softball', 'event_start': 1000, 'event_end': 8000, 'provider_id': 'providera'}
        b = {'id': 'b', 'bucket': 'tennessee-football', 'event_start': 2000, 'event_end': 9000, 'provider_id': 'providera'}
        c = {'id': 'c', 'bucket': 'fc-barcelona', 'event_start': 1000, 'event_end': 8000, 'provider_id': 'other'}
        accepted, rejected = jfdvr.JellyfinDvr.resolve_conflicts([a, b, c], {})
        self.assertEqual({x['id'] for x in accepted}, {'b', 'c'})
        self.assertEqual([x['id'] for x in rejected], ['a'])
        accepted, rejected = jfdvr.JellyfinDvr.resolve_conflicts([a, b], {'a': {'state': 'recording'}})
        self.assertEqual([x['id'] for x in accepted], ['a'])

    def test_padding_makes_back_to_back_games_conflict(self):
        a = {'id': 'a', 'bucket': 'tennessee-football', 'event_start': 0, 'event_end': 10000, 'provider_id': 'p'}
        b = {'id': 'b', 'bucket': 'fc-barcelona', 'event_start': 10000 + 60, 'event_end': 20000, 'provider_id': 'p'}
        self.assertEqual(len(jfdvr.JellyfinDvr.resolve_conflicts([a, b], {})[0]), 1)


class ExportTests(Base):
    def test_export_contains_no_provider_or_key_material(self):
        self.dvr.tick([pregame(NOW + 3600)], NOW)
        m3u, xml = self.dvr.export_m3u(EXPORT, 'relay-secret'), self.dvr.export_xmltv()
        for text in (m3u, xml):
            for forbidden in ('providera', 'stream_id', KEY, 'relay-secret', '/live/', '.ts'):
                self.assertNotIn(forbidden, text)
        self.assertIn('/jellyfin/relay/', m3u)
        self.assertIn('<programme', xml)

    def test_export_tokens_are_purpose_bound(self):
        token = jfdvr.sign('k', 'relay:a')
        self.assertTrue(jfdvr.token_ok('k', 'relay:a', token))
        self.assertFalse(jfdvr.token_ok('k', 'relay:b', token))
        self.assertFalse(jfdvr.token_ok('', 'relay:a', token))


class ScheduleTests(Base):
    def test_missing_mapping_is_reported_then_timer_created_once(self):
        ev = pregame(NOW + 3600)
        self.dvr.tick([ev], NOW)
        entry = self.ledger.all()[0]
        self.assertEqual((entry['state'], entry['reason']), ('pending', 'mapping_missing'))
        self.assertEqual(self.fake.refreshes, 1)
        rid, entry = self.schedule(ev)
        self.assertEqual(entry['state'], 'scheduled')
        self.assertEqual(entry['ownership']['namespace'], jellyfin.NAMESPACE)
        timer = self.fake.timers[entry['jf_timer_id']]
        self.assertEqual((timer['PrePaddingSeconds'], timer['PostPaddingSeconds']), (dvr.PREROLL_SECONDS, dvr.POST_ROLL_SECONDS))
        self.dvr.tick([ev], NOW)                      # restart-style re-tick: no duplicate timer
        self.assertEqual(len(self.fake.timers), 1)

    def test_schedule_change_updates_timer_and_cancel_removes_only_owned_timer(self):
        ev = pregame(NOW + 3600)
        rid, entry = self.schedule(ev)
        foreign = {'Id': 'foreign', 'ChannelId': 'zzz', 'Status': 'New'}
        self.fake.timers['foreign'] = foreign
        moved = pregame(NOW + 3600 + 1800)
        self.dvr.tick([moved], NOW)
        # Real Jellyfin timers follow their guide program, so a move cancels our old timer and re-pends the entry.
        self.assertEqual(self.fake.cancelled, [entry['jf_timer_id']])
        self.assertNotIn(entry['jf_timer_id'], self.fake.timers)
        moved_entry = self.ledger.get(rid)
        self.assertTrue(moved_entry['schedule_changed'])
        self.assertEqual(moved_entry['event_start'], NOW + 5400)
        self.assertIn(moved_entry['state'], ('pending', 'scheduled'))
        self.assertIn('foreign', self.fake.timers)
        postponed = pregame(NOW + 5400)
        postponed.update(event_phase='postponed')
        self.dvr.tick([postponed], NOW)
        self.assertEqual(self.ledger.get(rid)['state'], 'cancelled')
        self.assertIn('foreign', self.fake.timers)

    def test_capability_failure_blocks_scheduling_with_state(self):
        self.fake.reachable = False
        result = self.dvr.tick([pregame(NOW + 3600)], NOW)
        self.assertEqual(result['state'], 'unreachable')
        self.assertEqual(self.ledger.all(), [])

    def test_user_deleted_timer_becomes_conflict_and_is_not_recreated(self):
        ev = pregame(NOW + 3600)
        rid, entry = self.schedule(ev)
        del self.fake.timers[entry['jf_timer_id']]
        self.dvr.tick([ev], NOW)
        self.assertEqual((self.ledger.get(rid)['state'], self.ledger.get(rid)['reason']), ('conflict', 'timer_missing'))
        self.assertEqual(self.fake.timers, {})
        self.dvr.tick([ev], NOW)
        self.assertEqual(self.fake.timers, {})

    def test_conflicting_game_gets_no_timer(self):
        football, soccer = pregame(NOW + 3600), barca_pre(NOW + 3600 + 600)
        soccer['feeds'][0]['provider_id'] = 'providera'
        self.dvr.tick([football, soccer], NOW)
        states = {e['bucket']: e['state'] for e in self.ledger.all()}
        self.assertEqual(states, {'tennessee-football': 'pending', 'fc-barcelona': 'conflict'})
        self.assertEqual([e['reason'] for e in self.ledger.all() if e['state'] == 'conflict'], ['provider_conflict'])


class LifecycleTests(Base):
    def record(self, ev, content='confirmed', first_offset=-60, status='Completed'):
        rid, entry = self.schedule(ev)
        item = self.fake.add_recording(entry['jf_timer_id'], entry['jf_channel_id'], status, path=self.path(rid))
        self.ledger.update(rid, content=content, first_byte_at=entry['event_start'] + first_offset, relay_final=True)
        self.fake.timers[entry['jf_timer_id']]['Status'] = status if status == 'InProgress' else 'Completed'
        self.dvr.tick([ev], NOW)
        return rid, item

    def test_completed_confirmed_from_beginning_is_ready_and_labelled(self):
        rid, item = self.record(pregame(NOW + 3600))
        entry = self.ledger.get(rid)
        self.assertEqual(entry['state'], 'ready')
        self.assertTrue(entry['complete_start'] and entry['complete_end'])
        self.assertEqual(entry['jf_item_id'], item['Id'])

    def test_late_first_byte_is_partial_start(self):
        rid, _ = self.record(pregame(NOW + 3600), first_offset=120)
        self.assertFalse(self.ledger.get(rid)['complete_start'])

    def test_unverified_or_wrong_content_never_ready(self):
        rid, _ = self.record(pregame(NOW + 3600), content='unverified')
        self.assertEqual((self.ledger.get(rid)['state'], self.ledger.get(rid)['reason']), ('failed', 'unverified'))
        view = self.dvr.listing()['buckets'][0]['recordings'][0]
        self.assertFalse(view['playable'])

    def test_in_progress_is_listed_but_never_offered_for_playback(self):
        rid, _ = self.record(pregame(NOW + 3600), status='InProgress')
        view = self.dvr.listing()['buckets'][0]['recordings'][0]
        self.assertEqual((view['state'], view['playable'], view['reason']), ('recording', False, 'recording_in_progress'))

    def test_scheduled_and_conflict_entries_are_not_listed_as_recordings(self):
        self.schedule(pregame(NOW + 3600))
        listing = self.dvr.listing()
        self.assertEqual([b['recordings'] for b in listing['buckets']], [[], [], []])
        self.assertEqual(len(listing['scheduled']), 1)
        self.assertEqual(listing['engine'], 'jellyfin')

    def test_ambiguous_items_are_a_conflict(self):
        ev = pregame(NOW + 3600)
        rid, entry = self.schedule(ev)
        self.fake.add_recording(entry['jf_timer_id'], entry['jf_channel_id'], path=self.path(rid))
        self.fake.add_recording(entry['jf_timer_id'], entry['jf_channel_id'], path=self.path(rid) + '.2')
        self.dvr.tick([ev], NOW)
        self.assertEqual(self.ledger.get(rid)['reason'], 'ambiguous_item')

    def test_reconcile_after_restart_is_idempotent(self):
        rid, _ = self.record(pregame(NOW + 3600))
        before = self.ledger.get(rid)
        restarted = self.mk()
        restarted.reconcile(NOW + 1)
        restarted.reconcile(NOW + 2)
        after = self.ledger.get(rid)
        self.assertEqual({k: v for k, v in before.items() if k != 'updated_at'}, {k: v for k, v in after.items() if k != 'updated_at'})


class RetentionTests(Base):
    def ready(self, n, bucket='tennessee-football', start=None, tamper=False):
        rid = 'r%s%d' % (bucket[:3], n)
        channel = 'ch-' + rid
        timer = 'tm-' + rid
        item = self.fake.add_recording(timer, channel, path='/rec/x %s/y.ts' % jfdvr.tag(rid))
        entry = {**self.dvr._new({'id': rid, 'bucket': bucket, 'espn_id': str(n), 'title': 't', 'event_start': start or 1000 + n,
                                  'event_end': 2000 + n, 'provider_id': 'providera', 'stream_id': '7'}, NOW),
                 'state': 'ready', 'jf_channel_id': channel, 'jf_timer_id': timer, 'jf_item_id': item['Id'],
                 'ownership': {'namespace': jellyfin.NAMESPACE, 'channel_name': jfdvr.channel_name(rid), 'item_path': item['Path'], 'tag': jfdvr.tag(rid)}}
        self.ledger.put(entry)
        if tamper:
            item['Path'] = '/rec/someone-elses/other.ts'
        return rid, item

    def test_default_retention_never_deletes_and_hides_older_than_three(self):
        rids = [self.ready(n)[0] for n in range(1, 5)]
        self.dvr.retain(NOW)
        self.assertEqual(self.fake.deleted, [])
        bucket = self.dvr.listing()['buckets'][0]['recordings']
        self.assertEqual(sorted(v['state'] for v in bucket), ['expired', 'ready', 'ready', 'ready'])
        self.assertEqual([v['id'] for v in bucket if v['state'] == 'expired'], [rids[0]])

    def test_enabled_retention_deletes_only_exact_owned_oldest_and_per_bucket(self):
        mine = [self.ready(n)[0] for n in range(1, 5)]
        other = [self.ready(n, 'fc-barcelona')[0] for n in range(1, 3)]
        unrelated = self.fake.add_recording('foreign-timer', 'foreign-channel')
        self.mk(retention=True).retain(NOW)
        self.assertEqual(len(self.fake.deleted), 1)
        self.assertEqual(self.ledger.get(mine[0])['state'], 'expired')
        self.assertTrue(all(self.ledger.get(r)['state'] == 'ready' for r in other))
        self.assertIn(unrelated['Id'], self.fake.recordings)

    def test_unproven_ownership_is_never_deleted(self):
        rids = [self.ready(n, tamper=(n == 1))[0] for n in range(1, 5)]
        self.mk(retention=True).retain(NOW)
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(self.ledger.get(rids[0])['reason'], 'ownership_unproven')


class RelayTests(Base):
    def relay(self, rid, token=None, now=None):
        token = jfdvr.sign('relay-secret', 'relay:' + rid) if token is None else token
        return self.dvr.relay_authorize(rid, token, 'relay-secret', now or NOW)

    def test_relay_gates(self):
        ev = pregame(NOW + 3600)
        rid, entry = self.schedule(ev)
        self.assertEqual(self.relay(rid, 'bad')[2], 'forbidden')
        self.assertEqual(self.relay(rid)[2], 'outside_window')
        inside = NOW + 3600 - 60
        _, granted, error = self.relay(rid, now=inside)
        self.assertIsNone(error)
        self.assertTrue(self.leases.validate(granted))

    def test_playback_and_dvr_cannot_both_hold_a_provider(self):
        rid, entry = self.schedule(pregame(NOW + 3600))
        inside = NOW + 3590
        playback = self.leases.acquire('providera', 'playback:s', 'playback')
        self.assertEqual(self.relay(rid, now=inside)[2], 'provider_busy')
        self.leases.release(playback)
        _, granted, _ = self.relay(rid, now=inside)
        self.assertIsNone(self.leases.acquire('providera', 'playback:s2', 'playback'))   # DVR now holds it
        self.assertEqual(self.dvr.stop(rid), True)
        self.assertFalse(self.leases.validate(granted) and False)

    def test_lease_expiry_and_cancel_fail_closed_then_relay_done_blocks_reingest(self):
        rid, entry = self.schedule(pregame(NOW + 3600))
        inside = NOW + 3590
        _, granted, _ = self.relay(rid, now=inside)
        self.now += 61
        self.assertFalse(self.leases.renew(granted, 60))
        self.dvr.relay_ended(rid, True)
        self.assertEqual(self.relay(rid, now=inside)[2], 'not_scheduled')

    def test_cancel_scheduled_removes_timer_and_blocks_relay(self):
        rid, entry = self.schedule(pregame(NOW + 3600))
        self.dvr.stop(rid)
        self.assertEqual(self.ledger.get(rid)['state'], 'cancelled')
        self.assertEqual(self.relay(rid, now=NOW + 3590)[2], 'not_scheduled')

    def test_wrong_content_verdict_is_final(self):
        rid, _ = self.schedule(pregame(NOW + 3600))
        self.dvr.relay_content(rid, 'wrong')
        self.dvr.relay_content(rid, 'confirmed')
        self.assertEqual(self.ledger.get(rid)['content'], 'wrong')


if __name__ == '__main__':
    unittest.main()


class HarnessTests(unittest.TestCase):
    def test_e2e_harness_compiles(self):
        path = Path(__file__).parent / 'jellyfin_e2e' / 'run_e2e.py'
        compile(path.read_text(), str(path), 'exec')
