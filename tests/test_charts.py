"""Chart history: any supported ticker, any range, with the market closed and the session stopped."""
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app import Engine
from market import Alpaca, CHART_RANGES, now, parse, stamp, NY


def minute_bars(at, count, start_price=100.0):
    return [{'t': stamp(at - timedelta(minutes=count - i)), 'o': start_price + i, 'h': start_price + i + .5,
             'l': start_price + i - .5, 'c': start_price + i, 'v': 1000} for i in range(count)]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'APCA_API_KEY_ID': 'fake', 'APCA_API_SECRET_KEY': 'fake', 'ALPACA_STOCK_FEED': 'iex'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = Alpaca()
        self.at = now()

    def test_each_range_requests_its_own_bar_size(self):
        for span, spec in CHART_RANGES.items():
            with patch.object(self.provider, 'get', return_value={'bars': {'QQQ': []}}) as get:
                self.provider.history('QQQ', span, self.at)
            params = get.call_args.args[1]
            self.assertEqual(params['timeframe'], spec['timeframe'], span)
            self.assertEqual(params['feed'], 'iex', span)
            self.assertEqual(params['adjustment'], 'split', span)
            self.assertEqual(get.call_args.args[0], '/v2/stocks/bars', span)
            requested = self.at - parse(params['start'])
            self.assertAlmostEqual(requested.total_seconds(), spec['lookback'].total_seconds(), delta=5, msg=span)

    def test_five_minute_history_excludes_unfinished_candle(self):
        bars = minute_bars(self.at, 10)
        with patch.object(self.provider, 'get', return_value={'bars': {'QQQ': bars}}) as get:
            result = self.provider.history('QQQ', '1D', self.at, '5Min')
        self.assertEqual(get.call_args.args[1]['timeframe'], '5Min')
        self.assertTrue(all(parse(b['t']) + timedelta(minutes=5) <= self.at for b in result))

    def test_intraday_range_returns_the_last_real_session_not_an_empty_window(self):
        # Two sessions of minute bars; a 1D chart should show only the most recent one.
        friday = self.at - timedelta(days=3)
        thursday = self.at - timedelta(days=4)
        bars = minute_bars(thursday, 5) + minute_bars(friday, 7)
        with patch.object(self.provider, 'get', return_value={'bars': {'QQQ': bars}}):
            result = self.provider.history('QQQ', '1D', self.at)
        days = {parse(b['t']).astimezone(NY).date() for b in result}
        self.assertEqual(len(days), 1)
        self.assertEqual(days.pop(), parse(bars[-1]['t']).astimezone(NY).date())
        self.assertEqual(len(result), 7)

    def test_longer_ranges_keep_every_session(self):
        bars = minute_bars(self.at - timedelta(days=3), 5) + minute_bars(self.at - timedelta(days=1), 5)
        with patch.object(self.provider, 'get', return_value={'bars': {'QQQ': bars}}):
            self.assertEqual(len(self.provider.history('QQQ', '1M', self.at)), 10)

    def test_pagination_is_followed_and_bounded(self):
        pages = [{'bars': {'BTC/USD': minute_bars(self.at - timedelta(hours=3), 4)}, 'next_page_token': 'a'},
                 {'bars': {'BTC/USD': minute_bars(self.at - timedelta(hours=2), 4)}, 'next_page_token': 'b'},
                 {'bars': {'BTC/USD': minute_bars(self.at - timedelta(hours=1), 4)}}]
        with patch.object(self.provider, 'get', side_effect=pages) as get:
            result = self.provider.history('BTC/USD', '1M', self.at)
        self.assertEqual(len(result), 12)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args_list[1].args[1]['page_token'], 'a')
        # A provider that always returns a token must not loop forever.
        endless = {'bars': {'BTC/USD': minute_bars(self.at, 2)}, 'next_page_token': 'x'}
        with patch.object(self.provider, 'get', return_value=endless) as get:
            self.provider.history('BTC/USD', '1M', self.at)
        self.assertEqual(get.call_count, 6)

    def test_malformed_bars_are_dropped(self):
        bars = minute_bars(self.at, 3) + [{'t': stamp(self.at), 'o': None, 'h': 1, 'l': 1, 'c': 1}]
        with patch.object(self.provider, 'get', return_value={'bars': {'QQQ': bars}}):
            self.assertEqual(len(self.provider.history('QQQ', '1D', self.at)), 3)

    def test_bitcoin_uses_the_crypto_endpoint_without_feed_or_adjustment(self):
        with patch.object(self.provider, 'get', return_value={'bars': {'BTC/USD': []}}) as get:
            self.provider.history('BTC/USD', '1M', self.at)
        self.assertEqual(get.call_args.args[0], '/v1beta3/crypto/us/bars')
        self.assertNotIn('feed', get.call_args.args[1])
        self.assertNotIn('adjustment', get.call_args.args[1])


class ChartEndpointTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'APCA_API_KEY_ID': 'fake', 'APCA_API_SECRET_KEY': 'fake'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')
        self.at = now()

    def test_chart_works_with_the_session_stopped(self):
        self.assertFalse(self.engine.running)
        bars = minute_bars(self.at, 30)
        with patch.object(Alpaca, 'history', return_value=bars):
            result = self.engine.chart('QQQ', '1D')
        self.assertEqual(result['symbol'], 'QQQ')
        self.assertEqual(len(result['bars']), 30)
        self.assertEqual(result['summary']['bars'], 30)
        self.assertEqual(result['summary']['high'], max(b['h'] for b in bars))
        self.assertEqual(result['timeframe'], '1Min')

    def test_range_summary_reports_the_move_across_the_window(self):
        bars = minute_bars(self.at, 11, start_price=100.0)  # 100 -> 110
        with patch.object(Alpaca, 'history', return_value=bars):
            result = self.engine.chart('QQQ', '1Y')
        self.assertEqual(result['summary']['first'], 100)
        self.assertEqual(result['summary']['last'], 110)
        self.assertEqual(result['summary']['change_pct'], 10.0)

    def test_empty_history_is_reported_without_error(self):
        with patch.object(Alpaca, 'history', return_value=[]):
            result = self.engine.chart('QQQ', '5Y')
        self.assertEqual(result['bars'], [])
        self.assertEqual(result['summary'], {})

    def test_inputs_are_validated(self):
        with patch.object(Alpaca, 'history', return_value=[]):
            for symbol, span in [('../.env', '1D'), ('TOOLONG', '1D'), ('', '1D'), (None, '1D'), ('QQQ', '3Y'), ('QQQ', 'evil')]:
                with self.subTest(symbol=symbol, span=span), self.assertRaises(ValueError):
                    self.engine.chart(symbol, span)


if __name__ == '__main__':
    unittest.main()
