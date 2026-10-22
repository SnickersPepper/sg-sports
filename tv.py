"""Paired TV devices and serialized MPEG-TS relay. Credentials stay on the SG Sports backend."""
import guide
import livescores
import lvbp
import lvbp_broadcast
import truth
import verification
import dvr
import jellyfin
import jfdvr
import lease
import delivery
import hashlib
import ipaddress
import json
import os
import queue
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

A = None
GUARD = threading.RLock()
PAIR = {}
ACTIVE = set()
VERIFYING = set()

def configure(api):
    global A
    A = api
    verification.configure_binding(source_binder)

def source_binder():
    """Evidence is bound to the managed route a feed is delivered through (see verification.SOURCE_BINDING)."""
    store=getattr(A,'settings_store',None)
    return delivery.binder(store()) if store else (lambda feed:None)

def _provider_busy(pid):
    """Playback sessions, relays and verification samples on [pid]; the caller holds GUARD."""
    sessions=A.read_cache('tv-sessions') or {}
    return pid in VERIFYING or any(s.get('provider_id')==pid and (s.get('expires',0)>now() or sid in ACTIVE) for sid,s in sessions.items())

LEASES={}

def leases():
    """The one persistent provider-lease authority (SQLite under the data dir) for every managed entry point."""
    path=str(A.DATA/'provider-leases.sqlite')
    with GUARD:
        if path not in LEASES:LEASES[path]=delivery.DeliveryAuthority(path,resolver())
        return LEASES[path]

class LiveResolver(delivery.Resolver):
    """Delivery decisions read the live settings authority, so a committed route switch applies without a restart."""
    def __init__(self):super().__init__(None,lambda *a,**k:A.DISPATCHARR_TRANSPORT(*a,**k))
    @property
    def store(self):return A.settings_store()
    @store.setter
    def store(self,value):pass

def resolver():return LiveResolver()

def mediated(pid):
    """True when a Dispatcharr route owns this account: no direct provider contact (not even an account query) is allowed."""
    return resolver().mediated(pid)

def refusal_text():
    code=getattr(leases().last_refusal,'code',None)
    if not code or code=='provider_in_use':return 'This provider is in use by another SG Sports client. Try again shortly.'
    return {'channel_unmapped':'This channel is not mapped through the media relay.',
            'redirect_profile':'The media relay is set to redirect to the provider, which SG Sports will not use.'}.get(code,'The media relay is unavailable for this channel.')

class LeaseBudget:
    """dvr.Budget interface (legacy recorder) backed by the persistent lease authority."""
    def __init__(self):self.holds={}
    def acquire(self,provider_id,owner):
        with GUARD:
            if _provider_busy(provider_id):return False   # defense in depth: in-process sessions/samples
            granted=leases().acquire(provider_id,owner,'dvr',ttl=60)
        if not granted:return False
        self.holds[(provider_id,owner)]=granted
        def keepalive():
            # A crashed process stops renewing, so the lease expires on its own (fail closed).
            while self.holds.get((provider_id,owner)) is granted:
                time.sleep(15)
                if self.holds.get((provider_id,owner)) is granted and not leases().renew(granted,60):break
        threading.Thread(target=keepalive,daemon=True,name='dvr-lease').start()
        return True
    def release(self,provider_id,owner):
        leases().release(self.holds.pop((provider_id,owner),None))
    def held(self,provider_id):
        holder=leases().holder(provider_id)
        return 1 if holder and holder['kind']=='dvr' else 0

# Legacy recorder holds draw on the same persistent one-stream-per-provider authority.
DVR_BUDGET=LeaseBudget()
DVR_STOPS={}
DVR_STORE=None
JF=None

def dvr_engine():
    """'jellyfin' (default automatic DVR when enabled), 'legacy' (SG_DVR=1 only when Jellyfin is off) or 'off'."""
    if os.environ.get('SG_JELLYFIN_ENABLED')=='1':return 'jellyfin'
    return 'legacy' if os.environ.get('SG_DVR')=='1' else 'off'

def dvr_enabled():
    return dvr_engine()!='off'

def jf_dvr():
    """The Jellyfin DVR adapter for this process (config is read once per data dir)."""
    global JF
    with GUARD:
        if JF is None or JF[0]!=str(A.DATA):
            config=jellyfin.load_config(secrets_dir=str(A.SECRETS))
            JF=(str(A.DATA),config,jfdvr.JellyfinDvr(jellyfin.Client(config),jfdvr.Ledger(A.DATA/'dvr-ledger.sqlite'),leases(),
                retention_delete=os.environ.get('SG_JELLYFIN_RETENTION_DELETE')=='1'))
        return JF[2]

def dvr_store():
    global DVR_STORE
    if DVR_STORE is None:
        DVR_STORE=dvr.Store(A.DATA/'dvr');DVR_STORE.recover()
    return DVR_STORE

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def now(): return int(time.time())

TEAM_NAMES = {'fc-barcelona': 'Barcelona'}

def attach_live_events(events):
    result = []
    for event in events:
        live = livescores.for_event(A, event)
        # The provider's own guide slot already ended (see guide.GUIDE_GRACE_SECONDS) -- keep
        # the card up only while ESPN's own live-score status says the game is still going;
        # once ESPN confirms it's over, or never had anything to say about it at all, it drops.
        if event.get('guide_slot_ended') and not (live and live.get('verified_live')):
            continue
        result.append({**event, 'live': live} if live else event)

    # Providers can publish the same game in overlapping, badly timed guide blocks. The
    # guide-level merge intentionally keeps blocks hours apart because the same teams can play
    # a real doubleheader. Once ESPN has matched both blocks to one event id, however, identity
    # is unambiguous: make one card and retain every distinct channel from both listings.
    merged = []
    by_espn = {}
    for event in result:
        live = event.get('live') or {}
        espn_id = str(live.get('espn_id') or '')
        key = (event.get('sport'), event.get('league'), espn_id) if espn_id else None
        if not key or key not in by_espn:
            item = {**event, 'merged_event_ids': [event.get('id')]}
            merged.append(item)
            if key:
                by_espn[key] = item
            continue
        group = by_espn[key]
        group['merged_event_ids'].append(event.get('id'))
        existing = {(f.get('provider_id'), f.get('stream_id')) for f in group.get('feeds', [])}
        for feed in event.get('feeds', []):
            identity = (feed.get('provider_id'), feed.get('stream_id'))
            if identity not in existing:
                group.setdefault('feeds', []).append(feed)
                existing.add(identity)
        # Use the guide block closest to ESPN's actual first pitch for the card's displayed
        # time and id. The merged ids keep every original block resolvable for playback.
        actual_start = live.get('event_start')
        if actual_start and abs(event.get('start', 0) - actual_start) < abs(group.get('start', 0) - actual_start):
            preserved_feeds = group.get('feeds', [])
            preserved_ids = group['merged_event_ids']
            replacement = {**event, 'feeds': preserved_feeds, 'merged_event_ids': preserved_ids}
            group.clear(); group.update(replacement)
    # Phase, ESPN coverage and the ESPN-vs-provider channel comparison are computed once here,
    # after merging, so they describe the final feed set. A replay is never left looking live.
    at = now()
    result = [truth.annotate(event, event.get('live'), at) for event in merged]
    # Verified-live games first, then notable non-favorite games (ranked matchups,
    # playoffs, Champions League knockout rounds); otherwise keep the guide's own order.
    result.sort(key=lambda e: (not (e.get('live') or {}).get('verified_live'),
                                not (e.get('live') or {}).get('notable')))
    return result

def lvbp_events():
    """Official LVBP schedule events through the same truth and verification seams as guide events.

    Returns (events, schedule_source). The guide only contributes matched listings; a guide entry
    with no schedule event is never turned into a game."""
    at = now()
    source = lvbp.freshness(A, at)
    stored = lvbp.events(A, at)
    if not stored:
        return [], source
    records, _ = lvbp_broadcast.load_overrides()
    overrides, _ = lvbp_broadcast.validate_overrides(records, {e['id'] for e in stored}, at)
    cache = A.read_cache('sports-guide') or {}
    programs = [p for p in cache.get('programs', []) if p.get('league') == lvbp.LEAGUE] if guide.cache_fresh(cache, at) else []
    matches = lvbp_broadcast.match_programs(stored, programs)
    result = []
    for stored_event in stored:
        item, _ = lvbp_broadcast.to_event(A, stored_event, at, source, matches.get(stored_event['id'], ()), cache.get('updated_at'), overrides)
        item = verification.annotate(A, truth.annotate(item, None, at))
        result.append(lvbp_broadcast.after_verification(item))
    return result, source

def find_attached_event(event_id):
    """Resolve a current/later card after ESPN-id deduplication, including merged feeds."""
    candidates = (guide.current(A).get('events') or []) + guide.later_today(A)
    for event in attach_live_events(candidates):
        if event_id == event.get('id') or event_id in event.get('merged_event_ids', []):
            return verification.annotate(A,event)
    for event in lvbp_events()[0]:
        if event_id == event['id']:
            return event
    event = guide.find(A, event_id)
    if not event:
        return None
    return verification.annotate(A,truth.annotate(event, livescores.for_event(A, event), now()))

def attach_live_fixtures(fixtures):
    result = []
    for fixture in fixtures:
        live = livescores.for_fixture(A, fixture, TEAM_NAMES.get(fixture.get('team_id'), fixture.get('team_id', '')))
        item = {**fixture, 'live': live} if live else fixture
        kickoff = fixture.get('kickoff') or 0
        phase = truth.event_phase({'is_game': True, 'start': kickoff}, live, now())
        result.append({**item, 'event_phase': phase})
    return result

def sample_feed(event,feed):
    """Capture one bounded sample without exposing credentials to media tools."""
    pid=feed.get('provider_id')
    with GUARD:
        sessions=A.read_cache('tv-sessions') or {}
        busy=any(s.get('provider_id')==pid and (s.get('expires',0)>now() or sid in ACTIVE)
                 for sid,s in sessions.items())
        if busy or pid in VERIFYING or DVR_BUDGET.held(pid):raise verification.Busy()
        grant=leases().acquire(pid,'verify:'+secrets.token_hex(8),'verification',ttl=60,stream_id=str(feed.get('stream_id')))
        if not grant:raise verification.Busy()
        VERIFYING.add(pid)
    try:
        adapter=None
        if not mediated(pid):
            adapter=A.Xtream(A.PROVIDERS[pid]);account=adapter.account()
            if account.get('status')!='Active' or int(account.get('active_cons') or 0)>0:
                raise verification.Busy()
        sample={'provider_id':pid,'stream_id':str(feed.get('stream_id')),'host':adapter.host if adapter else None,'started':now()}
        opened=time.monotonic();first=None;chunks=[];size=0
        upstream=open_media(sample,lease=grant)
        with upstream:
            deadline=time.monotonic()+6
            while time.monotonic()<deadline and size<verification.MAX_SAMPLE_BYTES:
                chunk=upstream.read(min(32768,verification.MAX_SAMPLE_BYTES-size))
                if not chunk:break
                if first is None:first=time.monotonic()
                chunks.append(chunk);size+=len(chunk)
        if not chunks:raise ValueError('empty_sample')
        return b''.join(chunks),int(((first or time.monotonic())-opened)*1000)
    finally:
        with GUARD:VERIFYING.discard(pid)
        leases().release(grant)

def new_pair():
    with GUARD:
        code = str(secrets.randbelow(1000000)).zfill(6)
        PAIR.clear(); PAIR.update(code=digest(code), expires=now()+300, attempts=0)
        return {'code': code, 'expires_in': 300}

def pair(code):
    with GUARD:
        if PAIR.get('expires',0) < now() or PAIR.get('attempts',0) >= 5:
            raise ValueError('Create a new pairing code on the SG Sports web screen.')
        PAIR['attempts'] = PAIR.get('attempts',0)+1
        if not secrets.compare_digest(digest(str(code)), PAIR.get('code','')):
            raise ValueError('Pairing code incorrect.')
        token=secrets.token_urlsafe(40)
        devices=A.read_cache('tv-devices') or {}
        devices={k:v for k,v in devices.items() if v['expires']>now()}
        devices[digest(token)]={'expires':now()+30*86400}
        A.save_cache('tv-devices',devices);PAIR.clear()
        return {'token':token}

def device(headers):
    token=headers.get('Authorization','').removeprefix('Bearer ')
    key=digest(token)
    if (A.read_cache('tv-devices') or {}).get(key,{}).get('expires',0)<now():
        raise PermissionError()
    return key

def unpair(owner):
    """Drop one device's pairing and stop its sessions. Used by the TV itself and by administrator revocation."""
    with GUARD:
        devices=A.read_cache('tv-devices') or {};devices.pop(owner,None);A.save_cache('tv-devices',devices)
        sessions=A.read_cache('tv-sessions') or {}
        for sid in list(sessions):
            if sessions[sid]['owner']==owner:
                if sid in ACTIVE:sessions[sid]['stop']=True
                else:sessions.pop(sid);leases().cancel_owner('playback:'+sid)
        A.save_cache('tv-sessions',sessions)

def begin(owner,payload):
    with GUARD:
        event=find_attached_event(payload.get('event_id')) if payload.get('event_id') else None
        if payload.get('event_id'):
            if not event:raise ValueError('Feed is no longer airing in the current guide.')
            game={'feeds':event['feeds'],'fixture':{'id':event['id'],'kickoff':event['start'],'title':event['title']}}
        else:
            fixture_id=payload.get('fixture_id','')
            game=A.resolve_next_game(fixture_id) if fixture_id.startswith('next-') else A.resolve(fixture_id=fixture_id)
        kickoff=(game.get('fixture') or {}).get('kickoff',0)
        if kickoff and now()<kickoff-guide.PREGAME_TUNE_SECONDS:
            raise ValueError('This broadcast is available 30 minutes before game time.')
        feed=next((f for f in game.get('feeds',[]) if f['provider_id']==payload.get('provider_id') and f['stream_id']==payload.get('stream_id')),None)
        if not feed: raise ValueError('Feed is no longer in fresh listings. Refresh and choose again.')
        pid=feed['provider_id']; sessions=A.read_cache('tv-sessions') or {}
        if pid in VERIFYING:
            raise ValueError('Provider verification is finishing. Try again in a few seconds.')
        if DVR_BUDGET.held(pid):
            raise ValueError('This provider is recording a game. Watch it from Recordings or stop the recording first.')
        via_relay=mediated(pid)   # the same channel may fan out through the relay; any other session still contends for the shared hold
        if any(s['provider_id']==pid and (s['expires']>now() or sid in ACTIVE) and not (via_relay and str(s['stream_id'])==str(feed['stream_id'])) for sid,s in sessions.items()):
            raise ValueError('This provider already has an SG Sports playback session. Stop it first.')
        adapter=None
        if not via_relay:
            adapter=A.Xtream(A.PROVIDERS[pid]);account=adapter.account()
            if account.get('status')!='Active' or int(account.get('active_cons') or 0)>0:
                raise ValueError('Provider is inactive or its connection is already in use. Stop other players first.')
            if 'ts' not in (account.get('allowed_output_formats') or []):
                raise ValueError('This MVP requires an MPEG-TS stream from the provider.')
        sid=secrets.token_urlsafe(32)
        for k,v in sessions.items():
            if v['expires']<=now() and k not in ACTIVE:leases().cancel_owner('playback:'+k)   # lapsed session frees its lease
        if not leases().acquire(pid,'playback:'+sid,'playback',ttl=90,stream_id=str(feed['stream_id'])):
            raise ValueError(refusal_text())
        sessions={k:v for k,v in sessions.items() if v['expires']>now() or k in ACTIVE}
        sessions[sid]={'owner':owner,'provider_id':pid,'stream_id':feed['stream_id'],
            'fixture':game['fixture'],'expires':now()+90,'stop':False,'host':adapter.host if adapter else None,'started':now()}
        A.save_cache('tv-sessions',sessions)
        return {'session':sid,'stream_path':'/tv/stream/'+sid,'heartbeat_seconds':20}

def session(owner,sid):
    s=(A.read_cache('tv-sessions') or {}).get(sid)
    if not s or s['owner']!=owner or s['expires']<=now() or s['stop']:
        raise ValueError('Playback session ended. Select a feed again.')
    return s

def update(owner,sid,stop=False):
    with GUARD:
        sessions=A.read_cache('tv-sessions') or {};s=sessions.get(sid)
        if not s or s['owner']!=owner: raise ValueError('Session not found.')
        if stop:
            if sid in ACTIVE: s['stop']=True
            else:
                sessions.pop(sid);leases().cancel_owner('playback:'+sid)
        else:
            session(owner,sid)
            if not leases().acquire(s['provider_id'],'playback:'+sid,'playback',ttl=90,stream_id=str(s['stream_id'])):
                raise ValueError('Playback session ended. Select a feed again.')
            s['expires']=now()+90
        A.save_cache('tv-sessions',sessions)
    return {'ok':True}

def public_url(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid media destination.')
    addresses=socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Media destination must be a public provider endpoint.')

def abandon(lease=None,owner=None):
    """Give back the reservation of a mediated open that failed: a shared hold becomes its cooldown (the relay may have started
    an upstream), so nothing waits out the TTL and nothing else is admitted early."""
    if lease:leases().release(lease)
    if owner:leases().cancel_owner(owner)

def open_media(s,lease=None,owner=None):
    """The one managed media open. A mediated refusal or relay failure releases the caller's reservation (lease and/or owner).
    Direct accounts keep their previous behavior."""
    try:plan=resolver().decide(s['provider_id'],str(s['stream_id']))
    except delivery.Refused as refusal:
        leases().last_refusal.code=refusal.code;abandon(lease,owner);raise ValueError('Provider did not open this feed.') from None
    if plan:   # mediated account: only the relay is contacted, never a provider URL, and there is no fallback
        try:return delivery.open_stream(plan)
        except delivery.Refused as refusal:
            leases().last_refusal.code=refusal.code;abandon(lease,owner);raise ValueError('Provider did not open this feed.') from None
    adapter=A.Xtream(A.PROVIDERS[s['provider_id']])
    c=adapter.credentials
    url=s['host'].rstrip('/')+'/live/'+urllib.parse.quote(c['username'],safe='')+'/'+urllib.parse.quote(c['password'],safe='')+'/'+urllib.parse.quote(s['stream_id'],safe='')+'.ts'
    opener=urllib.request.build_opener(A.NoRedirect)
    for _ in range(4):
        public_url(url)
        try:
            return opener.open(urllib.request.Request(url,headers={'User-Agent':'SGSportsTV/0.1'}),timeout=12)
        except urllib.error.HTTPError as e:
            if e.code not in (301,302,303,307,308): raise ValueError('Provider did not open this feed.') from None
            target=e.headers.get('Location');e.close()
            if not target: raise ValueError('Provider redirect unavailable.')
            url=urllib.parse.urljoin(url,target)
    raise ValueError('Provider redirected too many times.')

def relay(handler,owner,sid):
    with GUARD:
        s=session(owner,sid)
        if sid in ACTIVE: raise ValueError('This playback session is already streaming.')
        ACTIVE.add(sid)
    try:
        try:
            upstream=open_media(s,owner='playback:'+sid)
        except Exception:
            handler.send(502,{'error':'Feed could not be opened. Choose another candidate.'});return
        with upstream:
            handler.send_response(200);handler.send_header('Content-Type','video/mp2t')
            handler.send_header('Cache-Control','no-store');handler.end_headers()
            while now()-s['started']<14400:
                with GUARD:
                    session(owner,sid)
                    if not leases().holds(s['provider_id'],'playback:'+sid):break   # lease lost or expired: fail closed
                chunk=upstream.read(32768)
                if not chunk: break
                handler.wfile.write(chunk)
    except Exception:
        pass  # Never log provider URLs or playback tokens.
    finally:
        with GUARD:
            ACTIVE.discard(sid)
            sessions=A.read_cache('tv-sessions') or {};sessions.pop(sid,None)
            A.save_cache('tv-sessions',sessions)
            leases().cancel_owner('playback:'+sid)
        handler.close_connection=True

def dvr_source(feed):
    """Context-manager factory over one provider stream. Opens only while the DVR holds the slot."""
    import contextlib
    pid=feed['provider_id']
    @contextlib.contextmanager
    def source():
        adapter=None
        if not mediated(pid):
            adapter=A.Xtream(A.PROVIDERS[pid]);account=adapter.account()
            if account.get('status')!='Active' or int(account.get('active_cons') or 0)>0 or 'ts' not in (account.get('allowed_output_formats') or []):
                raise ValueError('provider_unavailable')
        upstream=open_media({'provider_id':pid,'stream_id':str(feed['stream_id']),'host':adapter.host if adapter else None},owner=feed.get('owner'))
        try:
            yield iter(lambda:upstream.read(65536),b'')
        finally:
            upstream.close()
    return source

def dvr_event(espn_id):
    """The current canonical card for a recorded ESPN event, or None when the guide no longer lists it."""
    for event in attach_live_events((guide.current(A).get('events') or [])+guide.later_today(A)):
        if str((event.get('live') or {}).get('espn_id'))==espn_id:return event
    return None

def dvr_truth(bucket_id,espn_id):
    """Re-derive canonical truth for the recorded event; anything unclear is 'gone' (never guessed)."""
    try:
        event=dvr_event(espn_id)
        if not event:return {'phase':'gone'}
        live=event.get('live') or {};start=live.get('event_start') or event.get('start')
        phase=event.get('event_phase')
        if dvr.bucket_for(event)[0]==bucket_id:return {'phase':'live','event_start':start}
        if dvr.bucket_for(event,preroll=True)[0]==bucket_id:return {'phase':'pre','event_start':start}
        if phase in ('postponed','canceled','suspended'):return {'phase':'postponed'}
        if live.get('state')=='post' and not live.get('likely_replay') and event.get('sport') and phase in ('final','live'):return {'phase':'final','event_start':start}
    except Exception:
        pass
    return {'phase':'gone'}

def dvr_verify(bucket_id,espn_id):
    """Content check of a provisional capture from its own bytes: no second provider connection."""
    def verify(path,offset,length):
        event=dvr_event(espn_id)
        if not event or dvr.bucket_for(event)[0]!=bucket_id:return 'unknown'
        with open(path,'rb') as handle:
            handle.seek(offset);sample=handle.read(length)
        record=verification.inspect_sample(A,event,sample,0)
        if record.get('content_verified') is True:return 'confirmed'
        return 'wrong' if record.get('shadow_verdict')=='wrong_content' else 'unknown'
    return verify

def dvr_tick():
    """Start captures for eligible events (SG_DVR=1 only): verified-live ones, and bounded pre-roll captures."""
    engine=dvr_engine()
    if engine=='jellyfin':return jf_tick()
    if engine!='legacy':return []
    store=dvr_store()
    try:
        events=attach_live_events((guide.current(A).get('events') or [])+guide.later_today(A))
        events=[verification.annotate(A,e) for e in events]
    except Exception:
        return []
    started=[]
    for provisional,items in ((False,dvr.plan(events)),(True,dvr.plan_preroll(events,now()))):
        for bucket_id,event,feed in items:
            rid=dvr.recording_id(bucket_id,event)
            with GUARD:
                if rid in DVR_STOPS:continue
                stop=DVR_STOPS[rid]=threading.Event()
            espn_id=str((event.get('live') or {}).get('espn_id'))
            def work(bucket_id=bucket_id,event=event,feed=feed,rid=rid,stop=stop,espn_id=espn_id,provisional=provisional):
                try:
                    dvr.record(store,bucket_id,event,feed,dvr_source(feed),DVR_BUDGET,lambda:dvr_truth(bucket_id,espn_id),stop,
                               provisional=provisional,verify=dvr_verify(bucket_id,espn_id) if provisional else None)
                except Exception:
                    pass
                finally:
                    with GUARD:DVR_STOPS.pop(rid,None)
            threading.Thread(target=work,daemon=True,name='dvr-record').start()
            started.append(rid)
    return started

def jf_events():
    events=attach_live_events((guide.current(A).get('events') or [])+guide.later_today(A))
    return [verification.annotate(A,e) for e in events]

def jf_tick():
    """Jellyfin is the sole automatic DVR when enabled: plan from canonical events, schedule/reconcile timers."""
    try:
        return [jf_dvr().tick(jf_events())]
    except Exception:
        return []   # a failed tick changes nothing; the next tick reconciles idempotently

def jf_verify(bucket_id,espn_id,sample):
    """Content check of relayed bytes: no second provider connection."""
    event=dvr_event(espn_id)
    if not event or dvr.bucket_for(event)[0]!=bucket_id:return 'unknown'
    record=verification.inspect_sample(A,event,sample,0)
    if record.get('content_verified') is True:return 'confirmed'
    return 'wrong' if record.get('shadow_verdict')=='wrong_content' else 'unknown'

def jf_text(handler,status,text,content_type='text/plain; charset=utf-8'):
    body=text.encode();handler.send_response(status);handler.send_header('Content-Type',content_type)
    handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(body)));handler.end_headers();handler.wfile.write(body)

def jf_export(handler,token,name):
    """Playlist/guide that Jellyfin ingests: SG Sports relay URLs only, never provider URLs."""
    dvr_obj=jf_dvr();config=JF[1]
    if not config.enabled or config.problem or not jfdvr.token_ok(config.relay_key,'export',token):
        handler.send(403,{'error':'Forbidden.'});return
    if name=='playlist.m3u':jf_text(handler,200,dvr_obj.export_m3u(config.export_url,config.relay_key),'audio/x-mpegurl')
    elif name=='guide.xml' or (name.startswith('guide-') and name.endswith('.xml')):jf_text(handler,200,dvr_obj.export_xmltv(),'application/xml')
    else:handler.send(404,{'error':'Not found'})

JF_FANS={};JF_FANS_LOCK=threading.Lock()
TS_PACKET=188

class JfFan:
    """One provider stream per recording shared by every Jellyfin connection to its relay URL (Jellyfin probes the URL,
    then opens it again to record). Late joiners start on a TS packet boundary. The lease is held by the fan, not by a connection."""
    GRACE=20
    def __init__(self):
        self.lock=threading.Lock();self.subs={};self.alive=True;self.failed=False;self.idle_since=None
        self.ready=threading.Event();self.sent=0
    def subscribe(self):
        q=queue.Queue(maxsize=512)
        with self.lock:
            if not self.alive:return None
            self.subs[q]=False;self.idle_since=None
        return q
    def unsubscribe(self,q):
        with self.lock:
            self.subs.pop(q,None)
            if not self.subs:self.idle_since=time.monotonic()
    def publish(self,chunk):
        with self.lock:
            start=self.sent;self.sent+=len(chunk)
            for q,aligned in list(self.subs.items()):
                data=chunk
                if not aligned:
                    skip=(-start)%TS_PACKET
                    if skip>=len(chunk):continue
                    data=chunk[skip:];self.subs[q]=True
                try:q.put_nowait(data)
                except queue.Full:
                    self.subs.pop(q,None);self._end(q)
    def idle_for(self):
        with self.lock:
            return time.monotonic()-self.idle_since if not self.subs and self.idle_since else 0
    @staticmethod
    def _end(q):
        try:
            while True:q.get_nowait()
        except queue.Empty:pass
        try:q.put_nowait(None)
        except queue.Full:pass
    def close(self):
        with self.lock:
            self.alive=False
            for q in list(self.subs):self._end(q)
            self.subs.clear()

def jf_fan_run(fan,rid,entry,granted,dvr_obj):
    """Owns the single upstream for one recording and the lease; ends on game final, loss of lease, wrong content or idle."""
    final=False;ring=bytearray();attempts=0;final_since=None;source=None
    try:
        try:
            source=dvr_source({'provider_id':entry['provider_id'],'stream_id':entry['stream_id'],'owner':'dvr:'+rid})()
            chunks=source.__enter__()
        except Exception:
            source=None;fan.failed=True;return
        finally:
            fan.ready.set()
        try:
            renewed=checked=time.monotonic()
            for chunk in chunks:
                fan.publish(chunk)
                if fan.subs:dvr_obj.relay_started(rid)
                ring+=chunk
                if len(ring)>dvr.VERIFY_MAX_BYTES:del ring[:len(ring)-dvr.VERIFY_MAX_BYTES]
                tick=time.monotonic()
                if fan.idle_for()>fan.GRACE:break
                if tick-renewed>=15:
                    if not leases().renew(granted,60):break     # preempted, cancelled or expired: stop at once
                    renewed=tick
                if tick-checked>=30:
                    checked=tick
                    phase=dvr_truth(entry['bucket'],entry['espn_id']).get('phase')
                    if phase in ('gone','postponed'):break
                    if phase=='final':
                        final_since=final_since or tick
                        if tick-final_since>=dvr.POST_ROLL_SECONDS:final=True;break
                    current=dvr_obj.ledger.get(rid) or {}
                    if phase=='live' and current.get('content')=='unverified' and attempts<dvr.VERIFY_ATTEMPTS and len(ring)>=dvr.VERIFY_MIN_BYTES:
                        attempts+=1
                        dvr_obj.relay_content(rid,jf_verify(entry['bucket'],entry['espn_id'],bytes(ring)))
                    if (dvr_obj.ledger.get(rid) or {}).get('content')=='wrong':break
                    if (dvr_obj.ledger.get(rid) or {}).get('relay_done'):break
        finally:
            if source:source.__exit__(None,None,None)
    except Exception:
        pass
    finally:
        fan.close()
        leases().release(granted)
        dvr_obj.relay_ended(rid,final)
        with JF_FANS_LOCK:
            if JF_FANS.get(rid) is fan:del JF_FANS[rid]

def jf_relay(handler,rid,token):
    """Managed Jellyfin ingest: one provider stream per recording while it holds the fenced lease."""
    dvr_obj=jf_dvr();config=JF[1]
    if not config.enabled or config.problem:handler.send(403,{'error':'Forbidden.'});return
    with JF_FANS_LOCK:
        fan=JF_FANS.get(rid)
        if fan and fan.alive:
            if not jfdvr.token_ok(config.relay_key,'relay:'+rid,token):handler.send(403,{'error':'Relay unavailable.'});return
            q=fan.subscribe()
        else:
            entry,granted,error=dvr_obj.relay_authorize(rid,token,config.relay_key)
            if error:
                handler.send(403 if error=='forbidden' else 503,{'error':'Relay unavailable.'});return
            fan=JfFan();JF_FANS[rid]=fan
            q=fan.subscribe()
            fan.thread=threading.Thread(target=jf_fan_run,args=(fan,rid,entry,granted,dvr_obj),daemon=True);fan.thread.start()
    jf_relay_stream(handler,fan,q)

def jf_relay_stream(handler,fan,q):
    if q is None or not fan.ready.wait(20) or fan.failed:
        if q is not None:fan.unsubscribe(q)
        handler.send(502,{'error':'Relay unavailable.'});return
    try:
        handler.send_response(200);handler.send_header('Content-Type','video/mp2t')
        handler.send_header('Cache-Control','no-store');handler.end_headers()
        while True:
            try:data=q.get(timeout=45)
            except queue.Empty:break
            if data is None:break
            handler.wfile.write(data)
    except OSError:
        pass
    finally:
        fan.unsubscribe(q)
        handler.close_connection=True

def jf_media(handler,rid):
    """Completed Jellyfin recording, proxied by the backend so the API key never reaches the TV. Range is passed through."""
    dvr_obj=jf_dvr();config=JF[1];entry=dvr_obj.ledger.get(rid)
    shown=next((v for b in dvr_obj.listing()['buckets'] for v in b['recordings'] if v['id']==rid and v['playable']),None) if entry else None
    if not shown:handler.send(404,{'error':'This recording is not available.'});return
    try:
        item=dvr_obj.client.item(entry['jf_item_id'])
        if not dvr_obj.owned_exact(entry,item):raise jellyfin.JellyfinError('conflict')
        method,url,headers=dvr_obj.client.stream_request(entry['jf_item_id'],handler.headers.get('Range'))
        response=urllib.request.build_opener(jellyfin._NoRedirect).open(urllib.request.Request(url,headers=headers,method=method),timeout=30)
    except jellyfin.JellyfinError as error:
        if error.state=='not_found':dvr_obj.ledger.update(rid,state='expired',reason='media_missing',bytes=0)
        handler.send(404,{'error':'This recording is not available.'});return
    except urllib.error.HTTPError as error:
        error.close();handler.send(404,{'error':'This recording is not available.'});return
    except Exception:
        handler.send(502,{'error':'Recording storage is unavailable.'});return
    with response:
        handler.send_response(response.status);handler.send_header('Content-Type',response.headers.get('Content-Type','video/mp2t'))
        for name in ('Content-Length','Content-Range','Accept-Ranges'):
            if response.headers.get(name):handler.send_header(name,response.headers[name])
        handler.send_header('Cache-Control','no-store');handler.end_headers()
        try:
            for chunk in iter(lambda:response.read(65536),b''):handler.wfile.write(chunk)
        except Exception:
            pass
    handler.close_connection=True

def dvr_stop(rid):
    if dvr_engine()=='jellyfin':
        if not jf_dvr().stop(rid):raise ValueError('Session not found.')
        return {'ok':True}
    if not dvr_store().read(rid):raise ValueError('Session not found.')
    with GUARD:event=DVR_STOPS.get(rid)
    if event:event.set()
    return {'ok':True}

def dvr_media(handler,rid):
    """Stream recorded bytes from the start; follow a recording in progress, never a live provider."""
    store=dvr_store();meta=store.read(rid)
    if not meta or meta['state'] not in ('recording','ready'):
        handler.send(404,{'error':'This recording is not available.'});return
    if meta['state']=='ready':
        good,why=store.check_media(meta)
        if not good:
            store.expire(rid,why);handler.send(404,{'error':'This recording is not available.'});return
    try:handle=open(store.media_path(rid),'rb')
    except OSError:
        store.expire(rid,'media_missing');handler.send(404,{'error':'This recording is not available.'});return
    with handle:
        handler.send_response(200);handler.send_header('Content-Type','video/mp2t')
        handler.send_header('Cache-Control','no-store');handler.end_headers()
        idle=0
        try:
            while True:
                chunk=handle.read(65536)
                if chunk:
                    idle=0;handler.wfile.write(chunk);continue
                current=store.read(rid)
                if not current or current['state']!='recording' or idle>=240:break
                idle+=1;time.sleep(0.5)
        except Exception:
            pass
    handler.close_connection=True

def audio(owner,payload):
    s=session(owner,payload.get('session'))
    code=payload.get('language')
    aliases={'eng':'en','spa':'es','por':'pt','nor':'no','fra':'fr','fre':'fr','deu':'de','ger':'de','ita':'it','ara':'ar','cat':'ca','nld':'nl','dut':'nl'}
    code=str(code or '').lower().split('-')[0];code=aliases.get(code,code)
    if code not in A.LANGUAGES:return {'recorded':False}
    with A.LANGUAGE_LOCK:
        records=A.read_cache('languages') or {}
        key=A.language_key(s['fixture'],s['provider_id'],s['stream_id'])
        old=records.get(key,{})
        # Do not label a future game's audio from an unrelated current broadcast.
        if not s['fixture']['kickoff']-3600<=now()<s['fixture']['kickoff']+10800:return {'recorded':False}
        binding=A.language_binding(s['provider_id'],s['stream_id'])
        if old.get('source_binding')!=binding:old={}
        if old.get('evidence')!='user_confirmed' or old.get('expires_at',0)<=now():
            records[key]={'code':code,'name':A.LANGUAGES[code],'evidence':'stream_track_reported',
                'checked_at':now(),'observed_at':None,'expires_at':min(now()+21600,s['fixture']['kickoff']+10800),'source_binding':binding}
            A.save_cache('languages',records)
    return {'recorded':True}

def body(handler):
    size=int(handler.headers.get('Content-Length','0'))
    if not 0<size<=2048:raise ValueError('Invalid request.')
    value=json.loads(handler.rfile.read(size))
    if not isinstance(value,dict):raise ValueError('Invalid request.')
    return value

def with_team_ids(rows):
    """A next-games cache written before canonical IDs lacks team_id for at most its 2-hour life: fill it from the backend's own favorite list."""
    out=[]
    for row in rows:
        if isinstance(row,dict) and not row.get('team_id'):
            team=livescores.favorite_team(None,row.get('team_index'))
            row=dict(row,team_id=team['team_id']) if team else row
        out.append(row)
    return out

def handle(handler,path,mutation):
    if path.startswith('/jellyfin/') and not mutation:
        parts=path.split('/')
        try:
            if len(parts)==5 and parts[2]=='relay':jf_relay(handler,parts[3],parts[4])
            elif len(parts)==5 and parts[2]=='export':jf_export(handler,parts[3],parts[4])
            else:handler.send(404,{'error':'Not found'})
        except Exception:
            handler.send(502,{'error':'Relay unavailable.'})
        return True
    if not path.startswith('/tv/') and path!='/screen-pair':return False
    try:
        if path=='/screen-pair' and mutation:
            legacy = os.environ.get('SG_LEGACY_SCREEN_PAIRING') == '1' and A.screen_origin_ok(handler.headers) and secrets.compare_digest(handler.headers.get('X-SG-Screen-Token',''),A.SCREEN_TOKEN)
            if not (A.admin_session_ok(handler.headers) or legacy):
                raise PermissionError()
            result=new_pair()
        elif path=='/tv/pair' and mutation:result=pair(body(handler).get('code'))
        elif path=='/tv/home' and not mutation:
            result=A.screen_data();result.pop('screen_token',None)
            schedule=A.load_fixtures()
            fixtures=[f for f in schedule.get('fixtures',[]) if f['kickoff']>now()-10800][:12] if now()-schedule['checked_at']<=schedule['max_age_seconds'] else []
            result['fixtures']=attach_live_fixtures(fixtures)
            next_games_cache=A.read_cache('next-games') or {}
            next_games_fresh=now()-next_games_cache.get('updated_at',0)<7200
            next_games=with_team_ids(next_games_cache.get('games',[])) if next_games_fresh else []
            result['next_games_coverage']=with_team_ids(next_games_cache.get('coverage',[])) if next_games_fresh else []
            # ESPN's nextEvent can be stale (e.g. a sport's off-season last game) rather than
            # genuinely upcoming, so require kickoff to be in the future, not just within the window.
            result['next_games']=[g for g in next_games if now()<=g['kickoff']<=now()+A.NEXT_GAME_WINDOW]
        elif path=='/tv/favorites' and not mutation:
            result=A.favorites_status()
        else:
            owner=device(handler.headers)
            if path.startswith('/tv/artwork/') and not mutation:
                key=path[len('/tv/artwork/'):]
                status,content_type,data,headers=A.artwork_service().get(key,handler.headers.get('If-None-Match'))
                handler._status=status
                handler.send_response(status)
                handler.send_header('Content-Type',content_type)
                handler.send_header('Content-Length',str(len(data)))
                for name,value in headers.items():handler.send_header(name,value)
                handler.end_headers()
                if data:handler.wfile.write(data)
                return True
            if path.startswith('/tv/stream/') and not mutation:
                relay(handler,owner,path.split('/')[-1]);return True
            if path.startswith('/tv/recordings/media/') and not mutation:
                if dvr_engine()=='jellyfin':jf_media(handler,path.split('/')[-1])
                else:dvr_media(handler,path.split('/')[-1])
                return True
            payload=body(handler) if mutation else {}
            if path=='/tv/unpair' and mutation:
                unpair(owner)
                result={'ok':True}
            elif path=='/tv/play' and mutation:result=begin(owner,payload)
            elif path=='/tv/recordings' and not mutation:
                if dvr_engine()=='jellyfin':result={**jf_dvr().listing(),'enabled':True}
                else:result={**dvr_store().listing(),'enabled':dvr_enabled(),'engine':dvr_engine()}
            elif path=='/tv/recordings/stop' and mutation:result=dvr_stop(str(payload.get('id','')))
            elif path=='/tv/heartbeat' and mutation:result=update(owner,payload.get('session'))
            elif path=='/tv/stop' and mutation:result=update(owner,payload.get('session'),True)
            elif path=='/tv/audio' and mutation:result=audio(owner,payload)
            elif path=='/tv/sports' and not mutation:
                result=guide.current(A)
                result['events']=attach_live_events(result['events'])
                result['later']=attach_live_events(guide.later_today(A))
                result['lvbp'],source=lvbp_events()
                result['lvbp_schedule']={'state':source['state'],'checked_at':source['checked_at']}
            elif path=='/tv/sports/feeds' and mutation:
                event=find_attached_event(payload.get('event_id'))
                if not event:raise ValueError('Feed is outside its scheduled tune-in window.')
                result=event
            elif path=='/tv/verify' and mutation:
                if payload.get('event_id'):
                    event=find_attached_event(payload.get('event_id'))
                else:
                    fixture_id=payload.get('fixture_id','')
                    event=A.resolve_next_game(fixture_id) if fixture_id.startswith('next-') else A.resolve(fixture_id=fixture_id)
                    if event:event=verification.annotate(A,event)
                if not event:raise ValueError('Feed is outside its scheduled verification window.')
                result=verification.start(A,event,sample_feed,readiness=payload.get('mode')=='readiness')
            elif path=='/tv/feeds' and mutation:
                fixture_id=payload.get('fixture_id','')
                result=A.resolve_next_game(fixture_id) if fixture_id.startswith('next-') else A.resolve(fixture_id=fixture_id)
                result=verification.annotate(A,result)
            elif path=='/tv/team' and mutation:
                team=livescores.favorite_team(payload.get('team_id'),payload.get('team_index'))
                if team is None:raise ValueError('Unknown team.')
                result=livescores.team_hub(A,team)
            else:handler.send(404,{'error':'Not found'});return True
        handler.send(200,result)
    except PermissionError:handler.send(401,{'error':'Pair this TV from the SG Sports web screen.'})
    except ValueError as e:handler.send(409,{'error':str(e) if str(e).startswith(('This','Provider','Feed','Pairing','Create','Playback','Session','Confirm')) else 'Request could not be completed.'})
    except Exception:handler.send(502,{'error':'Provider or server unavailable. Try again.'})
    return True
