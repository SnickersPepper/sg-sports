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
import org.json.JSONObject
import java.net.InetAddress
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Plays one saved recording from the beginning. The video comes from the backend's own disk, so no
 * provider stream is opened and this screen takes no slot in the playback budget. It never shows a
 * live indicator; wording comes from [DvrLogic] so an incomplete recording says so.
 * Controls are always on screen (Pause/Play, Back) in a fixed order; Back exits to the Recordings
 * page, where focus is restored to the recording that was opened.
 */
@androidx.media3.common.util.UnstableApi
class RecordingPlayerActivity:ComponentActivity() {
 private val ui=Handler(Looper.getMainLooper())
 private val http=OkHttpClient.Builder().dns(Backend.dns()).connectTimeout(12,TimeUnit.SECONDS).readTimeout(35,TimeUnit.SECONDS).build()
 private var token=""
 private var backendBase=BuildConfig.SERVER_URL
 private var recordingId=""
 private var failures=0
 private var destroyed=false
 private var exo:ExoPlayer?=null
 private lateinit var view:PlayerView
 private lateinit var titleView:TextView
 private lateinit var captionView:TextView
 private lateinit var messageView:TextView
 private lateinit var pauseBtn:Button
 private lateinit var actionBtn:Button
 private lateinit var backBtn:Button
 private var recovery:PlaybackRecovery?=null

 private fun dp(v:Float)=(v*resources.displayMetrics.density+.5f).toInt()
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
 /** Main-thread callback; [failed] gets true when the server could not be reached at all. */
 private fun request(path:String,failed:(Boolean)->Unit,done:(JSONObject)->Unit) {
  val authToken=token
  io.execute {
   try {
    val b=Request.Builder().url(backendBase+path).header("Authorization","Bearer $authToken")
    http.newCall(b.build()).execute().use { r-> val result=JSONObject(r.body!!.string()); if(r.isSuccessful)ui.post { if(!destroyed)done(result) } else ui.post { if(!destroyed)failed(false) } }
   } catch(e:Exception) { ui.post { if(!destroyed)failed(true) } }
  }
 }

 override fun onCreate(state:Bundle?) {
  super.onCreate(state)
  window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
  @Suppress("DEPRECATION")
  window.decorView.systemUiVisibility=View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
  val designActivityClass="com.sgsports.tv.DesignActivity"
  val prefsName=if(designActivityClass.startsWith("$packageName.")) designActivityClass.substring(packageName.length+1) else designActivityClass
  getSharedPreferences(prefsName,0).let{token=Backend.token(it);backendBase=Backend.base(it)}
  recordingId=intent.getStringExtra("recording_id")?:""
  if(recordingId.isEmpty()){finish();return}
  val root=FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
  view=PlayerView(this).apply { useController=false;resizeMode=AspectRatioFrameLayout.RESIZE_MODE_FIT }
  root.addView(view,FrameLayout.LayoutParams(-1,-1))
  titleView=label(intent.getStringExtra("title")?:"Saved recording",24f,TEXT,true).apply { maxLines=1;ellipsize=android.text.TextUtils.TruncateAt.END }
  captionView=label(recordingPlayerLabel(null),16f,GOLD,true)
  messageView=label("",18f,TEXT,true).apply { visibility=View.GONE }
  pauseBtn=button("Pause"){ exo?.let{e->e.playWhenReady=!e.playWhenReady;refresh()} }
  actionBtn=button("Try again"){ retry() }.apply { visibility=View.GONE }
  backBtn=button("‹ Back to recordings"){ exit() }
  val row=LinearLayout(this).apply { gravity=Gravity.CENTER_VERTICAL }
  // Fixed D-pad order, left to right: Pause/Play, (Try again), Back.
  listOf(pauseBtn,actionBtn,backBtn).forEach{ row.addView(it,LinearLayout.LayoutParams(-2,-2).apply { marginEnd=dp(8f) }) }
  val bar=LinearLayout(this).apply {
   orientation=LinearLayout.VERTICAL;setPadding(dp(48f),dp(32f),dp(48f),dp(24f))
   background=GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,intArrayOf(Color.TRANSPARENT,0xE6000000.toInt()))
   listOf(titleView,captionView,messageView).forEach{ addView(it,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(6f) }) }
   addView(row,LinearLayout.LayoutParams(-2,-2).apply { topMargin=dp(8f) })
  }
  root.addView(bar,FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM))
  setContentView(root)
  if(BuildConfig.DESIGN_MODE){show(PlaybackRecovery("The design preview has no saved video to play.",RecoveryAction.BACK,"Back to recordings"));return}
  if(token.isEmpty()){show(PlaybackRecovery("Pair this TV to play recordings.",RecoveryAction.BACK,"Back to recordings"));return}
  request("/tv/recordings",{},{ r-> parseRecordings(r).buckets.flatMap{it.recordings}.firstOrNull{it.id==recordingId}?.let{captionView.text=recordingPlayerLabel(it)} })
  startPlayer()
  pauseBtn.requestFocus()
 }

 private fun startPlayer() {
  releasePlayer()
  val source=OkHttpDataSource.Factory(http).setDefaultRequestProperties(mapOf("Authorization" to "Bearer $token"))
  val player=ExoPlayer.Builder(this).setMediaSourceFactory(DefaultMediaSourceFactory(source)).build()
  view.player=player;exo=player
  player.addListener(object:Player.Listener {
   override fun onPlayerError(e:PlaybackException) { if(exo===player)playbackFailed() }
   override fun onPlaybackStateChanged(s:Int) {
    if(exo!==player)return
    if(s==Player.STATE_READY){messageView.visibility=View.GONE;actionBtn.visibility=View.GONE}
    if(s==Player.STATE_ENDED)playbackEnded()
    refresh()
   }
  })
  player.setMediaItem(MediaItem.fromUri(backendBase+"/tv/recordings/media/"+recordingId))
  player.prepare();player.play()
  refresh()
 }
 private fun releasePlayer(){ view.player=null;exo?.release();exo=null }
 private fun refresh(){ pauseBtn.text=if(exo?.playWhenReady==false)"Play" else "Pause";pauseBtn.visibility=if(exo!=null)View.VISIBLE else View.GONE }

 private fun show(r:PlaybackRecovery) {
  recovery=r
  messageView.text=r.message;messageView.visibility=View.VISIBLE
  actionBtn.text=r.actionLabel
  // A Back-only outcome has no separate action button: the Back button is the recovery.
  actionBtn.visibility=if(r.action==RecoveryAction.RETRY)View.VISIBLE else View.GONE
  refresh()
  ui.post { (if(r.action==RecoveryAction.RETRY)actionBtn else backBtn).requestFocus() }
 }
 private fun playbackFailed() {
  failures++
  releasePlayer()
  request("/tv/recordings",{offline->show(recordingPlaybackRecovery(null,failures,offline))}) { r->
   val latest=parseRecordings(r).buckets.flatMap{it.recordings}.firstOrNull{it.id==recordingId}
   latest?.let{captionView.text=recordingPlayerLabel(it)}
   show(recordingPlaybackRecovery(latest,failures,false))
  }
 }
 private fun playbackEnded() {
  request("/tv/recordings",{ show(PlaybackRecovery(recordingEndedMessage(null),RecoveryAction.BACK,"Back to recordings")) }) { r->
   val latest=parseRecordings(r).buckets.flatMap{it.recordings}.firstOrNull{it.id==recordingId}
   show(PlaybackRecovery(recordingEndedMessage(latest),RecoveryAction.BACK,"Back to recordings"))
  }
 }
 /** Retry restarts the saved video from the beginning; the server serves recordings from byte zero only. */
 private fun retry(){ messageView.visibility=View.GONE;actionBtn.visibility=View.GONE;startPlayer();pauseBtn.requestFocus() }
 private fun exit(){ releasePlayer();setResult(RESULT_CANCELED);finish() }

 @Suppress("DEPRECATION", "MissingSuperCall")
 override fun onBackPressed(){ exit() }
 override fun onStop(){ releasePlayer();if(!isFinishing)finish();super.onStop() }
 override fun onDestroy(){ destroyed=true;releasePlayer();super.onDestroy() }

 companion object {
  private val io=Executors.newSingleThreadExecutor { r->Thread(r,"sg-recording-io").apply { isDaemon=true } }
  private val TEXT=SgTheme.Text.toArgb();private val GOLD=SgTheme.Gold.toArgb()
  private val SURFACE=SgTheme.Surface.toArgb();private val SURFACE2=SgTheme.Surface2.toArgb();private val STROKE=SgTheme.StrokeRest.toArgb()
 }
}
