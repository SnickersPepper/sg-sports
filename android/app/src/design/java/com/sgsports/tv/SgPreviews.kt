package com.sgsports.tv

import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview

/** Start Interactive Mode in Split/Design to browse without any device or provider stream. */
@Composable private fun DesignPreview(page:String="Home") {
 val context=LocalContext.current
 var state by remember { mutableStateOf(sampleState(context)) }
 SgApp(state,initialPage=page,onGame={ game -> state=state.copy(selected=game,feeds=listOf(Feed("ESPN","ProviderB","English · sample label","logos/espn.png"),Feed("Alternate feed","ProviderA","Audio unknown",""))) },onSetting={ action -> if(action=="language")state=state.copy(language=if(state.language=="English")"Spanish" else "English") })
}
@Preview(name="Home · interactive",widthDp=960,heightDp=540,showBackground=true,backgroundColor=0xFF000000)
@Composable fun SgSportsInteractivePreview()=DesignPreview()
@Preview(name="Sports",widthDp=960,heightDp=540)
@Composable fun SportsPreview()=DesignPreview("Sports")
@Preview(name="Leagues",widthDp=960,heightDp=540)
@Composable fun LeaguesPreview()=DesignPreview("Leagues")
@Preview(name="Settings",widthDp=960,heightDp=540)
@Composable fun SettingsPreview()=DesignPreview("Settings")
