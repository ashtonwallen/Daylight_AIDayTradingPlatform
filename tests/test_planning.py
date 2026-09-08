import unittest
from datetime import timedelta
from market import now, stamp
from planning import build
from app import DEFAULT


class PlanningTests(unittest.TestCase):
    def test_closed_plans_are_opt_in_and_never_executable(self):
        at=now()
        bars=[{'t':stamp(at-timedelta(days=30-i)),'o':100+i,'h':101+i,'l':99+i,'c':100+i} for i in range(30)]
        snapshot={'market_open':False,'daily_bars':{'MSFT':bars}}
        config=dict(DEFAULT,closed_market_ideas=True)
        plans=build(snapshot,config,[],at)
        self.assertTrue(plans)
        self.assertTrue(plans[0]['planning_only'])
        self.assertIn('Long call candidate',plans[0]['instruments'])
        self.assertNotIn('entry',plans[0])
        self.assertIn('unavailable',plans[0]['research_status'])
        self.assertEqual(build(snapshot,DEFAULT,[],at),[])
        self.assertEqual(build(dict(snapshot,market_open=True),config,[],at),[])
        self.assertEqual(build(dict(snapshot,market_open=None),config,[],at),[])
        self.assertEqual(build(snapshot,config,[],at+timedelta(days=10)),[])

    def test_research_setting_is_explicit_in_status(self):
        import tempfile
        from pathlib import Path
        from app import Engine
        with tempfile.TemporaryDirectory() as directory:
            engine=Engine(Path(directory)/'db')
            engine.running=True;engine.snapshot={'at':stamp(now())}
            engine.config['research']=False
            status=engine.data_categories()['research']
            self.assertEqual(status['status'],'waiting')
            self.assertIn('disabled in session settings',status['detail'])
