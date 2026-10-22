"""Phase 7 Jellyfin DVR: SG Sports-owned policy over Jellyfin's timers and recordings.

SG Sports decides *what* is recorded (canonical event identity, the three supported buckets,
replay and wrong-content safeguards, conflicts); Jellyfin owns capture, timers, storage and media
playback. This module holds the durable event-to-timer/item ledger and every decision that touches
Jellyfin objects, and it only ever touches objects the ledger proves SG Sports created.

Nothing here contacts a provider. Jellyfin ingests only through the SG Sports relay, which tv.py
serves and which must hold a fenced lease (lease.py) for the whole time bytes flow.
"""
import contextlib
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from xml.sax.saxutils import escape

import dvr
import jellyfin
from dvr import BUCKETS, PREROLL_SECONDS, POST_ROLL_SECONDS, KEEP

HORIZON_SECONDS = 8 * 3600            # events further ahead than this are not scheduled yet
RELAY_SLACK_SECONDS = 5 * 60          # relay accepts a connection this long outside the padded window
RESCHEDULE_TOLERANCE = 60             # schedule moves smaller than this are ignored
MATCH_TOLERANCE = 120                 # program start vs event start
GUIDE_REFRESH_MIN_SECONDS = 120
SCAN_MIN_SECONDS = 30                # library-scan rate limit while waiting for a finished recording to be indexed
SCAN_GRACE_SECONDS = 300             # how long after the window a missing item may still be indexing
GUIDE_SETTLE_SECONDS = 3              # Jellyfin's guide refresh runs as an async task
CHECK_CACHE_SECONDS = 60
DURATION = {'Football': 4 * 3600, 'Softball': 3 * 3600, 'Soccer': 2 * 3600 + 15 * 60}
PRIORITY = ('tennessee-football', 'fc-barcelona', 'tennessee-softball')   # conflict order, highest first
LIVE_STATES = ('pending', 'scheduled', 'recording')
TERMINAL = ('ready', 'failed', 'expired', 'cancelled', 'conflict')


def tag(rid):
    """Unique marker carried in our programme titles, so Jellyfin's recording path/name proves which event a file belongs to."""
    return 'SGS-' + str(rid).rsplit('-', 1)[-1]


def channel_name(rid):
    return 'SG Sports ' + rid


class Ledger:
    """Durable SQLite ledger keyed by canonical SG Sports recording id (bucket + ESPN event id)."""

    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))
        self.lock = threading.RLock()
        with self._open() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entries (rid TEXT PRIMARY KEY, data TEXT NOT NULL)')

    def _open(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def get(self, rid):
        with self.lock, self._open() as db:
            row = db.execute('SELECT data FROM entries WHERE rid=?', (rid,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with self.lock, self._open() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM entries ORDER BY rid')]

    def put(self, entry):
        with self.lock, self._open() as db:
            db.execute('INSERT OR REPLACE INTO entries(rid,data) VALUES(?,?)', (entry['id'], json.dumps(entry, sort_keys=True)))
        return entry

    def update(self, rid, **fields):
        with self.lock:
            entry = self.get(rid)
            if entry is None:
                return None
            entry.update(fields)
            return self.put(entry)


def sign(relay_key, purpose):
    return hmac.new(relay_key.encode(), purpose.encode(), hashlib.sha256).hexdigest()[:32]


def token_ok(relay_key, purpose, presented):
    return bool(relay_key) and hmac.compare_digest(sign(relay_key, purpose), str(presented))


def window(entry):
    """(capture_start, capture_end) including padding."""
    return entry['event_start'] - entry['pre_seconds'], entry['event_end'] + entry['post_seconds']


class JellyfinDvr:
    def __init__(self, client, ledger, leases, clock=time.time, retention_delete=False):
        self.client, self.ledger, self.leases, self.clock = client, ledger, leases, clock
        self.retention_delete = retention_delete
        self.lock = threading.RLock()
        self._check = (0, None)
        self._last_refresh = 0

    # --- capability -------------------------------------------------------------------------
    def capability(self, force=False):
        at = self.clock()
        if not force and self._check[1] and at - self._check[0] < CHECK_CACHE_SECONDS:
            return self._check[1]
        result = self.client.check()
        self._check = (at, result)
        return result

    # --- planning (pure over canonical events) ------------------------------------------------
    def candidates(self, events, now):
        """Eligible (rid, bucket, event, feed, start, end) within the horizon, one per recording id."""
        found, seen = [], set()
        for event in events:
            bucket_id, _ = dvr.bucket_for(event)
            if not bucket_id:
                bucket_id, _ = dvr.bucket_for(event, preroll=True)
            if not bucket_id:
                continue
            live = event.get('live') or {}
            start = int(live.get('event_start') or event.get('start') or 0)
            feed = dvr.pick_feed(event) or dvr.pick_candidate_feed(event)
            rid = dvr.recording_id(bucket_id, event)
            if not start or not feed or rid in seen or start - PREROLL_SECONDS > now + HORIZON_SECONDS:
                continue
            seen.add(rid)
            end = start + DURATION.get(BUCKETS[bucket_id]['sport'], 3 * 3600)
            found.append({'id': rid, 'bucket': bucket_id, 'espn_id': dvr._text(live.get('espn_id')),
                          'title': dvr._text(event.get('title')), 'event_start': start, 'event_end': end,
                          'provider_id': dvr._text(feed['provider_id']), 'stream_id': dvr._text(feed['stream_id'])})
        return found

    @staticmethod
    def resolve_conflicts(items, existing):
        """Accept non-overlapping windows per provider. In-progress recordings always win; otherwise bucket
        priority, then earlier start, then id. Returns (accepted, rejected-with-reason)."""
        def key(item):
            prior = existing.get(item['id']) or {}
            return (0 if prior.get('state') == 'recording' else 1, PRIORITY.index(item['bucket']), item['event_start'], item['id'])
        accepted, rejected = [], []
        for item in sorted(items, key=key):
            lo, hi = item['event_start'] - PREROLL_SECONDS, item['event_end'] + POST_ROLL_SECONDS
            clash = [a for a in accepted if a['provider_id'] == item['provider_id']
                     and lo < a['event_end'] + POST_ROLL_SECONDS and a['event_start'] - PREROLL_SECONDS < hi]
            (rejected if clash else accepted).append(item)
        return accepted, rejected

    # --- export (what Jellyfin sees: no provider URLs) -----------------------------------------
    def exported(self):
        return [e for e in self.ledger.all() if e['state'] in LIVE_STATES]

    def export_m3u(self, base_url, relay_key):
        lines = ['#EXTM3U']
        for e in self.exported():
            name = channel_name(e['id'])
            lines.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s" group-title="SG Sports DVR",%s' % (e['id'], name, name))
            lines.append('%s/jellyfin/relay/%s/%s' % (base_url.rstrip('/'), e['id'], sign(relay_key, 'relay:' + e['id'])))
        return '\n'.join(lines) + '\n'

    def export_xmltv(self):
        def stamp(epoch):
            return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(epoch))
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="sgsports-dvr">']
        entries = self.exported()
        for e in entries:
            out.append('<channel id="%s"><display-name>%s</display-name></channel>' % (escape(e['id']), escape(channel_name(e['id']))))
        for e in entries:
            out.append('<programme start="%s" stop="%s" channel="%s"><title>%s</title></programme>' % (
                stamp(e['event_start']), stamp(e['event_end']), escape(e['id']), escape('%s %s' % (e['title'] or 'Game', tag(e['id'])))))
        out.append('</tv>')
        return ''.join(out)

    # --- scheduling tick -----------------------------------------------------------------------
    def sync_guide(self):
        """Publish the current guide under a content-versioned path (Jellyfin caches guides per path), then refresh."""
        version = hashlib.sha256(self.export_xmltv().encode()).hexdigest()[:12]
        config = self.client.config
        self.client.set_guide_path('%s/jellyfin/export/%s/guide-%s.xml' % (config.export_url.rstrip('/'), sign(config.relay_key, 'export'), version))
        return self.client.refresh_guide()

    def tick(self, events, now=None):
        """Plan, schedule/update/cancel timers, reconcile. Safe to call repeatedly (idempotent)."""
        now = int(self.clock() if now is None else now)
        with self.lock:
            cap = self.capability()
            if cap['state'] != 'ready' and not cap.get('awaiting_channels'):   # channels only appear once events are exported
                return {'state': cap['state'], 'detail': cap['detail']}
            existing = {e['id']: e for e in self.ledger.all()}
            accepted, rejected = self.resolve_conflicts(self.candidates(events, now), existing)
            wanted = {a['id'] for a in accepted}
            for item in rejected:
                prior = existing.get(item['id'])
                if prior and prior['state'] == 'scheduled':
                    self._cancel(prior, 'provider_conflict', now, state='conflict')
                elif not prior or prior['state'] in ('pending', 'conflict'):
                    self.ledger.put({**self._new(item, now), 'state': 'conflict', 'reason': 'provider_conflict'})
            for item in accepted:
                prior = existing.get(item['id'])
                if prior is None or prior['state'] == 'cancelled' or (prior['state'] == 'conflict' and prior.get('reason') == 'provider_conflict'):
                    self.ledger.put(self._new(item, now))
                elif prior['state'] in ('pending', 'scheduled'):
                    self._reschedule(prior, item, now)
            for prior in existing.values():       # no longer eligible: postponed, replay, vanished from the guide
                if prior['state'] in ('pending', 'scheduled') and prior['id'] not in wanted and prior['id'] not in {r['id'] for r in rejected}:
                    self._cancel(prior, 'no_longer_eligible', now, state='cancelled')
            self._map_and_schedule(now)
            self.reconcile(now)
            self.retain(now)
            return {'state': cap['state'], 'detail': cap['detail']}

    def _new(self, item, now):
        return {**item, 'state': 'pending', 'reason': None, 'namespace': jellyfin.NAMESPACE, 'schema': 1,
                'pre_seconds': PREROLL_SECONDS, 'post_seconds': POST_ROLL_SECONDS, 'jf_channel_id': None,
                'jf_program_id': None, 'jf_timer_id': None, 'jf_item_id': None, 'content': 'unverified',
                'first_byte_at': None, 'complete_start': False, 'complete_end': False, 'bytes': 0, 'duration_seconds': 0,
                'created_at': now, 'updated_at': now, 'started_at': None, 'relay_done': False, 'ownership': {}}

    def _reschedule(self, prior, item, now):
        moved = abs(item['event_start'] - prior['event_start']) >= RESCHEDULE_TOLERANCE or abs(item['event_end'] - prior['event_end']) >= RESCHEDULE_TOLERANCE
        if not moved:
            return
        fields = {'event_start': item['event_start'], 'event_end': item['event_end'], 'updated_at': now, 'schedule_changed': True}
        if prior.get('jf_timer_id'):
            # A real Jellyfin timer follows its guide program (its update only changes padding), so a moved game gets the
            # old timer cancelled and a new one on the program at the new time once the guide carries it.
            try:
                self.client.cancel_timer(prior['jf_timer_id'])
                fields.update(state='pending', reason=None, jf_timer_id=None, jf_program_id=None, ownership={})
            except jellyfin.JellyfinError as error:
                fields.update(state='conflict', reason='update_failed:' + error.state)
        self.ledger.update(prior['id'], **fields)

    def _cancel(self, entry, reason, now, state='cancelled'):
        """Cancel only a timer this ledger created. A failure leaves a conflict rather than guessing."""
        if entry.get('jf_timer_id'):
            try:
                self.client.cancel_timer(entry['jf_timer_id'])
            except jellyfin.JellyfinError as error:
                self.ledger.update(entry['id'], state='conflict', reason='cancel_failed:' + error.state, updated_at=now)
                return
        self.leases.cancel_owner('dvr:' + entry['id'])
        self.ledger.update(entry['id'], state=state, reason=reason, updated_at=now)

    def _needs_scan(self, now):
        if now - getattr(self, '_last_scan', 0) < SCAN_MIN_SECONDS:
            return False
        return any(e['state'] in ('scheduled', 'recording') and not e.get('jf_item_id') and now > e['event_end'] for e in self.ledger.all())

    def _programs_for(self, channel, entry):
        return [p for p in self.client.programs([channel['Id']], entry['event_start'], entry['event_end'])
                if p.get('ChannelId') == channel['Id'] and abs(jellyfin.parse_iso(p.get('StartDate')) - entry['event_start']) <= MATCH_TOLERANCE]

    def _map_and_schedule(self, now):
        """Find each pending entry's Jellyfin channel/program and create its timer; report missing mapping."""
        pending = [e for e in self.ledger.all() if e['state'] == 'pending']
        if not pending:
            return
        by_name = {c.get('Name'): c for c in self.client.channels()}
        if any(channel_name(e['id']) not in by_name for e in pending) and now - self._last_refresh >= GUIDE_REFRESH_MIN_SECONDS:
            self._last_refresh = now
            self.sync_guide()
            by_name = {c.get('Name'): c for c in self.client.channels()}
        for entry in pending:
            channel = by_name.get(channel_name(entry['id']))
            if not channel:
                self.ledger.update(entry['id'], reason='mapping_missing', updated_at=now)
                continue
            programs = self._programs_for(channel, entry)
            if not programs and now - self._last_refresh >= GUIDE_REFRESH_MIN_SECONDS:
                self._last_refresh = now
                self.sync_guide()
                time.sleep(GUIDE_SETTLE_SECONDS)
                programs = self._programs_for(channel, entry)
            if len(programs) != 1:
                self.ledger.update(entry['id'], reason='mapping_missing' if not programs else 'ambiguous_program', updated_at=now)
                continue
            program = programs[0]
            self.client.create_timer(channel['Id'], program['Id'], entry['pre_seconds'], entry['post_seconds'])
            ours = [t for t in self.client.timers() if t.get('ProgramId') == program['Id'] and t.get('ChannelId') == channel['Id']]
            if len(ours) != 1:
                self.ledger.update(entry['id'], state='conflict', reason='timer_ambiguous', updated_at=now)
                continue
            self.ledger.update(entry['id'], state='scheduled', reason=None, jf_channel_id=channel['Id'], jf_program_id=program['Id'],
                               jf_timer_id=ours[0]['Id'], updated_at=now,
                               ownership={'namespace': jellyfin.NAMESPACE, 'channel_name': channel_name(entry['id']),
                                          'timer_id': ours[0]['Id'], 'program_id': program['Id']})

    # --- reconciliation ----------------------------------------------------------------------
    def reconcile(self, now=None):
        """Idempotent. Timers/items that vanished or changed outside SG Sports become conflicts, never recreations."""
        now = int(self.clock() if now is None else now)
        timers = {t.get('Id'): t for t in self.client.timers()}
        if self._needs_scan(now):
            self._last_scan = now
            with contextlib.suppress(jellyfin.JellyfinError):
                self.client.scan_library()
        items = self.client.recordings()
        for entry in self.ledger.all():
            if entry['state'] not in ('scheduled', 'recording') or not entry.get('jf_timer_id'):
                continue
            timer = timers.get(entry['jf_timer_id'])
            matches = [i for i in items if tag(entry['id']) in str(i.get('Path') or '')]
            if len(matches) > 1:
                self.ledger.update(entry['id'], state='conflict', reason='ambiguous_item', updated_at=now)
                continue
            item = matches[0] if matches else None
            status = (timer or {}).get('Status')
            if item:
                self._adopt_item(entry, item, now)
                entry = self.ledger.get(entry['id'])
                if item.get('Status') == 'InProgress' or status == 'InProgress':
                    if entry['state'] != 'recording':
                        self.ledger.update(entry['id'], state='recording', started_at=entry.get('started_at') or now, updated_at=now)
                    continue
                self._finalize(entry, item, now)
            elif timer is None:
                if now > window(entry)[1] + SCAN_GRACE_SECONDS:
                    self.ledger.update(entry['id'], state='failed', reason='no_recording_produced', updated_at=now)
                else:
                    self.ledger.update(entry['id'], state='conflict', reason='timer_missing', updated_at=now)
            elif status == 'Cancelled':
                self.ledger.update(entry['id'], state='conflict', reason='timer_cancelled_externally', updated_at=now)
            elif status in ('Error', 'ConflictedNotOk'):
                self.ledger.update(entry['id'], state='failed', reason='recording_error', updated_at=now)
            elif status == 'Completed' and now > window(entry)[1] + SCAN_GRACE_SECONDS:
                self.ledger.update(entry['id'], state='failed', reason='no_recording_produced', updated_at=now)

    def _adopt_item(self, entry, item, now):
        if entry.get('jf_item_id') not in (None, item.get('Id')):
            self.ledger.update(entry['id'], state='conflict', reason='item_changed', updated_at=now)
            return
        ownership = {**entry.get('ownership', {}), 'item_id': item.get('Id'), 'item_path': item.get('Path'), 'tag': tag(entry['id'])}
        self.ledger.update(entry['id'], jf_item_id=item.get('Id'), ownership=ownership, updated_at=now)

    def _finalize(self, entry, item, now):
        """A Jellyfin item exists and is no longer recording. Labels stay conservative and rest on SG evidence."""
        size = 0
        for source in item.get('MediaSources') or []:
            size = max(size, int(source.get('Size') or 0))
        if entry['content'] == 'wrong':
            self.ledger.update(entry['id'], state='failed', reason='wrong_content', bytes=size, updated_at=now)
        elif entry['content'] != 'confirmed':
            self.ledger.update(entry['id'], state='failed', reason='unverified', bytes=size, updated_at=now)
        elif not size:
            self.ledger.update(entry['id'], state='failed', reason='no_recording_produced', updated_at=now)
        else:
            first = entry.get('first_byte_at')
            self.ledger.update(entry['id'], state='ready', reason=None, bytes=size, updated_at=now,
                               complete_start=bool(first and first <= entry['event_start']),
                               complete_end=bool(entry.get('relay_final')),
                               duration_seconds=int((item.get('RunTimeTicks') or 0) / 10_000_000))

    # --- retention: independent latest-three per bucket, owned objects only ---------------------
    @staticmethod
    def order(entry):
        return (-int(entry.get('event_start') or 0), -int(entry.get('created_at') or 0), entry['id'])

    def owned_exact(self, entry, item):
        """True only if the ledger proves ownership and the live Jellyfin item still reconciles to it: same item id and
        path as adopted, and the path carries this event's tag (and no other SG event's tag)."""
        own = entry.get('ownership') or {}
        path = str((item or {}).get('Path') or '')
        others = [e['id'] for e in self.ledger.all() if e['id'] != entry['id'] and tag(e['id']) in path]
        return bool(item and own.get('namespace') == jellyfin.NAMESPACE and entry.get('jf_item_id') and item.get('Id') == entry['jf_item_id']
                    and path and own.get('item_path') == path and tag(entry['id']) in path and own.get('tag') == tag(entry['id']) and not others)

    def retain(self, now):
        for bucket_id in BUCKETS:
            ready = sorted((e for e in self.ledger.all() if e['bucket'] == bucket_id and e['state'] == 'ready'), key=self.order)
            for entry in ready[KEEP:]:
                if not self.retention_delete:
                    continue          # default: hidden from the TV, nothing deleted in Jellyfin
                try:
                    item = self.client.item(entry['jf_item_id'])
                except jellyfin.JellyfinError:
                    continue
                if not self.owned_exact(entry, item):
                    self.ledger.update(entry['id'], state='conflict', reason='ownership_unproven', updated_at=now)
                    continue
                try:
                    self.client.delete_recording(entry['jf_item_id'])
                except jellyfin.JellyfinError:
                    continue
                self.ledger.update(entry['id'], state='expired', reason='evicted', bytes=0, updated_at=now)

    # --- relay authorization / evidence ---------------------------------------------------------
    def relay_authorize(self, rid, token, relay_key, now=None):
        """(entry, lease, error). Lease acquisition is the atomic one-upstream gate; everything fails closed."""
        now = int(self.clock() if now is None else now)
        if not token_ok(relay_key, 'relay:' + rid, token):
            return None, None, 'forbidden'
        entry = self.ledger.get(rid)
        if not entry or entry['state'] not in LIVE_STATES or entry.get('relay_done'):
            return None, None, 'not_scheduled'
        lo, hi = window(entry)
        if not lo - RELAY_SLACK_SECONDS <= now <= hi + RELAY_SLACK_SECONDS:
            return None, None, 'outside_window'
        lease = self.leases.acquire(entry['provider_id'], 'dvr:' + rid, 'dvr', ttl=60, exclusive=True, stream_id=str(entry['stream_id']))
        if not lease:
            self.ledger.update(rid, reason='provider_busy', updated_at=now)
            return entry, None, 'provider_busy'
        self.ledger.update(rid, reason=None, updated_at=now)
        return entry, lease, None

    def relay_started(self, rid, now=None):
        entry = self.ledger.get(rid)
        if entry and not entry.get('first_byte_at'):
            self.ledger.update(rid, first_byte_at=int(self.clock() if now is None else now))

    def relay_content(self, rid, verdict):
        """Verification of the relayed bytes: 'confirmed' | 'wrong' | 'unknown'. Wrong is final."""
        entry = self.ledger.get(rid)
        if entry and entry['content'] != 'wrong' and verdict in ('confirmed', 'wrong'):
            self.ledger.update(rid, content=verdict)

    def relay_ended(self, rid, final):
        """Relay stopped (game final plus post-roll, lease lost, or cut). Never re-ingest afterwards."""
        entry = self.ledger.get(rid)
        if entry:
            self.ledger.update(rid, relay_done=bool(final) or entry.get('relay_done'), relay_final=bool(final), updated_at=int(self.clock()))

    def stop(self, rid, now=None):
        """User cancel: a scheduled timer is cancelled; a running relay loses its lease and ends (fail closed)."""
        now = int(self.clock() if now is None else now)
        entry = self.ledger.get(rid)
        if not entry:
            return False
        if entry['state'] in ('pending', 'scheduled'):
            self._cancel(entry, 'cancelled', now)
        elif entry['state'] == 'recording':
            self.leases.cancel_owner('dvr:' + rid)
            self.ledger.update(rid, relay_done=True, updated_at=now)
        return True

    # --- TV listing ---------------------------------------------------------------------------
    def view(self, entry, rank_hidden=False):
        state = entry['state']
        playable = state == 'ready' and not rank_hidden
        reason = entry.get('reason')
        if rank_hidden:
            state, reason = 'expired', 'evicted'
        elif state in ('pending', 'scheduled', 'conflict', 'cancelled'):
            return None
        elif state == 'recording':
            reason = 'recording_in_progress'    # in-progress playback is not demonstrated: never offered
        out = {k: entry.get(k) for k in ('id', 'bucket', 'title', 'event_start', 'complete_start', 'complete_end', 'bytes',
                                          'duration_seconds', 'started_at', 'updated_at', 'provider_id')}
        out.update(state=state, reason=reason, playable=playable, team=BUCKETS.get(entry['bucket'], {}).get('label', ''))
        return out

    def listing(self):
        cap = self.capability()
        entries = self.ledger.all()
        buckets = []
        for bucket_id, spec in BUCKETS.items():
            mine = sorted((e for e in entries if e['bucket'] == bucket_id), key=self.order)
            ready = [e for e in mine if e['state'] == 'ready']
            hidden = {e['id'] for e in ready[KEEP:]}
            views = [v for v in (self.view(e, e['id'] in hidden) for e in mine) if v]
            buckets.append({'id': bucket_id, 'label': spec['label'], 'recordings': views})
        storage = {'ready': 'ok', 'storage_misconfigured': 'unknown'}.get(cap['state'], 'unknown')
        return {'buckets': buckets, 'storage': storage, 'engine': 'jellyfin', 'dvr_state': cap['state'],
                'scheduled': [{'id': e['id'], 'bucket': e['bucket'], 'title': e['title'], 'event_start': e['event_start'], 'state': e['state']}
                              for e in entries if e['state'] in ('pending', 'scheduled')]}
