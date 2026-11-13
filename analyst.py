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
trades: 0 to 3 trade suggestions, best first. Suggest one only when the signals and the wider
  context point the same way; an empty list is the right answer on most days. Each has
  symbol (one of the supplied stocks), instrument ("shares" to buy stock, "call" for an upward view
  with options, "put" for a downward view), reason (2-3 sentences tying it to the signals and context),
  based_on (ids of the signals it rests on),
  for shares: stop and target as prices either side of price_now, the target at least as far away as the stop,
  for options: horizon_days (how long the view needs, 2 to 45).
  Purchases only: no short selling, no spreads. Software will re-price every suggestion against the
  live quote, pick the option contract, size it to user_limits and reject anything that does not fit,
  so do not state share counts or contract symbols.
These are research notes for the user to decide on, not instructions. No guarantees, no price predictions.'''


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
            available, risk_left, _ = self.allowances()
            limits = (dict(self.config), available, risk_left)
        threading.Thread(target=self.signal_review_job, args=(context, scan['events'], limits), daemon=True).start()

    def trade_evidence(self, provider, trade, events, limits):
        """Everything software can measure about one suggestion before the model is asked to stand by it."""
        import optionsview
        symbol, way = trade['symbol'], -1 if trade['instrument'] == 'put' else 1
        facts = {'symbol': symbol, 'instrument': trade['instrument'], 'reason_given': trade['reason']}
        try:
            trade['check'] = check_trade(provider, trade, *limits)
        except (DataError, KeyError, ValueError, TypeError) as exc:
            trade['check'] = {'status': 'rejected', 'reason': 'Could not be checked: ' + str(exc)[:160]}
        facts['pricing_and_size'] = trade['check']
        facts['all_signals_in_this_stock_today'] = [compact(e) for e in events if e['symbol'] == symbol][:12]
        keys = [i.rsplit(':', 1)[1] for i in trade['based_on'] if i.split(':')[1] == symbol]
        history = self.signal_engine.history.get(symbol)
        if keys and history:
            trade['joint'] = facts['when_these_signals_fired_together_before'] = signals.joint_rate(history[1], keys, way)
        try:
            quotes = extras.live(provider, [symbol, 'SPY'])['quotes']
            facts['today_pct'] = {s: q.get('change_pct') for s, q in quotes.items()}
        except (DataError, ValueError):
            pass
        try:
            chain = optionsview.chain(provider, symbol)
            totals = chain['totals']
            facts['options_market'] = {'call_volume': totals['call_volume'], 'put_volume': totals['put_volume'],
                                       'contracts_trading_far_above_open_interest': [
                                           {k: u.get(k) for k in ('type', 'strike', 'expiration', 'volume', 'open_interest', 'premium')}
                                           for u in chain['unusual'][:4]]}
        except (DataError, ValueError):
            pass
        return facts

    def signal_review_job(self, context, events, limits):
        try:
            web = bool(ai.settings().get('web_research'))
            raw = ai.json_result(PROMPT, context, can_send=self.ai_allowed, web=web, max_tokens=4000)
            result = validate(raw, events)
            provider = self.data_provider()
            trades = clean_trades(raw, {e['symbol'] for e in events}, {e['id'] for e in events})
            evidence = [self.trade_evidence(provider, t, events, limits) for t in trades]
            tested = False
            if trades:
                with self.lock:
                    allowed = self.reserve_ai(1)
                    if allowed:
                        self.trace('Signal review', 'running', f'Testing {len(trades)} suggested trade(s) against measured evidence; one more AI request reserved.')
                if allowed:
                    try:
                        verdicts = ai.json_result(TEST_PROMPT, {'as_of': stamp(now()), 'overview_you_wrote': result['overview'],
                                                                'index_moves_pct': context['index_moves_pct'], 'suggestions': evidence},
                                                  can_send=self.ai_allowed, web=web, max_tokens=3000)
                        apply_tests(trades, verdicts)
                        tested = True
                    except Exception:
                        pass
            for trade in trades:
                trade.setdefault('test', {'verdict': 'untested', 'confidence': None, 'tested': 'The second pass did not run, so this suggestion was not challenged.', 'sources': []})
            trades.sort(key=lambda t: (t['test']['verdict'] != 'keep', t['check'].get('status') != 'ok'))
            result.update(status='complete', web=web, trades=trades, tested=tested, requests=2 if tested else 1)
        except Exception as exc:
            result = {'status': 'error', 'message': str(exc)[:200] if isinstance(exc, DataError) else 'No review was produced. Check the AI provider and try again.'}
        with self.lock:
            self.signal_review = dict(result, at=stamp(now()), date=context['session_date'], signals=len(context['signals']))
            self.signal_reviewing = False
            self.trace('Signal review', 'complete' if result['status'] == 'complete' else 'error', 'Signal review finished.')

INSTRUMENTS = {'shares': 'stock', 'call': 'call', 'put': 'put'}


def clean_trades(result, symbols, signal_ids):
    """The model's trade suggestions, reduced to the fields the checks understand."""
    out = []
    for item in (result.get('trades') if isinstance(result.get('trades'), list) else [])[:3]:
        if not isinstance(item, dict) or item.get('symbol') not in symbols or item.get('instrument') not in INSTRUMENTS:
            continue
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            continue
        number = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        days = item.get('horizon_days')
        out.append({'symbol': item['symbol'], 'instrument': item['instrument'], 'reason': item['reason'][:600],
                    'stop': number(item.get('stop')), 'target': number(item.get('target')),
                    'horizon_days': int(days) if isinstance(days, (int, float)) and not isinstance(days, bool) and 1 <= days <= 60 else 14,
                    'based_on': [i for i in (item.get('based_on') or []) if i in signal_ids][:4] if isinstance(item.get('based_on'), list) else []})
    return out


def check_trade(provider, trade, config, available, risk_left, select=None, at=None):
    """Price a suggestion against the live quote and size it inside the session's limits.

    The model chooses the idea; this decides whether it is tradeable and how big it may be.
    Anything that fails comes back as 'rejected' with the reason, never silently adjusted.
    """
    from datetime import timedelta
    from market import NY
    from strategy import size
    if select is None:
        from contracts import select
    at = at or now()
    reject = lambda reason: {'status': 'rejected', 'reason': reason}
    if trade['instrument'] == 'shares':
        card = select(provider, {'symbol': trade['symbol'], 'instrument': 'stock'}, available)
        if card.get('status') != 'candidate':
            return reject(card.get('reason') or 'No usable quote.')
        entry, stop, target = card['entry_reference'], trade['stop'], trade['target']
        if stop is None or target is None:
            return reject('The suggestion gave no stop or target price.')
        if not stop < entry < target:
            return reject(f'Stop {stop:.2f} and target {target:.2f} do not sit either side of the current price {entry:.2f}.')
        risk_pct = (entry - stop) / entry
        if not 0.002 <= risk_pct <= 0.10:
            return reject(f'The stop is {risk_pct:.1%} from the price; Daylight accepts 0.2% to 10%.')
        if target - entry < entry - stop:
            return reject('The target is closer than the stop, so the possible gain is smaller than the possible loss.')
        quantity = size(entry, stop, 'stock', config, available, risk_left)
        if quantity < 1:
            return reject('One share does not fit inside your budget and risk settings.')
        return {'status': 'ok', 'entry': round(entry, 2), 'stop': round(stop, 2), 'target': round(target, 2), 'quantity': quantity,
                'cost': round(quantity * entry, 2), 'risk': round(quantity * (entry - stop), 2),
                'reward': round(quantity * (target - entry), 2), 'quote_state': card.get('quote_state'), 'quote_at': card.get('quote_at')}
    horizon = (at.astimezone(NY).date() + timedelta(days=trade['horizon_days'])).isoformat()
    card = select(provider, {'symbol': trade['symbol'], 'instrument': INSTRUMENTS[trade['instrument']], 'horizon_end': horizon}, available)
    if card.get('status') != 'candidate':
        return reject(card.get('reason') or 'No suitable contract.')
    quantity = size(card['premium_per_share'], 0, 'option', config, available, risk_left)
    if quantity < 1:
        return reject(f"One contract costs ${card['cost_per_contract']:,.2f}, more than your budget and risk settings allow.")
    cost = round(quantity * card['cost_per_contract'], 2)
    return {'status': 'ok', 'contract': card['symbol'], 'expiration': card['expiration'], 'strike': card['strike'],
            'premium': card['premium_per_share'], 'delta': card.get('delta'), 'quantity': quantity, 'cost': cost, 'risk': cost,
            'quote_state': card.get('quote_state'), 'quote_at': card.get('quote_at')}


TEST_PROMPT = '''Earlier you suggested the trades below. Software has now measured each one. Your job is to try
to knock each suggestion down, then say honestly whether it still stands.

For each suggestion you are given: pricing_and_size (the live re-pricing; "rejected" means it cannot
be taken as proposed), every signal in that stock today with its history,
when_these_signals_fired_together_before (how often the price then went the trade's way; "enough"
is false when there are too few cases to trust), today's move in the stock and in SPY, and what the
options market is doing in that stock.

If web search is available, use it to check the news or catalyst your reason relied on, and look for
anything that contradicts the idea (earnings date inside the horizon, a scheduled event, a
downgrade). Report what you actually found. Never invent a source.

Return JSON: tests: one item per suggestion, in the same order, each with
  symbol, verdict ("keep" or "drop"), confidence ("low", "medium" or "high"),
  tested (2-4 sentences: what the measured evidence and your search showed, including what argues against it),
  sources (up to 3 URLs you actually opened for the news you relied on; empty if none).
Drop a suggestion when pricing_and_size is rejected, when the evidence is thin or mixed, or when the
news you relied on does not check out. Keeping nothing is a good answer.'''


def apply_tests(trades, result):
    """Attach the second-pass verdicts. A suggestion the checks rejected can never come back as kept."""
    items = result.get('tests') if isinstance(result, dict) and isinstance(result.get('tests'), list) else []
    for trade, item in zip(trades, items):
        if not isinstance(item, dict) or item.get('symbol') != trade['symbol'] or item.get('verdict') not in ('keep', 'drop'):
            continue
        verdict = item['verdict']
        note = item.get('tested') if isinstance(item.get('tested'), str) else ''
        if verdict == 'keep' and trade.get('check', {}).get('status') != 'ok':
            verdict, note = 'drop', (note + ' Dropped because it did not pass the pricing and size checks.').strip()
        sources = [s for s in (item.get('sources') or []) if isinstance(s, str) and s.startswith(('https://', 'http://'))][:3] if isinstance(item.get('sources'), list) else []
        trade['test'] = {'verdict': verdict, 'confidence': item.get('confidence') if item.get('confidence') in ('low', 'medium', 'high') else None,
                         'tested': note[:900], 'sources': sources}
