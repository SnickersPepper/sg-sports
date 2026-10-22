package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject

/** What the backend says about this TV's own profile: the shared revision and the allowed preferences only. */
data class TvSettingsData(val revision:Int,val profileName:String,val prefs:JSONObject)

/** Load/save state of the synced preferences, shown honestly on the Preferences page. */
sealed class PrefsUi {
 object Idle:PrefsUi()
 object Loading:PrefsUi()
 data class Ready(val data:TvSettingsData,val saving:Boolean=false,val notice:String=""):PrefsUi()
 data class Failed(val message:String,val retry:Boolean=true):PrefsUi()
 /** Another editor (the browser, another TV on this profile) saved first. [current] is what is stored now. */
 data class Conflict(val current:TvSettingsData,val attempted:JSONObject):PrefsUi()
}

val PREF_LANGUAGES=listOf("en","es","pt","fr","ar")
val PREF_QUALITIES=listOf("auto","high","low")
val PREF_KEEP_LATEST=listOf(1,3,5,10,25)

/** One selectable catalog entry as the backend describes it. IDs are the backend's canonical, stable IDs: the TV never derives one from a label or a list position. */
data class CatalogEntry(val id:String,val name:String,val sport:String,val gender:String,val league:String,val schedule:Boolean,val programMatching:Boolean){
 /** Display text only; two entries that differ in gender or sport never share a label. */
 val detail:String get()=sport+when(gender){"women"->" · Women's";"men"->" · Men's";else->""}
 val label:String get()=name+" · "+detail
 /** Honest support wording: selectable does not mean scheduled, matched in listings, or recordable. */
 val supportNote:String get()=if(schedule||programMatching)"" else "Selectable; no schedule or live matching yet"
}
data class FavoriteCatalog(val teams:List<CatalogEntry>,val leagues:List<CatalogEntry>)

fun parseCatalog(json:JSONObject):FavoriteCatalog? {
 if(!json.has("teams")||!json.has("leagues"))return null
 fun entries(key:String)=json.objects(key).mapNotNull{o->
  val id=o.optString("id").takeIf{it.isNotBlank()}?:return@mapNotNull null
  val support=o.optJSONObject("support")
  CatalogEntry(id,o.optString("name"),o.optString("sport"),o.optString("gender"),o.optString("league"),support?.optBoolean("schedule")==true,support?.optBoolean("program_matching")==true)
 }
 return FavoriteCatalog(entries("teams"),entries("leagues"))
}

/** A row in the favorites / competitions pickers. */
data class PrefChoice(val id:String,val title:String,val subtitle:String)

/** Title for a saved favorite ID, from the backend catalog first, then the installed favorites; null means the ID is unknown here and is kept as saved. */
fun favoriteTitle(id:String,catalog:FavoriteCatalog?,installed:List<Team>):String? =
 catalog?.teams?.firstOrNull{it.id==id}?.label?:installed.firstOrNull{it.id==id}?.let{"${it.name} · ${it.sport}"}

/** Teams that can still be added. Without a catalog the installed favorites are offered, by their own explicit IDs. */
fun teamChoices(catalog:FavoriteCatalog?,installed:List<Team>,chosen:List<String>):List<PrefChoice> =
 (catalog?.teams?.map{PrefChoice(it.id,it.name,it.detail+(if(it.supportNote.isEmpty())"" else " — "+it.supportNote))}
  ?:installed.map{PrefChoice(it.id,it.name,it.sport)}).filter{it.id !in chosen}

fun leagueChoices(catalog:FavoriteCatalog?):List<PrefChoice> = catalog?.leagues?.map{PrefChoice(it.id,it.name,it.sport)}?:emptyList()

/** An edit-in-progress copy of the preferences the TV is allowed to change. */
data class PrefsDraft(
 val favorites:List<String>,val favoritesUnset:Boolean,val leagues:List<String>,val language:String,val spoilers:Boolean,
 val startFromBeginning:Boolean,val quality:String,val recEnabled:Boolean,val recFavoritesOnly:Boolean,val keepLatest:Int
) {
 /** Top-level keys that differ from [base] (nested playback/recording keep the fields this TV does not edit). Empty means nothing to save. */
 fun changes(base:JSONObject):JSONObject {
  val out=JSONObject()
  val baseFavorites=base.optJSONArray("favorites")?.let{a->(0 until a.length()).map{a.optString(it)}}
  if(favoritesUnset||baseFavorites!=favorites)out.put("favorites",JSONArray(favorites))
  if(baseStrings(base,"leagues")!=leagues)out.put("leagues",JSONArray(leagues))
  if(base.optString("language","en")!=language)out.put("language",language)
  if(base.optBoolean("spoilers",false)!=spoilers)out.put("spoilers",spoilers)
  val playback=JSONObject(base.optJSONObject("playback")?.toString()?:"{}")
  var playbackChanged=false
  if(playback.optBoolean("start_from_beginning",false)!=startFromBeginning){playback.put("start_from_beginning",startFromBeginning);playbackChanged=true}
  if(playback.optString("quality","auto")!=quality){playback.put("quality",quality);playbackChanged=true}
  if(playbackChanged)out.put("playback",playback)
  val recording=JSONObject(base.optJSONObject("recording")?.toString()?:"{}")
  var recordingChanged=false
  if(recording.optBoolean("enabled",true)!=recEnabled){recording.put("enabled",recEnabled);recordingChanged=true}
  if(recording.optBoolean("favorites_only",true)!=recFavoritesOnly){recording.put("favorites_only",recFavoritesOnly);recordingChanged=true}
  if(recording.optInt("keep_latest",3)!=keepLatest){recording.put("keep_latest",keepLatest);recordingChanged=true}
  if(recordingChanged)out.put("recording",recording)
  return out
 }
 companion object {
  /** [legacySeed] is non-empty only for a proven pre-Phase-7A install ([FavoritesMigration]); a clean or unproven profile starts with no favorites. */
  fun from(prefs:JSONObject,legacySeed:List<String> = emptyList()):PrefsDraft {
   val stored=prefs.optJSONArray("favorites")?.let{a->(0 until a.length()).map{a.optString(it)}}
   val playback=prefs.optJSONObject("playback")?:JSONObject()
   val recording=prefs.optJSONObject("recording")?:JSONObject()
   return PrefsDraft(stored?:legacySeed,stored==null,baseStrings(prefs,"leagues"),prefs.optString("language","en"),prefs.optBoolean("spoilers",false),
    playback.optBoolean("start_from_beginning",false),playback.optString("quality","auto").takeIf{it in PREF_QUALITIES}?:"auto",
    recording.optBoolean("enabled",true),recording.optBoolean("favorites_only",true),recording.optInt("keep_latest",3))
  }
 }
}

/**
 * One-time carry-over of the seven historical bundled favorites for a client installed before Phase 7A.
 * The only pre-existing durable evidence is a pairing token without a recorded issuing backend ([BackendPolicy.KEY_TOKEN_BACKEND]
 * is written by every pairing since the backend binding). It is read once, at first launch of this build, and the verdict is
 * stored. Anything else (no token, bound token, already stamped) fails closed to no personal default; the bundled teams stay
 * selectable in the picker. The marker settles to [DONE] as soon as the backend profile holds a favorites field (even empty).
 */
object FavoritesMigration {
 const val KEY="favorites_migration"
 const val PENDING="legacy_pending"
 const val DONE="done"

 fun stamp(store:PrefStore){
  if(store.get(KEY)!=null)return
  val legacy=store.get(BackendPolicy.KEY_TOKEN).orEmpty().isNotEmpty()&&store.get(BackendPolicy.KEY_TOKEN_BACKEND)==null
  store.edit(mapOf(KEY to if(legacy)PENDING else DONE),emptySet())
 }

 /** The canonical IDs to seed, only while the migration is pending and the profile has no stored favorites; settles it otherwise. */
 fun seedFor(store:PrefStore,prefs:JSONObject,bundledIds:List<String>):List<String>{
  if(store.get(KEY)!=PENDING)return emptyList()
  if(prefs.has("favorites")){store.edit(mapOf(KEY to DONE),emptySet());return emptyList()}
  return bundledIds
 }
}

private fun baseStrings(prefs:JSONObject,key:String):List<String> = prefs.optJSONArray(key)?.let{a->(0 until a.length()).map{a.optString(it)}}?:emptyList()

fun <T> List<T>.movedUp(index:Int):List<T> = if(index<=0||index>=size)this else toMutableList().also{val v=it.removeAt(index);it.add(index-1,v)}
fun <T> List<T>.movedDown(index:Int):List<T> = if(index<0||index>=size-1)this else toMutableList().also{val v=it.removeAt(index);it.add(index+1,v)}
fun <T> List<T>.removedAt(index:Int):List<T> = if(index !in indices)this else toMutableList().also{it.removeAt(index)}
fun List<String>.withAdded(id:String):List<String> = if(id in this)this else this+id

fun parseTvSettings(json:JSONObject):TvSettingsData? {
 val prefs=json.optJSONObject("preferences")?:return null
 if(!json.has("revision"))return null
 return TvSettingsData(json.optInt("revision"),json.optString("profile_name","Household"),prefs)
}

/** Request body for a save: the revision this edit started from plus only the preferences that changed. */
fun saveBody(data:TvSettingsData,changes:JSONObject):JSONObject=JSONObject().put("revision",data.revision).put("preferences",changes)

/** Maps an HTTP outcome from /v1/tv/settings to what the page must show. [status] 0 means the server could not be reached. */
fun prefsOutcome(status:Int,body:JSONObject?,attempted:JSONObject=JSONObject()):PrefsUi = when{
 status==200&&body!=null->parseTvSettings(body)?.let{PrefsUi.Ready(it)}?:PrefsUi.Failed("The backend sent preferences this app cannot read.")
 status==409&&body!=null->body.optJSONObject("current")?.let(::parseTvSettings)?.let{PrefsUi.Conflict(it,attempted)}?:PrefsUi.Failed("Preferences changed elsewhere. Reload and try again.")
 status==401->PrefsUi.Failed("This TV is not paired with the selected backend. Pair it under Connection.",retry=false)
 status==403->PrefsUi.Failed(when(body?.optString("reason")){
  "no_profile_assigned"->"This TV is not assigned to a profile yet. Assign it on the backend's web setup page."
  else->"The backend no longer accepts this TV. Pair it again under Connection."},retry=false)
 status==422->PrefsUi.Failed("The backend rejected one of the values.")
 status==0->PrefsUi.Failed("Cannot reach the backend. Check the TV's home network connection.")
 else->PrefsUi.Failed("The backend could not load preferences (error $status).")
}

fun languageLabel(code:String)=java.util.Locale(code).getDisplayLanguage(java.util.Locale.ENGLISH).replaceFirstChar{it.uppercase()}.ifBlank{code}
