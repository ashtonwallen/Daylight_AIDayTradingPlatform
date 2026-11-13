"""Live prices, the morning brief, idea replay, unusual-activity history and position what-ifs.

All read-only. Nothing here suggests or places a trade; it reports what the market did or what a
position would be worth under stated assumptions.
"""
import json
import math
import re
from datetime import datetime, time as clock, timedelta

from market import NY, UTC, now, parse, stamp
from optionsview import finite, require

RATE = 0.04           # risk-free rate assumed by the what-if maths
MOVES = (-0.05, 0.0, 0.05)
LATER_DAYS = 7
OCC = re.compile(r'([A-Z]+)\s*(\d{6})([CP])(\d{8})')


# ---- unusual-activity history ----------------------------------------------------------------

def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS unusual (day TEXT, contract TEXT, payload TEXT, PRIMARY KEY (day, contract))')


def trading_day(at=None):
    """The session a reading belongs to: today once the market has opened, else the last weekday."""
    local = (at or now()).astimezone(NY)
    day = local.date()
    if local.time() < clock(9, 30):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return str(day)


def record_unusual(db, rows, at=None):
    day = trading_day(at)
    for row in rows:
        if row.get('symbol'):
            db.execute('INSERT OR REPLACE INTO unusual VALUES (?, ?, ?)', (day, row['symbol'], json.dumps(row)))
    return day


def flag_counts(db, lookback_days=14, at=None):
    """How many separate sessions each contract was flagged on recently."""
    since = str((at or now()).astimezone(NY).date() - timedelta(days=lookback_days))
    return dict(db.execute('SELECT contract, COUNT(*) FROM unusual WHERE day >= ? GROUP BY contract', (since,)).fetchall())


def with_history(db, rows, at=None):
    counts = flag_counts(db, at=at)
    return [dict(row, days_flagged=counts.get(row.get('symbol'), 0)) for row in rows]


def repeats(db, at=None):
    """Contracts flagged on two or more sessions in the last two weeks, most persistent first."""
    since = str((at or now()).astimezone(NY).date() - timedelta(days=14))
    found = db.execute('''SELECT contract, COUNT(*), MIN(day), MAX(day) FROM unusual WHERE day >= ?
                          GROUP BY contract HAVING COUNT(*) >= 2 ORDER BY COUNT(*) DESC, MAX(day) DESC LIMIT 25''', (since,)).fetchall()
    out = []
    for contract, count, first, last in found:
        payload = db.execute('SELECT payload FROM unusual WHERE contract = ? AND day = ?', (contract, last)).fetchone()
        out.append(dict(json.loads(payload[0]), days_flagged=count, first_day=first, last_day=last))
    return out


def last_recorded(db, before=None):
    """The most recent session with flags, optionally strictly before a given day."""
    query = 'SELECT MAX(day) FROM unusual' + (' WHERE day < ?' if before else '')
    day = db.execute(query, (before,) if before else ()).fetchone()[0]
    if not day:
        return None, []
    rows = [json.loads(p[0]) for p in db.execute('SELECT payload FROM unusual WHERE day = ?', (day,)).fetchall()]
    return day, sorted(rows, key=lambda r: -(r.get('premium') or 0))[:12]


# ---- live prices and the morning brief -------------------------------------------------------

def live(provider, symbols):
    symbols = [s for s in dict.fromkeys(symbols) if re.fullmatch('[A-Z]{1,5}', s or '')][:30]
    if not symbols:
        return {'quotes': {}, 'at': stamp(now())}
    return {'quotes': require(provider, 'live_quotes')(symbols), 'at': stamp(now())}


def brief(provider, symbols, db, at=None):
    at = at or now()
    quotes = live(provider, symbols)['quotes']
    try:
        daily = provider.daily_history(symbols[:12], at)
    except Exception:
        daily = {}
    try:
        session = provider.session(at)
    except Exception:
        session = None
    rows = []
    for symbol in symbols:
        q = quotes.get(symbol)
        if not q or not finite(q.get('last')) or not finite(q.get('prev_close')) or not q['prev_close']:
            continue
        closes = [b['c'] for b in daily.get(symbol, []) if finite(b.get('c'))]
        rows.append({'symbol': symbol, 'last': q['last'], 'prev_close': q['prev_close'],
                     'gap_pct': round((q['last'] / q['prev_close'] - 1) * 100, 2),
                     'five_day_pct': round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) > 5 else None,
                     'quote_at': q.get('time')})
    rows.sort(key=lambda r: -abs(r['gap_pct']))
    today = trading_day(at)
    # Before the open, the latest flags are yesterday's. During the day, show the previous session's.
    day, flagged = last_recorded(db, before=today if session and session.get('is_open') else None)
    return {'at': stamp(at), 'session': session, 'moves': rows, 'missing': [s for s in symbols if s not in {r['symbol'] for r in rows}],
            'unusual_day': day, 'unusual': flagged}


# ---- idea replay ---------------------------------------------------------------------------

def recent_ideas(db, limit=40):
    """Past ideas from saved scans. The same idea repeats every scan, so keep its first appearance."""
    seen, out = {}, []
    for (payload,) in db.execute('SELECT payload FROM scans ORDER BY at DESC LIMIT 600').fetchall():
        for idea in json.loads(payload).get('ideas', []):
            key = (idea.get('symbol'), idea.get('direction'), str(idea.get('created_at'))[:10])
            seen[key] = idea  # scans are newest first, so the last one written is the earliest
    for idea in sorted(seen.values(), key=lambda i: str(i.get('created_at')), reverse=True)[:limit]:
        out.append({k: idea.get(k) for k in ('id', 'symbol', 'underlying', 'asset', 'direction', 'entry', 'stop', 'target', 'created_at')})
    return out


def find_idea(db, idea_id):
    for (payload,) in db.execute('SELECT payload FROM scans ORDER BY at DESC LIMIT 600').fetchall():
        for idea in json.loads(payload).get('ideas', []):
            if idea.get('id') == idea_id:
                return idea
    return None


def grade(bars, start, stop, target):
    """Which level a long position reached first after it started."""
    for bar in bars:
        if parse(bar['t']) < start:
            continue
        hit_stop, hit_target = bar['l'] <= stop, bar['h'] >= target
        if hit_stop or hit_target:
            minutes = round((parse(bar['t']) - start).total_seconds() / 60)
            return {'result': 'both' if hit_stop and hit_target else 'stop' if hit_stop else 'target', 'at': bar['t'], 'minutes': max(minutes, 0)}
    return {'result': 'neither', 'at': None, 'minutes': None}


def replay(provider, item, at=None):
    at = at or now()
    start_text = item.get('opened_at') or item.get('created_at')
    if not start_text:
        raise ValueError('This item has no start time to replay from.')
    start = parse(start_text)
    underlying = item.get('underlying') or item.get('symbol')
    crypto = underlying == 'BTC/USD'
    begin = start - timedelta(minutes=20)
    if crypto:
        end = min(at, start + timedelta(hours=8))
    else:
        close = start.astimezone(NY).replace(hour=16, minute=0, second=0, microsecond=0).astimezone(UTC)
        end = min(at, max(close, start + timedelta(minutes=30)))
    base = '/v1beta3/crypto/us' if crypto else '/v2/stocks'
    params = {'symbols': underlying, 'timeframe': '1Min', 'start': stamp(begin), 'end': stamp(end), 'limit': 1000, 'sort': 'asc'}
    if not crypto:
        params['feed'] = getattr(provider, 'stock_feed', 'sip')
    bars = provider.get(base + '/bars', params).get('bars', {}).get(underlying, [])
    bars = [b for b in bars if all(finite(b.get(k)) for k in ('o', 'h', 'l', 'c'))]
    option = item.get('asset') == 'option'
    levels = None if option else {k: item.get(k) for k in ('entry', 'stop', 'target')}
    gradable = levels and all(finite(v) for v in levels.values())
    return {'symbol': item.get('symbol'), 'underlying': underlying, 'asset': item.get('asset'), 'direction': item.get('direction'),
            'start': stamp(start), 'bars': [{'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']} for b in bars],
            'levels': levels if gradable else None,
            'outcome': grade(bars, start, item['stop'], item['target']) if gradable and bars else None,
            'note': 'Option prices are not kept historically, so this shows the shares and is not graded.' if option else None}


# ---- position what-ifs -----------------------------------------------------------------------

def cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def option_price(kind, spot, strike, years, vol, rate=RATE):
    """Black-Scholes value of a European option. A stated model, not a forecast."""
    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if kind == 'C' else max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == 'C':
        return spot * cdf(d1) - strike * math.exp(-rate * years) * cdf(d2)
    return strike * math.exp(-rate * years) * cdf(-d2) - spot * cdf(-d1)


def implied_vol(kind, spot, strike, years, price):
    """The volatility at which the model reproduces the current price, or None if none does."""
    low, high = 0.01, 5.0
    if years <= 0 or not option_price(kind, spot, strike, years, low) <= price <= option_price(kind, spot, strike, years, high):
        return None
    for _ in range(60):
        mid = (low + high) / 2
        if option_price(kind, spot, strike, years, mid) < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def what_if(provider, positions, at=None):
    """Value change for each position if the share price moves 5% either way, today and in a week."""
    at = at or now()
    underlyings = [p['underlying'] for p in positions if p.get('asset') != 'bitcoin']
    quotes = live(provider, underlyings)['quotes'] if underlyings else {}
    rows = []
    for p in positions:
        quantity, multiplier = p.get('quantity') or 0, p.get('multiplier') or 1
        row = {'symbol': p['symbol'], 'source': p.get('source'), 'asset': p.get('asset'), 'quantity': quantity}
        spot = (quotes.get(p['underlying']) or {}).get('last')
        if p.get('asset') == 'bitcoin' or not finite(spot):
            rows.append(dict(row, note='No current share price.' if p.get('asset') != 'bitcoin' else 'Bitcoin is not covered.'))
            continue
        if p.get('asset') != 'option':
            value = spot * quantity
            rows.append(dict(row, spot=spot, value=round(value, 2),
                             scenarios=[{'move_pct': m * 100, 'today': round(value * m, 2), 'later': round(value * m, 2)} for m in MOVES]))
            continue
        match = OCC.fullmatch(p['symbol'])
        mark = p.get('mark')
        if not match or not finite(mark) or mark <= 0:
            rows.append(dict(row, spot=spot, note='No current option quote to work from.'))
            continue
        kind, strike = match[3], int(match[4]) / 1000
        expiry = datetime.strptime(match[2], '%y%m%d').replace(hour=16, tzinfo=NY)
        years = (expiry - at).total_seconds() / (365 * 86400)
        vol = implied_vol(kind, spot, strike, years, mark)
        if vol is None:
            rows.append(dict(row, spot=spot, note='The quote is outside what the pricing model can match (expired or very thin).'))
            continue
        value = mark * quantity * multiplier
        later = years - LATER_DAYS / 365
        scenarios = [{'move_pct': m * 100,
                      'today': round(option_price(kind, spot * (1 + m), strike, years, vol) * quantity * multiplier - value, 2),
                      'later': round(option_price(kind, spot * (1 + m), strike, later, vol) * quantity * multiplier - value, 2)}
                     for m in MOVES]
        rows.append(dict(row, spot=spot, value=round(value, 2), implied_vol_pct=round(vol * 100, 1),
                         days_left=max(0, round(years * 365)), scenarios=scenarios))
    return {'at': stamp(at), 'later_days': LATER_DAYS, 'rows': rows,
            'assumptions': f'Options use the Black-Scholes model with volatility held at today\'s implied level and a {RATE:.0%} interest rate. '
                           'Real prices will differ, mostly because implied volatility changes when the share price moves.'}
