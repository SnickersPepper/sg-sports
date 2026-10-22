package com.sgsports.tv

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * Compose-free palette for the hero's generated matchup background (deterministic, offline).
 *
 * Inputs are the two participants' canonical team colors (opaque 0xAARRGGBB, null when unknown or
 * malformed). Output is a left/right pair that is always dark enough for the hero's light text and
 * always distinguishable, without inventing a team identity for a side whose color is unknown.
 */
data class HeroPalette(val left:Int,val right:Int,val teamLeft:Boolean,val teamRight:Boolean)

/** Parses "RRGGBB" / "#RRGGBB" (optionally 0x-prefixed); anything else is null instead of an exception. */
fun parseHexColor(raw:String?):Long?{
 val s=raw?.trim()?.removePrefix("#")?.removePrefix("0x")?.removePrefix("0X")?:return null
 if(s.length!=6||!s.all{it in '0'..'9'||it in 'a'..'f'||it in 'A'..'F'})return null
 return 0xFF000000L or s.toLong(16)
}

// Both edges glow with their team color over a dark center; the glow is drawn at most ~60% over HERO_BASE under
// the names, so a cap of 0.24 keeps light text at >=4.5:1 on either side.
private const val MAX_LUM=0.24
private const val MIN_LUM=0.02
private const val MIN_DISTANCE=80.0
val HERO_BASE=0xFF070B12.toInt()
val HERO_NEUTRAL_LEFT=0xFF18222E.toInt()
val HERO_NEUTRAL_RIGHT=0xFF3B4859.toInt()

private fun chan(c:Int,shift:Int)=(c shr shift) and 0xFF
private fun lin(v:Int):Double{val s=v/255.0;return if(s<=0.03928)s/12.92 else ((s+0.055)/1.055).pow(2.4)}
fun relativeLuminance(c:Int):Double=0.2126*lin(chan(c,16))+0.7152*lin(chan(c,8))+0.0722*lin(chan(c,0))
fun contrastRatio(a:Int,b:Int):Double{val x=relativeLuminance(a);val y=relativeLuminance(b);return (max(x,y)+0.05)/(min(x,y)+0.05)}
/** "Redmean" RGB distance: cheap perceptual approximation, 0..~765. */
fun colorDistance(a:Int,b:Int):Double{
 val rm=(chan(a,16)+chan(b,16))/2.0
 val dr=(chan(a,16)-chan(b,16)).toDouble();val dg=(chan(a,8)-chan(b,8)).toDouble();val db=(chan(a,0)-chan(b,0)).toDouble()
 return sqrt((2+rm/256)*dr*dr+4*dg*dg+(2+(255-rm)/256)*db*db)
}

private fun toHsl(c:Int):DoubleArray{
 val r=chan(c,16)/255.0;val g=chan(c,8)/255.0;val b=chan(c,0)/255.0
 val mx=max(r,max(g,b));val mn=min(r,min(g,b));val l=(mx+mn)/2;val d=mx-mn
 if(d==0.0)return doubleArrayOf(0.0,0.0,l)
 val s=d/(1-abs(2*l-1))
 val h=when(mx){r->((g-b)/d).mod(6.0);g->(b-r)/d+2;else->(r-g)/d+4}*60
 return doubleArrayOf(h,s,l)
}
private fun fromHsl(h:Double,s:Double,l:Double):Int{
 val c=(1-abs(2*l-1))*s;val x=c*(1-abs((h/60).mod(2.0)-1));val m=l-c/2
 val (r,g,b)=when((h/60).toInt().coerceIn(0,5)){0->Triple(c,x,0.0);1->Triple(x,c,0.0);2->Triple(0.0,c,x);3->Triple(0.0,x,c);4->Triple(x,0.0,c);else->Triple(c,0.0,x)}
 fun q(v:Double)=((v+m)*255).let{Math.round(it).toInt().coerceIn(0,255)}
 return (0xFF shl 24) or (q(r) shl 16) or (q(g) shl 8) or q(b)
}
/** Same hue and saturation, lightness searched so the relative luminance lands on [lum]. */
private fun withLuminance(c:Int,lum:Double):Int{
 val hsl=toHsl(c);var lo=0.0;var hi=1.0
 repeat(24){val mid=(lo+hi)/2;if(relativeLuminance(fromHsl(hsl[0],hsl[1],mid))<lum)lo=mid else hi=mid}
 return fromHsl(hsl[0],hsl[1],(lo+hi)/2)
}
private fun clampLuminance(c:Int,max:Double):Int{val l=relativeLuminance(c);return if(l>max)withLuminance(c,max) else if(l<MIN_LUM)withLuminance(c,MIN_LUM) else c}

/** A muted, desaturated companion of a valid color: derived from it, so a missing side never borrows another identity. */
private fun derivedNeutral(c:Int,max:Double):Int{
 val hsl=toHsl(c)
 return clampLuminance(fromHsl(hsl[0],min(hsl[1],0.22),0.22),max)
}

/** [left]/[right] are the participants' team colors in event order; null = unknown or malformed. */
fun heroPalette(left:Int?,right:Int?):HeroPalette{
 val l=left?.let{clampLuminance(it,MAX_LUM)};val r=right?.let{clampLuminance(it,MAX_LUM)}
 var a:Int;var b:Int
 when{
  l!=null&&r!=null->{a=l;b=r}
  l!=null->{a=l;b=derivedNeutral(l,MAX_LUM)}
  r!=null->{a=derivedNeutral(r,MAX_LUM);b=r}
  else->return HeroPalette(HERO_NEUTRAL_LEFT,HERO_NEUTRAL_RIGHT,false,false)
 }
 if(colorDistance(a,b)<MIN_DISTANCE){
  // Too close to read as a matchup: spread luminance only, keeping each side's own hue.
  // One side goes deep, the other rich; the right is the lighter one.
  a=withLuminance(a,0.06);b=withLuminance(b,MAX_LUM)
 }
 return HeroPalette(a,b,l!=null,r!=null)
}

/** One side of the matchup as the hero presents it. Blank [name] = no such participant; null [color] = unknown. */
data class HeroSide(val name:String,val logo:String,val rank:Int?,val color:Int?)

private val TITLE_SPLIT=Regex("\\s+(?:vs\\.?|v|at|@)\\s+",RegexOption.IGNORE_CASE)

/**
 * The two sides in presentation order (away left, home right). Order and names come from the live score block when
 * present, else the backend's canonical `sides`, else the display title's two halves; colors resolve through the roster by
 * canonical key, sport and league -- never list position or title substring. Logos/ranks follow the game's own
 * home/away-ordered artwork only when it carries both crests; a lone crest is attached solely to the roster team
 * that matched this game. A game with one identifiable participant yields that side first and a blank second side.
 */
fun heroSides(game:Game,teams:List<Team>,catalog:TeamBrandCatalog?=null):List<HeroSide>{
 val live=game.live
 val sideKeys=game.raw.strings("sides").filter{it.isNotBlank()}
 val parts=game.title.split(TITLE_SPLIT).map{it.trim().substringAfterLast(" · ")}.filter{it.isNotEmpty()}
 fun rosterTeam(key:String)=teams.firstOrNull{t->teamKey(t.name)==key&&t.sport==game.sport&&(t.anyLeague||t.league==game.league)}
 val matched=teams.firstOrNull{it.id==game.teamId}
 val names:List<String>;val keys:List<String>;val ranks:List<Int?>
 when{
  live!=null&&live.home.isNotBlank()&&live.away.isNotBlank()->{names=listOf(live.away,live.home);keys=names.map(::teamKey);ranks=listOf(live.awayRank,live.homeRank)}
  sideKeys.size==2->{keys=sideKeys;names=if(parts.size==2)parts else keys.map{k->rosterTeam(k)?.name?:catalog?.resolve(k,game.sport,game.league)?.name?:k};ranks=if(game.logos.size==2)game.ranks else emptyList()}
  parts.size==2->{names=parts;keys=parts.map(::teamKey);ranks=if(game.logos.size==2)game.ranks else emptyList()}
  else->{
   val color=matched?.takeIf{it.colorKnown}?.color?.toInt()
   return listOf(HeroSide(game.title,matched?.logo?:game.logos.firstOrNull().orEmpty(),game.ranks.firstOrNull(),color),HeroSide("","",null,null))
  }
 }
 // Crest -> side by identity first: a roster team owns its own crest path. With both crests present and one side
 // identified, the other side takes the remaining crest; with no identity at all the game's own order stands.
 val twoCrests=game.logos.size==2
 val brands=keys.map{k->catalog?.resolve(k,game.sport,game.league)}
 val owned=keys.indices.map{i->(rosterTeam(keys[i])?.logo?:brands[i]?.logo)?.takeIf{it in game.logos}}
 val logoIndex=keys.indices.map{i->
  when{
   !twoCrests->-1
   owned[i]!=null->game.logos.indexOf(owned[i])
   owned[1-i]!=null->1-game.logos.indexOf(owned[1-i])
   else->i
  }
 }
 return keys.indices.map{i->
  val t=rosterTeam(keys[i])
  val logo=when{owned[i]!=null->owned[i]!!;brands[i]?.logo?.isNotBlank()==true&&logoIndex[i]<0->brands[i]!!.logo;logoIndex[i]>=0&&(brands[i]==null||brands[i]!!.logo.isBlank())->game.logos[logoIndex[i]];matched!=null&&teamKey(matched.name)==keys[i]->matched.logo;else->brands[i]?.logo.orEmpty()}
  val rank=if(live!=null&&live.home.isNotBlank()&&live.away.isNotBlank())ranks.getOrNull(i) else if(logoIndex[i]>=0)game.ranks.getOrNull(logoIndex[i]) else null
  HeroSide(names[i],logo,rank,t?.takeIf{it.colorKnown}?.color?.toInt()?:brands[i]?.color)
 }
}

/** Just the two colors of [heroSides], for palette selection. */
fun heroParticipantColors(game:Game,teams:List<Team>,catalog:TeamBrandCatalog?=null):Pair<Int?,Int?>{val s=heroSides(game,teams,catalog);return s[0].color to s[1].color}

enum class HeroArt { PERSONALIZED, MATCHUP }
/** Personalized artwork is always the first choice; the generated matchup is only for its absence. */
fun heroArt(personalizedAvailable:Boolean)=if(personalizedAvailable)HeroArt.PERSONALIZED else HeroArt.MATCHUP
