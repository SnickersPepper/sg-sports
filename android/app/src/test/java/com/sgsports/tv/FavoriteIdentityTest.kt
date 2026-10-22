package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Canonical favorite identity: IDs come from the backend catalog / explicit asset declarations, never from labels or list positions. */
class FavoriteIdentityTest {
 private val asset by lazy{TestAssets.file("favorites.json").readText().let{JSONObject(it)}}
 @org.junit.Before fun needsRoster(){TestAssets.assumePrivate()}
 private val installed get()=parseTeams(asset)
 private val expectedIds=listOf("team.soccer.men.la-liga.fc-barcelona","team.football.men.ncaa.tennessee-volunteers","team.softball.women.ncaa.tennessee-volunteers",
  "team.baseball.men.mlb.st-louis-cardinals","team.football.men.nfl.kansas-city-chiefs","team.baseball.men.mlb.houston-astros","team.football.men.ncaa.missouri-tigers")
 private fun game(title:String,league:String,sport:String)=Game("g",title,league,sport,"")
 private val catalogJson="""{"version":1,"teams":[
  {"id":"team.soccer.men.la-liga.fc-barcelona","name":"FC Barcelona","sport":"Soccer","gender":"men","league":"La Liga","support":{"schedule":true,"program_matching":true}},
  {"id":"team.soccer.women.liga-f.fc-barcelona","name":"FC Barcelona Femení","sport":"Soccer","gender":"women","league":"Liga F","support":{"schedule":false,"program_matching":false}},
  {"id":"team.football.men.ncaa.tennessee-volunteers","name":"Tennessee Volunteers","sport":"Football","gender":"men","league":"NCAA","support":{"schedule":true,"program_matching":true}},
  {"id":"team.softball.women.ncaa.tennessee-volunteers","name":"Tennessee Lady Volunteers","sport":"Softball","gender":"women","league":"NCAA","support":{"schedule":true,"program_matching":true}}
  ${(1..9).joinToString(""){""",{"id":"team.baseball.men.mlb.club-$it","name":"Club $it","sport":"Baseball","gender":"men","league":"MLB","support":{"schedule":false,"program_matching":false}}"""}}],
  "leagues":[{"id":"league.football.ncaa","name":"NCAA","sport":"Football","support":{"schedule":false,"program_matching":false}},
  {"id":"league.softball.ncaa","name":"NCAA","sport":"Softball","support":{"schedule":false,"program_matching":false}}]}"""
 private val catalog get()=parseCatalog(JSONObject(catalogJson))!!

 @Test fun installedFavoritesCarryExplicitUniqueCanonicalIds(){
  assertEquals(expectedIds,installed.map{it.id})
  assertEquals(installed.size,installed.map{it.id}.toSet().size)
  assertTrue(installed.none{it.id.startsWith("favorite-")})
 }

 @Test fun idsSurviveReorderAndRenameOfTheDeclaringAsset(){
  val teams=asset.getJSONArray("teams")
  val reversed=JSONArray((teams.length()-1 downTo 0).map{teams.getJSONObject(it)})
  val byId=installed.associateBy{it.id}
  for(t in parseTeams(JSONObject().put("teams",reversed))){assertEquals(byId.getValue(t.id).banner,t.banner);assertEquals(byId.getValue(t.id).pattern,t.pattern)}
  val renamed=JSONObject(asset.toString())
  for(i in 0 until renamed.getJSONArray("teams").length())renamed.getJSONArray("teams").getJSONObject(i).put("name","Renamed $i")
  assertEquals(expectedIds,parseTeams(renamed).map{it.id})
 }

 @Test fun noArrayPositionOrBarcelonaIndexSpecialCases(){
  // Only the declared flags matter: a team whose ID looks like the old position ID gets no special treatment ...
  val impostor=Team("favorite-0","Some Club","Soccer","La Liga","",0,0,"club")
  assertFalse(matches(impostor,game("Some Club vs Other","Copa","Soccer")))
  assertTrue(matches(impostor,game("Some Club vs Other","La Liga","Soccer")))
  // ... and the any-league behaviour belongs to the entry that declares it, whatever its ID is.
  val barca=installed.first{it.anyLeague}
  assertEquals("team.soccer.men.la-liga.fc-barcelona",barca.id)
  assertTrue(matches(barca,game("Barcelona vs Girona","Copa del Rey","Soccer")))
  assertFalse(matches(barca,game("RCD Español de Barcelona vs Girona","La Liga","Soccer")))
  assertEquals(listOf(barca.id),installed.filter{it.anyLeague}.map{it.id})
  assertEquals(listOf(barca.id),installed.filter{it.fixtureFeed}.map{it.id})
 }

 @Test fun sportDistinguishesTennesseeFootballFromSoftball(){
  val football=installed.first{it.id.contains("football.men.ncaa.tennessee")};val softball=installed.first{it.id.contains("softball.women.ncaa.tennessee")}
  assertNotEquals(football.id,softball.id)
  val footballGame=game("Tennessee vs Florida","NCAA","Football");val softballGame=game("Lady Vols vs Alabama","NCAA","Softball")
  assertTrue(matches(football,footballGame));assertFalse(matches(football,softballGame))
  assertTrue(matches(softball,softballGame));assertFalse(matches(softball,footballGame))
 }

 @Test fun catalogOffersMoreThanSevenAndKeepsMenAndWomenApart(){
  val c=catalog
  assertTrue(c.teams.size>7)
  val men=c.teams.first{it.id.contains("soccer.men")};val women=c.teams.first{it.id.contains("soccer.women")}
  assertNotEquals(men.label,women.label);assertTrue(men.label.endsWith("Men's"));assertTrue(women.label.endsWith("Women's"))
  assertEquals(2,c.leagues.filter{it.name=="NCAA"}.size) // one NCAA per sport, two IDs
  assertEquals(2,leagueChoices(c).map{it.id}.toSet().size)
 }

 @Test fun supportMetadataIsHonest(){
  val c=catalog
  assertEquals("",c.teams.first{it.id.contains("fc-barcelona")&&it.id.contains("men")}.supportNote)
  val extra=c.teams.first{it.id.contains("club-1")}
  assertTrue(extra.supportNote.contains("no schedule"))
  assertTrue(teamChoices(c,installed,emptyList()).first{it.id==extra.id}.subtitle.contains("no schedule"))
 }

 @Test fun pickerExcludesChosenAndFallsBackToInstalledWithoutACatalog(){
  val chosen=listOf(expectedIds[0])
  assertFalse(teamChoices(catalog,installed,chosen).any{it.id==expectedIds[0]})
  assertEquals(13,teamChoices(catalog,installed,emptyList()).size)
  val fallback=teamChoices(null,installed,chosen)
  assertEquals(expectedIds.drop(1),fallback.map{it.id})
  assertTrue(leagueChoices(null).isEmpty())
 }

 @Test fun unknownSavedIdsKeepTheirPlaceAndAreNeverRenamed(){
  assertNull(favoriteTitle("team.future.men.xyz.new-club",catalog,installed))
  assertEquals("Tennessee Volunteers · Football · Men's",favoriteTitle(expectedIds[1],catalog,installed))
  assertEquals("St. Louis Cardinals · Baseball",favoriteTitle(expectedIds[3],null,installed)) // offline: installed explicit IDs still resolve
  val base=JSONObject("""{"favorites":["${expectedIds[0]}","team.future.men.xyz.new-club","${expectedIds[1]}"]}""")
  val draft=PrefsDraft.from(base,emptyList())
  assertEquals(0,draft.changes(base).length()) // opening and closing the page changes nothing
  val moved=draft.copy(favorites=draft.favorites.movedUp(2))
  val saved=moved.changes(base).getJSONArray("favorites")
  assertEquals(listOf(expectedIds[0],expectedIds[1],"team.future.men.xyz.new-club"),(0 until saved.length()).map{saved.getString(it)})
 }

 @Test fun catalogParsingRejectsWhatItCannotUse(){
  assertNull(parseCatalog(JSONObject("{}")))
  val partial=parseCatalog(JSONObject("""{"teams":[{"name":"No id"},{"id":"team.a.men.b.c","name":"A","sport":"S"}],"leagues":[]}"""))!!
  assertEquals(listOf("team.a.men.b.c"),partial.teams.map{it.id})
  assertFalse(partial.teams[0].schedule)
 }
}
