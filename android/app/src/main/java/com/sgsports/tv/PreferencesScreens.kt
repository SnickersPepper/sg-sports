package com.sgsports.tv

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Pages reached from Settings; Back returns there. */
val SETTINGS_SUBPAGES=setOf("Preferences","Connection")

private fun <T> next(options:List<T>,current:T):T=options[(options.indexOf(current)+1).mod(options.size)]

@Composable private fun PrefCard(tag:String,title:String,detail:String,modifier:Modifier=Modifier,selected:Boolean=false,enabled:Boolean=true,accent:Color=SgTheme.Green,onClick:()->Unit){
 SgCard(modifier.testTag(tag),accent=accent,selected=selected,enabled=enabled,focusKey=tag,onClick=onClick){Label(title,16,bold=true);if(detail.isNotEmpty())Label(detail,13,SgTheme.Muted,lines=2)}
}

private fun changeSummary(changes:JSONObject):String=changes.keys().asSequence().map{when(it){"favorites"->"favorite teams";"leagues"->"competitions";"language"->"language";"spoilers"->"spoilers";"playback"->"playback";"recording"->"recordings";else->it}}.sorted().joinToString(", ")

/**
 * Settings > Preferences: the allowed profile preferences, loaded from and saved to the backend's versioned settings.
 * Everything is a focusable card (D-pad / OK), Back leaves the page, and each load/save outcome is stated in words.
 */
fun LazyListScope.preferencesItems(state:SgState,draft:PrefsDraft?,onDraft:(PrefsDraft)->Unit,onReload:()->Unit,onSave:(JSONObject)->Unit,onUseLatest:()->Unit,onOverwrite:(JSONObject)->Unit,onBack:()->Unit){
 when(val ui=state.prefs){
  PrefsUi.Idle,PrefsUi.Loading->item(key="prefs-loading"){Label("Loading preferences from the backend…",16,SgTheme.Muted,modifier=Modifier.testTag("prefs-loading"))}
  is PrefsUi.Failed->item(key="prefs-error"){
   Column(Modifier.testTag("prefs-error"),verticalArrangement=Arrangement.spacedBy(10.dp)){
    StatusCard("Preferences unavailable",ui.message,if(ui.retry)"Try again" else "Back to Settings"){if(ui.retry)onReload() else onBack()}
   }
  }
  is PrefsUi.Conflict->item(key="prefs-conflict"){
   Column(Modifier.testTag("prefs-conflict"),verticalArrangement=Arrangement.spacedBy(10.dp)){
    Label("Changed elsewhere",22,SgTheme.Gold,true)
    Label("These preferences were saved from another screen (the web page or another TV) while you were editing. Your unsaved changes: ${changeSummary(ui.attempted).ifBlank{"none"}}.",16,SgTheme.Muted,lines=4)
    Row(horizontalArrangement=Arrangement.spacedBy(12.dp)){
     PrefCard("prefs-use-latest","Use the latest","Discard my edits and show what is saved.",Modifier.weight(1f),onClick=onUseLatest)
     PrefCard("prefs-overwrite","Save mine over it","Apply only my edited preferences on top of the latest.",Modifier.weight(1f),accent=SgTheme.Gold,onClick={onOverwrite(ui.attempted)})
    }
   }
  }
  is PrefsUi.Ready->if(draft!=null){
   val changes=draft.changes(ui.data.prefs)
   val busy=ui.saving
   item(key="prefs-head"){
    Column(verticalArrangement=Arrangement.spacedBy(4.dp)){
     Label("Profile: ${ui.data.profileName} · saved version ${ui.data.revision}",14,SgTheme.Muted,modifier=Modifier.testTag("prefs-profile"))
     Label(if(busy)"Saving…" else ui.notice.ifBlank{if(changes.length()==0)"Everything shown is saved." else "Unsaved changes: ${changeSummary(changes)}."},14,if(changes.length()==0||busy)SgTheme.Muted else SgTheme.Gold,modifier=Modifier.testTag("prefs-status"))
    }
   }
   item(key="prefs-fav-title"){Section("Favorite teams")}
   if(draft.favoritesUnset)item(key="prefs-fav-migrate"){Label("Not saved to the backend yet. This is the list this TV already uses; Save shares it with the web page.",14,SgTheme.Gold,lines=3)}
   if(draft.favorites.isEmpty())item(key="prefs-fav-empty"){Label("No favorite teams. Add one below, or leave it empty.",15,SgTheme.Muted,modifier=Modifier.testTag("prefs-fav-empty"))}
   items(draft.favorites.size,key={"prefs-favrow-"+draft.favorites[it]}){i->
    val id=draft.favorites[i]
    Row(horizontalArrangement=Arrangement.spacedBy(10.dp),verticalAlignment=androidx.compose.ui.Alignment.CenterVertically){
     val title=favoriteTitle(id,state.catalog,state.teams)
     Mark(ArtworkKeys.forTeam(id,state.teams.firstOrNull{it.id==id}?.logo.orEmpty()),title?:id,34)
     Column(Modifier.weight(1f)){Label("${i+1}. ${title?:id}",16,bold=true,lines=1);if(title==null)Label("Not in this catalog; kept as saved",12,SgTheme.Muted)}
     PrefCard("pref-fav-up-$i","▲ Earlier","",Modifier.width(150.dp),enabled=i>0){onDraft(draft.copy(favorites=draft.favorites.movedUp(i)))}
     PrefCard("pref-fav-down-$i","▼ Later","",Modifier.width(150.dp),enabled=i<draft.favorites.size-1){onDraft(draft.copy(favorites=draft.favorites.movedDown(i)))}
     PrefCard("pref-fav-remove-$i","Remove","",Modifier.width(150.dp),accent=Color(0xFFFF5468)){onDraft(draft.copy(favorites=draft.favorites.removedAt(i)))}
    }
   }
   val candidates=teamChoices(state.catalog,state.teams,draft.favorites)
   if(candidates.isNotEmpty()){
    item(key="prefs-fav-add-title"){Label("Add a team",16,SgTheme.Muted,true)}
    items(candidates.chunked(3),key={"prefs-fav-add-"+it.first().id}){row->
     Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
      row.forEach{team->PrefCard("pref-fav-add-${team.id}","+ ${team.title}",team.subtitle,Modifier.weight(1f)){onDraft(draft.copy(favorites=draft.favorites.withAdded(team.id)))}}
      repeat(3-row.size){Spacer(Modifier.weight(1f))}
     }
    }
   }
   item(key="prefs-leagues-title"){Section("Competitions")}
   val leagueRows=leagueChoices(state.catalog)
   if(leagueRows.isEmpty())item(key="prefs-leagues-pending"){Label(if(state.catalog==null)"The competition list loads from the backend." else "The backend lists no competitions.",14,SgTheme.Muted)}
   items(leagueRows.chunked(4),key={"prefs-league-"+it.first().id}){row->
    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
     row.forEach{league->
      val on=league.id in draft.leagues
      PrefCard("pref-league-${league.id}",(if(on)"✓ " else "")+league.title,if(on)"Selected · ${league.subtitle}" else league.subtitle,Modifier.weight(1f),selected=on){onDraft(draft.copy(leagues=if(on)draft.leagues-league.id else draft.leagues+league.id))}
     }
     repeat(4-row.size){Spacer(Modifier.weight(1f))}
    }
   }
   val knownLeagues=leagueRows.map{it.id}.toSet()
   val keptLeagues=draft.leagues.filter{it !in knownLeagues}
   if(state.catalog!=null)items(keptLeagues.size,key={"prefs-league-kept-"+keptLeagues[it]}){i->
    val id=keptLeagues[i]
    PrefCard("pref-league-kept-$id","✓ $id","Not in this catalog; kept as saved. Select to remove.",Modifier.fillMaxWidth(),selected=true){onDraft(draft.copy(leagues=draft.leagues-id))}
   }
   item(key="prefs-play-title"){Section("Listening and playback")}
   item(key="prefs-play-row"){
    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
     PrefCard("pref-language","Preferred audio",languageLabel(draft.language),Modifier.weight(1f)){onDraft(draft.copy(language=next(PREF_LANGUAGES,draft.language)))}
     PrefCard("pref-spoilers","Spoilers",if(draft.spoilers)"Show scores and results" else "Hide scores and results",Modifier.weight(1f),selected=draft.spoilers){onDraft(draft.copy(spoilers=!draft.spoilers))}
     PrefCard("pref-start","Start from the beginning",if(draft.startFromBeginning)"On, when a recording allows it" else "Off",Modifier.weight(1f),selected=draft.startFromBeginning){onDraft(draft.copy(startFromBeginning=!draft.startFromBeginning))}
     PrefCard("pref-quality","Video quality",draft.quality.replaceFirstChar{it.uppercase()},Modifier.weight(1f)){onDraft(draft.copy(quality=next(PREF_QUALITIES,draft.quality)))}
    }
   }
   item(key="prefs-rec-title"){Section("Recordings")}
   item(key="prefs-rec-row"){
    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
     PrefCard("pref-rec-enabled","Record games",if(draft.recEnabled)"On" else "Off",Modifier.weight(1f),selected=draft.recEnabled){onDraft(draft.copy(recEnabled=!draft.recEnabled))}
     PrefCard("pref-rec-favorites","Favorites only",if(draft.recFavoritesOnly)"Only favorite teams" else "Any eligible game",Modifier.weight(1f),selected=draft.recFavoritesOnly){onDraft(draft.copy(recFavoritesOnly=!draft.recFavoritesOnly))}
     PrefCard("pref-rec-keep","Keep latest","${draft.keepLatest} per team",Modifier.weight(1f)){onDraft(draft.copy(keepLatest=next(PREF_KEEP_LATEST,draft.keepLatest)))}
    }
   }
   item(key="prefs-actions"){
    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
     PrefCard("pref-save","Save to backend",if(changes.length()==0)"Nothing to save" else "Shared with the web setup page",Modifier.weight(1f),enabled=changes.length()>0&&!busy){onSave(changes)}
     PrefCard("pref-discard","Discard changes","Back to what is saved",Modifier.weight(1f),enabled=changes.length()>0&&!busy,accent=Color(0xFFFF5468)){onDraft(PrefsDraft.from(ui.data.prefs,state.legacyFavoriteSeed))}
     PrefCard("pref-reload","Reload","Fetch the latest from the backend",Modifier.weight(1f),enabled=!busy,onClick=onReload)
    }
   }
  }
 }
}

/** Settings > Connection: which backend this TV talks to, deliberate re-pairing, and the non-secret web-administration address. */
fun LazyListScope.connectionItems(state:SgState,onChange:()->Unit,onPair:()->Unit,onForget:()->Unit){
 item(key="conn-backend"){
  Column(verticalArrangement=Arrangement.spacedBy(10.dp)){
   Section("Backend")
   Label(state.backendUrl.ifBlank{"Not set"},18,bold=true,modifier=Modifier.testTag("conn-backend-url"),lines=1)
   Label(if(state.paired)"This TV is paired with this backend." else "This TV is not paired yet.",14,SgTheme.Muted)
   Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
    PrefCard("conn-change","Change backend","Switching signs this TV out and needs a new code.",Modifier.weight(1f),accent=SgTheme.Gold,onClick=onChange)
    PrefCard("conn-pair",if(state.paired)"Pair again" else "Pair this TV","Enter the code shown by the backend.",Modifier.weight(1f),onClick=onPair)
    PrefCard("conn-forget","Forget this TV","Remove the pairing from the backend and here.",Modifier.weight(1f),enabled=state.paired,accent=Color(0xFFFF5468),onClick=onForget)
   }
  }
 }
 item(key="conn-admin"){
  Column(verticalArrangement=Arrangement.spacedBy(8.dp)){
   Section("Set up from a phone or computer")
   Label("Open this address in a browser on your home network. Sign in there; providers, guides and the household are configured on that page.",14,SgTheme.Muted,lines=3)
   Label(state.adminUrl.ifBlank{"Not available"},20,SgTheme.Green,true,modifier=Modifier.testTag("conn-admin-url"),lines=1)
   state.adminCheck.note.takeIf{it.isNotEmpty()}?.let{Label(it,14,if(state.adminCheck==AdminCheck.AVAILABLE)SgTheme.Green else SgTheme.Gold,modifier=Modifier.testTag("conn-admin-check"),lines=3)}
   Label("The address contains no password or key, and this TV never shows or sends one.",13,SgTheme.Muted,lines=2)
  }
 }
}
