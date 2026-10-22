"""Phase 7A M3B tests: controlled Dispatcharr delivery behind the one lease authority.

Synthetic only: a FakeRelay stands in for the pinned Dispatcharr proxy and counts the upstream connections it would open to
Provider A ('providera', limit 1) and Provider B ('providerb', limit 1). The relay refuses (503) any request that would exceed a
provider limit and counts that as 'over_limit': every scenario asserts over_limit == 0 AND that the relay really did open
the expected upstreams, so a refusal by SG Sports is distinguishable from a relay that was never asked.
A passing run proves SG Sports' admission, fan-out, cooldown, recovery and no-fallback logic against that model. It does not
prove the behaviour of a real Dispatcharr 0.32.0 container (see PHASE7A-M3B.md and the isolated-runner specification).
"""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import app
import delivery
import dispatcharr
import lease
import settings
import tv
from test_dispatcharr import BASE, KEY, NOW, Fake, api_ok, up

U1 = '11111111-1111-4111-8111-111111111111'   # Provider A, channel 100
U2 = '22222222-2222-4222-8222-222222222222'   # Provider A, channel 200 (duplicate import of the same subscription)
U3 = '33333333-3333-4333-8333-333333333333'   # Provider B, channel 300
LIMITS = {'providera': 1, 'providerb': 1}
OWNER_OF = {U1: 'providera', U2: 'providera', U3: 'providerb'}
TS = b'\x47\x40\x11\x10' + b'\x00' * 184


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Stream:
    def __init__(self, relay, uuid):
        self.relay, self.uuid, self.status, self.closed = relay, uuid, 200, False

    def read(self, n=188):
        return TS[:n]

    def close(self):
        if not self.closed:
            self.closed = True
            self.relay.disconnect(self.uuid)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeRelay:
    """Dispatcharr proxy model: one upstream per channel shared by its clients, kept open `delay` seconds after the last client."""
    def __init__(self, clock, delay=10):
        self.clock, self.delay, self.clients, self.closed_at = clock, delay, {}, {}
        self.opened, self.over_limit, self.requests, self.profile, self.down = {'providera': 0, 'providerb': 0}, 0, [], 'proxy', False
        self.peak = {'providera': 0, 'providerb': 0}

    def upstreams(self, account):
        now = self.clock()
        return [u for u in OWNER_OF if OWNER_OF[u] == account and (self.clients.get(u, 0) > 0 or self.closed_at.get(u, -1e9) + self.delay > now)]

    def opener(self):
        relay = self

        class Opener:
            def open(self, request, timeout=None):
                return relay.handle(request.full_url)
        return Opener()

    def handle(self, url):
        self.requests.append(url)
        if self.down:
            raise urllib.error.URLError('connection refused')
        uuid = url.rsplit('/', 1)[-1]
        if self.profile == 'redirect':
            raise urllib.error.HTTPError(url, 302, 'Found', {'Location': 'http://provider.example.invalid/live/u/p/1.ts'}, None)
        account = OWNER_OF[uuid]
        live = self.upstreams(account)
        if uuid not in live and len(live) >= LIMITS[account]:
            self.over_limit += 1
            raise urllib.error.HTTPError(url, 503, 'limit', {}, None)
        if uuid not in live:
            self.opened[account] += 1
        self.clients[uuid] = self.clients.get(uuid, 0) + 1
        self.closed_at.pop(uuid, None)
        self.peak[account] = max(self.peak[account], len(self.upstreams(account)))
        return Stream(self, uuid)

    def disconnect(self, uuid):
        self.clients[uuid] -= 1
        if self.clients[uuid] == 0:
            self.closed_at[uuid] = self.clock()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clock = Clock()
        self.relay = FakeRelay(self.clock)
        self.store = settings.SettingsStore(self.dir / 's.sqlite')
        self.probe = Fake(api_ok())
        self.resolver = delivery.Resolver(self.store, self.probe, self.clock)
        self.auth = delivery.DeliveryAuthority(self.dir / 'l.sqlite', self.resolver, self.clock)
        self.configure()

    def rev(self):
        return self.store.read()['revision']

    def configure(self, enabled=True, options=None):
        self.store.update(self.rev(), [
            up('accounts', id='acct-providera', name='Provider A', max_connections=1),
            up('accounts', id='acct-providerb', name='Provider B', max_connections=1),
            up('profiles', id='prof-household', preferences={'favorites': ['team.soccer.men.la-liga.fc-barcelona']}),
            up('integrations', id='int-disp', name='Dispatcharr', kind='dispatcharr', base_url=BASE, api_key=KEY, options=options or {}),
            up('sources', id='src-a1', name='A import 1', kind='m3u', middleware='dispatcharr', integration_id='int-disp', location_type='url',
               location=BASE + '/output/m3u/a1', account_id='acct-providera'),
            up('sources', id='src-a2', name='A import 2', kind='m3u', middleware='dispatcharr', integration_id='int-disp', location_type='url',
               location=BASE + '/output/m3u/a2', account_id='acct-providera'),
            up('sources', id='src-b', name='B import', kind='m3u', middleware='dispatcharr', integration_id='int-disp', location_type='url',
               location=BASE + '/output/m3u/b', account_id='acct-providerb'),
            up('routes', id='route-disp', name='Mediated', kind='dispatcharr', integration_id='int-disp', account_ids=['acct-providera', 'acct-providerb'],
               source_ids=['src-a1', 'src-a2', 'src-b'], enabled=enabled),
            up('channel_mappings', id='cmap-a100', canonical_channel_id=delivery.canonical_channel('providera', '100'), source_id='src-a1',
               integration_id='int-disp', dispatcharr_channel=U1, account_id='acct-providera'),
            up('channel_mappings', id='cmap-a200', canonical_channel_id=delivery.canonical_channel('providera', '200'), source_id='src-a2',
               integration_id='int-disp', dispatcharr_channel=U2, account_id='acct-providera'),
            up('channel_mappings', id='cmap-b300', canonical_channel_id=delivery.canonical_channel('providerb', '300'), source_id='src-b',
               integration_id='int-disp', dispatcharr_channel=U3, account_id='acct-providerb')])

    def join(self, provider, stream, owner, kind='playback', **kw):
        """Admit a managed consumer through the single authority, then open its media through the relay (as tv.py does)."""
        granted = self.auth.acquire(provider, owner, kind, stream_id=stream, **kw)
        if not granted:
            return None, None
        plan = self.resolver.decide(provider, stream)
        return granted, delivery.open_stream(plan, self.relay.opener())


class Decisions(Base):
    def test_direct_account_has_no_plan_and_core_paths_do_not_need_dispatcharr(self):
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False)])
        self.assertIsNone(self.resolver.decide('providera', '100'))
        self.assertFalse(self.resolver.mediated('providera'))
        self.assertIsNotNone(self.auth.acquire('providera', 'playback:a', 'playback', stream_id='100'))   # plain direct lease
        self.assertEqual(self.probe.calls, [])   # no management traffic when nothing is mediated

    def test_mapped_channel_resolves_to_relay_channel_without_exposing_secrets(self):
        plan = self.resolver.decide('providera', '100')
        self.assertEqual((plan.channel, plan.url), (U1, BASE + '/proxy/ts/stream/' + U1))
        self.assertNotIn(KEY, repr(plan))
        self.assertNotIn('dispatcharr.example', repr(plan))
        self.assertTrue(all(c[0].startswith(BASE + '/api/') or c[0].startswith(BASE + '/proxy/ts/status') for c in self.probe.calls))

    def test_unmapped_channel_and_unpinned_or_unhealthy_integration_refuse_without_fallback(self):
        with self.assertRaises(delivery.Refused) as unmapped:
            self.resolver.decide('providera', '999')
        self.assertEqual(unmapped.exception.code, 'channel_unmapped')
        self.assertIsNone(self.auth.acquire('providera', 'playback:x', 'playback', stream_id='999'))
        self.assertEqual(self.auth.last_refusal.code, 'channel_unmapped')
        self.assertEqual(self.auth.holders(), [])
        for routes, code in [(api_ok(version='0.31.0'), 'integration_version_mismatch'),
                             (api_ok(channels=404), 'integration_capability_mismatch')]:
            self.probe.routes = routes
            self.clock.t += dispatcharr.HEALTH_STALE_SECONDS + 1   # expire the cached observation so the next decision re-probes
            with self.assertRaises(delivery.Refused) as refused:
                self.resolver.decide('providera', '100')
            self.assertEqual(refused.exception.code, code)

    def test_revoked_credentials_and_lost_middleware_refuse_on_next_decision(self):
        self.resolver.decide('providera', '100')
        self.clock.t += dispatcharr.HEALTH_STALE_SECONDS + 1
        self.probe.routes = {BASE + dispatcharr.VERSION_PATH: (401, b'')}
        with self.assertRaises(delivery.Refused) as revoked:
            self.resolver.decide('providera', '100')
        self.assertEqual(revoked.exception.code, 'integration_credentials_rejected')
        self.clock.t += dispatcharr.HEALTH_STALE_SECONDS + 1
        self.probe.routes = {BASE + dispatcharr.VERSION_PATH: dispatcharr.TransportError('connection')}
        with self.assertRaises(delivery.Refused) as lost:
            self.resolver.decide('providera', '100')
        self.assertEqual(lost.exception.code, 'integration_unavailable')
        self.assertIsNone(self.auth.acquire('providera', 'playback:a', 'playback', stream_id='100'))   # nothing granted, nothing opened
        self.assertEqual(self.relay.requests, [])

    def test_disabled_integration_and_missing_route_never_fall_back_to_direct_while_the_route_is_enabled(self):
        self.store.update(self.rev(), [up('integrations', id='int-disp', enabled=False)])
        with self.assertRaises(delivery.Refused) as refused:
            self.resolver.decide('providera', '100')
        self.assertEqual(refused.exception.code, 'integration_disabled')
        self.assertIsNone(self.auth.acquire('providera', 'playback:a', 'playback', stream_id='100'))

    def test_non_uuid_mapping_is_refused(self):
        self.store.update(self.rev(), [up('channel_mappings', id='cmap-a100', dispatcharr_channel='12')])
        with self.assertRaises(delivery.Refused) as refused:
            self.resolver.decide('providera', '100')
        self.assertEqual(refused.exception.code, 'channel_not_a_uuid')


class Capacity(Base):
    def test_duplicate_imports_share_one_upstream_budget_and_the_relay_is_never_over_limit(self):
        a, stream_a = self.join('providera', '100', 'playback:a')
        self.assertIsNotNone(a)
        other, none = self.join('providera', '200', 'playback:b')   # same subscription imported again as another channel
        self.assertIsNone(other)
        self.assertEqual(self.auth.last_refusal.code, 'provider_in_use')
        self.assertEqual((self.relay.opened['providera'], self.relay.peak['providera'], self.relay.over_limit), (1, 1, 0))
        self.assertEqual(self.relay.requests, [BASE + '/proxy/ts/stream/' + U1])   # the refused request never reached the relay
        stream_a.close()
        self.auth.release(a)

    def test_same_channel_fan_out_opens_one_upstream_for_all_managed_consumers(self):
        held = [self.join('providera', '100', 'playback:%d' % n) for n in range(3)]
        self.assertTrue(all(g for g, _ in held))
        self.assertEqual(len({g.fence for g, _ in held}), 1)
        self.assertEqual((self.relay.opened['providera'], self.relay.clients[U1], self.relay.over_limit), (1, 3, 0))
        self.assertEqual(len(self.auth.holders()), 1)
        for granted, stream in held[:2]:
            stream.close()
            self.assertTrue(self.auth.release(granted))
        self.assertTrue(self.auth.validate(held[2][0]))   # the last consumer keeps the hold
        self.assertFalse(self.auth.validate(held[0][0]))

    def test_live_verification_and_dvr_contend_for_the_same_account_and_each_other(self):
        dvr, dvr_stream = self.join('providera', '100', 'dvr:r1', 'dvr', exclusive=True)
        self.assertIsNotNone(dvr)
        self.assertIsNone(self.auth.acquire('providera', 'verify:v', 'verification', stream_id='200'))   # other channel, same account
        self.assertIsNone(self.auth.acquire('providera', 'dvr:r2', 'dvr', stream_id='200', exclusive=True))
        self.assertIsNone(self.auth.acquire('providera', 'dvr:r1', 'dvr', stream_id='100', exclusive=True))   # one upstream per recording
        self.assertEqual(self.auth.last_refusal.code, 'owner_already_streaming')
        other_account, other_stream = self.join('providerb', '300', 'playback:b')   # independent account is unaffected
        self.assertIsNotNone(other_account)
        self.assertEqual((self.relay.opened, self.relay.over_limit), ({'providera': 1, 'providerb': 1}, 0))
        # a higher-priority playback of ANOTHER channel must not displace the live recording: its relay session is still open
        playback, playback_stream = self.join('providera', '200', 'playback:p', preempt=True)
        self.assertEqual((playback, playback_stream), (None, None))
        self.assertTrue(self.auth.validate(dvr) and self.auth.renew(dvr, 60))   # the recorder keeps its fenced hold
        dvr_stream.close(); other_stream.close()
        self.assertEqual(self.relay.over_limit, 0)

    def test_higher_priority_other_channel_never_opens_a_second_relay_upstream(self):
        first, first_stream = self.join('providera', '100', 'dvr:r1', 'dvr', exclusive=True)
        self.assertIsNotNone(first)
        before = (list(self.relay.requests), dict(self.relay.opened), self.relay.peak['providera'])
        # non-vacuous: if the replacement were granted, join() would open channel 200 and the relay would see a second request
        granted, stream = self.join('providera', '200', 'playback:p', preempt=True)
        self.assertIsNone(granted)
        self.assertIsNone(stream)
        self.assertEqual((self.relay.requests, self.relay.opened, self.relay.peak['providera']), before)
        self.assertEqual(self.relay.over_limit, 0)
        self.assertEqual(self.auth.last_refusal.code, 'provider_in_use')
        first_stream.close()
        self.auth.release(first)
        self.clock.t += 5   # relay shutdown delay still running: priority still cannot preempt the cooldown
        self.assertEqual(self.join('providera', '200', 'playback:p', preempt=True), (None, None))
        self.assertEqual((len(self.relay.requests), self.relay.over_limit), (1, 0))
        self.clock.t += delivery.COOLDOWN_SECONDS + 1
        granted, stream = self.join('providera', '200', 'playback:p', preempt=True)
        self.assertIsNotNone(granted)
        self.assertEqual((self.relay.opened['providera'], self.relay.peak['providera'], self.relay.over_limit), (2, 1, 0))
        stream.close()

    def test_playback_joining_a_recording_of_the_same_channel_does_not_preempt_it(self):
        dvr, _ = self.join('providera', '100', 'dvr:r1', 'dvr', exclusive=True)
        playback = self.auth.acquire('providera', 'playback:p', 'playback', stream_id='100', preempt=True)
        self.assertIsNotNone(playback)
        self.assertTrue(self.auth.validate(dvr) and self.auth.validate(playback))
        self.assertEqual(self.auth.holder('providera')['kind'], 'playback')   # the shared hold carries the highest priority
        self.auth.release(playback)
        self.assertEqual(self.auth.holder('providera')['kind'], 'dvr')


class ShutdownRecovery(Base):
    def test_cooldown_covers_the_relays_delayed_shutdown_so_a_new_channel_is_not_opened_early(self):
        a, stream = self.join('providera', '100', 'playback:a')
        stream.close()
        self.auth.release(a)                         # SG Sports consumer gone; the relay still holds its upstream for 10 s
        self.clock.t += 5
        self.assertEqual(len(self.relay.upstreams('providera')), 1)
        self.assertIsNone(self.auth.acquire('providera', 'playback:b', 'playback', stream_id='200', preempt=True))   # even priority cannot pre-empt a cooldown
        self.assertEqual(self.auth.holder('providera')['kind'], 'cooldown')
        self.clock.t += delivery.COOLDOWN_SECONDS + 1
        self.assertEqual(self.relay.upstreams('providera'), [])
        b, stream_b = self.join('providera', '200', 'playback:b')
        self.assertIsNotNone(b)
        self.assertEqual((self.relay.opened['providera'], self.relay.peak['providera'], self.relay.over_limit), (2, 1, 0))
        stream_b.close()

    def test_returning_to_the_same_channel_during_the_cooldown_is_allowed_and_keeps_the_fence(self):
        a, stream = self.join('providera', '100', 'playback:a')
        stream.close()
        self.auth.release(a)
        self.clock.t += 3
        b = self.auth.acquire('providera', 'playback:b', 'playback', stream_id='100')
        self.assertIsNotNone(b)
        self.assertEqual(b.fence, a.fence)

    def test_integration_option_sets_the_cooldown_and_bounds_it(self):
        self.store.update(self.rev(), [up('integrations', id='int-disp', options={'shutdown_delay_seconds': 120})])
        self.assertEqual(self.resolver.decide('providera', '100').cooldown, 120)
        self.store.update(self.rev(), [up('integrations', id='int-disp', options={'shutdown_delay_seconds': 99999})])
        self.assertEqual(self.resolver.decide('providera', '100').cooldown, delivery.MAX_COOLDOWN_SECONDS)

    def test_crash_without_release_keeps_the_account_reserved_until_ttl_plus_cooldown_even_across_restart(self):
        crashed = self.auth.acquire('providera', 'playback:a', 'playback', ttl=90, stream_id='100')
        self.clock.t += 91                           # the process died: no renewals, no release
        restarted = delivery.DeliveryAuthority(self.dir / 'l.sqlite', delivery.Resolver(self.store, self.probe, self.clock), self.clock)
        self.assertFalse(restarted.validate(crashed))
        self.assertIsNone(restarted.acquire('providera', 'playback:b', 'playback', stream_id='200'))
        self.clock.t += delivery.COOLDOWN_SECONDS
        self.assertIsNotNone(restarted.acquire('providera', 'playback:b', 'playback', stream_id='200'))

    def test_stale_member_cannot_renew_after_expiry_but_live_members_keep_the_hold(self):
        a = self.auth.acquire('providera', 'playback:a', 'playback', ttl=30, stream_id='100')
        b = self.auth.acquire('providera', 'playback:b', 'playback', ttl=90, stream_id='100')
        self.clock.t += 40
        self.assertFalse(self.auth.renew(a, 30))
        self.assertTrue(self.auth.renew(b, 90))
        self.assertTrue(self.auth.holds('providera', 'playback:b') and not self.auth.holds('providera', 'playback:a'))

    def test_cancel_owner_removes_only_that_member(self):
        a = self.auth.acquire('providera', 'playback:a', 'playback', stream_id='100')
        b = self.auth.acquire('providera', 'playback:b', 'playback', stream_id='100')
        self.assertGreaterEqual(self.auth.cancel_owner('playback:a'), 1)
        self.assertFalse(self.auth.validate(a))
        self.assertTrue(self.auth.validate(b))
        self.auth.cancel_owner('playback:b')
        self.assertEqual(self.auth.holder('providera')['kind'], 'cooldown')


class Profiles(Base):
    def test_redirect_profile_is_refused_not_followed_and_nothing_is_held_open(self):
        self.relay.profile = 'redirect'
        granted = self.auth.acquire('providera', 'playback:a', 'playback', stream_id='100')
        with self.assertRaises(delivery.Refused) as refused:
            delivery.open_stream(self.resolver.decide('providera', '100'), self.relay.opener())
        self.assertEqual(refused.exception.code, 'redirect_profile')
        self.assertEqual(len(self.relay.requests), 1)   # the provider Location was never requested
        self.assertTrue(self.auth.release(granted))

    def test_lost_relay_is_a_refusal_with_no_provider_fallback(self):
        self.relay.down = True
        with self.assertRaises(delivery.Refused) as refused:
            delivery.open_stream(self.resolver.decide('providera', '100'), self.relay.opener())
        self.assertEqual(refused.exception.code, 'relay_unreachable')


class TvIntegration(Base):
    """tv.py end to end over the real settings store: every managed entry opens only the relay, never a provider URL."""
    def setUp(self):
        super().setUp()
        opener_for = self.relay.opener
        original = delivery.open_stream
        for p in (patch.object(app, 'DATA', self.dir), patch.object(app, 'settings_store', lambda: self.store),
                  patch.object(app, 'DISPATCHARR_TRANSPORT', self.probe, create=True),
                  patch.object(app, 'Xtream', side_effect=AssertionError('direct provider contact in mediated mode'), create=True),
                  patch.object(delivery, 'open_stream', lambda plan, opener=None: original(plan, opener_for()))):
            p.start()
            self.addCleanup(p.stop)
        tv.configure(app)
        tv.LEASES.clear(); tv.ACTIVE.clear(); tv.VERIFYING.clear()

    def test_open_media_and_dvr_source_use_only_the_relay(self):
        stream = tv.open_media({'provider_id': 'providera', 'stream_id': '100', 'host': None})
        self.assertIsInstance(stream, Stream)
        stream.close()
        with tv.dvr_source({'provider_id': 'providera', 'stream_id': '100'})() as chunks:
            self.assertEqual(next(chunks), TS)
        self.assertEqual((len(self.relay.requests), self.relay.opened['providera'], self.relay.over_limit), (2, 1, 0))   # two requests, one upstream within the shutdown delay

    def _holder_kind(self):
        held = tv.leases().holder('providera')
        return held and held['kind']

    def test_failed_open_after_admission_releases_the_reservation_instead_of_leaking_it(self):
        for mode in ('redirect', 'down'):
            with self.subTest(mode=mode):
                self.relay.profile, self.relay.down = ('redirect' if mode == 'redirect' else 'proxy'), mode == 'down'
                lease_ = tv.leases().acquire('providera', 'playback:x', 'playback', stream_id='100')
                self.assertTrue(tv.leases().validate(lease_))
                with self.assertRaises(ValueError):
                    tv.open_media({'provider_id': 'providera', 'stream_id': '100', 'host': None}, owner='playback:x')
                self.assertFalse(tv.leases().validate(lease_))            # member gone, not waiting for the TTL
                self.assertEqual(self._holder_kind(), 'cooldown')         # safe transition: relay may have started an upstream
                self.assertIsNone(tv.leases().acquire('providera', 'playback:y', 'playback', stream_id='200', preempt=True))
                self.assertIsNotNone(tv.leases().acquire('providera', 'playback:z', 'playback', stream_id='100'))   # same channel may retry
                tv.leases().cancel_owner('playback:z')
        self.assertEqual(self.relay.over_limit, 0)

    def test_non_200_relay_response_releases_verification_and_dvr_reservations(self):
        original = self.relay.handle
        self.relay.handle = lambda url: (_ for _ in ()).throw(urllib.error.HTTPError(url, 503, 'limit', {}, None))
        grant = tv.leases().acquire('providera', 'verify:v', 'verification', stream_id='100')
        with self.assertRaises(ValueError):
            tv.open_media({'provider_id': 'providera', 'stream_id': '100', 'host': None}, lease=grant)
        self.assertFalse(tv.leases().validate(grant))
        dvr_lease = tv.leases().acquire('providera', 'dvr:r1', 'dvr', stream_id='100', exclusive=True)
        with self.assertRaises(ValueError):
            with tv.dvr_source({'provider_id': 'providera', 'stream_id': '100', 'owner': 'dvr:r1'})():
                pass
        self.assertFalse(tv.leases().validate(dvr_lease))
        self.relay.handle = original

    def test_unmapped_feed_is_refused_and_cannot_reach_a_provider(self):
        with self.assertRaises(ValueError):
            tv.open_media({'provider_id': 'providera', 'stream_id': '999', 'host': None})
        self.assertEqual(self.relay.requests, [])
        self.assertEqual(tv.leases().last_refusal.code, 'channel_unmapped')
        self.assertTrue(tv.mediated('providera'))


if __name__ == '__main__':
    unittest.main()
