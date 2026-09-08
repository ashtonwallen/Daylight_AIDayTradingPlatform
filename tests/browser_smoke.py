"""Real browser workflow against an isolated database and a patched live-provider fixture."""
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import timedelta
from app import Engine, handler_for
from market import Alpaca, now, stamp
from test_core import bullish_snapshot
from test_review import ASSESSMENT
from playwright.sync_api import sync_playwright, expect

def fake_init(self):
    """Stand in for credential loading while keeping the attributes the provider really has."""
    self.key, self.secret = 'browser-test', 'browser-test'
    self.stock_feed, self.option_feed, self._clock = 'iex', 'opra', None


def daily_bars(count=200):
    at = now()
    return [{'t': stamp(at - timedelta(days=count - i)), 'o': 100 + i, 'h': 101 + i,
             'l': 99 + i, 'c': 100 + i, 'v': 1000} for i in range(count)]


with tempfile.TemporaryDirectory() as directory:
    engine = Engine(Path(directory)/'journal.sqlite3')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(engine))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    original_collect = Alpaca.collect

    def collect(self, config, at):
        with patch.object(Alpaca, 'collect', original_collect):
            return bullish_snapshot(at)

    try:
        with patch.object(Alpaca, 'collect', collect), patch.object(Alpaca, '__init__', fake_init), patch.object(Alpaca, 'history', return_value=daily_bars()), patch.object(Alpaca, 'session', return_value=None), patch.object(Alpaca,'tracking_quotes',return_value=({},[])), patch.object(Engine,'launch_research'), patch('app.ai.configured',return_value=True), sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            expect(page.locator('#go')).to_be_visible()
            expect(page.locator('#refresh-market')).to_be_disabled()
            # Feature explanations belong in tooltips, not paragraphs above every section.
            assert page.locator('[data-tip]').count() == 0, 'every data-tip should become a help button'
            tooltips = page.locator('.help-button').count()
            assert tooltips > 25, f'expected explanatory tooltips throughout, found {tooltips}'
            wordy = page.evaluate("[...document.querySelectorAll('p.hint:not([id])')]"
                                  ".filter(p => p.textContent.trim().length > 140).map(p => p.textContent.trim().slice(0, 60))")
            assert not wordy, f'these explanations should be tooltips: {wordy}'
            page.locator('#theme-toggle').click()
            expect(page.locator('html')).to_have_attribute('data-mode', 'light')
            page.reload()
            expect(page.locator('html')).to_have_attribute('data-mode', 'light')
            expect(page.locator('#theme-toggle')).to_have_text('Dark mode')
            page.locator('#theme-toggle').click()
            expect(page.locator('html')).to_have_attribute('data-mode', 'dark')
            # Every theme keeps a light and a dark mode, and the choice survives a reload.
            for theme in ('slate', 'ember', 'mono', 'daylight'):
                page.locator('#theme-select').select_option(theme)
                expect(page.locator('html')).to_have_attribute('data-theme', theme)
                assert page.evaluate("getComputedStyle(document.body).backgroundColor") != 'rgba(0, 0, 0, 0)'
            page.locator('#theme-select').select_option('slate')
            page.reload()
            expect(page.locator('html')).to_have_attribute('data-theme', 'slate')
            expect(page.locator('html')).to_have_attribute('data-mode', 'dark')
            page.locator('#theme-select').select_option('daylight')
            risk_help = page.get_by_role('button', name='Help: Risk / idea %', exact=True)
            risk_help.focus()
            expect(page.locator('#help-popup')).to_contain_text('Limit 1 of 3')
            expect(page.locator('#limits-preview')).to_contain_text('Largest single position')
            page.keyboard.press('Escape')
            expect(page.locator('#help-popup')).to_be_hidden()
            risk_help.click()
            expect(page.locator('#help-popup')).to_be_visible()
            page.locator('#budget').click()
            expect(page.locator('#help-popup')).to_be_hidden()
            page.locator('#budget').fill('20000')
            page.locator('#go').click()
            expect(page.locator('#stop')).to_be_visible()
            # Stopping is destructive, so it confirms first and keeps the research.
            page.locator('#stop').click()
            expect(page.locator('#stop-confirm')).to_be_visible()
            page.locator('#stop-cancel').click()
            expect(page.locator('#stop-confirm')).to_be_hidden()
            assert engine.running, 'cancelling the confirm must not stop the session'
            # A checked safety toggle must never read as OFF while the session runs.
            expect(page.locator('#require-research')).to_be_checked()
            expect(page.locator('#require-research')).to_be_enabled()
            expect(page.locator('#budget')).to_be_disabled()
            expect(page.locator('#capital-locked')).to_be_visible()
            # Tabs announce themselves properly.
            assert page.locator('.tabs [role=tab]').count() == 8
            expect(page.locator('[data-tab="overview"]')).to_have_attribute('aria-selected', 'true')
            engine.scan()
            expect(page.locator('.idea').first).to_be_visible(timeout=10000)
            expect(page.locator('#chart svg')).to_be_visible()
            page.locator('#refresh-market').click()
            expect(page.locator('#refresh-market')).to_contain_text('Refreshing')
            assert engine.market_refresh_requested
            engine.scan()
            expect(page.locator('#refresh-market')).to_be_enabled(timeout=10000)
            expect(page.locator('#scan-changes')).to_contain_text('What changed?')
            expect(page.locator('#rejected')).to_be_visible()  # watchlist status replaces the bare rejection list
            assert page.locator('#market-chip').count() == 0, 'header market chip should be gone'
            expect(page.locator('#opportunity-alerts')).to_be_checked()
            page.locator('#change-history summary').click()
            expect(page.locator('#change-events')).to_contain_text('New qualifying idea')
            page.locator('#opportunity-alerts').uncheck()
            assert page.evaluate("localStorage.getItem('daylight-alerts')") == 'off'
            expect(page.locator('#discovery')).to_contain_text('Beyond your watchlist')
            expect(page.locator('#connection-feeds')).not_to_contain_text('available')
            page.locator('[data-chart-pick="BTC/USD"]').click()
            expect(page.locator('#chart-caption')).to_contain_text('BTC/USD')
            page.wait_for_timeout(3200)
            expect(page.locator('#chart-caption')).to_contain_text('BTC/USD')
            # The latest price is readable off the chart itself, not only from the axis.
            expect(page.locator('#chart-price')).to_contain_text('$')
            expect(page.locator('#chart-price .delta')).to_contain_text('%')
            expect(page.locator('#chart-range-summary')).to_contain_text('bars')
            assert page.locator('#chart .price-tag-text').count() == 1, 'last price should be marked on the axis'
            # Hovering any point reports that bar, which matters most in line mode.
            page.locator('#chart svg').hover(position={'x': 300, 'y': 100})
            expect(page.locator('#chart-hover')).to_contain_text('O $')
            # A monitored symbol on the live intraday range must render from the scan, with no fetch.
            chart_requests = []
            page.on('request', lambda request: chart_requests.append(request.url) if '/api/chart' in request.url else None)
            page.locator('[data-chart-pick="SPY"]').click()
            page.wait_for_timeout(1200)
            assert not chart_requests, f'live intraday chart should not fetch history: {chart_requests}'
            # Other ranges are fetched history and work regardless of market hours.
            with patch.object(Alpaca, 'history', return_value=daily_bars()):
                page.locator('[data-span="1Y"]').click()
                expect(page.locator('#chart-caption')).to_contain_text('1 year')
                expect(page.locator('#chart-range-summary')).to_contain_text('US market')  # session state is always stated
                expect(page.locator('#chart svg')).to_be_visible()
                # 200 bars defaults to a line; the toggle must switch both ways and stick.
                expect(page.locator('[data-chart-type="line"]')).to_have_class('chart-span active')
                expect(page.locator('#chart-interval')).to_be_hidden()
                assert page.locator('#chart polyline.line').count() == 1
                chart_box = page.locator('#chart svg').bounding_box()
                page.mouse.move(chart_box['x'] + chart_box['width'] * .3, chart_box['y'] + chart_box['height'] * .5)
                expect(page.locator('#chart-price')).to_contain_text('Close ·')
                hovered_price = page.locator('#chart-price strong').inner_text()
                page.mouse.move(chart_box['x'] + chart_box['width'] * .6, chart_box['y'] + chart_box['height'] * .5)
                assert page.locator('#chart-price strong').inner_text() != hovered_price
                page.mouse.move(0, 0)
                expect(page.locator('#chart-price')).to_contain_text('over')
                page.locator('[data-chart-type="candles"]').click()
                expect(page.locator('#chart-interval')).to_be_visible()
                assert page.locator('#chart rect.candle').count() == 200
                assert page.evaluate("localStorage.getItem('daylight-chart-type')") == 'candles'
                page.locator('[data-chart-type="line"]').click()
                assert page.locator('#chart polyline.line').count() == 1
                page.locator('[data-span="1D"]').click()
                page.wait_for_timeout(800)
                page.locator('[data-chart-type="candles"]').click()
                page.locator('#chart-interval').select_option('5Min')
                expect(page.locator('#chart-caption')).to_contain_text('5Min')
                with patch('chart_analysis.analyze', return_value={'bias': 'mixed', 'summary': 'Five-minute momentum is mixed.', 'observations': [], 'levels': [], 'scenarios': [], 'limitations': ['Historical candles only.']}):
                    page.locator('#analyze-chart').click()
                    expect(page.locator('#chart-analysis-result')).to_contain_text('Five-minute momentum is mixed', timeout=10000)
                assert engine.chart_analysis['chart']['timeframe'] == '5Min'
                page.locator('#chart-interval').select_option('auto')
            page.locator('[data-open]').first.click()
            page.locator('[data-tab="journal"]').click()
            expect(page.locator('.trade')).to_have_count(1)
            page.locator('[data-close]').click()
            expect(page.locator('.trade')).to_contain_text('Estimated P/L')
            page.locator('[data-tab="research"]').click()
            expect(page.locator('.agent')).to_have_count(3)
            expect(page.locator('#long-term-toggle')).not_to_be_checked()  # opt-in, so it costs nothing by default
            expect(page.locator('#refresh-research')).to_be_visible()
            page.locator('#long-risk').select_option('conservative')
            page.locator('#long-months').select_option('24')
            page.locator('[data-long-preset="overlooked"]').click()
            page.locator('#save-long-brief').click()
            expect(page.locator('#long-brief-status')).to_have_text('Saved')
            assert engine.config['long_months'] == 24
            assert engine.config['long_risk'] == 'conservative'
            engine.long_reports = [{'status': 'complete', 'at': stamp(now()), 'brief': {'long_risk': 'conservative', 'long_months': 24, 'long_focus': ''}, 'coverage': 'A sourced idea.', 'evidence': [], 'views': [{'symbol': 'MSFT', 'direction': 'mixed', 'horizon': 'Two years', 'summary': 'Compare growth with investment costs.', 'catalysts': [], 'risks': [], 'sources': []}]}]
            expect(page.locator('[data-explore-long]')).to_be_visible(timeout=10000)
            page.locator('[data-explore-long]').click()
            expect(page.locator('#idea-text')).to_have_value(__import__('re').compile('.*MSFT.*', __import__('re').DOTALL))
            page.locator('[data-tab="research"]').click()
            page.screenshot(path='data/research-desktop.png', full_page=True)
            page.locator('[data-tab="session-chat"]').click()
            page.locator('[data-chat-prompt]').first.click()
            with patch('session_chat.answer', return_value={'text': 'Current research is mixed. Quotes must be checked before acting.', 'sources': []}):
                page.locator('#send-chat').click()
                expect(page.locator('.chat-message.assistant')).to_contain_text('Current research is mixed', timeout=10000)
            page.screenshot(path='data/chat-desktop.png', full_page=True)
            page.locator('#clear-chat').click()
            expect(page.locator('.chat-message')).to_have_count(0)
            page.locator('[data-tab="idea-check"]').click()
            expect(page.locator('[data-idea-mode="outlook"]')).to_be_visible()
            page.locator('[data-idea-mode="thesis"]').click()
            page.locator('#idea-text').fill('Should I buy SPY given the current market?')
            thesis={'verdict':'mixed','outlook':'mixed','summary':'Evidence is mixed.','horizon':'One month','interpretation':'SPY stock purchase','thesis':[],'risks':['Timing uncertainty'],'missing':[],'invalidation':[],'sources':[],'instruments':[],'trade_request':{'symbol':None,'instrument':'none','horizon_end':None}}
            with patch('outlook.assess_thesis',return_value=thesis), patch('contracts.select',return_value={'status':'unavailable','reason':'No current contract data'}), patch.dict('os.environ',{'OPENAI_API_KEY':'browser-test-key'}):
                page.locator('#check-idea').click()
                expect(page.locator('#idea-result .review-verdict')).to_contain_text('MIXED',timeout=10000)
            expect(page.locator('#idea-result')).to_contain_text('Specific trade candidate')
            expect(page.locator('#thesis-history')).to_contain_text('Should I buy SPY')
            engine.config['mode']='live'
            page.locator('[data-tab="debug"]').click()
            expect(page.locator('#debug-events')).to_contain_text('Fetching completed minute candles')
            page.locator('#debug-pause').click()
            engine.trace('Data checks','warning','Browser test: example unavailable feed.')
            page.wait_for_timeout(3500)
            expect(page.locator('#debug-events')).not_to_contain_text('Browser test: example unavailable feed.')
            page.locator('#debug-pause').click()
            page.locator('#debug-filter').select_option('problems')
            expect(page.locator('#debug-events')).to_contain_text('Browser test: example unavailable feed.')
            expect(page.locator('#debug-events')).not_to_contain_text('Fetching completed minute candles')
            page.locator('#debug-filter').select_option('all')
            with page.expect_download() as download:
                page.locator('#debug-export').click()
            assert download.value.suggested_filename == 'daylight-activity.json'
            page.locator('#pause-ai').click()
            expect(page.locator('#pause-ai')).to_have_text('Resume AI')
            expect(page.locator('#ai-pause-note')).to_contain_text('AI paused')
            page.locator('[data-tab="my-positions"]').click()
            expect(page.locator('#translate-fill')).to_be_disabled()
            expect(page.locator('#auto-positions')).not_to_be_checked()
            page.locator('#fill-symbol').fill('AAPL')
            page.locator('#fill-quantity').fill('2')
            page.locator('#fill-price').fill('100')
            page.locator('#fill-stop').fill('95')
            page.locator('#save-fill').click()
            expect(page.locator('#manual-positions')).to_contain_text('AAPL')
            page.locator('[data-tab="ai-settings"]').click()
            page.locator('#provider').select_option('local')
            expect(page.locator('#local-url-row')).to_be_visible()
            expect(page.locator('#provider-capability')).to_contain_text('no autonomous web search')
            # Do not save provider credentials or make real inference calls in this browser test.
            page.locator('[data-tab="debug"]').click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Debug mobile overflow'
            page.set_viewport_size({'width':1440,'height':1100})
            page.locator('[data-tab="overview"]').click()
            Path('data').mkdir(exist_ok=True)
            page.screenshot(path='data/dashboard-desktop.png', full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
            page.screenshot(path='data/dashboard-mobile.png', full_page=True)
            page.locator('#stop').click()
            page.locator('#stop-yes').click()
            expect(page.locator('#go')).to_be_visible()
            expect(page.locator('.idea')).to_have_count(0)
            assert not errors, errors
            browser.close()
            print('Browser passed: start, candles, ideas, paper open/close, research, stop, desktop and mobile.')
    finally:
        engine.stop()
        server.shutdown()
        server.server_close()
