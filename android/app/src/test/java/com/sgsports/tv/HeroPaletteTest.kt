package com.sgsports.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Hero fallback palette on synthetic teams: deterministic, offline, always legible. */
class HeroPaletteTest {
 private val text=0xFFF4F1E8.toInt()
 private val orange=0xFFFF8200.toInt();private val blue=0xFF1F4E9C.toInt()
 private fun blendChannel(c:Int,a:Double,base:Int,shift:Int):Int=Math.round(((c shr shift) and 0xFF)*a+((base shr shift) and 0xFF)*(1-a)).toInt()
 private fun blend(c:Int,a:Double,base:Int):Int=(0xFF shl 24) or (blendChannel(c,a,base,16) shl 16) or (blendChannel(c,a,base,8) shl 8) or blendChannel(c,a,base,0)
 private fun assertLegible(p:HeroPalette){
  // Names sit on the team glow, drawn at most ~60% over the dark base.
  listOf(p.left,p.right).forEach{c->assertTrue("text contrast on $c",contrastRatio(text,blend(c,.6,HERO_BASE))>=4.5)}
  assertTrue("distinct ${colorDistance(p.left,p.right)}",colorDistance(p.left,p.right)>=80)
 }
 private fun team(id:String,name:String,color:Long,known:Boolean=true)=Team(id,name,"Football","NCAA","",color,color,colorKnown=known)
 private fun event(sides:List<String>?,teamId:String="",sport:String="Football")=Game("g","t","NCAA",sport,"",raw=JSONObject().apply{if(sides!=null)put("sides",JSONArray(sides))},teamId=teamId)

 @Test fun personalizedArtAlwaysWins(){
  assertEquals(HeroArt.PERSONALIZED,heroArt(true))
  assertEquals(HeroArt.MATCHUP,heroArt(false))
 }
 @Test fun twoValidColorsKeepBothIdentities(){
  val p=heroPalette(orange,blue)
  assertTrue(p.teamLeft&&p.teamRight)
  assertNotEquals(p.left,p.right)
  assertLegible(p)
  // Hue is preserved: the orange side stays warm, the blue side cool.
  assertTrue(((p.left shr 16) and 0xFF)>((p.left) and 0xFF))
  assertTrue(((p.right) and 0xFF)>((p.right shr 16) and 0xFF))
 }
 @Test fun oneMissingColorPairsWithDerivedNeutral(){
  val p=heroPalette(orange,null)
  assertTrue(p.teamLeft);assertFalse(p.teamRight);assertLegible(p)
  val q=heroPalette(null,blue)
  assertFalse(q.teamLeft);assertTrue(q.teamRight);assertLegible(q)
 }
 @Test fun bothMissingUsesAppNeutral(){
  val p=heroPalette(null,null)
  assertEquals(HERO_NEUTRAL_LEFT,p.left);assertEquals(HERO_NEUTRAL_RIGHT,p.right)
  assertFalse(p.teamLeft||p.teamRight);assertLegible(p)
 }
 @Test fun identicalAndNearIdenticalColorsStayDistinguishable(){
  assertLegible(heroPalette(orange,orange))
  assertLegible(heroPalette(0xFF123456.toInt(),0xFF133557.toInt()))
  assertLegible(heroPalette(0xFFFFFFFF.toInt(),0xFFFEFEFE.toInt()))
  assertLegible(heroPalette(0xFF000000.toInt(),0xFF000000.toInt()))
 }
 @Test fun extremeColorsAreClampedForReadability(){
  assertLegible(heroPalette(0xFFFFFF00.toInt(),0xFFFFFFFF.toInt()))
  assertLegible(heroPalette(0xFF000000.toInt(),0xFF050505.toInt()))
 }
 @Test fun malformedColorStringsNeverThrow(){
  listOf(null,"","zzzzzz","12345","1234567","#GG0000","0x12","  ","FF 000").forEach{assertNull(it,parseHexColor(it))}
  assertEquals(0xFFFF8200L,parseHexColor("FF8200"));assertEquals(0xFFFF8200L,parseHexColor("#ff8200"))
  val teams=parseTeams(JSONObject("""{"teams":[{"id":"a","name":"A","sport":"Football","color":"nope","secondary":""},{"id":"b","name":"B","sport":"Football"}]}"""))
  assertEquals(2,teams.size);assertTrue(teams.none{it.colorKnown})
  val (l,r)=heroParticipantColors(event(listOf("a","b")),teams)
  assertNull(l);assertNull(r);assertLegible(heroPalette(l,r))
 }
 @Test fun participantsResolveByCanonicalKeyInEventOrder(){
  val teams=listOf(team("t1","Tennessee Volunteers",0xFFFF8200),team("t2","Alabama Crimson Tide",0xFF9E1B32))
  val vols=teamKey("Tennessee Volunteers");val bama=teamKey("Alabama Crimson Tide")
  assertEquals(0xFFFF8200.toInt() to 0xFF9E1B32.toInt(),heroParticipantColors(event(listOf(vols,bama)),teams).let{it.first to it.second})
  assertEquals(0xFF9E1B32.toInt() to 0xFFFF8200.toInt(),heroParticipantColors(event(listOf(bama,vols)),teams).let{it.first to it.second})
  val half=heroParticipantColors(event(listOf(vols,"someone else")),teams)
  assertEquals(0xFFFF8200.toInt(),half.first);assertNull(half.second)
  val swapped=heroParticipantColors(event(listOf("someone else",vols)),teams)
  assertNull(swapped.first);assertEquals(0xFFFF8200.toInt(),swapped.second)
 }
 @Test fun sportMismatchDoesNotBorrowAnotherTeamsColor(){
  val teams=listOf(team("t1","Tennessee Volunteers",0xFFFF8200))
  val g=event(listOf(teamKey("Tennessee Volunteers"),"x"),sport="Softball")
  assertNull(heroParticipantColors(g,teams).first)
 }
 @Test fun withoutParticipantsOnlyTheMatchedTeamColorsOneSide(){
  val teams=listOf(team("t1","Tennessee Volunteers",0xFFFF8200),team("t2","Other",0xFF112233))
  val (l,r)=heroParticipantColors(event(null,teamId="t1"),teams)
  assertEquals(0xFFFF8200.toInt(),l);assertNull(r)
  assertEquals(null to null,heroParticipantColors(event(null),teams))
 }
 @Test fun bundledRosterParsesWithKnownColors(){
  TestAssets.assumePrivate()
  val installed=parseTeams(JSONObject(TestAssets.file("favorites.json").readText()))
  assertTrue(installed.isNotEmpty());assertTrue(installed.all{it.colorKnown})
  installed.forEach{assertLegible(heroPalette(it.color.toInt(),null))}
 }
 @Test fun paletteIsDeterministic(){assertEquals(heroPalette(orange,blue),heroPalette(orange,blue))}
 private fun live(away:String,home:String,aRank:Int?=null,hRank:Int?=null,state:String="in")=LiveScore(home,away,"3","7",hRank,aRank,state,"3rd",3,"5:05",verifiedLive=true,likelyReplay=false,notable=false)
 private fun game(title:String,logos:List<String>,live:LiveScore?=null,sides:List<String>?=null,teamId:String="",ranks:List<Int?> = emptyList(),phase:String="live")=
  Game("g",title,"NCAA","Football","",logos=logos,raw=JSONObject().apply{if(sides!=null)put("sides",JSONArray(sides))},teamId=teamId,live=live,ranks=ranks,eventPhase=phase)

 @Test fun liveSidesFollowAwayThenHomeWithTheirOwnCrestsRanksAndColors(){
  val teams=listOf(team("t1","Tennessee Volunteers",0xFFFF8200),team("t2","Alabama Crimson Tide",0xFF9E1B32))
  val g=game("Tennessee vs Alabama",listOf("logos/away.png","logos/home.png"),live("Alabama Crimson Tide","Tennessee Volunteers",6,14))
  val s=heroSides(g,teams)
  assertEquals(listOf("Alabama Crimson Tide","Tennessee Volunteers"),s.map{it.name})
  assertEquals(listOf("logos/away.png","logos/home.png"),s.map{it.logo})
  assertEquals(listOf<Int?>(6,14),s.map{it.rank})
  assertEquals(listOf<Int?>(0xFF9E1B32.toInt(),0xFFFF8200.toInt()),s.map{it.color})
 }
 @Test fun missingBrandingStillYieldsTwoNamedSides(){
  val s=heroSides(game("Sample Rockets vs Sample Comets",emptyList(),null,listOf("sample rockets","sample comets"),phase="upcoming"),emptyList())
  assertEquals(listOf("Sample Rockets","Sample Comets"),s.map{it.name})
  assertTrue(s.all{it.logo.isEmpty()&&it.color==null&&it.rank==null})
 }
 @Test fun loneCrestAttachesOnlyToTheRosterTeamThatMatched(){
  val teams=listOf(team("t1","Tennessee Volunteers",0xFFFF8200).copy(logo="logos/vols.png"))
  val g=game("Tennessee vs Florida",listOf("logos/stray.png"),null,listOf(teamKey("Tennessee Volunteers"),"florida"),teamId="t1",phase="upcoming")
  val s=heroSides(g,teams)
  assertEquals("logos/vols.png",s[0].logo);assertEquals("",s[1].logo)
 }
 @Test fun singleParticipantEventKeepsGeometryWithBlankSecondSide(){
  val s=heroSides(game("Masters Sunday",emptyList(),phase="upcoming"),emptyList())
  assertEquals(2,s.size);assertEquals("Masters Sunday",s[0].name);assertEquals("",s[1].name)
 }
 @Test fun lvbpStylePrefixIsDroppedFromSideNames(){
  val s=heroSides(game("Venezuela LVBP · Leones @ Tiburones",emptyList(),phase="upcoming"),emptyList())
  assertEquals(listOf("Leones","Tiburones"),s.map{it.name})
 }

 @Test fun monogramsComeFromWordInitials(){
  assertEquals("SR",heroInitials("Sample Rockets"));assertEquals("SW",heroInitials("Sample Wolves"))
  assertEquals("TEN",heroInitials("Tennessee"));assertEquals("",heroInitials(""))
 }
 /** The shared hero background is gradient/glow only: no stroked diagonal accent lines on either side. */
 @Test fun sharedHeroBackgroundDrawsNoDiagonalAccentLines(){
  val src=File("src/main/java/com/sgsports/tv/HeroBroadcast.kt").readText()
  assertFalse("drawLine",src.contains("drawLine"))
  assertFalse("diagonal accents",src.contains("diagonal",ignoreCase=true))
  assertTrue(src.contains("BroadcastHeroBackground"))
 }
 @Test fun crestsFollowTeamIdentityNotListOrder(){
  // Roster team's crest is listed first although the event names it second.
  val teams=listOf(team("t1","FC Barcelona",0xFFA50044).copy(sport="Soccer",logo="logos/bcn.png",anyLeague=true))
  val g=Game("g","Paris Saint Germain vs Barcelona","UEFA","Soccer","",logos=listOf("logos/bcn.png","logos/psg.png"),raw=JSONObject().put("sides",JSONArray(listOf("paris saint germain",teamKey("FC Barcelona")))),ranks=listOf(3,null),eventPhase="upcoming")
  val s=heroSides(g,teams)
  assertEquals(listOf("logos/psg.png","logos/bcn.png"),s.map{it.logo})
  assertEquals(listOf<Int?>(null,3),s.map{it.rank})
  assertEquals(listOf<Int?>(null,0xFFA50044.toInt()),s.map{it.color})
 }
}
