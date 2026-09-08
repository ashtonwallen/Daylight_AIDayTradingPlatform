"""Select a bounded, verified contract candidate for a researched thesis."""
import re
import math
from datetime import date, timedelta
from market import now, NY
from strategy import quote_state


def last_close(provider, symbol, at):
    """Official last completed daily close.

    Once the session ends, the "latest quote" is whatever was resting at the bell. On a
    single-exchange feed that is routinely one-sided (ask 0) or absurdly wide, so it is not a price.
    The last completed daily close is, and it is what any chart would show.
    """
    try:
        bars=provider.daily_history([symbol],at).get(symbol)
        if not isinstance(bars,list):return None
        for bar in reversed(bars):
            if not isinstance(bar,dict):continue
            close=bar.get('c')
            if isinstance(close,(int,float)) and not isinstance(close,bool) and math.isfinite(close) and close>0:
                return {'close':float(close),'at':bar.get('t')}
    except Exception:
        return None
    return None


def market_session(provider, at):
    """Best-effort session state. A provider without a clock simply yields no session context."""
    try:
        session=provider.session(at)
    except Exception:
        return None
    return session if isinstance(session,dict) else None


def select(provider, request, budget):
    symbol=request.get('symbol');kind=request.get('instrument')
    if not symbol or kind not in ('stock','call','put','bitcoin'):return {'status':'none','reason':request.get('reason') or 'No useful specific trade was identified.'}
    at=now()
    asset='bitcoin' if kind=='bitcoin' else 'stock'
    session=None if asset=='bitcoin' else market_session(provider,at)
    instrument=provider.instrument(symbol,at)
    q=instrument.get('quote',{})
    # A coherent quote from before the close prices a researched thesis perfectly well, and when even
    # that is unusable the daily close still does. Refusing both meant no thesis could ever name a
    # trade outside market hours, which is most of the time.
    state,detail=quote_state(q,at,session,asset)
    closed=asset!='bitcoin' and isinstance(session,dict) and session.get('status') in ('closed','closing')
    fallback=last_close(provider,symbol,at) if state=='unusable' and closed else None
    if state=='unusable' and not fallback:
        return {'status':'unavailable','reason':f'Current underlying quotes are unavailable ({detail}) Recheck the trade when fresh market data is available.'}
    if fallback:
        price,reference_state=fallback['close'],'close'
        basis=f" Reference is the last completed daily close ({str(fallback['at'])[:10]}); closing quotes were one-sided or unusably wide. Recheck at the open."
    else:
        price,reference_state=q['ap'],state
        basis=' Prices are the last quote before the close, not live; recheck at the open.' if state=='reference' else ''
    if kind in ('stock','bitcoin'):
        card={'status':'candidate','symbol':symbol,'instrument':kind,'entry_reference':price,
              'quote_at':fallback['at'] if fallback else q['t'],'quote_state':reference_state,
              'reason':'Broker-verified reference price; enter only when the research plan conditions are met. Confirm position size separately.'+basis}
        if not fallback:card.update(bid=q['bp'],ask=q['ap'])
        return card
    if provider.option_feed!='opra':return {'status':'unavailable','reason':'Specific option selection requires OPRA data access.'}
    if not (request.get('expiration') or request.get('horizon_end')):return {'status':'none','reason':'The assessment did not establish a usable option horizon.'}
    end=date.fromisoformat(request.get('expiration') or request['horizon_end'])
    if not at.astimezone(NY).date()<end<=at.astimezone(NY).date()+timedelta(days=730):return {'status':'unavailable','reason':'The intended horizon must be a future date within two years.'}
    last_expiry=end if request.get('expiration') else end+timedelta(days=21)
    exact_strike=request.get('strike')
    rows=provider.get('/v1beta1/options/snapshots/'+symbol,{'feed':'opra','limit':100,
        'strike_price_gte':exact_strike if exact_strike is not None else round(price*.9,2),
        'strike_price_lte':exact_strike if exact_strike is not None else round(price*1.1,2),
        'expiration_date_gte':str(end),'expiration_date_lte':str(last_expiry)}).get('snapshots',{})
    choices=[]
    for contract,data in rows.items():
        if not isinstance(data,dict):continue
        match=re.fullmatch(r'([A-Z]+)(\d{6})([CP])(\d{8})',contract)
        if not match or match[1]!=symbol or match[3]!=('C' if kind=='call' else 'P'):continue
        try:expiry=date.fromisoformat('20'+match[2][:2]+'-'+match[2][2:4]+'-'+match[2][4:])
        except ValueError:continue
        if not end<=expiry<=last_expiry:continue
        if exact_strike is not None and int(match[4])/1000 != exact_strike:continue
        quote=data.get('latestQuote') or {};delta=(data.get('greeks') or {}).get('delta')
        if quote_state(quote,at,session,'stock',.08)[0]=='unusable':continue
        if not isinstance(delta,(int,float)) or isinstance(delta,bool) or not math.isfinite(delta):continue
        if not (0 < abs(delta) <= 1 if exact_strike is not None else .4 <= abs(delta) <= .65):continue
        if (delta>0)!=(kind=='call'):continue
        premium=quote['ap']*100
        if premium>budget:continue
        spread=(quote['ap']-quote['bp'])/((quote['ap']+quote['bp'])/2)
        choices.append((abs(abs(delta)-.5)+spread,contract,expiry,quote,delta,int(match[4])/1000))
    if not choices:return {'status':'unavailable','reason':'No contract in the bounded sample passed quote, spread, delta, expiration and budget checks. No specific trade recommended.'}
    _,contract,expiry,quote,delta,strike=min(choices,key=lambda c:c[0])
    contract_state=quote_state(quote,at,session,'stock',.08)[0]
    return {'status':'candidate','symbol':contract,'underlying':symbol,'instrument':kind,'expiration':str(expiry),
            'strike':strike,'premium_per_share':quote['ap'],'cost_per_contract':round(quote['ap']*100,2),
            'bid':quote['bp'],'ask':quote['ap'],'quote_at':quote['t'],'delta':delta,'quote_state':contract_state,
            'reason':('User-specified strike preserved; ' if exact_strike is not None else '40–65 absolute delta; ') + ('user-specified expiration preserved; ' if request.get('expiration') else 'expiration at/after the research horizon; ') + 'spread ≤8% and cost within budget. Selected from a bounded broker sample, not an optimized or guaranteed best trade.' + (' Premium is the last quote before the close, not live; recheck at the open.' if contract_state=='reference' else ''),
            'risk':'Full premium can be lost. One-contract cost must fit your own risk allowance; no position size or order is authorized.'}
