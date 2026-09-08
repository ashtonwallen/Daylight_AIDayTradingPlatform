"""Session integration for manually reported fills and optional agent reviews."""
import copy
import json
import os
import threading
import time
import uuid
from market import DataError, age, describe_et, now, stamp
from positions import parse_fill, validate_fill, holdings, review_positions, consensus
from strategy import quote_problem, quote_state


class PositionsMixin:
    def init_positions(self):
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS fills (id TEXT PRIMARY KEY, payload TEXT)')
            rows=db.execute('SELECT payload FROM fills ORDER BY rowid').fetchall()
            paused=db.execute("SELECT payload FROM settings WHERE name='ai_paused'").fetchone()
        self.manual_fills=[json.loads(r[0]) for r in rows]
        self.ai_paused=bool(json.loads(paused[0])) if paused else False
        self.parsing_fill=False
        self.fill_draft={'status':'idle'}
        self.position_reviewing=False
        self.position_review={'status':'idle'}
        self.position_last_review=0

    def set_ai_paused(self, paused):
        if type(paused) is not bool:raise ValueError('AI pause must be true or false.')
        with self.lock:
            self.ai_paused=paused
            with self.db() as db:db.execute("INSERT OR REPLACE INTO settings VALUES ('ai_paused',?)",(json.dumps(paused),))
            self.trace('AI control','paused' if paused else 'idle',
                       'All new AI requests paused. Market monitoring continues; already-sent requests may finish and be billed.' if paused else 'AI requests enabled again on the normal schedule.')

    def ai_allowed(self):
        with self.lock:return not self.ai_paused

    def quote_context(self, position, quote):
        """Classify a quote as fresh, a last-close reference, or unusable.

        Outside US market hours a stock or option quote is necessarily old. That is a known market
        state, not a data failure, so the last quote before the close stays usable for valuation and
        advisory review. It is never usable for a simulated fill.
        """
        spread = .08 if position['asset'] == 'option' else .003
        session = (self.snapshot or {}).get('market_session') or {}
        state, detail = quote_state(quote, now(), session, position['asset'], spread)
        if state == 'reference':
            detail += ' · ' + (describe_et(quote['t']) or quote['t'])
        return state, detail

    def manual_view(self):
        rows=[]
        for p in holdings(self.manual_fills).values():
            if p['quantity']<=1e-8:continue
            q=self.quote_for(p) if self.config['mode']=='live' else {}
            state,detail=self.quote_context(p,q)
            usable=state in ('fresh','reference')
            rows.append(dict(p,cost_basis=round(p['quantity']*p['average_price']*p['multiplier'],2),
                             mark=q['bp'] if usable else None,quote_at=q.get('t'),
                             market_value=round(p['quantity']*q['bp']*p['multiplier'],2) if usable else None,
                             unrealized_pnl=round((q['bp']-p['average_price'])*p['quantity']*p['multiplier'],2) if usable else None,
                             mark_state=state,data_status=detail))
        return rows

    def price_history(self, positions):
        """Recent completed daily bars so a closed-session review still has price context."""
        symbols=sorted({p['underlying'] for p in positions if p['asset']!='bitcoin'})[:8]
        if not symbols or not self.provider:return {}
        try:bars=self.provider.daily_history(symbols,now())
        except Exception:return {}
        summary={}
        for symbol,rows in bars.items():
            closes=[b['c'] for b in rows if isinstance(b.get('c'),(int,float)) and not isinstance(b.get('c'),bool)]
            if len(closes)<6:continue
            window=closes[-20:]
            summary[symbol]={'last_close':round(closes[-1],4),'completed_sessions':len(closes),
                             'last_session':rows[-1].get('t'),
                             'change_5_session_pct':round((closes[-1]/closes[-6]-1)*100,2),
                             'change_20_session_pct':round((closes[-1]/closes[-21]-1)*100,2) if len(closes)>20 else None,
                             'low_20_session':round(min(window),4),'high_20_session':round(max(window),4)}
        return summary

    def manual_summary(self):
        """Totals across logged positions: open cost, current value when every mark is fresh, realized from logged sales."""
        try:
            positions = holdings(self.manual_fills)
        except ValueError:
            return {'open': 0, 'cost_basis': 0, 'market_value': None, 'unrealized': None, 'realized': 0}
        rows = self.manual_view()
        complete = rows and all(r['market_value'] is not None for r in rows)
        return {'open': len(rows), 'cost_basis': round(sum(r['cost_basis'] for r in rows), 2),
                'market_value': round(sum(r['market_value'] for r in rows), 2) if complete else None,
                'unrealized': round(sum(r['unrealized_pnl'] for r in rows), 2) if complete else None,
                'realized': round(sum(p['realized_pnl'] for p in positions.values()), 2)}

    def request_fill_preview(self, text):
        if not isinstance(text,str) or not 5<=len(text.strip())<=2000:raise ValueError('Describe one executed fill in 5–2,000 characters.')
        with self.lock:
            if self.ai_paused:raise ValueError('AI is paused. Manual trade entry remains available.')
            if self.parsing_fill:raise ValueError('A translation is already running.')
            from ai import configured
            if not configured():raise ValueError('Configure an AI provider or enter the fill manually.')
            if not self.reserve_ai(1):raise ValueError('Daily AI request cap reached. Manual entry remains available.')
            self.parsing_fill=True
            self.fill_draft={'status':'running'}
            self.trace('Trade translator','running','Translating one reported fill into editable fields; nothing is saved yet.')
            threading.Thread(target=self.fill_preview_job,args=(text.strip(),),daemon=True).start()

    def fill_preview_job(self, text):
        try:
            if not self.ai_allowed():raise DataError('AI paused before translation. Use manual entry.')
            data=parse_fill(text)
            if data.get('unsupported') is not False:raise DataError('Enter one executed long purchase or closing sale; proposals, shorts and spreads are unsupported.')
            draft={k:data.get(k) for k in ('symbol','asset','side','quantity','price','stop','executed_at','notes')}
            with self.lock:
                self.fill_draft={'status':'ready','id':uuid.uuid4().hex,'draft':draft}
                self.trace('Trade translator','complete','Translation ready. Review the fields before saving.')
        except Exception as exc:
            with self.lock:
                self.fill_draft={'status':'error','message':str(exc) if isinstance(exc,DataError) else 'Could not translate. Use manual entry.'}
                self.trace('Trade translator','error',self.fill_draft['message'])
        finally:
            with self.lock:self.parsing_fill=False

    def save_fill(self, data, request_id):
        try:uuid.UUID(request_id)
        except (ValueError,TypeError,AttributeError):raise ValueError('A valid submission ID is required.') from None
        fill=validate_fill(data)
        with self.lock:
            if any(f['id']==request_id for f in self.manual_fills):return
            positions=holdings(self.manual_fills+[fill])
            if sum(p['quantity']>1e-8 for p in positions.values())>12:raise ValueError('Up to 12 open manually logged positions are supported.')
            fill.update(id=request_id,recorded_at=stamp(now()))
            with self.db() as db:db.execute('INSERT INTO fills VALUES (?,?)',(request_id,json.dumps(fill)))
            self.manual_fills.append(fill)
            self.position_review={'status':'idle','message':'Positions changed; request a new review.'}
            self.trace('My positions','complete',f"Recorded {fill['side']} of {fill['quantity']:g} {fill['symbol']} at ${fill['price']:g}. No brokerage order sent.")

    def set_auto_position_review(self, enabled):
        if type(enabled) is not bool:raise ValueError('Automatic review must be true or false.')
        with self.lock:
            self.config['auto_position_review']=enabled
            with self.db() as db:db.execute("INSERT OR REPLACE INTO settings VALUES ('session',?)",(json.dumps(self.config),))
            self.trace('Position review','idle','Automatic reviews enabled.' if enabled else 'Position reviews are on demand only.')

    def build_review_context(self, positions=None):
        """Everything the review agents may use. Includes daily history so a closed session is still assessable."""
        positions=self.manual_view() if positions is None else positions
        session=(self.snapshot or {}).get('market_session') or {}
        return {'at':self.last_scan,'positions':positions,'budget':self.config['budget'],
                'market':[{k:a[k] for k in ('symbol','direction','reason','metrics')} for a in self.analyses],
                'warnings':self.errors,
                'eligible_entries':[{k:i[k] for k in ('symbol','entry','stop','quantity','risk','notional')} for i in self.ideas],
                'us_session':{'status':session.get('status','unknown'),'next_open':session.get('next_open'),
                              'note':'Intraday indicators are unavailable outside US market hours. Use the supplied daily history and last-close valuations; a closed session is not itself a reason to withhold an assessment.'},
                'daily_history':self.price_history(positions),
                'long_term_research':[v for r in self.long_reports for v in r.get('views',[])],
                'evidence':[e for r in self.reports if -5<=age(r.get('at'),now())<=1200 for e in r['evidence']
                            if e['fresh'] and -60<=age(e.get('published_at'),now())<=86400]}

    def request_position_review(self, automatic=False):
        with self.lock:
            if self.ai_paused:raise ValueError('AI is paused. Resume AI to review positions.')
            if not self.running or self.config['mode']!='live' or not self.snapshot:raise ValueError('Start a live session and wait for a scan.')
            from ai import configured
            if not configured():raise ValueError('Configure an AI provider first.')
            if self.position_reviewing:raise ValueError('A position review is already running.')
            positions=self.manual_view()
            if not positions:raise ValueError('Log an open position first.')
            if not self.reserve_ai(2):raise ValueError('Daily AI request cap reached.')
            self.position_reviewing=True
            self.position_last_review=time.monotonic()
            self.position_review={'status':'running','requested_at':stamp(now())}
            context=self.build_review_context(positions)
            self.trace('Position review','running','Two independent agents are reviewing the logged positions.',{'automatic':automatic})
            threading.Thread(target=self.position_review_job,args=(self.generation,len(self.manual_fills),copy.deepcopy(context)),daemon=True).start()

    def position_review_job(self, generation, fill_count, context):
        try:
            reports=review_positions(context,progress=lambda *args:self.trace(*args,generation=generation),can_send=self.ai_allowed)
            with self.lock:
                if not self.running or generation!=self.generation:return
                if len(self.manual_fills)!=fill_count:
                    self.position_review={'status':'cancelled','message':'Positions changed; request a fresh review.'}
                    return
                positions=self.manual_view()
                missing=any(p['market_value'] is None or (p['asset']!='option' and p['stop'] is None) for p in positions)
                exposure=sum(max(p['cost_basis'],p['market_value'] or 0) for p in positions)
                planned_risk=sum((p['market_value'] or p['cost_basis']) if p['asset']=='option' else max(0,(p['mark'] or p['average_price'])-(p['stop'] or 0))*p['quantity'] for p in positions)
                available=0 if missing else max(0,self.config['budget']-exposure)
                risk_left=0 if missing else max(0,self.config['budget']*self.config['loss_limit_pct']/100-planned_risk)
                results=[consensus(p,reports,self.quote_for(p),self.ideas,available,risk_left,now(),
                                   quote_state=p['mark_state'],quote_note=p['data_status']) for p in positions]
                # Scans slow down when nothing is live, so a two-minute context window would flag
                # every closed-session review as stale. Allow for the actual scan cadence.
                context_limit=120 if (self.snapshot or {}).get('equity_open') else max(900,self.scan_interval*2)
                for result in results:
                    if age(context['at'],now())>context_limit:
                        result['action']='REVIEW'
                        result['checks'].append('Market context is stale; request a new review.')
                    if missing:result['checks'].append('Adding requires fresh valuations and planned stops for logged stock/Bitcoin positions.')
                self.position_review={'status':'complete','checked_at':stamp(now()),'results':results,'context_at':context['at']}
                self.trace('Position review','complete','Advisory review finished.',{'results':[r['symbol']+': '+r['action'] for r in results]})
        except Exception:
            with self.lock:
                if generation==self.generation:self.position_review={'status':'error','message':'Position review failed; no action suggested.'}
        finally:
            with self.lock:self.position_reviewing=False
