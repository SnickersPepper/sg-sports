package com.sgsports.tv

import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import org.json.JSONObject
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.*
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private class FocusMemory {
 val requests=linkedMapOf<String,FocusRequester>()
 val last=mutableMapOf<String,String>()
 val rowLast=mutableMapOf<String,String>()
 /** Registered item ids of a page when focus last moved on it, so a vanished item restores to its nearest neighbour. */
 val order=mutableMapOf<String,List<String>>()
 val rowStates=mutableMapOf<String,LazyListState>()
}
private val LocalFocusMemory=staticCompositionLocalOf<FocusMemory?> {null}
private val LocalPage=staticCompositionLocalOf {""}
private val LocalRow=staticCompositionLocalOf<String?> {null}
/** Phase 2 design-system tokens (artifacts/phase2/DESIGN-SYSTEM.md). Legacy names map to the documented roles. */
object SgTheme {
 val Canvas=Color(0xFF000000);val Surface=Color(0xFF101214);val Surface2=Color(0xFF181B1F);val StrokeRest=Color(0xFF343A40)
 val Text=Color(0xFFF4F1E8);val Muted=Color(0xFFAAA9A6);val Green=Color(0xFF2EF58B)
 val Live=Color(0xFFF43F5E);val Gold=Color(0xFFF2B84B);val Replay=Color(0xFF6AA4FF)
}
@Composable fun Label(text:String,size:Int=14,color:Color=SgTheme.Text,bold:Boolean=false,modifier:Modifier=Modifier,lines:Int=2){Text(text,color=color,fontSize=size.sp,lineHeight=(size*1.25f).sp,fontWeight=if(bold)FontWeight.SemiBold else FontWeight.Normal,maxLines=lines,overflow=TextOverflow.Ellipsis,modifier=modifier)}
// Every crest/logo/background asset used to decode synchronously on the main thread inside
// `remember`, once per composable instance -- the same team crest was re-read-and-decoded from
// disk on every single GameCard that showed it, and the new full-screen JPEG backgrounds (much
// bigger than the old small PNG crests) blocked the UI thread on every page switch. This is the
// actual cause of the reported startup/navigation lag, not something vaguer. Fix: decode once
// per path for the whole process (a plain in-memory cache, since the asset set is fixed for the
// app's lifetime) and do the decode off the main thread, so composition never blocks on it.
private val assetImageCache=java.util.concurrent.ConcurrentHashMap<String,androidx.compose.ui.graphics.ImageBitmap>()
// ConcurrentHashMap rejects null values: a missing/undecodable asset is remembered here instead of cached as null.
private val assetImageMisses=java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
@Composable fun rememberAssetImage(context:android.content.Context,path:String):androidx.compose.ui.graphics.ImageBitmap?{
 if(path.isEmpty())return null
 assetImageCache[path]?.let{return it}
 var image by remember(path){mutableStateOf(assetImageCache[path])}
 LaunchedEffect(path){
  if(assetImageCache.containsKey(path)||assetImageMisses.contains(path))return@LaunchedEffect
  val key=ArtworkKeys.keyFor(path)
  val bundled=if(path.startsWith(ArtworkKeys.PREFIX))null else kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO){
   runCatching{context.assets.open(path).use{stream->
    // Backgrounds are opaque JPEGs -- RGB_565 halves the in-memory bitmap size (no alpha
    // channel to lose); crest/logo PNGs keep full ARGB for their transparency.
    val opts=if(path.startsWith("banners/"))android.graphics.BitmapFactory.Options().apply{inPreferredConfig=android.graphics.Bitmap.Config.RGB_565} else null
    android.graphics.BitmapFactory.decodeStream(stream,null,opts)?.asImageBitmap()
   }}.getOrNull()
  }
  // No bundled copy (the public build bundles none): ask the configured backend, which serves its cached art, and keep the last good copy on the device.
  val decoded=bundled?:key?.let{k->ArtworkRuntime.load(k)?.let{bytes->kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO){decodeBounded(bytes)}}}
  if(decoded!=null)assetImageCache[path]=decoded else if(key==null)assetImageMisses.add(path)   // backend art is retried later (the repository rate-limits it)
  image=decoded
 }
 return image
}
/** Decode downloaded artwork, sampling very large images down so a logo never costs more than a few MB of memory. */
private fun decodeBounded(bytes:ByteArray):androidx.compose.ui.graphics.ImageBitmap? = runCatching{
 val bounds=android.graphics.BitmapFactory.Options().apply{inJustDecodeBounds=true}
 android.graphics.BitmapFactory.decodeByteArray(bytes,0,bytes.size,bounds)
 var sample=1;while(bounds.outWidth/sample>1024||bounds.outHeight/sample>1024)sample*=2
 android.graphics.BitmapFactory.decodeByteArray(bytes,0,bytes.size,android.graphics.BitmapFactory.Options().apply{inSampleSize=sample})?.asImageBitmap()
}.getOrNull()
@Composable fun MonogramMark(label:String,size:Int){
 val shape=RoundedCornerShape((size/4).coerceAtLeast(6).dp);val hue=Monogram.hue(label)
 Box(Modifier.size(size.dp).clip(shape).background(Color.hsv(hue,0.45f,0.40f)).border(1.dp,Color.hsv(hue,0.35f,0.75f).copy(alpha=0.55f),shape),contentAlignment=Alignment.Center){Label(Monogram.initials(label),(size*0.38f).toInt().coerceIn(9,26),Color.White,true,lines=1)}
}
@Composable fun Mark(path:String,label:String,size:Int=38,rank:Int?=null,rankOnRight:Boolean=false){
 Box {
  if(path=="lady_vols")Image(androidx.compose.ui.res.painterResource(R.drawable.lady_vols),label,Modifier.size(size.dp))
  else {
   val context=LocalContext.current
   val bitmap=rememberAssetImage(context,path)
   if(bitmap!=null)Image(bitmap,contentDescription=label,modifier=Modifier.size(size.dp)) else MonogramMark(label,size)
  }
  // AP-style rank number for a ranked team -- a plain white numeral just outside the crest's
  // bottom-inner corner (the side facing the score between the two logos), never overlapping
  // the crest itself. Only ever shown when the backend actually reported a curated rank, never
  // guessed. 10dp keeps this inside SgCard's own 13dp padding in the tightest context
  // (Scoreboard's logos sit flush against the card edge).
  if(rank!=null)Label("$rank",if(size<30)11 else 14,Color.White,true,modifier=Modifier.align(if(rankOnRight)Alignment.BottomStart else Alignment.BottomEnd).offset(x=if(rankOnRight)(-10).dp else 10.dp))
 }
}
@Composable fun SportGlyph(sport:String,size:Int=22){
 Canvas(Modifier.size(size.dp)) {
  val w=this.size.width;val h=this.size.height;val ink=if(sport=="Softball")Color(0xFFFFDA44) else SgTheme.Text
  when(sport){
   "Golf"->{drawLine(ink,androidx.compose.ui.geometry.Offset(w*.3f,h*.1f),androidx.compose.ui.geometry.Offset(w*.3f,h*.9f),w*.07f);val p=androidx.compose.ui.graphics.Path().apply{moveTo(w*.35f,h*.1f);lineTo(w*.85f,h*.3f);lineTo(w*.35f,h*.5f);close()};drawPath(p,ink)}
   "Football"->{drawOval(ink,topLeft=androidx.compose.ui.geometry.Offset(0f,h*.2f),size=androidx.compose.ui.geometry.Size(w,h*.6f));drawLine(Color.Black,androidx.compose.ui.geometry.Offset(w*.2f,h*.5f),androidx.compose.ui.geometry.Offset(w*.8f,h*.5f),w*.06f);for(i in 3..7)drawLine(Color.Black,androidx.compose.ui.geometry.Offset(w*i/10,h*.35f),androidx.compose.ui.geometry.Offset(w*i/10,h*.65f),w*.035f)}
   else->{drawCircle(ink,w*.45f);if(sport=="Soccer"){
    val polygon=androidx.compose.ui.graphics.Path()
    val points=(0..4).map { i->val a=Math.PI*2*i/5-Math.PI/2;androidx.compose.ui.geometry.Offset(w*.5f+kotlin.math.cos(a).toFloat()*w*.19f,h*.5f+kotlin.math.sin(a).toFloat()*h*.19f) }
    points.forEachIndexed {i,p->if(i==0)polygon.moveTo(p.x,p.y) else polygon.lineTo(p.x,p.y)};polygon.close();drawPath(polygon,Color.Black)
    points.forEach { p-> val x=w*.5f+(p.x-w*.5f)*2.15f;val y=h*.5f+(p.y-h*.5f)*2.15f;drawLine(Color.Black,p,androidx.compose.ui.geometry.Offset(x,y),w*.045f);drawCircle(Color.Black,w*.075f,androidx.compose.ui.geometry.Offset(x,y)) }
    drawCircle(Color.Black,w*.45f,style=androidx.compose.ui.graphics.drawscope.Stroke(w*.035f))
   } else {drawArc(Color(0xFFB32636),-65f,130f,false,topLeft=androidx.compose.ui.geometry.Offset(-w*.15f,h*.1f),size=androidx.compose.ui.geometry.Size(w*.6f,h*.8f),style=androidx.compose.ui.graphics.drawscope.Stroke(w*.07f));drawArc(Color(0xFFB32636),115f,130f,false,topLeft=androidx.compose.ui.geometry.Offset(w*.55f,h*.1f),size=androidx.compose.ui.geometry.Size(w*.6f,h*.8f),style=androidx.compose.ui.graphics.drawscope.Stroke(w*.07f))}}
  }
 }
}
@Composable fun LeagueMark(league:String,size:Int=30){if(league in listOf("Football","Soccer","Baseball","Softball","Golf","Majors")){SportGlyph(if(league=="Majors")"Golf" else league,size);return};val context=LocalContext.current;val map=remember { assetJson(context,"leagues.json") };Mark(map.optString(league),league,size)}
/** Focus is readable without color: 2 dp warm-white stroke, 1.035 scale (120 ms ease-out) and a
 * surface-2 lift, with layout untouched. [accent] only marks a selected (non-focused) card;
 * [secondary] is accepted for caller compatibility and no longer tints the focused surface. */
@Composable fun SgCard(modifier:Modifier=Modifier,accent:Color=SgTheme.Green,secondary:Color=SgTheme.Surface,selected:Boolean=false,enabled:Boolean=true,onFocus:()->Unit={},onClick:()->Unit,contentPadding:Dp=14.dp,focusKey:String?=null,
 /** No resting surface or outline (only the focus ring): for a card whose content paints its own full-bleed artwork. */
 bare:Boolean=false,body:@Composable ColumnScope.()->Unit){
 val bank=LocalFocusMemory.current;val page=LocalPage.current;val row=LocalRow.current;val id=if(focusKey!=null&&row!=null)"$row/$focusKey" else "$page:${focusKey?:currentCompositeKeyHash}";val requester=remember {FocusRequester()}
 DisposableEffect(id,enabled){if(enabled)bank?.requests?.set(id,requester);onDispose{bank?.requests?.remove(id)}}
 val context=LocalContext.current
 val reduceMotion=remember{android.provider.Settings.Global.getFloat(context.contentResolver,android.provider.Settings.Global.ANIMATOR_DURATION_SCALE,1f)==0f}
 var focused by remember { mutableStateOf(false) };val scale by animateFloatAsState(if(focused&&!reduceMotion)1.035f else 1f,tween(120,easing=LinearOutSlowInEasing),label="Focus scale")
 Column(modifier.semantics{this.selected=selected}.focusRequester(requester).graphicsLayer { scaleX=scale;scaleY=scale }.onFocusChanged { focused=it.isFocused;if(it.isFocused){bank?.last?.set(page,id);bank?.let{b->b.order[page]=b.requests.keys.filter{k->k.startsWith("$page:")}};if(row!=null)bank?.rowLast?.set(row,id);onFocus()} }.clip(RoundedCornerShape(12.dp)).background(if(bare)Color.Transparent else if(focused)SgTheme.Surface2 else SgTheme.Surface).border(if(focused)2.dp else if(bare)0.dp else 1.dp,if(focused)SgTheme.Text else if(selected)accent.copy(alpha=.55f) else SgTheme.StrokeRest,RoundedCornerShape(12.dp)).focusable(enabled).clickable(enabled=enabled,onClick=onClick).padding(contentPadding),verticalArrangement=Arrangement.spacedBy(7.dp),content=body)
}
/** Horizontal row that remembers its own last focused item, so leaving and re-entering a row
 * (or returning from Details/Player) lands on that same object rather than the nearest one. */
@OptIn(ExperimentalComposeUiApi::class)
@Composable fun FocusLazyRow(rowKey:String,modifier:Modifier=Modifier,horizontalArrangement:Arrangement.Horizontal=Arrangement.spacedBy(12.dp),contentPadding:PaddingValues=PaddingValues(5.dp),content:LazyListScope.()->Unit){
 val bank=LocalFocusMemory.current;val scoped="${LocalPage.current}:$rowKey"
 val listState=remember(bank,scoped){bank?.rowStates?.getOrPut(scoped){LazyListState()}?:LazyListState()}
 CompositionLocalProvider(LocalRow provides scoped){
  LazyRow(modifier.focusProperties{enter={_->bank?.rowLast?.get(scoped)?.let{bank?.requests?.get(it)}?:FocusRequester.Default}}.focusGroup(),state=listState,horizontalArrangement=horizontalArrangement,contentPadding=contentPadding,content=content)
 }
}
/** ESPN venue and TV networks for a resolved event, as a trailing " · ..." detail; empty when ESPN gave neither. */
fun venueNetworks(live:LiveScore?):String=listOfNotNull(live?.venue?.takeIf{it.isNotBlank()},live?.networks?.takeIf{it.isNotEmpty()}?.joinToString(", "))
 .joinToString(" · ",prefix=" · ").takeIf{it!=" · "}.orEmpty()
@Composable fun Section(title:String){Label(title,21,bold=true,modifier=Modifier.padding(top=9.dp,bottom=5.dp))}
/** Solid-background pill for a status word, same visual language as the "My teams" LIVE badge — used for LIVE/REPLAY so both read at a glance instead of blending into surrounding muted text. */
@Composable fun StatusBadge(text:String,accent:Color,size:Int=10,modifier:Modifier=Modifier){Label(text,size,Color.White,true,modifier=modifier.background(accent,RoundedCornerShape(4.dp)).padding(horizontal=6.dp,vertical=2.dp))}
@Composable fun FilterRow(selected:String,onSelect:(String)->Unit){FocusLazyRow("sport-filter",horizontalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(4.dp)){items(competitions.keys.toList()){sport->SgCard(selected=selected==sport,focusKey="sport:$sport",onClick={onSelect(sport)}){Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(7.dp)){LeagueMark(sport,20);Label(sport,12,bold=true)}}}}}
/** Status line shared by GameCard and Hero: prefers verified ESPN state over the EPG's own replay guess. */
@Composable fun GameStatus(game:Game,size:Int=11,color:Color=SgTheme.Text,showScore:Boolean=true){
 val live=game.live
 if(game.fixture&&game.phase()==GamePhase.UPCOMING){Label(game.phaseLabel().let{if(it=="PREGAME")it else "UPCOMING"},size,SgTheme.Gold,true);return}
 val status=game.phaseLabel()
 val metadata=listOfNotNull(if(showScore&&live?.homeScore!=null&&live.awayScore!=null)"${live.awayScore}-${live.homeScore}" else null,game.clockText()).joinToString(" · ")
 Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(5.dp)){
  if(status=="LIVE"||status=="REPLAY")StatusBadge(status,if(status=="LIVE")SgTheme.Live else SgTheme.Replay,size) else Label(status,size,if(status=="FINAL")SgTheme.Muted else color)
  if(metadata.isNotEmpty())Label(metadata,size,color)
 }
}
/** Two team marks with the live/final score between them, justified edge-to-edge; falls back to a plain logo row before kickoff. */
@Composable fun Scoreboard(game:Game,size:Int=38){
 val live=game.live
 // ESPN's own score fields default to "0" even for a game that hasn't kicked off yet — a
 // non-null score string alone doesn't mean the game has actually started. state=="pre" is
 // that not-started case; only "in"/"post" carry a real score worth showing.
 if(live?.homeScore!=null&&live.awayScore!=null&&live.state!="pre"){
  Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.SpaceBetween){
   Mark(game.logos.getOrNull(0)?:"",game.league,size,game.ranks.getOrNull(0))
   Label("${live.awayScore} - ${live.homeScore}",22,SgTheme.Text,true)
   Mark(game.logos.getOrNull(1)?:"",game.league,size,game.ranks.getOrNull(1),rankOnRight=true)
  }
 } else Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){if(game.logos.isEmpty())LeagueMark(game.league,size) else game.logos.forEachIndexed { i,it-> Mark(it,game.league,size,game.ranks.getOrNull(i),rankOnRight=i>0) }}
}
fun lvbpTint(coverage:LvbpCoverage):Color=when(coverage){LvbpCoverage.CONFIRMED->SgTheme.Green;LvbpCoverage.POSSIBLE->SgTheme.Gold;LvbpCoverage.NONE->SgTheme.Muted}
/** Schedule-first evidence for an LVBP game: what the league lists, how sure we are, how fresh. No telemetry. */
@Composable fun LvbpEvidenceCard(ev:LvbpEvidence){
 val now=System.currentTimeMillis()/1000
 val stale=ev.scheduleState!="fresh"||ev.scheduleCheckedAt==null||now-ev.scheduleCheckedAt>LVBP_FRESH_SECONDS
 Column(Modifier.fillMaxWidth().background(SgTheme.Surface,RoundedCornerShape(9.dp)).padding(14.dp),verticalArrangement=Arrangement.spacedBy(4.dp)){
  Label(ev.coverage.label(),20,lvbpTint(ev.coverage),true,Modifier.testTag("lvbp-coverage-label"))
  if(ev.detail.isNotBlank())Label(ev.detail,14,SgTheme.Text,modifier=Modifier.testTag("lvbp-detail"),lines=3)
  listOfNotNull(ev.venue,ev.gameNumber?.let{"Doubleheader · game $it"}).takeIf{it.isNotEmpty()}?.let{Label(it.joinToString(" · "),13,SgTheme.Muted)}
  Label(lvbpFreshness(ev.scheduleState,ev.scheduleCheckedAt,now),13,if(stale)SgTheme.Gold else SgTheme.Muted,modifier=Modifier.testTag("lvbp-freshness"))
 }
}
@Composable fun GameCard(game:Game,teams:List<Team>,modifier:Modifier=Modifier,onClick:()->Unit){val team=teams.firstOrNull { it.id==game.teamId };SgCard(modifier.height(220.dp).testTag("game-${game.id}"),team?.let { Color(it.color) }?:SgTheme.Green,team?.let { Color(it.secondary) }?:SgTheme.Surface,focusKey="game:${game.id}",onClick=onClick){
 Scoreboard(game)
 Label(game.title,17,bold=true,lines=2,modifier=Modifier.height(43.dp))
 Row(verticalAlignment=Alignment.CenterVertically){Label(game.league,12,SgTheme.Muted,lines=1);if(game.conferences.isNotEmpty())Label(" - "+game.conferences.joinToString("/"),12,SgTheme.Gold,true,lines=1)}
 Label(game.subtitle,11,SgTheme.Muted)
 Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){GameStatus(game,showScore=false);if(game.live?.notable==true){Spacer(Modifier.weight(1f));val why=game.live?.relevance?.reasons?.firstOrNull()?.label;if(why!=null)Label(why,11,SgTheme.Gold,true,Modifier.testTag("relevance-reason-${game.id}"),lines=1) else Label("★",13,SgTheme.Gold,true)}
  game.lvbp()?.let{ev->Spacer(Modifier.weight(1f));Label(ev.coverage.label(),11,lvbpTint(ev.coverage),true,Modifier.testTag("lvbp-card-label-${game.id}"),lines=1)}}
}}

@Composable fun TeamBannerCard(team:Team,isLive:Boolean,coverage:ScheduleCoverage?,onClick:()->Unit){
 val context=LocalContext.current
 val banner=rememberAssetImage(context,"banners/${team.banner}-1.jpg")
 SgCard(Modifier.width(230.dp).height(132.dp).testTag("team-${team.id}"),Color(team.color),Color(team.secondary),focusKey="team:${team.id}",onClick=onClick,contentPadding=0.dp){
  Box(Modifier.fillMaxSize()){
   if(banner!=null)Image(banner,team.name,Modifier.matchParentSize(),contentScale=androidx.compose.ui.layout.ContentScale.Crop)
   else Box(Modifier.matchParentSize().background(Brush.horizontalGradient(listOf(Color(team.secondary),Color(team.color).copy(alpha=.55f)))))
   Box(Modifier.matchParentSize().background(Brush.verticalGradient(listOf(Color.Black.copy(alpha=.05f),Color.Black.copy(alpha=.88f)))))
   Column(Modifier.align(Alignment.BottomStart).padding(14.dp),verticalArrangement=Arrangement.spacedBy(3.dp)){
    Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)){Mark(team.logo,team.name,32);Label(team.name,17,bold=true,lines=1)}
    val availability=when{isLive->"Live now";coverage?.status=="unavailable"->"Schedule unavailable";coverage?.status=="no_upcoming_event"->"No upcoming game listed";else->"Open team hub"}
    Label(availability,12,if(isLive)SgTheme.Green else SgTheme.Muted,lines=1)
   }
   if(isLive)StatusBadge("LIVE",SgTheme.Live,10,modifier=Modifier.align(Alignment.TopEnd).padding(10.dp))
  }
 }
}
/** The featured game stays put while the viewer is reading or navigating. Left/right changes
 * it explicitly; refresh only resets it when the candidate set itself changes. */
@Composable fun FeaturedHero(candidates:List<Game>,teams:List<Team>,onWatchNow:(Game)->Unit,onBrowse:(Game)->Unit){
 val ids=candidates.map {it.id}
 var index by remember(ids) {mutableStateOf(0)}
 val featured=candidates[index.coerceIn(0,candidates.size-1)]
 Column(Modifier.onKeyEvent {ev->
  if(ev.type!=KeyEventType.KeyDown||candidates.size<=1)return@onKeyEvent false
  when(ev.key){
   // At the first entry Left is released so focus can reach the navigation rail (otherwise the rail is unreachable from the top of Home); Right still wraps.
   Key.DirectionLeft->if(index==0)false else {index-=1;true}
   Key.DirectionRight->{index=(index+1)%candidates.size;true}
   else->false
  }
 }) {
 Hero(featured,teams,onWatchNow=onWatchNow,onBrowse=onBrowse)
  if(candidates.size>1)Row(Modifier.fillMaxWidth().padding(top=7.dp),horizontalArrangement=Arrangement.SpaceBetween){
   Label("FEATURED  ${index+1} / ${candidates.size}",11,SgTheme.Muted,true)
   Label("←  →  Browse featured games",11,SgTheme.Muted)
  }
 }
}
/** One saved or in-progress recording. Playable cards open the player; the rest only refresh, and say why. */
@Composable fun RecordingCardView(r:Recording,onPlay:(Recording)->Unit,onRefresh:()->Unit){
 val card=recordingCard(r)
 val tint=when(card.kind){RecordingUi.READY,RecordingUi.RECORDING->SgTheme.Green;RecordingUi.PARTIAL,RecordingUi.STORAGE_PRESSURE->SgTheme.Gold;else->Color(0xFFFF5468)}
 SgCard(Modifier.fillMaxWidth().testTag("recording-${r.id}"),accent=tint,focusKey=recordingFocusId(r),onClick={if(card.playable)onPlay(r) else onRefresh()}){
  Label(card.badge,13,tint,true,lines=1);Label(card.title,20,bold=true,lines=1);Label(card.detail,15,SgTheme.Muted,lines=3)
  card.action?.let{Label("$it ›",16,SgTheme.Green,true)}
 }
}
@Composable fun StatusCard(title:String,detail:String,action:String="Refresh",onAction:()->Unit){SgCard(Modifier.fillMaxWidth(),focusKey="status:$title",onClick=onAction){Label(title,22,bold=true);Label(detail,16,SgTheme.Muted,lines=3);Label("$action ›",16,SgTheme.Green,true)}}
/** Skeleton that matches the card rhythm it stands in for, so content arriving does not shift the page. */
@Composable fun LoadingSkeleton(title:String){
 Column(Modifier.fillMaxWidth().testTag("state-loading"),verticalArrangement=Arrangement.spacedBy(12.dp)){
  Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)){CircularProgressIndicator(Modifier.size(22.dp),color=SgTheme.Green,strokeWidth=2.dp);Label(title,16,SgTheme.Muted,true)}
  Row(horizontalArrangement=Arrangement.spacedBy(12.dp)){repeat(3){Box(Modifier.weight(1f).height(120.dp).clip(RoundedCornerShape(12.dp)).background(SgTheme.Surface).border(1.dp,SgTheme.StrokeRest,RoundedCornerShape(12.dp)))}}
 }
}
/** Offline, error and stale states keep the page stable: an actionable one is a single focus target, a stale note is plain text. */
@Composable fun StateBannerView(b:StateBanner,onAction:()->Unit){
 if(b.kind==BannerKind.LOADING){LoadingSkeleton(b.title);return}
 val tag="state-${b.kind.name.lowercase()}"
 if(b.action!=null){
  SgCard(Modifier.fillMaxWidth().testTag(tag),accent=SgTheme.Gold,selected=true,focusKey="status:banner",onClick=onAction){Label(b.title,22,SgTheme.Gold,true);Label(b.detail,16,SgTheme.Muted,lines=3);Label("${b.action} ›",16,SgTheme.Text,true)}
 } else Column(Modifier.fillMaxWidth().testTag(tag).background(SgTheme.Surface,RoundedCornerShape(12.dp)).border(1.dp,SgTheme.Gold.copy(alpha=.55f),RoundedCornerShape(12.dp)).padding(horizontal=16.dp,vertical=12.dp),verticalArrangement=Arrangement.spacedBy(4.dp)){Label(b.title,16,SgTheme.Gold,true);Label(b.detail,14,SgTheme.Muted,lines=2)}
}
/** Stable identity of a feed row, independent of its position in the ranked list. */
fun Feed.focusId()="feed:$provider:${raw.optString("stream_id")}:$name"
@Composable fun FeedChoiceCard(feed:Feed,onFeed:(Feed)->Unit){
 val trust=feed.trust()
 val available=trust!=FeedTrust.FAILED
 val tint=when(trust){FeedTrust.VERIFIED->SgTheme.Green;FeedTrust.CHECKED->SgTheme.Replay;FeedTrust.SCHEDULED->SgTheme.Gold;FeedTrust.LISTED,FeedTrust.FAILED->SgTheme.Muted}
 SgCard(Modifier.fillMaxWidth().testTag("feed-${feed.provider}-${feed.raw.optString("stream_id")}"),enabled=available,accent=tint,focusKey=feed.focusId(),onClick={if(available)onFeed(feed)}){
  Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(16.dp)){
   Mark(feed.logo,feed.name,46)
   Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(4.dp)){
    Label(trust.label(),14,tint,true,Modifier.testTag("feed-label-${feed.provider}-${feed.raw.optString("stream_id")}"))
    Label(feed.name,20,bold=true)
    Label("${feed.provider} · ${feed.language}",14,SgTheme.Muted)
    // A disabled source stays readable and says why it cannot be played.
    if(!available)Label(feed.verification.ifEmpty{"Could not be opened when last checked."},14,SgTheme.Muted,modifier=Modifier.testTag("feed-reason-${feed.provider}-${feed.raw.optString("stream_id")}"))
    else if(feed.verification.isNotEmpty())Label(feed.verification,14,if(trust==FeedTrust.VERIFIED)SgTheme.Green else SgTheme.Muted)
    if(feed.timing.isNotEmpty())Label(feed.timing,14,if(trust==FeedTrust.VERIFIED)SgTheme.Green else SgTheme.Gold)
   }
   Label(when(trust){FeedTrust.VERIFIED,FeedTrust.CHECKED->"Watch  ›";FeedTrust.FAILED->"Not playable";else->"Try listed feed  ›"},16,if(available)SgTheme.Text else SgTheme.Muted,true)
  }
 }
}

/** The same composables power TV, emulator and Android Studio's interactive preview. */
@OptIn(ExperimentalFoundationApi::class)
@Composable fun SgApp(state:SgState,returnFocus:Int=0,landOnHome:Int=0,initialPage:String="Home",onRefresh:()->Unit={},onGame:(Game)->Unit={},onWatchNow:(Game)->Unit={},onFeed:(Feed)->Unit={},onSetting:(String)->Unit={},onPageChange:(String)->Unit={},onFavoriteOpen:(String)->Unit={},onRecording:(Recording)->Unit={},onRecordingsRefresh:()->Unit={},onRecordingsNeeded:()->Unit={},onPrefsReload:()->Unit={},onPrefsSave:(JSONObject)->Unit={},onPrefsUseLatest:()->Unit={},onPrefsOverwrite:(JSONObject)->Unit={},onConnectionChange:()->Unit={}){
 var page by rememberSaveable { mutableStateOf(initialPage) };var previous by rememberSaveable { mutableStateOf("Home") };var sport by rememberSaveable {mutableStateOf(competitions.keys.first())};var league by rememberSaveable {mutableStateOf("")};var conf by rememberSaveable {mutableStateOf("")};var favorite by rememberSaveable {mutableStateOf("")};var hubTab by rememberSaveable {mutableStateOf(HUB_OVERVIEW)};var chosen by remember {mutableStateOf<Game?>(null)}
 LaunchedEffect(page) {onPageChange(page)}
 val readyData=(state.prefs as? PrefsUi.Ready)?.data
 var draft by remember(readyData){mutableStateOf(readyData?.let{PrefsDraft.from(it.prefs,state.legacyFavoriteSeed)})}
 LaunchedEffect(favorite) {if(favorite.isNotEmpty())onFavoriteOpen(favorite)}
 LaunchedEffect(favorite,hubTab) {if(page=="Favorite"&&hubTab==HUB_RECORDED)onRecordingsNeeded()}
 val hubUi=rememberSaveable(favorite,saver=HubUiSaver){HubUi()}
 val hubContext=LocalContext.current
 // Loaded on first use (the first team hub opened), never at startup; the lazy holder survives page changes.
 val hubCatalog=remember{lazy(LazyThreadSafetyMode.NONE){TeamBrandCatalog.load(hubContext)}}
 val bank=remember {FocusMemory()};val scrolls=remember {mutableMapOf<String,LazyListState>()};val route="$page:$favorite:$hubTab:$sport:$league:$conf"
 val scroll=scrolls.getOrPut(route){LazyListState()}
 // Choosing a hub tab (or stepping Back to Overview) opens that tab at its top, where the tab strip and the focus that lands on it are visible; returning from Details or the player keeps the saved scroll.
 fun resetHubScroll(tab:String){scrolls.remove("Favorite:$favorite:$tab:$sport:$league:$conf")}
 // landOnHome ticks on every Activity resume (see DesignActivity) -- skip the very first tick
 // (0->1 alongside the initial composition itself) since Home is already the default landing
 // spot then; only actual returns-to-foreground should force it. Folded into the same effect
 // as the focus restore below (rather than a separate LaunchedEffect on the same key) so the
 // page/bank reset always happens before that effect reads them -- two effects reacting to the
 // same key have no guaranteed ordering between them.
 var lastLanding by rememberSaveable {mutableIntStateOf(0)}
 var wasLoading by remember {mutableStateOf(state.loading)}
 LaunchedEffect(route,returnFocus,landOnHome,state.loading){
  if(landOnHome>lastLanding&&lastLanding>0){page="Home";favorite="";league="";conf="";bank.last.remove("Home")}
  lastLanding=landOnHome
  // A live cold start briefly has favorite cards but no hero while /tv/home is in flight. Do
  // not preserve the temporary team-card focus once the real hero arrives: reset Home to its
  // editorial starting point so the page opens at the top instead of already scrolled down.
  val resetHomeFocus=page=="Home"&&wasLoading&&!state.loading
  if(resetHomeFocus)bank.last.remove("Home")
  wasLoading=state.loading
  // Asset-backed rows can finish composing a frame after the route itself. Retry briefly so a
  // playback return always lands on the same card instead of leaving focus on the window.
  repeat(4){attempt->
   if(attempt>0)kotlinx.coroutines.delay(40)
   // Exact item when it still exists, else its nearest surviving neighbour, else the first item on the page.
   val target=(if(resetHomeFocus)bank.requests["Home:home-hero"] else null)
    ?:restoreTarget(bank.last[page],bank.order[page].orEmpty(),bank.requests.keys.filter{it.startsWith("$page:")}.let{keys->
     // A status banner is reachable by D-pad but never the default landing spot, unless it was the last thing focused.
     if(bank.last[page] in keys)keys else keys.filterNot{it.endsWith(":status:banner")}.ifEmpty{keys}
    })?.let{bank.requests[it]}
   target?.let{runCatching{it.requestFocus()}}
  }
 }
 fun open(game:Game){previous=page;chosen=game;page="Details";onGame(game)}
 // Same destination as open() — Details still loads normally, so an empty/failed feed list
 // lands the user on the exact same "Where to watch" picker as today. onWatchNow additionally
 // asks the host activity to auto-play the top-ranked feed once it arrives.
 fun watchNow(game:Game){previous=page;chosen=game;page="Details";onWatchNow(game)}
 BackHandler(page!="Home"){backDestination(page,hubTab,previous)?.let{d->if(d.page=="Home"){favorite=""};if(d.hubTab!=hubTab&&d.page=="Favorite"){bank.last[d.page]="${d.page}:${hubTabFocusId(d.hubTab)}";resetHubScroll(d.hubTab)};page=d.page;hubTab=d.hubTab}}
 MaterialTheme(colorScheme=darkColorScheme(background=SgTheme.Canvas,surface=SgTheme.Surface,onSurface=SgTheme.Text,primary=SgTheme.Green)){
 Row(Modifier.fillMaxSize().background(SgTheme.Canvas)){
 var railFocused by remember {mutableStateOf(false)}
 val railWidth by animateDpAsState(if(railFocused)176.dp else 82.dp,label="Navigation rail")
 Column(Modifier.width(railWidth).fillMaxHeight().background(Color(0xFF080B0D)).border(width=1.dp,color=Color(0xFF1B2024)).onFocusChanged {railFocused=it.hasFocus}.focusGroup().padding(horizontal=10.dp,vertical=20.dp),verticalArrangement=Arrangement.spacedBy(12.dp),horizontalAlignment=Alignment.CenterHorizontally){Mark("logos/sgsports.png","SG Sports",54);Spacer(Modifier.height(12.dp));MAIN_NAV.forEach {name->SgCard(Modifier.fillMaxWidth().testTag("nav-$name"),selected=page==name,focusKey="nav:$name",onClick={page=name;favorite="";hubTab=HUB_OVERVIEW;league="";conf=""},contentPadding=10.dp){Box(Modifier.fillMaxWidth().height(30.dp),contentAlignment=if(railFocused)Alignment.CenterStart else Alignment.Center){Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)){NavIcon(name,if(railFocused)20 else 23);if(railFocused)Label(name,14,bold=true,lines=1)}}}};Spacer(Modifier.weight(1f));Label(if(state.demo)"2.0 LAB" else "SG SPORTS",10,SgTheme.Green,true)}
 CompositionLocalProvider(LocalFocusMemory provides bank,LocalPage provides page){
 Box(Modifier.weight(1f).fillMaxHeight()){
 if(page=="Favorite")state.teams.firstOrNull{it.id==favorite}?.let{TeamBackdrop(it)}
 // Sports defaults to a per-sport backdrop; a selected conference (drilled in from Leagues) or
 // the NFL league card swaps in that more specific art instead.
 val leagueBanner=when{conf.isNotEmpty()->"league-${conf.lowercase().replace(' ','-')}.jpg";league=="NFL"->"league-nfl.jpg";else->null}
 if(page=="Sports")PageBackdrop(leagueBanner?:"sport-${sport.lowercase()}.jpg")
 if(page=="Leagues")PageBackdrop("sport-${sport.lowercase()}.jpg")
 LazyColumn(Modifier.fillMaxSize().testTag("main-content"),state=scroll,contentPadding=PaddingValues(26.dp,18.dp,26.dp,30.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
 // The favorite team's crest/league/record live in the sticky header itself, not a regular
 // scrollable item — D-pad focus moving into the hub below (e.g. scrolling the roster into
 // view) was otherwise carrying this row off-screen along with it, since a plain item has no
 // way to stay pinned the way the page title already does.
 // The favorite team's crest/record and the Details page's matchup summary both live in the
 // sticky header so the identity and score remain visible while long feed/roster lists scroll.
 // Home has no greeting/date/tagline bar: the featured hero is the first thing on the page.
 if(page!="Home"){val headerBody:@Composable androidx.compose.foundation.lazy.LazyItemScope.()->Unit={val hubTeam=if(page=="Favorite")state.teams.firstOrNull {it.id==favorite} else null
  val detailsGame=if(page=="Details")(chosen?:state.selected) else null
  Row(Modifier.fillMaxWidth().background(if(page=="Favorite")Color.Transparent else Color.Black).padding(vertical=8.dp),verticalAlignment=Alignment.CenterVertically){
  hubTeam?.let {t->Mark(t.logo,t.name,44);Spacer(Modifier.width(14.dp))}
  detailsGame?.let {g->Row(verticalAlignment=Alignment.CenterVertically){Mark(g.logos.getOrNull(0)?:"",g.league,40,g.ranks.getOrNull(0));if(g.logos.size>1){Spacer(Modifier.width(6.dp));Mark(g.logos[1],"Opponent",40,g.ranks.getOrNull(1),rankOnRight=true)}};Spacer(Modifier.width(14.dp))}
  Column(Modifier.weight(1f)){Label(when(page){"Details"->detailsGame?.title?:"Where to watch";"Favorite"->hubTeam?.name?:"Your team";else->page},28,bold=true)
   val subtitle=if(hubTeam!=null){
    val snapshot=state.hub.takeIf{it.teamId==hubTeam.id}?.data?.snapshot?.content()?.value
    val recordLine=listOfNotNull(snapshot?.record,snapshot?.rank?.let {"#$it"},snapshot?.standing).joinToString(" · ")
    "${hubTeam.sport} · ${hubTeam.league}"+(if(recordLine.isNotEmpty())" · $recordLine" else "")
   } else if(detailsGame!=null){
    val live=detailsGame.live
    if(!detailsGame.fixture&&live?.homeScore!=null&&live.awayScore!=null)
     detailsGame.phaseLabel()+" · ${live.awayScore}-${live.homeScore}"+listOfNotNull(detailsGame.clockText(),live.detail.takeIf{it.isNotBlank()}).joinToString(" · ",prefix=" · ").takeIf{it!=" · "}.orEmpty()+venueNetworks(live)
    else "${detailsGame.league} · ${detailsGame.subtitle}"+venueNetworks(live)
   } else when(page){"Sports"->"Programs airing now. Games, replays and studio shows.";"Leagues"->"Browse competitions by sport. SEC comes first.";"Settings"->"Customize your experience on this TV.";"Preferences"->"Your profile's favorites, competitions and playback, saved on the backend.";"Connection"->"Which backend this TV uses, and how to set it up from a phone or computer.";"Recordings"->"Games saved for you to watch later.";else->""}
   Label(subtitle,14,SgTheme.Muted)
   detailsGame?.takeIf{it.live?.notable==true}?.live?.relevance?.reasons?.firstOrNull()?.let{Label(it.label,13,SgTheme.Gold,true,Modifier.testTag("relevance-reason-details"),lines=1)}};Label(if(state.demo)"2.0 LAB · SAMPLE DATA" else SimpleDateFormat("EEE, MMM d  ·  h:mm a",Locale.getDefault()).format(Date()),12,if(state.demo)SgTheme.Green else SgTheme.Muted)}};
 // A team hub's identity bar scrolls away with its page; every other page keeps it pinned.
 if(page=="Favorite")item(content=headerBody) else stickyHeader(content=headerBody)}
 // One truthful status for the whole page: loading, offline, error or stale, decided by stateBanner().
 val hasContent=when(page){"Settings","Recordings","Preferences","Connection"->true;"Details"->state.feeds.isNotEmpty();else->state.programs.isNotEmpty()||state.upcoming.isNotEmpty()||state.later.isNotEmpty()}
 stateBanner(state,hasContent)?.takeIf{page!="Details"||it.kind!=BannerKind.STALE}?.let{b->item(key="state-banner"){StateBannerView(b,onRefresh)}}
 when(page){
 "Home"->{
 val favorites=activeFavoriteTeams(state.teams,state.prefs)
 val (favoriteLive,worthWatching)=homeSections(state.programs,favorites)
 // Editorial priority is stable: a favorite live game, then a notable live game, then the next
 // favorite fixture. Nothing advances by timer while somebody is reading the screen.
 val heroCandidates=(favoriteLive+worthWatching+state.upcoming).distinctBy {it.id}
 if(heroCandidates.isNotEmpty()){
  // With no title bar above it, keep the hero inside the TV safe area.
  item(key="home-safe-top"){Spacer(Modifier.height(16.dp))}
  item {FeaturedHero(heroCandidates,state.teams+state.brandTeams,onWatchNow=::watchNow,onBrowse=::open)}
 } else if(!state.loading)item {StatusCard("Your game day","No favorite matchup is available in the current guide. Browse Sports for other programs.","Browse sports"){page="Sports"}}
 item{Section("My teams");FocusLazyRow("my-teams",Modifier.testTag("my-teams-row")){items(favorites,key={it.id}){team->
  TeamBannerCard(team,favoriteLive.any{isFavoriteEvent(team,it)},state.coverage[team.id]){favorite=team.id;hubTab=HUB_OVERVIEW;page="Favorite"}
 }}}
 if(state.startupChecks.total>0)item(key="startup-checks"){StartupChecksLine(state.startupChecks)}
 if(favoriteLive.isNotEmpty())item{Section("Your teams live");FocusLazyRow("teams-live",horizontalArrangement=Arrangement.spacedBy(14.dp)){items(favoriteLive,key={it.id}){g->GameCard(g,state.teams,Modifier.width(256.dp)){open(g)}}}}
 if(worthWatching.isNotEmpty())item{Section("Worth watching");FocusLazyRow("worth-watching",horizontalArrangement=Arrangement.spacedBy(14.dp)){items(worthWatching,key={it.id}){g->GameCard(g,state.teams,Modifier.width(256.dp)){open(g)}}}}
 if(state.upcoming.isNotEmpty())item{Section("Upcoming");FocusLazyRow("upcoming",horizontalArrangement=Arrangement.spacedBy(14.dp)){items(state.upcoming,key={it.id}){g->GameCard(g,state.teams,Modifier.width(256.dp)){open(g)}}}}
 }
 "Sports","Favorite"->{
 if(page=="Sports")item {FilterRow(sport){sport=it;league="";conf=""}}
 if(league=="NCAA")item{FocusLazyRow("conference-filter",horizontalArrangement=Arrangement.spacedBy(9.dp),contentPadding=PaddingValues(4.dp)){item{SgCard(selected=conf.isEmpty(),focusKey="conf-filter:all",onClick={conf=""}){Label("All conferences",14)}};items(NCAA_CONFERENCES){c->SgCard(selected=conf==c,accent=SgTheme.Gold,focusKey="conf-filter:$c",onClick={conf=c}){Label(c,14,if(conf==c)SgTheme.Gold else SgTheme.Text,true)}}}}
 if(page=="Sports"&&league==LVBP)state.lvbpSchedule?.let{s->item{Label(lvbpFreshness(s.state,s.checkedAt),13,if(s.state=="fresh")SgTheme.Muted else SgTheme.Gold,modifier=Modifier.testTag("lvbp-schedule-status"))}}
 val team=state.teams.firstOrNull {it.id==favorite}
 val games=state.programs.filter {g->if(page=="Favorite")team!=null&&matches(team,g) else g.sport==sport&&(league.isEmpty()||g.league==league)&&(conf.isEmpty()||g.conferences.contains(conf))}.sortedByDescending{it.conferences.isNotEmpty()}
 if(page=="Favorite"&&team!=null){
  item(key="hub-tabs"){HubTabStrip(hubTab){tab->resetHubScroll(tab);hubTab=tab;bank.last[page]="$page:${hubTabFocusId(tab)}"}}
  if(state.demo)item(key="hub-demo"){Label("SAMPLE DATA · Design fixture. The games, players, news and recordings on this screen are illustrative.",13,SgTheme.Gold,true,Modifier.testTag("hub-demo-note"),lines=2)}
  // Another team's answer never shows here: a hub state that does not belong to this team is treated as still loading.
  val hubState=(state.hub.takeIf{it.teamId==team.id}?:TeamHubState(team.id,0,true)).withLive(games.mapNotNull{liveGameEvent(it)},state.upcoming.filter{it.teamId==team.id}.mapNotNull{fixtureEvent(it,team.name)}+(games+state.later.filter{g->matches(team,g)}).mapNotNull{programEvent(it)})
  val ctx=HubCtx(team,state.teams+state.brandTeams,hubCatalog.value,hubState,(state.upcoming+state.programs).distinctBy{it.id},hubUi,System.currentTimeMillis()/1000,java.util.TimeZone.getDefault(),Locale.getDefault(),state.demo,
   onTab={tab->resetHubScroll(tab);hubTab=tab;bank.last[page]="$page:${hubTabFocusId(tab)}"},onOpen=::open,onWatch=::watchNow,onRetry={onFavoriteOpen(favorite)})
  when(hubTab){
   HUB_SCHEDULE->scheduleItems(ctx)
   HUB_ROSTER->rosterItems(ctx)
   HUB_NEWS->newsItems(ctx)
   HUB_RECORDED->recordedHubItems(ctx,recordedView(state.recordings,recordingBucketFor(team),team.name,state.offline,state.paired,RecFilter.values().firstOrNull{it.name==hubUi.recording}?:RecFilter.ALL),onRecording,onRecordingsRefresh,onPair={page="Connection"})
   else->overviewItems(ctx)
  }
 } else {
  if(games.isEmpty()&&state.later.isEmpty()&&!state.loading)item{StatusCard("No current listings",if(!state.paired&&!state.demo)"Pair this TV in Settings > Connection to load the program guide." else "No matching program in the current guide. Coverage varies by league.",if(!state.paired&&!state.demo)"Connection" else "Refresh"){if(!state.paired&&!state.demo)page="Connection" else onRefresh()}}
  // Live games, what's coming later today, replays and studio shows each get their own
  // scrollable row instead of one mixed grid — a replay or a highlights show sorted next to an
  // actual live game read as the same thing at a glance, which is exactly what this page's own
  // subtitle already promises not to do.
  val grouped=games.groupBy {bucket(it)}
  val later=state.later.filter {g->g.sport==sport&&(league.isEmpty()||g.league==league)&&(conf.isEmpty()||g.conferences.contains(conf))&&g.isGame}
  val scheduledNow=grouped[GameBucket.UPCOMING]?:emptyList()
  val comingNext=(scheduledNow+later.filter {it.phase()==GamePhase.UPCOMING}).distinctBy {it.id}.sortedBy {it.raw.optLong("start")}
  val replays=((grouped[GameBucket.REPLAY]?:emptyList())+later.filter {it.phase()==GamePhase.REPLAY}).distinctBy {it.id}.sortedBy {it.raw.optLong("start")}
  fun LazyListScope.gameRow(label:String,row:List<Game>){if(row.isNotEmpty())item{Section("$label · ${row.size}");FocusLazyRow(label){items(row,key={it.id}){g->GameCard(g,state.teams,Modifier.width(240.dp)){open(g)}}}}}
  gameRow("Live Games",grouped[GameBucket.LIVE]?:emptyList())
  gameRow("Coming Next",comingNext)
  gameRow("On Air · Not Confirmed Live",((grouped[GameBucket.ON_NOW]?:emptyList())+later.filter {it.phase()==GamePhase.ON_NOW}).distinctBy {it.id}.sortedBy {it.raw.optLong("start")})
  gameRow("Postponed or Canceled",((grouped[GameBucket.OFF]?:emptyList())+later.filter {bucket(it)==GameBucket.OFF}).distinctBy {it.id}.sortedBy {it.raw.optLong("start")})
  if(league==LVBP&&games.isEmpty()&&later.isEmpty()&&!state.loading)item{StatusCard("No LVBP games scheduled","The league lists no games for the coming days (the season may be over or not started), or its schedule is unavailable.","Refresh",onRefresh)}
  gameRow("Replays",replays)
  gameRow("Shows",grouped[GameBucket.SHOW]?:emptyList())
 }
 }
 "Leagues"->{
 item{FilterRow(sport){sport=it}}
 state.leagues.filterKeys {it==sport}.forEach {(s,ls)->
 item{Section(s);FocusLazyRow("leagues:$s"){items(ls){l->SgCard(Modifier.width(180.dp).height(143.dp),focusKey="league:$l",onClick={sport=s;league=l;conf="";page="Sports"}){LeagueMark(l,36);Label(l,16,bold=true);Label(if(l==LVBP)"${state.later.count{it.league==l}} scheduled" else "${state.programs.count{it.sport==s&&it.league==l}} on now",12,SgTheme.Muted);if(l=="NCAA")Label("★ SEC first",11,SgTheme.Gold)}}}}
 // Each conference is its own card, linked straight to that conference's games (not just
 // NCAA generally) and tinted with its own backdrop on the Sports page it opens into. SEC
 // keeps the gold "first" treatment it already had; the rest use the app's normal accent.
 if(ls.contains("NCAA"))item{Section("Conferences");FocusLazyRow("conferences"){items(NCAA_CONFERENCES){c->SgCard(Modifier.width(160.dp),focusKey="conf:$c",accent=if(c=="SEC")SgTheme.Gold else SgTheme.Green,onClick={sport=s;league="NCAA";conf=c;page="Sports"}){LeagueMark(c,32);Label(c,14,if(c=="SEC")SgTheme.Gold else SgTheme.Text,true);Label("${state.programs.count{it.sport==s&&it.conferences.contains(c)}} on now",11,SgTheme.Muted)}}}}
 }
 }
 "Details"->{val game=chosen?:state.selected
 if(game!=null){
 val verified=state.feeds.filter {it.trust()==FeedTrust.VERIFIED}
 val checked=state.feeds.filter {it.trust()==FeedTrust.CHECKED}
 val scheduled=state.feeds.filter {it.trust()==FeedTrust.SCHEDULED}
 val listed=state.feeds.filter {it.trust()==FeedTrust.LISTED}
 val failed=state.feeds.filter {it.trust()==FeedTrust.FAILED}
 game.lvbp()?.let {ev->item{LvbpEvidenceCard(ev)}}

 if(state.verifyTimedOut&&!state.verifying)item{Label("Checking is taking longer than usual. Streams are listed with the results received so far; go back and open the game again to keep checking.",14,SgTheme.Gold,modifier=Modifier.testTag("verify-timeout"),lines=3)}
 if(state.verifying)item{Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)){CircularProgressIndicator(Modifier.size(22.dp),color=SgTheme.Green,strokeWidth=2.dp);Label("Checking each stream against this game…",14,SgTheme.Text,true)}}
 if(verified.isNotEmpty()){item{Section("Verified choices")};items(verified){feed->FeedChoiceCard(feed,onFeed)}}
 if(checked.isNotEmpty()){item{Section("Healthy streams")};item{Label("Video and audio are healthy. The broadcast image did not provide enough team evidence to confirm the game.",14,SgTheme.Muted)};items(checked){feed->FeedChoiceCard(feed,onFeed)}}
 if(scheduled.isNotEmpty()){item{Section("Scheduled broadcasts")};item{Label("Matched to this game's schedule. Content has not been confirmed yet.",14,SgTheme.Muted)};items(scheduled){feed->FeedChoiceCard(feed,onFeed)}}
 if(listed.isNotEmpty()){item{Section(if(verified.isEmpty()&&scheduled.isEmpty())"Available listings" else "Other listed feeds")};item{Label("These listings may be useful, but SG Sports has not confirmed what they are currently showing.",14,SgTheme.Muted)};items(listed){feed->FeedChoiceCard(feed,onFeed)}}
 if(failed.isNotEmpty()){item{Section("Unavailable")};items(failed){feed->FeedChoiceCard(feed,onFeed)}}
 if(state.feeds.isEmpty()&&!state.loading&&state.error.isEmpty())item{if(game.lvbp()!=null)StatusCard("No verified feed","This game stays listed for information. No channel is offered until a broadcaster is confirmed.","Refresh",onRefresh) else StatusCard("Broadcast not available yet","No scheduled feed is listed for this game. The event remains available for information and a later refresh.","Refresh",onRefresh)}
 }
 }
 "Recordings"->{
  val screen=recordingsScreen(state.recordings,state.offline)
  screen.storageNotice?.let{notice->item{Label(notice,15,SgTheme.Gold,modifier=Modifier.testTag("recordings-storage"),lines=3)}}
  if(screen.kind!=RecordingsScreenKind.CONTENT)item{StatusCard(screen.title,screen.detail,"Refresh",onRecordingsRefresh)}
  else {
   state.recordings?.buckets?.forEach{bucket->
    item(key="bucket:"+bucket.id){Section(bucket.label)}
    if(bucket.recordings.isEmpty())item(key="empty:"+bucket.id){Label("Nothing recorded yet for ${bucket.label}.",15,SgTheme.Muted,modifier=Modifier.testTag("recordings-empty-"+bucket.id))}
    else items(bucket.recordings,key={it.id}){r->RecordingCardView(r,onRecording,onRecordingsRefresh)}
   }
   item(key="rec-refresh"){SgCard(Modifier.fillMaxWidth().testTag("recordings-refresh"),focusKey="rec-refresh",onClick=onRecordingsRefresh){Label("Refresh recordings ›",16,SgTheme.Green,true)}}
  }
 }
 "Preferences"->preferencesItems(state,draft,onDraft={draft=it},onReload=onPrefsReload,onSave=onPrefsSave,onUseLatest=onPrefsUseLatest,onOverwrite=onPrefsOverwrite,onBack={page="Settings"})
 "Connection"->connectionItems(state,onChange=onConnectionChange,onPair={onSetting("pair")},onForget={onSetting("forget")})
 "Settings"->{item{Row(horizontalArrangement=Arrangement.spacedBy(12.dp)){listOf("language" to "Preferred audio","refresh" to "Refresh games").forEach{(id,title)->SgCard(Modifier.weight(1f).height(183.dp).testTag("setting-$id"),focusKey="setting:$id",accent=if(id=="forget")Color(0xFFFF5468) else SgTheme.Green,onClick={onSetting(id)}){SettingIcon(id,30);Label(title,16,bold=true);Label(when(id){"pair"->if(state.paired)"Paired with your backend" else "Enter a code from the SG Sports web app.";"language"->"${state.language}\nWhen available";"refresh"->"Reload schedules and guide listings.";else->"Remove this TV's pairing."},13,SgTheme.Muted,lines=4)}}}}
 item{Row(horizontalArrangement=Arrangement.spacedBy(12.dp)){
  SgCard(Modifier.weight(1f).height(120.dp).testTag("setting-preferences"),focusKey="setting:preferences",onClick={page="Preferences"}){Label("Preferences",16,bold=true);Label("Favorites, competitions, audio, recordings. Saved on the backend.",13,SgTheme.Muted,lines=3)}
  SgCard(Modifier.weight(1f).height(120.dp).testTag("setting-connection"),focusKey="setting:connection",onClick={page="Connection"}){Label("Connection",16,bold=true);Label("Backend address, pairing and web setup.",13,SgTheme.Muted,lines=3)}
 }}
 item{Label(if(state.demo)"Design build · no provider streams or credentials are used." else ReleaseChannel.label(BuildConfig.VERSION_NAME,BuildConfig.RELEASE_CHANNEL),12,SgTheme.Muted)}}
 }
 }
 }
 }
 }
 }
}

@Composable fun NavIcon(name:String,size:Int=23){
 Canvas(Modifier.size(size.dp)){
  val w=this.size.width;val h=this.size.height
  val stroke=androidx.compose.ui.graphics.drawscope.Stroke(w*.075f,cap=androidx.compose.ui.graphics.StrokeCap.Round,join=androidx.compose.ui.graphics.StrokeJoin.Round)
  when(name){
   "Home"->{val p=androidx.compose.ui.graphics.Path().apply{moveTo(w*.12f,h*.48f);lineTo(w*.5f,h*.15f);lineTo(w*.88f,h*.48f);moveTo(w*.23f,h*.42f);lineTo(w*.23f,h*.86f);lineTo(w*.77f,h*.86f);lineTo(w*.77f,h*.42f)};drawPath(p,SgTheme.Text,style=stroke)}
   "Sports"->{TrophyIconCanvas(w,h,stroke,this)}
   "Leagues"->{for(row in 0..1)for(col in 0..1)drawRoundRect(SgTheme.Text,androidx.compose.ui.geometry.Offset(w*(.12f+col*.46f),h*(.12f+row*.46f)),androidx.compose.ui.geometry.Size(w*.30f,h*.30f),androidx.compose.ui.geometry.CornerRadius(w*.04f),style=stroke)}
   "Recordings"->{drawRoundRect(SgTheme.Text,androidx.compose.ui.geometry.Offset(w*.08f,h*.2f),androidx.compose.ui.geometry.Size(w*.84f,h*.6f),androidx.compose.ui.geometry.CornerRadius(w*.08f),style=stroke);drawCircle(SgTheme.Text,w*.1f,androidx.compose.ui.geometry.Offset(w*.5f,h*.5f))}
   else->{drawCircle(SgTheme.Text,w*.24f,androidx.compose.ui.geometry.Offset(w*.5f,h*.5f),style=stroke);drawCircle(SgTheme.Text,w*.07f,androidx.compose.ui.geometry.Offset(w*.5f,h*.5f));for(i in 0..7){val a=Math.PI*i/4;val x1=w*.5f+kotlin.math.cos(a).toFloat()*w*.34f;val y1=h*.5f+kotlin.math.sin(a).toFloat()*h*.34f;val x2=w*.5f+kotlin.math.cos(a).toFloat()*w*.45f;val y2=h*.5f+kotlin.math.sin(a).toFloat()*h*.45f;drawLine(SgTheme.Text,androidx.compose.ui.geometry.Offset(x1,y1),androidx.compose.ui.geometry.Offset(x2,y2),w*.075f)}}
  }
 }
}

/** Purpose-drawn settings symbols keep the screen in the same visual system as navigation.
 * Font glyphs vary by TV firmware and were the last obvious prototype artifact here. */
@Composable fun SettingIcon(name:String,size:Int=30){
 val ink=if(name=="forget")Color(0xFFFF5468) else SgTheme.Text
 Canvas(Modifier.size(size.dp)){
  val w=this.size.width;val h=this.size.height
  val stroke=androidx.compose.ui.graphics.drawscope.Stroke(w*.065f,cap=androidx.compose.ui.graphics.StrokeCap.Round,join=androidx.compose.ui.graphics.StrokeJoin.Round)
  when(name){
   "pair"->{
    drawRoundRect(ink,androidx.compose.ui.geometry.Offset(w*.08f,h*.12f),androidx.compose.ui.geometry.Size(w*.84f,h*.62f),androidx.compose.ui.geometry.CornerRadius(w*.06f),style=stroke)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.36f,h*.88f),androidx.compose.ui.geometry.Offset(w*.64f,h*.88f),w*.065f)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.5f,h*.74f),androidx.compose.ui.geometry.Offset(w*.5f,h*.88f),w*.065f)
   }
   "language"->{
    drawRoundRect(ink,androidx.compose.ui.geometry.Offset(w*.08f,h*.12f),androidx.compose.ui.geometry.Size(w*.84f,h*.64f),androidx.compose.ui.geometry.CornerRadius(w*.12f),style=stroke)
    val tail=androidx.compose.ui.graphics.Path().apply{moveTo(w*.3f,h*.76f);lineTo(w*.22f,h*.91f);lineTo(w*.47f,h*.76f)}
    drawPath(tail,ink,style=stroke)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.27f,h*.37f),androidx.compose.ui.geometry.Offset(w*.73f,h*.37f),w*.06f)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.27f,h*.53f),androidx.compose.ui.geometry.Offset(w*.59f,h*.53f),w*.06f)
   }
   "refresh"->{
    drawArc(ink,-45f,250f,false,androidx.compose.ui.geometry.Offset(w*.14f,h*.14f),androidx.compose.ui.geometry.Size(w*.72f,h*.72f),style=stroke)
    val arrow=androidx.compose.ui.graphics.Path().apply{moveTo(w*.75f,h*.08f);lineTo(w*.88f,h*.25f);lineTo(w*.67f,h*.27f)}
    drawPath(arrow,ink,style=stroke)
   }
   else->{
    drawRoundRect(ink,androidx.compose.ui.geometry.Offset(w*.25f,h*.25f),androidx.compose.ui.geometry.Size(w*.5f,h*.62f),androidx.compose.ui.geometry.CornerRadius(w*.05f),style=stroke)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.18f,h*.25f),androidx.compose.ui.geometry.Offset(w*.82f,h*.25f),w*.065f)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.4f,h*.13f),androidx.compose.ui.geometry.Offset(w*.6f,h*.13f),w*.065f)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.43f,h*.4f),androidx.compose.ui.geometry.Offset(w*.43f,h*.72f),w*.055f)
    drawLine(ink,androidx.compose.ui.geometry.Offset(w*.57f,h*.4f),androidx.compose.ui.geometry.Offset(w*.57f,h*.72f),w*.055f)
   }
  }
 }
}

private fun TrophyIconCanvas(w:Float,h:Float,stroke:androidx.compose.ui.graphics.drawscope.Stroke,scope:androidx.compose.ui.graphics.drawscope.DrawScope){with(scope){
 val p=androidx.compose.ui.graphics.Path().apply{moveTo(w*.25f,h*.16f);lineTo(w*.75f,h*.16f);lineTo(w*.7f,h*.52f);cubicTo(w*.67f,h*.68f,w*.57f,h*.72f,w*.5f,h*.72f);cubicTo(w*.43f,h*.72f,w*.33f,h*.68f,w*.3f,h*.52f);close();moveTo(w*.5f,h*.72f);lineTo(w*.5f,h*.86f);moveTo(w*.34f,h*.87f);lineTo(w*.66f,h*.87f)}
 drawPath(p,SgTheme.Text,style=stroke)
}}

/** Monochrome outline mark, matching the rail's cream line icons. */
@Composable fun TrophyIcon(size:Int=22){
 Canvas(Modifier.size(size.dp)) {
  val unit=this.size.width/24f
  val stroke=androidx.compose.ui.graphics.drawscope.Stroke(width=1.8f*unit,cap=androidx.compose.ui.graphics.StrokeCap.Round,join=androidx.compose.ui.graphics.StrokeJoin.Round)
  val cup=androidx.compose.ui.graphics.Path().apply {
   moveTo(6*unit,3*unit);lineTo(18*unit,3*unit);lineTo(18*unit,8*unit)
   cubicTo(18*unit,12*unit,15*unit,15*unit,12*unit,15*unit)
   cubicTo(9*unit,15*unit,6*unit,12*unit,6*unit,8*unit);close()
  }
  drawPath(cup,SgTheme.Text,style=stroke)
  val handles=androidx.compose.ui.graphics.Path().apply {
   moveTo(6*unit,5*unit);lineTo(3*unit,5*unit);lineTo(3*unit,7*unit)
   cubicTo(3*unit,10*unit,5*unit,11*unit,7*unit,11*unit)
   moveTo(18*unit,5*unit);lineTo(21*unit,5*unit);lineTo(21*unit,7*unit)
   cubicTo(21*unit,10*unit,19*unit,11*unit,17*unit,11*unit)
   moveTo(12*unit,15*unit);lineTo(12*unit,20*unit)
   moveTo(8*unit,21*unit);lineTo(16*unit,21*unit)
  }
  drawPath(handles,SgTheme.Text,style=stroke)
 }
}

/** Team atmosphere: a soft left-to-right gradient from the team's two colours, no crest and no pattern. */
@Composable fun BoxScope.TeamBackdrop(team:Team){
 Box(Modifier.matchParentSize().background(Brush.horizontalGradient(listOf(Color(team.color).copy(alpha=.24f),Color(team.secondary).copy(alpha=.24f)),startX=0f,endX=Float.POSITIVE_INFINITY)))
 Box(Modifier.matchParentSize().background(Color.Black.copy(alpha=.22f)))
}
/** Full-page backdrop for Sports/Leagues: a flat uniform scrim rather than a directional
 * gradient, since cards and text are scattered across the whole page, not confined to one edge
 * like a hero's left-aligned copy. Falls back to nothing (bare canvas) if the asset is missing,
 * same as HeroBanner's own real-photo path. */
@Composable fun BoxScope.PageBackdrop(filename:String){
 val context=LocalContext.current
 val photo=rememberAssetImage(context,"banners/$filename")
 if(photo!=null){
  Image(photo,null,Modifier.matchParentSize(),contentScale=androidx.compose.ui.layout.ContentScale.Crop)
  Box(Modifier.matchParentSize().background(Color.Black.copy(alpha=.5f)))
 }
}

/** Truthful progress for the automatic checks of favorite live / about-to-start games. Counts only: confirmation appears on each game's own screen once the server has evidence. */
@Composable fun StartupChecksLine(p:StartupProgress){
 val text=when{
  p.stopped->"Stream checks paused. They will resume the next time the guide refreshes."
  p.active->"Checking streams for your teams · ${p.finished} of ${p.total} done"
  p.limited->"Stream checks finished. Pre-game readiness needs a newer server, so those games are checked once they start."
  else->"Stream checks for your teams finished. Open a game to see what was found."
 }
 Label(text,14,SgTheme.Muted,modifier=Modifier.testTag("startup-checks"))
}
