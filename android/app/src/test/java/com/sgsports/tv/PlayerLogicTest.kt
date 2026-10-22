package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Phase 6: D-pad, overlay/Back, honest playback facts, screen states and focus restoration. */
class PlayerLogicTest {
 @org.junit.Before fun english() = java.util.Locale.setDefault(java.util.Locale.US)
 // ---- overlays, Back, D-pad
 @Test fun hiddenControlsSwallowTheFirstPressAndReveal() {
  val c=PlayerChrome()
  assertEquals(KeyResult.REVEAL_CONTROLS,c.key(Dpad.RIGHT));assertTrue(c.controls)
  assertEquals(KeyResult.MOVE_FOCUS,c.key(Dpad.RIGHT))
  assertEquals(KeyResult.PASS,c.key(Dpad.SELECT))
 }
 @Test fun selectWithControlsHiddenRevealsInsteadOfActing() {
  val c=PlayerChrome()
  assertEquals(KeyResult.REVEAL_CONTROLS,c.key(Dpad.SELECT))
 }
 @Test fun backClosesPickerThenControlsThenExits() {
  val c=PlayerChrome();c.controls=true;c.picker=true
  assertEquals(BackResult.CLOSE_PICKER,c.back());assertTrue("controls stay after the picker closes",c.controls)
  assertEquals(BackResult.HIDE_CONTROLS,c.back());assertFalse(c.controls)
  assertEquals(BackResult.EXIT,c.back())
 }
 @Test fun backFromAnErrorPanelExitsImmediately() {
  val c=PlayerChrome();c.controls=true;c.errorShown=true
  assertEquals(BackResult.EXIT,c.back())
 }
 @Test fun pickerKeysMoveFocusEvenWithControlsHidden() {
  val c=PlayerChrome();c.picker=true
  assertEquals(KeyResult.MOVE_FOCUS,c.key(Dpad.DOWN))
 }
 @Test fun focusMovesWithoutWrapping() {
  assertEquals(1,moveFocus(0,4,Dpad.RIGHT));assertEquals(0,moveFocus(0,4,Dpad.LEFT))
  assertEquals(3,moveFocus(3,4,Dpad.RIGHT));assertEquals(2,moveFocus(3,4,Dpad.LEFT))
  assertEquals("vertical keys do not leave a horizontal row",2,moveFocus(2,4,Dpad.UP))
  assertEquals(1,moveFocus(0,3,Dpad.DOWN,vertical=true));assertEquals(0,moveFocus(0,3,Dpad.UP,vertical=true))
  assertEquals(2,moveFocus(2,3,Dpad.DOWN,vertical=true));assertEquals(0,moveFocus(0,0,Dpad.RIGHT))
 }

 // ---- honest facts
 @Test fun qualityIsOnlyClaimedWhenThePlayerReportedIt() {
  assertNull(qualityLabel(null));assertNull(qualityLabel(0))
  assertEquals("Full HD",qualityLabel(1080));assertEquals("HD",qualityLabel(720));assertEquals("Standard definition",qualityLabel(480))
 }
 @Test fun factsNameEveryGapInsteadOfGuessing() {
  assertEquals("Quality and audio not reported yet",playbackFacts(null,null,false))
  assertEquals("Quality and audio not reported yet",playbackFacts(720,"en",false))
  assertEquals("HD · English audio",playbackFacts(720,"en",true))
  assertEquals("Quality not reported · Spanish audio",playbackFacts(null,"es",true))
  assertEquals("HD · Audio language not tagged",playbackFacts(720,null,true))
  assertNull(audioLanguageLabel("und"));assertNull(audioLanguageLabel(" "))
 }
 private fun live(extra:String="")=JSONObject("""{"id":"g","league":"NCAA","sport":"Football","event_phase":"live","live":{"home":"A","away":"B","home_score":"17","away_score":"14","state":"in","detail":"3rd","period":3,"display_clock":"8:42","verified_live":true,"checked_at":1000$extra}}""")
 private fun gameOf(e:JSONObject)=Game("g","B at A","NCAA","Football","",raw=e,live=liveScore(e),eventPhase=e.optString("event_phase"))
 @Test fun liveContextUsesOnlyBackendFacts() {
  assertEquals("LIVE · 14-17 · Q3 · 8:42 · Updated 10s ago",liveContext(gameOf(live()),1010))
  assertEquals("freshness is recomputed, never frozen","LIVE · 14-17 · Q3 · 8:42 · Last update 5 min ago",liveContext(gameOf(live()),1300))
  assertNull(liveContext(null))
  val noClock=JSONObject("""{"id":"g","event_phase":"live","live":{"home":"A","away":"B","home_score":"1","away_score":"0","state":"in"}}""")
  assertEquals("LIVE · 0-1 · Clock unavailable",liveContext(gameOf(noClock),1010))
  val pre=JSONObject("""{"id":"g","event_phase":"upcoming","live":{"home":"A","away":"B","home_score":"0","away_score":"0","state":"pre"}}""")
  assertEquals("a pregame score is not shown","UPCOMING",liveContext(gameOf(pre),1))
 }

 // ---- screen states
 private val now=10_000L
 @Test fun loadingOnlyWhenThereIsNothingToShow() {
  assertEquals(BannerKind.LOADING,stateBanner(SgState(loading=true),false,now)!!.kind)
  assertNull(stateBanner(SgState(loading=true,fresh=true),true,now))
 }
 @Test fun offlineWithAndWithoutLastKnownContent() {
  val s=SgState(offline=true,error="Cannot reach the SG Sports backend.",loadedAt=now-600)
  val withContent=stateBanner(s,true,now)!!
  assertEquals(BannerKind.OFFLINE,withContent.kind);assertTrue(withContent.detail.contains("10 min ago"))
  val empty=stateBanner(s,false,now)!!
  assertEquals("Check this TV's home network connection.",empty.detail);assertEquals("Try again",empty.action)
 }
 @Test fun serverErrorIsRecoverable() {
  val b=stateBanner(SgState(error="Server unavailable"),false,now)!!
  assertEquals(BannerKind.ERROR,b.kind);assertEquals("Server unavailable",b.detail);assertEquals("Try again",b.action)
 }
 @Test fun staleOnlyOnceReceivedDataIsFiveMinutesOld() {
  assertNull("fresh=false alone with recent data",stateBanner(SgState(fresh=false,loadedAt=now-30),true,now))
  assertNull("normal fresh data",stateBanner(SgState(fresh=true,loadedAt=now-30),true,now))
  assertNull("one second under the boundary",stateBanner(SgState(fresh=false,loadedAt=now-GUIDE_STALE_SECONDS+1),true,now))
  val edge=stateBanner(SgState(fresh=true,loadedAt=now-GUIDE_STALE_SECONDS),true,now)!!
  assertEquals("exactly five minutes shows",BannerKind.STALE,edge.kind)
  val old=stateBanner(SgState(fresh=false,loadedAt=now-GUIDE_STALE_SECONDS-60),true,now)!!
  assertEquals(BannerKind.STALE,old.kind);assertEquals("Guide may be out of date",old.title);assertEquals("Refresh",old.action)
  assertTrue(old.detail.startsWith("Last updated 6 min ago"))
  assertFalse("must not read as an in-progress operation",old.title.contains("updating",ignoreCase=true))
  assertNull("no received-data time means no staleness claim",stateBanner(SgState(fresh=false),true,now))
  assertNull("an empty page is the page's own empty state, not a stale banner",stateBanner(SgState(fresh=false,loadedAt=now-900),false,now))
 }

 // ---- focus restoration
 private val order=listOf("game:a","game:b","game:c","game:d")
 @Test fun exactItemWinsWhenStillPresentEvenIfReordered() {
  assertEquals("game:c",restoreTarget("game:c",order,listOf("game:c","game:a","game:b")))
 }
 @Test fun nearestSurvivorWhenTheItemIsGone() {
  assertEquals("game:c",restoreTarget("game:b",order,listOf("game:a","game:c","game:d")))
  assertEquals("game:a",restoreTarget("game:b",order,listOf("game:a")))
  assertEquals("prefers the following neighbour on a tie","game:d",restoreTarget("game:c",listOf("game:b","game:c","game:d"),listOf("game:b","game:d")))
 }
 @Test fun firstItemWhenNothingKnownAndNullWhenEmpty() {
  assertEquals("x:1",restoreTarget(null,emptyList(),listOf("x:1","x:2")))
  assertEquals("x:1",restoreTarget("gone",emptyList(),listOf("x:1","x:2")))
  assertNull(restoreTarget("game:a",order,emptyList()))
 }

 // ---- SGH-287: page-owned failures never raise the global banner
 @Test fun anOlderBackendWithoutRecordingsDoesNotRaiseTheGlobalErrorBanner() {
  assertTrue(isPageScopedFailure("/tv/team",502));assertFalse(isPageScopedFailure("/tv/team",200))   // the team hub owns its own failure state
  assertTrue(isPageScopedFailure("/tv/recordings",404))
  assertTrue(isPageScopedFailure("/tv/recordings",500))
  assertFalse(isPageScopedFailure("/tv/recordings",200))
  // Everything else still reports: a refused home, guide or pairing call is the page's real problem.
  listOf("/tv/home","/tv/sports","/tv/pair","/tv/feeds").forEach{assertFalse(it,isPageScopedFailure(it,404))}
 }
 @Test fun scopedRecordingsFailureLeavesNoGlobalBannerButRecordingsSaysUnavailable() {
  val s=SgState(paired=true,recordings=null)
  assertNull(stateBanner(s,hasContent=true,now=0))
  assertEquals(RecordingsScreenKind.UNAVAILABLE,recordingsScreen(s.recordings,s.offline).kind)
 }
}
