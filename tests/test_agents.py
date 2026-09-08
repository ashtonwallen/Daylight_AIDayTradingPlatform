"""Long-horizon research, on-demand research refresh and the per-headline agent."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Engine, DEFAULT
from market import Alpaca, DataError
from research import LONG_TERM_LANE, analyze_headline, decode_long_term, long_term_cycle, research_gate
from market import now, stamp


def sourced(payload, url='https://example.com/filing'):
    return {'status': 'completed', 'output': [
        {'type': 'web_search_call', 'action': {'sources': [{'url': url, 'title': 'Filing'}]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(payload)}]}]}


LONG_PAYLOAD = {'coverage': 'Two filings reviewed.', 'views': [
    {'symbol': 'MSFT', 'direction': 'bullish', 'horizon': 'next two quarters', 'summary': 'Guidance implies continued growth.',
     'catalysts': ['Q2 earnings in January'], 'risks': ['Capex outpaces revenue']}],
    'evidence': [{'symbol': 'MSFT', 'summary': 'Guidance raised', 'title': 'Filing', 'url': 'https://example.com/filing',
                  'published_at': '2026-04-02T12:00:00Z', 'sentiment': 'bullish'}]}


class LongTermResearchTests(unittest.TestCase):
    def test_open_discovery_brief_reaches_prompt_and_report(self):
        brief = {'long_risk': 'conservative', 'long_months': 24, 'long_focus': 'Avoid biotech'}
        with patch('ai.complete', return_value=sourced(LONG_PAYLOAD)) as call:
            report = long_term_cycle(['SPY'], brief=brief)
        prompt = call.call_args.args[1]
        self.assertIn('24 months', prompt)
        self.assertIn('Avoid biotech', prompt)
        self.assertIn('not just the watchlist', prompt)
        self.assertEqual(report['brief'], brief)
        self.assertEqual(report['views'][0]['symbol'], 'MSFT')
        unrelated = dict(LONG_PAYLOAD, views=[dict(LONG_PAYLOAD['views'][0], symbol='XYZ')])
        self.assertEqual(decode_long_term(sourced(unrelated), now())['views'], [])

    def test_dated_older_sources_survive_and_views_need_evidence(self):
        at = now()
        report = decode_long_term(sourced(LONG_PAYLOAD), at)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['views'][0]['direction'], 'bullish')
        # A months-old filing is legitimate here, unlike the intraday lanes.
        self.assertEqual(report['evidence'][0]['published_at'], '2026-04-02T12:00:00Z')
        # Uncited views are dropped rather than reported.
        report = decode_long_term(sourced(dict(LONG_PAYLOAD, evidence=[dict(LONG_PAYLOAD['evidence'][0], url='https://invented.example/')])), at)
        self.assertEqual(report['views'], [])
        self.assertEqual(report['status'], 'no verified evidence')

    def test_long_term_evidence_can_never_gate_an_intraday_entry(self):
        at = now()
        report = decode_long_term(sourced(LONG_PAYLOAD), at)
        # Even mixed into a report list, nothing here is fresh enough to satisfy or veto the live gate.
        allowed, note, evidence = research_gate([report], 'MSFT', 'bullish', at, True)
        self.assertFalse(allowed)
        self.assertEqual(evidence, [])
        self.assertEqual(report['lane'], LONG_TERM_LANE)

    def test_failed_request_reports_unavailable_without_raising(self):
        with patch('ai.complete', side_effect=DataError('Provider HTTP 429.')):
            report = long_term_cycle(['MSFT'])
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('429', report['coverage'])
        self.assertEqual(report['views'], [])


HEADLINE_PAYLOAD = {'takeaway': 'The filing confirms a supply agreement.', 'already_priced': 'Partly, after last week’s move.',
                    'affected': [{'symbol': 'NVDA', 'effect': 'positive', 'why': 'Named counterparty.'},
                                 {'symbol': 'BAD', 'effect': 'sideways', 'why': 'Invalid effect is dropped.'}],
                    'angles': [{'idea': 'Long shares of the supplier', 'horizon': 'weeks', 'conditions': 'Confirmed volumes', 'risks': 'Contract is non-binding'}],
                    'confidence': 'medium', 'missing': ['Contract value']}


class HeadlineAgentTests(unittest.TestCase):
    def test_validated_fields_and_dropped_invalid_rows(self):
        with patch('ai.complete', return_value=sourced(HEADLINE_PAYLOAD, 'https://example.com/story')):
            result = analyze_headline({'headline': 'Supplier signs deal', 'source': 'wire', 'url': 'https://example.com/story'}, {})
        self.assertEqual(result['confidence'], 'medium')
        self.assertEqual([a['symbol'] for a in result['affected']], ['NVDA'])
        self.assertEqual(result['angles'][0]['horizon'], 'weeks')
        self.assertEqual(result['sources'][0]['url'], 'https://example.com/story')

    def test_missing_takeaway_is_rejected(self):
        with patch('ai.complete', return_value=sourced(dict(HEADLINE_PAYLOAD, takeaway=''), 'https://example.com/story')):
            with self.assertRaises(ValueError):
                analyze_headline({'headline': 'Supplier signs deal'}, {})


class OnDemandResearchTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch.dict(os.environ, {'APCA_API_KEY_ID': 'test', 'APCA_API_SECRET_KEY': 'test', 'OPENAI_API_KEY': 'test'}),
                     patch.object(Alpaca, 'tracking_quotes', return_value=({}, []))):
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def test_manual_refresh_explains_why_it_cannot_run(self):
        with self.assertRaisesRegex(ValueError, 'Start a session'):
            self.engine.launch_research(force=True)
        self.engine.set_ai_paused(True)
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.engine.launch_research(force=True)
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.engine.launch_long_research(force=True)

    def test_research_preferences_persist_and_validate_without_ai_calls(self):
        self.engine.set_long_research_brief({'long_risk': 'aggressive', 'long_months': 12, 'long_focus': 'Industrials'})
        restored = Engine(self.engine.db_path)
        self.assertEqual(restored.config['long_months'], 12)
        self.assertEqual(restored.config['long_focus'], 'Industrials')
        self.assertEqual(self.engine.calls_used, 0)
        with self.assertRaises(ValueError):
            self.engine.set_long_research_brief({'long_months': True})
        self.engine.long_researching = True
        with self.assertRaisesRegex(ValueError, 'Wait'):
            self.engine.set_long_research_brief({'long_months': 3})

    def test_manual_refresh_ignores_the_interval_but_not_the_cap(self):
        with patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research'):
            self.engine.start(DEFAULT)
        self.engine.last_research = __import__('time').monotonic()  # interval not yet elapsed
        with patch('threading.Thread') as thread:
            self.engine.launch_research(force=True)
            thread.assert_called_once()
        self.assertEqual(self.engine.calls_used, 3)
        self.engine.researching = False
        self.engine.calls_used = self.engine.config['research_calls']
        with self.assertRaisesRegex(ValueError, 'request cap'):
            self.engine.launch_research(force=True)

    def test_long_term_toggle_is_off_by_default_and_persists(self):
        self.assertFalse(self.engine.config['long_term_research'])
        with patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research'):
            self.engine.start(DEFAULT)
        # Disabled: the scheduled path must not reserve a request.
        with patch('threading.Thread') as thread:
            self.engine.launch_long_research()
            thread.assert_not_called()
        self.assertEqual(self.engine.calls_used, 0)
        self.engine.set_long_term_research(True)
        self.assertTrue(Engine(self.engine.db_path).config['long_term_research'])
        with patch('threading.Thread') as thread:
            self.engine.launch_long_research()
            thread.assert_called_once()
        self.assertEqual(self.engine.calls_used, 1)

    def test_headline_analysis_requires_a_headline_from_the_current_feed(self):
        with patch.object(Engine, 'launch_research'), patch.object(Engine, 'launch_long_research'):
            self.engine.start(DEFAULT)
        with self.assertRaisesRegex(ValueError, 'no longer in the current news feed'):
            self.engine.request_news_analysis({'headline': 'Something invented'})
        self.engine.news = [{'headline': 'Real headline', 'url': 'https://example.com/x', 'source': 'wire'}]
        self.engine.set_ai_paused(True)
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.engine.request_news_analysis({'headline': 'Real headline'})


if __name__ == '__main__':
    unittest.main()
