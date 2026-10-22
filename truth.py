"""Server-computed event phase and provider/ESPN broadcast-network corroboration.

Pure functions over already-fetched data: no ESPN or provider access happens here. The phase
is emitted so clients never have to infer REPLAY from the mere absence of ESPN coverage.
"""
import re
import unicodedata

import guide

# Phases: live, pregame (inside the tune-in window), upcoming (earlier), final, postponed,
# canceled, replay, on_now (guide slot is airing but nothing independent says what it is), show.
PREGAME_PHASES = ('pregame', 'upcoming')
# An authoritative schedule's own status (LVBP) decides these phases before any time inference.
SCHEDULE_PHASES = {'postponed': 'postponed', 'rescheduled': 'postponed', 'canceled': 'canceled',
                   'suspended': 'suspended', 'final': 'final'}

def event_phase(event, live, at):
    """Return the one honest phase for a guide event, with ESPN evidence when it resolved."""
    if event.get('is_game') is False:
        return 'show'
    # A provider-declared rebroadcast is never live, whatever ESPN says about the original game.
    if event.get('repeat'):
        return 'replay'
    if event.get('schedule_status') in SCHEDULE_PHASES:
        return SCHEDULE_PHASES[event['schedule_status']]
    start = event.get('start') or 0
    state = (live or {}).get('state')
    status = (live or {}).get('status_name') or ''
    if live:
        if status == 'STATUS_POSTPONED':
            return 'postponed'
        if status == 'STATUS_CANCELED':
            return 'canceled'
        if live.get('likely_replay'):
            return 'replay'
        if state == 'in':
            return 'live'
        if state == 'post':
            return 'final'
        if state == 'pre':
            return _pregame(start, at)
    # No usable ESPN evidence. A game that has not started is upcoming; one whose guide slot is
    # underway is only "on now" -- it is not called live, and above all not called a replay.
    if start and at < start:
        return _pregame(start, at)
    return 'on_now'

def _pregame(start, at):
    return 'pregame' if start - guide.PREGAME_TUNE_SECONDS <= at else 'upcoming'

_QUALITY = re.compile(r'\b(?:hd|fhd|uhd|sd|4k|1080p?|720p?|east|west|us|usa)\b')

def network_key(name):
    """Normalize a channel or ESPN network label so 'US: ESPN 2 HD' equals 'ESPN2'."""
    folded = unicodedata.normalize('NFKD', str(name or '').casefold()).encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[^a-z0-9]+', ' ', _QUALITY.sub(' ', folded))
    text = re.sub(r'\b([a-z]+)\s+(\d)\b', r'\1\2', text)
    return ' '.join(text.split())

def broadcast_check(event):
    """Compare ESPN's networks with each provider's listed channel names for one event.

    Neither side is trusted by default: a provider whose channels do not include any ESPN
    network is reported as a disagreement, and so is the whole event if any one provider
    disagrees, so it can reach verification. Missing ESPN networks mean 'unknown', never
    agreement. Alternate-commentary feeds are not compared (their channel legitimately differs).
    """
    live = event.get('live') or {}
    networks = [n for n in (live.get('networks') or []) if network_key(n)]
    wanted = {network_key(n) for n in networks}
    providers = {}
    for feed in event.get('feeds', []):
        providers.setdefault(feed.get('provider_id'), []).append(feed)
    result = {'espn_networks': networks, 'providers': {}}
    statuses = []
    for pid, feeds in sorted(providers.items(), key=lambda kv: str(kv[0])):
        compared = [f for f in feeds if f.get('broadcast_kind') != 'alternate']
        channels = [f.get('display_name') for f in compared if f.get('display_name')]
        if not compared:
            status = 'not_compared'
        elif not wanted:
            status = 'unknown'
        else:
            status = 'corroborated' if any(network_key(c) in wanted for c in channels) else 'disagreement'
        result['providers'][pid] = {'status': status, 'channels': channels}
        if status != 'not_compared':
            statuses.append(status)
    if 'disagreement' in statuses:
        overall = 'disagreement'
    elif statuses and all(s == 'corroborated' for s in statuses):
        overall = 'corroborated'
    else:
        overall = 'unknown'
    result['status'] = overall
    result['needs_verification'] = overall == 'disagreement'
    return result

def annotate(event, live, at):
    """Return the event with ESPN state, explicit phase and broadcast comparison attached."""
    item = {**event}
    if live:
        item['live'] = live
    phase = event_phase(item, live, at)
    if phase == 'replay' and live:
        # Replays must never read as live: keep the nested ESPN flags consistent with the phase.
        item['live'] = {**live, 'verified_live': False, 'likely_replay': True}
    item['event_phase'] = phase
    item['espn_coverage'] = 'matched' if live else 'unmapped'
    item['broadcast_check'] = broadcast_check(item)
    return item
