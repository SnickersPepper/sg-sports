package com.sgsports.tv

import android.Manifest
import android.content.pm.PackageManager
import android.view.KeyEvent
import android.widget.Button
import androidx.compose.ui.test.*
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

@androidx.media3.common.util.UnstableApi
class PlaybackFocusTest {
 @get:Rule val ui=createAndroidComposeRule<DesignActivity>()
 private val instrumentation get()=InstrumentationRegistry.getInstrumentation()
 private fun key(code:Int){instrumentation.sendKeyDownUpSync(code);instrumentation.waitForIdleSync()}
 private fun player():PlayerActivity? {
  var found:PlayerActivity?=null
  instrumentation.runOnMainSync {
   found=ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).filterIsInstance<PlayerActivity>().firstOrNull()
  }
  return found
 }
 private fun waitPlayer(expected:Boolean){
  val deadline=System.currentTimeMillis()+8000
  while((player()!=null)!=expected&&System.currentTimeMillis()<deadline)Thread.sleep(50)
  assertEquals("Expected actual player Activity resumed=$expected",expected,player()!=null)
 }
 private fun waitFocused(tag:String){
  ui.waitUntil(2500){runCatching{ui.onNodeWithTag(tag).assertIsFocused();true}.getOrDefault(false)}
 }
 @Test fun stopAndBackRestoreFeedAndRemoteNavigation() {
  assertTrue(BuildConfig.DESIGN_MODE)
  assertEquals(PackageManager.PERMISSION_DENIED,ui.activity.checkSelfPermission(Manifest.permission.INTERNET))
  // Enter via a non-Home route, then activate an actual feed using the remote.
  ui.onNodeWithTag("nav-Sports").performClick()
  ui.onNodeWithText("Tennessee vs Oklahoma").performClick()
  repeat(2){ exit ->
   val feed=ui.onNodeWithTag("feed-ProviderA-sample-1")
   feed.performScrollTo()
   feed.performSemanticsAction(SemanticsActions.RequestFocus){it()}
   waitFocused("feed-ProviderA-sample-1")
   val before=feed.fetchSemanticsNode().id
   key(KeyEvent.KEYCODE_DPAD_CENTER)
   waitPlayer(true)
   key(KeyEvent.KEYCODE_DPAD_CENTER) // Show player controls, focuses Play/Pause.
   if(exit==0){
    key(KeyEvent.KEYCODE_DPAD_LEFT)
    var label=""
    instrumentation.runOnMainSync{label=(playerViewFocus() as? Button)?.text?.toString()?:""}
    assertTrue("D-pad reaches Stop & games: $label",label.contains("Stop & games"))
    key(KeyEvent.KEYCODE_DPAD_CENTER)
   }else{
    key(KeyEvent.KEYCODE_BACK)
    assertNotNull("First Back hides controls without exiting",player())
    key(KeyEvent.KEYCODE_BACK)
   }
   waitPlayer(false)
   ui.waitUntil(8000){ui.onAllNodesWithTag("feed-ProviderA-sample-1").fetchSemanticsNodes().isNotEmpty()}
   waitFocused("feed-ProviderA-sample-1")
   assertEquals("Same browsing card survives Activity return",before,ui.onNodeWithTag("feed-ProviderA-sample-1").fetchSemanticsNode().id)
   key(KeyEvent.KEYCODE_DPAD_DOWN)
   ui.onNodeWithTag("feed-ProviderB-sample-2").assertIsFocused()
   key(KeyEvent.KEYCODE_DPAD_UP)
   ui.onNodeWithTag("feed-ProviderA-sample-1").assertIsFocused()
  }
 }
 @Test fun foregroundResumeRestoresFeedAndRemoteNavigation() {
  ui.onNodeWithTag("nav-Sports").performClick()
  ui.onNodeWithText("Tennessee vs Oklahoma").performClick()
  ui.onNodeWithTag("feed-ProviderA-sample-1").performScrollTo().performSemanticsAction(SemanticsActions.RequestFocus){it()}
  ui.activityRule.scenario.moveToState(androidx.lifecycle.Lifecycle.State.CREATED)
  ui.activityRule.scenario.moveToState(androidx.lifecycle.Lifecycle.State.RESUMED)
  ui.waitForIdle()
  waitFocused("feed-ProviderA-sample-1")
  key(KeyEvent.KEYCODE_DPAD_DOWN)
  ui.onNodeWithTag("feed-ProviderB-sample-2").assertIsFocused()
 }
 // Called on main thread; avoid nesting runOnMainSync from player().
 private fun playerViewFocus()=ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).filterIsInstance<PlayerActivity>().firstOrNull()?.currentFocus
}
