package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

class FavoritesMigrationTest {
 private class Store(val values:MutableMap<String,String> = mutableMapOf()):PrefStore {
  override fun get(key:String)=values[key]
  override fun edit(puts:Map<String,String>,removes:Set<String>){removes.forEach{values.remove(it)};values.putAll(puts)}
 }
 private val seven=listOf("team.a","team.b","team.c","team.d","team.e","team.f","team.g")
 private val noField=JSONObject("{}")
 private fun seed(s:Store,prefs:JSONObject=noField)=FavoritesMigration.seedFor(s,prefs,seven)

 @Test fun cleanInstallIsEmptyAndStaysEmpty(){
  val s=Store();FavoritesMigration.stamp(s)
  assertEquals(FavoritesMigration.DONE,s.values[FavoritesMigration.KEY])
  assertTrue(seed(s).isEmpty());assertTrue(PrefsDraft.from(noField,seed(s)).favorites.isEmpty())
 }

 @Test fun pairingBoundToABackendIsNotLegacy(){
  val s=Store(mutableMapOf("token" to "t","token_backend" to "https://x"));FavoritesMigration.stamp(s)
  assertTrue(seed(s).isEmpty())
 }

 @Test fun absentFieldWithoutProvenanceStaysEmpty(){
  val s=Store(mutableMapOf("language" to "en"));FavoritesMigration.stamp(s)
  assertTrue(seed(s).isEmpty())
 }

 @Test fun legacyInstallSeedsExactCanonicalSevenUntilSettled(){
  val s=Store(mutableMapOf("token" to "t"));FavoritesMigration.stamp(s)
  assertEquals(FavoritesMigration.PENDING,s.values[FavoritesMigration.KEY])
  assertEquals(seven,seed(s));assertEquals(seven,seed(s)) // recoverable until the profile stores a value
  val draft=PrefsDraft.from(noField,seed(s))
  assertEquals(seven,draft.favorites);assertTrue(draft.favoritesUnset)
 }

 @Test fun storedEmptyListSettlesAndNeverRepopulates(){
  val s=Store(mutableMapOf("token" to "t"));FavoritesMigration.stamp(s)
  val cleared=JSONObject("""{"favorites":[]}""")
  assertTrue(seed(s,cleared).isEmpty())
  assertEquals(FavoritesMigration.DONE,s.values[FavoritesMigration.KEY])
  assertTrue(seed(s).isEmpty()) // even if the field later disappears
 }

 @Test fun legacySeedIsTheRealBundledCatalogIdsInFileOrder(){
  TestAssets.assumePrivate()
  val asset=JSONObject(TestAssets.file("favorites.json").readText())
  val raw=asset.getJSONArray("teams").let{a->(0 until a.length()).map{a.getJSONObject(it).getString("id")}}
  val s=Store(mutableMapOf("token" to "t"));FavoritesMigration.stamp(s)
  val seeded=FavoritesMigration.seedFor(s,noField,parseTeams(asset).map{it.id}) // production source: loadTeams ids
  assertEquals(raw,seeded)
  assertEquals(listOf("team.soccer.men.la-liga.fc-barcelona","team.football.men.ncaa.tennessee-volunteers","team.softball.women.ncaa.tennessee-volunteers",
   "team.baseball.men.mlb.st-louis-cardinals","team.football.men.nfl.kansas-city-chiefs","team.baseball.men.mlb.houston-astros","team.football.men.ncaa.missouri-tigers"),seeded)
  assertEquals(raw.size,raw.toSet().size)
 }

 @Test fun stampIsIdempotentAndNotRerunByUpgradeOrRestart(){
  val s=Store(mutableMapOf("token" to "t"));FavoritesMigration.stamp(s);seed(s,JSONObject("""{"favorites":[]}"""))
  FavoritesMigration.stamp(s);FavoritesMigration.stamp(s)
  assertEquals(FavoritesMigration.DONE,s.values[FavoritesMigration.KEY])
  s.values.remove("token_backend") // pairing record changes never re-arm it
  assertTrue(seed(s).isEmpty())
 }
}
