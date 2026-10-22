package com.sgsports.tv

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** [id] is the backend catalog's canonical ID. [banner] names the bundled banner artwork, [anyLeague] matches programmes of this team under any league label, [fixtureFeed] marks the team whose precise schedule arrives as fixtures instead of next-games. */
data class Team(val id:String,val name:String,val sport:String,val league:String,val logo:String,val color:Long,val secondary:Long,val pattern:String="",val banner:String=id,val anyLeague:Boolean=false,val fixtureFeed:Boolean=false,val colorKnown:Boolean=true,val legacyIndex:Int?=null){
 // matches() below runs across every program x every favorite team, every time Home
 // recomposes (on every visit, not just once) -- Regex(pattern) compiles fresh each call, and
 // that compile cost (not the match itself) is what made entering Home specifically slow.
 // Compiling once per team, lazily, turns that into a one-time cost.
 val compiled:Regex by lazy(LazyThreadSafetyMode.NONE){Regex(pattern,RegexOption.IGNORE_CASE)}
}
data class LiveScore(val home:String,val away:String,val homeScore:String?,val awayScore:String?,val homeRank:Int?,val awayRank:Int?,val state:String?,val detail:String,val period:Int?,val displayClock:String?,val verifiedLive:Boolean,val likelyReplay:Boolean,val notable:Boolean,val checkedAt:Long?=null,val venue:String="",val networks:List<String> = emptyList(),val relevance:Relevance?=null)
/** conferences is the backend's guide.conferences() value -- empty for anything outside NCAA
 * football, or an NCAA game with no roster-recognized team; one entry per side when both teams
 * are rostered (e.g. a Big 12 team at an SEC team lists both, not just whichever is named first),
 * e.g. ["SEC"] or ["Big 12","SEC"]. */
data class Game(val id:String,val title:String,val league:String,val sport:String,val subtitle:String,val logos:List<String> = emptyList(),val conferences:List<String> = emptyList(),val replay:Boolean=false,val channels:Int=0,val fixture:Boolean=false,val scheduled:Boolean=false,val airing:Boolean=false,val raw:JSONObject=JSONObject(),val teamId:String="",val live:LiveScore?=null,val isGame:Boolean=true,val ranks:List<Int?> = emptyList(),
 /** Server-computed phase (live/pregame/upcoming/final/postponed/canceled/replay/on_now/show); null only from an older backend. */
 val eventPhase:String?=null)
/** The guide-matched path (program()) stamps "start"; the fixture/next-game path (nextGame(),
 * fixture()) stamps "kickoff" -- same epoch-seconds meaning, different source, different key.
 * 0 (neither present) sorts as "already happened", the safe default for both sorting and any
 * "starting soon" check. */
fun Game.startEpoch():Long{val s=raw.optLong("start");return if(s>0)s else raw.optLong("kickoff")}
data class Feed(val name:String,val provider:String,val language:String,val logo:String,val raw:JSONObject=JSONObject(),val timing:String="",val verification:String="")
enum class FeedTrust { VERIFIED, CHECKED, SCHEDULED, LISTED, FAILED }
/** User-facing evidence label for the Where to Watch list; ranking and suppression are unchanged. */
fun FeedTrust.label():String=when(this){FeedTrust.VERIFIED->"Best match";FeedTrust.CHECKED->"Available";FeedTrust.SCHEDULED,FeedTrust.LISTED->"Unverified listing";FeedTrust.FAILED->"Unavailable"}
fun Feed.trust():FeedTrust=when {
 raw.optBoolean("content_verified")||raw.optString("availability")=="content_verified"->FeedTrust.VERIFIED
 raw.optBoolean("transport_verified")||raw.optString("availability")=="stream_healthy"->FeedTrust.CHECKED
 raw.optString("availability")=="scheduled_not_probed"||raw.optString("evidence")=="published_guide_event"->FeedTrust.SCHEDULED
 raw.optString("availability")=="probe_failed"->FeedTrust.FAILED
 else->FeedTrust.LISTED
}
data class ScheduleCoverage(val status:String,val source:String)
/** League name the backend uses for the schedule-first Venezuelan league (Phase 5A). */
const val LVBP="Venezuela LVBP"
/** Plain-language evidence for one LVBP game. Only this vocabulary reaches the screen: the backend's
 * coverage level is mapped here and an unknown value fails safe to "No verified feed". */
enum class LvbpCoverage { CONFIRMED, POSSIBLE, NONE }
fun LvbpCoverage.label():String=when(this){LvbpCoverage.CONFIRMED->"Confirmed feed";LvbpCoverage.POSSIBLE->"Possible feed";LvbpCoverage.NONE->"No verified feed"}
data class LvbpEvidence(val coverage:LvbpCoverage,val detail:String,val broadcasters:List<String>,val venue:String?,val gameNumber:Int?,val scheduleState:String,val scheduleCheckedAt:Long?)
data class LvbpSchedule(val state:String,val checkedAt:Long?)
fun parseLvbp(e:JSONObject):LvbpEvidence? {
 val j=e.optJSONObject("lvbp")?:return null
 val coverage=when(j.optString("coverage")){"confirmed"->LvbpCoverage.CONFIRMED;"possible"->LvbpCoverage.POSSIBLE;else->LvbpCoverage.NONE}
 val schedule=j.optJSONObject("schedule")
 return LvbpEvidence(coverage,j.optString("detail"),j.strings("broadcasters"),
  if(j.isNull("venue"))null else j.optString("venue").takeIf{it.isNotBlank()},
  if(j.has("game_number")&&!j.isNull("game_number"))j.optInt("game_number") else null,
  schedule?.optString("state").orEmpty().ifBlank{"unavailable"},
  if(schedule!=null&&schedule.has("checked_at")&&!schedule.isNull("checked_at"))schedule.optLong("checked_at") else null)
}
fun parseLvbpSchedule(j:JSONObject?):LvbpSchedule? = j?.let{LvbpSchedule(it.optString("state").ifBlank{"unavailable"},if(it.has("checked_at")&&!it.isNull("checked_at"))it.optLong("checked_at") else null)}
fun Game.lvbp():LvbpEvidence?=if(league==LVBP)parseLvbp(raw) else null
/** The schedule is called fresh only for this long, whatever state the server last reported. */
const val LVBP_FRESH_SECONDS=8*3600L
fun agoLabel(seconds:Long):String{val s=seconds.coerceAtLeast(0);return when{s<60->"just now";s<3600->"${s/60} min ago";s<86400->"${s/3600} h ago";else->"${s/86400} d ago"}}
/** Last-checked / stale wording for the LVBP schedule; never exposes source or telemetry detail. */
fun lvbpFreshness(state:String,checkedAt:Long?,now:Long=System.currentTimeMillis()/1000):String {
 if(checkedAt==null||state=="unavailable")return "Schedule unavailable right now"
 val age=now-checkedAt
 return if(state=="fresh"&&age<=LVBP_FRESH_SECONDS)"Schedule checked ${agoLabel(age)}" else "Schedule may be out of date · last checked ${agoLabel(age)}"
}
data class SgState(val teams:List<Team> = emptyList(),val upcoming:List<Game> = emptyList(),val programs:List<Game> = emptyList(),val later:List<Game> = emptyList(),val feeds:List<Feed> = emptyList(),val selected:Game?=null,val leagues:Map<String,List<String>> = competitions,val coverage:Map<String,ScheduleCoverage> = emptyMap(),val loading:Boolean=false,val verifying:Boolean=false,val error:String="",val fresh:Boolean=true,val paired:Boolean=false,val language:String="English",val demo:Boolean=false,val hub:TeamHubState=TeamHubState(),val lvbpSchedule:LvbpSchedule?=null,
 /** The last request could not reach the server at all (as opposed to the server refusing it). */
 val offline:Boolean=false,
 /** Extra branding-only roster entries (colors/crests) for the hero; empty outside Design fixtures. */
 val brandTeams:List<Team> = emptyList(),
 /** Canonical IDs a proven pre-Phase-7A install adopts once as its favorites; empty for every other profile. */
 val legacyFavoriteSeed:List<String> = emptyList(),
 /** Epoch seconds when programs last loaded; 0 means never. Drives truthful stale wording. */
 val loadedAt:Long=0L,
 /** Saved-game list from the server; null until it has loaded (shown as unavailable, never as empty). */
 val recordings:RecordingsSnapshot?=null,
 /** Synced preferences of this TV's profile (Settings > Preferences). */
 val prefs:PrefsUi=PrefsUi.Idle,
 /** The backend's selectable favorites and competitions; null until loaded (the pickers then offer only the installed favorites). */
 val catalog:FavoriteCatalog?=null,
 /** Selected backend and its non-secret administration page, shown under Settings > Connection. */
 val backendUrl:String="",val adminUrl:String="",val adminCheck:AdminCheck=AdminCheck.UNKNOWN,
 /** The stream check outlasted its bounded wait; the listed results are whatever arrived (shown instead of silently dropping the progress row). */
 val verifyTimedOut:Boolean=false,val startupChecks:StartupProgress=StartupProgress(),
 /** First-run setup flow; null once finished or when this TV does not need it. */
 val onboarding:OnboardingUi?=null)
val competitions=linkedMapOf("Football" to listOf("NFL","NCAA"),"Soccer" to listOf("UEFA Champions League","Copa Libertadores","MLS","Argentina Primera División","La Liga","Premier League","Ligue 1","Bundesliga","Serie A"),"Baseball" to listOf("MLB",LVBP),"Softball" to listOf("NCAA"),"UFC" to listOf("UFC"),"Golf" to listOf("PGA Tour","LPGA","DP World Tour","LIV Golf","Majors"))
/** Matches guide.conferences()'s possible values and the league-<slug>.jpg backdrops (lowercase,
 * spaces to hyphens). SEC first, matching the "SEC comes first" promise elsewhere. */
val NCAA_CONFERENCES=listOf("SEC","Big Ten","Big 12","ACC","Pac-12","American","Mountain West","Independent")
fun JSONObject.objects(key:String):List<JSONObject> { val a=optJSONArray(key)?:return emptyList();return (0 until a.length()).mapNotNull { a.optJSONObject(it) } }
fun JSONObject.strings(key:String):List<String> { val a=optJSONArray(key)?:return emptyList();return (0 until a.length()).map { a.optString(it) } }
fun assetJson(context:Context,name:String)=context.assets.open(name).bufferedReader().use { JSONObject(it.readText()) }
fun loadTeams(context:Context):List<Team> = parseTeams(assetJson(context,"favorites.json"))
/** Installed favorites as the bundled asset declares them: every ID, banner, colour and matching rule is explicit, nothing comes from list position. */
fun parseTeams(json:JSONObject):List<Team> = json.objects("teams").map { t ->
 val sport=t.optString("sport")
 val primary=parseHexColor(t.optString("color"));val secondary=parseHexColor(t.optString("secondary"))
 Team(t.getString("id"),if(sport=="Softball")"Lady Vols" else t.optString("name"),sport,t.optString("league","UEFA Champions League"),t.optString("logo"),
  primary?:0xFF3B4859L,secondary?:0xFF18222EL,t.optString("pattern","(?i)barcelona"),
  banner=t.optString("banner",t.getString("id")),anyLeague=t.optBoolean("any_league",false),fixtureFeed=t.optBoolean("fixture_feed",false),colorKnown=primary!=null,legacyIndex=if(t.has("legacy_team_index"))t.getInt("legacy_team_index") else null)
}
/** Body of /tv/team. The canonical team_id is authoritative; legacy_team_index is an explicit, asset-declared transition field for the already-deployed backend that only reads team_index. A team without a declared index sends the ID alone, never a guessed index. */
fun teamHubRequest(teams:List<Team>,teamId:String):JSONObject=JSONObject().put("team_id",teamId).also{b->teams.firstOrNull{it.id==teamId}?.legacyIndex?.let{b.put("team_index",it)}}
// isGame excludes anything that only mentions the team by name without being an actual
// broadcast — a betting/wagering companion show ("Missouri vs. Kansas ... In-Game Live
// Gameday") titles itself exactly like a real matchup but is a studio program, not the game.
fun matches(team:Team,g:Game)=g.isGame && g.sport==team.sport && (team.anyLeague || g.league==team.league) && team.compiled.containsMatchIn(g.title)
enum class GameBucket { LIVE, UPCOMING, ON_NOW, REPLAY, SHOW, OFF }
/** Same live/replay read GameStatus already renders per-card — reused here so the row a game
 * lands in always agrees with its own badge, instead of two independently-computed opinions. */
fun bucket(g:Game):GameBucket {
 if(!g.isGame)return GameBucket.SHOW
 return when(g.phase()) {
  GamePhase.LIVE->GameBucket.LIVE
  GamePhase.UPCOMING->GameBucket.UPCOMING
  // On air with no independent evidence of what it is: neither live nor a replay. Only a backend
  // that supplies event_phase can say so; the older inference below still files it as a replay.
  GamePhase.ON_NOW->if(g.eventPhase!=null)GameBucket.ON_NOW else GameBucket.REPLAY
  // A postponed, suspended or canceled game is neither a replay nor playable.
  GamePhase.POSTPONED,GamePhase.CANCELED,GamePhase.SUSPENDED->GameBucket.OFF
  else->GameBucket.REPLAY
 }
}
fun liveScore(e:JSONObject):LiveScore? {
 val live=e.optJSONObject("live")?:return null
 fun rank(key:String)=live.optInt(key,-1).let { if(it<0)null else it }
 fun score(key:String)=live.optString(key,"").ifBlank { null }
 return LiveScore(live.optString("home"),live.optString("away"),score("home_score"),score("away_score"),
  rank("home_rank"),rank("away_rank"),live.optString("state").ifBlank { null },live.optString("detail"),
  if(live.has("period")&&!live.isNull("period"))live.optInt("period") else null,
 live.optString("display_clock").ifBlank { null },live.optBoolean("verified_live"),
  live.optBoolean("likely_replay"),live.optBoolean("notable"),
  if(live.has("checked_at")&&!live.isNull("checked_at"))live.optLong("checked_at") else null,
  live.optString("venue"),live.strings("networks"),parseRelevance(live.optJSONObject("relevance")))
}
/** Closed reason vocabulary of relevance version 1; anything else is ignored rather than invented. */
val RELEVANCE_CODES=setOf("major_ufc","ranked_matchup","knockout","close_late","rivalry")
data class RelevanceReason(val code:String,val label:String)
/** Backend ranking hint for a verified-live game. The score only orders Worth watching and is never shown. */
data class Relevance(val score:Int,val reasons:List<RelevanceReason>)
/** Defensive parse: absent, malformed or non-version-1 objects yield null, i.e. the plain boolean behavior and source order. */
fun parseRelevance(o:JSONObject?):Relevance?{
 if(o==null||o.optInt("version",-1)!=1)return null
 val score=o.optInt("score",-1).takeIf{it in 0..100}?:return null
 val arr=o.optJSONArray("reasons")
 val reasons=(0 until (arr?.length()?:0)).mapNotNull{i->
  val r=arr?.optJSONObject(i)?:return@mapNotNull null
  val code=r.optString("code");val label=r.optString("label").trim()
  if(code in RELEVANCE_CODES&&label.isNotEmpty()&&label.length<=40)RelevanceReason(code,label) else null
 }.take(2)
 return Relevance(score,reasons)
}
enum class GamePhase { UPCOMING, LIVE, FINAL, REPLAY, ON_NOW, POSTPONED, CANCELED, SUSPENDED }
fun Game.phase():GamePhase=when(eventPhase) {
 "live"->GamePhase.LIVE
 "pregame","upcoming"->GamePhase.UPCOMING
 "final"->GamePhase.FINAL
 "postponed"->GamePhase.POSTPONED
 "canceled"->GamePhase.CANCELED
 "suspended"->GamePhase.SUSPENDED
 "replay"->GamePhase.REPLAY
 "on_now","show"->GamePhase.ON_NOW
 else->inferredPhase()
}
/** Status word for badges: the server's PREGAME distinction survives, everything else is the phase name. */
fun Game.phaseLabel():String=if(eventPhase=="pregame")"PREGAME" else phase().name.replace('_',' ')
/** Fallback for an older backend that sends no event_phase. */
private fun Game.inferredPhase():GamePhase=when {
 live?.likelyReplay==true||replay->GamePhase.REPLAY
 live?.verifiedLive==true->GamePhase.LIVE
 fixture||live?.state=="pre"->GamePhase.UPCOMING
 scheduled&&isGame->GamePhase.REPLAY
 live?.state=="post"->if(airing)GamePhase.REPLAY else GamePhase.FINAL
 airing&&isGame->GamePhase.REPLAY
 else->GamePhase.ON_NOW
}
fun Game.clockLabel():String? {
 val value=live?.displayClock?.takeIf {it.isNotBlank()}?:return null
 if(phase()!=GamePhase.LIVE)return null
 return when(sport){
  "Baseball","Softball"->live?.detail?.takeIf {it.isNotBlank()&&!it.equals("Scheduled",true)&&!it.equals("Final",true)}
   ?:value.takeUnless {it=="0:00"}
  "Soccer"->value
  else->listOfNotNull(live.period?.let{"Q$it"},value).joinToString(" · ")
 }
}
/** Updates older than this are presented as stale ("Last update N min ago"). */
const val STALE_AFTER_SECONDS=120L
/** Only ever derived from a backend-supplied checkedAt; no timestamp means no freshness claim. */
fun LiveScore.freshness(now:Long=System.currentTimeMillis()/1000):String?=checkedAt?.let {checked->
 val age=(now-checked).coerceAtLeast(0)
 when {age<5->"Updated just now";age<STALE_AFTER_SECONDS->"Updated ${age}s ago";else->"Last update ${age/60} min ago"}
}
fun LiveScore.isStale(now:Long=System.currentTimeMillis()/1000):Boolean=checkedAt?.let {now-it>=STALE_AFTER_SECONDS}?:false
/** Backend-supplied clock for a live game, or an explicit gap; never a locally running clock. */
fun Game.clockText():String?=if(phase()==GamePhase.LIVE)(clockLabel()?:"Clock unavailable") else null
fun timeLabel(epoch:Long)=if(epoch==0L)"Time not listed" else SimpleDateFormat("EEE, MMM d · h:mm a",Locale.getDefault()).format(Date(epoch*1000))
/** Card art is built without regard to home/away (own team first, or whichever identity the
 * scan happens to hit first) — but Scoreboard always reads "awayScore - homeScore" from
 * ESPN's real designation. Reordering here, once real score data says which side is which, is
 * what keeps a live score from landing next to the wrong team's crest. */
fun orderLogosForScore(nameA:String,logoA:String,nameB:String,logoB:String,live:LiveScore?):List<String> {
 val logos=listOfNotNull(logoA.ifEmpty{null},logoB.ifEmpty{null})
 if(live==null||logos.size<2)return logos
 fun namesMatch(a:String,b:String)=a.isNotBlank()&&b.isNotBlank()&&(a.contains(b,ignoreCase=true)||b.contains(a,ignoreCase=true))
 val aIsHome=namesMatch(nameA,live.home);val aIsAway=namesMatch(nameA,live.away)
 return if(aIsHome&&!aIsAway)listOf(logoB,logoA) else logos
}
/** AP-style rank for whichever side a name identifies, independent of logo ordering -- pairs
 * with a Game's own (already home/away-ordered) logos by matching each logo back to the name
 * it came from, so a rank badge never ends up on the wrong team's crest. */
fun rankFor(name:String,live:LiveScore?):Int? {
 if(live==null||name.isBlank())return null
 fun matches(other:String)=other.isNotBlank()&&(name.contains(other,ignoreCase=true)||other.contains(name,ignoreCase=true))
 return when { matches(live.home)->live.homeRank; matches(live.away)->live.awayRank; else->null }
}
class GameMapper(val context:Context) {
 private val logos=assetJson(context,"teams.json")
 private val channels=assetJson(context,"channels.json")
 private val identities=context.assets.open("team-identities.json").bufferedReader().use { JSONArray(it.readText()) }
 // program() below used to scan every event's title against all 520 identities/~1,228 aliases
 // individually -- even after precompiling those regexes (instead of rebuilding them per call),
 // that's still ~373 events x up to ~1,228 separate regex scans of the title on every refresh,
 // measured at ~1.9s of main-thread work, and the actual dominant cost behind the app feeling
 // laggy. The fix is algorithmic, not just caching: one combined alternation pattern per league
 // (built once, here) turns "scan the title N times, once per alias" into "scan the title once,
 // find whichever alias (if any) is in it" -- a single regex pass per event instead of hundreds.
 private class Bucket(val pattern:Regex,val byAlias:Map<String,List<Pair<String,String>>>)
 private val leagueBuckets:Map<String,Bucket>
 private val soccerBucket:Bucket?
 // Exact canonical-key -> (name,file) per league, plus one for every soccer league.
 private val keyIndex:Map<String,Map<String,Pair<String,String>>>
 private val soccerKeyIndex:Map<String,Pair<String,String>>
 init {
  data class Raw(val name:String,val file:String,val alias:String)
  val byLeague=mutableMapOf<String,MutableList<Raw>>();val soccerAll=mutableListOf<Raw>()
  for(i in 0 until identities.length()){
   val t=identities.getJSONObject(i)
   val name=t.optString("name");val file=t.optString("file");val league=t.optString("league");val sport=t.optString("sport")
   val aliases=t.optJSONArray("aliases")?:continue
   for(j in 0 until aliases.length()){
    val raw=Raw(name,file,aliases.optString(j))
    if(sport=="soccer")soccerAll.add(raw) else byLeague.getOrPut(league){mutableListOf()}.add(raw)
   }
  }
  // Longest alias first in the alternation, so e.g. "Ohio State" wins over a shorter alias that
  // could otherwise match the same starting position first.
  fun build(entries:List<Raw>,ncaaExclusion:Boolean):Bucket{
   val sorted=entries.sortedByDescending{it.alias.length}
   val exclusion=if(ncaaExclusion)"(?![ -](?:state|tech|southern|atlantic|international|el[ -]paso|san antonio|a&m|martin))" else ""
   val pattern=Regex("(?<![a-z])(?:"+sorted.joinToString("|"){Regex.escape(it.alias)}+")(?![a-z])"+exclusion,RegexOption.IGNORE_CASE)
   return Bucket(pattern,entries.groupBy({it.alias.lowercase()},{it.name to it.file}))
  }
  fun index(entries:List<Raw>)=entries.groupBy{teamKey(it.alias)}.mapValues{(_,v)->v.first().let{it.name to it.file}}
  keyIndex=byLeague.mapValues{(_,entries)->index(entries)}
  soccerKeyIndex=index(soccerAll)
  leagueBuckets=byLeague.mapValues{(league,entries)->build(entries,league=="NCAA")}
  soccerBucket=if(soccerAll.isNotEmpty())build(soccerAll,false) else null
 }
 fun fixture(f:JSONObject,teamId:String):Game {
  val opponent=f.optString("opponent");val live=liveScore(f)
  val logoA=logos.optString("FC Barcelona");val logoB=logos.optString(opponent)
  val art=orderLogosForScore("Barcelona",logoA,opponent,logoB,live)
  val ranks=art.map { logo->when(logo){logoA->rankFor("Barcelona",live);logoB->rankFor(opponent,live);else->null} }
  return Game(f.optString("id"),(if(f.optBoolean("home",true))"Barcelona vs $opponent" else "$opponent vs Barcelona"),f.optString("competition"),"Soccer",timeLabel(f.optLong("kickoff")),art,fixture=true,raw=f,teamId=teamId,live=live,ranks=ranks,eventPhase=f.optString("event_phase").ifBlank{null})
 }
 /** Exact-name identity lookup, for opponents named by the backend rather than found in free-text EPG titles. */
 private fun logoFor(name:String):String {
  for(i in 0 until identities.length()) {
   val t=identities.getJSONObject(i);val aliases=t.optJSONArray("aliases")?:continue
   if((0 until aliases.length()).any { aliases.optString(it).equals(name,ignoreCase=true) })return t.optString("file")
  }
  return ""
 }
 /** The next scheduled game for one favorite team, from the backend's ESPN-sourced next_games list. */
 fun nextGame(g:JSONObject,team:Team):Game {
  val opponent=g.optString("opponent");val home=g.optBoolean("home",true)
  val title=if(home)"${team.name} vs $opponent" else "$opponent vs ${team.name}"
  val ownLogo=team.logo.ifEmpty { logoFor(team.name) };val oppLogo=logoFor(opponent);val live=liveScore(g)
  val art=orderLogosForScore(team.name,ownLogo,opponent,oppLogo,live)
  val ranks=art.map { logo->when(logo){ownLogo->rankFor(team.name,live);oppLogo->rankFor(opponent,live);else->null} }
  return Game(g.optString("id"),title,g.optString("league"),g.optString("sport"),timeLabel(g.optLong("kickoff")),art,fixture=true,raw=g,teamId=team.id,live=live,ranks=ranks,eventPhase=g.optString("event_phase").ifBlank{null})
 }
 fun program(e:JSONObject,teams:List<Team>,scheduled:Boolean=false):Game {
  // 'title' (competition + matchup) drives identity matching below, since team names live in
  // the matchup half; 'display' is what actually renders as the card's big title — the clean
  // matchup alone, e.g. "FC Barcelona vs Feyenoord" instead of "Champions League 26/27 FC
  // Barcelona vs Feyenoord". The already-classified league field (shown separately, e.g. "UEFA
  // Champions League") covers the competition name, so no separate competition line is needed.
  val title=e.optString("title");val display=e.optString("matchup").ifEmpty { title };val league=e.optString("league");val sport=e.optString("sport")
  // The backend's canonical sides (guide.participant_sides) pick each team by exact identity key.
  // Only an older backend without them falls back to scanning the title text for aliases.
  val sides=e.strings("sides")
  val found=mutableListOf<Pair<String,String>>()
  if(sides.size==2){
   found.addAll(lookupIdentities(sides,keyIndex[league].orEmpty(),if(sport=="Soccer")soccerKeyIndex else emptyMap()))
  } else {
   val scanned=neutralizeEspanyol(title)
   fun scan(bucket:Bucket?){bucket?.pattern?.findAll(scanned)?.forEach { m->bucket.byAlias[m.value.lowercase()]?.let{found.addAll(it)} }}
   scan(leagueBuckets[league])
   if(sport=="Soccer")scan(soccerBucket)
  }
  // 'matchup' is display text only (a real provider subtitle, a venue name, an episode title —
  // whatever split_title found), not proof of an actual game; 'is_game' is the backend's own
  // separate, stricter "Team A vs/at/@ Team B" check (see guide.is_game_matchup) that a studio
  // show or a golf broadcast named after its course doesn't accidentally satisfy.
  val isGame=e.optBoolean("is_game")
  val live=liveScore(e)
  // found's scan order reflects identity-file position, not which side is actually home/away —
  // reorder the first two matches to line up with Scoreboard's "away - home" score reading.
  val distinctFound=found.distinctBy{it.second}
  val logos=if(distinctFound.size>=2)orderLogosForScore(distinctFound[0].first,distinctFound[0].second,distinctFound[1].first,distinctFound[1].second,live)
   else distinctFound.map{it.second}
  val ranks=if(distinctFound.size>=2){
   val (nameA,logoA)=distinctFound[0];val (nameB,logoB)=distinctFound[1]
   logos.map { logo->when(logo){logoA->rankFor(nameA,live);logoB->rankFor(nameB,live);else->null} }
  } else distinctFound.map { rankFor(it.first,live) }
  val base=Game(e.optString("id"),display,league,sport,timeLabel(e.optLong("start")),logos.take(2),e.strings("conferences"),e.optBoolean("repeat"),e.optJSONArray("feeds")?.length()?:0,scheduled=scheduled,airing=!scheduled,raw=e,live=live,isGame=isGame,ranks=ranks.take(2),eventPhase=e.optString("event_phase").ifBlank{null})
  val favorite=teams.firstOrNull { matches(it,base) }
  val artwork=if(favorite?.sport=="Softball") (listOf(favorite.logo)+base.logos.filterNot {it.endsWith("college-football_2633.png")}).distinct().take(2) else base.logos
  return base.copy(teamId=favorite?.id?:"",logos=artwork)
 }
 fun feed(f:JSONObject):Feed {
  val a=f.optJSONObject("audio_language")
  val name=a?.optString("name","Unknown")?:"Unknown"
  val evidence=a?.optString("evidence",a.optString("source","listed"))?:""
  val label=if(name=="Unknown"||name.isBlank())"Audio unknown" else "$name · ${if(evidence.contains("confirm"))"confirmed" else if(evidence.contains("track"))"track reported" else "listed / inferred"}"
  val notes=mutableListOf<String>()
  if(f.optString("broadcast_kind")=="alternate")notes.add("Alternate commentary")
  if(f.optString("availability")=="scheduled_not_probed")notes.add("Scheduled listing · playback not verified")
  if(f.optBoolean("requires_date_verification"))notes.add("Schedule date needs verification")
  return Feed(f.optString("display_name"),f.optString("provider_id"),label,channels.optString(f.optString("provider_id")+"_"+f.optString("stream_id"),channelBrand(f.optString("display_name"))),f,notes.joinToString(" · "),f.optString("verification_label"))
 }
}

private val TEAM_NOISE=Regex("[^a-z0-9]+")
private val TEAM_KEY_ALIASES=mapOf(
 "barca" to "barcelona","tennessee volunteers" to "tennessee","vols" to "tennessee","tennessee lady volunteers" to "tennessee","lady vols" to "tennessee",
 "st louis cardinals" to "st louis cardinals","stl cardinals" to "st louis cardinals","kc chiefs" to "kansas city chiefs",
 "missouri tigers" to "missouri","mizzou" to "missouri",
 "rcd espanol de barcelona" to "espanyol","espanol de barcelona" to "espanyol","rcd espanyol de barcelona" to "espanyol","espanyol de barcelona" to "espanyol","espanol" to "espanyol")
/** Mirror of the backend's guide.team_key, so a canonical side from the server equals an identity alias exactly. */
fun teamKey(value:String):String{
 val folded=java.text.Normalizer.normalize(value.lowercase(Locale.ROOT),java.text.Normalizer.Form.NFKD).filter{it.code<128}
 val n=TEAM_NOISE.replace(folded," ").trim().replace(Regex("\\s+")," ").replace(Regex("^(?:rcd|fc|cf)\\s+"),"").replace(Regex("\\s+(?:fc|cf)$"),"")
 return TEAM_KEY_ALIASES[n]?:n
}
/** Identities for the backend's canonical sides, in listed order, by exact key only -- never a substring of the title. */
fun lookupIdentities(sides:List<String>,league:Map<String,Pair<String,String>>,soccer:Map<String,Pair<String,String>>):List<Pair<String,String>> =
 sides.mapNotNull{league[it]?:soccer[it]}.distinctBy{it.second}
/** Older backends: keep "RCD Español de Barcelona" from also matching the Barcelona alias. */
fun neutralizeEspanyol(title:String):String=Regex("(?i)(?:rcd\\s+espa[nñ]y?ol(?:\\s+de\\s+barcelona)?|espa[nñ]y?ol\\s+de\\s+barcelona)").replace(title,"Espanyol")
/** Brand fallback is independent of provider stream IDs. */
fun channelBrand(name:String):String = if(Regex("(?i)\\bespn\\b").containsMatchIn(name))"logos/espn.png" else if(name.contains("paramount",ignoreCase=true))"logos/paramount.png" else ""
