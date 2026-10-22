"""Phase 7A M4A tests: source-generation evidence safety, switch admission, configuration preservation, truthful status.

Synthetic data and fakes only (FakeRelay/Fake transport/FakeJellyfin); no network, container, provider, Jellyfin or Dispatcharr instance.
They prove SG Sports' own invalidation, admission and preservation logic. They do not prove how a real Dispatcharr 0.32.0 behaves
(the isolated container gate is still outstanding; see PHASE7A-M4A.md).
"""
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import app
import delivery
import dispatcharr
import settings
import tv
import verification
import test_delivery
import test_jfdvr
from test_dispatcharr import BASE, KEY, NOW, Fake, api_ok, delete, up
from test_verification import FakeApi

NEW_UUID = '44444444-4444-4444-8444-444444444444'
SECRETS = [KEY, 'dispatcharr.example.invalid', 'dk-synthetic-key-0002']


def game():
    return {'id': 'g1', 'sport': 'Soccer', 'league': 'X', 'live': {'espn_id': '401', 'state': 'in'},
            'feeds': [{'provider_id': 'providera', 'stream_id': '100'}]}


def confirmed(binding):
    return {'status': 'confirmed', 'availability': 'content_verified', 'content_verified': True, 'transport_verified': True,
            'shadow_verdict': 'confirmed', 'shadow_confidence': .99, 'analysis_version': verification.ANALYSIS_VERSION,
            'verified_at': NOW, 'expires_at': NOW + 3600, 'height': 1080, 'source_binding': binding}


class Evidence(test_delivery.Base):
    """A changed mapped stream/provider generation invalidates every piece of source-bound evidence before it can rank as confirmed."""
    def setUp(self):
        super().setUp()
        verification.RUNNING.clear()
        verification.configure_binding(lambda: delivery.binder(self.store))
        self.addCleanup(verification.configure_binding, None)
        self.api = FakeApi()
        self.event = game()

    def binding(self):
        return delivery.source_binding(self.store.read(), 'providera', '100')

    def seed(self, binding='current'):
        binding = self.binding() if binding == 'current' else binding
        key = verification.record_key(self.event, self.event['feeds'][0])
        self.api.values['stream-verification'] = {key: confirmed(binding)}
        self.api.values['stream-reliability'] = {'providera|100': {'attempts': 9, 'successes': 9, 'confirmed': 9, 'source_binding': binding}}

    def shown(self):
        return verification.annotate(self.api, self.event, now=NOW)['feeds'][0]

    def test_evidence_counts_while_the_source_is_unchanged(self):
        self.seed()
        feed = self.shown()
        self.assertTrue(feed['content_verified'])
        self.assertIn('Game confirmed', feed['verification_label'])
        self.assertIsNotNone(self.binding())

    def test_remap_stream_change_key_rotation_or_mapping_removal_discards_confirmation(self):
        changes = [('channel remapped', [up('channel_mappings', id='cmap-a100', dispatcharr_channel=NEW_UUID)]),
                   ('upstream stream changed', [up('channel_mappings', id='cmap-a100', dispatcharr_stream='77')]),
                   ('integration key rotated', [up('integrations', id='int-disp', api_key='dk-synthetic-key-0002')]),
                   ('mapping removed', [delete('channel_mappings', 'cmap-a100')])]
        for label, ops in changes:
            with self.subTest(label):
                self.seed()
                self.assertTrue(self.shown()['content_verified'])
                self.store.update(self.rev(), ops)
                feed = self.shown()
                self.assertNotIn('content_verified', feed)
                self.assertNotIn('verification_label', feed)
                self.assertNotIn('transport_verified', feed)   # health evidence from the old source is gone too

    def test_unrelated_settings_changes_that_do_not_touch_the_feed_keep_evidence(self):
        self.seed()
        before = self.binding()
        self.store.update(self.rev(), [up('profiles', id='prof-household', preferences={'favorites': []}),
                                      up('channel_mappings', id='cmap-b300', dispatcharr_stream='9')])   # a different channel
        self.assertEqual(self.binding(), before)
        self.assertTrue(self.shown()['content_verified'])

    def test_stale_health_history_cannot_feed_provider_wide_ranking_after_remap(self):
        self.seed()
        bind = verification._binder()
        self.assertEqual(verification._provider_health(self.api.values['stream-reliability'], bind)['providera'], 0)
        self.store.update(self.rev(), [up('channel_mappings', id='cmap-a100', dispatcharr_channel=NEW_UUID)])
        self.assertNotIn('providera', verification._provider_health(self.api.values['stream-reliability'], verification._binder()))
        self.assertEqual(verification.annotate(self.api, self.event, now=NOW)['feeds'][0].get('transport_verified'), None)
        with patch.object(self.store, 'revision', side_effect=OSError('db')):   # unbindable: provider-wide history also ignored
            self.assertNotIn('providera', verification._provider_health(self.api.values['stream-reliability'], verification._binder()))

    def test_stale_health_cannot_reorder_competing_feeds_after_remap(self):
        self.event['feeds'] = [{'provider_id': 'providera', 'stream_id': '100'}, {'provider_id': 'direct', 'stream_id': '1'}]
        mid = {'attempts': 10, 'successes': 6}   # direct account: always unbound (None), history stays valid

        def order(history_providera):
            self.api.values['stream-verification'] = {}
            self.api.values['stream-reliability'] = {'providera|100': history_providera, 'direct|1': mid} if history_providera else {'direct|1': mid}
            return [f['provider_id'] for f in verification.annotate(self.api, self.event, now=NOW)['feeds']]

        good = {'attempts': 10, 'successes': 10, 'source_binding': self.binding()}
        poor = {'attempts': 10, 'successes': 1, 'source_binding': self.binding()}
        self.assertEqual(order(good)[0], 'providera')    # valid history moves the feed up...
        self.assertEqual(order(poor)[-1], 'providera')   # ...or down, so the stale case below is a real discriminator
        self.store.update(self.rev(), [up('channel_mappings', id='cmap-a100', dispatcharr_channel=NEW_UUID)])
        baseline = order(None)
        self.assertEqual(order(good), baseline)      # old-generation history (now stale) no longer changes the order
        self.assertEqual(order(poor), baseline)

    def test_user_confirmed_language_is_bound_to_the_managed_source(self):
        import app
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fixture = {'id': 'game-1', 'kickoff': 10000}
        channel = {'provider_id': 'providera', 'stream_id': '100', 'category': 'EN| STAN', 'name': 'Event'}
        payload = {'fixture_id': 'game-1', 'provider_id': 'providera', 'stream_id': '100', 'code': 'es', 'heard_audio': True}
        gm = {'fixture': fixture, 'feeds': [{'provider_id': 'providera', 'stream_id': '100'}]}
        with patch.object(app, 'DATA', Path(tmp.name)), patch.object(app, 'resolve', return_value=gm):
            app.save_language(payload, 9500)
            got = app.audio_language(fixture, channel, 9000, 9600)
            self.assertEqual((got['code'], got['evidence']), ('es', 'user_confirmed'))
            self.assertNotIn('source_binding', got)
            for label, ops in [('remap', [up('channel_mappings', id='cmap-a100', dispatcharr_channel=NEW_UUID)]),
                               ('stream', [up('channel_mappings', id='cmap-a100', dispatcharr_stream='77')]),
                               ('key', [up('integrations', id='int-disp', api_key='dk-synthetic-key-0002')])]:
                with self.subTest(label):
                    self.store.update(self.rev(), ops)
                    self.assertEqual(app.audio_language(fixture, channel, 9000, 9600)['evidence'], 'provider_label')
            app.save_language({**payload, 'code': 'en'}, 9700)   # re-confirmation applies to the new managed source
            self.assertEqual(app.audio_language(fixture, channel, 9000, 9800)['code'], 'en')
            self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False)])   # route switch
            self.assertEqual(app.audio_language(fixture, channel, 9000, 9800)['evidence'], 'provider_label')
            with patch.object(self.store, 'revision', side_effect=OSError('db')):
                self.assertEqual(app.audio_language(fixture, channel, 9000, 9800)['evidence'], 'provider_label')

    def test_stream_track_report_does_not_survive_a_source_change(self):
        import app
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fixture = {'id': 'game-1', 'kickoff': 10000}
        channel = {'provider_id': 'providera', 'stream_id': '100', 'category': 'EN| STAN', 'name': 'Event'}
        with patch.object(app, 'DATA', Path(tmp.name)):
            app.save_cache('languages', {app.language_key(fixture, 'providera', '100'): {
                'code': 'es', 'name': 'Spanish', 'evidence': 'stream_track_reported', 'checked_at': 9500, 'observed_at': None,
                'expires_at': 20000, 'source_binding': app.language_binding('providera', '100')}})
            self.assertEqual(app.audio_language(fixture, channel, 9000, 9600)['code'], 'es')
            self.store.update(self.rev(), [up('channel_mappings', id='cmap-a100', dispatcharr_stream='77')])
            self.assertEqual(app.audio_language(fixture, channel, 9000, 9600)['evidence'], 'provider_label')

    def test_switching_route_either_way_discards_evidence_from_the_other_route(self):
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False)])
        self.assertIsNone(self.binding())
        self.seed(None)                                    # direct evidence (also what pre-M4A records look like: no binding key)
        self.api.values['stream-verification'][verification.record_key(self.event, self.event['feeds'][0])].pop('source_binding')
        self.assertTrue(self.shown()['content_verified'])
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=True)])
        self.assertNotIn('content_verified', self.shown())   # mediated now: direct bytes prove nothing about the relay's bytes
        self.seed()
        self.assertTrue(self.shown()['content_verified'])
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False)])
        self.assertNotIn('content_verified', self.shown())   # and back

    def test_unreadable_settings_never_confirm(self):
        self.seed()
        with patch.object(self.store, 'revision', side_effect=OSError('db')):
            self.assertNotIn('content_verified', self.shown())

    def test_unbound_legacy_record_does_not_confirm_a_mediated_feed(self):
        self.seed(None)
        self.assertNotIn('content_verified', self.shown())

    def wait_idle(self):
        deadline = time.monotonic() + 15
        while verification.RUNNING and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(verification.RUNNING)

    def test_revalidation_samples_the_new_managed_bytes_and_restarts_health_history(self):
        self.seed()
        self.store.update(self.rev(), [up('channel_mappings', id='cmap-a100', dispatcharr_channel=NEW_UUID)])
        self.assertNotIn('content_verified', self.shown())
        sampled = []

        def sample_feed(event, feed):
            sampled.append(self.resolver.decide(feed['provider_id'], feed['stream_id']).channel)
            return test_delivery.TS, 5
        with patch.object(verification, 'inspect_sample', return_value=confirmed(None)):
            self.assertEqual(verification.start(self.api, self.event, sample_feed)['status'], 'started')
            self.wait_idle()
        self.assertEqual(sampled, [NEW_UUID])                               # the new relay channel, not the old mapping
        self.assertTrue(self.shown()['content_verified'])
        history = self.api.values['stream-reliability']['providera|100']
        self.assertEqual((history['attempts'], history['source_binding']), (1, self.binding()))   # not 10: old health is not inherited

    def test_a_source_change_during_sampling_keeps_nothing(self):
        def sample_feed(event, feed):
            self.store.update(self.rev(), [up('channel_mappings', id='cmap-a100', dispatcharr_channel=NEW_UUID)])
            return test_delivery.TS, 5
        with patch.object(verification, 'inspect_sample', return_value=confirmed(None)):
            verification.start(self.api, self.event, sample_feed)
            self.wait_idle()
        self.assertEqual(self.api.values.get('stream-verification', {}), {})
        self.assertEqual(self.api.values.get('stream-reliability', {}), {})

    def test_binding_digest_carries_no_url_key_or_upstream_id(self):
        text = self.binding() + json.dumps(verification.annotate(self.api, self.event, now=NOW))
        for secret in (KEY, BASE, test_delivery.U1, 'dispatcharr.example'):
            self.assertNotIn(secret, text)


class AppBase(test_delivery.Base):
    """app + tv over the real settings store with a direct route active and the mediated route staged (Provider A/B, limit 1 each)."""
    def setUp(self):
        super().setUp()
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False),
                                      up('routes', id='route-direct', name='Direct', kind='direct', account_ids=['acct-providera', 'acct-providerb'], source_ids=[], enabled=True)])
        opener_for, original = self.relay.opener, delivery.open_stream
        for p in (patch.object(app, 'DATA', self.dir), patch.object(app, 'settings_store', lambda: self.store),
                  patch.object(app, 'DISPATCHARR_TRANSPORT', self.probe, create=True),
                  patch.object(app, 'Xtream', side_effect=AssertionError('direct provider contact'), create=True),
                  patch.object(delivery, 'open_stream', lambda plan, opener=None: original(plan, opener_for()))):
            p.start()
            self.addCleanup(p.stop)
        tv.configure(app)
        self.addCleanup(verification.configure_binding, None)
        tv.LEASES.clear(); tv.ACTIVE.clear(); tv.VERIFYING.clear()

    def flags(self):
        return {r['id']: r['enabled'] for r in self.store.read()['routes']}

    def post(self, sub, **body):
        return app.settings_route(True, sub, json.dumps(body).encode())

    def stage(self, route='route-disp'):
        status, plan = self.post(['switch', 'preview'], activate_route_id=route)
        self.assertEqual((status, plan['ready']), (200, True))
        return plan


class SwitchAdmission(AppBase):
    def test_idle_accounts_switch_and_no_reservation_or_media_open_is_left_behind(self):
        plan = self.stage()
        self.assertIn('real_gate_synthetic_providers_only', [w['code'] for w in plan['warnings']])
        self.assertIn('stream_change_invalidates_evidence', [w['code'] for w in plan['warnings']])
        status, _ = self.post(['switch', 'commit'], plan_id=plan['plan_id'], revision=self.rev())
        self.assertEqual(status, 200)
        self.assertEqual(self.flags(), {'route-direct': False, 'route-disp': True})
        self.assertEqual([tv.leases().holder(p) for p in ('providera', 'providerb')], [None, None])   # commit-time holds are released
        self.assertEqual(self.relay.requests, [])                                                # switching opens no stream

    def test_unhealthy_target_integration_preserves_the_old_route_and_opens_nothing(self):
        plan = self.stage()
        with patch.object(app, 'DISPATCHARR_TRANSPORT', Fake({}), create=True):   # every management request answers 404
            status, payload = self.post(['switch', 'commit'], plan_id=plan['plan_id'], revision=self.rev())
        self.assertEqual((status, payload['error']), (422, 'invalid_settings'))
        blockers = payload['details'][0]['blockers']
        self.assertEqual([b['code'] for b in blockers], ['target_integration_unavailable'])
        self.assertEqual(self.flags(), {'route-direct': True, 'route-disp': False})
        self.assertEqual((self.relay.requests, self.relay.opened), ([], {'providera': 0, 'providerb': 0}))
        self.assertEqual([tv.leases().holder(p) for p in ('providera', 'providerb')], [None, None])
        self.assertNotIn(KEY, json.dumps(payload))
        self.assertTrue(self.stage()['ready'])    # still switchable once the integration is healthy (no lasting damage)

    def test_account_taken_between_check_and_commit_refuses_the_switch(self):
        plan = self.stage()
        other = tv.leases().acquire('providera', 'playback:other', 'playback')   # a managed consumer appears after the idle check
        with patch.object(app, 'active_account_ids', lambda: set()):          # the earlier snapshot said idle
            status, payload = self.post(['switch', 'commit'], plan_id=plan['plan_id'], revision=self.rev())
        self.assertEqual(status, 422)
        self.assertEqual(payload['details'][0]['blockers'], [{'code': 'target_account_unavailable', 'account_id': 'acct-providera'}])
        self.assertEqual(self.flags(), {'route-direct': True, 'route-disp': False})
        self.assertTrue(tv.leases().validate(other))                          # the existing session is untouched
        self.assertEqual(self.relay.requests, [])

    def test_shared_hold_cooldown_also_blocks_and_active_sessions_block_via_the_normal_check(self):
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=True), up('routes', id='route-direct', enabled=False)])
        held = tv.leases().acquire('providera', 'playback:a', 'playback', stream_id='100')
        self.assertIsNotNone(held)
        status, payload = self.post(['switch', 'preview'], activate_route_id='route-direct')
        self.assertEqual(status, 200)
        self.assertFalse(payload['ready'])
        self.assertEqual(payload['blockers'], [{'code': 'managed_sessions_active', 'account_id': 'acct-providera'}])
        tv.leases().release(held)                                              # last consumer leaves: the cooldown still protects the relay's upstream
        self.assertFalse(self.post(['switch', 'preview'], activate_route_id='route-direct')[1]['ready'])
        plan = self.post(['switch', 'preview'], activate_route_id='route-direct')[1]
        with patch.object(app, 'active_account_ids', lambda: set()):
            status, payload = self.post(['switch', 'commit'], plan_id=plan['plan_id'], revision=self.rev())
        self.assertEqual(status, 422)
        self.assertEqual(payload['details'][0]['blockers'][0]['code'], 'target_account_unavailable')
        self.assertEqual(self.flags(), {'route-direct': False, 'route-disp': True})

    def test_rollback_is_admitted_the_same_way(self):
        plan = self.stage()
        self.assertEqual(self.post(['switch', 'commit'], plan_id=plan['plan_id'], revision=self.rev())[0], 200)
        tv.leases().acquire('providerb', 'playback:m', 'playback', stream_id='300')
        with patch.object(app, 'active_account_ids', lambda: set()):
            status, payload = self.post(['switch', 'rollback'], plan_id=plan['plan_id'])
        self.assertEqual((status, payload['details'][0]['blockers'][0]['code']), (422, 'target_account_unavailable'))
        self.assertEqual(self.flags(), {'route-direct': False, 'route-disp': True})   # unchanged
        tv.leases().cancel_owner('playback:m')


class TruthfulStatus(AppBase):
    def test_contract_never_claims_assurance_or_failover(self):
        status = self.store.dispatcharr_status(NOW)
        contract = status['contract']
        self.assertEqual((contract['real_gate'], contract['budget_assurance'], contract['direct_fallback']), ('executed_synthetic_providers', 'not_assured', False))
        self.assertEqual((contract['automatic_failover'], contract['lifecycle_webhooks']), ('disabled', 'disabled'))
        self.assertFalse(contract['transport_health_is_game_verification'])

    def test_delivery_state_follows_health_and_never_offers_a_direct_fallback(self):
        integration = lambda: self.store.dispatcharr_status(int(time.time()))['integrations'][0]['delivery']
        self.assertFalse(integration()['available'])                           # never checked
        dispatcharr.probe_integration(self.store, 'int-disp', Fake(api_ok()), int(time.time()))
        self.assertTrue(integration()['available'])
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False)])
        self.assertEqual((integration()['budget_assurance'], integration()['real_gate'], integration()['on_failure']),
                         ('not_assured', 'executed_synthetic_providers', 'refuse_no_direct_fallback'))   # healthy but no enabled managed route
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=True), up('routes', id='route-direct', enabled=False)])
        self.assertEqual(integration()['budget_assurance'], dispatcharr.ASSURED_MANAGED_PROXY)
        dispatcharr.probe_integration(self.store, 'int-disp', Fake({BASE + dispatcharr.VERSION_PATH: (401, b'')}), int(time.time()))
        state = integration()
        self.assertEqual((state['available'], state['on_failure']), (False, 'refuse_no_direct_fallback'))
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=True), up('routes', id='route-direct', enabled=False)])
        with self.assertRaises(delivery.Refused) as refused:                   # revoked key: refuse, never fall back to the provider
            tv.resolver().decide('providera', '100')
        self.assertEqual(refused.exception.code, 'integration_credentials_rejected')
        self.assertEqual(self.relay.requests, [])

    def test_assurance_is_not_granted_without_a_managed_route_or_when_unhealthy(self):
        healthy = {'status': 'ok', 'stale': False, 'pin_status': dispatcharr.PIN_STATUS}
        route = {'kind': 'dispatcharr', 'enabled': True, 'integration_id': 'int-disp', 'account_ids': ['acct-a']}
        item = {'id': 'int-disp', 'enabled': True}
        states = lambda state, routes: dispatcharr.delivery_state(state, item, routes)['budget_assurance']
        self.assertEqual(states(healthy, [route]), dispatcharr.ASSURED_MANAGED_PROXY)
        self.assertEqual(states(healthy, []), 'not_assured')                                       # read-only / standard-output configuration
        self.assertEqual(states(healthy, [dict(route, enabled=False)]), 'not_assured')
        self.assertEqual(states(healthy, [dict(route, account_ids=[])]), 'not_assured')
        self.assertEqual(states(healthy, [dict(route, integration_id='other')]), 'not_assured')
        self.assertEqual(states(dict(healthy, status='down'), [route]), 'not_assured')
        self.assertEqual(states(dict(healthy, pin_status='mismatch'), [route]), 'not_assured')

    def test_setup_status_delivery_block_separates_transport_from_game_verification(self):
        status = self.store.setup_status()
        row = next(r for r in status['sources'] if r['source_id'] == 'src-a1')
        self.assertEqual((row['delivery']['budget_assurance'], row['delivery']['real_gate'], row['delivery']['direct_fallback']), ('not_assured', 'executed_synthetic_providers', False))
        self.assertFalse(row['delivery']['transport_health_is_game_verification'])
        self.assertEqual((row['states']['transport_tested'], row['states']['game_confirmed']), ('not_started', 'not_started'))

    def test_status_views_expose_no_key_url_or_upstream_id(self):
        dispatcharr.probe_integration(self.store, 'int-disp', Fake(api_ok()), int(time.time()))
        views = [self.store.dispatcharr_status(int(time.time())), self.store.setup_status(), self.store.client_view()]
        text = json.dumps(views)
        for secret in SECRETS + [test_delivery.U1, test_delivery.U2, test_delivery.U3]:
            self.assertNotIn(secret, text)
        for secret in SECRETS:   # the export keeps the (non-secret) mapping ids by design, but never a key or address
            self.assertNotIn(secret, json.dumps(self.store.export()))


class Preservation(test_jfdvr.Base):
    """Add/change/disable/remove/rollback an optional integration: favorites, devices, owned DVR timers and IDs survive; nothing is duplicated."""
    FAVORITES = ['team.future.x.y.t%d' % n for n in range(1, 10)][::-1]   # 9 entries, deliberately reverse-ordered

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = settings.SettingsStore(Path(tmp.name) / 's.sqlite', paired_devices={'cd' * 32: {'expires': 4_000_000_000}})
        self.rev = lambda: self.store.read()['revision']
        test_delivery.Base.configure(self)
        self.store.update(self.rev(), [up('routes', id='route-disp', enabled=False),
                                      up('routes', id='route-direct', name='Direct', kind='direct', account_ids=['acct-providera', 'acct-providerb'], source_ids=[], enabled=True),
                                      up('profiles', id='prof-household', preferences={'favorites': self.FAVORITES}),
                                      up('profiles', id='prof-guest', name='Guest', preferences={'favorites': []})])

    def user_state(self):
        view = self.store.read()
        return {k: view[k] for k in ('profiles', 'devices', 'accounts')}, {a['account_id']: a['max_connections'] for a in self.store.budgets()['accounts']}

    def test_integration_lifecycle_keeps_users_devices_dvr_and_budgets(self):
        event = test_jfdvr.pregame(test_jfdvr.NOW + 3600)
        rid, entry = self.schedule(event)
        timers, ledger = dict(self.fake.timers), self.ledger.all()
        before = self.user_state()
        self.assertEqual(len(before[0]['devices']), 1)
        self.assertEqual([p['preferences']['favorites'] for p in before[0]['profiles']], [self.FAVORITES, []])
        # add -> switch -> rollback -> switch -> change -> disable -> switch back -> remove
        plan = self.store.preview_switch('route-disp')
        self.store.commit_switch(plan['plan_id'], self.rev())
        self.store.rollback_switch(plan['plan_id'])
        plan = self.store.preview_switch('route-disp')
        self.store.commit_switch(plan['plan_id'], self.rev())
        self.store.update(self.rev(), [up('integrations', id='int-disp', api_key='dk-synthetic-key-0002')])
        self.store.update(self.rev(), [up('integrations', id='int-disp', enabled=False)])
        plan = self.store.preview_switch('route-direct')
        self.store.commit_switch(plan['plan_id'], self.rev())
        view = self.store.disconnect_integration(self.rev(), 'int-disp')
        self.assertEqual(view['integrations'], [])
        self.assertEqual(self.user_state(), before)
        self.dvr.tick([event], self.now)                      # the owned rule is reconciled idempotently afterwards
        self.assertEqual(self.fake.timers, timers)
        self.assertEqual(self.ledger.all(), ledger)
        self.assertEqual(len(self.fake.timers), 1)
        self.assertEqual(self.ledger.get(rid)['id'], rid)    # canonical recording identity unchanged

    def test_every_budget_stays_one_allowance_per_account_through_import_and_mediation(self):
        before = {a['account_id']: a['max_connections'] for a in self.store.budgets()['accounts']}
        plan = self.store.preview_switch('route-disp')
        self.store.commit_switch(plan['plan_id'], self.rev())
        after = {a['account_id']: a['max_connections'] for a in self.store.budgets()['accounts']}
        self.assertEqual(before, after)
        self.assertEqual(before, {'acct-providera': 1, 'acct-providerb': 1})   # three imports of two subscriptions: still two allowances
        self.assertEqual(self.store.budgets()['unresolved_allowance'], 0)

    def test_invalid_input_leaves_last_good_settings_intact(self):
        before = self.store.read()
        for bad in ([up('profiles', id='prof-household', preferences={'favorites': ['FC Barcelona']})],
                    [up('routes', id='route-disp', enabled=True)],                       # two active routes for one account
                    [up('channel_mappings', id='cmap-a100', dispatcharr_channel='not valid!')]):
            with self.assertRaises(settings.SettingsError):
                self.store.update(self.rev(), bad)
        self.assertEqual(self.store.read(), before)

    def test_active_session_blocks_both_directions_and_changes_nothing(self):
        plan = self.store.preview_switch('route-disp', active_accounts={'acct-providera'})
        self.assertFalse(plan['ready'])
        with self.assertRaises(settings.SettingsError):
            self.store.commit_switch(plan['plan_id'], self.rev(), active_accounts={'acct-providera'})
        self.assertEqual({r['id']: r['enabled'] for r in self.store.read()['routes']}, {'route-direct': True, 'route-disp': False})


class CleanDirectInstall(unittest.TestCase):
    """A direct-only install (no Dispatcharr, IPTVBoss or Jellyfin) is complete, and its redacted export restores into a clean store."""
    def test_direct_only_setup_export_and_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = settings.SettingsStore(Path(tmp) / 'a.sqlite')
            first.update(1, [up('accounts', id='acct-a', name='Provider A', max_connections=1),
                             up('sources', id='src-a', name='Direct', kind='m3u', location_type='url', location='https://iptv.example.invalid/get.php?u=secretvalue', account_id='acct-a'),
                             up('routes', id='route-direct', name='Direct', kind='direct', account_ids=['acct-a'], source_ids=['src-a'], enabled=True),
                             up('profiles', id='prof-household', preferences={'favorites': ['team.future.x.y.t2', 'team.future.x.y.t1']}),
                             up('profiles', id='prof-guest', name='Guest', preferences={'favorites': []})])
            view = first.read()
            for section in ('integrations', 'channel_mappings', 'capability_snapshots'):
                self.assertEqual(view[section], [])
            self.assertEqual(first.dispatcharr_status(NOW)['integrations'], [])
            self.assertNotIn('delivery', first.setup_status()['sources'][0])
            export = first.export()
            self.assertNotIn('secretvalue', json.dumps(export))
            supplied = {'%s/%s/%s' % (n['section'], n['id'], n['field']): 'https://iptv.example.invalid/get.php?u=secretvalue' for n in export['secrets_required']}
            second = settings.SettingsStore(Path(tmp) / 'b.sqlite')
            restored = second.restore(1, export, supplied)
            self.assertEqual([p['preferences']['favorites'] for p in restored['profiles'] if p['id'] in ('prof-household', 'prof-guest')],
                             [['team.future.x.y.t2', 'team.future.x.y.t1'], []])   # order kept, explicit empty stays empty
            self.assertEqual([r['id'] for r in restored['routes']], ['route-direct'])
            self.assertEqual(restored['integrations'], [])
            bad = dict(export, schema_version=export['schema_version'] + 1)
            with self.assertRaises(settings.SettingsError):
                second.restore(restored['revision'], bad, supplied)
            self.assertEqual(second.read()['profiles'], restored['profiles'])   # failed restore leaves the last-good settings


if __name__ == '__main__':
    unittest.main()
