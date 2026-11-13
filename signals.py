"""Intraday signals with measured base rates. Read-only; describes history, predicts nothing.

Every signal is a mechanical event on five-minute bars (a level crossed, a range broken, a burst of
volume). The same detector runs over each past session of the same stock, so an alert can say how
often the event has happened and what the price did afterwards, next to what it normally does from
that time of day. Most events turn out no different from an ordinary day, and the alert says so.
"""
import math
import threading
import time
from datetime import datetime, timedelta
from statistics import median

from market import DataError, NY, UTC, now
from optionsview import finite, require

SLOTS = 78                # five-minute bars in a regular session, 9:30 to 16:00
HISTORY_DAYS = 270        # calendar days of five-minute history to request
LOOKBACK = 20             # sessions used for "typical" ranges, volumes and extremes
MIN_CASES = 30            # below this a base rate is reported as too few to judge
PLAN_WINDOW = 30           # minutes after an event during which a plan is still offered

LABELS = {
    'prev_high': ("crossed above yesterday's high", 1), 'prev_low': ("fell below yesterday's low", -1),
    'prev_close_up': ("moved back above yesterday's close", 1), 'prev_close_down': ("dropped below yesterday's close", -1),
    'range_up': ('broke above its first 30 minutes', 1), 'range_down': ('broke below its first 30 minutes', -1),
    'vwap_up': ('reclaimed its average price for the day (VWAP)', 1), 'vwap_down': ('lost its average price for the day (VWAP)', -1),
    'gap_up': ('opened with a gap up', 1), 'gap_down': ('opened with a gap down', -1),
    'gap_fill_down': ('gave back its whole opening gap', -1), 'gap_fill_up': ('recovered its whole opening gap', 1),
    'high_20': ('made a new 20-day high', 1), 'low_20': ('made a new 20-day low', -1),
    'late_high': ('made a new high for the day after 11:00', 1), 'late_low': ('made a new low for the day after 11:00', -1),
    'big_up': ('jumped far more than usual in five minutes', 1), 'big_down': ('dropped far more than usual in five minutes', -1),
    'volume_up': ('rose on a burst of volume', 1), 'volume_down': ('fell on a burst of volume', -1),
    'first_half_up': ('finished its first half hour clearly higher', 1), 'first_half_down': ('finished its first half hour clearly lower', -1),
    'round_up': ('crossed above a round number', 1), 'round_down': ('fell below a round number', -1),
    'wide_day': ('has already moved more than a typical full day', 0),
    'turned_up': ('turned positive after being down 1% or more', 1), 'turned_down': ('turned negative after being up 1% or more', -1),
    'streak_up': ('opened after three or more up days in a row', 1), 'streak_down': ('opened after three or more down days in a row', -1),
}


def round_step(price):
    return 5 if price < 100 else 10 if price < 1000 else 50


def sessions_from(bars):
    """Group five-minute bars (epoch-millisecond 't') into regular sessions keyed by New York date."""
    days = {}
    for bar in bars:
        if not all(finite(bar.get(k)) for k in ('t', 'o', 'h', 'l', 'c')):
            continue
        local = datetime.fromtimestamp(bar['t'] / 1000, UTC).astimezone(NY)
        slot = ((local.hour * 60 + local.minute) - 570) // 5
        if 0 <= slot < SLOTS and local.weekday() < 5:
            days.setdefault(str(local.date()), {})[slot] = {'slot': slot, 'o': bar['o'], 'h': bar['h'], 'l': bar['l'],
                                                            'c': bar['c'], 'v': bar.get('v') or 0}
    out = []
    for day in sorted(days):
        rows = [days[day][s] for s in sorted(days[day])]
        out.append({'date': day, 'bars': rows, 'open': rows[0]['o'], 'close': rows[-1]['c'],
                    'high': max(b['h'] for b in rows), 'low': min(b['l'] for b in rows)})
    return out


def context(previous):
    """What 'usual' means going into a session, from the sessions before it."""
    recent = previous[-LOOKBACK:]
    if not recent:
        return None
    last = recent[-1]
    bar_ranges = [(b['h'] - b['l']) / b['c'] for s in recent for b in s['bars'] if b['c']]
    volume = {}
    for s in recent:
        for b in s['bars']:
            volume.setdefault(b['slot'], []).append(b['v'])
    streak = 0
    closes = [s['close'] for s in previous[-8:]]
    for a, b in zip(reversed(closes[:-1]), reversed(closes[1:])):
        step = 1 if b > a else -1 if b < a else 0
        if step == 0 or (streak and (step > 0) != (streak > 0)):
            break
        streak += step
    return {'prev_high': last['high'], 'prev_low': last['low'], 'prev_close': last['close'],
            'high_20': max(s['high'] for s in recent), 'low_20': min(s['low'] for s in recent),
            'day_range': median((s['high'] - s['low']) / s['open'] for s in recent),
            'bar_range': median(bar_ranges) if bar_ranges else 0,
            'volume': {slot: sum(v) / len(v) for slot, v in volume.items()}, 'streak': streak,
            'full': len(recent) >= LOOKBACK}


def detect(session, ctx):
    """Every event in one session, each fired at most once, in time order."""
    events, fired = [], set()

    def fire(key, bar, level=None):
        if key not in fired:
            fired.add(key)
            events.append({'key': key, 'slot': bar['slot'], 'price': bar['c'], 'level': level, 'direction': LABELS[key][1]})

    bars = session['bars']
    first = bars[0]
    gap = first['o'] / ctx['prev_close'] - 1
    if gap >= 0.005:
        fire('gap_up', first, ctx['prev_close'])
    elif gap <= -0.005:
        fire('gap_down', first, ctx['prev_close'])
    if ctx['streak'] >= 3:
        fire('streak_up', first)
    elif ctx['streak'] <= -3:
        fire('streak_down', first)
    high, low = first['h'], first['l']
    range_high = range_low = None
    money = volume = 0.0
    side = None          # which side of VWAP the last several closes were on
    held = 0
    previous_close = first['o']
    step = round_step(first['o'])
    for bar in bars:
        slot, close = bar['slot'], bar['c']
        typical = (bar['h'] + bar['l'] + close) / 3
        money += typical * bar['v']; volume += bar['v']
        vwap = money / volume if volume else close
        if slot <= 5:
            range_high = bar['h'] if range_high is None else max(range_high, bar['h'])
            range_low = bar['l'] if range_low is None else min(range_low, bar['l'])
        if slot == 5:
            move = close / first['o'] - 1
            if move >= 0.003:
                fire('first_half_up', bar)
            elif move <= -0.003:
                fire('first_half_down', bar)
        # Levels carried in from earlier sessions.
        if previous_close <= ctx['prev_high'] < close:
            fire('prev_high', bar, ctx['prev_high'])
        if previous_close >= ctx['prev_low'] > close:
            fire('prev_low', bar, ctx['prev_low'])
        if slot > 0 and previous_close < ctx['prev_close'] <= close:
            fire('gap_fill_up' if gap <= -0.005 else 'prev_close_up', bar, ctx['prev_close'])
        if slot > 0 and previous_close > ctx['prev_close'] >= close:
            fire('gap_fill_down' if gap >= 0.005 else 'prev_close_down', bar, ctx['prev_close'])
        if ctx['full'] and close > ctx['high_20']:
            fire('high_20', bar, ctx['high_20'])
        if ctx['full'] and close < ctx['low_20']:
            fire('low_20', bar, ctx['low_20'])
        # Levels made today.
        if slot > 5 and range_high is not None:
            if close > range_high:
                fire('range_up', bar, range_high)
            if close < range_low:
                fire('range_down', bar, range_low)
        if slot >= 18:
            if bar['h'] > high:
                fire('late_high', bar, high)
            if bar['l'] < low:
                fire('late_low', bar, low)
        if slot >= 6:
            now_side = 1 if close > vwap else -1
            if side is not None and now_side != side and held >= 6:
                fire('vwap_up' if now_side > 0 else 'vwap_down', bar, round(vwap, 2))
            held = held + 1 if now_side == side else 1
            side = now_side
        # Unusual bars.
        change = close / previous_close - 1 if previous_close else 0
        if ctx['bar_range'] and abs(change) >= max(4 * ctx['bar_range'], 0.004):
            fire('big_up' if change > 0 else 'big_down', bar)
        usual = ctx['volume'].get(slot)
        if usual and bar['v'] >= 3 * usual and abs(change) >= ctx['bar_range']:
            fire('volume_up' if change > 0 else 'volume_down', bar)
        crossed = math.floor(close / step) - math.floor(previous_close / step)
        if slot > 0 and crossed:
            fire('round_up' if crossed > 0 else 'round_down', bar, (math.floor(max(close, previous_close) / step)) * step)
        high, low = max(high, bar['h']), min(low, bar['l'])
        if ctx['day_range'] and (high - low) / first['o'] > ctx['day_range']:
            fire('wide_day', bar)
        if low <= first['o'] * 0.99 and previous_close < first['o'] <= close:
            fire('turned_up', bar, first['o'])
        if high >= first['o'] * 1.01 and previous_close > first['o'] >= close:
            fire('turned_down', bar, first['o'])
        previous_close = close
    return events


def after(session, slot, price):
    """Return from a price to the close, and over the next half hour, for the bar at or after a slot."""
    later = [b for b in session['bars'] if b['slot'] >= slot + 6]
    return session['close'] / price - 1, (later[0]['c'] / price - 1 if later else None)


def base_rates(history):
    """For each event key: outcomes after every past occurrence, and the ordinary outcome from the same time of day."""
    table, ordinary = {}, {}
    for index in range(1, len(history)):
        session = history[index]
        ctx = context(history[:index])
        if not ctx:
            continue
        for bar in session['bars']:
            ordinary.setdefault(bar['slot'], []).append(session['close'] / bar['c'] - 1)
        for event in detect(session, ctx):
            if event['slot'] >= SLOTS - 1:
                continue
            to_close, half_hour = after(session, event['slot'], event['price'])
            table.setdefault(event['key'], []).append({'slot': event['slot'], 'to_close': to_close, 'half_hour': half_hour})
    return table, ordinary


def describe(event, table, ordinary, sessions):
    """Attach the base rate and a plain verdict to one of today's events."""
    cases = table.get(event['key'], [])
    direction = event['direction'] or 1
    normal = ordinary.get(event['slot'], [])
    n = len(cases)
    stats = {'cases': n, 'sessions': sessions}
    if n:
        returns = [c['to_close'] for c in cases]
        halves = [c['half_hour'] for c in cases if c['half_hour'] is not None]
        stats.update(continued_pct=round(100 * sum(direction * r > 0 for r in returns) / n),
                     median_to_close_pct=round(100 * median(returns), 2),
                     median_half_hour_pct=round(100 * median(halves), 2) if halves else None)
    if normal:
        usual = sum(direction * r > 0 for r in normal) / len(normal)
        stats.update(usual_pct=round(100 * usual), usual_median_pct=round(100 * median(normal), 2))
        if n >= MIN_CASES and 0 < usual < 1:
            z = (stats['continued_pct'] / 100 - usual) / math.sqrt(usual * (1 - usual) / n)
            stats['z'] = round(z, 1)
    if not event['direction']:
        verdict, lean = 'note', 'A measure of how active the day is, with no direction.'
    elif n < MIN_CASES:
        verdict, lean = 'few', f'Only {n} past case{"" if n == 1 else "s"}: too few to say anything.'
    elif stats.get('z', 0) >= 2:
        verdict = 'continues'
        lean = 'In the past the move kept going into the close more often than on an ordinary day.'
    elif stats.get('z', 0) <= -2:
        verdict = 'fades'
        lean = 'In the past the move reversed by the close more often than on an ordinary day.'
    else:
        verdict, lean = 'ordinary', 'In the past this was no different from an ordinary day.'
    return dict(event, stats=stats, verdict=verdict, lean=lean)


def analyse(symbol, bars, today=None):
    """Today's events for one symbol, each with its history. `bars` are five-minute bars, oldest first."""
    sessions = sessions_from(bars)
    if len(sessions) < LOOKBACK + 2:
        raise DataError(f'{symbol}: not enough five-minute history for base rates.')
    today = today or sessions[-1]['date']
    history = [s for s in sessions if s['date'] < today]
    current = next((s for s in sessions if s['date'] == today), None)
    if not current:
        return {'symbol': symbol, 'date': today, 'sessions': len(history), 'events': []}
    table, ordinary = base_rates(history)
    ctx = context(history)
    events = []
    for event in detect(current, ctx):
        minutes = 570 + event['slot'] * 5 + 5      # an event is known when its bar closes
        row = describe(event, table, ordinary, len(history) - 1)
        row.update(symbol=symbol, id=f"{today}:{symbol}:{event['key']}", what=LABELS[event['key']][0],
                   time=f'{minutes // 60}:{minutes % 60:02d}', date=today, day_range=ctx['day_range'],
                   last=current['close'],
                   minutes_ago=(current['bars'][-1]['slot'] - event['slot']) * 5)
        row['lean_direction'] = lean_direction(row)
        events.append(row)
    return {'symbol': symbol, 'date': today, 'sessions': len(history) - 1, 'events': events}


class Signals:
    """Keeps each symbol's history for the day and refreshes only today's bars."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.history = {}       # symbol -> (date fetched, bars before today)
        self.result = None
        self.result_at = 0
        self.lock = threading.Lock()

    def bars(self, provider, symbol, at):
        fetch = require(provider, 'five_minute_bars')
        today = at.astimezone(NY).date()
        cached = self.history.get(symbol)
        if not cached or cached[0] != today:
            start = datetime.combine(today - timedelta(days=HISTORY_DAYS), datetime.min.time(), NY)
            midnight = datetime.combine(today, datetime.min.time(), NY).timestamp() * 1000
            cached = (today, [b for b in fetch(symbol, start) if b['t'] < midnight])
            self.history[symbol] = cached
        latest = fetch(symbol, datetime.combine(today - timedelta(days=4), datetime.min.time(), NY))
        known = cached[1][-1]['t'] if cached[1] else 0
        return cached[1] + [b for b in latest if b['t'] > known]

    def scan(self, provider, symbols, at=None):
        at = at or now()
        with self.lock:
            if self.result and self.clock() - self.result_at < 45 and self.result['symbols'] == symbols:
                return self.result
            events, errors, sessions, date = [], [], 0, None
            for symbol in symbols:
                try:
                    found = analyse(symbol, self.bars(provider, symbol, at))
                    events.extend(found['events'])
                    sessions, date = max(sessions, found['sessions']), found['date']
                except DataError as exc:
                    errors.append(str(exc))
            events.sort(key=lambda e: (e['time'].zfill(5), e['symbol']), reverse=True)
            self.result = {'date': date, 'symbols': symbols, 'events': events, 'errors': errors, 'sessions': sessions,
                           'kinds': len(LABELS)}
            self.result_at = self.clock()
            return self.result


def lean_direction(event):
    """+1 when history leans up after this event, -1 when it leans down, 0 when it says nothing."""
    if event['verdict'] == 'continues':
        return event['direction']
    if event['verdict'] == 'fades':
        return -event['direction']
    return 0


def plan(event, last, day_range, config, available, risk_left):
    """A sized purchase for an event whose history leans up. Rules only; nothing is placed.

    The stop sits half a typical day's range below the price and the target twice that above it,
    and the share count is whatever keeps the loss at the stop inside the session's risk setting.
    """
    from strategy import size
    lean = lean_direction(event)
    if lean and event.get('minutes_ago', 0) > PLAN_WINDOW:
        return {'side': 'up' if lean > 0 else 'down', 'note': f'This fired more than {PLAN_WINDOW} minutes ago, so no plan is drawn from it now.'}
    if lean < 0:
        return {'side': 'down', 'note': 'History leans down from here. Daylight only plans purchases; a put is the matching instrument (Options tab).'}
    if lean == 0 or not finite(last) or last <= 0:
        return None
    distance = max(last * (day_range or 0) * 0.5, last * 0.003)
    stop, target = round(last - distance, 2), round(last + 2 * distance, 2)
    quantity = size(last, stop, 'stock', config, available, risk_left)
    if quantity < 1:
        return {'side': 'up', 'note': 'History leans up, but one share does not fit inside your budget and risk settings.'}
    return {'side': 'up', 'entry': round(last, 2), 'stop': stop, 'target': target, 'quantity': quantity,
            'cost': round(quantity * last, 2), 'risk': round(quantity * (last - stop), 2),
            'reward': round(quantity * (target - last), 2)}
