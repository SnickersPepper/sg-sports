package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Phase 5A correction: every LVBP club and the league resolve to a bundled local mark, with no cross-team collisions. */
class LvbpIdentityTest {
 private val assets=listOf(File("src/main/assets"),File("app/src/main/assets")).first{it.isDirectory}
 private val identities=JSONArray(File(assets,"team-identities.json").readText())
 private val lvbpIdentities=(0 until identities.length()).map{identities.getJSONObject(it)}.filter{it.optString("league")==LVBP}
 /** Same construction as the mapper's per-league key index. */
 private val keyIndex:Map<String,Pair<String,String>> = lvbpIdentities.flatMap{t->
  val aliases=t.getJSONArray("aliases");(0 until aliases.length()).map{teamKey(aliases.getString(it)) to (t.getString("name") to t.getString("file"))}
 }.groupBy({it.first},{it.second}).mapValues{it.value.first()}
 private val clubs=mapOf(
  "Águilas del Zulia" to "aguilas-del-zulia","Bravos de Margarita" to "bravos-de-margarita","Cardenales de Lara" to "cardenales-de-lara",
  "Caribes de Anzoátegui" to "caribes-de-anzoategui","Leones del Caracas" to "leones-del-caracas","Navegantes del Magallanes" to "navegantes-del-magallanes",
  "Tiburones de La Guaira" to "tiburones-de-la-guaira","Tigres de Aragua" to "tigres-de-aragua")
 private fun file(name:String)=lookupIdentities(listOf(teamKey(name)),keyIndex,emptyMap()).firstOrNull()?.second

 @Test fun allEightCanonicalClubsResolveToTheirOwnBundledMark() {
  for((name,id) in clubs){
   assertEquals(name,"logos/lvbp_$id.png",file(name))
   assertTrue("$name asset exists",TestAssets.crestOk("logos/lvbp_$id.png"))
  }
  assertEquals(8,clubs.values.map{file(clubs.keys.first{k->clubs[k]==it})}.toSet().size)
 }

 @Test fun accentlessShortAndBackendIdAliasesResolveToTheRightClub() {
  val expect=mapOf("Aguilas del Zulia" to "aguilas-del-zulia","Aguilas" to "aguilas-del-zulia","Zulia" to "aguilas-del-zulia","Caribes de Anzoategui" to "caribes-de-anzoategui",
   "Anzoátegui" to "caribes-de-anzoategui","Leones" to "leones-del-caracas","Caracas" to "leones-del-caracas","Magallanes" to "navegantes-del-magallanes",
   "Navegantes" to "navegantes-del-magallanes","La Guaira" to "tiburones-de-la-guaira","Tiburones" to "tiburones-de-la-guaira","Tigres" to "tigres-de-aragua",
   "Aragua" to "tigres-de-aragua","Bravos" to "bravos-de-margarita","Margarita" to "bravos-de-margarita","Cardenales" to "cardenales-de-lara","Lara" to "cardenales-de-lara",
   "TIBURONES DE LA GUAIRA" to "tiburones-de-la-guaira","tigres-de-aragua" to "tigres-de-aragua","leones-del-caracas" to "leones-del-caracas")
  for((alias,id) in expect)assertEquals(alias,"logos/lvbp_$id.png",file(alias))
 }

 @Test fun noAliasIsSharedBetweenClubsAndEveryClubHasADistinctFile() {
  val seen=mutableMapOf<String,String>()
  for(t in lvbpIdentities){
   val aliases=t.getJSONArray("aliases")
   for(i in 0 until aliases.length()){
    val key=teamKey(aliases.getString(i));val owner=seen.put(key,t.getString("name"))
    assertTrue("alias '$key' claimed by $owner and ${t.getString("name")}",owner==null||owner==t.getString("name"))
   }
  }
  assertEquals(8,lvbpIdentities.size)
  assertEquals(8,lvbpIdentities.map{it.getString("file")}.toSet().size)
  // The LVBP identities must not leak into another league's index nor reuse a non-LVBP asset.
  val others=(0 until identities.length()).map{identities.getJSONObject(it)}.filter{it.optString("league")!=LVBP}.map{it.getString("file")}.toSet()
  assertTrue(lvbpIdentities.none{it.getString("file") in others})
 }

 @Test fun unknownOrBlankTeamsKeepTheFallback() {
  assertNull(file("Gigantes de Carolina"))
  assertNull(file("Leones de Yucatán"))
  assertNull(file(""))
 }

 @Test fun leagueCardUsesTheLvbpMark() {
  val leagues=JSONObject(File(assets,"leagues.json").readText())
  assertEquals("logos/league_baseball_lvbp.png",leagues.optString(LVBP))
  assertTrue(TestAssets.crestOk(leagues.getString(LVBP)))
  assertNotEquals(leagues.optString("MLB"),leagues.optString(LVBP))
 }
}
