"""Connection-safe, event-scoped stream verification.

The caller owns provider connection admission and returns a small in-memory MPEG-TS sample.
This module never receives provider credentials or a provider URL. External tools only see a
temporary local file, so credentials cannot leak through process arguments or diagnostic output.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

LOCK = threading.RLock()
RUNNING = set()
MAX_SAMPLE_BYTES = 24 * 1024 * 1024
RESULT_TTL = 3 * 3600
FAILURE_TTL = 5 * 60
ANALYSIS_VERSION = 5
MIN_OBSERVATIONS = 3  # reliability history is ignored until this many checks exist
FRESH_WINDOW = 30 * 60  # evidence younger than this outranks older evidence of the same tier
TELEMETRY_WINDOW = 60 * 60  # resolution/fps/bitrate older than this no longer influence order
# Content verdicts are active: repeated positive evidence confirms a feed and repeated explicit
# evidence of another event or sport omits it. Everything else stays unconfirmed, never wrong.
# SG_VERIFICATION_MODE=shadow is a kill switch that records verdicts without acting on them.
SHADOW_MODE = os.environ.get('SG_VERIFICATION_MODE') == 'shadow'
# Only these fields reach the TV client. Resolution, bitrate, frame rate, latency and reliability
# are backend-only ranking inputs and never leave annotate().
PUBLIC_FIELDS = ('status', 'availability', 'content_verified', 'transport_verified',
                 'verified_at', 'expires_at')
GENERIC_TEAM_MARKERS = {
    'THE', 'STATE', 'UNIVERSITY', 'COLLEGE', 'UNITED', 'CITY', 'CLUB', 'TEAM',
    'NEW', 'LOS', 'SAINT', 'SPORTING', 'ATHLETICS', 'FOOTBALL', 'BASEBALL',
    'SOFTBALL', 'FC', 'SC', 'CF', 'ST',
}

class Busy(Exception):
    """Provider is already serving playback or another verification sample."""


def event_key(event):
    live = event.get('live') or {}
    fallback = event.get('id') or (event.get('fixture') or {}).get('id', '')
    return 'espn:' + str(live['espn_id']) if live.get('espn_id') else 'guide:' + str(fallback)


# Source binding (Phase 7A M4A). SOURCE_BINDING() returns feed -> fingerprint of the managed route/mapping the feed's bytes come from (None
# for a direct account). Stored evidence carries the fingerprint it was measured under and is ignored once the feed's fingerprint differs,
# so re-mapping a Dispatcharr stream, rotating its key or switching route can never leave a stale confirmation, language or health claim.
SOURCE_BINDING = None


def configure_binding(factory):
    global SOURCE_BINDING
    SOURCE_BINDING = factory


def _binder():
    if SOURCE_BINDING is None:
        return lambda feed: None
    try:
        return SOURCE_BINDING()
    except Exception:
        return lambda feed: 'binding_unavailable'


def _bound(record, binding):
    return isinstance(record, dict) and record.get('source_binding') == binding


def record_key(event, feed):
    return '|'.join((event_key(event), str(feed.get('provider_id', '')), str(feed.get('stream_id', ''))))


def _fraction(value):
    try:
        if '/' in str(value):
            a, b = str(value).split('/', 1)
            return round(float(a) / float(b), 2) if float(b) else None
        return round(float(value), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _run(args, timeout=15):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=timeout, check=False)
        return result.stdout[:256 * 1024] if result.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _media_info(path):
    raw = _run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)])
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
    audios = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if not videos:
        return None
    video = videos[0]
    width, height = int(video.get('width') or 0), int(video.get('height') or 0)
    fps = _fraction(video.get('avg_frame_rate') or video.get('r_frame_rate'))
    try:
        bitrate = round(int((data.get('format') or {}).get('bit_rate') or video.get('bit_rate') or 0) / 1_000_000, 1)
    except (TypeError, ValueError):
        bitrate = 0
    return {'width': width, 'height': height, 'fps': fps, 'bitrate_mbps': bitrate or None,
            'video_codec': video.get('codec_name'), 'audio_tracks': len(audios)}


def _ocr_variant(path, directory, prefix, video_filter, complex_filter=False):
    pattern = str(directory / (prefix + '-%02d.png'))
    filter_flag = '-filter_complex' if complex_filter else '-vf'
    _run(['ffmpeg', '-loglevel', 'error', '-i', str(path), filter_flag, video_filter,
          '-frames:v', '2', pattern], timeout=20)
    result = []
    for frame in sorted(directory.glob(prefix + '-*.png')):
        value = _run(['tesseract', str(frame), 'stdout', '--psm', '11'], timeout=10)
        result.append(value.upper() if value else '')
    return result


def _ocr(path, directory):
    # Join the two usual scorebug bands into one image per moment. Two moments three seconds
    # apart preserve repeatability while cutting OCR latency from nine large images to two.
    return _ocr_variant(path, directory, 'scorebands',
        '[0:v]fps=1/3,split=2[a][b];'
        '[a]crop=iw:ih*0.38:0:0,scale=1600:-2:flags=lanczos[top];'
        '[b]crop=iw:ih*0.38:0:ih*0.62,scale=1600:-2:flags=lanczos[bottom];'
        '[top][bottom]vstack=inputs=2', complex_filter=True)


def _ocr_regions(path, directory):
    # A smaller full-frame fallback catches centered broadcast slates without making every check
    # pay the cost of OCR over a large 2x image.
    return _ocr_variant(path, directory, 'full', 'fps=1/3,scale=1280:-2:flags=lanczos')


def _espn_game(api, event):
    wanted = str((event.get('live') or {}).get('espn_id') or '')
    if not wanted:
        return None
    cache = api.read_cache('live-scores') or {}
    return next((g for g in cache.get('games', []) if str(g.get('espn_id')) == wanted), None)


def _markers(side):
    values = [(value,4) for value in (side.get('tokens') or [])]
    abbreviation = str(side.get('abbreviation') or '').strip()
    if abbreviation:
        values.append((abbreviation,2))
    # Scorebugs mostly use an official abbreviation or a distinctive mascot/location word.
    # Generic fragments are deliberately excluded because shared words must never confirm a game.
    result=set()
    for value,minimum in values:
        marker=re.sub(r'[^A-Z0-9]', '', str(value).upper())
        if len(marker)>=minimum and marker not in GENERIC_TEAM_MARKERS:result.add(marker)
    return sorted(result,key=len,reverse=True)


def _frames(value):
    if isinstance(value, str):
        return [value.upper()] if value else []
    return [str(item or '').upper() for item in (value or [])]


def _frame_markers(game, text):
    normalized = re.sub(r'[^A-Z0-9:]+', ' ', text.upper())
    found = {}
    for side_name in ('home', 'away'):
        found[side_name] = next((m for m in _markers(game.get(side_name) or {})
            if re.search(r'(?<![A-Z0-9])' + re.escape(m) + r'(?![A-Z0-9])', normalized)), None)
    return normalized, found


def _score_near_marker(text, markers):
    for marker in markers:
        escaped = re.escape(marker)
        match = re.search(r'(?<![A-Z0-9])' + escaped + r'(?![A-Z0-9])[^A-Z0-9]{0,5}(\d{1,3})(?!\d)', text)
        if match:
            return int(match.group(1))
        match = re.search(r'(?<!\d)(\d{1,3})[^A-Z0-9]{0,5}(?<![A-Z0-9])' + escaped + r'(?![A-Z0-9])', text)
        if match:
            return int(match.group(1))
    return None


def _clock_seconds(value):
    match = re.search(r'(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)', str(value or ''))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _observed_state(game, normalized):
    sport = game.get('sport')
    result = {
        'home_score': _score_near_marker(normalized, _markers(game.get('home') or {})),
        'away_score': _score_near_marker(normalized, _markers(game.get('away') or {})),
        'period': None, 'half': None, 'clock': _clock_seconds(normalized),
    }
    if sport == 'Football':
        match = re.search(r'\bQ(?:TR)?\s*([1-4])\b|\b([1-4])(?:ST|ND|RD|TH)\b', normalized)
        if match:
            result['period'] = int(match.group(1) or match.group(2))
    elif sport in ('Baseball', 'Softball'):
        match = re.search(r'\b(TOP|BOT|BOTTOM|MID|END)\s*(\d{1,2})(?:ST|ND|RD|TH)?\b', normalized)
        if match:
            result['half'] = match.group(1).upper()
            result['period'] = int(match.group(2))
    return result


def _integer_score(side):
    try:
        return int(side.get('score'))
    except (TypeError, ValueError):
        return None


def _state_matches(game, observed):
    if observed['home_score'] is None or observed['away_score'] is None:
        return False, False
    scores = (observed['home_score'] == _integer_score(game.get('home') or {}) and
              observed['away_score'] == _integer_score(game.get('away') or {}))
    period = observed.get('period')
    period_matches = period is not None and period == game.get('period')
    if game.get('sport') in ('Baseball', 'Softball') and period_matches:
        detail = str(game.get('detail') or '').upper()
        half = observed.get('half') or ''
        if half in ('TOP',): period_matches = 'TOP' in detail
        elif half in ('BOT', 'BOTTOM'): period_matches = 'BOT' in detail
        elif half in ('MID',): period_matches = 'MID' in detail
        elif half in ('END',): period_matches = 'END' in detail
    clock_matches = False
    observed_clock, espn_clock = observed.get('clock'), _clock_seconds(game.get('display_clock'))
    if observed_clock is not None and espn_clock is not None:
        clock_matches = abs(observed_clock - espn_clock) <= 120
    return scores and period_matches, clock_matches


def _team_evidence(game, texts):
    frame_hits, markers = 0, {'home': set(), 'away': set()}
    state_matches, clock_matches = 0, 0
    for text in _frames(texts):
        normalized, found = _frame_markers(game, text)
        if found['home'] and found['away'] and found['home'] != found['away']:
            frame_hits += 1
            markers['home'].add(found['home']); markers['away'].add(found['away'])
            state_match, clock_match = _state_matches(game, _observed_state(game, normalized))
            state_matches += int(state_match)
            clock_matches += int(clock_match)
    return {'team_frames': frame_hits, 'state_frames': state_matches,
            'clock_frames': clock_matches,
            'markers': sorted(markers['home'] | markers['away'])}


def _analysis(api, event, texts):
    game = _espn_game(api, event)
    if not game:
        return {'verdict':'unknown', 'confidence':0.0, 'reason':'espn_event_unavailable',
                'team_frames':0, 'state_frames':0, 'clock_frames':0, 'markers':[]}
    evidence = _team_evidence(game, texts)
    if evidence['team_frames'] >= 2:
        if game.get('state') == 'in' and evidence['state_frames'] >= 2:
            verdict, confidence, reason = 'confirmed', .99, 'teams_and_live_state_match'
        else:
            verdict, confidence, reason = 'likely_match', .80, 'teams_repeat_state_unconfirmed'
        return {**evidence, 'verdict':verdict, 'confidence':confidence, 'reason':reason}

    cache = api.read_cache('live-scores') or {}
    for other in cache.get('games', []):
        if str(other.get('espn_id')) == str(game.get('espn_id')):
            continue
        other_evidence = _team_evidence(other, texts)
        if other_evidence['team_frames'] >= 2:
            same_sport = other.get('sport') == game.get('sport')
            return {**other_evidence, 'verdict':'wrong_content', 'confidence':.99,
                    'reason':'different_espn_matchup_detected' if same_sport else 'different_sport_detected',
                    'detected_espn_id':str(other.get('espn_id') or '')}
    return {**evidence, 'verdict':'unknown', 'confidence':.20,
            'reason':'insufficient_repeatable_team_evidence'}


def _observation(event, feed, record):
    """Return the bounded, spoiler-safe evidence needed to calibrate the classifier.

    Raw OCR, frames, scores, provider URLs, and credentials are intentionally absent.
    """
    return {
        'analysis_version': record.get('analysis_version'),
        'event_key': event_key(event),
        'sport': event.get('sport'), 'league': event.get('league'),
        'provider_id': feed.get('provider_id'), 'stream_id': str(feed.get('stream_id') or ''),
        'verdict': record.get('shadow_verdict'), 'confidence': record.get('shadow_confidence'),
        'reason': record.get('evidence_reason'),
        'team_frames': record.get('team_evidence_frames', 0),
        'state_frames': record.get('state_evidence_frames', 0),
        'clock_frames': record.get('clock_evidence_frames', 0),
        'detected_espn_id': record.get('detected_espn_id'),
        'transport_verified': bool(record.get('transport_verified')),
        'verified_at': record.get('verified_at'),
    }


def _is_replay(event):
    live = event.get('live') or {}
    return event.get('event_phase') == 'replay' or bool(live.get('likely_replay')) or bool(event.get('replay'))


def _failure_record(now):
    return {'status': 'unavailable', 'availability': 'probe_failed',
            'content_verified': False, 'transport_verified': False,
            'analysis_version': ANALYSIS_VERSION,
            'analysis_mode': 'shadow' if SHADOW_MODE else 'active',
            'shadow_verdict': 'unavailable', 'shadow_confidence': 0.0,
            'evidence_reason': 'transport_probe_failed',
            'verified_at': now, 'expires_at': now + FAILURE_TTL}


def inspect_sample(api, event, sample, open_latency_ms, now=None):
    now = int(time.time()) if now is None else int(now)
    if not sample:
        return _failure_record(now)
    with tempfile.TemporaryDirectory(prefix='sgsports-verify-') as folder:
        directory = Path(folder)
        media = directory / 'sample.ts'
        media.write_bytes(sample[:MAX_SAMPLE_BYTES])
        info = _media_info(media)
        if not info:
            return _failure_record(now)
        texts = []
        # OCR cannot establish identity without an ESPN event to compare against. Skipping it
        # avoids wasting CPU on old replays whose original event is absent from today's cache.
        if _espn_game(api, event):
            texts = _frames(_ocr(media, directory))
            analysis = _analysis(api, event, texts)
            if analysis['verdict'] not in ('confirmed', 'wrong_content'):
                regions = _frames(_ocr_regions(media, directory))
                count = max(len(texts), len(regions))
                combined = [(' '.join((texts[i] if i < len(texts) else '',
                                        regions[i] if i < len(regions) else ''))).strip()
                            for i in range(count)]
                analysis = _analysis(api, event, combined)
        else:
            analysis = _analysis(api, event, [])
    if _is_replay(event) and analysis['verdict'] in ('confirmed', 'wrong_content'):
        # A replay is never live content for the current event, and a replay of another game is
        # legitimate programming. Neither may confirm or suppress a feed.
        analysis = {**analysis, 'verdict': 'likely_match' if analysis['verdict'] == 'confirmed' else 'unknown',
                    'confidence': .20, 'reason': 'replay_not_live_evidence'}
    promoted = analysis['verdict'] == 'confirmed' and not SHADOW_MODE
    return {**info, 'status': 'confirmed' if promoted else 'healthy_unconfirmed',
            'availability': 'content_verified' if promoted else 'stream_healthy',
            'content_verified': promoted, 'transport_verified': True,
            'content_evidence': 'shadow_analysis_v' + str(ANALYSIS_VERSION),
            'analysis_version': ANALYSIS_VERSION, 'analysis_mode': 'shadow' if SHADOW_MODE else 'active',
            'shadow_verdict': analysis['verdict'], 'shadow_confidence': analysis['confidence'],
            'evidence_reason': analysis['reason'], 'team_evidence_frames': analysis['team_frames'],
            'state_evidence_frames': analysis['state_frames'], 'clock_evidence_frames': analysis['clock_frames'],
            'ocr_frames_analyzed': len(texts),
            'matched_markers': analysis['markers'],
            'detected_espn_id': analysis.get('detected_espn_id'), 'open_latency_ms': int(open_latency_ms),
            'verified_at': now, 'expires_at': now + RESULT_TTL}


def _number(value):
    """Finite non-negative number, or 0.0 for missing, malformed, negative or boolean metrics."""
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _age_label(record, now):
    age = max(0, now - int(_number(record.get('verified_at')) or now))
    stamp = 'just now' if age < 60 else str(age // 60) + 'm ago'
    if record.get('content_verified'):
        return 'Game confirmed ' + stamp
    if record.get('shadow_verdict') == 'likely_match' and record.get('transport_verified'):
        return 'Likely this game ' + stamp + ' · not confirmed'
    if record.get('transport_verified'):
        return 'Stream healthy ' + stamp + ' · game not confirmed'
    return 'Could not open ' + stamp


def _tier(feed):
    """Event-confidence tier. Lower is better; it depends only on current Phase 4 evidence."""
    if feed.get('content_verified'):
        return 0
    if feed.get('transport_verified'):
        return 1 if feed.get('shadow_verdict') == 'likely_match' else 2
    if feed.get('availability') == 'probe_failed':
        return 5
    if feed.get('availability') == 'scheduled_not_probed' or feed.get('evidence') == 'published_guide_event':
        return 3
    return 4


def _language_rank(feed, preferred):
    language = feed.get('audio_language')
    code = language.get('code') if isinstance(language, dict) else None
    if preferred and code == preferred:
        return 0
    return 1 if code else 2


def _provider_health(reliability, bind=None):
    """Provider-wide transport success rate bucketed 0 (healthy) .. 2 (poor); 1 when unknown.
    Only histories bound to the stream's current managed source count; stale or unbindable ones are ignored."""
    totals = {}
    for key, history in reliability.items():
        if not isinstance(history, dict):
            continue
        if bind is not None:
            provider, _, stream = str(key).partition('|')
            if not _bound(history, bind({'provider_id': provider, 'stream_id': stream})):
                continue
        got = totals.setdefault(str(key).split('|', 1)[0], [0.0, 0.0])
        got[0] += _number(history.get('successes')); got[1] += _number(history.get('attempts'))
    health = {}
    for provider, (ok, tried) in totals.items():
        rate = ok / tried if tried >= MIN_OBSERVATIONS else None
        health[provider] = 1 if rate is None else 0 if rate >= .8 else 1 if rate >= .5 else 2
    return health


def _rank_key(feed, event, now, preferred, health):
    """Deterministic, provider-neutral ordering. Earlier elements dominate later ones.

    1 event-confidence tier, 2 evidence freshness bucket, 3 resolution/frame-rate/bitrate buckets
    (ignored once telemetry is stale), 4 language, 5 provider health, 6 stream reliability,
    7 opening latency, 8 hashed identity so exact ties never favour a provider by name.
    """
    rank = feed['_rank']
    tier = _tier({**feed, **rank})
    age = max(0, now - int(_number(rank.get('verified_at')) or now)) if rank else None
    fresh = 2 if age is None else 0 if age <= FRESH_WINDOW else 1
    measured = bool(rank.get('transport_verified')) and age is not None and age <= TELEMETRY_WINDOW
    height, fps, bitrate = (_number(rank.get(k)) if measured else 0 for k in ('height', 'fps', 'bitrate_mbps'))
    resolution = 0 if height >= 1080 else 1 if height >= 720 else 2 if height else 3
    smooth = 0 if fps >= 50 else 1 if fps >= 24 else 2
    steady = 0 if bitrate >= 3 else 1 if bitrate >= 1 else 2
    latency = int(_number(rank.get('open_latency_ms')) // 250) if measured else 99999
    identity = hashlib.sha1(record_key(event, feed).encode()).hexdigest()
    return (tier, fresh, resolution, smooth, steady, _language_rank(feed, preferred),
            health.get(str(feed.get('provider_id', '')), 1),
            -round(_number(rank.get('reliability_percent')) / 10), latency, identity)


READINESS_TTL = 10 * 60  # transport-only readiness is short-lived: it only shortens the wait, it never confirms content


def inspect_transport(sample, open_latency_ms, now=None):
    """Transport-only evidence for a pregame feed: the route delivered decodable video. No OCR and
    no identity claim; the record can never carry content_verified or a game verdict."""
    now = int(time.time()) if now is None else int(now)
    if not sample:
        return _failure_record(now)
    with tempfile.TemporaryDirectory(prefix='sgsports-verify-') as folder:
        media = Path(folder) / 'sample.ts'
        media.write_bytes(sample[:MAX_SAMPLE_BYTES])
        info = _media_info(media)
    if not info:
        return _failure_record(now)
    return {**info, 'status': 'healthy_unconfirmed', 'availability': 'stream_healthy',
            'content_verified': False, 'transport_verified': True, 'readiness_only': True,
            'analysis_version': ANALYSIS_VERSION, 'analysis_mode': 'readiness',
            'shadow_verdict': 'unknown', 'shadow_confidence': 0.0,
            'evidence_reason': 'pregame_transport_only', 'open_latency_ms': int(open_latency_ms),
            'verified_at': now, 'expires_at': now + READINESS_TTL}


def annotate(api, event, now=None, preferred_language=None):
    now = int(time.time()) if now is None else int(now)
    records = api.read_cache('stream-verification') or {}
    reliability = api.read_cache('stream-reliability') or {}
    records = records if isinstance(records, dict) else {}
    reliability = reliability if isinstance(reliability, dict) else {}
    feeds, omitted = [], 0
    bind = _binder()
    for feed in event.get('feeds', []):
        record = records.get(record_key(event, feed))
        binding = bind(feed)
        # Expired, malformed, other-version or other-source evidence can neither confirm nor suppress a feed.
        if (not isinstance(record, dict) or _number(record.get('expires_at')) <= now
                or record.get('analysis_version') != ANALYSIS_VERSION or not _bound(record, binding)):
            record = {}
        history = reliability.get('|'.join((str(feed.get('provider_id','')),str(feed.get('stream_id','')))))
        history = history if isinstance(history, dict) and _bound(history, binding) else {}
        attempts = _number(history.get('attempts'))
        reliability_percent = round(100 * min(_number(history.get('successes')), attempts) / attempts) \
            if attempts >= MIN_OBSERVATIONS else None
        verdict = record.get('shadow_verdict')
        if record and _is_replay(event):
            # Evidence recorded before the event was known to be a replay cannot act on it.
            if verdict in ('confirmed', 'wrong_content', 'likely_match'):
                verdict = None
            record = {**record, 'content_verified': False, 'shadow_verdict': verdict,
                      'availability': 'stream_healthy' if record.get('transport_verified') else record.get('availability')}
        if verdict == 'wrong_content' and not SHADOW_MODE:
            omitted += 1
            continue
        public = {k: record[k] for k in PUBLIC_FIELDS if k in record}
        if record:
            public['verification_label'] = _age_label(record, now)
        # Backend-only ranking inputs ride along privately and are stripped before returning.
        feeds.append({**feed, **public, '_rank': {**record, 'reliability_percent': reliability_percent}})

    health = _provider_health(reliability, bind)
    feeds.sort(key=lambda feed: _rank_key(feed, event, now, preferred_language, health))
    feeds = [{k: v for k, v in feed.items() if k != '_rank'} for feed in feeds]
    with LOCK:
        running = event_key(event) in RUNNING
    return {**event, 'feeds': feeds, 'verification': {'running': running, 'omitted_wrong_content': omitted}}


def start(api, event, sample_feed, readiness=False):
    key = event_key(event)
    if not event.get('feeds'):
        return {'status': 'no_candidates'}
    # Future games are never content-probed: a healthy channel before kickoff can be showing
    # unrelated programming and must never be promoted as game-confirmed evidence. Only an explicit
    # readiness request inside the tune-in window takes a transport-only sample.
    live = event.get('live') or {}
    transport_only = False
    if event.get('pregame') or live.get('state') == 'pre':
        if not (readiness and event.get('event_phase') == 'pregame'):
            return {'status': 'not_started'}
        transport_only = True
    now = int(time.time())
    cached = api.read_cache('stream-verification') or {}
    bind = _binder()
    pending = [feed for feed in event.get('feeds', [])
               if ((cached.get(record_key(event, feed)) or {}).get('readiness_only') and not transport_only) or
               (cached.get(record_key(event, feed)) or {}).get('expires_at', 0) <= now or
               (cached.get(record_key(event, feed)) or {}).get('analysis_version') != ANALYSIS_VERSION or
               not _bound(cached.get(record_key(event, feed)), bind(feed))]
    if not pending:
        return {'status': 'fresh'}
    with LOCK:
        if key in RUNNING:
            return {'status': 'already_running'}
        RUNNING.add(key)

    def work():
        try:
            records = api.read_cache('stream-verification') or {}
            for feed in pending:
                before = _binder()(feed)
                try:
                    sample, latency = sample_feed(event, feed)
                    record = inspect_transport(sample, latency) if transport_only else inspect_sample(api, event, sample, latency)
                except Busy:
                    continue
                except Exception:
                    now = int(time.time())
                    record = _failure_record(now)
                if _binder()(feed) != before:
                    continue   # the feed was re-mapped or re-routed while sampling: these bytes may belong to the old source, so keep nothing
                record['source_binding'] = before
                with LOCK:
                    records = api.read_cache('stream-verification') or records
                    records[record_key(event, feed)] = record
                    api.save_cache('stream-verification', records)
                    reliability = api.read_cache('stream-reliability') or {}
                    history_key = '|'.join((str(feed.get('provider_id','')),str(feed.get('stream_id',''))))
                    history = reliability.get(history_key)
                    if not isinstance(history, dict) or not _bound(history, before):
                        history = {'attempts':0,'successes':0,'confirmed':0}   # health measured through another source is not this source's health
                    history['source_binding'] = before
                    history['attempts'] += 1
                    history['successes'] += int(bool(record.get('transport_verified')))
                    history['confirmed'] += int(bool(record.get('content_verified')))
                    history['shadow_confirmed'] = history.get('shadow_confirmed', 0) + int(record.get('shadow_verdict') == 'confirmed')
                    history['wrong_content'] = history.get('wrong_content', 0) + int(record.get('shadow_verdict') == 'wrong_content')
                    history['last_checked'] = record.get('verified_at')
                    reliability[history_key] = history
                    api.save_cache('stream-reliability', reliability)
                    if not transport_only:   # readiness holds no content signal, so it stays out of classifier calibration
                        observations = api.read_cache('stream-verification-observations') or []
                        observations.append(_observation(event, feed, record))
                        api.save_cache('stream-verification-observations', observations[-500:])
                time.sleep(1)
        finally:
            with LOCK:
                RUNNING.discard(key)
    threading.Thread(target=work, daemon=True, name='stream-verification').start()
    return {'status': 'started'}
