"""Controlled optional Dispatcharr delivery (Phase 7A M3B, D3): the mediated route behind the existing lease/open interface.

One allocation authority. The persistent provider lease (lease.py) stays the only place a managed consumer is admitted,
whether the account is delivered directly or through a Dispatcharr proxy. Mediated mode adds three things on top of it:
- same-channel fan-out: consumers of one mapped Dispatcharr channel join one shared hold (one upstream at the relay);
  any other channel, verification or recording on the same account is refused while the hold lives;
- a cooldown after the last consumer leaves (default COOLDOWN_SECONDS), because the relay can keep its upstream open for a
  shutdown delay after its last client goes; releasing the account earlier could exceed the provider limit;
- fail closed: an unmapped channel, an unhealthy or unpinned integration, a Redirect/Direct-URL profile or an unreachable relay
  refuses the request. There is never a silent fallback to a provider URL.

Only the media path is handled here (GET /proxy/ts/stream/<channel uuid>, which in the pinned release needs no credential and is
gated by Dispatcharr's own network policy). The management API key is never sent on that path. Nothing here writes to Dispatcharr.
"""
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import dispatcharr
import lease

COOLDOWN_SECONDS = 30
MAX_COOLDOWN_SECONDS = 600
STREAM_TIMEOUT = 12
UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
HEALTHY = ('ok', 'degraded')


class Refused(Exception):
    """Delivery is refused; code is a short machine-readable reason (never carries a URL)."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def account_for_provider(provider_id):
    """Legacy provider id -> settings account id (same naming as the legacy import)."""
    return 'acct-' + re.sub(r'[^a-z0-9_-]', '-', str(provider_id).lower()).strip('-_')[:40]


def canonical_channel(provider_id, stream_id):
    """Route-independent identity of a legacy feed. Mappings key on this, so a route change never renames a channel."""
    slug = re.sub(r'[^a-z0-9._-]', '-', ('%s-%s' % (provider_id, stream_id)).lower()).strip('-._')[:80]
    return 'channel.' + slug


def route_for(view, provider_id):
    """The enabled Dispatcharr route covering the account, or None for a direct account."""
    account = account_for_provider(provider_id)
    return next((r for r in view['routes'] if r.get('enabled', True) and r.get('kind') == 'dispatcharr' and account in r.get('account_ids', [])), None)


def mapping_for(view, route, integration_id, provider_id, stream_id):
    wanted, account = canonical_channel(provider_id, stream_id), account_for_provider(provider_id)
    return next((m for m in view['channel_mappings'] if m.get('canonical_channel_id') == wanted and m.get('integration_id') == integration_id
                 and m.get('source_id') in route.get('source_ids', []) and m.get('account_id') in (None, account)), None)


def source_binding(view, provider_id, stream_id):
    """Fingerprint of everything that decides which managed bytes a feed delivers: None for a direct account, otherwise a digest of
    the route, integration and channel-mapping generations plus the mapped Dispatcharr channel/stream ids. Content, language and
    health evidence is stamped with it and stops counting the moment it changes (re-mapping, key rotation, route switch,
    removal). It carries no URL, key or upstream id outward: only the digest is stored."""
    route = route_for(view, provider_id)
    if route is None:
        return None
    integration_id = route.get('integration_id')
    integration = next((i for i in view['integrations'] if i['id'] == integration_id), None)
    mapping = mapping_for(view, route, integration_id, provider_id, stream_id) if integration and stream_id is not None else None
    parts = [route['id'], route.get('generation'), integration_id, integration and integration.get('generation'),
             mapping and mapping['id'], mapping and mapping.get('generation'),
             mapping and mapping.get('dispatcharr_channel'), mapping and mapping.get('dispatcharr_stream')]
    return 'dispatcharr:' + hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


_VIEWS = {}


def binder(store):
    """feed -> source_binding for one coherent settings revision (cached by revision so a ranking pass costs one cheap query).
    If the settings cannot be read the binding is unknowable, so every feed gets a value that matches no stored evidence."""
    try:
        revision = store.revision()
        cached = _VIEWS.get(store.path)
        if cached is None or cached[0] != revision:
            view = store.read()
            cached = _VIEWS[store.path] = (view['revision'], view)
        view = cached[1]
    except Exception:
        return lambda feed: 'binding_unavailable'
    return lambda feed: source_binding(view, feed.get('provider_id'), feed.get('stream_id'))


class Plan:
    """A mediated delivery decision. base_url is backend-only and is not part of repr/str."""
    def __init__(self, share, base_url, channel, cooldown, integration_id):
        self.share, self.base_url, self.channel, self.cooldown, self.integration_id = share, base_url, channel, cooldown, integration_id

    def __repr__(self):
        return 'Plan(share=%r)' % self.share

    @property
    def url(self):
        return self.base_url.rstrip('/') + '/proxy/ts/stream/' + self.channel


class Resolver:
    """Decides, per account and feed, direct or mediated delivery from the settings authority (never from a connection poll)."""
    def __init__(self, store, transport=None, clock=time.time):
        self.store, self.transport, self.clock = store, transport or dispatcharr.urllib_transport, clock

    def _route(self, view, provider_id):
        return route_for(view, provider_id)

    def mediated(self, provider_id):
        """True when an enabled Dispatcharr route covers the account (so no direct provider contact is allowed)."""
        return self._route(self.store.read(), provider_id) is not None

    def _healthy(self, view, integration_id):
        now = int(self.clock())
        snap = next((o for o in view['capability_snapshots'] if o['subject'] == 'integration' and o['subject_id'] == integration_id), None)
        if snap is None or now - int(snap['observed_at']) > dispatcharr.HEALTH_STALE_SECONDS:
            try:
                dispatcharr.probe_integration(self.store, integration_id, self.transport, now)
            except Exception:
                raise Refused('integration_unavailable') from None
            snap = next((o for o in self.store.read()['capability_snapshots'] if o['subject'] == 'integration' and o['subject_id'] == integration_id), None)
        if snap is None:
            raise Refused('integration_unavailable')
        if snap.get('status') not in HEALTHY:
            raise Refused('integration_' + str(snap.get('status')))
        if snap.get('pin_status') != 'pinned':
            raise Refused('integration_not_pinned')

    def decide(self, provider_id, stream_id):
        """None for a direct account; a Plan for a mediated one; raises Refused otherwise."""
        view = self.store.read()
        route = self._route(view, provider_id)
        if route is None:
            return None
        integration = next((i for i in view['integrations'] if i['id'] == route.get('integration_id') and i.get('kind') == 'dispatcharr'), None)
        if integration is None or not integration.get('enabled', True):
            raise Refused('integration_disabled')
        if stream_id is None:
            raise Refused('channel_required')
        mapping = mapping_for(view, route, integration['id'], provider_id, stream_id)
        if mapping is None:
            raise Refused('channel_unmapped')
        uuid = mapping.get('dispatcharr_channel') or ''
        if not UUID_RE.match(uuid):
            raise Refused('channel_not_a_uuid')
        self._healthy(view, integration['id'])
        creds, _ = self.store.integration_credentials(integration['id'])
        if not creds.get('base_url'):
            raise Refused('integration_unavailable')
        try:
            cooldown = max(0, min(MAX_COOLDOWN_SECONDS, int((integration.get('options') or {}).get('shutdown_delay_seconds', COOLDOWN_SECONDS))))
        except (TypeError, ValueError):
            cooldown = COOLDOWN_SECONDS
        return Plan('%s:%s' % (integration['id'], uuid.lower()), creds['base_url'], uuid.lower(), cooldown, integration['id'])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_stream(plan, opener=None):
    """Open the mediated stream. A redirect is refused (the Direct URL/Redirect profile would send the consumer to the provider)."""
    opener = opener or urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(plan.url, headers={'User-Agent': 'SGSportsTV/0.1'}, method='GET')
    try:
        response = opener.open(request, timeout=STREAM_TIMEOUT)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise Refused('redirect_profile' if code in (301, 302, 303, 307, 308) else 'relay_http_%d' % code) from None
    except (OSError, ValueError):
        raise Refused('relay_unreachable') from None
    status = getattr(response, 'status', 200)
    if status in (301, 302, 303, 307, 308):
        response.close()
        raise Refused('redirect_profile')
    if status != 200:
        response.close()
        raise Refused('relay_http_%s' % status)
    return response


class DeliveryAuthority(lease.LeaseAuthority):
    """The lease authority every managed entry point already uses, made delivery-aware: mediated accounts are admitted through
    shared holds keyed by relay channel; direct accounts behave exactly as before."""
    def __init__(self, path, resolver, clock=time.time):
        super().__init__(path, clock)
        self.resolver = resolver
        self.last_refusal = threading.local()

    def acquire(self, provider, owner, kind, ttl=lease.DEFAULT_TTL, priority=None, preempt=False, exclusive=False, stream_id=None):
        self.last_refusal.code = None
        try:
            plan = self.resolver.decide(provider, stream_id)
        except Refused as refusal:
            self.last_refusal.code = refusal.code
            return None
        if plan is None:
            return super().acquire(provider, owner, kind, ttl, priority, preempt, exclusive)
        if exclusive and self.holds(provider, owner):
            self.last_refusal.code = 'owner_already_streaming'
            return None
        granted = self.acquire_shared(provider, plan.share, owner, kind, ttl, priority, preempt, plan.cooldown)
        if granted is None:
            self.last_refusal.code = 'provider_in_use'
        return granted

