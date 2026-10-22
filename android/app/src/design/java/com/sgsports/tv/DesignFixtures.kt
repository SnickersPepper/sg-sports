package com.sgsports.tv

import android.content.Context
import org.json.JSONObject

/** Design-flavor-only sample data. Studio and Living Room builds compile a no-op DesignFixtures
 * instead (src/studio, src/livingroom), so none of these fixtures ship in those artifacts. */
// Sample live-score states, one per case the real API can return, so design mode
// exercises the same UI branches as production without needing network access.
private fun sampleCheckedAt()=System.currentTimeMillis()/1000-24
val sampleLiveGame=LiveScore("Tennessee","Oklahoma","17","14",8,null,"in","3rd Quarter",3,"8:42",verifiedLive=true,likelyReplay=false,notable=false,checkedAt=sampleCheckedAt())
val sampleLiveReplay=LiveScore("Houston Astros","Texas Rangers","4","2",null,null,"post","Final",9,"Final",verifiedLive=false,likelyReplay=true,notable=false,checkedAt=sampleCheckedAt())
val sampleLiveFinal=LiveScore("Kansas City Chiefs","Cincinnati Bengals","27","20",null,null,"post","Final",4,"Final",verifiedLive=false,likelyReplay=false,notable=false,checkedAt=sampleCheckedAt())
val sampleLiveNotable=LiveScore("St. Louis Cardinals","Chicago Cubs","2","1",null,null,"in","Bottom 7th",7,"Bot 7th",verifiedLive=true,likelyReplay=false,notable=true,checkedAt=sampleCheckedAt())
private fun heroLive(home:String,away:String,homeScore:String,awayScore:String,homeRank:Int?,awayRank:Int?,detail:String,period:Int,clock:String,state:String="in")=
 LiveScore(home,away,homeScore,awayScore,homeRank,awayRank,state,detail,period,clock,verifiedLive=state=="in",likelyReplay=false,notable=false,checkedAt=System.currentTimeMillis()/1000-12)
private fun heroSample(id:String,title:String,sides:List<String>,teamId:String,phase:String,sport:String="Football",league:String="NCAA",live:LiveScore?=null)=
 Game(id,title,league,sport,"Sample · Sat, 7:00 PM",raw=JSONObject().put("sides",org.json.JSONArray(sides)).put("start",System.currentTimeMillis()/1000+3*86400),fixture=live==null,teamId=teamId,live=live,eventPhase=phase)
/** Branding-only roster entries so the hero can show contrasting, similar and artwork cases; never favorites. */
private fun heroBrandTeams()=listOf(
 Team("design.sample.rockets","Sample Rockets","Football","NCAA","",0xFF1E5AA8L,0xFF0B2A57L),
 Team("design.sample.comets","Sample Comets","Football","NCAA","",0xFFFF8A00L,0xFF7A3E00L),
 Team("design.sample.art","Sample Rockets","Football","NCAA","",0xFF1E5AA8L,0xFF0B2A57L),
 Team("design.sample.twins-a","Sample Twins A","Soccer","Sample League","",0xFFC41E3AL,0xFF571326L),
 Team("design.sample.twins-b","Sample Twins B","Soccer","Sample League","",0xFFD0213DL,0xFF571326L))
fun sampleState(context:Context):SgState {
 val teams=loadTeams(context)
 val fixture=Game("design-barca","Barcelona vs Feyenoord","UEFA Champions League","Soccer","Sample · Today, 12:45 PM",listOf(teams[0].logo,assetJson(context,"teams.json").optString("Feyenoord")),channels=4,fixture=true,teamId=teams[0].id)
 val games=listOf(fixture,Game("design-vols","Tennessee vs Oklahoma","NCAA","Football","Sample · 7:00 PM",listOf("logos/espn_football_college-football_201.png",teams[1].logo),conferences=listOf("SEC"),channels=2,teamId=teams[1].id,live=sampleLiveGame,ranks=listOf(null,8),raw=JSONObject().put("live",JSONObject().put("espn_id","fx-f3"))),Game("design-cross-conf","Texas A&M at Arizona State","NCAA","Football","Sample · Sat, 8:00 PM",listOf(teams[1].logo,"logos/espn_football_college-football_201.png"),conferences=listOf("SEC","Big 12"),channels=2),Game("design-softball","Lady Vols vs Georgia","NCAA","Softball","Sample · 6:00 PM",listOf(teams[2].logo,"logos/espn_football_college-football_61.png"),conferences=listOf("SEC"),channels=2,teamId=teams[2].id),Game("design-chiefs","Chiefs vs Bengals","NFL","Football","Sample · 3:25 PM",listOf(teams[4].logo,"logos/espn_football_nfl_4.png"),channels=3,teamId=teams[4].id,live=sampleLiveFinal),Game("design-cards","Cardinals vs Cubs","MLB","Baseball","Sample · 6:45 PM",listOf(teams[3].logo,"logos/espn_baseball_mlb_16.png"),channels=2,teamId=teams[3].id,live=sampleLiveNotable),Game("design-astros","Astros vs Rangers","MLB","Baseball","Sample · 7:10 PM",listOf(teams[5].logo,"logos/espn_baseball_mlb_13.png"),channels=2,teamId=teams[5].id,live=sampleLiveReplay))
 // One "next game" sample per favorite, matching what the real next_games API field supplies.
 val upcoming=listOf(fixture,
  Game("next-vols","Tennessee vs Florida","NCAA","Football","Sample · Sat, 3:30 PM",listOf(teams[1].logo,"logos/espn_football_college-football_57.png"),conferences=listOf("SEC"),fixture=true,teamId=teams[1].id),
  Game("next-softball","Lady Vols vs Alabama","NCAA","Softball","Sample · Fri, 6:00 PM",listOf(teams[2].logo,"logos/espn_football_college-football_333.png"),conferences=listOf("SEC"),fixture=true,teamId=teams[2].id),
  Game("next-cards","Cardinals vs Brewers","MLB","Baseball","Sample · Tomorrow, 7:15 PM",listOf(teams[3].logo,"logos/espn_baseball_mlb_8.png"),fixture=true,teamId=teams[3].id),
  Game("next-chiefs","Chiefs vs Broncos","NFL","Football","Sample · Sun, 4:25 PM",listOf(teams[4].logo,"logos/espn_football_nfl_7.png"),fixture=true,teamId=teams[4].id),
  Game("next-astros","Astros vs Mariners","MLB","Baseball","Sample · Tomorrow, 8:10 PM",listOf(teams[5].logo,"logos/espn_baseball_mlb_12.png"),fixture=true,teamId=teams[5].id),
  // Hero review fixtures (synthetic, Design only): every combination goes through the same shared hero.
  heroSample("design-hero-two","Tennessee vs Missouri",listOf(teamKey(teams[1].name),teamKey(teams[6].name)),teams[1].id,phase="upcoming"),
  heroSample("design-hero-one","Tennessee vs Sample Owls",listOf(teamKey(teams[1].name),"sample owls"),teams[1].id,phase="upcoming"),
  heroSample("design-hero-none","Sample Wolves vs Sample Hawks",listOf("sample wolves","sample hawks"),"",phase="upcoming",sport="Basketball",league="NBA"),
  heroSample("design-hero-art-live","Sample Rockets vs Sample Comets",listOf("sample rockets","sample comets"),"design.sample.art",phase="live",live=heroLive("Sample Comets","Sample Rockets","24","17",11,null,"3rd Quarter",3,"5:05")),
  heroSample("design-hero-contrast","Sample Rockets vs Sample Comets",listOf("sample rockets","sample comets"),"",phase="upcoming"),
  heroSample("design-hero-similar-final","Sample Twins A vs Sample Twins B",listOf("sample twins a","sample twins b"),"",phase="final",sport="Soccer",league="Sample League",live=heroLive("Sample Twins B","Sample Twins A","1","2",null,null,"Full time",2,"90:00",state="post")),
  heroSample("design-hero-baseball-live","Cardinals at Cubs",listOf(teamKey(teams[3].name),"chicago cubs"),teams[3].id,phase="live",sport="Baseball",league="MLB",live=heroLive("Chicago Cubs","St. Louis Cardinals","2","1",null,null,"Bottom 7th",7,"Bot 7th")),
  // Canonical-branding review fixtures: no game-supplied crests, so crest and color come only from team identity.
  heroSample("design-brand-byu-nd","BYU vs Notre Dame",listOf(teamKey("BYU"),teamKey("Notre Dame")),"",phase="upcoming"),
  heroSample("design-brand-nfl","Green Bay Packers vs Chicago Bears",listOf(teamKey("Green Bay Packers"),teamKey("Chicago Bears")),"",phase="upcoming",league="NFL"),
  heroSample("design-brand-mlb","New York Yankees vs Boston Red Sox",listOf(teamKey("New York Yankees"),teamKey("Boston Red Sox")),"",phase="live",sport="Baseball",league="MLB",live=heroLive("Boston Red Sox","New York Yankees","3","4",null,null,"Top 6th",6,"Top 6th")),
  heroSample("design-brand-epl","Liverpool vs Real Madrid",listOf(teamKey("Liverpool"),teamKey("Real Madrid")),"",phase="upcoming",sport="Soccer",league="UEFA Champions League"),
  heroSample("design-brand-big10","Michigan vs Ohio State",listOf(teamKey("Michigan Wolverines"),teamKey("Ohio State Buckeyes")),"",phase="final",live=heroLive("Ohio State Buckeyes","Michigan Wolverines","24","27",3,2,"Final",4,"Final",state="post")),
  heroSample("design-hero-postponed","Sample Rockets vs Sample Comets",listOf("sample rockets","sample comets"),"",phase="postponed"),
  Game("next-mizzou","Missouri vs Auburn","NCAA","Football","Sample · Sat, 12:00 PM",listOf(teams[6].logo,"logos/espn_football_college-football_2.png"),conferences=listOf("SEC"),fixture=true,teamId=teams[6].id))
 // Schedule-first LVBP samples, one per evidence level plus a postponed game, shaped exactly like the
 // backend's "lvbp" list. Design mode only: Studio and Living Room build get real data when paired.
 val mapper=GameMapper(context)
 val checked=System.currentTimeMillis()/1000-1800
 val day=System.currentTimeMillis()/1000+86400
 fun lvbpSample(id:String,matchup:String,start:Long,phase:String,coverage:String,detail:String,names:List<String>):Game {
  val raw=JSONObject().put("id",id).put("title","Venezuela LVBP · $matchup").put("matchup",matchup).put("league",LVBP).put("sport","Baseball")
   .put("is_game",true).put("start",start).put("event_phase",phase).put("sides",org.json.JSONArray()).put("feeds",org.json.JSONArray())
   .put("lvbp",JSONObject().put("coverage",coverage).put("detail",detail).put("broadcasters",org.json.JSONArray(names)).put("venue","Estadio Sample")
    .put("schedule",JSONObject().put("state","fresh").put("checked_at",checked)))
  return mapper.program(raw,teams,scheduled=true)
 }
 val lvbp=listOf(
  lvbpSample("sample-lvbp-confirmed","Leones del Caracas @ Tiburones de La Guaira",day,"upcoming","confirmed","The league lists Televen for this game and its channel was checked recently.",listOf("Televen")),
  lvbpSample("sample-lvbp-possible","Navegantes del Magallanes @ Tigres de Aragua",day+3600,"upcoming","possible","A channel guide suggests Meridiano TV. The league has not confirmed this.",listOf("Meridiano TV")),
  lvbpSample("sample-lvbp-none","Bravos de Margarita @ Águilas del Zulia",day+7200,"upcoming","none","The league lists ByM Sport for this game. SG Sports has no channel for it.",listOf("ByM Sport")),
  lvbpSample("sample-lvbp-postponed","Caribes de Anzoátegui @ Cardenales de Lara",day+10800,"postponed","none","The league lists no channel for this game.",emptyList()))
 return SgState(teams=teams,brandTeams=heroBrandTeams(),upcoming=upcoming,programs=games,later=lvbp,lvbpSchedule=LvbpSchedule("fresh",checked),
  coverage=teams.associate {it.id to ScheduleCoverage("available","ESPN schedule")},paired=false,demo=true)
}

/** One sample per recording state the server can report, newest first inside each team. */
fun sampleRecordings():RecordingsSnapshot {
 val now=System.currentTimeMillis()/1000
 fun rec(id:String,bucket:String,team:String,title:String,state:String,reason:String?,start:Boolean,end:Boolean,playable:Boolean,age:Long,provider:String="dream")=
  Recording(id,bucket,team,title,state,reason,start,end,playable,now-age,now-age+(if(start)-120 else 1500),provider)
 return RecordingsSnapshot(true,"ok",listOf(
  RecordingBucket("tennessee-football","Tennessee football",listOf(
   rec("vf1","tennessee-football","Tennessee football","Tennessee vs Oklahoma","recording",null,true,false,true,1800),
   rec("vf2","tennessee-football","Tennessee football","Tennessee vs Georgia","ready",null,true,true,true,8*86400),
   rec("vf3","tennessee-football","Tennessee football","Tennessee vs Florida","ready","storage_pressure",true,false,true,15*86400))),
  RecordingBucket("tennessee-softball","Tennessee softball",listOf(
   rec("vs1","tennessee-softball","Tennessee softball","Tennessee vs LSU","ready","interrupted",false,false,true,2*86400),
   rec("vs2","tennessee-softball","Tennessee softball","Tennessee vs Auburn","failed","provider_unavailable",true,false,false,4*86400),
   rec("vs3","tennessee-softball","Tennessee softball","Tennessee vs Texas","expired","evicted",true,true,false,9*86400))),
  RecordingBucket("fc-barcelona","FC Barcelona",listOf())))
}

object DesignFixtures {
 fun state(context:Context):SgState? = sampleState(context)
 fun teamHub(teamId:String):HubFixture? = hubFixture(teamId)
 fun recordings():RecordingsSnapshot? = sampleRecordings()
 fun feeds(mapper:GameMapper):List<Feed>? {
  val confirmed=JSONObject().put("content_verified",true).put("availability","content_verified").put("verification_label","Game confirmed just now")
  val checked=JSONObject().put("transport_verified",true).put("availability","stream_healthy").put("verification_label","Stream healthy just now · game not confirmed")
  val scheduled=JSONObject().put("availability","scheduled_not_probed").put("broadcast_kind","alternate")
  val failed=JSONObject().put("availability","probe_failed").put("verification_label","Could not open just now")
  return listOf(
   mapper.feed(confirmed.put("display_name","Main broadcast").put("provider_id","ProviderA").put("stream_id","sample-1")),
   mapper.feed(checked.put("display_name","Backup HD").put("provider_id","ProviderB").put("stream_id","sample-2")),
   Feed("Peyton & Eli","ProviderB","English · listed","logos/espn.png",scheduled,"Alternate commentary · Playback not verified"),
   mapper.feed(failed.put("display_name","Unavailable candidate").put("provider_id","ProviderA").put("stream_id","sample-3")))
 }
}
