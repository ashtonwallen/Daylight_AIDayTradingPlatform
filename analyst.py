"""An AI read of the day's signals against the bigger picture. One request, on demand.

The detectors in signals.py say what happened and how often it mattered before. This step hands
those facts, with the wider market, the session's news and research, and recent option flags, to
the selected model and asks what it makes of them. Code still outranks the model: a signal can only
be marked "act" if the rules already produced a sized plan for it, and every read must point at a
signal that was actually supplied.
"""
import threading

import ai
import extras
import signals
from market import DataError, now, stamp

STANCES = ('act', 'watch', 'ignore')
PROMPT = '''You are a market analyst reviewing mechanical intraday signals for one private user.
Each signal is an event on five-minute bars with its measured history in the same stock: how many
times it happened ("cases"), how often the move then continued into the close ("continued_pct"),
how often that happens from the same time on any day ("usual_pct"), and the median move to the close.
"verdict" is the statistical read: continues, fades, ordinary, few (too few cases) or note.
A "plan" with entry, stop and target exists only where the rules allow a purchase.

Think about the signals together and in context, not one at a time:
- What is the broad market doing (index moves supplied), and do the signals agree with it or fight it?
- Do several signals in one stock, or the same signal across stocks, tell one story?
- Does the supplied news, research or option activity explain a move or contradict it?
- Is a statistical lean believable here, or is it a small sample or one of many tests that passed by luck?
If web search is available, check today's main market driver and any news on the stocks with the
strongest signals, and say what you found. Never invent prices, statistics or news.

Return JSON:
overview: 2-4 sentences on the day's bigger picture and what the signals say as a group.
reads: up to 8 items, most important first, each with
  id (exact signal id supplied), stance (act, watch or ignore),
  read (2-3 sentences: what this likely is and why it does or does not matter today),
  confirm (one sentence: what would have to happen next to support it),
  wrong (one sentence: what would show this read is wrong).
Use "act" only for a signal that has a plan with an entry and that you judge supported by the
context; otherwise "watch" or "ignore". Most signals deserve "ignore"; say so plainly.
These are research notes, not instructions. No guarantees, no price predictions.'''


def compact(event):
    stats = event.get('stats', {})
    plan = event.get('plan') or {}
    return {'id': event['id'], 'symbol': event['symbol'], 'time_et': event['time'], 'what': event['what'],
            'direction': event['direction'], 'level': event.get('level'), 'price_now': event.get('last'),
            'minutes_ago': event.get('minutes_ago'), 'verdict': event['verdict'],
            'cases': stats.get('cases'), 'continued_pct': stats.get('continued_pct'), 'usual_pct': stats.get('usual_pct'),
            'median_to_close_pct': stats.get('median_to_close_pct'), 'usual_median_pct': stats.get('usual_median_pct'),
            'plan': {k: plan[k] for k in ('entry', 'stop', 'target', 'quantity', 'risk') if k in plan} or None}


def choose(events, limit=30):
    """Signals worth the model's attention: those with a lean first, then the most recent."""
    leaning = [e for e in events if e['verdict'] in ('continues', 'fades')]
    rest = [e for e in events if e['verdict'] not in ('continues', 'fades')]
    return (leaning + rest)[:limit]


def validate(result, events):
    """Keep only reads that point at a supplied signal, and never let the model promote one past the rules."""
    if not isinstance(result, dict) or not isinstance(result.get('overview'), str) or not isinstance(result.get('reads'), list):
        raise DataError('The analyst returned an unusable answer.')
    by_id = {e['id']: e for e in events}
    reads = []
    for item in result['reads'][:8]:
        if not isinstance(item, dict) or item.get('id') not in by_id or item.get('stance') not in STANCES:
            continue
        if not all(isinstance(item.get(k), str) for k in ('read', 'confirm', 'wrong')):
            continue
        event, stance, lowered = by_id[item['id']], item['stance'], None
        if stance == 'act' and not (event.get('plan') or {}).get('entry'):
            stance, lowered = 'watch', 'Lowered from "act": the rules produced no sized plan for this signal.'
        reads.append({'id': item['id'], 'symbol': event['symbol'], 'what': event['what'], 'time': event['time'],
                      'stance': stance, 'lowered': lowered, 'read': item['read'][:700],
                      'confirm': item['confirm'][:300], 'wrong': item['wrong'][:300]})
    return {'overview': result['overview'][:1200], 'reads': reads}


class SignalAnalystMixin:
    def analyst_context(self, scan):
        provider = self.data_provider()
        try:
            market = extras.live(provider, ['SPY', 'QQQ', 'IWM', 'DIA'])['quotes']
        except (DataError, ValueError):
            market = {}
        with self.lock:
            snapshot = self.snapshot or {}
            news = [{'headline': str(n.get('headline', ''))[:200], 'symbols': n.get('symbols', [])[:5], 'at': n.get('created_at')}
                    for n in snapshot.get('news', [])[:12]]
            research = [{'lane': r.get('agent'), 'conclusion': str(r.get('conclusion', ''))[:500]}
                        for r in self.reports if r.get('conclusion')][:4]
            settings = {k: self.config[k] for k in ('budget', 'risk_pct', 'max_position_pct')}
        with self.db() as db:
            day, flags = extras.last_recorded(db)
        return {'as_of': stamp(now()), 'session_date': scan.get('date'), 'history_sessions': scan.get('sessions'),
                'index_moves_pct': {s: q.get('change_pct') for s, q in market.items()},
                'signals': [compact(e) for e in choose(scan['events'])],
                'news_from_this_session': news, 'research_from_this_session': research,
                'option_flags': {'day': day, 'contracts': [{k: f.get(k) for k in ('underlying', 'type', 'strike', 'expiration', 'volume', 'open_interest', 'premium')} for f in flags[:8]]},
                'user_limits': settings,
                'note': 'Signals marked ordinary did nothing unusual historically. With this many tests, about one in twenty will look significant by luck.'}

    def request_signal_review(self):
        with self.lock:
            if self.ai_paused:
                raise ValueError('AI is paused. Resume AI to have the signals reviewed.')
            if not ai.configured():
                raise ValueError('Configure an AI provider first.')
            if self.signal_reviewing:
                raise ValueError('A signal review is already running.')
        scan = self.signal_scan()
        if not scan['events']:
            raise ValueError('There are no signals to review yet.')
        context = self.analyst_context(scan)
        with self.lock:
            if not self.reserve_ai(1):
                raise ValueError('The shared daily AI request cap is reached.')
            self.signal_reviewing = True
            self.signal_review = {'status': 'running', 'signals': len(context['signals'])}
            self.trace('Signal review', 'running', f"Reviewing {len(context['signals'])} signals against the wider market; one AI request reserved.")
        threading.Thread(target=self.signal_review_job, args=(context, scan['events']), daemon=True).start()

    def signal_review_job(self, context, events):
        try:
            web = bool(ai.settings().get('web_research'))
            result = validate(ai.json_result(PROMPT, context, can_send=self.ai_allowed, web=web, max_tokens=3500), events)
            result.update(status='complete', web=web)
        except Exception as exc:
            result = {'status': 'error', 'message': str(exc)[:200] if isinstance(exc, DataError) else 'No review was produced. Check the AI provider and try again.'}
        with self.lock:
            self.signal_review = dict(result, at=stamp(now()), date=context['session_date'], signals=len(context['signals']))
            self.signal_reviewing = False
            self.trace('Signal review', 'complete' if result['status'] == 'complete' else 'error', 'Signal review finished.')


__all__ = ['SignalAnalystMixin', 'validate', 'choose', 'compact', 'signals']
