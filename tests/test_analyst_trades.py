import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyst

CONFIG = {'budget': 10000, 'risk_pct': 1, 'max_position_pct': 25}
SYMBOLS, IDS = {'NVDA', 'SPY'}, {'d:NVDA:prev_high'}


def shares(stop, target, symbol='NVDA'):
    return {'symbol': symbol, 'instrument': 'shares', 'reason': 'r', 'stop': stop, 'target': target, 'horizon_days': 14, 'based_on': []}


def quote(price=100.0, status='candidate', state='fresh'):
    def select(provider, request, budget):
        if status != 'candidate':
            return {'status': status, 'reason': 'Current underlying quotes are unavailable.'}
        if request['instrument'] == 'stock':
            return {'status': 'candidate', 'entry_reference': price, 'quote_state': state, 'quote_at': 't'}
        return {'status': 'candidate', 'symbol': 'NVDA261218C00100000', 'expiration': '2026-12-18', 'strike': 100.0,
                'premium_per_share': 2.0, 'cost_per_contract': 200.0, 'delta': .5, 'quote_state': state, 'quote_at': 't',
                'asked': request}
    return select


class CleanTests(unittest.TestCase):
    def test_only_known_symbols_instruments_and_signal_ids_survive(self):
        raw = {'trades': [
            {'symbol': 'NVDA', 'instrument': 'shares', 'reason': 'x' * 900, 'stop': 95, 'target': '110', 'based_on': ['d:NVDA:prev_high', 'made:up']},
            {'symbol': 'TSLA', 'instrument': 'shares', 'reason': 'not scanned'},
            {'symbol': 'SPY', 'instrument': 'short', 'reason': 'no shorts'},
            {'symbol': 'SPY', 'instrument': 'put', 'reason': 'ok', 'horizon_days': 500},
            {'symbol': 'SPY', 'instrument': 'call', 'reason': 'fourth'}]}
        trades = analyst.clean_trades(raw, SYMBOLS, IDS)
        self.assertEqual([(t['symbol'], t['instrument']) for t in trades], [('NVDA', 'shares')])   # only the first three are read
        self.assertEqual((trades[0]['stop'], trades[0]['target'], trades[0]['based_on'], len(trades[0]['reason'])),
                         (95.0, None, ['d:NVDA:prev_high'], 600))
        put = analyst.clean_trades({'trades': [raw['trades'][3]]}, SYMBOLS, IDS)[0]
        self.assertEqual(put['horizon_days'], 14)
        self.assertEqual(analyst.clean_trades({'trades': 'none'}, SYMBOLS, IDS), [])


class CheckTests(unittest.TestCase):
    def check(self, trade, select=None, available=10000, risk_left=300):
        return analyst.check_trade(object(), trade, CONFIG, available, risk_left, select=select or quote())

    def test_shares_are_repriced_at_the_live_quote_and_sized_to_the_limits(self):
        result = self.check(shares(98.0, 105.0))
        self.assertEqual((result['status'], result['entry'], result['quantity'], result['cost'], result['risk'], result['reward']),
                         ('ok', 100.0, 25, 2500.0, 50.0, 125.0))

    def test_shares_are_rejected_with_a_reason_when_levels_do_not_fit_the_live_price(self):
        self.assertIn('either side', self.check(shares(101.0, 105.0))['reason'])          # price already below the stop
        self.assertIn('0.2% to 10%', self.check(shares(80.0, 150.0))['reason'])
        self.assertIn('closer than the stop', self.check(shares(95.0, 102.0))['reason'])
        self.assertIn('no stop or target', self.check(shares(None, 105.0))['reason'])
        self.assertIn('does not fit', self.check(shares(98.0, 105.0), available=50)['reason'])
        self.assertIn('unavailable', self.check(shares(98.0, 105.0), quote(status='unavailable'))['reason'])

    def test_option_contract_comes_from_the_rules_and_the_whole_cost_is_the_risk(self):
        trade = {'symbol': 'NVDA', 'instrument': 'put', 'reason': 'r', 'stop': None, 'target': None, 'horizon_days': 10, 'based_on': []}
        asked = {}
        def select(provider, request, budget):
            asked.update(request)
            return quote()(provider, request, budget)
        result = analyst.check_trade(object(), trade, dict(CONFIG, risk_pct=5), 10000, 300, select=select)
        self.assertEqual((asked['instrument'], asked['symbol']), ('put', 'NVDA'))
        self.assertEqual((result['status'], result['quantity'], result['cost'], result['risk'], result['contract']),
                         ('ok', 1, 200.0, 200.0, 'NVDA261218C00100000'))

    def test_option_too_expensive_for_the_risk_setting_is_rejected(self):
        trade = {'symbol': 'NVDA', 'instrument': 'call', 'reason': 'r', 'stop': None, 'target': None, 'horizon_days': 10, 'based_on': []}
        result = analyst.check_trade(object(), trade, dict(CONFIG, risk_pct=1), 10000, 150, select=quote())
        self.assertEqual(result['status'], 'rejected'); self.assertIn('One contract costs $200.00', result['reason'])


if __name__ == '__main__':
    unittest.main()


class SecondPassTests(unittest.TestCase):
    def trades(self):
        return [{'symbol': 'NVDA', 'check': {'status': 'ok'}}, {'symbol': 'SPY', 'check': {'status': 'rejected'}}, {'symbol': 'AMD', 'check': {'status': 'ok'}}]

    def test_verdicts_attach_in_order_and_a_rejected_trade_cannot_be_kept(self):
        trades = self.trades()
        analyst.apply_tests(trades, {'tests': [
            {'symbol': 'NVDA', 'verdict': 'keep', 'confidence': 'medium', 'tested': 'held up', 'sources': ['https://example.com/a', 'javascript:alert(1)', 5]},
            {'symbol': 'SPY', 'verdict': 'keep', 'confidence': 'certain', 'tested': 'looks fine'},
            {'symbol': 'WRONG', 'verdict': 'keep', 'tested': 'mismatched row'}]})
        self.assertEqual(trades[0]['test'], {'verdict': 'keep', 'confidence': 'medium', 'tested': 'held up', 'sources': ['https://example.com/a']})
        self.assertEqual((trades[1]['test']['verdict'], trades[1]['test']['confidence']), ('drop', None))
        self.assertIn('did not pass the pricing', trades[1]['test']['tested'])
        self.assertNotIn('test', trades[2])

    def test_unusable_second_pass_changes_nothing(self):
        trades = self.trades()
        analyst.apply_tests(trades, 'nonsense')
        self.assertTrue(all('test' not in t for t in trades))


class JointRateTests(unittest.TestCase):
    def test_counts_only_sessions_where_every_signal_fired(self):
        import signals
        from tests.test_signals import flat, session_bars, weekdays
        bars = []
        for index, day in enumerate(weekdays(60)):
            closes = flat()[:20] + [100.6 + 0.02 * i for i in range(58)] if index % 3 == 2 else flat()[:-1] + [99.5]
            bars += session_bars(day, closes)
        both = signals.joint_rate(bars, ['prev_high', 'range_up'], 1)
        self.assertGreater(both['cases'], 10)
        self.assertEqual((both['went_the_trade_way_pct'], both['enough']), (100, False))
        self.assertEqual(signals.joint_rate(bars, ['prev_high', 'low_20'], 1)['cases'], 0)
        self.assertEqual(signals.joint_rate(bars, ['prev_high'], -1)['went_the_trade_way_pct'], 0)
