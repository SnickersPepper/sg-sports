"""Administrator authentication: username and password, forced first change, opaque browser sessions, throttling, recovery.

State lives in two private files under the data directory:
  admin-auth.json      username, scrypt verifier (salted, memory-hard), must_change flag, session epoch.
  admin-sessions.json  SHA-256 of each opaque session id, its CSRF token and timestamps, tied to the epoch.
A fresh instance (no admin-auth.json and no legacy administrator token) starts as admin / admin with must_change set:
the only things such a session may do are change the password and sign out. An upgraded instance that still holds a legacy
administrator token (SG_ADMIN_TOKEN_FILE, secrets/api_token or admin_token.sha256) stays in 'migrate' mode: the token is
accepted once, only to choose the first username and password, and then no longer authenticates anything.
Recovery is `python adminauth.py reset` run inside the container; it changes the epoch, which invalidates every session.
Plaintext passwords are never stored, returned or logged. Android TV pairing and device tokens are a separate system (tv.py).
"""
import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:      # pragma: no cover - not available off POSIX
    fcntl = None

AUTH_FILE = 'admin-auth.json'
SESSIONS_FILE = 'admin-sessions.json'
LOCK_FILE = '.admin-auth.lock'
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin'
SCRYPT = {'n': 2 ** 15, 'r': 8, 'p': 1}          # 32 MiB per hash
MIN_PASSWORD, MAX_PASSWORD = 10, 256
USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9._@-]{2,63}$')
MAX_SESSIONS = 20
TOUCH_INTERVAL = 60
COOKIE = 'sg_admin'
COOKIE_SECURE = '__Host-sg_admin'

_LOCK = threading.RLock()
_HASH_SLOTS = threading.BoundedSemaphore(2)       # bounds concurrent 32 MiB hashes
_clock = time.time


def idle_seconds():
    return int(os.environ.get('SG_ADMIN_IDLE_SECONDS') or 1800)


def absolute_seconds():
    return int(os.environ.get('SG_ADMIN_SESSION_SECONDS') or 12 * 3600)


# ---------------------------------------------------------------- files

def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    os.replace(tmp, path)


def _read_json(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@contextlib.contextmanager
def _locked(data_dir):
    """Thread lock plus an advisory file lock, so the recovery command and the server never interleave writes."""
    with _LOCK:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        handle = open(Path(data_dir) / LOCK_FILE, 'a')
        try:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            handle.close()


# ---------------------------------------------------------------- password hashing

def _b64(raw):
    return base64.b64encode(raw).decode()


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    with _HASH_SLOTS:
        digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=64 << 20, dklen=32, **SCRYPT)
    return 'scrypt$%d$%d$%d$%s$%s' % (SCRYPT['n'], SCRYPT['r'], SCRYPT['p'], _b64(salt), _b64(digest))


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, expected = stored.split('$')
        if scheme != 'scrypt':
            return False
        salt, expected = base64.b64decode(salt), base64.b64decode(expected)
        if int(n) > 2 ** 16:
            return False
        with _HASH_SLOTS:
            digest = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=96 << 20, dklen=len(expected))
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(digest, expected)


_DUMMY = None


def _dummy_hash():
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password('dummy-' + secrets.token_hex(8))
    return _DUMMY


# ---------------------------------------------------------------- legacy administrator token

def _supplied_token(secrets_dir):
    override = os.environ.get('SG_ADMIN_TOKEN_FILE')
    for path in ([Path(override)] if override else []) + [Path(secrets_dir) / 'api_token']:
        try:
            value = path.read_text().strip()
        except OSError:
            continue
        if value:
            return value
    return ''


def _legacy_digest(data_dir):
    try:
        return (Path(data_dir) / 'admin_token.sha256').read_text().strip()
    except OSError:
        return ''


def legacy_present(data_dir, secrets_dir):
    return bool(_supplied_token(secrets_dir) or _legacy_digest(data_dir))


def _legacy_token_ok(data_dir, secrets_dir, presented):
    if not isinstance(presented, str) or not presented:
        return False
    supplied = _supplied_token(secrets_dir)
    if supplied:
        return hmac.compare_digest(presented, supplied)
    stored = _legacy_digest(data_dir)
    return bool(stored) and hmac.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), stored)


# ---------------------------------------------------------------- records

class AuthStateError(Exception):
    """admin-auth.json exists but cannot be trusted (unreadable, malformed, wrong schema). Never treated as 'no account'."""


def _valid_record(record):
    try:
        scheme, n, r, p, salt, digest = record['verifier'].split('$')
        salt, digest = base64.b64decode(salt, validate=True), base64.b64decode(digest, validate=True)
        n, r, p = int(n), int(r), int(p)
        return (record.get('version') == 1 and scheme == 'scrypt' and 2 ** 14 <= n <= 2 ** 16 and n & (n - 1) == 0
                and 1 <= r <= 16 and 1 <= p <= 4 and len(salt) >= 16 and len(digest) == 32
                and isinstance(record['username'], str) and USERNAME_RE.match(record['username']) is not None
                and isinstance(record['must_change'], bool) and isinstance(record['epoch'], int) and not isinstance(record['epoch'], bool))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def _auth(data_dir):
    """The account record, None only when the file genuinely does not exist; AuthStateError for anything else."""
    path = Path(data_dir) / AUTH_FILE
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise AuthStateError('unreadable') from error
    try:
        record = json.loads(text)
    except ValueError as error:
        raise AuthStateError('malformed') from error
    if not isinstance(record, dict) or not _valid_record(record):
        raise AuthStateError('invalid')
    return record


INVALID_HELP = ('The administrator record in the data volume is unreadable or invalid, so sign-in is disabled. '
                'On the host run: docker compose exec sgsports python adminauth.py reset')


def _save_auth(data_dir, record):
    _write_private(Path(data_dir) / AUTH_FILE, json.dumps(record, sort_keys=True))


def _new_record(username, password, must_change, epoch):
    return {'version': 1, 'username': username, 'verifier': hash_password(password), 'must_change': must_change,
            'epoch': epoch, 'updated': int(_clock())}


def mode(data_dir, secrets_dir):
    """'login' when an administrator account exists (or is created fresh); 'migrate' for an upgraded token-only instance."""
    try:
        if _auth(data_dir):
            return 'login'
    except AuthStateError:
        return 'invalid'
    return 'migrate' if legacy_present(data_dir, secrets_dir) else 'login'


def prepare(data_dir, secrets_dir, log=print):
    """Create the fresh-install default account when there is no administrator authority. Returns the mode."""
    with _locked(data_dir):
        for stale in ('claim-code',):
            (Path(data_dir) / stale).unlink(missing_ok=True)
        try:
            if _auth(data_dir):
                return 'login'
        except AuthStateError:
            log(INVALID_HELP)
            return 'invalid'
        if legacy_present(data_dir, secrets_dir):
            log('SG Sports found a legacy administrator token. Open the root address and use "Move to a username and password" once.')
            return 'migrate'
        _save_auth(data_dir, _new_record(DEFAULT_USERNAME, DEFAULT_PASSWORD, True, secrets.randbelow(2 ** 31)))
        _drop_sessions(data_dir)
        log('SG Sports is a fresh install: sign in at the root address as admin / admin; you must choose a new password immediately.')
        return 'login'


def validate_username(username):
    return isinstance(username, str) and bool(USERNAME_RE.match(username.strip().lower()))


def validate_password(password, username=''):
    """Policy for any password the user chooses. Returns an error code or None."""
    if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        return 'password_length'
    if password.lower() in (DEFAULT_PASSWORD, str(username).lower()) or password.lower() == 'password':
        return 'password_too_weak'
    return None


# ---------------------------------------------------------------- throttling

class Backoff:
    """Per-key failure counter with exponential wait (free for the first 3 failures), bounded memory, and self-healing:
    the wait expires on its own and failures older than `forget` seconds are dropped, so nothing needs database editing."""

    def __init__(self, cap, forget=900, max_keys=1024):
        self.cap, self.forget, self.max_keys, self.state, self.lock = cap, forget, max_keys, {}, threading.Lock()

    def wait(self, key):
        with self.lock:
            entry = self.state.get(key)
            if not entry:
                return 0
            count, last = entry
            now = _clock()
            if now - last > self.forget:
                del self.state[key]
                return 0
            if count < 3:
                return 0
            remaining = min(self.cap, 2 ** (count - 3)) - (now - last)
            return int(remaining) + 1 if remaining > 0 else 0

    def fail(self, key):
        with self.lock:
            count, last = self.state.get(key, (0, 0))
            now = _clock()
            self.state[key] = (1 if now - last > self.forget else count + 1, now)
            while len(self.state) > self.max_keys:
                self.state.pop(next(iter(self.state)))

    def clear(self, key):
        with self.lock:
            self.state.pop(key, None)


CLIENT_BACKOFF = Backoff(cap=300)
ACCOUNT_BACKOFF = Backoff(cap=30)       # kept short so a stranger cannot lock the owner out for long


def _throttle_wait(client):
    return max(CLIENT_BACKOFF.wait(client or '-'), ACCOUNT_BACKOFF.wait('account'))


def _failed(client):
    CLIENT_BACKOFF.fail(client or '-')
    ACCOUNT_BACKOFF.fail('account')


def _succeeded(client):
    CLIENT_BACKOFF.clear(client or '-')
    ACCOUNT_BACKOFF.clear('account')


def reset_throttle():
    CLIENT_BACKOFF.state.clear()
    ACCOUNT_BACKOFF.state.clear()


# ---------------------------------------------------------------- sessions

def _sid_hash(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


def _sessions(data_dir, epoch):
    stored = _read_json(Path(data_dir) / SESSIONS_FILE) or {}
    if stored.get('epoch') != epoch or not isinstance(stored.get('sessions'), dict):
        return {}
    now = _clock()
    return {k: v for k, v in stored['sessions'].items()
            if isinstance(v, dict) and now - v.get('created', 0) < absolute_seconds() and now - v.get('last', 0) < idle_seconds()}


def _save_sessions(data_dir, epoch, sessions):
    while len(sessions) > MAX_SESSIONS:
        oldest = min(sessions, key=lambda k: sessions[k]['created'])
        del sessions[oldest]
    _write_private(Path(data_dir) / SESSIONS_FILE, json.dumps({'epoch': epoch, 'sessions': sessions}))


def _drop_sessions(data_dir):
    (Path(data_dir) / SESSIONS_FILE).unlink(missing_ok=True)


def _issue(data_dir, record, extra_sessions=None):
    """Create a session for the account and return its public view (the raw id is only in the cookie)."""
    sid = secrets.token_urlsafe(32)
    now = _clock()
    sessions = dict(extra_sessions) if extra_sessions is not None else _sessions(data_dir, record['epoch'])
    csrf = secrets.token_urlsafe(24)
    sessions[_sid_hash(sid)] = {'csrf': csrf, 'created': now, 'last': now}
    _save_sessions(data_dir, record['epoch'], sessions)
    return {'sid': sid, 'csrf': csrf, 'username': record['username'], 'must_change': bool(record['must_change'])}


def parse_cookie(header):
    """The opaque session id from a Cookie header, or ''."""
    if not isinstance(header, str):
        return ''
    found = ''
    for part in header.split(';'):
        name, _, value = part.strip().partition('=')
        if name == COOKIE_SECURE:
            return value
        if name == COOKIE:
            found = value
    return found


def set_cookie(sid, secure):
    name = COOKIE_SECURE if secure else COOKIE
    return '%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d%s' % (name, sid, absolute_seconds(), '; Secure' if secure else '')


def clear_cookie(secure):
    name = COOKIE_SECURE if secure else COOKIE
    return '%s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0%s' % (name, '; Secure' if secure else '')


def session_for(data_dir, secrets_dir, cookie_header):
    """The signed-in session for a Cookie header (enforcing idle and absolute expiry and the epoch), or None."""
    sid = parse_cookie(cookie_header)
    if not sid or len(sid) > 128:
        return None
    try:
        record = _auth(data_dir)
    except AuthStateError:
        return None
    if not record:
        return None
    key = _sid_hash(sid)
    with _locked(data_dir):
        sessions = _sessions(data_dir, record['epoch'])
        entry = sessions.get(key)
        if not entry:
            return None
        now = _clock()
        if now - entry['last'] >= TOUCH_INTERVAL:
            entry['last'] = now
            _save_sessions(data_dir, record['epoch'], sessions)
    return {'key': key, 'csrf': entry['csrf'], 'username': record['username'], 'must_change': bool(record['must_change'])}


def csrf_ok(session, presented):
    return bool(session) and isinstance(presented, str) and bool(presented) and hmac.compare_digest(presented, session['csrf'])


def logout(data_dir, session):
    if not session:
        return
    try:
        record = _auth(data_dir)
    except AuthStateError:
        return
    if not record:
        return
    with _locked(data_dir):
        sessions = _sessions(data_dir, record['epoch'])
        if sessions.pop(session['key'], None) is not None:
            _save_sessions(data_dir, record['epoch'], sessions)


# ---------------------------------------------------------------- operations

def login(data_dir, secrets_dir, username, password, client):
    """Returns ('ok', session) | ('invalid', None) | ('throttled', seconds) | ('migrate_required', None)."""
    wait = _throttle_wait(client)
    if wait:
        return 'throttled', wait
    try:
        record = _auth(data_dir)
        if not record:
            if legacy_present(data_dir, secrets_dir):
                return 'migrate_required', None
            if prepare(data_dir, secrets_dir, lambda _: None) == 'invalid':
                return 'unavailable', None
            record = _auth(data_dir)
    except AuthStateError:
        return 'unavailable', None
    ok = isinstance(username, str) and isinstance(password, str) and 0 < len(password) <= MAX_PASSWORD
    stored = record['verifier'] if ok else _dummy_hash()
    password_ok = verify_password(password if ok else 'x', stored)          # always hashed: the same work for any username
    user_ok = ok and hmac.compare_digest(username.strip().lower().encode(), record['username'].encode())
    if not (ok and password_ok and user_ok):
        _failed(client)
        return 'invalid', None
    with _locked(data_dir):
        try:
            current = _auth(data_dir)
        except AuthStateError:
            return 'unavailable', None
        # the credentials were checked against a snapshot: if a change or reset landed meanwhile, they are void
        if not current or current['verifier'] != record['verifier'] or current['epoch'] != record['epoch'] or current['username'] != record['username']:
            _failed(client)
            return 'invalid', None
        _succeeded(client)
        return 'ok', _issue(data_dir, current)


def change_credentials(data_dir, session, current_password, new_password, new_username, client):
    """Change the password and/or username after re-checking the current password. A password change revokes every session
    and returns a fresh one. Returns ('ok', session_or_None) or ('error', code) or ('throttled', seconds)."""
    wait = _throttle_wait(client)
    if wait:
        return 'throttled', wait
    if new_password is None and new_username is None:
        return 'error', 'nothing_to_change'
    with _locked(data_dir):
        try:
            record = _auth(data_dir)
        except AuthStateError:
            return 'error', 'auth_state_invalid'
        if not record or not session:
            return 'error', 'unauthorized'
        if not isinstance(current_password, str) or not verify_password(current_password, record['verifier']):
            _failed(client)
            return 'error', 'current_password_incorrect'
        _succeeded(client)
        username = record['username']
        if new_username is not None:
            if not validate_username(new_username):
                return 'error', 'invalid_username'
            username = new_username.strip().lower()
        if record['must_change'] and new_password is None:
            return 'error', 'password_change_required'
        if new_password is not None:
            problem = validate_password(new_password, username)
            if problem:
                return 'error', problem
            if hmac.compare_digest(new_password.encode(), current_password.encode()):
                return 'error', 'password_unchanged'
        updated = dict(record, username=username, updated=int(_clock()))
        if new_password is None:
            _save_auth(data_dir, updated)
            return 'ok', None
        updated.update(verifier=hash_password(new_password), must_change=False, epoch=record['epoch'] + 1)
        _save_auth(data_dir, updated)
        return 'ok', _issue(data_dir, updated, extra_sessions={})


def migrate(data_dir, secrets_dir, token, username, password, client):
    """Exchange the legacy administrator token (once) for a username and password. Returns like login()."""
    wait = _throttle_wait(client)
    if wait:
        return 'throttled', wait
    with _locked(data_dir):
        try:
            existing = _auth(data_dir)
        except AuthStateError:
            return 'unavailable', None
        if existing:
            return 'invalid', None                      # nothing to migrate: the legacy token no longer exists as a credential
        if not _legacy_token_ok(data_dir, secrets_dir, token):
            _failed(client)
            return 'invalid', None
        if not validate_username(username):
            return 'error', 'invalid_username'
        problem = validate_password(password, username.strip().lower())
        if problem:
            return 'error', problem
        record = _new_record(username.strip().lower(), password, False, secrets.randbelow(2 ** 31))
        record['legacy_disabled'] = True
        _save_auth(data_dir, record)
        (Path(data_dir) / 'admin_token.sha256').unlink(missing_ok=True)   # the supplied-secret file belongs to the operator; it is simply never consulted again
        _succeeded(client)
        return 'ok', _issue(data_dir, record, extra_sessions={})


def reset(data_dir, password=None, username=None):
    """Operator recovery: replace the account and invalidate every session. With no password it restores admin / admin
    and requires a change at the next sign-in."""
    with _locked(data_dir):
        try:
            previous = _auth(data_dir) or {}
        except AuthStateError:
            previous = {}                                  # a damaged record is exactly what recovery replaces
        name = (username or previous.get('username') or DEFAULT_USERNAME).strip().lower()
        if not validate_username(name):
            raise ValueError('invalid_username')
        if password is None:
            record = _new_record(DEFAULT_USERNAME if username is None else name, DEFAULT_PASSWORD, True, secrets.randbelow(2 ** 31))
        else:
            problem = validate_password(password, name)
            if problem:
                raise ValueError(problem)
            record = _new_record(name, password, False, secrets.randbelow(2 ** 31))
        record['legacy_disabled'] = True
        _save_auth(data_dir, record)
        _drop_sessions(data_dir)
        (Path(data_dir) / 'admin_token.sha256').unlink(missing_ok=True)
        reset_throttle()
        return record['username']


# ---------------------------------------------------------------- recovery command

USAGE = """Recover administrator access (run inside the container, e.g. `docker compose exec sgsports python adminauth.py reset`):
  python adminauth.py reset [--username NAME]            read a new password from the terminal (typed twice, not shown)
  printf '%s' "$NEW" | python adminauth.py reset         read it from standard input
  python adminauth.py reset --default                    restore admin / admin and require a change at the next sign-in
Every existing session is invalidated. The password is never printed."""


def main(argv, data_dir=None):
    data_dir = Path(data_dir or os.environ.get('SG_DATA', '/data'))
    args = list(argv[1:])
    if not args or args[0] != 'reset':
        print(USAGE)
        return 2
    args = args[1:]
    username = None
    if '--username' in args:
        index = args.index('--username')
        username = args[index + 1] if index + 1 < len(args) else ''
        del args[index:index + 2]
    use_default = '--default' in args
    if [a for a in args if a != '--default']:
        print(USAGE)
        return 2
    password = None
    if not use_default:
        if sys.stdin.isatty():
            import getpass
            password = getpass.getpass('New administrator password: ')
            if password != getpass.getpass('Repeat it: '):
                print('The passwords differ; nothing was changed.')
                return 1
        else:
            password = sys.stdin.readline().rstrip('\r\n')
    try:
        name = reset(data_dir, password, username)
    except ValueError as error:
        print('Nothing was changed: %s.' % error)
        return 1
    print('Administrator "%s" reset; all sessions were signed out.%s' % (name, ' Sign in as admin / admin and choose a new password.' if use_default else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
