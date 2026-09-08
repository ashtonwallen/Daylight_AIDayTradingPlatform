import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app import Engine, DEFAULT
from idea_review import conclude, validate_assessment
from market import now, stamp
from strategy import make_ideas
from test_core import bullish_snapshot


ASSESSMENT = {'symbol':'SPY','asset':'stock','direction':'Buy','conditional':False,
              'custom_levels':False,'requested_notional':None,'requested_quantity':None,
              'specific_contract':None,'verdict':'yes','summary':'Trend currently supports the proposal.',
              'supporting':['Moving averages align.'],'risks':['The market can reverse.']}


class ReviewChecks(unittest.TestCase):
    def setUp(self):
        self.at=now()
        self.ideas,_,_=make_ideas(bullish_snapshot(self.at),DEFAULT,[],self.at,10000,300)
        for idea in self.ideas: idea['simulated']=False

    def verdict(self,assessment=None,ideas=None,at=None,held=None):
        return conclude(assessment or ASSESSMENT,self.ideas if ideas is None else ideas,
                        dict(DEFAULT,mode='live'),at or self.at,10000,300,held or set())

    def test_ai_yes_requires_eligible_fresh_supported_proposal(self):
        self.assertEqual(self.verdict()['verdict'],'yes')
        self.assertEqual(self.verdict(ideas=[])['verdict'],'no')
        self.assertEqual(self.verdict(at=self.at+timedelta(minutes=2))['verdict'],'no')
        self.assertEqual(self.verdict(held={'SPY'})['verdict'],'no')
        for change in ({'requested_quantity':999999},{'requested_quantity':.5},
                       {'requested_notional':.1},{'conditional':True},{'custom_levels':True},
                       {'direction':'unsupported'},{'verdict':'no'}):
            self.assertEqual(self.verdict(dict(ASSESSMENT,**change))['verdict'],'no',change)

    def test_invalid_response_cannot_become_yes(self):
        for change in ({'verdict':'maybe'},{'conditional':'false'},{'requested_quantity':float('nan')},
                       {'supporting':'Buy now'},{'symbol':[]},{'custom_levels':None}):
            with self.assertRaises(ValueError):validate_assessment(dict(ASSESSMENT,**change))

    def test_restart_preserves_settings_quota_and_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'journal.sqlite3'
            engine=Engine(path)
            config=dict(DEFAULT,budget=1234)
            engine.start(config)
            # Read the cap from config so this stays true whatever the default becomes.
            cap=engine.daily_ai_limit()
            self.assertTrue(engine.reserve_ai(cap-1))
            other=Engine(path)
            self.assertEqual(other.config['budget'],1234)
            self.assertFalse(other.running)
            self.assertFalse(other.reserve_ai(3))   # the shared count survives a restart
            self.assertTrue(other.reserve_ai(1))    # exactly at the cap is allowed
            self.assertFalse(engine.reserve_ai(1))
            self.assertEqual(engine.ai_usage()['requests'],cap)
            engine.config['mode']='live'
            engine.ideas=copy.deepcopy(self.ideas)
            context={'at':stamp(self.at),'research':[]}
            with patch('app.analyze_proposal',return_value=ASSESSMENT):
                engine.review_job(engine.generation,'Buy SPY',context)
            self.assertEqual(engine.idea_review['status'],'complete')
            self.assertEqual(len(Engine(path).review_history),1)


if __name__=='__main__':unittest.main()
