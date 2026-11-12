import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import Bridge, compact, live_provider, merge, padded
from market import Alpaca, DataError, stamp
from strategy import quote_problem

UTC = timezone.utc
ENV = {'MARKET_DATA_URL': 'http://127.0.0.1:17860', 'APCA_API_KEY_ID': '', 'APCA_API_SECRET_KEY': ''}


def ms(dt):
    return int(dt.timestamp() * 1000)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, ENV)
        patcher.start(); self.addCleanup(patcher.stop)
        self.at = datetime(2026, 11, 12, 15, 30, 20, tzinfo=UTC)
        self.bridge = Bridge()
        self.calls = []

    def serve(self, answers):
        def fetch(path, params):
            self.calls.append((path, params))
            return answers[path]
        return patch.object(self.bridge, 'fetch', side_effect=fetch)

    def test_factory_picks_bridge_only_when_configured(self):
        self.assertIsInstance(live_provider(), Bridge)
        with patch.dict(os.environ, {'MARKET_DATA_URL': '', 'APCA_API_KEY_ID': 'k', 'APCA_API_SECRET_KEY': 's'}):
            provider = live_provider()
            self.assertIs(type(provider), Alpaca)

    def test_bridge_must_be_on_this_computer(self):
        with patch.dict(os.environ, {'MARKET_DATA_URL': 'https://example.com'}):
            with self.assertRaises(DataError):
                Bridge()

    def test_option_symbols_round_trip(self):
        self.assertEqual(compact('SPY   261120C00565000'), 'SPY261120C00565000')
        self.assertEqual(padded('SPY261120C00565000'), 'SPY   261120C00565000')
        self.assertIsNone(compact('SPY'))

    def test_instrument_has_alpaca_shapes_and_passes_the_quote_check(self):
        minute = self.at.replace(second=0, microsecond=0)
        bars = [{'t': ms(minute - timedelta(minutes=m)), 'o': 1, 'h': 2, 'l': .5, 'c': 1.5, 'v': 10} for m in (2, 1, 0)]
        answers = {'/history': {'bars': bars},
                   '/quotes': {'quotes': {'SPY': {'bid': 565.1, 'ask': 565.12, 'bidSize': 300, 'askSize': 400,
                                                  'quoteTime': ms(self.at - timedelta(seconds=2))}}}}
        with self.serve(answers):
            instrument = self.bridge.instrument('SPY', self.at)
        # The still-forming minute is dropped, as it is with Alpaca.
        self.assertEqual([b['t'] for b in instrument['bars']],
                         [stamp(minute - timedelta(minutes=2)), stamp(minute - timedelta(minutes=1))])
        self.assertEqual(instrument['quote']['bp'], 565.1)
        self.assertEqual(instrument['feed'], 'BRIDGE')
        self.assertIsNone(quote_problem(instrument['quote'], self.at))
        self.assertEqual(self.calls[0][1]['frequency'], '1m')

    def test_missing_quote_fails_the_check_instead_of_inventing_a_price(self):
        with self.serve({'/history': {'bars': []}, '/quotes': {'quotes': {}}}):
            instrument = self.bridge.instrument('ZZZZ', self.at)
        self.assertIsNotNone(quote_problem(instrument['quote'], self.at))

    def test_option_snapshots_are_filtered_and_renamed(self):
        def contract(strike, kind='CALL'):
            return {'symbol': f'SPY   261127{kind[0]}{int(strike * 1000):08d}', 'strike': strike, 'bid': 1.2,
                    'ask': 1.24, 'bidSize': 5, 'askSize': 6, 'delta': .5, 'iv': 18.0,
                    'quoteTime': ms(self.at)}
        with self.serve({'/chain': {'contracts': [contract(540.0), contract(565.0), contract(600.0)]}}):
            rows = self.bridge.options('SPY', 565.0, self.at)
        self.assertEqual(list(rows), ['SPY261127C00565000'])
        row = rows['SPY261127C00565000']
        self.assertEqual(row['greeks']['delta'], .5)
        self.assertAlmostEqual(row['impliedVolatility'], .18)
        self.assertEqual(row['latestQuote']['ap'], 1.24)
        self.assertEqual(self.calls[0][1]['start'], '2026-11-19')

    def test_clock_and_session(self):
        answers = {'/clock': {'timestamp': stamp(self.at), 'isOpen': True,
                              'nextOpen': '2026-11-13T14:30:00Z', 'nextClose': '2026-11-12T21:00:00Z'}}
        with self.serve(answers):
            session = self.bridge.session(self.at)
        self.assertEqual(session['status'], 'open')
        self.assertTrue(session['entries_open'])

    def test_hourly_bars_are_built_from_half_hours(self):
        start = datetime(2026, 11, 12, 14, 30, tzinfo=UTC)
        halves = [{'t': stamp(start + timedelta(minutes=30 * i)), 'o': i, 'h': i + 1, 'l': i - 1, 'c': i + .5, 'v': 10}
                  for i in range(3)]
        hours = merge(halves, 2)
        self.assertEqual(len(hours), 2)
        self.assertEqual((hours[0]['o'], hours[0]['h'], hours[0]['l'], hours[0]['c'], hours[0]['v']), (0, 2, -1, 1.5, 20))

    def test_bitcoin_and_news_without_alpaca_keys(self):
        self.assertIsNone(self.bridge.alpaca)
        self.assertEqual(self.bridge.get('/v1beta1/news', {}), {'news': []})
        with self.assertRaises(DataError):
            self.bridge.get('/v1beta3/crypto/us/bars', {'symbols': 'BTC/USD'})

    def test_tracking_quotes_for_an_option_position(self):
        answers = {'/quotes': {'quotes': {'SPY   261127C00565000': {'bid': 1.2, 'ask': 1.24, 'bidSize': 1,
                                                                    'askSize': 1, 'quoteTime': ms(self.at)}}}}
        with self.serve(answers):
            quotes, errors = self.bridge.tracking_quotes([{'symbol': 'SPY261127C00565000', 'asset': 'option'}])
        self.assertEqual(errors, [])
        self.assertEqual(quotes['SPY261127C00565000']['bp'], 1.2)
        self.assertEqual(self.calls[0][1]['symbols'], 'SPY   261127C00565000')


if __name__ == '__main__':
    unittest.main()
