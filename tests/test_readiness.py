"""Live-session readiness: configurable daily budget and unusable option entitlements."""
import json
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




class OpenPositionPinningTests(unittest.TestCase):
    """An open paper position pins the capital it was sized against, not the research settings."""

    def setUp(self):
        for mock in (patch.dict(os.environ, {'APCA_API_KEY_ID': 'x', 'APCA_API_SECRET_KEY': 'x', 'OPENAI_API_KEY': 'x'}),
                     patch.object(Alpaca, 'tracking_quotes', return_value=({}, [])),
                     patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research')):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def open_paper_trade(self, config):
        from market import now, stamp
        trade = {'id': 't1', 'symbol': 'BTC/USD', 'underlying': 'BTC/USD', 'asset': 'bitcoin', 'status': 'open',
                 'mode': 'live', 'config': dict(config), 'notional': 100, 'risk': 10, 'quantity': .001,
                 'entry': 100000, 'stop': 90000, 'target': 120000, 'multiplier': 1,
                 'opened_at': stamp(now()), 'mark_status': 'Awaiting next quote'}
        self.engine.save_trade(trade)
        self.engine.trades.append(trade)

    def test_research_settings_stay_editable_while_a_position_is_open(self):
        old = dict(DEFAULT, research_interval=600, research_calls=36)
        self.open_paper_trade(old)
        self.engine.config = dict(DEFAULT, research_interval=1200, research_calls=60)
        with self.engine.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)", (json.dumps(self.engine.config),))
        # A restart must not drag the cadence back to whatever the open trade was opened under.
        reloaded = Engine(self.engine.db_path)
        self.assertEqual(reloaded.config['research_interval'], 1200)
        self.assertEqual(reloaded.config['research_calls'], 60)
        # Starting with new research settings is allowed.
        reloaded.start(dict(DEFAULT, research_interval=1800, research_calls=60))
        self.assertEqual(reloaded.config['research_interval'], 1800)

    def test_capital_settings_are_still_pinned(self):
        self.open_paper_trade(dict(DEFAULT, budget=8000))
        self.engine.config = validate(dict(DEFAULT, budget=8000))
        for change in ({'budget': 30000}, {'risk_pct': 2}, {'loss_limit_pct': 4}, {'max_position_pct': 50}):
            attempt = dict(DEFAULT, budget=8000)
            attempt.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'cannot change while paper positions are open'):
                self.engine.start(validate(attempt))

    def test_a_reloaded_engine_keeps_the_positions_capital_basis(self):
        self.open_paper_trade(dict(DEFAULT, budget=8000, risk_pct=2, loss_limit_pct=4))
        with self.engine.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)", (json.dumps(dict(DEFAULT, budget=99999)),))
        reloaded = Engine(self.engine.db_path)
        self.assertEqual(reloaded.config['budget'], 8000)
        self.assertEqual(reloaded.config['risk_pct'], 2)


if __name__ == '__main__':
    unittest.main()
