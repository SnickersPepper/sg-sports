package com.sgsports.tv

import org.json.JSONObject
import java.net.URI
import java.text.Normalizer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/*
 * Team Hub data model and rules (SGH-301). No Android or Compose types, so every rule is unit-tested.
 *
 * Nothing here is keyed to a team, sport or league: the backend's /tv/team answer is normalised once at this boundary
 * into independent resources ([Res]) and everything the UI shows is derived from those facts. A fact the backend did
 * not send stays unknown (null / absent), never a default: null is not zero, a missing time is "TBD", a missing link
 * is no action.
 */

/** Why a resource has no content. Distinct from "has no data" (an empty [Res.Ready]). */
enum class FailKind { OFFLINE, PERMISSION, UNAVAILABLE }

/** One independently loaded section: a failure in one never hides another. */
sealed class Res<out T> {
 object Loading:Res<Nothing>()
 /** [asOf] is when the backend answered (epoch seconds); [stale] means a later refresh failed and this is the last good copy. */
 data class Ready<out T>(val value:T,val asOf:Long?,val stale:Boolean=false):Res<T>()
 data class Failed<out T>(val kind:FailKind,val last:Ready<T>?=null):Res<T>()
}
/** True for a resource that failed and has nothing older to show. */
fun Res<*>.failedEmpty():Boolean = this is Res.Failed<*> && last==null
fun Res<*>.failKindOrNull():FailKind? = (this as? Res.Failed<*>)?.kind
/** The content to render for a resource: fresh, or the last good copy kept through a failed refresh. */
fun <T> Res<T>.content():Res.Ready<T>? = when(this){is Res.Ready->this;is Res.Failed->last;Res.Loading->null}

data class HubSnapshot(val record:String?,val rank:Int?,val standing:String?)
data class HubSide(val id:String,val name:String,val abbreviation:String,val score:String?,val rank:Int?)
enum class HubStatus { SCHEDULED, LIVE, FINAL, POSTPONED, CANCELLED, SUSPENDED, UNKNOWN }
/** [start] is epoch seconds; 0 means the provider has no confirmed start (TBD). [detail] is the provider's own status text, kept verbatim. */
data class HubEvent(val id:String,val sport:String,val league:String,val home:HubSide,val away:HubSide,val start:Long,val status:HubStatus,
 val detail:String,val venue:String,val networks:List<String>,val season:String,
 /** When the score/status was last confirmed (epoch seconds) if the source says so; null = only the hub load time is known. */
 val updatedAt:Long?=null)
data class ScheduleData(val events:List<HubEvent>,
 /** False when the backend only supplied upcoming games: absence of results is then "not provided", not "none played". */
 val hasResults:Boolean)
data class RosterPlayer(val id:String,val name:String,val jersey:String?,val position:String?,val positionName:String?)
data class RosterData(val players:List<RosterPlayer>,val injuries:List<Pair<String,String>>)
data class NewsArticle(val id:String,val headline:String,val source:String?,val published:Long?,val url:String?)

data class TeamHubData(val snapshot:Res<HubSnapshot>,val schedule:Res<ScheduleData>,val roster:Res<RosterData>,val news:Res<List<NewsArticle>>){
 companion object{
  val LOADING=TeamHubData(Res.Loading,Res.Loading,Res.Loading,Res.Loading)
  fun failed(kind:FailKind)=TeamHubData(Res.Failed(kind),Res.Failed(kind),Res.Failed(kind),Res.Failed(kind))
 }
}

/**
 * Everything the hub screen knows. [teamId] and [requestId] identify the one request whose answer may be applied:
 * a response for any other team or an older request is dropped, so one team's data can never show on another.
 */
data class TeamHubState(val teamId:String="",val requestId:Int=0,val loading:Boolean=false,val failure:FailKind?=null,val data:TeamHubData=TeamHubData.LOADING)

/** Starts a request. Last-good content is kept only when refreshing the same team; a different team starts empty. */
fun beginHubLoad(prev:TeamHubState,teamId:String,requestId:Int):TeamHubState =
 if(prev.teamId==teamId&&prev.data!==TeamHubData.LOADING)prev.copy(requestId=requestId,loading=true)
 else TeamHubState(teamId,requestId,true,null,TeamHubData.LOADING)

fun applyHubResponse(prev:TeamHubState,teamId:String,requestId:Int,json:JSONObject,now:Long):TeamHubState =
 if(prev.teamId!=teamId||prev.requestId!=requestId)prev else prev.copy(loading=false,failure=null,data=parseTeamHubData(json,now))

/** A failed request keeps the same team's last good sections, flagged stale; with nothing to keep every section reports the failure. */
fun applyHubFailure(prev:TeamHubState,teamId:String,requestId:Int,kind:FailKind):TeamHubState {
 if(prev.teamId!=teamId||prev.requestId!=requestId)return prev
 fun <T> Res<T>.afterFailure():Res<T> = when(this){
  is Res.Ready->Res.Failed(kind,copy(stale=true))
  is Res.Failed->Res.Failed(kind,last?.copy(stale=true))
  Res.Loading->Res.Failed(kind)
 }
 val d=prev.data
 return prev.copy(loading=false,failure=kind,data=TeamHubData(d.snapshot.afterFailure(),d.schedule.afterFailure(),d.roster.afterFailure(),d.news.afterFailure()))
}

// ---- Parsing -----------------------------------------------------------------------------------------------

private fun JSONObject.text(key:String):String = if(isNull(key))"" else optString(key,"").trim()
private fun JSONObject.textOrNull(key:String):String? = text(key).takeIf{it.isNotEmpty()}

/**
 * /tv/team -> independent resources. Sections the backend names in `unavailable` fail on their own; a backend that
 * predates that field simply returns empty lists, which show as "no data". `events` (full schedule) is preferred when the
 * backend provides it, else `games` (upcoming only).
 */
fun parseTeamHubData(j:JSONObject,now:Long):TeamHubData {
 val down=j.strings("unavailable").toSet()
 fun <T> section(name:String,build:()->T):Res<T> = if(name in down)Res.Failed(FailKind.UNAVAILABLE) else Res.Ready(build(),now)
 return TeamHubData(
  section("overview"){HubSnapshot(j.textOrNull("record"),if(j.isNull("rank"))null else j.optInt("rank").takeIf{it>0},j.textOrNull("standing"))},
  section("games"){
   val full=j.has("events")&&j.optJSONArray("events")!=null
   ScheduleData(dedupeEvents(j.objects(if(full)"events" else "games").mapNotNull(::parseHubEvent)),full)
  },
  section("roster"){RosterData(j.objects("roster").mapIndexedNotNull{i,p->parseRosterPlayer(p,i)}.distinctBy{it.id},j.objects("injuries").mapNotNull{
   val n=it.textOrNull("name");if(n==null)null else n to it.text("status")})},
  section("news"){prepareNews(j.objects("news").mapNotNull(::parseArticle))})
}

private fun parseSide(o:JSONObject?):HubSide {
 if(o==null)return HubSide("","","",null,null)
 return HubSide(o.text("id"),o.text("name"),o.text("abbreviation"),o.textOrNull("score"),if(o.isNull("rank"))null else o.optInt("rank").takeIf{it in 1..99})
}

/** Provider status -> one of the app's states without losing the original wording (kept in [HubEvent.detail]). */
fun hubStatus(state:String?,statusName:String?,completed:Boolean):HubStatus {
 val name=statusName.orEmpty().lowercase(Locale.ROOT)
 return when{
  "postpone" in name->HubStatus.POSTPONED
  "cancel" in name->HubStatus.CANCELLED
  "suspend" in name->HubStatus.SUSPENDED
  state=="in"->HubStatus.LIVE
  state=="post"->if(completed||"final" in name)HubStatus.FINAL else HubStatus.UNKNOWN
  state=="pre"->HubStatus.SCHEDULED
  else->HubStatus.UNKNOWN
 }
}

fun parseHubEvent(g:JSONObject):HubEvent? {
 val home=parseSide(g.optJSONObject("home"));val away=parseSide(g.optJSONObject("away"))
 if(home.name.isBlank()||away.name.isBlank())return null
 val start=g.optLong("start",0).coerceAtLeast(0)
 val status=hubStatus(g.textOrNull("state"),g.textOrNull("status_name"),g.optBoolean("completed"))
 val explicit=g.text("espn_id")
 val id=explicit.ifEmpty{"${g.text("sport")}|${g.text("league")}|${away.id.ifEmpty{away.name}}|${home.id.ifEmpty{home.name}}|$start"}
 // A score is only a fact once the game has started; a scheduled game's "0" is a provider default.
 val scored=status==HubStatus.LIVE||status==HubStatus.FINAL||status==HubStatus.SUSPENDED
 fun HubSide.visible()=if(scored)this else copy(score=null)
 return HubEvent(id,g.text("sport"),g.text("league"),home.visible(),away.visible(),start,status,g.text("detail"),g.text("venue"),
  g.strings("networks").filter{it.isNotBlank()},seasonLabel(g.textOrNull("season_slug"),g.optInt("season_type",0)))
}

/** Human label for a provider season slug, or "" when none was supplied. */
fun seasonLabel(slug:String?,type:Int):String =
 slug?.takeIf{it.isNotBlank()}?.split('-','_')?.filter{it.isNotEmpty()}?.joinToString(" "){w->w.replaceFirstChar{it.uppercase()}}.orEmpty()

/** [index] only disambiguates players the provider gave no id, so two genuine namesakes stay two players. */
fun parseRosterPlayer(p:JSONObject,index:Int=0):RosterPlayer? {
 val name=p.textOrNull("name")?:return null
 return RosterPlayer(p.text("id").ifEmpty{"$name#$index"},name,p.textOrNull("jersey"),p.textOrNull("position"),p.textOrNull("position_name"))
}

fun parseArticle(a:JSONObject):NewsArticle? {
 val headline=a.textOrNull("headline")?:return null
 val url=safeArticleUrl(a.textOrNull("url"))
 val id=a.textOrNull("id")?:url?.let(::canonicalUrlKey)?:normalizedHeadline(headline)
 return NewsArticle(id,headline,a.textOrNull("source"),parsePublished(a.opt("published")),url)
}

// ---- Events ------------------------------------------------------------------------------------------------

/** One entry per event: by id, then by the same two teams at the same start (a provider relisting under a new id). */
fun dedupeEvents(events:List<HubEvent>):List<HubEvent> {
 val ids=HashSet<String>();val pairs=HashSet<String>()
 return events.filter{e->
  val pair=listOf(e.home.id.ifEmpty{e.home.name},e.away.id.ifEmpty{e.away.name}).joinToString("|")+"@"+e.start
  ids.add(e.id)&&(e.start==0L||pairs.add(pair))
 }
}

/** The team the schedule belongs to, as a provider side id: by canonical name key first, else the only side present in every game; null when unknown. */
fun ownSideId(events:List<HubEvent>,teamName:String):String? {
 val key=teamKey(teamName)
 val byName=events.flatMap{listOf(it.home,it.away)}.filter{it.id.isNotEmpty()&&teamKey(it.name)==key}.map{it.id}.distinct()
 if(byName.size==1)return byName[0]
 if(events.size<2)return null
 val counts=events.flatMap{listOf(it.home.id,it.away.id)}.filter{it.isNotEmpty()}.groupingBy{it}.eachCount()
 val top=counts.maxByOrNull{it.value}?:return null
 return if(top.value==events.size&&counts.count{it.value==top.value}==1)top.key else null
}

/** True when [own] plays at home, false when away, null when the team is unknown (then no home/away claim is made). */
fun isHomeGame(e:HubEvent,own:String?):Boolean? = when{own==null->null;e.home.id==own->true;e.away.id==own->false;else->null}

private const val UPCOMING_GRACE_SECONDS=3*3600L

/**
 * The featured game: a live game first, then the nearest confirmed upcoming one, then the latest completed one (always
 * labelled Final by the UI). Ties break by earliest start, then id, so the choice is deterministic across refreshes
 * and doubleheaders. A postponed, cancelled, TBD or unknown-status event is never featured.
 */
fun featuredEvent(events:List<HubEvent>,now:Long):HubEvent? {
 events.filter{it.status==HubStatus.LIVE}.minWithOrNull(compareBy<HubEvent>({it.start},{it.id}))?.let{return it}
 events.filter{it.status==HubStatus.SCHEDULED&&it.start>0&&it.start>=now-UPCOMING_GRACE_SECONDS}
  .minWithOrNull(compareBy<HubEvent>({it.start},{it.id}))?.let{return it}
 return events.filter{it.status==HubStatus.FINAL}.maxWithOrNull(compareBy<HubEvent>({it.start},{it.id}))
}

/** Next games for the Overview module, soonest first, TBD last, excluding the featured game. */
fun upcomingEvents(events:List<HubEvent>,featuredId:String?,limit:Int):List<HubEvent> =
 events.filter{(it.status==HubStatus.SCHEDULED||it.status==HubStatus.POSTPONED)&&it.id!=featuredId}.sortedWith(scheduleOrder).take(limit)

/** Confirmed starts in order, then events with no confirmed start; id keeps the order stable. */
val scheduleOrder:Comparator<HubEvent> = compareBy<HubEvent>({if(it.start>0)0 else 1},{it.start},{it.id})

enum class StatusFilter(val label:String){ ALL("All"),UPCOMING("Upcoming"),RESULTS("Results"),LIVE("Live") }
data class ScheduleFilter(val status:StatusFilter=StatusFilter.ALL,val competition:String?=null,val season:String?=null)

fun matchesStatus(e:HubEvent,f:StatusFilter)=when(f){
 StatusFilter.ALL->true
 // A game in progress is still ahead of the final whistle: it belongs with the upcoming ones as well as under Live.
 StatusFilter.UPCOMING->e.status==HubStatus.SCHEDULED||e.status==HubStatus.POSTPONED||e.status==HubStatus.UNKNOWN||e.status==HubStatus.LIVE||e.status==HubStatus.SUSPENDED
 StatusFilter.RESULTS->e.status==HubStatus.FINAL||e.status==HubStatus.CANCELLED
 StatusFilter.LIVE->e.status==HubStatus.LIVE||e.status==HubStatus.SUSPENDED
}
fun filterSchedule(events:List<HubEvent>,f:ScheduleFilter):List<HubEvent> {
 val shown=events.filter{matchesStatus(it,f.status)&&(f.competition==null||it.league==f.competition)&&(f.season==null||it.season==f.season)}
 // Results read newest first; everything else in calendar order.
 return if(f.status==StatusFilter.RESULTS)shown.sortedWith(compareBy<HubEvent>({if(it.start>0)0 else 1},{-it.start},{it.id})) else shown.sortedWith(scheduleOrder)
}
/** Starting filter: upcoming (which includes a game in progress) when there is any, otherwise results, otherwise everything. */
fun defaultStatusFilter(events:List<HubEvent>):StatusFilter = when{
 events.any{matchesStatus(it,StatusFilter.UPCOMING)}->StatusFilter.UPCOMING
 events.any{matchesStatus(it,StatusFilter.RESULTS)}->StatusFilter.RESULTS
 else->StatusFilter.ALL
}
/** Competition / season choices exist only when the data really spans more than one. */
fun competitionsOf(events:List<HubEvent>):List<String> = events.map{it.league}.filter{it.isNotBlank()}.distinct().sorted().let{if(it.size>1)it else emptyList()}
fun seasonsOf(events:List<HubEvent>):List<String> = events.map{it.season}.filter{it.isNotBlank()}.distinct().sorted().let{if(it.size>1)it else emptyList()}

data class DayGroup(val key:String,val events:List<HubEvent>)
const val TBD_DAY="tbd"
/** Groups already-ordered events by the viewer's local calendar date (DST-safe: the date comes from the zone, never from adding 24 h); events with no start share one trailing group. */
fun groupByLocalDate(events:List<HubEvent>,zone:TimeZone):List<DayGroup> {
 val fmt=SimpleDateFormat("yyyy-MM-dd",Locale.ROOT).also{it.timeZone=zone}
 val out=LinkedHashMap<String,MutableList<HubEvent>>()
 for(e in events)out.getOrPut(if(e.start>0)fmt.format(Date(e.start*1000)) else TBD_DAY){ArrayList()}.add(e)
 return out.map{DayGroup(it.key,it.value)}
}
fun dayHeading(key:String,zone:TimeZone,locale:Locale):String {
 if(key==TBD_DAY)return "Date to be announced"
 val parse=SimpleDateFormat("yyyy-MM-dd",Locale.ROOT).also{it.timeZone=zone}
 val d=parse.parse(key)?:return key
 return SimpleDateFormat("EEEE, MMM d",locale).also{it.timeZone=zone}.format(d)
}
/** "7:30 PM" in the viewer's zone, or "Time TBD" when the provider has no confirmed start. */
fun startTimeLabel(e:HubEvent,zone:TimeZone,locale:Locale):String =
 if(e.start<=0)"Time TBD" else SimpleDateFormat("h:mm a",locale).also{it.timeZone=zone}.format(Date(e.start*1000))
fun startDateTimeLabel(e:HubEvent,zone:TimeZone,locale:Locale):String =
 if(e.start<=0)"Date and time TBD" else SimpleDateFormat("EEE, MMM d · h:mm a",locale).also{it.timeZone=zone}.format(Date(e.start*1000))

/** Label for the state, with the provider's own wording kept for the exceptional ones (e.g. "Postponed", "Delayed"). */
fun statusLabel(e:HubEvent):String = when(e.status){
 HubStatus.SCHEDULED->"Upcoming"
 HubStatus.LIVE->"Live"
 HubStatus.FINAL->"Final"
 HubStatus.POSTPONED->"Postponed"
 HubStatus.CANCELLED->"Cancelled"
 HubStatus.SUSPENDED->"Suspended"
 HubStatus.UNKNOWN->e.detail.ifBlank{"Status unavailable"}
}
/** Score text only when both scores exist and the state carries one. */
fun scoreLine(e:HubEvent):String? = if(e.away.score!=null&&e.home.score!=null)"${e.away.score} - ${e.home.score}" else null

/**
 * Game Details is offered only for an event the existing flows can resolve: the same provider event id as an upcoming
 * fixture or a current guide program. Anything else (past results, far-future games) has no details action.
 */
fun findSupportedGame(e:HubEvent,candidates:List<Game>):Game? =
 if(e.id.isBlank())null else candidates.firstOrNull{g->
  g.raw.optString("espn_id")==e.id||g.raw.optJSONObject("live")?.optString("espn_id")==e.id||g.id==e.id
 }

/** Provider event -> the Game the shared hero logic understands; sides are listed away first, home second. */
fun HubEvent.asGame(teamId:String):Game {
 val phase=when(status){HubStatus.LIVE->"live";HubStatus.FINAL->"final";HubStatus.POSTPONED->"postponed";HubStatus.CANCELLED->"canceled";HubStatus.SUSPENDED->"suspended";HubStatus.SCHEDULED->"upcoming";HubStatus.UNKNOWN->null}
 val raw=JSONObject().put("sides",org.json.JSONArray(listOf(teamKey(away.name),teamKey(home.name)))).put("start",start).put("espn_id",id)
 val live=LiveScore(home.name,away.name,home.score,away.score,home.rank,away.rank,when(status){HubStatus.LIVE->"in";HubStatus.FINAL->"post";else->"pre"},detail,null,null,
  verifiedLive=false,likelyReplay=false,notable=false)
 return Game(id,"${away.name} vs ${home.name}",league,sport,"",fixture=true,raw=raw,teamId=teamId,live=live,eventPhase=phase)
}

// ---- Roster ------------------------------------------------------------------------------------------------

/** Display order of position codes per sport, used only to order the filter chips; the chips themselves come from the data. */
private val POSITION_ORDER=mapOf(
 "football" to listOf("QB","RB","FB","WR","TE","OL","OT","T","G","C","DL","DE","DT","NT","LB","DB","CB","S","K","P","LS"),
 "soccer" to listOf("G","GK","D","M","F"),
 "baseball" to listOf("P","SP","RP","C","1B","2B","3B","SS","LF","CF","RF","OF","IF","DH"),
 "softball" to listOf("P","C","1B","2B","3B","SS","LF","CF","RF","OF","IF","DH"),
 "basketball" to listOf("PG","SG","SF","PF","G","F","C"))
fun rosterPositions(players:List<RosterPlayer>,sport:String):List<String> {
 val order=POSITION_ORDER[sport.lowercase(Locale.ROOT)].orEmpty()
 return players.mapNotNull{it.position}.distinct().sortedWith(compareBy<String>({order.indexOf(it).let{i->if(i<0)Int.MAX_VALUE else i}},{it}))
}
fun foldText(s:String):String = Normalizer.normalize(s.lowercase(Locale.ROOT),Normalizer.Form.NFKD).filter{it.code<128}.trim()
/** Position and free-text filter (name or jersey, accent- and case-insensitive); order is jersey number (numeric, unknown last) then name. */
fun filterRoster(players:List<RosterPlayer>,position:String?,query:String):List<RosterPlayer> {
 val q=foldText(query)
 return players.filter{p->(position==null||p.position==position)&&(q.isEmpty()||foldText(p.name).contains(q)||(p.jersey?.let{foldText(it)}==q))}
  .sortedWith(compareBy<RosterPlayer>({it.jersey?.toIntOrNull()?:Int.MAX_VALUE},{foldText(it.name)},{it.id}))
}
fun playerInitials(name:String):String = heroInitials(name).take(2)
fun jerseyLabel(p:RosterPlayer)=p.jersey?.let{"#$it"}?:"—"

// ---- News --------------------------------------------------------------------------------------------------

/** Only plain http(s) links with a host and no credentials; anything else is dropped (no action) rather than opened. */
fun safeArticleUrl(raw:String?):String? {
 val s=raw?.trim()?:return null
 if(s.isEmpty()||s.length>2048)return null
 return try{
  val u=URI(s)
  if((u.scheme?.lowercase(Locale.ROOT) !in setOf("http","https"))||u.host.isNullOrBlank()||u.userInfo!=null)null else s
 }catch(e:Exception){null}
}
fun urlHost(url:String):String? = try{URI(url).host?.lowercase(Locale.ROOT)?.removePrefix("www.")}catch(e:Exception){null}
/** Same article under tracking parameters or a trailing slash is one key. */
fun canonicalUrlKey(url:String):String = try{
 val u=URI(url);(u.host?.lowercase(Locale.ROOT)?.removePrefix("www.")?:"")+(u.path?.trimEnd('/')?:"")
}catch(e:Exception){url}
fun normalizedHeadline(h:String)=foldText(h).replace(Regex("[^a-z0-9]+")," ").trim()

/** Epoch seconds from an ISO-8601 instant or a numeric epoch; null for anything malformed (shown as "date unavailable"). */
fun parsePublished(v:Any?):Long? {
 if(v==null||v==JSONObject.NULL)return null
 if(v is Number)return v.toLong().takeIf{it>0}
 val s=v.toString().trim()
 if(s.isEmpty())return null
 s.toLongOrNull()?.let{return it.takeIf{n->n>0}}
 for(p in listOf("yyyy-MM-dd'T'HH:mm:ssXXX","yyyy-MM-dd'T'HH:mm:ss.SSSXXX","yyyy-MM-dd'T'HH:mmXXX")){
  try{
   val f=SimpleDateFormat(p,Locale.ROOT);f.isLenient=false;f.timeZone=TimeZone.getTimeZone("UTC")
   return f.parse(s)!!.time/1000
  }catch(e:Exception){}
 }
 return null
}

/** Deduplicated (by id, canonical link, then normalised headline) and newest first; undated stories follow dated ones. */
fun prepareNews(list:List<NewsArticle>):List<NewsArticle> {
 val ids=HashSet<String>();val urls=HashSet<String>();val heads=HashSet<String>()
 return list.filter{a->
  val urlKey=a.url?.let(::canonicalUrlKey)
  ids.add(a.id)&&(urlKey==null||urls.add(urlKey))&&heads.add(normalizedHeadline(a.headline))
 }.sortedWith(compareBy<NewsArticle>({if(it.published==null)1 else 0},{-(it.published?:0L)},{it.id}))
}
/** The first (newest) story is featured; the rest are listed. */
fun featuredNews(list:List<NewsArticle>):Pair<NewsArticle?,List<NewsArticle>> = list.firstOrNull() to list.drop(1)
fun publishedLabel(a:NewsArticle,now:Long):String {
 val t=a.published?:return "Date unavailable"
 val age=now-t
 return if(age>=0&&age<=30L*86400)agoLabel(age) else SimpleDateFormat("MMM d, yyyy",Locale.getDefault()).format(Date(t*1000))
}
fun attributionLabel(a:NewsArticle):String = a.source?:a.url?.let(::urlHost)?:"Source not provided"

// ---- Recorded games ----------------------------------------------------------------------------------------

enum class RecFilter(val label:String){ ALL("All"),READY("Ready"),RECORDING("Recording"),SCHEDULED("Scheduled"),FAILED("Failed") }
/** Which saved games each filter shows. The DVR listing carries no scheduled entries, so Scheduled is always empty (stated by the UI, never faked). */
fun recordingMatches(r:Recording,f:RecFilter)=when(f){
 RecFilter.ALL->true
 RecFilter.READY->r.state=="ready"
 RecFilter.RECORDING->r.state=="recording"
 RecFilter.SCHEDULED->false
 RecFilter.FAILED->r.state=="failed"
}
fun recordingCounts(list:List<Recording>):Map<RecFilter,Int> = RecFilter.values().associateWith{f->list.count{recordingMatches(it,f)}}

/** What a single recording may offer. Only actions the existing DVR contract supports exist; resume, details, cancel and retry have no backend and are never rendered. */
data class RecordingActions(val play:String?)
fun recordingActions(r:Recording):RecordingActions { val c=recordingCard(r);return RecordingActions(if(c.playable)c.action else null) }

data class RecordedView(val base:TeamRecordings,val filter:RecFilter,val shown:List<Recording>,
 /** The tab has recordings but none match [filter]. */
 val noMatches:Boolean,
 /** This TV is not paired: the list cannot be requested at all (permission, not empty). */
 val needsPairing:Boolean)
fun recordedView(snapshot:RecordingsSnapshot?,bucketId:String?,teamName:String,offline:Boolean,paired:Boolean,filter:RecFilter):RecordedView {
 val base=teamRecordings(snapshot,bucketId,teamName,offline)
 val shown=base.recordings.filter{recordingMatches(it,filter)}
 return RecordedView(base,filter,shown,base.kind==TeamRecordingsKind.CONTENT&&shown.isEmpty(),bucketId!=null&&snapshot==null&&!paired)
}

// ---- Request outcomes ---------------------------------------------------------------------------------------

/** HTTP status of a refused /tv/team request (0 = the backend could not be reached) -> what the hub tells the viewer. */
fun hubFailKind(status:Int):FailKind = when(status){0->FailKind.OFFLINE;401,403->FailKind.PERMISSION;else->FailKind.UNAVAILABLE}

/** A design-fixture answer: a /tv/team body, or a failure to exercise the error states. Only the Design flavor ever supplies one. */
class HubFixture(val json:JSONObject?=null,val failure:FailKind?=null)

/** Title and explanation for a resource that could not load. [savedShown] is true when an earlier good copy is still on screen. */
fun hubFailureText(kind:FailKind,what:String,savedAsOf:Long?,savedShown:Boolean,now:Long):Pair<String,String> {
 val saved=if(savedShown)" Showing what was saved"+(savedAsOf?.let{" · updated "+agoLabel(now-it)}.orEmpty())+"." else ""
 return when(kind){
  FailKind.OFFLINE->"Can't reach SG Sports" to "Check this TV's home network connection.$saved"
  FailKind.PERMISSION->"Pair this TV to see $what" to "Open Settings, then Connection, to pair this TV with your SG Sports backend.$saved"
  FailKind.UNAVAILABLE->what.replaceFirstChar{it.uppercase()}+" unavailable right now" to "The sports data provider did not answer. Try again in a moment.$saved"
 }
}

// ---- Live truth from the guide -----------------------------------------------------------------------------

/** A verified-live guide program as a hub event, so a game in progress shows its current score even when the provider schedule has not caught up. */
fun liveGameEvent(g:Game):HubEvent? {
 val l=g.live?:return null
 if(g.phase()!=GamePhase.LIVE||l.home.isBlank()||l.away.isBlank())return null
 val id=g.raw.optJSONObject("live")?.optString("espn_id").orEmpty().ifEmpty{g.raw.optString("espn_id")}
 if(id.isEmpty())return null
 return HubEvent(id,g.sport,g.league,HubSide("",l.home,"",l.homeScore,l.homeRank),HubSide("",l.away,"",l.awayScore,l.awayRank),g.startEpoch(),HubStatus.LIVE,g.clockText()?:l.detail,l.venue,l.networks,"",l.checkedAt)
}
/** Live facts override the matching provider event's status, scores and detail (keeping its team ids and season); a live game the schedule lacks is added. */
fun mergeLive(events:List<HubEvent>,live:List<HubEvent>):List<HubEvent> {
 val byId=live.associateBy{it.id}
 val merged=events.map{e->byId[e.id]?.let{l->e.copy(status=l.status,detail=l.detail,home=e.home.copy(score=l.home.score,rank=l.home.rank?:e.home.rank),away=e.away.copy(score=l.away.score,rank=l.away.rank?:e.away.rank),
  venue=e.venue.ifEmpty{l.venue},networks=e.networks.ifEmpty{l.networks},start=if(e.start>0)e.start else l.start,updatedAt=l.updatedAt)}?:e}
 return dedupeEvents(merged+live.filter{l->events.none{it.id==l.id}})
}
/** The hub with in-progress guide games folded into its schedule; every other resource, and a schedule that failed outright, is left exactly as it was. */
fun TeamHubState.withLive(live:List<HubEvent>,fixtures:List<HubEvent> = emptyList()):TeamHubState {
 if(live.isEmpty()&&fixtures.isEmpty())return this
 fun Res.Ready<ScheduleData>.merged()=copy(value=value.copy(events=addMissing(mergeLive(value.events,live),fixtures)))
 val s=data.schedule
 val merged=when(s){is Res.Ready->s.merged();is Res.Failed->s.last?.let{Res.Failed(s.kind,it.merged())}?:s;Res.Loading->s}
 return copy(data=data.copy(schedule=merged))
}

/** The team's next scheduled game as the app already knows it (the backend's next-games list), as a hub event: it fills the schedule when the hub's own schedule section is empty or lags, and it is the event Game Details and stream search resolve. */
fun fixtureEvent(g:Game,teamName:String):HubEvent? {
 // The home fixture (Barcelona) carries no provider event id; its own id then identifies it.
 val id=g.raw.optString("espn_id").ifEmpty{g.id};val opponent=g.raw.optString("opponent");val start=g.startEpoch()
 if(!g.fixture||id.isEmpty()||opponent.isBlank()||start<=0)return null
 val own=HubSide(g.raw.optString("team_espn_id"),teamName,"",null,null);val opp=HubSide(g.raw.optString("opponent_espn_id"),opponent,"",null,null)
 val home=g.raw.optBoolean("home",true)
 val status=when(g.phase()){GamePhase.UPCOMING->HubStatus.SCHEDULED;GamePhase.LIVE->HubStatus.LIVE;GamePhase.POSTPONED->HubStatus.POSTPONED;GamePhase.CANCELED->HubStatus.CANCELLED;GamePhase.SUSPENDED->HubStatus.SUSPENDED;GamePhase.FINAL->HubStatus.FINAL;else->HubStatus.UNKNOWN}
 return HubEvent(id,g.sport,g.league,if(home)own else opp,if(home)opp else own,start,status,"","",emptyList(),"")
}
/** Adds events whose provider id the schedule does not already list; listed ones keep the provider's version. */
fun addMissing(events:List<HubEvent>,extra:List<HubEvent>):List<HubEvent> = dedupeEvents(events+extra.filter{x->events.none{it.id==x.id}})

private val PROGRAM_SPLIT=Regex("\\s+(?:vs\\.?|v|at|@)\\s+",RegexOption.IGNORE_CASE)
/** A guide program for a game that has not started (listed ahead of time), as a hub event; its own id is what Game Details / stream search resolve. Only when the matchup names two sides. */
fun programEvent(g:Game):HubEvent? {
 val start=g.startEpoch()
 if(!g.isGame||g.phase()!=GamePhase.UPCOMING||start<=0||g.id.isBlank())return null
 val parts=g.title.split(PROGRAM_SPLIT).map{it.trim()}.filter{it.isNotEmpty()}
 if(parts.size!=2)return null
 val awayFirst=Regex("\\s+(?:at|@)\\s+",RegexOption.IGNORE_CASE).containsMatchIn(g.title) // "Away at Home" / "Away @ Home"; "Home vs Away" lists home first
 val (first,second)=parts
 val home=if(awayFirst)second else first;val away=if(awayFirst)first else second
 return HubEvent(g.id,g.sport,g.league,HubSide("",home,"",null,null),HubSide("",away,"",null,null),start,HubStatus.SCHEDULED,"","",emptyList(),"")
}
