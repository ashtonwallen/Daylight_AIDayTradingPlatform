import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from app import Engine
from contracts import select
from market import now, stamp, DataError
from outlook import validate


def response(sourced=True):
    data={'verdict':'cautiously-favorable','outlook':'bullish','summary':'Conditional case for calls',
          'horizon':'One month','interpretation':'NVDA calls for one month','missing':['Expiry and price'],
          'thesis':['Potential catalyst'],'risks':['Time decay'],'instruments':[], 'invalidation':['Catalyst fails'],
          'sources':[{'url':'https://example.com/filing','title':'Filing','published_at':None}],
          'trade_request':{'symbol':'NVDA','instrument':'call','horizon_end':str(now().date()+timedelta(days=30))}}
    output=[{'type':'message','content':[{'type':'output_text','text':json.dumps(data)}]}]
    if sourced:output.append({'type':'web_search_call','action':{'sources':[{'url':'https://example.com/filing'}]}})
    return {'status':'completed','output':output}


class ThesisTests(unittest.TestCase):
    def test_open_ended_research_selects_a_trade_and_carries_exit_plan(self):
        from outlook import assess_thesis
        from trade_plan import recommendation
        reply=response()
        payload=json.loads(reply['output'][0]['content'][0]['text'])
        payload['trade_plan']={'entry':'Enter after catalyst confirmation.', 'exit':'Close after the catalyst or at the review date.',
                               'invalidation':'Exit if guidance is cut.', 'review_by':payload['trade_request']['horizon_end']}
        reply['output'][0]['content'][0]['text']=json.dumps(payload)
        with patch('ai.complete', return_value=reply) as call:
            report=assess_thesis('Find a useful AI infrastructure trade for the next month.', {'requested_at':stamp(now())}, lambda:True)
        self.assertIn('independently choose ONE', call.call_args.args[0])
        with patch('contracts.select', return_value={'status':'candidate', 'symbol':'NVDA'}) as selector:
            result=recommendation(report, Mock(), 1000)
        self.assertEqual(selector.call_args.args[1]['symbol'],'NVDA')
        self.assertEqual(result['plan']['exit'],payload['trade_plan']['exit'])
        with patch('contracts.select') as selector:
            self.assertEqual(recommendation(dict(report,trade_request={'instrument':'none','reason':'No edge.'}),Mock(),1000)['status'],'none')
            selector.assert_not_called()

    def test_explicit_contract_constraints_are_preserved(self):
        at=now();end=at.date()+timedelta(days=30)
        selected='NVDA'+end.strftime('%y%m%d')+'C00090000'
        other='NVDA'+end.strftime('%y%m%d')+'C00100000'
        provider=Mock();provider.option_feed='opra'
        quote={'bp':2,'ap':2.05,'bs':10,'as':10,'t':stamp(at)}
        provider.instrument.return_value={'quote':dict(quote,bp=100,ap=100.01)}
        provider.get.return_value={'snapshots':{selected:{'latestQuote':quote,'greeks':{'delta':.8}},other:{'latestQuote':quote,'greeks':{'delta':.5}}}}
        result=select(provider,{'symbol':'NVDA','instrument':'call','horizon_end':str(end),'expiration':str(end),'strike':90},1000)
        self.assertEqual(result['symbol'],selected)
        self.assertEqual(provider.get.call_args.args[1]['strike_price_gte'],90)
        self.assertEqual(provider.get.call_args.args[1]['expiration_date_lte'],str(end))

    def test_grading_requires_sources_and_valid_structure(self):
        self.assertEqual(validate(response(),thesis=True)['verdict'],'cautiously-favorable')
        self.assertEqual(validate(response(False),thesis=True)['verdict'],'insufficient-evidence')
        broken=response();broken['output'][0]['content'][0]['text']='{}'
        with self.assertRaises(DataError):validate(broken,thesis=True)

    def test_specific_contract_is_real_fresh_and_directionally_correct(self):
        at=now();end=at.date()+timedelta(days=30)
        symbol='NVDA'+end.strftime('%y%m%d')+'C00100000'
        quote={'bp':2,'ap':2.05,'bs':10,'as':10,'t':stamp(at)}
        provider=Mock();provider.option_feed='opra'
        provider.instrument.return_value={'quote':{'bp':100,'ap':100.01,'bs':10,'as':10,'t':stamp(at)}}
        provider.get.return_value={'snapshots':{symbol:{'latestQuote':quote,'greeks':{'delta':.5}}}}
        request={'symbol':'NVDA','instrument':'call','horizon_end':str(end)}
        result=select(provider,request,1000)
        self.assertEqual(result['symbol'],symbol)
        self.assertEqual(result['cost_per_contract'],205)
        self.assertEqual(select(provider,request,100)['status'],'unavailable')
        self.assertEqual(select(provider,dict(request,instrument='put'),1000)['status'],'unavailable')
        quote['t']=stamp(at-timedelta(seconds=46))
        self.assertEqual(select(provider,request,1000)['status'],'unavailable')

    def test_stopped_session_can_research_outside_watchlist_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';engine=Engine(path)
            engine.config['symbols']=['SPY']
            with patch('app.ai.configured',return_value=True), patch('app.ai.settings',return_value={'web_research':True}), patch('app.threading.Thread') as thread:
                engine.request_thesis('NVDA calls over the next month')
                self.assertTrue(thread.called)
                self.assertEqual(engine.ai_usage()['requests'],1)
            report=validate(response(),thesis=True)
            with patch('outlook.assess_thesis',return_value=report),patch('app.Alpaca'),patch('contracts.select',return_value={'status':'unavailable','reason':'No option entitlement'}):
                engine.thesis_job(0,'NVDA calls over the next month',{'budget':1000})
            self.assertEqual(engine.thesis_report['status'],'complete')
            self.assertFalse(engine.outlook_running)
            self.assertEqual(Engine(path).outlook_history[0]['kind'],'thesis')
            engine.set_ai_paused(True)
            with self.assertRaises(ValueError):engine.request_thesis('MSFT stock for a year')
