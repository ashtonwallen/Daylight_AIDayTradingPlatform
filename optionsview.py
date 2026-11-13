"""Option chain view and unusual-activity flags. Read-only; describes the market, suggests nothing."""
import math
import re
from datetime import timedelta

from market import DataError, NY, now

# A contract is flagged when today's volume is larger than every position that existed this morning.
MIN_VOLUME = 500
MIN_RATIO = 1.5
MIN_DAYS = 2
LOOKAHEAD_DAYS = 60


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch('[A-Z]{1,5}', symbol.strip().upper()):
        raise ValueError('Enter a stock ticker (1–5 letters).')
    return symbol.strip().upper()


def require(provider, method):
    if not hasattr(provider, method):
        raise DataError('This needs Schwab data. Alpaca does not supply option volume, open interest or order books.')
    return getattr(provider, method)


def side(contract):
    bid, ask = contract.get('bid'), contract.get('ask')
    mid = (bid + ask) / 2 if finite(bid) and finite(ask) and ask >= bid > 0 else None
    return {'symbol': contract.get('symbol'), 'bid': bid, 'ask': ask, 'mid': mid, 'last': contract.get('last'),
            'iv': contract.get('iv') if finite(contract.get('iv')) and contract['iv'] > 0 else None,
            'delta': contract.get('delta') if finite(contract.get('delta')) and abs(contract['delta']) <= 1 else None,
            'volume': contract.get('volume') or 0, 'open_interest': contract.get('openInterest') or 0}


def unusual(contracts, underlying, symbol):
    """Contracts trading well above their open interest, largest ratio first."""
    rows = []
    for c in contracts:
        volume, oi = c.get('volume') or 0, c.get('openInterest') or 0
        # Contracts expiring today or tomorrow always trade far above open interest; that is routine.
        if volume < MIN_VOLUME or volume < MIN_RATIO * max(oi, 1) or (c.get('dte') or 0) < MIN_DAYS:
            continue
        price = c.get('mark') if finite(c.get('mark')) and c['mark'] > 0 else c.get('last')
        strike = c.get('strike')
        rows.append({
            'underlying': symbol, 'symbol': ''.join(str(c.get('symbol') or '').split()), 'type': 'call' if c.get('type') == 'CALL' else 'put',
            'strike': strike, 'expiration': c.get('expiration'), 'days': c.get('dte'),
            'volume': volume, 'open_interest': oi, 'ratio': round(volume / max(oi, 1), 1),
            'price': price, 'premium': round(volume * price * 100) if finite(price) else None,
            'iv': c.get('iv') if finite(c.get('iv')) and c['iv'] > 0 else None,
            'distance_pct': round((strike / underlying - 1) * 100, 1) if finite(strike) and finite(underlying) and underlying else None,
        })
    return sorted(rows, key=lambda r: (-(r['premium'] or 0)))


def chain(provider, symbol, expiration=None):
    """One expiration's calls and puts side by side, plus the list of expirations to choose from."""
    symbol = check_symbol(symbol)
    today = now().astimezone(NY).date()
    data = require(provider, 'option_chain')(symbol, today, today + timedelta(days=LOOKAHEAD_DAYS))
    contracts = [c for c in data.get('contracts', []) if finite(c.get('strike')) and c.get('expiration')]
    expirations = sorted({c['expiration'] for c in contracts})
    if not expirations:
        raise DataError(f'No option contracts returned for {symbol}.')
    if expiration not in expirations:
        # Default to the first expiration that is not today: same-day contracts are mostly noise.
        expiration = next((e for e in expirations if e > str(today)), expirations[0])
    strikes = {}
    for c in contracts:
        if c['expiration'] == expiration:
            strikes.setdefault(c['strike'], {})['call' if c.get('type') == 'CALL' else 'put'] = side(c)
    underlying = data.get('underlyingPrice')
    rows = [{'strike': strike, 'call': sides.get('call'), 'put': sides.get('put')} for strike, sides in sorted(strikes.items())]
    days = next((c.get('dte') for c in contracts if c['expiration'] == expiration), None)
    return {'symbol': symbol, 'underlying': underlying, 'expirations': expirations, 'expiration': expiration,
            'days': days, 'rows': rows, 'unusual': unusual(contracts, underlying, symbol)[:12],
            'totals': {'call_volume': sum((r['call'] or {}).get('volume', 0) for r in rows),
                       'put_volume': sum((r['put'] or {}).get('volume', 0) for r in rows),
                       'call_open_interest': sum((r['call'] or {}).get('open_interest', 0) for r in rows),
                       'put_open_interest': sum((r['put'] or {}).get('open_interest', 0) for r in rows)}}


def scan(provider, symbols):
    """Unusual activity across a watchlist."""
    fetch = require(provider, 'option_chain')
    today = now().astimezone(NY).date()
    found, errors = [], []
    for symbol in list(dict.fromkeys(symbols))[:12]:
        try:
            data = fetch(symbol, today, today + timedelta(days=LOOKAHEAD_DAYS))
            found.extend(unusual(data.get('contracts', []), data.get('underlyingPrice'), symbol)[:6])
        except DataError as exc:
            errors.append(f'{symbol}: {exc}')
    return {'rows': sorted(found, key=lambda r: -(r['premium'] or 0))[:30], 'errors': errors,
            'symbols': list(dict.fromkeys(symbols))[:12],
            'rule': f'Volume at least {MIN_VOLUME} contracts and {MIN_RATIO}× open interest, expiring in {MIN_DAYS} to {LOOKAHEAD_DAYS} days'}


def book(provider, symbol):
    symbol = check_symbol(symbol)
    data = require(provider, 'order_book')(symbol)
    def levels(rows):
        return [{'price': l['price'], 'size': l['size'],
                 'venues': [v.get('exchange') for v in l.get('exchanges', []) if v.get('exchange')][:6]}
                for l in rows if finite(l.get('price')) and finite(l.get('size'))]
    bids, asks = levels(data.get('bids', [])), levels(data.get('asks', []))
    best_bid, best_ask = (bids[0]['price'] if bids else None), (asks[0]['price'] if asks else None)
    return {'symbol': symbol, 'status': data.get('status'), 'detail': data.get('detail'), 'venue': data.get('venue'),
            'age_seconds': data.get('ageSeconds'), 'bids': bids, 'asks': asks,
            'spread': round(best_ask - best_bid, 4) if best_bid and best_ask else None,
            'mid': round((best_ask + best_bid) / 2, 4) if best_bid and best_ask else None}
