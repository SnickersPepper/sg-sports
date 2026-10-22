package com.sgsports.tv

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.compose.runtime.*
import androidx.compose.ui.platform.ComposeView
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.Locale

/** Compose presentation over the existing authenticated API and native Media3 player. */
@androidx.media3.common.util.UnstableApi
class DesignActivity:MainActivity() {
 private var state by mutableStateOf(SgState(loading=true))
 private var root:ComposeView?=null
 private lateinit var mapper:GameMapper
 private var returnFocus by mutableIntStateOf(0)
 // A cold launch starts at Home. Activity resume (including player exit) keeps the
 // current route and restores its last focused item without reattaching ComposeView.
 private var landOnHome by mutableIntStateOf(0)
 private var hasResumed=false
 private var lastRefresh=0L
 /** The favorite whose precise schedule arrives as fixtures (declared in favorites.json), never inferred from list position. */
 private val fixtureTeamId get()=state.teams.firstOrNull{it.fixtureFeed}?.id.orEmpty()
 // Set from an external ADB/deep-link intent extra (e.g. a Home Assistant automation calling
 // "am start -n com.sgsports.tv/.DesignActivity --es watch_fixture <id>"). Consumed once
 // state.upcoming actually contains a matching game -- that list only exists after /tv/home
 // resolves, so a cold start can't call watchNow() before then.
 private var pendingWatchFixture:String?=null
 // Keeps data fresh without three independent triggers stepping on each other: one 90s
 // foreground timer (naturally off-screen — and so inert — during playback or once backgrounded,
 // since onResume/onPause bracket it) plus an immediate refresh on tab change, throttled against
 // whichever fired more recently so switching tabs quickly can't fire two refreshes back to back.
 // Quiet: the loading skeleton replacing on-screen cards every 90s read as a flash/flicker to
 // someone just sitting there browsing, not as useful feedback — this refresh has nothing to
 // show them until it lands anyway, so it swaps the data in directly instead.
 private val periodicRefresh=object:Runnable { override fun run(){refresh(showLoading=false);ui.postDelayed(this,90_000)} }
 private fun refreshIfStale(){if(System.currentTimeMillis()-lastRefresh>=20_000)loadHome()}
 override fun onResume(){
  super.onResume();ui.postDelayed(periodicRefresh,90_000)
  if(!hasResumed){hasResumed=true;landOnHome++}else returnFocus++
  offerStartupChecks()
 }
 // Favorite live / about-to-start games are checked first, serially, as soon as the catalog is on
 // screen. The queue is dropped whenever the screen leaves the foreground (player, another app),
 // so a check never runs under playback; coming back re-offers the catalog, and finished work is
 // remembered so resume or recomposition cannot repeat it.
 private var startupInFlight=false
 private val startupChecks by lazy{StartupVerification({System.currentTimeMillis()/1000},object:CheckPort{
  private var epoch=0
  override fun check(game:Game,readiness:Boolean,done:(CheckOutcome)->Unit){
   val mine=++epoch;startupInFlight=true
   fun finish(o:CheckOutcome){if(mine!=epoch)return;startupInFlight=false;done(o)}
   val deny=Runnable{finish(CheckOutcome.DENIED)}
   fun poll(attempt:Int){
    if(attempt>=VERIFY_POLL_LIMIT){finish(CheckOutcome.CHECKED);return}
    ui.postDelayed({
     if(mine!=epoch)return@postDelayed
     request("/tv/sports/feeds",obj("event_id",game.id),deny){r->if(mine!=epoch)return@request;if(r.optJSONObject("verification")?.optBoolean("running")==true)poll(attempt+1) else finish(CheckOutcome.CHECKED)}
    },1500)
   }
   request("/tv/verify",if(readiness)obj("event_id",game.id).put("mode","readiness") else obj("event_id",game.id),deny){r->
    if(mine!=epoch)return@request
    when(r.optString("status")){
     "started","already_running"->poll(0)
     "fresh"->finish(CheckOutcome.FRESH)
     "not_started"->finish(CheckOutcome.NOT_STARTED)
     "no_candidates"->finish(CheckOutcome.NO_CANDIDATES)
     else->finish(CheckOutcome.FAILED)
    }
   }
  }
  override fun cancel(){epoch++;startupInFlight=false}
 },{hasResumed&&state.selected==null&&token.isNotEmpty()&&!BuildConfig.DESIGN_MODE},{p->state=state.copy(startupChecks=p)})}
 private fun offerStartupChecks(){
  if(!hasResumed||state.programs.isEmpty())return
  // The profile's favorites decide what is checked, so wait for them (a failed load falls back to the installed list).
  if(state.prefs is PrefsUi.Idle&&!BuildConfig.DESIGN_MODE&&token.isNotEmpty()){loadPrefs();return}
  if(state.prefs is PrefsUi.Loading)return
  startupChecks.offer(state.programs,activeFavoriteTeams(state.teams,state.prefs))
 }
 override fun onPause(){super.onPause();ui.removeCallbacks(periodicRefresh);startupChecks.cancel()}
 override fun onCreate(savedInstanceState:Bundle?) {
  // Preserve pairing when the existing living-room app changes launcher Activity.
  val current=getPreferences(0)
  if(!current.contains("token")) {
   val old=getSharedPreferences("MainActivity",0)
   if(old.contains("token"))current.edit().putString("token",old.getString("token",""))
    .putString("language",old.getString("language","en")).apply()
  }
  FavoritesMigration.stamp(SharedPrefStore(current))
  ArtworkRuntime.install(this){Backend.base(current) to Backend.token(current)}
  super.onCreate(savedInstanceState)
  pendingWatchFixture=intent.getStringExtra("watch_fixture")
 }
 override fun onNewIntent(intent:Intent) {
  super.onNewIntent(intent)
  setIntent(intent)
  pendingWatchFixture=intent.getStringExtra("watch_fixture")
  tryConsumePendingWatch()
 }
 private fun tryConsumePendingWatch() {
  val id=pendingWatchFixture?:return
  val game=state.upcoming.firstOrNull { it.id==id }?:return
  pendingWatchFixture=null
  watchNow(game)
 }
 // A background check's refusal shows in the Home status line, not as a page-wide error.
 override fun scopedFailure(path:String,status:Int)=isPageScopedFailure(path,status)||(startupInFlight&&(path=="/tv/verify"||path=="/tv/sports/feeds"))
 override fun loadHome()=refresh(showLoading=true)
 private fun refresh(showLoading:Boolean) {
  lastRefresh=System.currentTimeMillis()
  if(!::mapper.isInitialized)mapper=GameMapper(this)
  if(root==null){root=ComposeView(this).apply { setViewCompositionStrategy(androidx.compose.ui.platform.ViewCompositionStrategy.DisposeOnViewTreeLifecycleDestroyed); setContent { val ob=state.onboarding;if(ob!=null)OnboardingScreen(ob,onEnterAddress={onboardAddressDialog()},onRetry={onboardTest(ob.backend)},onEnterCode={onboardCodeDialog()},onBack={onboardBack()},onFinish={finishOnboarding(false)},onChoosePreferences={finishOnboarding(true)}) else SgApp(state,returnFocus,landOnHome=landOnHome,initialPage=startPage,onRefresh={loadHome()},onGame={openGame(it)},onWatchNow={watchNow(it)},onFeed={play(it)},onSetting={setting(it)},onPageChange={p->if(p=="Recordings")loadRecordings(force=false) else if(p=="Preferences")loadPrefs() else if(p=="Connection")checkAdmin() else refreshIfStale()},onFavoriteOpen={openTeamHub(it)},onRecording={playRecording(it)},onRecordingsRefresh={loadRecordings()},onRecordingsNeeded={loadRecordings(force=false)},onPrefsReload={loadPrefs()},onPrefsSave={savePrefs(it)},onPrefsUseLatest={useLatestPrefs()},onPrefsOverwrite={savePrefs(it,overwrite=true)},onConnectionChange={changeBackend()}) } };setContentView(root)}
  if(!onboardingChecked&&!BuildConfig.DESIGN_MODE){
   onboardingChecked=true
   if(OnboardingLogic.shouldOnboard(Backend.token(getPreferences(0)).isNotEmpty(),BuildConfig.SERVER_URL,getPreferences(0).getBoolean(OnboardingLogic.KEY_DONE,false),false))beginOnboarding()
  }
  if(state.onboarding!=null)return
  val backend=Backend.base(getPreferences(0))
  if(backend.isEmpty()&&!BuildConfig.DESIGN_MODE){
   state=state.copy(loading=false,offline=false,paired=false,error="No SG Sports backend is selected yet. Enter the address of your backend to continue.",backendUrl="",adminUrl="")
   if(!backendPrompted){backendPrompted=true;changeBackend()}
   return
  }
  if(BuildConfig.DESIGN_MODE){DesignFixtures.state(this)?.let{state=it.copy(backendUrl=backend,adminUrl=BackendPolicy.adminUrl(backend),hub=state.hub,recordings=state.recordings);return}}
  state=state.copy(teams=loadTeams(this),loading=showLoading,error="",offline=false,paired=token.isNotEmpty(),language=Locale(selectedLanguage).displayLanguage,backendUrl=backend,adminUrl=BackendPolicy.adminUrl(backend))
  request("/tv/home",null) { data ->
   home=data
   val fixtures=data.objects("fixtures").map { mapper.fixture(it,fixtureTeamId) }.toMutableList()
   data.optJSONObject("game")?.optJSONObject("fixture")?.let { f -> if(fixtures.none { it.id==f.optString("id") })fixtures.add(0,mapper.fixture(f,fixtureTeamId)) }
   // Barcelona keeps its precise, feed-resolvable schedule fixture; the other six favorites
   // get their next game from the backend's ESPN-sourced list. Matched by the canonical team_id
   // the backend attaches to every entry, never by position: the backend already drops teams
   // with no game in the next 2 weeks, which shifts array positions.
   // The same 2-week cap is applied here to Barcelona's own next fixture, since that one
   // isn't filtered server-side.
   val within2Weeks=System.currentTimeMillis()/1000+14*86400
   val barcaNext=fixtures.firstOrNull()?.takeIf { it.raw.optLong("kickoff")<=within2Weeks }
   val nextGames=data.objects("next_games").mapNotNull { g -> state.teams.firstOrNull { it.id==g.optString("team_id") }?.takeIf { !it.fixtureFeed }?.let { mapper.nextGame(g,it) } }
   val coverage=data.objects("next_games_coverage").mapNotNull { c->
    state.teams.firstOrNull { it.id==c.optString("team_id") }?.id?.let {id->id to ScheduleCoverage(c.optString("status"),c.optString("source"))}
   }.toMap()
   // Closest-to-start-first, not favorites.json order -- the same rule as every other game row
   // in the app (Coming Next already sorted this way; Team Hub's games list is server-sorted).
   state=state.copy(upcoming=(listOfNotNull(barcaNext)+nextGames).sortedBy { it.startEpoch() },coverage=coverage,loading=false)
   tryConsumePendingWatch()
   loadRecordings()
   if(token.isNotEmpty())request("/tv/sports",null) { guide ->
    sportsData=guide
    // Restrict to the user's selected competitions; retain the backend's exact spelling.
    val catalog=guide.optJSONObject("sports")
    val leagues=competitions.keys.associateWith { sport -> val a=catalog?.optJSONArray(sport); if(a==null)competitions.getValue(sport) else (0 until a.length()).map { a.optString(it) } }
    // request() already hands this callback the main thread (so UI-touching callers don't need
    // to think about it), but mapping every event against the team-identity patterns is real CPU
    // work -- ~370 events measured at ~170ms even after the matching itself was made fast, which
    // is still a dropped-frames-worth of main-thread block on every refresh. Reusing the same
    // background executor the network call itself already runs on keeps that off the UI thread;
    // only the resulting state write comes back to main.
    val teamsSnapshot=state.teams
    io.execute {
     val events=guide.objects("events").map { mapper.program(it,teamsSnapshot) }
     // Official LVBP schedule events ride the same list as other not-yet-airing games; they carry
     // their own evidence block and freshness, and are never auto-played.
     val later=(guide.objects("later")+guide.objects("lvbp")).map { mapper.program(it,teamsSnapshot,scheduled=true) }
     val lvbpSchedule=parseLvbpSchedule(guide.optJSONObject("lvbp_schedule"))
     ui.post { state=state.copy(programs=events,later=later,leagues=leagues,fresh=guide.optBoolean("fresh"),loading=false,lvbpSchedule=lvbpSchedule,loadedAt=System.currentTimeMillis()/1000);offerStartupChecks() }
    }
   }
  }
 }
 override fun drawHome(){returnToBrowsing()}
 override fun returnToBrowsing(){root?.let { setContentView(it) };returnFocus++}
 override fun message(value:String){state=state.copy(loading=false,error=value,offline=value.startsWith("Cannot reach"));if(value.startsWith("TV paired"))loadHome()}
 private fun feedPath(game:Game)=if(game.fixture)"/tv/feeds" else "/tv/sports/feeds"
 private fun feedPayload(game:Game)=obj(if(game.fixture)"fixture_id" else "event_id",game.id)
 private fun applyFeedSnapshot(game:Game,result:JSONObject):Boolean {
  if(state.selected?.id!=game.id)return false
  val running=result.optJSONObject("verification")?.optBoolean("running")?:false
  state=state.copy(feeds=result.objects("feeds").map {mapper.feed(it)},loading=false,verifying=running)
  return running
 }
 private fun pollVerification(game:Game,attempt:Int=0){
  if(attempt>=VERIFY_POLL_LIMIT){if(state.selected?.id==game.id)state=state.copy(verifying=false,verifyTimedOut=true);return}
  ui.postDelayed({
   if(state.selected?.id!=game.id)return@postDelayed
   request(feedPath(game),feedPayload(game)){result->if(applyFeedSnapshot(game,result))pollVerification(game,attempt+1)}
  },1500)
 }
 private fun startVerification(game:Game){
  request("/tv/verify",feedPayload(game)){result->
   if(state.selected?.id!=game.id)return@request
   val status=result.optString("status")
   val running=status=="started"||status=="already_running"
   state=state.copy(verifying=running)
   if(running)pollVerification(game)
  }
 }
 private fun openGame(game:Game) {
  startupChecks.cancel()
  state=state.copy(selected=game,feeds=emptyList(),verifying=false,verifyTimedOut=false,error="")
  // Sample feeds describe the sample football game; an LVBP sample game carries its own evidence instead.
  if(BuildConfig.DESIGN_MODE){DesignFixtures.feeds(mapper)?.let{state=state.copy(feeds=if(game.league==LVBP)emptyList() else it);return}}
  if(token.isEmpty()){state=state.copy(error="Pair this TV in Settings > Connection to see available feeds.");return}
  state=state.copy(loading=true)
  request(feedPath(game),feedPayload(game)) { result ->
   if(state.selected?.id==game.id){applyFeedSnapshot(game,result);startVerification(game)}
  }
 }
 // Lands on the same Details/"Where to watch" screen as openGame() — an empty or failed feed
 // fetch leaves the user on that exact picker, never a dead end. The only difference: once feeds
 // arrive, the top-ranked one (the API already returns feeds sorted best-first) is played
 // immediately, so a normal tap never sees the list at all.
 private fun watchNow(game:Game) {
  startupChecks.cancel()
  state=state.copy(selected=game,feeds=emptyList(),verifying=false,verifyTimedOut=false,error="")
  if(BuildConfig.DESIGN_MODE){openGame(game);return}
  if(token.isEmpty()){state=state.copy(error="Pair this TV in Settings > Connection to see available feeds.");return}
  state=state.copy(loading=true)
  request(if(game.fixture)"/tv/feeds" else "/tv/sports/feeds",obj(if(game.fixture)"fixture_id" else "event_id",game.id)) { result ->
   if(state.selected?.id!=game.id)return@request
   val feeds=result.objects("feeds").map { mapper.feed(it) }
   state=state.copy(feeds=feeds,loading=false)
   feeds.firstOrNull()?.let { play(it) }
  }
 }
 /** Saved-game list, shared by the Recordings tab and team hubs. At most one request is in flight; [force] (refresh buttons, home reload) bypasses the short freshness window. An unpaired TV has no list rather than an empty one. */
 private var recordingsInFlight=false
 private var recordingsLoadedAt=0L
 private fun loadRecordings(force:Boolean=true){
  if(BuildConfig.DESIGN_MODE){DesignFixtures.recordings()?.let{state=state.copy(recordings=it.organized())};return}
  if(token.isEmpty()){state=state.copy(recordings=null);return}
  if(recordingsInFlight)return
  if(!force&&state.recordings!=null&&System.currentTimeMillis()-recordingsLoadedAt<RECORDINGS_FRESH_MS)return
  recordingsInFlight=true
  request("/tv/recordings",null,Runnable{recordingsInFlight=false}){r->recordingsInFlight=false;recordingsLoadedAt=System.currentTimeMillis();state=state.copy(recordings=parseRecordings(r))}
 }
 /** Saved video plays from the backend's own disk: no provider stream is opened, so it takes no budget slot. */
 private fun playRecording(r:Recording){
  playerLauncher.launch(Intent(this,RecordingPlayerActivity::class.java).putExtra("recording_id",r.id).putExtra("title",r.title.ifBlank{r.team}))
 }
 /** Each open or retry gets its own request id; an answer for another team or an older request is dropped by applyHubResponse/applyHubFailure, and /tv/team failures are shown by the hub itself (see isPageScopedFailure). */
 private var hubRequest=0
 private fun openTeamHub(teamId:String) {
  val id=++hubRequest
  state=state.copy(hub=beginHubLoad(state.hub,teamId,id))
  if(BuildConfig.DESIGN_MODE){
   val fixture=DesignFixtures.teamHub(teamId)
   if(fixture!=null){
    val failure=fixture.failure;val body=fixture.json
    state=state.copy(hub=if(failure!=null)applyHubFailure(state.hub,teamId,id,failure) else if(body!=null)applyHubResponse(state.hub,teamId,id,body,System.currentTimeMillis()/1000) else state.hub)
    return
   }
  }
  if(token.isEmpty()){state=state.copy(hub=applyHubFailure(state.hub,teamId,id,FailKind.PERMISSION));return}
  requestStatus("/tv/team",teamHubRequest(state.teams,teamId),MainActivity.Failure{status->state=state.copy(hub=applyHubFailure(state.hub,teamId,id,hubFailKind(status)))}) { result ->
   state=state.copy(hub=applyHubResponse(state.hub,teamId,id,result,System.currentTimeMillis()/1000))
  }
 }
 /** What the player may show or offer without asking the server again: the card's own live facts and other live games for the split picker. */
 private fun contextOf(g:Game)=JSONObject().apply{put("event_phase",g.eventPhase.orEmpty());put("sport",g.sport);put("league",g.league);g.raw.optJSONObject("live")?.let{put("live",it)}}
 private fun playerExtrasFor(game:Game)=Bundle().apply {
  putString("context",contextOf(game).toString())
  // Recordings in progress hold their provider in the player's budget too, so it never offers that provider as a second source.
  putString("dvr_holds",JSONObject(activeRecordingHolds(state.recordings)).toString())
  val others=(state.programs+state.upcoming).filter{it.id!=game.id&&it.isGame&&it.phase()==GamePhase.LIVE}.distinctBy{it.id}.take(10)
  putString("candidates",org.json.JSONArray(others.map{obj("id",it.id,"title",it.title,"fixture",it.fixture,"context",contextOf(it))}).toString())
 }
 private fun play(feed:Feed){
  val game=state.selected?:return
  if(BuildConfig.DESIGN_MODE){
   playerLauncher.launch(Intent(this,PlayerActivity::class.java).putExtra("title",game.title))
   return
  }
  val feeds=org.json.JSONArray(state.feeds.map { it.raw })
  playerExtras=playerExtrasFor(game)
  startFeed(if(game.fixture)game.raw else obj("id",game.id,"event_id",game.id),feeds,state.feeds.indexOf(feed))
 }
 private fun setting(action:String){
  if(action=="refresh"){loadHome();return}
  if(action=="language"){
   android.app.AlertDialog.Builder(this).setTitle("Preferred audio language").setItems(arrayOf("English","Spanish","Portuguese","French","Arabic")){_,i->selectedLanguage=arrayOf("en","es","pt","fr","ar")[i];getPreferences(0).edit().putString("language",selectedLanguage).apply();state=state.copy(language=Locale(selectedLanguage).displayLanguage)}.show();return
  }
  if(BuildConfig.DESIGN_MODE){state=state.copy(error="Design preview uses sample data. Use the studio build to pair with a backend.");return}
  if(action=="pair")pairDialog()
  if(action=="forget")request("/tv/unpair",obj()){token="";Backend.forgetPairing(getPreferences(0));state=state.copy(paired=false,programs=emptyList(),feeds=emptyList(),recordings=null,prefs=PrefsUi.Idle,catalog=null)}
 }

 // ---- Settings > Preferences: the profile's allowed preferences, shared with the web setup page through one backend revision ----
 /** One call to the paired-TV settings endpoint. Reports (HTTP status, body); status 0 means unreachable. Dropped if the backend was switched meanwhile. */
 private fun settingsCall(payload:JSONObject?,path:String="/v1/tv/settings",done:(Int,JSONObject?)->Unit){
  val prefs=getPreferences(0);val base=Backend.base(prefs);val auth=Backend.token(prefs)
  if(auth.isEmpty()){done(401,null);return}
  io.execute {
   var status=0;var body:JSONObject?=null
   try {
    val b=Request.Builder().url(base+path).header("Authorization","Bearer $auth")
    if(payload!=null)b.post(payload.toString().toRequestBody("application/json".toMediaType()))
    http.newCall(b.build()).execute().use { r->status=r.code;body=try{JSONObject(r.body!!.string())}catch(e:Exception){null} }
   } catch(e:Exception) { status=0 }
   ui.post { if(Backend.base(getPreferences(0))==base)done(status,body) }
  }
 }
 private fun applyServerLanguage(data:TvSettingsData){
  val code=data.prefs.optString("language","").takeIf{it in PREF_LANGUAGES}?:return
  selectedLanguage=code;getPreferences(0).edit().putString("language",code).apply()
  state=state.copy(language=Locale(code).displayLanguage)
 }
 /** The selectable favorites/competitions are the backend's; a failure leaves the pickers on the installed favorites rather than blocking Preferences. */
 private fun loadCatalog(){
  settingsCall(null,"/v1/tv/catalog"){status,body->if(status==200&&body!=null)parseCatalog(body)?.let{state=state.copy(catalog=it)}}
 }
 private fun loadPrefs(){
  if(BuildConfig.DESIGN_MODE){
   state=state.copy(prefs=PrefsUi.Ready(TvSettingsData(7,"Household (sample)",JSONObject().put("favorites",org.json.JSONArray(listOf("team.soccer.men.la-liga.fc-barcelona","team.football.men.ncaa.tennessee-volunteers"))).put("leagues",org.json.JSONArray(listOf("league.football.nfl"))))))
   return
  }
  state=state.copy(prefs=PrefsUi.Loading)
  loadCatalog()
  settingsCall(null){status,body->
   val outcome=prefsOutcome(status,body)
   if(outcome is PrefsUi.Ready)applyServerLanguage(outcome.data)
   val ready=if(outcome is PrefsUi.Conflict)outcome.let{PrefsUi.Ready(it.current)} else outcome
   state=state.copy(prefs=ready,legacyFavoriteSeed=(ready as? PrefsUi.Ready)?.let{legacySeedFor(it.data)}.orEmpty())
   offerStartupChecks()
  }
 }
 /** Sends only the changed preferences at the revision they were edited from; a 409 becomes an explicit choice, never a silent overwrite. */
 private fun savePrefs(changes:JSONObject,overwrite:Boolean=false){
  val current=when(val ui=state.prefs){is PrefsUi.Ready->ui.data;is PrefsUi.Conflict->if(overwrite)ui.current else return;else->return}
  if(BuildConfig.DESIGN_MODE){state=state.copy(prefs=PrefsUi.Ready(current,notice="Design preview: nothing is saved."));return}
  state=state.copy(prefs=PrefsUi.Ready(current,saving=true))
  settingsCall(saveBody(current,changes)){status,body->
   val outcome=prefsOutcome(status,body,changes)
   state=state.copy(prefs=when{
    outcome is PrefsUi.Ready->{applyServerLanguage(outcome.data);legacySeedFor(outcome.data);outcome.copy(notice="Saved. The web setup page now shows the same preferences.")}
    // The edit is kept on screen when the backend refused a value or could not be reached.
    outcome is PrefsUi.Failed&&(status==422||status==0)->PrefsUi.Ready(current,notice=outcome.message)
    else->outcome},legacyFavoriteSeed=if(outcome is PrefsUi.Ready)emptyList() else state.legacyFavoriteSeed)
  }
 }
 private fun legacySeedFor(data:TvSettingsData)=FavoritesMigration.seedFor(SharedPrefStore(getPreferences(0)),data.prefs,state.teams.map{it.id})
 private fun useLatestPrefs(){val ui=state.prefs as? PrefsUi.Conflict?:return;state=state.copy(prefs=PrefsUi.Ready(ui.current))}

 /** Settings > Connection: confirm the shown administration address really serves the setup page (plain GET, no credential sent). A stale answer for a backend switched meanwhile is dropped. */
 private fun checkAdmin(){
  if(BuildConfig.DESIGN_MODE)return
  val url=BackendPolicy.adminUrl(Backend.base(getPreferences(0)))
  state=state.copy(adminCheck=AdminCheck.CHECKING)
  io.execute {
   val status=try{http.newCall(Request.Builder().url(url).build()).execute().use{it.code}}catch(e:Exception){0}
   ui.post { if(state.adminUrl==url)state=state.copy(adminCheck=BackendPolicy.adminCheckFor(status)) }
  }
 }
 // ---- First run (builds without a built-in backend): address -> connection test -> pairing -> success -> optional preferences. ----
 private var onboardingChecked=false
 private var startPage by mutableStateOf("Home")
 private fun beginOnboarding(){
  val ob=OnboardingLogic.initial(Backend.base(getPreferences(0)))
  state=state.copy(onboarding=ob,loading=false,error="")
  if(ob.step==OnboardingStep.TESTING)onboardTest(ob.backend)
 }
 private fun onboardTest(url:String){
  state=state.copy(onboarding=OnboardingLogic.addressAccepted(url))
  probeBackend(url){ok->
   val current=state.onboarding?:return@probeBackend
   // Dropped when the person went back or chose another address while the test ran.
   if(current.backend!=url||current.step!=OnboardingStep.TESTING)return@probeBackend
   if(ok){
    BackendPolicy.switchTo(SharedPrefStore(getPreferences(0)),BuildConfig.SERVER_URL,url)
    state=state.copy(onboarding=OnboardingLogic.testPassed(current),backendUrl=url,adminUrl=BackendPolicy.adminUrl(url))
   } else state=state.copy(onboarding=OnboardingLogic.testFailed(current))
  }
 }
 private fun onboardAddressDialog(){
  val input=android.widget.EditText(this).apply{setText(state.onboarding?.backend.orEmpty());hint="http://192.168.1.20:8099";setSingleLine();inputType=android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI;setSelectAllOnFocus(true)}
  android.app.AlertDialog.Builder(this).setTitle("Backend address").setMessage("The address of your SG Sports backend, for example http://192.168.1.20:8099 or https://sports.example.com.").setView(input)
   .setPositiveButton("Test connection"){_,_->onboardAddress(input.text.toString())}.setNegativeButton("Cancel",null).show()
 }
 private fun onboardAddress(text:String){
  val ob=state.onboarding?:return
  when(val r=BackendPolicy.normalize(text)){
   is AddressResult.Rejected->state=state.copy(onboarding=OnboardingLogic.addressRejected(ob,r.reason))
   is AddressResult.Ok->onboardTest(r.url)
  }
 }
 private fun onboardCodeDialog(){
  val input=android.widget.EditText(this).apply{hint="6-digit code";setSingleLine();inputType=android.text.InputType.TYPE_CLASS_NUMBER}
  android.app.AlertDialog.Builder(this).setTitle("Pairing code").setMessage("Enter the six digits shown in the backend console.").setView(input)
   .setPositiveButton("Pair"){_,_->onboardPair(OnboardingLogic.cleanCode(input.text.toString()))}.setNegativeButton("Cancel",null).show()
 }
 private fun onboardPair(code:String){
  val ob=state.onboarding?:return
  if(!OnboardingLogic.codeLooksComplete(code)){state=state.copy(onboarding=ob.copy(error="Enter all six digits of the code."));return}
  val base=ob.backend
  state=state.copy(onboarding=OnboardingLogic.pairing(ob))
  io.execute {
   var status=0;var issued=""
   try {
    http.newCall(Request.Builder().url(base+"/tv/pair").post(obj("code",code).toString().toRequestBody("application/json".toMediaType())).build()).execute().use { r->
     status=r.code;if(r.isSuccessful)issued=try{JSONObject(r.body!!.string()).getString("token")}catch(e:Exception){""}
    }
   } catch(e:Exception) { status=0 }
   ui.post {
    val current=state.onboarding?:return@post
    if(current.backend!=base)return@post
    if(status==200&&issued.isNotEmpty()){
     token=issued;Backend.savePairing(getPreferences(0),issued)
     getPreferences(0).edit().putBoolean(OnboardingLogic.KEY_DONE,true).apply()
     state=state.copy(onboarding=current.copy(step=OnboardingStep.DONE,busy=false,error=""),paired=true)
    } else state=state.copy(onboarding=current.copy(busy=false,error=OnboardingLogic.pairMessage(if(status==200)500 else status)))
   }
  }
 }
 private fun onboardBack(){
  val ob=state.onboarding?:return
  OnboardingLogic.back(ob)?.let{state=state.copy(onboarding=it)}
 }
 private fun finishOnboarding(toPreferences:Boolean){
  getPreferences(0).edit().putBoolean(OnboardingLogic.KEY_DONE,true).apply()
  startPage=if(toPreferences)"Preferences" else "Home"
  state=state.copy(onboarding=null,loading=true)
  refresh(showLoading=true)
 }
 // ---- Settings > Connection: runtime backend selection. Changing it is a deliberate re-pair; the old credential never goes to the new address. ----
 private var backendPrompted=false
 private fun changeBackend(){
  if(BuildConfig.DESIGN_MODE){state=state.copy(error="Design preview uses sample data. Use the studio build to pair with a backend.");return}
  val input=android.widget.EditText(this).apply{setText(Backend.base(getPreferences(0)));setSingleLine();inputType=android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI;setSelectAllOnFocus(true)}
  android.app.AlertDialog.Builder(this).setTitle("Backend address").setMessage("The address of your SG Sports backend, for example https://sports.example.com or http://192.168.1.20:8099.").setView(input)
   .setPositiveButton("Continue"){_,_->confirmBackendChange(input.text.toString())}
   .setNegativeButton("Cancel",null)
   .apply{if(BuildConfig.SERVER_URL.isNotEmpty())setNeutralButton("Built-in default"){_,_->confirmBackendChange(BuildConfig.SERVER_URL)}}
   .show()
 }
 private fun confirmBackendChange(text:String){
  when(val checked=BackendPolicy.normalize(text)){
   is AddressResult.Rejected->message(checked.reason)
   is AddressResult.Ok->{
    val current=Backend.base(getPreferences(0))
    if(checked.url==current){message("That backend is already selected.");return}
    probeBackend(checked.url){ok->
     if(!ok){message("No SG Sports backend answered at ${checked.url}. Check the address, port and that the backend is running.");return@probeBackend}
     if(current.isEmpty()){applyBackendChange(checked.url);return@probeBackend}
    android.app.AlertDialog.Builder(this).setTitle("Switch backend and pair again?")
     .setMessage("This TV will sign out of $current and connect to ${checked.url}.\n\nThe current pairing is not sent to the new backend. You will need a new pairing code from the new backend, and preferences come from its profile.\n\nSwitch only to a backend you run or trust.")
     .setPositiveButton("Switch and pair again"){_,_->applyBackendChange(checked.url)}.setNegativeButton("Keep current backend",null).show()
    }
   }
  }
 }
 private fun applyBackendChange(url:String){
  val prefs=getPreferences(0);val oldBase=Backend.base(prefs);val oldToken=Backend.token(prefs)
  when(val result=BackendPolicy.switchTo(SharedPrefStore(prefs),BuildConfig.SERVER_URL,url)){
   is SwitchResult.Rejected->message(result.reason)
   is SwitchResult.Unchanged->message("That backend is already selected.")
   is SwitchResult.Switched->{
    token=""
    state=state.copy(paired=false,upcoming=emptyList(),programs=emptyList(),later=emptyList(),feeds=emptyList(),selected=null,hub=TeamHubState(),recordings=null,prefs=PrefsUi.Idle,catalog=null,error="",
     backendUrl=result.url,adminUrl=BackendPolicy.adminUrl(result.url),adminCheck=AdminCheck.UNKNOWN)
    if(oldToken.isNotEmpty())releaseOldPairing(oldBase,oldToken)
    pairDialog()
   }
  }
 }
 /** Confirms the address serves an SG Sports backend (GET /health, no credential is sent) before anything is switched. */
 private fun probeBackend(url:String,done:(Boolean)->Unit){
  io.execute {
   val ok=try{ http.newCall(Request.Builder().url(url+"/health").build()).execute().use{r->r.isSuccessful&&JSONObject(r.body?.string().orEmpty()).optString("service")=="sgsports"} }catch(e:Exception){false}
   ui.post{done(ok)}
  }
 }
 /** Best effort: tell the backend that issued the old pairing to drop it. Only that backend ever sees that token. */
 private fun releaseOldPairing(base:String,oldToken:String){
  io.execute { try{ http.newCall(Request.Builder().url(base+"/tv/unpair").header("Authorization","Bearer $oldToken").post("{}".toRequestBody("application/json".toMediaType())).build()).execute().close() }catch(e:Exception){} }
 }
}
