"""LVBP broadcaster assignments, provider candidates, manual overrides and coverage.

Four planes stay independent:
  schedule (lvbp.py) -> per-game assignment evidence (here) -> provider channel candidates (here)
  -> health and content verification (verification.py).
A generic all-games service listing is never a linear-channel assignment, a healthy channel is not
game confirmation, and an access error is an expiring observation rather than a provider ban.
"""
import datetime as dt
import json
import re
import time
import unicodedata
from pathlib import Path

import lvbp
import verification

OVERRIDES_PATH = Path(__file__).parent / 'lvbp_overrides.json'
OFFICIAL_TYPES = ('official_league', 'official_team', 'official_broadcaster', 'manual_override')
ASSIGNMENT_TTL = 36 * 3600        # per-game official evidence stays usable this long after observation
EPG_TTL = 6 * 3600
HEALTH_WINDOW = 3 * 3600          # channel health older than this cannot support "Confirmed"
JOIN_TOLERANCE = 45 * 60          # guide slot vs scheduled first pitch
OVERRIDE_MAX_SECONDS = 30 * 86400
OVERRIDE_OPERATIONS = ('set_status', 'set_assignment', 'remove_assignment')

BROADCASTERS = {b['id']: b for b in lvbp.DATA['broadcasters']}
CHANNELS = [c for c in lvbp.DATA['channels']]
LABELS = {'confirmed': 'Confirmed feed', 'possible': 'Possible feed', 'none': 'No verified feed'}


def _fold(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii').casefold()
    text = re.sub(r'\|[^|]*\|', ' ', text)                       # |VE| |LATIN| |Venezuela| prefixes
    text = re.sub(r'^\s*(?:lat|latin|ve|venezuela|us)\s*[-:]\s*', ' ', text)
    text = re.sub(r'\b(?:hd|fhd|uhd|sd|4k|1080p?|720p?)\b', ' ', text)
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text).split())


_ALIASES = {}
for _b in lvbp.DATA['broadcasters']:
    for _alias in (_b['name'], *_b['aliases']):
        _ALIASES[_fold(_alias)] = _b['id']


def broadcaster_id(label):
    """Canonical broadcaster id for a league logo label or provider channel name; exact alias only."""
    return _ALIASES.get(_fold(label))


# ------------------------------------------------------------------------------ health

def health(api, now):
    """Latest unexpired channel-health observation per provider|stream, newest wins."""
    seen = {}
    records = list(lvbp.DATA.get('observations', []))
    cached = api.read_cache('lvbp-health')
    if isinstance(cached, list):
        records += cached
    for r in records:
        if not isinstance(r, dict):
            continue
        key = '%s|%s' % (r.get('provider'), r.get('stream_id'))
        expires = (r.get('observed_at') or 0) + (r.get('ttl') or 0)
        if expires > now and (key not in seen or r['observed_at'] > seen[key]['observed_at']):
            seen[key] = {'result': r.get('result'), 'observed_at': r['observed_at'], 'expires_at': expires}
    return seen


def _feed_broadcaster(feed):
    return feed.get('broadcaster_id') or broadcaster_id(feed.get('display_name'))


def _verified_recently(api, event, feed, now):
    records = api.read_cache('stream-verification') or {}
    record = records.get(verification.record_key(event, feed)) if isinstance(records, dict) else None
    return (isinstance(record, dict) and record.get('transport_verified') is True
            and (record.get('verified_at') or 0) >= now - HEALTH_WINDOW
            and (record.get('expires_at') or 0) > now)


# ------------------------------------------------------------------------------ candidates

def candidates(api, broadcaster_ids, now):
    """Provider channels that are technical candidates for the given broadcasters.

    A candidate must still exist in the provider's current catalog and must not contradict its
    guide id. Variants of one broadcaster on one provider (alias/HD duplicates) collapse to one
    candidate, chosen by current health evidence and then by file order, never by provider name.
    """
    seen_health = health(api, now)
    chosen = {}
    for channel in CHANNELS:
        if not channel.get('enabled') or channel['broadcaster'] not in broadcaster_ids:
            continue
        catalog = {str(c.get('stream_id')): c for c in (api.read_cache(channel['provider']) or {}).get('channels', [])}
        entry = catalog.get(str(channel['stream_id']))
        if not entry:
            continue
        epg = entry.get('epg_channel_id')
        if channel.get('guide_ids') and epg and epg not in channel['guide_ids']:
            continue
        observed = seen_health.get('%s|%s' % (channel['provider'], channel['stream_id']), {})
        score = 0 if observed.get('result') == 'ok' else 2 if observed else 1
        key = (channel['provider'], channel['broadcaster'])
        if key not in chosen or score < chosen[key][0]:
            chosen[key] = (score, channel, entry)
    feeds = []
    for (score, channel, entry) in chosen.values():
        feeds.append({'provider_id': channel['provider'], 'stream_id': str(channel['stream_id']),
                      'display_name': entry.get('name') or BROADCASTERS[channel['broadcaster']]['name'],
                      'broadcaster_id': channel['broadcaster'], 'broadcast_kind': 'main',
                      'evidence': 'broadcaster_candidate', 'availability': 'scheduled_not_probed'})
    return sorted(feeds, key=lambda f: (f['broadcaster_id'], f['provider_id'], f['stream_id']))


# ------------------------------------------------------------------------------ assignments

def schedule_assignments(event):
    """Official per-game evidence the league publishes beside the schedule row."""
    records, unmapped = [], []
    for label in event.get('broadcasters_raw', []):
        bid = broadcaster_id(label)
        if bid and BROADCASTERS[bid].get('generic_service'):
            continue  # an all-games service listing is not evidence about any specific channel
        if not bid:
            unmapped.append(label)
            continue
        records.append({'event_id': event['id'], 'broadcaster_id': bid, 'evidence_type': 'official_league',
                        'source_url': event.get('source_url'), 'observed_at': event['fetched_at'],
                        'confidence': 0.95, 'expires_at': event['fetched_at'] + ASSIGNMENT_TTL})
    return records, unmapped


def match_programs(events, programs):
    """Join guide programs to schedule events by team identity plus bounded start time.

    A guide entry that matches no schedule event is ignored (it never creates a game); one that
    is equally close to two events (a doubleheader boundary) stays unmatched rather than guessed.
    """
    by_event = {}
    for program in programs:
        if program.get('league') != lvbp.LEAGUE or program.get('repeat') or not program.get('is_game'):
            continue
        sides = [lvbp.team_id(s) for s in program.get('sides', [])]
        if len(sides) != 2 or None in sides or sides[0] == sides[1]:
            continue
        pair = frozenset(sides)
        near = sorted((abs(program['start'] - e['start']), e['id']) for e in events
                      if e.get('start') and frozenset((e['away_team_id'], e['home_team_id'])) == pair
                      and abs(program['start'] - e['start']) <= JOIN_TOLERANCE)
        if not near or (len(near) > 1 and near[0][0] == near[1][0]):
            continue
        by_event.setdefault(near[0][1], []).append(program)
    return by_event


def epg_assignments(event, programs, guide_updated_at, now):
    records, feeds = [], []
    if not guide_updated_at or guide_updated_at + EPG_TTL <= now:
        return records, feeds  # an old guide snapshot is not evidence about this game
    for program in programs:
        for feed in program.get('feeds', []):
            feeds.append({**feed, 'evidence': feed.get('evidence', 'published_guide_event'),
                          'availability': feed.get('availability', 'scheduled_not_probed')})
            bid = broadcaster_id(feed.get('display_name'))
            if bid:
                records.append({'event_id': event['id'], 'broadcaster_id': bid, 'evidence_type': 'epg',
                                'source_url': None, 'observed_at': guide_updated_at, 'confidence': 0.5,
                                'expires_at': (guide_updated_at or 0) + EPG_TTL})
    return records, feeds


# ------------------------------------------------------------------------------ overrides

def load_overrides(path=OVERRIDES_PATH):
    """(records, error). Unreadable or malformed files fail closed: no overrides, visible error."""
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return [], 'overrides_unreadable'
    if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('overrides'), list):
        return [], 'overrides_unreadable'
    return raw['overrides'], None


def _epoch(value):
    try:
        return int(dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
    except (TypeError, ValueError):
        return None


def validate_overrides(records, event_ids, now):
    """Split overrides into (active, rejected). Every rule fails closed and carries a reason."""
    active, rejected = [], []
    for r in records:
        reason = None
        if not isinstance(r, dict):
            rejected.append({'id': None, 'reason': 'not_an_object'})
            continue
        created, expires = _epoch(r.get('created_at')), _epoch(r.get('expires_at'))
        value = r.get('value')
        if not all(isinstance(r.get(k), str) and r.get(k).strip() for k in ('id', 'reason', 'author', 'target_id')):
            reason = 'missing_field'
        elif not str(r.get('source_url', '')).startswith('https://'):
            reason = 'source_url_required'
        elif created is None or expires is None or expires <= created or expires - created > OVERRIDE_MAX_SECONDS:
            reason = 'invalid_expiry'
        elif expires <= now:
            reason = 'expired'
        elif r.get('target_type') != 'event' or r.get('operation') not in OVERRIDE_OPERATIONS:
            reason = 'unknown_operation'
        elif r['target_id'] not in event_ids:
            reason = 'unknown_event'
        elif not isinstance(value, dict):
            reason = 'invalid_value'
        elif r['operation'] == 'set_status' and value.get('status') not in ('postponed', 'suspended', 'canceled'):
            reason = 'invalid_status'
        elif r['operation'] in ('set_assignment', 'remove_assignment') and value.get('broadcaster_id') not in BROADCASTERS:
            reason = 'unknown_broadcaster'
        elif BROADCASTERS.get(value.get('broadcaster_id'), {}).get('generic_service'):
            reason = 'generic_service_is_not_an_assignment'
        if reason:
            rejected.append({'id': r.get('id'), 'reason': reason})
        else:
            active.append(r)
    # Two live overrides that disagree about the same target cannot both be trusted: reject both.
    groups = {}
    for r in active:
        key = (r['target_id'], r['operation'], r['value'].get('broadcaster_id') if r['operation'] != 'set_status' else None)
        groups.setdefault(key, []).append(r)
    conflicted = {id(r) for g in groups.values() if len({json.dumps(x['value'], sort_keys=True) for x in g}) > 1 for r in g}
    # set_assignment vs remove_assignment for the same broadcaster is also a contradiction.
    pairs = {}
    for r in active:
        if r['operation'] in ('set_assignment', 'remove_assignment'):
            pairs.setdefault((r['target_id'], r['value']['broadcaster_id']), set()).add(r['operation'])
    for r in active:
        if r['operation'] in ('set_assignment', 'remove_assignment') and len(pairs[(r['target_id'], r['value']['broadcaster_id'])]) > 1:
            conflicted.add(id(r))
    for r in active:
        if id(r) in conflicted:
            rejected.append({'id': r['id'], 'reason': 'conflicting_override'})
    return [r for r in active if id(r) not in conflicted], rejected


# ------------------------------------------------------------------------------ evaluation

def evaluate(api, event, now, programs=(), guide_updated_at=None, overrides=()):
    """Everything known about one event's broadcast, without opening any media."""
    status = event['status']
    official, unmapped = schedule_assignments(event)
    epg, epg_feeds = epg_assignments(event, programs, guide_updated_at, now)
    removed = set()
    for r in overrides:
        if r['target_id'] != event['id']:
            continue
        if r['operation'] == 'set_status':
            status = r['value']['status']
        elif r['operation'] == 'set_assignment':
            official.append({'event_id': event['id'], 'broadcaster_id': r['value']['broadcaster_id'],
                             'evidence_type': 'manual_override', 'source_url': r['source_url'],
                             'observed_at': _epoch(r['created_at']), 'confidence': 0.9,
                             'expires_at': _epoch(r['expires_at']), 'override_id': r['id']})
        else:
            removed.add(r['value']['broadcaster_id'])
    official = [a for a in official if a['broadcaster_id'] not in removed and a['expires_at'] > now]
    epg = [a for a in epg if a['broadcaster_id'] not in removed and a['expires_at'] > now]
    expired = any(a['expires_at'] <= now for a in schedule_assignments(event)[0])
    official_ids = sorted({a['broadcaster_id'] for a in official})
    conflicts = sorted({a['broadcaster_id'] for a in epg} - set(official_ids)) if official_ids else []
    assigned = official_ids or sorted({a['broadcaster_id'] for a in epg})
    # Candidates come only from official assignments plus the guide's own matched listing;
    # without assignment evidence no provider channel is guessed.
    feeds = candidates(api, set(official_ids), now)
    known = {(f['provider_id'], f['stream_id']) for f in feeds}
    for feed in epg_feeds:
        if (feed['provider_id'], feed['stream_id']) not in known:
            feeds.append(feed)
            known.add((feed['provider_id'], feed['stream_id']))
    # A guide listing for a channel that contradicts the official assignment is a disagreement,
    # not support: it stays visible as a candidate but cannot raise the coverage level.
    seen_health = health(api, now)
    supported = [f for f in feeds if _feed_broadcaster(f) in official_ids]
    healthy = any(seen_health.get('%s|%s' % (f['provider_id'], f['stream_id']), {}).get('result') == 'ok'
                  or _verified_recently(api, event, f, now) for f in supported)
    if status in ('canceled', 'postponed', 'suspended', 'rescheduled', 'final'):
        coverage = 'none'
    elif supported and healthy:
        coverage = 'confirmed'
    elif supported or (not official_ids and epg_feeds):
        coverage = 'possible'
    else:
        coverage = 'none'
    return {'status': status, 'coverage': coverage, 'official': official, 'epg': epg, 'assigned': assigned,
            'conflicts': conflicts, 'feeds': feeds, 'unmapped_labels': unmapped, 'expired': expired and not official}


def _detail(coverage, evaluation, names):
    who = ', '.join(names)
    if coverage == 'confirmed':
        return 'The league lists %s for this game and its channel was checked recently.' % who
    if coverage == 'possible':
        if evaluation['official']:
            return '%s is listed for this game, but its channel has not been checked recently.' % who
        return 'A channel guide suggests %s. The league has not confirmed this.' % who
    if evaluation['assigned']:
        return 'The league lists %s for this game. SG Sports has no channel for it.' % who
    if evaluation['unmapped_labels']:
        return 'The league lists %s for this game. SG Sports has no channel for it.' % ', '.join(evaluation['unmapped_labels'])
    return 'No broadcaster is listed for this game yet.'


def to_event(api, event, now, source, programs=(), guide_updated_at=None, overrides=()):
    """Normalized schedule event in the shape the guide, truth and TV seams already consume."""
    evaluation = evaluate(api, event, now, programs, guide_updated_at, overrides)
    names = [BROADCASTERS[b]['name'] for b in evaluation['assigned']]
    if not names and evaluation['feeds']:
        names = [evaluation['feeds'][0].get('display_name') or 'a listed channel']
    away, home = lvbp.team_name(event['away_team_id']), lvbp.team_name(event['home_team_id'])
    start = event['start'] or 0  # 0 = time not listed (status-only row); never a guessed first pitch
    item = {'id': event['id'], 'title': 'Venezuela LVBP · %s @ %s' % (away, home), 'competition': lvbp.LEAGUE,
            'matchup': '%s @ %s' % (away, home), 'is_game': True, 'sport': lvbp.SPORT, 'league': lvbp.LEAGUE,
            'conferences': [], 'start': start, 'end': start + lvbp.GAME_LENGTH, 'feeds': evaluation['feeds'],
            'live_tag': False, 'repeat': False, 'schedule_status': evaluation['status'],
            'pregame': bool(start) and now < start,
            'sides': [event['away_team_id'], event['home_team_id']], 'schedule_event': True,
            'lvbp': {'coverage': evaluation['coverage'], 'label': LABELS[evaluation['coverage']],
                     'detail': _detail(evaluation['coverage'], evaluation, names), 'broadcasters': names, 'broadcaster_ids': evaluation['assigned'],
                     'assignment_checked_at': event['fetched_at'], 'venue': event.get('venue') or None,
                     'game_number': event['game_number'] if event.get('doubleheader') else None,
                     'schedule': {'state': source['state'], 'checked_at': source['checked_at']}}}
    return item, evaluation


def after_verification(item):
    """Drop a Confirmed claim when verification has omitted every channel that supported it."""
    block = item.get('lvbp')
    if not block or block['coverage'] != 'confirmed':
        return item
    if any(_feed_broadcaster(f) in block['broadcaster_ids'] for f in item.get('feeds', [])):
        return item
    return {**item, 'lvbp': {**block, 'coverage': 'none', 'label': LABELS['none'],
            'detail': 'The channel for this game was showing something else when last checked.'}}


# ------------------------------------------------------------------------------ coverage

def coverage_report(api, now=None, window_hours=168, programs=(), overrides=None):
    """Windowed LVBP coverage with an explicit denominator; playback reliability is separate."""
    now = int(time.time()) if now is None else int(now)
    source = lvbp.freshness(api, now)
    end = now + window_hours * 3600
    stored = lvbp.events(api, now, days=window_hours / 24)
    stored = [e for e in stored if now <= lvbp.anchor(e) <= end or (e.get('start') is None and lvbp.anchor(e) < now)]
    if overrides is None:
        records, error = load_overrides()
        overrides, rejected = validate_overrides(records, {e['id'] for e in stored}, now)
    else:
        error, rejected = None, []
    matches = match_programs(stored, list(programs))
    counts = {k: 0 for k in ('confirmed', 'possible', 'none')}
    out_of_scope = {}
    missing, unmapped, mapped_candidates, official_assigned, epg_only, conflicts, unassigned, stale = {}, {}, 0, 0, 0, 0, 0, 0
    guide_updated = (api.read_cache('sports-guide') or {}).get('updated_at')
    denominator = 0
    for event in stored:
        _, ev = to_event(api, event, now, source, matches.get(event['id'], ()), guide_updated, overrides)
        if ev['status'] in ('postponed', 'canceled', 'suspended', 'rescheduled'):
            out_of_scope[ev['status']] = out_of_scope.get(ev['status'], 0) + 1
            continue
        denominator += 1
        counts[ev['coverage']] += 1
        official_assigned += bool(ev['official'])
        epg_only += bool(ev['epg'] and not ev['official'])
        conflicts += bool(ev['conflicts'])
        unassigned += not ev['assigned'] and not ev['unmapped_labels']
        stale += bool(ev['expired']) or source['state'] == 'stale'
        mapped_candidates += bool(ev['feeds'])
        for bid in ev['assigned']:
            if not any(f.get('broadcaster_id') == bid for f in ev['feeds']):
                name = BROADCASTERS[bid]['name']
                missing[name] = missing.get(name, 0) + 1
        for label in ev['unmapped_labels']:
            unmapped[label] = unmapped.get(label, 0) + 1
    observed = health(api, now)
    playback = {'note': 'Short technical observations only; separate from guide coverage.',
                'ok': sum(1 for o in observed.values() if o['result'] == 'ok'),
                'failed': sum(1 for o in observed.values() if o['result'] != 'ok'),
                'by_result': {}}
    for o in observed.values():
        playback['by_result'][o['result']] = playback['by_result'].get(o['result'], 0) + 1
    return {'generated_at': now, 'window': {'start': now, 'end': end, 'hours': window_hours},
            'denominator': denominator, 'schedule': source,
            'out_of_scope': out_of_scope,
            'confirmed': counts['confirmed'], 'possible': counts['possible'], 'no_verified_feed': counts['none'],
            'official_assignments': official_assigned, 'epg_only': epg_only, 'assignment_conflicts': conflicts,
            'no_assignment': unassigned, 'mapped_candidates': mapped_candidates,
            'stale_evidence': stale,
            'gaps': {'broadcasters_without_channel': missing, 'unmapped_broadcaster_labels': unmapped},
            'overrides': {'active': len(overrides), 'rejected': rejected, 'error': error},
            'playback_reliability': playback}
