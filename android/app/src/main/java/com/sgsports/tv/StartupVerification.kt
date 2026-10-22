package com.sgsports.tv

/** How the server answered one stream check. Only [CHECKED] means probes ran; nothing here is a confirmation -- evidence stays on the server and shows on the game's own screen. */
enum class CheckOutcome { CHECKED, FRESH, NOT_STARTED, NO_CANDIDATES, DENIED, FAILED, CANCELLED }

/** Where startup checks run. [check] performs one whole check (start, then follow it until the server says it stopped) and calls [done] exactly once; [cancel] abandons the one in flight. */
interface CheckPort {
 /** [readiness] is true for a game still before kickoff: the server may only take a transport-only sample (the stream delivers), never a content check. */
 fun check(game:Game,readiness:Boolean,done:(CheckOutcome)->Unit)
 fun cancel()
}

/** Progress for the Home status line. Counts only; it never says a stream is confirmed. */
data class StartupProgress(val total:Int=0,val finished:Int=0,val current:String="",val stopped:Boolean=false,val limited:Boolean=false){
 val active get()=total>finished&&!stopped
}

/** A game is "near start" when it kicks off within this many seconds (the server's own tune-in window). */
const val NEAR_START_SECONDS=1800L

/** The generation of one event's feed set. A different phase or feed list is new work; the same one is never checked twice. */
fun checkGeneration(g:Game,phase:String):String{
 val feeds=g.raw.optJSONArray("feeds")
 val signature=(0 until (feeds?.length()?:0)).map{val f=feeds!!.optJSONObject(it);f?.optString("provider_id").orEmpty()+":"+f?.optString("stream_id").orEmpty()}.sorted().joinToString(",")
 return canonicalEventKey(g)+"#"+phase+"#"+signature
}

/**
 * On Home load, checks the active profile's favorite events that are live or about to start, one at
 * a time, ahead of anything else. It owns no provider budget: every check goes through the server's
 * single lease authority, and a denial stops the queue (fail closed) instead of retrying around it.
 * Android-free and clocked from outside so it is deterministic under test.
 */
class StartupVerification(private val nowSeconds:()->Long,private val port:CheckPort,private val canRun:()->Boolean,private val onProgress:(StartupProgress)->Unit){
 private val settled=mutableSetOf<String>()
 private val queue=ArrayDeque<Triple<Game,String,Boolean>>()
 private var running=false
 private var inFlight:String?=null
 private var generation=0
 private var total=0
 private var finished=0
 private var stopped=false
 private var limited=false

 private fun eligiblePhase(g:Game):String?{
  if(!g.isGame)return null
  return when(g.phase()){
   GamePhase.LIVE->"live"
   GamePhase.UPCOMING->{val start=g.startEpoch();if(start>0&&start-nowSeconds() in 0..NEAR_START_SECONDS)"near" else null}
   else->null
  }
 }

 /** Called with every freshly loaded catalog (and again on resume); adds only work not already queued, running or settled. */
 fun offer(programs:List<Game>,favorites:List<Team>){
  val fresh=programs.mapNotNull{g->
   if(favorites.none{isFavoriteEvent(it,g)})return@mapNotNull null
   val phase=eligiblePhase(g)?:return@mapNotNull null
   val key=checkGeneration(g,phase)
   if(key in settled||key==inFlight||queue.any{it.second==key})return@mapNotNull null
   Triple(g,key,phase)
  }.distinctBy{it.second}.sortedWith(compareBy({if(it.third=="live")0 else 1},{it.first.startEpoch()}))
  if(fresh.isEmpty())return
  if(!running&&queue.isEmpty()){total=0;finished=0;stopped=false;limited=false}
  fresh.forEach{queue.addLast(Triple(it.first,it.second,it.third=="near"));total++}
  pump()
 }

 private fun report(current:String=""){onProgress(StartupProgress(total,finished,current,stopped,limited))}

 private fun pump(){
  if(running||stopped||queue.isEmpty())return
  if(!canRun()){report();return}
  val (game,key,readiness)=queue.removeFirst()
  running=true;inFlight=key;val mine=generation
  report(game.id)
  var answered=false
  port.check(game,readiness){outcome->
   if(answered||mine!=generation)return@check
   answered=true;running=false;inFlight=null;finished++
   when(outcome){
    CheckOutcome.NOT_STARTED->{settled.add(key);if(readiness)limited=true}   // an older backend ignores the readiness request: say so, once, without an error
    CheckOutcome.CHECKED,CheckOutcome.FRESH,CheckOutcome.NO_CANDIDATES->settled.add(key)
    CheckOutcome.DENIED,CheckOutcome.FAILED->{stopped=true;queue.clear();settled.add(key)}   // no retry loop around a refusal
    CheckOutcome.CANCELLED->{}
   }
   report()
   pump()
  }
 }

 /** Resume after a pause (the screen came back, playback ended): continues what is queued, never re-adds settled work. */
 fun resume()=pump()

 /** Leaving the screen or switching backend: abandon the check in flight and forget the queue. Settled keys stay, so coming back does not repeat finished work. */
 fun cancel(){
  generation++
  if(running)port.cancel()
  running=false;inFlight=null;queue.clear();total=0;finished=0;stopped=false;limited=false
  report()
 }
}
