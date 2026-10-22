"""Official LVBP schedule ingestion: the event foundation for Venezuela LVBP.

Source: the league's own statistics site (stats.lvbp.com, operated with TuJonron). Its schedule
fragment returns two calendar days per request with the visitor/home teams, Caracas wall-clock
time, venue and the per-game broadcaster logos the league publishes. Only public metadata is
read; no media is touched. See evidence/lvbp/SOURCE-SELECTION.md for the schema and reuse notes.

The schedule only establishes that a game exists. Broadcaster assignment, provider mapping and
stream health live in lvbp_broadcast.py / verification.py and are never inferred here.
External markup is untrusted data: anything that does not match the captured schema raises
SchemaError and the refresh fails closed, keeping the last-known-good schedule.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

LEAGUE = 'Venezuela LVBP'
SPORT = 'Baseball'
SOURCE_ID = 'stats.lvbp.com'
SOURCE_URL = 'https://stats.lvbp.com/resources/views/schedule.php'
SOURCE_TIMEZONE = 'America/Caracas'
CACHE = 'lvbp-schedule'
DATA = json.loads((Path(__file__).parent / 'lvbp_data.json').read_text(encoding='utf-8'))

REFRESH_SECONDS = 6 * 3600       # normal cadence; the source is a league statistics site
BACKOFF_BASE = 15 * 60           # first retry after a failure, doubling up to REFRESH_SECONDS
FRESH_SECONDS = 8 * 3600         # newer than this is "fresh"
STALE_LIMIT = 7 * 86400          # last-known-good older than this is no longer shown at all
WINDOW_DAYS = 12                 # calendar days fetched per refresh (two days per request)
PAGE_LIMIT = 6                   # hard cap on requests per refresh
MAX_PAGE_BYTES = 512 * 1024
GAME_LENGTH = 4 * 3600           # display-only estimate of the guide slot
UNRESOLVED_AFTER = 6 * 3600      # a still-"scheduled" game this long after first pitch is unresolved
STATUSES = ('scheduled', 'in_progress', 'final', 'postponed', 'suspended', 'canceled', 'rescheduled', 'unknown')
UPCOMING_STATUSES = ('scheduled', 'in_progress', 'postponed', 'suspended', 'canceled', 'unknown')


class SchemaError(ValueError):
    """The source no longer looks like the captured schema."""


def _fold(value):
    folded = unicodedata.normalize('NFKD', str(value or '').casefold()).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', folded).split())


TEAMS = {t['id']: t for t in DATA['teams']}
_ALIASES = {}
for _team in DATA['teams']:
    for _alias in (_team['name'], *_team['aliases']):
        _ALIASES[_fold(_alias)] = _team['id']


def team_id(name):
    """Canonical team id for any accented/unaccented/short/long alias, or None."""
    return _ALIASES.get(_fold(name))


def team_league(text):
    """LVBP when a title names two different LVBP clubs (never on one ambiguous nickname)."""
    folded = ' ' + _fold(text) + ' '
    found = {tid for alias, tid in _ALIASES.items() if ' ' + alias + ' ' in folded}
    return LEAGUE if len(found) >= 2 else None


STATUS_ONLY = ('postponed', 'suspended', 'canceled', 'rescheduled')


def anchor(event):
    """Sort/window instant: first pitch when known, else the start of the Caracas calendar day."""
    if event.get('start') is not None:
        return event['start']
    day = dt.date.fromisoformat(event['local_date'])
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=_zone()).timestamp())


def season_for(at):
    """Season label for an instant, e.g. 2026-27. The LVBP season starts in October."""
    local = dt.datetime.fromtimestamp(at, _zone())
    start = local.year if local.month >= 10 else local.year - 1
    return '%d-%02d' % (start, (start + 1) % 100)


def _zone():
    try:
        return ZoneInfo(SOURCE_TIMEZONE)
    except Exception:  # tzdata missing: Caracas has been a fixed UTC-4 with no DST since 2016
        return dt.timezone(dt.timedelta(hours=-4))


# -------------------------------------------------------------------------------- parsing

_MONTHS = {m: i + 1 for i, m in enumerate(
    ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
     'octubre', 'noviembre', 'diciembre'))}
_HEADER = re.compile(r'<span class="game-date">([^<]+)</span>')
_DAY = re.compile(r'<div class="col-12 fw-bold my-2">\s*([^<]+?)\s*</div>')
_DAY_TEXT = re.compile(r'^\S+\s+(\d{1,2})\s+de\s+(\S+)$')
_GAME = re.compile(r'<span class="game">(.*?)</span>', re.S)
_TIME = re.compile(r'<span class="game-time">\s*([^<]*?)\s*</span>')
_VENUE = re.compile(r'<span class="game-status">\s*([^<]*?)\s*</span>')
_SOURCE_GAME = re.compile(r'gameday\.php\?game=(\d+)')
_ALT = re.compile(r'<img[^>]*\balt="([^"]+)"')
_CLOCK = re.compile(r'^(\d{1,2}):([0-5]\d)\s*([AP])\.?M\.?$', re.I)
_SCORE = re.compile(r'^[A-Z]{2,4} \d{1,2}, [A-Z]{2,4} \d{1,2}$')
_NO_GAMES = 'No hay juegos programados'


def _day_date(label, requested):
    match = _DAY_TEXT.match(' '.join(html.unescape(label).split()))
    month = _MONTHS.get(_fold(match.group(2))) if match else None
    if not match or not month:
        raise SchemaError('day heading')
    candidates = []
    for year in (requested.year - 1, requested.year, requested.year + 1):
        try:
            candidates.append(dt.date(year, month, int(match.group(1))))
        except ValueError:
            continue
    best = min(candidates, key=lambda d: abs((d - requested).days), default=None)
    if best is None or abs((best - requested).days) > 3:
        raise SchemaError('day heading outside request')
    return best


def _status_and_time(text, local_date):
    """(status, startUtc-or-None) from the schedule's time cell."""
    text = ' '.join(html.unescape(text).split())
    clock = _CLOCK.match(text)
    if clock:
        hour = int(clock.group(1))
        if not 1 <= hour <= 12:
            raise SchemaError('clock')
        hour = hour % 12 + (12 if clock.group(3).upper() == 'P' else 0)
        local = dt.datetime(local_date.year, local_date.month, local_date.day, hour, int(clock.group(2)), tzinfo=_zone())
        return 'scheduled', int(local.timestamp())
    if _SCORE.match(text):
        return 'final', None
    folded = _fold(text)
    for word, status in (('pospuest', 'postponed'), ('suspendid', 'suspended'), ('cancelad', 'canceled'),
                         ('reprogramad', 'rescheduled'), ('suspend', 'suspended')):
        if word in folded:
            return status, None
    return 'unknown', None


def parse_page(markup, requested, fetched_at):
    """Parse one schedule fragment into row dicts. Raises SchemaError on any unexpected shape."""
    if not isinstance(markup, str) or len(markup) > MAX_PAGE_BYTES or not _HEADER.search(markup):
        raise SchemaError('page header')
    rows = []
    sections = _DAY.split(markup)
    if len(sections) < 3:
        raise SchemaError('day sections')
    for i in range(1, len(sections), 2):
        local_date = _day_date(sections[i], requested)
        body = sections[i + 1]
        items = body.split('<div class="list-group-item">')[1:]
        if not items and _NO_GAMES not in body:
            raise SchemaError('empty section without notice')
        for item in items:
            game = _GAME.search(item)
            if not game and _NO_GAMES in item:
                continue  # the source's own "no games scheduled" row
            if not game:
                raise SchemaError('game cell')
            names = [html.unescape(re.sub(r'<[^>]+>', ' ', part)).strip() for part in re.sub(r'<img[^>]*>', '', game.group(1)).split('@')]
            if len(names) != 2:
                raise SchemaError('matchup')
            away, home = team_id(names[0]), team_id(names[1])
            if not away or not home or away == home:
                raise SchemaError('unknown team')
            time_cell = _TIME.search(item)
            if not time_cell:
                raise SchemaError('time cell')
            status, start = _status_and_time(time_cell.group(1), local_date)
            venue = _VENUE.search(item)
            source_game = _SOURCE_GAME.search(item)
            labels = []
            for label in _ALT.findall(item):
                label = html.unescape(label).strip()
                if label and label not in labels:
                    labels.append(label[:80])
            venue_text = ' '.join(html.unescape(venue.group(1)).split())[:120] if venue else ''
            rows.append({'date': local_date, 'away': away, 'home': home, 'status': status, 'start': start,
                         'venue': venue_text if status != 'final' else '',
                         'source_event_id': source_game.group(1) if source_game else None,
                         'broadcasters_raw': labels, 'time_text': time_cell.group(1).strip()[:40]})
    return rows


def _event_id(season, row, ordinal):
    identity = '|'.join((season, row['away'], row['home'], row['date'].isoformat(), str(ordinal)))
    return 'lvbp-' + hashlib.sha256(identity.encode()).hexdigest()[:20]


def build_events(rows, fetched_at, previous=None):
    """Turn parsed rows into occurrence-aware events.

    Identical rows (same teams, date, time text and venue) are duplicates and collapse. A second
    row for the same teams and date with different content is a real doubleheader game: the
    ordinal is the row's position among that day's games for the pair, so the identity survives a
    time change and never collides.
    """
    previous = previous or {}
    seen, counters, events = set(), {}, {}
    for row in rows:
        fingerprint = (row['date'], row['away'], row['home'], row['time_text'], row['venue'], tuple(row['broadcasters_raw']))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        season = season_for(int(dt.datetime(row['date'].year, row['date'].month, row['date'].day, 12, tzinfo=_zone()).timestamp()))
        key = (row['date'], row['away'], row['home'])
        counters[key] = counters.get(key, 0) + 1
        event_id = _event_id(season, row, counters[key])
        start = row['start']
        if start is None and previous.get(event_id, {}).get('start'):
            start = previous[event_id]['start']  # keep the known first pitch if the time cell lost it
        event = {'id': event_id, 'season': season, 'away_team_id': row['away'], 'home_team_id': row['home'],
                 'venue': row['venue'] or previous.get(event_id, {}).get('venue', ''), 'start': start,
                 'local_date': row['date'].isoformat(), 'source_timezone': SOURCE_TIMEZONE,
                 'status': row['status'], 'source_url': SOURCE_URL, 'source': SOURCE_ID,
                 'source_event_id': row['source_event_id'], 'fetched_at': fetched_at,
                 'game_number': counters[key], 'broadcasters_raw': row['broadcasters_raw']}
        events[event_id] = event
    for key in {(e['local_date'], e['away_team_id'], e['home_team_id']) for e in events.values()}:
        total = sum(1 for e in events.values() if (e['local_date'], e['away_team_id'], e['home_team_id']) == key)
        for e in events.values():
            if (e['local_date'], e['away_team_id'], e['home_team_id']) == key:
                e['doubleheader'] = total > 1
    return events


def reconcile(previous_events, new_events, window_start, window_end):
    """Link a vanished upcoming game to a same-teams newcomer instead of silently duplicating.

    Returns (events, withdrawn_ids). The old record stays as 'rescheduled' with superseded_by so
    nothing disappears without explanation; a vanished game with no replacement is withdrawn.
    """
    events = dict(new_events)
    withdrawn = []
    for old_id, old in previous_events.items():
        if old_id in new_events or old.get('status') in ('final', 'rescheduled'):
            continue
        day = old.get('local_date', '')
        if not (window_start.isoformat() <= day <= window_end.isoformat()) or old.get('start', 0) is None:
            continue
        pair = (old['away_team_id'], old['home_team_id'])
        successors = [e for eid, e in new_events.items()
                      if (e['away_team_id'], e['home_team_id']) == pair and eid not in previous_events]
        if len(successors) == 1:
            successors[0]['supersedes'] = old_id
            events[old_id] = {**old, 'status': 'rescheduled', 'superseded_by': successors[0]['id']}
        else:
            withdrawn.append(old_id)
    return events, withdrawn


# -------------------------------------------------------------------------------- refresh

def _fetch_page(date):
    request = urllib.request.Request(
        SOURCE_URL, data=('date=' + date.isoformat()).encode(), method='POST',
        headers={'User-Agent': 'SGSports/2.0 schedule-metadata', 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise OSError('status')
        return response.read(MAX_PAGE_BYTES + 1).decode('utf-8')


def source_enabled():
    return os.environ.get('SG_LVBP_SOURCE', 'on') != 'off'


def _state(api):
    cache = api.read_cache(CACHE)
    return cache if isinstance(cache, dict) else {}


def refresh(api, fetch=None, now=None, force=False):
    """Refresh the schedule. Failure keeps last-known-good and backs off; never raises."""
    now = int(time.time()) if now is None else int(now)
    cache = _state(api)
    status = {**{'consecutive_failures': 0, 'last_success': None, 'next_attempt': 0}, **(cache.get('status') or {})}
    if not source_enabled():
        status.update(state='disabled', last_attempt=now)
        api.save_cache(CACHE, {**cache, 'status': status})
        return {'state': 'disabled'}
    if not force and status.get('next_attempt', 0) > now:
        return {'state': 'backoff', 'next_attempt': status['next_attempt']}
    fetch = fetch or _fetch_page
    first = dt.datetime.fromtimestamp(now, _zone()).date()
    last = first + dt.timedelta(days=WINDOW_DAYS - 1)
    try:
        rows = []
        for index in range(min(PAGE_LIMIT, (WINDOW_DAYS + 1) // 2)):
            day = first + dt.timedelta(days=2 * index)
            rows.extend(parse_page(fetch(day), day, now))
        previous = {e['id']: e for e in cache.get('events', [])}
        events = build_events(rows, now, previous)
        upcoming_before = [e for e in previous.values()
                           if now <= (e.get('start') or 0) <= now + 7 * 86400 and e.get('status') in ('scheduled', 'unknown')]
        if not events and upcoming_before:
            raise SchemaError('unexpected empty schedule')
        merged, withdrawn = reconcile(previous, events, first, last)
        status.update(state='ok', last_attempt=now, last_success=now, consecutive_failures=0,
                      next_attempt=now + REFRESH_SECONDS, error=None)
        api.save_cache(CACHE, {'season': season_for(now), 'fetched_at': now, 'source': SOURCE_ID,
                               'source_url': SOURCE_URL, 'window': [first.isoformat(), last.isoformat()],
                               'events': sorted(merged.values(), key=lambda e: (e['local_date'], e['id'])),
                               'withdrawn': withdrawn, 'status': status})
        return {'state': 'ok', 'events': len(merged), 'withdrawn': len(withdrawn)}
    except Exception as error:
        code = 'schema_changed' if isinstance(error, SchemaError) else 'fetch_failed'
        failures = int(status.get('consecutive_failures') or 0) + 1
        status.update(state=code, last_attempt=now, consecutive_failures=failures, error=code,
                      next_attempt=now + min(BACKOFF_BASE * 2 ** (failures - 1), REFRESH_SECONDS))
        api.save_cache(CACHE, {**cache, 'status': status})  # last-known-good events stay untouched
        return {'state': code, 'next_attempt': status['next_attempt']}


# -------------------------------------------------------------------------------- reading

def freshness(api, now=None):
    """Plain schedule-source health: fresh, stale (last-known-good) or unavailable."""
    now = int(time.time()) if now is None else int(now)
    cache = _state(api)
    fetched = cache.get('fetched_at')
    status = cache.get('status') or {}
    if not fetched:
        state = 'unavailable'
    elif cache.get('season') != season_for(now):
        # A previous season's schedule must never pass for the current one.
        state = 'unavailable'
    elif now - fetched > STALE_LIMIT:
        state = 'unavailable'
    elif now - fetched > FRESH_SECONDS:
        state = 'stale'
    else:
        state = 'fresh'
    return {'state': state, 'checked_at': fetched, 'last_attempt': status.get('last_attempt'),
            'source_state': status.get('state')}


def events(api, now=None, days=10):
    """Stored events inside the display window, newest-evidence rules applied.

    Final and superseded games are omitted. A game still marked scheduled well after first pitch
    is unresolved, so it is not offered as live or upcoming.
    """
    now = int(time.time()) if now is None else int(now)
    if freshness(api, now)['state'] == 'unavailable':
        return []
    result = []
    for event in _state(api).get('events', []):
        if event.get('status') == 'final' or event.get('superseded_by') or event.get('season') != season_for(now):
            continue
        start = event.get('start')
        if start is None:
            # An authoritative status row with no first pitch (postponed, suspended, canceled or
            # rescheduled with no successor yet) stays visible by its Caracas calendar day; no
            # first pitch is invented.
            if event.get('status') not in STATUS_ONLY:
                continue
            day = anchor(event)
            if day + 86400 < now - UNRESOLVED_AFTER or day > now + days * 86400:
                continue
            result.append(event)
            continue
        if event.get('status') == 'rescheduled':
            continue
        if event['status'] in ('scheduled', 'unknown') and now > start + UNRESOLVED_AFTER:
            continue
        if start < now - UNRESOLVED_AFTER or start > now + days * 86400:
            continue
        result.append(event)
    result.sort(key=lambda e: (anchor(e), e['id']))
    return result


def team_name(team):
    return TEAMS[team]['name']


def title(event):
    return '%s @ %s' % (team_name(event['away_team_id']), team_name(event['home_team_id']))
