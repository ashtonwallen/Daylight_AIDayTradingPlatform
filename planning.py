"""Next-session scenarios using completed daily bars and existing sourced research."""
from market import age, stamp
from research import research_gate
from strategy import positive


def build(snapshot, config, reports, at):
    if not config.get('closed_market_ideas') or snapshot.get('market_open') is not False:return []
    plans=[]
    for symbol, bars in snapshot.get('daily_bars',{}).items():
        if len(bars)<20 or not 0<=age(bars[-1].get('t'),at)<=7*86400:continue
        recent=bars[-20:]
        if any(not all(positive(b.get(k)) for k in ('o','h','l','c')) or not b['l']<=min(b['o'],b['c'])<=max(b['o'],b['c'])<=b['h'] for b in recent):continue
        closes=[b['c'] for b in recent]
        fast,slow=sum(closes[-5:])/5,sum(closes)/20
        direction='bullish' if closes[-1]>fast>slow else 'bearish' if closes[-1]<fast<slow else None
        if not direction:continue
        allowed,reason,evidence=research_gate(reports,symbol,direction,at,False)
        if not allowed:continue
        instruments=[]
        if direction=='bullish' and 'stock' in config['assets']:instruments.append('Stock purchase candidate')
        if 'option' in config['assets']:instruments.append('Long call candidate' if direction=='bullish' else 'Long put candidate')
        if not instruments:continue
        plans.append({'symbol':symbol,'direction':direction,'instruments':instruments,'planning_only':True,
                      'created_at':stamp(at),'reference_close':closes[-1],'reference_at':recent[-1]['t'],
                      'five_session_change':round((closes[-1]/closes[-6]-1)*100,2),
                      'summary':f'Completed daily closes show a {direction} 5/20-session trend. {reason}',
                      'research_status':'Current sources available' if evidence else 'Technical scenario only — catalyst research unavailable or stale',
                      'sources':evidence[:6],
                      'conditions':['Recheck catalysts and overnight news before the next session.',
                                    'Require fresh quotes, acceptable spreads and intraday confirmation after opening.',
                                    'Recalculate sizing from the actual entry and available risk budget.'],
                      'option_note':'Call/put direction only. Strike, expiration, premium and contract liquidity must be checked when current option quotes are available.' if 'option' in config['assets'] else None})
    return plans[:8]
