package com.sgsports.tv

/**
 * Phase 6: the single owner of provider connection slots for the player (one pane or two).
 * Pure Kotlin on the main thread so the budget rules are unit-testable without Android.
 *
 * Rules enforced here, not in the UI:
 * - at most [paneLimit] panes, and at most [perProviderLimit] upstream streams per provider;
 * - a pane switching sources releases (stops) its previous session BEFORE the next one is opened;
 * - a stale open that finishes after its pane moved on is stopped immediately and keeps counting
 *   against the provider until then, so the budget is never exceeded even for a moment;
 * - verification / DVR holds share the same per-provider budget as playback;
 * - audio follows the active pane without touching any stream.
 */
enum class PaneStatus { EMPTY, OPENING, PLAYING, ERROR }
enum class RejectReason { BAD_PANE, NEEDS_FIRST_PANE, NOT_PLAYABLE, SAME_PROVIDER, PROVIDER_BUSY }
sealed class Decision {
 object Accepted:Decision()
 data class Rejected(val reason:RejectReason,val message:String):Decision()
}
/** Server-side side effects. [open] must call back exactly once: a session id, or null on failure. */
interface StreamGateway {
 fun stop(session:String)
 fun open(pane:Int,feed:Feed,result:(String?)->Unit)
}
class Pane {
 var status=PaneStatus.EMPTY;internal set
 var feed:Feed?=null;internal set
 var session="";internal set
 var error="";internal set
 internal var generation=0
 /** A source waiting for a draining stale open on its provider to finish. */
 internal var queued:Feed?=null
}
class PlaybackCoordinator(private val gateway:StreamGateway,val paneLimit:Int=2,val perProviderLimit:Int=1) {
 val panes=List(paneLimit){Pane()}
 var audioPane=0;private set
 /** Called after a pane changes state asynchronously (an open landed, failed or was stopped as stale). */
 var onChange:()->Unit={}
 private val holds=mutableMapOf<String,MutableList<String>>()
 private val draining=mutableMapOf<String,Int>()

 private fun live(p:Pane)=p.status==PaneStatus.OPENING&&p.queued==null||p.status==PaneStatus.PLAYING
 /** A pane that has claimed a provider, including one queued behind a draining stale open. */
 private fun claims(p:Pane)=p.feed!=null&&(p.status==PaneStatus.OPENING||p.status==PaneStatus.PLAYING)
 /** Upstream streams currently counted against [provider]: active panes, stale opens still draining, external holds. */
 fun inUse(provider:String)=panes.count{live(it)&&it.feed?.provider==provider}+(draining[provider]?:0)+(holds[provider]?.size?:0)
 fun withinBudget()=panes.mapNotNull{if(live(it))it.feed?.provider else null}.toSet().plus(draining.keys).plus(holds.keys).all{inUse(it)<=perProviderLimit}
 fun activeCount()=panes.count{live(it)}
 fun providersInUse():Set<String> =panes.filter{live(it)}.mapNotNull{it.feed?.provider}.toSet()

 /** Verification or DVR takes a slot on [provider]; false when the budget has none free. */
 fun hold(provider:String,owner:String):Boolean {
  if(inUse(provider)>=perProviderLimit)return false
  holds.getOrPut(provider){mutableListOf()}.add(owner);return true
 }
 fun releaseHold(provider:String,owner:String) {
  holds[provider]?.let{it.remove(owner);if(it.isEmpty())holds.remove(provider)}
  pump()
 }

 /** Feeds the second pane may offer: not failed, and only from a provider no other pane or hold is using. */
 fun secondPaneChoices(feeds:List<Feed>,forPane:Int=1):List<Feed> {
  val taken=panes.withIndex().filter{(i,p)->i!=forPane&&claims(p)}.mapNotNull{it.value.feed?.provider}.toSet()+holds.keys+draining.keys
  return feeds.filter{it.trust()!=FeedTrust.FAILED&&it.provider !in taken}
 }
 /** Plain-language reason a game has no second-pane choice, or null when something is offered. */
 fun constraintMessage(feeds:List<Feed>):String? {
  if(secondPaneChoices(feeds).isNotEmpty())return null
  val first=providersInUse().firstOrNull()
  return if(first==null)"No playable feed is listed for this game."
  else "This game has no playable feed from a second provider. Your first game keeps playing because each provider allows only one stream at a time."
 }

 fun open(index:Int,feed:Feed):Decision {
  val pane=panes.getOrNull(index)?:return Decision.Rejected(RejectReason.BAD_PANE,"Split screen shows at most $paneLimit games.")
  if(index>0&&!claims(panes[0]))return Decision.Rejected(RejectReason.NEEDS_FIRST_PANE,"Start the first game before adding a second.")
  if(feed.trust()==FeedTrust.FAILED)return Decision.Rejected(RejectReason.NOT_PLAYABLE,"This source could not be opened when last checked.")
  // Judge against everyone else first, so a refused switch never stops what is already playing.
  val others=panes.filterIndexed{i,p->i!=index&&claims(p)&&p.feed?.provider==feed.provider}
  if(others.isNotEmpty())return Decision.Rejected(RejectReason.SAME_PROVIDER,"Another game is already using this provider. Choose a source from the other provider.")
  if((holds[feed.provider]?.size?:0)>=perProviderLimit)return Decision.Rejected(RejectReason.PROVIDER_BUSY,"This provider is busy with a background check or recording. Try again in a moment.")
  release(pane)
  pane.feed=feed;pane.error=""
  if(inUse(feed.provider)>=perProviderLimit){pane.status=PaneStatus.OPENING;pane.queued=feed;return Decision.Accepted}
  start(index,pane,feed)
  return Decision.Accepted
 }
 /** A session the browsing screen already opened (and counted against the provider) becomes pane [index]'s stream. */
 fun adopt(index:Int,feed:Feed,session:String) {
  val pane=panes.getOrNull(index)?:return
  release(pane);pane.feed=feed;pane.session=session;pane.status=PaneStatus.PLAYING;pane.error=""
 }
 private fun start(index:Int,pane:Pane,feed:Feed) {
  pane.queued=null;pane.status=PaneStatus.OPENING;pane.feed=feed
  val ticket=++pane.generation
  gateway.open(index,feed){session->
   if(ticket!=pane.generation){
    // The pane moved on while this was in flight: give the upstream slot straight back.
    if(session!=null)gateway.stop(session)
    draining[feed.provider]=(draining[feed.provider]?:1)-1
    if(draining[feed.provider]==0)draining.remove(feed.provider)
    pump();onChange();return@open
   }
   if(session==null){pane.status=PaneStatus.ERROR;pane.error="This source could not be opened.";pump()}
   else{pane.session=session;pane.status=PaneStatus.PLAYING}
   onChange()
  }
 }
 /** Frees the pane's slot; an open still in flight keeps its provider counted until it lands and is stopped. */
 private fun release(pane:Pane) {
  val feed=pane.feed
  if(pane.status==PaneStatus.OPENING&&pane.queued==null&&feed!=null)draining[feed.provider]=(draining[feed.provider]?:0)+1
  if(pane.session.isNotEmpty())gateway.stop(pane.session)
  pane.generation++;pane.session="";pane.queued=null;pane.status=PaneStatus.EMPTY
 }
 private fun pump() {
  panes.forEachIndexed{i,p->val q=p.queued;if(q!=null&&inUse(q.provider)<perProviderLimit)start(i,p,q)}
 }

 /** Playback failed: release the session and slot, keep the feed so the UI can offer retry or another source. */
 fun fail(index:Int,message:String) {
  val pane=panes.getOrNull(index)?:return
  val feed=pane.feed
  release(pane);pane.feed=feed;pane.status=PaneStatus.ERROR;pane.error=message
  pump()
 }
 /** Sources the pane could switch to after a failure: same game's other feeds that are playable and budget-legal. */
 fun recoveryChoices(index:Int,feeds:List<Feed>):List<Feed> {
  val current=panes.getOrNull(index)?.feed
  return secondPaneChoices(feeds,index).filter{it!==current&&!(it.provider==current?.provider&&it.raw.optString("stream_id")==current.raw.optString("stream_id"))}
 }
 fun close(index:Int) {
  val pane=panes.getOrNull(index)?:return
  release(pane);pane.feed=null;pane.error=""
  if(index==0)panes.drop(1).indices.forEach{close(it+1)}
  if(audioPane>=activeIndexCount())audioPane=0
  pump()
 }
 private fun activeIndexCount()=panes.indexOfLast{it.feed!=null}+1
 fun closeAll()=panes.indices.reversed().forEach{close(it)}
 /** Audio moves between panes; no stream is opened, stopped or re-requested. */
 fun setAudio(index:Int):Boolean {
  if(index !in panes.indices||panes[index].feed==null)return false
  audioPane=index;return true
 }
}
