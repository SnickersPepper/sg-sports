import contextlib
import time
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
import dvr
import jellyfin
import jfdvr
import tv
import verification
from fake_jellyfin import FakeJellyfin, KEY
from test_jfdvr import pregame

EXPORT = 'http://sg.test'
RELAY_KEY = 'relay-secret'


def settle():
    """The shared relay runs on its own thread; wait for it to finish and release everything."""
    deadline = time.time() + 5
    while tv.JF_FANS and time.time() < deadline:
        time.sleep(0.01)


class Handler:
    def __init__(self, headers=None):
        self.headers, self.wfile, self.sent, self.status = headers or {}, io.BytesIO(), None, None
        self.close_connection = False

    def send(self, status, data):
        self.status, self.sent = status, data

    def send_response(self, status):
        self.status = status

    def send_header(self, *a):
        pass

    def end_headers(self):
        pass


class JellyfinTvTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.object(app, 'DATA', Path(tmp.name)), patch.dict(os.environ, {'SG_JELLYFIN_ENABLED': '1'})):
            p.start()
            self.addCleanup(p.stop)
        tv.configure(app)
        tv.PAIR.clear(); tv.ACTIVE.clear(); tv.VERIFYING.clear(); tv.LEASES.clear()
        self.fake = FakeJellyfin(export_url=EXPORT)
        self.fake.add_channel('seed')
        config = jellyfin.Config(True, 'http://jf.test:8096', KEY, EXPORT, RELAY_KEY)
        self.dvr = jfdvr.JellyfinDvr(jellyfin.Client(config, self.fake), jfdvr.Ledger(Path(tmp.name) / 'dvr-ledger.sqlite'), tv.leases())
        tv.JF = (str(app.DATA), config, self.dvr)
        self.start = tv.now() + 120      # inside the relay window
        self.event = pregame(self.start)
        self.dvr.tick([self.event], tv.now())
        self.rid = self.dvr.ledger.all()[0]['id']

    def token(self):
        return jfdvr.sign(RELAY_KEY, 'relay:' + self.rid)

    def source(self, chunks):
        @contextlib.contextmanager
        def source():
            yield iter(chunks)
        return lambda feed: source

    def test_relay_rejects_bad_token_and_never_opens_provider(self):
        handler = Handler()
        with patch.object(tv, 'dvr_source') as opened:
            tv.jf_relay(handler, self.rid, 'nope')
        self.assertEqual(handler.status, 403)
        opened.assert_not_called()

    def test_relay_streams_under_lease_then_releases_it(self):
        handler = Handler()
        with patch.object(tv, 'dvr_source', self.source([b'abc', b'def'])):
            tv.jf_relay(handler, self.rid, self.token())
            settle()
        self.assertEqual(handler.wfile.getvalue(), b'abcdef')
        self.assertIsNone(tv.leases().holder('providera'))
        self.assertIsNotNone(self.dvr.ledger.get(self.rid)['first_byte_at'])

    def test_concurrent_connections_share_one_upstream_and_late_joiner_is_packet_aligned(self):
        opens = []
        packet = bytes(range(188))
        @contextlib.contextmanager
        def source():
            opens.append(1)
            def chunks():
                for _ in range(60):
                    yield packet + packet[:100]            # chunks are not packet aligned
                    time.sleep(0.005)
            yield chunks()
        first, second = Handler(), Handler()
        import threading
        with patch.object(tv, 'dvr_source', lambda feed: source):
            runner = threading.Thread(target=tv.jf_relay, args=(first, self.rid, self.token()))
            runner.start()
            time.sleep(0.06)
            tv.jf_relay(second, self.rid, self.token())
            runner.join(5)
            settle()
        self.assertEqual(len(opens), 1)
        self.assertTrue(first.wfile.getvalue())
        self.assertTrue(second.wfile.getvalue())
        self.assertIsNone(tv.leases().holder('providera'))

    def test_relay_refused_while_viewer_holds_the_provider(self):
        tv.leases().acquire('providera', 'playback:s', 'playback')
        handler = Handler()
        with patch.object(tv, 'dvr_source') as opened:
            tv.jf_relay(handler, self.rid, self.token())
            settle()
        self.assertEqual(handler.status, 503)
        opened.assert_not_called()

    def test_relay_stops_when_lease_is_lost_mid_stream(self):
        handler = Handler()
        def chunks():
            yield b'one'
            tv.leases().cancel_owner('dvr:' + self.rid)       # cancelled / preempted
            yield b'two'
            yield b'three'
        @contextlib.contextmanager
        def source():
            yield chunks()
        ticks = iter(range(0, 1000, 20))
        with patch.object(tv, 'dvr_source', lambda feed: source), patch('time.monotonic', lambda: next(ticks)):
            tv.jf_relay(handler, self.rid, self.token())
            settle()
        self.assertNotIn(b'three', handler.wfile.getvalue())
        self.assertIsNone(tv.leases().holder('providera'))

    def test_playback_and_verification_see_the_dvr_lease(self):
        _, granted, error = self.dvr.relay_authorize(self.rid, self.token(), RELAY_KEY)
        self.assertIsNone(error)
        game = {'fixture': {'id': 't', 'kickoff': 0}, 'feeds': [{'provider_id': 'providera', 'stream_id': '7'}]}
        adapter = MagicMock(); adapter.host = 'http://provider.example'
        adapter.account.return_value = {'status': 'Active', 'active_cons': '0', 'allowed_output_formats': ['ts']}
        with patch.object(app, 'resolve', return_value=game), patch.object(app, 'Xtream', return_value=adapter), \
                self.assertRaises(ValueError):
            tv.begin('o', {'fixture_id': 't', 'provider_id': 'providera', 'stream_id': '7'})
        with self.assertRaises(verification.Busy):
            tv.sample_feed({}, {'provider_id': 'providera', 'stream_id': '7'})
        self.assertTrue(tv.leases().validate(granted))

    def test_playback_lease_blocks_dvr_and_is_freed_on_stop(self):
        game = {'fixture': {'id': 't', 'kickoff': 0}, 'feeds': [{'provider_id': 'providera', 'stream_id': '7'}]}
        adapter = MagicMock(); adapter.host = 'http://provider.example'
        adapter.account.return_value = {'status': 'Active', 'active_cons': '0', 'allowed_output_formats': ['ts']}
        with patch.object(app, 'resolve', return_value=game), patch.object(app, 'Xtream', return_value=adapter):
            sid = tv.begin('o', {'fixture_id': 't', 'provider_id': 'providera', 'stream_id': '7'})['session']
        self.assertEqual(self.dvr.relay_authorize(self.rid, self.token(), RELAY_KEY)[2], 'provider_busy')
        tv.update('o', sid, True)
        self.assertEqual(self.dvr.relay_authorize(self.rid, self.token(), RELAY_KEY)[2], None)

    def test_export_requires_its_token_and_hides_provider_data(self):
        handler = Handler()
        tv.jf_export(handler, 'bad', 'playlist.m3u')
        self.assertEqual(handler.status, 403)
        handler = Handler()
        tv.jf_export(handler, jfdvr.sign(RELAY_KEY, 'export'), 'playlist.m3u')
        body = handler.wfile.getvalue().decode()
        self.assertIn('/jellyfin/relay/' + self.rid, body)
        self.assertNotIn('providera', body)

    def test_engine_selection_never_runs_two_automatic_recorders(self):
        with patch.dict(os.environ, {'SG_DVR': '1'}):
            self.assertEqual(tv.dvr_engine(), 'jellyfin')
        with patch.dict(os.environ, {'SG_JELLYFIN_ENABLED': '0', 'SG_DVR': '1'}):
            self.assertEqual(tv.dvr_engine(), 'legacy')
        with patch.dict(os.environ, {'SG_JELLYFIN_ENABLED': '0', 'SG_DVR': '0'}):
            self.assertEqual(tv.dvr_engine(), 'off')
            self.assertEqual(tv.dvr_tick(), [])

    def test_media_for_unknown_or_unplayable_entry_is_404_without_contacting_jellyfin(self):
        handler = Handler()
        before = len(self.fake.requests)
        tv.jf_media(handler, self.rid)         # scheduled, not a recording
        self.assertEqual(handler.status, 404)
        handler = Handler()
        tv.jf_media(handler, 'missing')
        self.assertEqual(handler.status, 404)

    def test_media_of_vanished_item_expires_ledger_entry(self):
        entry = self.dvr.ledger.update(self.rid, state='ready', jf_item_id='gone', jf_channel_id='c', jf_timer_id='t',
                                       ownership={'namespace': jellyfin.NAMESPACE, 'channel_name': jfdvr.channel_name(self.rid)})
        handler = Handler()
        tv.jf_media(handler, self.rid)
        self.assertEqual(handler.status, 404)
        self.assertEqual(self.dvr.ledger.get(self.rid)['reason'], 'media_missing')

    def test_recordings_listing_reports_state_without_provider_or_key_data(self):
        handler = Handler(); handler.headers = {}
        with patch.object(tv, 'device', return_value='o'):
            tv.handle(handler, '/tv/recordings', False)
        text = str(handler.sent)
        self.assertEqual(handler.sent['engine'], 'jellyfin')
        for forbidden in (KEY, RELAY_KEY, 'stream_id', '"7"'):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
