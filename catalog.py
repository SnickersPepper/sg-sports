"""Backend-owned canonical favorite team / competition catalog (Phase 7A).

Every entry has an explicit, hand-assigned stable ID. IDs are never computed from display names, sport labels or list
positions, so renaming or reordering an entry never changes what a saved preference refers to. The catalog carries no
default selection: choosing favorites is always a per-profile preference.

`support` is honest metadata about what the backend can do today for the entry; being selectable implies nothing more.
  schedule         the backend fetches this team's next-game schedule (livescores.FAVORITE_TEAMS)
  program_matching a client ships identity data to match this team in programme listings
No DVR support is claimed for any entry here; recording eligibility is decided elsewhere.

`legacy_ids` are the IDs earlier builds synthesised from labels (sport + display name). They are used ONLY by the explicit
settings migration to translate already-saved preferences; nothing else resolves an entry through them.
"""
import re

ID_RE = re.compile(r'^(team|league)(\.[a-z0-9]+(-[a-z0-9]+)*){2,5}$')
GENDERS = ('men', 'women', 'mixed')


def _team(id, name, sport, gender, league, legacy_ids=(), schedule=False, program_matching=False):
    return {'id': id, 'kind': 'team', 'name': name, 'sport': sport, 'gender': gender, 'league': league,
            'legacy_ids': list(legacy_ids), 'support': {'schedule': schedule, 'program_matching': program_matching}}


def _league(id, name, sport, legacy_ids=()):
    return {'id': id, 'kind': 'league', 'name': name, 'sport': sport, 'legacy_ids': list(legacy_ids),
            'support': {'schedule': False, 'program_matching': False}}


# The seven favorites the installed Android build ships with (migrated explicitly; see settings migration 2).
_INSTALLED = [
    _team('team.soccer.men.la-liga.fc-barcelona', 'FC Barcelona', 'Soccer', 'men', 'La Liga', ['soccer.fc-barcelona'], True, True),
    _team('team.football.men.ncaa.tennessee-volunteers', 'Tennessee Volunteers', 'Football', 'men', 'NCAA', ['football.tennessee-volunteers'], True, True),
    _team('team.softball.women.ncaa.tennessee-volunteers', 'Tennessee Lady Volunteers', 'Softball', 'women', 'NCAA',
          ['softball.tennessee-volunteers', 'softball.lady-vols'], True, True),
    _team('team.baseball.men.mlb.st-louis-cardinals', 'St. Louis Cardinals', 'Baseball', 'men', 'MLB', ['baseball.st-louis-cardinals'], True, True),
    _team('team.football.men.nfl.kansas-city-chiefs', 'Kansas City Chiefs', 'Football', 'men', 'NFL', ['football.kansas-city-chiefs'], True, True),
    _team('team.baseball.men.mlb.houston-astros', 'Houston Astros', 'Baseball', 'men', 'MLB', ['baseball.houston-astros'], True, True),
    _team('team.football.men.ncaa.missouri-tigers', 'Missouri Tigers', 'Football', 'men', 'NCAA', ['football.missouri-tigers'], True, True),
]
# (slug, display name). The slug is the literal, permanent ID suffix.
_MLB = [('arizona-diamondbacks', 'Arizona Diamondbacks'), ('athletics', 'Athletics'), ('atlanta-braves', 'Atlanta Braves'), ('baltimore-orioles', 'Baltimore Orioles'),
        ('boston-red-sox', 'Boston Red Sox'), ('chicago-cubs', 'Chicago Cubs'), ('chicago-white-sox', 'Chicago White Sox'), ('cincinnati-reds', 'Cincinnati Reds'),
        ('cleveland-guardians', 'Cleveland Guardians'), ('colorado-rockies', 'Colorado Rockies'), ('detroit-tigers', 'Detroit Tigers'),
        ('kansas-city-royals', 'Kansas City Royals'), ('los-angeles-angels', 'Los Angeles Angels'), ('los-angeles-dodgers', 'Los Angeles Dodgers'),
        ('miami-marlins', 'Miami Marlins'), ('milwaukee-brewers', 'Milwaukee Brewers'), ('minnesota-twins', 'Minnesota Twins'), ('new-york-mets', 'New York Mets'),
        ('new-york-yankees', 'New York Yankees'), ('philadelphia-phillies', 'Philadelphia Phillies'), ('pittsburgh-pirates', 'Pittsburgh Pirates'),
        ('san-diego-padres', 'San Diego Padres'), ('san-francisco-giants', 'San Francisco Giants'), ('seattle-mariners', 'Seattle Mariners'),
        ('tampa-bay-rays', 'Tampa Bay Rays'), ('texas-rangers', 'Texas Rangers'), ('toronto-blue-jays', 'Toronto Blue Jays'), ('washington-nationals', 'Washington Nationals')]
_NFL = [('arizona-cardinals', 'Arizona Cardinals'), ('atlanta-falcons', 'Atlanta Falcons'), ('baltimore-ravens', 'Baltimore Ravens'), ('buffalo-bills', 'Buffalo Bills'),
        ('carolina-panthers', 'Carolina Panthers'), ('chicago-bears', 'Chicago Bears'), ('cincinnati-bengals', 'Cincinnati Bengals'), ('cleveland-browns', 'Cleveland Browns'),
        ('dallas-cowboys', 'Dallas Cowboys'), ('denver-broncos', 'Denver Broncos'), ('detroit-lions', 'Detroit Lions'), ('green-bay-packers', 'Green Bay Packers'),
        ('houston-texans', 'Houston Texans'), ('indianapolis-colts', 'Indianapolis Colts'), ('jacksonville-jaguars', 'Jacksonville Jaguars'),
        ('las-vegas-raiders', 'Las Vegas Raiders'), ('los-angeles-chargers', 'Los Angeles Chargers'), ('los-angeles-rams', 'Los Angeles Rams'),
        ('miami-dolphins', 'Miami Dolphins'), ('minnesota-vikings', 'Minnesota Vikings'), ('new-england-patriots', 'New England Patriots'),
        ('new-orleans-saints', 'New Orleans Saints'), ('new-york-giants', 'New York Giants'), ('new-york-jets', 'New York Jets'),
        ('philadelphia-eagles', 'Philadelphia Eagles'), ('pittsburgh-steelers', 'Pittsburgh Steelers'), ('san-francisco-49ers', 'San Francisco 49ers'),
        ('seattle-seahawks', 'Seattle Seahawks'), ('tampa-bay-buccaneers', 'Tampa Bay Buccaneers'), ('tennessee-titans', 'Tennessee Titans'),
        ('washington-commanders', 'Washington Commanders')]
_INSTALLED_IDS = {e['id'] for e in _INSTALLED}


def _league_teams(sport, league, slug, gender, rows):
    out = []
    for suffix, name in rows:
        id = 'team.%s.%s.%s.%s' % (sport.lower(), gender, slug, suffix)
        if id not in _INSTALLED_IDS:
            out.append(_team(id, name, sport, gender, league))
    return out


TEAMS = (_INSTALLED
         + [_team('team.soccer.women.liga-f.fc-barcelona', 'FC Barcelona Femení', 'Soccer', 'women', 'Liga F')]
         + _league_teams('Baseball', 'MLB', 'mlb', 'men', _MLB)
         + _league_teams('Football', 'NFL', 'nfl', 'men', _NFL))

LEAGUES = [
    _league('league.football.nfl', 'NFL', 'Football', ['nfl']),
    _league('league.football.ncaa', 'NCAA', 'Football'),  # legacy 'ncaa' was shared with Softball: ambiguous, deliberately not remapped
    _league('league.soccer.uefa-champions-league', 'UEFA Champions League', 'Soccer', ['uefa-champions-league']),
    _league('league.soccer.copa-libertadores', 'Copa Libertadores', 'Soccer', ['copa-libertadores']),
    _league('league.soccer.mls', 'MLS', 'Soccer', ['mls']),
    _league('league.soccer.argentina-primera-division', 'Argentina Primera División', 'Soccer', ['argentina-primera-division']),
    _league('league.soccer.la-liga', 'La Liga', 'Soccer', ['la-liga']),
    _league('league.soccer.premier-league', 'Premier League', 'Soccer', ['premier-league']),
    _league('league.soccer.ligue-1', 'Ligue 1', 'Soccer', ['ligue-1']),
    _league('league.soccer.bundesliga', 'Bundesliga', 'Soccer', ['bundesliga']),
    _league('league.soccer.serie-a', 'Serie A', 'Soccer', ['serie-a']),
    _league('league.baseball.mlb', 'MLB', 'Baseball', ['mlb']),
    _league('league.baseball.venezuela-lvbp', 'Venezuela LVBP', 'Baseball', ['venezuela-lvbp']),
    _league('league.softball.ncaa', 'NCAA', 'Softball'),
    _league('league.ufc.ufc', 'UFC', 'UFC', ['ufc']),
    _league('league.golf.pga-tour', 'PGA Tour', 'Golf', ['pga-tour']),
    _league('league.golf.lpga', 'LPGA', 'Golf', ['lpga']),
    _league('league.golf.dp-world-tour', 'DP World Tour', 'Golf', ['dp-world-tour']),
    _league('league.golf.liv-golf', 'LIV Golf', 'Golf', ['liv-golf']),
    _league('league.golf.majors', 'Majors', 'Golf', ['majors']),
]
ENTRIES = list(TEAMS) + LEAGUES
BY_ID = {e['id']: e for e in ENTRIES}


def validate():
    """Structural self-check used by tests: unique, well-formed IDs; no legacy-ID collisions; legacy IDs map to exactly one entry."""
    errors, seen_legacy = [], {}
    if len(BY_ID) != len(ENTRIES):
        errors.append('duplicate_id')
    for e in ENTRIES:
        if not ID_RE.match(e['id']) or not e['id'].startswith(e['kind'] + '.'):
            errors.append('bad_id:' + e['id'])
        if e['kind'] == 'team' and e['gender'] not in GENDERS:
            errors.append('bad_gender:' + e['id'])
        for legacy in e['legacy_ids']:
            if (e['kind'], legacy) in seen_legacy or legacy in BY_ID:
                errors.append('legacy_collision:' + legacy)
            seen_legacy[(e['kind'], legacy)] = e['id']
    return errors


LEGACY_MAP = {}
for _e in ENTRIES:
    for _l in _e['legacy_ids']:
        LEGACY_MAP[(_e['kind'], _l)] = _e['id']


def migrate_ids(kind, ids):
    """Translate saved preference IDs of one kind ('team' for favorites, 'league' for leagues) through the explicit legacy table.

    Canonical and unknown IDs are preserved exactly; order is kept; a translated ID that duplicates an earlier one is dropped.
    """
    out = []
    for value in ids:
        mapped = LEGACY_MAP.get((kind, value), value)
        if mapped not in out:
            out.append(mapped)
    return out


def view():
    """The wire contract: everything a client needs to list, label and filter selectable favorites and competitions."""
    def public(e):
        item = {'id': e['id'], 'name': e['name'], 'sport': e['sport'], 'support': dict(e['support'])}
        if e['kind'] == 'team':
            item.update(gender=e['gender'], league=e['league'])
        return item
    return {'version': 1, 'teams': [public(e) for e in TEAMS], 'leagues': [public(e) for e in LEAGUES]}
