"""Shared research trade intent and broker-backed plan assembly; no extra AI calls."""
import math
import re
from datetime import date


TRADE_INSTRUCTIONS = '''
Return trade_request and trade_plan as well as the research fields.
For an open-ended question, independently choose ONE best supported ticker, instrument and horizon.
Do not require the user to supply a ticker or direction. Explain your choice and assumptions.
If the user specifies a ticker, instrument, strike or expiration, retain those constraints exactly;
evaluate that trade, never silently substitute another. If no useful trade is supported, choose none
and explain why. A bearish outlook must not become a long stock purchase.
trade_request: {symbol: uppercase stock/ETF ticker or BTC/USD or null,
instrument: stock/call/put/bitcoin/none, horizon_end: YYYY-MM-DD or null,
expiration: YYYY-MM-DD or null, strike: positive number or null, reason: string}.
Choose horizon_end from the research horizon and supplied current date, stating any assumption.
Only set expiration/strike when explicitly specified by the user; otherwise the broker selector
will choose an existing contract. Never invent an option symbol, quote, premium or Greeks.
trade_plan: {entry: string describing when to enter or wait, exit: string describing when to
take profit or close, invalidation: string, review_by: YYYY-MM-DD or null}.
Make conditions concrete, with dated catalysts or observable triggers where supported. Provide a
time exit/review even for buy-and-hold ideas. For long options, address decay, catalyst volatility
crush and closing before expiration. Do not invent numerical price levels without market evidence.
Broker verification will append the specific security, reference entry price and contract details.
No guaranteed returns, automatic orders or numerical win probabilities.'''


def intent(value):
    value = value if isinstance(value, dict) else {}
    symbol = value.get('symbol')
    kind = value.get('instrument', 'none')
    if kind not in ('stock', 'call', 'put', 'bitcoin', 'none'):
        raise ValueError('Invalid trade instrument.')
    if symbol is not None and (not isinstance(symbol, str) or not re.fullmatch(r'[A-Z]{1,5}|BTC/USD', symbol)):
        raise ValueError('Invalid trade ticker.')
    if symbol == 'BTC/USD' and kind not in ('bitcoin', 'none'):
        raise ValueError('BTC/USD requires the bitcoin instrument.')
    if kind == 'bitcoin' and symbol != 'BTC/USD':
        raise ValueError('Bitcoin requires BTC/USD.')
    result = {'symbol': symbol, 'instrument': kind}
    for key in ('horizon_end', 'expiration'):
        val = value.get(key)
        if val is not None:
            try:
                if not isinstance(val, str): raise ValueError()
                date.fromisoformat(val)
            except ValueError:
                raise ValueError(f'trade_request.{key} must be a YYYY-MM-DD date.') from None
        result[key] = val
    strike = value.get('strike')
    if strike is not None and (type(strike) not in (int, float) or not math.isfinite(strike) or strike <= 0):
        raise ValueError('Invalid option strike.')
    result['strike'] = strike
    result['reason'] = str(value.get('reason') or 'No useful specific trade was supported by this assessment.')[:700]
    return result


def plan(value):
    value = value if isinstance(value, dict) else {}
    result = {k: value.get(k, '')[:900] if isinstance(value.get(k, ''), str) else ''
              for k in ('entry', 'exit', 'invalidation')}
    review = value.get('review_by')
    if review is not None:
        if not isinstance(review, str): raise ValueError('Invalid trade review date.')
        date.fromisoformat(review)
    return dict(result, review_by=review)


def recommendation(report, provider, budget):
    request = report.get('trade_request') or {}
    if report.get('verdict') in ('unfavorable', 'insufficient-evidence') or report.get('outlook', report.get('direction')) == 'no-conclusion':
        return {'status': 'none', 'reason': 'The assessment does not support taking a specific trade.'}
    if request.get('instrument') == 'none' or not request.get('symbol'):
        return {'status': 'none', 'reason': request.get('reason') or 'No useful specific trade was identified.'}
    if report.get('outlook', report.get('direction')) == 'bearish' and request.get('instrument') in ('stock', 'bitcoin'):
        return {'status': 'none', 'reason': 'A long purchase conflicts with the bearish assessment.'}
    from contracts import select
    try:
        if provider is None:
            from market import Alpaca
            provider = Alpaca()
        result = select(provider, request, budget)
    except Exception:
        result = {'status': 'unavailable', 'reason': 'Broker data could not verify a specific trade. Recheck market-data access and freshness.'}
    if result.get('status') == 'candidate':
        result['plan'] = report.get('trade_plan') or {}
        result['selection_reason'] = request.get('reason', '')
        result['review_by'] = (report.get('trade_plan') or {}).get('review_by') or request.get('horizon_end')
    return result
