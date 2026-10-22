"""Read the existing published IPTV guide without changing it or opening media."""
import datetime as dt
import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import lvbp

ROOT=Path('/guide')
LEAGUES={
 'Football':['NFL','NCAA'],
 'Soccer':['UEFA Champions League','Copa Libertadores','MLS','Argentina Primera División','La Liga','Premier League','Ligue 1','Bundesliga','Serie A'],
 'Baseball':['MLB',lvbp.LEAGUE], 'Softball':['NCAA'], 'UFC':['UFC'],
 'Golf':['PGA Tour','LPGA','DP World Tour','LIV Golf','Majors']}
RULES=[
 ('Soccer','UEFA Champions League',r'champions league'),('Soccer','Copa Libertadores',r'copa libertadores'),('Soccer','UEFA Europa League',r'europa league'),('Soccer','UEFA Conference League',r'conference league'),
 ('Soccer','Premier League',r'(?:english |england |barclays )?premier league'),
 # Checked ahead of the 'La Liga' pattern below, which "LALIGA HYPERMOTION" (Spain's second
 # division, a separate competition from the top-flight LaLiga EA Sports) would otherwise also
 # match — it's excluded, not "La Liga", since only the first division was ever wanted here.
 ('Soccer','La Liga 2',r'hypermotion|segunda divisi[oó]n'),
 ('Soccer','La Liga',r'la ?liga'),('Soccer','Serie A',r'\bserie a\b'),('Soccer','Bundesliga',r'bundesliga'),('Soccer','Ligue 1',r'ligue 1'),('Soccer','Primeira Liga',r'primeira liga|liga portugal'),('Soccer','Eredivisie',r'eredivisie'),
 ('Soccer','Argentina Primera División',r'argentin.*(?:soccer|f[uú]tbol|primera|liga)|liga profesional.*(?:argentina|f[uú]tbol)|\blpf\b'),('Soccer','Venezuela Liga FUTVE',r'liga futve|venezuel.*(?:soccer|f[uú]tbol|primera)'),('Soccer','MLS',r'\bmls\b|major league soccer'),
 ('Baseball','Venezuela LVBP',r'\blvbp\b|liga venezolana.*b[eé]isbol|venezuel.*(?:baseball|b[eé]isbol)'),('Baseball','Banana Ball',r'banana ball'),('Baseball','MLB',r'\bmlb\b|major league baseball'),
 ('Softball','NCAA',r'(?:ncaa|college).*softball|softball.*(?:ncaa|college)'),
 ('Football','High School Football',r'high school football'),('Football','NFL',r'\bnfl\b|national football league'),('Football','NCAA',r'college football|ncaa.*football'),
 ('UFC','UFC',r'\bufc\b|ultimate fighting championship'),
 ('Golf','LPGA',r'\blpga\b'),('Golf','DP World Tour',r'dp world tour'),('Golf','LIV Golf',r'liv golf'),('Golf','PGA Tour',r'pga tour'),('Golf','Majors',r'(?:golf.*(?:masters|open championship|pga championship))')]

EXCLUDED_LEAGUES={'Primeira Liga','Eredivisie','UEFA Europa League','UEFA Conference League','Venezuela Liga FUTVE','La Liga 2','High School Football'}
# Classified and kept in the guide cache so the official LVBP schedule can join guide listings, but
# never listed on their own: a guide entry with no schedule event must not invent a game.
SCHEDULE_BACKED_LEAGUES={lvbp.LEAGUE}
HIDDEN_LEAGUES=EXCLUDED_LEAGUES|SCHEDULE_BACKED_LEAGUES

# Most "Soccer" guide entries never spell out a league in the title at all — just "Soccer ·
# Team A vs Team B" — so the only way to tell a wanted domestic league (La Liga, Premier
# League, ...) apart from an unwanted one (Saudi Pro League, German 3. Liga, US college
# soccer, ...) is by recognizing one of the two team names. Rosters cover only the seven
# domestic leagues with a stable per-season team list; Champions League and Copa Libertadores
# draw from many leagues each year and are identified by their explicit RULES text instead.
# The same problem shows up for MLB team-branded channels ("MLB St. Louis Cardinals HD"),
# whose own guide title is often nothing but "Team A @ Team B" with no "MLB" anywhere in it.
def _load_roster(filename):
    try:
        raw=json.loads((Path(__file__).parent/filename).read_text())
    except (OSError,ValueError):
        return {}
    return {alias.casefold():league for alias,league in raw.items()}

def _roster_pattern(rosters, exclusion=''):
    return re.compile(r'\b(?:'+'|'.join(sorted((re.escape(a) for a in rosters),key=len,reverse=True))+r')\b'+exclusion,re.I) if rosters else None

def _roster_league(text,rosters,pattern):
    # A handful of aliases are bare city/short names ("Chicago", "Orlando", ...) that a
    # same-name team in a league we don't roster can also go by — one such match isn't
    # trustworthy alone. Requiring two matches that agree on the same league is what "Sevilla
    # vs Valencia" gives (both resolve to La Liga) but "Bay FC vs. Orlando" doesn't (only
    # "Orlando" resolves at all).
    if not pattern:return None
    matches=pattern.findall(text)
    if len(matches)<2:return None
    leagues={rosters[m.casefold()] for m in matches}
    return leagues.pop() if len(leagues)==1 else None

SOCCER_ROSTERS=_load_roster('soccer_teams.json')
SOCCER_TEAM_PATTERN=_roster_pattern(SOCCER_ROSTERS)
MLB_ROSTERS=_load_roster('mlb_teams.json')
MLB_TEAM_PATTERN=_roster_pattern(MLB_ROSTERS)

def soccer_team_league(text):return _roster_league(text,SOCCER_ROSTERS,SOCCER_TEAM_PATTERN)
def mlb_team_league(text):return _roster_league(text,MLB_ROSTERS,MLB_TEAM_PATTERN)

# A provider "New episode" badge some channels embed directly in the title text.
NEW_BADGE=re.compile(r'[ \t]*ᴺᵉʷ[ \t]*')
MATCHUP_WORD=re.compile(r'\bvs\.?\b|\bat\b|@|\s-\s',re.I)
# A companion/wagering show can title itself exactly like a real matchup ("Missouri vs.
# Kansas College Football In-Game Live Gameday") — the tell is descriptive text trailing the
# matchup itself, which a real broadcast's title never carries.
SHOW_WORD=re.compile(r'\b(?:gameday|wagering|in-game|highlights|postgame|pregame|recap|preview|coverage|report|sportscenter|roundtable)\b',re.I)

def is_game_matchup(matchup):
    """Whether a split_title() matchup candidate actually reads as 'Team A vs/at/@ Team B',
    independent of where it came from — a real provider <sub-title> is no more reliable a
    signal here than an embedded newline; a venue name or episode title can show up in either."""
    return bool(matchup) and bool(MATCHUP_WORD.search(matchup)) and not SHOW_WORD.search(matchup)

def split_title(raw_title,xml_subtitle):
    """Split one EPG title into (competition, matchup) for display.

    Real guide data almost never populates XMLTV's own <sub-title> element (verified against
    the live guide: ~1 in 44 on-now programs has one) — the actual separation, when a program
    has it at all, is an embedded newline or a "Competition: Team A vs Team B" colon prefix
    inside the single <title> string, e.g. "College Football\nSMU at Florida State" or
    "Soccer: FIU vs Stetson". <sub-title> is still honored first when a provider does supply it.
    """
    text=NEW_BADGE.sub('',raw_title).strip()
    if xml_subtitle:
        return text,NEW_BADGE.sub('',xml_subtitle).strip()
    if '\n' in text:
        competition,matchup=text.split('\n',1)
        return competition.strip(),matchup.strip()
    if ': ' in text:
        prefix,rest=text.split(': ',1)
        if MATCHUP_WORD.search(rest):
            return prefix.strip(),rest.strip()
    return text,''

def classify(title,categories):
    text=' '.join([title,*categories])
    if re.search(r'\bwnba\b',text,re.I):return None
    for sport,league,pattern in RULES:
        if re.search(pattern,text,re.I):return None if league in EXCLUDED_LEAGUES else (sport,league)
    if re.search(r'soccer|fútbol|futbol',text,re.I):
        league=soccer_team_league(text)
        return ('Soccer',league) if league else None
    for sport,pattern in [('Softball',r'softball'),('Baseball',r'baseball'),('Football',r'football'),('Golf',r'golf')]:
        if re.search(pattern,text,re.I):
            # Unlike soccer's whitelist, baseball content we can't identify a team for (minor
            # league, international, ...) still belongs in the app — just without a league name.
            if sport=='Baseball':
                if lvbp.team_league(text):return sport,lvbp.LEAGUE
                league=mlb_team_league(text)
                if league:return sport,league
            return sport,'Unspecified competition'
    return None

# Conference-vs-conference collisions (e.g. bare "Georgia" also matching "Georgia Tech", a
# different conference) are already resolved at roster-build time in
# .dev/build_ncaa_conferences.py -- an ambiguous short alias is dropped there, not here, so a
# single match against this roster is trustworthy on its own (unlike soccer/MLB team rosters,
# which require two team names agreeing since a bare city name alone isn't enough evidence).
# Same disambiguation the Android app's own team-identity matcher already applies (GameMapper's
# ncaaExclusion): a bare school name like "Iowa" must not match inside a different school's name
# that happens to start with it ("Iowa State") -- letting both "iowa" and "iowa state" live in the
# roster (each mapped to their own real conference) needs this so a title naming both teams
# ("Iowa State at Iowa") credits each school to its own conference instead of the shorter alias
# either being dropped entirely or mismatching into the longer name.
NCAA_SUFFIX_EXCLUSION=r'(?![ -](?:state|tech|southern|atlantic|international|el[ -]paso|san antonio|a&m|martin))'
# The mirror-image case: "Kentucky" must not match inside "Western Kentucky" (Conference USA,
# untracked), nor "North Carolina" inside a school like "North Carolina Wesleyan" -- a directional
# qualifier *before* the alias, rather than a suffix after it. Python's re module requires a
# fixed-width lookbehind, and these words aren't all the same length, so this is checked in plain
# code (see the finditer loop in conferences()) against the text immediately preceding each match,
# rather than folded into NCAA_PATTERN itself. See .dev/build_ncaa_conferences.py's matching
# is_safe_prefix_collision, which is what lets a build keep the short alias in the roster at all
# once this runtime check can be trusted to rule out the ambiguous case on its own.
NCAA_DIRECTIONAL_PREFIX=re.compile(r'\b(?:west(?:ern)?|east(?:ern)?|north(?:ern)?|south(?:ern)?|central)\s+$',re.I)
NCAA_ROSTERS=_load_roster('ncaa_conferences.json')
NCAA_PATTERN=_roster_pattern(NCAA_ROSTERS, NCAA_SUFFIX_EXCLUSION)
# The conference's own name, spoken directly ("SEC Championship", "Big Ten Media Days"), is a
# safe signal independent of any team-name collision risk -- unlike a bare school name, no other
# conference can also be named "Big 12" or "ACC".
CONFERENCE_NAME=re.compile(r'\b(?:sec|southeastern conference|big ten|acc|atlantic coast conference|big 12|pac-?12|american(?: athletic)? conference|mountain west)\b',re.I)
CONFERENCE_NAME_TO_LEAGUE={'sec':'SEC','southeastern conference':'SEC','big ten':'Big Ten','acc':'ACC','atlantic coast conference':'ACC','big 12':'Big 12','pac-12':'Pac-12','pac12':'Pac-12','american conference':'American','american athletic conference':'American','mountain west':'Mountain West'}
def conferences(title,league):
    """Every conference either side of the matchup belongs to, not just the first team named --
    a cross-conference game (e.g. a Big 12 team at an SEC team) belongs in both conferences'
    tabs, not only the tab of whichever team the title happens to mention first."""
    if league!='NCAA' or re.search(r'preview|highlights|recap|studio|\b(?:sec|big ten|big 12|acc|pac-?12|american|mountain west) (?:now|inside|nation)\b|talk|countdown|press conference|in the booth|the difference',title,re.I):return []
    if not re.search(r'\b(?:vs\.?|versus|at|championship|tournament)\b|@',title,re.I):return []
    found=[]
    for match in (NCAA_PATTERN.finditer(title) if NCAA_PATTERN else []):
        if NCAA_DIRECTIONAL_PREFIX.search(title,0,match.start()):continue
        league_name=NCAA_ROSTERS.get(match.group(0).casefold())
        if league_name and league_name not in found:found.append(league_name)
    if found:return found
    name=CONFERENCE_NAME.search(title)
    return [CONFERENCE_NAME_TO_LEAGUE[name.group(0).casefold()]] if name else []

def timestamp(value):
    if not value or not re.fullmatch(r'\d{14} [+-]\d{4}',value):return None
    try:return int(dt.datetime.strptime(value,'%Y%m%d%H%M%S %z').timestamp())
    except ValueError:return None

# A provider's guide slot is a fixed block (e.g. noon-3:30) that a real game routinely runs past
# -- overtime, weather delays, extra innings. The slot ending is not the game ending, so both
# refresh() (below, so a recently-ended slot is still stored at all) and current() (so it's still
# offered as a candidate) get this same grace window; a card in it is only actually dropped once
# ESPN's own live-score status says the game is over (see tv.attach_live_events) -- this is just
# the outer time bound on how long a slot can be resurrected by that check, a safety ceiling
# against a stale entry lingering forever if ESPN never reports on it at all.
GUIDE_GRACE_SECONDS=4*3600
PREGAME_TUNE_SECONDS=30*60
SAME_EVENT_START_TOLERANCE=30*60

TEAM_NOISE=re.compile(r"[^a-z0-9]+")
ALTERNATE_BROADCAST=re.compile(r'\b(?:peyton\s+(?:and|&)\s+eli|manningcast|alternate(?:\s+commentary)?|en espa[nñ]ol|spanish)\b',re.I)
FAVORITE_ALIASES={
    'fc barcelona':'barcelona','barca':'barcelona',
    'tennessee volunteers':'tennessee','vols':'tennessee',
    'tennessee lady volunteers':'tennessee','lady vols':'tennessee',
    'st louis cardinals':'st louis cardinals','stl cardinals':'st louis cardinals',
    'kansas city chiefs':'kansas city chiefs','kc chiefs':'kansas city chiefs',
    'houston astros':'houston astros','missouri tigers':'missouri','mizzou':'missouri'}
TEAM_ALIASES={
    'rcd espanol de barcelona':'espanyol','espanol de barcelona':'espanyol',
    'rcd espanyol de barcelona':'espanyol','espanyol de barcelona':'espanyol',
    'espanol':'espanyol',
}
# Identities whose long-form names contain another club's name. Matching must never let one
# stand in for the other: an Espanyol listing is not a Barcelona listing and vice versa.
COLLISION_GROUPS=({'barcelona','espanyol'},)

def _team_name(value):
    """Normalize punctuation plus the seven favorites' explicit, reviewed aliases."""
    folded=unicodedata.normalize('NFKD',value.casefold()).encode('ascii','ignore').decode('ascii')
    normalized=' '.join(TEAM_NOISE.sub(' ',folded).split())
    normalized=re.sub(r'^(?:rcd|fc|cf)\s+','',normalized)
    normalized=re.sub(r'\s+(?:fc|cf)$','',normalized)
    return FAVORITE_ALIASES.get(normalized,TEAM_ALIASES.get(normalized,normalized))

team_key=_team_name

def participant_sides(event):
    """The two canonical team keys in the order the listing names them, or None."""
    if not event.get('is_game'):
        return None
    text=event.get('matchup') or event.get('competition') or event.get('title','')
    parts=MATCHUP_WORD.split(text,maxsplit=1)
    if len(parts)!=2:
        return None
    left,right=(_team_name(p) for p in parts)
    if not left or not right or left==right:
        return None
    return (left,right)

def participant_pair(event):
    """Return the two explicitly named sides of a real matchup, independent of home order."""
    sides=participant_sides(event)
    return tuple(sorted(sides)) if sides else None

def _broadcast_kind(title):
    return 'alternate' if ALTERNATE_BROADCAST.search(title or '') else 'main'

def _audio_label(*texts):
    text=' '.join(t or '' for t in texts)
    if re.search(r'\b(?:en espa[nñ]ol|spanish|espa[nñ]ol)\b',text,re.I):
        return {'code':'es','name':'Spanish','evidence':'guide_label'}
    return None

def consolidate_events(programs):
    """Combine guide airings only when both named teams, sport, league and start agree.

    The small start tolerance handles providers rounding the same kickoff differently. A
    second game between the same teams later that day remains a separate card.
    """
    groups=[]
    for event in sorted(programs,key=lambda e:(e['start'],e.get('title',''))):
        pair=participant_pair(event)
        group=next((g for g in groups if pair and g['_pair']==pair and
                    g['sport']==event['sport'] and g['league']==event['league'] and
                    g.get('repeat')==event.get('repeat') and
                    abs(g['_anchor']-event['start'])<=SAME_EVENT_START_TOLERANCE),None)
        if group is None:
            item={**event,'source_programs':[{'id':event['id'],'title':event['title'],
                  'start':event['start'],'end':event['end']}],
                  'matchup_key':'|'.join(pair) if pair else '',
                  'participants':list(pair) if pair else [],
                  'sides':list(participant_sides(event) or ()),
                  '_pair':pair,'_anchor':event['start']}
            for feed in item.get('feeds',[]):
                feed.setdefault('broadcast_kind',_broadcast_kind(event.get('title','')))
                audio=_audio_label(event.get('title',''),feed.get('display_name',''))
                if audio and not feed.get('audio_language'):feed['audio_language']=audio
            groups.append(item)
            continue
        group['start']=min(group['start'],event['start']);group['end']=max(group['end'],event['end'])
        group['live_tag']=bool(group.get('live_tag') or event.get('live_tag'))
        group['source_programs'].append({'id':event['id'],'title':event['title'],'start':event['start'],'end':event['end']})
        existing={(f['provider_id'],f['stream_id']) for f in group.get('feeds',[])}
        for feed in event.get('feeds',[]):
            key=(feed['provider_id'],feed['stream_id'])
            if key in existing:continue
            copy={**feed,'broadcast_kind':_broadcast_kind(event.get('title',''))}
            audio=_audio_label(event.get('title',''),feed.get('display_name',''))
            if audio and not copy.get('audio_language'):copy['audio_language']=audio
            group['feeds'].append(copy);existing.add(key)
    result=[]
    for group in groups:
        pair=group.pop('_pair');anchor=group.pop('_anchor')
        if pair and len(group['source_programs'])>1:
            identity='|'.join((group['sport'],group['league'],*pair,str(anchor//SAME_EVENT_START_TOLERANCE),str(bool(group.get('repeat')))))
            group['id']='event-'+hashlib.sha256(identity.encode()).hexdigest()[:24]
        result.append(group)
    return result

def refresh(api):
    # Resolve the release once so an atomic publisher switch cannot mix two releases.
    release=(ROOT/'current').resolve(strict=True)
    if ROOT.resolve() not in release.parents:raise ValueError('Invalid guide release')
    checked=int(time.time());stamp=int((release/'TV.xml').stat().st_mtime)
    if checked-stamp>86400:raise ValueError('Guide publication is stale')
    adapters={pid:api.Xtream(c) for pid,c in api.PROVIDERS.items()}
    catalogs={pid:{c['stream_id']:c for c in (api.read_cache(pid) or {}).get('channels',[])} for pid in adapters}
    mapping={};meta=''
    for line in (release/'TV.m3u').read_text().splitlines():
        if line.startswith('#EXTINF:'):meta=line
        elif line.startswith(('http://','https://')):
            u=urllib.parse.urlsplit(line);parts=[urllib.parse.unquote(x) for x in u.path.split('/') if x]
            match=re.search(r'tvg-id="([^"]+)"',meta)
            if not match or len(parts)<3:continue
            for pid,adapter in adapters.items():
                credentials=adapter.credentials
                if parts[-3]!=credentials['username'] or parts[-2]!=credentials['password']:continue
                sid=parts[-1].split('.')[0];channel=catalogs[pid].get(sid)
                if channel:
                    name=re.search(r'tvg-name="([^"]+)"',meta)
                    mapping.setdefault(match[1],[]).append({'provider_id':pid,'stream_id':sid,'display_name':name[1] if name else channel['name']})
    events={};programs=0;root=None
    for action,node in ET.iterparse(release/'TV.xml',events=['start','end']):
        if root is None:root=node
        if action!='end':continue
        if node.tag=='programme':
            feeds=mapping.get(node.get('channel'),[]);start=timestamp(node.get('start'));end=timestamp(node.get('stop'))
            competition,matchup=split_title(node.findtext('title') or '',node.findtext('sub-title') or '');title=competition+(' · '+matchup if matchup else '');category=[c.text or '' for c in node.findall('category')];kind=classify(title,category)
            # checked<end+GUIDE_GRACE_SECONDS (not just checked<end): a program whose guide slot
            # recently ended must still be stored so current()'s own grace-window check and
            # tv.attach_live_events' live-status check get a chance to keep showing it -- otherwise
            # this refresh (which rebuilds the whole cache from scratch every cycle) would already
            # have thrown the event away before either of those ever runs.
            if feeds and start and end and start<end and checked<end+GUIDE_GRACE_SECONDS and start<checked+86400 and kind:
                key=hashlib.sha256((title.casefold()+'|'+str(start)+'|'+str(end)).encode()).hexdigest()[:24]
                # 'title' stays the full provider string (competition + matchup) for existing
                # matching/search code (classify, conference, ESPN live-score token matching);
                # 'competition'/'matchup' let the client show the matchup prominently and the
                # competition name small, instead of one long run-on string.
                # 'matchup' is display text only — it's whatever a provider's own <sub-title>
                # or the title's own layout happened to hand back (a venue, an episode name, a
                # real "Team A vs Team B"), which is exactly why it's fine for the big card
                # title but not reliable enough on its own for "is this actually a game".
                # 'is_game' is the separate, stricter answer to that question specifically.
                # When there's no real subtitle or embedded newline at all, split_title()
                # leaves matchup empty and the raw title lands whole in competition — including
                # the common case of a title that IS nothing but "Team A @ Team B" already (an
                # MLB team channel's own listing, say). Falling back to competition there is
                # what catches those; is_game_matchup's SHOW_WORD check is what keeps a
                # wagering/companion show titled the same way from passing too.
                is_game=is_game_matchup(matchup or competition)
                is_replay=node.find('previously-shown') is not None or bool(CLASSIC_REBROADCAST.match(matchup or competition))
                item=events.setdefault(key,{'id':key,'title':title,'competition':competition,'matchup':matchup,'is_game':is_game,'sport':kind[0],'league':kind[1],'conferences':conferences(title,kind[1]),'start':start,'end':end,'feeds':[],'live_tag':node.find('live') is not None,'repeat':is_replay})
                existing={(f['provider_id'],f['stream_id']) for f in item['feeds']}
                for feed in feeds:
                    identity=(feed['provider_id'],feed['stream_id'])
                    if identity not in existing:
                        item['feeds'].append(feed);existing.add(identity)
                programs+=1
            node.clear();root.clear()
    programs=consolidate_events(list(events.values()))
    result={'updated_at':checked,'source_updated_at':stamp,'source':'Published IPTV guide','mapped_channels':len(mapping),'programs':programs}
    api.save_cache('sports-guide',result)
    return {'events':len(programs),'mapped_channels':len(mapping),'on_now':sum(e['start']<=checked<e['end'] for e in programs)}

# A sports network commonly rebroadcasts a classic/replay game under a title literally prefixed
# with the season it originally aired ("2025: Kansas City Chiefs at Denver Broncos") -- a real,
# unambiguous in-guide signal that this is a replay. This is the only signal available for an
# old rebroadcast: the provider's own <previously-shown> tag is frequently absent, and ESPN's
# live-score matching only looks back 10 days (see livescores._date_range), far short of a
# full-season-old game, so for_event() correctly finds nothing to compare against either.
CLASSIC_REBROADCAST=re.compile(r'^\s*(?:19|20)\d{2}:')

def is_placeholder(title):
    # 'Next Game/Match/Fixture · Team A @ Team B on <date> at <time>' is a schedule-announcement
    # slot some channels air as filler — the EPG block itself is genuinely on now, but the game
    # it names is hours (or a full day) away and there is no broadcast to watch yet. The exact
    # wording varies by provider ("Next Game", "Next Match", ...), so match the one part that
    # doesn't: the embedded "on YYYY-MM-DD at H:MMAM/PM" itself, rather than chasing every prefix.
    # Caught here rather than in split_title/classify since it's a real program otherwise (has a
    # real matchup, classifies as a real league) — it just isn't actually airing.
    return bool(re.search(r'\bupcoming\b|no (?:live )?event|off[- ]?air|offline|not (?:yet )?started|event (?:starts|will begin)|coming soon|\bon \d{4}-\d{2}-\d{2} at \d{1,2}:\d{2}\s*[ap]m\b',title,re.I))

def cache_fresh(cache,at):
    return at-cache.get('updated_at',0)<1800 and at-cache.get('source_updated_at',0)<86400

def current(api,at=None):
    at=int(time.time()) if at is None else at
    cache=api.read_cache('sports-guide') or {}
    fresh=at-cache.get('updated_at',0)<1800 and at-cache.get('source_updated_at',0)<86400
    events=[{**e,'guide_slot_ended':at>=e['end']} for e in cache.get('programs',[])
            if e['start']<=at<e['end']+GUIDE_GRACE_SECONDS and not is_placeholder(e.get('title','')) and e.get('league') not in HIDDEN_LEAGUES] if fresh else []
    events.sort(key=lambda e:('SEC' not in e.get('conferences',()),e['start'],e.get('title','')))
    return {'events':events,'updated_at':cache.get('updated_at'),'fresh':fresh,'source':cache.get('source','Published IPTV guide'),'mapped_channels':cache.get('mapped_channels',0),'sports':LEAGUES}

def find(api,event_id,at=None,pregame_seconds=PREGAME_TUNE_SECONDS):
    """Resolve a card during its playable window, including the pregame tune-in period."""
    at=int(time.time()) if at is None else at
    cache=api.read_cache('sports-guide') or {}
    fresh=at-cache.get('updated_at',0)<1800 and at-cache.get('source_updated_at',0)<86400
    if not fresh:return None
    for event in cache.get('programs',[]):
        aliases={event.get('id'),*(p.get('id') for p in event.get('source_programs',[]))}
        if event_id in aliases and event['start']-pregame_seconds<=at<event['end']+GUIDE_GRACE_SECONDS and event.get('league') not in SCHEDULE_BACKED_LEAGUES and not is_placeholder(event.get('title','')):
            return {**event,'pregame':at<event['start'],'guide_slot_ended':at>=event['end']}
    return None

def for_fixture(api,fixture,at=None):
    """Find a published-guide event for a scheduled game without opening any stream."""
    at=int(time.time()) if at is None else at
    cache=api.read_cache('sports-guide') or {}
    if at-cache.get('updated_at',0)>=1800 or at-cache.get('source_updated_at',0)>=86400:return None
    wanted=tuple(sorted((_team_name(fixture.get('team_name','')),_team_name(fixture.get('opponent','')))))
    if not all(wanted):return None
    candidates=[]
    for event in cache.get('programs',[]):
        if event.get('sport')!=fixture.get('sport') or event.get('league')!=fixture.get('league'):continue
        if event.get('repeat') or participant_pair(event)!=wanted:continue
        delta=abs(event['start']-fixture.get('kickoff',0))
        if delta<=SAME_EVENT_START_TOLERANCE:candidates.append((delta,event))
    if len(candidates)!=1:return None
    event=candidates[0][1]
    return {**event,'pregame':at<event['start'],'guide_slot_ended':at>=event['end']}

# The household's own timezone — EPG start/end times are plain epoch seconds with no timezone
# of their own, so "later today" has to be judged against somebody's local midnight, and this
# is a single-home app rather than a multi-timezone product.
HOUSEHOLD_TZ=ZoneInfo('America/New_York')

def later_today(api,at=None):
    """Games with their own real broadcast slot scheduled to start later today (household-local
    calendar day) but not on now yet — 'Coming Next'. Studio shows are left in (same shape as
    current()'s events); the client already knows how to tell a game from a show via 'matchup'
    and filters this row down to games itself, so that one rule lives in exactly one place."""
    at=int(time.time()) if at is None else at
    cache=api.read_cache('sports-guide') or {}
    fresh=at-cache.get('updated_at',0)<1800 and at-cache.get('source_updated_at',0)<86400
    if not fresh:
        return []
    end_of_day=dt.datetime.fromtimestamp(at,HOUSEHOLD_TZ).replace(hour=23,minute=59,second=59,microsecond=0)
    end_ts=int(end_of_day.timestamp())
    events=[e for e in cache.get('programs',[]) if at<e['start']<=end_ts and not is_placeholder(e.get('title','')) and e.get('league') not in HIDDEN_LEAGUES]
    events.sort(key=lambda e:('SEC' not in e.get('conferences',()),e['start'],e.get('title','')))
    return events
