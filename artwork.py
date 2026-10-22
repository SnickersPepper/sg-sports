"""Backend-owned team and league artwork service (SGH-302).

The public (neutral) release carries no third-party logos. Each installation resolves them at runtime, on the operator's own
host, from a source the operator chooses, and serves the cached result to paired TVs:

  espn   ESPN public sports metadata (default; the operator can switch it off). Runtime retrieval only; nothing is redistributed.
  url    A base address the operator controls (`<base>/<key>.png|webp|jpg`). The address is a stored secret and never echoed.
  pack   A directory of the operator's own images in the data volume (`pack/pack.json` plus files).
  off    No artwork is fetched or served; TVs draw team-colour monograms.

Artwork is addressed by a canonical key, never a URL: `espn_<sport>_<league>_<teamid>`, `league_<sport>_<league>` or a backend
catalog ID (`team.…`, `league.…`). Fetched images are validated (magic bytes, size, dimensions; SVG and anything else is refused),
stored with their source, retrieval time and validators in the persistent data volume, and served with the sniffed content type.
A failure never discards the last good copy. All network access goes through one bounded, allow-listed fetch path.
"""
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import struct
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import catalog

SCHEMA_VERSION = 1
MODES = ('espn', 'url', 'pack', 'off')
DEFAULT_MODE = 'off'
EXTENSIONS = ('.png', '.webp', '.jpg')
MAX_IMAGE_BYTES = 1_000_000
MAX_JSON_BYTES = 6_000_000
MAX_DIMENSION = 2048
CACHE_MAX_BYTES = 64 * 1024 * 1024
CACHE_MAX_ENTRIES = 3000
TIMEOUT = 8
MAX_REDIRECTS = 3
CONCURRENCY = 4
REVALIDATE_AFTER = 7 * 86400
FAIL_BACKOFF = 900
DIRECTORY_TTL = 7 * 86400
PACK_MANIFEST_MAX = 256_000
STATUS_ENTRY_LIMIT = 200

KEY_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{2,110}$')
ESPN_TEAM_RE = re.compile(r'^espn_([a-z0-9_-]+)_(\d{1,9})$')
ESPN_LEAGUE_RE = re.compile(r'^league_([a-z0-9_-]+)$')
PACK_FILE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$')

ESPN_API_HOSTS = ('site.api.espn.com', 'site.web.api.espn.com')
ESPN_API = 'https://site.api.espn.com/apis/site/v2/sports/'
ESPN_PATHS = ('football/nfl', 'baseball/mlb', 'basketball/nba', 'football/college-football', 'baseball/college-softball', 'mma/ufc',
              'soccer/eng.1', 'soccer/esp.1', 'soccer/ita.1', 'soccer/ger.1', 'soccer/fra.1', 'soccer/usa.1', 'soccer/arg.1', 'soccer/ned.1',
              'soccer/por.1', 'soccer/ven.1', 'soccer/conmebol.libertadores', 'soccer/uefa.champions', 'soccer/uefa.europa',
              'soccer/uefa.europa.conf', 'golf/pga', 'golf/lpga', 'golf/eur', 'golf/liv')
ESPN_BY_SLUG = {p.replace('/', '_').replace('.', '_'): p for p in ESPN_PATHS}
# (catalog sport, catalog league name) -> ESPN path. Entries the catalog has no honest ESPN equivalent for are simply absent.
CATALOG_ESPN = {
    ('Football', 'NFL'): 'football/nfl', ('Baseball', 'MLB'): 'baseball/mlb', ('Football', 'NCAA'): 'football/college-football',
    ('Softball', 'NCAA'): 'baseball/college-softball', ('UFC', 'UFC'): 'mma/ufc', ('Soccer', 'La Liga'): 'soccer/esp.1',
    ('Soccer', 'Premier League'): 'soccer/eng.1', ('Soccer', 'Serie A'): 'soccer/ita.1', ('Soccer', 'Bundesliga'): 'soccer/ger.1',
    ('Soccer', 'Ligue 1'): 'soccer/fra.1', ('Soccer', 'MLS'): 'soccer/usa.1', ('Soccer', 'Argentina Primera División'): 'soccer/arg.1',
    ('Soccer', 'Copa Libertadores'): 'soccer/conmebol.libertadores', ('Soccer', 'UEFA Champions League'): 'soccer/uefa.champions',
    ('Golf', 'PGA Tour'): 'golf/pga', ('Golf', 'LPGA'): 'golf/lpga', ('Golf', 'DP World Tour'): 'golf/eur', ('Golf', 'LIV Golf'): 'golf/liv',
}

PROVIDERS = {
    'espn': {
        'name': 'ESPN public sports metadata',
        'terms_url': 'https://disneytermsofuse.com/',
        'attribution': 'Team and league names and logos are trademarks of their respective owners.',
        'license': 'No redistribution licence is granted. Images are retrieved at runtime from ESPN public metadata endpoints for display in this '
                   'private installation only. These endpoints are not a documented API: review ESPN\'s terms and turn this source off if you do not accept them.',
    },
    'url': {
        'name': 'Operator-supplied base address',
        'terms_url': None,
        'attribution': 'Provided by the operator.',
        'license': 'The operator is responsible for the right to use these images.',
    },
    'pack': {
        'name': 'Operator-supplied logo pack',
        'terms_url': None,
        'attribution': 'Taken from the pack manifest.',
        'license': 'The operator is responsible for the right to use these images.',
    },
    'off': {'name': 'No artwork', 'terms_url': None, 'attribution': '', 'license': ''},
}


class ArtworkError(Exception):
    """A refusal or failure with a short, non-sensitive code."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# --- image validation ----------------------------------------------------------------------------
PNG_SIG = b'\x89PNG\r\n\x1a\n'
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def sniff_image(body):
    """Return (content_type, extension, width, height) for a PNG/JPEG/WebP within limits, else raise ArtworkError.

    The type comes from the bytes, never from a header or file name. SVG, HTML and every other format are refused outright.
    """
    if not isinstance(body, (bytes, bytearray)) or len(body) < 16:
        raise ArtworkError('image_too_small')
    if len(body) > MAX_IMAGE_BYTES:
        raise ArtworkError('image_too_large')
    head = bytes(body[:1024]).lower()
    if body[:8] == PNG_SIG:
        if body[12:16] != b'IHDR':
            raise ArtworkError('image_corrupt')
        width, height = struct.unpack('>II', body[16:24])
        kind = ('image/png', '.png')
    elif body[:3] == b'\xff\xd8\xff':
        width, height = _jpeg_size(body)
        kind = ('image/jpeg', '.jpg')
    elif body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        width = height = None
        kind = ('image/webp', '.webp')
    elif b'<svg' in head or head.lstrip().startswith(b'<?xml'):
        raise ArtworkError('svg_not_supported')
    elif b'<html' in head or b'<!doctype' in head:
        raise ArtworkError('not_an_image')
    else:
        raise ArtworkError('unsupported_image_type')
    if width is not None and not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
        raise ArtworkError('image_dimensions')
    return kind[0], kind[1], width, height


def _jpeg_size(body):
    i, n = 2, len(body)
    while i + 4 <= n:
        if body[i] != 0xFF:
            i += 1
            continue
        marker = body[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            i += 2
            continue
        length = struct.unpack('>H', body[i + 2:i + 4])[0]
        if marker in _SOF:
            if i + 9 > n:
                break
            height, width = struct.unpack('>HH', body[i + 5:i + 9])
            return width, height
        if length < 2:
            break
        i += 2 + length
    raise ArtworkError('image_corrupt')


# --- bounded, allow-listed fetching ---------------------------------------------------------------
class FetchPolicy:
    """Which URLs a fetch may touch at every hop (including redirects), and whether private addresses are refused."""
    def __init__(self, allows, public_only):
        self.allows = allows
        self.public_only = public_only


def espn_policy():
    def allows(url):
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or '').lower()
        return (parts.scheme == 'https' and not parts.username and not parts.password and parts.port in (None, 443)
                and (host in ESPN_API_HOSTS or host == 'espncdn.com' or host.endswith('.espncdn.com')))
    return FetchPolicy(allows, True)


def base_policy(base):
    """The operator's own base address: same scheme, host and port at every hop. LAN addresses are allowed (the operator chose it)."""
    root = urllib.parse.urlsplit(base)

    def allows(url):
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == root.scheme and (parts.hostname or '').lower() == (root.hostname or '').lower() and parts.port == root.port
                and not parts.username and not parts.password)
    return FetchPolicy(allows, False)


def _assert_public(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise ArtworkError('dns_failed')
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split('%')[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ArtworkError('address_not_public')


def http_hop(url, headers, max_bytes, public_only):
    """One GET, no redirect following, body read to at most max_bytes+1 so the caller can tell it was truncated."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ArtworkError('bad_url')
    if public_only:
        _assert_public(parts.hostname)
    cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=TIMEOUT)
    try:
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        conn.request('GET', path, headers=dict({'User-Agent': 'sgsports-artwork/1', 'Accept': 'image/png,image/webp,image/jpeg,application/json'}, **headers))
        response = conn.getresponse()
        body = response.read(max_bytes + 1)
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    except (OSError, http.client.HTTPException):
        raise ArtworkError('network_error')
    finally:
        conn.close()


def fetch(url, policy, headers=None, max_bytes=MAX_IMAGE_BYTES, hop=http_hop):
    """GET with at most MAX_REDIRECTS redirects, each re-checked against the policy. Returns (status, headers, body, final_url)."""
    for _ in range(MAX_REDIRECTS + 1):
        if not policy.allows(url):
            raise ArtworkError('host_not_allowed')
        status, response_headers, body = hop(url, headers or {}, max_bytes, policy.public_only)
        if status in (301, 302, 303, 307, 308):
            location = response_headers.get('location')
            if not location:
                raise ArtworkError('bad_redirect')
            url = urllib.parse.urljoin(url, location)
            continue
        if len(body) > max_bytes:
            raise ArtworkError('response_too_large')
        return status, response_headers, body, url
    raise ArtworkError('too_many_redirects')


# --- on-disk cache --------------------------------------------------------------------------------
def _atomic_write(path, data):
    tmp = path.with_name(path.name + '.tmp%d' % os.getpid())
    with open(tmp, 'wb') as handle:
        handle.write(data)
    os.replace(tmp, path)


class Cache:
    """Validated images plus an index of where each came from. Survives restarts; a damaged index is set aside and rebuilt empty."""
    def __init__(self, root):
        self.root = Path(root)
        self.files = self.root / 'files'
        self.directories = self.root / 'directory'
        self.lock = threading.RLock()
        self._index = None

    def _load(self):
        if self._index is not None:
            return self._index
        self.files.mkdir(parents=True, exist_ok=True)
        self.directories.mkdir(parents=True, exist_ok=True)
        path = self.root / 'index.json'
        index = {'schema_version': SCHEMA_VERSION, 'entries': {}}
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict) and data.get('schema_version') == SCHEMA_VERSION and isinstance(data.get('entries'), dict):
                    index = data
                else:
                    raise ValueError('shape')
            except (OSError, ValueError):
                try:
                    os.replace(path, self.root / 'index.damaged.json')
                except OSError:
                    pass
        self._index = index
        return index

    def _save(self):
        _atomic_write(self.root / 'index.json', json.dumps(self._index, sort_keys=True).encode())

    def entry(self, key):
        with self.lock:
            entry = self._load()['entries'].get(key)
            return dict(entry) if entry else None

    def entries(self):
        with self.lock:
            return {k: dict(v) for k, v in self._load()['entries'].items()}

    def read(self, key):
        """(entry, bytes) for a key whose file is present and matches its recorded hash, else None."""
        entry = self.entry(key)
        if not entry or not entry.get('file'):
            return None
        try:
            data = (self.files / entry['file']).read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            return None
        return entry, data

    def put(self, key, data, content_type, extension, meta, now):
        with self.lock:
            index = self._load()
            previous = index['entries'].get(key)
            name = key + extension
            _atomic_write(self.files / name, data)
            if previous and previous.get('file') and previous['file'] != name:
                (self.files / previous['file']).unlink(missing_ok=True)
            index['entries'][key] = dict(meta, file=name, content_type=content_type, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                         retrieved_at=now, last_used=now, failed_at=None, last_error=None)
            self._evict(now)
            self._save()

    def touch(self, key, now, **fields):
        with self.lock:
            entry = self._load()['entries'].get(key)
            if entry is not None:
                entry.update(fields, retrieved_at=now, failed_at=None, last_error=None)
                self._save()

    def used(self, key, now):
        with self.lock:
            entry = self._load()['entries'].get(key)
            if entry is not None and now - entry.get('last_used', 0) > 3600:
                entry['last_used'] = now
                self._save()

    def failed(self, key, source, code, now):
        """Record a failure without discarding a previous good copy of the same source."""
        with self.lock:
            entries = self._load()['entries']
            entry = entries.get(key)
            if entry is None or entry.get('source') != source:
                entry = {'source': source, 'file': None}
                if key in entries and entries[key].get('file'):
                    (self.files / entries[key]['file']).unlink(missing_ok=True)
                entries[key] = entry
            entry.update(failed_at=now, last_error=code)
            self._evict(now)
            self._save()

    def _evict(self, now):
        entries = self._load()['entries']
        order = sorted(entries, key=lambda k: entries[k].get('last_used', entries[k].get('failed_at') or 0))
        total = sum(e.get('bytes', 0) for e in entries.values())
        for key in order:
            if total <= CACHE_MAX_BYTES and len(entries) <= CACHE_MAX_ENTRIES:
                break
            entry = entries.pop(key)
            total -= entry.get('bytes', 0)
            if entry.get('file'):
                (self.files / entry['file']).unlink(missing_ok=True)

    def clear(self, directories=True):
        with self.lock:
            index = self._load()
            removed = len(index['entries'])
            for entry in index['entries'].values():
                if entry.get('file'):
                    (self.files / entry['file']).unlink(missing_ok=True)
            index['entries'] = {}
            if directories:
                for path in self.directories.glob('*.json'):
                    path.unlink(missing_ok=True)
            self._save()
            return removed

    def directory(self, slug):
        try:
            data = json.loads((self.directories / (slug + '.json')).read_text())
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def save_directory(self, slug, data):
        self._load()
        _atomic_write(self.directories / (slug + '.json'), json.dumps(data).encode())


# --- ESPN metadata ---------------------------------------------------------------------------------
def slugify(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def parse_directory(data):
    """ESPN `/teams` -> [{'id','names','logo'}]. Raises ArtworkError('provider_format') when the shape is not recognised."""
    try:
        rows = data['sports'][0]['leagues'][0]['teams']
        out = []
        for row in rows:
            team = row['team']
            logos = team.get('logos') or []
            if not logos or not team.get('id'):
                continue
            names = [team.get(k) for k in ('displayName', 'shortDisplayName', 'name')]
            if team.get('location') and team.get('name'):
                names.append('%s %s' % (team['location'], team['name']))
            out.append({'id': str(team['id']), 'names': [n for n in names if n], 'logo': logos[0]['href']})
        return out
    except (KeyError, IndexError, TypeError):
        raise ArtworkError('provider_format')


def parse_league_logo(data):
    try:
        logos = data['leagues'][0]['logos']
        pick = next((x for x in logos if 'dark' in x.get('rel', [])), logos[0])
        return pick['href']
    except (KeyError, IndexError, TypeError, StopIteration):
        raise ArtworkError('provider_format')


class ArtworkService:
    """Resolve, fetch, cache and serve artwork. `config` returns {'mode','enabled','url_template'→base address} or None (defaults)."""

    def __init__(self, root, config, hop=http_hop, clock=time.time, overrides=None):
        self.root = Path(root)
        self.config = config
        self.hop = hop
        self.clock = clock
        self.overrides = dict(overrides or {})   # catalog team ID -> ESPN team ID, for teams whose names do not match ESPN's
        self.cache = Cache(self.root)
        self._flight_lock = threading.Lock()
        self._flights = {}
        self._gate = threading.Semaphore(CONCURRENCY)
        self._sync_lock = threading.Lock()
        self._sync = {'state': 'idle', 'started_at': None, 'finished_at': None, 'total': 0, 'done': 0, 'ok': 0, 'failed': 0}

    # --- configuration ---
    def settings(self):
        raw = self.config() or {}
        mode = raw.get('mode', DEFAULT_MODE)
        if mode not in MODES:
            mode = 'off'
        if raw.get('enabled', True) is False:
            mode = 'off'
        base = raw.get('url_template') if mode == 'url' else None
        if mode == 'url' and not base:
            mode = 'off'
        return mode, base

    @staticmethod
    def source_tag(mode, base):
        if mode == 'url':
            return 'url:' + (urllib.parse.urlsplit(base).hostname or '')
        return mode

    # --- key resolution ---
    def espn_target(self, key):
        """Where ESPN would find this key: {'kind','path','slug','id','team_id','name'} or None. Only table entries are ever built into URLs."""
        match = ESPN_TEAM_RE.match(key)
        if match and match.group(1) in ESPN_BY_SLUG:
            return {'kind': 'team', 'path': ESPN_BY_SLUG[match.group(1)], 'slug': match.group(1), 'id': match.group(2), 'name': None}
        match = ESPN_LEAGUE_RE.match(key)
        if match and match.group(1) in ESPN_BY_SLUG:
            return {'kind': 'league', 'path': ESPN_BY_SLUG[match.group(1)], 'slug': match.group(1), 'id': None, 'name': None}
        entry = catalog.BY_ID.get(key)
        if entry is None:
            return None
        league = entry['name'] if entry['kind'] == 'league' else entry['league']
        path = CATALOG_ESPN.get((entry['sport'], league))
        if path is None:
            return None
        slug = path.replace('/', '_').replace('.', '_')
        if entry['kind'] == 'league':
            return {'kind': 'league', 'path': path, 'slug': slug, 'id': None, 'name': None}
        return {'kind': 'team', 'path': path, 'slug': slug, 'id': self.overrides.get(key), 'name': key.rsplit('.', 1)[-1]}

    def resolvable(self, key):
        mode, base = self.settings()
        if not KEY_RE.match(key):
            return False
        return mode in ('url', 'pack') or (mode == 'espn' and self.espn_target(key) is not None)

    # --- serving ---
    def get(self, key, if_none_match=None):
        """-> (status, content_type, body, headers). Never raises: any failure is a plain 404 the TV answers with a monogram."""
        mode, base = self.settings()
        if mode == 'off':
            return self._miss(404, 'artwork_disabled')
        if not KEY_RE.match(key):
            return self._miss(400, 'invalid_key')
        try:
            if mode == 'pack':
                return self._serve_pack(key, if_none_match)
            source = self.source_tag(mode, base)
            now = self.clock()
            found = self._usable(key, source)
            fresh = found and now - found[0]['retrieved_at'] < REVALIDATE_AFTER
            backoff = self.cache.entry(key)
            recently_failed = backoff and backoff.get('source') == source and backoff.get('failed_at') and now - backoff['failed_at'] < FAIL_BACKOFF
            if not fresh and not recently_failed:
                self._ensure(key, mode, base, source)
                found = self._usable(key, source)
            if not found:
                entry = self.cache.entry(key) or {}
                return self._miss(404, entry.get('last_error') or 'artwork_unavailable')
            self.cache.used(key, now)
            return self._image(found[0]['content_type'], found[1], found[0]['sha256'], if_none_match)
        except ArtworkError as error:
            return self._miss(404, error.code)
        except Exception:
            return self._miss(404, 'artwork_unavailable')

    def _usable(self, key, source):
        found = self.cache.read(key)
        return found if found and found[0].get('source') == source else None

    @staticmethod
    def _miss(status, code):
        return status, 'application/json', json.dumps({'error': code}).encode(), {'Cache-Control': 'no-store'}

    @staticmethod
    def _image(content_type, body, digest, if_none_match):
        etag = '"%s"' % digest[:32]
        headers = {'ETag': etag, 'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff',
                   'Content-Security-Policy': "default-src 'none'; sandbox"}
        if if_none_match and etag in [t.strip() for t in if_none_match.split(',')]:
            return 304, content_type, b'', headers
        return 200, content_type, body, headers

    # --- fetching ---
    def _ensure(self, key, mode, base, source, force=False):
        """Fetch (or revalidate) one key, collapsing concurrent requests for it. The outcome is read back from the cache."""
        with self._flight_lock:
            event = self._flights.get(key)
            leader = event is None
            if leader:
                event = self._flights[key] = threading.Event()
        if not leader:
            event.wait(TIMEOUT * (MAX_REDIRECTS + 4))
            return
        try:
            with self._gate:
                self._fetch(key, mode, base, source)
        except ArtworkError as error:
            self.cache.failed(key, source, error.code, self.clock())
        except Exception:
            self.cache.failed(key, source, 'unexpected_error', self.clock())
        finally:
            with self._flight_lock:
                self._flights.pop(key, None)
            event.set()

    def _fetch(self, key, mode, base, source):
        previous = self._usable(key, source)
        previous = previous[0] if previous else None
        if mode == 'espn':
            target = self.espn_target(key)
            if target is None:
                raise ArtworkError('unsupported_key')
            urls = [self._espn_logo_url(target)]
            policy, shown = espn_policy(), None
        else:
            root = base.rstrip('/')
            urls = [root + '/' + urllib.parse.quote(key, safe='') + ext for ext in EXTENSIONS]
            policy, shown = base_policy(root), urllib.parse.urlsplit(root).hostname
        last = 'not_found'
        for url in urls:
            headers = {}
            url_key = hashlib.sha256(url.encode()).hexdigest()[:24]   # the address itself may be a secret: never persist it
            if previous and previous.get('source_url_key') == url_key:
                if previous.get('etag'):
                    headers['If-None-Match'] = previous['etag']
                if previous.get('last_modified'):
                    headers['If-Modified-Since'] = previous['last_modified']
            status, response, body, final = fetch(url, policy, headers, MAX_IMAGE_BYTES, self.hop)
            if status == 304 and previous:
                self.cache.touch(key, self.clock())
                return
            if status == 200:
                content_type, extension, _, _ = sniff_image(body)
                meta = {'source': source, 'source_url': None if shown else final, 'source_host': shown or urllib.parse.urlsplit(final).hostname,
                        'source_url_key': url_key, 'etag': response.get('etag'), 'last_modified': response.get('last-modified')}
                self.cache.put(key, body, content_type, extension, meta, self.clock())
                return
            last = 'http_%d' % status if status != 404 else 'not_found'
        raise ArtworkError(last)

    def _espn_json(self, url):
        status, _, body, _ = fetch(url, espn_policy(), None, MAX_JSON_BYTES, self.hop)
        if status != 200:
            raise ArtworkError('http_%d' % status)
        try:
            return json.loads(body)
        except ValueError:
            raise ArtworkError('provider_format')

    def _directory(self, target, need):
        """Cached per-league directory (`teams` rows, `league_logo`), refreshed weekly; a stale copy survives a failed refresh."""
        slug, now = target['slug'], self.clock()
        data = self.cache.directory(slug) or {}
        have = data.get('teams') is not None if need == 'teams' else bool(data.get('league_logo'))
        if have and now - data.get('fetched_at', 0) < DIRECTORY_TTL:
            return data
        try:
            fresh = dict(data, fetched_at=now, schema_version=SCHEMA_VERSION)
            if need == 'teams':
                fresh['teams'] = parse_directory(self._espn_json(ESPN_API + target['path'] + '/teams?limit=1000'))
            else:
                fresh['league_logo'] = parse_league_logo(self._espn_json(ESPN_API + target['path'] + '/scoreboard?limit=1'))
            self.cache.save_directory(slug, fresh)
            return fresh
        except ArtworkError:
            if have:
                return data
            raise

    def _espn_logo_url(self, target):
        if target['kind'] == 'league':
            logo = self._directory(target, 'league').get('league_logo')
            if not logo:
                raise ArtworkError('not_found')
            return logo
        rows = self._directory(target, 'teams').get('teams') or []
        if target['id']:
            row = next((r for r in rows if r['id'] == target['id']), None)
        else:
            row = next((r for r in rows if target['name'] in {slugify(n) for n in r['names']}), None)
        if row is None:
            raise ArtworkError('not_found')
        return row['logo']

    # --- operator pack ---
    def pack_dir(self):
        return self.root / 'pack'

    def pack_manifest(self):
        """(manifest or None, problems). The manifest is optional; when present it must be schema-valid."""
        path = self.pack_dir() / 'pack.json'
        if not path.exists():
            return None, []
        try:
            if path.stat().st_size > PACK_MANIFEST_MAX:
                return None, ['manifest_too_large']
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None, ['manifest_unreadable']
        problems = []
        if not isinstance(data, dict):
            return None, ['manifest_not_object']
        if data.get('schema_version') != 1:
            problems.append('unsupported_schema_version')
        for field in ('name', 'license', 'attribution'):
            if not isinstance(data.get(field), str) or not data[field].strip() or len(data[field]) > 300:
                problems.append(field + '_required')
        entries = data.get('entries', {})
        if not isinstance(entries, dict) or len(entries) > 5000:
            problems.append('entries_invalid')
        else:
            for key, name in entries.items():
                if not (isinstance(key, str) and KEY_RE.match(key) and isinstance(name, str) and PACK_FILE_RE.match(name) and '..' not in name):
                    problems.append('entry_invalid')
                    break
        return (None if problems else data), problems

    def _serve_pack(self, key, if_none_match):
        manifest, problems = self.pack_manifest()
        if problems:
            return self._miss(404, 'pack_invalid')
        names = [(manifest or {}).get('entries', {}).get(key)] if manifest else []
        names += [key + ext for ext in EXTENSIONS + ('.jpeg',)]
        directory = self.pack_dir().resolve()
        for name in filter(None, names):
            candidate = directory / name
            try:
                resolved = candidate.resolve()
                if resolved.parent != directory or not resolved.is_file() or resolved.stat().st_size > MAX_IMAGE_BYTES:
                    continue
                data = resolved.read_bytes()
                content_type, _, _, _ = sniff_image(data)
            except (OSError, ArtworkError):
                continue
            return self._image(content_type, data, hashlib.sha256(data).hexdigest(), if_none_match)
        return self._miss(404, 'not_found')

    # --- operator actions ---
    def clear(self):
        return {'cleared': self.cache.clear()}

    def sync(self):
        """Start a background refresh of the catalog's artwork plus every cached item. Returns False when one is already running."""
        mode, base = self.settings()
        with self._sync_lock:
            if self._sync['state'] == 'running':
                return False
            self._sync = {'state': 'running', 'started_at': int(self.clock()), 'finished_at': None, 'total': 0, 'done': 0, 'ok': 0, 'failed': 0}
        threading.Thread(target=self._run_sync, args=(mode, base), daemon=True, name='artwork-sync').start()
        return True

    def run_sync_blocking(self):
        mode, base = self.settings()
        with self._sync_lock:
            self._sync = {'state': 'running', 'started_at': int(self.clock()), 'finished_at': None, 'total': 0, 'done': 0, 'ok': 0, 'failed': 0}
        self._run_sync(mode, base)

    def _run_sync(self, mode, base):
        try:
            if mode in ('off', 'pack'):
                keys = []
            else:
                source = self.source_tag(mode, base)
                keys = [e['id'] for e in catalog.ENTRIES if mode == 'url' or self.espn_target(e['id']) is not None]
                keys += [k for k, e in self.cache.entries().items() if e.get('source') == source and k not in keys]
            self._sync['total'] = len(keys)
            source = self.source_tag(mode, base)

            def one(key):
                self._ensure(key, mode, base, source)
                found = self._usable(key, source)
                with self._sync_lock:
                    self._sync['done'] += 1
                    self._sync['ok' if found else 'failed'] += 1
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                list(pool.map(one, keys))
            state = 'done'
        except Exception:
            state = 'error'
        with self._sync_lock:
            self._sync.update(state=state, finished_at=int(self.clock()))

    def status(self):
        mode, base = self.settings()
        configured = (self.config() or {})
        entries = self.cache.entries()
        rows = [{'key': k, 'source': e.get('source'), 'source_host': e.get('source_host'), 'source_url': e.get('source_url'), 'bytes': e.get('bytes', 0),
                 'ok': bool(e.get('file')), 'retrieved_at': e.get('retrieved_at'), 'etag': e.get('etag'), 'last_modified': e.get('last_modified'),
                 'failed_at': e.get('failed_at'), 'last_error': e.get('last_error')} for k, e in entries.items()]
        rows.sort(key=lambda r: (r['ok'], -(r['retrieved_at'] or 0)))
        out = {'schema_version': SCHEMA_VERSION, 'mode': mode, 'configured_mode': configured.get('mode', DEFAULT_MODE),
               'enabled': configured.get('enabled', True) is not False, 'url_configured': bool(configured.get('url_template')),
               'modes': list(MODES), 'provider': PROVIDERS[mode],
               'cache': {'entries': sum(1 for r in rows if r['ok']), 'failed': sum(1 for r in rows if not r['ok']), 'bytes': sum(r['bytes'] for r in rows),
                         'max_bytes': CACHE_MAX_BYTES, 'max_entries': CACHE_MAX_ENTRIES},
               'sync': dict(self._sync), 'entries': rows[:STATUS_ENTRY_LIMIT]}
        if mode == 'pack':
            manifest, problems = self.pack_manifest()
            out['pack'] = {'directory': 'pack', 'manifest': None if manifest is None else {k: manifest[k] for k in ('name', 'license', 'attribution')},
                           'problems': problems, 'files': sum(1 for p in self.pack_dir().glob('*') if p.is_file() and p.name != 'pack.json') if self.pack_dir().is_dir() else 0}
            if manifest:
                out['provider'] = dict(PROVIDERS['pack'], name=manifest['name'], attribution=manifest['attribution'], license=manifest['license'])
        return out
