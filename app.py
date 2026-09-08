"""Local, single-user suggestion dashboard. Run: python app.py."""
import copy
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from collections import deque
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))


load_env()
from market import Alpaca, DataError, CHART_RANGES, now, stamp, age, describe_et
from research import research_cycle, long_term_cycle, analyze_headline
from strategy import make_ideas, quote_problem
from discovery import candidates as discover_candidates
from changes import ChangeLog
from planning import build as build_plans
from idea_review import analyze_proposal, conclude
from position_engine import PositionsMixin
from session_chat import ChatMixin
from market import chart_spec
from chart_analysis import ChartAnalysisMixin
from positions import holdings
import ai

DEFAULT = {'budget': 10000, 'risk_pct': 1, 'loss_limit_pct': 3, 'mode': 'live',
           'assets': ['stock', 'option', 'bitcoin'], 'symbols': ['SPY', 'QQQ', 'AAPL', 'NVDA'],
           'research': True, 'require_research': True, 'research_interval': 1200, 'research_calls': 60,
           'auto_position_review': False, 'closed_market_ideas': False, 'long_term_research': False,
           'max_position_pct': 25, 'long_risk': 'balanced', 'long_months': 6, 'long_focus': '',
           'daily_ai_limit': 250}

# Settings an open paper position's reserved capital and risk were sized against. Only these are
# pinned while a position is open; everything else is operational and may change mid-position.
CAPITAL_SETTINGS = ('budget', 'risk_pct', 'loss_limit_pct', 'max_position_pct', 'mode')

# Settings that may be retuned while the session runs. These change what research is requested and
# what evidence a live idea needs, which is exactly what you want to adjust while watching it work.
# They never alter how an existing position was sized, so no restart is required.
LIVE_SETTINGS = ('research', 'require_research', 'research_interval', 'research_calls',
                 'daily_ai_limit', 'long_term_research')


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Settings must be a JSON object.')
    if 'mode' in data and data['mode'] != 'live':
        raise ValueError('Live market data is the only supported session mode.')
    config = dict(DEFAULT, **{k: v for k, v in data.items() if k in DEFAULT})
    # Ceilings are guardrails against a typo, not trading advice. Conventional discretionary risk is
    # 1-2% per idea, but a long option reserves its entire premium, so small accounts need more room
    # before any option can be sized at all.
    for key, lo, hi in [('budget', 10, 10000000), ('risk_pct', .1, 25), ('loss_limit_pct', .5, 50),
                        ('max_position_pct', 5, 100), ('research_interval', 300, 3600), ('research_calls', 3, 300),
                        ('daily_ai_limit', 10, 2000)]:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{key} must be between {lo} and {hi}.')
    if config['loss_limit_pct'] < config['risk_pct']:
        raise ValueError('Session loss allowance must be at least the per-idea risk allowance.')
    config['mode'] = 'live'
    if not isinstance(config['assets'], list) or not config['assets'] or any(a not in ('stock', 'option', 'bitcoin') for a in config['assets']):
        raise ValueError('Choose stocks, options and/or Bitcoin.')
    if not isinstance(config['symbols'], list) or not 1 <= len(config['symbols']) <= 8:
        raise ValueError('Provide 1–8 stock/option underlying symbols.')
    if any(not isinstance(s, str) or not re.fullmatch('[A-Z]{1,5}', s) for s in config['symbols']):
        raise ValueError('Use uppercase stock symbols, 1–5 letters. Bitcoin is selected separately.')
    config['symbols'] = list(dict.fromkeys(config['symbols']))
    if config['long_risk'] not in ('conservative', 'balanced', 'aggressive'):
        raise ValueError('Choose conservative, balanced or aggressive research risk.')
    if type(config['long_months']) is not int or config['long_months'] not in (1, 3, 6, 12, 24):
        raise ValueError('Choose a research horizon of 1, 3, 6, 12 or 24 months.')
    if not isinstance(config['long_focus'], str) or len(config['long_focus']) > 1000:
        raise ValueError('Research interests must be text of at most 1,000 characters.')
    config['long_focus'] = config['long_focus'].strip()
    for key in ('research', 'require_research', 'auto_position_review', 'closed_market_ideas', 'long_term_research'):
        if type(config[key]) is not bool:
            raise ValueError(f'{key} must be true or false.')
    if config['mode'] == 'live' and config['require_research'] and not config['research']:
        raise ValueError('Enable research or turn off the research requirement for technical-only mode.')
    return config


class Engine(PositionsMixin, ChatMixin, ChartAnalysisMixin):
    def __init__(self, db_path=None):
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.db_path = Path(db_path or ROOT / 'data' / 'journal.sqlite3')
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scans (id TEXT PRIMARY KEY, at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS ai_usage (day TEXT PRIMARY KEY, requests INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS outlooks (id TEXT PRIMARY KEY, at TEXT, payload TEXT)')
            outlook_rows=db.execute('SELECT payload FROM outlooks ORDER BY at DESC LIMIT 20').fetchall()
            rows = db.execute('SELECT payload FROM trades').fetchall()
            saved = db.execute("SELECT payload FROM settings WHERE name='session'").fetchone()
            last_scan = db.execute('SELECT payload FROM scans ORDER BY at DESC LIMIT 1').fetchone()
            review_rows = db.execute('SELECT payload FROM reviews ORDER BY at DESC LIMIT 20').fetchall()
        self.trades = [json.loads(row[0]) for row in rows]
        self.config = copy.deepcopy(DEFAULT)
        if saved or last_scan:
            try:
                self.config = validate(json.loads(saved[0]) if saved else json.loads(last_scan[0])['config'])
            except (ValueError, TypeError, KeyError):
                pass
        open_trades = [t for t in self.trades if t['status'] == 'open']
        if open_trades:
            # An open paper position pins only what its reserved capital and risk were sized against.
            # Research cadence, request caps and watchlist are operational and stay freely editable.
            pinned = open_trades[0]['config']
            self.config = validate(dict(self.config, **{k: pinned[k] for k in CAPITAL_SETTINGS if k in pinned}))
        self.running = False
        self.generation = 0
        self.scanning = False
        self.market_refresh_requested = False
        self.researching = False
        self.calls_used = 0
        self.reports = []
        self.changes=ChangeLog()
        self.outlook = {'status': 'idle'}
        self.thesis_report={'status':'idle'}
        self.outlook_history=[json.loads(r[0]) for r in outlook_rows]
        self.outlook_running = False
        self.chat_running = False
        self.chat_messages = []
        self.chart_analyzing = False
        self.chart_analysis = {}
        self.last_research = 0
        self.last_scan = None
        self.next_scan = None
        self.ideas, self.analyses, self.rejected, self.errors, self.news = [], [], [], [], []
        self.snapshot = None
        self.provider = None
        self.activity = deque(maxlen=300)
        self.workers = {}
        self.event_counter = 0
        self.reviewing = False
        self.review_calls = 0
        self.idea_review = {'status': 'idle'}
        self.review_history = [json.loads(r[0]) for r in review_rows]
        self.market_session = None
        self.scan_interval = 60
        self.seen_warnings = set()
        self.ai_test = {'status': 'idle'}
        self.ai_testing = False
        self.long_reports = []
        self.long_researching = False
        self.last_long_research = 0
        self.news_analysis = {}
        self.news_analyzing = None
        self.init_positions()
        self.trace('Session', 'idle', 'Server ready. Start a session to see market and research activity.')

    def trace(self, component, status, message, details=None, generation=None):
        """Bounded operational events only: never prompts, raw responses or model reasoning."""
        with self.lock:
            if generation is not None and (not self.running or generation != self.generation):
                return
            secret_values = [os.getenv(k) for k in ('APCA_API_KEY_ID', 'APCA_API_SECRET_KEY', 'OPENAI_API_KEY','ANTHROPIC_API_KEY','GOOGLE_API_KEY','LOCAL_AI_API_KEY') if os.getenv(k)]
            def clean(value):
                if isinstance(value, dict):
                    return {str(k): clean(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [clean(v) for v in value[:20]]
                if isinstance(value, str):
                    for secret in secret_values:
                        value = value.replace(secret, '[redacted]')
                    return value[:1500]
                return value
            self.event_counter += 1
            event = clean({'id': self.event_counter, 'at': stamp(now()), 'component': component,
                           'status': status, 'message': message, 'details': details or {}})
            self.activity.append(event)
            self.workers[component] = {k: event[k] for k in ('at', 'status', 'message')}

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_trade(self, trade):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO trades VALUES (?, ?)', (trade['id'], json.dumps(trade)))

    def daily_ai_limit(self):
        """Shared UTC-day request budget. Configurable because the right number depends on the
        provider's pricing, not on anything this app can know."""
        try:
            return int(self.config.get('daily_ai_limit', 120))
        except (TypeError, ValueError):
            return 120

    def ai_usage(self):
        day = stamp(now())[:10]
        with self.db() as db:
            row = db.execute('SELECT requests FROM ai_usage WHERE day=?', (day,)).fetchone()
        return {'day': day, 'requests': row[0] if row else 0, 'limit': self.daily_ai_limit()}

    def reserve_ai(self, count, ignore_pause=False):
        """Reserve before sending. This shared UTC-day cap survives app/session restarts."""
        if self.ai_paused and not ignore_pause:return False
        day = stamp(now())[:10]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO ai_usage VALUES (?,0)', (day,))
            limit = self.daily_ai_limit()
            result = db.execute('UPDATE ai_usage SET requests=requests+? WHERE day=? AND requests+?<=?', (count,day,count,limit))
            return result.rowcount == 1

    def feed_health(self):
        at = now()
        rows = []
        if self.snapshot:
            for symbol, instrument in self.snapshot['instruments'].items():
                quote = instrument.get('quote', {})
                problem = quote_problem(quote, at)
                bars = instrument.get('bars', [])
                quote_age = age(quote.get('t'), at)
                rows.append({'symbol': symbol, 'feed': instrument['feed'],
                             'status': 'warning' if problem else 'fresh',
                             'detail': problem or 'Current two-sided quote', 'quote_at': quote.get('t'),
                             'quote_age': round(quote_age,1) if math.isfinite(quote_age) else None,
                             'candle_at': bars[-1].get('t') if bars else None, 'candles': len(bars)})
        return rows

    def allowances(self):
        current = [t for t in self.trades if t['mode'] == self.config['mode']]
        open_trades = [t for t in current if t['status'] == 'open']
        # UTC-day paper losses persist across stop/start and process restarts.
        today = stamp(now())[:10]
        losses = sum(max(0, -t.get('pnl', 0)) for t in current if t.get('closed_at', '')[:10] == today)
        available = max(0, self.config['budget'] - losses - sum(t['notional'] for t in open_trades))
        risk_left = max(0, self.config['budget'] * self.config['loss_limit_pct']/100 - losses - sum(t['risk'] for t in open_trades))
        return available, risk_left, losses

    def data_categories(self):
        """Report each live data category independently so partial feeds remain visible."""
        snapshot = self.snapshot or {}
        instruments = snapshot.get('instruments', {})
        categories = {
            'stocks': {'label':'Stocks', 'status':'available' if any(i.get('asset') == 'stock' for i in instruments.values()) else 'unavailable',
                       'detail':f"{sum(i.get('asset') == 'stock' for i in instruments.values())} symbols with market data"},
            'options': {'label':'Options', 'status':'available' if snapshot.get('chains') else 'unavailable',
                        'detail':f"{sum(len(v) for v in snapshot.get('chains',{}).values())} contracts returned"},
            'bitcoin': {'label':'Bitcoin', 'status':'available' if instruments.get('BTC/USD') else 'unavailable',
                        'detail':'BTC/USD quote and candles' if instruments.get('BTC/USD') else 'No BTC/USD quote'},
            'news': {'label':'News', 'status':'available' if snapshot.get('news') else 'unavailable',
                     'detail':f"{len(snapshot.get('news',[]))} headlines returned"},
            'research': {'label':'AI research', 'status':'available' if any(r.get('evidence') for r in self.reports) else 'unavailable',
                         'detail':f"{len(self.reports)} research lanes reported"},
        }
        at=now()
        for category,asset in [('stocks','stock'),('bitcoin','bitcoin')]:
            matching=[i for i in instruments.values() if i.get('asset')==asset]
            fresh=sum(not quote_problem(i.get('quote',{}),at) and bool(i.get('bars')) and 60<=age(i['bars'][-1].get('t'),at)<=150 for i in matching)
            categories[category].update(status='available' if fresh else 'unavailable',detail=f'{fresh} / {len(matching)} instruments with fresh quotes and completed candles')
        contracts=[c for chain in snapshot.get('chains',{}).values() for c in chain.values()]
        fresh_options=sum(not quote_problem(c.get('latestQuote',{}),at,.08) for c in contracts)
        categories['options'].update(status='available' if fresh_options else 'unavailable',detail=f'{fresh_options} / {len(contracts)} contracts with fresh two-sided quotes')
        for key,asset in [('stocks','stock'),('options','option'),('bitcoin','bitcoin')]:
            if asset not in self.config['assets']:
                categories[key].update(status='waiting',detail='Not selected for monitoring')
            elif key in ('stocks','options') and snapshot and not snapshot.get('equity_open'):
                session=snapshot.get('market_session') or {}
                reopen=describe_et(session.get('next_open'))
                if session.get('status')=='closing':
                    detail='Final 15 minutes of the session; no new stock or option entries'
                elif session.get('status')=='closed':
                    detail='US stock session closed'+(f'; reopens {reopen}' if reopen else '')
                else:
                    detail='Session status unavailable; entitlements not verified this scan'
                categories[key].update(status='waiting',detail=detail)
        fresh_reports=[r for r in self.reports if r.get('status')=='complete' and -5<=age(r.get('at'),at)<=1200 and any(e.get('fresh') and -60<=age(e.get('published_at'),at)<=86400 for e in r.get('evidence',[]))]
        categories['research'].update(status='available' if fresh_reports else 'unavailable',detail=f'{len(fresh_reports)} research lanes with current sourced evidence')
        if not fresh_reports:
            failed=[r for r in self.reports if r.get('status')=='unavailable']
            if self.researching:
                categories['research'].update(status='waiting',detail='Research agents are running; waiting for validated evidence')
            elif failed:
                categories['research'].update(status='unavailable',detail='; '.join(r['lane']+': '+r.get('coverage','Request failed') for r in failed))
            else:
                categories['research'].update(status='waiting',detail='Requests completed, but no sources passed publication-time and freshness checks' if self.reports else 'Waiting for the first research cycle')
        if self.ai_paused or not self.config['research']:
            categories['research'].update(status='waiting',detail='AI research disabled in session settings'+(' (all AI calls are also paused)' if self.ai_paused else '') if not self.config['research'] else 'All AI calls paused — use Resume AI to enable requests')
        if snapshot and age(snapshot.get('at'),at)>150:
            categories['news'].update(status='unavailable',detail='News retrieval is stale; waiting for another scan')
        if not self.running or not snapshot:
            for item in categories.values(): item.update(status='waiting', detail='Monitoring stopped' if not self.running else 'Waiting for the first live scan')
        return categories

    def discovery_view(self):
        rows=discover_candidates(self.reports, self.config['symbols'], now())
        for row in rows:
            idea=next((i for i in self.ideas if i['underlying']==row['symbol'] and age(i['quote'].get('t'), now())<=45 and age(i['created_at'], now())<=60), None)
            rejection=next((r['reason'] for r in self.rejected if r['symbol']==row['symbol']), None)
            if not self.running:
                row['status']='Session stopped'
            elif 'stock' not in self.config['assets']:
                row['status']='Enable stocks to check this candidate'
            elif idea:
                row['status']='Qualified idea — see Market desk'
            elif rejection:
                row['status']=rejection
            elif self.snapshot and not self.snapshot.get('equity_open'):
                row['status']='Waiting for the US stock session'
        return rows

    def chart(self, symbol, span='1D', interval='auto'):
        """Historical bars for any supported ticker.

        Charts read market data only, so they work with the session stopped and the market closed.
        """
        if not isinstance(symbol,str):raise ValueError('Enter a ticker or BTC/USD.')
        symbol=symbol.strip().upper()
        if symbol not in ('BTC/USD',) and not re.fullmatch('[A-Z]{1,5}',symbol):
            raise ValueError('Enter a stock ticker (1–5 letters) or BTC/USD.')
        spec = chart_spec(span, interval)
        with self.lock:
            provider=self.provider
            session=self.market_session
        provider=provider or Alpaca()
        at=now()
        if symbol!='BTC/USD' and not session:
            # So a chart viewed without a running session can still say whether the market is open.
            # The clock is cached until its own boundary, so this is not a per-request call.
            # The clock is a nicety here; never let it fail the chart itself.
            try:session=provider.session(at)
            except Exception:session=None
        bars=provider.history(symbol,span,at) if interval == 'auto' else provider.history(symbol,span,at,interval)
        closes=[b['c'] for b in bars]
        summary={}
        if closes:
            summary={'first':closes[0],'last':closes[-1],'high':max(b['h'] for b in bars),
                     'low':min(b['l'] for b in bars),'bars':len(bars),
                     'change_pct':round((closes[-1]/closes[0]-1)*100,2) if closes[0] else None,
                     'from':bars[0]['t'],'to':bars[-1]['t']}
        return {'symbol':symbol,'span':span,'interval':interval,'label':spec['label'],
                'timeframe':spec['timeframe'],'asset':'bitcoin' if symbol=='BTC/USD' else 'stock',
                'feed':'Alpaca crypto (venue-specific)' if symbol=='BTC/USD' else str(getattr(provider,'stock_feed','') or 'stock').upper(),
                'bars':bars,'summary':summary,'metrics':{},'session':session,'fetched_at':stamp(at)}

    def state(self):
        with self.lock:
            available, risk_left, losses = self.allowances()
            current = [t for t in self.trades if t['mode']==self.config['mode']]
            closed = [t for t in current if t['status']=='closed']
            open_trades = [t for t in current if t['status']=='open']
            fresh_marks = [t for t in open_trades if t.get('marked_at') and 0 <= age(t['marked_at'], now()) <= 45]
            journal_summary = {'closed':len(closed), 'wins':sum(t.get('pnl',0)>0 for t in closed),
                               'net':round(sum(t.get('pnl',0) for t in closed),2), 'mode':self.config['mode'],
                               'open':len(open_trades), 'reserved':round(sum(t['notional'] for t in open_trades),2),
                               'unrealized':round(sum(t.get('unrealized',0) for t in fresh_marks),2) if open_trades and len(fresh_marks)==len(open_trades) else None}
            return copy.deepcopy({'application': 'daylight', 'version': '0.5.0', 'running': self.running, 'scanning': self.scanning,
                'config': self.config, 'last_scan': self.last_scan, 'next_scan': self.next_scan,
                'scan_interval': self.scan_interval, 'market_session': self.market_session, 'ai_test': self.ai_test,
                'market_refresh_requested': self.market_refresh_requested,
                'manual_summary': self.manual_summary(),
                'ideas': self.ideas, 'analyses': self.analyses, 'rejected': self.rejected,
                'errors': self.errors, 'news': self.news, 'reports': self.reports,
                'long_research': self.long_reports, 'long_researching': self.long_researching,
                'chat': {'running': self.chat_running, 'messages': self.chat_messages},
                'chart_analysis': self.chart_analysis, 'chart_analyzing': self.chart_analyzing,
                'news_analysis': self.news_analysis, 'news_analyzing': self.news_analyzing,
                'researching': self.researching, 'calls_used': self.calls_used,
                'debug': {'events': list(self.activity), 'workers': self.workers},
                'idea_review': self.idea_review, 'review_calls': self.review_calls,
                'review_history': self.review_history, 'ai_usage': self.ai_usage(), 'feed_health': self.feed_health(),
                'journal_summary': journal_summary,
                'outlook': self.outlook,
                'thesis_report': self.thesis_report,
                'changes': self.changes.events,
                # Built from the last snapshot even when stopped: these are conditional plans for the
                # next open, so stopping the scanner is no reason to throw them away.
                'next_session_ideas': build_plans(self.snapshot or {},self.config,self.reports,now()),
                'outlook_history': self.outlook_history,
                'discoveries': self.discovery_view(),
                'data_categories': self.data_categories(),
                'ai_paused':self.ai_paused,'ai_inflight':self.chart_analyzing or self.chat_running or self.researching or self.reviewing or self.parsing_fill or self.position_reviewing or self.outlook_running or self.long_researching or bool(self.news_analyzing),
                'manual_positions':self.manual_view(),'manual_fills':list(reversed(self.manual_fills[-50:])),
                'fill_draft':self.fill_draft,'position_review':self.position_review,
                'ai_settings':ai.settings(),
                'trades': list(reversed(self.trades[-100:])), 'available': available,
                'risk_left': risk_left, 'paper_losses_today': losses,
                'connections': {'alpaca': bool(os.getenv('APCA_API_KEY_ID') and os.getenv('APCA_API_SECRET_KEY')),
                    'openai': bool(os.getenv('OPENAI_API_KEY')), 'stock_feed': os.getenv('ALPACA_STOCK_FEED', 'sip'),
                    'option_feed': os.getenv('ALPACA_OPTION_FEED', 'opra')}, 'server_time': stamp(now())})

    def start(self, data):
        config = validate(data)
        provider = Alpaca()
        if config['research'] and not self.ai_paused and not ai.configured():
            raise ValueError('Configure an AI provider, pause AI, or disable AI research and its requirement.')
        with self.lock:
            if (self.running or self.scanning or self.researching or self.reviewing or self.position_reviewing
                or self.outlook_running or self.long_researching or self.chat_running or self.chart_analyzing):
                raise ValueError('Stop the session and wait for in-flight work to finish before starting again.')
            if (any(t['status'] == 'open' for t in self.trades)
                    and any(config[k] != self.config[k] for k in CAPITAL_SETTINGS)):
                raise ValueError('Budget, risk and position limits cannot change while paper positions are open, '
                                 'because their reserved capital was sized against them. Close them first, or restore '
                                 'the previous values. Research settings can be changed freely.')
            self.config, self.provider = config, provider
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)", (json.dumps(config),))
            self.running = True
            self.generation += 1
            # Per-scan results are rebuilt immediately, but research cost real AI requests and is
            # timestamped evidence: the freshness gate already refuses anything stale, so keeping it
            # across a restart is safe and stops a stray Stop/Go from destroying paid work.
            self.ideas, self.analyses, self.rejected, self.errors = [], [], [], []
            self.snapshot = None
            self.calls_used, self.last_research = 0, 0
            self.last_long_research = 0
            self.news_analysis = {}
            self.review_calls = 0
            self.idea_review = {'status': 'idle'}
            self.next_scan = stamp(now())
            self.scan_interval = 60
            self.seen_warnings = set()
            self.workers = {}
            self.trace('Session', 'running', f"Started {config['mode']} session with ${config['budget']:,.2f} paper capital.",
                       {'assets': config['assets'], 'watchlist': config['symbols']})
            if not config['research']:
                self.trace('Research', 'idle', 'AI research is disabled for this session.')
            self.wake.set()

    def stop(self):
        with self.lock:
            self.changes.record(self.ideas,[],[],['Monitoring stopped; current ideas withdrawn.'],now())
            self.running = False
            self.generation += 1
            self.ideas = []
            self.next_scan = None
            self.market_refresh_requested = False
            if self.idea_review.get('status') == 'running':
                self.idea_review = {'status': 'cancelled', 'message': 'Session stopped; assessment result will be discarded.'}
            if self.position_review.get('status')=='running':
                self.position_review={'status':'cancelled','message':'Session stopped; review results discarded.'}
            if self.outlook_running:
                self.outlook={'status':'cancelled','message':'Session stopped; outlook result discarded.'}
                if self.thesis_report.get('status')=='running':self.thesis_report={'status':'cancelled','message':'Session stopped; thesis review discarded.'}
            for worker in list(self.workers):
                if self.workers[worker]['status'] == 'running':
                    self.workers[worker] = {'at': stamp(now()), 'status': 'stopped', 'message': 'Session stopped; in-flight results will be discarded.'}
            self.trace('Session', 'stopped', 'Session stopped. No new scans or research will be scheduled; in-flight results are discarded.')
            self.wake.set()

    def research_job(self, generation, symbols):
        try:
            reports = research_cycle(symbols, progress=lambda component, status, message, details=None:
                                     self.trace(component, status, message, details, generation=generation),can_send=self.ai_allowed)
            with self.lock:
                if self.running and generation == self.generation:
                    self.reports = reports
                    self.trace('Research', 'complete', 'Research cycle finished. Fresh evidence will be considered on the next market scan.',
                               {'reports': len(reports), 'evidence_items': sum(len(r['evidence']) for r in reports)})
        except Exception as exc:
            self.trace('Research', 'error', f'Research cycle failed ({type(exc).__name__}); unavailable evidence cannot unlock ideas.', generation=generation)
        finally:
            with self.lock:
                self.researching = False

    def request_review(self, proposal):
        if not isinstance(proposal,str) or not 5 <= len(proposal.strip()) <= 2000:
            raise ValueError('Describe your idea in 5–2,000 characters.')
        with self.lock:
            if self.ai_paused:raise ValueError('AI is paused. Resume AI before checking an idea.')
            if not self.running or self.config['mode'] != 'live':
                raise ValueError('Start a live-data session before checking a real-market idea.')
            if not ai.configured():
                raise ValueError('Configure an AI provider for natural-language idea checks.')
            if not self.snapshot or not self.last_scan or age(self.last_scan,now()) > 120:
                raise ValueError('Wait for a current market scan before checking an idea.')
            if self.reviewing:
                raise ValueError('An idea check is already running.')
            if self.review_calls >= 10:
                raise ValueError('The 10-request idea-check cap for this session has been reached.')
            if not self.reserve_ai(1):
                raise ValueError('The shared daily AI request cap is reached. Market scans continue; new AI requests resume next UTC day.')
            self.reviewing = True
            self.review_calls += 1
            self.idea_review = {'status':'running', 'proposal':proposal.strip(), 'requested_at':stamp(now())}
            available, risk_left, _ = self.allowances()
            research_context = []
            for report in self.reports:
                current = -5 <= age(report.get('at'), now()) <= 1200
                usable = [e for e in report['evidence'] if current and e['fresh']
                          and -60 <= age(e['published_at'], now()) <= 86400]
                research_context.append({'lane':report['lane'], 'status':report['status'] if current else 'stale',
                                         'at':report['at'], 'coverage':report['coverage'], 'evidence':usable,
                                         'excluded_items':len(report['evidence'])-len(usable)})
            context = {'at':self.last_scan, 'mode':self.config['mode'], 'budget':self.config['budget'],
                       'available':available,'risk_left':risk_left,'errors':self.errors,'rejected':self.rejected,
                       'analyses':[{k:a[k] for k in ('symbol','asset','direction','reason','metrics')} for a in self.analyses],
                       'eligible_ideas':[{k:i[k] for k in ('symbol','underlying','asset','direction','entry','stop','target','quantity','risk','notional')} for i in self.ideas],
                       'research':research_context}
            self.trace('Idea check','running','Comparing a user proposal with the latest scan and research.',
                       {'checks_used':self.review_calls,'check_cap':10})
            threading.Thread(target=self.review_job,args=(self.generation,proposal.strip(),copy.deepcopy(context)),daemon=True).start()

    def request_outlook(self, proposal):
        if not isinstance(proposal, str) or not 5 <= len(proposal.strip()) <= 2000:
            raise ValueError('Describe a longer-term question in 5–2,000 characters.')
        with self.lock:
            if self.ai_paused: raise ValueError('AI is paused. Resume AI before requesting an outlook.')
            if not self.running or self.config['mode'] != 'live': raise ValueError('Start a live-data session before requesting a long-term outlook.')
            if not ai.configured(): raise ValueError('Configure an AI provider first.')
            if not ai.settings()['web_research']: raise ValueError('Long-term research requires a provider with web search.')
            if self.outlook_running: raise ValueError('A long-term outlook request is already running.')
            if not self.reserve_ai(1): raise ValueError('The shared daily AI request cap is reached.')
            self.outlook_running = True
            self.outlook = {'status':'running', 'proposal':proposal.strip(), 'requested_at':stamp(now())}
            context = {'requested_at':stamp(now()), 'watchlist':self.config['symbols'], 'budget':self.config['budget']}
            self.trace('Long-term outlook','running','Researching an investment thesis; one AI request reserved.')
            threading.Thread(target=self.outlook_job, args=(self.generation, proposal.strip(), copy.deepcopy(context)), daemon=True).start()

    def request_thesis(self, proposal):
        if not isinstance(proposal,str) or not 5<=len(proposal.strip())<=2000:raise ValueError('Describe your proposed trade in 5–2,000 characters.')
        with self.lock:
            if self.ai_paused:raise ValueError('AI is paused. Resume AI to research your thesis.')
            if not ai.configured() or not ai.settings()['web_research']:raise ValueError('Configure an AI provider with web search for thesis research.')
            if self.outlook_running:raise ValueError('A thesis or long-term outlook is already being researched.')
            if not self.reserve_ai(1):raise ValueError('The shared daily AI request cap is reached.')
            self.outlook_running=True
            self.thesis_report={'status':'running','question':proposal.strip()}
            context={'requested_at':stamp(now()),'budget':self.config['budget']}
            self.trace('Thesis review','running','Researching the proposed instrument and timeframe; one AI request reserved.')
            threading.Thread(target=self.thesis_job,args=(self.generation,proposal.strip(),context),daemon=True).start()

    def thesis_job(self, generation, proposal, context):
        try:
            from outlook import assess_thesis
            result=assess_thesis(proposal,context,self.ai_allowed)
            from trade_plan import recommendation
            result['specific_trade']=recommendation(result, None, context['budget'])
            if result['trade_request']['instrument'] in ('call','put') and result['specific_trade']['status']!='candidate' and result['verdict']=='favorable':
                result['verdict']='cautiously-favorable'
                result['missing'].append('A specific option contract has not passed current market-data checks.')
            record=dict(result,status='complete',kind='thesis',question=proposal,checked_at=stamp(now()),id=uuid.uuid4().hex)
            with self.lock:
                if generation!=self.generation:return
                with self.db() as db:db.execute('INSERT INTO outlooks VALUES (?,?,?)',(record['id'],record['checked_at'],json.dumps(record)))
                self.thesis_report=record
                self.outlook_history=[record]+self.outlook_history[:19]
                self.trace('Thesis review','complete','Thesis verdict: '+record['verdict'])
        except Exception as exc:
            with self.lock:
                if generation==self.generation:self.thesis_report={'status':'error','message':str(exc) if isinstance(exc,DataError) else 'Research failed; no verdict issued.'}
        finally:
            with self.lock:self.outlook_running=False

    def outlook_job(self, generation, proposal, context):
        try:
            from outlook import assess
            result = assess(proposal, context, self.ai_allowed)
            from trade_plan import recommendation
            result['specific_trade'] = recommendation(result, None, context.get('budget', self.config['budget']))
            result['status']='complete'; result['checked_at']=stamp(now()); result['question']=proposal
            with self.lock:
                if self.running and generation == self.generation:
                    self.outlook=result
                    record=dict(result,id=uuid.uuid4().hex)
                    with self.db() as db:db.execute('INSERT INTO outlooks VALUES (?,?,?)',(record['id'],record['checked_at'],json.dumps(record)))
                    self.outlook_history=[record]+self.outlook_history[:19]
                    self.trace('Long-term outlook','complete','Outlook saved: '+result['outlook'],{'sources':len(result['sources'])})
        except Exception as exc:
            with self.lock:
                if self.running and generation == self.generation:
                    self.outlook={'status':'error','question':proposal,'message':str(exc) if isinstance(exc,DataError) else 'Outlook failed; no conclusion issued.'}
        finally:
            with self.lock: self.outlook_running=False

    def review_job(self, generation, proposal, context):
        try:
            if not self.ai_allowed():raise DataError('AI paused before the idea assessment was sent.')
            assessment = analyze_proposal(proposal,context)
            with self.lock:
                if not self.running or generation != self.generation:
                    return
                available,risk_left,_ = self.allowances()
                result = conclude(assessment,self.ideas,self.config,now(),available,risk_left,
                                  {t['underlying'] for t in self.trades if t['status']=='open'})
                if age(context['at'], now()) > 120:
                    result.update(verdict='no', label='Not supported right now', expires_at=None)
                    result['checks'].append('The market context used by the AI is now stale; request a new assessment.')
                if not result['sources']:
                    result['sources'] = [e for r in context['research'] for e in r['evidence']
                                         if e['symbol'] in (assessment['symbol'],'MARKET')][:6]
                self.idea_review = {'status':'complete','proposal':proposal,'result':result,'context_at':context['at']}
                record = dict(copy.deepcopy(self.idea_review), id=uuid.uuid4().hex)
                with self.db() as db:
                    db.execute('INSERT INTO reviews VALUES (?,?,?)', (record['id'], result['checked_at'], json.dumps(record)))
                self.review_history = [record] + self.review_history[:19]
                self.trace('Idea check','complete',f"Assessment: {result['verdict'].upper()} — {result['label']}.",
                           {'interpretation':result['interpretation'],'checks':result['checks']})
        except Exception as exc:
            with self.lock:
                if self.running and generation == self.generation:
                    message = str(exc) if isinstance(exc,DataError) else 'Idea assessment failed; no verdict issued.'
                    self.idea_review = {'status':'error','proposal':proposal,'message':message}
                    self.trace('Idea check','error',message)
        finally:
            with self.lock:
                self.reviewing = False

    def research_symbols(self):
        config = self.config
        symbols = config['symbols'] + (['BTC/USD'] if 'bitcoin' in config['assets'] else [])
        return symbols + [r['symbol'] for r in discover_candidates(self.reports, config['symbols'], now())]

    def research_blocker(self, calls):
        """Shared preconditions for any research request. Returns a message, or None when it may run."""
        if self.ai_paused: return 'AI is paused. Resume AI to run research.'
        if not ai.configured(): return 'Configure an AI provider first.'
        if not ai.settings()['web_research']: return 'The selected provider has no web search adapter.'
        if not self.running: return 'Start a session before running research.'
        if not self.config['research']: return 'AI research is disabled in this session’s settings.'
        if self.calls_used + calls > self.config['research_calls']:
            return 'This session’s AI request cap is reached. Restart the session to allow more research.'
        return None

    def launch_research(self, force=False):
        with self.lock:
            config = self.config
            if force:
                blocker = self.research_blocker(3)
                if blocker: raise ValueError(blocker)
                if self.researching: raise ValueError('A research cycle is already running.')
            elif (self.ai_paused or not ai.settings()['web_research'] or not self.running or config['mode'] != 'live'
                  or not config['research'] or self.researching or self.calls_used + 3 > config['research_calls']
                  or time.monotonic() - self.last_research < config['research_interval']):
                return
            self.researching = True
            if not self.reserve_ai(3):
                self.researching = False
                self.last_research = time.monotonic()
                self.trace('Research', 'warning', 'Shared daily AI request cap reached; market scans continue without new AI requests.')
                if force: raise ValueError('The shared daily AI request cap is reached. New AI requests resume next UTC day.')
                return
            self.calls_used += 3  # Reserve before requests, including failures. No unbounded retries.
            self.trace('Research', 'running', 'Launching three independent web-research agents.'
                       + (' Requested manually.' if force else ''),
                       {'requests_reserved': self.calls_used, 'request_cap': config['research_calls'], 'manual': force})
            self.last_research = time.monotonic()
            threading.Thread(target=self.research_job, args=(self.generation, self.research_symbols()), daemon=True).start()

    def launch_long_research(self, force=False):
        """Optional fourth agent on a multi-week horizon. One request; never gates intraday entries."""
        with self.lock:
            config = self.config
            if force:
                blocker = self.research_blocker(1)
                if blocker: raise ValueError(blocker)
                if self.long_researching: raise ValueError('Long-term research is already running.')
            elif (not config['long_term_research'] or self.ai_paused or not ai.settings()['web_research'] or not self.running
                  or not config['research'] or self.long_researching or self.calls_used + 1 > config['research_calls']
                  or time.monotonic() - self.last_long_research < config['research_interval']):
                return
            self.long_researching = True
            if not self.reserve_ai(1):
                self.long_researching = False
                self.last_long_research = time.monotonic()
                if force: raise ValueError('The shared daily AI request cap is reached. New AI requests resume next UTC day.')
                return
            self.calls_used += 1
            self.last_long_research = time.monotonic()
            self.trace('Long-term research', 'running', 'Requesting one multi-week research report.'
                       + (' Requested manually.' if force else ''),
                       {'requests_reserved': self.calls_used, 'request_cap': config['research_calls'], 'manual': force})
            brief = {k: config[k] for k in ('long_risk', 'long_months', 'long_focus')}
            threading.Thread(target=self.long_research_job, args=(self.generation, self.research_symbols(), brief), daemon=True).start()

    def long_research_job(self, generation, symbols, brief=None):
        try:
            report = long_term_cycle(symbols, progress=lambda component, status, message, details=None:
                                     self.trace(component, status, message, details, generation=generation),
                                     can_send=self.ai_allowed, brief=brief)
            from trade_plan import recommendation
            for view in report.get('views', []):
                view['specific_trade'] = recommendation(view, None, self.config['budget'])
            with self.lock:
                if self.running and generation == self.generation:
                    self.long_reports = [report]
        except Exception as exc:
            self.trace('Long-term research', 'error', f'Long-term research failed ({type(exc).__name__}); no view issued.', generation=generation)
        finally:
            with self.lock:
                self.long_researching = False

    def set_long_research_brief(self, data):
        with self.lock:
            if self.long_researching:
                raise ValueError('Wait for the current long-horizon report before changing its brief.')
            self.config = validate(dict(self.config, **{k: data[k] for k in ('long_risk', 'long_months', 'long_focus') if k in data}))
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)", (json.dumps(self.config),))

    def update_live_settings(self, data):
        """Retune research settings without stopping the session."""
        if not isinstance(data, dict): raise ValueError('Expected a settings object.')
        changes = {k: v for k, v in data.items() if k in LIVE_SETTINGS}
        if not changes: raise ValueError('No adjustable research settings were supplied.')
        with self.lock:
            config = validate(dict(self.config, **changes))
            previous = {k: self.config[k] for k in changes}
            self.config = config
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)", (json.dumps(config),))
            if not config['long_term_research']:
                self.long_reports = []
            described = ', '.join(f'{k} {previous[k]} → {config[k]}' for k in sorted(changes) if previous[k] != config[k])
            self.trace('Session', 'idle', 'Research settings updated' + (f': {described}.' if described else ' with no change.'))
            self.wake.set()

    def set_long_term_research(self, enabled):
        if type(enabled) is not bool: raise ValueError('Long-term research must be true or false.')
        with self.lock:
            self.config['long_term_research'] = enabled
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)", (json.dumps(self.config),))
            if not enabled:
                self.long_reports = []
            self.trace('Long-term research', 'idle',
                       'Long-term research enabled: one extra AI request per research cycle.' if enabled
                       else 'Long-term research disabled. No long-horizon AI requests will be made.')

    def request_news_analysis(self, article):
        """One AI request digging into a single headline the user picked. Never automatic."""
        if not isinstance(article, dict): raise ValueError('Select a headline to analyse.')
        headline, link = article.get('headline'), article.get('url')
        with self.lock:
            if not isinstance(headline, str) or not headline.strip(): raise ValueError('This headline is missing its text.')
            if not any(n.get('headline') == headline for n in self.news):
                raise ValueError('This headline is no longer in the current news feed. Refresh and try again.')
            blocker = ('AI is paused. Resume AI to analyse a headline.' if self.ai_paused else
                       'Configure an AI provider first.' if not ai.configured() else
                       'Headline analysis needs a provider with web search.' if not ai.settings()['web_research'] else None)
            if blocker: raise ValueError(blocker)
            if self.news_analyzing: raise ValueError('A headline is already being analysed.')
            if not self.reserve_ai(1): raise ValueError('The shared daily AI request cap is reached.')
            self.news_analyzing = headline
            self.news_analysis[headline] = {'status': 'running', 'headline': headline, 'requested_at': stamp(now())}
            context = {'watchlist': self.config['symbols'],
                       'market': [{k: a[k] for k in ('symbol', 'direction', 'reason')} for a in self.analyses][:8]}
            self.trace('Headline agent', 'running', f'Digging into one headline: {headline[:120]}',
                       {'source': article.get('source'), 'url': link})
            threading.Thread(target=self.news_analysis_job, args=(dict(article), copy.deepcopy(context)), daemon=True).start()

    def news_analysis_job(self, article, context):
        headline = article['headline']
        try:
            if not self.ai_allowed(): raise DataError('AI paused before the headline request was sent.')
            result = analyze_headline(article, context, can_send=self.ai_allowed)
            with self.lock:
                self.news_analysis[headline] = dict(result, status='complete', headline=headline)
                self.trace('Headline agent', 'complete', f'Headline assessed with {result["confidence"]} confidence.',
                           {'affected': [a['symbol'] + ': ' + a['effect'] for a in result['affected']], 'angles': len(result['angles'])})
        except Exception as exc:
            message = str(exc) if isinstance(exc, DataError) else 'The headline could not be assessed; no conclusion issued.'
            with self.lock:
                self.news_analysis[headline] = {'status': 'error', 'headline': headline, 'message': message}
                self.trace('Headline agent', 'error', message)
        finally:
            with self.lock:
                self.news_analyzing = None
                # Keep only the most recent assessments so the state payload stays small.
                if len(self.news_analysis) > 12:
                    for key in list(self.news_analysis)[:-12]:
                        del self.news_analysis[key]

    def request_market_refresh(self):
        with self.lock:
            if not self.running:
                raise ValueError('Start a session before refreshing market data.')
            if self.scanning or self.market_refresh_requested:
                raise ValueError('Market data is already refreshing.')
            self.market_refresh_requested = True
            self.next_scan = stamp(now())
            self.trace('Market data', 'queued', 'Manual market refresh requested. No AI research will be triggered.')
            self.wake.set()

    def scan(self):
        with self.lock:
            if not self.running or self.scanning:
                return
            self.scanning = True
            market_only = self.market_refresh_requested
            self.market_refresh_requested = False
            generation, config, provider = self.generation, copy.deepcopy(self.config), self.provider
            started = time.monotonic()
            self.trace('Market data', 'running', 'Fetching completed minute candles, current quotes and market-session status.',
                       {'provider': provider.name})
        try:
            if not market_only:
                self.launch_research()
                self.launch_long_research()
            monitored_config=copy.deepcopy(config)
            with self.lock:
                manual=[p for p in holdings(self.manual_fills).values() if p['quantity']>1e-8]
                discoveries=discover_candidates(self.reports, config['symbols'], now())
            discovered_symbols=[r['symbol'] for r in discoveries] if 'stock' in config['assets'] else []
            if config['mode']=='live':
                monitored_config['symbols']=list(dict.fromkeys(config['symbols']+discovered_symbols+[p['underlying'] for p in manual if p['asset']!='bitcoin']))
                if any(p['asset']=='bitcoin' for p in manual) and 'bitcoin' not in monitored_config['assets']:monitored_config['assets'].append('bitcoin')
                if any(p['asset']!='bitcoin' for p in manual) and 'stock' not in monitored_config['assets']:monitored_config['assets'].append('stock')
            snapshot = provider.collect(monitored_config, now())
            # Existing positions remain observable even when new-entry filters close the session
            # or the selected option has fallen out of the current near-the-money chain.
            if config['mode'] == 'live':
                with self.lock:
                    positions = [copy.deepcopy(t) for t in self.trades if t['status'] == 'open']+manual
                snapshot['tracking_quotes'], tracking_errors = provider.tracking_quotes(positions)
                snapshot['errors'].extend(tracking_errors)
            at = now()  # Check freshness after network/research latency, never at request start.
            self.trace('Market data', 'complete', f"Received data for {len(snapshot['instruments'])} instruments and {len(snapshot['news'])} headlines.",
                       {'equity_session_open': snapshot['equity_open'], 'clock': (snapshot.get('market_session') or {}).get('source'),
                        'candles': {s: len(i['bars']) for s, i in snapshot['instruments'].items()}}, generation=generation)
            self.note_session(snapshot.get('market_session'), generation)
            # Repeating an unchanged warning every minute buries real changes; log warnings when they appear.
            for warning in snapshot['errors']:
                if warning not in self.seen_warnings:
                    self.trace('Data checks', 'warning', warning, generation=generation)
            cleared = self.seen_warnings - set(snapshot['errors'])
            if cleared:
                self.trace('Data checks', 'complete', f'{len(cleared)} earlier warning(s) cleared.', {'cleared': sorted(cleared)}, generation=generation)
            self.seen_warnings = set(snapshot['errors'])
            with self.lock:
                available, risk_left, _ = self.allowances()
                reports = copy.deepcopy(self.reports)
            self.trace('Signal checks', 'running', 'Checking candle freshness, trend, volume, market alignment, research and position size.',
                       {'available_capital': round(available, 2), 'remaining_risk': round(risk_left, 2)}, generation=generation)
            idea_config=copy.deepcopy(config)
            idea_config['symbols']=config['symbols']+discovered_symbols
            idea_config['discovery_symbols']=discovered_symbols
            ideas, analyses, rejected = make_ideas(snapshot, idea_config, reports, at, available, risk_left)
            for idea in ideas:
                idea['origin']='discovery' if idea['underlying'] in discovered_symbols else 'watchlist'
            with self.lock:
                if not self.running or generation != self.generation:
                    return
                self.snapshot = snapshot
                for idea in ideas:
                    idea['id'] = uuid.uuid4().hex
                self.changes.record(self.ideas,ideas,rejected,snapshot['errors'],at)
                self.ideas, self.analyses, self.rejected = ideas, analyses, rejected
                self.errors, self.news = snapshot['errors'], snapshot['news']
                if config['research'] and not ai.settings()['web_research']:
                    self.errors.append('Selected local provider has no web-search adapter. Existing market data can be assessed, but required fresh web research may withhold new ideas.')
                for analysis in analyses:
                    self.trace('Price action', 'complete', f"{analysis['symbol']}: {analysis['direction']}. {analysis['reason']}",
                               {'symbol': analysis['symbol'], 'metrics': analysis['metrics']})
                for rejection in rejected:
                    self.trace('Decision', 'waiting', f"{rejection['symbol']}: wait. {rejection['reason']}")
                for idea in ideas:
                    self.trace('Decision', 'complete', f"{idea['symbol']}: {idea['direction']} idea passed the configured filters.",
                               {'entry': idea['entry'], 'stop': idea['stop'], 'target': idea['target'],
                                'quantity': idea['quantity'], 'reserved_risk': idea['risk'], 'reasons': idea['reasons']})
                self.trace('Signal checks', 'complete', f'{len(ideas)} ideas qualified; {len(rejected)} observations explain why to wait.')
                if config['mode'] == 'live' and self.calls_used + 3 > config['research_calls']:
                    self.errors.append('AI request cap reached for this session; research will age out. Restart explicitly to allow more calls.')
                    self.trace('Research', 'warning', 'Request cap reached. Cached research remains usable only while fresh.')
                self.last_scan = stamp(at)
                self.scan_interval = self.choose_interval(snapshot, at)
                self.next_scan = stamp(at + timedelta(seconds=self.scan_interval))
                self.mark_positions(at)
                self.trace('Session', 'waiting', 'Scan finished. Waiting for the next scan.',
                           {'elapsed_seconds': round(time.monotonic()-started, 1), 'next_scan': self.next_scan,
                            'interval_seconds': self.scan_interval})
                with self.db() as db:
                    db.execute('INSERT INTO scans VALUES (?, ?, ?)', (uuid.uuid4().hex, stamp(at), json.dumps({
                        'config': config, 'ideas': ideas, 'rejected': rejected, 'errors': self.errors,
                        'reports': reports, 'analyses': analyses})))
                if (not market_only and self.config.get('auto_position_review') and not self.ai_paused and manual and not self.position_reviewing
                    and not self.researching and time.monotonic()-self.position_last_review>=self.config['research_interval']):
                    try:self.request_position_review(automatic=True)
                    except ValueError as exc:
                        self.position_last_review=time.monotonic()
                        self.trace('Position review','warning',str(exc))
        except Exception as exc:
            with self.lock:
                if generation == self.generation:
                    self.changes.record(self.ideas,[],[],['Market scan failed; current ideas withdrawn until data returns.'],now())
                    self.ideas = []
                    self.errors = [str(exc) if isinstance(exc, DataError) else f'Scan failed ({type(exc).__name__}); suggestions withheld.']
                    self.next_scan = stamp(now() + timedelta(seconds=60))
                    self.trace('Market data', 'error', self.errors[0])
                    self.trace('Session', 'waiting', 'Suggestions cleared after a scan error. Retrying on the next scan.', {'next_scan': self.next_scan})
        finally:
            with self.lock:
                self.scanning = False

    def choose_interval(self, snapshot, at):
        """Scan every minute while anything is live; otherwise wait until shortly before the next open.

        With no live instruments (stocks closed, Bitcoin not selected) there is nothing new to observe,
        so the news feed and next-session plans refresh every ten minutes instead of every minute.
        """
        if snapshot['instruments']:
            return 60
        session = snapshot.get('market_session') or {}
        wait = 600
        if session.get('next_open'):
            until_open = -age(session['next_open'], at)
            if math.isfinite(until_open):
                wait = min(wait, max(60, until_open - 60))
        return int(wait)

    def note_session(self, session, generation):
        """Trace the US session state only when it changes, never on every scan."""
        if not session:
            return
        previous = (self.market_session or {}).get('status')
        with self.lock:
            self.market_session = session
        if session['status'] == previous:
            return
        reopen, close = describe_et(session.get('next_open')), describe_et(session.get('next_close'))
        if session['status'] == 'closed':
            message = 'US stock/options session closed' + (f'; reopens {reopen}.' if reopen else '.') + ' Bitcoin monitoring continues when selected.'
        elif session['status'] == 'closing':
            message = 'Final 15 minutes of the US session: no new stock or option entries.'
        elif session['status'] == 'open':
            message = 'US stock/options session open' + (f' until {close}.' if close else '.')
        else:
            message = session.get('detail') or 'US session status unavailable this scan.'
        self.trace('Market session', 'idle' if session['status'] != 'unknown' else 'warning', message,
                   {'next_open': session.get('next_open'), 'next_close': session.get('next_close')}, generation=generation)

    def request_ai_test(self):
        """One small request to confirm the saved provider, model and credential actually respond."""
        with self.lock:
            if self.ai_testing:
                raise ValueError('A connection test is already running.')
            if not ai.configured():
                raise ValueError('Save a provider, model and credential first.')
            if not self.reserve_ai(1, ignore_pause=True):
                raise ValueError('The shared daily AI request cap is reached; try again next UTC day.')
            settings = ai.settings()
            self.ai_testing = True
            self.ai_test = {'status': 'running', 'provider': settings['label'], 'model': settings['model'], 'requested_at': stamp(now())}
            self.trace('AI settings', 'running', f"Testing {settings['label']} · {settings['model']} with one small request.")
            threading.Thread(target=self.ai_test_job, args=(settings,), daemon=True).start()

    def ai_test_job(self, settings):
        try:
            result = ai.complete('Reply with the single word OK.', 'Connection test.', max_tokens=200)
            text = ' '.join(c.get('text', '') for o in result.get('output', []) for c in o.get('content', []) if c.get('type') == 'output_text').strip()
            outcome = {'status': 'ok', 'message': f"{settings['label']} responded with model {settings['model']}." + (f' Reply: “{text[:60]}”' if text else ''), 'checked_at': stamp(now()), 'provider': settings['label'], 'model': settings['model']}
        except DataError as exc:
            outcome = {'status': 'error', 'message': str(exc), 'checked_at': stamp(now()), 'provider': settings['label'], 'model': settings['model']}
        except Exception:
            outcome = {'status': 'error', 'message': 'The provider returned an unexpected response.', 'checked_at': stamp(now()), 'provider': settings['label'], 'model': settings['model']}
        with self.lock:
            self.ai_test = outcome
            self.ai_testing = False
            self.trace('AI settings', 'complete' if outcome['status'] == 'ok' else 'error', outcome['message'])

    def quote_for(self, trade):
        if not self.snapshot:
            return {}
        if trade['symbol'] in self.snapshot.get('tracking_quotes', {}):
            return self.snapshot['tracking_quotes'][trade['symbol']]
        if trade['asset'] == 'option':
            return self.snapshot['chains'].get(trade['underlying'], {}).get(trade['symbol'], {}).get('latestQuote', {})
        return self.snapshot['instruments'].get(trade['symbol'], {}).get('quote', {})

    def mark_positions(self, at):
        for trade in self.trades:
            if trade['status'] != 'open' or trade['mode'] != self.config['mode']:
                continue
            quote = self.quote_for(trade)
            if quote_problem(quote, at, .08 if trade['asset'] == 'option' else .003):
                trade['mark_status'] = 'No fresh quote; valuation unavailable'
                continue
            trade['mark'], trade['marked_at'] = quote['bp'], quote['t']
            trade['unrealized'] = round((quote['bp'] - trade['entry']) * trade['quantity'] * trade['multiplier'], 2)
            trade['mark_status'] = ('Stop reference crossed' if quote['bp'] <= trade['stop'] else
                                    'Target reference crossed' if quote['bp'] >= trade['target'] else 'Tracking')

    def paper_open(self, idea_id):
        with self.lock:
            if not self.running:
                raise ValueError('Start the session before tracking an idea.')
            idea = next((i for i in self.ideas if i['id'] == idea_id), None)
            if not idea or age(idea['expires_at'], now()) >= 0 or quote_problem(idea['quote'], now(), .08 if idea['asset']=='option' else .003):
                raise ValueError('Idea or quote expired. Wait for the next scan.')
            if any(t['status'] == 'open' and t['underlying'] == idea['underlying'] for t in self.trades):
                raise ValueError('A paper position for this underlying is already open.')
            available, risk_left, _ = self.allowances()
            if idea['notional'] > available or idea['risk'] > risk_left:
                raise ValueError('Remaining capital or risk allowance changed. Wait for a new scan.')
            trade = dict(copy.deepcopy(idea), id=uuid.uuid4().hex, mode=self.config['mode'],
                         config=copy.deepcopy(self.config), status='open', opened_at=stamp(now()),
                         multiplier=100 if idea['asset']=='option' else 1, mark_status='Awaiting next quote')
            self.save_trade(trade)
            self.trades.append(trade)
            self.trace('Paper journal', 'complete', f"{trade['symbol']}: hypothetical position opened; capital and risk reserved.",
                       {'notional': trade['notional'], 'risk': trade['risk']})

    def paper_close(self, trade_id):
        with self.lock:
            trade = next((t for t in self.trades if t['id'] == trade_id and t['status']=='open'), None)
            if not trade:
                raise ValueError('Open paper position not found.')
            quote = self.quote_for(trade)
            if quote_problem(quote, now(), .08 if trade['asset']=='option' else .003):
                raise ValueError('A fresh quote is required to close the paper estimate. Resume the session and wait for data.')
            trade.update(status='closed', exit=quote['bp'], closed_at=stamp(now()),
                         pnl=round((quote['bp']-trade['entry'])*trade['quantity']*trade['multiplier'], 2))
            self.save_trade(trade)
            self.trace('Paper journal', 'complete', f"{trade['symbol']}: hypothetical position closed at the observed bid.", {'estimated_pnl': trade['pnl']})

    def loop(self):
        while True:
            if self.running and (not self.next_scan or age(self.next_scan, now()) >= 0):
                self.scan()
            self.wake.wait(1)
            self.wake.clear()


def handler_for(engine):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, body, mime='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def local_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.local_host():
                return self.send(403, {'error': 'Localhost access only.'})
            path = urlparse(self.path).path
            if path == '/api/state':
                return self.send(200, dict(engine.state(), csrf=token))
            if path == '/api/export':
                with engine.db() as db:
                    rows = db.execute('SELECT payload FROM scans ORDER BY at DESC LIMIT 1000').fetchall()
                state = engine.state()
                return self.send(200, {'scans': [json.loads(r[0]) for r in rows], 'trades': state['trades'], 'idea_checks': state['review_history'],'reported_fills':state['manual_fills']})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.send(404, {'error': 'Not found'})
            file, mime = files[path]
            self.send(200, (ROOT / 'static' / file).read_bytes(), mime)

        def do_POST(self):
            if not self.local_host() or self.headers.get('X-CSRF-Token') != token:
                return self.send(403, {'error': 'Reload this local dashboard before trying again.'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 <= length <= (200000 if self.path == '/api/chart/analyze' else 16000):
                    raise ValueError('Request too large.')
                data = json.loads(self.rfile.read(length) or '{}')
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object.')
                if self.path == '/api/start':
                    engine.start(data)
                elif self.path == '/api/stop':
                    engine.stop()
                elif self.path == '/api/market/refresh':
                    engine.request_market_refresh()
                elif self.path == '/api/shutdown':
                    engine.stop()
                    self.send(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif self.path == '/api/idea/check':
                    engine.request_review(data.get('proposal'))
                elif self.path == '/api/outlook':
                    engine.request_outlook(data.get('proposal'))
                elif self.path == '/api/thesis':
                    engine.request_thesis(data.get('proposal'))
                elif self.path == '/api/chart':
                    self.send(200, engine.chart(data.get('symbol'), data.get('span', '1D'), data.get('interval', 'auto')))
                    return
                elif self.path == '/api/ai/pause':
                    engine.set_ai_paused(data.get('paused'))
                elif self.path == '/api/settings/research':
                    engine.update_live_settings(data)
                elif self.path == '/api/fills/delete':
                    engine.delete_fill(data.get('id'))
                elif self.path == '/api/fills/preview':
                    engine.request_fill_preview(data.get('text'))
                elif self.path == '/api/fills':
                    engine.save_fill(data.get('fill'),data.get('request_id'))
                elif self.path == '/api/positions/review':
                    engine.request_position_review()
                elif self.path == '/api/positions/auto':
                    engine.set_auto_position_review(data.get('enabled'))
                elif self.path == '/api/planning':
                    enabled=data.get('enabled')
                    if type(enabled) is not bool:raise ValueError('Planning must be true or false.')
                    with engine.lock:
                        engine.config['closed_market_ideas']=enabled
                        with engine.db() as db:db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)",(json.dumps(engine.config),))
                        engine.wake.set()
                elif self.path == '/api/research/refresh':
                    engine.launch_research(force=True)
                elif self.path == '/api/chat':
                    engine.request_chat(data.get('message'))
                elif self.path == '/api/chat/clear':
                    engine.clear_chat()
                elif self.path == '/api/chart/analyze':
                    engine.request_chart_analysis(data)
                elif self.path == '/api/research/long':
                    engine.launch_long_research(force=True)
                elif self.path == '/api/research/long/toggle':
                    engine.set_long_term_research(data.get('enabled'))
                elif self.path == '/api/research/long/settings':
                    engine.set_long_research_brief(data)
                elif self.path == '/api/news/analyze':
                    engine.request_news_analysis(data.get('article'))
                elif self.path == '/api/settings/ai/test':
                    engine.request_ai_test()
                elif self.path == '/api/settings/ai':
                    with engine.lock:
                        if (not engine.ai_paused or engine.researching or engine.reviewing or engine.parsing_fill
                            or engine.position_reviewing or engine.outlook_running or engine.long_researching or engine.news_analyzing or engine.chat_running or engine.chart_analyzing):
                            raise ValueError('Pause all AI and wait for in-flight requests to finish before changing providers.')
                        ai.save_settings(data)
                        engine.reports=[]
                        engine.ideas=[]
                        engine.last_research=0
                        engine.idea_review={'status':'idle'}
                        engine.position_review={'status':'idle'}
                        engine.ai_test={'status':'idle'}
                        engine.trace('AI settings','complete','AI provider settings saved; AI remains paused until resumed.')
                elif self.path == '/api/paper/open':
                    engine.paper_open(data.get('id'))
                elif self.path == '/api/paper/close':
                    engine.paper_close(data.get('id'))
                else:
                    return self.send(404, {'error': 'Not found'})
                self.send(200, {'ok': True})
            except (ValueError, DataError) as exc:
                self.send(400, {'error': str(exc)})

    return Handler


if __name__ == '__main__':
    engine = Engine()
    threading.Thread(target=engine.loop, daemon=True).start()
    port = int(os.getenv('PORT', '8765'))
    server = ThreadingHTTPServer(('127.0.0.1', port), handler_for(engine))
    print(f'Day Trade Suggester: http://127.0.0.1:{port}  |  Ctrl+C to stop', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.server_close()
