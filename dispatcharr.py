"""Optional Dispatcharr support (Phase 7A M3A): D1 standard outputs and the D2 read-only capability contract.

Metadata only. Nothing here opens a media stream, starts a recording, writes to Dispatcharr or touches its database.
The management API key and base URL are backend-only: they are resolved from the settings secret store for one request,
sent only to the configured host, and never returned, logged or stored in an observation. Failures are reported as short
codes, never as exception text (which can carry URLs). Delivery assurance is never claimed from a playlist: a standard
output proves a playlist shape, not who holds upstream capacity (that is D3, out of scope for this module).
"""
import gzip
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Auth header variants. Both are accepted by apps/accounts/authentication.py ApiKeyAuthentication in the pinned release
# (X-API-Key first, then 'Authorization: ApiKey <key>'); the variant stays an explicit per-integration choice, never auto-tried.
AUTH_VARIANTS = ('x-api-key', 'authorization-apikey')
DEFAULT_AUTH_VARIANT = 'x-api-key'
# The one release the contract was verified against (source + registry evidence in PHASE7A-M3B.md). Any other version is
# reported as version_mismatch: no delivery assurance is claimed for an unverified release.
PINNED_RELEASE = {'version': '0.32.0', 'image': 'ghcr.io/dispatcharr/dispatcharr',
                  'index_digest': 'sha256:b7d695c5cc98b9abd64c74a94539c1ebffc23d965021613c820c67e25dea41a3',
                  'linux_amd64_digest': 'sha256:5586d023b906496667bcd4a00b3973d7217e5628df9bd48a66d65e86fe5e29f0'}
PINNED_VERSION = (0, 32, 0)
PIN_STATUS = 'pinned'

VERSION_PATH = '/api/core/version/'
CAPABILITY_PATHS = {
    'channels': '/api/channels/channels/?page_size=1',
    'streams': '/api/channels/streams/?page_size=1',
    'm3u_accounts': '/api/m3u/accounts/',
    'proxy_status': '/proxy/ts/status',
}
REQUIRED_CAPABILITIES = ('channels', 'streams')
HEALTH_STALE_SECONDS = 900
REQUEST_TIMEOUT = 5
JSON_LIMIT = 262144
PLAYLIST_LIMIT = 8 * 1024 * 1024
GUIDE_LIMIT = 16 * 1024 * 1024
ENTRY_LIMIT = 50000
GUIDE_CHANNEL_LIMIT = 50000
PAGE_SIZE_LIMIT = 100

PROXY_PATH = re.compile(r'^/proxy/(ts|hls)/stream/[0-9A-Za-z-]{8,64}/?$')
ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
API_KEY_RE = re.compile(r'^[A-Za-z0-9._~+/=-]{8,256}$')


class TransportError(Exception):
    """A request failed; code is one of timeout, connection, tls, too_large, redirect."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def valid_base_url(value):
    try:
        parts = urllib.parse.urlsplit(value)
        return (parts.scheme in ('http', 'https') and bool(parts.hostname) and not parts.username and not parts.password
                and not parts.query and not parts.fragment and not re.search(r'\s', value) and (parts.port or True))
    except ValueError:
        return False


def valid_api_key(value):
    return isinstance(value, str) and bool(API_KEY_RE.match(value))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def urllib_transport(url, headers, timeout=REQUEST_TIMEOUT, max_bytes=JSON_LIMIT):
    """GET only; no redirects (a redirect would forward the key to another host); bounded body. Returns (status, bytes)."""
    request = urllib.request.Request(url, headers=headers, method='GET')
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status, body = response.status, response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            body = error.read(max_bytes + 1)
        finally:
            error.close()
    except TimeoutError:
        raise TransportError('timeout') from None
    except urllib.error.URLError as error:
        raise TransportError('tls' if 'SSL' in type(error.reason).__name__ else 'connection') from None
    except OSError:
        raise TransportError('connection') from None
    if len(body) > max_bytes:
        raise TransportError('too_large')
    return status, body


def _auth_headers(api_key, variant):
    if variant == 'authorization-apikey':
        return {'Authorization': 'ApiKey ' + api_key, 'Accept': 'application/json'}
    return {'X-API-Key': api_key, 'Accept': 'application/json'}


def parse_version(text):
    match = re.match(r'^v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?', str(text or '').strip())
    return tuple(int(g or 0) for g in match.groups()) if match else None


def _join(base_url, path):
    return base_url.rstrip('/') + path


def probe(creds, transport, now):
    """Read-only capability negotiation. creds = {'base_url','api_key','auth_variant'} resolved by the backend.

    Returns a secret-free observation body. Only the version string is read from a response; every other probe keeps
    nothing but its HTTP class (available / absent / unauthorized / error).
    """
    body = {'pin_status': 'unpinned', 'version': None, 'capabilities': {}, 'status': 'not_configured', 'reason': None,
            'supported_window': {'pinned': PINNED_RELEASE['version']}}
    if not (creds.get('base_url') and creds.get('api_key')):
        body['reason'] = 'base_url_and_api_key_required'
        return body
    variant = creds.get('auth_variant') or DEFAULT_AUTH_VARIANT
    if variant not in AUTH_VARIANTS:
        body.update(status='capability_mismatch', reason='unsupported_auth_variant')
        return body
    headers = _auth_headers(creds['api_key'], variant)
    body['auth_variant'] = variant
    try:
        status, raw = transport(_join(creds['base_url'], VERSION_PATH), headers, REQUEST_TIMEOUT, JSON_LIMIT)
        if status in (401, 403):
            body.update(status='credentials_rejected', reason='http_%d' % status)
            return body
        if status != 200:
            body.update(status='unavailable', reason='http_%d' % status)
            return body
        try:
            data = json.loads(raw)
            version = data.get('version') if isinstance(data, dict) else None
        except ValueError:
            version = None
        parsed = parse_version(version)
        body['version'] = str(version)[:32] if parsed else None
        for name, path in CAPABILITY_PATHS.items():
            code, _ = transport(_join(creds['base_url'], path), headers, REQUEST_TIMEOUT, JSON_LIMIT)
            body['capabilities'][name] = ('available' if code == 200 else 'unauthorized' if code in (401, 403)
                                          else 'absent' if code in (404, 405) else 'error')
    except TransportError as error:
        body.update(status='unavailable', reason=error.code)
        return body
    caps = body['capabilities']
    if parsed == PINNED_VERSION:
        body['pin_status'] = PIN_STATUS
    if parsed is None:
        body.update(status='version_mismatch', reason='version_unreadable')
    elif parsed != PINNED_VERSION:
        body.update(status='version_mismatch', reason='not_the_pinned_release')
    elif any(v == 'unauthorized' for v in caps.values()):
        body.update(status='credentials_rejected', reason='key_not_authorized')
    elif any(caps.get(c) != 'available' for c in REQUIRED_CAPABILITIES):
        body.update(status='capability_mismatch', reason='required_capability_missing')
    elif any(v != 'available' for v in caps.values()):
        body.update(status='degraded', reason='optional_capability_missing')
    else:
        body.update(status='ok', reason=None)
    return body


def health(observation, now):
    """Read view of an observation: adds age and a visible stale flag (stale health is never reported as ok)."""
    if not observation:
        return {'status': 'never_checked', 'stale': True, 'age_seconds': None}
    age = max(0, int(now) - int(observation['observed_at']))
    stale = age > HEALTH_STALE_SECONDS
    return {'status': 'stale' if stale else observation['body'].get('status', 'unavailable'), 'last_status': observation['body'].get('status'),
            'stale': stale, 'age_seconds': age, 'version': observation['body'].get('version'), 'reason': observation['body'].get('reason'),
            'capabilities': observation['body'].get('capabilities', {}), 'pin_status': observation['body'].get('pin_status', 'unpinned'),
            'observed_at': observation['observed_at']}


def parse_m3u(text):
    """Entries from an M3U body: [{'tvg_id','name','url'}]. Bounded; the URLs are classified then discarded by callers."""
    entries, pending = [], None
    for line in str(text).splitlines():
        line = line.strip()
        if line.startswith('#EXTINF'):
            attrs = dict(ATTR.findall(line))
            pending = {'tvg_id': attrs.get('tvg-id', ''), 'name': line.rsplit(',', 1)[-1].strip()[:120] if ',' in line else ''}
        elif line and not line.startswith('#') and pending is not None:
            pending['url'] = line
            entries.append(pending)
            pending = None
            if len(entries) >= ENTRY_LIMIT:
                break
    return entries


def url_shape(url):
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return 'unparseable'
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return 'unparseable'
    return 'proxy_path' if PROXY_PATH.match(parts.path) else 'direct_or_other'


def guide_channel_ids(raw):
    """XMLTV channel ids from XML or XML.GZ bytes. DTD/entity declarations are refused before parsing."""
    if raw[:2] == b'\x1f\x8b':
        try:
            raw = gzip.decompress(raw) if len(raw) <= GUIDE_LIMIT else b''
        except (OSError, EOFError):
            raise ValueError('guide_unreadable') from None
        if len(raw) > GUIDE_LIMIT:
            raise ValueError('guide_too_large')
    head = raw[:4096].lower()
    if b'<!doctype' in head or b'<!entity' in raw[:65536].lower():
        raise ValueError('guide_dtd_not_allowed')
    ids = set()
    try:
        for _, element in ET.iterparse(io.BytesIO(raw), events=('end',)):
            if element.tag == 'channel' and element.get('id'):
                ids.add(element.get('id'))
                if len(ids) >= GUIDE_CHANNEL_LIMIT:
                    break
            element.clear()
    except ET.ParseError:
        raise ValueError('guide_unreadable') from None
    return ids


def assess(playlist_text, guide_raw=None):
    """Classify a Dispatcharr standard-output playlist (and optional matching guide). Secret-free, URL-free result.

    budget_assurance is always 'not_assured' here: a playlist shows a URL shape only. A proxy-looking path cannot show
    that the channel's stream profile is not Redirect, and any direct URL is a bypass of a central allocator.
    """
    entries = parse_m3u(playlist_text)
    counts = {'proxy_path': 0, 'direct_or_other': 0, 'unparseable': 0}
    for entry in entries:
        counts[url_shape(entry['url'])] += 1
    reasons = ['standard_output_only']
    if not entries:
        profile = 'empty'
        reasons.append('no_entries')
    elif counts['proxy_path'] == len(entries):
        profile = 'proxy_path'
        reasons.append('redirect_profile_not_detectable_from_playlist')
    elif counts['proxy_path']:
        profile = 'mixed'
        reasons += ['direct_url_present', 'redirect_profile_not_detectable_from_playlist']
    else:
        profile = 'direct_or_unknown'
        reasons.append('direct_url_present' if counts['direct_or_other'] else 'unknown_profile')
    if counts['unparseable']:
        reasons.append('unparseable_entries')
    result = {'entries': len(entries), 'proxy_path': counts['proxy_path'], 'direct_or_other': counts['direct_or_other'], 'unparseable': counts['unparseable'],
              'delivery_profile': profile, 'budget_assurance': 'not_assured', 'assurance_reasons': reasons,
              'catalog': 'imported' if entries else 'empty', 'guide': None, 'status': 'ok' if entries else 'empty'}
    if guide_raw is not None:
        wanted = {e['tvg_id'] for e in entries if e['tvg_id']}
        try:
            have = guide_channel_ids(guide_raw)
            result['guide'] = {'channels': len(have), 'playlist_ids': len(wanted), 'matched': len(wanted & have),
                               'unmatched': len(wanted - have)}
        except ValueError as error:
            result['guide'] = {'error': str(error)}
    return result


def _page_items(data):
    if isinstance(data, dict) and isinstance(data.get('results'), list):
        return data['results']
    return data if isinstance(data, list) else []


def sanitize_channel(item):
    """Allowlist of a channel record. Stream URLs, credentials and every other field are dropped."""
    if not isinstance(item, dict):
        return None
    streams = [s for s in item.get('streams', []) if isinstance(s, int) and not isinstance(s, bool)][:50] if isinstance(item.get('streams'), list) else []
    return {'channel': str(item.get('id'))[:64], 'uuid': str(item.get('uuid') or '')[:64] or None, 'name': str(item.get('name') or '')[:120],
            'number': item.get('channel_number') if isinstance(item.get('channel_number'), (int, float)) and not isinstance(item.get('channel_number'), bool) else None,
            'streams': streams, 'stream_profile_id': item.get('stream_profile_id') if isinstance(item.get('stream_profile_id'), int) else None}


def sanitize_stream(item):
    if not isinstance(item, dict):
        return None
    account = item.get('m3u_account')
    return {'stream': str(item.get('id'))[:64], 'name': str(item.get('name') or '')[:120],
            'account': str(account)[:64] if isinstance(account, (int, str)) and not isinstance(account, bool) else None}


def read_page(creds, transport, kind, page):
    """One bounded page of channels or streams for explicit mapping. Raises TransportError on failure."""
    path = {'channels': '/api/channels/channels/', 'streams': '/api/channels/streams/'}[kind]
    url = _join(creds['base_url'], path + '?page_size=%d&page=%d' % (PAGE_SIZE_LIMIT, page))
    status, raw = transport(url, _auth_headers(creds['api_key'], creds.get('auth_variant') or DEFAULT_AUTH_VARIANT), REQUEST_TIMEOUT, JSON_LIMIT * 4)
    if status in (401, 403):
        return {'status': 'credentials_rejected', 'items': []}
    if status != 200:
        return {'status': 'unavailable', 'items': []}
    try:
        data = json.loads(raw)
    except ValueError:
        return {'status': 'unavailable', 'items': []}
    clean = sanitize_channel if kind == 'channels' else sanitize_stream
    return {'status': 'ok', 'items': [c for c in map(clean, _page_items(data)[:PAGE_SIZE_LIMIT]) if c]}


# --- orchestration over the settings store (duck-typed: no import of settings here) ------------------------
def probe_integration(store, integration_id, transport, now):
    creds, generation = store.integration_credentials(integration_id)
    body = probe(creds, transport, now)
    store.record_observation('integration', integration_id, generation, body, now)
    return health({'observed_at': now, 'body': body}, now)


def assess_source(store, source_id, guide_source_id, transport, now):
    """Fetch the playlist (and optional guide) the operator configured and classify it. No stream URL is ever requested."""
    playlist_url, generation = store.input_location('sources', source_id)
    guide_url = store.input_location('guide_sources', guide_source_id)[0] if guide_source_id else None
    unavailable = {'status': 'unavailable', 'budget_assurance': 'not_assured', 'catalog': 'not_imported'}
    try:
        status, raw = transport(playlist_url, {'Accept': '*/*'}, REQUEST_TIMEOUT * 2, PLAYLIST_LIMIT)
        if status != 200:
            body = dict(unavailable, reason='playlist_http_%d' % status)
        else:
            guide_raw = None
            if guide_url:
                gstatus, graw = transport(guide_url, {'Accept': '*/*'}, REQUEST_TIMEOUT * 2, GUIDE_LIMIT)
                guide_raw = graw if gstatus == 200 else None
            body = assess(raw.decode('utf-8', 'replace'), guide_raw)
            if guide_url and guide_raw is None:
                body['guide'] = {'error': 'guide_unavailable'}
    except TransportError as error:
        body = dict(unavailable, reason=error.code)
    store.record_observation('source', source_id, generation, body, now)
    return body


def check_guide(store, guide_source_id, transport):
    """Read a configured XMLTV guide once and report whether it is reachable and parses. Secret-free; nothing is stored or imported."""
    url, _ = store.input_location('guide_sources', guide_source_id)
    try:
        status, raw = transport(url, {'Accept': '*/*'}, REQUEST_TIMEOUT * 2, GUIDE_LIMIT)
    except TransportError as error:
        return {'status': 'unavailable', 'reason': error.code}
    if status != 200:
        return {'status': 'unavailable', 'reason': 'guide_http_%d' % status}
    try:
        return {'status': 'ok', 'channels': len(guide_channel_ids(raw))}
    except ValueError as error:
        return {'status': 'unreadable', 'reason': str(error)}


# What SG Sports will truthfully say about an optional Dispatcharr connection. These are statements about the integration's
# evidence level, not settings: none of them can be switched on by configuration.
# Evidence level of the pinned release. Job 77d9bd4e ran real Dispatcharr v0.32.0 with real SG Sports lease/delivery code, synthetic
# Provider A/B and a synthetic Jellyfin consumer (13/13). It did not run a Jellyfin server; the separate pinned Jellyfin 10.10.7 job
# c95989c8 (24/24) is the real-server evidence. No combined real-Dispatcharr + real-Jellyfin stack has run. No real provider playback.
REAL_GATE = 'executed_synthetic_providers'
ASSURED_MANAGED_PROXY = 'assured_managed_proxy_route'   # only for an enabled, mapped, healthy, pinned Proxy route admitted by SG Sports leases


def capability_contract():
    """Static, secret-free statement served to the browser (and usable by TV-facing clients) about what an integration does and does not guarantee."""
    return {'pinned_release': PINNED_RELEASE['version'], 'real_gate': REAL_GATE, 'budget_assurance': 'not_assured',
            'budget_assurance_states': {'not_assured': 'default: standard outputs, read-only access, playlists, direct/redirect profiles, unmapped or unhealthy routes',
                                        ASSURED_MANAGED_PROXY: 'only a managed Proxy route (explicit account mapping, pinned healthy integration, redirect refused) where SG Sports leases are the allocation authority'},
            'gate_evidence': {'dispatcharr': 'job 77d9bd4e: real Dispatcharr v0.32.0 + real SG Sports lease/delivery code, synthetic providers and synthetic Jellyfin consumer',
                              'jellyfin': 'job c95989c8: real Jellyfin 10.10.7 (separate stack)', 'combined_real_stack': False, 'real_provider_playback': False},
            'budget_authority': 'sg_sports_lease', 'transport_health_is_game_verification': False, 'direct_fallback': False,
            'automatic_failover': 'disabled', 'lifecycle_webhooks': 'disabled',
            'stream_change': 'invalidates_game_language_and_health_evidence_until_reverified_on_new_managed_bytes',
            'route_change': 'manual_staged_switch_requires_idle_accounts_and_target_admission'}


def delivery_state(state, integration, routes=()):
    """Whether SG Sports would deliver through this integration right now. Anything else refuses; there is never a direct fallback.

    budget_assurance is ASSURED_MANAGED_PROXY only when usable and an enabled dispatcharr route with explicit accounts targets this
    integration (SG Sports allocates via leases, redirects refused). Otherwise it stays 'not_assured'."""
    usable = (integration.get('enabled', True) and state.get('status') in ('ok', 'degraded') and not state.get('stale')
              and state.get('pin_status') == PIN_STATUS)
    managed = bool(usable) and any(r.get('kind') == 'dispatcharr' and r.get('enabled', True) and r.get('integration_id') == integration.get('id')
                                   and r.get('account_ids') for r in routes)
    return {'available': bool(usable), 'on_failure': 'refuse_no_direct_fallback', 'budget_assurance': ASSURED_MANAGED_PROXY if managed else 'not_assured',
            'real_gate': REAL_GATE,
            'reason': None if usable else ('disabled' if not integration.get('enabled', True) else state.get('reason') or state.get('status'))}
