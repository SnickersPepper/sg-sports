"""SG Sports metadata-only API. No media URL construction or playback requests."""
import base64
import hmac
import sys
import tv
import secrets
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from schedule import fetch_schedule
import livescores
import guide
import catalog
import settings
import dispatcharr
import delivery
import lease
import ops
import artwork
import adminauth

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('SG_DATA', '/data'))
SECRETS = Path(os.environ.get('SG_SECRETS', '/run/secrets'))
def _providers_file():
    # Operator-supplied list (SG_PROVIDERS_FILE or <data>/providers.json); the bundled file holds only synthetic examples.
    override = os.environ.get('SG_PROVIDERS_FILE')
    if override:
        return Path(override)
    return DATA / 'providers.json' if (DATA / 'providers.json').exists() else ROOT / 'providers.json'


PROVIDERS = {p['id']: p for p in json.loads(_providers_file().read_text())}
LOCK = threading.Lock()
LOCK_SETTINGS = threading.Lock()
LANGUAGE_LOCK = threading.Lock()
SCREEN_TOKEN = secrets.token_urlsafe(32)


def secure_request(headers):
    """True when the browser reached us over HTTPS (operator-forced, or a trusted TLS-terminating proxy said so)."""
    if os.environ.get('SG_COOKIE_SECURE') == '1':
        return True
    return os.environ.get('SG_TRUST_PROXY') == '1' and (headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower() == 'https'


def admin_session(headers):
    return adminauth.session_for(DATA, SECRETS, headers.get('Cookie', ''))


def admin_session_ok(headers, mutation=True):
    """An administrator browser session that has finished the forced password change (and, for a write, carries its CSRF token from our own origin)."""
    session = admin_session(headers)
    if not session or session['must_change']:
        return False
    if mutation:
        if headers.get('Origin') and not screen_origin_ok(headers):
            return False
        return adminauth.csrf_ok(session, headers.get('X-SG-CSRF', ''))
    return True


def screen_origin_ok(headers):
    """The browser page must be this backend's own page: Origin matches the Host it was served from, or an operator-listed SG_ALLOWED_ORIGINS entry (for a TLS-terminating proxy that rewrites Host)."""
    origin = headers.get('Origin', '')
    if not origin:
        return False
    allowed = [o.strip().rstrip('/') for o in os.environ.get('SG_ALLOWED_ORIGINS', '').split(',') if o.strip()]
    if origin in allowed:
        return True
    host = headers.get('X-Forwarded-Host' if os.environ.get('SG_TRUST_PROXY') == '1' else 'Host', '') or headers.get('Host', '')
    return bool(host) and urllib.parse.urlsplit(origin).netloc == host
LANGUAGES = {'en': 'English', 'es': 'Spanish', 'pt': 'Portuguese', 'no': 'Norwegian', 'fr': 'French', 'de': 'German', 'it': 'Italian', 'ar': 'Arabic', 'ca': 'Catalan', 'nl': 'Dutch'}
STOP = threading.Event()
METRICS = ops.Metrics()
LIMITER = ops.RateLimiter()
SPORTS = re.compile(r'sport|espn|bein|liga|football|soccer|dazn|premier|barcelona|barca|barça|champions league', re.I)
BARCA = re.compile(r'\b(?:fc\s+barcelona|barcelona|barça|barca)\b', re.I)
EXCLUDE_PATTERN = re.compile(r'\b(?:replay|highlights|women|liga f|u19|u-19|handball|basketball|ehf)\b', re.I)
NEXT_GAME_WINDOW = 14 * 86400

class ProviderError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class Xtream:
    def __init__(self, config):
        self.config = config
        try:
            self.credentials = json.loads((SECRETS / (config['id'] + '.json')).read_text())
        except (OSError, ValueError):
            raise ProviderError('credentials_required') from None
        if not all(self.credentials.get(k) for k in ('username', 'password')):
            raise ProviderError('credentials_required')
        self.host = None

    def request(self, host, action=None, **params):
        if action not in (None, 'get_live_categories', 'get_live_streams', 'get_short_epg'):
            raise ProviderError('action_not_allowed')
        query = {k: self.credentials[k] for k in ('username', 'password')}
        if action:
            query['action'] = action
        query.update(params)
        url = host.rstrip('/') + '/player_api.php?' + urllib.parse.urlencode(query)
        try:
            # No redirects: credentials must never follow an untrusted Location header.
            with urllib.request.build_opener(NoRedirect).open(url, timeout=12) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError()
            return json.loads(raw)
        except Exception:
            # Network exception messages commonly contain the credential-bearing URL.
            raise ProviderError('metadata_request_failed') from None

    def account(self):
        for host in self.config['hosts']:
            try:
                result = self.request(host)
                info = result.get('user_info', {})
                if str(info.get('auth')) != '1':
                    continue
                self.host = host
                return {k: info.get(k) for k in ('status', 'exp_date', 'active_cons', 'max_connections', 'allowed_output_formats')}
            except (ProviderError, AttributeError):
                continue
        raise ProviderError('no_host_authenticated')

ADAPTERS = {'xtream': Xtream}

def read_cache(pid):
    try:
        return json.loads((DATA / (pid + '.json')).read_text())
    except (OSError, ValueError):
        return None

def refresh(pid):
    cfg = PROVIDERS[pid]
    adapter = ADAPTERS[cfg['type']](cfg)
    account = adapter.account()
    categories = adapter.request(adapter.host, 'get_live_categories')
    streams = adapter.request(adapter.host, 'get_live_streams')
    if not isinstance(categories, list) or not isinstance(streams, list):
        raise ProviderError('invalid_catalog')
    cats = {str(c['category_id']): str(c.get('category_name', '')) for c in categories}
    channels = []
    for c in streams:
        category = cats.get(str(c.get('category_id')), '')
        channels.append({'provider_id': pid, 'stream_id': str(c['stream_id']),
                         'name': str(c.get('name', '')), 'category': category,
                         'epg_channel_id': c.get('epg_channel_id'),
                         'sports': bool(SPORTS.search(category + ' ' + str(c.get('name', ''))))})
    old = read_cache(pid) or {}
    result = {'provider_id': pid, 'updated_at': int(time.time()), 'host': adapter.host,
              'account': account, 'categories': [{'id': k, 'name': v} for k, v in cats.items()],
              'channels': channels, 'epg': old.get('epg', []),
              'epg_updated_at': old.get('epg_updated_at'),
              'epg_checked_ids': old.get('epg_checked_ids', []), 'epg_capabilities': {
                  'channel_ids_present': sum(bool(c['epg_channel_id']) for c in channels),
                  'short_epg': old.get('epg_capabilities', {}).get('short_epg', 'not_tested'), 'xmltv': 'not_tested'},
              'epg_coverage': {'checked': len(old.get('epg_checked_ids', [])), 'total_sports': sum(c['sports'] for c in channels)}}
    save_cache(pid, result)
    return summary(result)

def save_cache(pid, value):
    DATA.mkdir(parents=True, exist_ok=True)
    temp = DATA / (pid + '.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(DATA / (pid + '.json'))

def decode(value):
    try:
        return base64.b64decode(value, validate=True).decode('utf-8')
    except (ValueError, UnicodeError, TypeError):
        return str(value or '')

def epoch(event, prefix):
    try:
        return int(event[prefix + '_timestamp'])
    except (ValueError, TypeError, KeyError):
        return None  # Do not guess a provider's timezone from naive timestamps.

def refresh_epg(pid, offset=0, limit=20, search=""):
    """Optional name filter keeps targeted discovery bounded."""
    cached = read_cache(pid)
    if not cached:
        raise ProviderError('catalog_required')
    adapter = ADAPTERS[PROVIDERS[pid]['type']](PROVIDERS[pid])
    adapter.account()
    channels = [c for c in cached['channels'] if c['sports']]
    total_sports = len(channels)
    channels = [c for c in channels if search.casefold() in c['name'].casefold()]
    selected = channels[offset:offset + limit]
    if not selected:
        return {'checked_this_batch': 0, 'next_offset': None}
    fresh, successful = [], set()
    for channel in selected:
        try:
            response = adapter.request(adapter.host, 'get_short_epg', stream_id=channel['stream_id'], limit=20)
            rows = response.get('epg_listings')
            if not isinstance(rows, list):
                continue
            successful.add(channel['stream_id'])
            for event in rows:
                start, end = epoch(event, 'start'), epoch(event, 'stop') or epoch(event, 'end')
                if start is None or end is None or end <= start:
                    continue
                fresh.append({'stream_id': channel['stream_id'], 'title': decode(event.get('title')),
                              'start': start, 'end': end, 'fetched_at': int(time.time())})
        except (ProviderError, AttributeError):
            pass
        time.sleep(0.15)
    cached['epg'] = [e for e in cached['epg'] if e['stream_id'] not in successful] + fresh
    checked = set(cached.get('epg_checked_ids', [])) | successful
    cached['epg_checked_ids'] = sorted(checked)
    cached['epg_capabilities']['short_epg'] = 'supported' if checked else 'unavailable_or_failed'
    cached['epg_coverage'] = {'checked': len(checked), 'total_sports': total_sports}
    cached['epg_updated_at'] = int(time.time())
    save_cache(pid, cached)
    return {'checked_this_batch': len(successful), 'requested': len(selected),
            'events': len(fresh), 'coverage': cached['epg_coverage'],
            'next_offset': offset + limit if offset + limit < len(channels) else None}

def summary(cached):
    return {**{k: cached[k] for k in ('provider_id', 'updated_at', 'account', 'epg_capabilities', 'epg_coverage')},
            'categories': len(cached['categories']), 'live_channels': len(cached['channels']),
            'sports_channels': sum(c['sports'] for c in cached['channels'])}

def load_fixtures():
    try:
        path = DATA / 'fixtures.json'
        return json.loads((path if path.exists() else ROOT / 'fixtures.json').read_text())
    except (OSError, ValueError):
        return {'fixtures': [], 'checked_at': 0, 'max_age_seconds': 0}


# A match that goes to extra time/penalties, or just runs long, shouldn't stop being "current"
# the instant this fixed window elapses -- the same problem guide.GUIDE_GRACE_SECONDS fixes for
# the sports guide, here applied to Barcelona's own schedule. Only ESPN's own live-score status
# actually still saying 'in progress' extends it past the window; once ESPN says the game is
# over (or was never matched at all), the normal 3-hour window is the real cutoff.
FIXTURE_GRACE_SECONDS = 4 * 3600

def _fixture_still_airing(f, now):
    if now < f['kickoff'] + 10800:
        return True
    if now < f['kickoff'] + 10800 + FIXTURE_GRACE_SECONDS:
        live = livescores.for_fixture(sys.modules[__name__], f, 'Barcelona')
        return bool(live and live['verified_live'])
    return False

def resolve(now=None, mode='current', fixture_id=None):
    now = int(time.time()) if now is None else now
    schedule = load_fixtures()
    result = {'team_id': 'fc-barcelona', 'as_of': now, 'status': 'fixture_data_required',
              'fixture': None, 'feeds': [], 'coverage': {}, 'playback_enabled': False,
              'game_verified': False, 'live_status_verified': False,
              'fixture_source': schedule.get('source'),
              'fixture_checked_at': schedule['checked_at'],
              'fixture_refresh_mode': schedule.get('refresh_mode')}
    if now - schedule['checked_at'] > schedule['max_age_seconds']:
        return result
    # A three-hour scheduled window is not live-score or match-status evidence.
    fixtures = sorted(schedule['fixtures'], key=lambda f: f['kickoff'])
    candidates = [f for f in fixtures if
                  (f['kickoff'] > now if mode == 'next' else f['kickoff'] <= now and _fixture_still_airing(f, now))]
    if fixture_id is not None:
        candidates = [f for f in fixtures if f['id'] == fixture_id and _fixture_still_airing(f, now)]
    if not candidates:
        result['status'] = 'no_current_scheduled_fixture' if mode == 'current' else 'no_next_fixture_in_snapshot'
        upcoming = [f for f in fixtures if f['kickoff'] > now]
        result['next_fixture'] = upcoming[0] if upcoming else None
        return result
    fixture = candidates[0]
    live = livescores.for_fixture(sys.modules[__name__], fixture, 'Barcelona')
    result.update(fixture=fixture, game_verified=True, status='no_matching_feed_in_cache',
                   live=live, live_status_verified=bool(live and live['verified_live']))
    day = fixture['kickoff_iso'][:10]
    opponent = re.compile(r'\b' + re.escape(fixture['opponent']) + r'\b', re.I)
    exclude = EXCLUDE_PATTERN
    for pid in PROVIDERS:
        cached = read_cache(pid)
        if not cached:
            result['coverage'][pid] = {'status': 'catalog_required'}
            continue
        result['coverage'][pid] = {**cached['epg_coverage'],
            'catalog_age_seconds': now - cached['updated_at'], 'epg_updated_at': cached.get('epg_updated_at')}
        if now - cached['updated_at'] > 21600:
            continue
        events = {}
        for event in cached['epg']:
            events.setdefault(event['stream_id'], []).append(event)
        for channel in cached['channels']:
            text = channel['name']
            score, evidence = 0, None
            if BARCA.search(text) and opponent.search(text) and not exclude.search(text):
                if day in text:
                    score, evidence = 70, 'event_channel_teams_and_date'
                else:
                    for raw_date in re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text):
                        try:
                            difference = abs((datetime.fromisoformat(raw_date) - datetime.fromisoformat(day)).days)
                        except ValueError:
                            continue
                        if difference == 1:
                            score, evidence = 40, 'teams_adjacent_date_timezone_unverified'
            for event in events.get(channel['stream_id'], []):
                title = event['title']
                if (now - event.get('fetched_at', 0) <= 21600 and
                    BARCA.search(title) and opponent.search(title) and not exclude.search(title) and
                    event['start'] <= fixture['kickoff'] < event['end']):
                    score, evidence = 100, 'epg_teams_and_kickoff'
            if score:
                # No provider event title/name: those fields can reveal scores.
                result['feeds'].append({'provider_id': pid, 'stream_id': channel['stream_id'],
                    'display_name': channel['category'] + ' #' + channel['stream_id'],
                    'rank': score, 'evidence': evidence, 'availability': 'not_probed',
                    'provider_time_verified': score == 100,
                    'requires_date_verification': score == 40,
                    'audio_language': audio_language(fixture, channel, cached['updated_at'], now)})
    result['feeds'].sort(key=lambda f: (-f['rank'], f['provider_id'], f['stream_id']))
    if result['feeds']:
        result['status'] = 'candidate_feeds'
    result['note'] = 'Fixture verified from schedule snapshot; feed names are provider claims. Ranking reflects metadata evidence, not measured stream quality. No playback opened.'
    return result


SETTINGS = {}
SETTINGS_BODY_LIMIT = 262144

def legacy_providers():
    """Transitional import of repository provider definitions into a NEW settings store.

    SGSPORTS_LEGACY_IMPORT=auto (default) imports only providers whose credential file already exists, so an
    existing install keeps working while a clean install starts with no providers; 'off' never imports.
    """
    if os.environ.get('SGSPORTS_LEGACY_IMPORT', 'auto') == 'off':
        return []
    return [p for p in PROVIDERS.values() if (SECRETS / (p['id'] + '.json')).exists()]

def parse_settings_body(headers, rfile):
    """Bounded JSON body for a settings mutation; raises ValueError before reading on bad framing."""
    if (headers.get('Content-Type') or '').split(';')[0].strip().lower() != 'application/json':
        raise ValueError('unsupported_media_type')
    length = (headers.get('Content-Length') or '').strip()
    if not re.fullmatch(r'[0-9]{1,9}', length):
        raise ValueError('invalid_content_length')
    size = int(length)
    if size == 0:
        raise ValueError('invalid_content_length')
    if size > SETTINGS_BODY_LIMIT:
        raise ValueError('body_too_large')
    raw = rfile.read(size)
    if len(raw) != size:
        raise ValueError('short_body')
    return raw

def settings_store():
    """The persistent settings authority for this data dir (created and migrated on first use)."""
    path = str(DATA / 'settings.sqlite')
    with LOCK_SETTINGS:
        if path not in SETTINGS:
            SETTINGS[path] = settings.SettingsStore(path, providers=legacy_providers(), paired_devices=read_cache('tv-devices') or {})
        return SETTINGS[path]

ARTWORK = {}


def artwork_service():
    """The team-artwork service for this data dir; configuration comes from the versioned settings document."""
    path = str(DATA / 'artwork')
    with LOCK_SETTINGS:
        if path not in ARTWORK:
            overrides = {t['team_id']: str(t['espn_id']) for t in livescores.FAVORITE_TEAMS if t.get('espn_id')}
            ARTWORK[path] = artwork.ArtworkService(path, lambda: settings_store().artwork_config(), overrides=overrides)
        return ARTWORK[path]


def artwork_route(mutation, sub):
    """Artwork administration (administrator scope). Configuration itself is a normal settings write (integration kind 'artwork')."""
    service = artwork_service()
    if not mutation and sub == []:
        return 200, service.status()
    if mutation and sub == ['sync']:
        return (202, service.status()) if service.sync() else (409, {'error': 'sync_in_progress'})
    if mutation and sub == ['clear']:
        return 200, dict(service.clear(), status=service.status())
    return 404, {'error': 'not_found'}


def settings_route(mutation, sub, raw=b''):
    """Settings API (administrator scope). Returns (status, payload)."""
    try:
        store = settings_store()
        store.sync_devices(read_cache('tv-devices') or {})
        if not mutation and sub == []:
            return 200, store.read()
        if not mutation and sub == ['client']:
            return 200, store.client_view()
        if not mutation and sub == ['export']:
            return 200, store.export()
        if not mutation and sub == ['budgets']:
            return 200, store.budgets()
        if not mutation and sub == ['setup-status']:
            return 200, store.setup_status()
        if sub[:1] in (['dispatcharr'], ['switch']):
            return dispatcharr_route(store, mutation, sub, raw)
        if mutation and len(sub) == 3 and sub[0] == 'devices' and sub[2] == 'revoke':
            return 200, revoke_device(store, sub[1])
        if mutation and sub == ['restore']:
            if not 0 < len(raw) <= SETTINGS_BODY_LIMIT:
                return 400, {'error': 'invalid_request'}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return 400, {'error': 'invalid_request'}
            return 200, store.restore(payload.get('revision'), payload.get('export'), payload.get('secrets', {}))
        if mutation and sub == []:
            if not 0 < len(raw) <= SETTINGS_BODY_LIMIT:
                return 400, {'error': 'invalid_request'}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return 400, {'error': 'invalid_request'}
            return 200, store.update(payload.get('revision'), payload.get('ops'))
        return 404, {'error': 'not_found'}
    except settings.Conflict as conflict:
        return 409, {'error': 'revision_conflict', 'revision': conflict.revision}
    except settings.SettingsError as error:
        return 422, {'error': 'invalid_settings', 'details': error.errors}
    except settings.SchemaTooNew:
        return 503, {'error': 'settings_schema_unsupported'}
    except ValueError:
        return 400, {'error': 'invalid_request'}

def ops_route(mutation, sub, headers, rfile):
    """Operations API (administrator scope): sanitised diagnostics, metrics, storage and backup verification. Returns (status, payload)."""
    if not mutation and sub in (['diagnostics'], ['metrics'], ['storage']):
        dvr_store = tv.dvr_store() if tv.dvr_enabled() else None
        dvr_root = DATA / 'dvr' if dvr_store else None
        if sub == ['metrics']:
            return 200, ops.sanitize({'release_channel': ops.release_channel(), 'metrics': METRICS.snapshot(), 'rate_limits': LIMITER.snapshot()})
        if sub == ['storage']:
            return 200, ops.sanitize(ops.storage_report(DATA, dvr_root))
        return 200, ops.diagnostics(METRICS, LIMITER, DATA, dvr_root, dvr_store, settings_store())
    if not mutation and sub == ['backup']:
        try:
            return 200, ops.make_backup(settings_store())
        except ops.BackupError:
            return 500, {'error': 'backup_refused'}
    if mutation and sub == ['backup', 'verify']:
        try:
            bundle = json.loads(parse_settings_body(headers, rfile))
        except ValueError as error:
            return {'unsupported_media_type': 415, 'body_too_large': 413}.get(str(error), 400), {'error': str(error)}
        problems = ops.verify_backup(bundle)
        return (200 if not problems else 422), {'ok': not problems, 'problems': problems}
    return 404, {'error': 'not_found'}

DISPATCHARR_TRANSPORT = dispatcharr.urllib_transport

def active_account_ids():
    """Accounts with a managed session right now (derived from the lease authority; legacy provider id -> acct-<slug>)."""
    holders = tv.leases().holders()
    return {'acct-' + re.sub(r'[^a-z0-9_-]', '-', str(h['provider']).lower()).strip('-_')[:40] for h in holders}

def provider_for_account(account_id):
    return next((pid for pid in PROVIDERS if delivery.account_for_provider(pid) == account_id), None)

def admit_switch(affected, enabled_routes):
    """Admit a route switch before anything is written: the target Dispatcharr integration must be healthy and pinned (a read-only
    management check, no media is opened), and every affected account must be idle and is held for the commit so no managed consumer
    can slip in between the check and the write. Refusal raises and leaves the old route and settings untouched; nothing falls back."""
    blockers = []
    store = settings_store()
    view = store.read()
    resolver = tv.resolver()
    for route in view['routes']:
        if route['id'] in enabled_routes and route.get('kind') == 'dispatcharr':
            try:
                resolver._healthy(view, route.get('integration_id'))
            except delivery.Refused as refusal:
                blockers.append({'code': 'target_integration_unavailable', 'route_id': route['id'], 'detail': refusal.code})
    auth, held = tv.leases(), []
    for account in affected:
        provider = provider_for_account(account)
        if provider is None or blockers:
            continue   # an account with no legacy provider has no managed consumer to protect
        grant = lease.LeaseAuthority.acquire(auth, provider, 'switch:' + secrets.token_hex(8), 'switch', ttl=60, priority=10 ** 5)
        if grant is None:
            blockers.append({'code': 'target_account_unavailable', 'account_id': account})
            break
        held.append(grant)
    if blockers:
        for grant in held:
            auth.release(grant)
        raise settings.SettingsError([{'path': 'plan', 'error': 'switch_not_ready', 'blockers': blockers}])
    return lambda: [auth.release(grant) for grant in held]

def dispatcharr_route(store, mutation, sub, raw):
    """Optional-Dispatcharr administration (read-only upstream contract + staged route switches). Returns (status, payload)."""
    body = {}
    if mutation:
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict) or any(body.get(k) is not None and not isinstance(body.get(k), str) for k in ('integration_id', 'source_id', 'guide_source_id', 'plan_id', 'activate_route_id')):
            return 400, {'error': 'invalid_request'}
    now = int(time.time())
    if sub == ['dispatcharr'] and not mutation:
        return 200, store.dispatcharr_status(now)
    if sub == ['switch'] and not mutation:
        return 200, {'switches': store.switches()}
    if not mutation:
        return 404, {'error': 'not_found'}
    if sub == ['dispatcharr', 'probe']:
        return 200, dispatcharr.probe_integration(store, body.get('integration_id'), DISPATCHARR_TRANSPORT, now)
    if sub == ['dispatcharr', 'assess']:
        return 200, dispatcharr.assess_source(store, body.get('source_id'), body.get('guide_source_id'), DISPATCHARR_TRANSPORT, now)
    if sub == ['dispatcharr', 'check-guide']:
        return 200, dispatcharr.check_guide(store, body.get('guide_source_id'), DISPATCHARR_TRANSPORT)
    if sub == ['dispatcharr', 'disconnect']:
        return 200, store.disconnect_integration(body.get('revision'), body.get('integration_id'))
    if sub == ['dispatcharr', 'channels']:
        creds, _ = store.integration_credentials(body.get('integration_id'))
        kind, page = body.get('kind', 'channels'), body.get('page', 1)
        if kind not in ('channels', 'streams') or not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 10000 or not (creds['base_url'] and creds['api_key']):
            raise settings.SettingsError([{'path': 'request', 'error': 'invalid_channel_read'}])
        try:
            return 200, dispatcharr.read_page(creds, DISPATCHARR_TRANSPORT, kind, page)
        except dispatcharr.TransportError as error:
            return 200, {'status': 'unavailable', 'reason': error.code, 'items': []}
    if sub == ['switch', 'preview']:
        return 200, store.preview_switch(body.get('activate_route_id'), active_account_ids())
    if sub == ['switch', 'commit']:
        return 200, store.commit_switch(body.get('plan_id'), body.get('revision'), active_account_ids(), admit=admit_switch)
    if sub == ['switch', 'rollback']:
        return 200, store.rollback_switch(body.get('plan_id'), active_account_ids(), admit=admit_switch)
    if sub == ['switch', 'cancel']:
        return 200, {'switches': store.cancel_switch(body.get('plan_id'))}
    return 404, {'error': 'not_found'}

def revoke_device(store, device_id):
    """Administrator revocation: remove the pairing record (the authority) and stop that TV's sessions."""
    for key in list(read_cache('tv-devices') or {}):
        if 'dev-' + key[:16] == device_id:
            tv.unpair(key)
            store.sync_devices(read_cache('tv-devices') or {})
            return store.read()
    raise settings.SettingsError([{'path': 'devices.' + str(device_id)[:40], 'error': 'not_paired'}])

def tv_settings_route(mutation, device_key, raw=b''):
    """Paired-TV settings (least privilege): the TV's own profile and allowed preferences only. Returns (status, payload)."""
    try:
        store = settings_store()
        store.sync_devices(read_cache('tv-devices') or {})
        device_id = 'dev-' + device_key[:16]
        if not mutation:
            return 200, store.tv_settings(device_id)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 400, {'error': 'invalid_request'}
        extra = [k for k in payload if k not in ('revision', 'preferences')]
        if extra:
            return 422, {'error': 'invalid_settings', 'details': [{'path': str(k)[:40], 'error': 'unknown_field'} for k in extra]}
        return 200, store.update_tv_settings(device_id, payload.get('revision'), payload.get('preferences'))
    except settings.Conflict as conflict:
        try:
            current = store.tv_settings(device_id)
        except settings.Forbidden:
            current = None
        return 409, {'error': 'revision_conflict', 'revision': conflict.revision, 'current': current}
    except settings.Forbidden as forbidden:
        return 403, {'error': 'forbidden', 'reason': forbidden.reason}
    except settings.SettingsError as error:
        return 422, {'error': 'invalid_settings', 'details': error.errors}
    except settings.SchemaTooNew:
        return 503, {'error': 'settings_schema_unsupported'}
    except ValueError:
        return 400, {'error': 'invalid_request'}

def favorites_status(now=None):
    """Per-favorite next kickoff and live status only -- no feed candidates, no device
    pairing required. For lightweight external pollers (e.g. a Home Assistant sensor)
    that just need to know who plays when, not how to watch it.
    """
    now = int(time.time()) if now is None else now
    teams = []
    barca = resolve(now)
    if not barca.get('fixture'):
        barca = resolve(now, mode='next')
    if barca.get('fixture'):
        f = barca['fixture']
        teams.append({'team_id': livescores.FAVORITE_TEAMS[0]['team_id'], 'team_index': 0, 'team_name': 'Barcelona', 'opponent': f['opponent'],
                      'home': f['home'], 'kickoff': f['kickoff'], 'fixture_id': f['id'],
                      'verified_live': bool(barca.get('live_status_verified'))})
    cache = read_cache('next-games') or {}
    if now - cache.get('updated_at', 0) <= 7200:
        for game in cache.get('games', []):
            if game['team_index'] == 0:
                continue  # Barcelona already added above, from its own precise schedule.
            live = livescores.for_fixture(sys.modules[__name__], game, game['team_name'])
            teams.append({'team_id': game.get('team_id'), 'team_index': game['team_index'], 'team_name': game['team_name'],
                          'opponent': game['opponent'], 'home': game['home'], 'kickoff': game['kickoff'],
                          'fixture_id': game['id'], 'verified_live': bool(live and live['verified_live'])})
    return {'as_of': now, 'teams': sorted(teams, key=lambda t: t['kickoff'])}


def resolve_next_game(fixture_id, now=None):
    """Generalized version of resolve() for the other six favorites' next-game cards.

    Only matches by EPG event (team+opponent text and the event's own start/end window
    covering kickoff) — no channel-name/date heuristic, since that pattern was tuned to
    Barcelona's specific provider channel naming. That means candidates only appear once
    the guide actually carries the game, which happens automatically as kickoff nears
    (the guide's own rolling ~24h window), not on a fixed schedule of its own.
    """
    now = int(time.time()) if now is None else now
    cache = read_cache('next-games') or {}
    result = {'as_of': now, 'status': 'fixture_data_required', 'fixture': None, 'feeds': [],
              'game_verified': False, 'live_status_verified': False}
    game = next((g for g in cache.get('games', []) if g['id'] == fixture_id), None)
    if not game or now - cache.get('updated_at', 0) > 7200:
        return result
    live = livescores.for_fixture(sys.modules[__name__], game, game['team_name'])
    result.update(fixture=game, game_verified=True, status='no_matching_feed_in_cache',
                   live=live, live_status_verified=bool(live and live['verified_live']))
    published=guide.for_fixture(sys.modules[__name__],game,now)
    if published:
        for feed in published.get('feeds',[]):
            result['feeds'].append({**feed,'rank':100,'evidence':'published_guide_event',
                'availability':'scheduled_not_probed','provider_time_verified':True,
                'requires_date_verification':False})
    team = re.compile(r'\b' + re.escape(game['team_name']) + r'\b', re.I)
    opponent = re.compile(r'\b' + re.escape(game['opponent']) + r'\b', re.I)
    for pid in PROVIDERS:
        cached = read_cache(pid)
        if not cached or now - cached['updated_at'] > 21600:
            continue
        events = {}
        for event in cached['epg']:
            events.setdefault(event['stream_id'], []).append(event)
        for channel in cached['channels']:
            for event in events.get(channel['stream_id'], []):
                title = event['title']
                if (now - event.get('fetched_at', 0) <= 21600 and
                    team.search(title) and opponent.search(title) and not EXCLUDE_PATTERN.search(title) and
                    event['start'] <= game['kickoff'] < event['end']):
                    candidate={'provider_id': pid, 'stream_id': channel['stream_id'],
                        'display_name': channel['category'] + ' #' + channel['stream_id'],
                        'rank': 100, 'evidence': 'epg_teams_and_kickoff', 'availability': 'not_probed',
                        'provider_time_verified': True, 'requires_date_verification': False,
                        'audio_language': audio_language(game, channel, cached['updated_at'], now)}
                    if not any(f['provider_id']==pid and f['stream_id']==channel['stream_id'] for f in result['feeds']):
                        result['feeds'].append(candidate)
                    break
    result['feeds'].sort(key=lambda f: (f['provider_id'], f['stream_id']))
    if result['feeds']:
        result['status'] = 'candidate_feeds'
    result['note'] = 'Fixture and guide timing are metadata evidence. A listed feed is not confirmed to be carrying the game until playback verification runs. No playback opened.'
    return result



def language_key(fixture, pid, stream_id):
    return '|'.join((fixture['id'], str(fixture['kickoff']), pid, stream_id))


def language_binding(provider_id, stream_id):
    import verification
    return verification._binder()({'provider_id': provider_id, 'stream_id': stream_id})


def audio_language(fixture, channel, cached_at, now):
    records = read_cache('languages') or {}
    record = records.get(language_key(fixture, channel['provider_id'], channel['stream_id']))
    # Confirmed/reported language is evidence about the bytes of one managed source: another source cannot reuse it.
    if record and now < record['expires_at'] and record.get('source_binding') == language_binding(channel['provider_id'], channel['stream_id']):
        return {k: v for k, v in record.items() if k != 'source_binding'}
    # Explicit language labels only. A country's code is not its audio language.
    label = channel.get('category', '') + ' ' + channel.get('name', '')
    found = [code for code, name in LANGUAGES.items() if re.search(r'\b' + name + r'\b', label, re.I)]
    if re.search(r'(?:^|\|)EN\|', channel.get('category', ''), re.I):
        found.append('en')
    found = sorted(set(found))
    code = found[0] if len(found) == 1 else None
    return {'code': code, 'name': LANGUAGES.get(code, 'Unknown'),
            'evidence': 'provider_label' if code else 'unknown',
            'checked_at': None, 'observed_at': cached_at if code else None,
            'expires_at': None}


def save_language(payload, now=None):
    now = int(time.time()) if now is None else now
    pid, stream_id = payload.get('provider_id'), payload.get('stream_id')
    code = payload.get('code')
    if code is not None and code not in LANGUAGES:
        raise ValueError('invalid_language')
    game = next((g for g in (resolve(now), resolve(now, 'next'))
                 if (g.get('fixture') or {}).get('id') == payload.get('fixture_id')), None)
    if not game or not any(f['provider_id'] == pid and f['stream_id'] == stream_id for f in game['feeds']):
        raise ValueError('feed_no_longer_available')
    fixture = game['fixture']
    if code and (payload.get('heard_audio') is not True or not fixture['kickoff'] - 3600 <= now < fixture['kickoff'] + 10800):
        raise ValueError('confirm_only_after_listening_during_game_window')
    with LANGUAGE_LOCK:
        records = read_cache('languages') or {}
        records = {k: v for k, v in records.items() if v['expires_at'] > now}
        key = language_key(fixture, pid, stream_id)
        if code is None:
            records.pop(key, None)
        else:
            records[key] = {'code': code, 'name': LANGUAGES[code], 'evidence': 'user_confirmed',
                            'checked_at': now, 'observed_at': None,
                            'expires_at': min(now + 21600, fixture['kickoff'] + 10800),
                            'source_binding': language_binding(pid, stream_id)}
        save_cache('languages', records)
    return {'saved': True}

def scheduler_status():
    state = read_cache('scheduler') or {'jobs': {}}
    return {'enabled': os.environ.get('SG_AUTO_REFRESH') == '1', **state}


def run_due_jobs(now=None):
    now = int(time.time()) if now is None else now
    state = read_cache('scheduler') or {'jobs': {}}
    jobs = [('sports-guide', 300), ('fixtures', 21600), ('live-scores', 60), ('next-games', 3600), ('standings-top5', 3600), ('lvbp-schedule', 900)] + [(pid, 7200) for pid in PROVIDERS]
    if tv.dvr_engine() != 'off':
        jobs.append(('dvr', 60))
    for job, interval in jobs:
        previous = state['jobs'].get(job, {})
        if previous.get('next_run', 0) > now:
            continue
        if not LOCK.acquire(blocking=False):
            return
        try:
            entry = {**previous, 'started_at': int(time.time()), 'status': 'refreshing'}
            state['jobs'][job] = entry
            save_cache('scheduler', state)
            try:
                if job == 'sports-guide':
                    import guide
                    guide.refresh(sys.modules[__name__])
                elif job == 'fixtures':
                    result = fetch_schedule()
                    save_cache('fixtures', result)
                elif job == 'live-scores':
                    livescores.refresh(sys.modules[__name__])
                elif job == 'next-games':
                    livescores.refresh_next_games(sys.modules[__name__])
                elif job == 'standings-top5':
                    livescores.refresh_standings_top5(sys.modules[__name__])
                elif job == 'dvr':
                    tv.dvr_tick()
                elif job == 'lvbp-schedule':
                    # lvbp.refresh() owns its 6-hour cadence and failure backoff; this 15-minute
                    # tick only lets it retry sooner than the generic scheduler delay would.
                    import lvbp, lvbp_broadcast
                    api = sys.modules[__name__]
                    lvbp.refresh(api)
                    guide_cache = read_cache('sports-guide') or {}
                    save_cache('lvbp-coverage', lvbp_broadcast.coverage_report(api, programs=guide_cache.get('programs', [])))
                else:
                    refresh(job)
                    refresh_epg(job, limit=20, search='barcelona')
                entry.update(status='ok', last_success=int(time.time()), error=None)
                delay = interval
            except Exception:
                # Never persist exception text that might contain a credential URL.
                entry.update(status='failed', error='Refresh failed; previous data retained.')
                delay = min(interval, 3600)
            entry['next_run'] = int(time.time()) + delay
            save_cache('scheduler', state)
        finally:
            LOCK.release()


def scheduler_loop():
    while not STOP.is_set():
        try:
            run_due_jobs()
        except Exception:
            pass  # Existing cache stays usable; the persisted heartbeat will age out.
        STOP.wait(60)


def screen_data():
    current, upcoming = resolve(), resolve(mode='next')
    providers = []
    for pid, config in PROVIDERS.items():
        cached = read_cache(pid)
        providers.append({'id': pid, 'name': config['name'], 'max_connections': config['max_connections'],
            'status': cached['account'].get('status') if cached else 'Awaiting setup',
            'updated_at': cached['updated_at'] if cached else None,
            'sports_channels': sum(c['sports'] for c in cached['channels']) if cached else 0})
    return {'game': current if current.get('fixture') else upcoming,
            'scheduled_now': bool(current.get('fixture')), 'providers': providers,
            'refresh': scheduler_status(), 'language_options': LANGUAGES, 'screen_token': SCREEN_TOKEN}

def session_view(session, mode):
    if not session:
        return {'authenticated': False, 'mode': mode}
    return {'authenticated': True, 'mode': 'login', 'username': session['username'], 'must_change': session['must_change'], 'csrf': session['csrf']}


def auth_route(handler, path, mutation, client):
    """Administrator sign-in, sign-out, first-login password change and legacy-token migration. Returns True when handled."""
    if not path.startswith('/v1/auth/'):
        return False
    action = path[len('/v1/auth/'):]
    mode = adminauth.mode(DATA, SECRETS)
    secure = secure_request(handler.headers)
    session = admin_session(handler.headers)
    if action == 'session' and not mutation:
        handler.send(200, session_view(session, mode))
        return True
    if not mutation or action not in ('login', 'logout', 'credentials', 'migrate'):
        handler.send(404, {'error': 'not_found'})
        return True
    if handler.headers.get('Origin') and not screen_origin_ok(handler.headers):
        handler.send(403, {'error': 'csrf_rejected'})
        return True
    try:
        raw = parse_settings_body(handler.headers, handler.rfile)
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError('invalid_request')
    except (ValueError, TypeError):
        handler.send(400, {'error': 'invalid_request'})
        return True

    def granted(issued):
        handler._extra_headers = [('Set-Cookie', adminauth.set_cookie(issued['sid'], secure))]
        handler.send(200, session_view({'username': issued['username'], 'must_change': issued['must_change'], 'csrf': issued['csrf']}, 'login'))

    def refused(outcome, detail):
        if outcome == 'throttled':
            handler._extra_headers = [('Retry-After', str(detail))]
            handler.send(429, {'error': 'rate_limited', 'retry_after': detail})
        elif outcome == 'unavailable':
            handler.send(503, {'error': 'auth_state_invalid', 'detail': adminauth.INVALID_HELP})
        elif outcome == 'migrate_required':
            handler.send(409, {'error': 'migration_required'})
        elif outcome == 'error':
            handler.send(422, {'error': detail})
        else:
            handler.send(401, {'error': 'invalid_credentials'})

    if action == 'login':
        outcome, detail = adminauth.login(DATA, SECRETS, body.get('username'), body.get('password'), client)
    elif action == 'migrate':
        if mode == 'invalid':
            handler.send(503, {'error': 'auth_state_invalid', 'detail': adminauth.INVALID_HELP})
            return True
        if mode != 'migrate':
            handler.send(409, {'error': 'nothing_to_migrate'})
            return True
        outcome, detail = adminauth.migrate(DATA, SECRETS, body.get('token'), body.get('username'), body.get('password'), client)
    elif action == 'logout':
        if session and not adminauth.csrf_ok(session, handler.headers.get('X-SG-CSRF', '')):
            handler.send(403, {'error': 'csrf_rejected'})
            return True
        adminauth.logout(DATA, session)
        handler._extra_headers = [('Set-Cookie', adminauth.clear_cookie(secure))]
        handler.send(200, {'authenticated': False, 'mode': mode})
        return True
    else:
        if not session:
            handler.send(401, {'error': 'unauthorized'})
            return True
        if not adminauth.csrf_ok(session, handler.headers.get('X-SG-CSRF', '')):
            handler.send(403, {'error': 'csrf_rejected'})
            return True
        outcome, detail = adminauth.change_credentials(DATA, session, body.get('current_password'), body.get('new_password'), body.get('new_username'), client)
        if outcome == 'ok' and detail is None:
            handler.send(200, session_view(dict(admin_session(handler.headers)), mode))
            return True
    if outcome == 'ok':
        granted(detail)
    else:
        refused(outcome, detail)
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, data):
        body = json.dumps(data).encode()
        self._status = status
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for name, value in getattr(self, '_extra_headers', ()):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, mutation):
        started = time.monotonic()
        try:
            self.route(mutation)
        finally:
            status = getattr(self, '_status', 0)
            if status:
                METRICS.observe(urllib.parse.urlsplit(self.path).path, status, time.monotonic() - started)

    def route(self, mutation):
        path = urllib.parse.urlsplit(self.path)
        if tv.handle(self, path.path, mutation):
            return
        if not mutation and path.path in ('/setup', '/setup/'):
            # The administration console is the root address; /setup stays as a permanent-compatibility redirect for old bookmarks, docs and installed TVs.
            self.send_response(302)
            self.send_header('Location', '/')
            self.send_header('Content-Length', '0')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self._status = 302
            return
        if not mutation and path.path in ('/', '/setup.css', '/setup.js', '/prefs-logic.js'):
            # The root is a static administration shell with no data; every settings call it makes needs the administrator session.
            names = {'/': ('setup.html', 'text/html; charset=utf-8'),
                     '/setup.css': ('setup.css', 'text/css'), '/setup.js': ('setup.js', 'text/javascript'),
                     '/prefs-logic.js': ('prefs-logic.js', 'text/javascript')}
            filename, content_type = names[path.path]
            body = (ROOT / 'web' / filename).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            return self.wfile.write(body)
        if path.path == '/screen-data' and not mutation:
            return self.send(200, screen_data())
        if path.path == '/screen-language' and mutation:
            if not screen_origin_ok(self.headers) or not hmac.compare_digest(self.headers.get('X-SG-Screen-Token', ''), SCREEN_TOKEN):
                return self.send(403, {'error': 'Reload the screen and try again.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2048 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('invalid_request')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('invalid_request')
                return self.send(200, save_language(payload))
            except (ValueError, TypeError):
                return self.send(400, {'error': 'Confirm only after listening, from one hour before kickoff through the scheduled game. Reload if the feed has changed.'})
        if path.path == '/health' and not mutation:
            return self.send(200, {'status': 'ok', 'service': 'sgsports', 'metadata_only': True, 'release_channel': ops.release_channel()})
        if path.path == '/v1/tv/settings':
            try:
                key = tv.device(self.headers)
            except PermissionError:
                return self.send(401, {'error': 'Pair this TV from the SG Sports web screen.'})
            try:
                raw = parse_settings_body(self.headers, self.rfile) if mutation else b''
            except ValueError as error:
                return self.send({'unsupported_media_type': 415, 'body_too_large': 413}.get(str(error), 400), {'error': str(error)})
            return self.send(*tv_settings_route(mutation, key, raw))
        if path.path == '/v1/tv/catalog' and not mutation:
            try:
                tv.device(self.headers)
            except PermissionError:
                return self.send(401, {'error': 'Pair this TV from the SG Sports web screen.'})
            return self.send(200, catalog.view())
        if path.path == '/v1/settings/client' and not mutation:
            try:
                tv.device(self.headers)
                return self.send(*settings_route(False, ['client']))
            except PermissionError:
                pass
        client = (getattr(self, 'client_address', None) or (None,))[0]   # in-process callers (tests) have no peer and are not limited
        wait = LIMITER.blocked('auth_fail', client) if client else 0
        if wait:
            return self.send(429, {'error': 'rate_limited', 'retry_after': wait})
        if auth_route(self, path.path, mutation, client):
            return
        session = admin_session(self.headers)
        if not session:
            if client and self.headers.get('Cookie'):
                LIMITER.spend('auth_fail', client)
            return self.send(401, {'error': 'unauthorized'})
        if session['must_change']:
            return self.send(403, {'error': 'password_change_required'})
        if mutation and not (adminauth.csrf_ok(session, self.headers.get('X-SG-CSRF', '')) and (not self.headers.get('Origin') or screen_origin_ok(self.headers))):
            return self.send(403, {'error': 'csrf_rejected'})
        if mutation:
            wait = LIMITER.spend('admin_write', 'admin') if client else 0
            if wait:
                return self.send(429, {'error': 'rate_limited', 'retry_after': wait})
        parts = path.path.strip('/').split('/')
        query = urllib.parse.parse_qs(path.query)
        try:
            if parts[:2] == ['v1', 'artwork']:
                return self.send(*artwork_route(mutation, parts[2:]))
            if parts[:2] == ['v1', 'ops']:
                return self.send(*ops_route(mutation, parts[2:], self.headers, self.rfile))
            if parts == ['v1', 'catalog', 'favorites'] and not mutation:
                return self.send(200, catalog.view())
            if parts[:2] == ['v1', 'settings']:
                try:
                    raw = parse_settings_body(self.headers, self.rfile) if mutation else b''
                except ValueError as error:
                    return self.send({'unsupported_media_type': 415, 'body_too_large': 413}.get(str(error), 400), {'error': str(error)})
                return self.send(*settings_route(mutation, parts[2:], raw))
            if parts == ['v1', 'providers'] and not mutation:
                return self.send(200, [{'id': p['id'], 'name': p['name'], 'type': p['type'],
                    'max_connections': p['max_connections'], 'host_count': len(p['hosts']),
                    'credentials_configured': (SECRETS / (p['id'] + '.json')).exists(),
                    'inventory': summary(read_cache(p['id'])) if read_cache(p['id']) else None} for p in PROVIDERS.values()])
            if parts[:3] == ['v1', 'teams', 'fc-barcelona'] and len(parts) == 4 and parts[3] in ('current', 'next') and not mutation:
                return self.send(200, resolve(mode=parts[3]))
            if len(parts) != 4 or parts[:2] != ['v1', 'providers'] or parts[2] not in PROVIDERS:
                return self.send(404, {'error': 'not_found'})
            pid, action = parts[2:]
            if mutation and action in ('refresh', 'refresh-epg'):
                if not LOCK.acquire(blocking=False):
                    return self.send(409, {'error': 'refresh_in_progress'})
                try:
                    if action == 'refresh':
                        result = refresh(pid)
                    else:
                        offset = max(0, int(query.get('offset', ['0'])[0]))
                        limit = min(20, max(1, int(query.get('limit', ['20'])[0])))
                        result = refresh_epg(pid, offset, limit, query.get('q', [''])[0])
                    return self.send(200, result)
                finally:
                    LOCK.release()
            if not mutation and action in ('inventory', 'categories', 'channels'):
                cached = read_cache(pid)
                if not cached:
                    return self.send(409, {'error': 'catalog_required'})
                if action == 'inventory':
                    return self.send(200, summary(cached))
                rows = cached[action]
                if action == 'channels':
                    if query.get('sports') == ['true']:
                        rows = [r for r in rows if r['sports']]
                    search = query.get('q', [''])[0].casefold()
                    rows = [r for r in rows if search in r['name'].casefold()]
                offset = max(0, int(query.get('offset', ['0'])[0]))
                limit = min(500, max(1, int(query.get('limit', ['100'])[0])))
                return self.send(200, {'total': len(rows), 'items': rows[offset:offset + limit]})
            return self.send(404, {'error': 'not_found'})
        except ProviderError as e:
            return self.send(409, {'error': str(e)})
        except (ValueError, KeyError, TypeError):
            return self.send(400, {'error': 'invalid_request_or_provider_data'})
        except Exception:
            return self.send(500, {'error': 'internal_error'})

tv.configure(sys.modules[__name__])

if __name__ == '__main__':
    adminauth.prepare(DATA, SECRETS)
    if os.environ.get('SG_AUTO_REFRESH') == '1':
        threading.Thread(target=scheduler_loop, daemon=True, name='metadata-refresh').start()
    ThreadingHTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
