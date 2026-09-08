"""Market-clock caching, quiet closed-market scanning and two-horizon research conclusions."""
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app import Engine, DEFAULT
from market import Alpaca, describe_et, now, stamp
from research import decode_response
from test_core import bullish_snapshot


def clock(at, is_open, next_open, next_close):
    return {'timestamp': stamp(at), 'is_open': is_open, 'next_open': stamp(next_open), 'next_close': stamp(next_close)}


class ClockCacheTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'APCA_API_KEY_ID': 'fake', 'APCA_API_SECRET_KEY': 'fake'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = Alpaca()
        self.at = now()

    def test_closed_clock_is_not_refetched_until_the_next_open(self):
        opens = self.at + timedelta(hours=17)
        state = {'is_open': False}
        def clock_now(*args, **kwargs):
            # The real clock stamps each response at request time.
            at = clock_now.at
            return clock(at, state['is_open'], opens if not state['is_open'] else opens + timedelta(days=1), opens + timedelta(hours=6, minutes=30))
        def session_at(at):
            clock_now.at = at
            return self.provider.session(at)
        with patch.object(self.provider, 'get', side_effect=clock_now) as get:
            first = session_at(self.at)
            self.assertEqual(first['status'], 'closed')
            self.assertEqual(first['source'], 'fetched')
            for minutes in (1, 5, 30, 59):
                again = session_at(self.at + timedelta(minutes=minutes))
                self.assertEqual((again['status'], again['source']), ('closed', 'cached'), minutes)
            self.assertEqual(get.call_count, 1)
            # An hour later the cache is refreshed even though the boundary has not passed.
            session_at(self.at + timedelta(minutes=61))
            self.assertEqual(get.call_count, 2)
            # Once the next open arrives the clock is asked again.
            state['is_open'] = True
            reopened = session_at(opens + timedelta(seconds=1))
            self.assertEqual(get.call_count, 3)
            self.assertEqual(reopened['status'], 'open')
            self.assertTrue(reopened['entries_open'])

    def test_closing_window_blocks_entries_but_keeps_market_data(self):
        close = self.at + timedelta(minutes=10)
        with patch.object(self.provider, 'get', return_value=clock(self.at, True, self.at + timedelta(days=1), close)):
            session = self.provider.session(self.at)
        self.assertEqual(session['status'], 'closing')
        self.assertFalse(session['entries_open'])
        snapshot = bullish_snapshot(self.at)
        snapshot['equity_open'] = False
        snapshot['simulated'] = False
        from strategy import make_ideas
        ideas, _, rejected = make_ideas(snapshot, dict(DEFAULT, require_research=False), [], self.at, 10000, 300)
        self.assertFalse(any(i['asset'] in ('stock', 'option') for i in ideas))
        self.assertTrue(any('final 15 minutes' in r['reason'] for r in rejected))

    def test_missing_boundaries_are_not_cached(self):
        with patch.object(self.provider, 'get', return_value={'timestamp': stamp(self.at), 'is_open': False}) as get:
            self.provider.session(self.at)
            self.provider.session(self.at + timedelta(minutes=1))
            self.assertEqual(get.call_count, 2)

    def test_describe_et_is_readable(self):
        self.assertEqual(describe_et('2026-09-08T09:30:00-04:00'), 'Tue Sep 8, 9:30 AM ET')
        self.assertIsNone(describe_et(None))


class QuietClosedMarketTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch('app.Engine.launch_research'), patch.object(Alpaca, 'tracking_quotes', return_value=({}, [])),
                     patch.dict(os.environ, {'APCA_API_KEY_ID': 'test', 'APCA_API_SECRET_KEY': 'test', 'OPENAI_API_KEY': 'test'})):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def closed_snapshot(self, at, with_bitcoin=False):
        snapshot = bullish_snapshot(at)
        opens = at + timedelta(hours=17)
        snapshot.update(simulated=False, equity_open=False, market_open=False, chains={},
                        market_session={'status': 'closed', 'is_open': False, 'entries_open': False,
                                        'next_open': stamp(opens), 'next_close': stamp(opens + timedelta(hours=6.5)),
                                        'checked_at': stamp(at), 'source': 'cached', 'detail': None})
        snapshot['instruments'] = {'BTC/USD': snapshot['instruments']['BTC/USD']} if with_bitcoin else {}
        return snapshot

    def test_closed_session_is_logged_once_and_scans_slow_down(self):
        self.engine.start(dict(DEFAULT, assets=['stock', 'option']))
        with patch.object(self.engine.provider, 'collect', side_effect=lambda config, at: self.closed_snapshot(at)):
            for _ in range(3):
                self.engine.next_scan = None
                self.engine.scan()
        events = [e for e in self.engine.activity if e['component'] == 'Market session']
        self.assertEqual(len(events), 1)
        self.assertIn('reopens', events[0]['message'])
        self.assertEqual(events[0]['status'], 'idle')
        self.assertFalse(any('session closed' in e['message'] for e in self.engine.activity if e['component'] == 'Data checks'))
        self.assertEqual(self.engine.scan_interval, 600)
        state = self.engine.state()
        self.assertEqual(state['market_session']['status'], 'closed')
        self.assertNotIn('US stock/options session closed. Bitcoin can continue running.', state['errors'])

    def test_bitcoin_keeps_the_minute_cadence_and_reopen_is_traced(self):
        self.engine.start(DEFAULT)
        with patch.object(self.engine.provider, 'collect', side_effect=lambda config, at: self.closed_snapshot(at, with_bitcoin=True)):
            self.engine.scan()
        self.assertEqual(self.engine.scan_interval, 60)
        with patch.object(self.engine.provider, 'collect', side_effect=lambda config, at: bullish_snapshot(at)):
            self.engine.next_scan = None
            self.engine.scan()
        statuses = [e['message'] for e in self.engine.activity if e['component'] == 'Market session']
        self.assertEqual(len(statuses), 2)
        self.assertIn('open', statuses[1])

    def test_repeated_warnings_are_traced_once(self):
        self.engine.start(DEFAULT)
        def collect(config, at):
            snapshot = bullish_snapshot(at)
            snapshot['errors'] = ['News unavailable: Alpaca /v1beta1/news: HTTP 500.']
            return snapshot
        with patch.object(self.engine.provider, 'collect', side_effect=collect):
            for _ in range(3):
                self.engine.next_scan = None
                self.engine.scan()
        warnings = [e for e in self.engine.activity if e['component'] == 'Data checks' and e['status'] == 'warning']
        self.assertEqual(len(warnings), 1)
        with patch.object(self.engine.provider, 'collect', side_effect=lambda config, at: bullish_snapshot(at)):
            self.engine.next_scan = None
            self.engine.scan()
        cleared = [e for e in self.engine.activity if e['component'] == 'Data checks' and e['status'] == 'complete']
        self.assertEqual(len(cleared), 1)

    def test_connection_test_reserves_a_request_and_reports_outcome(self):
        self.engine.set_ai_paused(True)
        with patch('app.ai.complete', return_value={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}]}):
            self.engine.request_ai_test()
            for _ in range(200):
                if not self.engine.ai_testing:
                    break
                import time; time.sleep(.01)
        self.assertEqual(self.engine.ai_test['status'], 'ok')
        self.assertIn('OK', self.engine.ai_test['message'])
        self.assertEqual(self.engine.ai_usage()['requests'], 1)


class ConclusionTests(unittest.TestCase):
    def response(self, payload):
        return {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'action': {'sources': [{'url': 'https://example.com/source'}]}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(payload)}]}]}

    def test_both_horizons_are_kept_when_sourced(self):
        at = now()
        evidence = {'symbol': 'SPY', 'summary': 'Guidance raised', 'url': 'https://example.com/source', 'title': 'Filing',
                    'published_at': stamp(at), 'sentiment': 'bullish', 'event_risk': False}
        report = decode_response(self.response({'coverage': 'ok', 'conclusion': 'Intraday view.',
                                                'long_term_conclusion': 'Months view.', 'evidence': [evidence]}), 'News & filings', at)
        self.assertEqual(report['conclusion'], 'Intraday view.')
        self.assertEqual(report['long_term_conclusion'], 'Months view.')
        stale = dict(evidence, published_at=stamp(at - timedelta(days=30)))
        report = decode_response(self.response({'coverage': 'ok', 'conclusion': 'Intraday view.',
                                                'long_term_conclusion': 'Months view.', 'evidence': [stale]}), 'News & filings', at)
        self.assertIn('No conclusion', report['conclusion'])
        self.assertEqual(report['long_term_conclusion'], 'Months view.')
        report = decode_response(self.response({'coverage': 'nothing', 'conclusion': 'Made up', 'evidence': []}), 'News & filings', at)
        self.assertIn('No longer-term conclusion', report['long_term_conclusion'])


if __name__ == '__main__':
    unittest.main()
