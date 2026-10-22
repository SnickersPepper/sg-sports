import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import guide
import livescores
import lvbp
from schedule import parse_schedule

PAGE = '''<li class="fixture-result-list__fixture" data-fixture-id="1"><div data-kickoff="1788972300000"></div><div class="fixture-info__name fixture-info__name--home">FC Barcelona</div><div class="fixture-info__name fixture-info__name--away">Feyenoord</div><span class="fixture-result-list__name visually-hidden">UEFA Champions League</span></li>'''

class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = patch.object(app, 'DATA', Path(self.temp.name))
        self.data.start(); self.addCleanup(self.data.stop)

    def test_official_schedule_epoch_and_duplicate(self):
        result = parse_schedule(PAGE + PAGE, now=1788875760)
        self.assertEqual(len(result['fixtures']), 1)
        self.assertEqual(result['fixtures'][0]['kickoff_iso'], '2026-09-09T18:45:00+02:00')
        self.assertEqual(result['fixtures'][0]['opponent'], 'Feyenoord')

    def test_empty_or_tba_page_rejected(self):
        for page in ('', PAGE.replace('data-kickoff', 'data-unconfirmed'), PAGE.replace('Feyenoord', 'Feyenoord Date and time to be announced')):
            with self.assertRaises(ValueError):
                parse_schedule(page, now=1788875760)

    def test_failure_retains_previous_cache_and_retries(self):
        app.save_cache('fixtures', {'sentinel': 'last_good'})
        with patch.object(app, 'fetch_schedule', side_effect=ValueError('private')), patch.object(app, 'refresh', side_effect=app.ProviderError('private')), patch.object(lvbp, 'refresh'):
            app.run_due_jobs(now=1)
        self.assertEqual(app.read_cache('fixtures'), {'sentinel': 'last_good'})
        state = app.read_cache('scheduler')
        self.assertEqual(state['jobs']['fixtures']['status'], 'failed')
        self.assertNotIn('private', str(state))
        self.assertGreater(state['jobs']['fixtures']['next_run'], 1)

    def test_success_and_not_due(self):
        fixtures = {'fixtures': []}
        with patch.object(guide, 'refresh'), patch.object(livescores, 'refresh'), patch.object(lvbp, 'refresh'), patch.object(app, 'fetch_schedule', return_value=fixtures) as fetch, patch.object(app, 'refresh') as refresh, patch.object(app, 'refresh_epg') as epg:
            app.run_due_jobs(now=1)
            app.run_due_jobs(now=2)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(refresh.call_count, len(app.PROVIDERS))
        self.assertEqual(epg.call_count, len(app.PROVIDERS))
        self.assertEqual(app.read_cache('fixtures'), fixtures)
        self.assertTrue(all(j['status'] == 'ok' for j in app.read_cache('scheduler')['jobs'].values()))

    def test_refresh_lock_blocks_scheduler(self):
        with app.LOCK, patch.object(app, 'fetch_schedule') as fetch:
            app.run_due_jobs(now=1)
        fetch.assert_not_called()

if __name__ == '__main__': unittest.main()
