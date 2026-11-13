import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import signals
from market import DataError, NY

CONFIG = {'budget': 10000, 'risk_pct': 1, 'max_position_pct': 25}


def session_bars(day, closes, volume=1000):
    """Five-minute bars for one New York session from a list of closes, starting 9:30."""
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    bars, previous = [], closes[0]
    for i, close in enumerate(closes):
        bars.append({'t': int((start + timedelta(minutes=5 * i)).timestamp() * 1000), 'o': previous,
                     'h': max(previous, close) + .05, 'l': min(previous, close) - .05, 'c': close, 'v': volume})
        previous = close
    return bars


def weekdays(count, first=datetime(2026, 3, 2)):
    day, out = first, []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def flat(level=100.0, length=signals.SLOTS):
    return [level + (0.1 if i % 2 else -0.1) for i in range(length)]


class SessionTests(unittest.TestCase):
    def test_bars_are_grouped_into_regular_sessions_only(self):
        day = datetime(2026, 3, 2)
        bars = session_bars(day, flat())
        early = dict(bars[0], t=bars[0]['t'] - 3600_000)       # 8:30, before the open
        sessions = signals.sessions_from([early] + bars + [{'t': 1, 'o': None}])
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0]['date'], len(sessions[0]['bars']), sessions[0]['bars'][0]['slot']), ('2026-03-02', signals.SLOTS, 0))

    def test_round_number_step_scales_with_price(self):
        self.assertEqual([signals.round_step(p) for p in (40, 350, 5000)], [5, 10, 50])


class DetectTests(unittest.TestCase):
    def setUp(self):
        days = weekdays(22)
        self.history = signals.sessions_from([b for d in days[:21] for b in session_bars(d, flat())])
        self.day = days[21]
        self.ctx = signals.context(self.history)

    def detect(self, closes):
        return {e['key']: e for e in signals.detect(signals.sessions_from(session_bars(self.day, closes))[0], self.ctx)}

    def test_crossing_yesterdays_high_and_the_opening_range_fire_once_each(self):
        closes = flat()[:20] + [101.0, 101.5, 100.0, 101.6] + [101.6] * 54
        events = self.detect(closes)
        self.assertEqual(events['prev_high']['slot'], 20)
        self.assertAlmostEqual(events['prev_high']['level'], self.ctx['prev_high'])
        self.assertEqual(events['range_up']['slot'], 20)
        self.assertEqual(events['high_20']['direction'], 1)
        self.assertIn('late_high', events)
        self.assertNotIn('prev_low', events)

    def test_gap_and_fill(self):
        closes = [101.0] * 10 + [99.9] + [99.9] * 67
        events = self.detect(closes)
        self.assertEqual(events['gap_up']['slot'], 0)
        self.assertEqual(events['gap_fill_down']['slot'], 10)
        self.assertNotIn('prev_close_down', events)

    def test_quiet_day_fires_nothing_directional(self):
        events = self.detect(flat())
        self.assertFalse({k for k in events if signals.LABELS[k][1]} - {'round_up', 'round_down', 'vwap_up', 'vwap_down', 'prev_close_up', 'prev_close_down'})


class BaseRateTests(unittest.TestCase):
    def describe(self, returns, ordinary, direction=1):
        table = {'prev_high': [{'slot': 10, 'to_close': r, 'half_hour': r / 2} for r in returns]}
        return signals.describe({'key': 'prev_high', 'slot': 10, 'price': 100, 'level': 99, 'direction': direction},
                                table, {10: ordinary}, 180)

    def test_verdicts(self):
        coin = [0.01, -0.01] * 50
        strong = self.describe([0.01] * 32 + [-0.01] * 8, coin)
        self.assertEqual((strong['verdict'], strong['stats']['continued_pct'], strong['stats']['usual_pct']), ('continues', 80, 50))
        self.assertEqual(signals.lean_direction(strong), 1)
        fade = self.describe([-0.01] * 32 + [0.01] * 8, coin)
        self.assertEqual((fade['verdict'], signals.lean_direction(fade)), ('fades', -1))
        self.assertEqual(self.describe([0.01] * 21 + [-0.01] * 19, coin)['verdict'], 'ordinary')
        few = self.describe([0.01] * 10, coin)
        self.assertEqual(few['verdict'], 'few'); self.assertIn('too few', few['lean'])
        down_fades = self.describe([0.01] * 32 + [-0.01] * 8, coin, direction=-1)
        self.assertEqual((down_fades['verdict'], signals.lean_direction(down_fades)), ('fades', 1))

    def test_plan_is_sized_to_the_risk_setting_and_only_for_fresh_upward_leans(self):
        up = {'verdict': 'continues', 'direction': 1, 'minutes_ago': 5}
        plan = signals.plan(up, 100.0, 0.02, CONFIG, 10000, 300)
        self.assertEqual((plan['entry'], plan['stop'], plan['target'], plan['quantity']), (100.0, 99.0, 102.0, 25))
        self.assertEqual((plan['cost'], plan['risk'], plan['reward']), (2500.0, 25.0, 50.0))   # capped by max position
        self.assertIn('fired more than', signals.plan(dict(up, minutes_ago=45), 100.0, 0.02, CONFIG, 10000, 300)['note'])
        self.assertEqual(signals.plan({'verdict': 'continues', 'direction': -1, 'minutes_ago': 5}, 100.0, 0.02, CONFIG, 10000, 300)['side'], 'down')
        self.assertIsNone(signals.plan({'verdict': 'ordinary', 'direction': 1, 'minutes_ago': 5}, 100.0, 0.02, CONFIG, 10000, 300))
        self.assertIn('does not fit', signals.plan(up, 100.0, 0.02, CONFIG, 50, 300)['note'])

    def test_analyse_finds_a_pattern_that_was_built_into_the_history(self):
        # Every third day breaks yesterday's high at 11:10 and keeps rising; other days drift nowhere.
        days, bars = weekdays(99), []
        for index, day in enumerate(days):
            if index % 3 == 2:
                closes = flat()[:20] + [100.6 + 0.02 * i for i in range(58)]
            else:
                closes = flat()[:-1] + [99.5]      # ordinary days drift off into the close
            bars += session_bars(day, closes)
        result = signals.analyse('TEST', bars)
        event = next(e for e in result['events'] if e['key'] == 'prev_high')
        self.assertEqual(result['date'], str(days[-1].date()))
        self.assertGreaterEqual(event['stats']['cases'], signals.MIN_CASES)
        self.assertEqual(event['verdict'], 'continues')
        self.assertEqual(event['stats']['continued_pct'], 100)
        self.assertEqual((event['time'], event['symbol'], event['id']), ('11:15', 'TEST', f'{days[-1].date()}:TEST:prev_high'))

    def test_short_history_is_refused(self):
        with self.assertRaises(DataError):
            signals.analyse('TEST', session_bars(datetime(2026, 3, 2), flat()))


class EngineTests(unittest.TestCase):
    def test_history_is_fetched_once_a_day_and_a_repeat_scan_is_served_from_cache(self):
        days = weekdays(40, datetime(2026, 9, 1))
        bars = [b for d in days for b in session_bars(d, flat())]
        calls = []

        class Provider:
            def five_minute_bars(self, symbol, start):
                calls.append(start)
                return [b for b in bars if b['t'] >= start.timestamp() * 1000]

        engine = signals.Signals()
        at = datetime(days[-1].year, days[-1].month, days[-1].day, 15, 0, tzinfo=NY)
        first = engine.scan(Provider(), ['TEST'], at)
        self.assertEqual(first['errors'], [])
        self.assertEqual(len(calls), 2)                 # long history, then the recent days
        self.assertIs(engine.scan(Provider(), ['TEST'], at), first)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
