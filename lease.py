"""Persistent, atomic provider-lease authority (one upstream stream per provider).

Every SG Sports-managed entry point to a provider stream (live playback, content verification,
the Jellyfin DVR relay and the legacy recorder) must hold a lease here before it opens an upstream
connection and must keep proving it still holds it while the bytes flow.

Guarantees (SQLite, one writer at a time via BEGIN IMMEDIATE, so they hold across threads and
across processes sharing the file):
- at most one unexpired lease per provider;
- fencing: every grant gets a strictly increasing per-provider token; a holder whose lease was
  expired, released or preempted fails `validate`/`renew` and must stop streaming (fail closed);
- an expired lease is never renewable and is reclaimed only by the next acquire, so a crashed
  holder cannot keep a provider after its TTL, and a restart cannot resurrect a stale hold;
- priority preemption is opt-in per acquire and only replaces a strictly lower priority holder.

Unmanaged clients that talk to the provider directly cannot be seen here: that is outside the
guarantee and documented as such.
"""
import os
import sqlite3
import threading
import time

PRIORITY = {'verification': 10, 'dvr': 50, 'playback': 100}
DEFAULT_TTL = 90
COOLDOWN_PRIORITY = 10 ** 6   # a cooldown is never preempted: the relay may still hold the provider's upstream


class Lease(dict):
    """A granted lease: provider, owner, kind, priority, fence, acquired, expires."""

    @property
    def fence(self):
        return self['fence']


class LeaseAuthority:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        self._local = threading.local()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS leases (provider TEXT PRIMARY KEY, owner TEXT NOT NULL,'
                       ' kind TEXT NOT NULL, priority INTEGER NOT NULL, fence INTEGER NOT NULL,'
                       ' acquired REAL NOT NULL, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS fences (provider TEXT PRIMARY KEY, last INTEGER NOT NULL)')
            # Shared (same-channel fan-out) holds: one lease row per provider, one member row per consumer, plus the
            # cooldown the provider stays reserved after the last member leaves (an external relay may keep its upstream open).
            db.execute('CREATE TABLE IF NOT EXISTS shares (provider TEXT PRIMARY KEY, share TEXT NOT NULL, cooldown REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS members (provider TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL,'
                       ' priority INTEGER NOT NULL, expires REAL NOT NULL, PRIMARY KEY (provider, owner))')

    def _db(self):
        db = getattr(self._local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            db.execute('PRAGMA journal_mode=WAL')
            self._local.db = db
        return db

    def _tx(self):
        authority = self

        class Tx:
            def __enter__(self):
                self.db = authority._db()
                self.db.execute('BEGIN IMMEDIATE')
                return self.db

            def __exit__(self, kind, value, trace):
                self.db.execute('ROLLBACK' if kind else 'COMMIT')
                return False
        return Tx()

    @staticmethod
    def _lease(row):
        return Lease(provider=row[0], owner=row[1], kind=row[2], priority=row[3], fence=row[4],
                     acquired=row[5], expires=row[6])

    def _active(self, db, provider, now):
        row = db.execute('SELECT provider,owner,kind,priority,fence,acquired,expires FROM leases WHERE provider=?',
                         (provider,)).fetchone()
        return self._lease(row) if row and row[6] > now else None

    def _grant(self, db, provider, owner, kind, priority, ttl, now):
        row = db.execute('SELECT last FROM fences WHERE provider=?', (provider,)).fetchone()
        fence = (row[0] if row else 0) + 1
        db.execute('INSERT OR REPLACE INTO fences(provider,last) VALUES(?,?)', (provider, fence))
        db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?,?,?,?,?)',
                   (provider, owner, kind, priority, fence, now, now + ttl))
        return Lease(provider=provider, owner=owner, kind=kind, priority=priority, fence=fence,
                     acquired=now, expires=now + ttl)

    def acquire(self, provider, owner, kind, ttl=DEFAULT_TTL, priority=None, preempt=False, exclusive=False, stream_id=None):
        """stream_id is a routing hint for delivery-aware subclasses and is ignored here. Return a Lease or None. Re-acquiring as the same live owner renews it and keeps its fence, unless exclusive:
        then a second concurrent holder of the same owner is refused (one upstream per owner)."""
        priority = PRIORITY.get(kind, 0) if priority is None else int(priority)
        if not provider or not owner or ttl <= 0:
            return None
        with self._tx() as db:
            now = self.clock()
            held = self._active(db, provider, now)
            if held and held['owner'] == owner and held['kind'] == kind and not exclusive:
                db.execute('UPDATE leases SET expires=? WHERE provider=? AND fence=?',
                           (now + ttl, provider, held['fence']))
                return Lease(held, expires=now + ttl)
            if held and not (preempt and priority > held['priority']):
                return None
            return self._grant(db, provider, owner, kind, priority, ttl, now)

    def renew(self, lease, ttl=DEFAULT_TTL):
        """Extend a lease that is still the live holder. False means the caller lost it and must stop."""
        if lease.get('share'):
            return self._shared_renew(lease, ttl)
        with self._tx() as db:
            now = self.clock()
            held = self._active(db, lease['provider'], now)
            if not held or held['fence'] != lease['fence'] or held['owner'] != lease['owner']:
                return False
            db.execute('UPDATE leases SET expires=? WHERE provider=? AND fence=?',
                       (now + ttl, lease['provider'], lease['fence']))
            lease['expires'] = now + ttl
            return True

    def validate(self, lease):
        """Read-only fencing check: is this exact grant still the unexpired holder?"""
        if not lease:
            return False
        if lease.get('share'):
            return self._shared_valid(lease)
        now = self.clock()
        row = self._db().execute('SELECT owner,fence,expires FROM leases WHERE provider=?', (lease['provider'],)).fetchone()
        return bool(row and row[2] > now and row[1] == lease['fence'] and row[0] == lease['owner'])

    def release(self, lease):
        """Release only if the grant is still current; a stale holder never frees someone else's lease."""
        if not lease:
            return False
        if lease.get('share'):
            return self._shared_release(lease)
        with self._tx() as db:
            cur = db.execute('DELETE FROM leases WHERE provider=? AND fence=? AND owner=?',
                             (lease['provider'], lease['fence'], lease['owner']))
            return cur.rowcount == 1

    def cancel_owner(self, owner):
        """Drop every lease an owner holds (cancellation); the holder's next validate fails."""
        with self._tx() as db:
            count = db.execute('DELETE FROM leases WHERE owner=?', (owner,)).rowcount
            now = self.clock()
            for (provider,) in db.execute('SELECT DISTINCT provider FROM members WHERE owner=?', (owner,)).fetchall():
                db.execute('DELETE FROM members WHERE provider=? AND owner=?', (provider, owner))
                self._refresh(db, provider, now)
                count += 1
            return count

    def holds(self, provider, owner):
        """True if owner currently holds provider, as a plain holder or as a live member of a shared hold."""
        held = self.holder(provider)
        if not held:
            return False
        if held['owner'] == owner:
            return True
        row = self._db().execute('SELECT 1 FROM members WHERE provider=? AND owner=? AND expires>?', (provider, owner, self.clock())).fetchone()
        return bool(row and held['owner'].startswith('share:'))

    # --- shared holds: same-channel fan-out through an external relay ------------------------------------------
    def _refresh(self, db, provider, now):
        """Recompute a shared lease from its live members. With none left it becomes a non-preemptable cooldown so a
        different channel cannot be opened while the relay may still hold the provider's single upstream."""
        row = db.execute('SELECT share, cooldown FROM shares WHERE provider=?', (provider,)).fetchone()
        if not row:
            return
        db.execute('DELETE FROM members WHERE provider=? AND expires<=?', (provider, now))
        members = db.execute('SELECT kind, priority, expires FROM members WHERE provider=? ORDER BY priority DESC', (provider,)).fetchall()
        if members:
            db.execute('UPDATE leases SET kind=?, priority=?, expires=? WHERE provider=? AND owner=?',
                       (members[0][0], members[0][1], max(m[2] for m in members) + row[1], provider, 'share:' + row[0]))
        else:
            held = db.execute('SELECT expires FROM leases WHERE provider=? AND owner=?', (provider, 'share:' + row[0])).fetchone()
            if held:
                db.execute('UPDATE leases SET kind=?, priority=?, expires=? WHERE provider=? AND owner=?',
                           ('cooldown', COOLDOWN_PRIORITY, min(held[0], now + row[1]), provider, 'share:' + row[0]))

    def acquire_shared(self, provider, share, member, kind, ttl=DEFAULT_TTL, priority=None, preempt=False, cooldown=0):
        """Join (or create) the provider's hold for one share key (one channel on one relay). Another share key is refused
        while the hold lives (members or cooldown): the old relay session may still hold the provider's single upstream, so a higher
        priority cannot displace it. preempt is accepted for interface parity and ignored; priority only raises the hold that
        same-channel consumers join. Returns a member Lease (carries 'share') or None."""
        priority = PRIORITY.get(kind, 0) if priority is None else int(priority)
        if not provider or not share or not member or ttl <= 0:
            return None
        with self._tx() as db:
            now = self.clock()
            held = self._active(db, provider, now)
            if held and held['owner'] == 'share:' + share:
                db.execute('UPDATE shares SET cooldown=MAX(cooldown, ?) WHERE provider=?', (cooldown, provider))
            elif held:
                return None
            else:
                self._grant(db, provider, 'share:' + share, kind, priority, ttl + cooldown, now)
                db.execute('DELETE FROM members WHERE provider=?', (provider,))
                db.execute('INSERT OR REPLACE INTO shares VALUES(?,?,?)', (provider, share, cooldown))
            db.execute('INSERT OR REPLACE INTO members VALUES(?,?,?,?,?)', (provider, member, kind, priority, now + ttl))
            self._refresh(db, provider, now)
            current = self._active(db, provider, now)
            return Lease(provider=provider, owner=member, kind=kind, priority=priority, fence=current['fence'],
                         acquired=now, expires=now + ttl, share=share)

    def _member_ok(self, db, lease, now):
        held = self._active(db, lease['provider'], now)
        if not held or held['fence'] != lease['fence'] or held['owner'] != 'share:' + lease['share']:
            return False
        return db.execute('SELECT 1 FROM members WHERE provider=? AND owner=? AND expires>?', (lease['provider'], lease['owner'], now)).fetchone() is not None

    def _shared_valid(self, lease):
        with self._tx() as db:
            return self._member_ok(db, lease, self.clock())

    def _shared_renew(self, lease, ttl):
        with self._tx() as db:
            now = self.clock()
            if not self._member_ok(db, lease, now):
                return False
            db.execute('UPDATE members SET expires=? WHERE provider=? AND owner=?', (now + ttl, lease['provider'], lease['owner']))
            self._refresh(db, lease['provider'], now)
            lease['expires'] = now + ttl
            return True

    def _shared_release(self, lease):
        with self._tx() as db:
            now = self.clock()
            if not self._member_ok(db, lease, now):
                return False
            db.execute('DELETE FROM members WHERE provider=? AND owner=?', (lease['provider'], lease['owner']))
            self._refresh(db, lease['provider'], now)
            return True

    def holder(self, provider):
        with self._tx() as db:
            return self._active(db, provider, self.clock())

    def holders(self):
        now = self.clock()
        rows = self._db().execute('SELECT provider,owner,kind,priority,fence,acquired,expires FROM leases').fetchall()
        return [self._lease(r) for r in rows if r[6] > now]
