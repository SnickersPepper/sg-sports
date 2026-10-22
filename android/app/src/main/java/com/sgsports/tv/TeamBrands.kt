package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject

/**
 * Canonical team branding: the crest from team-identities.json plus the curated colors in team-brand-colors.json, both keyed by
 * the same identity (league, sport, name). Resolution is by exact canonical alias key ([teamKey]) inside the event's own sport and
 * league -- never a substring of a display name -- and an alias shared by several identities (e.g. "Tigers") resolves to nothing
 * rather than to a guess, so a crest or color can never land on the wrong team.
 */
data class TeamBrand(val name:String,val league:String,val sport:String,val logo:String,val color:Int?,val secondary:Int?)

class TeamBrandCatalog(identities:JSONArray,colors:JSONObject?){
 private val brands:List<TeamBrand>
 private val byScope=HashMap<String,Map<String,List<TeamBrand>>>()

 init{
  val colorByIdentity=HashMap<String,Pair<Int?,Int?>>()
  val arr=colors?.optJSONArray("teams")
  if(arr!=null)for(i in 0 until arr.length()){
   val c=arr.optJSONObject(i)?:continue
   colorByIdentity[identityKey(c.optString("league"),c.optString("sport"),c.optString("name"))]=
    parseHexColor(c.optString("color"))?.toInt() to parseHexColor(c.optString("secondary"))?.toInt()
  }
  val list=ArrayList<TeamBrand>()
  for(i in 0 until identities.length()){
   val t=identities.optJSONObject(i)?:continue
   val league=t.optString("league");val sport=t.optString("sport");val name=t.optString("name")
   val col=colorByIdentity[identityKey(league,sport,name)]
   list.add(TeamBrand(name,league,sport,t.optString("file"),col?.first,col?.second))
  }
  brands=list
  // Alias index per scope; a scope is either one league of a sport, or every league of a sport.
  val aliasesOf=HashMap<TeamBrand,List<String>>()
  for(i in 0 until identities.length()){
   val t=identities.optJSONObject(i)?:continue
   val a=t.optJSONArray("aliases")?:continue
   val b=brands[i]
   aliasesOf[b]=(0 until a.length()).map{teamKey(a.optString(it))}.filter{it.isNotEmpty()}.plus(teamKey(b.name)).distinct()
  }
  fun build(sel:(TeamBrand)->Boolean):Map<String,List<TeamBrand>>{
   val m=HashMap<String,MutableList<TeamBrand>>()
   for(b in brands)if(sel(b))for(k in aliasesOf[b].orEmpty())m.getOrPut(k){ArrayList()}.add(b)
   return m
  }
  for(b in brands){
   byScope.getOrPut(scopeKey(b.sport,b.league)){build{x->x.sport==b.sport&&x.league==b.league}}
   byScope.getOrPut(scopeKey(b.sport,null)){build{x->x.sport==b.sport}}
  }
 }

 /** The one identity this canonical [key] names in the event's sport and league, or null when unknown or ambiguous. */
 fun resolve(key:String,sport:String,league:String):TeamBrand?{
  val s=sport.lowercase()
  // Soccer events carry competition names of their own (Champions League, domestic cups), so every soccer league is searched.
  val scope=if(s=="soccer")scopeKey(s,null) else scopeKey(s,league)
  val hits=byScope[scope]?.get(key)?.distinct()?:return null
  // The same club listed under several competitions shares one crest file: that is one identity (prefer the entry carrying colors).
  val clubs=hits.groupBy{it.logo.ifBlank{it.name}}
  return clubs.values.singleOrNull()?.let{g->g.firstOrNull{it.color!=null}?:g.first()}
 }

 val size get()=brands.size
 val withColor get()=brands.count{it.color!=null}
 fun all():List<TeamBrand> = brands

 private fun scopeKey(sport:String,league:String?)=sport+"|"+(league?.lowercase()?:"*")
 private fun identityKey(league:String,sport:String,name:String)="${sport.lowercase()}|${league.lowercase()}|${name.lowercase()}"

 companion object{
  fun load(context:android.content.Context):TeamBrandCatalog{
   val ids=context.assets.open("team-identities.json").bufferedReader().use{JSONArray(it.readText())}
   val colors=runCatching{context.assets.open("team-brand-colors.json").bufferedReader().use{JSONObject(it.readText())}}.getOrNull()
   return TeamBrandCatalog(ids,colors)
  }
 }
}
