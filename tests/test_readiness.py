"""Live-session readiness: configurable daily budget and unusable option entitlements."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Engine, DEFAULT, validate
from market import Alpaca, DataError, now, stamp
from test_core import bullish_snapshot


class DailyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def test_the_reported_limit_tracks_the_configured_default(self):
        self.assertEqual(self.engine.daily_ai_limit(), DEFAULT['daily_ai_limit'])
        self.assertEqual(self.engine.ai_usage()['limit'], DEFAULT['daily_ai_limit'])

    def test_limit_is_enforced_at_the_configured_value(self):
        self.engine.config['daily_ai_limit'] = 20
        self.assertEqual(self.engine.ai_usage()['limit'], 20)
        self.assertTrue(self.engine.reserve_ai(19))
        self.assertFalse(self.engine.reserve_ai(2))   # would exceed 20
        self.assertTrue(self.engine.reserve_ai(1))    # exactly 20 is allowed
        self.assertFalse(self.engine.reserve_ai(1))
        self.assertEqual(self.engine.ai_usage()['requests'], 20)

    def test_raising_the_limit_frees_further_requests_without_losing_the_count(self):
        self.engine.config['daily_ai_limit'] = 10
        self.assertTrue(self.engine.reserve_ai(10))
        self.assertFalse(self.engine.reserve_ai(1))
        self.engine.config['daily_ai_limit'] = 40
        self.assertTrue(self.engine.reserve_ai(5))
        self.assertEqual(self.engine.ai_usage()['requests'], 15)

    def test_limit_persists_and_is_validated(self):
        self.assertEqual(validate(dict(DEFAULT, daily_ai_limit=500))['daily_ai_limit'], 500)
        for bad in (9, 2001, 'lots', True, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(dict(DEFAULT, daily_ai_limit=bad))
        with patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research'), \
             patch.dict(os.environ, {'APCA_API_KEY_ID': 'x', 'APCA_API_SECRET_KEY': 'x', 'OPENAI_API_KEY': 'x'}):
            self.engine.start(dict(DEFAULT, daily_ai_limit=300))
        self.assertEqual(Engine(self.engine.db_path).daily_ai_limit(), 300)

    def test_a_corrupt_stored_value_falls_back_rather_than_crashing(self):
        self.engine.config['daily_ai_limit'] = 'unlimited'
        self.assertEqual(self.engine.daily_ai_limit(), 120)


class OptionEntitlementTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'APCA_API_KEY_ID': 'x', 'APCA_API_SECRET_KEY': 'x'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = Alpaca()
        self.at = now()

    def collect(self, option_error):
        """Run collect() with an open market and a controllable options failure."""
        clock = {'timestamp': stamp(self.at), 'is_open': True,
                 'next_open': stamp(self.at), 'next_close': stamp(self.at.replace(year=self.at.year + 1))}
        instrument = bullish_snapshot(self.at)['instruments']['SPY']
        with patch.object(self.provider, 'get', side_effect=lambda path, *a, **k: clock if path == '/v2/clock' else {'news': []}), \
             patch.object(self.provider, 'instrument', return_value=instrument), \
             patch.object(self.provider, 'options', side_effect=DataError(option_error)):
            return self.provider.collect(dict(DEFAULT, symbols=['SPY', 'QQQ', 'AAPL'], assets=['stock', 'option']), self.at)

    def test_missing_opra_is_reported_once_and_not_retried(self):
        message = 'Alpaca /v1beta1/options/snapshots/SPY: feed entitlement or permission missing.'
        first = self.collect(message)
        entitlement = [e for e in first['errors'] if 'OPRA' in e]
        self.assertEqual(len(entitlement), 1, first['errors'])
        self.assertIn('Uncheck Options', entitlement[0])
        self.assertIn('Stocks and Bitcoin are unaffected', entitlement[0])
        # Second scan must not call the options endpoint again.
        with patch.object(self.provider, 'options', side_effect=AssertionError('should not retry')) as options:
            second = self.collect(message)
            options.assert_not_called()
        self.assertEqual([e for e in second['errors'] if 'OPRA' in e], entitlement)

    def test_a_transient_option_failure_still_retries_and_reports_verbatim(self):
        message = 'Alpaca /v1beta1/options/snapshots/SPY: rate limit reached; will retry next cycle.'
        result = self.collect(message)
        self.assertIn(message, result['errors'])
        self.assertFalse(any('OPRA' in e for e in result['errors']))
        self.assertIsNone(self.provider._options_denied)

    def test_a_fresh_session_provider_retries_entitlements(self):
        self.provider._options_denied = 'stale denial'
        self.assertIsNone(Alpaca()._options_denied)


if __name__ == '__main__':
    unittest.main()
