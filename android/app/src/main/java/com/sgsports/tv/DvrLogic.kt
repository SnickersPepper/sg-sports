package com.sgsports.tv

import org.json.JSONObject

/**
 * Phase 7: pure DVR state mapping, wording, focus order, budget sync and playback recovery.
 * No Android types, so every rule is unit-tested. Wording never claims live status: a recording is
 * a saved file with an honest completeness, and only the server's own fields decide what is shown.
 */
data class Recording(val id:String,val bucket:String,val team:String,val title:String,val state:String,val reason:String?,
 val completeStart:Boolean,val completeEnd:Boolean,val playable:Boolean,val eventStart:Long,val startedAt:Long,val provider:String)
data class RecordingBucket(val id:String,val label:String,val recordings:List<Recording>)
data class RecordingsSnapshot(val enabled:Boolean,val storage:String,val buckets:List<RecordingBucket>)

fun parseRecordings(j:JSONObject):RecordingsSnapshot {
 val buckets=j.objects("buckets").map{b->
  RecordingBucket(b.optString("id"),b.optString("label"),b.objects("recordings").map{r->
   Recording(r.optString("id"),r.optString("bucket"),r.optString("team"),r.optString("title"),r.optString("state"),
    r.optString("reason").takeIf{it.isNotBlank()&&!r.isNull("reason")},
    r.optBoolean("complete_start"),r.optBoolean("complete_end"),r.optBoolean("playable"),
    r.optLong("event_start"),r.optLong("started_at"),r.optString("provider_id"))
  }.filter{it.id.isNotEmpty()})
 }.filter{it.id.isNotEmpty()}
 return RecordingsSnapshot(j.optBoolean("enabled"),j.optString("storage").ifBlank{"unknown"},buckets).organized()
}

/** The server keeps this many recordings per collection (dvr.KEEP); the client applies the same cap to each collection on its own. */
const val RECORDINGS_PER_COLLECTION=3

/** Best available "when was this game": the game's own start, else when capture began. Never the position in the JSON. */
fun recordingTime(r:Recording):Long=if(r.eventStart>0)r.eventStart else r.startedAt
/** Explicit, total order: newest game first; ties fall back to capture start, then id so the result never depends on input order. */
fun newestFirst(list:List<Recording>):List<Recording> =
 list.sortedWith(compareByDescending<Recording>{recordingTime(it)}.thenByDescending{it.startedAt}.thenBy{it.id})

/** Client-side normal form: each collection sorted newest first and capped independently; a recording id appears once overall (first collection wins). */
fun RecordingsSnapshot.organized():RecordingsSnapshot {
 val seen=mutableSetOf<String>()
 return copy(buckets=buckets.map{b->b.copy(recordings=newestFirst(b.recordings.filter{seen.add(it.id)}).take(RECORDINGS_PER_COLLECTION))})
}

/** The recording collection a favorite team feeds, or null when the DVR does not record that team. Matches dvr.BUCKETS. */
fun recordingBucketFor(sport:String,league:String,name:String):String? = when{
 sport=="Soccer"&&name.contains("Barcelona",ignoreCase=true)->"fc-barcelona"
 league=="NCAA"&&sport=="Football"&&name.contains("Tennessee",ignoreCase=true)->"tennessee-football"
 league=="NCAA"&&sport=="Softball"&&(name.contains("Tennessee",ignoreCase=true)||name.contains("Lady Vols",ignoreCase=true))->"tennessee-softball"
 else->null
}
fun recordingBucketFor(team:Team):String?=recordingBucketFor(team.sport,team.league,team.name)

enum class TeamRecordingsKind { NOT_RECORDED, UNAVAILABLE, DISABLED, EMPTY, CONTENT }
data class TeamRecordings(val kind:TeamRecordingsKind,val title:String,val detail:String,val storageNotice:String?,val recordings:List<Recording>)

/** What a team hub's Recorded Games tab shows: only [bucketId]'s recordings (newest first), with the same truthful states as the main page. */
fun teamRecordings(snapshot:RecordingsSnapshot?,bucketId:String?,teamName:String,offline:Boolean):TeamRecordings {
 if(bucketId==null)return TeamRecordings(TeamRecordingsKind.NOT_RECORDED,"Games aren't recorded for $teamName",
  "Automatic recording covers Tennessee football, Tennessee softball and FC Barcelona only.",null,emptyList())
 val own=snapshot?.let{s->s.copy(buckets=s.buckets.filter{it.id==bucketId})}
 val screen=recordingsScreen(own,offline)
 val recs=own?.buckets?.flatMap{newestFirst(it.recordings)}.orEmpty()
 return when(screen.kind) {
  RecordingsScreenKind.UNAVAILABLE->TeamRecordings(TeamRecordingsKind.UNAVAILABLE,screen.title,screen.detail,null,emptyList())
  RecordingsScreenKind.DISABLED->TeamRecordings(TeamRecordingsKind.DISABLED,screen.title,screen.detail,screen.storageNotice,emptyList())
  RecordingsScreenKind.EMPTY->TeamRecordings(TeamRecordingsKind.EMPTY,"No $teamName recordings yet",
   "$teamName games are saved automatically when a confirmed live feed is available. The latest three are kept.",screen.storageNotice,emptyList())
  RecordingsScreenKind.CONTENT->TeamRecordings(TeamRecordingsKind.CONTENT,screen.title,"",screen.storageNotice,recs)
 }
}

// ---- Navigation -----------------------------------------------------------------------------------
val MAIN_NAV=listOf("Home","Sports","Leagues","Recordings","Settings")
const val HUB_OVERVIEW="Overview"
const val HUB_SCHEDULE="Schedule"
const val HUB_ROSTER="Roster"
const val HUB_NEWS="News"
const val HUB_RECORDED="Recorded Games"
val HUB_TABS=listOf(HUB_OVERVIEW,HUB_SCHEDULE,HUB_ROSTER,HUB_NEWS,HUB_RECORDED)
fun hubTabFocusId(tab:String)="hub-tab:$tab"
/** Fixed D-pad order inside a hub's Recorded Games tab: the tab strip, then recordings newest first, then refresh. */
fun hubRecordedFocusOrder(t:TeamRecordings):List<String> =
 HUB_TABS.map(::hubTabFocusId)+(if(t.kind==TeamRecordingsKind.CONTENT)t.recordings.map(::recordingFocusId)+"rec-refresh" else emptyList())

data class NavPosition(val page:String,val hubTab:String)
/** Back: Settings sub-pages (Preferences, Connection) return to Settings; Details returns to where it was opened; any other hub tab steps back to its Overview first; any other page returns Home. Null on Home (system handles it). */
fun backDestination(page:String,hubTab:String,previous:String):NavPosition? = when{
 page=="Home"->null
 page=="Details"->NavPosition(previous,hubTab)
 page in SETTINGS_SUBPAGES->NavPosition("Settings",hubTab)
 page=="Favorite"&&hubTab!=HUB_OVERVIEW->NavPosition("Favorite",HUB_OVERVIEW)
 else->NavPosition("Home",HUB_OVERVIEW)
}

enum class RecordingUi { RECORDING, READY, PARTIAL, STORAGE_PRESSURE, FAILED, EXPIRED }

/** Reasons the server gives that are about disk space. */
private val SPACE_REASONS=setOf("storage_pressure","capacity_unknown")

fun recordingUi(r:Recording):RecordingUi=when(r.state) {
 "recording"->RecordingUi.RECORDING
 "ready"->when{
  r.reason in SPACE_REASONS->RecordingUi.STORAGE_PRESSURE
  fromBeginning(r)&&r.completeEnd->RecordingUi.READY
  else->RecordingUi.PARTIAL
 }
 "failed"->if(r.reason in SPACE_REASONS)RecordingUi.STORAGE_PRESSURE else RecordingUi.FAILED
 // Anything unrecognised is never shown as playable.
 else->RecordingUi.EXPIRED
}

/** Zero tolerance: "from the beginning" needs the server's claim AND capture that began no later than the game's start. */
fun fromBeginning(r:Recording)=r.completeStart&&(r.eventStart<=0||r.startedAt<=r.eventStart)
private fun missedText(r:Recording):String{val s=(r.startedAt-r.eventStart).coerceAtLeast(0);return if(s<60)"under a minute" else "${s/60} min"}
private fun endReason(reason:String?)=when(reason) {
 "interrupted"->"The recording was interrupted, so the end is missing."
 "storage_pressure"->"Recording stopped early because storage ran low."
 "cancelled"->"Recording was stopped before the game ended."
 "source_ended_early"->"The stream ended before the game did."
 "event_unconfirmed"->"Recording stopped when the game could no longer be confirmed."
 "max_duration"->"Recording stopped at its length limit."
 else->"The end of the game was not captured."
}

data class RecordingCard(val kind:RecordingUi,val title:String,val badge:String,val detail:String,val action:String?,val playable:Boolean)

/** The one place wording is decided for a recording. A late start is always stated; "from the beginning" only when the server says so. */
fun recordingCard(r:Recording):RecordingCard {
 val kind=recordingUi(r)
 val title=r.title.ifBlank{r.team}
 val start=if(fromBeginning(r))"From the beginning" else "Starts ${missedText(r)} into the game"
 return when(kind) {
  RecordingUi.RECORDING->RecordingCard(kind,title,"Recording",
   if(r.playable)"$start. You can watch what has been saved so far; it is not the live broadcast." else "Saving this game. Playback is available once the first part is saved.",
   if(r.playable)"Watch from ${if(fromBeginning(r))"the beginning" else "the start of the recording"}" else null,r.playable)
  RecordingUi.READY->RecordingCard(kind,title,"Saved","Complete recording, from the beginning to the end of the game.","Watch from the beginning",r.playable)
  RecordingUi.PARTIAL->RecordingCard(kind,title,"Saved · incomplete",
   listOfNotNull(if(!fromBeginning(r))"The first ${missedText(r)} of the game were not recorded." else null,if(!r.completeEnd)endReason(r.reason) else null).joinToString(" "),
   if(r.playable)"Watch what was saved" else null,r.playable)
  RecordingUi.STORAGE_PRESSURE->if(r.playable)
   RecordingCard(kind,title,"Saved · storage was low","Recording stopped early because storage ran low. The first part was saved.","Watch what was saved",true)
  else RecordingCard(kind,title,"Not recorded","There was not enough free storage to record this game.",null,false)
  RecordingUi.FAILED->RecordingCard(kind,title,"Recording failed",when(r.reason) {
   "provider_busy"->"The provider was already being used for another stream."
   "provider_unavailable"->"The game's source could not be opened."
   "interrupted"->"The recording was interrupted before anything was saved."
   "too_many_attempts"->"Recording was tried several times without success."
   "wrong_content"->"The captured video did not match this game, so it was deleted."
   "unverified"->"The video could not be confirmed as this game, so it was deleted."
   "schedule_changed"->"The game's schedule changed, so the early capture was deleted."
   "not_started"->"The game did not start when scheduled, so the early capture was deleted."
   "cancelled"->"Recording was stopped before the game could be confirmed."
   else->"Nothing was saved."
  },null,false)
  RecordingUi.EXPIRED->RecordingCard(kind,title,"Expired",when(r.reason) {
   "evicted"->"Removed to keep only the latest three games for this team."
   "media_missing"->"The saved video is no longer available."
   "integrity_failed"->"The saved video was damaged and was removed."
   else->"This recording is no longer available."
  },null,false)
 }
}

enum class RecordingsScreenKind { UNAVAILABLE, DISABLED, EMPTY, CONTENT }
data class RecordingsScreen(val kind:RecordingsScreenKind,val title:String,val detail:String,
 /** Shown above the list when storage limits recording; null when storage is fine. */
 val storageNotice:String?)

/** Page-level state: [snapshot] null means the list could not be loaded; never presented as "empty". */
fun recordingsScreen(snapshot:RecordingsSnapshot?,offline:Boolean):RecordingsScreen {
 if(snapshot==null)return RecordingsScreen(RecordingsScreenKind.UNAVAILABLE,"Recordings unavailable",
  if(offline)"Can't reach SG Sports, so saved games can't be listed. Check this TV's home network connection." else "Saved games could not be loaded. Try again in a moment.",null)
 val notice=if(!snapshot.enabled)null else when(snapshot.storage) {
  "pressure"->"Storage is low. New games may not be recorded until space is freed."
  "unknown"->"Free storage can't be checked, so new games are not being recorded."
  else->null
 }
 if(!snapshot.enabled&&snapshot.buckets.all{it.recordings.isEmpty()})return RecordingsScreen(RecordingsScreenKind.DISABLED,"Recording is turned off",
  "Automatic recording is not turned on, so nothing is being saved.",notice)
 if(snapshot.buckets.all{it.recordings.isEmpty()})return RecordingsScreen(RecordingsScreenKind.EMPTY,"No recordings yet",
  "Tennessee football, Tennessee softball and FC Barcelona games are saved automatically when a confirmed live feed is available. The latest three of each are kept.",notice)
 return RecordingsScreen(RecordingsScreenKind.CONTENT,"Recordings","",notice)
}

fun recordingFocusId(r:Recording)="rec:${r.id}"
/** Fixed, top-to-bottom D-pad order: buckets in server order, newest game first, then the refresh action. */
fun recordingFocusOrder(snapshot:RecordingsSnapshot?):List<String> =
 (snapshot?.buckets?.flatMap{b->b.recordings.map(::recordingFocusId)}.orEmpty())+"rec-refresh"
/** Where focus lands when the page opens or is restored (the exact recording if it survives, else its nearest neighbour). */
fun recordingsFocusTarget(last:String?,before:RecordingsSnapshot?,now:RecordingsSnapshot?):String? =
 restoreTarget(last,recordingFocusOrder(before),recordingFocusOrder(now))

/** Providers a recording currently occupies on the server; only these appear as budget holds. */
fun activeRecordingHolds(snapshot:RecordingsSnapshot?):Map<String,String> =
 snapshot?.buckets?.flatMap{it.recordings}?.filter{it.state=="recording"&&it.provider.isNotEmpty()}?.associate{"dvr:${it.id}" to it.provider}.orEmpty()

/** Makes the coordinator's DVR holds equal [wanted] (owner -> provider). Returns owners the budget could not grant, which stay unheld rather than exceed the limit. */
fun syncDvrHolds(coordinator:PlaybackCoordinator,held:MutableMap<String,String>,wanted:Map<String,String>):Set<String> {
 held.filter{(owner,provider)->wanted[owner]!=provider}.forEach{(owner,provider)->coordinator.releaseHold(provider,owner);held.remove(owner)}
 val refused=mutableSetOf<String>()
 wanted.forEach{(owner,provider)->if(owner !in held){if(coordinator.hold(provider,owner))held[owner]=provider else refused+=owner}}
 return refused
}

// ---- Playback recovery -------------------------------------------------------------------------
enum class RecoveryAction { RETRY, BACK }
data class PlaybackRecovery(val message:String,val action:RecoveryAction,val actionLabel:String)
const val MAX_RECORDING_RETRIES=2

/** After a playback error: [latest] is the freshly fetched record (null when it could not be fetched or no longer exists). */
fun recordingPlaybackRecovery(latest:Recording?,failures:Int,offline:Boolean):PlaybackRecovery {
 if(latest==null)return if(offline&&failures<=MAX_RECORDING_RETRIES)PlaybackRecovery("Can't reach SG Sports to play this recording.",RecoveryAction.RETRY,"Try again")
  else PlaybackRecovery("This recording is no longer available.",RecoveryAction.BACK,"Back to recordings")
 val card=recordingCard(latest)
 if(!card.playable)return PlaybackRecovery(card.detail.ifBlank{"This recording is no longer available."},RecoveryAction.BACK,"Back to recordings")
 return if(failures<=MAX_RECORDING_RETRIES)PlaybackRecovery("The saved video stopped playing.",RecoveryAction.RETRY,"Try again")
 else PlaybackRecovery("The saved video could not be played. It may be incomplete.",RecoveryAction.BACK,"Back to recordings")
}
/** The saved video reached its end; if the recording is still going the viewer simply caught up to it. */
fun recordingEndedMessage(latest:Recording?):String =
 if(latest?.state=="recording")"You've caught up to what has been saved so far. The game is still being recorded; this is not live."
 else if(latest!=null&&recordingUi(latest)==RecordingUi.READY)"End of the recording."
 else "End of what was saved."

/** Header line for the player: states the recording is saved video, never live. */
fun recordingPlayerLabel(r:Recording?):String =
 if(r==null)"Saved recording" else "Saved recording · "+(if(fromBeginning(r))"from the beginning" else "starts ${missedText(r)} into the game")
const val RECORDINGS_FRESH_MS=15_000L
