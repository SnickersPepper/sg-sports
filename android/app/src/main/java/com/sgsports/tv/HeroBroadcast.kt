package com.sgsports.tv

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.material3.Text

/*
 * The one featured-game hero. Every carousel entry -- live, scheduled, final, with or without personalized
 * artwork or branding -- is drawn by [Hero] -> [BroadcastHeroBackground] + [TeamColumn] x2 + [ScoreboardColumn].
 * Branding is resolved by heroSides()/heroPalette() (HeroPalette.kt); nothing here is keyed to a team or matchup.
 */

private val HERO_HEIGHT=320.dp
private val GOLD=SgTheme.Gold

/** Personalized artwork, when bundled, is only the bottom layer: the common team-color treatment is always drawn over it. */
@Composable fun BoxScope.BroadcastHeroBackground(game:Game,teams:List<Team>,palette:HeroPalette){
 val team=teams.firstOrNull{it.id==game.teamId}
 val variant=remember(game.id){Math.floorMod(game.id.fold(0){h,c->31*h+c.code},3)}
 val context=LocalContext.current
 val photo=team?.let{t->rememberAssetImage(context,"banners/${t.id}-${variant+1}.jpg")}
 val left=Color(palette.left);val right=Color(palette.right)
 Box(Modifier.matchParentSize().background(Color(HERO_BASE)))
 if(heroArt(photo!=null)==HeroArt.PERSONALIZED&&photo!=null){
  Image(photo,null,Modifier.matchParentSize().testTag("hero-art-personalized").graphicsLayer{alpha=.38f},contentScale=ContentScale.Crop)
 }
 Canvas(Modifier.matchParentSize().testTag("hero-art-matchup")){
  val w=size.width;val h=size.height
  // Team-color lighting from each outer edge.
  drawRect(Brush.radialGradient(listOf(left.copy(alpha=.78f),left.copy(alpha=.34f),Color.Transparent),center=Offset(w*.04f,h*.5f),radius=w*.52f))
  drawRect(Brush.radialGradient(listOf(right.copy(alpha=.78f),right.copy(alpha=.34f),Color.Transparent),center=Offset(w*.96f,h*.5f),radius=w*.52f))
  drawRect(Brush.radialGradient(listOf(left.copy(alpha=.22f),Color.Transparent),center=Offset(w*.16f,h*.0f),radius=w*.3f))
  drawRect(Brush.radialGradient(listOf(right.copy(alpha=.22f),Color.Transparent),center=Offset(w*.84f,h*.0f),radius=w*.3f))
  // Deep dark center keeps the scoreboard the focus.
  drawRect(Brush.radialGradient(listOf(Color(HERO_BASE).copy(alpha=.92f),Color(HERO_BASE).copy(alpha=.55f),Color.Transparent),center=Offset(w*.5f,h*.5f),radius=w*.34f))
  drawRect(Brush.verticalGradient(listOf(Color.Transparent,Color.Black.copy(alpha=.35f))))
 }
}

/** Placeholder monogram for a side with no usable crest: word initials, never a guessed identity. */
internal fun heroInitials(name:String):String{
 val words=name.split(Regex("[^\\p{L}\\p{N}]+")).filter{it.isNotEmpty()}
 return when{words.isEmpty()->"";words.size==1->words[0].take(3);else->words.take(3).joinToString(""){it.take(1)}}.uppercase()
}

@Composable private fun LogoImage(path:String,label:String,modifier:Modifier,alpha:Float=1f):Boolean{
 if(path.isBlank())return false
 if(path=="lady_vols"){
  Image(androidx.compose.ui.res.painterResource(R.drawable.lady_vols),label,modifier.graphicsLayer{this.alpha=alpha},contentScale=ContentScale.Fit);return true
 }
 val bitmap=rememberAssetImage(LocalContext.current,path)?:return false
 Image(bitmap,label,modifier.graphicsLayer{this.alpha=alpha},contentScale=ContentScale.Fit)
 return true
}

@Composable private fun HeroText(text:String,size:Int,color:Color=SgTheme.Text,bold:Boolean=true,modifier:Modifier=Modifier,lines:Int=1,spacing:Float=0f){
 Text(text,color=color,fontSize=size.sp,lineHeight=(size*1.2f).sp,fontWeight=if(bold)FontWeight.Bold else FontWeight.Normal,letterSpacing=spacing.sp,maxLines=lines,overflow=TextOverflow.Ellipsis,textAlign=TextAlign.Center,modifier=modifier)
}

@Composable private fun TeamColumn(side:HeroSide,sport:String,league:String,color:Color,tag:String,modifier:Modifier){
 Box(modifier.fillMaxHeight()){
  // Oversized outer-edge watermark of the real crest (only when one resolved), kept faint and cropped by the card.
  if(side.logo.isNotBlank()){
   val outerStart=tag=="away"
   Box(Modifier.align(if(outerStart)Alignment.CenterStart else Alignment.CenterEnd).offset(x=if(outerStart)(-70).dp else 70.dp).size(300.dp)){LogoImage(side.logo,"",Modifier.fillMaxSize(),.13f)}
  }
  Column(Modifier.align(Alignment.Center).fillMaxWidth(),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(6.dp)){
   Box(Modifier.size(112.dp),contentAlignment=Alignment.Center){
    Canvas(Modifier.fillMaxSize()){drawCircle(Brush.radialGradient(listOf(color.copy(alpha=.55f),Color.Transparent)),radius=size.minDimension*.62f)}
    if(side.name.isBlank())SportGlyph(sport,64)
    else if(!LogoImage(side.logo,side.name,Modifier.size(96.dp).testTag("hero-$tag-logo")))
     Box(Modifier.size(84.dp).clip(RoundedCornerShape(14.dp)).background(Color(0xFF151B24)).border(1.dp,color.copy(alpha=.6f),RoundedCornerShape(14.dp)),contentAlignment=Alignment.Center){HeroText(heroInitials(side.name),24)}
   }
   Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)){
    side.rank?.let{HeroText("$it",20,SgTheme.Muted,modifier=Modifier.testTag("hero-$tag-rank"))}
    HeroText(side.name.ifBlank{sport.uppercase()}.uppercase(),22,lines=2,spacing=.5f,modifier=Modifier.testTag("hero-$tag-name"))
   }
   if(side.name.isBlank())HeroText(league.uppercase(),12,SgTheme.Muted,false,spacing=2f)
  }
 }
}

@Composable private fun StatePill(text:String,tint:Color,dot:Boolean,modifier:Modifier=Modifier){
 Row(modifier.clip(RoundedCornerShape(50)).background(tint.copy(alpha=.16f)).border(1.dp,tint.copy(alpha=.6f),RoundedCornerShape(50)).padding(horizontal=14.dp,vertical=3.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(6.dp)){
  if(dot)Box(Modifier.size(9.dp).clip(RoundedCornerShape(50)).background(tint))
  HeroText(text,13,tint,spacing=1.5f)
 }
}

@Composable private fun ScoreboardColumn(game:Game,ctaLabel:String,ctaPlays:Boolean,ctaFocused:Boolean,modifier:Modifier){
 val live=game.live;val phase=game.phase()
 val hasScore=live?.homeScore!=null&&live.awayScore!=null&&live.state!="pre"&&phase in setOf(GamePhase.LIVE,GamePhase.FINAL,GamePhase.REPLAY,GamePhase.SUSPENDED)
 val clock=game.clockText()
 Column(modifier.fillMaxHeight(),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.Center){
  Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp)){
   Box(Modifier.weight(1f).height(1.dp).background(Color.White.copy(alpha=.18f)))
   HeroText(game.league.uppercase(),12,SgTheme.Muted,false,spacing=3f)
   Box(Modifier.weight(1f).height(1.dp).background(Color.White.copy(alpha=.18f)))
  }
  Spacer(Modifier.height(6.dp))
  val label=game.phaseLabel()
  when(phase){
   GamePhase.LIVE->StatePill("LIVE",SgTheme.Live,true,Modifier.testTag("hero-state"))
   GamePhase.REPLAY->StatePill("REPLAY",SgTheme.Replay,false,Modifier.testTag("hero-state"))
   GamePhase.UPCOMING->StatePill(if(label=="PREGAME")"PREGAME" else "UPCOMING",GOLD,false,Modifier.testTag("hero-state"))
   else->StatePill(label,SgTheme.Muted,false,Modifier.testTag("hero-state"))
  }
  if(hasScore){
   HeroText("${live.awayScore} - ${live.homeScore}",60,modifier=Modifier.testTag("hero-score"))
  } else {
   HeroText("VS",44,SgTheme.Muted,modifier=Modifier.testTag("hero-score"))
  }
  // One gold status line: the backend clock when live, else the scheduled time; never a locally-running clock.
  val gold=clock?:game.subtitle.takeIf{phase==GamePhase.UPCOMING&&it.isNotBlank()}
  if(gold!=null)HeroText(gold,18,GOLD,modifier=Modifier.testTag(if(clock!=null)"hero-clock" else "hero-time"))
  val freshness=live?.takeIf{phase==GamePhase.LIVE}?.freshness()
  val detail=listOfNotNull(live?.detail?.takeIf{it.isNotBlank()&&it!=clock&&phase!=GamePhase.UPCOMING},freshness).joinToString(" · ")
  if(detail.isNotEmpty())HeroText(detail,12,if(live?.isStale()==true)GOLD else SgTheme.Muted,false)
  Spacer(Modifier.height(8.dp))
  Row(Modifier.clip(RoundedCornerShape(10.dp)).background(if(ctaFocused)SgTheme.Text else Color(0xFF111821)).border(1.dp,if(ctaFocused)SgTheme.Text else Color.White.copy(alpha=.22f),RoundedCornerShape(10.dp)).padding(horizontal=26.dp,vertical=9.dp).testTag("hero-cta"),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)){
   if(ctaPlays)HeroText("▶",13,if(ctaFocused)Color(0xFF0B0F16) else SgTheme.Text)
   HeroText(ctaLabel,15,if(ctaFocused)Color(0xFF0B0F16) else SgTheme.Text,spacing=.5f,modifier=Modifier.testTag("hero-cta-label"))
  }
 }
}

/** Shared hero for every featured entry. Whole card is the single focus target; routing/eligibility are unchanged. */
@Composable fun Hero(game:Game,teams:List<Team>,onWatchNow:(Game)->Unit={},onBrowse:(Game)->Unit={}){
 // Auto-play only makes sense once there's realistically something to play: already live, or within the
 // backend's 30-minute pregame tune-in window. Any further out, the card opens the channel list instead.
 val secondsToStart=game.startEpoch()-System.currentTimeMillis()/1000
 val startsSoon=game.live?.verifiedLive==true||(game.startEpoch()>0&&secondsToStart in 0..1800)
 val context=LocalContext.current
 val catalog=remember{TeamBrandCatalog.load(context)}
 val sides=remember(game,teams){heroSides(game,teams,catalog)}
 val palette=remember(sides){heroPalette(sides[0].color,sides[1].color)}
 var focused by remember{mutableStateOf(false)}
 val cta=when{game.phase()==GamePhase.LIVE->"Watch Live";startsSoon->"Tune In Early";else->"Game Details"}
 SgCard(Modifier.fillMaxWidth().height(HERO_HEIGHT).onFocusChanged{focused=it.hasFocus},Color(palette.left),Color(palette.right),onClick={if(startsSoon)onWatchNow(game) else onBrowse(game)},contentPadding=0.dp,focusKey="home-hero"){
  Box(Modifier.fillMaxSize()){
   BroadcastHeroBackground(game,teams,palette)
   Row(Modifier.fillMaxSize().padding(horizontal=20.dp,vertical=10.dp),verticalAlignment=Alignment.CenterVertically){
    TeamColumn(sides[0],game.sport,game.league,Color(palette.left),"away",Modifier.weight(1f))
    ScoreboardColumn(game,cta,game.phase()==GamePhase.LIVE||startsSoon,focused,Modifier.weight(1.3f))
    TeamColumn(sides[1],game.sport,game.league,Color(palette.right),"home",Modifier.weight(1f))
   }
  }
 }
}
