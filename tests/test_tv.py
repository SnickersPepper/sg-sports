import tempfile
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import app
import tv
import guide
import livescores
import verification

class TVTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        p=patch.object(app,'DATA',Path(tmp.name));p.start();self.addCleanup(p.stop)
        tv.PAIR.clear();tv.ACTIVE.clear()
        tv.VERIFYING.clear();verification.RUNNING.clear()
        self.game={'fixture':{'id':'test','kickoff':10000},'feeds':[{'provider_id':'providera','stream_id':'42'}]}
        self.payload={'fixture_id':'test','provider_id':'providera','stream_id':'42'}

    def test_home_next_games_filtered_to_two_week_window(self):
        near = {'id': 'next-1', 'team_name': 'A', 'sport': 'Football', 'league': 'NFL',
                'opponent': 'B', 'home': True, 'competition': 'NFL', 'kickoff': app.NEXT_GAME_WINDOW - 100}
        far = {'id': 'next-2', 'team_name': 'C', 'sport': 'Softball', 'league': 'NCAA',
               'opponent': 'D', 'home': True, 'competition': 'NCAA', 'kickoff': app.NEXT_GAME_WINDOW + 100000}
        # ESPN's "next event" can be stale off-season data pointing at a game already played.
        stale_past = {'id': 'next-3', 'team_name': 'E', 'sport': 'Softball', 'league': 'NCAA',
                      'opponent': 'F', 'home': True, 'competition': 'NCAA', 'kickoff': -100}
        app.save_cache('next-games', {'updated_at': 0, 'games': [near, far, stale_past]})
        with patch.object(tv, 'now', return_value=0):
            handler = MagicMock(); handler.headers = {}
            tv.handle(handler, '/tv/home', False)
        result = handler.send.call_args[0][1]
        self.assertEqual([g['id'] for g in result['next_games']], ['next-1'])

    def test_favorites_excludes_barcelona_duplicate_and_attaches_live_status(self):
        team1 = {'id': 'next-12-1', 'team_index': 1, 'team_name': 'Kansas City Chiefs',
                 'sport': 'Football', 'league': 'NFL', 'opponent': 'Broncos', 'home': True, 'kickoff': 500}
        barca_dupe = {'id': 'next-83-9', 'team_index': 0, 'team_name': 'Barcelona',
                      'sport': 'Soccer', 'league': 'La Liga', 'opponent': 'Sevilla', 'home': True, 'kickoff': 100}
        app.save_cache('next-games', {'updated_at': 0, 'games': [team1, barca_dupe]})
        with patch.object(app, 'load_fixtures', return_value={'fixtures': [], 'checked_at': 0, 'max_age_seconds': 0}), \
             patch('livescores.for_fixture', return_value={'verified_live': True}), \
             patch('time.time', return_value=0):
            handler = MagicMock(); handler.headers = {}
            tv.handle(handler, '/tv/favorites', False)
        result = handler.send.call_args[0][1]
        self.assertEqual([t['fixture_id'] for t in result['teams']], ['next-12-1'])
        self.assertTrue(result['teams'][0]['verified_live'])

    def test_guide_slot_ended_card_survives_only_while_espn_says_still_live(self):
        # A provider's guide slot is a fixed block a real game can run past -- the card must stay
        # up past that point as long as ESPN's own live-score status still says the game is
        # going, and disappear the moment ESPN says it's over (or never had it at all).
        still_playing={'id':'a','guide_slot_ended':True,'start':1,'end':2}
        finished={'id':'b','guide_slot_ended':True,'start':1,'end':2}
        unmatched={'id':'c','guide_slot_ended':True,'start':1,'end':2}
        still_within_slot={'id':'d','guide_slot_ended':False,'start':1,'end':2}
        def fake_for_event(api,event):
            return {'a':{'verified_live':True},'b':{'verified_live':False,'state':'post'},'d':None}.get(event['id'])
        with patch('livescores.for_event',side_effect=fake_for_event):
            result=tv.attach_live_events([still_playing,finished,unmatched,still_within_slot])
        self.assertEqual({e['id'] for e in result},{'a','d'})

    def test_relevance_score_does_not_reorder_backend_events(self):
        # Phase 8: score ordering belongs to Android Worth watching, after favorite exclusion.
        def ev(i): return {'id':i,'sport':'Baseball','league':'MLB','start':1,'end':2}
        lives={'a':{'verified_live':True,'notable':False,'relevance':{'score':0}},
               'b':{'verified_live':True,'notable':False,'relevance':{'score':90}},
               'c':{'verified_live':True,'notable':False,'relevance':{'score':40}}}
        with patch('livescores.for_event',side_effect=lambda api,e:lives[e['id']]):
            result=tv.attach_live_events([ev('a'),ev('b'),ev('c')])
        self.assertEqual([e['id'] for e in result],['a','b','c'])

    def test_same_espn_event_merges_overlapping_guide_blocks_and_feeds(self):
        early={'id':'early','sport':'Baseball','league':'MLB','start':1000,'end':9000,
               'feeds':[{'provider_id':'providerb','stream_id':'regional'}]}
        official={'id':'official','sport':'Baseball','league':'MLB','start':5000,'end':12000,
                  'feeds':[{'provider_id':'providerb','stream_id':'regional'},
                           {'provider_id':'providerb','stream_id':'team'}]}
        def live(api,event):
            return {'espn_id':'401','event_start':5100,'verified_live':True,'notable':False}
        with patch('livescores.for_event',side_effect=live):
            result=tv.attach_live_events([early,official])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['id'],'official')
        self.assertEqual(result[0]['start'],5000)
        self.assertEqual(result[0]['merged_event_ids'],['early','official'])
        self.assertEqual({f['stream_id'] for f in result[0]['feeds']},{'regional','team'})

    def test_sports_route_attaches_live_data_to_coming_next_too(self):
        # 'later' (Coming Next -- games that haven't started yet) went straight from
        # guide.later_today() into the response with no live-score attachment at all, unlike
        # 'events' a line above it -- so a ranked team's rank (and any score) never appeared on
        # an upcoming game's card, only once it was already live.
        code=tv.new_pair()['code'];token=tv.pair(code)['token']
        handler=MagicMock();handler.headers={'Authorization':'Bearer '+token}
        later_event={'id':'x','title':'Alabama at Kentucky','sport':'Football','league':'NCAA','start':1,'end':2}
        with patch('guide.current',return_value={'events':[],'updated_at':0,'fresh':True,'source':'x','mapped_channels':0,'sports':{}}), \
             patch('guide.later_today',return_value=[later_event]), \
             patch('livescores.for_event',return_value={'verified_live':False,'notable':False,'home_rank':1}):
            tv.handle(handler,'/tv/sports',False)
        result=handler.send.call_args[0][1]
        self.assertEqual(result['later'][0]['live']['home_rank'],1)

    def test_team_route_rejects_out_of_range_index_and_delegates_valid_one(self):
        code=tv.new_pair()['code'];token=tv.pair(code)['token']
        def request(team_index):
            body=('{"team_index": %r}' % team_index).encode()
            handler=MagicMock();handler.headers={'Authorization':'Bearer '+token,'Content-Length':str(len(body))}
            handler.rfile=io.BytesIO(body)
            tv.handle(handler,'/tv/team',True)
            return handler
        bad=request(999)
        self.assertEqual(bad.send.call_args[0][0],409)
        with patch('livescores.team_hub',return_value={'record':'9-2'}) as hub:
            ok=request(0)
        hub.assert_called_once_with(app,livescores.FAVORITE_TEAMS[0])
        self.assertEqual(ok.send.call_args[0],(200,{'record':'9-2'}))

    def test_team_route_by_canonical_id_and_total_hub_failure_is_not_an_empty_200(self):
        code=tv.new_pair()['code'];token=tv.pair(code)['token']
        def request(payload):
            body=json.dumps(payload).encode()
            handler=MagicMock();handler.headers={'Authorization':'Bearer '+token,'Content-Length':str(len(body))}
            handler.rfile=io.BytesIO(body)
            tv.handle(handler,'/tv/team',True)
            return handler
        team=livescores.FAVORITE_TEAMS[3]
        with patch('livescores.team_hub',return_value={'record':'9-2'}) as hub:
            ok=request({'team_id':team['team_id']})
        hub.assert_called_once_with(app,team)
        self.assertEqual(ok.send.call_args[0][0],200)
        self.assertEqual(request({'team_id':'team.baseball.men.mlb.unknown'}).send.call_args[0][0],409)
        with patch('livescores._get',side_effect=Exception('provider unreachable')):
            failed=request({'team_id':team['team_id']})
        self.assertEqual(failed.send.call_args[0][0],502)

    def test_team_route_transition_request_carries_id_and_installed_index_in_agreement(self):
        # The TV sends team_id plus legacy_team_index; the installed asset order must equal the backend catalog order.
        root=Path(__file__).parent.parent/'android/app/src'
        roster=root/'private/assets-art/favorites.json'   # the maintainer's roster; the public tree ships none
        if not roster.exists():self.skipTest('maintainer roster is not part of this tree')
        assets=json.loads(roster.read_text())['teams']
        self.assertEqual([t['id'] for t in assets],[t['team_id'] for t in livescores.FAVORITE_TEAMS])
        self.assertEqual([t['legacy_team_index'] for t in assets],list(range(len(assets))))
        code=tv.new_pair()['code'];token=tv.pair(code)['token']
        def request(payload):
            body=json.dumps(payload).encode()
            handler=MagicMock();handler.headers={'Authorization':'Bearer '+token,'Content-Length':str(len(body))}
            handler.rfile=io.BytesIO(body)
            tv.handle(handler,'/tv/team',True)
        for i,t in enumerate(assets):
            with patch('livescores.team_hub',return_value={'record':'1-0'}) as hub:
                request({'team_id':t['id'],'team_index':t['legacy_team_index']})
            hub.assert_called_once_with(app,livescores.FAVORITE_TEAMS[i])
        # A stale or conflicting index can never override the canonical ID.
        with patch('livescores.team_hub',return_value={}) as hub:
            request({'team_id':assets[3]['id'],'team_index':0})
        hub.assert_called_once_with(app,livescores.FAVORITE_TEAMS[3])

    def test_pair_code_one_use_and_wrong_attempt_limit(self):
        code=tv.new_pair()['code'];token=tv.pair(code)['token']
        self.assertEqual(tv.device({'Authorization':'Bearer '+token}),tv.digest(token))
        self.assertNotIn(token,str(app.read_cache('tv-devices')))
        with self.assertRaises(ValueError):tv.pair(code)
        tv.new_pair()
        for _ in range(5):
            with self.assertRaises(ValueError):tv.pair('not-a-code')
        with self.assertRaises(ValueError):tv.pair('not-a-code')
        with self.assertRaises(PermissionError):tv.device({'Authorization':'Bearer invalid'})

    def start(self):
        adapter=MagicMock();adapter.host='http://provider.example'
        adapter.account.return_value={'status':'Active','active_cons':'0','allowed_output_formats':['ts']}
        with patch.object(app,'resolve',return_value=self.game),patch.object(app,'Xtream',return_value=adapter):
            return tv.begin('owner',self.payload)

    def test_reservation_owner_and_double_stream_guard(self):
        first=self.start();sid=first['session']
        self.assertNotIn('provider.example',str(first))
        with self.assertRaises(ValueError):self.start()
        with self.assertRaises(ValueError):tv.update('other',sid)
        tv.ACTIVE.add(sid);tv.update('owner',sid,True)
        with self.assertRaises(ValueError):self.start()
        tv.ACTIVE.clear()
        sessions=app.read_cache('tv-sessions');sessions[sid]['expires']=0;app.save_cache('tv-sessions',sessions)
        self.assertTrue(self.start()['session'])

    def test_next_game_fixture_id_routes_through_resolve_next_game(self):
        # A favorite other than Barcelona uses a 'next-<espn_id>-<event_id>' fixture id, which
        # app.resolve() (Barcelona's own schedule lookup) can never match -- must go through
        # resolve_next_game() instead, same branching /tv/feeds already uses.
        adapter=MagicMock();adapter.host='http://provider.example'
        adapter.account.return_value={'status':'Active','active_cons':'0','allowed_output_formats':['ts']}
        payload={'fixture_id':'next-12-999','provider_id':'providera','stream_id':'42'}
        with patch.object(app,'resolve_next_game',return_value=self.game) as next_game, \
             patch.object(app,'resolve') as barcelona_resolve, \
             patch.object(app,'Xtream',return_value=adapter):
            result=tv.begin('owner',payload)
        next_game.assert_called_once_with('next-12-999')
        barcelona_resolve.assert_not_called()
        self.assertTrue(result['session'])

    def test_scheduled_feed_can_start_five_minutes_early_but_not_hours_early(self):
        adapter=MagicMock();adapter.host='http://provider.example'
        adapter.account.return_value={'status':'Active','active_cons':'0','allowed_output_formats':['ts']}
        game={'fixture':{'id':'next-12-999','kickoff':10000},'feeds':[{'provider_id':'providera','stream_id':'42'}]}
        payload={'fixture_id':'next-12-999','provider_id':'providera','stream_id':'42'}
        with patch.object(app,'resolve_next_game',return_value=game),patch.object(app,'Xtream',return_value=adapter):
            with patch.object(tv,'now',return_value=5000):
                with self.assertRaisesRegex(ValueError,'30 minutes'):
                    tv.begin('owner',payload)
            with patch.object(tv,'now',return_value=9700):
                self.assertTrue(tv.begin('owner',payload)['session'])

    def test_external_active_connection_blocks_start(self):
        adapter=MagicMock();adapter.account.return_value={'status':'Active','active_cons':'1'}
        with patch.object(app,'resolve',return_value=self.game),patch.object(app,'Xtream',return_value=adapter):
            with self.assertRaises(ValueError):tv.begin('owner',self.payload)
        self.assertIsNone(app.read_cache('tv-sessions'))

    def test_active_playback_blocks_verification_before_provider_access(self):
        app.save_cache('tv-sessions',{'live':{'provider_id':'providera','expires':200,'stop':False}})
        with patch.object(tv,'now',return_value=100),patch.object(app,'Xtream') as adapter:
            with self.assertRaises(verification.Busy):
                tv.sample_feed({}, {'provider_id':'providera','stream_id':'42'})
        adapter.assert_not_called()

    def test_playback_refuses_to_race_provider_verification(self):
        tv.VERIFYING.add('providera')
        with patch.object(app,'resolve',return_value=self.game):
            with self.assertRaisesRegex(ValueError,'verification is finishing'):
                tv.begin('owner',self.payload)

    def test_private_media_redirect_rejected(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('192.0.2.55',80))]):
            with self.assertRaises(ValueError):tv.public_url('http://provider.example/live')
        with self.assertRaises(ValueError):tv.public_url('file:///etc/passwd')

    def test_audio_report_is_tag_not_confirmation_and_event_scoped(self):
        with patch.object(tv,'now',return_value=9500):
            sid=self.start()['session'];tv.audio('owner',{'session':sid,'language':'eng'})
            r=next(iter(app.read_cache('languages').values()))
            self.assertEqual(r['evidence'],'stream_track_reported');self.assertEqual(r['code'],'en')
            app.save_cache('languages',{})
        with patch.object(tv,'now',return_value=6000):
            tv.audio('owner',{'session':sid,'language':'en'})
            self.assertEqual(app.read_cache('languages'),{})

    def test_relay_cleanup_on_success_and_failure(self):
        sid=self.start()['session']
        handler=MagicMock();handler.wfile=io.BytesIO()
        with patch.object(tv,'open_media',return_value=io.BytesIO(b'test-stream-data')):
            tv.relay(handler,'owner',sid)
        self.assertEqual(handler.wfile.getvalue(),b'test-stream-data')
        self.assertNotIn(sid,app.read_cache('tv-sessions'));self.assertNotIn(sid,tv.ACTIVE)
        sid=self.start()['session']
        with patch.object(tv,'open_media',side_effect=RuntimeError('secret-url')):
            tv.relay(handler,'owner',sid)
        self.assertNotIn(sid,app.read_cache('tv-sessions'))
        self.assertNotIn('secret-url',str(handler.send.call_args))
