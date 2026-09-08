"""Read-only providers. No order, brokerage balance, or execution API exists here."""
import json
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo('America/New_York')


def now():
    return datetime.now(UTC)


def stamp(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def age(value, at):
    try:
        return (at - parse(value)).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return float('inf')


def describe_et(value):
    """Human-readable New York time such as 'Tue Sep 8, 9:30 AM ET', or None if unparseable."""
    try:
        dt = parse(value).astimezone(NY)
    except (ValueError, TypeError, AttributeError):
        return None
    hour = dt.hour % 12 or 12
    return f"{dt.strftime('%a %b')} {dt.day}, {hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} ET"


UNKNOWN_SESSION = {'status': 'unknown', 'is_open': False, 'entries_open': False, 'next_open': None,
                   'next_close': None, 'checked_at': None, 'source': None, 'detail': None}

# Chart ranges use the bar size a trader would normally pair with each span. Intraday spans look
# back over several calendar days so a weekend or holiday still shows the last real session.
CHART_RANGES = {
    '1D': {'timeframe': '1Min', 'lookback': timedelta(days=7), 'session_only': True,
           'label': 'Latest session · 1-minute bars'},
    '5D': {'timeframe': '15Min', 'lookback': timedelta(days=9), 'label': '5 days · 15-minute bars'},
    '1M': {'timeframe': '1Hour', 'lookback': timedelta(days=32), 'label': '1 month · hourly bars'},
    '6M': {'timeframe': '1Day', 'lookback': timedelta(days=190), 'label': '6 months · daily bars'},
    '1Y': {'timeframe': '1Day', 'lookback': timedelta(days=370), 'label': '1 year · daily bars'},
    '5Y': {'timeframe': '1Week', 'lookback': timedelta(days=1835), 'label': '5 years · weekly bars'},
}


CHART_INTERVALS = {'1Min': 1, '5Min': 5, '15Min': 15, '1Hour': 60}


def chart_spec(span, interval='auto'):
    if span not in CHART_RANGES:
        raise ValueError('Choose one of the supported chart ranges.')
    spec = dict(CHART_RANGES[span])
    if interval != 'auto':
        if span not in ('1D', '5D') or interval not in CHART_INTERVALS:
            raise ValueError('Minute and hourly candle intervals are available for 1D and 5D ranges.')
        spec['timeframe'] = interval
        spec['label'] = ('Latest session' if span == '1D' else '5 days') + ' · ' + interval + ' candles'
    return spec


class DataError(Exception):
    pass


class Alpaca:
    name = 'Alpaca'

    def __init__(self):
        self.key = os.getenv('APCA_API_KEY_ID', '')
        self.secret = os.getenv('APCA_API_SECRET_KEY', '')
        self.stock_feed = os.getenv('ALPACA_STOCK_FEED', 'sip')
        self.option_feed = os.getenv('ALPACA_OPTION_FEED', 'opra')
        self._clock = None
        self._options_denied = None  # Set when the account lacks OPRA, to stop retrying every scan.
        if not self.key or not self.secret:
            raise DataError('Add Alpaca credentials to .env and restart the server before using live mode.')
        if self.stock_feed not in ('sip', 'iex') or self.option_feed not in ('opra', 'indicative'):
            raise DataError('Invalid data feed in .env. Stocks: sip/iex. Options: opra/indicative.')

    def get(self, path, params=None, clock=False):
        host = 'https://data.alpaca.markets'
        if clock:
            host = ('https://api.alpaca.markets' if os.getenv('ALPACA_ACCOUNT') == 'live'
                    else 'https://paper-api.alpaca.markets')
        request = Request(host + path + ('?' + urlencode(params) if params else ''), headers={
            'APCA-API-KEY-ID': self.key, 'APCA-API-SECRET-KEY': self.secret,
            'User-Agent': 'DayTradeSuggester/0.1',
        })
        try:
            with urlopen(request, timeout=12) as response:
                return json.load(response)
        except HTTPError as exc:
            descriptions = {401: 'credentials rejected', 403: 'feed entitlement or permission missing',
                            429: 'rate limit reached; will retry next cycle'}
            raise DataError(f'Alpaca {path}: {descriptions.get(exc.code, "HTTP " + str(exc.code))}.') from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise DataError(f'Alpaca {path}: data unavailable; will retry next cycle.') from None

    def session(self, at):
        """Equity session state. The clock is requested only once its known boundary has passed.

        Alpaca's clock returns is_open plus next_open/next_close, so the state is certain until that
        boundary. A cached answer is trusted for at most an hour as a guard against schedule changes.
        """
        cached = getattr(self, '_clock', None)
        if cached and 0 <= age(cached['checked_at'], at) < 3600:
            boundary = cached['next_close'] if cached['is_open'] else cached['next_open']
            if boundary and age(boundary, at) < 0:
                return self.session_state(cached, at, 'cached')
        clock = self.get('/v2/clock', clock=True)
        if not -5 <= age(clock.get('timestamp'), at) <= 90:
            raise DataError('Market clock is stale; stocks and options withheld.')
        record = {'is_open': bool(clock.get('is_open')), 'next_open': clock.get('next_open'),
                  'next_close': clock.get('next_close'), 'checked_at': stamp(at)}
        try:
            parse(record['next_open']); parse(record['next_close'])
            self._clock = record
        except (ValueError, TypeError, AttributeError):
            self._clock = None  # Without both boundaries the state cannot be cached safely.
        return self.session_state(record, at, 'fetched')

    @staticmethod
    def session_state(record, at, source):
        is_open = record['is_open']
        remaining = -age(record['next_close'], at) if record.get('next_close') else float('inf')
        closing = is_open and remaining < 900
        return {'status': 'closing' if closing else 'open' if is_open else 'closed', 'is_open': is_open,
                'entries_open': is_open and not closing, 'next_open': record.get('next_open'),
                'next_close': record.get('next_close'), 'checked_at': record['checked_at'],
                'source': source, 'detail': None}

    def instrument(self, symbol, at):
        crypto = symbol == 'BTC/USD'
        base = '/v1beta3/crypto/us' if crypto else '/v2/stocks'
        start = at - timedelta(minutes=90)
        if not crypto:
            opening = at.astimezone(NY).replace(hour=9, minute=30, second=0, microsecond=0)
            start = max(start, opening.astimezone(UTC))
        params = {'symbols': symbol, 'timeframe': '1Min', 'start': stamp(start),
                  'end': stamp(at.replace(second=0, microsecond=0) - timedelta(microseconds=1)),
                  'limit': 1000, 'sort': 'asc'}
        if not crypto:
            params['feed'] = self.stock_feed
        bars = self.get(base + '/bars', params).get('bars', {}).get(symbol, [])
        quote_params = {'symbols': symbol}
        if not crypto:
            quote_params['feed'] = self.stock_feed
        quote_path = base + ('/latest/quotes' if crypto else '/quotes/latest')
        quote = self.get(quote_path, quote_params).get('quotes', {}).get(symbol, {})
        return {'symbol': symbol, 'asset': 'bitcoin' if crypto else 'stock', 'bars': bars,
                'quote': quote, 'feed': 'Alpaca crypto (venue-specific)' if crypto else self.stock_feed.upper()}

    def history(self, symbol, span, at, interval='auto'):
        """Completed bars for a chart span. Independent of market hours and of the live session."""
        spec = chart_spec(span, interval)
        crypto = symbol == 'BTC/USD'
        base = '/v1beta3/crypto/us' if crypto else '/v2/stocks'
        params = {'symbols': symbol, 'timeframe': spec['timeframe'], 'sort': 'asc', 'limit': 10000,
                  'start': stamp(at - spec['lookback']),
                  'end': stamp(at.replace(second=0, microsecond=0) - timedelta(microseconds=1))}
        if not crypto:
            params['feed'] = self.stock_feed
            params['adjustment'] = 'split'
        # Alpaca pages long windows, and the crypto endpoint pages sooner than the stock one.
        # Follow a bounded number of pages so a range ends where it should, never unbounded.
        bars, token = [], None
        for _ in range(6):
            page = self.get(base + '/bars', dict(params, page_token=token) if token else params)
            bars.extend(page.get('bars', {}).get(symbol, []))
            token = page.get('next_page_token')
            if not token:
                break
        bars = [b for b in bars if all(isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool)
                                       and math.isfinite(b[k]) for k in ('o', 'h', 'l', 'c'))]
        minutes = CHART_INTERVALS.get(spec['timeframe'])
        if minutes:
            bars = [b for b in bars if parse(b['t']) + timedelta(minutes=minutes) <= at]
        if spec.get('session_only') and bars:
            # Show the most recent trading day rather than a partial slice of several.
            last_day = parse(bars[-1]['t']).astimezone(NY).date()
            bars = [b for b in bars if parse(b['t']).astimezone(NY).date() == last_day]
        return bars[-750:]

    def options(self, underlying, price, at):
        if self.option_feed != 'opra':
            raise DataError('Options withheld: OPRA required; indicative quotes are modified and trades delayed.')
        date = at.astimezone(NY).date()
        result = self.get('/v1beta1/options/snapshots/' + underlying, {
            'feed': 'opra', 'limit': 100, 'strike_price_gte': round(price * .98, 2),
            'strike_price_lte': round(price * 1.02, 2),
            'expiration_date_gte': str(date + timedelta(days=7)),
            'expiration_date_lte': str(date + timedelta(days=21)),
        })
        # A bounded first page is intentional: rank only these returned contracts.
        return result.get('snapshots', {})

    def tracking_quotes(self, positions):
        quotes, errors = {}, []
        for trade in positions:
            symbol = trade['symbol']
            try:
                if trade['asset'] == 'option':
                    result = self.get('/v1beta1/options/quotes/latest', {'symbols': symbol, 'feed': 'opra'})
                elif trade['asset'] == 'bitcoin':
                    result = self.get('/v1beta3/crypto/us/latest/quotes', {'symbols': symbol})
                else:
                    result = self.get('/v2/stocks/quotes/latest', {'symbols': symbol, 'feed': self.stock_feed})
                quotes[symbol] = result.get('quotes', {}).get(symbol, {})
            except DataError as exc:
                errors.append(f'Paper position {symbol}: {exc}')
        return quotes, errors

    def daily_history(self, symbols, at):
        result=self.get('/v2/stocks/bars',{'symbols':','.join(symbols),'timeframe':'1Day',
            'start':stamp(at-timedelta(days=60)),'end':stamp(at),'limit':10000,'sort':'asc',
            'feed':self.stock_feed,'adjustment':'split'})
        local=at.astimezone(NY)
        return {s:[b for b in bars if parse(b['t']).astimezone(NY).date()<local.date()
                   or (parse(b['t']).astimezone(NY).date()==local.date() and local.hour>=16)]
                for s,bars in result.get('bars',{}).items()}

    def collect(self, config, at):
        assets = config['assets']
        errors, instruments, chains = [], {}, {}
        session = dict(UNKNOWN_SESSION)
        if 'stock' in assets or 'option' in assets:
            try:
                session = self.session(at)
            except (DataError, KeyError, ValueError, TypeError) as exc:
                session['detail'] = str(exc)
                errors.append(str(exc))
        # A closed session is reported as state, not repeated as a warning on every scan.
        equity_open = session['entries_open']
        market_open = session['is_open'] if session['status'] != 'unknown' else None
        # Quotes and candles stay visible through the closing window; new entries are gated separately.
        symbols = list(dict.fromkeys(config['symbols'] + ['SPY'])) if session['is_open'] else []
        if 'bitcoin' in assets:
            symbols.append('BTC/USD')
        with ThreadPoolExecutor(max_workers=5) as pool:
            jobs = {s: pool.submit(self.instrument, s, at) for s in symbols}
            for symbol, job in jobs.items():
                try:
                    instruments[symbol] = job.result()
                except DataError as exc:
                    errors.append(f'{symbol}: {exc}')
        if 'option' in assets and equity_open and self._options_denied:
            errors.append(self._options_denied)
        elif 'option' in assets and equity_open:
            option_errors = []
            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs = {s: pool.submit(self.options, s, instruments[s]['bars'][-1]['c'], at)
                        for s in config['symbols'][:3] if instruments.get(s, {}).get('bars')}
                for symbol, job in jobs.items():
                    try:
                        chains[symbol] = job.result()
                    except DataError as exc:
                        option_errors.append(str(exc))
            # One clear, actionable line beats the same entitlement error once per underlying, and
            # there is no point re-asking every 60 seconds for an entitlement the account lacks.
            if option_errors and all('entitlement' in e or 'OPRA required' in e for e in option_errors):
                self._options_denied = ('Option ideas unavailable: this Alpaca plan does not include the OPRA '
                                        'options feed. Uncheck Options in your session settings, or add an OPRA '
                                        'subscription. Stocks and Bitcoin are unaffected.')
                errors.append(self._options_denied)
            else:
                errors.extend(option_errors)
        news = []
        daily_bars={}
        if config.get('closed_market_ideas') and market_open is False:
            try:daily_bars=self.daily_history(config['symbols'][:8],at)
            except (DataError,ValueError,KeyError) as exc:errors.append('Next-session history unavailable: '+str(exc))
        try:
            news = self.get('/v1beta1/news', {'symbols': ','.join(config['symbols']),
                            'start': stamp(at - timedelta(hours=24)), 'limit': 30}) .get('news', [])
        except DataError as exc:
            errors.append('News unavailable: ' + str(exc))
        return {'instruments': instruments, 'chains': chains, 'news': news, 'errors': errors,
                'equity_open': equity_open, 'market_open': market_open, 'market_session': session,
                'daily_bars': daily_bars, 'at': stamp(at), 'simulated': False}


class Demo:
    name = 'Synthetic demo'

    def collect(self, config, at):
        instruments, chains = {}, {}
        bases = {'SPY': 565, 'QQQ': 480, 'AAPL': 225, 'NVDA': 130, 'MSFT': 420, 'BTC/USD': 92000}
        symbols = list(dict.fromkeys(config['symbols'] + ['SPY']))
        if 'bitcoin' in config['assets']:
            symbols.append('BTC/USD')
        for index, symbol in enumerate(symbols):
            rng = random.Random(symbol)
            base = bases.get(symbol, 100 + index * 30)
            bars = []
            # Stable, synthetic series at each wall-clock minute; these are not historical prices.
            minute = int(at.timestamp() // 60)
            for i in range(65):
                phase = minute - 65 + i
                c = base * (1 + .00015 * (phase % 120) + .001 * math.sin(phase / 6 + index))
                c *= 1 + rng.uniform(-.00007, .00007)
                bars.append({'t': stamp(at.replace(second=0, microsecond=0) - timedelta(minutes=65-i)),
                             'o': c * .9999, 'h': c * 1.0003, 'l': c * .9996,
                             'c': round(c, 4), 'v': 20000 + rng.randrange(10000), 'vw': c})
            price = bars[-1]['c']
            instruments[symbol] = {'symbol': symbol, 'asset': 'bitcoin' if symbol == 'BTC/USD' else 'stock',
                'bars': bars, 'quote': {'bp': price * .9999, 'ap': price * 1.0001, 'bs': 100, 'as': 100, 't': stamp(at)},
                'feed': 'SYNTHETIC'}
            if symbol in config['symbols'][:3] and 'option' in config['assets']:
                expiry = (at.astimezone(NY) + timedelta(days=14)).strftime('%y%m%d')
                strike = round(price / 5) * 5
                chains[symbol] = {}
                for kind, delta in [('C', .5), ('P', -.5)]:
                    contract = f'{symbol}{expiry}{kind}{strike * 1000:08d}'
                    chains[symbol][contract] = {'latestQuote': {'bp': 1.20, 'ap': 1.24, 'bs': 50, 'as': 50, 't': stamp(at)},
                        'greeks': {'delta': delta}, 'impliedVolatility': .3}
        return {'instruments': instruments, 'chains': chains, 'news': [],
                'errors': ['Synthetic prices and contracts: demo results do not measure strategy performance.'],
                'equity_open': True, 'market_open': True, 'at': stamp(at), 'simulated': True,
                'market_session': dict(UNKNOWN_SESSION, status='open', is_open=True, entries_open=True, source='synthetic')}
