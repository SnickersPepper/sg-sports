package com.sgsports.tv

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.Locale
import java.util.TimeZone

/*
 * Team Hub screens (SGH-301): Overview, Schedule, Roster, News, Recorded Games. Pure presentation over TeamHubModel.kt:
 * every fact shown comes from a resource, every unavailable one is stated, and nothing is keyed to a team, sport or league.
 */

internal object HubStyle {
 val Panel=Color(0xFF12191F)
 val Danger=Color(0xFFFF5468)
 val PanelShape=RoundedCornerShape(18.dp)
}

/** Per-team, per-screen filter choices; saved with the screen and reset when another team opens. Empty string = "not chosen". */
class HubUi {
 var status by mutableStateOf("");var competition by mutableStateOf("");var season by mutableStateOf("")
 var position by mutableStateOf("");var query by mutableStateOf("");var recording by mutableStateOf("")
}
val HubUiSaver=listSaver<HubUi,String>(
 save={listOf(it.status,it.competition,it.season,it.position,it.query,it.recording)},
 restore={v->HubUi().also{it.status=v[0];it.competition=v[1];it.season=v[2];it.position=v[3];it.query=v[4];it.recording=v[5]}})

/** Everything a tab needs, so the five tabs share one identity, theme and set of actions. */
class HubCtx(val team:Team,val teams:List<Team>,val catalog:TeamBrandCatalog,val hub:TeamHubState,
 /** Upcoming fixtures and guide programs: the only events whose Game Details / Watch flows already exist. */
 val pool:List<Game>,val ui:HubUi,val now:Long,val zone:TimeZone,val locale:Locale,val demo:Boolean,
 val onTab:(String)->Unit,val onOpen:(Game)->Unit,val onWatch:(Game)->Unit,val onRetry:()->Unit)

// ---- Building blocks ---------------------------------------------------------------------------------------

@Composable fun HubPanel(title:String,modifier:Modifier=Modifier,content:@Composable ColumnScope.()->Unit){
 Column(modifier.clip(HubStyle.PanelShape).background(HubStyle.Panel).border(1.dp,SgTheme.StrokeRest,HubStyle.PanelShape).padding(16.dp),verticalArrangement=Arrangement.spacedBy(8.dp)){
  Label(title,16,SgTheme.Text,true,lines=2);content()
 }
}
/** Static placeholder that reserves the space the content will take; not focusable, no animation. */
@Composable fun HubSkeleton(label:String,rows:Int=3,modifier:Modifier=Modifier){
 Column(modifier.fillMaxWidth().testTag("hub-loading").semantics{contentDescription="Loading $label"},verticalArrangement=Arrangement.spacedBy(8.dp)){
  Label("Loading $label…",14,SgTheme.Muted,true,lines=1)
  repeat(rows){Box(Modifier.fillMaxWidth().height(54.dp).clip(RoundedCornerShape(12.dp)).background(SgTheme.Surface).border(1.dp,SgTheme.StrokeRest,RoundedCornerShape(12.dp)))}
 }
}
/** Plain, non-focusable explanation: empty, no matches, or a limit of what the backend supplies. */
@Composable fun HubNotice(title:String,detail:String,tag:String="hub-notice",tint:Color=SgTheme.Text){
 Column(Modifier.fillMaxWidth().testTag(tag).clip(RoundedCornerShape(14.dp)).background(SgTheme.Surface).border(1.dp,SgTheme.StrokeRest,RoundedCornerShape(14.dp)).padding(16.dp),verticalArrangement=Arrangement.spacedBy(4.dp)){
  Label(title,18,tint,true,lines=2);Label(detail,14,SgTheme.Muted,lines=4)
 }
}
/** A failed resource: what happened, whether older content is still shown, and one retry target. */
@Composable fun HubFailure(kind:FailKind,what:String,savedAsOf:Long?,savedShown:Boolean,now:Long,retryKey:String,onRetry:()->Unit){
 val (title,detail)=hubFailureText(kind,what,savedAsOf,savedShown,now)
 SgCard(Modifier.fillMaxWidth().testTag("hub-failure-$retryKey"),accent=SgTheme.Gold,selected=true,focusKey="hub-retry:$retryKey",onClick=onRetry){
  Label(title,20,SgTheme.Gold,true,lines=2);Label(detail,14,SgTheme.Muted,lines=3)
  Label(if(kind==FailKind.PERMISSION)"Try again after pairing ›" else "Try again ›",16,SgTheme.Text,true)
 }
}
@Composable fun HubChip(label:String,selected:Boolean,focusKey:String,tag:String,onClick:()->Unit){
 SgCard(Modifier.testTag(tag),selected=selected,focusKey=focusKey,onClick=onClick,contentPadding=10.dp){Label(label,15,if(selected)SgTheme.Green else SgTheme.Text,true,lines=1)}
}
@Composable fun HubPill(text:String,tint:Color,dot:Boolean=false,modifier:Modifier=Modifier){
 Row(modifier.clip(RoundedCornerShape(50)).background(tint.copy(alpha=.16f)).border(1.dp,tint.copy(alpha=.6f),RoundedCornerShape(50)).padding(horizontal=12.dp,vertical=3.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(6.dp)){
  if(dot)Box(Modifier.size(8.dp).clip(CircleShape).background(tint))
  Text(text,color=tint,fontSize=12.sp,fontWeight=FontWeight.Bold,letterSpacing=1.2.sp,maxLines=1)
 }
}
fun statusTint(s:HubStatus):Color=when(s){
 HubStatus.LIVE->SgTheme.Live;HubStatus.SCHEDULED->SgTheme.Gold;HubStatus.FINAL->SgTheme.Muted
 HubStatus.POSTPONED,HubStatus.CANCELLED,HubStatus.SUSPENDED->HubStyle.Danger;HubStatus.UNKNOWN->SgTheme.Muted
}

/**
 * One resource as list items: skeleton while loading, a failure card (with the last good content below it when there is
 * some), a notice when it is genuinely empty, otherwise [content]. Other resources are unaffected by this one's state.
 */
fun <T> LazyListScope.resourceItems(c:HubCtx,key:String,what:String,res:Res<T>,isEmpty:(T)->Boolean,empty:Pair<String,String>,content:LazyListScope.(T)->Unit) {
 val shown=res.content()
 if(res is Res.Loading||(res is Res.Failed&&shown==null&&c.hub.loading)){item(key="$key-loading"){HubSkeleton(what)};return}
 if(res is Res.Failed)item(key="$key-failure"){HubFailure(res.kind,what,shown?.asOf,shown!=null,c.now,key,c.onRetry)}
 if(shown==null)return
 if(isEmpty(shown.value))item(key="$key-empty"){HubNotice(empty.first,empty.second,"hub-empty-$key")} else content(shown.value)
}

// ---- Hero --------------------------------------------------------------------------------------------------

@Composable private fun HeroTeam(side:HeroSide,color:Color,tag:String,outerStart:Boolean,modifier:Modifier){
 Box(modifier){
  // Decorative watermark: laid over the column without taking part in its size (matchParentSize), so it can never make the hero taller or push content.
  if(side.logo.isNotBlank())Box(Modifier.matchParentSize().clearAndSetSemantics{}){Box(Modifier.align(if(outerStart)Alignment.CenterStart else Alignment.CenterEnd).requiredSize(190.dp).offset(x=if(outerStart)(-56).dp else 56.dp).graphicsLayer{alpha=.12f}){Mark(side.logo,"",190)}}
  Column(Modifier.align(Alignment.Center).fillMaxWidth(),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(6.dp)){
   Box(contentAlignment=Alignment.Center){
    Canvas(Modifier.size(84.dp)){drawCircle(Brush.radialGradient(listOf(color.copy(alpha=.5f),Color.Transparent)),radius=size.minDimension*.55f)}
    if(side.name.isBlank())SportGlyph("",44) else Mark(side.logo,side.name,64,side.rank)
   }
   Text(side.name.ifBlank{"To be determined"}.uppercase(),color=SgTheme.Text,fontSize=17.sp,lineHeight=21.sp,fontWeight=FontWeight.Bold,letterSpacing=.4.sp,maxLines=3,overflow=TextOverflow.Ellipsis,textAlign=TextAlign.Center,modifier=Modifier.testTag("team-hero-$tag-name"))
  }
 }
}

/** Native compact matchup hero: separate gradient, watermark, crest, name, status, score/time and action layers; it grows rather than clips. */
@Composable fun TeamMatchupHero(c:HubCtx,event:HubEvent,supported:Game?,asOf:Long?){
 val game=remember(event,c.team.id){event.asGame(c.team.id)}
 val sides=remember(game,c.teams){heroSides(game,c.teams,c.catalog)}
 val palette=remember(sides){heroPalette(sides[0].color,sides[1].color)}
 val left=Color(palette.left);val right=Color(palette.right)
 val shape=RoundedCornerShape(12.dp)
 val live=event.status==HubStatus.LIVE
 val cta=when{supported==null->null;live->"▶  Watch Live";event.start>0&&event.start-c.now in 0..1800->"▶  Tune In Early";else->"Find a stream"}
 // The whole hero is the one focus target: with a resolvable game it opens the stream search (Watch Live when it is on or about to start, else the game's feed list).
 val startsSoon=event.start>0&&event.start-c.now in 0..1800
 SgCard(Modifier.fillMaxWidth().testTag("team-hero"),accent=SgTheme.Text,focusKey="hub-hero-cta",onClick={supported?.let{g->if(live||startsSoon)c.onWatch(g) else c.onOpen(g)}},contentPadding=0.dp,bare=true){
 Box(Modifier.fillMaxWidth().heightIn(min=168.dp).clip(shape)){
  Box(Modifier.matchParentSize().background(Color(HERO_BASE)))
  Canvas(Modifier.matchParentSize()){
   val w=size.width;val h=size.height
   drawRect(Brush.radialGradient(listOf(left.copy(alpha=.78f),left.copy(alpha=.34f),Color.Transparent),center=Offset(w*.04f,h*.5f),radius=w*.5f))
   drawRect(Brush.radialGradient(listOf(right.copy(alpha=.78f),right.copy(alpha=.34f),Color.Transparent),center=Offset(w*.96f,h*.5f),radius=w*.5f))
   drawRect(Brush.radialGradient(listOf(Color(HERO_BASE).copy(alpha=.92f),Color(HERO_BASE).copy(alpha=.5f),Color.Transparent),center=Offset(w*.5f,h*.5f),radius=w*.3f))
  }
  Row(Modifier.fillMaxWidth().padding(horizontal=16.dp,vertical=12.dp),verticalAlignment=Alignment.CenterVertically){
   HeroTeam(sides[0],left,"away",true,Modifier.weight(1f))
   Column(Modifier.weight(1.3f).padding(horizontal=6.dp),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(4.dp)){
    // League and state share one line, and freshness/venue share another, so the centre stays compact (about half the Home hero's height).
    Row(horizontalArrangement=Arrangement.spacedBy(10.dp),verticalAlignment=Alignment.CenterVertically){
     Text(event.league.uppercase(),color=SgTheme.Muted,fontSize=11.sp,letterSpacing=1.5.sp,maxLines=2,textAlign=TextAlign.Center,overflow=TextOverflow.Ellipsis,modifier=Modifier.weight(1f,false))
     HubPill(statusLabel(event).uppercase(),statusTint(event.status),dot=live,modifier=Modifier.testTag("team-hero-status"))
    }
    Text(scoreLine(event)?:"VS",color=if(scoreLine(event)!=null)SgTheme.Text else SgTheme.Muted,fontSize=if(scoreLine(event)!=null)34.sp else 28.sp,fontWeight=FontWeight.Bold,maxLines=1,modifier=Modifier.testTag("team-hero-score"))
    val detailLine=when{
     live->event.detail.ifBlank{null}
     event.status==HubStatus.SCHEDULED||event.status==HubStatus.POSTPONED||event.status==HubStatus.FINAL->startDateTimeLabel(event,c.zone,c.locale)
     else->event.detail.ifBlank{null}
    }
    detailLine?.let{Text(it,color=SgTheme.Gold,fontSize=15.sp,fontWeight=FontWeight.SemiBold,maxLines=2,textAlign=TextAlign.Center,modifier=Modifier.testTag("team-hero-time"))}
    // The score is the backend's as of the last hub load or guide check, never a live feed: say how old it is.
    val age=if(live)asOf?.let{(c.now-it).coerceAtLeast(0)} else null
    val freshness=if(!live)null else if(age==null)"Update time unavailable" else "As of ${agoLabel(age)}"
    val note=listOfNotNull(freshness,event.venue.takeIf{it.isNotBlank()}).joinToString(" · ")
    if(note.isNotEmpty())Text(note,color=if(live&&(age==null||age>=STALE_AFTER_SECONDS))SgTheme.Gold else SgTheme.Muted,fontSize=12.sp,maxLines=2,textAlign=TextAlign.Center,overflow=TextOverflow.Ellipsis,modifier=Modifier.testTag("team-hero-fresh"))
    if(cta!=null)Text("$cta  ›",color=SgTheme.Text,fontSize=16.sp,fontWeight=FontWeight.Bold,maxLines=1,modifier=Modifier.testTag("team-hero-cta").clip(RoundedCornerShape(10.dp)).background(Color.White.copy(alpha=.12f)).padding(horizontal=18.dp,vertical=7.dp))
   }
   HeroTeam(sides[1],right,"home",false,Modifier.weight(1f))
  }
 }
 }
}

// ---- Game rows ---------------------------------------------------------------------------------------------

@Composable fun HubGameRow(c:HubCtx,e:HubEvent,own:String?,supported:Game?){
 val game=remember(e,c.team.id){e.asGame(c.team.id)}
 val sides=remember(game,c.teams){heroSides(game,c.teams,c.catalog)}
 val home=isHomeGame(e,own)
 val opponent=when(home){true->e.away.name;false->e.home.name;null->null}
 val title=when(home){true->"vs ${e.away.name}";false->"at ${e.home.name}";null->"${e.away.name} at ${e.home.name}"}
 val tint=statusTint(e.status)
 SgCard(Modifier.fillMaxWidth().testTag("hub-game-${e.id}"),focusKey="hub-ev:${e.id}",onClick={supported?.let(c.onOpen)},contentPadding=12.dp){
  Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)){
   Column(Modifier.width(104.dp)){
    Label(startTimeLabel(e,c.zone,c.locale),15,SgTheme.Text,true,lines=1)
    Label(statusLabel(e),12,tint,true,lines=1)
   }
   Row(horizontalArrangement=Arrangement.spacedBy(4.dp)){Mark(sides[0].logo,sides[0].name,34);Mark(sides[1].logo,sides[1].name,34)}
   Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(2.dp)){
    Label(title,17,SgTheme.Text,true,lines=2)
    Label(listOf(if(opponent!=null&&home==true)"Home" else if(opponent!=null)"Away" else "",e.league,e.venue).filter{it.isNotBlank()}.joinToString(" · "),12,SgTheme.Muted,lines=2)
   }
   Column(horizontalAlignment=Alignment.End){
    scoreLine(e)?.let{Label(it,20,SgTheme.Text,true,lines=1)}
    if(e.status==HubStatus.LIVE&&e.detail.isNotBlank())Label(e.detail,12,SgTheme.Gold,lines=1)
    if(supported!=null)Label("Game Details ›",13,SgTheme.Green,true,lines=1)
   }
  }
 }
}

// ---- Tabs --------------------------------------------------------------------------------------------------

fun LazyListScope.overviewItems(c:HubCtx){
 val d=c.hub.data
 // Everything failed the same way with nothing saved: one explanation and one retry, not four.
 if(!c.hub.loading&&d.schedule.failedEmpty()&&d.snapshot.failedEmpty()&&d.news.failedEmpty()){
  item(key="ov-all-down"){HubFailure(d.schedule.failKindOrNull()?:FailKind.UNAVAILABLE,"this team's information",null,false,c.now,"overview",c.onRetry)}
  return
 }
 val schedule=d.schedule.content()
 item(key="ov-hero"){
  when{
   d.schedule is Res.Loading||(schedule==null&&c.hub.loading)->HubSkeleton("the matchup",1)
   schedule==null->HubFailure((d.schedule.failKindOrNull()?:FailKind.UNAVAILABLE),"the matchup",null,false,c.now,"overview",c.onRetry)
   else->{
    val featured=remember(schedule.value,c.hub.data){featuredEvent(schedule.value.events,c.now)}
    if(featured==null)HubNotice("No game to feature","This team has no live, upcoming or completed game in the provider's schedule right now.","hub-empty-hero")
    else TeamMatchupHero(c,featured,findSupportedGame(featured,c.pool),featured.updatedAt?:schedule.asOf)
   }
  }
 }
 if(d.schedule is Res.Failed&&schedule!=null)item(key="ov-hero-failure"){HubFailure((d.schedule.failKindOrNull()?:FailKind.UNAVAILABLE),"the schedule",schedule.asOf,true,c.now,"overview",c.onRetry)}
 item(key="ov-modules"){
  Row(Modifier.fillMaxWidth().height(IntrinsicSize.Max),horizontalArrangement=Arrangement.spacedBy(14.dp)){
   UpcomingModule(c,Modifier.weight(1f).fillMaxHeight())
   SnapshotModule(c,Modifier.weight(1f).fillMaxHeight())
   NewsModule(c,Modifier.weight(1f).fillMaxHeight())
  }
 }
}

@Composable private fun <T> ModuleBody(c:HubCtx,key:String,what:String,res:Res<T>,isEmpty:(T)->Boolean,empty:String,content:@Composable ColumnScope.(T)->Unit){
 val shown=res.content()
 Column(verticalArrangement=Arrangement.spacedBy(6.dp)){
  when{
   res is Res.Loading||(res is Res.Failed&&shown==null&&c.hub.loading)->HubSkeleton(what,1)
   else->{
    if(res is Res.Failed)HubFailure(res.kind,what,shown?.asOf,shown!=null,c.now,key,c.onRetry)
    if(shown!=null){if(isEmpty(shown.value))Label(empty,14,SgTheme.Muted,lines=4,modifier=Modifier.testTag("hub-empty-$key")) else content(shown.value)}
   }
  }
 }
}
@Composable private fun ModuleLink(label:String,key:String,onClick:()->Unit){
 SgCard(Modifier.fillMaxWidth().testTag("hub-link-$key"),focusKey="hub-link:$key",onClick=onClick,contentPadding=10.dp){Label("$label ›",15,SgTheme.Green,true,lines=1)}
}

@Composable private fun UpcomingModule(c:HubCtx,modifier:Modifier){
 HubPanel("Upcoming games",modifier.testTag("module-upcoming")){
  ModuleBody(c,"upcoming","the schedule",c.hub.data.schedule,{it.events.none{e->e.status==HubStatus.SCHEDULED||e.status==HubStatus.POSTPONED}},"No upcoming games in the provider's schedule."){data->
   val featured=featuredEvent(data.events,c.now)
   val own=ownSideId(data.events,c.team.name)
   upcomingEvents(data.events,featured?.id,4).forEach{e->
    val home=isHomeGame(e,own)
    Column(Modifier.fillMaxWidth()){
     Label(startDateTimeLabel(e,c.zone,c.locale)+if(e.status==HubStatus.POSTPONED)" · Postponed" else "",13,SgTheme.Gold,true,lines=2)
     Label(when(home){true->"vs ${e.away.name}";false->"at ${e.home.name}";null->"${e.away.name} at ${e.home.name}"},15,SgTheme.Text,lines=2)
    }
   }
  }
  Spacer(Modifier.weight(1f))
  ModuleLink("Full schedule","schedule"){c.onTab(HUB_SCHEDULE)}
 }
}
@Composable private fun SnapshotModule(c:HubCtx,modifier:Modifier){
 HubPanel("Season snapshot",modifier.testTag("module-snapshot")){
  ModuleBody(c,"snapshot","the season snapshot",c.hub.data.snapshot,{it.record==null&&it.rank==null&&it.standing==null},"The provider has no record or standing for this team right now."){s->
   // Only what the provider supplied: no placeholder values for missing fields.
   listOfNotNull(s.record?.let{"Record" to it},s.rank?.let{"Ranking" to "#$it"},s.standing?.let{"Standing" to it}).forEach{(k,v)->
    Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically){
     Label(k,14,SgTheme.Muted,lines=1);Label(v,18,SgTheme.Text,true,lines=2,modifier=Modifier.weight(1f,false).padding(start=8.dp))
    }
   }
   c.hub.data.snapshot.content()?.asOf?.let{Label("From the sports data provider · updated ${agoLabel(c.now-it)}",12,SgTheme.Muted,lines=2)}
  }
  Spacer(Modifier.weight(1f))
 }
}
@Composable private fun NewsModule(c:HubCtx,modifier:Modifier){
 HubPanel("Latest news",modifier.testTag("module-news")){
  ModuleBody(c,"news-module","news",c.hub.data.news,{it.isEmpty()},"No recent articles for this team."){list->
   list.take(3).forEach{a->
    Column(Modifier.fillMaxWidth()){
     Label(a.headline,15,SgTheme.Text,lines=3)
     Label(attributionLabel(a)+" · "+publishedLabel(a,c.now),12,SgTheme.Muted,lines=1)
    }
   }
  }
  Spacer(Modifier.weight(1f))
  ModuleLink("All news","news"){c.onTab(HUB_NEWS)}
 }
}

fun LazyListScope.scheduleItems(c:HubCtx){
 resourceItems(c,"schedule","the schedule",c.hub.data.schedule,{it.events.isEmpty()},"No games listed" to "The provider lists no games for this team right now."){data->
  val events=data.events
  val own=ownSideId(events,c.team.name)
  val ui=c.ui
  val status=StatusFilter.values().firstOrNull{it.name==ui.status}?:defaultStatusFilter(events)
  val competitions=competitionsOf(events);val seasons=seasonsOf(events)
  val filter=ScheduleFilter(status,ui.competition.ifEmpty{null}?.takeIf{it in competitions},ui.season.ifEmpty{null}?.takeIf{it in seasons})
  item(key="sched-filters"){
   Column(verticalArrangement=Arrangement.spacedBy(6.dp)){
    FocusLazyRow("sched-status",horizontalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(4.dp)){
     items(StatusFilter.values().toList(),key={it.name}){f->HubChip(f.label,f==status,"sched-status:${f.name}","hub-filter-${f.name}"){ui.status=f.name}}
    }
    if(competitions.isNotEmpty())FocusLazyRow("sched-comp",horizontalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(4.dp)){
     items(listOf("")+competitions,key={"c:$it"}){comp->HubChip(comp.ifEmpty{"All competitions"},(filter.competition?:"")==comp,"sched-comp:$comp","hub-comp-$comp"){ui.competition=comp}}
    }
    if(seasons.isNotEmpty())FocusLazyRow("sched-season",horizontalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(4.dp)){
     items(listOf("")+seasons,key={"s:$it"}){s->HubChip(s.ifEmpty{"All seasons"},(filter.season?:"")==s,"sched-season:$s","hub-season-$s"){ui.season=s}}
    }
   }
  }
  if(!data.hasResults)item(key="sched-no-results"){HubNotice("Past results aren't included","This backend only supplies upcoming games, so completed games and scores are not shown.","hub-notice-results")}
  val shown=filterSchedule(events,filter)
  if(shown.isEmpty()){
   item(key="sched-none"){HubNotice("No games match these filters","Choose another filter to see more of the schedule.","hub-empty-filter")}
   item(key="sched-reset"){SgCard(Modifier.fillMaxWidth().testTag("hub-filter-reset"),focusKey="sched-reset",onClick={ui.status=StatusFilter.ALL.name;ui.competition="";ui.season=""}){Label("Show all games ›",16,SgTheme.Green,true)}}
  } else groupByLocalDate(shown,c.zone).forEach{g->
   item(key="day-${g.key}"){Label(dayHeading(g.key,c.zone,c.locale),17,SgTheme.Muted,true,Modifier.padding(top=6.dp),lines=1)}
   items(g.events,key={"ev-${it.id}"}){e->HubGameRow(c,e,own,findSupportedGame(e,c.pool))}
  }
 }
}

@Composable private fun HubSearchField(value:String,onChange:(String)->Unit){
 var focused by remember{mutableStateOf(false)}
 val shape=RoundedCornerShape(12.dp)
 BasicTextField(value,onChange,singleLine=true,textStyle=TextStyle(color=SgTheme.Text,fontSize=16.sp),cursorBrush=SolidColor(SgTheme.Green),
  modifier=Modifier.width(260.dp).testTag("roster-search").onFocusChanged{focused=it.isFocused},
  decorationBox={inner->
   Box(Modifier.clip(shape).background(SgTheme.Surface2).border(if(focused)2.dp else 1.dp,if(focused)SgTheme.Text else SgTheme.StrokeRest,shape).padding(horizontal=14.dp,vertical=11.dp)){
    if(value.isEmpty())Label("Search name or number",15,SgTheme.Muted,lines=1)
    inner()
   }
  })
}

@Composable private fun PlayerCard(c:HubCtx,p:RosterPlayer,injury:String?,modifier:Modifier){
 val tint=Color(c.team.color)
 SgCard(modifier.testTag("hub-player-${p.id}"),accent=tint,focusKey="hub-pl:${p.id}",onClick={},contentPadding=10.dp){
  Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp)){
   // No headshot source exists in the app; a neutral initials badge in the team's colour stands in.
   Box(Modifier.size(44.dp).clip(CircleShape).background(tint.copy(alpha=.28f)).border(1.dp,tint.copy(alpha=.7f),CircleShape),contentAlignment=Alignment.Center){Label(playerInitials(p.name),15,SgTheme.Text,true,lines=1)}
   Column(Modifier.weight(1f)){
    Label(p.name,15,SgTheme.Text,true,lines=2)
    Label(listOf(jerseyLabel(p),p.position?:"Position unknown").joinToString(" · "),12,SgTheme.Muted,lines=1)
    injury?.let{Label(it.ifBlank{"Injury listed"},12,HubStyle.Danger,true,lines=1)}
   }
  }
 }
}

fun LazyListScope.rosterItems(c:HubCtx){
 resourceItems(c,"roster","the roster",c.hub.data.roster,{it.players.isEmpty()},"No roster listed" to "The provider lists no players for this team right now."){data->
  val ui=c.ui
  val positions=rosterPositions(data.players,c.team.sport)
  val position=ui.position.ifEmpty{null}?.takeIf{it in positions}
  item(key="roster-filters"){
   Column(verticalArrangement=Arrangement.spacedBy(6.dp)){
    if(positions.isNotEmpty())FocusLazyRow("roster-pos",horizontalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(4.dp)){
     items(listOf("")+positions,key={"p:$it"}){pos->HubChip(pos.ifEmpty{"All"},(position?:"")==pos,"roster-pos:$pos","hub-pos-$pos"){ui.position=pos}}
    }
    HubSearchField(ui.query){ui.query=it}
   }
  }
  val shown=filterRoster(data.players,position,ui.query)
  if(shown.isEmpty()){
   item(key="roster-none"){HubNotice("No players match","Clear the search or choose another position.","hub-empty-filter")}
   item(key="roster-reset"){SgCard(Modifier.fillMaxWidth().testTag("hub-filter-reset"),focusKey="roster-reset",onClick={ui.position="";ui.query=""}){Label("Show all players ›",16,SgTheme.Green,true)}}
  } else {
   val injured=data.injuries.toMap()
   items(shown.chunked(3),key={row->"pl-"+row.first().id}){row->
    Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(10.dp)){
     row.forEach{p->PlayerCard(c,p,injured[p.name],Modifier.weight(1f))}
     repeat(3-row.size){Spacer(Modifier.weight(1f))}
    }
   }
  }
 }
}

@Composable private fun NewsImageFallback(c:HubCtx,modifier:Modifier){
 val tint=Color(c.team.color)
 Box(modifier.clip(RoundedCornerShape(12.dp)).background(Brush.linearGradient(listOf(tint.copy(alpha=.55f),Color(c.team.secondary).copy(alpha=.35f),HubStyle.Panel))),contentAlignment=Alignment.Center){
  Mark(c.team.logo,c.team.name,56)
 }
}
@Composable private fun NewsCard(c:HubCtx,a:NewsArticle,featured:Boolean){
 SgCard(Modifier.fillMaxWidth().testTag(if(featured)"hub-news-featured" else "hub-news-${a.id}"),focusKey="hub-news:${a.id}",onClick={},contentPadding=12.dp){
  Row(horizontalArrangement=Arrangement.spacedBy(14.dp),verticalAlignment=Alignment.CenterVertically){
   NewsImageFallback(c,Modifier.size(if(featured)132.dp else 76.dp))
   Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(4.dp)){
    if(featured)Label("LATEST",11,SgTheme.Gold,true,lines=1)
    Label(a.headline,if(featured)24 else 17,SgTheme.Text,true,lines=4)
    Label(attributionLabel(a)+" · "+publishedLabel(a,c.now),13,SgTheme.Muted,lines=2)
    a.url?.let{u->urlHost(u)?.takeIf{it!=attributionLabel(a)}?.let{h->Label(h,12,SgTheme.Muted,lines=1)}}
   }
  }
 }
}
fun LazyListScope.newsItems(c:HubCtx){
 resourceItems(c,"news","news",c.hub.data.news,{it.isEmpty()},"No recent articles" to "The provider has no recent articles for this team."){list->
  val (top,rest)=featuredNews(list)
  top?.let{a->item(key="news-top"){NewsCard(c,a,true)}}
  items(rest,key={"news-${it.id}"}){a->NewsCard(c,a,false)}
 }
}

/** Recorded Games: filters with counts, then only what the DVR contract supports (play); scheduled/resume/cancel/retry have no backend and are not shown. */
fun LazyListScope.recordedHubItems(c:HubCtx,view:RecordedView,onRecording:(Recording)->Unit,onRefresh:()->Unit,onPair:()->Unit) {
 view.base.storageNotice?.let{notice->item(key="rec-storage"){Label(notice,15,SgTheme.Gold,modifier=Modifier.testTag("recordings-storage"),lines=3)}}
 when{
  view.base.kind==TeamRecordingsKind.NOT_RECORDED->item(key="rec-status"){Column(Modifier.fillMaxWidth().testTag("hub-recordings-not-recorded"),verticalArrangement=Arrangement.spacedBy(4.dp)){Label(view.base.title,20,bold=true);Label(view.base.detail,15,SgTheme.Muted,lines=3)}}
  view.needsPairing->item(key="rec-status"){StatusCard("Pair this TV to see recordings","Open Settings, then Connection, to pair this TV with your SG Sports backend.","Open Connection",onPair)}
  view.base.kind!=TeamRecordingsKind.CONTENT->item(key="rec-status"){StatusCard(view.base.title,view.base.detail,"Refresh",onRefresh)}
  else->{
   val counts=recordingCounts(view.base.recordings)
   item(key="rec-filters"){
    FocusLazyRow("rec-filter",horizontalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(4.dp)){
     items(RecFilter.values().toList(),key={it.name}){f->HubChip("${f.label} · ${counts[f]?:0}",f==view.filter,"rec-filter:${f.name}","hub-rec-filter-${f.name}"){c.ui.recording=f.name}}
    }
   }
   if(view.filter==RecFilter.SCHEDULED)item(key="rec-scheduled"){HubNotice("Scheduled recordings aren't listed","The recording service reports saved and in-progress games only, so upcoming recordings can't be shown here.","hub-notice-scheduled")}
   else if(view.noMatches)item(key="rec-none"){HubNotice("No ${view.filter.label.lowercase()} recordings","Nothing saved for this team matches this filter.","hub-empty-filter")}
   items(view.shown,key={it.id}){r->RecordingCardView(r,onRecording,onRefresh)}
   item(key="rec-refresh"){SgCard(Modifier.fillMaxWidth().testTag("recordings-refresh"),focusKey="rec-refresh",onClick=onRefresh){Label("Refresh recordings ›",16,SgTheme.Green,true)}}
  }
 }
}

/** The five-tab strip. Selected styling (green) is separate from remote focus (white outline); the strip scrolls so every tab stays reachable at any text size. */
@Composable fun HubTabStrip(selected:String,onSelect:(String)->Unit){
 Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).focusGroup(),horizontalArrangement=Arrangement.spacedBy(12.dp)){
  HUB_TABS.forEach{tab->SgCard(Modifier.testTag("hub-tab-$tab"),selected=selected==tab,focusKey=hubTabFocusId(tab),onClick={onSelect(tab)},contentPadding=12.dp){Label(tab,16,if(selected==tab)SgTheme.Green else SgTheme.Text,true,lines=1)}}
 }
}
