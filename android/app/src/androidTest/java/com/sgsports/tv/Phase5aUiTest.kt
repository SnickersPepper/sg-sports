package com.sgsports.tv

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Phase 5A: LVBP appears under Baseball with plain-language evidence and freshness (Design flavor samples). */
class Phase5aUiTest {
 @get:Rule val ui=createAndroidComposeRule<DesignActivity>()

 private fun openBaseball() {
  assertTrue(BuildConfig.DESIGN_MODE)
  ui.onNodeWithTag("nav-Sports").performClick()
  ui.onAllNodesWithText("Baseball").onFirst().performClick()
 }

 @Test fun scheduleGamesShowCoverageLabelsOnTheirCards() {
  openBaseball()
  for((id,label) in listOf("confirmed" to "Confirmed feed","possible" to "Possible feed","none" to "No verified feed")) {
   ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag("game-sample-lvbp-$id"))
   ui.onNodeWithTag("lvbp-card-label-sample-lvbp-$id",useUnmergedTree=true).assertTextEquals(label)
  }
 }

 @Test fun detailsShowEvidenceAndFreshnessWithoutTelemetry() {
  openBaseball()
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag("game-sample-lvbp-confirmed"))
  ui.onNodeWithTag("game-sample-lvbp-confirmed").performClick()
  ui.onNodeWithTag("lvbp-coverage-label",useUnmergedTree=true).assertTextEquals("Confirmed feed")
  ui.onNodeWithTag("lvbp-freshness",useUnmergedTree=true).assertTextContains("Schedule checked",substring=true)
  ui.onNodeWithText("fps",substring=true).assertDoesNotExist()
  ui.onNodeWithText("Mbps",substring=true).assertDoesNotExist()
 }

 @Test fun gameWithoutAChannelStaysVisibleWithNoPlayablePromise() {
  openBaseball()
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag("game-sample-lvbp-none"))
  ui.onNodeWithTag("game-sample-lvbp-none").performClick()
  ui.onNodeWithTag("lvbp-coverage-label",useUnmergedTree=true).assertTextEquals("No verified feed")
  ui.onNodeWithText("Watch  ›").assertDoesNotExist()
 }
}
