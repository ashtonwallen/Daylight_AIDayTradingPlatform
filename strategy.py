"""Transparent heuristics, not a trained or validated trading strategy."""
import math
import re
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from market import age, parse, stamp, NY
from research import research_gate


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def quote_structure_problem(quote, max_spread=.003):
    """Validity checks that do not depend on age.

    A quote from before the close is old but still a usable valuation reference, so age is checked
    separately from whether the quote itself is coherent.
    """
    bid, ask = quote.get('bp'), quote.get('ap')
    if not positive(bid) or not positive(ask) or ask < bid:
        return 'Missing or invalid bid/ask.'
    if not positive(quote.get('bs')) or not positive(quote.get('as')):
        return 'No displayed two-sided liquidity.'
    if (ask - bid) / ((ask + bid) / 2) > max_spread:
        return 'Bid/ask spread exceeds the liquidity limit.'
    return None


def quote_state(quote, at, session=None, asset='stock', max_spread=.003):
    """Classify a quote as 'fresh', 'reference' or 'unusable', with a reason.

    Outside US market hours a stock or option quote is necessarily old. That is a known market
    state, not a data failure, so the last quote before the close stays usable for research and
    valuation. It is never usable for a simulated fill or a live entry. Bitcoin trades around the
    clock, so an old quote there is always a genuine failure.
    """
    if not quote_problem(quote, at, max_spread):
        return 'fresh', 'Fresh quote'
    structure = quote_structure_problem(quote, max_spread)
    closed = asset != 'bitcoin' and isinstance(session, dict) and session.get('status') in ('closed', 'closing')
    if structure or not closed or not quote.get('t'):
        return 'unusable', structure or quote_problem(quote, at, max_spread)
    return 'reference', 'Last quote before the close'


def quote_problem(quote, at, max_spread=.003):
    """Full gate for anything that simulates a fill or a live entry: coherent AND current."""
    problem = quote_structure_problem(quote, max_spread)
    if problem:
        return problem
    if not -5 <= age(quote.get('t'), at) <= 45:
        return 'Quote older than 45 seconds or timestamp invalid.'
    return None


def technical(instrument, at):
    bars = instrument.get('bars', [])
    result = {'symbol': instrument['symbol'], 'asset': instrument['asset'], 'feed': instrument['feed'],
              'direction': 'wait', 'reason': '', 'bars': bars[-45:], 'metrics': {}}
    problem = quote_problem(instrument.get('quote', {}), at)
    if problem:
        return dict(result, reason=problem)
    if len(bars) < 25:
        return dict(result, reason='Warming up: need 25 completed one-minute candles.')
    if not 60 <= age(bars[-1].get('t'), at) <= 150:
        return dict(result, reason='Completed candle is missing, stale, or still forming.')
    try:
        recent = bars[-25:]
        for i, bar in enumerate(recent):
            if not all(positive(bar.get(k)) for k in ('o', 'h', 'l', 'c')):
                return dict(result, reason='Invalid candle prices.')
            if not bar['l'] <= min(bar['o'], bar['c']) <= max(bar['o'], bar['c']) <= bar['h']:
                return dict(result, reason='Inconsistent OHLC candle.')
            if not isinstance(bar.get('v'), (float, int)) or not math.isfinite(bar['v']) or bar['v'] < 0:
                return dict(result, reason='Invalid candle volume.')
            if i and (parse(bar['t']) - parse(recent[i-1]['t'])).total_seconds() != 60:
                return dict(result, reason='Gaps or duplicate minute candles; withholding signal.')
        closes = [b['c'] for b in recent]
        fast, slow = sum(closes[-5:]) / 5, sum(closes[-20:]) / 20
        volumes = [b['v'] for b in recent]
        if sum(volumes) <= 0:
            return dict(result, reason='No traded volume in candle window.')
        vwap = sum(((b['h'] + b['l'] + b['c']) / 3) * b['v'] for b in recent) / sum(volumes)
        atr = sum(max(recent[i]['h'] - recent[i]['l'], abs(recent[i]['h']-recent[i-1]['c']),
                      abs(recent[i]['l']-recent[i-1]['c'])) for i in range(11, 25)) / 14
        relvol = volumes[-1] / (sum(volumes[-21:-1]) / 20) if sum(volumes[-21:-1]) else 0
        change = (closes[-1] / closes[-6] - 1) * 100
        midpoint = (instrument['quote']['bp'] + instrument['quote']['ap']) / 2
        if abs(midpoint - closes[-1]) > max(3 * atr, closes[-1] * .005):
            return dict(result, reason='Quote moved too far from the completed candle.')
        direction = ('bullish' if fast > slow and closes[-1] > vwap and change > .02 else
                     'bearish' if fast < slow and closes[-1] < vwap and change < -.02 else 'wait')
        if relvol < .5:
            direction = 'wait'
        return dict(result, direction=direction, reason=('Trend and rolling VWAP agree.' if direction != 'wait'
                    else 'No clear price/volume alignment; wait.'),
                    metrics={'price': closes[-1], 'fast': fast, 'slow': slow, 'vwap': vwap,
                             'atr': atr, 'relvol': relvol, 'change': change})
    except (ValueError, KeyError, TypeError):
        return dict(result, reason='Malformed market data; withholding signal.')


def size(entry, stop, asset, config, available, risk_left):
    multiplier = 100 if asset == 'option' else 1
    # Long options reserve the entire debit, not an assumed stop execution.
    unit_risk = entry * multiplier if asset == 'option' else (entry - stop) * multiplier
    if unit_risk <= 0 or entry <= 0:
        return 0
    quantity = min(config['budget'] * config['risk_pct'] / 100 / unit_risk,
                   risk_left / unit_risk, available / (entry * multiplier),
                   config['budget'] * config.get('max_position_pct', 25) / 100 / (entry * multiplier))
    return math.floor(max(0, quantity) * 1000000) / 1000000 if asset == 'bitcoin' else math.floor(max(0, quantity))


def make_ideas(snapshot, config, reports, at, available, risk_left):
    with ThreadPoolExecutor(max_workers=5) as pool:
        analyses = list(pool.map(lambda item: technical(item, at), snapshot['instruments'].values()))
    market = next((a for a in analyses if a['symbol'] == 'SPY'), None)
    ideas, rejected = [], []
    for analysis in analyses:
        symbol, direction = analysis['symbol'], analysis['direction']
        if symbol not in config['symbols'] and symbol != 'BTC/USD':
            continue
        if direction == 'wait':
            rejected.append({'symbol': symbol, 'reason': analysis['reason']})
            continue
        if symbol != 'BTC/USD' and not snapshot.get('equity_open', True):
            rejected.append({'symbol': symbol, 'reason': 'No new stock or option entries in the final 15 minutes of the session.'})
            continue
        if symbol != 'BTC/USD' and (not market or market['direction'] == 'wait'):
            rejected.append({'symbol': symbol, 'reason': 'SPY context is unavailable or inconclusive.'})
            continue
        if symbol != 'BTC/USD' and market['direction'] != direction:
            rejected.append({'symbol': symbol, 'reason': 'Price direction conflicts with SPY market trend.'})
            continue
        allowed, research_note, evidence = research_gate(reports, symbol, direction, at,
            (config['require_research'] or symbol in config.get('discovery_symbols', [])) and not snapshot['simulated'])
        if symbol in config.get('discovery_symbols', []) and not any(e['symbol']==symbol and e['lane']!='Social sentiment' for e in evidence):
            allowed, research_note = False, 'Discovery needs current company-specific news corroboration; social discussion alone is insufficient.'
        if not allowed:
            rejected.append({'symbol': symbol, 'reason': research_note})
            continue
        instrument = snapshot['instruments'][symbol]
        candidates = []
        if analysis['asset'] in config['assets'] and direction == 'bearish':
            rejected.append({'symbol': symbol, 'reason': 'Bearish price action; no short stock or Bitcoin entries in this version.'})
        if (analysis['asset'] in config['assets']) and direction == 'bullish':
            entry = round(instrument['quote']['ap'], 2)
            distance = max(analysis['metrics']['atr'] * 1.5, entry * .002)
            candidates.append({'symbol': symbol, 'underlying': symbol, 'asset': analysis['asset'],
                'entry': entry, 'stop': round(entry-distance, 2), 'target': round(entry+distance*2, 2),
                'quote': instrument['quote'], 'feed': instrument['feed'], 'direction': 'Buy',
                'note': 'Exit levels are planning references; actual fills can exceed planned loss.'})
        if 'option' in config['assets'] and symbol in snapshot['chains']:
            kind = 'C' if direction == 'bullish' else 'P'
            options = []
            for contract, data in snapshot['chains'][symbol].items():
                match = re.fullmatch(r'([A-Z]+)(\d{6})([CP])(\d{8})', contract)
                if not match or match[1] != symbol or match[3] != kind:
                    continue
                try:
                    expiry = parse('20' + match[2][:2] + '-' + match[2][2:4] + '-' + match[2][4:] + 'T00:00:00Z').date()
                    if not 7 <= (expiry - at.astimezone(NY).date()).days <= 21:
                        continue
                    quote = data.get('latestQuote', {})
                    delta = data.get('greeks', {}).get('delta')
                    if quote_problem(quote, at, .08) or not isinstance(delta, (float, int)) or not .35 <= abs(delta) <= .65:
                        continue
                    if (delta > 0) != (kind == 'C'):
                        continue
                    options.append((abs(abs(delta)-.5), contract, quote, expiry, int(match[4]) / 1000, delta))
                except (ValueError, TypeError):
                    continue
            for _, contract, quote, expiry, strike, delta in sorted(options)[:1]:
                entry = round(quote['ap'], 2)
                candidates.append({'symbol': contract, 'underlying': symbol, 'asset': 'option',
                    'entry': entry, 'stop': round(entry*.7, 2), 'target': round(entry*1.6, 2),
                    'quote': quote, 'feed': 'SYNTHETIC' if snapshot['simulated'] else 'OPRA',
                    'direction': 'Long call' if kind == 'C' else 'Long put',
                    'note': f'{expiry} · ${strike:g} strike · delta {delta:.2f}. Entire premium can be lost; close intraday. No automatic exercise handling.'})
            if not options:
                rejected.append({'symbol': symbol + ' options', 'reason': 'No current 7–21 DTE contract met spread and delta filters in the sampled chain.'})
        for item in candidates:
            quantity = size(item['entry'], item['stop'], item['asset'], config, available, risk_left)
            if quantity <= 0 or (item['asset'] == 'bitcoin' and quantity * item['entry'] < 1):
                rejected.append({'symbol': item['symbol'], 'reason': 'Budget or remaining risk allowance too small for this position.'})
                continue
            multiplier = 100 if item['asset'] == 'option' else 1
            item.update({'quantity': quantity, 'notional': math.ceil(quantity*item['entry']*multiplier*100-1e-8)/100,
                'risk': math.ceil(quantity*(item['entry'] if item['asset']=='option' else item['entry']-item['stop'])*multiplier*100-1e-8)/100,
                'created_at': stamp(at), 'expires_at': stamp(at+timedelta(seconds=60)),
                'reasons': [analysis['reason'], f"5-minute change {analysis['metrics']['change']:+.2f}%; relative volume {analysis['metrics']['relvol']:.2f}×.", research_note],
                'evidence': evidence[:8], 'simulated': snapshot['simulated'],
                'rank': round(abs(analysis['metrics']['change']) * 10 + min(analysis['metrics']['relvol'], 3), 3)})
            ideas.append(item)
    return sorted(ideas, key=lambda x: x['rank'], reverse=True)[:8], analyses, rejected
