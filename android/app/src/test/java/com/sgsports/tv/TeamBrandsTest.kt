package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Canonical branding coverage and safety, on the bundled identity + color assets (no network, no providers). */
class TeamBrandsTest {
 private val assets=File("src/main/assets")
 private val ids=JSONArray(File(assets,"team-identities.json").readText())
 private val colors=JSONObject(File(assets,"team-brand-colors.json").readText())
 private val catalog=TeamBrandCatalog(ids,colors)
 private val text=0xFFF4F1E8.toInt()
 private fun brand(key:String,sport:String,league:String)=catalog.resolve(teamKey(key),sport,league)
 private fun game(title:String,sport:String,league:String,logos:List<String>,sides:List<String>?=null,live:LiveScore?=null)=
  Game("g",title,league,sport,"",logos=logos,raw=JSONObject().apply{if(sides!=null)put("sides",JSONArray(sides))},live=live,eventPhase="upcoming")

 @Test fun byuAndNotreDameResolveByCanonicalIdentityWithCrestAndColor(){
  val byu=brand("BYU","Football","NCAA");val nd=brand("Notre Dame","Football","NCAA")
  assertNotNull(byu);assertNotNull(nd)
  assertEquals("BYU Cougars",byu!!.name);assertEquals("Notre Dame Fighting Irish",nd!!.name)
  assertNotEquals(byu.logo,nd.logo)
  assertTrue(TestAssets.crestOk(byu.logo));assertTrue(TestAssets.crestOk(nd.logo))
  assertNotNull(byu.color);assertNotNull(nd.color);assertNotEquals(byu.color,nd.color)
  // The full canonical name works as well as the short one.
  assertEquals(byu,brand("BYU Cougars","Football","NCAA"))
 }
 @Test fun acronymSchoolsResolveThroughTheirBareAcronym(){
  for((acr,full) in listOf("LSU" to "LSU Tigers","SMU" to "SMU Mustangs","TCU" to "TCU Horned Frogs","UCF" to "UCF Knights","USC" to "USC Trojans","UAB" to "UAB Blazers","VMI" to "VMI Keydets"))
   assertEquals(full,brand(acr,"Football","NCAA")?.name)
 }
 @Test fun ambiguousMascotAliasesResolveToNothing(){
  for(k in listOf("cougars","tigers","bulldogs","wildcats","eagles"))assertNull(k,brand(k,"Football","NCAA"))
 }
 @Test fun sameNameInAnotherSportOrLeagueNeverBorrowsTheCrest(){
  assertEquals("Arizona Cardinals",brand("Arizona Cardinals","Football","NFL")?.name)
  assertNull(brand("Arizona Cardinals","Baseball","MLB"))
  assertNull(brand("St. Louis Cardinals","Football","NFL"))
  assertEquals("St. Louis Cardinals",brand("St. Louis Cardinals","Baseball","MLB")?.name)
 }
 @Test fun representativeMajorTeamsAcrossSupportedLeaguesHaveCrestAndColor(){
  val sample=listOf(
   Triple("Green Bay Packers","Football","NFL"),Triple("Kansas City Chiefs","Football","NFL"),
   Triple("New York Yankees","Baseball","MLB"),Triple("Los Angeles Dodgers","Baseball","MLB"),
   Triple("Liverpool","Soccer","Premier League"),Triple("Real Madrid","Soccer","La Liga"),Triple("Bayern Munich","Soccer","Bundesliga"),
   Triple("Juventus","Soccer","Serie A"),Triple("Paris Saint-Germain","Soccer","Ligue 1"),
   Triple("Michigan Wolverines","Football","NCAA"),Triple("Texas Longhorns","Football","NCAA"),Triple("Ohio State Buckeyes","Football","NCAA"),
   Triple("Georgia Bulldogs","Football","NCAA"),Triple("Boca Juniors","Soccer","Argentina Primera División"),Triple("Leones del Caracas","Baseball","Venezuela LVBP"))
  for((n,s,l) in sample){val b=brand(n,s,l);assertNotNull("$n",b);assertNotNull("$n color",b!!.color);assertTrue("$n crest",TestAssets.crestOk(b.logo))}
 }
 @Test fun everyColorEntryNamesARealIdentityWithValidHexAndLegibleHero(){
  val arr=colors.getJSONArray("teams");val real=HashSet<String>()
  for(i in 0 until ids.length()){val t=ids.getJSONObject(i);real.add("${t.getString("league")}|${t.getString("sport")}|${t.getString("name")}")}
  val seen=HashSet<String>()
  for(i in 0 until arr.length()){
   val c=arr.getJSONObject(i);val key="${c.getString("league")}|${c.getString("sport")}|${c.getString("name")}"
   assertTrue("unknown identity $key",key in real);assertTrue("duplicate $key",seen.add(key))
   val primary=parseHexColor(c.getString("color"));assertNotNull("bad color $key",primary)
   assertNotNull("bad secondary $key",parseHexColor(c.getString("secondary")))
   val p=heroPalette(primary!!.toInt(),null)
   // Names sit on the team glow, drawn at most ~60% over the dark hero base.
   fun glow(c:Int,sh:Int)=Math.round(((c shr sh) and 0xFF)*.6+((HERO_BASE shr sh) and 0xFF)*.4).toInt()
   val over=(0xFF shl 24) or (glow(p.left,16) shl 16) or (glow(p.left,8) shl 8) or glow(p.left,0)
   assertTrue("legibility $key",contrastRatio(text,over)>=4.5)
  }
 }
 @Test fun coverageFloorsHoldPerLeague(){
  fun covered(league:String)=catalog.all().filter{it.league==league}.let{l->l.count{it.color!=null} to l.size}
  for(l in listOf("NFL","MLB","Premier League","Serie A")){val(c,t)=covered(l);assertEquals(l,t,c)}
  assertTrue(catalog.withColor>=500);assertEquals(528,catalog.size)
 }
 @Test fun heroPairsBYUAndNotreDameWithTheirOwnCrestsEvenWhenGameCrestsAreInTheWrongOrder(){
  val byu=brand("BYU","Football","NCAA")!!;val nd=brand("Notre Dame","Football","NCAA")!!
  // Event names BYU first; the game's own crest list is reversed (list order is not identity).
  val g=game("BYU vs Notre Dame","Football","NCAA",listOf(nd.logo,byu.logo),sides=listOf(teamKey("BYU"),teamKey("Notre Dame")))
  val s=heroSides(g,emptyList(),catalog)
  assertEquals(listOf(byu.logo,nd.logo),s.map{it.logo})
  assertEquals(listOf(byu.color,nd.color),s.map{it.color})
  assertNotEquals(heroPalette(s[0].color,s[1].color).left,heroPalette(s[0].color,s[1].color).right)
 }
 @Test fun unknownTeamsKeepSafeNeutralFallbackWithoutBorrowingBranding(){
  val g=game("Sample Wolves vs Sample Hawks","Football","NCAA",emptyList(),sides=listOf("sample wolves","sample hawks"))
  val s=heroSides(g,emptyList(),catalog)
  assertTrue(s.all{it.logo.isEmpty()&&it.color==null})
  assertEquals(HERO_NEUTRAL_LEFT,heroPalette(null,null).left)
  // One known, one unknown: only the known side is branded.
  val half=heroSides(game("BYU vs Sample Hawks","Football","NCAA",emptyList(),sides=listOf(teamKey("BYU"),"sample hawks")),emptyList(),catalog)
  assertNotNull(half[0].color);assertNull(half[1].color);assertEquals("",half[1].logo)
 }
 @Test fun homeNoLongerHasTheGreetingDateTaglineBar(){
  val src=File("src/main/java/com/sgsports/tv/SgScreens.kt").readText()
  assertTrue(src.contains("if(page!=\"Home\"){val headerBody")&&src.contains("stickyHeader(content=headerBody)"))   // Home has no bar; a team hub's bar scrolls, other pages pin it
  assertFalse(src.contains("morning"));assertFalse(src.contains("One place."))
 }
}
