package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TvSettingsLogicTest {
 private fun prefs(json:String)=JSONObject(json)
 private val seed=listOf("team.soccer.men.la-liga.fc-barcelona","team.football.men.ncaa.tennessee-volunteers")

 @Test fun cleanProfileWithNoFavoritesFieldStartsEmptyAndLegacySeedIsSavedExplicitly(){
  val clean=PrefsDraft.from(prefs("{}"))
  assertTrue(clean.favoritesUnset);assertTrue(clean.favorites.isEmpty())
  val draft=PrefsDraft.from(prefs("{}"),seed)
  assertTrue(draft.favoritesUnset);assertEquals(seed,draft.favorites)
  val changes=draft.changes(prefs("{}"))
  assertEquals(listOf("favorites"),changes.keys().asSequence().toList())
  assertEquals(seed,(0 until changes.getJSONArray("favorites").length()).map{changes.getJSONArray("favorites").getString(it)})
 }

 @Test fun storedFavoritesWinAndEmptyListIsStoredNotMigrated(){
  val draft=PrefsDraft.from(prefs("""{"favorites":[]}"""),seed)
  assertFalse(draft.favoritesUnset);assertTrue(draft.favorites.isEmpty())
  assertEquals(0,draft.changes(prefs("""{"favorites":[]}""")).length())
  val cleared=PrefsDraft.from(prefs("""{"favorites":["a.b","c.d"]}"""),seed).let{it.copy(favorites=emptyList())}
  assertEquals(0,cleared.changes(prefs("""{"favorites":["a.b","c.d"]}""")).getJSONArray("favorites").length())
 }

 @Test fun reorderRemoveAddAndMoreThanSevenFavorites(){
  val many=(1..10).map{"team.football.men.nfl.entry-$it"}
  var list=many
  list=list.movedUp(3);assertEquals("team.football.men.nfl.entry-4",list[2])
  list=list.movedDown(0);assertEquals("team.football.men.nfl.entry-2",list[0])
  assertEquals(list,list.movedUp(0));assertEquals(list,list.movedDown(list.size-1)) // edges are no-ops
  list=list.removedAt(9);assertEquals(9,list.size)
  list=list.withAdded("team.soccer.women.liga-f.fc-barcelona").withAdded("team.soccer.women.liga-f.fc-barcelona");assertEquals(10,list.size)
  val base=prefs("""{"favorites":${JSONArray(many)}}""")
  val changes=PrefsDraft.from(base,seed).copy(favorites=list).changes(base)
  assertEquals(10,changes.getJSONArray("favorites").length())
 }

 @Test fun onlyChangedKeysAreSentAndUnknownNestedFieldsSurvive(){
  val base=prefs("""{"language":"en","playback":{"quality":"auto","audio_language":"es"},"recording":{"keep_latest":3},"leagues":["nfl"]}""")
  val draft=PrefsDraft.from(base,seed).copy(favorites=emptyList(),quality="high",keepLatest=10,leagues=listOf("nfl","mlb"))
  val changes=draft.changes(base)
  assertEquals(setOf("favorites","leagues","playback","recording"),changes.keys().asSequence().toSet())
  assertEquals("es",changes.getJSONObject("playback").getString("audio_language")) // not edited here, not dropped
  assertEquals("high",changes.getJSONObject("playback").getString("quality"))
  assertEquals(10,changes.getJSONObject("recording").getInt("keep_latest"))
  assertFalse(changes.has("language"));assertFalse(changes.has("spoilers"))
  // a TV never names anything outside its allowed keys
  assertTrue(changes.keys().asSequence().all{it in setOf("favorites","leagues","language","spoilers","playback","recording")})
 }

 @Test fun saveBodyCarriesTheRevisionTheEditStartedFrom(){
  val body=saveBody(TvSettingsData(12,"Household",prefs("{}")),JSONObject().put("spoilers",true))
  assertEquals(12,body.getInt("revision"));assertTrue(body.getJSONObject("preferences").getBoolean("spoilers"))
  assertEquals(setOf("revision","preferences"),body.keys().asSequence().toSet())
 }

 @Test fun outcomesAreMappedToHonestStates(){
  val ok=JSONObject("""{"revision":4,"profile_id":"prof-household","profile_name":"Household","preferences":{"favorites":["a.b"]}}""")
  assertEquals(4,(prefsOutcome(200,ok) as PrefsUi.Ready).data.revision)
  val attempted=JSONObject().put("favorites",JSONArray(listOf("x.y")))
  val conflict=prefsOutcome(409,JSONObject().put("error","revision_conflict").put("current",ok),attempted) as PrefsUi.Conflict
  assertEquals(4,conflict.current.revision);assertEquals(attempted.toString(),conflict.attempted.toString())
  assertFalse((prefsOutcome(401,null) as PrefsUi.Failed).retry)
  assertTrue((prefsOutcome(403,JSONObject().put("reason","no_profile_assigned")) as PrefsUi.Failed).message.contains("profile"))
  assertTrue((prefsOutcome(0,null) as PrefsUi.Failed).retry)
  assertTrue(prefsOutcome(200,JSONObject("{}")) is PrefsUi.Failed)
  assertTrue(prefsOutcome(409,JSONObject("{}")) is PrefsUi.Failed)
  assertTrue(prefsOutcome(500,null) is PrefsUi.Failed)
 }
}
