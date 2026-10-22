"""Best-effort live scores from ESPN's public (undocumented) scoreboard API.

Matched against guide events / fixtures by team-name tokens, not a paid feed.
Never blocks guide or fixture availability: any failure here just means no
live-score data is attached, the rest of the app works exactly as before.
"""
import re
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from zoneinfo import ZoneInfo

import guide

BASE = 'https://site.api.espn.com/apis/site/v2/sports'
HOUSEHOLD_TZ = ZoneInfo('America/New_York')

# (sport, league) as used elsewhere in this backend -> ESPN scoreboard path.
ENDPOINTS = {
    ('Soccer', 'Premier League'): 'soccer/eng.1',
    ('Soccer', 'La Liga'): 'soccer/esp.1',
    ('Soccer', 'Serie A'): 'soccer/ita.1',
    ('Soccer', 'Bundesliga'): 'soccer/ger.1',
    ('Soccer', 'Ligue 1'): 'soccer/fra.1',
    ('Soccer', 'UEFA Champions League'): 'soccer/uefa.champions',
    ('Soccer', 'Argentina Primera División'): 'soccer/arg.1',
    ('Soccer', 'Venezuela Liga FUTVE'): 'soccer/ven.1',
    ('Soccer', 'MLS'): 'soccer/usa.1',
    ('Football', 'NFL'): 'football/nfl',
    ('Football', 'NCAA'): 'football/college-football',
    ('Baseball', 'MLB'): 'baseball/mlb',
    ('Softball', 'NCAA'): 'baseball/college-softball',
}

STOP_WORDS = {'the', 'of', 'state', 'university', 'college', 'and', 'at', 'vs'}

def _tokens(*parts):
    words = set()
    for part in parts:
        folded = unicodedata.normalize('NFKD', part or '').encode('ascii', 'ignore').decode('ascii')
        words.update(w.lower() for w in re.findall(r"[A-Za-z']+", folded) if len(w) > 2)
    return words - STOP_WORDS

def team_tokens(team):
    return _tokens(team.get('location'), team.get('name'), team.get('shortDisplayName'),
                    team.get('displayName'), team.get('abbreviation'))

def text_has_tokens(text, tokens):
    text = (text or '').lower()
    return any(re.search(r'\b' + re.escape(t) + r'\b', text) for t in tokens)

def _timestamp(value):
    if not value:
        return None
    try:
        return int(dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp())
    except (ValueError,TypeError):
        return None

def _get(url):
    # This unofficial endpoint 403s on browser-like or custom User-Agent strings
    # but allows curl's own default one; matched empirically, not documented by ESPN.
    request = urllib.request.Request(url, headers={'User-Agent': 'curl/8.7.1'})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('response_too_large')
    import json
    return json.loads(raw.decode('utf-8'))

def _scoreboard_date():
    # ESPN's site-v2 scoreboard currently rejects YYYYMMDD-YYYYMMDD ranges with HTTP 400.
    # A failed wide request meant every league silently lost ESPN state, and the client then
    # treated every guide game as live. Live verification only needs today's board: a current
    # guide matchup that ESPN does not report as state=in is not verified live and is rendered
    # as a replay by the TV client. This also keeps the minute refresh to one request per league.
    return dt.datetime.now(HOUSEHOLD_TZ).strftime('%Y%m%d')

def _fetch(path):
    return _get(BASE + '/' + path + '/scoreboard?dates=' + _scoreboard_date() + '&limit=1000')

# Each favorite team's stable ESPN team id, looked up once by hand against
# site.api.espn.com/apis/site/v2/sports/{path}/teams?limit=1000 — matched by id,
# not name, so ESPN's own display-name spelling can't cause a false mismatch.
FAVORITE_TEAMS = [
    {'team_id': 'team.soccer.men.la-liga.fc-barcelona', 'name': 'Barcelona', 'sport': 'Soccer', 'league': 'La Liga', 'path': 'soccer/esp.1', 'espn_id': 83},
    {'team_id': 'team.football.men.ncaa.tennessee-volunteers', 'name': 'Tennessee Volunteers', 'sport': 'Football', 'league': 'NCAA', 'path': 'football/college-football', 'espn_id': 2633},
    {'team_id': 'team.softball.women.ncaa.tennessee-volunteers', 'name': 'Tennessee Lady Volunteers', 'sport': 'Softball', 'league': 'NCAA', 'path': 'baseball/college-softball', 'espn_id': 611},
    {'team_id': 'team.baseball.men.mlb.st-louis-cardinals', 'name': 'St. Louis Cardinals', 'sport': 'Baseball', 'league': 'MLB', 'path': 'baseball/mlb', 'espn_id': 24},
    {'team_id': 'team.football.men.nfl.kansas-city-chiefs', 'name': 'Kansas City Chiefs', 'sport': 'Football', 'league': 'NFL', 'path': 'football/nfl', 'espn_id': 12},
    {'team_id': 'team.baseball.men.mlb.houston-astros', 'name': 'Houston Astros', 'sport': 'Baseball', 'league': 'MLB', 'path': 'baseball/mlb', 'espn_id': 18},
    {'team_id': 'team.football.men.ncaa.missouri-tigers', 'name': 'Missouri Tigers', 'sport': 'Football', 'league': 'NCAA', 'path': 'football/college-football', 'espn_id': 142},
]

def favorite_team(team_id=None, team_index=None):
    """A schedule-covered favorite by canonical catalog ID. The numeric index is accepted only for the already-installed
    TV build that predates canonical IDs; a request that names a team_id never falls back to it."""
    if team_id is not None:
        return next((t for t in FAVORITE_TEAMS if t['team_id'] == team_id), None) if isinstance(team_id, str) else None
    if isinstance(team_index, int) and not isinstance(team_index, bool) and 0 <= team_index < len(FAVORITE_TEAMS):
        return FAVORITE_TEAMS[team_index]
    return None

def _schedule_event(event, team_espn_id):
    competition = event.get('competitions', [{}])[0]
    status = competition.get('status', {}) or {}
    kind = status.get('type', {}) or {}
    competitors = competition.get('competitors', [])
    home = next((c for c in competitors if c.get('homeAway') == 'home'), {})
    away = next((c for c in competitors if c.get('homeAway') == 'away'), {})
    home_id=str((home.get('team') or {}).get('id'))
    away_id=str((away.get('team') or {}).get('id'))
    if str(team_espn_id) not in (home_id,away_id):
        return None, False, None, kind.get('state')
    is_home = home_id == str(team_espn_id)
    opponent = (away if is_home else home).get('team') or {}
    start = _timestamp(event.get('date'))
    return opponent.get('displayName'), is_home, start, kind.get('state')

def refresh_next_games(api):
    games = []
    coverage = []
    errors = 0
    for team_index, team in enumerate(FAVORITE_TEAMS):
        try:
            # ESPN's own 'nextEvent' pointer on the team endpoint lags behind real game state --
            # it kept pointing at a game that had already started (sometimes long since finished)
            # for hours afterward, which the future-only check downstream correctly refuses to
            # show, so the team just silently dropped out of "next game" instead of falling back
            # to the real next one. Fetching the actual schedule and picking the first game whose
            # own status is still 'pre' (same technique team_hub() already uses for its games
            # list, including the postponed-game trap: 'post' with no completion is not "next")
            # doesn't have that lag.
            schedule = _get(BASE + '/' + team['path'] + '/teams/' + str(team['espn_id']) + '/schedule?fixture=true')
            upcoming = []
            for event in schedule.get('events', []):
                opponent, is_home, start, state = _schedule_event(event, team['espn_id'])
                if opponent and start and state == 'pre':
                    upcoming.append((start, event, opponent, is_home))
            if not upcoming:
                coverage.append({'team_id':team['team_id'],'team_index':team_index,'team_name':team['name'],
                                 'status':'no_upcoming_event','source':'ESPN schedule'})
                continue
            upcoming.sort(key=lambda u: u[0])
            start, event, opponent, is_home = upcoming[0]
            competition=(event.get('competitions') or [{}])[0]
            opponent_entry=next((c for c in competition.get('competitors',[]) if
                str((c.get('team') or {}).get('id'))!=str(team['espn_id'])),{})
            games.append({'id': 'next-' + str(team['espn_id']) + '-' + str(event.get('id', '')),
                'team_id': team['team_id'], 'team_index': team_index, 'team_name': team['name'], 'sport': team['sport'], 'league': team['league'],
                'opponent': opponent, 'home': is_home,
                'competition': team['league'], 'kickoff': start,
                'espn_id':str(event.get('id') or ''),'team_espn_id':str(team['espn_id']),
                'opponent_espn_id':str((opponent_entry.get('team') or {}).get('id') or '')})
            coverage.append({'team_id':team['team_id'],'team_index':team_index,'team_name':team['name'],
                             'status':'available','source':'ESPN schedule'})
        except Exception:
            errors += 1
            coverage.append({'team_id':team['team_id'],'team_index':team_index,'team_name':team['name'],
                             'status':'unavailable','source':'ESPN schedule'})
    result = {'updated_at': int(time.time()), 'games': games, 'coverage':coverage,'errors': errors}
    api.save_cache('next-games', result)
    return {'games': len(games), 'errors': errors}

def _roster_players(payload):
    # NFL/NCAA group the roster by position ({'position':..,'items':[...players]}); soccer
    # returns a flat list of player objects directly — normalize both to one player list.
    players = []
    for entry in payload.get('athletes') or []:
        players.extend(entry.get('items', [entry]))
    return players

HUB_EVENTS_CAP = 120
HUB_NEWS_CAP = 8

def _hub_event(game):
    """One schedule entry for the TV's team hub: only the facts the screens show, the provider's status kept verbatim."""
    def side(s):
        return {'id': s.get('id', ''), 'name': s.get('name', ''), 'abbreviation': s.get('abbreviation', ''),
                'score': s.get('score'), 'rank': s.get('rank')}
    return {'espn_id': game['espn_id'], 'sport': game['sport'], 'league': game['league'],
            'home': side(game['home']), 'away': side(game['away']), 'state': game['state'],
            'status_name': game['status_name'], 'completed': game['completed'], 'detail': game['detail'],
            'period': game['period'], 'display_clock': game['display_clock'], 'venue': game['venue'],
            'networks': game['networks'], 'season_type': game['season_type'], 'season_slug': game['season_slug'],
            'start': game['start']}

class HubUnavailable(Exception):
    """Every team-hub section failed: the provider (or this server's route to it) is down, which is
    not the same as a team that genuinely has no record, schedule, roster or news."""

def team_hub(api, team):
    """On-demand condensed team page: record/rank/standing, the next 5 scheduled games, a
    full roster, any reported injuries, and a few news headlines — fetched live, not
    cached/scheduled, since this is only requested when a user actually opens that team's hub.

    Each section is independent: any single call can hiccup, but that only blanks its own
    section (and is named in 'unavailable'), never the whole hub. The sections are fetched
    concurrently so a slow provider costs one timeout, not four in series (four serial 15s
    timeouts outlast the TV client's 35s read timeout and the hub then never rendered at all).
    If EVERY section fails the hub raises HubUnavailable instead of answering an
    indistinguishable-from-real empty HTTP 200.
    """
    base = BASE + '/' + team['path'] + '/teams/' + str(team['espn_id'])
    result = {'record': None, 'rank': None, 'standing': None, 'games': [], 'roster': [], 'injuries': [], 'news': []}

    def overview():
        team_data = (_get(base).get('team') or {})
        items = (team_data.get('record') or {}).get('items') or []
        overall = next((i for i in items if i.get('type') == 'total'), items[0] if items else None)
        return {'record': overall.get('summary') if overall else None,
                'rank': team_data.get('rank'), 'standing': team_data.get('standingSummary')}

    def games():
        # '?fixture=true' is required for soccer: without it ESPN's schedule endpoint returns
        # only a handful of already-played games and no upcoming fixtures at all (verified
        # against Barcelona's own schedule — 4 events, all completed, with the flag omitted).
        # Harmless elsewhere: NFL returns the identical 17-game season with or without it.
        schedule = _get(base + '/schedule?fixture=true')
        # A postponed game is 'completed': false too, same as a genuine upcoming one -- ESPN
        # just never re-schedules it in this feed. Filtering on 'not completed' alone let stale
        # postponed games (weeks old) sort ahead of real upcoming games by original date, filling
        # most of the 5 slots with games that already aren't happening. 'state' == 'pre' is the
        # actual "hasn't been played, is still ahead of us" signal.
        normalized = []
        everything = []
        for event in schedule.get('events', []):
            game = _normalize(team['sport'], team['league'], event)
            if game:
                everything.append(game)
                if game['state'] == 'pre' and game['start']:
                    normalized.append(game)
        # 'games' keeps its original meaning (next five upcoming) for TVs that predate the redesigned hub;
        # 'events' is the whole provider schedule (results, live, postponed, TBD) in a compact shape.
        return {'games': sorted(normalized, key=lambda g: g['start'])[:5],
                'events': [_hub_event(g) for g in sorted(everything, key=lambda g: (g['start'] is None, g['start'] or 0, str(g['espn_id'])))[:HUB_EVENTS_CAP]]}

    def roster():
        players = _roster_players(_get(base + '/roster'))
        return {'roster': [{'name': p.get('displayName'), 'jersey': p.get('jersey'),
                    'position': (p.get('position') or {}).get('abbreviation')} for p in players],
                'injuries': [{'name': p.get('displayName'), 'status': p['injuries'][0].get('status')}
                    for p in players if p.get('injuries')][:6]}

    def news():
        data = _get(BASE + '/' + team['path'] + '/news?team=' + str(team['espn_id']))
        # id / url / source are additive: the provider's own article id and web link, and who supplied the feed.
        return {'news': [{'headline': a.get('headline'), 'published': a.get('published'), 'id': str(a.get('id') or ''),
                          'url': ((a.get('links') or {}).get('web') or {}).get('href'), 'source': 'ESPN'}
                    for a in (data.get('articles') or [])[:HUB_NEWS_CAP]]}

    sections = {'overview': overview, 'games': games, 'roster': roster, 'news': news}
    failed = []
    with ThreadPoolExecutor(max_workers=len(sections)) as pool:
        futures = {name: pool.submit(fn) for name, fn in sections.items()}
        for name, future in futures.items():
            try:
                result.update(future.result())
            except Exception:
                failed.append(name)
    if len(failed) == len(sections):
        raise HubUnavailable()
    result['unavailable'] = failed
    return result

def _clean_rank(value):
    # ESPN's curatedRank.current uses 99 as a sentinel for "not ranked" (every NFL team reports
    # 99, since the NFL has no top-25 poll at all) -- not a real poll position. Only 1-25 is ever
    # a genuine AP/Coaches-poll-style rank; anything else is "no rank", not "ranked 99th".
    return value if isinstance(value, int) and 1 <= value <= 25 else None

def _team_keys(team):
    """Canonical identity keys for an ESPN team. Deliberately excludes bare location: 'Tennessee'
    is also the location of Tennessee Tech, and 'Barcelona' of nobody but Barcelona."""
    return sorted({k for k in (guide.team_key(team.get(f) or '') for f in ('displayName', 'shortDisplayName')) if k})

def _networks(competition):
    names = []
    for broadcast in competition.get('broadcasts') or []:
        names.extend(broadcast.get('names') or [])
    for geo in competition.get('geoBroadcasts') or []:
        if ((geo.get('type') or {}).get('shortName') or 'TV') == 'TV':
            name = (geo.get('media') or {}).get('shortName')
            if name:
                names.append(name)
    return list(dict.fromkeys(n for n in names if n))

def _normalize(sport, league, event):
    competition = event.get('competitions', [{}])[0]
    status = competition.get('status', {}) or {}
    kind = status.get('type', {}) or {}
    sides = {}
    for competitor in competition.get('competitors', []):
        side = competitor.get('homeAway')
        if side not in ('home', 'away'):
            continue
        team = competitor.get('team', {}) or {}
        sides[side] = {'id':str(team.get('id') or ''),'name': team.get('displayName', ''),
                        'keys': _team_keys(team), 'logo': team.get('logo') or '',
                        'abbreviation': team.get('abbreviation', ''), 'score': competitor.get('score'),
                        'rank': _clean_rank((competitor.get('curatedRank') or {}).get('current')),
                        'tokens': sorted(team_tokens(team))}
    if 'home' not in sides or 'away' not in sides:
        return None
    start = _timestamp(event.get('date'))
    season = event.get('season') or {}
    return {'espn_id': event.get('id'), 'sport': sport, 'league': league,
            'home': sides['home'], 'away': sides['away'],
            'status_name': kind.get('name') or '',
            'venue': ((competition.get('venue') or {}).get('fullName') or ''),
            'networks': _networks(competition),
            'state': kind.get('state'), 'completed': bool(kind.get('completed')),
            'detail': kind.get('shortDetail') or kind.get('detail') or '',
            'period': status.get('period'), 'display_clock': status.get('displayClock'),
            'season_type': season.get('type'), 'season_slug': season.get('slug'), 'start': start}

# Games worth surfacing/highlighting even when no favorite team is involved.
KNOCKOUT_SLUGS = re.compile(r'round-of-16|quarterfinal|semifinal|\bfinal\b', re.I)

# Unlike NCAA football, ESPN's soccer scoreboard doesn't carry a per-event rank field at all
# (verified live: 'curatedRank' is null for every Premier League/La Liga competitor) -- "top 5"
# for these leagues has to come from the actual standings table, fetched and cached separately
# (see refresh_standings_top5), not something available on the event itself.
STANDINGS_LEAGUES = {'Premier League': 'soccer/eng.1', 'La Liga': 'soccer/esp.1'}

def refresh_standings_top5(api):
    top5 = {}
    errors = 0
    for league, path in STANDINGS_LEAGUES.items():
        try:
            data = _get('http://site.api.espn.com/apis/v2/sports/' + path + '/standings')
            groups = data.get('standings') or (data.get('children') or [{}])[0].get('standings', {}).get('entries')
            entries = groups[0].get('standings', {}).get('entries', []) if isinstance(groups, list) and groups and 'standings' in groups[0] else (groups or [])
            ranked = []
            for e in entries:
                rank = next((s.get('value') for s in e.get('stats', []) if s.get('name') == 'rank'), None)
                name = (e.get('team') or {}).get('displayName')
                if rank and name:
                    ranked.append((rank, name))
            top5[league] = [name for rank, name in sorted(ranked) if rank <= 5]
        except Exception:
            errors += 1
    result = {'updated_at': int(time.time()), 'top5': top5, 'errors': errors}
    api.save_cache('standings-top5', result)
    return result

# --- Phase 8: deterministic, explainable relevance ----------------------------------------------
# One pure evaluator is the single source of both the legacy boolean `notable` and the structured
# `relevance` payload, so the two cannot disagree. It reads only normalized event facts (sport,
# league, canonical participants, verified state, period/clock, scores, ranks, season metadata)
# plus the already-cached standings table. Anything missing, malformed or unsupported adds zero.
RELEVANCE_VERSION = 1
RELEVANCE_MAX_SCORE = 100
MAX_REASONS = 2

# Closed vocabulary: code -> (weight, label). Weight order is also the reason display order
# (heavier first, then the tuple order below), so ordering is stable and provider independent.
RELEVANCE_FEATURES = (
    ('major_ufc', 45, 'Major UFC fight'),
    ('ranked_matchup', 40, 'Two ranked teams'),
    ('knockout', 35, 'Knockout or postseason game'),
    ('close_late', 30, 'One-score game late'),
    ('rivalry', 25, 'Rivalry game'),
)
_WEIGHT = {code: weight for code, weight, _ in RELEVANCE_FEATURES}
_LABEL = {code: label for code, _, label in RELEVANCE_FEATURES}

# Sport-specific "late and close" definitions: (first qualifying period, max margin). Only sports
# whose ESPN scoreboard period is unambiguous are listed; everything else contributes zero.
# Football: 4th quarter or later, one possession (8). Baseball/softball: 7th inning or later,
# within 2 runs (softball games are 7 innings, so 6th is the first "late" frame there).
LATE_CLOSE = {
    ('Football', 'NFL'): (4, 8), ('Football', 'NCAA'): (4, 8),
    ('Baseball', 'MLB'): (7, 2), ('Softball', 'NCAA'): (6, 2),
}
SOCCER_LATE_MINUTE = 75   # second half, 75th minute or later, within one goal
SOCCER_LATE_LEAGUES = frozenset(league for sport, league in ENDPOINTS if sport == 'Soccer')
STANDINGS_MAX_AGE = 86400

# Reviewed product metadata (not a user preference): exact ESPN display-name pairs per sport and
# league. Membership is by canonical team key equality of both sides, never by substring.
_RIVALRY_NAMES = {
    ('Football', 'NCAA'): [('Alabama Crimson Tide', 'Auburn Tigers'),
                           ('Ohio State Buckeyes', 'Michigan Wolverines'),
                           ('Georgia Bulldogs', 'Florida Gators'),
                           ('Texas Longhorns', 'Oklahoma Sooners'),
                           ('Tennessee Volunteers', 'Alabama Crimson Tide')],
    ('Football', 'NFL'): [('Green Bay Packers', 'Chicago Bears'),
                          ('Dallas Cowboys', 'Philadelphia Eagles'),
                          ('Kansas City Chiefs', 'Las Vegas Raiders')],
    ('Baseball', 'MLB'): [('New York Yankees', 'Boston Red Sox'),
                          ('Los Angeles Dodgers', 'San Francisco Giants'),
                          ('Chicago Cubs', 'St. Louis Cardinals')],
    ('Soccer', 'La Liga'): [('Real Madrid', 'Barcelona'), ('Atletico Madrid', 'Real Madrid')],
    ('Soccer', 'Premier League'): [('Liverpool', 'Manchester United'), ('Arsenal', 'Tottenham Hotspur'),
                                    ('Manchester City', 'Manchester United')],
    ('Soccer', 'Serie A'): [('Inter Milan', 'AC Milan'), ('Juventus', 'Inter Milan')],
}
RIVALRIES = {scope: frozenset(frozenset((guide.team_key(a), guide.team_key(b))) for a, b in pairs)
             for scope, pairs in _RIVALRY_NAMES.items()}

def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None

def _ranked_matchup(api, game):
    sport, league = game['sport'], game['league']
    if (sport, league) == ('Football', 'NCAA'):
        home, away = game['home'].get('rank'), game['away'].get('rank')
        return bool(home and away and 1 <= home <= 25 and 1 <= away <= 25)
    if league in STANDINGS_LEAGUES:
        cache = api.read_cache('standings-top5') or {}
        # Standings move slowly -- a day-old table is still fine for "top-5 clash"; older is stale.
        if int(time.time()) - cache.get('updated_at', 0) > STANDINGS_MAX_AGE:
            return False
        top5 = set(cache.get('top5', {}).get(league, []))
        return bool(top5) and game['home']['name'] in top5 and game['away']['name'] in top5
    return False

def _knockout(game):
    if game['league'] in ('NFL', 'MLB'):
        return game.get('season_type') == 3
    if game['league'] == 'UEFA Champions League':
        return bool(KNOCKOUT_SLUGS.search(game.get('season_slug') or ''))
    return False

def _close_late(game):
    # Only an independently verified live game can be "late"; never inferred from airtime.
    if game.get('state') != 'in' or game.get('completed'):
        return False
    home, away = _int(game['home'].get('score')), _int(game['away'].get('score'))
    period = game.get('period')
    if home is None or away is None or not isinstance(period, int) or isinstance(period, bool):
        return False
    margin = abs(home - away)
    scope = (game['sport'], game['league'])
    if scope in LATE_CLOSE:
        first_late, max_margin = LATE_CLOSE[scope]
        return period >= first_late and margin <= max_margin
    if game['league'] in SOCCER_LATE_LEAGUES and game['sport'] == 'Soccer':
        match = re.match(r"^\s*(\d{1,3})(?:\+\d+)?\s*'?\s*$", str(game.get('display_clock') or ''))
        return bool(match) and period >= 2 and int(match.group(1)) >= SOCCER_LATE_MINUTE and margin <= 1
    return False

def _rivalry(game):
    pairs = RIVALRIES.get((game['sport'], game['league']))
    if not pairs:
        return False
    for a in game['home'].get('keys') or []:
        for b in game['away'].get('keys') or []:
            if a != b and frozenset((a, b)) in pairs:
                return True
    return False

def _major_ufc(game):
    # Fail closed. The ESPN scoreboard pipeline carries no UFC endpoint, and the guide's event
    # facts expose no explicit normalized card/main-event metadata, so a UFC fight cannot be shown
    # to be a real, major, verified-live event. A title token is never enough (it would admit
    # studio shows, replays and prelims). Becomes supportable only once such metadata exists.
    return False

def evaluate_relevance(api, game):
    """Pure, bounded, deterministic evaluation. Returns the structured `relevance` payload plus
    the derived boolean. Any evaluation error yields the safe default."""
    safe = {'notable': False, 'relevance': {'version': RELEVANCE_VERSION, 'score': 0, 'reasons': []}}
    try:
        checks = {'major_ufc': _major_ufc(game), 'ranked_matchup': _ranked_matchup(api, game),
                  'knockout': _knockout(game), 'close_late': _close_late(game), 'rivalry': _rivalry(game)}
        hits = [code for code, _, _ in RELEVANCE_FEATURES if checks[code]]
    except Exception:
        return safe
    if not hits:
        return safe
    score = min(RELEVANCE_MAX_SCORE, sum(_WEIGHT[c] for c in hits))
    reasons = [{'code': c, 'label': _LABEL[c]} for c in hits[:MAX_REASONS]]
    return {'notable': True, 'relevance': {'version': RELEVANCE_VERSION, 'score': score, 'reasons': reasons}}

def is_notable(api, game):
    return evaluate_relevance(api, game)['notable']

def refresh(api):
    games = []
    errors = 0
    for (sport, league), path in ENDPOINTS.items():
        try:
            data = _fetch(path)
        except Exception:
            errors += 1
            continue
        for event in data.get('events', []):
            normalized = _normalize(sport, league, event)
            if normalized:
                games.append(normalized)
    result = {'updated_at': int(time.time()), 'games': games, 'endpoints': len(ENDPOINTS), 'errors': errors}
    api.save_cache('live-scores', result)
    return {'games': len(games), 'errors': errors}

def _identity_groups(keys):
    return {i for group in guide.COLLISION_GROUPS for i in group if i in keys}

def _pair_matches(pair, game):
    """Exact canonical identity: each named side equals one distinct ESPN side's own team key."""
    home, away = set(game['home'].get('keys') or ()), set(game['away'].get('keys') or ())
    a, b = pair
    return bool((a in home and b in away) or (a in away and b in home))

def _collides(pair, game):
    """A token match must not cross a known name collision (Espanyol 'de Barcelona')."""
    espn = set((game['home'].get('keys') or ())) | set(game['away'].get('keys') or ())
    return any((set(pair) & group) != (espn & group) for group in guide.COLLISION_GROUPS)

def _best_match(cache, sport, league, tokens_a, tokens_b, near=None, window=6 * 3600, allow_recent_replay=False, espn_id=None, pair=None):
    fresh = int(time.time()) - cache.get('updated_at', 0) < 20 * 60
    if not fresh:
        return None
    matched = []
    exact = []
    for game in cache.get('games', []):
        if game['sport'] != sport or game['league'] != league:
            continue
        if espn_id and str(game.get('espn_id'))==str(espn_id):
            return game
        if pair and _pair_matches(pair, game):
            exact.append(game)
            continue
        home_tokens, away_tokens = set(game['home']['tokens']), set(game['away']['tokens'])
        if ((tokens_a & home_tokens and tokens_b & away_tokens) or
            (tokens_a & away_tokens and tokens_b & home_tokens)):
            if not (pair and _collides(pair, game)):
                matched.append(game)
    # Exact canonical identity outranks the looser name-token overlap.
    matched = exact or matched
    if not matched:
        return None
    timed=[]
    for game in matched:
        if not near:
            gap = 0  # no expected time to compare against -- every candidate is equally plausible
        elif game.get('start'):
            gap = abs(game['start'] - near)
            if gap > window:
                continue
        else:
            # An expected time was given, but this candidate's own start is unknown -- that's
            # the worst case, not a perfect match. Leaving this at gap=0 (like the "no start"
            # branch above) would let an unrelated same-matchup game with unknown timing always
            # win over a real candidate with a known, merely-nonzero gap.
            gap = float('inf')
        timed.append((gap,game))
    best=None
    if timed:
        best_gap=min(gap for gap,_ in timed)
        nearest=[game for gap,game in timed if gap==best_gap]
        best=nearest[0] if len(nearest)==1 else None
    if timed or not allow_recent_replay:
        return best
    # Nothing near the expected time (or no expected time at all): this EPG slot may be
    # airing a replay of an already-finished game between the same two teams, potentially
    # from days ago — pick the most recent completed match instead of giving up.
    completed = [g for g in matched if g['completed'] and g.get('start')]
    return max(completed, key=lambda g: g['start']) if completed else None

def for_event(api, event):
    """event: a guide.current() dict with title/sport/league/start."""
    league_key = (event.get('sport'), event.get('league'))
    if league_key not in ENDPOINTS:
        return None
    cache = api.read_cache('live-scores') or {}
    title = event.get('title', '')
    tokens = _tokens(title)
    # Only a slot that has actually begun can be an old game's rebroadcast; an event still ahead
    # of us must not be pinned to a finished game just because the same teams met earlier.
    underway = bool(event.get('start')) and event['start'] <= int(time.time())
    match = _best_match(cache, event['sport'], event['league'], tokens, tokens, near=event.get('start'),
                        allow_recent_replay=underway, pair=guide.participant_pair(event))
    if not match:
        return None
    return _present(api, match, event.get('start'), event.get('end'), cache.get('updated_at'))

def for_fixture(api, fixture, team_name):
    """fixture: a schedule.py-style dict with opponent/kickoff/competition."""
    league_key=(fixture.get('sport'),fixture.get('league'))
    if league_key not in ENDPOINTS:
        league = fixture.get('competition') or ''
        candidates=[k for k in ENDPOINTS if k[1].casefold()==league.casefold()]
        league_key=candidates[0] if len(candidates)==1 else None
    if league_key is None:
        return None
    cache = api.read_cache('live-scores') or {}
    team_tok, opp_tok = _tokens(team_name), _tokens(fixture.get('opponent', ''))
    match = _best_match(cache, league_key[0], league_key[1], team_tok, opp_tok,
                        near=fixture.get('kickoff'),espn_id=fixture.get('espn_id'))
    if not match:
        return None
    return _present(api, match, fixture.get('kickoff'), fixture.get('kickoff', 0) + 4 * 3600,
                    cache.get('updated_at'))

def _present(api, game, slot_start, slot_end, checked_at=None):
    verified_live = game['state'] == 'in'
    now = int(time.time())
    # A finished game inside its guide slot is a rebroadcast only when ESPN says it began well
    # before this slot did; one that just ended within its own slot is simply final.
    began = game.get('start')
    replay = bool(slot_start and slot_end and game['completed'] and slot_start <= now < slot_end
                  and (not began or began < slot_start - guide.SAME_EVENT_START_TOLERANCE))
    evaluation = evaluate_relevance(api, game)
    return {'status_name': game.get('status_name') or '', 'venue': game.get('venue') or '',
            'networks': game.get('networks') or [],
            'home_logo': game['home'].get('logo') or '', 'away_logo': game['away'].get('logo') or '',
            'completed': game['completed'],'espn_id':game.get('espn_id'),'event_start':game.get('start'),'checked_at':checked_at,
            'home': game['home']['name'], 'away': game['away']['name'],
            'home_espn_id':game['home'].get('id'),'away_espn_id':game['away'].get('id'),
            'home_score': game['home']['score'], 'away_score': game['away']['score'],
            'home_rank': game['home'].get('rank'), 'away_rank': game['away'].get('rank'),
            'state': game['state'], 'detail': game['detail'], 'period': game['period'],
            'display_clock': game['display_clock'], 'verified_live': verified_live,
            'likely_replay': replay, 'notable': evaluation['notable'], 'relevance': evaluation['relevance']}
