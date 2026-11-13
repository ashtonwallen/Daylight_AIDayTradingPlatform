import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import optionsview
import schwabdata
from market import Alpaca, DataError

ENV = {'SCHWAB_APP_KEY': 'key', 'SCHWAB_APP_SECRET': 'secret', 'MARKET_DATA_URL': '',
       'APCA_API_KEY_ID': 'a', 'APCA_API_SECRET_KEY': 'b'}


class SchwabLoginTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        for p in (patch.dict(os.environ, ENV), patch.object(schwabdata, 'TOKEN_FILE', Path(folder.name) / 'token.json')):
            p.start(); self.addCleanup(p.stop)

    def test_authorize_url_carries_key_and_callback_only(self):
        url = schwabdata.authorize_url()
        self.assertIn('client_id=key', url); self.assertIn('redirect_uri=https%3A%2F%2F127.0.0.1', url)
        self.assertNotIn('secret', url)

    def test_connect_exchanges_the_code_and_status_never_exposes_tokens(self):
        sent = {}
        def answer(form):
            sent.update(form)
            return {'access_token': 'ACCESS', 'refresh_token': 'REFRESH', 'expires_in': 1800}
        with patch.object(schwabdata, 'token_request', side_effect=answer):
            schwabdata.connect('https://127.0.0.1/?code=C0.abc%40&session=x')
        self.assertEqual((sent['grant_type'], sent['code']), ('authorization_code', 'C0.abc@'))
        status = schwabdata.status()
        self.assertTrue(status['connected'])
        self.assertAlmostEqual(status['login_expires_in_hours'], 168, delta=0.1)
        self.assertNotIn('ACCESS', json.dumps(status)); self.assertNotIn('REFRESH', json.dumps(status))
        self.assertIsInstance(schwabdata.live_provider(), schwabdata.Schwab)
        self.assertEqual(schwabdata.source_name(), 'schwab')

    def test_address_without_a_code_is_rejected(self):
        with self.assertRaises(ValueError):
            schwabdata.connect('https://127.0.0.1/?error=access_denied')

    def test_access_token_refreshes_when_stale_and_stops_after_seven_days(self):
        schwabdata.write_token({'access_token': 'OLD', 'refresh_token': 'R', 'access_expires_at': 0, 'login_at': time.time()})
        with patch.object(schwabdata, 'token_request', return_value={'access_token': 'NEW', 'expires_in': 1800}) as request:
            self.assertEqual(schwabdata.access_token(), 'NEW')
            self.assertEqual(schwabdata.access_token(), 'NEW')
            self.assertEqual(request.call_count, 1)
        schwabdata.write_token({'access_token': 'OLD', 'refresh_token': 'R', 'access_expires_at': 0, 'login_at': time.time() - 8 * 86400})
        with self.assertRaises(DataError):
            schwabdata.access_token()
        self.assertFalse(schwabdata.status()['connected'])

    def test_without_a_login_alpaca_stays_the_source(self):
        self.assertIs(type(schwabdata.live_provider()), Alpaca)
        self.assertEqual(schwabdata.source_name(), 'alpaca')

    def test_direct_provider_shapes_schwab_answers(self):
        provider = schwabdata.Schwab(token=lambda: 'T')
        at = datetime(2026, 11, 12, 15, 30, 20, tzinfo=timezone.utc)
        answers = {
            '/marketdata/v1/pricehistory': {'candles': [{'datetime': 1794497280000, 'open': 1, 'high': 2, 'low': .5, 'close': 1.5, 'volume': 9}]},
            '/marketdata/v1/quotes': {'SPY': {'quote': {'bidPrice': 5.0, 'askPrice': 5.01, 'bidSize': 100, 'askSize': 200, 'quoteTime': 1794497418000}}},
            '/marketdata/v1/markets': {'equity': {'EQ': {'sessionHours': {'regularMarket': [{'start': '2026-11-12T09:30:00-05:00', 'end': '2026-11-12T16:00:00-05:00'}]}}}},
        }
        asked = []
        def api(path, params, ttl):
            asked.append((path, params)); return answers[path]
        with patch.object(provider, 'api', side_effect=api), patch.object(schwabdata, 'now', return_value=at):
            instrument = provider.instrument('SPY', at)
            session = provider.session(at)
        self.assertEqual(instrument['quote']['bp'], 5.0)
        self.assertEqual(instrument['feed'], 'SCHWAB')
        self.assertEqual(len(instrument['bars']), 1)
        self.assertEqual(session['status'], 'open')
        self.assertEqual(asked[0][1]['frequencyType'], 'minute')


def contract(kind, strike, volume, oi, dte=9, expiration='2099-11-21', bid=1.0, ask=1.1):
    return {'symbol': f'NVDA  991121{kind[0]}{int(strike * 1000):08d}', 'type': kind, 'strike': strike, 'expiration': expiration,
            'dte': dte, 'bid': bid, 'ask': ask, 'mark': (bid + ask) / 2, 'last': bid, 'volume': volume, 'openInterest': oi,
            'iv': 40.0, 'delta': .5 if kind == 'CALL' else -.5}


class Provider:
    def __init__(self, contracts):
        self.contracts = contracts

    def option_chain(self, symbol, start, end, strikes=40):
        return {'underlyingPrice': 160.0, 'contracts': self.contracts}


class OptionsViewTests(unittest.TestCase):
    def test_chain_pairs_calls_and_puts_and_defaults_to_the_next_expiration(self):
        provider = Provider([contract('CALL', 160.0, 10, 50), contract('PUT', 160.0, 20, 60), contract('CALL', 165.0, 5, 5),
                             contract('CALL', 160.0, 1, 1, dte=40, expiration='2099-12-22')])
        view = optionsview.chain(provider, ' nvda ')
        self.assertEqual(view['expiration'], '2099-11-21')
        self.assertEqual(view['expirations'], ['2099-11-21', '2099-12-22'])
        self.assertEqual([r['strike'] for r in view['rows']], [160.0, 165.0])
        self.assertEqual(view['rows'][0]['put']['open_interest'], 60)
        self.assertIsNone(view['rows'][1]['put'])
        self.assertEqual(view['totals']['call_volume'], 15)
        self.assertEqual(optionsview.chain(provider, 'NVDA', '2099-12-22')['expiration'], '2099-12-22')

    def test_unusual_needs_volume_well_above_open_interest_and_skips_expiring_contracts(self):
        rows = optionsview.unusual([contract('CALL', 170.0, 5000, 400), contract('PUT', 150.0, 5000, 4900),
                                    contract('CALL', 175.0, 300, 10), contract('CALL', 180.0, 9000, 5, dte=1)], 160.0, 'NVDA')
        self.assertEqual([r['strike'] for r in rows], [170.0])
        self.assertEqual((rows[0]['ratio'], rows[0]['premium'], rows[0]['distance_pct'], rows[0]['symbol']),
                         (12.5, 525000, 6.2, 'NVDA991121C00170000'))

    def test_views_that_need_schwab_say_so_on_alpaca(self):
        with self.assertRaises(DataError):
            optionsview.chain(object(), 'NVDA')
        with self.assertRaises(ValueError):
            optionsview.chain(Provider([]), 'NVDA1')

    def test_book_reports_spread_and_mid(self):
        class Book:
            def order_book(self, symbol):
                return {'status': 'live', 'venue': 'NASDAQ', 'ageSeconds': .4,
                        'bids': [{'price': 10.0, 'size': 300, 'exchanges': [{'exchange': 'ARCX', 'size': 300}]}],
                        'asks': [{'price': 10.02, 'size': 100, 'exchanges': []}]}
        view = optionsview.book(Book(), 'f')
        self.assertEqual((view['spread'], view['mid'], view['bids'][0]['venues']), (0.02, 10.01, ['ARCX']))


if __name__ == '__main__':
    unittest.main()
