package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Home placement for the active profile's favorites, on synthetic provider-neutral events. */
class FavoriteSectionsTest {
 private val installed by lazy{parseTeams(JSONObject(TestAssets.file("favorites.json").readText()))}
 private val vols by lazy{installed.first{it.id=="team.football.men.ncaa.tennessee-volunteers"}}
 @org.junit.Before fun needsRoster(){TestAssets.assumePrivate()}
 private val prefsWith:(List<String>?)->PrefsUi={ids->PrefsUi.Ready(TvSettingsData(1,"Test",JSONObject().apply{if(ids!=null)put("favorites",JSONArray(ids))}))}
 private fun live(home:String,away:String,notable:Boolean)=LiveScore(home,away,"7","3",null,null,"in","Q2",2,"4:00",verifiedLive=true,likelyReplay=false,notable=notable)
 private fun event(id:String,title:String,sides:List<String>?,start:Long=1_000_000,notable:Boolean=false,sport:String="Football",league:String="NCAA",isGame:Boolean=true,names:Pair<String,String>?=null):Game{
  val raw=JSONObject().put("start",start)
  if(sides!=null)raw.put("sides",JSONArray(sides))
  val ls=names?.let{live(it.first,it.second,notable)}?:live("x","y",notable).takeIf{notable}
  return Game(id,title,league,sport,"",raw=raw,live=ls,isGame=isGame,eventPhase="live")
 }

 @Test fun favoriteIsMyTeamsAndNeverAlsoWorthWatching(){
  val vsAlabama=event("a","Tennessee vs Alabama",listOf("tennessee","alabama"),notable=true)
  val other=event("b","Ohio State vs Michigan",listOf("ohio state","michigan"),notable=true)
  val s=homeSections(listOf(vsAlabama,other),listOf(vols))
  assertEquals(listOf("a"),s.favoriteLive.map{it.id})
  assertEquals(listOf("b"),s.worthWatching.map{it.id})
 }

 @Test fun homeAndAwayReversalAreBothFavoriteEvents(){
  val home=event("a","Alabama at Tennessee",listOf("alabama","tennessee"),notable=true)
  val away=event("b","Tennessee at Alabama",listOf("tennessee","alabama"),start=2_000_000,notable=true)
  val s=homeSections(listOf(home,away),listOf(vols))
  assertEquals(listOf("a","b"),s.favoriteLive.map{it.id})
  assertTrue(s.worthWatching.isEmpty())
 }

 @Test fun canonicalParticipantsDecideWhenTheTitleDoesNot(){
  // The display title omits the team name entirely; the backend's canonical sides still identify it.
  val g=event("a","Rocky Top Rivalry",listOf("tennessee","alabama"),notable=true)
  assertFalse(matches(vols,g))
  assertTrue(isFavoriteEvent(vols,g))
  assertTrue(homeSections(listOf(g),listOf(vols)).worthWatching.isEmpty())
 }

 @Test fun scoreBlockNamesIdentifyAFavoriteWhenNoSidesAreSent(){
  val g=event("a","Rivalry game",null,notable=true,names="Tennessee Volunteers" to "Alabama")
  assertTrue(isFavoriteEvent(vols,g))
 }

 @Test fun duplicateCardsOfOneEventShowOnceAndNeverUnderWorthWatching(){
  val first=event("a","Tennessee vs Alabama",listOf("tennessee","alabama"),start=1_000_000,notable=true)
  val dup=event("a2","Alabama at Tennessee",listOf("alabama","tennessee"),start=1_000_000+600,notable=true)
  val s=homeSections(listOf(first,dup),listOf(vols))
  assertEquals(listOf("a"),s.favoriteLive.map{it.id})
  assertTrue(s.worthWatching.isEmpty())
 }

 @Test fun aNonFavoriteDuplicatePairCollapsesToOneWorthWatchingCard(){
  val a=event("a","Ohio State vs Michigan",listOf("ohio state","michigan"),notable=true)
  val b=event("b","Michigan at Ohio State",listOf("michigan","ohio state"),start=1_000_000+300,notable=true)
  assertEquals(listOf("a"),homeSections(listOf(a,b),listOf(vols)).worthWatching.map{it.id})
 }

 @Test fun doubleheaderGamesStayDistinct(){
  val g1=event("a","Tennessee vs Alabama",listOf("tennessee","alabama"),start=1_000_000)
  val g2=event("b","Tennessee vs Alabama",listOf("tennessee","alabama"),start=1_000_000+4*3600)
  assertEquals(listOf("a","b"),homeSections(listOf(g1,g2),listOf(vols)).favoriteLive.map{it.id})
 }

 @Test fun sportAndLeagueScopeKeepSoftballAndSoccerApart(){
  val softball=event("s","Tennessee vs Alabama",listOf("tennessee","alabama"),sport="Softball")
  assertFalse(isFavoriteEvent(vols,softball))
  val otherLeague=event("o","Tennessee vs Alabama",listOf("tennessee","alabama"),league="Other")
  assertFalse(isFavoriteEvent(vols,otherLeague))
  val sameKeyDifferentTeam=event("t","Tennessee State vs Alabama State",listOf("tennessee state","alabama state"))
  assertFalse(isFavoriteEvent(vols,sameKeyDifferentTeam))
 }

 @Test fun profileFavoritesIsolateAndEmptyMeansNone(){
  assertEquals(listOf(vols.id),activeFavoriteTeams(installed,prefsWith(listOf(vols.id))).map{it.id})
  assertTrue(activeFavoriteTeams(installed,prefsWith(emptyList())).isEmpty())
  // No saved field, or no prefs yet: the installed list stands, as before.
  assertEquals(installed.size,activeFavoriteTeams(installed,prefsWith(null)).size)
  assertEquals(installed.size,activeFavoriteTeams(installed,PrefsUi.Idle).size)
  // Unknown IDs are ignored; a profile without Tennessee gets no Tennessee placement.
  val other=activeFavoriteTeams(installed,prefsWith(listOf("team.unknown",installed.first{it.id.contains("barcelona")}.id)))
  val g=event("a","Tennessee vs Alabama",listOf("tennessee","alabama"),notable=true)
  val s=homeSections(listOf(g),other)
  assertTrue(s.favoriteLive.isEmpty());assertEquals(listOf("a"),s.worthWatching.map{it.id})
  assertTrue(homeSections(listOf(g),emptyList()).favoriteLive.isEmpty())
 }
}
