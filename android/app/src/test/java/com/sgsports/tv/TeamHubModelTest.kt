package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.Locale
import java.util.TimeZone

/** SGH-301: Team Hub rules. Pure JVM; no network, no Android, no team- or sport-specific expectations. */
class TeamHubModelTest {
 private val now=1_800_000_000L
 private fun ev(id:String,start:Long,status:HubStatus,home:String="Home",away:String="Away",league:String="League",season:String="",hs:String?=null,aws:String?=null,detail:String="")=
  HubEvent(id,"Football",league,HubSide("h-$home",home,"",hs,null),HubSide("a-$away",away,"",aws,null),start,status,detail,"",emptyList(),season)
 private fun arr(vararg o:JSONObject)=JSONArray().also{a->o.forEach{a.put(it)}}
 private fun gameJson(id:String,state:String,name:String="STATUS_SCHEDULED",completed:Boolean=false,start:Long=now+100,hs:Any?="0",aws:Any?="0",home:String="Home Team",away:String="Away Team")=
  JSONObject().put("espn_id",id).put("sport","Football").put("league","NCAA").put("state",state).put("status_name",name).put("completed",completed).put("start",start).put("detail","d")
   .put("home",JSONObject().put("id","1").put("name",home).put("score",hs ?: JSONObject.NULL).put("rank",JSONObject.NULL))
   .put("away",JSONObject().put("id","2").put("name",away).put("score",aws ?: JSONObject.NULL).put("rank",5))

 // ---- parsing: separate resources, contract tolerance, null versus zero ----
 @Test fun everySectionParsesIndependentlyAndANamedFailureOnlyFailsItself() {
  val j=JSONObject().put("record","9-2").put("rank",8).put("standing","1st").put("games",arr(gameJson("e1","pre")))
   .put("roster",arr(JSONObject().put("name","P One").put("jersey","7").put("position","QB"))).put("injuries",JSONArray()).put("news",JSONArray())
   .put("unavailable",JSONArray().put("news").put("overview"))
  val d=parseTeamHubData(j,now)
  assertTrue(d.snapshot is Res.Failed);assertTrue(d.news is Res.Failed)
  assertEquals(FailKind.UNAVAILABLE,(d.news as Res.Failed).kind)
  assertEquals(1,(d.schedule as Res.Ready).value.events.size)
  assertEquals("P One",(d.roster as Res.Ready).value.players.single().name)
 }
 @Test fun anOlderBackendWithOnlyGamesIsMarkedAsHavingNoResults() {
  val old=parseTeamHubData(JSONObject().put("games",arr(gameJson("e1","pre"))),now).schedule as Res.Ready
  assertFalse(old.value.hasResults)
  val full=parseTeamHubData(JSONObject().put("events",arr(gameJson("e1","post","STATUS_FINAL",true,hs="3",aws="1"))),now).schedule as Res.Ready
  assertTrue(full.value.hasResults)
 }
 @Test fun aScheduledGamesDefaultZeroScoreIsNotAScoreAndNullIsNotZero() {
  val scheduled=parseHubEvent(gameJson("e1","pre"))!!
  assertNull(scheduled.home.score);assertNull(scoreLine(scheduled))
  val finalGame=parseHubEvent(gameJson("e2","post","STATUS_FINAL",true,hs="0",aws="3"))!!
  assertEquals("3 - 0",scoreLine(finalGame))
  val liveNoScore=parseHubEvent(gameJson("e3","in","STATUS_IN_PROGRESS",hs=null,aws=null))!!
  assertNull(scoreLine(liveNoScore))   // unknown, never "0 - 0"
 }
 @Test fun providerStatusMapsWithoutLosingExceptionalStates() {
  assertEquals(HubStatus.POSTPONED,hubStatus("post","STATUS_POSTPONED",false))
  assertEquals(HubStatus.CANCELLED,hubStatus("post","STATUS_CANCELED",false))
  assertEquals(HubStatus.SUSPENDED,hubStatus("in","STATUS_SUSPENDED",false))
  assertEquals(HubStatus.LIVE,hubStatus("in","STATUS_IN_PROGRESS",false))
  assertEquals(HubStatus.FINAL,hubStatus("post","STATUS_FULL_TIME",true))
  assertEquals(HubStatus.FINAL,hubStatus("post","STATUS_FINAL",false))
  assertEquals(HubStatus.UNKNOWN,hubStatus("post","STATUS_ABANDONED",false))   // completed=false and not final: never invent a result
  assertEquals(HubStatus.SCHEDULED,hubStatus("pre","STATUS_SCHEDULED",false))
  assertEquals(HubStatus.UNKNOWN,hubStatus(null,null,false))
 }
 @Test fun aGameWithoutAnIdGetsAStableSyntheticIdAndOneWithoutTeamsIsDropped() {
  val a=parseHubEvent(JSONObject().put("sport","S").put("league","L").put("start",5).put("state","pre").put("home",JSONObject().put("id","1").put("name","H")).put("away",JSONObject().put("id","2").put("name","A")))!!
  val b=parseHubEvent(JSONObject().put("sport","S").put("league","L").put("start",5).put("state","pre").put("home",JSONObject().put("id","1").put("name","H")).put("away",JSONObject().put("id","2").put("name","A")))!!
  assertEquals(a.id,b.id);assertTrue(a.id.isNotEmpty())
  assertNull(parseHubEvent(JSONObject().put("home",JSONObject().put("name","H"))))
 }
 @Test fun duplicateEventsAreRemovedByIdAndByRelistedPairing() {
  val list=dedupeEvents(listOf(ev("1",100,HubStatus.SCHEDULED),ev("1",100,HubStatus.SCHEDULED),ev("2",100,HubStatus.SCHEDULED),ev("3",200,HubStatus.SCHEDULED)))
  assertEquals(listOf("1","3"),list.map{it.id})
  // TBD games (start 0) between the same teams are different fixtures, not relistings.
  assertEquals(2,dedupeEvents(listOf(ev("a",0,HubStatus.SCHEDULED),ev("b",0,HubStatus.SCHEDULED))).size)
 }

 // ---- identity ----
 @Test fun ownTeamIsFoundByCanonicalNameOrByAppearingInEveryGameAndNeverGuessed() {
  val games=listOf(ev("1",1,HubStatus.FINAL,home="Alpha FC",away="X"),ev("2",2,HubStatus.FINAL,home="Y",away="Alpha FC"))
  assertEquals("h-Alpha FC",ownSideId(games.take(1),"Alpha"))   // by canonical key (fc stripped)
  assertEquals("h-Alpha FC",ownSideId(games.take(1),"Alpha FC"))
  val noName=listOf(ev("1",1,HubStatus.FINAL,home="Q",away="R"))
  assertNull(ownSideId(noName,"Unrelated"))   // one game, no name match: unknown
  assertNull(isHomeGame(noName[0],null))
  assertEquals(true,isHomeGame(noName[0],"h-Q"));assertEquals(false,isHomeGame(noName[0],"a-R"))
 }
 @Test fun sameNameInDifferentLeaguesStaysTwoEventsAndGameDetailsMatchesOnProviderEventId() {
  val a=ev("100",1,HubStatus.SCHEDULED,league="L1");val b=ev("200",1,HubStatus.SCHEDULED,league="L2")
  val pool=listOf(Game("next-1-100","t","L1","Football","",raw=JSONObject().put("espn_id","100")),
   Game("p","t","L2","Football","",raw=JSONObject().put("live",JSONObject().put("espn_id","200"))))
  assertEquals("next-1-100",findSupportedGame(a,pool)!!.id);assertEquals("p",findSupportedGame(b,pool)!!.id)
  assertNull(findSupportedGame(ev("300",1,HubStatus.SCHEDULED),pool))   // not resolvable by the existing flows: no Game Details
  assertNull(findSupportedGame(a.copy(id=""),pool))
 }
 @Test fun theHubEventBuildsTheSharedHeroSidesAwayFirstWithTheTeamsOwnColourAndNoNameBranches() {
  val team=Team("t.alpha","Alpha Hawks","Football","NCAA","logos/alpha.png",0xFF112233L,0xFF223344L)
  val e=HubEvent("9","Football","NCAA",HubSide("2","Beta Owls","",null,null),HubSide("1","Alpha Hawks","",null,null),now+10,HubStatus.SCHEDULED,"","",emptyList(),"")
  val sides=heroSides(e.asGame(team.id),listOf(team))
  assertEquals(listOf("Alpha Hawks","Beta Owls"),sides.map{it.name})   // away left, home right
  assertEquals(0xFF112233.toInt(),sides[0].color);assertNull(sides[1].color)   // unknown colour stays unknown, never borrowed
 }

 // ---- featured game ----
 @Test fun featuredPrefersLiveThenNearestConfirmedUpcomingThenLatestFinal() {
  val live=ev("L",now-1000,HubStatus.LIVE);val soon=ev("U1",now+500,HubStatus.SCHEDULED);val later=ev("U2",now+9000,HubStatus.SCHEDULED)
  val old=ev("F1",now-9000,HubStatus.FINAL);val recent=ev("F2",now-5000,HubStatus.FINAL)
  assertEquals("L",featuredEvent(listOf(later,soon,old,live,recent),now)!!.id)
  assertEquals("U1",featuredEvent(listOf(later,soon,old,recent),now)!!.id)
  assertEquals("F2",featuredEvent(listOf(old,recent),now)!!.id)
  assertNull(featuredEvent(emptyList(),now))
 }
 @Test fun featuredNeverPicksUnconfirmedPostponedCancelledOrStaleScheduledGames() {
  val tbd=ev("T",0,HubStatus.SCHEDULED);val post=ev("P",now+5,HubStatus.POSTPONED);val canc=ev("C",now+6,HubStatus.CANCELLED);val unk=ev("K",now+7,HubStatus.UNKNOWN)
  assertNull(featuredEvent(listOf(tbd,post,canc,unk),now))
  val longAgoScheduled=ev("S",now-5*3600,HubStatus.SCHEDULED)   // 'pre' long after its start: not a confirmed upcoming game
  assertEquals("F",featuredEvent(listOf(longAgoScheduled,ev("F",now-100000,HubStatus.FINAL)),now)!!.id)
 }
 @Test fun featuredTieBreaksAreDeterministicForDoubleheaders() {
  val g1=ev("B",now+100,HubStatus.SCHEDULED);val g2=ev("A",now+100,HubStatus.SCHEDULED)
  assertEquals("A",featuredEvent(listOf(g1,g2),now)!!.id);assertEquals("A",featuredEvent(listOf(g2,g1),now)!!.id)
  val early=ev("E",now+100,HubStatus.SCHEDULED);val late=ev("Z",now+4000,HubStatus.SCHEDULED)
  assertEquals("E",featuredEvent(listOf(late,early),now)!!.id)
 }
 @Test fun upcomingModuleSkipsTheFeaturedGameAndPutsTbdLast() {
  val list=listOf(ev("1",now+10,HubStatus.SCHEDULED),ev("2",0,HubStatus.SCHEDULED),ev("3",now+5,HubStatus.SCHEDULED),ev("4",now+1,HubStatus.FINAL),ev("5",now+20,HubStatus.POSTPONED))
  assertEquals(listOf("3","5","2"),upcomingEvents(list,"1",10).map{it.id})
 }

 // ---- schedule controls, ordering, time zones ----
 @Test fun scheduleFiltersOrderAndDefaultsAreSportNeutral() {
  val list=listOf(ev("a",300,HubStatus.SCHEDULED,league="X",season="Regular"),ev("b",100,HubStatus.FINAL,league="Y",season="Regular"),ev("c",200,HubStatus.FINAL,league="X",season="Post"),
   ev("d",0,HubStatus.SCHEDULED,league="X"),ev("e",250,HubStatus.LIVE,league="X"),ev("f",400,HubStatus.POSTPONED,league="X"),ev("g",50,HubStatus.CANCELLED,league="X"))
  assertEquals(listOf("g","b","c","e","a","f","d"),filterSchedule(list,ScheduleFilter()).map{it.id})
  assertEquals(listOf("c","b","g"),filterSchedule(list,ScheduleFilter(StatusFilter.RESULTS)).map{it.id})   // newest result first
  assertEquals(listOf("e","a","f","d"),filterSchedule(list,ScheduleFilter(StatusFilter.UPCOMING)).map{it.id})   // the live game is still ahead of the final whistle
  assertEquals(listOf("e"),filterSchedule(list,ScheduleFilter(StatusFilter.LIVE)).map{it.id})
  assertEquals(listOf("c"),filterSchedule(list,ScheduleFilter(StatusFilter.RESULTS,competition="X",season="Post")).map{it.id})
  assertEquals(StatusFilter.UPCOMING,defaultStatusFilter(list))
  assertEquals(StatusFilter.UPCOMING,defaultStatusFilter(list.filter{it.status!=HubStatus.LIVE}))
  assertEquals(StatusFilter.UPCOMING,defaultStatusFilter(list.filter{it.status==HubStatus.LIVE||it.status==HubStatus.FINAL}))
  assertEquals(StatusFilter.RESULTS,defaultStatusFilter(list.filter{it.status==HubStatus.FINAL}))
  assertEquals(StatusFilter.ALL,defaultStatusFilter(emptyList()))
 }
 @Test fun competitionAndSeasonControlsAppearOnlyWhenTheDataSpansMoreThanOne() {
  assertTrue(competitionsOf(listOf(ev("a",1,HubStatus.FINAL,league="X"),ev("b",2,HubStatus.FINAL,league="X"))).isEmpty())
  assertEquals(listOf("X","Y"),competitionsOf(listOf(ev("a",1,HubStatus.FINAL,league="Y"),ev("b",2,HubStatus.FINAL,league="X"))))
  assertTrue(seasonsOf(listOf(ev("a",1,HubStatus.FINAL,season=""),ev("b",2,HubStatus.FINAL,season="S"))).isEmpty())
 }
 @Test fun localDateGroupingFollowsTheViewersZoneAcrossADstChange() {
  val ny=TimeZone.getTimeZone("America/New_York")
  // 2026-11-01 is the US fall-back day (25 hours long): 00:30 EDT and 23:30 EST are the same local date.
  val early=parsePublished("2026-11-01T04:30:00Z")!!;val lateSameDay=parsePublished("2026-11-02T04:30:00Z")!!;val next=parsePublished("2026-11-02T06:00:00Z")!!
  val groups=groupByLocalDate(listOf(ev("1",early,HubStatus.SCHEDULED),ev("2",lateSameDay,HubStatus.SCHEDULED),ev("3",next,HubStatus.SCHEDULED),ev("4",0,HubStatus.SCHEDULED)),ny)
  assertEquals(listOf("2026-11-01","2026-11-02",TBD_DAY),groups.map{it.key})
  assertEquals(listOf("1","2"),groups[0].events.map{it.id})
  // The same instants fall on different dates in UTC: grouping is about the viewer, not the server.
  assertEquals(listOf("2026-11-01","2026-11-02","2026-11-02",TBD_DAY),groupByLocalDate(listOf(ev("1",early,HubStatus.SCHEDULED),ev("2",lateSameDay,HubStatus.SCHEDULED),ev("3",next,HubStatus.SCHEDULED),ev("4",0,HubStatus.SCHEDULED)),TimeZone.getTimeZone("UTC")).flatMap{g->g.events.map{g.key}})
  assertEquals("Date to be announced",dayHeading(TBD_DAY,ny,Locale.US))
 }
 @Test fun timesShowInTheViewersZoneAndMissingStartsAreTbd() {
  val ny=TimeZone.getTimeZone("America/New_York")
  val e=ev("1",parsePublished("2026-07-04T23:30:00Z")!!,HubStatus.SCHEDULED)
  assertEquals("7:30 PM",startTimeLabel(e,ny,Locale.US).replace('\u202f',' '))   // newer JDKs put a narrow no-break space before PM
  assertEquals("Time TBD",startTimeLabel(ev("2",0,HubStatus.SCHEDULED),ny,Locale.US))
  assertEquals("Date and time TBD",startDateTimeLabel(ev("2",0,HubStatus.SCHEDULED),ny,Locale.US))
 }
 @Test fun statusLabelsKeepExceptionalStatesVisible() {
  assertEquals("Upcoming",statusLabel(ev("1",1,HubStatus.SCHEDULED)));assertEquals("Postponed",statusLabel(ev("1",1,HubStatus.POSTPONED)));assertEquals("Cancelled",statusLabel(ev("1",1,HubStatus.CANCELLED)))
  assertEquals("Suspended",statusLabel(ev("1",1,HubStatus.SUSPENDED)));assertEquals("Final",statusLabel(ev("1",1,HubStatus.FINAL)))
  assertEquals("Rain delay",statusLabel(ev("1",1,HubStatus.UNKNOWN,detail="Rain delay")));assertEquals("Status unavailable",statusLabel(ev("1",1,HubStatus.UNKNOWN)))
 }

 // ---- request state: identity / cache isolation, stale responses, last-good ----
 private fun body(rec:String)=JSONObject().put("record",rec).put("games",JSONArray()).put("roster",JSONArray()).put("news",JSONArray())
 @Test fun anAnswerForAnotherTeamOrAnOlderRequestIsDropped() {
  var s=beginHubLoad(TeamHubState(),"team.a",1)
  s=beginHubLoad(s,"team.b",2)
  assertEquals(TeamHubData.LOADING,s.data)   // switching teams never keeps the previous team's content
  val staleA=applyHubResponse(s,"team.a",1,body("A-record"),now);assertSame(s,staleA)
  val olderB=applyHubResponse(s,"team.b",1,body("B-old"),now);assertSame(s,olderB)
  val ok=applyHubResponse(s,"team.b",2,body("B-record"),now)
  assertEquals("B-record",((ok.data.snapshot as Res.Ready).value.record));assertFalse(ok.loading)
  assertEquals("team.b",ok.teamId)
 }
 @Test fun aFailedRefreshKeepsTheSameTeamsLastGoodContentFlaggedStale() {
  var s=applyHubResponse(beginHubLoad(TeamHubState(),"t",1),"t",1,body("9-2"),now)
  s=beginHubLoad(s,"t",2)
  assertTrue(s.loading);assertTrue(s.data.snapshot is Res.Ready)   // refresh in progress: still showing the good copy
  val failed=applyHubFailure(s,"t",2,FailKind.OFFLINE)
  val snap=failed.data.snapshot as Res.Failed
  assertEquals(FailKind.OFFLINE,snap.kind);assertEquals("9-2",snap.last!!.value.record);assertTrue(snap.last!!.stale)
  assertEquals("9-2",failed.data.snapshot.content()!!.value.record)
  // A failure that belongs to an abandoned request changes nothing.
  assertSame(s,applyHubFailure(s,"t",1,FailKind.OFFLINE));assertSame(s,applyHubFailure(s,"other",2,FailKind.OFFLINE))
  // A later success replaces the stale copy.
  val recovered=applyHubResponse(beginHubLoad(failed,"t",3),"t",3,body("10-2"),now+60)
  assertEquals("10-2",(recovered.data.snapshot as Res.Ready).value.record);assertFalse((recovered.data.snapshot as Res.Ready).stale)
 }
 @Test fun withNothingToKeepEverySectionReportsTheFailureAndKindsMapFromStatus() {
  val f=applyHubFailure(beginHubLoad(TeamHubState(),"t",1),"t",1,FailKind.PERMISSION)
  listOf(f.data.snapshot,f.data.schedule,f.data.roster,f.data.news).forEach{r->assertTrue(r is Res.Failed);assertNull(r.content())}
  assertEquals(FailKind.OFFLINE,hubFailKind(0));assertEquals(FailKind.PERMISSION,hubFailKind(401));assertEquals(FailKind.PERMISSION,hubFailKind(403))
  assertEquals(FailKind.UNAVAILABLE,hubFailKind(502));assertEquals(FailKind.UNAVAILABLE,hubFailKind(409))
 }
 @Test fun aFailedResourceWithNothingSavedIsDistinguishedFromOneStillShowingSavedContent() {
  val ready=Res.Ready("x",now)
  assertTrue(Res.Failed<String>(FailKind.OFFLINE).failedEmpty());assertFalse(Res.Failed(FailKind.OFFLINE,ready.copy(stale=true)).failedEmpty())
  assertFalse(ready.failedEmpty());assertFalse(Res.Loading.failedEmpty())
  assertEquals(FailKind.PERMISSION,Res.Failed<String>(FailKind.PERMISSION).failKindOrNull());assertNull(ready.failKindOrNull())
 }
 @Test fun failureWordingSeparatesOfflinePermissionAndUnavailableAndStatesWhenSavedContentIsShown() {
  val offline=hubFailureText(FailKind.OFFLINE,"the roster",null,false,now)
  val perm=hubFailureText(FailKind.PERMISSION,"the roster",null,false,now)
  val down=hubFailureText(FailKind.UNAVAILABLE,"the roster",now-300,true,now)
  assertTrue("Can't reach" in offline.first);assertTrue("Pair this TV" in perm.first);assertTrue("The roster unavailable" in down.first)
  assertTrue("saved" in down.second&&"5 min ago" in down.second)
  assertEquals(3,setOf(offline.first,perm.first,down.first).size)
 }

 // ---- live truth from the guide ----
 private fun liveGame(id:String?,home:String="H",away:String="A"):Game {
  val raw=JSONObject().put("start",now-600).apply{if(id!=null)put("live",JSONObject().put("espn_id",id))}
  val live=LiveScore(home,away,"3","2",null,3,"in","3rd - 4:00",3,"4:00",verifiedLive=true,likelyReplay=false,notable=false,checkedAt=now-20,venue="V",networks=listOf("N"))
  return Game("g","$away vs $home","L","Football","",raw=raw,live=live,eventPhase="live")
 }
 @Test fun aLiveGuideGameOverridesTheProviderEventWithoutLosingItsTeamIds() {
  val provider=ev("77",now-600,HubStatus.SCHEDULED,home="H",away="A",season="Regular")
  val live=liveGameEvent(liveGame("77"))!!
  assertEquals(HubStatus.LIVE,live.status);assertEquals("3",live.home.score);assertEquals(now-20,live.updatedAt)
  val merged=mergeLive(listOf(provider),listOf(live)).single()
  assertEquals(HubStatus.LIVE,merged.status);assertEquals("3",merged.home.score);assertEquals("h-H",merged.home.id);assertEquals("Regular",merged.season)
  // A live game the schedule lacks is added; one without an event id cannot be matched and is not invented.
  assertEquals(2,mergeLive(listOf(ev("1",5,HubStatus.SCHEDULED)),listOf(live)).size)
  assertNull(liveGameEvent(liveGame(null)))
  assertNull(liveGameEvent(liveGame("77").copy(eventPhase="final")))
 }
 @Test fun foldingLiveGamesIntoTheHubOnlyTouchesTheSchedule() {
  val data=parseTeamHubData(JSONObject().put("record","1-0").put("games",arr(gameJson("77","pre",start=now-600))).put("roster",JSONArray()).put("news",JSONArray()),now)
  val s=TeamHubState("t",1,false,null,data).withLive(listOf(liveGameEvent(liveGame("77"))!!))
  assertEquals(HubStatus.LIVE,(s.data.schedule as Res.Ready).value.events.single().status)
  assertSame(data.snapshot,s.data.snapshot);assertSame(data.roster,s.data.roster)
  val failed=TeamHubState("t",1,false,FailKind.OFFLINE,TeamHubData.failed(FailKind.OFFLINE))
  assertSame(failed.data.schedule,failed.withLive(listOf(liveGameEvent(liveGame("77"))!!)).data.schedule)
 }

 private fun fixtureGame(home:Boolean=false)=Game("next-9-77","Lady vs Opp","NCAA","Softball","",fixture=true,eventPhase="upcoming",
  raw=JSONObject().put("espn_id","77").put("opponent","Opp State").put("home",home).put("kickoff",now+86400).put("team_espn_id","9").put("opponent_espn_id","5"))
 @Test fun theTeamsNextFixtureFillsAnEmptyScheduleAndStaysResolvableForStreamSearch() {
  val e=fixtureEvent(fixtureGame(),"Lady Vols")!!
  assertEquals("77",e.id);assertEquals("Opp State",e.home.name);assertEquals("Lady Vols",e.away.name);assertEquals(HubStatus.SCHEDULED,e.status)
  assertEquals("Lady Vols",fixtureEvent(fixtureGame(true),"Lady Vols")!!.home.name)
  assertNull(fixtureEvent(fixtureGame().copy(fixture=false),"T"));assertNull(fixtureEvent(fixtureGame().copy(raw=JSONObject()),"T"))
  val empty=parseTeamHubData(JSONObject().put("games",JSONArray()).put("roster",JSONArray()).put("news",JSONArray()),now)
  val s=TeamHubState("t",1,false,null,empty).withLive(emptyList(),listOf(e))
  assertEquals(listOf("77"),(s.data.schedule as Res.Ready).value.events.map{it.id})
  assertNotNull(findSupportedGame(e,listOf(fixtureGame())))
  // The provider's own version of the same game wins; nothing is duplicated.
  val provider=ev("77",now+86400,HubStatus.SCHEDULED)
  assertEquals(1,addMissing(listOf(provider),listOf(e)).size);assertEquals("Home",addMissing(listOf(provider),listOf(e))[0].home.name)
 }

 @Test fun anAnnouncedGuideProgramBecomesAnUpcomingEventResolvableByItsOwnId() {
  val p=Game("prog-1","Tennessee vs Opp State","NCAA","Softball","",raw=JSONObject().put("start",now+3*86400),eventPhase="upcoming")
  val e=programEvent(p)!!
  assertEquals("prog-1",e.id);assertEquals(HubStatus.SCHEDULED,e.status);assertEquals("Tennessee",e.home.name)
  assertEquals("Tennessee",programEvent(p.copy(title="Tennessee at Opp State"))!!.away.name)
  for(t in listOf("Away at Home","Away @ Home","Home vs Away","Home vs. Away")){val x=programEvent(p.copy(title=t))!!;assertEquals(t,"Home",x.home.name);assertEquals(t,"Away",x.away.name)}
  val ag=p.copy(title="Away at Home");assertEquals("prog-1",findSupportedGame(programEvent(ag)!!,listOf(ag))!!.id)
  assertNull(programEvent(p.copy(title="Studio show")));assertNull(programEvent(p.copy(isGame=false)));assertNull(programEvent(p.copy(eventPhase="live")))
  assertEquals("prog-1",findSupportedGame(e,listOf(p))!!.id)
 }

 // ---- roster ----
 private fun p(id:String,name:String,jersey:String?,pos:String?)=RosterPlayer(id,name,jersey,pos,null)
 @Test fun positionFiltersComeFromTheDataInSportOrderAndUnknownPositionsStayOutOfTheChips() {
  val players=listOf(p("1","A","1","WR"),p("2","B","2","QB"),p("3","C","3","ZZ"),p("4","D","4",null),p("5","E","5","QB"))
  assertEquals(listOf("QB","WR","ZZ"),rosterPositions(players,"Football"))
  assertEquals(listOf("QB","WR","ZZ"),rosterPositions(players,"Unheard Of Sport").sorted())   // no registry entry: still derived from the data
  assertTrue(rosterPositions(listOf(p("1","A",null,null)),"Football").isEmpty())
 }
 @Test fun rosterSearchFoldsAccentsMatchesJerseysAndSortsUnknownNumbersLast() {
  val players=listOf(p("1","Álvaro Núñez","7","K"),p("2","Zed Plain",null,"QB"),p("3","Bob Seven","7","QB"),p("4","Cy Ten","10","QB"),p("5","Dee Two","2","QB"))
  assertEquals(listOf("1"),filterRoster(players,null,"alvaro nunez").map{it.id})
  assertEquals(listOf("1","3"),filterRoster(players,null,"7").map{it.id}.filter{it in setOf("1","3")})   // duplicate numbers both appear
  assertEquals(listOf("5","3","4","2"),filterRoster(players,"QB","").map{it.id})   // jersey 2, 7, 10, then unknown
  assertEquals(emptyList<RosterPlayer>(),filterRoster(players,null,"nobody"))
  assertEquals("—",jerseyLabel(players[1]));assertEquals("#7",jerseyLabel(players[0]))
  assertEquals("ÁN",playerInitials("Álvaro Núñez"))
 }
 @Test fun namesakesWithoutProviderIdsRemainTwoPlayers() {
  val d=parseTeamHubData(JSONObject().put("roster",arr(JSONObject().put("name","Same Name").put("jersey","1"),JSONObject().put("name","Same Name").put("jersey","2"))),now)
  assertEquals(2,(d.roster as Res.Ready).value.players.size)
 }

 // ---- news ----
 @Test fun onlyPlainWebLinksSurviveAndTheCanonicalKeyIgnoresTrackingAndSlashes() {
  assertEquals("https://example.com/a",safeArticleUrl(" https://example.com/a "))
  listOf("javascript:alert(1)","file:///etc/passwd","intent://x#Intent;end","ftp://example.com/a","https://user:pw@example.com/a","not a url","","https:///nohost",null).forEach{assertNull(it.toString(),safeArticleUrl(it))}
  assertNull(safeArticleUrl("https://example.com/"+"a".repeat(3000)))
  assertEquals(canonicalUrlKey("https://www.Example.com/story/?utm=1#frag"),canonicalUrlKey("http://example.com/story"))
  assertEquals("example.com",urlHost("https://www.example.com/x"))
 }
 @Test fun newsIsDeduplicatedByIdLinkAndHeadlineNewestFirstAndUndatedLast() {
  val a=NewsArticle("1","Big Win!","Wire",100,"https://x.com/a?utm=1")
  val dupLink=NewsArticle("2","Different headline","Wire",200,"https://x.com/a/")
  val dupHead=NewsArticle("3","big   win","Other",300,null)
  val undated=NewsArticle("4","Undated story",null,null,null)
  val newest=NewsArticle("5","Newest","Wire",900,null)
  val sameId=NewsArticle("1","Another","Wire",50,null)
  assertEquals(listOf("5","1","4"),prepareNews(listOf(a,dupLink,dupHead,undated,newest,sameId)).map{it.id})
  val (top,rest)=featuredNews(prepareNews(listOf(a,newest)));assertEquals("5",top!!.id);assertEquals(listOf("1"),rest.map{it.id})
 }
 @Test fun articleParsingNeverInventsSourceDateOrLink() {
  val x=parseArticle(JSONObject().put("headline","H").put("published","garbage").put("url","javascript:alert(1)"))!!
  assertNull(x.source);assertNull(x.published);assertNull(x.url)
  assertEquals("Source not provided",attributionLabel(x));assertEquals("Date unavailable",publishedLabel(x,now))
  assertEquals("example.com",attributionLabel(NewsArticle("1","H",null,1,"https://example.com/x")))
  assertNull(parseArticle(JSONObject().put("headline","  ")))
  assertEquals(1_700_000_000L,parsePublished("2023-11-14T22:13:20Z"));assertEquals(1_700_000_000L,parsePublished(1_700_000_000L))
  assertNull(parsePublished("2023-13-45T99:99:99Z"));assertNull(parsePublished(JSONObject.NULL));assertNull(parsePublished(""))
 }

 // ---- recorded games ----
 private fun rec(id:String,state:String,reason:String?=null,playable:Boolean=true,start:Boolean=true,end:Boolean=true)=Recording(id,"b","Team","T $id",state,reason,start,end,playable,1000,900,"p")
 @Test fun everyFilterShowsOnlyItsOwnStateAndScheduledIsNeverFaked() {
  val all=listOf(rec("r","ready"),rec("p","ready",end=false,reason="interrupted"),rec("l","recording",end=false),rec("f","failed","provider_busy",false),rec("x","expired","evicted",false))
  assertEquals(listOf("r","p"),all.filter{recordingMatches(it,RecFilter.READY)}.map{it.id})
  assertEquals(listOf("l"),all.filter{recordingMatches(it,RecFilter.RECORDING)}.map{it.id})
  assertEquals(listOf("f"),all.filter{recordingMatches(it,RecFilter.FAILED)}.map{it.id})
  assertTrue(all.none{recordingMatches(it,RecFilter.SCHEDULED)})
  assertEquals(5,all.count{recordingMatches(it,RecFilter.ALL)})
  assertEquals(mapOf(RecFilter.ALL to 5,RecFilter.READY to 2,RecFilter.RECORDING to 1,RecFilter.SCHEDULED to 0,RecFilter.FAILED to 1),recordingCounts(all))
 }
 @Test fun onlyPlayIsEverOfferedAndOnlyForPlayableRecordings() {
  assertNotNull(recordingActions(rec("r","ready")).play)
  assertNotNull(recordingActions(rec("l","recording",end=false)).play)
  listOf(rec("f","failed","provider_unavailable",false),rec("x","expired","media_missing",false),rec("n","ready",playable=false)).forEach{assertNull(it.id,recordingActions(it).play)}
 }
 @Test fun recordedViewSeparatesNotRecordedPairingUnavailableEmptyNoMatchesAndContent() {
  fun snap(vararg r:Recording)=RecordingsSnapshot(true,"ok",listOf(RecordingBucket("bk","bk",r.toList())))
  assertEquals(TeamRecordingsKind.NOT_RECORDED,recordedView(snap(rec("r","ready")),null,"T",false,true,RecFilter.ALL).base.kind)
  val unpaired=recordedView(null,"bk","T",false,false,RecFilter.ALL);assertTrue(unpaired.needsPairing);assertEquals(TeamRecordingsKind.UNAVAILABLE,unpaired.base.kind)
  val offline=recordedView(null,"bk","T",true,true,RecFilter.ALL);assertFalse(offline.needsPairing)   // paired but unreachable is not a pairing problem
  assertEquals(TeamRecordingsKind.EMPTY,recordedView(snap(),"bk","T",false,true,RecFilter.ALL).base.kind)
  val none=recordedView(snap(rec("r","ready")),"bk","T",false,true,RecFilter.FAILED);assertTrue(none.noMatches);assertTrue(none.shown.isEmpty())
  val some=recordedView(snap(rec("r","ready")),"bk","T",false,true,RecFilter.READY);assertFalse(some.noMatches);assertEquals(listOf("r"),some.shown.map{it.id})
 }
}
