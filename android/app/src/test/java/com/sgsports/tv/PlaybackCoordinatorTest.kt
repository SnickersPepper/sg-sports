package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Phase 6: provider budget, release-before-open and split-screen rules. No network, no Android. */
class PlaybackCoordinatorTest {
 /** Records every call in order; opens complete only when the test says so, like a slow server. */
 private class FakeGateway:StreamGateway {
  val log=mutableListOf<String>()
  val pending=ArrayDeque<Triple<Int,Feed,(String?)->Unit>>()
  val activeSessions=mutableSetOf<String>()
  var counter=0
  override fun stop(session:String){log.add("stop:$session");activeSessions.remove(session)}
  override fun open(pane:Int,feed:Feed,result:(String?)->Unit){log.add("open:${feed.name}");pending.add(Triple(pane,feed,result))}
  fun finish(session:String?=null):String?{val (_,_,cb)=pending.removeFirst();val s=session?:"s${++counter}";activeSessions.add(s);cb(s);return s}
  fun fail(){val (_,_,cb)=pending.removeFirst();cb(null)}
  fun finishNext()=finish()
 }
 private fun feed(provider:String,id:String,extra:String="")=Feed("$provider-$id",provider,"English","",JSONObject("""{"provider_id":"$provider","stream_id":"$id","display_name":"$provider-$id"$extra}"""))
 private val dreamA=feed("dream","1");private val dreamB=feed("dream","2")
 private val magnumA=feed("magnum","1");private val magnumB=feed("magnum","2")
 private fun coordinator():Pair<PlaybackCoordinator,FakeGateway>{val g=FakeGateway();return PlaybackCoordinator(g) to g}

 @Test fun switchingSourceStopsPreviousSessionBeforeOpeningNext() {
  val (c,g)=coordinator()
  assertEquals(Decision.Accepted,c.open(0,dreamA));g.finish()
  assertEquals(Decision.Accepted,c.open(0,magnumA))
  assertEquals(listOf("open:dream-1","stop:s1","open:magnum-1"),g.log)
 }
 @Test fun switchingToAnotherStreamOfTheSameProviderStillReleasesFirst() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.open(0,dreamB)
  assertEquals(listOf("open:dream-1","stop:s1","open:dream-2"),g.log)
  assertTrue(c.withinBudget())
 }
 @Test fun rapidSwitchWaitsForTheStaleOpenToLandThenStopsIt() {
  val (c,g)=coordinator()
  c.open(0,dreamA)                       // still in flight on the server
  c.open(0,dreamB)                       // same provider: must not open until the first has been released
  assertEquals(listOf("open:dream-1"),g.log)
  assertEquals(1,c.inUse("dream"))
  g.finish("late")                       // stale open lands: it is stopped, then the queued source opens
  assertEquals(listOf("open:dream-1","stop:late","open:dream-2"),g.log)
  g.finish("fresh")
  assertEquals(PaneStatus.PLAYING,c.panes[0].status);assertEquals("fresh",c.panes[0].session)
  assertEquals(1,c.inUse("dream"))
 }
 @Test fun staleOpenOnAnotherProviderNeverOverlapsItsOwnProvider() {
  val (c,g)=coordinator()
  c.open(0,dreamA);c.open(0,magnumA)
  assertEquals(1,c.inUse("dream"));assertEquals(1,c.inUse("magnum"))
  assertTrue(c.withinBudget())
  g.finish("late-dream");assertEquals(0,c.inUse("dream"))
  assertTrue("stale session stopped",g.log.contains("stop:late-dream"))
 }

 @Test fun secondPaneOffersOnlyTheOtherProvider() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish()
  val choices=c.secondPaneChoices(listOf(dreamB,magnumA,magnumB))
  assertEquals(listOf(magnumA,magnumB),choices)
 }
 @Test fun sameProviderSecondPaneIsRejectedAndFirstKeepsPlaying() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish()
  val d=c.open(1,dreamB)
  assertTrue(d is Decision.Rejected);assertEquals(RejectReason.SAME_PROVIDER,(d as Decision.Rejected).reason)
  assertEquals(PaneStatus.PLAYING,c.panes[0].status)
  assertEquals(listOf("open:dream-1"),g.log)
 }
 @Test fun otherProviderUnavailableStaysSinglePaneWithExplanation() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish()
  val game2=listOf(dreamB,feed("magnum","9",""","availability":"probe_failed""""))
  assertTrue(c.secondPaneChoices(game2).isEmpty())
  val msg=c.constraintMessage(game2)!!
  assertTrue(msg.contains("first game keeps playing"))
  assertEquals(1,c.activeCount());assertEquals(PaneStatus.PLAYING,c.panes[0].status)
  assertNull(c.constraintMessage(listOf(magnumA)))
 }
 @Test fun failedSourceCannotBeOpened() {
  val (c,g)=coordinator()
  val d=c.open(0,feed("dream","x",""","availability":"probe_failed""""))
  assertEquals(RejectReason.NOT_PLAYABLE,(d as Decision.Rejected).reason);assertTrue(g.log.isEmpty())
 }
 @Test fun secondPaneNeedsAFirstAndNeverExceedsTwoPanes() {
  val (c,g)=coordinator()
  assertEquals(RejectReason.NEEDS_FIRST_PANE,(c.open(1,magnumA) as Decision.Rejected).reason)
  assertEquals(RejectReason.BAD_PANE,(c.open(2,magnumA) as Decision.Rejected).reason)
  c.open(0,dreamA);g.finish();c.open(1,magnumA);g.finish()
  assertEquals(2,c.activeCount());assertEquals(2,c.panes.size)
 }
 @Test fun audioSwitchDoesNotReopenOrStopAnyStream() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.open(1,magnumA);g.finish()
  val before=g.log.toList()
  assertTrue(c.setAudio(1));assertEquals(1,c.audioPane)
  assertTrue(c.setAudio(0));assertEquals(0,c.audioPane)
  assertEquals(before,g.log)
  assertEquals(setOf("s1","s2"),g.activeSessions)
  assertFalse("empty pane cannot take audio",PlaybackCoordinator(FakeGateway()).setAudio(1))
 }
 @Test fun providerFailureReleasesItsSlotAndRecoveryStaysWithinBudget() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.open(1,magnumA);g.finish()
  c.fail(1,"This feed could not play.")
  assertEquals(PaneStatus.ERROR,c.panes[1].status);assertEquals(0,c.inUse("magnum"));assertEquals(1,c.inUse("dream"))
  assertTrue(g.log.contains("stop:s2"))
  // recovery offers a different magnum source, never a dream one while pane 0 is on dream
  val options=c.recoveryChoices(1,listOf(dreamB,magnumA,magnumB))
  assertEquals(listOf(magnumB),options)
  assertEquals(Decision.Accepted,c.open(1,magnumB));g.finish()
  assertEquals(2,c.activeCount());assertTrue(c.withinBudget())
 }
 @Test fun singlePaneFailureLeavesRetryAndAnotherSource() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.fail(0,"x")
  assertEquals(0,c.activeCount());assertEquals(dreamA,c.panes[0].feed)
  assertEquals(listOf(dreamB,magnumA),c.recoveryChoices(0,listOf(dreamA,dreamB,magnumA)))
 }
 @Test fun failedOpenFreesTheSlot() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.fail()
  assertEquals(PaneStatus.ERROR,c.panes[0].status);assertEquals(0,c.inUse("dream"))
  assertEquals(Decision.Accepted,c.open(0,dreamB));assertEquals(1,c.inUse("dream"))
 }
 @Test fun refusedSwitchLeavesTheCurrentStreamPlaying() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.open(1,magnumA);g.finish()
  val d=c.open(0,magnumB)               // pane 0 may not take pane 1's provider
  assertTrue(d is Decision.Rejected)
  assertEquals(PaneStatus.PLAYING,c.panes[0].status);assertFalse(g.log.any{it=="stop:s1"})
 }
 @Test fun verificationAndDvrHoldsShareTheProviderBudget() {
  val (c,g)=coordinator()
  assertTrue(c.hold("dream","verify"))
  assertFalse("budget already used by verification",c.hold("dream","dvr"))
  assertEquals(RejectReason.PROVIDER_BUSY,(c.open(0,dreamA) as Decision.Rejected).reason)
  assertEquals(Decision.Accepted,c.open(0,magnumA));g.finish()
  assertTrue(c.secondPaneChoices(listOf(dreamA,dreamB)).isEmpty())
  c.releaseHold("dream","verify")
  assertEquals(listOf(dreamA,dreamB),c.secondPaneChoices(listOf(dreamA,dreamB)))
  assertTrue(c.hold("magnum","x").not())
 }
 @Test fun closingTheFirstPaneClosesTheSecondAndReleasesBoth() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.open(1,magnumA);g.finish()
  c.close(0)
  assertEquals(0,c.activeCount());assertEquals(setOf<String>(),g.activeSessions)
  assertEquals(0,c.audioPane)
 }
 @Test fun closingTheSecondPaneKeepsTheFirstAndFreesItsProvider() {
  val (c,g)=coordinator()
  c.open(0,dreamA);g.finish();c.open(1,magnumA);g.finish();c.setAudio(1)
  c.close(1)
  assertEquals(1,c.activeCount());assertEquals(0,c.audioPane);assertEquals(0,c.inUse("magnum"))
 }

 @Test fun adoptedSessionCountsAgainstTheProviderAndIsReleasedOnSwitch() {
  val (c,g)=coordinator()
  c.adopt(0,dreamA,"already-open")
  assertEquals(1,c.inUse("dream"));assertEquals(listOf(magnumA),c.secondPaneChoices(listOf(dreamB,magnumA)))
  c.open(0,magnumA)
  assertEquals(listOf("stop:already-open","open:magnum-1"),g.log)
 }

 /** Randomized operation sequences: the per-provider limit and pane limit hold after every step. */
 @Test fun budgetNeverExceededUnderRandomOperations() {
  val feeds=listOf(dreamA,dreamB,magnumA,magnumB)
  val rnd=java.util.Random(6)
  repeat(200) {
   val (c,g)=coordinator()
   repeat(60) {
    when(rnd.nextInt(7)) {
     0,1->c.open(rnd.nextInt(3),feeds[rnd.nextInt(4)])
     2->if(g.pending.isNotEmpty())g.finishNext()
     3->if(g.pending.isNotEmpty())g.fail()
     4->c.fail(rnd.nextInt(2),"err")
     5->c.close(rnd.nextInt(2))
     else->c.setAudio(rnd.nextInt(2))
    }
    assertTrue(c.withinBudget())
    assertTrue(c.activeCount()<=2)
    assertTrue(feeds.map{it.provider}.toSet().all{p->c.inUse(p)<=1})
    // every server session still open belongs to a pane that is playing
    assertTrue(g.activeSessions.all{s->c.panes.any{it.session==s}})
   }
  }
 }
}
