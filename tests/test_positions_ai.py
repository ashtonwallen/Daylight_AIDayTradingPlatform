import copy
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta

import ai
from app import Engine, DEFAULT
from market import now, stamp, DataError
from positions import validate_fill, holdings, consensus


def fill(side='buy',quantity=10,price=100):
    return validate_fill({'symbol':'AAPL','asset':'stock','side':side,'quantity':quantity,'price':price,'stop':90})


class PositionAndAIControls(unittest.TestCase):
    def test_weighted_cost_partial_sale_and_oversell(self):
        ledger=[fill(),fill(quantity=10,price=120),fill('sell',5,130)]
        p=holdings(ledger)['AAPL']
        self.assertEqual(p['quantity'],15)
        self.assertEqual(p['average_price'],110)
        self.assertEqual(p['realized_pnl'],100)
        with self.assertRaises(ValueError):holdings(ledger+[fill('sell',16,130)])
        with self.assertRaises(ValueError):validate_fill({'symbol':'SPY260918C00600000','asset':'option','side':'buy','quantity':.5,'price':2})

    def test_no_consensus_or_stale_quotes_never_suggest_action(self):
        at=now()
        p=holdings([fill()])['AAPL']
        q={'bp':105,'ap':105.01,'bs':10,'as':10,'t':stamp(at)}
        reports=[{'agent':name,'status':'complete','positions':[{'symbol':'AAPL','action':'EXIT','summary':'Adverse momentum.','factors':[]}]} for name in ('analyst','risk')]
        self.assertEqual(consensus(p,reports,q,[],0,0,at)['action'],'EXIT')
        reports[1]['positions'][0]['action']='HOLD'
        self.assertEqual(consensus(p,reports,q,[],0,0,at)['action'],'REVIEW')
        reports[1]['positions'][0]['action']='EXIT'
        q['t']=stamp(at-timedelta(minutes=1))
        self.assertEqual(consensus(p,reports,q,[],0,0,at)['action'],'REVIEW')

    def test_pause_blocks_all_request_paths_but_manual_logging_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            e=Engine(Path(directory)/'journal.sqlite3')
            e.set_ai_paused(True)
            self.assertFalse(e.reserve_ai(1))
            for call in (lambda:e.request_review('Should I buy AAPL?'),lambda:e.request_fill_preview('Bought 10 AAPL at 100'),lambda:e.request_position_review()):
                with self.assertRaisesRegex(ValueError,'paused'):call()
            token=str(uuid.uuid4())
            e.save_fill(fill(),token)
            e.save_fill(fill(),token)
            reloaded=Engine(e.db_path)
            self.assertTrue(reloaded.ai_paused)
            self.assertEqual(len(reloaded.manual_fills),1)
            self.assertEqual(reloaded.manual_view()[0]['quantity'],10)
            self.assertFalse(reloaded.config['auto_position_review'])
            self.assertEqual(reloaded.ai_usage()['requests'],0)

    def test_provider_settings_do_not_expose_keys_or_accept_remote_local_url(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ai,'ROOT',Path(directory)), patch.dict(os.environ,{},clear=True):
            ai.save_settings({'provider':'anthropic','model':'test-model','api_key':'fake-secret'})
            self.assertNotIn('fake-secret',json.dumps(ai.settings()))
            self.assertTrue(ai.settings()['configured'])
            ai.save_settings({'provider':'local','model':'loaded-model','api_key':'','local_url':'http://localhost:1234'})
            self.assertEqual(ai.settings()['local_url'],'http://localhost:1234/v1')
            self.assertFalse(ai.settings()['web_research'])
            with self.assertRaises(ValueError):ai.local_url('https://external.example/v1')
            with self.assertRaises(ValueError):ai.save_settings({'provider':'openai','model':'gpt-test','api_key':'bad\nOTHER=secret'})

    def test_provider_response_normalization_keeps_only_public_text_and_sources(self):
        google={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'hidden','thought':True},{'text':'{"ok":true}'}]},'groundingMetadata':{'groundingChunks':[{'web':{'uri':'https://example.com','title':'Source'}}]}}]}
        normalized=ai.normalize('google',google)
        self.assertNotIn('hidden',json.dumps(normalized))
        self.assertEqual(normalized['output'][0]['type'],'web_search_call')
        claude={'stop_reason':'end_turn','content':[{'type':'thinking','thinking':'hidden'},{'type':'text','text':'{"ok":true}','citations':[{'url':'https://example.com','title':'Source'}]}]}
        self.assertNotIn('hidden',json.dumps(ai.normalize('anthropic',claude)))
        with patch.dict(os.environ,{'AI_PROVIDER':'local','LOCAL_AI_MODEL':'local-model'}),patch('ai.urlopen') as network:
            with self.assertRaises(DataError):ai.complete('Test','{}',can_send=lambda:False)
            network.assert_not_called()


if __name__=='__main__':unittest.main()
