package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject

/*
 * Design-flavor-only Team Hub fixtures (marker: design-hub-fixture). Shaped exactly like the backend's /tv/team answer,
 * so the production parser and screens run unchanged. Everything here is illustrative: scores, dates, players, news and
 * results are invented and the Studio/Living Room builds do not contain this file (scripts/check_flavor_fixtures.sh).
 * One scenario per team so each resource state can be reviewed: full, partial failure, older contract, offline, pairing, empty.
 */
private const val HUB_FIXTURE_MARKER="design-hub-fixture"
private const val DAY=86400L

private fun side(id:String,name:String,score:String?=null,rank:Int?=null)=JSONObject().put("id",id).put("name",name).put("abbreviation",name.take(3).uppercase())
 .put("score",score?:JSONObject.NULL).put("rank",rank?:JSONObject.NULL)
private fun event(id:String,homeId:String,home:String,awayId:String,away:String,at:Long?,state:String,statusName:String,completed:Boolean,detail:String,
 hs:String?=null,aws:String?=null,league:String,venue:String="",season:String="regular-season",hr:Int?=null,ar:Int?=null):JSONObject {
 val o=JSONObject().put("espn_id",id).put("league",league).put("sport","").put("home",side(homeId,home,hs,hr)).put("away",side(awayId,away,aws,ar))
  .put("state",state).put("status_name",statusName).put("completed",completed).put("detail",detail).put("venue",venue).put("networks",JSONArray()).put("season_slug",season).put("season_type",2)
 if(at!=null)o.put("start",at)
 return o
}
private fun player(id:String,name:String,jersey:String?,pos:String?,posName:String?=null)=JSONObject().put("id",id).put("name",name)
 .put("jersey",jersey?:JSONObject.NULL).put("position",pos?:JSONObject.NULL).put("position_name",posName?:JSONObject.NULL)
private fun article(id:String?,headline:String,source:String?,published:String?,url:String?)=JSONObject().apply{
 if(id!=null)put("id",id);put("headline",headline);put("source",source?:JSONObject.NULL);put("published",published?:JSONObject.NULL);put("url",url?:JSONObject.NULL)
}
private fun base(record:String?,rank:Int?,standing:String?)=JSONObject().put("fixture",HUB_FIXTURE_MARKER).put("record",record?:JSONObject.NULL).put("rank",rank?:JSONObject.NULL).put("standing",standing?:JSONObject.NULL)
private fun arr(vararg o:JSONObject)=JSONArray().also{a->o.forEach{a.put(it)}}
private fun iso(epoch:Long)=java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'",java.util.Locale.ROOT).also{it.timeZone=java.util.TimeZone.getTimeZone("UTC")}.format(java.util.Date(epoch*1000))

private fun footballHub(now:Long)=base("9-2",8,"1st in SEC").put("events",arr(
 event("fx-f1","2633","Tennessee Volunteers","238","Vanderbilt Commodores",now-22*DAY,"post","STATUS_FINAL",true,"Final","38","14","NCAA","Sample Stadium"),
 event("fx-f2","57","Florida Gators","2633","Tennessee Volunteers",now-15*DAY,"post","STATUS_FINAL",true,"Final","17","24","NCAA"),
 event("fx-f3","2633","Tennessee Volunteers","201","Oklahoma Sooners",now-3600,"in","STATUS_IN_PROGRESS",false,"3rd - 8:42","17","14","NCAA","Sample Stadium",hr=8),
 event("fx-f4","61","Georgia Bulldogs","2633","Tennessee Volunteers",now+6*DAY,"pre","STATUS_SCHEDULED",false,"Sat 7:30 PM",league="NCAA"),
 event("fx-f5","2633","Tennessee Volunteers","96","Kentucky Wildcats",now+13*DAY,"pre","STATUS_SCHEDULED",false,"Sat 12:00 PM",league="NCAA"),
 event("fx-f6","2633","Tennessee Volunteers","142","Missouri Tigers",null,"pre","STATUS_SCHEDULED",false,"TBD",league="NCAA"),
 event("fx-f7","2","Auburn Tigers","2633","Tennessee Volunteers",now+27*DAY,"post","STATUS_POSTPONED",false,"Postponed",league="NCAA"),
 event("fx-f8","61","Georgia Bulldogs","2633","Tennessee Volunteers",now+40*DAY,"pre","STATUS_SCHEDULED",false,"TBD",league="NCAA",season="post-season")
)).put("roster",arr(
 player("fp1","Josh Star","7","QB","Quarterback"),player("fp2","Marcus Lane","21","RB","Running Back"),player("fp3","Deion Cross","4","WR","Wide Receiver"),
 player("fp4","Malik Reeves","11","WR","Wide Receiver"),player("fp5","Trey Holcomb","55","OL","Offensive Lineman"),player("fp6","Sam Whitfield",null,"DB","Defensive Back"),
 player("fp7","Álvaro Núñez","7","K","Kicker"),player("fp8","Devon Park","88","TE","Tight End"),player("fp9","Cole Brandt","34","LB","Linebacker"),
 player("fp10","Isaiah Grant","92","DL","Defensive Lineman"),player("fp11","Noah Whitaker","15",null),player("fp12","A. Very Long Hyphenated-Surname Example Jr.","99","DL")
)).put("injuries",arr(JSONObject().put("name","Josh Star").put("status","Questionable"),JSONObject().put("name","Cole Brandt").put("status","Out"))).put("news",arr(
 article("fn1","Sample: Vols climb in latest poll after statement win","Sample Wire",iso(now-3*3600),"https://news.example.com/vols-climb?utm_source=tv"),
 article("fn1b","Sample: Vols climb in latest poll after statement win","Sample Wire",iso(now-3*3600),"https://news.example.com/vols-climb/"),
 article("fn2","Sample: Injury report ahead of the weekend",null,iso(now-DAY),"https://example.org/injury-report"),
 article("fn3","Sample: Coach reflects on the season so far","Sample Sports","not-a-date","javascript:alert(1)"),
 article("fn4","Sample: Preview — what to watch on Saturday","Sample Wire",iso(now-5*DAY),null)
)).put("unavailable",JSONArray())

private fun soccerHub(now:Long)=base("21-5-4",null,"1st in La Liga").put("events",arr(
 event("fx-s1","83","FC Barcelona","86","Real Madrid",now-9*DAY,"post","STATUS_FULL_TIME",true,"FT","3","2","La Liga"),
 event("fx-s2","131","Paris Saint-Germain","83","FC Barcelona",now-2*DAY,"post","STATUS_FULL_TIME",true,"FT","1","1","UEFA Champions League"),
 event("fx-s3","83","FC Barcelona","96","Sample Visitors",now+4*DAY,"pre","STATUS_SCHEDULED",false,"Sat 3:00 PM",league="La Liga"),
 event("fx-s4","83","FC Barcelona","131","Paris Saint-Germain",now+11*DAY,"pre","STATUS_SCHEDULED",false,"Wed 3:00 PM",league="UEFA Champions League"),
 event("fx-s5","93","Sample Rivals","83","FC Barcelona",now+18*DAY,"post","STATUS_POSTPONED",false,"Postponed",league="La Liga"),
 event("fx-s6","83","FC Barcelona","94","Sample Neighbours",null,"pre","STATUS_SCHEDULED",false,"TBD",league="La Liga")
)).put("roster",arr(
 player("sp1","Marc Sample","1","G","Goalkeeper"),player("sp2","João Exemplo","4","D","Defender"),player("sp3","Pedri Demo","8","M","Midfielder"),
 player("sp4","Lamine Ejemplo","19","F","Forward"),player("sp5","Raphael Muestra","11","F","Forward"),player("sp6","Gavi Prueba","6","M","Midfielder")
)).put("injuries",JSONArray()).put("news",JSONArray()).put("unavailable",JSONArray().put("news"))

/** Older backend: only the next few upcoming games, no results, no article ids/links. */
private fun softballHub(now:Long)=base("34-8",4,"2nd in SEC").put("games",arr(
 event("fx-b1","611","Tennessee Lady Volunteers","333","Alabama Crimson Tide",now+2*DAY,"pre","STATUS_SCHEDULED",false,"Fri 6:00 PM",league="NCAA"),
 event("fx-b2","99","LSU Tigers","611","Tennessee Lady Volunteers",now+3*DAY,"pre","STATUS_SCHEDULED",false,"Sat 2:00 PM",league="NCAA")
)).put("roster",JSONArray()).put("injuries",JSONArray()).put("news",arr(
 JSONObject().put("headline","Sample: Lady Vols open the weekend series").put("published",iso(now-6*3600)))).put("unavailable",JSONArray())

private fun baseballHub(now:Long)=base("88-74",null,"2nd in AL West").put("events",arr(
 event("fx-m1","18","Houston Astros","13","Texas Rangers",now-DAY,"post","STATUS_FINAL",true,"Final","4","2","MLB","Sample Park"),
 event("fx-m2","18","Houston Astros","13","Texas Rangers",now+DAY,"pre","STATUS_SCHEDULED",false,"Tue 7:10 PM",league="MLB"),
 event("fx-m3","12","Seattle Mariners","18","Houston Astros",now+3*DAY,"pre","STATUS_SCHEDULED",false,"Thu 9:40 PM",league="MLB")
)).put("roster",arr(
 player("mp1","Alex Pitcher","32","SP","Starting Pitcher"),player("mp2","Jordan Catcher","10","C","Catcher"),player("mp3","Riley Short","2","SS","Shortstop"),
 player("mp4","Casey Outfield","27","CF","Center Fielder"),player("mp5","Morgan Reliever","45","RP","Relief Pitcher")
)).put("injuries",JSONArray()).put("news",arr(article("mn1","Sample: Rotation set for the series","Sample Wire",iso(now-2*3600),"https://news.example.com/rotation"))).put("unavailable",JSONArray())

/** Partial answer: the overview and roster sections failed, schedule is empty, news is empty. */
private fun partialHub(now:Long)=JSONObject().put("fixture",HUB_FIXTURE_MARKER).put("record",JSONObject.NULL).put("rank",JSONObject.NULL).put("standing",JSONObject.NULL)
 .put("events",JSONArray()).put("roster",JSONArray()).put("injuries",JSONArray()).put("news",JSONArray()).put("unavailable",JSONArray().put("overview").put("roster"))

/** The real answer stamps every game with its team's sport; the fixtures do the same so crest and colour lookups resolve exactly as in production. */
private fun JSONObject.sport(name:String):JSONObject{for(k in listOf("events","games")){val a=optJSONArray(k)?:continue;for(i in 0 until a.length())a.getJSONObject(i).put("sport",name)};return this}

/** Fixture answer for a favorite, keyed by its canonical ID's sport/team slug; unknown teams get no fixture (the real request path). */
fun hubFixture(teamId:String):HubFixture? {
 val now=System.currentTimeMillis()/1000
 return when{
  teamId.endsWith("fc-barcelona")->HubFixture(soccerHub(now).sport("Soccer"))
  teamId.startsWith("team.football.men.ncaa.tennessee")->HubFixture(footballHub(now).sport("Football"))
  teamId.startsWith("team.softball.")->HubFixture(softballHub(now).sport("Softball"))
  teamId.endsWith("houston-astros")->HubFixture(baseballHub(now).sport("Baseball"))
  teamId.endsWith("st-louis-cardinals")->HubFixture(failure=FailKind.OFFLINE)
  teamId.endsWith("kansas-city-chiefs")->HubFixture(failure=FailKind.PERMISSION)
  teamId.endsWith("missouri-tigers")->HubFixture(partialHub(now))
  else->null
 }
}
