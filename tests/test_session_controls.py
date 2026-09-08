"""Stopping and restarting must not destroy research, and research settings retune live."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import Engine, DEFAULT, LIVE_SETTINGS, CAPITAL_SETTINGS
from market import Alpaca, now, stamp
from positions import validate_fill


def report(lane='News & filings'):
    return {'lane': lane, 'status': 'complete', 'at': stamp(now()), 'coverage': 'ok',
            'conclusion': 'Intraday view.', 'long_term_conclusion': 'Longer view.',
            'evidence': [{'symbol': 'SPY', 'lane': lane, 'url': 'https://example.com/a', 'title': 'Source',
                          'summary': 'Something', 'published_at': stamp(now()), 'fresh': True,
                          'sentiment': 'bullish', 'event_risk': False}]}


class ResearchSurvivesSessionControlTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch.dict(os.environ, {'APCA_API_KEY_ID': 'x', 'APCA_API_SECRET_KEY': 'x', 'OPENAI_API_KEY': 'x'}),
                     patch.object(Alpaca, 'tracking_quotes', return_value=({}, [])),
                     patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research')):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def test_stopping_keeps_research_and_only_withdraws_live_ideas(self):
        self.engine.start(DEFAULT)
        self.engine.reports = [report()]
        self.engine.ideas = [{'id': 'i1', 'symbol': 'SPY', 'underlying': 'SPY', 'direction': 'Buy', 'entry': 100,
                              'reasons': []}]
        self.engine.stop()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.ideas, [], 'live ideas are withdrawn')
        self.assertEqual(len(self.engine.reports), 1, 'research the user paid for is kept')

    def test_restarting_keeps_research_because_the_freshness_gate_governs_it(self):
        self.engine.start(DEFAULT)
        self.engine.reports = [report()]
        self.engine.long_reports = [{'lane': 'Long-term research', 'status': 'complete', 'views': [], 'evidence': []}]
        self.engine.stop()
        self.engine.start(DEFAULT)
        self.assertEqual(len(self.engine.reports), 1)
        self.assertEqual(len(self.engine.long_reports), 1)
        # Per-scan output is still rebuilt from scratch.
        self.assertEqual(self.engine.ideas, [])
        self.assertEqual(self.engine.calls_used, 0)


class LiveSettingsTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch.dict(os.environ, {'APCA_API_KEY_ID': 'x', 'APCA_API_SECRET_KEY': 'x', 'OPENAI_API_KEY': 'x'}),
                     patch.object(Alpaca, 'tracking_quotes', return_value=({}, [])),
                     patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research')):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def test_research_settings_change_without_stopping_and_persist(self):
        self.engine.start(DEFAULT)
        self.engine.update_live_settings({'research_interval': 1800, 'require_research': False})
        self.assertTrue(self.engine.running, 'retuning must not stop the session')
        self.assertEqual(self.engine.config['research_interval'], 1800)
        self.assertFalse(self.engine.config['require_research'])
        self.assertEqual(Engine(self.engine.db_path).config['research_interval'], 1800)

    def test_capital_settings_are_not_reachable_through_the_live_endpoint(self):
        self.engine.start(dict(DEFAULT, budget=8000))
        for field in CAPITAL_SETTINGS:
            self.assertNotIn(field, LIVE_SETTINGS)
        with self.assertRaisesRegex(ValueError, 'No adjustable research settings'):
            self.engine.update_live_settings({'budget': 99999})
        self.assertEqual(self.engine.config['budget'], 8000)

    def test_invalid_values_are_rejected_and_leave_config_intact(self):
        self.engine.start(DEFAULT)
        with self.assertRaises(ValueError):
            self.engine.update_live_settings({'research_interval': 5})
        self.assertEqual(self.engine.config['research_interval'], DEFAULT['research_interval'])

    def test_turning_long_research_off_clears_its_stale_views(self):
        self.engine.start(dict(DEFAULT, long_term_research=True))
        self.engine.long_reports = [{'lane': 'Long-term research', 'status': 'complete', 'views': [{}], 'evidence': []}]
        self.engine.update_live_settings({'long_term_research': False})
        self.assertEqual(self.engine.long_reports, [])


class FillRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def log(self, side='buy', quantity=10, price=100):
        token = str(uuid.uuid4())
        self.engine.save_fill(validate_fill({'symbol': 'AAPL', 'asset': 'stock', 'side': side,
                                             'quantity': quantity, 'price': price, 'stop': 90}), token)
        return token

    def test_a_logged_fill_can_be_removed(self):
        token = self.log()
        self.assertEqual(len(self.engine.manual_view()), 1)
        self.engine.delete_fill(token)
        self.assertEqual(self.engine.manual_view(), [])
        self.assertEqual(Engine(self.engine.db_path).manual_fills, [], 'removal persists')

    def test_removing_a_purchase_under_a_later_sale_is_refused(self):
        buy = self.log()
        self.log('sell', 10, 120)
        with self.assertRaisesRegex(ValueError, 'Remove the later sale first'):
            self.engine.delete_fill(buy)
        self.assertEqual(len(self.engine.manual_fills), 2, 'ledger untouched after a refused removal')

    def test_unknown_ids_are_rejected(self):
        for bad in ('', None, 'not-a-fill'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.delete_fill(bad)


if __name__ == '__main__':
    unittest.main()
