import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import app

class LanguageTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        p=patch.object(app,'DATA',Path(t.name));p.start();self.addCleanup(p.stop)
        self.fixture={'id':'game-1','kickoff':10000}
        self.channel={'provider_id':'providera','stream_id':'42','category':'EN| STAN','name':'Event'}
        self.game={'fixture':self.fixture,'feeds':[{'provider_id':'providera','stream_id':'42'}]}
        self.payload={'fixture_id':'game-1','provider_id':'providera','stream_id':'42','code':'es','heard_audio':True}

    def test_inference_is_not_confirmation(self):
        a=app.audio_language(self.fixture,self.channel,9000,9500)
        self.assertEqual(a['evidence'],'provider_label');self.assertIsNone(a['checked_at'])
        self.channel['category']='|US| STAN'
        self.assertIsNone(app.audio_language(self.fixture,self.channel,9000,9500)['code'])

    def test_confirmation_correction_reset_and_isolation(self):
        with patch.object(app,'resolve',return_value=self.game):
            app.save_language(self.payload,9500)
            self.assertEqual(app.audio_language(self.fixture,self.channel,9000,9600)['code'],'es')
            self.assertEqual(app.audio_language({'id':'game-2','kickoff':10000},self.channel,9000,9600)['evidence'],'provider_label')
            self.assertEqual(app.audio_language(self.fixture,{**self.channel,'provider_id':'providerb'},9000,9600)['evidence'],'provider_label')
            self.assertEqual(app.audio_language(self.fixture,self.channel,9000,21000)['evidence'],'provider_label')
            app.save_language({**self.payload,'code':'en'},9700)
            self.assertEqual(app.audio_language(self.fixture,self.channel,9000,9800)['code'],'en')
            app.save_language({**self.payload,'code':None},9800)
            self.assertEqual(app.audio_language(self.fixture,self.channel,9000,9900)['evidence'],'provider_label')

    def test_future_unheard_and_invalid_rejected(self):
        with patch.object(app,'resolve',return_value=self.game):
            for payload,now in [(self.payload,100),({**self.payload,'heard_audio':False},9500),({**self.payload,'code':'bad'},9500),({**self.payload,'stream_id':'bad'},9500)]:
                with self.assertRaises(ValueError):app.save_language(payload,now)
        self.assertIsNone(app.read_cache('languages'))
