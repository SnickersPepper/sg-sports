package com.sgsports.tv

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** One large, focusable action. Focus is a thick bright ring plus a lighter fill, readable from across a room. */
@Composable private fun ObAction(tag:String,title:String,detail:String,requester:FocusRequester?,primary:Boolean,enabled:Boolean=true,onClick:()->Unit){
 var focused by remember{mutableStateOf(false)}
 val ring=if(primary)SgTheme.Green else SgTheme.Gold
 Column(Modifier.fillMaxWidth().testTag(tag)
  .then(if(requester!=null)Modifier.focusRequester(requester) else Modifier)
  .onFocusChanged{focused=it.isFocused}
  .clip(RoundedCornerShape(14.dp))
  .background(if(focused)SgTheme.Surface2 else SgTheme.Surface)
  .border(if(focused)4.dp else 1.dp,if(focused)ring else SgTheme.StrokeRest,RoundedCornerShape(14.dp))
  .clickable(enabled=enabled,onClick=onClick)
  .padding(horizontal=22.dp,vertical=16.dp),verticalArrangement=Arrangement.spacedBy(4.dp)){
  Label(title,26,if(enabled)SgTheme.Text else SgTheme.Muted,bold=true,lines=1)
  if(detail.isNotEmpty())Label(detail,18,SgTheme.Muted,lines=2)
 }
}

/**
 * The first-run flow, full screen. D-pad moves between the large actions, OK activates, Back goes one step towards the address.
 * Text is typed through the system dialogs the activity opens (address, six-digit code); the administrator password is never entered on the TV.
 */
@Composable fun OnboardingScreen(ui:OnboardingUi,onEnterAddress:()->Unit,onRetry:()->Unit,onEnterCode:()->Unit,onBack:()->Unit,onFinish:()->Unit,onChoosePreferences:()->Unit){
 val first=remember(ui.step,ui.busy,ui.error){FocusRequester()}
 LaunchedEffect(ui.step,ui.busy,ui.error){if(!ui.busy){delay(200);runCatching{first.requestFocus()}}}
 BackHandler(ui.step==OnboardingStep.PAIR||ui.busy){onBack()}
 val stepNumber=when(ui.step){OnboardingStep.ADDRESS->1;OnboardingStep.TESTING->1;OnboardingStep.PAIR->2;OnboardingStep.DONE->3}
 Box(Modifier.fillMaxSize().background(SgTheme.Canvas).padding(horizontal=72.dp,vertical=24.dp)){
  // Scrolls so every action stays reachable by D-pad on a small or zoomed display; focus brings the focused action into view.
  Column(Modifier.widthIn(max=900.dp).fillMaxHeight().verticalScroll(rememberScrollState()).align(Alignment.CenterStart),verticalArrangement=Arrangement.spacedBy(10.dp)){
   Label("SG SPORTS · SET UP",18,SgTheme.Green,bold=true,lines=1)
   Label("Step $stepNumber of 3",16,SgTheme.Muted,lines=1,modifier=Modifier.testTag("ob-step"))
   when(ui.step){
    OnboardingStep.ADDRESS->{
     Label("Connect to your SG Sports backend",40,bold=true,modifier=Modifier.semantics{heading()}.testTag("ob-title"),lines=2)
     Label("Enter the address of the SG Sports backend you run, for example http://192.168.1.20:8099. You set it up on a phone or computer; nothing is typed here except this address and a code.",20,SgTheme.Muted,lines=4)
     if(ui.error.isNotEmpty())Label(ui.error,20,SgTheme.Gold,bold=true,lines=4,modifier=Modifier.semantics{liveRegion=LiveRegionMode.Assertive}.testTag("ob-error"))
     if(ui.backend.isNotEmpty())ObAction("ob-retry","Try ${ui.backend} again","Run the connection test once more.",first,true,onClick=onRetry)
     ObAction("ob-address",if(ui.backend.isEmpty())"Enter backend address" else "Edit address","Type the address with the remote or a keyboard.",if(ui.backend.isEmpty())first else null,ui.backend.isEmpty(),onClick=onEnterAddress)
    }
    OnboardingStep.TESTING->{
     Label("Checking the connection…",40,bold=true,modifier=Modifier.semantics{heading()}.testTag("ob-title"),lines=2)
     Label("Looking for SG Sports at ${ui.backend}",20,SgTheme.Muted,lines=2,modifier=Modifier.semantics{liveRegion=LiveRegionMode.Polite})
     Label("Press Back to cancel.",18,SgTheme.Muted,lines=1)
    }
    OnboardingStep.PAIR->{
     Label("Pair this TV",34,bold=true,modifier=Modifier.semantics{heading()}.testTag("ob-title"),lines=2)
     Label("Connected to ${ui.backend}.",20,SgTheme.Green,lines=2,modifier=Modifier.testTag("ob-connected"))
     Label("1. On a phone or computer open the backend console:",18,SgTheme.Text,lines=2)
     Label(ui.consoleUrl,28,SgTheme.Green,bold=true,lines=2,modifier=Modifier.testTag("ob-console-url"))
     Label("2. Sign in there, find TV pairing and choose Create a TV pairing code.",18,SgTheme.Text,lines=3)
     Label("3. Choose Enter pairing code below and type the six digits. The code works for five minutes.",18,SgTheme.Text,lines=3)
     if(ui.error.isNotEmpty())Label(ui.error,20,SgTheme.Gold,bold=true,lines=4,modifier=Modifier.semantics{liveRegion=LiveRegionMode.Assertive}.testTag("ob-error"))
     ObAction("ob-code","Enter pairing code","Six digits from the console.",first,true,enabled=!ui.busy,onClick=onEnterCode)
     ObAction("ob-edit","Change backend address","Go back and use another address.",null,false,enabled=!ui.busy,onClick=onBack)
     ObAction("ob-skip","Skip for now","Pair later in Settings > Connection.",null,false,enabled=!ui.busy,onClick=onFinish)
    }
    OnboardingStep.DONE->{
     Label("This TV is paired",40,bold=true,modifier=Modifier.semantics{heading()}.testTag("ob-title"),lines=2)
     Label("Your games and channels now come from ${ui.backend}. You can change favorites, competitions and the backend later in Settings.",20,SgTheme.Muted,lines=3)
     ObAction("ob-prefs","Choose favorite teams (optional)","Open Preferences now.",first,true,onClick=onChoosePreferences)
     ObAction("ob-done","Show my games","Skip this and go to the home screen.",null,false,onClick=onFinish)
    }
   }
  }
 }
}
