package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Fake clock and fake port only: no network, no provider URL anywhere in these tests. */
class StartupVerificationTest {
 private val installed by lazy{parseTeams(JSONObject(TestAssets.file("favorites.json").readText()))}
 private val vols by lazy{installed.first{it.id=="team.football.men.ncaa.tennessee-volunteers"}}
 @org.junit.Before fun needsRoster(){TestAssets.assumePrivate()}
 private val now=1_000_000L
 private fun game(id:String,sides:List<String>,phase:String,start:Long,feeds:List<String> = listOf("p:1"),sport:String="Football"):Game{
  val raw=JSONObject().put("start",start).put("sides",JSONArray(sides)).put("feeds",JSONArray(feeds.map{val (p,s)=it.split(":");JSONObject().put("provider_id",p).put("stream_id",s)}))
  return Game(id,"t$id","NCAA",sport,"",raw=raw,eventPhase=phase)
 }
 private val volsLive=game("live",listOf("tennessee","alabama"),"live",now-600)
 private class FakePort:CheckPort{
  val started=mutableListOf<String>();val readiness=mutableListOf<Boolean>();val pending=ArrayDeque<(CheckOutcome)->Unit>();var cancelled=0
  override fun check(game:Game,readiness:Boolean,done:(CheckOutcome)->Unit){started.add(game.id);this.readiness.add(readiness);pending.addLast(done)}
  override fun cancel(){cancelled++}
  fun finish(o:CheckOutcome=CheckOutcome.CHECKED){pending.removeFirst()(o)}
 }
 private var run=true
 private val progress=mutableListOf<StartupProgress>()
 private fun sut(port:FakePort)=StartupVerification({now},port,{run},{progress.add(it)})

 @Test fun liveFavoritesGoFirstThenNearStartAndRunSerially(){
  val port=FakePort();val s=sut(port)
  val near=game("near",listOf("tennessee","georgia"),"pregame",now+900)
  val live2=game("live2",listOf("florida","tennessee"),"live",now-3600)
  s.offer(listOf(near,volsLive,live2),listOf(vols))
  assertEquals(listOf("live2"),port.started)   // earliest-started live game; one at a time
  port.finish();assertEquals(listOf("live2","live"),port.started)
  port.finish();assertEquals(listOf("live2","live","near"),port.started)
  port.finish();assertFalse(progress.last().active);assertEquals(3,progress.last().finished)
 }

 @Test fun offWindowAndNonFavoriteAndOtherSportNeverTakeASlot(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(game("far",listOf("tennessee","georgia"),"upcoming",now+7200),
   game("past",listOf("tennessee","georgia"),"final",now-9000),
   game("other",listOf("ohio state","michigan"),"live",now-60),
   game("soft",listOf("tennessee","alabama"),"live",now-60,sport="Softball")),listOf(vols))
  assertTrue(port.started.isEmpty());assertEquals(0,progress.lastOrNull()?.total?:0)
 }

 @Test fun emptyFavoritesProfileChecksNothing(){
  val port=FakePort();sut(port).offer(listOf(volsLive),emptyList())
  assertTrue(port.started.isEmpty())
 }

 @Test fun repeatedOffersResumeAndRecompositionAreDeduplicated(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(volsLive),listOf(vols));s.offer(listOf(volsLive),listOf(vols));s.resume()
  assertEquals(1,port.started.size)            // already in flight
  port.finish()
  s.offer(listOf(volsLive),listOf(vols));s.resume()
  assertEquals(1,port.started.size)            // already settled for this generation
  // A changed feed set is a new source generation and is checked again.
  s.offer(listOf(game("live",listOf("tennessee","alabama"),"live",now-600,feeds=listOf("p:1","p:2"))),listOf(vols))
  assertEquals(2,port.started.size)
 }

 @Test fun goingLiveAfterPregameIsNewWork(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(game("g",listOf("tennessee","georgia"),"pregame",now+600)),listOf(vols))
  port.finish(CheckOutcome.NOT_STARTED)
  s.offer(listOf(game("g",listOf("tennessee","georgia"),"live",now-1)),listOf(vols))
  assertEquals(listOf("g","g"),port.started)
 }

 @Test fun leavingTheScreenCancelsInFlightAndDropsTheQueue(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(volsLive,game("b",listOf("tennessee","florida"),"live",now-30)),listOf(vols))
  s.cancel()
  assertEquals(1,port.cancelled)
  port.finish(CheckOutcome.CANCELLED)          // a late answer from the abandoned check changes nothing
  assertEquals(1,port.started.size);assertEquals(0,progress.last().total)
  // Coming back re-offers; the abandoned game was never settled so it is checked.
  s.offer(listOf(volsLive),listOf(vols));assertEquals(2,port.started.size)
 }

 @Test fun noCheckStartsWhilePlaybackOrAManualCheckHoldsTheScreen(){
  val port=FakePort();val s=sut(port);run=false
  s.offer(listOf(volsLive),listOf(vols))
  assertTrue(port.started.isEmpty())
  run=true;s.resume();assertEquals(1,port.started.size)
 }

 @Test fun capacityDenialFailsClosedWithoutRetryOrFurtherChecks(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(volsLive,game("b",listOf("tennessee","florida"),"live",now-30)),listOf(vols))
  port.finish(CheckOutcome.DENIED)
  assertEquals(1,port.started.size);assertTrue(progress.last().stopped);assertFalse(progress.last().active)
  s.resume();s.offer(listOf(volsLive),listOf(vols));assertEquals(1,port.started.size)
 }

 @Test fun nearStartIsAReadinessRequestAndLiveIsAFullCheck(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(game("near",listOf("tennessee","georgia"),"pregame",now+900),volsLive),listOf(vols))
  port.finish()
  assertEquals(listOf("live","near"),port.started);assertEquals(listOf(false,true),port.readiness)
 }

 @Test fun anOlderBackendAnsweringNotStartedForReadinessIsShownAsLimitedNotAsAnError(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(game("near",listOf("tennessee","georgia"),"pregame",now+900)),listOf(vols))
  port.finish(CheckOutcome.NOT_STARTED)
  assertTrue(progress.last().limited);assertFalse(progress.last().stopped);assertFalse(progress.last().active)
  // Still not repeated for the same generation.
  s.offer(listOf(game("near",listOf("tennessee","georgia"),"pregame",now+900)),listOf(vols))
  assertEquals(1,port.started.size)
 }

 @Test fun readinessDenialStopsTheQueueWithoutRetry(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(game("n1",listOf("tennessee","georgia"),"pregame",now+900),game("n2",listOf("tennessee","florida"),"pregame",now+1200)),listOf(vols))
  port.finish(CheckOutcome.DENIED)
  assertEquals(1,port.started.size);assertTrue(progress.last().stopped)
 }

 @Test fun aGameOutsideTheTuneInWindowIsNeverOffered(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(game("far",listOf("tennessee","georgia"),"upcoming",now+NEAR_START_SECONDS+60)),listOf(vols))
  assertTrue(port.started.isEmpty())
 }

 @Test fun aDuplicateCompletionCallbackIsIgnored(){
  val port=FakePort();val s=sut(port)
  s.offer(listOf(volsLive,game("b",listOf("tennessee","florida"),"live",now-30)),listOf(vols))
  val done=port.pending.first();port.finish();done(CheckOutcome.CHECKED)
  assertEquals(2,port.started.size)
 }
}
