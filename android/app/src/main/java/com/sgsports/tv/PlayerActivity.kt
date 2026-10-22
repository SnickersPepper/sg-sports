package com.sgsports.tv

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.compose.ui.graphics.toArgb
import androidx.media3.common.*
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.InetAddress
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** The game a pane is showing; [context] carries only what the browsing card already knew (never invented). */
private class GameRef(val id:String,val title:String,val fixture:Boolean,val context:Game?)
private class PaneUi(val frame:FrameLayout,val view:PlayerView,val label:TextView,val status:TextView,val errorPanel:LinearLayout,val errorText:TextView,val retry:Button,val another:Button,val stop:Button) {
 var exo:ExoPlayer?=null
 var game:GameRef?=null
 var feeds:List<Feed> = emptyList()
 var height:Int?=null
 var audioTag:String?=null
 var reported=false
 fun errorButtons()=listOf(another,retry,stop).filter{it.visibility==View.VISIBLE}
}

/**
 * Full-screen native playback (one game, or two side by side) in its own Activity so that exiting is
 * just finish(): Android's Activity resume returns to the browsing screen with its focus intact.
 * Every upstream connection goes through [PlaybackCoordinator], which owns the provider budget:
 * a pane releases its previous stream before opening another and no provider is ever used twice.
 */
@androidx.media3.common.util.UnstableApi
class PlayerActivity:ComponentActivity(),StreamGateway {
 private val ui=Handler(Looper.getMainLooper())
 private val http=OkHttpClient.Builder().dns(Backend.dns()).connectTimeout(12,TimeUnit.SECONDS).readTimeout(35,TimeUnit.SECONDS).build()
 private var token=""
 private var backendBase=BuildConfig.SERVER_URL
 private var selectedLanguage="en"
 private val coordinator=PlaybackCoordinator(this)
 private val chrome=PlayerChrome()
 private lateinit var panes:List<PaneUi>
 private lateinit var overlay:View
 private lateinit var titleView:TextView
 private lateinit var contextView:TextView
 private lateinit var factsView:TextView
 private lateinit var noticeView:TextView
 private lateinit var stopBtn:Button
 private lateinit var pauseBtn:Button
 private lateinit var nextBtn:Button
 private lateinit var tracksBtn:Button
 private lateinit var fitBtn:Button
 private lateinit var splitBtn:Button
 private lateinit var audioBtn:Button
 private lateinit var pickerPanel:View
 private lateinit var pickerList:LinearLayout
 private lateinit var pickerNote:TextView
 private val pickerButtons=mutableListOf<Button>()
 private var pickerTicket=0
 private var candidates:List<GameRef> = emptyList()
 private val streamPaths=mutableMapOf<String,String>()
 private var destroyed=false
 private var hadError=false
 /** Providers the server is recording right now, held in the same per-provider budget as playback (owner -> provider). */
 private val dvrHolds=mutableMapOf<String,String>()

 private fun dp(v:Float)=(v*resources.displayMetrics.density+.5f).toInt()
 private fun obj(vararg pairs:Any):JSONObject { val o=JSONObject(); var i=0; while(i<pairs.size){o.put(pairs[i] as String,pairs[i+1]);i+=2}; return o }
 /** Always calls back on the main thread; [failed] covers network errors and refusals. Stop/heartbeat ignore it. */
 private fun request(path:String,payload:JSONObject?,failed:()->Unit={},done:(JSONObject)->Unit) {
  if(BuildConfig.DESIGN_MODE){ui.post(failed);return}
  val authToken=token
  io.execute {
   try {
    val b=Request.Builder().url(backendBase+path)
    if(authToken.isNotEmpty())b.header("Authorization","Bearer $authToken")
    if(payload!=null)b.post(payload.toString().toRequestBody("application/json".toMediaType()))
    http.newCall(b.build()).execute().use { r-> val result=JSONObject(r.body!!.string()); if(r.isSuccessful)ui.post { done(result) } else ui.post(failed) }
   } catch(e:Exception) { ui.post(failed) }
  }
 }
 private fun feedOf(raw:JSONObject)=Feed(raw.optString("display_name"),raw.optString("provider_id"),"","",raw)
 private fun providerName(p:String?)=p?.replaceFirstChar{it.uppercase()}?:"this provider"

 // ---- StreamGateway: the only code that talks to /tv/play and /tv/stop for the player ----
 override fun stop(session:String) { streamPaths.remove(session);request("/tv/stop",obj("session",session)) { } }
 override fun open(pane:Int,feed:Feed,result:(String?)->Unit) {
  val ref=panes[pane].game
  if(ref==null){ui.post{result(null)};return}
  request("/tv/play",obj("fixture_id",ref.id,"event_id",if(ref.fixture)"" else ref.id,"provider_id",feed.provider,"stream_id",feed.raw.optString("stream_id")),{result(null)}) { r->
   val sid=r.optString("session");val path=r.optString("stream_path")
   if(sid.isEmpty()||path.isEmpty())result(null) else {streamPaths[sid]=path;result(sid)}
  }
 }

 // ---- styling: Phase 2 tokens, focus readable without color (stroke + lift + scale) ----
 private fun surface(fill:Int,stroke:Int,strokeDp:Float,radiusDp:Float=8f)=GradientDrawable().apply { setColor(fill);cornerRadius=dp(radiusDp).toFloat();setStroke(dp(strokeDp),stroke) }
 private fun label(value:String,size:Float,color:Int,bold:Boolean=false)=TextView(this).apply { text=value;textSize=size;setTextColor(color);if(bold)setTypeface(null,android.graphics.Typeface.BOLD);includeFontPadding=false }
 private fun button(text:String,run:()->Unit)=Button(this).apply {
  this.text=text;textSize=16f;setTextColor(TEXT);isAllCaps=false;gravity=Gravity.CENTER;minHeight=0;minimumHeight=0;setPadding(dp(16f),dp(12f),dp(16f),dp(12f))
  background=surface(SURFACE,STROKE,1f);isFocusable=true;isFocusableInTouchMode=true;isClickable=true;setOnClickListener { run() }
  setOnFocusChangeListener { v,focused->
   v.background=if(focused)surface(SURFACE2,TEXT,2f) else surface(SURFACE,STROKE,1f)
   v.animate().scaleX(if(focused)1.035f else 1f).scaleY(if(focused)1.035f else 1f).setDuration(120).start()
  }
 }

 override fun onCreate(state:Bundle?) {
  super.onCreate(state)
  window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
  @Suppress("DEPRECATION")
  window.decorView.systemUiVisibility=View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
  // Activity.getPreferences() keys on the class name relative to the applicationId, which a flavor
  // suffix breaks; derive the same file name DesignActivity's own getPreferences(0) writes.
  val designActivityClass="com.sgsports.tv.DesignActivity"
  val prefsName=if(designActivityClass.startsWith("$packageName.")) designActivityClass.substring(packageName.length+1) else designActivityClass
  val prefs=getSharedPreferences(prefsName,0)
  token=Backend.token(prefs);backendBase=Backend.base(prefs)
  selectedLanguage=prefs.getString("language","en")?:"en"
  val session=intent.getStringExtra("session")?:""
  val path=intent.getStringExtra("stream_path")?:""
  val title=intent.getStringExtra("title")?:"SG Sports"
  if(!BuildConfig.DESIGN_MODE&&(session.isEmpty()||path.isEmpty())){finish();return}
  val fixtureId=intent.getStringExtra("fixture_id")?:""
  val eventId=intent.getStringExtra("event_id")?:""
  val feeds=try { JSONArray(intent.getStringExtra("feeds")?:"[]") } catch(e:Exception) { JSONArray() }
  val feedIndex=intent.getIntExtra("feed_index",0)
  val context=try { intent.getStringExtra("context")?.let{JSONObject(it)} } catch(e:Exception) { null }
  candidates=try { JSONArray(intent.getStringExtra("candidates")?:"[]").let{a->(0 until a.length()).mapNotNull{a.optJSONObject(it)}.map{c->
   GameRef(c.optString("id"),c.optString("title"),c.optBoolean("fixture"),gameOf(c.optJSONObject("context"),c.optString("id"),c.optString("title"),c.optBoolean("fixture")))}} } catch(e:Exception) { emptyList() }

  val root=FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
  val stage=LinearLayout(this).apply { orientation=LinearLayout.HORIZONTAL }
  panes=listOf(buildPane(0),buildPane(1))
  panes.forEach{ stage.addView(it.frame,LinearLayout.LayoutParams(0,-1,1f).apply { marginStart=if(it===panes[1])dp(4f) else 0 }) }
  panes[1].frame.visibility=View.GONE
  root.addView(stage,FrameLayout.LayoutParams(-1,-1))
  root.addView(buildControls(),FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM))
  root.addView(buildPicker(),FrameLayout.LayoutParams(dp(520f),-1,Gravity.END))
  setContentView(root)

  panes[0].game=GameRef(fixtureId,title,eventId.isEmpty(),gameOf(context,fixtureId,title,false))
  panes[0].feeds=(0 until feeds.length()).mapNotNull{feeds.optJSONObject(it)}.map(::feedOf)
  val current=panes[0].feeds.getOrNull(feedIndex)?:Feed(title,"","","",JSONObject())
  if(!BuildConfig.DESIGN_MODE)streamPaths[session]=path
  coordinator.onChange={sync()}
  coordinator.adopt(0,current,session.ifEmpty{"design"})
  val wantedHolds=try { JSONObject(intent.getStringExtra("dvr_holds")?:"{}").let{o->o.keys().asSequence().associateWith{o.optString(it)}} } catch(e:Exception) { emptyMap() }
  syncDvrHolds(coordinator,dvrHolds,wantedHolds)
  sync()
  panes[0].view.requestFocus()
  if(!BuildConfig.DESIGN_MODE)ui.postDelayed(heartbeat,20000)
 }
 private fun gameOf(j:JSONObject?,id:String,title:String,fixture:Boolean):Game? = j?.let{Game(id,title,it.optString("league"),it.optString("sport"),"",raw=it,fixture=fixture,live=liveScore(it),eventPhase=it.optString("event_phase").ifBlank{null})}

 private fun buildPane(index:Int):PaneUi {
  val frame=FrameLayout(this)
  val view=PlayerView(this).apply { useController=false;resizeMode=AspectRatioFrameLayout.RESIZE_MODE_FIT }
  frame.addView(view,FrameLayout.LayoutParams(-1,-1))
  val nameTag=label("",16f,TEXT,true).apply { setPadding(dp(14f),dp(8f),dp(14f),dp(8f));background=surface(0xB3000000.toInt(),Color.TRANSPARENT,0f);visibility=View.GONE }
  frame.addView(nameTag,FrameLayout.LayoutParams(-2,-2,Gravity.TOP or Gravity.START).apply { setMargins(dp(16f),dp(16f),0,0) })
  val status=label("",20f,TEXT,true).apply { visibility=View.GONE }
  frame.addView(status,FrameLayout.LayoutParams(-2,-2,Gravity.CENTER))
  val errorText=label("",20f,TEXT,true).apply { gravity=Gravity.CENTER }
  lateinit var another:Button;lateinit var retry:Button;lateinit var stopB:Button
  another=button("Try another source"){ nextSource(index)?.let{openOn(index,it)} }
  retry=button("Try again"){ coordinator.panes[index].feed?.let{openOn(index,it)} }
  stopB=button("‹ Stop & games"){exit()}
  val errorPanel=LinearLayout(this).apply {
   orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER_HORIZONTAL;setPadding(dp(32f),dp(24f),dp(32f),dp(24f));background=surface(SURFACE,STROKE,1f,12f);visibility=View.GONE
   addView(errorText,LinearLayout.LayoutParams(-2,-2).apply { bottomMargin=dp(16f) })
   val row=LinearLayout(this@PlayerActivity)
   listOf(another,retry,stopB).forEach{ row.addView(it,LinearLayout.LayoutParams(-2,-2).apply { marginEnd=dp(8f) }) }
   addView(row)
  }
  frame.addView(errorPanel,FrameLayout.LayoutParams(-2,-2,Gravity.CENTER))
  return PaneUi(frame,view,nameTag,status,errorPanel,errorText,retry,another,stopB)
 }
 private fun buildControls():View {
  val overlayBox=LinearLayout(this).apply {
   orientation=LinearLayout.VERTICAL;setPadding(dp(48f),dp(32f),dp(48f),dp(24f))
   background=GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,intArrayOf(Color.TRANSPARENT,0xE6000000.toInt()));visibility=View.GONE
  }
  titleView=label("",24f,TEXT,true).apply { maxLines=1;ellipsize=android.text.TextUtils.TruncateAt.END }
  contextView=label("",16f,GOLD,true)
  factsView=label("",14f,MUTED)
  noticeView=label("",14f,GOLD).apply { visibility=View.GONE }
  listOf(titleView,contextView,factsView,noticeView).forEach{ overlayBox.addView(it,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(4f) }) }
  stopBtn=button("‹ Stop & games"){exit()}
  pauseBtn=button("Pause"){ panes[coordinator.audioPane].exo?.let{e->e.playWhenReady=!e.playWhenReady;showControls()} }
  nextBtn=button("Next source"){ nextSource(coordinator.audioPane)?.let{openOn(coordinator.audioPane,it)} }
  tracksBtn=button("Audio tracks"){audioDialog()}
  fitBtn=button("Picture: Fit"){
   val zoom=panes[0].view.resizeMode==AspectRatioFrameLayout.RESIZE_MODE_FIT
   panes.forEach{it.view.resizeMode=if(zoom)AspectRatioFrameLayout.RESIZE_MODE_ZOOM else AspectRatioFrameLayout.RESIZE_MODE_FIT}
   fitBtn.text=if(zoom)"Picture: Fill (crop)" else "Picture: Fit";showControls()
  }
  splitBtn=button("Add second game"){ if(coordinator.panes[1].feed!=null){closeSecond()} else openPicker() }
  audioBtn=button("Audio: switch game"){ coordinator.setAudio(1-coordinator.audioPane);sync();showControls() }
  val row=LinearLayout(this).apply { gravity=Gravity.CENTER_VERTICAL }
  listOf(stopBtn,pauseBtn,nextBtn,tracksBtn,fitBtn,splitBtn,audioBtn).forEach{ row.addView(it,LinearLayout.LayoutParams(0,-2,1f).apply { marginEnd=dp(8f) }) }
  overlayBox.addView(row)
  overlay=overlayBox
  return overlayBox
 }
 private fun controlButtons()=listOf(stopBtn,pauseBtn,nextBtn,tracksBtn,fitBtn,splitBtn,audioBtn).filter{it.visibility==View.VISIBLE}
 private fun buildPicker():View {
  val box=LinearLayout(this).apply {
   orientation=LinearLayout.VERTICAL;setPadding(dp(32f),dp(32f),dp(32f),dp(32f));background=surface(0xF2000000.toInt(),STROKE,1f,0f);visibility=View.GONE
  }
  box.addView(label("Add a second game",28f,TEXT,true),LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(8f) })
  pickerNote=label("",16f,MUTED)
  box.addView(pickerNote,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(16f) })
  pickerList=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL }
  box.addView(ScrollView(this).apply { addView(pickerList) },LinearLayout.LayoutParams(-1,0,1f))
  pickerPanel=box
  return box
 }

 // ---- reconciling coordinator state with players and views ----
 private fun releaseExo(p:PaneUi) { p.view.player=null;p.exo?.release();p.exo=null;p.height=null;p.audioTag=null;p.reported=false }
 private fun sync() {
  if(destroyed)return
  val split=coordinator.panes[1].feed!=null
  panes[1].frame.visibility=if(split)View.VISIBLE else View.GONE
  panes.forEachIndexed { i,p->
   val st=coordinator.panes[i]
   when(st.status) {
    PaneStatus.PLAYING->{
     if(p.exo==null){
      if(BuildConfig.DESIGN_MODE){p.exo=ExoPlayer.Builder(this).build();p.view.player=p.exo}
      else streamPaths[st.session]?.let{startPlayer(i,it)}
     }
     p.status.visibility=View.GONE;p.errorPanel.visibility=View.GONE
    }
    PaneStatus.OPENING->{releaseExo(p);p.status.text="Opening…";p.status.visibility=View.VISIBLE;p.errorPanel.visibility=View.GONE}
    PaneStatus.ERROR->{
     releaseExo(p);p.status.visibility=View.GONE
     p.errorText.text="This source stopped playing."
     p.another.visibility=if(nextSource(i)!=null)View.VISIBLE else View.GONE
     p.errorPanel.visibility=View.VISIBLE
    }
    PaneStatus.EMPTY->{releaseExo(p);p.status.visibility=View.GONE;p.errorPanel.visibility=View.GONE}
   }
   // Audio follows the active pane; changing it only moves volume and the highlight, never a stream.
   p.exo?.volume=if(i==coordinator.audioPane)1f else 0f
   p.frame.foreground=if(split&&i==coordinator.audioPane)surface(Color.TRANSPARENT,TEXT,3f,0f) else null
   p.label.visibility=if(split&&p.game!=null)View.VISIBLE else View.GONE
   p.label.text=(if(i==coordinator.audioPane)"Audio on · " else "")+(p.game?.title?:"")
  }
  chrome.errorShown=coordinator.panes.any{it.status==PaneStatus.ERROR}
  if(chrome.errorShown&&!hadError){hideControlsNow();ui.post{ panes.firstOrNull{it.errorPanel.visibility==View.VISIBLE}?.errorButtons()?.firstOrNull()?.requestFocus() }}
  if(!chrome.errorShown&&hadError)ui.post{panes[coordinator.audioPane].view.requestFocus()}
  hadError=chrome.errorShown
  refreshControls()
 }
 private fun startPlayer(i:Int,path:String) {
  val p=panes[i]
  val source=OkHttpDataSource.Factory(http).setDefaultRequestProperties(mapOf("Authorization" to "Bearer $token"))
  val exo=ExoPlayer.Builder(this).setMediaSourceFactory(DefaultMediaSourceFactory(source)).build()
  exo.trackSelectionParameters=exo.trackSelectionParameters.buildUpon().setPreferredAudioLanguage(selectedLanguage).build()
  exo.volume=if(i==coordinator.audioPane)1f else 0f
  p.view.player=exo;p.exo=exo
  exo.addListener(object:Player.Listener {
   override fun onTracksChanged(tracks:Tracks) {
    if(p.exo!==exo)return
    p.reported=true
    for(g in tracks.groups){
     for(t in 0 until g.length)if(g.isTrackSelected(t)){
      val f=g.getTrackFormat(t)
      if(g.type==C.TRACK_TYPE_VIDEO&&f.height>0)p.height=f.height
      if(g.type==C.TRACK_TYPE_AUDIO&&f.language!=null){
       p.audioTag=f.language
       val s=coordinator.panes[i].session
       if(s.isNotEmpty())request("/tv/audio",obj("session",s,"language",f.language!!)) { }
      }
     }
    }
    refreshControls()
   }
   override fun onVideoSizeChanged(size:VideoSize) { if(p.exo===exo&&size.height>0){p.height=size.height;refreshControls()} }
   override fun onPlayerError(e:PlaybackException) { if(p.exo===exo){coordinator.fail(i,"This source stopped playing.");sync()} }
  })
  exo.setMediaItem(MediaItem.fromUri(backendBase+path))
  exo.prepare();exo.play()
 }
 private fun refreshControls() {
  if(!::overlay.isInitialized)return
  val ai=coordinator.audioPane;val p=panes[ai];val st=coordinator.panes[ai]
  titleView.text=p.game?.title?:"SG Sports"
  contextView.text=liveContext(p.game?.context).orEmpty()
  contextView.visibility=if(contextView.text.isEmpty())View.GONE else View.VISIBLE
  val feedName=st.feed?.name?.takeIf{it.isNotBlank()}
  factsView.text=listOfNotNull(feedName,if(BuildConfig.DESIGN_MODE)"Offline lifecycle test · no stream" else playbackFacts(p.height,p.audioTag,p.reported)).joinToString(" · ")
  val playing=p.exo?.playWhenReady!=false
  pauseBtn.text=if(playing)"Pause" else "Play"
  pauseBtn.visibility=if(p.exo!=null)View.VISIBLE else View.GONE
  tracksBtn.visibility=if(p.exo!=null)View.VISIBLE else View.GONE
  nextBtn.visibility=if(nextSource(ai)!=null)View.VISIBLE else View.GONE
  val split=coordinator.panes[1].feed!=null
  splitBtn.text=if(split)"Close second game" else "Add second game"
  splitBtn.visibility=if(split||(candidates.isNotEmpty()&&coordinator.panes[0].status==PaneStatus.PLAYING))View.VISIBLE else View.GONE
  audioBtn.visibility=if(split)View.VISIBLE else View.GONE
 }

 // ---- source switching ----
 private fun sameFeed(a:Feed,b:Feed)=a.provider==b.provider&&a.raw.optString("stream_id")==b.raw.optString("stream_id")
 /** The next ranked, playable, budget-legal source after the current one; null when there is nowhere to go. */
 private fun nextSource(i:Int):Feed? {
  val p=panes[i];val cur=coordinator.panes[i].feed?:return null
  val allowed=coordinator.recoveryChoices(i,p.feeds)
  if(allowed.isEmpty())return null
  val at=p.feeds.indexOfFirst{sameFeed(it,cur)}
  return (1..p.feeds.size).map{p.feeds[(at+it).mod(p.feeds.size)]}.firstOrNull{f->allowed.any{it===f}}
 }
 private fun openOn(i:Int,feed:Feed) {
  when(val d=coordinator.open(i,feed)) {
   is Decision.Rejected->notice(d.message)
   Decision.Accepted->{notice("");panes[i].status.text="Opening…"}
  }
  sync()
  if(controlsVisible())showControls()
 }
 private fun notice(message:String) { noticeView.text=message;noticeView.visibility=if(message.isEmpty())View.GONE else View.VISIBLE }
 private fun closeSecond() {
  coordinator.close(1);panes[1].game=null;panes[1].feeds=emptyList()
  sync();showControls();splitBtn.requestFocus()
 }

 // ---- split-screen picker ----
 private fun openPicker() {
  val first=coordinator.panes[0].feed?.provider
  pickerNote.text="Your first game is using ${providerName(first)}. The second game plays from the other provider, because each provider allows only one stream at a time."
  pickerList.removeAllViews();pickerButtons.clear()
  if(candidates.isEmpty())pickerList.addView(label("No other live games right now.",18f,MUTED))
  candidates.forEach{c->
   val b=button(c.title){chooseCandidate(c)}.apply { gravity=Gravity.START or Gravity.CENTER_VERTICAL }
   pickerButtons.add(b);pickerList.addView(b,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(8f) })
  }
  chrome.picker=true;pickerPanel.visibility=View.VISIBLE
  ui.removeCallbacks(hideControls)
  pickerButtons.firstOrNull()?.requestFocus()
 }
 private fun closePicker() {
  pickerTicket++;pickerPanel.visibility=View.GONE
  (if(chrome.controls)splitBtn else panes[coordinator.audioPane].view).requestFocus()
  if(chrome.controls)scheduleHide()
 }
 private fun chooseCandidate(c:GameRef) {
  val ticket=++pickerTicket
  pickerNote.text="Checking sources for ${c.title}…"
  request(if(c.fixture)"/tv/feeds" else "/tv/sports/feeds",obj(if(c.fixture)"fixture_id" else "event_id",c.id),{ if(ticket==pickerTicket)pickerNote.text="Couldn't load sources for ${c.title}. Your first game keeps playing." }) { r->
   if(ticket!=pickerTicket||destroyed)return@request
   val feeds=r.objects("feeds").map(::feedOf)
   val choices=coordinator.secondPaneChoices(feeds)
   if(choices.isEmpty()){pickerNote.text=coordinator.constraintMessage(feeds)?:"No playable feed is listed for this game.";return@request}
   panes[1].game=c;panes[1].feeds=feeds
   when(val d=coordinator.open(1,choices.first())) {
    is Decision.Rejected->{pickerNote.text=d.message;if(coordinator.panes[1].feed==null)panes[1].game=null}
    Decision.Accepted->{chrome.picker=false;closePicker();sync()}
   }
  }
 }

 // ---- controls visibility, audio tracks, keys, lifecycle ----
 private fun controlsVisible()=overlay.visibility==View.VISIBLE
 private val hideControls=Runnable { if(!chrome.picker&&!chrome.errorShown)hideControlsNow() }
 private fun hideControlsNow() { chrome.controls=false;overlay.visibility=View.GONE;if(!chrome.picker&&!chrome.errorShown)panes[coordinator.audioPane].view.requestFocus() }
 private fun scheduleHide() { ui.removeCallbacks(hideControls);ui.postDelayed(hideControls,5000) }
 private fun showControls() {
  val hidden=!controlsVisible()
  chrome.controls=true;refreshControls();overlay.visibility=View.VISIBLE
  if(hidden)(if(pauseBtn.visibility==View.VISIBLE)pauseBtn else stopBtn).requestFocus()
  scheduleHide()
 }
 private val heartbeat:Runnable=object:Runnable { override fun run() {
  coordinator.panes.forEach{ if(it.status==PaneStatus.PLAYING&&it.session.isNotEmpty())request("/tv/heartbeat",obj("session",it.session)) { } }
  ui.postDelayed(this,20000)
 } }
 private fun audioDialog() {
  val exo=panes[coordinator.audioPane].exo?:return
  val labels=ArrayList<String>();val choices=ArrayList<TrackSelectionOverride>()
  for(g in exo.currentTracks.groups)if(g.type==C.TRACK_TYPE_AUDIO)for(i in 0 until g.length)if(g.isTrackSupported(i)) {
   labels.add((audioLanguageLabel(g.getTrackFormat(i).language)?:"Language not tagged")+" · track "+(i+1))
   choices.add(TrackSelectionOverride(g.mediaTrackGroup,i))
  }
  if(labels.isEmpty()) { android.app.AlertDialog.Builder(this).setTitle("SG Sports").setMessage("No audio tracks reported yet.").setPositiveButton("OK",null).show();return }
  android.app.AlertDialog.Builder(this).setTitle("Audio tracks · reported by the stream").setItems(labels.toTypedArray()) { _,index-> exo.trackSelectionParameters=exo.trackSelectionParameters.buildUpon().setOverrideForType(choices[index]).build() }.show()
 }
 private fun stopPlayback() {
  ui.removeCallbacks(hideControls);ui.removeCallbacks(heartbeat)
  pickerTicket++
  coordinator.onChange={}
  if(::panes.isInitialized)panes.forEach{releaseExo(it)}
  coordinator.closeAll()
 }
 private fun exit() { stopPlayback();setResult(RESULT_CANCELED);finish() }

 // Back always resolves here: close the picker, then hide the controls, then leave playback.
 @Suppress("DEPRECATION", "MissingSuperCall")
 override fun onBackPressed() {
  when(chrome.back()) {
   BackResult.CLOSE_PICKER->closePicker()
   BackResult.HIDE_CONTROLS->{ui.removeCallbacks(hideControls);hideControlsNow()}
   BackResult.EXIT->exit()
  }
 }
 private fun toDpad(code:Int)=when(code) {
  KeyEvent.KEYCODE_DPAD_LEFT->Dpad.LEFT;KeyEvent.KEYCODE_DPAD_RIGHT->Dpad.RIGHT
  KeyEvent.KEYCODE_DPAD_UP->Dpad.UP;KeyEvent.KEYCODE_DPAD_DOWN->Dpad.DOWN
  KeyEvent.KEYCODE_DPAD_CENTER,KeyEvent.KEYCODE_ENTER->Dpad.SELECT
  else->null
 }
 private fun focusList():List<View> = when {
  chrome.picker->pickerButtons
  chrome.errorShown->panes.firstOrNull{it.errorPanel.visibility==View.VISIBLE}?.errorButtons().orEmpty()
  else->controlButtons()
 }
 @Suppress("RestrictedApi")
 override fun dispatchKeyEvent(event:KeyEvent):Boolean {
  val key=toDpad(event.keyCode)
  if(key==null||!::panes.isInitialized||coordinator.activeCount()+panes.count{it.errorPanel.visibility==View.VISIBLE}==0)return super.dispatchKeyEvent(event)
  if(chrome.key(key)==KeyResult.REVEAL_CONTROLS) {
    // The first press only reveals the controls (on key-up, matching the old behavior).
    if(event.action==KeyEvent.ACTION_UP)showControls() else chrome.controls=false
    return true
  }
  if(event.action==KeyEvent.ACTION_DOWN) {
   if(controlsVisible())scheduleHide()
   if(key!=Dpad.SELECT) {
    val list=focusList()
    if(list.isNotEmpty())list[moveFocus(list.indexOf(currentFocus).coerceAtLeast(0),list.size,key,vertical=chrome.picker)].requestFocus()
    return true
   }
  }
  return super.dispatchKeyEvent(event)
 }
 override fun onStop() { stopPlayback();if(!isFinishing)finish();super.onStop() }
 override fun onDestroy() { destroyed=true;stopPlayback();super.onDestroy() }

 companion object {
  // One process-wide serial executor: a stop request issued while the Activity is closing still goes
  // out, in order, ahead of anything after it -- that ordering is what makes release-before-open real.
  private val io=Executors.newSingleThreadExecutor { r->Thread(r,"sg-player-io").apply { isDaemon=true } }
  private val TEXT=SgTheme.Text.toArgb();private val MUTED=SgTheme.Muted.toArgb();private val GOLD=SgTheme.Gold.toArgb()
  private val SURFACE=SgTheme.Surface.toArgb();private val SURFACE2=SgTheme.Surface2.toArgb();private val STROKE=SgTheme.StrokeRest.toArgb()
 }
}
