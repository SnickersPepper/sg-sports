package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Phase 7: DVR state mapping, wording, focus order, budget sync and playback recovery. No network, no Android. */
class DvrLogicTest {
 private fun rec(id:String="r1",state:String="ready",reason:String?=null,start:Boolean=true,end:Boolean=true,playable:Boolean=true,eventStart:Long=1000,startedAt:Long=900,provider:String="dream",bucket:String="tennessee-football",title:String="Tennessee vs Florida")=
  Recording(id,bucket,"Tennessee football",title,state,reason,start,end,playable,eventStart,startedAt,provider)
 private fun snap(vararg buckets:RecordingBucket,enabled:Boolean=true,storage:String="ok")=RecordingsSnapshot(enabled,storage,buckets.toList())
 private fun bucket(id:String,vararg r:Recording)=RecordingBucket(id,id,r.toList())

 // ---- state mapping ----
 @Test fun everyServerStateMapsToOneHonestUiState() {
  assertEquals(RecordingUi.RECORDING,recordingUi(rec(state="recording",end=false)))
  assertEquals(RecordingUi.READY,recordingUi(rec()))
  assertEquals(RecordingUi.PARTIAL,recordingUi(rec(start=false)))
  assertEquals(RecordingUi.PARTIAL,recordingUi(rec(end=false,reason="interrupted")))
  assertEquals(RecordingUi.STORAGE_PRESSURE,recordingUi(rec(end=false,reason="storage_pressure")))
  assertEquals(RecordingUi.STORAGE_PRESSURE,recordingUi(rec(state="failed",reason="storage_pressure",playable=false)))
  assertEquals(RecordingUi.STORAGE_PRESSURE,recordingUi(rec(state="failed",reason="capacity_unknown",playable=false)))
  assertEquals(RecordingUi.FAILED,recordingUi(rec(state="failed",reason="provider_busy",playable=false)))
  assertEquals(RecordingUi.EXPIRED,recordingUi(rec(state="expired",reason="evicted",playable=false)))
  assertEquals(RecordingUi.EXPIRED,recordingUi(rec(state="something-new",playable=false)))
 }
 @Test fun onlyCompleteRecordingsClaimTheBeginningAndAllEnd() {
  val ready=recordingCard(rec())
  assertEquals("Watch from the beginning",ready.action);assertTrue(ready.playable)
  val late=recordingCard(rec(start=false,startedAt=1000+12*60))
  assertTrue("12 min" in late.detail);assertFalse(late.action!!.contains("beginning"))
  val early=recordingCard(rec(end=false,reason="storage_pressure"))
  assertEquals("Watch what was saved",early.action)
 }
 @Test fun unplayableStatesOfferNoPlayAction() {
  listOf(rec(state="failed",reason="provider_unavailable",playable=false),rec(state="expired",reason="media_missing",playable=false),
   rec(state="failed",reason="storage_pressure",playable=false)).forEach{
   val c=recordingCard(it);assertFalse(c.playable);assertNull(c.action);assertTrue(c.detail.isNotBlank())
  }
  assertTrue("storage" in recordingCard(rec(state="failed",reason="storage_pressure",playable=false)).detail)
  assertTrue("latest three" in recordingCard(rec(state="expired",reason="evicted",playable=false)).detail)
 }
 @Test fun recordingInProgressIsNeverCalledLive() {
  val c=recordingCard(rec(state="recording",end=false))
  assertEquals("Recording",c.badge);assertTrue("not the live broadcast" in c.detail)
  listOf(rec(),rec(start=false),rec(state="recording",end=false),rec(state="failed",playable=false)).forEach{
   val card=recordingCard(it);val text=card.badge+" "+card.detail+" "+(card.action?:"")
   assertFalse(text.replace("not the live broadcast","").contains("live",ignoreCase=true))
  }
  assertFalse(recordingCard(rec(state="recording",end=false,playable=false)).playable)
 }
 @Test fun parsesServerPayloadAndDropsMalformedRows() {
  val j=JSONObject("""{"enabled":true,"storage":"pressure","buckets":[{"id":"fc-barcelona","label":"FC Barcelona","recordings":[
   {"id":"a","bucket":"fc-barcelona","team":"FC Barcelona","title":"t","state":"ready","reason":null,"complete_start":true,"complete_end":false,"playable":true,"event_start":5,"started_at":9,"provider_id":"dream"},{"title":"no id"}]}]}""")
  val s=parseRecordings(j);assertTrue(s.enabled);assertEquals("pressure",s.storage)
  val r=s.buckets.single().recordings.single();assertNull(r.reason);assertTrue(r.completeStart);assertFalse(r.completeEnd);assertEquals("dream",r.provider)
 }

 @Test fun noToleranceForFromTheBeginning() {
  // Even one second late: the server flag alone cannot make it a complete, "from the beginning" recording.
  val oneSecondLate=rec(start=true,startedAt=1001)
  assertFalse(fromBeginning(oneSecondLate));assertEquals(RecordingUi.PARTIAL,recordingUi(oneSecondLate))
  assertFalse(recordingCard(oneSecondLate).action!!.contains("beginning"))
  assertTrue("under a minute" in recordingCard(oneSecondLate).detail)
  assertTrue(fromBeginning(rec(startedAt=1000)));assertTrue(fromBeginning(rec(startedAt=400)))
  assertEquals(RecordingUi.READY,recordingUi(rec(startedAt=400)))
 }
 @Test fun partialRecordingsNeverClaimComplete() {
  listOf(rec(start=false),rec(end=false,reason="interrupted"),rec(start=false,end=false),rec(end=false,reason="storage_pressure"),rec(state="recording",end=false)).forEach{
   val c=recordingCard(it);assertNotEquals(RecordingUi.READY,c.kind)
   assertFalse("Complete" in c.detail);assertNotEquals("Saved",c.badge)
  }
 }
 @Test fun discardedCapturesAreHonestAndNotPlayable() {
  listOf("wrong_content","unverified","schedule_changed","not_started","cancelled").forEach{reason->
   val c=recordingCard(rec(state="failed",reason=reason,playable=false,start=false,end=false))
   assertEquals(RecordingUi.FAILED,c.kind);assertFalse(c.playable);assertNull(c.action);assertTrue("deleted" in c.detail||"stopped" in c.detail)
  }
  assertTrue("damaged" in recordingCard(rec(state="expired",reason="integrity_failed",playable=false)).detail)
 }

 // ---- page states ----
 @Test fun pageStatesAreDistinct() {
  assertEquals(RecordingsScreenKind.UNAVAILABLE,recordingsScreen(null,false).kind)
  assertTrue("Can't reach" in recordingsScreen(null,true).detail)
  assertEquals(RecordingsScreenKind.DISABLED,recordingsScreen(snap(bucket("a"),enabled=false),false).kind)
  assertEquals(RecordingsScreenKind.EMPTY,recordingsScreen(snap(bucket("a")),false).kind)
  assertEquals(RecordingsScreenKind.CONTENT,recordingsScreen(snap(bucket("a",rec())),false).kind)
  // Recording turned off but old saved games remain: they are still shown.
  assertEquals(RecordingsScreenKind.CONTENT,recordingsScreen(snap(bucket("a",rec()),enabled=false),false).kind)
 }
 @Test fun storageNoticeIsStatedNeverImplied() {
  assertNull(recordingsScreen(snap(bucket("a",rec())),false).storageNotice)
  assertTrue("Storage is low" in recordingsScreen(snap(bucket("a",rec()),storage="pressure"),false).storageNotice!!)
  assertTrue("not being recorded" in recordingsScreen(snap(bucket("a"),storage="unknown"),false).storageNotice!!)
  assertEquals(RecordingsScreenKind.EMPTY,recordingsScreen(snap(bucket("a"),storage="pressure"),false).kind)
  assertNull(recordingsScreen(snap(bucket("a"),enabled=false,storage="pressure"),false).storageNotice)
  assertNull(recordingsScreen(snap(bucket("a",rec()),enabled=false,storage="unknown"),false).storageNotice)
 }

 // ---- D-pad order and focus restoration ----
 private val three=snap(bucket("football",rec("f1"),rec("f2")),bucket("softball"),bucket("barca",rec("b1")))
 @Test fun focusOrderIsFixedTopToBottomThenRefresh() {
  assertEquals(listOf("rec:f1","rec:f2","rec:b1","rec-refresh"),recordingFocusOrder(three))
  assertEquals(listOf("rec-refresh"),recordingFocusOrder(null))
 }
 @Test fun focusReturnsToTheOpenedRecordingOrItsNearestNeighbour() {
  assertEquals("rec:f2",recordingsFocusTarget("rec:f2",three,three))
  val afterEviction=snap(bucket("football",rec("f1")),bucket("softball"),bucket("barca",rec("b1")))
  assertEquals("rec:b1",recordingsFocusTarget("rec:f2",three,afterEviction))  // the following surviving neighbour wins
  val onlyRefresh=snap(bucket("football"),bucket("softball"),bucket("barca"))
  assertEquals("rec-refresh",recordingsFocusTarget("rec:f2",three,onlyRefresh))
  assertEquals("rec:f1",recordingsFocusTarget(null,three,three))
 }

 // ---- provider budget ----
 private class Gateway:StreamGateway {
  val opened=mutableListOf<String>()
  override fun stop(session:String){}
  override fun open(pane:Int,feed:Feed,result:(String?)->Unit){opened.add(feed.provider);result("s${opened.size}")}
 }
 private fun feed(provider:String)=Feed(provider,provider,"","",JSONObject("""{"provider_id":"$provider","stream_id":"1"}"""))
 @Test fun onlyInProgressRecordingsHoldAProvider() {
  val holds=activeRecordingHolds(snap(bucket("a",rec("1",state="recording",end=false,provider="dream"),rec("2",provider="magnum"),rec("3",state="failed",provider="magnum",playable=false))))
  assertEquals(mapOf("dvr:1" to "dream"),holds)
  assertTrue(activeRecordingHolds(null).isEmpty())
 }
 @Test fun dvrHoldBlocksPlaybackOnThatProviderAndReleaseRestoresIt() {
  val g=Gateway();val c=PlaybackCoordinator(g);val held=mutableMapOf<String,String>()
  assertTrue(syncDvrHolds(c,held,mapOf("dvr:1" to "dream")).isEmpty())
  val refused=c.open(0,feed("dream"))
  assertTrue(refused is Decision.Rejected&&refused.reason==RejectReason.PROVIDER_BUSY)
  assertEquals(emptyList<String>(),g.opened)
  assertEquals(Decision.Accepted,c.open(0,feed("magnum")))
  syncDvrHolds(c,held,emptyMap())          // recording ended or was cancelled
  assertEquals(0,c.inUse("dream"))
  assertEquals(Decision.Accepted,c.open(1,feed("dream")))
  assertTrue(c.withinBudget())
 }
 @Test fun holdSyncIsIdempotentAndNeverExceedsTheLimit() {
  val c=PlaybackCoordinator(Gateway());val held=mutableMapOf<String,String>()
  syncDvrHolds(c,held,mapOf("dvr:1" to "dream"));syncDvrHolds(c,held,mapOf("dvr:1" to "dream"))
  assertEquals(1,c.inUse("dream"))
  // A second recording on the same provider cannot be granted: refused, not over-counted.
  val refused=syncDvrHolds(c,held,mapOf("dvr:1" to "dream","dvr:2" to "dream"))
  assertEquals(setOf("dvr:2"),refused);assertEquals(1,c.inUse("dream"));assertTrue(c.withinBudget())
  // Provider changes for the same owner: old slot released, new one taken.
  syncDvrHolds(c,held,mapOf("dvr:1" to "magnum"))
  assertEquals(0,c.inUse("dream"));assertEquals(1,c.inUse("magnum"))
 }
 @Test fun playbackInFlightKeepsItsSlotWhenADvrHoldIsRequested() {
  val g=Gateway();val c=PlaybackCoordinator(g);val held=mutableMapOf<String,String>()
  c.open(0,feed("dream"))
  val refused=syncDvrHolds(c,held,mapOf("dvr:9" to "dream"))
  assertEquals(setOf("dvr:9"),refused);assertEquals(1,c.inUse("dream"));assertTrue(c.withinBudget())
  assertEquals(PaneStatus.PLAYING,c.panes[0].status)
 }
 @Test fun secondPaneNeverOffersARecordingProvider() {
  val c=PlaybackCoordinator(Gateway());val held=mutableMapOf<String,String>()
  c.open(0,feed("magnum"));syncDvrHolds(c,held,mapOf("dvr:1" to "dream"))
  assertTrue(c.secondPaneChoices(listOf(feed("dream"),feed("magnum"))).isEmpty())
  syncDvrHolds(c,held,emptyMap())
  assertEquals(listOf("dream"),c.secondPaneChoices(listOf(feed("dream"),feed("magnum"))).map{it.provider})
 }

 // ---- playback recovery ----
 @Test fun recoveryRetriesThenGivesUpHonestly() {
  val ok=rec()
  assertEquals(RecoveryAction.RETRY,recordingPlaybackRecovery(ok,1,false).action)
  assertEquals(RecoveryAction.RETRY,recordingPlaybackRecovery(ok,MAX_RECORDING_RETRIES,false).action)
  val gaveUp=recordingPlaybackRecovery(ok,MAX_RECORDING_RETRIES+1,false)
  assertEquals(RecoveryAction.BACK,gaveUp.action);assertTrue("incomplete" in gaveUp.message)
 }
 @Test fun recoveryStopsWhenTheRecordingIsGoneOrNoLongerPlayable() {
  val gone=recordingPlaybackRecovery(null,1,false)
  assertEquals(RecoveryAction.BACK,gone.action);assertTrue("no longer available" in gone.message)
  val evicted=recordingPlaybackRecovery(rec(state="expired",reason="evicted",playable=false),1,false)
  assertEquals(RecoveryAction.BACK,evicted.action);assertTrue("latest three" in evicted.message)
  val missing=recordingPlaybackRecovery(rec(state="expired",reason="media_missing",playable=false),1,false)
  assertTrue("no longer available" in missing.message)
 }
 @Test fun offlineRecoveryRetriesThenStops() {
  assertEquals(RecoveryAction.RETRY,recordingPlaybackRecovery(null,1,true).action)
  assertEquals(RecoveryAction.BACK,recordingPlaybackRecovery(null,MAX_RECORDING_RETRIES+1,true).action)
 }
 @Test fun endOfVideoDistinguishesCaughtUpFromFinished() {
  assertTrue("not live" in recordingEndedMessage(rec(state="recording",end=false)))
  assertEquals("End of the recording.",recordingEndedMessage(rec()))
  assertEquals("End of what was saved.",recordingEndedMessage(rec(end=false,reason="interrupted")))
  assertEquals("End of what was saved.",recordingEndedMessage(null))
 }
 @Test fun playerLabelStatesCompletenessAndNeverLive() {
  assertEquals("Saved recording · from the beginning",recordingPlayerLabel(rec()))
  assertTrue("8 min into the game" in recordingPlayerLabel(rec(start=false,startedAt=1000+8*60)))
  assertEquals("Saved recording",recordingPlayerLabel(null))
 }

 // ---- Phase 7 revision: ordering, per-team collections, navigation ----
 private fun json(vararg buckets:String)="""{"enabled":true,"storage":"ok","buckets":[${buckets.joinToString(",")}]}"""
 private fun jb(id:String,vararg rows:String)="""{"id":"$id","label":"$id","recordings":[${rows.joinToString(",")}]}"""
 private fun jr(id:String,eventStart:Long,startedAt:Long=eventStart-60)="""{"id":"$id","bucket":"x","state":"ready","complete_start":true,"complete_end":true,"playable":true,"event_start":$eventStart,"started_at":$startedAt,"provider_id":"dream"}"""

 @Test fun parsedRecordingsAreNewestFirstWhateverTheServerOrder() {
  val a=parseRecordings(JSONObject(json(jb("b",jr("old",100),jr("new",300),jr("mid",200)))))
  val b=parseRecordings(JSONObject(json(jb("b",jr("mid",200),jr("new",300),jr("old",100)))))
  assertEquals(listOf("new","mid","old"),a.buckets.single().recordings.map{it.id})
  assertEquals(a,b)
 }
 @Test fun orderUsesGameStartThenCaptureStartThenId() {
  assertEquals(listOf("late-game","no-start-but-recent","a","b"),
   newestFirst(listOf(rec("b",eventStart=500,startedAt=400),rec("a",eventStart=500,startedAt=400),
    rec("no-start-but-recent",eventStart=0,startedAt=700),rec("late-game",eventStart=900,startedAt=100))).map{it.id})
  assertEquals(900L,recordingTime(rec(eventStart=900,startedAt=100)));assertEquals(700L,recordingTime(rec(eventStart=0,startedAt=700)))
 }
 @Test fun eachCollectionKeepsItsOwnLatestThreeAndIdsAreUnique() {
  val five=(1..5).map{jr("f$it",it*100L)}.toTypedArray()
  val s=parseRecordings(JSONObject(json(jb("football",*five),jb("barca",jr("b1",50),jr("f5",9999)))))
  assertEquals(listOf("f5","f4","f3"),s.buckets[0].recordings.map{it.id})
  assertEquals(listOf("b1"),s.buckets[1].recordings.map{it.id})   // f5 already shown in football; one id appears once
 }
 @Test fun teamsMapToTheirOwnCollectionOnly() {
  assertEquals("fc-barcelona",recordingBucketFor("Soccer","UEFA Champions League","FC Barcelona"))
  assertEquals("tennessee-football",recordingBucketFor("Football","NCAA","Tennessee Volunteers"))
  assertEquals("tennessee-softball",recordingBucketFor("Softball","NCAA","Lady Vols"))
  assertNull(recordingBucketFor("Football","NFL","Kansas City Chiefs"))
  assertNull(recordingBucketFor("Baseball","MLB","St. Louis Cardinals"))
  assertNull(recordingBucketFor("Football","NFL","Tennessee Titans"))
 }
 private val mixed=snap(bucket("tennessee-football",rec("f1"),rec("f2")),bucket("tennessee-softball",rec("s1",bucket="tennessee-softball")),bucket("fc-barcelona"))
 @Test fun hubShowsOnlyThatTeamsRecordingsNewestFirst() {
  val v=teamRecordings(mixed,"tennessee-softball","Lady Vols",false)
  assertEquals(TeamRecordingsKind.CONTENT,v.kind);assertEquals(listOf("s1"),v.recordings.map{it.id})
  val f=teamRecordings(snap(bucket("tennessee-football",rec("old",eventStart=100,startedAt=50),rec("new",eventStart=900,startedAt=800)),bucket("fc-barcelona",rec("x"))),"tennessee-football","Tennessee Volunteers",false)
  assertEquals(listOf("new","old"),f.recordings.map{it.id})
 }
 @Test fun hubStatesAreTruthfulAndDistinct() {
  assertEquals(TeamRecordingsKind.UNAVAILABLE,teamRecordings(null,"fc-barcelona","FC Barcelona",true).kind)
  assertTrue("Can't reach" in teamRecordings(null,"fc-barcelona","FC Barcelona",true).detail)
  assertEquals(TeamRecordingsKind.EMPTY,teamRecordings(mixed,"fc-barcelona","FC Barcelona",false).kind)   // other teams' recordings don't make it content
  assertEquals(TeamRecordingsKind.EMPTY,teamRecordings(snap(),"fc-barcelona","FC Barcelona",false).kind)   // collection missing from the payload
  assertEquals(TeamRecordingsKind.DISABLED,teamRecordings(snap(bucket("fc-barcelona"),enabled=false),"fc-barcelona","FC Barcelona",false).kind)
  assertEquals(TeamRecordingsKind.CONTENT,teamRecordings(snap(bucket("fc-barcelona",rec(bucket="fc-barcelona")),enabled=false),"fc-barcelona","FC Barcelona",false).kind)
  val pressure=teamRecordings(snap(bucket("fc-barcelona"),storage="pressure"),"fc-barcelona","FC Barcelona",false)
  assertEquals(TeamRecordingsKind.EMPTY,pressure.kind);assertTrue("Storage is low" in pressure.storageNotice!!)
  val none=teamRecordings(mixed,null,"St. Louis Cardinals",false)
  assertEquals(TeamRecordingsKind.NOT_RECORDED,none.kind);assertTrue(none.recordings.isEmpty())
  assertEquals(TeamRecordingsKind.NOT_RECORDED,teamRecordings(null,null,"St. Louis Cardinals",true).kind)  // no collection: never "unavailable"
 }
 @Test fun hubPlaybackCardsUseTheSameWordingAsTheMainPage() {
  val r=rec(state="failed",playable=false,reason="provider_unavailable")
  val v=teamRecordings(snap(bucket("tennessee-football",r)),"tennessee-football","Tennessee Volunteers",false)
  assertEquals(recordingCard(r),recordingCard(v.recordings.single()));assertFalse(recordingCard(v.recordings.single()).playable)
 }
 @Test fun hubFocusOrderIsTabsThenRecordingsThenRefresh() {
  val v=teamRecordings(snap(bucket("tennessee-football",rec("f1",eventStart=3),rec("f2",eventStart=2),rec("f3",eventStart=1))),"tennessee-football","Tennessee Volunteers",false)
  val tabs=listOf("hub-tab:Overview","hub-tab:Schedule","hub-tab:Roster","hub-tab:News","hub-tab:Recorded Games")
  assertEquals(tabs+listOf("rec:f1","rec:f2","rec:f3","rec-refresh"),hubRecordedFocusOrder(v))
  assertEquals(tabs,hubRecordedFocusOrder(teamRecordings(null,"tennessee-football","T",false)))
  // focus returns to the opened recording, or its next surviving neighbour
  val after=hubRecordedFocusOrder(teamRecordings(snap(bucket("tennessee-football",rec("f1",eventStart=3),rec("f3",eventStart=1))),"tennessee-football","T",false))
  assertEquals("rec:f3",restoreTarget("rec:f2",hubRecordedFocusOrder(v),after))
 }
 @Test fun recordingsIsAFirstClassMainTabAndSettingsKeepsItsOwn() {
  assertEquals(listOf("Home","Sports","Leagues","Recordings","Settings"),MAIN_NAV)
  assertEquals(1,MAIN_NAV.count{it=="Recordings"})
 }
 @Test fun backStepsFromHubRecordedGamesToOverviewThenHome() {
  assertNull(backDestination("Home",HUB_OVERVIEW,"Home"))
  assertEquals(NavPosition("Favorite",HUB_OVERVIEW),backDestination("Favorite",HUB_RECORDED,"Home"))
  listOf(HUB_SCHEDULE,HUB_ROSTER,HUB_NEWS).forEach{assertEquals(NavPosition("Favorite",HUB_OVERVIEW),backDestination("Favorite",it,"Home"))}   // every other hub tab steps back to Overview first
  assertEquals(NavPosition("Home",HUB_OVERVIEW),backDestination("Favorite",HUB_OVERVIEW,"Home"))
  assertEquals(NavPosition("Home",HUB_OVERVIEW),backDestination("Recordings",HUB_OVERVIEW,"Home"))   // a main tab, not a Settings child
  assertEquals(NavPosition("Home",HUB_OVERVIEW),backDestination("Settings",HUB_OVERVIEW,"Home"))
  assertEquals(NavPosition("Favorite",HUB_RECORDED),backDestination("Details",HUB_RECORDED,"Favorite"))   // Details returns to where it was opened
 }
}
