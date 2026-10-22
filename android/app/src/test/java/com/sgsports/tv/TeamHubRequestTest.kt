package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** /tv/team transition contract: canonical team_id is authoritative; legacy_team_index is only an explicit, asset-declared field for the deployed backend. */
class TeamHubRequestTest {
 private val teams by lazy{parseTeams(JSONObject(TestAssets.file("favorites.json").readText()))}
 @org.junit.Before fun needsRoster(){TestAssets.assumePrivate()}

 @Test fun everyInstalledFavoriteSendsItsOwnIdAndMatchingLegacyIndex(){
  val expected=listOf("team.soccer.men.la-liga.fc-barcelona","team.football.men.ncaa.tennessee-volunteers","team.softball.women.ncaa.tennessee-volunteers",
   "team.baseball.men.mlb.st-louis-cardinals","team.football.men.nfl.kansas-city-chiefs","team.baseball.men.mlb.houston-astros","team.football.men.ncaa.missouri-tigers")
  expected.forEachIndexed{i,id->
   val b=teamHubRequest(teams,id)
   assertEquals(id,b.getString("team_id"));assertEquals(i,b.getInt("team_index"));assertEquals(2,b.length())
  }
 }

 @Test fun reorderedCatalogKeepsEachTeamsDeclaredIndex(){
  val reordered=teams.reversed()
  for(t in teams){val b=teamHubRequest(reordered,t.id);assertEquals(t.id,b.getString("team_id"));assertEquals(t.legacyIndex,b.getInt("team_index"))}
 }

 @Test fun unknownOrUndeclaredTeamsSendIdOnlyNeverAGuessedIndex(){
  val unknown=teamHubRequest(teams,"team.baseball.men.mlb.seattle-mariners")
  assertEquals("team.baseball.men.mlb.seattle-mariners",unknown.getString("team_id"));assertFalse(unknown.has("team_index"))
  val noIndex=teams.map{if(it.id.endsWith("houston-astros"))it.copy(legacyIndex=null) else it}
  assertFalse(teamHubRequest(noIndex,"team.baseball.men.mlb.houston-astros").has("team_index"))
  // Same display name, different sport: Tennessee football and softball stay distinct.
  assertNotEquals(teamHubRequest(teams,"team.football.men.ncaa.tennessee-volunteers").getInt("team_index"),teamHubRequest(teams,"team.softball.women.ncaa.tennessee-volunteers").getInt("team_index"))
 }
}
