import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from app import Engine, DEFAULT
from discovery import candidates
from market import now, stamp, DataError
from outlook import validate
from strategy import make_ideas
from test_core import bullish_snapshot


def report(lane='Social sentiment', symbol='MSFT'):
    return {'lane':lane,'status':'complete','at':stamp(now()),'evidence':[
        {'symbol':symbol,'lane':lane,'url':'https://example.com/'+lane.replace(' ','-'),
         'summary':'Company discussion','title':'Source','published_at':stamp(now()),
         'fresh':True,'sentiment':'bullish','event_risk':False}]}


class DiscoveryTests(unittest.TestCase):
    def test_leads_are_bounded_deduplicated_and_expire(self):
        reports=[report(symbol=s) for s in ['MSFT','AMD','INTC','META','TSLA','F','GE','XOM','SPY','INVALID!']]
        rows=candidates(reports,['SPY'],now())
        self.assertEqual(len(rows),6)
        self.assertFalse(any(r['corroborated'] for r in rows))
        self.assertFalse(candidates(reports,[],now()+timedelta(minutes=21)))
        self.assertEqual(len(candidates([report(),report()],[],now())[0]['sources']),1)

    def test_social_lead_needs_company_news_even_in_technical_only_session(self):
        at=now()
        snapshot=bullish_snapshot(at)
        snapshot['simulated']=False
        snapshot['instruments']['MSFT']=copy.deepcopy(snapshot['instruments']['SPY'])
        snapshot['instruments']['MSFT']['symbol']='MSFT'
        config=dict(DEFAULT,assets=['stock'],symbols=['SPY','MSFT'],discovery_symbols=['MSFT'],require_research=False)
        social=report()
        macro=report('Macro & catalysts','MARKET')
        ideas,_,_=make_ideas(snapshot,config,[social,macro],at,10000,300)
        self.assertFalse(any(i['symbol']=='MSFT' for i in ideas))
        ideas,_,_=make_ideas(snapshot,config,[social,report('News & filings')],at,10000,300)
        self.assertTrue(any(i['symbol']=='MSFT' for i in ideas))
        snapshot['instruments']['MSFT']['quote']['t']=stamp(at-timedelta(seconds=46))
        ideas,_,_=make_ideas(snapshot,config,[social,report('News & filings')],at,10000,300)
        self.assertFalse(any(i['symbol']=='MSFT' for i in ideas))

    def test_discovered_symbol_is_fetched_and_produces_labeled_idea(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=Engine(Path(directory)/'test.sqlite3')
            engine.running=True
            engine.config=dict(DEFAULT,assets=['stock'])
            engine.reports=[report('News & filings')]
            snapshot=bullish_snapshot(now());snapshot['simulated']=False
            snapshot['instruments']['MSFT']=dict(copy.deepcopy(snapshot['instruments']['SPY']),symbol='MSFT')
            from unittest.mock import Mock
            engine.provider=Mock(name='Alpaca')
            engine.provider.name='Alpaca'
            engine.provider.collect.return_value=snapshot
            engine.provider.tracking_quotes.return_value=({},[])
            with patch.object(engine,'launch_research'):engine.scan()
            self.assertIn('MSFT',engine.provider.collect.call_args.args[0]['symbols'])
            self.assertTrue(any(i['symbol']=='MSFT' and i['origin']=='discovery' for i in engine.ideas))
            self.assertNotIn('MSFT',engine.config['symbols'])


class OutlookTests(unittest.TestCase):
    def response(self):
        data={'outlook':'bullish','summary':'A thesis','horizon':'12 months','thesis':['Catalyst'],
              'risks':['Execution'],'instruments':['Investigate shares'],'invalidation':['Revenue misses'],
              'sources':[{'url':'https://example.com/filing','title':'Filing','published_at':None}]}
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(data)}]}]}

    def test_unsourced_claims_cannot_produce_recommendation(self):
        self.assertEqual(validate(self.response())['outlook'],'no-conclusion')
        self.assertEqual(validate(self.response())['instruments'],[])
        response=self.response()
        response['output'].insert(0,{'type':'web_search_call','action':{'sources':[{'url':'https://example.com/filing'}]}})
        self.assertEqual(validate(response)['outlook'],'bullish')

    def test_outlook_persists_and_pause_prevents_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.sqlite3';engine=Engine(path);engine.running=True
            result=validate(self.response())
            with patch('outlook.assess',return_value=result):engine.outlook_job(0,'Research a thesis',{})
            self.assertEqual(Engine(path).outlook_history[0]['question'],'Research a thesis')
            engine.set_ai_paused(True)
            with patch('outlook.assess') as assess:
                with self.assertRaises(ValueError):engine.request_outlook('Research Microsoft')
                assess.assert_not_called()
