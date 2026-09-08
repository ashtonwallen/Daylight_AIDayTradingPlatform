"""Sizing limits and tolerant JSON recovery from provider replies."""
import json
import unittest

from ai import extract_json
from app import DEFAULT, validate
from market import DataError, now, stamp
from outlook import validate as validate_outlook
from strategy import size


class SizingLimitTests(unittest.TestCase):
    def test_three_limits_and_which_one_binds(self):
        config = dict(DEFAULT, budget=10000, risk_pct=1, max_position_pct=25)
        # $100 of risk over a $2 stop is 50 shares, and 50 x $50 = $2,500 fits the position cap exactly.
        self.assertEqual(size(50, 48, 'stock', config, 10000, 300), 50)
        # Tightening only the position cap reduces the same idea.
        self.assertEqual(size(50, 48, 'stock', dict(config, max_position_pct=5), 10000, 300), 10)
        # Raising the position cap does not override per-idea risk.
        self.assertEqual(size(50, 48, 'stock', dict(config, max_position_pct=100), 10000, 300), 50)

    def test_small_account_needs_headroom_for_a_single_contract(self):
        # A $1.24 contract reserves $124 of premium, so 2% of $1,000 can never fund one.
        small = dict(DEFAULT, budget=1000, risk_pct=2, max_position_pct=25)
        self.assertEqual(size(1.24, .87, 'option', small, 1000, 30), 0)
        # The ceiling now reaches far enough that the same account can size one contract.
        self.assertEqual(size(1.24, .87, 'option', dict(small, risk_pct=15), 1000, 300), 1)

    def test_validation_range_and_default(self):
        self.assertEqual(validate(DEFAULT)['max_position_pct'], 25)
        self.assertEqual(validate(dict(DEFAULT, risk_pct=20, loss_limit_pct=40))['risk_pct'], 20)
        for bad in ({'max_position_pct': 4}, {'max_position_pct': 101}, {'max_position_pct': 'half'},
                    {'risk_pct': 26}, {'loss_limit_pct': 51}, {'risk_pct': 10, 'loss_limit_pct': 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(dict(DEFAULT, **bad))


class JsonRecoveryTests(unittest.TestCase):
    def test_recovers_json_from_prose_and_fences(self):
        payload = {'outlook': 'bullish', 'note': 'a } brace inside a string'}
        body = json.dumps(payload)
        for variant in (body,
                        f'```json\n{body}\n```',
                        f'```\n{body}\n```',
                        f'Here is the report you asked for:\n\n{body}\n\nLet me know if you need more.',
                        f'Sure! Here you go:\n```json\n{body}\n```\nThat covers it.',
                        f'{body}\n\nNote: sources were limited.'):
            with self.subTest(variant=variant[:40]):
                self.assertEqual(extract_json(variant), payload)

    def test_unrecoverable_replies_say_why(self):
        for text, expected in [('', 'no text'), ('I could not complete that request.', 'no JSON object'),
                               ('{"outlook": "bullish", "thesis": [', 'cut off'), ('[1, 2, 3]', 'not an object')]:
            with self.subTest(text=text), self.assertRaises(ValueError) as error:
                extract_json(text)
            self.assertIn(expected, str(error.exception))


def outlook_response(payload, text=None):
    return {'status': 'completed', 'output': [
        {'type': 'web_search_call', 'action': {'sources': [{'url': 'https://example.com/filing'}]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': text or json.dumps(payload)}]}]}


GOOD_OUTLOOK = {'outlook': 'bullish', 'summary': 'A thesis', 'horizon': '12 months', 'thesis': ['Catalyst'],
                'risks': ['Execution'], 'instruments': ['Shares'], 'invalidation': ['Revenue misses'],
                'sources': [{'url': 'https://example.com/filing', 'title': 'Filing', 'published_at': None}]}


class OutlookDiagnosticsTests(unittest.TestCase):
    def test_prose_wrapped_reply_is_accepted(self):
        # Search grounding rules out strict JSON mode, so providers wrap the object in prose.
        wrapped = 'Based on my research:\n```json\n' + json.dumps(GOOD_OUTLOOK) + '\n```\nSources are cited above.'
        self.assertEqual(validate_outlook(outlook_response(None, wrapped))['outlook'], 'bullish')

    def test_free_form_labels_are_mapped_not_rejected(self):
        # Providers answer in prose style; the accepted set stays fixed but the label is matched leniently.
        for given, expected in [('Cautiously Bullish', 'bullish'), ('BEARISH', 'bearish'), ('positive', 'bullish'),
                                ('Neutral', 'mixed'), ('No Conclusion', 'no-conclusion'), ('inconclusive', 'no-conclusion')]:
            with self.subTest(given=given):
                self.assertEqual(validate_outlook(outlook_response(dict(GOOD_OUTLOOK, outlook=given)))['outlook'], expected)

    def test_verdict_leniency_does_not_confuse_similar_labels(self):
        thesis_payload = dict(GOOD_OUTLOOK, verdict='favorable', interpretation='Long NVDA calls',
                              missing=[], trade_request={'symbol': 'NVDA', 'instrument': 'call', 'horizon_end': '2026-10-07'})
        for given, expected in [('Cautiously Favorable', 'cautiously-favorable'), ('Unfavorable', 'unfavorable'),
                                ('favorable', 'favorable'), ('Insufficient Evidence', 'insufficient-evidence')]:
            with self.subTest(given=given):
                self.assertEqual(validate_outlook(outlook_response(dict(thesis_payload, verdict=given)), thesis=True)['verdict'], expected)

    def test_failures_name_the_offending_field(self):
        cases = [(dict(GOOD_OUTLOOK, outlook='banana'), '"outlook" was "banana"'),
                 (dict(GOOD_OUTLOOK, outlook=None), '"outlook" was null'),
                 (dict(GOOD_OUTLOOK, summary=''), '"summary" was missing'),
                 (dict(GOOD_OUTLOOK, thesis='one long string'), '"thesis" was not a list'),
                 (dict(GOOD_OUTLOOK, sources='none'), '"sources" was not a list')]
        for payload, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(DataError) as error:
                validate_outlook(outlook_response(payload))
            self.assertIn(expected, str(error.exception))

    def test_non_json_reply_explains_itself(self):
        with self.assertRaises(DataError) as error:
            validate_outlook(outlook_response(None, 'I was unable to research that topic.'))
        self.assertIn('no JSON object', str(error.exception))

    def test_thesis_fields_are_named_too(self):
        thesis_payload = dict(GOOD_OUTLOOK, verdict='favorable', interpretation='Long NVDA calls for a month',
                              missing=['Strike data'], trade_request={'symbol': 'NVDA', 'instrument': 'call', 'horizon_end': '2026-10-07'})
        self.assertEqual(validate_outlook(outlook_response(thesis_payload), thesis=True)['verdict'], 'favorable')
        with self.assertRaises(DataError) as error:
            validate_outlook(outlook_response(dict(thesis_payload, verdict='banana')), thesis=True)
        self.assertIn('"verdict" was "banana"', str(error.exception))
        with self.assertRaises(DataError) as error:
            validate_outlook(outlook_response(dict(thesis_payload, trade_request={'symbol': 'NVDA', 'instrument': 'call', 'horizon_end': 'next month'})), thesis=True)
        self.assertIn('horizon_end', str(error.exception))


if __name__ == '__main__':
    unittest.main()
