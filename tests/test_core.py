import copy
import json
import math
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app import Engine, DEFAULT, handler_for, validate
from market import Alpaca, Demo, DataError, now, stamp
from research import decode_response, research_gate, research_cycle
from strategy import technical, quote_problem, size, make_ideas


def bullish_snapshot(at):
    snapshot = Demo().collect(DEFAULT, at)
    for instrument in snapshot['instruments'].values():
        for index, bar in enumerate(instrument['bars']):
            price = 100 + index * .05
            bar.update(o=price-.03, h=price+.04, l=price-.04, c=price, v=10000)
        instrument['quote'].update(bp=103.19, ap=103.21)
    return snapshot


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.at = now()
        self.snapshot = bullish_snapshot(self.at)

    def test_bullish_trend_and_freshness(self):
        instrument = self.snapshot['instruments']['SPY']
        self.assertEqual(technical(instrument, self.at)['direction'], 'bullish')
        instrument['quote']['t'] = stamp(self.at-timedelta(seconds=46))
        self.assertEqual(technical(instrument, self.at)['direction'], 'wait')

    def test_bad_quotes_never_signal(self):
        quote = self.snapshot['instruments']['SPY']['quote']
        for changes in ({'bp': math.nan}, {'ap': -1}, {'bp': 1000}, {'as': 0},
                        {'t': stamp(self.at+timedelta(seconds=10))}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(quote_problem(dict(quote, **changes), self.at))

    def test_missing_and_unfinished_candles_block(self):
        original = self.snapshot['instruments']['SPY']
        for mode in ('gap', 'forming', 'volume', 'ohlc'):
            instrument = copy.deepcopy(original)
            if mode == 'gap': instrument['bars'].pop(-5)
            if mode == 'forming': instrument['bars'][-1]['t'] = stamp(self.at)
            if mode == 'volume': instrument['bars'][-1]['v'] = math.nan
            if mode == 'ohlc': instrument['bars'][-1]['l'] = 1000
            self.assertEqual(technical(instrument, self.at)['direction'], 'wait', mode)

    def test_option_reserves_full_premium_and_multiplier(self):
        config = dict(DEFAULT, budget=10000, risk_pct=1)
        self.assertEqual(size(1.24, .87, 'option', config, 10000, 300), 0)
        config['budget'] = 20000
        self.assertEqual(size(1.24, .87, 'option', config, 20000, 600), 1)
        self.assertEqual(size(1.24, .87, 'option', config, 100, 600), 0)

    def test_all_ideas_within_risk_and_capital(self):
        config = dict(DEFAULT, budget=20000)
        ideas, _, _ = make_ideas(self.snapshot, config, [], self.at, 700, 150)
        self.assertTrue(ideas)
        self.assertTrue(any(i['asset']=='option' for i in ideas))
        for idea in ideas:
            self.assertLessEqual(idea['notional'], 700)
            self.assertLessEqual(idea['risk'], 150)
            self.assertGreater(idea['entry'], idea['stop'])
            self.assertGreater(idea['target'], idea['entry'])

    def test_live_requires_relevant_research(self):
        self.snapshot['simulated'] = False
        ideas, _, rejected = make_ideas(self.snapshot, DEFAULT, [], self.at, 10000, 300)
        self.assertEqual(ideas, [])
        self.assertTrue(any('research' in r['reason'] for r in rejected))

    def test_option_indicative_feed_is_rejected(self):
        with patch.dict(os.environ, {'APCA_API_KEY_ID':'fake','APCA_API_SECRET_KEY':'fake','ALPACA_OPTION_FEED':'indicative'}):
            with self.assertRaises(DataError):
                Alpaca().options('SPY', 100, self.at)

    def test_closed_stock_market_does_not_block_bitcoin(self):
        with patch.dict(os.environ, {'APCA_API_KEY_ID':'fake','APCA_API_SECRET_KEY':'fake'}):
            provider = Alpaca()
        with patch.object(provider, 'get', side_effect=lambda path, *a, **k: {'timestamp':stamp(self.at), 'is_open':False} if path=='/v2/clock' else {'news':[]}), patch.object(provider, 'instrument', return_value=self.snapshot['instruments']['BTC/USD']) as fetch:
            result = provider.collect(DEFAULT, self.at)
            fetch.assert_called_once_with('BTC/USD', self.at)
            self.assertFalse(result['equity_open'])
            self.assertIn('BTC/USD', result['instruments'])


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.at = now()
        self.evidence = {'symbol':'SPY','summary':'Fresh report','url':'https://example.com/source','title':'Source',
                         'published_at':stamp(self.at),'sentiment':'bullish','event_risk':False}

    def response(self, evidence):
        return {'status':'completed','output':[
            {'type':'web_search_call','action':{'sources':[{'url':'https://example.com/source'}]}},
            {'type':'message','content':[{'type':'output_text','text':json.dumps({'coverage':'partial','evidence':evidence})}]}]}

    def test_unsourced_claims_are_discarded(self):
        fake = dict(self.evidence, url='https://invented.example/')
        report = decode_response(self.response([self.evidence, fake]), 'News & filings', self.at)
        self.assertEqual(len(report['evidence']), 1)
        self.assertTrue(research_gate([report],'SPY','bullish',self.at,True)[0])
        self.assertFalse(research_gate([report],'AAPL','bullish',self.at,True)[0])

    def test_incomplete_and_searchless_reports_are_rejected(self):
        response = self.response([self.evidence])
        response['status'] = 'incomplete'
        response['incomplete_details'] = {'reason': 'max_output_tokens'}
        with self.assertRaisesRegex(ValueError, 'max_output_tokens'):
            decode_response(response, 'News & filings', self.at)
        response['status'] = 'completed'
        response['output'] = response['output'][1:]
        with self.assertRaisesRegex(ValueError, 'did not search'):
            decode_response(response, 'News & filings', self.at)

    def test_agent_progress_reports_lifecycle_without_raw_output(self):
        events = []
        report = decode_response(self.response([self.evidence]), 'News & filings', self.at)
        with patch('research.run_agent', return_value=report):
            result = research_cycle(['SPY'], progress=lambda *event: events.append(event))
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(e[1]=='running' for e in events), 3)
        self.assertEqual(sum(e[1]=='complete' for e in events), 3)
        for lane in ('News & filings','Social sentiment','Macro & catalysts'):
            self.assertEqual([e[1] for e in events if e[0]==lane], ['running','complete'])
        self.assertNotIn('output_text', json.dumps(events))

    def test_unknown_dates_and_old_reports_cannot_satisfy_gate(self):
        report = decode_response(self.response([dict(self.evidence,published_at=None)]),'News & filings',self.at)
        self.assertFalse(research_gate([report],'SPY','bullish',self.at,True)[0])
        report = decode_response(self.response([self.evidence]),'News & filings',self.at)
        self.assertFalse(research_gate([report],'SPY','bullish',self.at+timedelta(minutes=21),True)[0])

    def test_social_cannot_unlock_live_ideas_and_conflicts_veto(self):
        report = decode_response(self.response([self.evidence]),'Social sentiment',self.at)
        self.assertFalse(research_gate([report],'SPY','bullish',self.at,True)[0])
        report = decode_response(self.response([dict(self.evidence,sentiment='bearish')]),'News & filings',self.at)
        self.assertFalse(research_gate([report],'SPY','bullish',self.at,True)[0])


class EngineTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch('app.Engine.launch_research'), patch.object(Alpaca,'tracking_quotes',return_value=({},[])),
                     patch.dict(os.environ,{'APCA_API_KEY_ID':'test','APCA_API_SECRET_KEY':'test','OPENAI_API_KEY':'test'})):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name)/'journal.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def scan(self):
        self.engine.start(DEFAULT)
        with patch.object(self.engine.provider,'collect',return_value=bullish_snapshot(now())):
            self.engine.scan()
        self.assertTrue(self.engine.ideas)

    def test_manual_market_refresh_skips_ai_and_rejects_duplicates(self):
        with self.assertRaisesRegex(ValueError, 'Start a session'):
            self.engine.request_market_refresh()
        self.engine.start(DEFAULT)
        self.engine.request_market_refresh()
        self.assertTrue(self.engine.market_refresh_requested)
        with self.assertRaisesRegex(ValueError, 'already refreshing'):
            self.engine.request_market_refresh()
        with patch.object(self.engine.provider, 'collect', return_value=bullish_snapshot(now())) as collect, \
             patch.object(self.engine, 'launch_research') as research, \
             patch.object(self.engine, 'launch_long_research') as long_research, \
             patch.object(self.engine, 'request_position_review') as review:
            self.engine.scan()
        collect.assert_called_once()
        research.assert_not_called()
        long_research.assert_not_called()
        review.assert_not_called()
        self.assertFalse(self.engine.market_refresh_requested)
        self.assertFalse(self.engine.scanning)
        self.assertIsNotNone(self.engine.last_scan)
        self.assertEqual(self.engine.ai_usage()['requests'], 0)
        self.engine.request_market_refresh()
        self.engine.stop()
        self.assertFalse(self.engine.market_refresh_requested)

    def test_paper_budget_duplicate_and_restart_persistence(self):
        self.scan()
        idea = self.engine.ideas[0]
        available = self.engine.allowances()[0]
        self.engine.paper_open(idea['id'])
        self.assertAlmostEqual(self.engine.allowances()[0],available-idea['notional'])
        with self.assertRaises(ValueError): self.engine.paper_open(idea['id'])
        reloaded = Engine(self.engine.db_path)
        self.assertEqual(len(reloaded.trades),1)
        self.assertAlmostEqual(reloaded.allowances()[0],self.engine.allowances()[0])
        self.engine.paper_close(self.engine.trades[0]['id'])
        self.assertEqual(self.engine.trades[0]['status'],'closed')
        self.assertGreater(self.engine.allowances()[2],0)  # crossing bid/ask incurs an estimated loss

    def test_expired_idea_and_changed_budget_block(self):
        self.scan()
        self.engine.ideas[0]['expires_at'] = stamp(now()-timedelta(seconds=1))
        with self.assertRaises(ValueError): self.engine.paper_open(self.engine.ideas[0]['id'])
        self.engine.paper_open(self.engine.ideas[1]['id'])
        self.engine.stop()
        with self.assertRaises(ValueError): self.engine.start(dict(DEFAULT,budget=30000))

    def test_stop_discards_inflight_scan(self):
        self.engine.start(DEFAULT)
        def collect(*args):
            self.engine.stop()
            return bullish_snapshot(now())
        with patch.object(self.engine.provider,'collect',side_effect=collect):
            self.engine.scan()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.ideas,[])

    def test_provider_failure_clears_previous_ideas(self):
        self.scan()
        with patch.object(self.engine.provider,'collect',side_effect=DataError('Unavailable')):
            self.engine.scan()
        self.assertEqual(self.engine.ideas,[])
        self.assertEqual(self.engine.errors,['Unavailable'])

    def test_http_local_access_csrf_and_settings(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.engine))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        host=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(host+'/api/state') as response: state=json.load(response)
            self.assertIn('csrf',state)
            self.assertNotIn('APCA_API_SECRET_KEY',json.dumps(state))
            with self.assertRaises(HTTPError) as error: urlopen(Request(host+'/api/start',data=b'{}'))
            self.assertEqual(error.exception.code,403)
            request=Request(host+'/api/start',data=json.dumps(DEFAULT).encode(),headers={'X-CSRF-Token':state['csrf']})
            with urlopen(request) as response: self.assertTrue(json.load(response)['ok'])
            with self.assertRaises(HTTPError): urlopen(host+'/.env')
            with self.assertRaises(HTTPError): urlopen(Request(host+'/api/state',headers={'Host':'evil.example'}))
        finally:
            server.shutdown()
            server.server_close()

    def test_validation_rejects_unsafe_inputs(self):
        for data in ({'budget':float('nan')},{'budget':-1},{'assets':[]},{'symbols':['../.env']},
                     {'mode':'real'},{'research':'false'},{'research_calls':10000}):
            with self.subTest(data=data),self.assertRaises(ValueError): validate(data)

    def test_debug_events_bounded_redacted_and_stale_callbacks_discarded(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'private-test-value'}):
            self.engine.trace('Test', 'warning', 'private-test-value', {'nested': ['private-test-value']})
            self.assertNotIn('private-test-value', json.dumps(self.engine.state()['debug']))
            self.assertIn('[redacted]', json.dumps(self.engine.state()['debug']))
        self.scan()
        messages = [e['message'] for e in self.engine.activity]
        self.assertTrue(any('Fetching completed' in m for m in messages))
        self.assertTrue(any('passed the configured filters' in m for m in messages))
        previous_generation = self.engine.generation
        self.engine.stop()
        count = self.engine.event_counter
        self.engine.trace('News & filings', 'complete', 'Old report', generation=previous_generation)
        self.assertEqual(self.engine.event_counter, count)
        for i in range(310): self.engine.trace('Test', 'complete', str(i))
        self.assertEqual(len(self.engine.activity), 300)
        self.assertEqual(self.engine.activity[-1]['message'], '309')


if __name__=='__main__': unittest.main()
