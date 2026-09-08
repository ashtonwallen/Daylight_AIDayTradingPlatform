import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Engine
from session_chat import answer


class SessionChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / 'journal.sqlite3')

    def test_prompt_uses_snapshot_and_only_returns_existing_source_links(self):
        response = {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': json.dumps({
            'answer': 'The session is stopped; quotes may be stale.',
            'source_urls': ['https://example.com/filing', 'https://invented.example/']})}]}]}
        with patch('ai.complete', return_value=response) as call:
            result = answer('What changed?', [], {'running': False, 'evidence': [{'url': 'https://example.com/filing'}]}, lambda: True)
        self.assertFalse(call.call_args.kwargs['web'])
        self.assertFalse(call.call_args.args[1]['session']['running'])
        self.assertEqual(len(result['sources']), 1)

    def test_pause_duplicate_and_cap_block_before_sending(self):
        self.engine.ai_paused = True
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.engine.request_chat('Brief me')
        self.engine.ai_paused = False
        with patch('ai.configured', return_value=True):
            self.engine.chat_running = True
            with self.assertRaisesRegex(ValueError, 'Wait'):
                self.engine.request_chat('Brief me')
            self.engine.chat_running = False
            with patch.object(self.engine, 'reserve_ai', return_value=False):
                with self.assertRaisesRegex(ValueError, 'cap'):
                    self.engine.request_chat('Brief me')
        self.assertEqual(self.engine.chat_messages, [])

    def test_context_and_history_are_bounded_and_exclude_credentials(self):
        self.engine.chat_messages = [{'role': 'user', 'text': str(i)} for i in range(20)]
        self.engine.snapshot = {'instruments': {'SPY': {'bars': [{'t': i} for i in range(40)]}}}
        with patch('ai.configured', return_value=True), patch.object(self.engine, 'state', return_value={
            'config': self.engine.config, 'ai_settings': {'api_key': 'NEVER-SEND'}, 'csrf': 'SECRET'}), patch('session_chat.threading.Thread') as thread:
            self.engine.request_chat('Brief me')
        args = thread.call_args.kwargs['args']
        self.assertEqual(len(args[2]), 12)
        context = args[3]
        self.assertNotIn('ai_settings', context)
        self.assertNotIn('csrf', context)
        self.assertEqual(context['market_data']['instruments']['SPY']['bars'][0]['t'], 32)
        self.assertEqual(self.engine.ai_usage()['requests'], 1)
        self.assertTrue(self.engine.chat_running)

    def test_reply_failure_and_session_change_release_worker(self):
        self.engine.chat_running = True
        with patch('session_chat.answer', return_value={'text': 'A reply', 'sources': []}):
            self.engine.chat_job(self.engine.generation, 'Hi', [], {'captured_at': 'now'})
        self.assertEqual(self.engine.chat_messages[-1]['text'], 'A reply')
        self.assertFalse(self.engine.chat_running)
        with patch('session_chat.answer', side_effect=ValueError('bad response')):
            self.engine.chat_job(self.engine.generation, 'Hi', [], {'captured_at': 'now'})
        self.assertEqual(self.engine.chat_messages[-1]['status'], 'error')
        with patch('session_chat.answer', return_value={'text': 'outdated', 'sources': []}):
            self.engine.chat_job(-1, 'Hi', [], {'captured_at': 'now'})
        self.assertNotEqual(self.engine.chat_messages[-1]['text'], 'outdated')
        self.engine.clear_chat()
        self.assertEqual(self.engine.chat_messages, [])
