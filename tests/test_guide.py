import datetime as dt
import unittest
from unittest.mock import MagicMock,patch
from zoneinfo import ZoneInfo
import guide
import tv

class GuideTests(unittest.TestCase):
 def test_selected_taxonomy_and_ambiguous_league(self):
  for title,expected in [('Venezuelan Professional Baseball',('Baseball','Venezuela LVBP')),('Liga FUTVE',None),('NCAA Softball',('Softball','NCAA')),('NFL Live',('Football','NFL')),('NBA Today',None),('WNBA Basketball',None),('College Basketball',None),('Soccer',None),('Liga Profesional de Fútbol Argentina',('Soccer','Argentina Primera División'))]:
   self.assertEqual(guide.classify(title,[]),expected)
 def test_soccer_without_an_explicit_league_name_is_identified_by_team_roster(self):
  # Most guide entries are just "Soccer · Team A vs Team B" with no league spelled out at all.
  self.assertEqual(guide.classify('Soccer · Sevilla vs Valencia',[]),('Soccer','La Liga'))
  self.assertEqual(guide.classify('Soccer · Manchester City vs Chelsea',[]),('Soccer','Premier League'))
 def test_mlb_team_channels_are_identified_by_roster_even_with_no_mlb_text(self):
  # An MLB team-branded channel's own listing is often nothing but "Team A @ Team B" — no
  # "MLB" or "Baseball" text at all in the title itself, only in the XMLTV category.
  self.assertEqual(guide.classify('Chicago White Sox @ St. Louis Cardinals',['Baseball']),('Baseball','MLB'))
  self.assertEqual(guide.classify('New York Mets @ New York Yankees',['Baseball']),('Baseball','MLB'))
 def test_baseball_with_no_recognized_team_still_falls_back_to_unspecified(self):
  # Unlike soccer's strict whitelist, unidentified baseball content (minor league,
  # international, ...) still belongs in the app, just without a specific league name.
  # (Venezuelan baseball specifically is caught earlier by its own RULES entry and, since
  # Phase 5A, routed through the schedule-backed LVBP join — see tests/test_lvbp.py.)
  self.assertEqual(guide.classify('Japanese Professional Baseball',['Baseball']),('Baseball','Unspecified competition'))
 def test_soccer_from_an_unwanted_league_or_unknown_team_is_dropped_not_unspecified(self):
  # A team-name collision with any whitelisted-league roster is what makes a match here — an
  # Eredivisie, Saudi Pro League, German 3. Liga, or US college soccer game has neither an
  # explicit league name nor a recognized team, so it must not fall back to "Unspecified".
  for title in ['Soccer · AZ vs Willem II','Soccer · UCF vs VCU','Soccer · Bradley vs E Illinois','Fútbol Mexicano Primera División · Necaxa vs. Puebla']:
   self.assertIsNone(guide.classify(title,[]))
 def test_a_single_bare_city_alias_is_not_enough_to_claim_a_league(self):
  # "Orlando" and "Chicago" are also NWSL's Orlando Pride and Chicago Stars, not just MLS's
  # Orlando City and Chicago Fire — one lone match must not be trusted; it takes both sides of
  # the matchup resolving to the same roster to call it.
  self.assertIsNone(guide.classify('Upcoming · Soccer: Bay FC vs. Orlando — Sep 27 05:00 PM EDT',[]))
  self.assertIsNone(guide.classify('Upcoming · Soccer: Chicago Stars vs. Washington — Sep 20 05:00 PM EDT',[]))
 def test_split_title_prefers_real_subtitle_over_embedded_newline(self):
  self.assertEqual(guide.split_title('NBA Shot Clock',"'90s Champions Collection"),('NBA Shot Clock',"'90s Champions Collection"))
 def test_is_game_matchup_is_stricter_than_split_title_since_display_text_is_not_always_a_game(self):
  # A real provider <sub-title> or a newline-split second line is good enough for display (the
  # NBA Shot Clock case above, or "Golf at Altitude" / "Plum Creek Golf Course") but is not on
  # its own proof of an actual "Team A vs Team B" game — that's judged separately here.
  self.assertTrue(guide.is_game_matchup('SMU at Florida State'))
  self.assertTrue(guide.is_game_matchup('Al Qadsiah vs. Al Ettifaq'))
  self.assertFalse(guide.is_game_matchup("'90s Champions Collection"))
  self.assertFalse(guide.is_game_matchup('Plum Creek Golf Course'))
  self.assertFalse(guide.is_game_matchup(''))
 def test_a_wagering_companion_show_titled_like_a_matchup_is_not_a_game(self):
  # SportsGrid's whole live schedule is these: same "Team vs Team" shape as a real listing,
  # but it's commentary on the game, not the broadcast — the trailing show-format words are
  # the only thing that tells the two apart.
  self.assertFalse(guide.is_game_matchup('Missouri vs. Kansas College Football In-Game Live Gameday'))
  self.assertFalse(guide.is_game_matchup('Philadelphia Eagles vs. Washington Commanders NFL Football In-Game Live Gameday'))
 def test_a_team_channels_title_with_no_separator_still_counts_as_a_real_matchup(self):
  # An MLB team channel's own listing is often nothing but "Team A @ Team B" — split_title()
  # has no newline/colon to split on, so the whole string lands in competition and matchup
  # stays empty; refresh() falls back to competition for exactly this shape of title.
  competition,matchup=guide.split_title('Chicago White Sox @ St. Louis Cardinals','')
  self.assertEqual(matchup,'')
  self.assertTrue(guide.is_game_matchup(matchup or competition))
 def test_split_title_embedded_newline_is_the_common_real_pattern(self):
  self.assertEqual(guide.split_title('College Football\nSMU at Florida State',''),('College Football','SMU at Florida State'))
  self.assertEqual(guide.split_title('Fútbol Saudi Pro League ᴺᵉʷ\nAl Qadsiah vs. Al Ettifaq',''),('Fútbol Saudi Pro League','Al Qadsiah vs. Al Ettifaq'))
 def test_split_title_colon_prefix_only_when_remainder_looks_like_a_matchup(self):
  self.assertEqual(guide.split_title('Soccer: FIU vs Stetson',''),('Soccer','FIU vs Stetson'))
  self.assertEqual(guide.split_title('Next Game: Baltimore Orioles @ Toronto Blue Jays on 2026-09-11',''),('Next Game','Baltimore Orioles @ Toronto Blue Jays on 2026-09-11'))
  self.assertEqual(guide.split_title('SportsCenter: Top 10',''),('SportsCenter: Top 10',''))
  self.assertEqual(guide.split_title('LALIGA EA SPORTS (T26/27): Deportivo - Valencia',''),('LALIGA EA SPORTS (T26/27)','Deportivo - Valencia'))
 def test_split_title_no_pattern_falls_back_to_whole_title(self):
  self.assertEqual(guide.split_title('Champions League 26/27',''),('Champions League 26/27',''))
 def test_later_today_includes_only_same_calendar_day_and_not_yet_started(self):
  noon=dt.datetime(2026,1,15,12,0,0,tzinfo=ZoneInfo('America/New_York'))
  at=int(noon.timestamp())
  tonight=int((noon+dt.timedelta(hours=6)).timestamp())
  near_midnight=int(noon.replace(hour=23,minute=58).timestamp())
  tomorrow=int((noon+dt.timedelta(hours=13)).timestamp())
  already_started=at-100
  cache={'updated_at':at,'source_updated_at':at,'programs':[
   {'id':'tonight','start':tonight,'end':tonight+3600,'title':'A vs B'},
   {'id':'near-midnight','start':near_midnight,'end':near_midnight+3600,'title':'A vs B'},
   {'id':'tomorrow','start':tomorrow,'end':tomorrow+3600,'title':'A vs B'},
   {'id':'already-started','start':already_started,'end':already_started+3600,'title':'A vs B'},
  ]}
  api=MagicMock();api.read_cache.return_value=cache
  self.assertEqual([e['id'] for e in guide.later_today(api,at)],['tonight','near-midnight'])
 def test_later_today_fails_closed_on_stale_cache(self):
  api=MagicMock();api.read_cache.return_value={'updated_at':0,'source_updated_at':0,'programs':[{'id':'x','start':999999999,'end':999999999+3600,'title':'A vs B'}]}
  self.assertEqual(guide.later_today(api,100000),[])
 def test_current_window_and_stale_data_fail_closed(self):
  api=MagicMock();cache={'updated_at':10000,'source_updated_at':10000,'programs':[{'id':'now','start':9999,'end':10001},{'id':'ended','start':9990,'end':10000},{'id':'future','start':10001,'end':10005}]};api.read_cache.return_value=cache
  # 'ended' now surfaces too (within the grace window covered by the next test below) --
  # this assertion is about 'future' staying excluded and the stale-cache case, not that.
  self.assertEqual({e['id'] for e in guide.current(api,10000)['events']},{'now','ended'})
  self.assertEqual(guide.current(api,12000)['events'],[])
 def test_a_recently_ended_guide_slot_stays_a_candidate_within_the_grace_window(self):
  # The provider's guide slot ending is not the same as the real game ending (see
  # tv.attach_live_events, which is what actually decides whether to drop it, using ESPN's own
  # live-score status) -- current() itself just needs to widen its candidate window and flag
  # which ones are past their nominal end, rather than excluding them outright.
  api=MagicMock();cache={'updated_at':10000,'source_updated_at':10000,'programs':[
   {'id':'in-progress','start':9999,'end':10001},
   {'id':'slot-just-ended','start':9990,'end':10000},
   {'id':'way-past-grace','start':0,'end':10000-guide.GUIDE_GRACE_SECONDS-1},
   {'id':'not-started-yet','start':10001,'end':10005}]}
  api.read_cache.return_value=cache
  events={e['id']:e for e in guide.current(api,10000)['events']}
  self.assertEqual(set(events),{'in-progress','slot-just-ended'})
  self.assertFalse(events['in-progress']['guide_slot_ended'])
  self.assertTrue(events['slot-just-ended']['guide_slot_ended'])
 def test_timezone_required(self):
  self.assertIsNone(guide.timestamp('20260908120000'))
  self.assertEqual(guide.timestamp('20260908120000 +0000'),guide.timestamp('20260908080000 -0400'))
 def test_expired_guide_event_cannot_start_stream(self):
  with patch.object(guide,'find',return_value=None),patch.object(tv.A,'Xtream') as adapter:
   with self.assertRaises(ValueError):tv.begin('owner',{'event_id':'expired','provider_id':'providerb','stream_id':'1'})
   adapter.assert_not_called()

 def test_conference_identification_covers_all_eight_and_avoids_similarly_named_schools(self):
  for title,expected in [('College Football · Auburn at Vanderbilt',['SEC']),('College Football · LSU at Clemson',['SEC','ACC']),
   ('SEC Football Championship',['SEC']),('Ohio State vs Michigan',['Big Ten']),('Big Ten Championship',['Big Ten']),
   ('BYU at Arizona State',['Big 12']),('SMU vs Miami',['ACC']),('Boise State at Fresno State',['Pac-12']),
   ('Memphis vs Navy',['American']),('Air Force at Nevada',['Mountain West'])]:
   self.assertEqual(guide.conferences(title,'NCAA'),expected)
  # A bare short name that collides with a DIFFERENT, genuinely irreconcilable school (Miami
  # Hurricanes/ACC vs Miami (OH)/untracked -- "(OH)" isn't a safe suffix, it's a different school
  # wearing the same name) is dropped at roster-build time rather than guessed at -- these fall
  # through to [], not a wrong conference, the safer of the two failure modes with no way to ask.
  # A directional-qualifier collision (Kentucky/SEC vs Western Kentucky, Alabama/SEC vs South
  # Alabama) is safe and excluded at match time instead, so the bare name still resolves when it
  # appears on its own but not when the qualifier is right there in the same title.
  for title in ['NCAA Softball · Western Kentucky at South Alabama','SEC Now','NFL Live','NCAA Football · Florida A&M at Nowhere State','College Football · Georgia Press Conference']:
   self.assertEqual(guide.conferences(title,'NCAA'),[])
 def test_cross_conference_matchup_is_tagged_with_both_conferences(self):
  # A real bug: the old single-value classifier tagged a cross-conference game only under
  # whichever team the title happened to name first, so it never appeared in the second team's
  # conference tab at all. Both sides' conferences must come back, in the order they're named.
  self.assertEqual(guide.conferences('Arizona State vs Texas A&M','NCAA'),['Big 12','SEC'])
  self.assertEqual(guide.conferences('Notre Dame at NC State','NCAA'),['Independent','ACC'])
 def test_bare_school_name_still_matches_its_own_conference_next_to_a_longer_namesake(self):
  # A second real bug: "Iowa" (Big Ten) was reported under both "Iowa State" and "Iowa" for
  # collision safety at roster-build time, so a game naming both schools only ever credited
  # Iowa State's conference -- Iowa's own never appeared. The exclusion suffix lets the bare
  # name match on its own while still refusing to match as a false prefix of the longer name.
  self.assertEqual(guide.conferences('Iowa State at Iowa','NCAA'),['Big 12','Big Ten'])
  # Same fix, same shape of bug: "Oklahoma" (SEC) was reported under "Oklahoma State" (Big 12)
  # for roster-build collision safety, so it never got its own conference tag on the live TV
  # guide even in a game that had nothing to do with Oklahoma State.
  self.assertEqual(guide.conferences('Iowa State at Oklahoma','NCAA'),['Big 12','SEC'])
  self.assertEqual(guide.conferences('NCAA Softball · Oklahoma vs Somewhere','NCAA'),['SEC'])
  self.assertEqual(guide.conferences('UCF at Pittsburgh','NCAA'),['Big 12','ACC'])
 def test_classic_rebroadcast_title_prefix_is_recognized_as_replay(self):
  # A third real bug: a channel rebroadcasting an old game under a "<season>: Team A at Team B"
  # title has no <previously-shown> tag and is far too old for ESPN's 10-day live-score lookback
  # to match at all -- for_event() correctly returns nothing, leaving no other signal that this
  # isn't a genuinely live broadcast. The title's own season prefix is the only signal left.
  self.assertTrue(guide.CLASSIC_REBROADCAST.match('2025: Kansas City Chiefs at Denver Broncos'))
  self.assertTrue(guide.CLASSIC_REBROADCAST.match('1999: Super Bowl XXXIV'))
  self.assertIsNone(guide.CLASSIC_REBROADCAST.match('Kansas City Chiefs at Denver Broncos'))
  self.assertIsNone(guide.CLASSIC_REBROADCAST.match('Week 3: Kansas City Chiefs at Denver Broncos'))
 def test_sec_is_sorted_first(self):
  api=MagicMock();api.read_cache.return_value={'updated_at':10000,'source_updated_at':10000,'programs':[{'id':'other','start':9900,'end':11000},{'id':'sec','start':9990,'end':11000,'conferences':['SEC']}]}
  self.assertEqual([e['id'] for e in guide.current(api,10000)['events']],['sec','other'])

 def test_upcoming_placeholder_is_not_on_now(self):
  api=MagicMock();api.read_cache.return_value={'updated_at':10000,'source_updated_at':10000,'programs':[{'id':'placeholder','start':9999,'end':11000,'title':'Upcoming: NCAA Football: Texas vs LSU — tomorrow'}]}
  self.assertEqual(guide.current(api,10000)['events'],[])

 def test_next_game_announcement_placeholder_is_not_on_now(self):
  api=MagicMock();api.read_cache.return_value={'updated_at':10000,'source_updated_at':10000,'programs':[
   {'id':'nextgame','start':9999,'end':11000,'title':'Next Game · Baltimore Orioles @ Toronto Blue Jays on 2026-09-11 at 07:07PM EDT'}]}
  self.assertEqual(guide.current(api,10000)['events'],[])
 def test_announcement_placeholder_is_caught_regardless_of_provider_wording(self):
  # Different providers phrase the same filler-announcement pattern differently ("Next Game",
  # "Next Match", ...) — is_placeholder matches the embedded date/time itself, not the prefix.
  self.assertTrue(guide.is_placeholder('Next Match · English Premier League on 2026-09-12 at 12:30PM EDT'))
  self.assertFalse(guide.is_placeholder('College Football · Missouri at Kansas'))

 def test_excluded_soccer_leagues_do_not_appear(self):
  for title in ['Primeira Liga','Eredivisie','UEFA Europa League','UEFA Conference League']:
   self.assertIsNone(guide.classify(title,['Soccer']))
   self.assertNotIn(title,guide.LEAGUES['Soccer'])
  self.assertEqual(guide.classify('UEFA Champions League',[]),('Soccer','UEFA Champions League'))
 def test_laliga_hypermotion_is_the_second_division_not_la_liga(self):
  # "LALIGA HYPERMOTION" is Spain's second division, a different competition from the
  # first-division "LaLiga EA Sports" — only the first division was ever asked for.
  for title in ['LALIGA HYPERMOTION · UD Almería - Cádiz CF','Resúmenes LALIGA HyperMotion (T26/27) · Burgos - Ceuta','Previa LaLiga HyperMotion · Córdoba - Almería']:
   self.assertIsNone(guide.classify(title,[]))
  self.assertEqual(guide.classify('LALIGA EA SPORTS (T26/27): Deportivo - Valencia',[]),('Soccer','La Liga'))
 def test_high_school_football_is_excluded(self):
  self.assertIsNone(guide.classify('High School Football · Casteel at Higley',[]))
  self.assertEqual(guide.classify('College Football · Missouri at Kansas',[]),('Football','NCAA'))
 def test_banana_ball_gets_its_own_league_not_unspecified(self):
  self.assertEqual(guide.classify('Banana Ball World Tour · Savannah Bananas vs. Party Animals',[]),('Baseball','Banana Ball'))

 def test_same_game_airings_become_one_card_with_distinct_broadcast_choices(self):
  def event(event_id,title,start,feeds,is_game=True,repeat=False):
   competition,matchup=guide.split_title(title,'')
   return {'id':event_id,'title':title,'competition':competition,'matchup':matchup,
    'is_game':is_game,'sport':'Football','league':'NFL','conferences':[],
    'start':start,'end':start+10800,'feeds':feeds,'live_tag':False,'repeat':repeat}
  programs=[
   event('main','Denver Broncos at Kansas City Chiefs',10000,[{'provider_id':'providera','stream_id':'1','display_name':'ESPN'}]),
   event('spanish','KC Chiefs - Denver Broncos',10000,[{'provider_id':'providera','stream_id':'2','display_name':'DAZN Español'}]),
   event('manning','Monday Night Football with Peyton and Eli: Denver Broncos at Kansas City Chiefs',10000,[{'provider_id':'providerb','stream_id':'3','display_name':'ESPN2'}]),
  ]
  merged=guide.consolidate_events(programs)
  self.assertEqual(len(merged),1)
  self.assertEqual(len(merged[0]['feeds']),3)
  self.assertEqual({f['broadcast_kind'] for f in merged[0]['feeds']},{'main','alternate'})
  spanish=next(f for f in merged[0]['feeds'] if f['stream_id']=='2')
  self.assertEqual(spanish['audio_language']['code'],'es')
  self.assertEqual(len(merged[0]['source_programs']),3)

 def test_provider_at_symbol_and_rounded_mlb_start_still_merge(self):
  def event(event_id,title,competition,matchup,start,feeds):
   return {'id':event_id,'title':title,'competition':competition,'matchup':matchup,
    'is_game':True,'sport':'Baseball','league':'MLB','conferences':[],
    'start':start,'end':start+10800,'feeds':feeds,'live_tag':False,'repeat':False}
  programs=[
   event('network','MLB Baseball · Athletics at Tampa Bay Rays','MLB Baseball','Athletics at Tampa Bay Rays',10000,
         [{'provider_id':'providera','stream_id':'1','display_name':'MLB Network'}]),
   event('team','Athletics @ Tampa Bay Rays','Athletics @ Tampa Bay Rays','',10600,
         [{'provider_id':'providerb','stream_id':'2','display_name':'Rays TV'}]),
  ]
  merged=guide.consolidate_events(programs)
  self.assertEqual(len(merged),1)
  self.assertEqual(len(merged[0]['feeds']),2)
  self.assertEqual(len(merged[0]['source_programs']),2)

 def test_same_soccer_match_from_two_provider_labels_merges(self):
  def event(event_id,title,competition,matchup,feeds):
   return {'id':event_id,'title':title,'competition':competition,'matchup':matchup,
    'is_game':True,'sport':'Soccer','league':'Copa Libertadores','conferences':[],
    'start':10000,'end':17200,'feeds':feeds,'live_tag':False,'repeat':False}
  merged=guide.consolidate_events([
   event('english','Copa Libertadores Soccer · Platense vs. Fluminense','Copa Libertadores Soccer','Platense vs. Fluminense',
         [{'provider_id':'providera','stream_id':'1','display_name':'beIN'}]),
   event('spanish','Fútbol CONMEBOL Copa Libertadores · Platense vs. Fluminense','Fútbol CONMEBOL Copa Libertadores','Platense vs. Fluminense',
         [{'provider_id':'providerb','stream_id':'2','display_name':'beIN Español'}]),
  ])
  self.assertEqual(len(merged),1)
  self.assertEqual(len(merged[0]['feeds']),2)

 def test_doubleheader_replay_and_studio_show_are_not_collapsed(self):
  def event(event_id,title,start,is_game=True,repeat=False):
   competition,matchup=guide.split_title(title,'')
   return {'id':event_id,'title':title,'competition':competition,'matchup':matchup,
    'is_game':is_game,'sport':'Baseball','league':'MLB','conferences':[],
    'start':start,'end':start+7200,'feeds':[{'provider_id':'providera','stream_id':event_id,'display_name':'SPORTS'}],
    'live_tag':False,'repeat':repeat}
  programs=[event('early','San Francisco Giants at St. Louis Cardinals',10000),
            event('rounded','San Francisco Giants @ St. Louis Cardinals',10900),
            event('late','San Francisco Giants at St. Louis Cardinals',20000),
            event('replay','San Francisco Giants at St. Louis Cardinals',10000,repeat=True),
            event('show','Giants vs Cardinals Pregame',10000,is_game=False)]
  merged=guide.consolidate_events(programs)
  self.assertEqual(len(merged),4)
  self.assertEqual(sorted(len(e['source_programs']) for e in merged),[1,1,1,2])

 def test_find_allows_pregame_tune_in_but_fails_closed_outside_window(self):
  event={'id':'game','start':10000,'end':12000,'title':'Chiefs at Broncos','source_programs':[{'id':'old-card'}]}
  api=MagicMock();api.read_cache.return_value={'updated_at':9800,'source_updated_at':9800,'programs':[event]}
  self.assertTrue(guide.find(api,'game',at=9700)['pregame'])
  self.assertEqual(guide.find(api,'old-card',at=9700)['id'],'game')
  self.assertIsNone(guide.find(api,'game',at=8000))

 def test_fixture_match_requires_one_unambiguous_non_replay_event(self):
  base={'id':'game','title':'Kansas City Chiefs at Denver Broncos','competition':'Kansas City Chiefs at Denver Broncos',
   'matchup':'','is_game':True,'sport':'Football','league':'NFL','start':10000,'end':13000,'feeds':[],'repeat':False}
  fixture={'team_name':'Kansas City Chiefs','opponent':'Denver Broncos','sport':'Football','league':'NFL','kickoff':10000}
  api=MagicMock();api.read_cache.return_value={'updated_at':9000,'source_updated_at':9000,'programs':[base]}
  self.assertEqual(guide.for_fixture(api,fixture,at=9000)['id'],'game')
  api.read_cache.return_value={'updated_at':9000,'source_updated_at':9000,'programs':[base,{**base,'id':'ambiguous'}]}
  self.assertIsNone(guide.for_fixture(api,fixture,at=9000))
