import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyst
from market import DataError


def event(key, verdict, plan=None, symbol='NVDA'):
    return {'id': f'2026-11-12:{symbol}:{key}', 'symbol': symbol, 'time': '10:00', 'what': key, 'direction': 1,
            'level': 100.0, 'last': 101.0, 'minutes_ago': 5, 'verdict': verdict, 'plan': plan,
            'stats': {'cases': 59, 'continued_pct': 61, 'usual_pct': 48, 'median_to_close_pct': 0.44, 'usual_median_pct': 0.02}}


PLAN = {'side': 'up', 'entry': 101.0, 'stop': 100.0, 'target': 103.0, 'quantity': 10, 'cost': 1010.0, 'risk': 10.0, 'reward': 20.0}
EVENTS = [event('prev_high', 'ordinary'), event('first_half_up', 'continues', PLAN), event('range_up', 'continues', {'side': 'up', 'note': 'x'})]


def read(event_id, stance, **changes):
    return dict({'id': event_id, 'stance': stance, 'read': 'r', 'confirm': 'c', 'wrong': 'w'}, **changes)


class AnalystTests(unittest.TestCase):
    def test_signals_with_a_lean_are_sent_first_and_compacted(self):
        chosen = analyst.choose(EVENTS, limit=2)
        self.assertEqual([e['what'] for e in chosen], ['first_half_up', 'range_up'])
        sent = analyst.compact(EVENTS[1])
        self.assertEqual(sent['plan'], {'entry': 101.0, 'stop': 100.0, 'target': 103.0, 'quantity': 10, 'risk': 10.0})
        self.assertEqual((sent['cases'], sent['continued_pct'], sent['usual_pct']), (59, 61, 48))
        self.assertIsNone(analyst.compact(EVENTS[0])['plan'])

    def test_act_survives_only_where_the_rules_made_a_plan(self):
        result = analyst.validate({'overview': 'o', 'reads': [
            read(EVENTS[1]['id'], 'act'), read(EVENTS[2]['id'], 'act'), read(EVENTS[0]['id'], 'act')]}, EVENTS)
        self.assertEqual([r['stance'] for r in result['reads']], ['act', 'watch', 'watch'])
        self.assertIsNone(result['reads'][0]['lowered'])
        self.assertIn('no sized plan', result['reads'][1]['lowered'])

    def test_reads_about_unknown_signals_or_with_bad_shapes_are_dropped(self):
        result = analyst.validate({'overview': 'o', 'reads': [
            read('2026-11-12:TSLA:made_up', 'act'), read(EVENTS[0]['id'], 'buy now'), read(EVENTS[0]['id'], 'watch', read=None),
            'nonsense', read(EVENTS[0]['id'], 'ignore', read='x' * 2000)]}, EVENTS)
        self.assertEqual(len(result['reads']), 1)
        self.assertEqual((result['reads'][0]['stance'], len(result['reads'][0]['read'])), ('ignore', 700))

    def test_unusable_answers_are_refused(self):
        for bad in (None, {'overview': 1, 'reads': []}, {'overview': 'o'}):
            with self.assertRaises(DataError):
                analyst.validate(bad, EVENTS)


if __name__ == '__main__':
    unittest.main()


class CitationMarkerTests(unittest.TestCase):
    def test_claude_citation_markers_are_removed_and_their_text_and_sources_kept(self):
        import ai
        reply = {'stop_reason': 'end_turn', 'content': [
            {'type': 'text', 'text': '{"overview": "<cite index=\'1-4,1-6\'>Shares fell</cite> and <CITE index="2-1">the VIX rose</cite>. A < B stays."}',
             'citations': [{'url': 'https://example.com/a', 'title': 'A'}]}]}
        result = ai.normalize('anthropic', reply)
        self.assertEqual(ai.extract_json(ai.response_text(result)), {'overview': 'Shares fell and the VIX rose. A < B stays.'})
        self.assertEqual(result['output'][0]['action']['sources'], [{'url': 'https://example.com/a', 'title': 'A'}])
