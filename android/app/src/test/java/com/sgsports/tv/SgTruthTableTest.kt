package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Truth tables for the state-honest labels the Phase 2 foundation seam depends on. */
class SgTruthTableTest {
 private fun score(state:String?="in",live:Boolean=false,replay:Boolean=false,clock:String?="8:42",period:Int?=3,detail:String="3rd Quarter",checked:Long?=null,home:String="Tennessee",away:String="Oklahoma",homeRank:Int?=null,awayRank:Int?=null)=
  LiveScore(home,away,"17","14",homeRank,awayRank,state,detail,period,clock,verifiedLive=live,likelyReplay=replay,notable=false,checkedAt=checked)
 private fun game(sport:String="Football",live:LiveScore?=null,replay:Boolean=false,fixture:Boolean=false,scheduled:Boolean=false,airing:Boolean=false,isGame:Boolean=true)=
  Game("g","Tennessee vs Oklahoma","NCAA",sport,"",replay=replay,fixture=fixture,scheduled=scheduled,airing=airing,live=live,isGame=isGame)

 @Test fun phaseTruthTable() {
  assertEquals(GamePhase.REPLAY,game(replay=true).phase())
  assertEquals("likelyReplay beats verifiedLive",GamePhase.REPLAY,game(live=score(live=true,replay=true)).phase())
  assertEquals(GamePhase.LIVE,game(live=score(live=true)).phase())
  assertEquals("verified live beats fixture",GamePhase.LIVE,game(fixture=true,live=score(live=true)).phase())
  assertEquals(GamePhase.UPCOMING,game(fixture=true).phase())
  assertEquals(GamePhase.UPCOMING,game(live=score(state="pre")).phase())
  assertEquals(GamePhase.REPLAY,game(scheduled=true).phase())
  assertEquals(GamePhase.ON_NOW,game(scheduled=true,isGame=false).phase())
  assertEquals(GamePhase.FINAL,game(live=score(state="post")).phase())
  assertEquals(GamePhase.REPLAY,game(live=score(state="post"),airing=true).phase())
  assertEquals(GamePhase.REPLAY,game(airing=true).phase())
  assertEquals(GamePhase.ON_NOW,game(airing=true,isGame=false).phase())
  assertEquals(GamePhase.ON_NOW,game().phase())
 }

 @Test fun bucketTruthTable() {
  assertEquals(GameBucket.SHOW,bucket(game(live=score(live=true),isGame=false)))
  assertEquals(GameBucket.LIVE,bucket(game(live=score(live=true))))
  assertEquals(GameBucket.UPCOMING,bucket(game(fixture=true)))
  assertEquals(GameBucket.REPLAY,bucket(game(live=score(state="post"))))
  assertEquals(GameBucket.REPLAY,bucket(game(replay=true)))
  assertEquals(GameBucket.REPLAY,bucket(game()))
 }

 @Test fun clockIsBackendSuppliedOrExplicitlyUnavailable() {
  assertEquals("Q3 · 8:42",game(live=score(live=true)).clockText())
  assertEquals("Clock unavailable",game(live=score(live=true,clock=null)).clockText())
  assertEquals("Clock unavailable",game(live=score(live=true,clock="  ")).clockText())
  assertEquals("8:42",game(sport="Soccer",live=score(live=true)).clockText())
  assertEquals("Bottom 7th",game(sport="Baseball",live=score(live=true,detail="Bottom 7th",clock="Bot 7th")).clockText())
  assertEquals("Clock unavailable",game(sport="Baseball",live=score(live=true,detail="",clock="0:00")).clockText())
  assertNull("replay never shows a clock",game(live=score(replay=true)).clockText())
  assertNull("final never shows a clock",game(live=score(state="post")).clockText())
  assertNull("upcoming never shows a clock",game(fixture=true).clockText())
 }

 @Test fun freshnessBoundaries() {
  val now=10_000L
  fun at(age:Long)=score(checked=now-age)
  assertNull(score(checked=null).freshness(now))
  assertEquals("Updated just now",at(0).freshness(now))
  assertEquals("Updated just now",at(4).freshness(now))
  assertEquals("Updated 5s ago",at(5).freshness(now))
  assertEquals("Updated 119s ago",at(119).freshness(now))
  assertEquals("Last update 2 min ago",at(120).freshness(now))
  assertEquals("Last update 2 min ago",at(179).freshness(now))
  assertEquals("Last update 60 min ago",at(3600).freshness(now))
  assertEquals("future timestamps clamp",  "Updated just now",score(checked=now+500).freshness(now))
  assertFalse(score(checked=null).isStale(now))
  assertFalse(at(119).isStale(now))
  assertTrue(at(120).isStale(now))
 }

 private fun feed(vararg pairs:Pair<String,Any>)=Feed("n","P","l","",JSONObject().also{o->pairs.forEach{(k,v)->o.put(k,v)}})
 @Test fun feedTrustLabels() {
  assertEquals("Best match",feed("content_verified" to true).trust().label())
  assertEquals("Best match",feed("availability" to "content_verified").trust().label())
  assertEquals("Verified wins over failed",FeedTrust.VERIFIED,feed("content_verified" to true,"availability" to "probe_failed").trust())
  assertEquals("Available",feed("transport_verified" to true).trust().label())
  assertEquals("Available",feed("availability" to "stream_healthy").trust().label())
  assertEquals("Unverified listing",feed("availability" to "scheduled_not_probed").trust().label())
  assertEquals("Unverified listing",feed("evidence" to "published_guide_event").trust().label())
  assertEquals("Unverified listing",feed().trust().label())
  assertEquals("Unavailable",feed("availability" to "probe_failed").trust().label())
 }

 @Test fun feedFocusIdIgnoresListPosition() {
  val a=Feed("Main","ProviderA","","",JSONObject().put("stream_id","1"))
  val b=Feed("Main","ProviderA","","",JSONObject().put("stream_id","2"))
  assertEquals(listOf(a,b).map{it.focusId()},listOf(b,a).map{it.focusId()}.reversed())
  assertNotEquals(a.focusId(),b.focusId())
 }

 @Test fun logoOrderFollowsHomeAwayDesignation() {
  assertEquals(listOf("o.png","t.png"),orderLogosForScore("Tennessee","t.png","Oklahoma","o.png",score()))
  assertEquals("away-first stays",listOf("t.png","o.png"),orderLogosForScore("Oklahoma","t.png","Tennessee","o.png",score()))
  assertEquals("no live data",listOf("t.png","o.png"),orderLogosForScore("Tennessee","t.png","Oklahoma","o.png",null))
  assertEquals("ambiguous name keeps order",listOf("t.png","o.png"),orderLogosForScore("Texas","t.png","Oklahoma","o.png",score(home="Texas Tech",away="Texas")))
  assertEquals("missing logo",listOf("t.png"),orderLogosForScore("Tennessee","t.png","Oklahoma","",score()))
  assertEquals("case-insensitive",listOf("o.png","t.png"),orderLogosForScore("TENNESSEE","t.png","oklahoma","o.png",score()))
 }

 @Test fun rankFollowsTheNamedSide() {
  val live=score(homeRank=8,awayRank=null)
  assertEquals(8,rankFor("Tennessee",live))
  assertNull(rankFor("Oklahoma",live))
  assertNull(rankFor("Georgia",live))
  assertNull(rankFor("",live))
  assertNull(rankFor("Tennessee",null))
 }

 private fun served(phase:String,live:LiveScore?=null,isGame:Boolean=true,scheduled:Boolean=false,airing:Boolean=false)=
  Game("g","Tennessee vs Oklahoma","NCAA","Football","",live=live,isGame=isGame,scheduled=scheduled,airing=airing,eventPhase=phase)

 @Test fun serverPhaseIsAuthoritativeAndNeverInferredFromMissingEspn() {
  // The old inference called any scheduled or airing game with no ESPN data a REPLAY.
  assertEquals(GamePhase.UPCOMING,served("upcoming",scheduled=true).phase())
  assertEquals(GamePhase.UPCOMING,served("pregame",scheduled=true).phase())
  assertEquals("PREGAME",served("pregame",scheduled=true).phaseLabel())
  assertEquals(GamePhase.ON_NOW,served("on_now",airing=true).phase())
  assertEquals(GamePhase.LIVE,served("live",score(live=true),airing=true).phase())
  assertEquals(GamePhase.FINAL,served("final",score(state="post")).phase())
  assertEquals(GamePhase.POSTPONED,served("postponed",score(state="post")).phase())
  assertEquals(GamePhase.CANCELED,served("canceled").phase())
  assertEquals(GamePhase.REPLAY,served("replay",airing=true).phase())
  assertEquals("a replay never reads as live even if the nested flag leaked",GamePhase.REPLAY,served("replay",score(live=true)).phase())
  assertEquals(GamePhase.ON_NOW,served("show",isGame=false).phase())
  assertEquals("unknown value falls back to inference",GamePhase.REPLAY,served("bogus",airing=true).phase())
 }

 @Test fun serverPhaseBuckets() {
  assertEquals(GameBucket.UPCOMING,bucket(served("pregame",scheduled=true)))
  assertEquals(GameBucket.ON_NOW,bucket(served("on_now",airing=true)))
  assertEquals(GameBucket.LIVE,bucket(served("live",score(live=true))))
  assertEquals(GameBucket.REPLAY,bucket(served("replay")))
  assertEquals(GameBucket.SHOW,bucket(served("show",isGame=false)))
  assertNull("on now shows no clock",served("on_now",airing=true).clockText())
  assertNull("postponed shows no clock",served("postponed",score(state="post")).clockText())
 }

 @Test fun espnVenueAndNetworksAreParsedAndShown() {
  val live=liveScore(JSONObject("{\"live\":{\"home\":\"A\",\"away\":\"B\",\"state\":\"in\",\"detail\":\"\",\"venue\":\"Neyland Stadium\",\"networks\":[\"ESPN\",\"SEC Network\"]}}"))!!
  assertEquals("Neyland Stadium",live.venue)
  assertEquals(listOf("ESPN","SEC Network"),live.networks)
  assertEquals(" · Neyland Stadium · ESPN, SEC Network",venueNetworks(live))
  assertEquals("",venueNetworks(null))
  assertEquals("",venueNetworks(score()))
 }

 @Test fun teamKeyMatchesBackendCanonicalization() {
  assertEquals("espanyol",teamKey("RCD Español de Barcelona"))
  assertEquals("espanyol",teamKey("Espanyol"))
  assertEquals("barcelona",teamKey("FC Barcelona"))
  assertEquals("alaves",teamKey("Alavés"))
  assertEquals("tennessee",teamKey("Tennessee Volunteers"))
 }

 @Test fun espanyolAndBarcelonaNeverShareALogo() {
  val index=mapOf("barcelona" to ("Barcelona" to "bcn.png"),"espanyol" to ("Espanyol" to "esp.png"),"getafe" to ("Getafe" to "get.png"))
  assertEquals(listOf("esp.png","get.png"),lookupIdentities(listOf("espanyol","getafe"),index,emptyMap()).map{it.second})
  assertEquals(listOf("bcn.png","get.png"),lookupIdentities(listOf("barcelona","getafe"),index,emptyMap()).map{it.second})
  assertEquals("unknown side is simply absent, never a substring guess",listOf("esp.png"),lookupIdentities(listOf("espanyol","nobody"),index,emptyMap()).map{it.second})
  assertEquals(listOf("esp.png"),lookupIdentities(listOf("espanyol","espanyol"),index,emptyMap()).map{it.second})
  assertEquals("Espanyol vs Getafe",neutralizeEspanyol("RCD Español de Barcelona vs Getafe"))
  assertEquals("Espanyol vs Getafe",neutralizeEspanyol("Espanyol de Barcelona vs Getafe"))
  assertEquals("Spanish-language label is left alone","DAZN Español",neutralizeEspanyol("DAZN Español"))
 }

 @Test fun feedModelCarriesNoQualityTelemetry() {
  val fields=Feed::class.java.declaredFields.map {it.name.lowercase()}
  listOf("quality","reliab","bitrate","fps","latency","width","height").forEach { banned ->
   assertTrue("Feed must not expose $banned",fields.none {it.contains(banned)})
  }
  val trusts=FeedTrust.values().map {it.label()}
  assertEquals(listOf("Best match","Available","Unverified listing","Unverified listing","Unavailable"),trusts)
 }
}
