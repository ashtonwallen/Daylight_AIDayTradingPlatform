let lastDraft='',providerLoaded=false,fillSubmission=crypto.randomUUID();
let lastChangeId=null;
$('#pause-ai').onclick=()=>post('/api/ai/pause',{paused:!state.ai_paused}).catch(e=>toast(e.message));
$('#auto-positions').onchange=async e=>{try{await post('/api/positions/auto',{enabled:e.target.checked});}catch(err){toast(err.message);e.target.checked=state.config.auto_position_review;}};
$('#review-positions').onclick=()=>post('/api/positions/review').catch(e=>toast(e.message));
$('#translate-form').onsubmit=async e=>{e.preventDefault();$('#translate-message').textContent='';try{await post('/api/fills/preview',{text:$('#fill-text').value});}catch(err){$('#translate-message').textContent=err.message;}};
$('#fill-form').onsubmit=async e=>{
  e.preventDefault();$('#fill-error').textContent='';$('#save-fill').disabled=true;
  const fill={asset:$('#fill-asset').value,side:$('#fill-side').value,symbol:$('#fill-symbol').value.toUpperCase(),quantity:Number($('#fill-quantity').value),price:Number($('#fill-price').value),stop:$('#fill-stop').value?Number($('#fill-stop').value):null,executed_at:$('#fill-time').value||null,notes:$('#fill-notes').value};
  try{await post('/api/fills',{fill,request_id:fillSubmission});fillSubmission=crypto.randomUUID();$('#fill-form').reset();toast('Reported fill saved. No brokerage order sent.');}catch(err){$('#fill-error').textContent=err.message;}finally{$('#save-fill').disabled=false;}
};
function providerFields(){
  const p=state?.ai_settings?.providers.find(p=>p.id===$('#provider').value);
  $('#provider-model').value=p?.model||'';$('#provider-key').value='';$('#provider-clear').checked=false;
  $('#local-url-row').hidden=$('#provider').value!=='local';
  $('#provider-capability').textContent=p?.web_research?'Native web research is available only on compatible models with the required account access. Model IDs and entitlements are checked on the next AI request. Google site restrictions are requested in the prompt; OpenAI/Anthropic enforce domain filters.':'Local inference can assess collected market data. This adapter has no autonomous web search, so required research can withhold entry ideas.';
}
$('#provider').onchange=providerFields;
$('#provider-form').onsubmit=async e=>{e.preventDefault();$('#provider-message').textContent='';try{await post('/api/settings/ai',{provider:$('#provider').value,model:$('#provider-model').value,api_key:$('#provider-key').value,clear_key:$('#provider-clear').checked,local_url:$('#provider-url').value});$('#provider-key').value='';$('#provider-clear').checked=false;$('#provider-message').textContent='Saved. Resume AI when ready; credentials are validated on the next request.';}catch(err){$('#provider-message').textContent=err.message;}};
function renderExtensions(){
  if(!state)return;
  renderChanges();
  renderPlanning();
  if(!window.liveCopyApplied){
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);const replacements=[['Demo makes no API calls.','Live mode may make AI requests when research is enabled.'],['Requires live mode.','Uses the live market snapshot.'],['Synthetic prices, illustrative contracts. No real market signals.','Live data is unavailable for this category; no suggestion is issued.']];let node;while(node=walker.nextNode())for(const [a,b] of replacements)if(node.nodeValue.includes(a))node.nodeValue=node.nodeValue.replaceAll(a,b);window.liveCopyApplied=true;
  }
  const modeSelect=document.getElementById('mode');
  if(modeSelect){const label=modeSelect.closest('label'); if(label)label.remove(); modeSelect.remove();}
  setTimeout(()=>{const modeBanner=document.getElementById('mode-banner'); if(modeBanner)modeBanner.textContent=`LIVE MARKET DATA · ${state.connections.stock_feed.toUpperCase()} stocks / ${state.connections.option_feed.toUpperCase()} options / Alpaca Bitcoin`;const connection=document.getElementById('connection'); if(connection)connection.textContent='LIVE DATA';const scan=document.getElementById('next-scan'); if(scan&&state.running)scan.textContent=`Next scan ${time(state.next_scan)} · 60s cadence`;const researchState=document.getElementById('research-state'); if(researchState&&!state.researching)researchState.textContent=`${state.reports.length} live research reports`;const caption=document.getElementById('chart-caption');if(caption)caption.textContent=`Showing ${selected} · ${caption.textContent.replace(/^Showing [^·]+ · /,'')}`;},0);
  let category=document.getElementById('category-health');
  if(!category){category=document.createElement('div');category.id='category-health';category.className='category-health';const debug=document.getElementById('debug');debug.insertBefore(category,debug.querySelector('#debug-workers'));}
  category.innerHTML=Object.values(state.data_categories||{}).map(c=>`<div class="category-item ${esc(c.status)}"><strong>${esc(c.label)}</strong><span>${esc(c.status)}</span><small>${esc(c.detail)}</small></div>`).join('');
  const connections=document.querySelector('.connections');
  if(connections){const feedRows=document.getElementById('connection-feeds');const markup=Object.values(state.data_categories||{}).map(c=>{const meaning=c.status==='available'?'Green: usable data received':c.status==='waiting'?'Yellow: waiting, paused or not currently applicable':'Red: unavailable or failed freshness checks';return `<div class="connection-feed"><span>${esc(c.label)}</span><button type="button" class="feed-light ${esc(c.status)}" aria-label="${esc(c.label+': '+meaning)}" data-help="${esc(meaning+'. '+c.detail)}"></button></div>`;}).join('');if(feedRows.dataset.markup!==markup){feedRows.innerHTML=markup;feedRows.dataset.markup=markup;feedRows.querySelectorAll('[data-help]').forEach(b=>{b.onmouseenter=()=>openHelp(b);b.onmouseleave=()=>{if(document.activeElement!==b)closeHelp();};b.onfocus=()=>openHelp(b);b.onblur=closeHelp;b.onclick=()=>openHelp(b);});}}
  const filterLabel=[...document.querySelectorAll('.section-title span')].find(x=>x.textContent.trim()==='The filter matters');
  if(filterLabel)filterLabel.remove();
  let discovery=document.getElementById('discovery');
  if(!discovery){discovery=document.createElement('section');discovery.id='discovery';discovery.className='discovery-section';document.getElementById('overview').append(discovery);}
  discovery.innerHTML='<div class="section-title"><h2>Beyond your watchlist</h2><span class="muted">Up to 6 research leads</span></div><p class="hint">News and public social searches can surface new names. Leads need fresh quotes, price confirmation and company-specific news before they become trade ideas. Public search does not measure Reddit traction.</p>'+(state.discoveries?.length?state.discoveries.map(d=>`<article class="evidence"><div class="section-title"><h3>${esc(d.symbol)}</h3><button type="button" class="text-button" data-discovery-chart="${esc(d.symbol)}">View chart</button></div><p>${esc(d.reason)}</p><p class="hint">${esc(d.status)}</p>${d.sources.map(e=>`<p class="hint"><a href="${url(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a> · ${esc(e.lane)} · ${esc(new Date(e.published_at).toLocaleString())}</p>`).join('')}</article>`).join(''):'<div class="empty"><h3>No current discovery leads</h3><p>Leads appear after a sourced research cycle. Enable AI web research to search beyond your watchlist.</p></div>');
  discovery.querySelectorAll('[data-discovery-chart]').forEach(b=>b.onclick=()=>{selected=b.dataset.discoveryChart;$('#chart-symbol-input').value=selected;drawChart();document.querySelector('.chart-panel').scrollIntoView({behavior:'smooth',block:'center'});});
  const chartHeading=document.querySelector('.chart-panel .panel-heading');
  if(chartHeading&&!document.getElementById('chart-symbol-input')){
    const picker=document.createElement('div'); picker.className='chart-picker';
    picker.innerHTML='<label for="chart-symbol-input" class="sr-only">Chart ticker</label><input id="chart-symbol-input" maxlength="12" placeholder="Ticker"><button id="chart-symbol-go" type="button" class="compact">Show</button><div id="chart-quick-picks" class="chart-quick-picks"></div>';
    chartHeading.insertBefore(picker, chartHeading.querySelector('#chart-symbol'));
    const select=chartHeading.querySelector('#chart-symbol'); if(select)select.hidden=true;
    const input=picker.querySelector('input'); input.value=selected;
    picker.querySelector('button').onclick=()=>selectChart(input.value);
    input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();picker.querySelector('button').click();}};
  }
  const picks=document.getElementById('chart-quick-picks');
  if(picks){const symbols=[...new Set([...(state.config.symbols||[]),'BTC/USD'])];picks.innerHTML=symbols.map(s=>`<button type="button" class="chart-pick ${s===selected?'active':''}" data-chart-pick="${esc(s)}">${esc(s)}</button>`).join('');picks.querySelectorAll('[data-chart-pick]').forEach(b=>b.onclick=()=>selectChart(b.dataset.chartPick));}
  const caption=document.getElementById('chart-caption'); if(caption)caption.dataset.chartLabel='Showing '+selected;
  let outlook=document.getElementById('long-term-outlook');
  if(!outlook){
    outlook=document.createElement('section'); outlook.id='long-term-outlook'; outlook.className='panel long-term-outlook';
    outlook.innerHTML='<div class="section-title"><h2>Long-term outlook</h2><span class="muted">6–18 month research</span></div><p class="hint">Ask about a longer-term thesis, such as Microsoft LEAPS over the next year. This uses a separate research request and returns an evidence-based outlook, not an order or guarantee.</p><form id="outlook-form"><textarea id="outlook-text" rows="3" maxlength="2000" placeholder="Could Microsoft LEAPS make sense over the next year given valuation, macro conditions and catalysts?"></textarea><button class="secondary" type="submit">Research longer-term outlook · 1 AI request ↗</button><p id="outlook-message" class="hint"></p></form><div id="outlook-result"></div>';
    document.getElementById('research').append(outlook);
    document.getElementById('outlook-form').onsubmit=async e=>{e.preventDefault();const m=$('#outlook-message');m.textContent='';try{await post('/api/outlook',{proposal:$('#outlook-text').value});}catch(err){m.textContent=err.message;}};
  }
  const o=state.outlook||{status:'idle'}, result=$('#outlook-result');
  if(o.status==='running')result.innerHTML='<div class="alert">Researching the longer-term question across current macro, company and catalyst sources.</div>';
  else if(o.status==='error')result.innerHTML=`<div class="alert">${esc(o.message)}</div>`;
  else if(o.status==='complete'){const list=(xs,empty)=>`<ul>${xs.length?xs.map(x=>`<li>${esc(x)}</li>`).join(''):`<li>${empty}</li>`}</ul>`;result.innerHTML=`<article class="outlook-result"><div class="review-verdict ${o.outlook==='bullish'?'verdict-yes':o.outlook==='bearish'?'verdict-no':''}">${esc((o.outlook||'no-conclusion').toUpperCase())}<span>${esc(o.horizon||'Long-term view')} · ${time(o.checked_at)}</span></div><p>${esc(o.summary||'No conclusion provided.')}</p><div class="review-columns"><div><h3>Thesis</h3>${list(o.thesis||[],'No thesis factors returned.')}</div><div><h3>Risks</h3>${list(o.risks||[],'No risks returned.')}</div></div>${(o.instruments||[]).length?`<h3>Instruments to investigate</h3>${list(o.instruments,'No instruments suggested.')}`:''}</article>`;}
  else result.innerHTML='';
  $('#outlook-form button').disabled=state.ai_paused||o.status==='running'||!state.running;
  const sourceLinks=sources=>sources.map(s=>`<p class="hint"><a href="${url(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a> · ${esc(s.published_at||'Publication date unverified')}</p>`).join('');
  if(o.status==='complete')result.insertAdjacentHTML('beforeend',`<h3>What would change this view</h3><ul>${(o.invalidation||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h3>Sources</h3>${sourceLinks(o.sources||[])}<p class="hint">Assessed ${esc(new Date(o.checked_at).toLocaleString())}. Source dates and interpretations require review. This is a thesis assessment, not a current entry signal.</p>`);
  let history=$('#outlook-history');if(!history){history=document.createElement('details');history.id='outlook-history';outlook.append(history);}
  history.innerHTML='<summary>Saved outlooks</summary>'+(state.outlook_history||[]).map(h=>`<article class="evidence"><h3>${esc(h.question)}</h3><p class="hint">${esc(new Date(h.checked_at).toLocaleString())} · ${esc(h.outlook)} · historical assessment</p><p>${esc(h.summary)}</p>${sourceLinks(h.sources||[])}</article>`).join('');
  $('#pause-ai').textContent=state.ai_paused?'Resume AI':'Pause all AI';
  $('#pause-ai').classList.toggle('paused',!!state.ai_paused);
  $('#ai-pause-note').hidden=!state.ai_paused;
  $('#ai-pause-note').textContent='AI paused · no new AI requests. Market scans and manual logging continue.'+(state.ai_inflight?' Already-sent requests are still finishing and may be billed.':'');
  $('#auto-positions').checked=!!state.config.auto_position_review;
  $('#review-positions').disabled=state.ai_paused||state.position_review?.status==='running';
  $('#translate-fill').disabled=state.ai_paused||state.fill_draft?.status==='running';
  $('#check-idea').disabled=state.ai_paused||state.thesis_report?.status==='running';
  const draft=state.fill_draft||{status:'idle'};
  if(draft.status==='running')$('#translate-message').textContent='Translating. The fields below remain editable for manual entry.';
  else if(draft.status==='error')$('#translate-message').textContent=draft.message;
  else if(draft.status==='ready'&&draft.id!==lastDraft){
    lastDraft=draft.id;fillSubmission=crypto.randomUUID();
    for(const [key,id] of Object.entries({symbol:'symbol',asset:'asset',side:'side',quantity:'quantity',price:'price',stop:'stop',executed_at:'time',notes:'notes'}))$('#fill-'+id).value=draft.draft[key]??'';
    $('#translate-message').textContent='Review the translated fields and fill in anything missing before confirming.';
  }
  const positions=state.manual_positions||[];
  $('#manual-positions').innerHTML=positions.length?positions.map(p=>`<article class="trade"><div class="trade-head"><strong>${esc(p.symbol)}</strong><span class="pill">USER-REPORTED · ${esc(p.asset)}</span></div><p>${p.quantity.toLocaleString('en-US',{maximumFractionDigits:8})} units · average fill ${money(p.average_price)}${p.asset==='option'?' × 100 shares/contract':''}</p><p class="hint">Cost basis ${money(p.cost_basis)} · ${p.mark===null?'Fresh valuation unavailable':'mark '+money(p.mark)+' · unrealized '+money(p.unrealized_pnl)}<br>Planned stop ${p.stop?money(p.stop):'not logged'} · ${esc(p.data_status)}</p></article>`).join(''):'<div class="empty"><h3>Your real trades, in one place.</h3><p>Translate a fill or enter its fields manually.<br>Nothing here places an order.</p></div>';
  const review=state.position_review||{status:'idle'};
  if(review.status==='running')$('#position-review').innerHTML='<div class="alert">Position analyst and risk reviewer are assessing your positions. Follow them in Debug.</div>';
  else if(review.status==='complete')$('#position-review').innerHTML=review.results.map(r=>{
    const stale=!state.running||Date.now()-Date.parse(r.checked_at)>120000||Date.now()-Date.parse(r.quote_at)>45000;
    return `<article class="panel review-result"><div class="review-verdict ${r.action==='HOLD'?'verdict-yes':'verdict-no'}">${stale?'RECHECK':esc(r.action)}<span>${esc(r.symbol)} · ${stale?'assessment or quote is stale':r.agreement?'agents agree':'no consensus'}</span></div><p class="hint">${time(r.checked_at)} · advisory only; verify in your broker before acting.</p>${r.checks.map(c=>`<p class="hint">${esc(c)}</p>`).join('')}<div class="review-columns">${r.votes.map(v=>`<div><h3>${esc(v.agent)} · ${esc(v.action)}</h3><p class="hint">${esc(v.summary)}</p><ul>${v.factors.map(f=>`<li>${esc(f)}</li>`).join('')}</ul></div>`).join('')}</div></article>`;
  }).join('');
  else $('#position-review').innerHTML=review.message?`<p class="hint">${esc(review.message)}</p>`:'';
  $('#fill-history').innerHTML=(state.manual_fills||[]).map(f=>`<div class="reject"><strong>${esc(f.side.toUpperCase())} ${esc(f.symbol)}</strong><span>${f.quantity} at ${money(f.price)} · ${f.executed_at?esc(f.executed_at):'fill time not provided'}<br>Recorded ${esc(new Date(f.recorded_at).toLocaleString())}</span></div>`).join('');
  const settings=state.ai_settings;
  if(settings){
    $('#provider-current').textContent=settings.label+' · '+settings.model;
    if(!providerLoaded){$('#provider').value=settings.provider;$('#provider-url').value=settings.local_url;providerFields();providerLoaded=true;}
    $('#save-provider').disabled=!state.ai_paused||state.ai_inflight;
  }
}
if(state)renderExtensions();
// Plain-language help: focus/hover to read, tap to toggle, Escape to dismiss.
const helpCopy = {
  risk: 'Maximum planned loss for one idea, as a percentage of your entered budget. Example: 1% of $10,000 is $100. Stocks and Bitcoin use entry-to-stop distance to size the trade; long options reserve the entire premium. Actual losses can exceed a stop estimate.',
  loss: 'Total paper risk allowance: this percentage of your budget, minus today\'s realized paper losses and risk reserved for open paper positions. At 3% of $10,000, it starts at $300. The day resets at midnight UTC. This does not automatically close positions.',
  budget: 'The amount you choose to allocate to this workspace. Used for suggestion sizing; it is not your broker balance or verified buying power.',
  mode: 'Live mode requests current Alpaca data. Individual feeds can be unavailable because of market hours, permissions, subscriptions, or provider errors.',
  symbols: 'Stock tickers to monitor. For options, an underlying is the stock the contract is based on. Option discovery scans the first three tickers; Bitcoin is selected separately.',
  'research-interval': 'How often the three web-research agents run while the session is active. Market scans run separately, about once per minute. Optional automatic position reviews use this cadence too.',
  'research-calls': 'Maximum background web-research requests per session. Each cycle uses three requests, so 36 allows 12 cycles. Other AI features have separate limits and all share the 120-request UTC-day cap. Requests are not tokens or dollars; failures count.',
  'fill-symbol': 'Stocks use a ticker such as AAPL; Bitcoin uses BTC/USD. Options require the full contract identifier, including underlying, expiration, call/put and strike, as shown by your broker.',
  'fill-quantity': 'Number of shares, Bitcoin units, or option contracts you actually traded. Options must be whole contracts; one standard contract represents 100 shares.',
  'fill-price': 'Actual execution price for one share or one Bitcoin. For options, enter premium per share: a $2.50 fill costs $250 per standard contract.',
  'fill-stop': 'Your planned exit price if the trade goes against you. This is only a planning reference; saving it does not place a stop order.',
  'provider-model': 'The exact API model identifier from the selected provider, or the loaded model ID in LM Studio. A product subscription name is not a model ID.',
};
const helpPopup = document.createElement('div');
helpPopup.id = 'help-popup'; helpPopup.className = 'help-popup'; helpPopup.role = 'tooltip'; helpPopup.hidden = true;
document.body.append(helpPopup);
let helpOwner = null;
function closeHelp() {
  helpOwner?.removeAttribute('aria-describedby'); helpOwner = null; helpPopup.hidden = true;
}
function openHelp(button) {
  closeHelp(); helpOwner = button; button.setAttribute('aria-describedby', helpPopup.id);
  helpPopup.textContent = button.dataset.help; helpPopup.hidden = false;
  const box = button.getBoundingClientRect();
  helpPopup.style.left = Math.max(8, Math.min(box.left, innerWidth - helpPopup.offsetWidth - 8)) + 'px';
  helpPopup.style.top = Math.max(8, box.bottom + helpPopup.offsetHeight + 10 < innerHeight ? box.bottom + 8 : box.top - helpPopup.offsetHeight - 8) + 'px';
}
function addHelp(target, text, name) {
  if (!target || target.querySelector('.help-button')) return;
  const button = document.createElement('button'); button.type = 'button'; button.className = 'help-button';
  button.textContent = '?'; button.dataset.help = text; button.setAttribute('aria-label', 'Help: ' + name);
  button.onmouseenter = () => openHelp(button);
  button.onmouseleave = () => { if (document.activeElement !== button && !helpPopup.matches(':hover')) closeHelp(); };
  button.onfocus = () => openHelp(button); button.onblur = closeHelp;
  // Prevent the containing label from toggling its input when help is tapped.
  button.onpointerdown = e => { button.wasOpen = e.pointerType === 'touch' && helpOwner === button && document.activeElement === button; };
  button.onclick = e => { e.preventDefault(); button.wasOpen ? closeHelp() : openHelp(button); button.wasOpen = false; };
  target.append(button);
}
Object.entries(helpCopy).forEach(([id, text]) => addHelp(document.querySelector(`label[for="${id}"]`), text, document.querySelector(`label[for="${id}"]`)?.textContent.trim()));
[
  ['available', 'Paper capital left after reserving open paper positions and subtracting today\'s paper losses. Reported actual trades are a separate ledger; verify your broker buying power.'],
  ['risk-left', helpCopy.loss],
  ['calls', helpCopy['research-calls']],
].forEach(([id, text]) => addHelp(document.getElementById(id)?.previousElementSibling, text, document.getElementById(id)?.previousElementSibling?.textContent));
const metricHelp = [
  'Volume-weighted average price over the last 25 one-minute bars, using each bar\'s average high, low and close. This is a rolling reference, not a full-session VWAP.',
  'Average True Range over 14 bars: a measure of recent price movement in dollars, including gaps. Higher values mean larger recent moves, not a predicted direction.',
  'Latest completed minute\'s volume divided by the average of the previous 20 minutes. 2× means twice that recent average; this is not a comparison with the same time on previous days.',
  'Percentage price change over five minutes. A recent move does not predict the next move.',
];
new MutationObserver(() => {
  document.querySelectorAll('#metrics > span').forEach((node, i) => addHelp(node, metricHelp[i], node.firstChild.textContent.trim()));
}).observe(document.getElementById('metrics'), {childList:true});
helpPopup.onmouseleave = () => { if (document.activeElement !== helpOwner) closeHelp(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHelp(); });
document.addEventListener('click', e => { if (!e.target.closest('.help-button, .help-popup, .feed-light')) closeHelp(); });
window.addEventListener('resize', closeHelp);
document.addEventListener('scroll', closeHelp, true);

const agentsObserver=new MutationObserver(()=>document.querySelectorAll('#agents .agent').forEach((card,i)=>{
  const report=state?.reports?.[i];
  if(report&&!card.querySelector('.agent-conclusion')){
    const p=document.createElement('p'); p.className='agent-conclusion'; p.innerHTML='<strong>Conclusion</strong> '+esc(report.conclusion||'No conclusion: this lane did not support a defensible synthesis.'); card.append(p);
  }
}));
agentsObserver.observe(document.getElementById('agents'),{childList:true,subtree:true});

function alertPreference(){try{return localStorage.getItem('daylight-alerts')!=='off';}catch{return true;}}
function renderThesisReview(){
  const r=state.thesis_report;
  $('#review-count').textContent='Open-ended research · 1 AI request';
  $('#check-idea').textContent=r.status==='running'?'Researching thesis…':'Research my thesis';
  $('#check-idea').disabled=state.ai_paused||r.status==='running';
  $('#review-history').hidden=true;
  let history=$('#thesis-history');if(!history){history=document.createElement('div');history.id='thesis-history';$('#review-history').after(history);}
  history.innerHTML=(state.outlook_history||[]).filter(h=>h.kind==='thesis').map(h=>`<article class="evidence"><h3>${esc(h.question)}</h3><p>${esc(h.verdict.replaceAll('-',' '))} · ${esc(h.summary)}</p><p class="hint">${esc(new Date(h.checked_at).toLocaleString())} · historical assessment</p></article>`).join('')||'<p class="hint">Completed thesis assessments are saved locally.</p>';
  const result=$('#idea-result');
  if(r.status==='idle'){result.innerHTML='<div class="empty"><h3>Research a trade thesis</h3><p>Any ticker and timeframe. Include direction, holding period and optional budget or contract details.</p></div>';return;}
  if(r.status==='running'){result.innerHTML='<div class="alert">Researching price context, dated catalysts and risks for your proposed trade. Specific contracts will be checked against market data where available.</div>';return;}
  if(r.status!=='complete'){result.innerHTML=`<div class="alert">${esc(r.message||'No assessment available.')}</div>`;return;}
  const list=xs=>`<ul>${(xs||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
  const trade=r.specific_trade||{};
  let concrete=`<p class="hint">${esc(trade.reason||'No specific trade verified.')}</p>`;
  if(trade.status==='candidate'){
    const label=trade.instrument==='stock'?`Buy ${trade.symbol} shares`:`${trade.underlying} ${trade.expiration} $${trade.strike} ${trade.instrument==='call'?'Call':'Put'}`;
    concrete=`<h3>${esc(label)}</h3><p class="hint">${esc(trade.symbol)} · ${esc(new Date(trade.quote_at).toLocaleString())}${Date.now()-Date.parse(trade.quote_at)>45000?' · QUOTE STALE — recheck before trading':''}</p><p>${trade.cost_per_contract?`Ask premium ${money(trade.premium_per_share)} per share · ${money(trade.cost_per_contract)} per contract`:`Ask reference ${money(trade.entry_reference)} per share`}</p><p class="hint">${esc(trade.reason)} ${esc(trade.risk||'Position size must be assessed separately.')}</p>`;
  }
  result.innerHTML=`<article class="panel review-result"><div class="review-verdict">${esc(r.verdict.replaceAll('-',' ').toUpperCase())}<span>${esc(r.horizon)}</span></div><p>${esc(r.summary)}</p><p class="hint">Interpreted as: ${esc(r.interpretation)}</p><div class="review-columns"><div><h3>Supporting research</h3>${list(r.thesis)}<h3>What would change this view</h3>${list(r.invalidation)}</div><div><h3>Risks</h3>${list(r.risks)}<h3>Missing information</h3>${list(r.missing)}</div></div><h3>Specific trade candidate</h3>${concrete}<h3>Research sources</h3>${(r.sources||[]).map(s=>`<p class="hint"><a href="${url(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a> · ${esc(s.published_at||'Date unverified')}</p>`).join('')}<p class="hint">Assessed ${esc(new Date(r.checked_at).toLocaleString())}. Verdict assesses the thesis, not a probability of profit or approval to execute.</p></article>`;
}
function renderPlanning(){
  let panel=$('#next-session-plans');
  if(!panel){panel=document.createElement('section');panel.id='next-session-plans';panel.className='panel scan-changes';panel.innerHTML='<h2>Next-session ideas</h2><label class="check"><input id="closed-market-ideas" type="checkbox"> Generate stock and option plans while the market is closed</label><p class="hint">Uses completed daily prices and available catalyst research. These are conditional plans, not live entries. Adds historical market-data requests; no extra AI requests.</p><div id="planning-results"></div>';$('#overview').append(panel);$('#closed-market-ideas').onchange=e=>post('/api/planning',{enabled:e.target.checked}).catch(err=>toast(err.message));}
  $('#closed-market-ideas').checked=!!state.config.closed_market_ideas;
  const plans=state.next_session_ideas||[];
  $('#planning-results').innerHTML=!state.config.closed_market_ideas?'':plans.length?plans.map(p=>`<article class="evidence"><h3>${esc(p.symbol)} · ${esc(p.instruments.join(' / '))}</h3><span class="pill">NEXT-SESSION PLAN · RECHECK AT OPEN</span><p>${esc(p.summary)}</p><p class="hint">${esc(p.research_status)}<br>Last completed close: ${money(p.reference_close)} · ${esc(new Date(p.reference_at).toLocaleDateString())}<br>5-session change: ${p.five_session_change}%</p><ul>${p.conditions.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>${p.option_note?`<p class="hint">${esc(p.option_note)}</p>`:''}${p.sources.map(s=>`<p class="hint"><a href="${url(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></p>`).join('')}</article>`).join(''):'<p class="hint">No next-session plans yet. Start monitoring and wait for a scan while the US stock market is closed. Plans need at least 20 completed daily bars with a clear trend; conflicting current research can withhold them.</p>';
}
function desktopPreference(){try{return localStorage.getItem('daylight-desktop-alerts')==='on';}catch{return false;}}
function renderChanges(){
  let panel=document.getElementById('scan-changes');
  if(!panel){
    panel=document.createElement('section');panel.id='scan-changes';panel.className='panel scan-changes';
    panel.innerHTML='<div class="section-title"><h2>What changed?</h2><span class="muted">No extra AI requests</span></div><div class="alert-controls"><label class="check"><input id="opportunity-alerts" type="checkbox"> Opportunity alerts</label><button id="desktop-alerts" type="button" class="text-button">Enable desktop alerts</button></div><p class="hint">New qualifying ideas trigger an alert, with a 10-minute cooldown per symbol and direction. Desktop alerts require this page to remain open. History below describes scan changes, not current trade instructions.</p><details id="change-history"><summary>Recent scan changes</summary><div id="change-events"></div></details><p id="change-summary" class="hint" role="status"></p>';
    document.getElementById('overview').append(panel);
    $('#opportunity-alerts').checked=alertPreference();
    $('#opportunity-alerts').onchange=e=>{try{localStorage.setItem('daylight-alerts',e.target.checked?'on':'off');}catch{}};
    $('#desktop-alerts').onclick=async()=>{
      if(!('Notification' in window)){toast('Desktop notifications are not supported by this browser.');return;}
      if(desktopPreference()){try{localStorage.setItem('daylight-desktop-alerts','off');}catch{}renderChanges();return;}
      try{const permission=await Notification.requestPermission();if(permission==='granted'){localStorage.setItem('daylight-desktop-alerts','on');}else toast('Desktop alerts were not enabled. In-page alerts still work.');}catch{toast('Desktop alerts could not be enabled.');}
      renderChanges();
    };
  }
  $('#desktop-alerts').textContent=desktopPreference()?'Disable desktop alerts':'Enable desktop alerts';
  const events=state.changes||[],latest=events.at(-1)?.id||0;
  const pending=lastChangeId===null?[]:events.filter(e=>e.id>lastChangeId);
  lastChangeId=latest;
  const active=pending.filter(e=>e.notify&&Date.now()-Date.parse(e.at)<60000&&state.running&&state.ideas.some(i=>i.symbol===e.symbol&&i.direction===e.direction&&Date.parse(i.expires_at)>Date.now()&&Date.now()-Date.parse(i.quote.t)<45000));
  if(alertPreference()&&active.length){
    const message=active.map(e=>`${e.symbol}: ${e.direction}`).join(' · ');
    toast('New qualifying ideas: '+message+'. Review current prices on Market desk.');
    if(desktopPreference()&&'Notification' in window&&Notification.permission==='granted'){
      try{const notification=new Notification('Daylight: new qualifying ideas',{body:message,tag:'daylight-opportunities'});notification.onclick=()=>{window.focus();notification.close();document.querySelector('[data-tab="overview"]').click();};setTimeout(()=>notification.close(),30000);}catch{}
    }
  }
  const list=$('#change-events');
  // Rebuild only on a new event so keyboard focus and reading position survive polling.
  if(list.dataset.latest!==String(latest)){
    list.dataset.latest=String(latest);
    list.innerHTML=[...events].reverse().slice(0,50).map(e=>`<article class="scan-change"><div><strong>${esc(e.symbol)} · ${esc(e.direction)}</strong><span class="muted">${esc(e.kind)} · ${time(e.at)}</span></div><p>${esc(e.message)}</p></article>`).join('')||'<p class="hint">Changes will appear after market scans produce or withdraw ideas.</p>';
  }
  $('#change-summary').textContent=events.length?`Latest: ${events.at(-1).symbol} · ${events.at(-1).kind} · ${time(events.at(-1).at)}`:'No idea changes recorded yet.';
}
