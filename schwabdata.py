"""Stock and option data straight from the Schwab Trader API, with your own developer app. Read-only.

Setup: create an app at developer.schwab.com with the "Market Data Production" product and the
callback URL https://127.0.0.1, put SCHWAB_APP_KEY and SCHWAB_APP_SECRET in .env, then connect
from Settings. Schwab makes you log in again every 7 days.

Only market-data endpoints are called. There is no order or account code here.
"""
import base64
import json
import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from bridge import Bridge
from market import Alpaca, DataError, NY, UTC, now, parse, stamp

API = 'https://api.schwabapi.com'
TOKEN_FILE = Path(__file__).resolve().parent / 'data' / 'schwab_token.json'
LOGIN_DAYS = 7
LOCK = threading.Lock()
FREQUENCY = {'1m': ('minute', 1), '5m': ('minute', 5), '10m': ('minute', 10), '15m': ('minute', 15),
             '30m': ('minute', 30), '1d': ('daily', 1), '1w': ('weekly', 1)}


def credentials():
    return os.getenv('SCHWAB_APP_KEY', '').strip(), os.getenv('SCHWAB_APP_SECRET', '').strip()


def callback_url():
    return os.getenv('SCHWAB_CALLBACK_URL', 'https://127.0.0.1').strip()


def configured():
    return all(credentials())


def read_token():
    try:
        token = json.loads(TOKEN_FILE.read_text(encoding='utf-8'))
        return token if isinstance(token, dict) and token.get('refresh_token') else None
    except (OSError, ValueError):
        return None


def write_token(token):
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    pending = TOKEN_FILE.with_suffix('.pending')
    pending.write_text(json.dumps(token), encoding='utf-8')
    os.replace(pending, TOKEN_FILE)


def status():
    """What Settings shows. Never includes a key or token."""
    token = read_token()
    left = token['login_at'] + LOGIN_DAYS * 86400 - time.time() if token else 0
    return {'configured': configured(), 'connected': left > 0,
            'login_expires_in_hours': round(left / 3600, 1) if token else None,
            'authorize_url': authorize_url() if configured() else None, 'callback_url': callback_url()}


def authorize_url():
    key, _ = credentials()
    return API + '/v1/oauth/authorize?' + urlencode({'response_type': 'code', 'client_id': key,
                                                     'redirect_uri': callback_url()})


def token_request(form):
    key, secret = credentials()
    if not key or not secret:
        raise DataError('Add SCHWAB_APP_KEY and SCHWAB_APP_SECRET to .env and restart.')
    basic = base64.b64encode(f'{key}:{secret}'.encode()).decode()
    request = Request(API + '/v1/oauth/token', data=urlencode(form).encode(), method='POST', headers={
        'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        raise DataError('Schwab refused the login' + (' (the address was already used or has expired; start again).'
                                                      if exc.code in (400, 401) else f' (HTTP {exc.code}).')) from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise DataError('Schwab login: not reachable.') from None


def connect(redirected):
    """Finish the login from the address the browser ended on after you approved access."""
    if not isinstance(redirected, str) or len(redirected) > 4000:
        raise ValueError('Paste the full address from the browser after logging in.')
    query = parse_qs(urlsplit(redirected.strip()).query)
    code = (query.get('code') or [''])[0]
    if not code:
        raise ValueError('That address has no login code. Paste the full address the browser ended on.')
    data = token_request({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': callback_url()})
    if not data.get('refresh_token') or not data.get('access_token'):
        raise DataError('Schwab login: unexpected answer.')
    with LOCK:
        write_token({'access_token': data['access_token'], 'refresh_token': data['refresh_token'],
                     'access_expires_at': time.time() + int(data.get('expires_in', 1800)) - 60,
                     'login_at': time.time()})


def disconnect():
    with LOCK:
        TOKEN_FILE.unlink(missing_ok=True)


def access_token():
    with LOCK:
        token = read_token()
        if not token:
            raise DataError('Schwab is not connected. Connect it in Settings.')
        if time.time() < token.get('access_expires_at', 0):
            return token['access_token']
        if time.time() > token['login_at'] + LOGIN_DAYS * 86400:
            raise DataError('The Schwab login has expired (it lasts 7 days). Connect again in Settings.')
        data = token_request({'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
        token.update(access_token=data['access_token'],
                     refresh_token=data.get('refresh_token') or token['refresh_token'],
                     access_expires_at=time.time() + int(data.get('expires_in', 1800)) - 60)
        write_token(token)
        return token['access_token']


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def to_ms(value):
    return int(parse(value).timestamp() * 1000) if value else None


class Schwab(Bridge):
    """Answers the same questions as the local bridge, by asking Schwab directly."""
    name = 'Schwab'

    def __init__(self, token=access_token):
        if token is access_token and not status()['connected']:
            raise DataError('Schwab is not connected. Connect it in Settings.')
        self.token = token
        self.url = API
        self.stock_feed = 'schwab'
        self.option_feed = 'opra'
        self._clock = None
        self._options_denied = None
        self.cache = {}
        self.calls = []
        self.cache_lock = threading.Lock()
        try:
            self.alpaca = Alpaca()
        except DataError:
            self.alpaca = None

    def api(self, path, params, ttl):
        key = (path, tuple(sorted((k, str(v)) for k, v in params.items() if v is not None)))
        with self.cache_lock:
            hit = self.cache.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
            # Schwab allows about 120 requests a minute; stop short of it.
            cutoff = time.monotonic() - 60
            self.calls = [t for t in self.calls if t > cutoff]
            if len(self.calls) >= 100:
                raise DataError('Schwab: rate limit reached; will retry next cycle.')
            self.calls.append(time.monotonic())
        request = Request(API + path + '?' + urlencode({k: v for k, v in params.items() if v is not None}),
                          headers={'Authorization': 'Bearer ' + self.token(), 'Accept': 'application/json'})
        try:
            with urlopen(request, timeout=20) as response:
                data = json.load(response)
        except HTTPError as exc:
            reasons = {401: 'login rejected; connect again in Settings', 429: 'rate limit reached; will retry next cycle'}
            raise DataError(f'Schwab {path}: {reasons.get(exc.code, "HTTP " + str(exc.code))}.') from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise DataError(f'Schwab {path}: data unavailable; will retry next cycle.') from None
        with self.cache_lock:
            self.cache[key] = (time.monotonic() + ttl, data)
            if len(self.cache) > 500:
                self.cache = {k: v for k, v in self.cache.items() if v[0] > time.monotonic()}
        return data

    def fetch(self, path, params):
        handler = {'/clock': self.clock, '/history': self.bars_data, '/quotes': self.quotes_data,
                   '/chain': self.chain_data}.get(path)
        if not handler:
            raise DataError(f'Schwab: {path} needs the local market-data bridge.')
        return handler(params)

    def hours(self, day):
        data = self.api('/marketdata/v1/markets', {'markets': 'equity', 'date': day.isoformat()}, 600)
        return [s for group in data.values() for product in (group or {}).values()
                for s in ((product.get('sessionHours') or {}).get('regularMarket') or [])]

    def clock(self, _params=None, at=None):
        at = at or now()
        is_open, next_open, next_close = False, None, None
        for offset in range(10):
            for session in self.hours((at.astimezone(NY) + timedelta(days=offset)).date()):
                start, end = parse(session['start']), parse(session['end'])
                if start <= at < end:
                    is_open, next_close = True, end
                elif at < start:
                    next_open, next_close = next_open or start, next_close or end
            if next_open:
                break
        text = lambda d: stamp(d.astimezone(UTC)) if d else None
        return {'timestamp': text(at), 'isOpen': is_open, 'nextOpen': text(next_open), 'nextClose': text(next_close)}

    def bars_data(self, params):
        kind, size = FREQUENCY[params['frequency']]
        data = self.api('/marketdata/v1/pricehistory', {
            'symbol': params['symbol'], 'frequencyType': kind, 'frequency': size,
            'periodType': 'day' if kind == 'minute' else 'year',
            'startDate': to_ms(params.get('start')), 'endDate': to_ms(params.get('end')),
            'needExtendedHoursData': 'false'}, 300 if kind != 'minute' else 30)
        return {'bars': [{'t': c['datetime'], 'o': c['open'], 'h': c['high'], 'l': c['low'], 'c': c['close'],
                          'v': c.get('volume')} for c in data.get('candles') or []
                         if all(number(c.get(k)) is not None for k in ('datetime', 'open', 'high', 'low', 'close'))]}

    def quotes_data(self, params):
        data = self.api('/marketdata/v1/quotes', {'symbols': params['symbols']}, 2)
        quotes = {}
        for symbol, row in data.items():
            q = row.get('quote') if isinstance(row, dict) else None
            if q:
                quotes[symbol] = {'bid': number(q.get('bidPrice')), 'ask': number(q.get('askPrice')),
                                  'bidSize': number(q.get('bidSize')), 'askSize': number(q.get('askSize')),
                                  'last': number(q.get('lastPrice')), 'quoteTime': number(q.get('quoteTime')),
                                  'tradeTime': number(q.get('tradeTime')), 'prevClose': number(q.get('closePrice')),
                                  'changePct': number(q.get('netPercentChange'))}
        return {'quotes': quotes}

    def chain_data(self, params):
        data = self.api('/marketdata/v1/chains', {
            'symbol': params['symbol'], 'contractType': 'ALL', 'strikeCount': params.get('strikes'),
            'includeUnderlyingQuote': 'true', 'fromDate': params.get('start'), 'toDate': params.get('end')}, 10)
        contracts = []
        for side in ('callExpDateMap', 'putExpDateMap'):
            for strikes in (data.get(side) or {}).values():
                for rows in strikes.values():
                    for c in rows:
                        contracts.append({
                            'symbol': c.get('symbol'), 'type': c.get('putCall'), 'strike': number(c.get('strikePrice')),
                            'expiration': str(c.get('expirationDate') or '')[:10] or None,
                            'dte': number(c.get('daysToExpiration')), 'bid': number(c.get('bid')),
                            'ask': number(c.get('ask')), 'bidSize': number(c.get('bidSize')),
                            'askSize': number(c.get('askSize')), 'last': number(c.get('last')),
                            'mark': number(c.get('mark')), 'volume': number(c.get('totalVolume')),
                            'openInterest': number(c.get('openInterest')), 'iv': number(c.get('volatility')),
                            'delta': number(c.get('delta')), 'gamma': number(c.get('gamma')),
                            'theta': number(c.get('theta')), 'vega': number(c.get('vega')),
                            'quoteTime': number(c.get('quoteTimeInLong'))})
        contracts.sort(key=lambda c: (c['expiration'] or '', c['strike'] or 0, c['type'] or ''))
        return {'symbol': data.get('symbol'), 'underlyingPrice': number(data.get('underlyingPrice')),
                'expirations': sorted({c['expiration'] for c in contracts if c['expiration']}),
                'contracts': contracts}


def live_provider():
    """Where stock and option data comes from: the local bridge, then Schwab, then Alpaca."""
    if os.getenv('MARKET_DATA_URL', '').strip():
        return Bridge()
    if configured() and status()['connected']:
        return Schwab()
    return Alpaca()


def source_name():
    if os.getenv('MARKET_DATA_URL', '').strip():
        return 'bridge'
    if configured() and status()['connected']:
        return 'schwab'
    return 'alpaca'
