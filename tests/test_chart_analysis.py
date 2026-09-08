import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Engine
from chart_analysis import validate_chart
from market import now
from test_charts import minute_bars


class ChartAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')
        self.data = {'symbol': 'QQQ', 'span': '1D', 'interval': '5Min', 'bars': minute_bars(now(), 30)}

    def test_snapshot_preserves_every_candle_and_requested_interval(self):
        result = validate_chart(self.data)
        self.assertEqual(result['timeframe'], '5Min')
        self.assertEqual(len(result['candles']), 30)
        self.assertEqual(result['metrics']['sma20'], sum(b['c'] for b in self.data['bars'][-20:]) / 20)
        self.assertIsNone(result['metrics']['sma50'])
        with self.assertRaises(ValueError):
            validate_chart(dict(self.data, bars=list(reversed(self.data['bars']))))
        with self.assertRaises(ValueError):
            validate_chart(dict(self.data, span='1Y'))

    def test_pause_and_quota_gate_and_successful_worker(self):
        self.engine.ai_paused = True
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.engine.request_chart_analysis(self.data)
        self.engine.ai_paused = False
        with patch('ai.configured', return_value=True), patch('chart_analysis.threading.Thread') as thread:
            self.engine.request_chart_analysis(self.data)
        self.assertEqual(self.engine.ai_usage()['requests'], 1)
        self.assertTrue(self.engine.chart_analyzing)
        with patch('chart_analysis.analyze', return_value={'summary': 'Trend is rising', 'bias': 'bullish'}):
            self.engine.chart_analysis_job(*thread.call_args.kwargs['args'])
        self.assertEqual(self.engine.chart_analysis['status'], 'complete')
        self.assertFalse(self.engine.chart_analyzing)
        with patch('ai.configured', return_value=True), patch.object(self.engine, 'reserve_ai', return_value=False):
            with self.assertRaisesRegex(ValueError, 'cap'):
                self.engine.request_chart_analysis(self.data)
