package com.sgsports.tv

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class Phase3UiTest {
 @get:Rule val ui=createAndroidComposeRule<DesignActivity>()

 @Test fun homeUsesLiveClockAndEveryFavoriteBanner() {
  assertTrue(BuildConfig.DESIGN_MODE)
  ui.onNodeWithTag("nav-Home").performClick()
  ui.onNodeWithTag("hero-away-name",useUnmergedTree=true).assertTextEquals("OKLAHOMA")
  ui.onNodeWithTag("hero-home-name",useUnmergedTree=true).assertTextEquals("TENNESSEE")
  ui.onNodeWithTag("hero-clock",useUnmergedTree=true).assertTextEquals("Q3 · 8:42")
  for(id in listOf("team.soccer.men.la-liga.fc-barcelona","team.football.men.ncaa.tennessee-volunteers","team.softball.women.ncaa.tennessee-volunteers","team.baseball.men.mlb.st-louis-cardinals","team.football.men.nfl.kansas-city-chiefs","team.baseball.men.mlb.houston-astros","team.football.men.ncaa.missouri-tigers")) {
   val tag="team-$id"
   ui.onNodeWithTag("my-teams-row").performScrollToNode(hasTestTag(tag))
   ui.onNodeWithTag(tag).assertExists()
  }
 }

 /** The Design fixtures put a synthetic matchup with bundled event artwork at carousel entry 12; its CTA must keep its label. */
@OptIn(ExperimentalTestApi::class)
 @Test fun eventArtworkHeroKeepsCtaLabelAndSameLayout() {
  ui.onNodeWithTag("nav-Home").performClick()
  repeat(11){ui.onRoot().performKeyInput{pressKey(androidx.compose.ui.input.key.Key.DirectionRight)};ui.waitForIdle()}
  ui.onNodeWithTag("hero-art-personalized",useUnmergedTree=true).assertExists()
  ui.onNodeWithTag("hero-art-matchup",useUnmergedTree=true).assertExists()
  ui.onNodeWithTag("hero-cta-label",useUnmergedTree=true).assertTextEquals("Watch Live")
  ui.onNodeWithTag("hero-away-name",useUnmergedTree=true).assertTextEquals("SAMPLE ROCKETS")
 }

 @Test fun homeHasNoGreetingDateOrTaglineBar() {
  ui.onNodeWithTag("nav-Home").performClick()
  ui.onNodeWithText("All your teams. All their games. One place.").assertDoesNotExist()
  ui.onNodeWithText("Good evening").assertDoesNotExist();ui.onNodeWithText("Good morning").assertDoesNotExist();ui.onNodeWithText("Good afternoon").assertDoesNotExist()
  ui.onNodeWithTag("hero-away-name",useUnmergedTree=true).assertExists()
 }

 @Test fun feedEvidenceAndSettingsUseFinishedInformationArchitecture() {
  ui.onNodeWithTag("nav-Sports").performClick()
  ui.onNodeWithText("Tennessee vs Oklahoma").performClick()
  ui.onNodeWithTag("main-content").performScrollToNode(hasText("Verified choices"))
  ui.onNodeWithText("Verified choices").assertExists()
  ui.onNodeWithTag("main-content").performScrollToNode(hasText("Healthy streams"))
  ui.onNodeWithText("Healthy streams").assertExists()
  ui.onNodeWithText("1080p · 30 fps · 5.1 Mbps").assertDoesNotExist()
  ui.onNodeWithTag("main-content").performScrollToNode(hasText("Scheduled broadcasts"))
  ui.onNodeWithText("Scheduled broadcasts").assertExists()
  ui.onNodeWithTag("feed-ProviderA-sample-1").assertExists()
  ui.onNodeWithTag("main-content").performScrollToNode(hasTestTag("feed-ProviderB-sample-2"))
  ui.onNodeWithTag("feed-ProviderB-sample-2").assertExists()
  ui.onNodeWithTag("main-content").performScrollToNode(hasText("Unavailable"))
  ui.onNodeWithText("Unavailable").assertExists()
  ui.onNodeWithTag("nav-Settings").performClick()
  listOf("language","refresh").forEach {
   ui.onNodeWithTag("setting-$it").assertExists()
  }
  // Pairing has exactly one home, Settings > Connection.
  listOf("pair","forget").forEach { ui.onNodeWithTag("setting-$it").assertDoesNotExist() }
  ui.onNodeWithTag("setting-connection").performClick()
  ui.onNodeWithTag("conn-pair").assertExists()
  ui.onNodeWithTag("conn-forget").assertExists()
 }
}
