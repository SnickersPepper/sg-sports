import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import verification


class FakeApi:
    def __init__(self):
        self.values={}
    def read_cache(self,key):
        return self.values.get(key)
    def save_cache(self,key,value):
        self.values[key]=value


class VerificationTests(unittest.TestCase):
    def setUp(self):
        verification.RUNNING.clear()
        self.api=FakeApi()
        self.event={'id':'guide-a','sport':'Baseball','league':'MLB','start':100,
                    'live':{'espn_id':'401','state':'in'},'feeds':[
                     {'provider_id':'providera','stream_id':'1','display_name':'One'},
                     {'provider_id':'providera','stream_id':'2','display_name':'Two'}]}
        self.api.values['live-scores']={'games':[{'espn_id':'401',
            'sport':'Baseball','state':'in','period':3,'detail':'Top 3rd','display_clock':'',
            'home':{'tokens':['cardinals','louis'],'abbreviation':'STL','score':'1'},
            'away':{'tokens':['giants','francisco'],'abbreviation':'SF','score':'3'}}]}

    def test_repeated_teams_and_live_state_confirm_the_feed(self):
        with patch.object(verification,'_media_info',return_value={'width':1920,'height':1080,'fps':59.94,'bitrate_mbps':8.2,'video_codec':'h264','audio_tracks':2}), \
             patch.object(verification,'_ocr',return_value=['SF 3  STL 1  TOP 3RD','SF 3  STL 1  TOP 3RD']):
            result=verification.inspect_sample(self.api,self.event,b'not-real-ts',125,now=1000)
        self.assertEqual(result['status'],'confirmed')
        self.assertTrue(result['content_verified'])
        self.assertEqual(result['analysis_mode'],'active')
        self.assertEqual(result['shadow_verdict'],'confirmed')
        self.assertEqual(result['state_evidence_frames'],2)
        self.assertEqual(set(result['matched_markers']),{'SF','STL'})
        self.assertEqual(result['height'],1080)

    def test_healthy_video_without_both_teams_stays_unconfirmed(self):
        with patch.object(verification,'_media_info',return_value={'width':1280,'height':720,'fps':30.0,'bitrate_mbps':4.0,'video_codec':'h264','audio_tracks':1}), \
             patch.object(verification,'_ocr',return_value=['SF 3  BASEBALL']*3), \
             patch.object(verification,'_ocr_regions',return_value=[]):
            result=verification.inspect_sample(self.api,self.event,b'not-real-ts',80,now=1000)
        self.assertEqual(result['status'],'healthy_unconfirmed')
        self.assertTrue(result['transport_verified'])
        self.assertFalse(result['content_verified'])
        self.assertEqual(result['shadow_verdict'],'unknown')

    def test_missing_espn_identity_skips_content_ocr(self):
        self.api.values['live-scores']={'games':[]}
        with patch.object(verification,'_media_info',return_value={'width':1280,'height':720,'fps':30.0,'bitrate_mbps':4.0,'video_codec':'h264','audio_tracks':1}), \
             patch.object(verification,'_ocr') as ocr, patch.object(verification,'_ocr_regions') as regions:
            result=verification.inspect_sample(self.api,self.event,b'not-real-ts',80,now=1000)
        ocr.assert_not_called();regions.assert_not_called()
        self.assertEqual(result['evidence_reason'],'espn_event_unavailable')
        self.assertEqual(result['ocr_frames_analyzed'],0)

    def test_team_names_without_game_state_are_only_a_likely_match(self):
        with patch.object(verification,'_media_info',return_value={'width':1280,'height':720,'fps':30.0,'bitrate_mbps':4.0,'video_codec':'h264','audio_tracks':1}), \
             patch.object(verification,'_ocr',return_value=['SF STL BASEBALL','SF STL BASEBALL']), \
             patch.object(verification,'_ocr_regions',return_value=[]):
            result=verification.inspect_sample(self.api,self.event,b'not-real-ts',80,now=1000)
        self.assertEqual(result['shadow_verdict'],'likely_match')
        self.assertEqual(result['team_evidence_frames'],2)
        self.assertEqual(result['state_evidence_frames'],0)

    def test_one_frame_never_confirms_content(self):
        evidence=verification._analysis(self.api,self.event,['SF 3 STL 1 TOP 3RD','',''])
        self.assertEqual(evidence['verdict'],'unknown')

    def test_generic_shared_team_words_are_not_identity_evidence(self):
        game={'home':{'tokens':['state','wildcats'],'abbreviation':''},
              'away':{'tokens':['state','bulldogs'],'abbreviation':''},
              'sport':'Football','state':'in','period':1}
        evidence=verification._team_evidence(game,['STATE 7 STATE 3 Q1']*3)
        self.assertEqual(evidence['team_frames'],0)

    def test_repeated_different_matchup_is_recorded_as_wrong_content_in_shadow(self):
        self.api.values['live-scores']['games'].append({'espn_id':'999','sport':'Baseball',
            'state':'in','period':4,'detail':'Bottom 4th','display_clock':'',
            'home':{'tokens':['cubs','chicago'],'abbreviation':'CHC','score':'2'},
            'away':{'tokens':['dodgers','angeles'],'abbreviation':'LAD','score':'5'}})
        evidence=verification._analysis(self.api,self.event,['LAD 5 CHC 2 BOT 4TH']*2)
        self.assertEqual(evidence['verdict'],'wrong_content')
        self.assertEqual(evidence['detected_espn_id'],'999')

    def test_live_football_requires_repeated_score_and_quarter_match(self):
        self.event['sport']='Football';self.event['league']='NFL'
        self.api.values['live-scores']['games'][0]={
            'espn_id':'401','sport':'Football','state':'in','period':3,
            'detail':'3rd Quarter','display_clock':'4:21',
            'home':{'tokens':['chiefs','kansas'],'abbreviation':'KC','score':'17'},
            'away':{'tokens':['bills','buffalo'],'abbreviation':'BUF','score':'14'}}
        evidence=verification._analysis(self.api,self.event,['BUF 14 KC 17 Q3 4:21']*2)
        self.assertEqual(evidence['verdict'],'confirmed')
        self.assertEqual(evidence['state_frames'],2)
        self.assertEqual(evidence['clock_frames'],2)

    def test_completed_replay_is_not_state_confirmed_from_final_espn_data(self):
        self.api.values['live-scores']['games'][0]['state']='post'
        evidence=verification._analysis(self.api,self.event,['SF 3 STL 1 TOP 3RD']*2)
        self.assertEqual(evidence['verdict'],'likely_match')

    def test_calibration_observation_never_persists_raw_media_or_ocr(self):
        record={'analysis_version':verification.ANALYSIS_VERSION,'shadow_verdict':'confirmed','shadow_confidence':.99,
                'evidence_reason':'teams_and_live_state_match','team_evidence_frames':2,
                'state_evidence_frames':2,'clock_evidence_frames':1,'transport_verified':True,
                'verified_at':1000,'raw_ocr':'SF 3 STL 1','provider_url':'secret'}
        observation=verification._observation(self.event,self.event['feeds'][0],record)
        self.assertNotIn('raw_ocr',observation)
        self.assertNotIn('provider_url',observation)
        self.assertNotIn('score',str(observation))

    def test_verified_feed_sorts_before_higher_resolution_unconfirmed_feed(self):
        first,second=self.event['feeds']
        self.record(first,'unknown',width=3840,height=2160,fps=60)
        self.record(second,'confirmed')
        self.assertEqual(self.streams(),['2','1'])

    def record(self,feed,verdict,event=None,**extra):
        record={'shadow_verdict':verdict,'content_verified':verdict=='confirmed','transport_verified':True,
                'availability':'content_verified' if verdict=='confirmed' else 'stream_healthy',
                'width':1280,'height':720,'fps':30,'bitrate_mbps':4.0,'open_latency_ms':90,
                'analysis_version':verification.ANALYSIS_VERSION,'verified_at':1000,'expires_at':5000,**extra}
        self.api.values.setdefault('stream-verification',{})[verification.record_key(event or self.event,feed)]=record

    def streams(self,event=None):
        return [f['stream_id'] for f in verification.annotate(self.api,event or self.event,now=1100)['feeds']]

    def inspect(self,event,ocr):
        info={'width':1280,'height':720,'fps':30.0,'bitrate_mbps':4.0,'video_codec':'h264','audio_tracks':1}
        with patch.object(verification,'_media_info',return_value=info), \
             patch.object(verification,'_ocr',return_value=ocr), patch.object(verification,'_ocr_regions',return_value=[]):
            return verification.inspect_sample(self.api,event,b'x',80,now=1000)

    def add_game(self,espn_id,sport,home,away):
        self.api.values['live-scores']['games'].append({'espn_id':espn_id,'sport':sport,'state':'in','period':1,
            'detail':'','display_clock':'','home':{'tokens':[home],'abbreviation':home[:3].upper(),'score':'2'},
            'away':{'tokens':[away],'abbreviation':away[:3].upper(),'score':'5'}})

    def test_wrong_event_feed_is_omitted_and_confirmed_feed_remains(self):
        first,second=self.event['feeds']
        self.record(first,'confirmed');self.record(second,'wrong_content')
        result=verification.annotate(self.api,self.event,now=1100)
        self.assertEqual([f['stream_id'] for f in result['feeds']],['1'])
        self.assertEqual(result['verification']['omitted_wrong_content'],1)

    def test_wrong_sport_detection_yields_wrong_content_and_omission(self):
        self.add_game('777','Football','chiefs','bills')
        result=self.inspect(self.event,['CHI 5 BIL 2 Q1']*2)
        self.assertEqual(result['shadow_verdict'],'wrong_content')
        self.assertEqual(result['evidence_reason'],'different_sport_detected')
        self.api.values['stream-verification']={verification.record_key(self.event,self.event['feeds'][0]):result}
        self.assertEqual(self.streams(),['2'])

    def test_wrong_event_needs_repeated_frames(self):
        self.add_game('778','Baseball','cubs','dodgers')
        result=self.inspect(self.event,['CUB 5 DOD 2','',''])
        self.assertEqual(result['shadow_verdict'],'unknown')

    def test_unknown_and_unconfirmed_feeds_are_retained(self):
        first,second=self.event['feeds']
        self.record(first,'unknown');self.record(second,'likely_match')
        self.assertEqual(sorted(self.streams()),['1','2'])
        self.api.values['live-scores']={'games':[]}
        result=self.inspect(self.event,[])
        self.assertEqual(result['shadow_verdict'],'unknown')
        self.assertNotEqual(result['status'],'wrong_content')

    def test_replay_is_never_promoted_or_suppressed(self):
        replay={**self.event,'event_phase':'replay','live':{'espn_id':'401','state':'post','likely_replay':True}}
        result=self.inspect(replay,['SF 3  STL 1  TOP 3RD']*2)
        self.assertFalse(result['content_verified'])
        self.assertEqual(result['shadow_verdict'],'likely_match')
        self.add_game('779','Baseball','cubs','dodgers')
        result=self.inspect(replay,['CUB 5 DOD 2']*2)
        self.assertEqual(result['shadow_verdict'],'unknown')
        first,second=self.event['feeds']
        self.record(first,'confirmed',replay);self.record(second,'wrong_content',replay)
        feeds=verification.annotate(self.api,replay,now=1100)['feeds']
        self.assertEqual(len(feeds),2)
        self.assertFalse(any(f.get('content_verified') for f in feeds))

    def test_replay_evidence_is_scoped_to_its_own_event(self):
        first=self.event['feeds'][0]
        replay={**self.event,'id':'guide-b','live':{'espn_id':'402','state':'post','likely_replay':True}}
        self.assertNotEqual(verification.record_key(self.event,first),verification.record_key(replay,first))
        self.record(first,'wrong_content',replay)
        self.assertEqual(sorted(self.streams()),['1','2'])

    def test_providers_are_judged_by_identical_rules(self):
        events=[{**self.event,'feeds':[{'provider_id':p,'stream_id':'1','display_name':'Main'} for p in ('providera','providerb')]}]
        event=events[0]
        for feed in event['feeds']:
            self.record(feed,'confirmed',event)
        self.assertEqual(self.streams(event),['1','1'])
        self.assertEqual(sorted(f['provider_id'] for f in verification.annotate(self.api,event,now=1100)['feeds']),['providera','providerb'])
        # Provider disagreement: one explicit wrong verdict removes only that provider's feed.
        self.record(event['feeds'][0],'wrong_content',event)
        self.assertEqual([f['provider_id'] for f in verification.annotate(self.api,event,now=1100)['feeds']],['providerb'])
        self.record(event['feeds'][0],'unknown',event)
        self.assertEqual(len(verification.annotate(self.api,event,now=1100)['feeds']),2)

    def test_stale_and_version_mismatched_evidence_is_ignored(self):
        first,second=self.event['feeds']
        self.record(first,'wrong_content',expires_at=1050)
        self.record(second,'confirmed',analysis_version=verification.ANALYSIS_VERSION-1)
        feeds=verification.annotate(self.api,self.event,now=1100)['feeds']
        self.assertEqual(sorted(f['stream_id'] for f in feeds),['1','2'])
        self.assertFalse(any(f.get('content_verified') for f in feeds))
        self.assertFalse(any(f.get('transport_verified') for f in feeds))

    def test_alternate_broadcast_survives_epg_disagreement(self):
        alt={'provider_id':'providera','stream_id':'3','display_name':'Manningcast','broadcast_kind':'alternate'}
        event={**self.event,'feeds':[*self.event['feeds'],alt],
               'broadcast_check':{'status':'disagreement','needs_verification':True,'espn_networks':['ESPN']}}
        self.assertEqual(sorted(self.streams(event)),['1','2','3'])
        self.record(alt,'confirmed',event)
        self.assertEqual(self.streams(event)[0],'3')

    def test_quality_telemetry_is_backend_only(self):
        first,second=self.event['feeds']
        self.record(first,'likely_match',width=3840,height=2160,fps=60)
        self.record(second,'likely_match')
        self.api.values['stream-reliability']={'providera|1':{'attempts':5,'successes':5}}
        result=verification.annotate(self.api,self.event,now=1100)
        self.assertEqual([f['stream_id'] for f in result['feeds']],['1','2'])
        banned={'width','height','fps','bitrate_mbps','open_latency_ms','reliability_percent','quality_label',
                'reliability_label','video_codec','audio_tracks','detected_espn_id','matched_markers','shadow_confidence','_rank'}
        for feed in result['feeds']:
            self.assertFalse(banned&set(feed),banned&set(feed))
        text=str([f.get('verification_label') for f in result['feeds']])
        for token in ('p ','fps','Mbps','%'):
            self.assertNotIn(token,text)

    def pair(self):
        event={**self.event,'feeds':[{'provider_id':p,'stream_id':'1','display_name':'Main'} for p in ('providera','providerb')]}
        return event,event['feeds']

    def order(self,event):
        return [f['provider_id'] for f in verification.annotate(self.api,event,now=1100)['feeds']]

    def test_confirmed_first_across_both_providers(self):
        event,(a,b)=self.pair()
        for winner,loser in ((a,b),(b,a)):
            self.record(loser,'unknown',event,width=3840,height=2160,fps=60,bitrate_mbps=20)
            self.record(winner,'confirmed',event)
            self.assertEqual(self.order(event)[0],winner['provider_id'])

    def test_likely_match_ranks_between_confirmed_and_healthy(self):
        first,second=self.event['feeds']
        third={'provider_id':'providera','stream_id':'3'}
        event={**self.event,'feeds':[first,second,third]}
        self.record(first,'unknown',event,height=2160);self.record(second,'likely_match',event);self.record(third,'confirmed',event)
        self.assertEqual(self.streams(event),['3','2','1'])

    def test_unknown_feed_without_any_record_is_kept_and_never_labeled_wrong(self):
        feeds=verification.annotate(self.api,self.event,now=1100)['feeds']
        self.assertEqual(len(feeds),2)
        self.assertNotIn('wrong',str([f.get('verification_label') for f in feeds]).lower())

    def test_fresh_evidence_outranks_older_evidence_of_same_tier(self):
        first,second=self.event['feeds']
        self.record(first,'confirmed',verified_at=1000,height=2160,fps=60,expires_at=9000)
        self.record(second,'confirmed',verified_at=4900,expires_at=9000)
        feeds=verification.annotate(self.api,self.event,now=5000)['feeds']
        self.assertEqual([f['stream_id'] for f in feeds],['2','1'])

    def test_stale_telemetry_stops_influencing_order(self):
        first,second=self.event['feeds']
        self.record(first,'unknown',verified_at=1000,height=2160,fps=60,expires_at=9000)
        self.record(second,'unknown',verified_at=1000,expires_at=9000)
        key=lambda f:verification.record_key(self.event,f)
        import hashlib
        expected=sorted((first,second),key=lambda f:hashlib.sha1(key(f).encode()).hexdigest())
        feeds=verification.annotate(self.api,self.event,now=5000)['feeds']
        self.assertEqual([f['stream_id'] for f in feeds],[f['stream_id'] for f in expected])

    def test_malformed_metrics_are_safe(self):
        first,second=self.event['feeds']
        self.record(first,'unknown',width='x',height=float('nan'),fps=None,bitrate_mbps=-5,open_latency_ms='bad')
        self.record(second,'unknown',height=True,fps=[],bitrate_mbps={},verified_at='junk')
        self.api.values['stream-reliability']={'providera|1':{'attempts':'many','successes':None},'providera|2':'oops'}
        self.assertEqual(sorted(self.streams()),['1','2'])
        self.api.values['stream-verification']='garbage';self.api.values['stream-reliability']=[]
        self.assertEqual(sorted(self.streams()),['1','2'])

    def test_measured_quality_orders_equal_evidence(self):
        first,second=self.event['feeds']
        self.record(first,'unknown',height=720,fps=30);self.record(second,'unknown',height=1080,fps=60)
        self.assertEqual(self.streams(),['2','1'])
        self.record(second,'unknown',height=1080,fps=30,bitrate_mbps=0.5);self.record(first,'unknown',height=1080,fps=30,bitrate_mbps=6)
        self.assertEqual(self.streams(),['1','2'])

    def test_exact_ties_are_stable_and_not_provider_ordered(self):
        event,(a,b)=self.pair()
        for feed in (a,b):self.record(feed,'unknown',event)
        first=self.order(event)
        flipped={**event,'feeds':[b,a]}
        self.assertEqual(self.order(flipped),first)
        self.assertEqual(self.order(event),first)

    def test_language_preference_and_known_language(self):
        first,second=self.event['feeds']
        third={'provider_id':'providera','stream_id':'3'}
        event={**self.event,'feeds':[{**first},{**second,'audio_language':{'code':'es'}},{**third,'audio_language':{'code':'en'}}]}
        for feed in event['feeds']:self.record(feed,'unknown',event)
        order=lambda lang:[f['stream_id'] for f in verification.annotate(self.api,event,now=1100,preferred_language=lang)['feeds']]
        self.assertEqual(order('en'),['3','2','1'])
        self.assertEqual(order('es'),['2','3','1'])
        self.assertEqual(order(None)[-1],'1')

    def test_quality_outranks_language_and_unknown_language_is_not_dropped(self):
        first,second=self.event['feeds']
        event={**self.event,'feeds':[{**first,'audio_language':{'code':'en'}},second]}
        self.record(event['feeds'][0],'unknown',event,height=720);self.record(second,'unknown',event,height=1080)
        self.assertEqual(self.streams(event),['2','1'])

    def test_provider_health_then_reliability_are_secondary(self):
        event,(a,b)=self.pair()
        for feed in (a,b):self.record(feed,'unknown',event)
        for bad,good in ((a,b),(b,a)):
            self.api.values['stream-reliability']={bad['provider_id']+'|1':{'attempts':10,'successes':2},
                                                   good['provider_id']+'|1':{'attempts':10,'successes':10}}
            self.assertEqual(self.order(event)[0],good['provider_id'])
        # Evidence tier still dominates health and reliability.
        self.record(bad,'confirmed',event)
        self.assertEqual(self.order(event)[0],bad['provider_id'])

    def test_reliability_needs_enough_observations(self):
        first,second=self.event['feeds']
        self.record(first,'unknown');self.record(second,'unknown')
        self.api.values['stream-reliability']={'providera|2':{'attempts':1,'successes':1}}
        import hashlib
        base=self.streams()
        self.api.values['stream-reliability']={}
        self.assertEqual(self.streams(),base)

    def test_wrong_content_omits_only_the_current_feed_even_with_alternate_metadata(self):
        alt={'provider_id':'providera','stream_id':'3','broadcast_kind':'alternate'}
        event={**self.event,'feeds':[*self.event['feeds'],alt],'broadcast_check':{'status':'disagreement'}}
        self.record(event['feeds'][0],'wrong_content',event)
        self.assertEqual(sorted(self.streams(event)),['2','3'])

    def test_replay_confirmed_and_likely_evidence_is_inert_for_ranking(self):
        replay={**self.event,'event_phase':'replay'}
        first,second=self.event['feeds']
        self.record(first,'confirmed',replay);self.record(second,'likely_match',replay)
        feeds=verification.annotate(self.api,replay,now=1100)['feeds']
        self.assertEqual(len(feeds),2)
        self.assertFalse(any(f.get('content_verified') for f in feeds))
        self.assertNotIn('Likely',str([f.get('verification_label') for f in feeds]))

    def test_user_labels_are_nontechnical(self):
        first,second=self.event['feeds']
        self.record(first,'confirmed',height=2160,fps=60,bitrate_mbps=18);self.record(second,'likely_match')
        feeds=verification.annotate(self.api,self.event,now=1100)['feeds']
        labels=[f['verification_label'] for f in feeds]
        self.assertEqual(labels,['Game confirmed 1m ago','Likely this game 1m ago · not confirmed'])
        for f in feeds:
            self.assertNotIn('shadow_verdict',f);self.assertNotIn('audio_language_rank',f)

    def test_shadow_kill_switch_records_without_acting(self):
        first=self.event['feeds'][0]
        self.record(first,'wrong_content')
        with patch.object(verification,'SHADOW_MODE',True):
            self.assertEqual(sorted(self.streams()),['1','2'])

    def test_future_game_is_never_probed(self):
        event={**self.event,'live':{'espn_id':'401','state':'pre'}}
        calls=[]
        self.assertEqual(verification.start(self.api,event,lambda e,f:calls.append(f))['status'],'not_started')
        self.assertEqual(calls,[])

    def test_busy_provider_is_skipped_without_recording_false_failure(self):
        def busy(event,feed):
            raise verification.Busy()
        self.assertEqual(verification.start(self.api,self.event,busy)['status'],'started')
        deadline=time.time()+2
        while verification.event_key(self.event) in verification.RUNNING and time.time()<deadline:
            time.sleep(.01)
        self.assertEqual(self.api.values.get('stream-verification',{}),{})

    def test_current_version_failure_obeys_short_cache_ttl(self):
        feed=self.event['feeds'][0]
        self.api.values['stream-verification']={verification.record_key(self.event,feed):{
            **verification._failure_record(int(time.time())), 'expires_at':int(time.time())+60}}
        calls=[]
        self.assertEqual(verification.start(self.api,{**self.event,'feeds':[feed]},lambda e,f:calls.append(f))['status'],'fresh')
        self.assertEqual(calls,[])

    # --- SGH-289: near-start transport readiness (never content confirmation) ---
    INFO={'width':1280,'height':720,'fps':30.0,'bitrate_mbps':4.0,'video_codec':'h264','audio_tracks':1}

    def pregame(self,**extra):
        return {**self.event,'live':{'espn_id':'401','state':'pre'},'event_phase':'pregame',**extra}

    def wait(self,event):
        deadline=time.time()+8
        while verification.event_key(event) in verification.RUNNING and time.time()<deadline:
            time.sleep(.01)

    def test_near_start_readiness_invokes_sampling_and_records_transport_only(self):
        event=self.pregame();calls=[]
        def sample(e,f):
            calls.append(f['stream_id']);return b'ts',50
        with patch.object(verification,'_media_info',return_value=self.INFO), \
             patch.object(verification,'_ocr') as ocr:
            self.assertEqual(verification.start(self.api,event,sample,readiness=True)['status'],'started')
            self.wait(event)
        ocr.assert_not_called()
        self.assertEqual(sorted(calls),['1','2'])
        for record in self.api.values['stream-verification'].values():
            self.assertTrue(record['transport_verified']);self.assertFalse(record['content_verified'])
            self.assertEqual(record['availability'],'stream_healthy');self.assertEqual(record['shadow_verdict'],'unknown')
            self.assertNotEqual(record['status'],'confirmed')
        self.assertNotIn('stream-verification-observations',self.api.values)
        public=verification.annotate(self.api,event)['feeds'][0]
        self.assertFalse(public['content_verified']);self.assertTrue(public['transport_verified'])

    def test_readiness_without_flag_or_outside_window_stays_unprobed(self):
        calls=[]
        sample=lambda e,f:calls.append(f)
        self.assertEqual(verification.start(self.api,self.pregame(),sample)['status'],'not_started')
        far={**self.pregame(),'event_phase':'upcoming'}
        self.assertEqual(verification.start(self.api,far,sample,readiness=True)['status'],'not_started')
        self.assertEqual(calls,[])

    def test_readiness_cannot_confirm_even_if_sampler_is_perfect(self):
        with patch.object(verification,'_media_info',return_value=self.INFO):
            record=verification.inspect_transport(b'ts',10,now=1000)
        self.assertFalse(record['content_verified']);self.assertTrue(record['readiness_only'])
        self.assertEqual(record['expires_at'],1000+verification.READINESS_TTL)
        with patch.object(verification,'_media_info',return_value=None):
            self.assertFalse(verification.inspect_transport(b'bad',10,now=1000)['transport_verified'])

    def test_live_check_after_readiness_still_runs_full_content_verification(self):
        feed=self.event['feeds'][0];event={**self.event,'feeds':[feed]}
        with patch.object(verification,'_media_info',return_value=self.INFO):
            self.api.values['stream-verification']={verification.record_key(event,feed):verification.inspect_transport(b'ts',1)}
        calls=[]
        def sample(e,f):
            calls.append(f);return b'ts',5
        with patch.object(verification,'_media_info',return_value=self.INFO), \
             patch.object(verification,'_ocr',return_value=['SF 3  STL 1  TOP 3RD']*2):
            self.assertEqual(verification.start(self.api,event,sample)['status'],'started')
            self.wait(event)
        self.assertEqual(len(calls),1)
        self.assertTrue(self.api.values['stream-verification'][verification.record_key(event,feed)]['content_verified'])

    def test_fresh_readiness_is_not_repeated_but_denial_records_nothing(self):
        event=self.pregame();feed=event['feeds'][0];event={**event,'feeds':[feed]}
        def busy(e,f):raise verification.Busy()
        self.assertEqual(verification.start(self.api,event,busy,readiness=True)['status'],'started')
        self.wait(event)
        self.assertEqual(self.api.values.get('stream-verification',{}),{})
        with patch.object(verification,'_media_info',return_value=self.INFO):
            verification.start(self.api,event,lambda e,f:(b'ts',1),readiness=True);self.wait(event)
        calls=[]
        self.assertEqual(verification.start(self.api,event,lambda e,f:calls.append(f),readiness=True)['status'],'fresh')
        self.assertEqual(calls,[])

    def test_source_generation_change_invalidates_readiness(self):
        feed=self.event['feeds'][0];event={**self.pregame(),'feeds':[feed]}
        verification.configure_binding(lambda:(lambda f:'route-a'))
        try:
            with patch.object(verification,'_media_info',return_value=self.INFO):
                verification.start(self.api,event,lambda e,f:(b'ts',1),readiness=True);self.wait(event)
            self.assertTrue(verification.annotate(self.api,event)['feeds'][0].get('transport_verified'))
            verification.configure_binding(lambda:(lambda f:'route-b'))
            self.assertFalse(verification.annotate(self.api,event)['feeds'][0].get('transport_verified'))
        finally:
            verification.configure_binding(None)

    def test_route_change_during_readiness_sample_keeps_nothing(self):
        feed=self.event['feeds'][0];event={**self.pregame(),'feeds':[feed]}
        state={'route':'a'}
        verification.configure_binding(lambda:(lambda f:state['route']))
        def sample(e,f):
            state['route']='b';return b'ts',1
        try:
            with patch.object(verification,'_media_info',return_value=self.INFO):
                verification.start(self.api,event,sample,readiness=True);self.wait(event)
            self.assertEqual(self.api.values.get('stream-verification',{}),{})
        finally:
            verification.configure_binding(None)


if __name__=='__main__':unittest.main()
