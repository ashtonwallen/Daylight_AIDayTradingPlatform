"""Optional stock and option data from a local market-data bridge. Read-only.

Set MARKET_DATA_URL to a service on this computer that fronts a broker's market data (the
author's fronts Schwab). The bridge answers the same questions Daylight asks Alpaca, in the same
shapes, so every freshness, spread and sizing check runs unchanged. Bitcoin and the news feed
still come from Alpaca when its keys are present.
"""
import json
import os
import re
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from market import Alpaca, DataError, UTC, parse, stamp

FREQUENCIES = {'1Min': ('1m', 1), '5Min': ('5m', 1), '15Min': ('15m', 1), '30Min': ('30m', 1),
               '1Hour': ('30m', 2), '1Day': ('1d', 1), '1Week': ('1w', 1)}
OCC = re.compile(r'([A-Z]+)\s*(\d{6})([CP])(\d{8})')


def compact(symbol):
    """Broker option symbols pad the root to six characters; Daylight uses the unpadded form."""
    match = OCC.fullmatch(str(symbol or ''))
    return ''.join(match.groups()) if match else None


def padded(symbol):
    match = OCC.fullmatch(str(symbol or ''))
    return f'{match[1]:<6}{match[2]}{match[3]}{match[4]}' if match else symbol


def from_ms(value):
    try:
        return stamp(datetime.fromtimestamp(value / 1000, UTC))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def quote_row(q, time_key='quoteTime'):
    return {'bp': q.get('bid') or 0, 'ap': q.get('ask') or 0, 'bs': q.get('bidSize') or 0,
            'as': q.get('askSize') or 0, 't': from_ms(q.get(time_key))}


def merge(bars, size):
    """Combine consecutive bars within one day, e.g. two 30-minute bars into an hour."""
    if size == 1:
        return bars
    out, group = [], []
    for bar in bars:
        if group and (len(group) == size or parse(bar['t']) - parse(group[0]['t']) >= timedelta(minutes=30 * size)):
            out.append(group); group = []
        group.append(bar)
    if group:
        out.append(group)
    return [{'t': g[0]['t'], 'o': g[0]['o'], 'h': max(b['h'] for b in g), 'l': min(b['l'] for b in g),
             'c': g[-1]['c'], 'v': sum(b['v'] or 0 for b in g), 'vw': g[-1]['c']} for g in out]


class Bridge(Alpaca):
    name = 'Local market-data bridge'

    def __init__(self):
        self.url = os.getenv('MARKET_DATA_URL', '').strip().rstrip('/')
        host = urlsplit(self.url).hostname
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise DataError('MARKET_DATA_URL must point at this computer (127.0.0.1).')
        # The bridge serves consolidated quotes, so the code checks that require SIP and OPRA hold.
        self.stock_feed = 'bridge'
        self.option_feed = 'opra'
        self._clock = None
        self._options_denied = None
        try:
            self.alpaca = Alpaca()
        except DataError:
            self.alpaca = None  # Bitcoin and news need Alpaca keys; stocks and options do not.

    def fetch(self, path, params):
        request = Request(self.url + path + '?' + urlencode({k: v for k, v in params.items() if v is not None}),
                          headers={'User-Agent': 'Daylight/bridge'})
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                code = json.load(exc).get('error')
            except (ValueError, AttributeError, OSError):
                code = None
            reasons = {'login_required': 'broker login expired; log in again on the bridge',
                       'rate_limited': 'rate limit reached; will retry next cycle'}
            raise DataError(f'Bridge {path}: {reasons.get(code or "", "HTTP " + str(exc.code))}.') from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise DataError(f'Bridge {path}: not reachable; is the market-data service running?') from None

    def get(self, path, params=None, clock=False):
        params = dict(params or {})
        if path == '/v2/clock':
            data = self.fetch('/clock', {})
            return {'timestamp': data.get('timestamp'), 'is_open': data.get('isOpen'),
                    'next_open': data.get('nextOpen'), 'next_close': data.get('nextClose')}
        if path == '/v2/stocks/bars':
            return {'bars': {s: self.bars(s, params) for s in params['symbols'].split(',')}, 'next_page_token': None}
        if path == '/v2/stocks/quotes/latest':
            return {'quotes': self.stock_quotes(params['symbols'].split(','))}
        if path.startswith('/v1beta1/options/snapshots/'):
            return {'snapshots': self.snapshots(path.rsplit('/', 1)[1], params)}
        if path == '/v1beta1/options/quotes/latest':
            return {'quotes': self.option_quotes(params['symbols'].split(','))}
        if path == '/v1beta1/news' and not self.alpaca:
            return {'news': []}
        if self.alpaca and (path.startswith('/v1beta3/crypto/') or path == '/v1beta1/news'):
            return self.alpaca.get(path, params, clock)
        raise DataError(f'Bridge: {path} needs Alpaca credentials (Bitcoin and news come from Alpaca).')

    def bars(self, symbol, params):
        frequency, size = FREQUENCIES[params['timeframe']]
        data = self.fetch('/history', {'symbol': symbol, 'frequency': frequency, 'start': params.get('start'),
                                       'end': params.get('end')})
        end = parse(params['end']) if params.get('end') else None
        bars = [{'t': from_ms(b['t']), 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b.get('v') or 0,
                 'vw': b['c']} for b in data.get('bars', [])]
        bars = [b for b in bars if b['t'] and (end is None or parse(b['t']) <= end)]
        return merge(bars, size)

    def stock_quotes(self, symbols):
        found = self.fetch('/quotes', {'symbols': ','.join(symbols)}).get('quotes', {})
        return {s: quote_row(q) for s, q in found.items()}

    def snapshots(self, underlying, params):
        data = self.fetch('/chain', {'symbol': underlying, 'strikes': 120,
                                     'start': params.get('expiration_date_gte'),
                                     'end': params.get('expiration_date_lte')})
        low, high = params.get('strike_price_gte'), params.get('strike_price_lte')
        rows = {}
        for c in data.get('contracts', []):
            symbol = compact(c.get('symbol'))
            strike = c.get('strike')
            if not symbol or strike is None or (low is not None and strike < low) or (high is not None and strike > high):
                continue
            rows[symbol] = {'latestQuote': quote_row(c), 'greeks': {'delta': c.get('delta')},
                            'impliedVolatility': c['iv'] / 100 if isinstance(c.get('iv'), (int, float)) else None}
            if len(rows) >= int(params.get('limit') or 100):
                break
        return rows

    def option_quotes(self, symbols):
        found = self.fetch('/quotes', {'symbols': ','.join(padded(s) for s in symbols)}).get('quotes', {})
        return {compact(s) or s: quote_row(q) for s, q in found.items()}


def live_provider():
    """The bridge when MARKET_DATA_URL is set, otherwise Alpaca."""
    return Bridge() if os.getenv('MARKET_DATA_URL', '').strip() else Alpaca()
