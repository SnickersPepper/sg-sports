package com.sgsports.tv

import java.util.Locale

/** Phase 6 pure rules shared by the player and the browsing screens; no Android types so they run as plain unit tests. */

// ---- Player overlays, Back and D-pad ---------------------------------------------------------
enum class BackResult { CLOSE_PICKER, HIDE_CONTROLS, EXIT }
enum class KeyResult { REVEAL_CONTROLS, MOVE_FOCUS, PASS }
enum class Dpad { LEFT, RIGHT, UP, DOWN, SELECT }

/** What is open on top of the video. Back closes the topmost layer first; only a bare video exits. */
class PlayerChrome {
 var controls=false;var picker=false;var errorShown=false
 fun back():BackResult = when {
  picker->{picker=false;BackResult.CLOSE_PICKER}
  controls&&!errorShown->{controls=false;BackResult.HIDE_CONTROLS}
  else->BackResult.EXIT
 }
 /** Hidden controls swallow a first D-pad press and reveal themselves; visible controls move focus explicitly. */
 fun key(key:Dpad):KeyResult = when {
  key==Dpad.SELECT&&(controls||picker||errorShown)->KeyResult.PASS
  !controls&&!picker&&!errorShown->{controls=true;KeyResult.REVEAL_CONTROLS}
  else->KeyResult.MOVE_FOCUS
 }
}
/** Focus index after one D-pad press inside a row (horizontal) or list (vertical); never wraps. */
fun moveFocus(index:Int,count:Int,key:Dpad,vertical:Boolean=false):Int {
 if(count<=0)return 0
 val step=when(key){
  Dpad.LEFT->if(vertical)0 else -1
  Dpad.RIGHT->if(vertical)0 else 1
  Dpad.UP->if(vertical)-1 else 0
  Dpad.DOWN->if(vertical)1 else 0
  Dpad.SELECT->0
 }
 return (index+step).coerceIn(0,count-1)
}

// ---- Honest playback facts -------------------------------------------------------------------
/** Only what the player itself reported. No height means no quality claim. */
fun qualityLabel(videoHeight:Int?):String? = when {
 videoHeight==null||videoHeight<=0->null
 videoHeight>=1080->"Full HD"
 videoHeight>=720->"HD"
 else->"Standard definition"
}
fun audioLanguageLabel(tag:String?):String? = tag?.takeIf{it.isNotBlank()&&it!="und"}?.let{Locale(it).displayLanguage.takeIf{n->n.isNotBlank()}}
/** One line for the controls: "HD · English audio", or an explicit gap for whatever was not reported. */
fun playbackFacts(videoHeight:Int?,audioTag:String?,reportedByStream:Boolean):String {
 val quality=qualityLabel(videoHeight)
 val language=audioLanguageLabel(audioTag)
 if(!reportedByStream||(quality==null&&language==null))return "Quality and audio not reported yet"
 return listOfNotNull(quality?:"Quality not reported",language?.let{"$it audio"}?:"Audio language not tagged").joinToString(" · ")
}
/** Compact live context under the title, built only from the card's own data; freshness is recomputed when shown. */
fun liveContext(game:Game?,now:Long=System.currentTimeMillis()/1000):String? {
 val g=game?:return null
 val live=g.live
 val score=if(g.phase()!=GamePhase.UPCOMING&&live?.homeScore!=null&&live.awayScore!=null)"${live.awayScore}-${live.homeScore}" else null
 val parts=listOfNotNull(g.phaseLabel(),score,g.clockText(),live?.freshness(now))
 return parts.joinToString(" · ")
}

/**
 * The server refused an optional, page-owned request. The Recordings list is fetched on every home refresh but belongs to Recordings and
 * team hubs, which already say "unavailable"; an older backend without it must not paint "Something went wrong" over Home and Settings.
 */
/** The Recordings page and a team hub each own their failure state and show it themselves, so a refusal never raises the global banner. */
fun isPageScopedFailure(path:String,status:Int)=(path=="/tv/recordings"||path=="/tv/team")&&status>=400

// ---- Screen states ----------------------------------------------------------------------------
enum class BannerKind { LOADING, OFFLINE, ERROR, STALE }
data class StateBanner(val kind:BannerKind,val title:String,val detail:String,val action:String?)
/** The guide is called out only once the last successfully received data is this old; the backend fresh flag never decides it. */
const val GUIDE_STALE_SECONDS=300L
/** Stream checks run one feed after another on the backend; the TV waits this many 1.5 s polls (about a minute) before saying so. */
const val VERIFY_POLL_LIMIT=40
/** The one place that decides which truthful status the screen shows; null means plain content. */
fun stateBanner(state:SgState,hasContent:Boolean,now:Long=System.currentTimeMillis()/1000):StateBanner? {
 if(state.offline)return StateBanner(BannerKind.OFFLINE,"Can't reach SG Sports",
  if(hasContent)"Showing the last guide that loaded${state.loadedAt.takeIf{it>0}?.let{" ("+agoLabel(now-it)+")"}.orEmpty()}. Availability may have changed."
  else "Check this TV's home network connection.","Try again")
 if(state.error.isNotEmpty())return StateBanner(BannerKind.ERROR,if(state.demo)"Design mode" else "Something went wrong",state.error,"Try again")
 if(state.loading&&!hasContent)return StateBanner(BannerKind.LOADING,"Loading your games","",null)
 if(hasContent&&state.loadedAt>0&&now-state.loadedAt>=GUIDE_STALE_SECONDS)
  return StateBanner(BannerKind.STALE,"Guide may be out of date",
   "Last updated ${agoLabel(now-state.loadedAt)}. Showing the last listings received; availability may have changed.","Refresh")
 return null
}

// ---- Focus restoration -----------------------------------------------------------------------
/**
 * Where focus returns after leaving a page. The exact item when it still exists; otherwise the
 * nearest surviving neighbour in the order the page had when the viewer left; otherwise the first item.
 * Identity keys, never positions, so a re-sorted row still restores to the same game.
 */
fun restoreTarget(last:String?,order:List<String>,available:List<String>):String? {
 if(available.isEmpty())return null
 if(last!=null&&last in available)return last
 val at=order.indexOf(last)
 if(at>=0){
  val live=available.toSet()
  for(d in 1 until order.size){
   order.getOrNull(at+d)?.takeIf{it in live}?.let{return it}
   order.getOrNull(at-d)?.takeIf{it in live}?.let{return it}
  }
 }
 return available.first()
}
