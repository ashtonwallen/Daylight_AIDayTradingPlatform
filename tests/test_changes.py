import unittest
from datetime import timedelta
from changes import ChangeLog
from market import now


def idea(symbol='SPY',entry=100,identity='first'):
    return {'id':identity,'symbol':symbol,'underlying':symbol,'direction':'Buy','entry':entry,'reasons':['Trend and volume aligned.']}


class ChangesTests(unittest.TestCase):
    def test_uuid_churn_does_not_realert_and_requalification_has_cooldown(self):
        log=ChangeLog();at=now();first=idea()
        log.record([], [first],[],[],at)
        self.assertTrue(log.events[0]['notify'])
        second=idea(identity='new-id')
        log.record([first],[second],[],[],at+timedelta(seconds=60))
        self.assertEqual(len(log.events),1)
        log.record([second],[],[{'symbol':'SPY','reason':'Quote stale'}],[],at+timedelta(seconds=120))
        self.assertEqual(log.events[-1]['message'],'Quote stale')
        log.record([],[second],[],[],at+timedelta(seconds=180))
        self.assertFalse(log.events[-1]['notify'])
        log.record([second],[],[],['Session closed'],at+timedelta(seconds=300))
        log.record([],[second],[],[],at+timedelta(seconds=601))
        self.assertTrue(log.events[-1]['notify'])

    def test_material_move_and_failure_are_explained_without_buy_alert(self):
        log=ChangeLog();at=now()
        log.record([idea()],[idea(entry=101)],[],[],at)
        self.assertEqual(log.events[0]['kind'],'updated')
        self.assertIn('+1.00%',log.events[0]['message'])
        self.assertFalse(log.events[0]['notify'])
        log.record([idea()],[],[],['Market scan failed'],at)
        self.assertEqual(log.events[-1]['message'],'Market scan failed')
        self.assertFalse(log.events[-1]['notify'])

    def test_history_is_bounded(self):
        log=ChangeLog()
        for i in range(150):log.record([],[idea(symbol=str(i))],[],[],now())
        self.assertEqual(len(log.events),100)
        self.assertEqual(log.sequence,150)
