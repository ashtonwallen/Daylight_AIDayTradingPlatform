"""Position reviews outside US market hours use last-close references instead of refusing."""
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import Engine, DEFAULT
from market import Alpaca, now, stamp
from positions import consensus, holdings, validate_fill
from strategy import quote_problem, quote_structure_problem


def stock_fill(symbol='UMAC', quantity=10, price=100, stop=90):
    return validate_fill({'symbol': symbol, 'asset': 'stock', 'side': 'buy',
                          'quantity': quantity, 'price': price, 'stop': stop})


def closed_session(at):
    opens = at + timedelta(hours=17)
    return {'status': 'closed', 'is_open': False, 'entries_open': False, 'next_open': stamp(opens),
            'next_close': stamp(opens + timedelta(hours=6.5)), 'checked_at': stamp(at), 'source': 'cached', 'detail': None}


class QuoteClassificationTests(unittest.TestCase):
    def test_structure_and_age_are_separable(self):
        at = now()
        old = {'bp': 104, 'ap': 104.02, 'bs': 10, 'as': 10, 't': stamp(at - timedelta(hours=3))}
        self.assertIsNone(quote_structure_problem(old))          # coherent
        self.assertIsNotNone(quote_problem(old, at))             # but not current
        broken = dict(old, bp=0)
        self.assertIsNotNone(quote_structure_problem(broken))

    def test_closed_session_marks_are_references_and_bitcoin_is_not(self):
        at = now()
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(Path(directory) / 'journal.sqlite3')
            engine.snapshot = {'market_session': closed_session(at)}
            old = {'bp': 104, 'ap': 104.02, 'bs': 10, 'as': 10, 't': stamp(at - timedelta(hours=3))}
            state, detail = engine.quote_context({'asset': 'stock'}, old)
            self.assertEqual(state, 'reference')
            self.assertIn('Last quote before the close', detail)
            # Bitcoin trades around the clock, so an old quote there is a genuine failure.
            self.assertEqual(engine.quote_context({'asset': 'bitcoin'}, old)[0], 'unusable')
            # A closed session never rescues an incoherent quote.
            self.assertEqual(engine.quote_context({'asset': 'stock'}, dict(old, bp=0))[0], 'unusable')
            # While the market is open, a stale quote stays unusable.
            engine.snapshot = {'market_session': dict(closed_session(at), status='open', is_open=True, entries_open=True)}
            self.assertEqual(engine.quote_context({'asset': 'stock'}, old)[0], 'unusable')


class ClosedSessionConsensusTests(unittest.TestCase):
    def setUp(self):
        self.at = now()
        self.position = holdings([stock_fill()])['UMAC']
        self.old = {'bp': 104, 'ap': 104.02, 'bs': 10, 'as': 10, 't': stamp(self.at - timedelta(hours=3))}
        self.reports = [{'agent': name, 'status': 'complete', 'positions': [
            {'symbol': 'UMAC', 'action': 'HOLD', 'summary': 'Above cost basis.', 'factors': []}]}
            for name in ('analyst', 'risk')]

    def test_reference_quote_keeps_the_agreed_action(self):
        result = consensus(self.position, self.reports, self.old, [], 0, 0, self.at,
                           quote_state='reference', quote_note='Last quote before the close · Mon Sep 7, 4:00 PM ET')
        self.assertEqual(result['action'], 'HOLD')
        self.assertEqual(result['quote_state'], 'reference')
        self.assertTrue(any('reference price from the previous session' in c for c in result['checks']))

    def test_unusable_quote_still_forces_review(self):
        result = consensus(self.position, self.reports, self.old, [], 0, 0, self.at,
                           quote_state='unusable', quote_note='Missing or invalid bid/ask.')
        self.assertEqual(result['action'], 'REVIEW')

    def test_default_behaviour_is_unchanged_while_open(self):
        # Callers that pass no state keep the original strict gate.
        self.assertEqual(consensus(self.position, self.reports, self.old, [], 0, 0, self.at)['action'], 'REVIEW')
        fresh = dict(self.old, t=stamp(self.at))
        self.assertEqual(consensus(self.position, self.reports, fresh, [], 0, 0, self.at)['action'], 'HOLD')


class ClosedSessionReviewTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch.dict(os.environ, {'APCA_API_KEY_ID': 'test', 'APCA_API_SECRET_KEY': 'test', 'OPENAI_API_KEY': 'test'}),
                     patch.object(Alpaca, 'tracking_quotes', return_value=({}, []))):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')
        self.at = now()
        self.engine.running = True
        self.engine.scan_interval = 600
        self.engine.last_scan = stamp(self.at - timedelta(minutes=5))
        self.engine.manual_fills = [dict(stock_fill(), id='x', recorded_at=stamp(self.at))]
        self.engine.snapshot = {'market_session': closed_session(self.at), 'equity_open': False, 'instruments': {}, 'chains': {},
                                'tracking_quotes': {'UMAC': {'bp': 104, 'ap': 104.02, 'bs': 10, 'as': 10,
                                                             't': stamp(self.at - timedelta(hours=3))}}}

    def test_closed_session_position_is_still_valued(self):
        row = self.engine.manual_view()[0]
        self.assertEqual(row['mark_state'], 'reference')
        self.assertEqual(row['mark'], 104)
        self.assertEqual(row['unrealized_pnl'], 40)  # 10 shares from $100 to $104

    def test_review_completes_with_hold_and_supplies_price_history(self):
        history = {'UMAC': [{'t': stamp(self.at - timedelta(days=30 - i)), 'c': 100 + i} for i in range(30)]}
        self.engine.provider = Mock()
        self.engine.provider.daily_history.return_value = history
        captured = {}
        def review(context, progress=None, can_send=None):
            captured.update(context)
            return [{'agent': name, 'status': 'complete', 'positions': [
                {'symbol': 'UMAC', 'action': 'HOLD', 'summary': 'Trend intact on daily closes.', 'factors': ['Above the 20-session low.']}]}
                for name in ('Position analyst', 'Position risk reviewer')]
        with patch('position_engine.review_positions', side_effect=review):
            self.engine.position_review_job(self.engine.generation, len(self.engine.manual_fills),
                                            dict(self.engine.build_review_context()))
        result = self.engine.position_review
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['results'][0]['action'], 'HOLD')
        self.assertNotIn('Market context is stale; request a new review.', result['results'][0]['checks'])
        # The agents receive real price context rather than an empty intraday snapshot.
        self.assertEqual(captured['us_session']['status'], 'closed')
        self.assertEqual(captured['daily_history']['UMAC']['last_close'], 129)
        self.assertEqual(captured['daily_history']['UMAC']['change_5_session_pct'], 4.03)




class ClosedSessionContractTests(unittest.TestCase):
    """A researched thesis should still name a contract outside market hours."""

    def setUp(self):
        self.at = now()
        self.end = self.at.date() + timedelta(days=30)
        self.contract = 'NVDA' + self.end.strftime('%y%m%d') + 'C00100000'
        self.request = {'symbol': 'NVDA', 'instrument': 'call', 'horizon_end': str(self.end)}

    def provider(self, age_seconds, session):
        stamped = stamp(self.at - timedelta(seconds=age_seconds))
        option = {'bp': 2, 'ap': 2.05, 'bs': 10, 'as': 10, 't': stamped}
        provider = Mock()
        provider.option_feed = 'opra'
        provider.session.return_value = session
        provider.instrument.return_value = {'quote': {'bp': 100, 'ap': 100.01, 'bs': 10, 'as': 10, 't': stamped}}
        provider.get.return_value = {'snapshots': {self.contract: {'latestQuote': option, 'greeks': {'delta': .5}}}}
        return provider

    def test_closed_market_yields_a_labelled_last_close_candidate(self):
        from contracts import select
        result = select(self.provider(6 * 3600, closed_session(self.at)), self.request, 1000)
        self.assertEqual(result['status'], 'candidate')
        self.assertEqual(result['symbol'], self.contract)
        self.assertEqual(result['quote_state'], 'reference')
        self.assertEqual(result['cost_per_contract'], 205)
        self.assertIn('last quote before the close', result['reason'])

    def test_stale_quote_while_open_is_still_refused(self):
        from contracts import select
        open_session = dict(closed_session(self.at), status='open', is_open=True, entries_open=True)
        self.assertEqual(select(self.provider(6 * 3600, open_session), self.request, 1000)['status'], 'unavailable')

    def test_fresh_quotes_are_unchanged_and_unlabelled(self):
        from contracts import select
        result = select(self.provider(0, closed_session(self.at)), self.request, 1000)
        self.assertEqual(result['quote_state'], 'fresh')
        self.assertNotIn('last quote before the close', result['reason'])

    def test_incoherent_quote_is_never_rescued_by_a_closed_market(self):
        from contracts import select
        provider = self.provider(6 * 3600, closed_session(self.at))
        provider.instrument.return_value = {'quote': {'bp': 0, 'ap': 100.01, 'bs': 10, 'as': 10, 't': stamp(self.at)}}
        self.assertEqual(select(provider, self.request, 1000)['status'], 'unavailable')

    def test_a_provider_without_a_clock_degrades_to_strict(self):
        from contracts import select
        provider = self.provider(6 * 3600, None)
        provider.session.side_effect = RuntimeError('no clock')
        self.assertEqual(select(provider, self.request, 1000)['status'], 'unavailable')

    def test_one_sided_closing_quote_falls_back_to_the_daily_close(self):
        from contracts import select
        # After the bell a single-exchange feed often has no offer at all (ask 0), which is not a price.
        provider = self.provider(6 * 3600, closed_session(self.at))
        provider.instrument.return_value = {'quote': {'bp': 219.49, 'ap': 0, 'bs': 100, 'as': 0, 't': stamp(self.at)}}
        provider.daily_history.return_value = {'NVDA': [
            {'t': stamp(self.at - timedelta(days=4)), 'c': 225.0},
            {'t': stamp(self.at - timedelta(days=3)), 'c': 230.19}]}
        result = select(provider, {'symbol': 'NVDA', 'instrument': 'stock'}, 5000)
        self.assertEqual(result['status'], 'candidate')
        self.assertEqual(result['quote_state'], 'close')
        self.assertEqual(result['entry_reference'], 230.19)
        self.assertIn('last completed daily close', result['reason'])
        # A close is a reference, not a tradeable two-sided market, so no bid/ask is implied.
        self.assertNotIn('bid', result)
        self.assertEqual(provider.daily_history.call_args.args[0], ['NVDA'])

    def test_the_close_fallback_only_applies_when_the_market_is_shut(self):
        from contracts import select
        open_session = dict(closed_session(self.at), status='open', is_open=True, entries_open=True)
        provider = self.provider(0, open_session)
        provider.instrument.return_value = {'quote': {'bp': 219.49, 'ap': 0, 'bs': 100, 'as': 0, 't': stamp(self.at)}}
        provider.daily_history.return_value = {'NVDA': [{'t': stamp(self.at), 'c': 230.19}]}
        self.assertEqual(select(provider, {'symbol': 'NVDA', 'instrument': 'stock'}, 5000)['status'], 'unavailable')
        provider.daily_history.assert_not_called()

    def test_no_usable_daily_history_still_refuses(self):
        from contracts import select
        provider = self.provider(6 * 3600, closed_session(self.at))
        provider.instrument.return_value = {'quote': {'bp': 219.49, 'ap': 0, 'bs': 100, 'as': 0, 't': stamp(self.at)}}
        provider.daily_history.return_value = {'NVDA': [{'t': stamp(self.at), 'c': 0}]}
        self.assertEqual(select(provider, {'symbol': 'NVDA', 'instrument': 'stock'}, 5000)['status'], 'unavailable')

    def test_bitcoin_never_uses_a_reference_price(self):
        from contracts import select
        provider = self.provider(6 * 3600, closed_session(self.at))
        result = select(provider, {'symbol': 'BTC', 'instrument': 'bitcoin'}, 1000)
        self.assertEqual(result['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
