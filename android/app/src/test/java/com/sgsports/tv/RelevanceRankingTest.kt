package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Phase 8 ordering and parsing of the backend's versioned relevance hint, on synthetic events. */
class RelevanceRankingTest {
 private val installed by lazy{parseTeams(JSONObject(TestAssets.file("favorites.json").readText()))}
 private val vols by lazy{installed.first{it.id=="team.football.men.ncaa.tennessee-volunteers"}}
 @org.junit.Before fun needsRoster(){TestAssets.assumePrivate()}
 private fun rel(score:Int?,code:String="close_late")=score?.let{Relevance(it,listOf(RelevanceReason(code,"One-score game late")))}
 private fun game(id:String,a:String,b:String,score:Int?,phase:String="live",start:Long=1_000_000,notable:Boolean=true,isGame:Boolean=true):Game{
  val raw=JSONObject().put("start",start).put("sides",JSONArray(listOf(a,b)))
  val ls=LiveScore(a,b,"7","3",null,null,"in","Q4",4,"4:00",verifiedLive=true,likelyReplay=false,notable=notable,relevance=rel(score))
  return Game(id,"$a vs $b","NCAA","Football","",raw=raw,live=ls,isGame=isGame,eventPhase=phase)
 }
 private fun worth(vararg g:Game,favs:List<Team> = listOf(vols))=homeSections(g.toList(),favs).worthWatching.map{it.id}

 @Test fun higherScoreFirstThenCanonicalKeyForTies(){
  val ids=listOf(game("bb","texas","oklahoma",30),game("hi","georgia","florida",70),game("aa","ohio","michigan",30))
  // michigan|ohio sorts before oklahoma|texas, whatever the source order.
  assertEquals(listOf("hi","aa","bb"),worth(*ids.toTypedArray()))
  assertEquals(worth(*ids.toTypedArray()),worth(*ids.reversed().toTypedArray()))
 }

 @Test fun noRelevanceKeepsSourceOrder(){
  assertEquals(listOf("z","y","x"),worth(game("z","ohio","michigan",null),game("y","georgia","florida",null),game("x","texas","oklahoma",null)))
 }

 @Test fun favoritesStayAheadAtEveryScoreAndNeverAppearInWorthWatching(){
  val fav=game("fav","tennessee","alabama",0)
  val top=game("top","georgia","florida",100)
  val s=homeSections(listOf(top,fav),listOf(vols))
  assertEquals(listOf("fav"),s.favoriteLive.map{it.id})
  assertEquals(listOf("top"),s.worthWatching.map{it.id})
  assertEquals(listOf("fav","top"),(s.favoriteLive+s.worthWatching).map{it.id})
 }

 @Test fun rankingNeverAdmitsIneligibleGames(){
  val ineligible=listOf(game("rep","georgia","florida",100,phase="replay"),game("fin","texas","oklahoma",100,phase="final"),
   game("now","ohio","michigan",100,phase="on_now"),game("show","a","b",100,phase="show"),game("pp","c","d",100,phase="postponed"),
   game("flag","e","f",100,notable=false))
  assertTrue(worth(*ineligible.toTypedArray()).isEmpty())
 }

 @Test fun duplicatesCollapseButDoubleheadersSurvive(){
  val a=game("a","ohio","michigan",40);val dup=game("a2","michigan","ohio",90,start=1_000_000+300)
  assertEquals(listOf("a2"),worth(a,dup))
  val g1=game("g1","ohio","michigan",40);val g2=game("g2","ohio","michigan",40,start=1_000_000+4*3600)
  assertEquals(listOf("g1","g2"),worth(g1,g2))
 }

 @Test fun parsesVersionedRelevanceAndIgnoresUnknowns(){
  val ok=parseRelevance(JSONObject("""{"version":1,"score":72,"reasons":[{"code":"close_late","label":"One-score game late"},{"code":"nope","label":"Invented"},{"code":"rivalry","label":"Rivalry game"},{"code":"knockout","label":"Third"}]}"""))!!
  assertEquals(72,ok.score)
  assertEquals(listOf("close_late","rivalry"),ok.reasons.map{it.code})
  assertNull(parseRelevance(null))
  assertNull(parseRelevance(JSONObject("""{"version":2,"score":72,"reasons":[]}""")))
  assertNull(parseRelevance(JSONObject("""{"score":72,"reasons":[]}""")))
  assertNull(parseRelevance(JSONObject("""{"version":1,"score":500,"reasons":[]}""")))
  assertNull(parseRelevance(JSONObject("""{"version":1,"score":"x"}""")))
  assertTrue(parseRelevance(JSONObject("""{"version":1,"score":10,"reasons":[{"code":"rivalry","label":""}]}"""))!!.reasons.isEmpty())
 }

 @Test fun liveScoreParsesOptionalRelevanceAndOlderBackendsStillWork(){
  val base="""{"home":"A","away":"B","state":"in","detail":"Q4","verified_live":true,"notable":true"""
  val withRel=liveScore(JSONObject("""{"live":$base,"relevance":{"version":1,"score":30,"reasons":[{"code":"close_late","label":"One-score game late"}]}}}"""))!!
  assertEquals(30,withRel.relevance?.score)
  val legacy=liveScore(JSONObject("""{"live":$base}}"""))!!
  assertNull(legacy.relevance);assertTrue(legacy.notable)
 }
}
