package com.sgsports.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Phase 5A: LVBP schedule events parse into plain-language evidence with no stream telemetry. */
class LvbpModelTest {
 private fun event(coverage:String="possible",phase:String="upcoming",extra:String="")=JSONObject("""{
  "id":"lvbp-abc","title":"Venezuela LVBP · Leones del Caracas @ Tiburones de La Guaira","matchup":"Leones del Caracas @ Tiburones de La Guaira",
  "league":"Venezuela LVBP","sport":"Baseball","is_game":true,"start":1792100000,"event_phase":"$phase","sides":[],"feeds":[],
  "lvbp":{"coverage":"$coverage","label":"BACKEND LABEL IS IGNORED","detail":"The league lists Televen for this game.","broadcasters":["Televen"],
   "venue":"Estadio Universitario","game_number":null,"schedule":{"state":"fresh","checked_at":1792099000}$extra}}""")
 private fun game(e:JSONObject)=Game(e.optString("id"),e.optString("matchup"),e.optString("league"),e.optString("sport"),"",raw=e,eventPhase=e.optString("event_phase").ifBlank{null})

 @Test fun baseballBrowsingListsLvbp() {
  assertTrue(competitions.getValue("Baseball").contains(LVBP))
  assertEquals("Venezuela LVBP",LVBP)
 }

 @Test fun coverageLabelsUseOnlyTheThreeApprovedPhrases() {
  assertEquals("Confirmed feed",game(event("confirmed")).lvbp()!!.coverage.label())
  assertEquals("Possible feed",game(event("possible")).lvbp()!!.coverage.label())
  assertEquals("No verified feed",game(event("none")).lvbp()!!.coverage.label())
  assertEquals("unknown backend value fails safe",LvbpCoverage.NONE,game(event("verified-live-100%")).lvbp()!!.coverage)
  assertEquals(listOf("Confirmed feed","Possible feed","No verified feed"),LvbpCoverage.values().map {it.label()})
 }

 @Test fun parsesBroadcastersVenueAndNulls() {
  val ev=game(event()).lvbp()!!
  assertEquals(listOf("Televen"),ev.broadcasters)
  assertEquals("Estadio Universitario",ev.venue)
  assertNull(ev.gameNumber)
  assertEquals("fresh",ev.scheduleState)
  assertEquals(1792099000L,ev.scheduleCheckedAt)
  val nulls=JSONObject("""{"league":"Venezuela LVBP","lvbp":{"coverage":"none","detail":"","broadcasters":[],"venue":null,"game_number":2}}""")
  val parsed=parseLvbp(nulls)!!
  assertNull("JSON null venue is absent, not the text null",parsed.venue)
  assertEquals(2,parsed.gameNumber)
  assertEquals("missing schedule block reads as unavailable","unavailable",parsed.scheduleState)
  assertNull(parseLvbp(JSONObject("""{"league":"MLB"}""")))
  assertNull("only LVBP games carry evidence",Game("x","t","MLB","Baseball","",raw=event()).lvbp())
 }

 @Test fun scheduleFreshnessIsPlainLanguageAndNeverFreshForever() {
  val now=2_000_000L
  assertEquals("Schedule checked just now",lvbpFreshness("fresh",now-10,now))
  assertEquals("Schedule checked 5 min ago",lvbpFreshness("fresh",now-300,now))
  assertEquals("Schedule checked 3 h ago",lvbpFreshness("fresh",now-3*3600,now))
  assertEquals("Schedule may be out of date · last checked 9 h ago",lvbpFreshness("stale",now-9*3600,now))
  assertEquals("server says fresh but the client clock says old","Schedule may be out of date · last checked 9 h ago",lvbpFreshness("fresh",now-9*3600,now))
  assertEquals("Schedule may be out of date · last checked 2 d ago",lvbpFreshness("stale",now-2*86400,now))
  assertEquals("Schedule unavailable right now",lvbpFreshness("unavailable",now-60,now))
  assertEquals("Schedule unavailable right now",lvbpFreshness("fresh",null,now))
  assertEquals("future timestamps clamp","Schedule checked just now",lvbpFreshness("fresh",now+500,now))
  listOf(lvbpFreshness("stale",now-9*3600,now),lvbpFreshness("fresh",now-300,now)).forEach {text ->
   listOf("fps","bitrate","Mbps","reliab","latency","407","timeout").forEach {banned->assertFalse(text.contains(banned,true))}
  }
 }

 @Test fun scheduleBlockParses() {
  val parsed=parseLvbpSchedule(JSONObject("""{"state":"stale","checked_at":123}"""))!!
  assertEquals(LvbpSchedule("stale",123L),parsed)
  assertEquals(LvbpSchedule("unavailable",null),parseLvbpSchedule(JSONObject("{}")))
  assertNull(parseLvbpSchedule(null))
 }

 @Test fun scheduleStatusesNeverReadAsReplayOrLive() {
  assertEquals(GamePhase.UPCOMING,game(event(phase="upcoming")).phase())
  assertEquals(GamePhase.UPCOMING,game(event(phase="pregame")).phase())
  assertEquals(GamePhase.ON_NOW,game(event(phase="on_now")).phase())
  assertEquals(GamePhase.POSTPONED,game(event(phase="postponed")).phase())
  assertEquals(GamePhase.CANCELED,game(event(phase="canceled")).phase())
  assertEquals("a suspended game is not a replay",GamePhase.SUSPENDED,game(event(phase="suspended")).phase())
  assertEquals("SUSPENDED",game(event(phase="suspended")).phaseLabel())
  assertEquals(GameBucket.UPCOMING,bucket(game(event(phase="upcoming"))))
  assertEquals(GameBucket.ON_NOW,bucket(game(event(phase="on_now"))))
  for(phase in listOf("postponed","canceled","suspended"))assertEquals(phase,GameBucket.OFF,bucket(game(event(phase=phase))))
  assertNull("no clock before first pitch",game(event(phase="upcoming")).clockText())
  assertNull("no clock for a suspended game",game(event(phase="suspended")).clockText())
 }

 @Test fun evidenceModelCarriesNoTelemetry() {
  val fields=(LvbpEvidence::class.java.declaredFields+LvbpSchedule::class.java.declaredFields).map {it.name.lowercase()}
  listOf("bitrate","fps","latency","width","height","reliab","quality","resolution","provider","stream").forEach {banned->
   assertTrue("LVBP evidence must not expose $banned",fields.none {it.contains(banned)})
  }
  // Even if a misbehaving backend put telemetry inside the block, the parsed evidence has nowhere to keep it.
  val text=game(event(extra=""","height":1080,"fps":60,"bitrate_mbps":8,"reliability_percent":97""")).lvbp().toString()
  listOf("1080","fps","bitrate","reliab").forEach {assertFalse(it,text.contains(it,true))}
 }

 @Test fun gameIdentityIsStableAcrossRefreshForFocusRestore() {
  val a=game(event());val b=game(event(coverage="confirmed"))
  assertEquals("game:${a.id}","game:${b.id}")
  assertEquals("lvbp-abc",a.id)
 }
}
