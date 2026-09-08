"""Technical interpretation of the exact candle snapshot submitted by the chart."""
import math
import re
import threading

import ai
from market import chart_spec, now, parse, stamp


def validate_chart(data):
    symbol = data.get('symbol')
    if not isinstance(symbol, str) or not re.fullmatch(r'(?:[A-Z]{1,5}|BTC/USD)', symbol):
        raise ValueError('Choose a chart ticker first.')
    spec = chart_spec(data.get('span'), data.get('interval', 'auto'))
    bars = data.get('bars')
    if not isinstance(bars, list) or not 5 <= len(bars) <= 750:
        raise ValueError('Chart analysis requires 5–750 displayed candles.')
    clean, previous = [], None
    for b in bars:
        if not isinstance(b, dict):
            raise ValueError('Invalid chart candle.')
        try:
            at = parse(b['t'])
            if at.tzinfo is None or at > now() or (previous and at <= previous):
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError('Chart candles need ordered, dated timestamps.') from None
        values = [b.get(k) for k in ('o', 'h', 'l', 'c')]
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError('Chart prices must be positive finite numbers.')
        o, h, low, c = values
        if low > min(o, c) or h < max(o, c) or low > h:
            raise ValueError('Inconsistent candle high/low.')
        v = b.get('v')
        volume = v if type(v) in (int, float) and math.isfinite(v) and v >= 0 else None
        clean.append([stamp(at), o, h, low, c, volume])
        previous = at
    closes = [b[4] for b in clean]
    return {'symbol': symbol, 'span': data['span'], 'interval': data.get('interval', 'auto'),
            'timeframe': spec['timeframe'], 'captured_at': stamp(now()),
            'from': clean[0][0], 'to': clean[-1][0], 'count': len(clean),
            'provenance': 'Displayed chart snapshot submitted by the local browser; not independently refetched.',
            'columns': ['time', 'open', 'high', 'low', 'close', 'volume'], 'candles': clean,
            'metrics': {'first_close': closes[0], 'last_close': closes[-1],
                        'high': max(b[2] for b in clean), 'low': min(b[3] for b in clean),
                        'change_pct': round((closes[-1] / closes[0] - 1) * 100, 3),
                        'sma20': sum(closes[-20:]) / 20 if len(closes) >= 20 else None,
                        'sma50': sum(closes[-50:]) / 50 if len(closes) >= 50 else None}}


def analyze(context, can_send):
    response = ai.complete('''Analyze this displayed chart snapshot as a technical research assistant.
Use only supplied OHLCV candles and calculated metrics. The requested interval applies to every candle;
the history span is a separate window. Explain trend, momentum, volume confirmation (or missing volume),
plausible support/resistance, bullish and bearish scenarios, and invalidation. Price levels must be
grounded in the candles. Do not invent indicators, news, options or current quotes. Small samples,
gaps, stale dates and incomplete daily/weekly bars limit confidence; say so. This is a historical
technical interpretation, not a live execution signal or a prediction. No order sizing or guarantees.
Return JSON: bias (bullish/bearish/mixed/insufficient-data), summary (string),
observations (array of strings), levels (array of strings), scenarios (array of strings),
limitations (array of strings). Keep each list to four concise items.''', context,
                           web=False, max_tokens=2500, can_send=can_send)
    if response.get('status') != 'completed':
        raise ValueError('Chart analysis was incomplete.')
    text = '\n'.join(c.get('text', '') for o in response.get('output', []) for c in o.get('content', [])
                     if c.get('type') == 'output_text')
    result = ai.extract_json(text)
    if result.get('bias') not in ('bullish', 'bearish', 'mixed', 'insufficient-data') or not isinstance(result.get('summary'), str):
        raise ValueError('Chart analysis returned an invalid assessment.')
    clean = {'bias': result['bias'], 'summary': result['summary'][:1600]}
    for key in ('observations', 'levels', 'scenarios', 'limitations'):
        if not isinstance(result.get(key), list):
            raise ValueError('Chart analysis omitted required details.')
        clean[key] = [x[:700] for x in result[key][:4] if isinstance(x, str)]
    return clean


class ChartAnalysisMixin:
    def request_chart_analysis(self, data):
        context = validate_chart(data)
        with self.lock:
            if self.ai_paused:
                raise ValueError('AI is paused. Resume AI to analyze a chart.')
            if not ai.configured():
                raise ValueError('Configure an AI provider first.')
            if self.chart_analyzing:
                raise ValueError('A chart analysis is already running.')
            if not self.reserve_ai(1):
                raise ValueError('The shared daily AI request cap is reached.')
            self.chart_analyzing = True
            self.chart_analysis = {'status': 'running', 'chart': {k: v for k, v in context.items() if k != 'candles'}}
            self.trace('Chart analysis', 'running', f"Analyzing {context['symbol']} {context['timeframe']} chart; one AI request reserved.")
            threading.Thread(target=self.chart_analysis_job, args=(self.generation, context), daemon=True).start()

    def chart_analysis_job(self, generation, context):
        try:
            result = dict(analyze(context, self.ai_allowed), status='complete')
        except Exception:
            result = {'status': 'error', 'summary': 'No technical assessment was produced. Check AI availability or pause status and try again.'}
        with self.lock:
            if generation != self.generation:
                result = {'status': 'error', 'summary': 'Session changed during analysis. Run again for the current chart.'}
            self.chart_analysis = dict(result, chart={k: v for k, v in context.items() if k != 'candles'}, at=stamp(now()))
            self.chart_analyzing = False
            self.trace('Chart analysis', result['status'], 'Chart analysis finished.')
