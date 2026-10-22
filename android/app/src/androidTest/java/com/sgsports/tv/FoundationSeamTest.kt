package com.sgsports.tv

import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Phase 2 foundation seam: navigation contract, focus identity/restoration, honest recovery states. */
class FoundationSeamTest {
 @get:Rule val ui=createAndroidComposeRule<ComponentActivity>()
 private val instrumentation get()=InstrumentationRegistry.getInstrumentation()
 private fun key(code:Int){instrumentation.sendKeyDownUpSync(code);instrumentation.waitForIdleSync()}
 private fun focus(tag:String){ui.onAllNodesWithTag(tag)[0].performSemanticsAction(SemanticsActions.RequestFocus){it()}}
 private fun isFocused(tag:String)=runCatching{ui.onAllNodesWithTag(tag)[0].assertIsFocused();true}.getOrDefault(false)
 private fun waitFocused(tag:String)=ui.waitUntil(3000){isFocused(tag)}
 private fun back()=ui.runOnUiThread{ui.activity.onBackPressedDispatcher.onBackPressed()}
 private var state by mutableStateOf(SgState())

 private fun launch(initial:SgState,page:String="Home"){
  assertTrue(BuildConfig.DESIGN_MODE)
  state=initial
  ui.setContent{SgApp(state,initialPage=page)}
 }
 private fun sample()=DesignFixtures.state(ui.activity)!!

 @Test fun railHasExactlyFiveDestinations() {
  launch(sample())
  ui.onAllNodes(SemanticsMatcher("rail destination"){it.config.getOrNull(SemanticsProperties.TestTag)?.startsWith("nav-")==true}).assertCountEquals(5)
  listOf("Home","Sports","Leagues","Recordings","Settings").forEach{ui.onNodeWithTag("nav-$it").assertExists()}
 }

 @Test fun recordingsRailDestinationSelectsAndBackReturnsHome() {
  launch(sample())
  focus("nav-Recordings");waitFocused("nav-Recordings")
  key(KeyEvent.KEYCODE_DPAD_CENTER) // remote select, not a pointer click
  ui.waitForIdle()
  ui.onNodeWithTag("nav-Recordings").assertIsSelected()
  ui.onNodeWithTag("nav-Home").assertIsNotSelected()
  back()
  ui.waitForIdle()
  ui.onNodeWithTag("nav-Home").assertIsSelected()
  ui.onNodeWithTag("nav-Recordings").assertIsNotSelected()
  // Focus must be restored to a real Home item: never lost to the window, never left on the vacated Recordings entry.
  ui.waitUntil(3000){ui.onAllNodes(isFocused()).fetchSemanticsNodes().isNotEmpty()}
  assertTrue(!isFocused("nav-Recordings"))
 }

 @Test fun teamHubRecordedGamesTabBackReturnsOverviewThenHome() {
  launch(sample())
  ui.onNodeWithTag("my-teams-row").performScrollToNode(hasTestTag("team-team.softball.women.ncaa.tennessee-volunteers"))
  ui.onAllNodesWithTag("team-team.softball.women.ncaa.tennessee-volunteers")[0].performClick()
  ui.waitUntil(3000){ui.onAllNodesWithTag("hub-tab-Recorded Games").fetchSemanticsNodes().isNotEmpty()}
  ui.onNodeWithTag("hub-tab-Overview").assertIsSelected()
  ui.onNodeWithTag("hub-tab-Recorded Games").performClick()
  ui.waitForIdle()
  ui.onNodeWithTag("hub-tab-Recorded Games").assertIsSelected()
  back()
  ui.waitForIdle()
  ui.onNodeWithTag("hub-tab-Overview").assertIsSelected()
  back()
  ui.waitForIdle()
  ui.onNodeWithTag("nav-Home").assertIsSelected()
 }

 @Test fun detailsReturnRestoresOriginatingCardAfterReorder() {
  launch(sample())
  val tag="game-next-softball"
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag(tag))
  focus(tag);waitFocused(tag)
  ui.onAllNodesWithTag(tag)[0].performClick()
  ui.waitUntil(3000){ui.onAllNodesWithTag(tag).fetchSemanticsNodes().isEmpty()}
  // The list reorders while Details is open; focus must follow identity, not position.
  ui.runOnUiThread{state=state.copy(upcoming=state.upcoming.reversed())}
  back()
  ui.waitForIdle()
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag(tag))
  waitFocused(tag)
 }

 @Test fun rowsRememberTheirOwnFocus() {
  launch(sample())
  ui.onNodeWithTag("my-teams-row").performScrollToNode(hasTestTag("team-team.softball.women.ncaa.tennessee-volunteers"))
  focus("team-team.softball.women.ncaa.tennessee-volunteers");waitFocused("team-team.softball.women.ncaa.tennessee-volunteers")
  val below="game-next-softball"
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag(below))
  focus(below);waitFocused(below)
  repeat(4){if(!isFocused("team-team.softball.women.ncaa.tennessee-volunteers"))key(KeyEvent.KEYCODE_DPAD_UP)}
  waitFocused("team-team.softball.women.ncaa.tennessee-volunteers")
 }

 @Test fun emptyStateHasOneFocusableRecoveryAction() {
  launch(SgState(demo=true),page="Sports")
  ui.onAllNodesWithText("Refresh ›").assertCountEquals(1)
  ui.onNodeWithText("Refresh ›").assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Focused)).assertHasClickAction()
 }

 @Test fun errorStateHasOneFocusableRecoveryAction() {
  launch(SgState(demo=true,error="Sample failure",fresh=true),page="Settings")
  ui.onAllNodesWithText("Refresh ›").assertCountEquals(1)
  ui.onNodeWithText("Refresh ›").assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Focused)).assertHasClickAction()
 }

 @Test fun disabledSourceExplainsWhyAndTrustLabelsAreExplicit() {
  launch(sample())
  ui.onNodeWithTag("nav-Sports").performClick()
  ui.onNodeWithText("Tennessee vs Oklahoma").performClick()
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag("feed-reason-ProviderA-sample-3"))
  ui.onNodeWithTag("feed-reason-ProviderA-sample-3").assertTextEquals("Could not open just now")
  ui.onNodeWithTag("feed-label-ProviderA-sample-1",useUnmergedTree=true).assertTextEquals("Best match")
  ui.onNodeWithTag("feed-label-ProviderB-sample-2",useUnmergedTree=true).assertTextEquals("Available")
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag("feed-label-ProviderB-"))
  ui.onNodeWithTag("feed-label-ProviderB-",useUnmergedTree=true).assertTextEquals("Unverified listing")
 }
}
