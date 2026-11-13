import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extras
from market import DataError, stamp

UTC = timezone.utc
THURSDAY = datetime(2026, 11, 12, 16, 0, tzinfo=UTC)   # 11:00 in New York, market open


def database():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE scans (id TEXT PRIMARY KEY, at TEXT, payload TEXT)')
    extras.init(db)
    return db


class Provider:
    stock_feed = 'bridge'

    def __init__(self, quotes=None, bars=None):
        self.quotes, self.bars, self.asked = quotes or {}, bars or [], None

    def live_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    def daily_history(self, symbols, at):
        return {s: [{'c': 100 + i} for i in range(10)] for s in symbols}

    def session(self, at):
        return {'status': 'open', 'is_open': True}

    def get(self, path, params=None, clock=False):
        self.asked = (path, params)
        return {'bars': {params['symbols']: self.bars}}


class UnusualHistoryTests(unittest.TestCase):
    def test_trading_day_rolls_back_before_the_open_and_over_weekends(self):
        self.assertEqual(extras.trading_day(THURSDAY), '2026-11-12')
        self.assertEqual(extras.trading_day(datetime(2026, 11, 12, 13, 0, tzinfo=UTC)), '2026-11-11')  # 8 am
        self.assertEqual(extras.trading_day(datetime(2026, 11, 14, 18, 0, tzinfo=UTC)), '2026-11-13')  # Saturday
        self.assertEqual(extras.trading_day(datetime(2026, 11, 16, 13, 0, tzinfo=UTC)), '2026-11-13')  # Monday 8 am

    def test_repeat_flags_are_counted_per_session_not_per_scan(self):
        db = database()
        row = {'symbol': 'NVDA261120C00170000', 'premium': 5}
        other = {'symbol': 'AAPL261120P00350000', 'premium': 9}
        extras.record_unusual(db, [row, other], THURSDAY - timedelta(days=1))
        extras.record_unusual(db, [row], THURSDAY)
        extras.record_unusual(db, [row], THURSDAY + timedelta(hours=2))   # same session again
        annotated = extras.with_history(db, [row, other], THURSDAY)
        self.assertEqual([r['days_flagged'] for r in annotated], [2, 1])
        repeats = extras.repeats(db, THURSDAY)
        self.assertEqual([(r['symbol'], r['days_flagged'], r['first_day'], r['last_day']) for r in repeats],
                         [('NVDA261120C00170000', 2, '2026-11-11', '2026-11-12')])
        self.assertEqual(extras.last_recorded(db, before='2026-11-12')[0], '2026-11-11')
        self.assertEqual(extras.last_recorded(database()), (None, []))


class BriefAndLiveTests(unittest.TestCase):
    def test_brief_ranks_by_size_of_move_and_lists_missing_symbols(self):
        provider = Provider({'SPY': {'last': 101.0, 'prev_close': 100.0}, 'NVDA': {'last': 94.0, 'prev_close': 100.0},
                             'BAD': {'last': None, 'prev_close': 100.0}})
        db = database()
        extras.record_unusual(db, [{'symbol': 'X', 'premium': 1}], THURSDAY - timedelta(days=1))
        result = extras.brief(provider, ['SPY', 'NVDA', 'BAD', 'GONE'], db, THURSDAY)
        self.assertEqual([(r['symbol'], r['gap_pct']) for r in result['moves']], [('NVDA', -6.0), ('SPY', 1.0)])
        self.assertEqual(result['moves'][0]['five_day_pct'], round((109 / 104 - 1) * 100, 2))
        self.assertEqual(result['missing'], ['BAD', 'GONE'])
        self.assertEqual(result['unusual_day'], '2026-11-11')

    def test_live_needs_a_source_that_can_supply_it(self):
        with self.assertRaises(DataError):
            extras.live(object(), ['SPY'])
        self.assertEqual(extras.live(Provider(), ['BTC/USD', ''])['quotes'], {})


def bar(minute, low, high):
    return {'t': stamp(THURSDAY + timedelta(minutes=minute)), 'o': low, 'h': high, 'l': low, 'c': high}


class ReplayTests(unittest.TestCase):
    def test_grade_reports_which_level_came_first(self):
        bars = [bar(-5, 80, 200), bar(1, 99, 101), bar(2, 100, 104.5), bar(3, 90, 91)]
        self.assertEqual(extras.grade(bars, THURSDAY, 95, 104), {'result': 'target', 'at': bars[2]['t'], 'minutes': 2})
        self.assertEqual(extras.grade(bars, THURSDAY, 99.5, 110)['result'], 'stop')
        self.assertEqual(extras.grade([bar(1, 90, 110)], THURSDAY, 95, 104)['result'], 'both')
        self.assertEqual(extras.grade([bar(1, 99, 101)], THURSDAY, 95, 104)['result'], 'neither')

    def test_replay_of_a_stock_idea_is_graded_and_an_option_is_not(self):
        provider = Provider(bars=[bar(1, 99, 101), bar(2, 100, 105)])
        idea = {'symbol': 'SPY', 'underlying': 'SPY', 'asset': 'stock', 'entry': 100, 'stop': 95, 'target': 104,
                'created_at': stamp(THURSDAY), 'direction': 'Buy'}
        result = extras.replay(provider, idea, THURSDAY + timedelta(hours=1))
        self.assertEqual(result['outcome']['result'], 'target')
        self.assertEqual(result['levels'], {'entry': 100, 'stop': 95, 'target': 104})
        self.assertEqual(provider.asked[0], '/v2/stocks/bars')
        option = dict(idea, symbol='SPY261120C00100000', asset='option')
        result = extras.replay(provider, option, THURSDAY + timedelta(hours=1))
        self.assertIsNone(result['outcome']); self.assertIsNone(result['levels']); self.assertIn('not graded', result['note'])

    def test_recent_ideas_keep_the_first_appearance_of_a_repeating_idea(self):
        db = database()
        for minute, idea_id in ((0, 'first'), (1, 'second')):
            at = stamp(THURSDAY + timedelta(minutes=minute))
            db.execute('INSERT INTO scans VALUES (?, ?, ?)', (idea_id, at, json.dumps({'ideas': [
                {'id': idea_id, 'symbol': 'SPY', 'direction': 'Buy', 'created_at': at, 'entry': 1, 'stop': 0, 'target': 2}]})))
        ideas = extras.recent_ideas(db)
        self.assertEqual([i['id'] for i in ideas], ['first'])
        self.assertEqual(extras.find_idea(db, 'second')['symbol'], 'SPY')
        self.assertIsNone(extras.find_idea(db, 'nope'))


class WhatIfTests(unittest.TestCase):
    def test_model_reproduces_a_price_from_its_own_implied_volatility(self):
        price = extras.option_price('C', 100, 105, 0.25, 0.30)
        self.assertAlmostEqual(extras.implied_vol('C', 100, 105, 0.25, price), 0.30, places=4)
        # Put-call parity holds for the model.
        put = extras.option_price('P', 100, 105, 0.25, 0.30)
        self.assertAlmostEqual(price - put, 100 - 105 * 2.718281828459045 ** (-extras.RATE * 0.25), places=6)
        self.assertEqual(extras.option_price('C', 110, 105, 0, 0.3), 5)
        self.assertIsNone(extras.implied_vol('C', 100, 105, 0.25, 500))

    def test_stock_moves_are_linear_and_a_long_call_loses_value_with_time(self):
        provider = Provider({'SPY': {'last': 100.0, 'prev_close': 99.0}})
        mark = extras.option_price('C', 100, 100, 30 / 365, 0.25)
        expiry = (THURSDAY + timedelta(days=30)).strftime('%y%m%d')
        positions = [{'symbol': 'SPY', 'underlying': 'SPY', 'asset': 'stock', 'quantity': 10, 'multiplier': 1, 'mark': 100, 'source': 'logged'},
                     {'symbol': f'SPY{expiry}C00100000', 'underlying': 'SPY', 'asset': 'option', 'quantity': 2, 'multiplier': 100, 'mark': mark, 'source': 'paper'},
                     {'symbol': 'ZZZ', 'underlying': 'ZZZ', 'asset': 'stock', 'quantity': 1, 'multiplier': 1, 'mark': None, 'source': 'logged'}]
        rows = extras.what_if(provider, positions, THURSDAY)['rows']
        self.assertEqual([s['today'] for s in rows[0]['scenarios']], [-50.0, 0.0, 50.0])
        call = rows[1]
        self.assertAlmostEqual(call['implied_vol_pct'], 25.0, delta=1.5)
        down, flat, up = call['scenarios']
        self.assertLess(down['today'], 0); self.assertAlmostEqual(flat['today'], 0, delta=0.5); self.assertGreater(up['today'], 0)
        self.assertLess(flat['later'], flat['today'])          # a week of time decay
        self.assertGreater(-down['today'], 0); self.assertLess(-down['today'], call['value'])   # cannot lose more than it is worth
        self.assertIn('No current share price', rows[2]['note'])


if __name__ == '__main__':
    unittest.main()
