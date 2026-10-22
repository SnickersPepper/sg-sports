"""Phase 7 DVR: start-from-beginning recordings for three supported teams, last three each.

Everything here is derived from the canonical event the Phase 3-5 pipeline already built
(tv.attach_live_events + verification.annotate). Nothing in this module contacts a provider:
the caller injects the media source and the provider-slot budget, so the same code is testable
with temporary storage and fake sources. It never invents a live status; a recording is a file
with an honest state, not a claim that the game is airing.
"""
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

import guide

KEEP = 3                          # latest recordings retained per supported team/sport bucket
PREROLL_SECONDS = 10 * 60         # provisional capture may start this long before the scheduled start
POST_ROLL_SECONDS = 3 * 60        # capture continues this long after the game is final
SCHEDULE_GRACE = 15 * 60          # a game that has not gone live this long after its start is abandoned
VERIFY_MIN_BYTES = 2 << 20        # live bytes needed before content verification is attempted
VERIFY_MAX_BYTES = 8 << 20
VERIFY_ATTEMPTS = 5
VERIFY_DEADLINE = 15 * 60         # live but unconfirmed this long => discarded
MAX_RECORDING_BYTES = 8 << 30     # per-recording cap
MIN_FREE_BYTES = 20 << 30         # disk space that is never given to the DVR
MAX_SECONDS = 5 * 3600
MAX_RECONNECTS = 3
TS_PACKET = 188
RECHECK_SECONDS = 60

# Exactly three buckets. Identity is the ESPN team id in the matched live record, never a name.
BUCKETS = {
    'tennessee-football': {'label': 'Tennessee football', 'sport': 'Football',
                           'leagues': ('NCAA',), 'espn_team_id': '2633', 'team_key': 'tennessee'},
    'tennessee-softball': {'label': 'Tennessee softball', 'sport': 'Softball',
                           'leagues': ('NCAA',), 'espn_team_id': '611', 'team_key': 'tennessee'},
    'fc-barcelona': {'label': 'FC Barcelona', 'sport': 'Soccer',
                     'leagues': ('La Liga', 'UEFA Champions League'), 'espn_team_id': '83',
                     'team_key': 'barcelona'},
}
NOT_MENS = ('women', 'femen', 'feminin', 'femení', 'lady', 'ladies', ' b ', 'juvenil', 'youth')

STATES = ('provisional', 'recording', 'ready', 'failed', 'expired')


def _text(value):
    return str(value if value is not None else '')


def _agrees(guide_key, espn_name):
    """A guide side names an ESPN side: same canonical key, or every word of it is in ESPN's name."""
    folded = guide.team_key(_text(espn_name))
    return guide_key == folded or set(guide_key.split()) <= set(folded.split())


def _sides_agree(event, live, spec):
    """The listing's two named teams map one-to-one onto ESPN's sides, our team on our ESPN id."""
    sides = guide.participant_sides(event)
    if not sides or spec['team_key'] not in sides or sides[0] == sides[1]:
        return False
    ours = spec['team_key']
    other = sides[1] if sides[0] == ours else sides[0]
    home_is_ours = _text(live.get('home_espn_id')) == spec['espn_team_id']
    ours_name, other_name = (live.get('home'), live.get('away')) if home_is_ours else (live.get('away'), live.get('home'))
    if ours == other or not _agrees(ours, ours_name) or not _agrees(other, other_name):
        return False
    return not any((set(sides) & group) != ({guide.team_key(_text(ours_name)), guide.team_key(_text(other_name))} & group)
                   for group in guide.COLLISION_GROUPS)


def bucket_for(event, preroll=False):
    """Return (bucket_id, reason). Unambiguous, non-replay truth only: live, or (preroll) pregame before kickoff."""
    if not isinstance(event, dict) or event.get('is_game') is not True:
        return None, 'not_a_game'
    live = event.get('live') or {}
    phase = event.get('event_phase')
    if preroll:
        if phase != 'pregame' or live.get('state') != 'pre' or live.get('verified_live'):
            return None, 'not_pregame'
    elif phase != 'live' or live.get('verified_live') is not True or live.get('state') != 'in':
        return None, 'not_verified_live'
    if event.get('replay') or event.get('repeat') or live.get('likely_replay') or live.get('completed'):
        return None, 'replay_risk'
    espn_id = _text(live.get('espn_id'))
    if not espn_id or espn_id == 'None':
        return None, 'no_event_identity'
    ids = (_text(live.get('home_espn_id')), _text(live.get('away_espn_id')))
    matches = []
    for bucket_id, spec in BUCKETS.items():
        if event.get('sport') != spec['sport'] or event.get('league') not in spec['leagues']:
            continue
        if spec['espn_team_id'] in ids:
            matches.append(bucket_id)
    if not matches:
        return None, 'unsupported_team_or_sport'
    if len(matches) != 1 or ids[0] == ids[1]:
        return None, 'ambiguous_identity'
    bucket_id = matches[0]
    spec = BUCKETS[bucket_id]
    # The guide listing and ESPN must name the same two sides, or the identity is ambiguous.
    if not _sides_agree(event, live, spec):
        return None, 'ambiguous_identity'
    if bucket_id == 'fc-barcelona':
        haystack = ' ' + ' '.join(_text(x).lower() for x in (event.get('title'), live.get('home'), live.get('away'))) + ' '
        if any(word in haystack for word in NOT_MENS):
            return None, 'not_mens_first_team'
    return bucket_id, None


def pick_feed(event):
    """The best feed that the verification pipeline has content-confirmed for this event, or None."""
    for feed in event.get('feeds', []):
        if (feed.get('content_verified') is True and feed.get('transport_verified') is True
                and feed.get('provider_id') and feed.get('stream_id')
                and feed.get('status') != 'unavailable'):
            return feed
    return None


def pick_candidate_feed(event):
    """Pregame candidate: a listed main feed no check has failed or contradicted. Not yet content-confirmed."""
    if (event.get('broadcast_check') or {}).get('status') == 'disagreement':
        return None
    for feed in event.get('feeds', []):
        if (feed.get('provider_id') and feed.get('stream_id') and feed.get('availability') != 'probe_failed'
                and feed.get('status') != 'unavailable' and feed.get('broadcast_kind') != 'alternate'):
            return feed
    return None


def plan_preroll(events, now):
    """Pregame events inside the bounded pre-roll window that may be captured provisionally."""
    chosen, seen = [], set()
    for event in events:
        bucket_id, _ = bucket_for(event, preroll=True)
        if not bucket_id:
            continue
        start = int((event.get('live') or {}).get('event_start') or event.get('start') or 0)
        feed = pick_candidate_feed(event)
        rid = recording_id(bucket_id, event)
        if start and start - PREROLL_SECONDS <= now <= start and feed and rid not in seen:
            seen.add(rid)
            chosen.append((bucket_id, event, feed))
    return chosen


def recording_id(bucket_id, event):
    espn_id = _text((event.get('live') or {}).get('espn_id'))
    return bucket_id + '-' + hashlib.sha256((bucket_id + '|' + espn_id).encode()).hexdigest()[:16]


def plan(events):
    """Pure selection: one (bucket, event, feed) per distinct recording id from canonical events."""
    chosen, seen = [], set()
    for event in events:
        bucket_id, _ = bucket_for(event)
        if not bucket_id:
            continue
        feed = pick_feed(event)
        rid = recording_id(bucket_id, event)
        if feed and rid not in seen:
            seen.add(rid)
            chosen.append((bucket_id, event, feed))
    return chosen


class Capacity:
    """Free-space probe. Any failure to know the answer is reported as unknown (fail closed)."""

    def __init__(self, root, usage=shutil.disk_usage):
        self.root, self.usage = Path(root), usage

    def free(self):
        try:
            return int(self.usage(self.root).free)
        except Exception:
            return None


class Store:
    """One directory per recording: meta.json (state) and media.ts (MPEG-TS bytes)."""

    def __init__(self, root, capacity=None, keep=KEEP, max_bytes=MAX_RECORDING_BYTES, min_free=MIN_FREE_BYTES, clock=time.time):
        self.root = Path(root)
        self.keep, self.max_bytes, self.min_free, self.clock = keep, max_bytes, min_free, clock
        self.capacity = capacity or Capacity(self.root)
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, rid):
        if not rid or '/' in rid or '\\' in rid or rid.startswith('.'):
            raise ValueError('Recording not found.')
        return self.root / rid

    def media_path(self, rid):
        return self.dir(rid) / 'media.ts'

    def read(self, rid):
        try:
            meta = json.loads((self.dir(rid) / 'meta.json').read_text())
            return meta if isinstance(meta, dict) and meta.get('state') in STATES else None
        except (OSError, ValueError):
            return None

    def write(self, meta):
        path = self.dir(meta['id'])
        path.mkdir(parents=True, exist_ok=True)
        tmp = path / 'meta.json.tmp'
        tmp.write_text(json.dumps(meta, sort_keys=True))
        os.replace(tmp, path / 'meta.json')

    def all(self):
        found = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir():
                meta = self.read(child.name)
                if meta:
                    found.append(meta)
        return found

    @staticmethod
    def order(meta):
        """Newest game first; created time and id make ties deterministic."""
        return (-int(meta.get('event_start') or 0), -int(meta.get('created_at') or 0), meta['id'])

    def holding_media(self, meta):
        """Only validated, playable media counts: provisional captures never do."""
        return (meta['state'] == 'recording' and meta.get('playable') is True) or (meta['state'] == 'ready' and meta.get('bytes', 0) > 0)

    def bucket(self, bucket_id):
        return sorted((m for m in self.all() if m.get('bucket') == bucket_id), key=self.order)

    def enforce(self, bucket_id):
        """Keep the newest `keep` recordings that hold media; evict the rest deterministically."""
        with self.lock:
            retained = [m for m in self.bucket(bucket_id) if self.holding_media(m)]
            for meta in retained[self.keep:]:
                self.expire(meta['id'], 'evicted')
            # Tombstones and no-media failures never accumulate: keep only the newest `keep`.
            marks = [m for m in self.bucket(bucket_id) if not self.holding_media(m) and m['state'] != 'provisional']
            for meta in marks[self.keep:]:
                shutil.rmtree(self.dir(meta['id']), ignore_errors=True)

    def discard(self, rid, reason, meta=None):
        """Quarantined capture is removed; only an honest failed marker with no media remains."""
        with self.lock:
            meta = meta or self.read(rid) or {'id': rid}
            try:
                self.media_path(rid).unlink()
            except OSError:
                pass
            meta.update(state='failed', reason=reason, bytes=0, playable=False, complete_start=False, complete_end=False,
                        updated_at=int(self.clock()))
            meta.pop('sha256', None)
            self.write(meta)
            return meta

    def check_media(self, meta, full=False):
        """(ok, reason): size matches, TS sync bytes at head and tail, and (full) the recorded SHA-256."""
        path = self.media_path(meta['id'])
        try:
            size = path.stat().st_size
        except OSError:
            return False, 'media_missing'
        if size != meta.get('bytes') or size % TS_PACKET or not size:
            return False, 'integrity_failed'
        try:
            with open(path, 'rb') as handle:
                if handle.read(1) != b'\x47':
                    return False, 'integrity_failed'
                handle.seek(size - TS_PACKET)
                if handle.read(1) != b'\x47':
                    return False, 'integrity_failed'
                if full and meta.get('sha256'):
                    handle.seek(0)
                    digest = hashlib.sha256()
                    for block in iter(lambda: handle.read(1 << 20), b''):
                        digest.update(block)
                    if digest.hexdigest() != meta['sha256']:
                        return False, 'integrity_failed'
        except OSError:
            return False, 'media_missing'
        return True, None

    def expire(self, rid, reason):
        with self.lock:
            meta = self.read(rid)
            if not meta:
                return
            try:
                self.media_path(rid).unlink()
            except OSError:
                pass
            meta.update(state='expired', reason=reason, bytes=0, playable=False, updated_at=int(self.clock()))
            meta.pop('sha256', None)
            self.write(meta)

    def recover(self):
        """Startup reconciliation after a restart or crash. Idempotent."""
        with self.lock:
            for child in sorted(self.root.iterdir()):
                if not child.is_dir():
                    continue
                for stale in child.glob('*.tmp'):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
                meta = self.read(child.name)
                if not meta:
                    shutil.rmtree(child, ignore_errors=True)   # orphan or unreadable
                    continue
                media = child / 'media.ts'
                size = media.stat().st_size if media.exists() else 0
                state = meta['state']
                if state == 'provisional':        # a quarantined capture can never be confirmed after a restart
                    meta = self.discard(meta['id'], 'interrupted', meta)
                elif state in ('failed', 'expired'):
                    if size:
                        media.unlink()
                    meta['bytes'] = 0
                elif state == 'recording':        # no recorder survives a restart: keep the validated prefix
                    if size % TS_PACKET:
                        with open(media, 'r+b') as handle:
                            size -= size % TS_PACKET
                            handle.truncate(size)
                    meta.update(bytes=size)
                    meta.pop('sha256', None)
                    if size and meta.get('playable') is True and self.check_media(meta)[0]:
                        meta.update(state='ready', reason='interrupted', complete_end=False, playable=True)
                    else:
                        self.discard(meta['id'], 'interrupted', meta)
                        meta = self.read(meta['id'])
                elif state == 'ready':
                    good, why = self.check_media(meta)
                    if not good:
                        self.expire(meta['id'], why)
                        meta = self.read(meta['id'])
                meta['updated_at'] = int(self.clock())
                self.write(meta)
            for bucket_id in BUCKETS:
                self.enforce(bucket_id)

    def admit(self, bucket_id, rid):
        """(ok, reason). Fail closed on unknown capacity; account for the eviction the new item causes."""
        with self.lock:
            existing = self.read(rid)
            if existing and (existing['state'] in ('recording', 'provisional') or (existing['state'] == 'ready' and existing.get('bytes'))):
                return False, 'already_recorded'
            if existing and existing.get('attempts', 0) >= 3:
                return False, 'too_many_attempts'
            free = self.capacity.free()
            if free is None:
                return False, 'capacity_unknown'
            retained = [m for m in self.bucket(bucket_id) if self.holding_media(m) and m['id'] != rid]
            reclaim = sum(m.get('bytes', 0) for m in retained[self.keep - 1:])
            if free + reclaim - self.min_free < self.max_bytes:
                return False, 'storage_pressure'
            return True, None

    def view(self, meta):
        """The fields the TV may see. No stream ids, hosts or paths."""
        bucket = BUCKETS.get(meta.get('bucket'), {})
        return {k: meta.get(k) for k in (
            'id', 'bucket', 'title', 'event_start', 'state', 'reason', 'complete_start', 'complete_end',
            'bytes', 'duration_seconds', 'started_at', 'updated_at', 'provider_id', 'playable')} | {
            'team': bucket.get('label', '')}

    def listing(self):
        """All recordings per bucket, newest first, plus an honest overall storage verdict."""
        with self.lock:
            free = self.capacity.free()
            pressure = 'unknown' if free is None else ('pressure' if free - self.min_free < self.max_bytes else 'ok')
            buckets = {b: [self.view(m) for m in self.bucket(b) if m['state'] != 'provisional'] for b in BUCKETS}
            return {'buckets': [{'id': b, 'label': BUCKETS[b]['label'], 'recordings': buckets[b]} for b in BUCKETS],
                    'storage': pressure}


def _complete_start(started_at, event_start):
    """Zero tolerance: "from the beginning" only if capture began at or before ESPN's start."""
    return bool(event_start) and started_at <= event_start


def _truth(status, default='live'):
    value = status() if status else {'phase': default}
    if isinstance(value, str):
        value = {'phase': value if value in ('pre', 'live', 'final', 'postponed') else 'gone'}
    return value if isinstance(value, dict) else {'phase': 'gone'}


def record(store, bucket_id, event, feed, source, budget, status=None, stop=None, sleep=time.sleep,
           provisional=False, verify=None):
    """Capture one event. Returns the final meta.

    source():  context manager yielding an iterator of byte chunks (raises on failure).
    budget:    object with acquire(provider_id, owner)->bool and release(provider_id, owner).
    status():  canonical truth, re-evaluated periodically: 'pre'|'live'|'final'|'postponed'|'gone' or a
               dict {'phase', 'event_start'} (event_start follows schedule changes).
    verify(path, offset, length): 'confirmed'|'wrong'|'unknown' for a provisional capture, taken from
               the DVR's own bytes so verification needs no second provider slot.
    stop:      threading.Event for cancellation.
    A provisional (pre-roll) capture is quarantined: never playable, never counted for retention and
    never listed until the same bytes are content-confirmed after the game goes live. The provider
    slot is acquired before anything else and released on every exit path.
    """
    stop = stop or threading.Event()
    rid = recording_id(bucket_id, event)
    pid = _text(feed.get('provider_id'))
    owner = 'dvr:' + rid
    ok, reason = store.admit(bucket_id, rid)
    if not ok:
        return {'id': rid, 'state': 'failed', 'reason': reason, 'admitted': False}
    if not budget.acquire(pid, owner):
        return {'id': rid, 'state': 'failed', 'reason': 'provider_busy', 'admitted': False}
    now = int(store.clock())
    live = event.get('live') or {}
    previous = store.read(rid) or {}
    event_start = int(live.get('event_start') or event.get('start') or 0)
    meta = {'id': rid, 'bucket': bucket_id, 'espn_id': _text(live.get('espn_id')), 'title': _text(event.get('title')),
            'event_start': event_start, 'created_at': now,
            'started_at': now, 'updated_at': now, 'state': 'provisional' if provisional else 'recording', 'reason': None,
            'complete_start': False if provisional else _complete_start(now, event_start),
            'complete_end': False, 'bytes': 0, 'duration_seconds': 0, 'provider_id': pid, 'playable': False,
            'attempts': int(previous.get('attempts', 0)) + 1}
    try:
        with store.lock:
            shutil.rmtree(store.dir(rid), ignore_errors=True)
            store.write(meta)
        return _capture(store, meta, source, status, stop, sleep, verify)
    finally:
        budget.release(pid, owner)


def _capture(store, meta, source, status, stop, sleep, verify=None):
    rid, started = meta['id'], time.monotonic()
    committed, last_check, reconnects, end = False, started, 0, None
    media = store.media_path(rid)
    pending = b''
    digest = hashlib.sha256()
    live_offset = live_seen = final_deadline = None
    attempts = 0

    def flush():
        nonlocal committed
        meta['updated_at'] = int(store.clock())
        meta['duration_seconds'] = int(time.monotonic() - started)
        meta['playable'] = meta['state'] != 'provisional' and meta['bytes'] > 0
        store.write(meta)
        if meta['playable'] and not committed:
            committed = True
            store.enforce(meta['bucket'])   # first validated media: now evict the oldest, never earlier

    def checkpoint():
        """Re-evaluate canonical truth; returns an end tuple or None."""
        nonlocal live_offset, live_seen, final_deadline, attempts
        flush()
        free = store.capacity.free()
        if free is None or free < store.min_free:
            return ('stopped', 'storage_pressure')
        truth = _truth(status)
        phase, now = truth.get('phase'), int(store.clock())
        if truth.get('event_start'):
            meta['event_start'] = int(truth['event_start'])     # follow schedule changes
        start = meta['event_start']
        if meta['state'] == 'provisional':
            if phase == 'postponed':
                return ('discard', 'schedule_changed')
            if phase in ('gone', 'final'):
                return ('discard', 'unverified')
            if phase == 'pre':
                if start and start - meta['started_at'] > PREROLL_SECONDS:
                    return ('discard', 'schedule_changed')      # pre-roll is bounded
                if start and now > start + SCHEDULE_GRACE:
                    return ('discard', 'not_started')
                return None
            if live_offset is None:
                live_offset, live_seen = meta['bytes'], now
            if now - live_seen > VERIFY_DEADLINE:
                return ('discard', 'unverified')
            have = meta['bytes'] - live_offset
            if have >= VERIFY_MIN_BYTES and attempts < VERIFY_ATTEMPTS:
                attempts += 1
                try:
                    verdict = verify(media, live_offset, min(have, VERIFY_MAX_BYTES)) if verify else 'unknown'
                except Exception:
                    verdict = 'unknown'
                if verdict == 'wrong':
                    return ('discard', 'wrong_content')
                if verdict == 'confirmed':
                    meta.update(state='recording', complete_start=_complete_start(meta['started_at'], start), verified_at=now)
                    flush()
                elif attempts >= VERIFY_ATTEMPTS:
                    return ('discard', 'unverified')
            return None
        if phase == 'final':
            if final_deadline is None:
                final_deadline = now + POST_ROLL_SECONDS         # bounded post-roll
        elif phase != 'live':
            return ('stopped', 'event_unconfirmed')
        return None

    try:
        with open(media, 'ab') as out:
            while end is None:
                try:
                    with source() as chunks:
                        for chunk in chunks:
                            if stop.is_set():
                                end = ('stopped', 'cancelled')
                                break
                            pending += chunk
                            usable = len(pending) - len(pending) % TS_PACKET
                            if usable:
                                out.write(pending[:usable]); out.flush()
                                digest.update(pending[:usable])
                                meta['bytes'] += usable
                                pending = pending[usable:]
                                if not committed and meta['state'] != 'provisional':
                                    flush()
                            if meta['bytes'] >= store.max_bytes:
                                end = ('stopped', 'storage_pressure'); break
                            if time.monotonic() - started > MAX_SECONDS:
                                end = ('stopped', 'max_duration'); break
                            if final_deadline is not None and int(store.clock()) >= final_deadline:
                                end = ('final', None); break
                            if time.monotonic() - last_check >= RECHECK_SECONDS:
                                last_check = time.monotonic()
                                end = checkpoint()
                                if end:
                                    break
                except Exception:
                    pass           # a failed open/read is handled as a source end below
                if end is not None:
                    break
                if stop.is_set():
                    end = ('stopped', 'cancelled'); break
                truth = _truth(status, 'final')
                phase = truth.get('phase')
                if phase == 'final' and meta['state'] != 'provisional':
                    end = ('final', None)
                elif phase in ('live', 'pre') and reconnects < MAX_RECONNECTS:
                    reconnects += 1
                    sleep(min(5 * reconnects, 15))     # the held provider slot is reused, never doubled
                elif meta['state'] == 'provisional':
                    end = ('discard', 'schedule_changed' if phase == 'postponed' else 'unverified')
                else:
                    end = ('stopped', 'source_ended_early' if meta['bytes'] else 'provider_unavailable')
    except OSError:
        end = ('stopped', 'storage_pressure')
    except Exception:
        end = ('stopped', 'provider_unavailable')

    kind, reason = end
    if meta['state'] == 'provisional' and kind != 'discard':
        kind, reason = 'discard', ('cancelled' if reason == 'cancelled' else 'unverified')   # unverified is never retained
    if kind == 'discard':
        return store.discard(rid, reason, meta)
    if not meta['bytes']:
        meta.update(state='failed', reason=reason or 'provider_unavailable', playable=False)
    else:
        meta.update(state='ready', reason=reason, complete_end=(kind == 'final'), playable=True, sha256=digest.hexdigest())
    meta['updated_at'] = int(store.clock())
    meta['duration_seconds'] = int(time.monotonic() - started)
    store.write(meta)
    if meta['state'] == 'ready':
        good, why = store.check_media(meta)
        if not good:
            store.expire(rid, why)
            return store.read(rid)
    store.enforce(meta['bucket'])
    return meta


class Budget:
    """Backend provider-slot budget shared by playback sessions, verification and DVR.

    `busy(pid)` is supplied by the owner of the other users of the slot (tv.py: live sessions,
    streaming relays and verification samples); this class only guarantees one DVR hold at a time
    per provider and that the check and the claim are atomic under `guard`.
    """

    def __init__(self, guard, busy, limit=1):
        self.guard, self.busy, self.limit = guard, busy, limit
        self.holds = {}

    def acquire(self, provider_id, owner):
        with self.guard:
            if self.busy(provider_id) or len(self.holds.get(provider_id, ())) >= self.limit:
                return False
            self.holds.setdefault(provider_id, set()).add(owner)
            return True

    def release(self, provider_id, owner):
        with self.guard:
            owners = self.holds.get(provider_id)
            if owners:
                owners.discard(owner)
                if not owners:
                    self.holds.pop(provider_id, None)

    def held(self, provider_id):
        with self.guard:
            return len(self.holds.get(provider_id, ()))
