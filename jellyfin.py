"""Small Jellyfin HTTP adapter for the optional Phase 7 DVR.

Only supported public Jellyfin HTTP endpoints are used (the documented Live TV, System, Items and
Environment APIs); nothing here reads Jellyfin's database or assumes a shared filesystem. The base
URL and API key are backend-only: they come from the environment or a mode-restricted secret file,
travel only in the Authorization header, and never appear in URLs, logs, errors or responses.

Network access goes through an injectable `transport`, so the unit suite runs against an in-process
fake and the isolated integration harness (tests/jellyfin_e2e) runs against a real pinned server.
"""
import ipaddress
import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request

# Server line this adapter targets. Anything else reports `incompatible` rather than guessing.
SUPPORTED_VERSIONS = ('10.10',)
PINNED_TEST_IMAGE = 'jellyfin/jellyfin:10.10.7'
NAMESPACE = 'sgsports-dvr'

STATES = ('not_found', 'disabled', 'not_configured', 'unreachable', 'denied', 'incompatible', 'livetv_unavailable',
          'tuner_missing', 'guide_missing', 'mapping_missing', 'storage_misconfigured', 'ready')


class JellyfinError(Exception):
    """A classified adapter failure. `state` is one of STATES; the message never contains the key."""

    def __init__(self, state, detail=''):
        super().__init__(state + (': ' + detail if detail else ''))
        self.state, self.detail = state, detail


class Config:
    def __init__(self, enabled=False, base_url='', api_key='', export_url='', relay_key='', problem=None):
        self.enabled, self.base_url, self.api_key = enabled, base_url, api_key
        self.export_url, self.relay_key, self.problem = export_url, relay_key, problem

    def __repr__(self):
        return 'Config(enabled=%r, base_url=%r, api_key=<hidden>)' % (self.enabled, self.base_url)


def _read_secret(path):
    """Contents of a secret file, or raise JellyfinError if it is readable by group/other."""
    try:
        mode = os.stat(path).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise JellyfinError('not_configured', 'secret file must not be accessible by group or others (chmod 600)')
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return ''


def _private_host(host):
    """Plain HTTP is accepted only for loopback, private, link-local or single-label (container) hosts."""
    if host == 'localhost' or '.' not in host and ':' not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_private or address.is_link_local
    except ValueError:
        return False


def validate_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password \
            or parts.query or parts.fragment:
        raise JellyfinError('not_configured', 'base URL must be http(s)://host[:port][/path] with no credentials or query')
    if parts.scheme == 'http' and not _private_host(parts.hostname):
        raise JellyfinError('not_configured', 'remote Jellyfin servers require HTTPS')
    return url.rstrip('/')


def load_config(env=None, secrets_dir=None):
    """Read configuration. Never raises: a bad setup is a Config carrying `problem`."""
    env = os.environ if env is None else env
    secrets_dir = secrets_dir if secrets_dir is not None else env.get('SG_SECRETS', '/run/secrets')
    if env.get('SG_JELLYFIN_ENABLED') != '1':
        return Config(enabled=False)
    try:
        url = validate_url(env.get('SG_JELLYFIN_URL', ''))
        key = env.get('SG_JELLYFIN_API_KEY', '')
        if not key:
            key = _read_secret(env.get('SG_JELLYFIN_API_KEY_FILE') or os.path.join(secrets_dir, 'jellyfin_api_key'))
        if not key:
            raise JellyfinError('not_configured', 'no API key (SG_JELLYFIN_API_KEY or a mode-600 key file)')
        relay = env.get('SG_JELLYFIN_RELAY_KEY', '') or _read_secret(
            env.get('SG_JELLYFIN_RELAY_KEY_FILE') or os.path.join(secrets_dir, 'jellyfin_relay_key'))
        export = env.get('SG_JELLYFIN_EXPORT_URL', '').rstrip('/')
        if not relay or not export:
            raise JellyfinError('not_configured', 'relay key and SG_JELLYFIN_EXPORT_URL are required for managed ingest')
        return Config(True, url, key, export, relay)
    except JellyfinError as problem:
        return Config(True, problem=problem)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def urllib_transport(method, url, headers, body, timeout):
    """(status, body_bytes). Never follows redirects, so the key cannot be forwarded elsewhere."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


class Client:
    def __init__(self, config, transport=urllib_transport, timeout=15):
        self.config, self.transport, self.timeout = config, transport, timeout

    def request(self, method, path, query=None, payload=None, auth=True, ok=(200, 204)):
        """JSON response (or None for 204). Failures are classified; the key is only ever a header."""
        url = self.config.base_url + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        headers = {'Accept': 'application/json'}
        if auth:
            headers['Authorization'] = 'MediaBrowser Token="%s", Client="SG Sports", Device="sgsports", DeviceId="%s", Version="2"' % (
                self.config.api_key, NAMESPACE)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        try:
            status, raw = self.transport(method, url, headers, body, self.timeout)
        except Exception:
            raise JellyfinError('unreachable', 'Jellyfin did not answer') from None
        if status in (401, 403):
            raise JellyfinError('denied', 'Jellyfin rejected the API key (HTTP %d)' % status)
        if status >= 500:
            raise JellyfinError('unreachable', 'Jellyfin server error (HTTP %d)' % status)
        if status == 404 and 404 not in ok:
            raise JellyfinError('not_found', 'not found: %s' % path)
        if status not in ok:
            raise JellyfinError('incompatible', 'unexpected HTTP %d from %s %s' % (status, method, path))
        if status == 204 or not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise JellyfinError('incompatible', 'non-JSON response from %s' % path) from None

    # --- capability --------------------------------------------------------------------------
    def check(self):
        """Ordered capability check. Returns {'state', 'detail', 'version'}; reachable-and-authenticated is not 'ready'."""
        config = self.config
        if not config.enabled:
            return {'state': 'disabled', 'detail': 'DVR is turned off.', 'version': None}
        if config.problem:
            return {'state': config.problem.state, 'detail': config.problem.detail, 'version': None}
        version = None
        try:
            public = self.request('GET', '/System/Info/Public', auth=False)
            version = str((public or {}).get('Version') or '')
            if (public or {}).get('ProductName') != 'Jellyfin Server' or '.'.join(version.split('.')[:2]) not in SUPPORTED_VERSIONS:
                raise JellyfinError('incompatible', 'server %r is outside the supported versions %s' % (version, ', '.join(SUPPORTED_VERSIONS)))
            self.request('GET', '/System/Info')
            info = self.request('GET', '/LiveTv/Info', ok=(200,))
            if not (info or {}).get('IsEnabled'):
                raise JellyfinError('livetv_unavailable', 'Live TV is not enabled on this server')
            options = self.request('GET', '/System/Configuration/livetv', ok=(200,)) or {}
            hosts = options.get('TunerHosts') or []
            if not hosts:
                raise JellyfinError('tuner_missing', 'no M3U tuner host is configured')
            if not any(str(h.get('Url', '')).startswith(config.export_url) for h in hosts):
                raise JellyfinError('mapping_missing', 'no tuner host points at the SG Sports playlist export')
            if not options.get('ListingProviders'):
                raise JellyfinError('guide_missing', 'no XMLTV guide provider is configured')
            path = options.get('RecordingPath')
            if not path:
                raise JellyfinError('storage_misconfigured', 'no recording path is set in Live TV settings')
            try:
                self.request('POST', '/Environment/ValidatePath', payload={'Path': path, 'ValidateWritable': True, 'IsFile': False})
            except JellyfinError as error:
                if error.state == 'denied':
                    raise
                raise JellyfinError('storage_misconfigured', 'recording path is missing or not writable by Jellyfin') from None
            if not self.channels():
                return {'state': 'mapping_missing', 'detail': 'Jellyfin has no channels from the SG Sports playlist (refresh the guide)',
                        'version': version, 'awaiting_channels': True}
            return {'state': 'ready', 'detail': 'Jellyfin DVR is ready.', 'version': version}
        except JellyfinError as error:
            return {'state': error.state, 'detail': error.detail, 'version': version}

    # --- channels / programs --------------------------------------------------------------------
    def channels(self):
        data = self.request('GET', '/LiveTv/Channels', {'EnableFavoriteSorting': 'false'}, ok=(200,)) or {}
        return list(data.get('Items') or [])

    def programs(self, channel_ids, start, end):
        data = self.request('GET', '/LiveTv/Programs', {
            'ChannelIds': ','.join(channel_ids), 'MinStartDate': iso(start - 86400), 'MaxStartDate': iso(end),
            'EnableTotalRecordCount': 'false'}, ok=(200,)) or {}
        return list(data.get('Items') or [])

    def set_guide_path(self, url):
        """Point our XMLTV listing provider at url. Jellyfin caches a downloaded guide per path for a day, so a changed
        guide needs a new path. Returns True if a provider changed; touches only providers under our export URL."""
        options = self.request('GET', '/System/Configuration/livetv', ok=(200,)) or {}
        base = self.config.export_url.rstrip('/') + '/'
        for provider in options.get('ListingProviders') or []:
            current = str(provider.get('Path') or '')
            if provider.get('Type') == 'xmltv' and current.startswith(base) and current != url:
                # Jellyfin's guide download cache is per listing-provider Id, so a changed guide needs a replaced provider.
                if provider.get('Id'):
                    self.request('DELETE', '/LiveTv/ListingProviders', {'id': provider['Id']})
                fresh = {k: v for k, v in provider.items() if k != 'Id'}
                self.request('POST', '/LiveTv/ListingProviders', {'validateListings': 'false'}, payload={**fresh, 'Path': url})
                return True
        return False

    def refresh_guide(self):
        """Ask Jellyfin to re-read the XMLTV guide now (its own scheduled task)."""
        tasks = self.request('GET', '/ScheduledTasks', {'isHidden': 'false'}, ok=(200,)) or []
        for task in tasks:
            if task.get('Key') == 'RefreshGuide':
                self.request('POST', '/ScheduledTasks/Running/' + urllib.parse.quote(task['Id'], safe=''))
                return True
        return False

    # --- timers ---------------------------------------------------------------------------------
    def timers(self):
        return list((self.request('GET', '/LiveTv/Timers', ok=(200,)) or {}).get('Items') or [])

    def create_timer(self, channel_id, program_id, pre_seconds, post_seconds):
        defaults = self.request('GET', '/LiveTv/Timers/Defaults', {'programId': program_id}, ok=(200,)) or {}
        timer = {**defaults, 'ChannelId': channel_id, 'ProgramId': program_id, 'PrePaddingSeconds': int(pre_seconds),
                 'PostPaddingSeconds': int(post_seconds), 'IsPrePaddingRequired': False, 'IsPostPaddingRequired': False}
        for key in ('Id', 'Type'):
            timer.pop(key, None)
        self.request('POST', '/LiveTv/Timers', payload=timer)

    def update_timer(self, timer_id, changes):
        self.request('POST', '/LiveTv/Timers/' + urllib.parse.quote(timer_id, safe=''), payload={**changes, 'Id': timer_id})

    def cancel_timer(self, timer_id):
        self.request('DELETE', '/LiveTv/Timers/' + urllib.parse.quote(timer_id, safe=''))

    # --- recordings / playback -----------------------------------------------------------------
    # A completed recording is a library item (Type Movie, with a Path) that only appears after a library scan, and the
    # listing is per user, so the owning admin user is discovered once from the API key.
    ITEM_FIELDS = 'Path,MediaSources'

    def user_id(self):
        if getattr(self, '_user_id', None) is None:
            self._user_id = ''
            try:
                users = self.request('GET', '/Users', ok=(200,)) or []
                admin = next((u for u in users if (u.get('Policy') or {}).get('IsAdministrator')), users[0] if users else None)
                self._user_id = (admin or {}).get('Id') or ''
            except JellyfinError:
                pass
        return self._user_id

    def recordings(self):
        query = {'EnableTotalRecordCount': 'false', 'Fields': self.ITEM_FIELDS}
        if self.user_id():
            query['userId'] = self.user_id()
        return list((self.request('GET', '/LiveTv/Recordings', query, ok=(200,)) or {}).get('Items') or [])

    def item(self, item_id):
        query = {'Ids': item_id, 'Fields': self.ITEM_FIELDS}
        if self.user_id():
            query['userId'] = self.user_id()
        items = (self.request('GET', '/Items', query, ok=(200,)) or {}).get('Items') or []
        if not items:
            raise JellyfinError('not_found', 'recording item is gone')
        return items[0]

    def delete_recording(self, item_id):
        self.request('DELETE', '/Items/' + urllib.parse.quote(item_id, safe=''))

    def scan_library(self):
        """Ask Jellyfin to index finished recording files (it does not do so on its own for hours)."""
        self.request('POST', '/Library/Refresh')

    def stream_request(self, item_id, range_header=None):
        """(method, url, headers) for a backend-side media fetch. The key rides in a header, never the URL."""
        headers = {'Authorization': 'MediaBrowser Token="%s"' % self.config.api_key}
        if range_header:
            headers['Range'] = range_header
        url = '%s/Videos/%s/stream?static=true' % (self.config.base_url, urllib.parse.quote(item_id, safe=''))
        return 'GET', url, headers


def iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.0000000Z')


def parse_iso(value):
    import datetime
    text = str(value or '').strip().rstrip('Z')
    if '.' in text:
        head, frac = text.split('.', 1)
        text = head + '.' + frac[:6]
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return int(datetime.datetime.strptime(text, fmt).replace(tzinfo=datetime.timezone.utc).timestamp())
        except ValueError:
            continue
    return 0
