/* Daylight dashboard. One file, organized top to bottom:
   utilities → theme → API → session form → tabs → header/stats → market desk → idea check →
   research → journal → positions → activity/debug → AI settings → help popups → polling.
   Rendering is idempotent: every section is rebuilt from /api/state, but a container's HTML is only
   replaced when it actually changed, so text selection, open <details> and focus survive polling. */

// ---------- Utilities ----------
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = n => Number(n || 0).toLocaleString('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2});
const signedMoney = n => (Number(n) > 0 ? '+' : '') + money(n);
const time = s => s ? new Date(s).toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
const dateTime = s => s && !isNaN(Date.parse(s)) ? new Date(s).toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : null;
const url = s => { try { const u = new URL(s); return ['https:', 'http:'].includes(u.protocol) ? esc(u.href) : '#'; } catch { return '#'; } };
const pnlClass = n => n > 0 ? 'positive' : n < 0 ? 'negative' : '';
function etTime(iso, withDay) {
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const et = x => x.toLocaleDateString('en-US', {timeZone:'America/New_York'});
  const opts = {timeZone:'America/New_York', hour:'numeric', minute:'2-digit'};
  if (withDay || et(d) !== et(new Date())) opts.weekday = 'short';
  return d.toLocaleString('en-US', opts) + ' ET';
}
function relative(iso) {
  const seconds = Math.round((Date.parse(iso) - Date.now()) / 1000);
  if (isNaN(seconds)) return '';
  const abs = Math.abs(seconds), unit = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.floor(abs/60)}m ${abs%60}s` : `${Math.floor(abs/3600)}h ${Math.floor(abs%3600/60)}m`;
  return seconds >= 0 ? `in ${unit}` : `${unit} ago`;
}
const interval = s => s % 60 === 0 && s >= 120 ? `every ${s/60} min` : `every ${s}s`;
const store = {
  get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} },
};
const lastHTML = new WeakMap();
function patch(el, html) {
  if (!el || lastHTML.get(el) === html) return false;
  lastHTML.set(el, html);
  el.innerHTML = html;
  return true;
}
function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
function toast(message) {
  const item = document.createElement('div');
  item.className = 'toast-item';
  item.innerHTML = `<span>${esc(message)}</span><button type="button" aria-label="Dismiss">×</button>`;
  item.querySelector('button').onclick = () => item.remove();
  const stack = $('#toast');
  stack.append(item);
  while (stack.children.length > 3) stack.firstChild.remove();
  setTimeout(() => item.remove(), 7000);
}
const list = (items, empty) => `<ul>${(items || []).length ? items.map(x => `<li>${esc(x)}</li>`).join('') : `<li>${esc(empty)}</li>`}</ul>`;
// Gemini returns opaque signed redirect links instead of publisher URLs, so say so rather than
// presenting "vertexaisearch.cloud.google.com" as if it were the source.
const isRedirect = link => typeof link === 'string' && link.includes('vertexaisearch.cloud.google.com/grounding-api-redirect/');
const sourceHost = link => { try { return new URL(link).hostname.replace(/^www\./, ''); } catch { return ''; } };
const sourceLabel = s => isRedirect(s.url) ? 'Source via Google Search · opens the publisher' : (s.title || sourceHost(s.url) || 'Source');
const sourceLinks = sources => (sources || []).map(s => `<p class="hint"><a href="${url(s.url)}" target="_blank" rel="noopener noreferrer">${esc(sourceLabel(s))}</a> · ${esc(dateTime(s.published_at) || 'Publication date unverified')}${isRedirect(s.url) ? '' : sourceHost(s.url) && s.title ? ' · ' + esc(sourceHost(s.url)) : ''}</p>`).join('');

// ---------- Theme (browser preference only; never triggers requests) ----------
// A theme is a palette; a mode is light or dark within it. Both are browser preferences and never
// reach the server. Older installs stored 'light'/'dark' under daylight-theme, so migrate those.
const THEMES = ['daylight', 'slate', 'ember', 'mono'];
function applyTheme(theme, mode) {
  const root = document.documentElement;
  root.dataset.theme = THEMES.includes(theme) ? theme : 'daylight';
  root.dataset.mode = mode === 'light' ? 'light' : 'dark';
  const light = root.dataset.mode === 'light';
  const button = $('#theme-toggle');
  button.textContent = light ? 'Dark mode' : 'Light mode';
  button.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
  $('#theme-select').value = root.dataset.theme;
}
const storedTheme = store.get('daylight-theme', 'daylight');
applyTheme(THEMES.includes(storedTheme) ? storedTheme : 'daylight',
           store.get('daylight-mode', storedTheme === 'light' ? 'light' : 'dark'));
$('#theme-toggle').onclick = () => {
  const mode = document.documentElement.dataset.mode === 'light' ? 'dark' : 'light';
  applyTheme(document.documentElement.dataset.theme, mode);
  store.set('daylight-mode', mode);
  store.set('daylight-theme', document.documentElement.dataset.theme);
};
$('#theme-select').onchange = e => {
  applyTheme(e.target.value, document.documentElement.dataset.mode);
  store.set('daylight-theme', e.target.value);
};

// ---------- API ----------
let state = null, csrf = null, busy = false, connected = false, firstRender = true;
async function post(path, data = {}) {
  const res = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify(data)});
  const result = await res.json();
  if (!res.ok) throw Error(result.error || 'Request failed');
  await refresh();
  return result;
}
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const res = await fetch('/api/state');
    if (!res.ok) throw Error('Server unavailable');
    state = await res.json();
    csrf = state.csrf;
    connected = true;
    render();
  } catch {
    connected = false;
    setText($('#status'), 'Server disconnected');
    $('#status-dot').classList.remove('live');
    $$('[data-open],[data-close]').forEach(b => b.disabled = true);
  } finally { busy = false; }
}

// ---------- Session form ----------
function readConfig() {
  return {mode:'live', budget:Number($('#budget').value), risk_pct:Number($('#risk').value), loss_limit_pct:Number($('#loss').value),
    max_position_pct:Number($('#max-position').value),
    assets:$$('[name=asset]:checked').map(x => x.value), symbols:$('#symbols').value.toUpperCase().split(/[\s,]+/).filter(Boolean),
    research:$('#research').checked, require_research:$('#require-research').checked,
    research_interval:Number($('#research-interval').value), research_calls:Number($('#research-calls').value),
    daily_ai_limit:Number($('#daily-ai-limit').value),
    auto_position_review:state?.config?.auto_position_review || false, closed_market_ideas:state?.config?.closed_market_ideas || false,
    long_term_research:state?.config?.long_term_research || false,
    long_risk:state?.config?.long_risk || 'balanced', long_months:state?.config?.long_months || 6, long_focus:state?.config?.long_focus || ''};
}
function fillConfig(c) {
  $('#budget').value = c.budget; $('#risk').value = c.risk_pct; $('#loss').value = c.loss_limit_pct; $('#symbols').value = c.symbols.join(', ');
  $('#max-position').value = c.max_position_pct ?? 25;
  $$('[name=asset]').forEach(x => x.checked = c.assets.includes(x.value));
  $('#research').checked = c.research; $('#require-research').checked = c.require_research;
  $('#research-interval').value = c.research_interval; $('#research-calls').value = c.research_calls;
  $('#daily-ai-limit').value = c.daily_ai_limit ?? 120;
  budgetPreview();
  symbolsCount(); limitsPreview();
}
// The three limits interact, and the binding one is rarely obvious. Show them in dollars.
function limitsPreview() {
  const budget = Number($('#budget').value) || 0, risk = Number($('#risk').value) || 0;
  const loss = Number($('#loss').value) || 0, position = Number($('#max-position').value) || 0;
  const perIdea = budget * risk / 100, perDay = budget * loss / 100, maxPosition = budget * position / 100;
  let note = '';
  if (loss < risk) note = 'Loss allowance must be at least the risk per idea.';
  else if ($('[name=asset][value=option]').checked && perIdea < 100)
    note = `A long option reserves its whole premium, so ${money(perIdea)} per idea only funds contracts under ${money(perIdea / 100)} per share. Raise Risk / idea % if option ideas never appear.`;
  else if (maxPosition < perIdea) note = 'Max position size is below your per-idea risk, so it is the limit that will bind first.';
  patch($('#limits-preview'), `<div><span>Most one idea may lose</span><b>${money(perIdea)}</b></div>
    <div><span>Most all ideas may lose today</span><b>${money(perDay)}</b></div>
    <div><span>Largest single position</span><b>${money(maxPosition)}</b></div>
    ${note ? `<span class="note">${esc(note)}</span>` : ''}`);
}
// Research spend is easy to misjudge: 3 requests per cycle across a 6.5-hour session adds up fast.
function budgetPreview() {
  const interval = Number($('#research-interval').value) || 600;
  const sessionCap = Number($('#research-calls').value) || 0;
  const dailyCap = Number($('#daily-ai-limit').value) || 0;
  const used = state?.ai_usage?.requests || 0;
  const cyclesPerDay = Math.floor(6.5 * 3600 / interval);
  const wanted = cyclesPerDay * 3;
  const hours = (Math.floor(sessionCap / 3) * interval / 3600);
  const remaining = Math.max(0, dailyCap - used);
  let note = '';
  if (sessionCap < wanted) note = `The session cap stops research after about ${hours.toFixed(1)}h. Raise it to ${wanted} to cover a full session.`;
  if (remaining < wanted) note = `Only ${remaining} requests are left today, but a full session at this cadence wants ${wanted}. Slow the cadence or raise the daily cap.`;
  patch($('#budget-preview'), `<div><span>Full session at this cadence</span><b>${wanted} requests</b></div>
    <div><span>Session cap covers</span><b>${hours.toFixed(1)} hours</b></div>
    <div><span>Left today</span><b>${remaining} of ${dailyCap}</b></div>
    ${note ? `<span class="note">${esc(note)}</span>` : ''}`);
}
['#research-interval', '#research-calls', '#daily-ai-limit'].forEach(id => $(id).addEventListener('input', budgetPreview));
$('#research-interval').addEventListener('change', budgetPreview);
['#budget', '#risk', '#loss', '#max-position'].forEach(id => $(id).addEventListener('input', limitsPreview));
$$('[name=asset]').forEach(x => x.addEventListener('change', limitsPreview));
function symbolsCount() {
  const symbols = [...new Set($('#symbols').value.toUpperCase().split(/[\s,]+/).filter(Boolean))];
  const options = $('[name=asset][value=option]').checked ? ` · options scan ${symbols.slice(0, 3).join(', ') || '—'}` : '';
  setText($('#symbols-count'), `${symbols.length} / 8${options}`);
  $('#symbols-count').style.color = symbols.length > 8 ? 'var(--red)' : '';
}
$('#symbols').addEventListener('input', symbolsCount);
$$('[name=asset]').forEach(x => x.addEventListener('change', symbolsCount));
$('#session-form').addEventListener('submit', async e => {
  e.preventDefault(); $('#form-error').textContent = ''; $('#go').disabled = true;
  try { await post('/api/start', readConfig()); } catch (err) { $('#form-error').textContent = err.message; } finally { $('#go').disabled = false; }
});
$('#stop').onclick = () => post('/api/stop').catch(e => toast(e.message));
$('#refresh-market').onclick = async () => {
  $('#refresh-market').disabled = true;
  try { await post('/api/market/refresh'); toast('Market refresh requested. No AI requests.'); }
  catch (e) { toast(e.message); }
  finally { if (state) renderHeader(state); }
};
$('#pause-ai').onclick = () => post('/api/ai/pause', {paused:!state.ai_paused}).catch(e => toast(e.message));

// ---------- Tabs (remembered per browser) ----------
$('#chat-form').onsubmit = async e => {
  e.preventDefault();
  const message = $('#chat-input').value.trim();
  if (!message) return;
  $('#send-chat').disabled = true;
  setText($('#chat-error'), '');
  try { await post('/api/chat', {message}); $('#chat-input').value = ''; }
  catch (err) { setText($('#chat-error'), err.message); }
  finally { if (state) renderChat(state); }
};
$('#clear-chat').onclick = () => post('/api/chat/clear').catch(e => toast(e.message));
$$('[data-chat-prompt]').forEach(button => button.onclick = () => {
  $('#chat-input').value = button.dataset.chatPrompt;
  $('#chat-input').focus();
});
function renderChat(s) {
  const chat = s.chat || {messages:[], running:false};
  const blocker = s.ai_paused ? 'AI is paused.' : !s.ai_settings?.configured ? 'Configure an AI provider first.' : s.ai_usage.requests >= s.ai_usage.limit ? 'Daily AI request cap reached.' : '';
  $('#send-chat').disabled = chat.running || !!blocker;
  $('#clear-chat').disabled = chat.running || !chat.messages.length;
  setText($('#chat-status'), chat.running ? 'Reading session context…' : blocker || 'Uses existing session data · No new web search');
  const box = $('#chat-messages');
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const html = chat.messages.map(m => `<article class="chat-message ${m.role === 'user' ? 'user' : 'assistant'}"><div class="chat-meta">${m.role === 'user' ? 'You' : 'Assistant'} · ${esc(time(m.at))}${m.context_at ? ` · Context captured ${esc(time(m.context_at))}` : ''}</div><p>${esc(m.text)}</p>${sourceLinks(m.sources || [])}</article>`).join('') || '<p class="hint">No messages yet. Replies use your latest available session data, including its gaps and timestamps.</p>';
  if (box.innerHTML !== html) { patch(box, html); if (nearBottom) box.scrollTop = box.scrollHeight; }
}
function showTab(name) {
  if (!$(`[data-tab="${name}"]`)) name = 'overview';
  $$('[data-tab]').forEach(x => x.classList.toggle('active', x.dataset.tab === name));
  $$('.tab-content').forEach(x => x.hidden = x.id !== name);
  store.set('daylight-tab', name);
}
$$('[data-tab]').forEach(b => b.onclick = () => showTab(b.dataset.tab));
showTab(store.get('daylight-tab', 'overview'));

// ---------- Header, status and stats ----------
function renderHeader(s) {
  const c = s.config;
  if (firstRender || s.running) { fillConfig(c); firstRender = false; }
  $('#settings').disabled = s.running; $('#go').hidden = s.running; $('#stop').hidden = !s.running;
  $('#go').disabled = s.scanning || s.researching;
  setText($('#status'), s.running ? (s.scanning ? 'Scanning the market…' : 'Session running') : 'Session stopped');
  $('#refresh-market').disabled = !s.running || s.scanning || s.market_refresh_requested;
  setText($('#refresh-market'), s.scanning || s.market_refresh_requested ? 'Refreshing market data…' : 'Refresh market data');
  $('#refresh-market').title = !s.running ? 'Start a session to refresh market data.' : 'Refresh quotes, candles, headlines and position marks. Uses existing research; no AI requests.';
  $('#status-dot').classList.toggle('live', s.running);
  setText($('#available'), money(s.available)); setText($('#risk-left'), money(s.risk_left));
  setText($('#last-scan'), time(s.last_scan)); setText($('#calls'), `${s.calls_used} / ${c.research_calls}`);
  setText($('#connection'), 'LIVE DATA');
  setText($('#mode-banner'), `LIVE MARKET DATA · ${s.connections.stock_feed.toUpperCase()} stocks / ${s.connections.option_feed.toUpperCase()} options / Alpaca Bitcoin${!c.require_research ? ' · technical-only ideas permitted' : ''}`);
  setText($('#alpaca-status'), s.connections.alpaca ? 'Key configured' : 'Not configured');
  setText($('#ai-status'), s.ai_settings?.configured ? `${s.ai_settings.label} · ${s.ai_settings.model}` : 'Not configured');
  const feeds = s.feed_health || [], fresh = feeds.filter(f => f.status === 'fresh').length;
  setText($('#feed-summary'), `${fresh} / ${feeds.length} feeds with fresh quotes · ${s.running ? 'monitoring' : 'session stopped'}`);
  setText($('#scan-cadence'), s.running ? `Scans ${interval(s.scan_interval || 60)}` : '');
  setText($('#daily-usage'), `Daily AI allowance: ${s.ai_usage?.requests || 0} / ${s.ai_usage?.limit || 120} requests (UTC)`);
  // AI pause
  setText($('#pause-ai'), s.ai_paused ? 'Resume AI' : 'Pause all AI');
  $('#pause-ai').classList.toggle('paused', !!s.ai_paused);
  $('#ai-pause-note').hidden = !s.ai_paused;
  setText($('#ai-pause-note'), 'AI paused · no new AI requests. Market scans and manual logging continue.' + (s.ai_inflight ? ' Already-sent requests are still finishing and may be billed.' : ''));
}

// ---------- Market desk ----------
const REASONS = [
  ['Quote older than 45 seconds', 'The latest bid/ask is more than 45 seconds old. Ideas are only built on prices that are current right now; this clears when a fresh quote arrives.'],
  ['Missing or invalid bid/ask', 'No usable two-sided quote is available, so there is no reliable price to plan an entry from.'],
  ['No displayed two-sided liquidity', 'Bid or ask size is zero. Without visible liquidity on both sides an entry estimate would be unreliable.'],
  ['Bid/ask spread exceeds', 'The gap between bid and ask is too wide for this asset. Crossing a wide spread eats into the planned reward-to-risk before the trade even starts.'],
  ['Warming up', 'The scanner needs 25 completed one-minute candles after the open before its trend, VWAP and volume readings mean anything.'],
  ['Completed candle is missing, stale, or still forming', 'The most recent finished minute candle has not arrived or is too old. For Bitcoin this usually means a quiet minute with no trades on Alpaca’s venue; it clears when trading resumes.'],
  ['Gaps or duplicate minute candles', 'Some minutes are missing or repeated in the last 25 candles. Indicators computed over a broken series are unreliable, so the scanner waits. Bitcoin feeds skip minutes without trades; this clears once volume returns.'],
  ['Quote moved too far from the completed candle', 'Price jumped well beyond the last completed candle, so a plan would be based on stale structure. The next candle usually resolves this.'],
  ['No clear price/volume alignment', 'Short and medium moving averages, rolling VWAP and volume do not agree on a direction. This is the normal “nothing to do” state and it is fine to wait.'],
  ['No traded volume', 'No shares or coins traded during the window, so volume-based checks cannot run.'],
  ['SPY context is unavailable or inconclusive', 'Stock and option ideas require the broad market (SPY) to show the same direction. SPY currently has no clear trend or no fresh data.'],
  ['Price direction conflicts with SPY', 'This symbol is moving against the broad market. The scanner only takes setups aligned with SPY.'],
  ['Waiting for current, sourced news or macro research', 'Your settings require fresh, cited news or macro evidence for this asset before a live idea is shown. Untick “Require sourced research” for technical-only ideas.'],
  ['Research flagged a material event', 'A cited source points to an imminent high-impact event (earnings, policy decision, halt). Review the sources under Research before considering an entry.'],
  ['Price signal conflicts with sourced news/macro sentiment', 'The price trend points one way and cited news or macro sources point the other. Conflicts veto ideas.'],
  ['Bearish price action; no short', 'This version only takes long stock and Bitcoin positions plus long puts. A bearish read can still produce a long-put idea when options are enabled.'],
  ['No current 7–21 DTE contract', 'None of the sampled option contracts expiring in 7–21 days met the spread and delta filters with fresh two-sided quotes.'],
  ['Budget or remaining risk allowance too small', 'Even one share, contract or unit would break one of your three limits: risk per idea, remaining loss allowance, or max position size. The sidebar shows each in dollars. For options this is usually risk per idea, since a contract reserves its full premium.'],
  ['Discovery needs current company-specific news', 'A name found through social discussion needs a cited, company-specific news source too before it can qualify.'],
  ['No new stock or option entries in the final 15 minutes', 'New intraday entries stop 15 minutes before the close to avoid holding into the bell. Existing positions keep updating.'],
  ['Invalid candle', 'The market data provider returned a malformed candle; the scanner refuses to compute on bad data.'],
  ['Inconsistent OHLC', 'A candle’s open/high/low/close values contradict each other; the scanner refuses to compute on bad data.'],
  ['Malformed market data', 'The market data provider returned something unexpected; this scan is skipped for the symbol.'],
];
const explain = reason => REASONS.find(([key]) => reason.startsWith(key) || reason.includes(key))?.[1] || '';

function ideaCard(i) {
  const expired = Date.parse(i.expires_at) <= Date.now() || Date.now() - Date.parse(i.quote.t) > 45000;
  return `<article class="idea"><div class="idea-top"><div><div class="ticker">${esc(i.asset === 'option' ? i.underlying : i.symbol)}</div><div class="idea-type">${esc(i.direction)} · ${i.asset === 'option' ? 'OPTION' : i.asset.toUpperCase()}</div></div><span class="pill">${i.origin === 'discovery' ? 'DISCOVERED IDEA' : 'WATCHLIST IDEA'}</span></div>
  <div class="levels"><div><span>ENTRY REFERENCE</span><strong>${money(i.entry)}</strong></div><div><span>STOP REFERENCE</span><strong>${money(i.stop)}</strong></div><div><span>TARGET REFERENCE</span><strong>${money(i.target)}</strong></div></div>
  <div class="sizing"><span>${esc(i.quantity)} ${i.asset === 'option' ? 'contracts' : i.asset === 'bitcoin' ? 'BTC' : 'shares'} · ${money(i.notional)}</span><span>${i.asset === 'option' ? 'Premium' : 'Planned risk'} ${money(i.risk)}</span></div>
  <ul>${i.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
  <p class="hint">${esc(i.note)}</p>${i.asset === 'option' ? `<p class="hint">${esc(i.symbol)} · prices per share; 100 shares/contract</p>` : ''}
  <p class="hint">${esc(i.feed)} · quote ${time(i.quote.t)} · idea expires ${time(i.expires_at)}</p>
  <button class="track" data-open="${esc(i.id)}" ${expired ? 'disabled' : ''}>${expired ? 'Expired · awaiting next scan' : 'Track on paper ↗'}</button></article>`;
}

function renderDesk(s) {
  const c = s.config, session = s.market_session;
  setText($('#next-scan'), s.running ? `Next scan ${relative(s.next_scan) || time(s.next_scan)} · ${interval(s.scan_interval || 60)}` : 'Start a session to scan');
  // Session banner: calm status, replaces the old per-scan warning.
  const banner = $('#session-banner');
  let bannerText = '';
  if (s.running && session?.status === 'closed' && (c.assets.includes('stock') || c.assets.includes('option'))) {
    const reopen = session.next_open ? `reopens ${etTime(session.next_open, true)} (${relative(session.next_open)})` : 'reopen time unavailable';
    const rest = c.assets.includes('bitcoin') ? 'Bitcoin scanning continues every 60s.' : `Nothing is live right now, so scans slow to ${interval(s.scan_interval || 600)}; news and next-session plans still refresh.`;
    bannerText = `US stock & options session closed · ${reopen}. ${rest}`;
  } else if (s.running && session?.status === 'closing') {
    bannerText = `Final 15 minutes of the US session · no new stock or option entries. Existing positions keep updating${session.next_close ? ' until ' + etTime(session.next_close) : ''}.`;
  }
  banner.hidden = !bannerText;
  banner.className = 'session-banner ' + (session?.status || '');
  setText(banner, bannerText);
  patch($('#alerts'), s.errors.map(e => `<div class="alert">${esc(e)}</div>`).join(''));
  patch($('#ideas'), s.ideas.length ? s.ideas.map(ideaCard).join('') :
    `<div class="empty"><div class="empty-icon">⌁</div><h3>${s.running ? 'Watching. No qualifying ideas yet.' : 'A little context before your next move.'}</h3><p>${s.running ? 'Research, price action and risk limits need to align.<br>The list below explains what each symbol is waiting for.' : 'Set your budget and hit Go.<br>Your observations and ideas will appear here.'}</p></div>`);
  renderWatchlist(s);
  renderChart(s);
  renderDiscovery(s);
  renderPlanning(s);
  renderChanges(s);
}

// Watchlist status: one row per monitored symbol, so "nothing happened" is still explained.
function renderWatchlist(s) {
  const session = s.market_session, rows = [];
  const seen = new Set();
  const add = (symbol, state, reason) => { if (symbol && !seen.has(symbol)) { seen.add(symbol); rows.push({symbol, state, reason}); } };
  for (const i of s.ideas) add(i.asset === 'option' ? i.symbol : i.underlying, 'idea', `Qualifying ${i.direction.toLowerCase()} idea above. Entry reference ${money(i.entry)}.`);
  for (const r of s.rejected) add(r.symbol, /research|conflict|event|SPY|final 15/i.test(r.reason) ? 'blocked' : 'waiting', r.reason);
  // Symbols with market data but no verdict either way still deserve a line.
  for (const a of s.analyses) if (a.symbol !== 'SPY' || s.config.symbols.includes('SPY')) add(a.symbol, 'waiting', a.reason || 'Monitored; no signal this scan.');
  const counts = {idea:0, waiting:0, blocked:0};
  rows.forEach(r => counts[r.state]++);
  setText($('#watchlist-summary'), rows.length ? `${counts.idea} qualifying · ${counts.waiting} waiting · ${counts.blocked} blocked` : '');
  const labels = {idea:'Idea', waiting:'Waiting', blocked:'Blocked'};
  patch($('#rejected'), rows.length ? rows.map(r => {
    const why = r.state === 'idea' ? '' : explain(r.reason);
    return `<div class="reject"><strong>${esc(r.symbol)}</strong><span class="state ${r.state}">${labels[r.state]}</span><span class="reason">${esc(r.reason)}</span>${why ? `<span class="why">${esc(why)}</span>` : ''}</div>`;
  }).join('') : `<p class="hint">${!s.running ? 'Start a session to see what each symbol is doing.' : session?.status === 'closed' && !s.config.assets.includes('bitcoin') ? `Nothing is being monitored while the US session is closed${session.next_open ? '; it reopens ' + etTime(session.next_open, true) : ''}.` : 'Waiting for the first scan of this session.'}</p>`);
}

// Chart. The live one-minute view of a monitored symbol comes from the scan and updates itself.
// Every other range is fetched history, which works with the market closed and the session stopped.
let selected = store.get('daylight-chart', 'SPY');
let chartSpan = store.get('daylight-chart-span', '1D');
let chartInterval = store.get('daylight-chart-interval', 'auto');
if (!['1D', '5D'].includes(chartSpan)) chartInterval = 'auto';
let chartHoverIndex = null, displayedChart = null;
let chartData = null, chartLoading = false, chartError = '';
const chartKey = () => selected + '|' + chartSpan + '|' + chartInterval;
const liveChart = s => chartSpan === '1D' && ['auto', '1Min'].includes(chartInterval) && s?.analyses?.find(a => a.symbol === selected && a.bars?.length);
async function loadChart(force) {
  const key = chartKey();
  if (chartLoading) return;
  // A monitored symbol's live intraday view comes from the scan and refreshes itself; never fetch it.
  if (liveChart(state)) return;
  if (!force && (chartData?.key === key || chartError === key)) return;
  chartLoading = true;
  $('#chart-symbol-go').disabled = true;
  setText($('#chart-caption'), `Loading ${selected} · ${chartSpan}…`);
  try {
    const response = await fetch('/api/chart', {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf},
                                                body:JSON.stringify({symbol:selected, span:chartSpan, interval:chartInterval})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Chart data unavailable');
    if (key === chartKey()) { data.key = key; chartData = data; chartError = ''; }
  } catch (e) {
    if (key === chartKey()) { chartData = null; chartError = key; toast(e.message); }
  } finally {
    chartLoading = false; $('#chart-symbol-go').disabled = false; renderChart(state);
  }
}
function selectChart(value) {
  const requested = String(value).trim().toUpperCase();
  if (!/^(?:[A-Z]{1,5}|BTC\/USD)$/.test(requested)) { toast('Enter a stock ticker or BTC/USD.'); return; }
  selected = requested; store.set('daylight-chart', selected);
  chartHoverIndex = null;
  $('#chart-symbol-input').value = selected;
  chartError = '';
  renderChart(state);
  loadChart(true);
}
function selectSpan(span) {
  chartHoverIndex = null;
  if (!['1D', '5D'].includes(span)) { chartInterval = 'auto'; store.set('daylight-chart-interval', chartInterval); }
  chartSpan = span; store.set('daylight-chart-span', span);
  chartError = '';
  renderChart(state);
  loadChart(true);
}
$('#chart-symbol-go').onclick = () => selectChart($('#chart-symbol-input').value);
$('#chart-symbol-input').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); selectChart(e.target.value); } };
$('#chart-symbol-input').value = selected;
$$('[data-span]').forEach(b => b.onclick = () => selectSpan(b.dataset.span));
$('#chart-interval').onchange = e => {
  chartInterval = e.target.value; store.set('daylight-chart-interval', chartInterval);
  chartHoverIndex = null; chartError = ''; renderChart(state); loadChart(true);
};
$('#chart-refresh').onclick = () => { chartError = ''; if (liveChart(state)) refresh(); else loadChart(true); };
$('#analyze-chart').onclick = async () => {
  if (!displayedChart) return;
  $('#analyze-chart').disabled = true;
  try { await post('/api/chart/analyze', displayedChart); }
  catch (e) { toast(e.message); }
  finally { if (state) renderChart(state); }
};
// Chart style. "auto" picks candles until bars get too dense to read; an explicit choice sticks.
let chartType = store.get('daylight-chart-type', 'auto');
const effectiveType = count => chartType === 'auto' ? (count > 180 ? 'line' : 'candles') : chartType;
$$('[data-chart-type]').forEach(b => b.onclick = () => {
  chartType = b.dataset.chartType; store.set('daylight-chart-type', chartType); renderChart(state);
});
// Crosshair: read the price at any point, which candle tooltips alone cannot do in line mode.
// Bound to the container, which survives redraws, so it never needs reattaching.
$('#chart').addEventListener('pointermove', e => {
  const svg = $('#chart').querySelector('svg');
  if (!svg || !chartGeometry?.bars.length) return;
  const box = svg.getBoundingClientRect();
  if (!box.width) return;
  const {bars, left, dx, w, span} = chartGeometry;
  const matrix = svg.getScreenCTM();
  const x = matrix ? new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse()).x : (e.clientX - box.left) / box.width * w;
  const index = Math.max(0, Math.min(bars.length - 1, Math.round((x - left) / dx - .5)));
  chartHoverIndex = index;
  const bar = bars[index], lineX = (left + dx * (index + .5)).toFixed(2);
  const cross = svg.querySelector('.crosshair');
  if (cross) { cross.setAttribute('x1', lineX); cross.setAttribute('x2', lineX); cross.style.opacity = '.9'; }
  // Bitcoin volume is fractional coins, so rounding it to whole units would read as zero.
  const volume = Number.isFinite(bar.v) && bar.v > 0
    ? '  Vol ' + bar.v.toLocaleString('en-US', {maximumFractionDigits: bar.v < 1000 ? 4 : 0}) : '';
  setText($('#chart-hover'), `${barLabel(bar.t, span)}   O ${tick(bar.o)}  H ${tick(bar.h)}  L ${tick(bar.l)}  C ${tick(bar.c)}${volume}`);
  patch($('#chart-price'), `<strong>${money(bar.c)}</strong><span class="muted">Close · ${esc(new Date(bar.t).toLocaleString())}</span>`);
});
$('#chart').addEventListener('pointerleave', () => {
  chartHoverIndex = null;
  const cross = $('#chart').querySelector('.crosshair');
  if (cross) cross.style.opacity = '0';
  setText($('#chart-hover'), '');
  renderChart(state);
});
const METRIC_HELP = [
  'Volume-weighted average price over the last 25 one-minute bars, using each bar’s average of high, low and close. A rolling reference, not a full-session VWAP.',
  'Average True Range over 14 bars: recent price movement in dollars, including gaps. Higher means larger recent moves, not a predicted direction.',
  'Latest completed minute’s volume divided by the average of the previous 20 minutes. 2× means twice that recent average.',
  'Percentage price change over five minutes. A recent move does not predict the next move.',
];
// Axis and tag prices: cents matter under $1,000, thousands separators matter above it.
const tick = p => p >= 1000 ? '$' + Math.round(p).toLocaleString('en-US') : '$' + p.toFixed(2);
const SPAN_WORDS = {'1D':'the session', '5D':'5 days', '1M':'1 month', '6M':'6 months', '1Y':'1 year', '5Y':'5 years'};
const spanWords = span => SPAN_WORDS[span] || span;
let chartGeometry = null;
const barLabel = (iso, span) => ['6M', '1Y', '5Y'].includes(span)
  ? new Date(iso).toLocaleDateString('en-US', {month:'short', day:'numeric', year:span === '5Y' ? '2-digit' : undefined})
  : ['1M', '5D'].includes(span) ? new Date(iso).toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric'})
  : time(iso);
function renderChart(s) {
  if (!s) return;
  $('#chart-interval').value = chartInterval;
  $('#chart-interval').disabled = !['1D', '5D'].includes(chartSpan);
  const picks = [...new Set([...(s.config.symbols || []), 'BTC/USD', ...(chartData ? [chartData.symbol] : [])])];
  if (patch($('#chart-quick-picks'), picks.map(p => `<button type="button" class="chart-pick ${p === selected ? 'active' : ''}" data-chart-pick="${esc(p)}">${esc(p)}</button>`).join('')))
    $$('[data-chart-pick]').forEach(b => b.onclick = () => selectChart(b.dataset.chartPick));
  $$('[data-chart-pick]').forEach(b => b.classList.toggle('active', b.dataset.chartPick === selected));
  $$('[data-span]').forEach(b => b.classList.toggle('active', b.dataset.span === chartSpan));

  const live = liveChart(s);
  const source = live || (chartData?.key === chartKey() ? chartData : null);
  const bars = source?.bars || [];
  $('#chart-interval-control').hidden = effectiveType(bars.length) !== 'candles';
  displayedChart = bars.length ? {symbol:selected, span:chartSpan, interval:chartInterval, bars:bars.map(b => ({t:b.t, o:b.o, h:b.h, l:b.l, c:b.c, v:b.v}))} : null;
  $('#analyze-chart').disabled = bars.length < 5 || s.ai_paused || !s.ai_settings?.configured || s.chart_analyzing || s.ai_usage.requests >= s.ai_usage.limit;
  const result = s.chart_analysis || {}, reportChart = result.chart;
  const sameChart = reportChart && reportChart.symbol === selected && reportChart.span === chartSpan && reportChart.interval === chartInterval;
  setText($('#chart-analysis-status'), s.chart_analyzing ? `Analyzing ${reportChart?.symbol || 'chart'}…` : s.ai_paused ? 'Chart analysis paused with all AI.' : !s.ai_settings?.configured ? 'Configure an AI provider for chart analysis.' : '');
  let assessment = '';
  if (sameChart && result.status === 'complete') {
    assessment = `<article class="long-view"><h3>${esc(reportChart.symbol)} · ${esc(reportChart.timeframe)} · ${esc(result.bias)}</h3><p class="hint">${reportChart.count} candles · ${esc(dateTime(reportChart.from))} – ${esc(dateTime(reportChart.to))}${bars.at(-1)?.t !== reportChart.to ? ' · Chart has changed; refresh analysis for the latest candles.' : ''}</p><p>${esc(result.summary)}</p>${['observations','levels','scenarios','limitations'].map(k => `<h4>${esc({observations:'Technical picture', levels:'Levels to watch', scenarios:'Scenarios & invalidation', limitations:'Limitations'}[k])}</h4>${list(result[k], 'None returned.')}`).join('')}</article>`;
  } else if (sameChart && result.status === 'error') assessment = `<p class="hint">${esc(result.summary)}</p>`;
  patch($('#chart-analysis-result'), assessment);
  // The market being shut is context, never a reason to withhold the chart.
  const session = (source?.session) || s.market_session;
  const closedNote = selected === 'BTC/USD' ? 'Bitcoin trades 24/7'
    : session?.status === 'closed' ? `US market closed${session.next_open ? ' · reopens ' + etTime(session.next_open, true) : ''}`
    : session?.status === 'closing' ? 'US market closes shortly'
    : session?.status === 'open' ? 'US market open' : '';

  if (!bars.length) {
    if (!chartLoading) loadChart(false);
    chartGeometry = null;
    setText($('#chart-caption'), chartLoading ? `Loading ${selected} · ${chartSpan}…` : `${selected} · ${chartSpan}`);
    patch($('#chart-price'), '');
    patch($('#chart-hover'), '');
    patch($('#chart-range-summary'), '');
    patch($('#chart'), `<p class="hint">${chartLoading ? 'Loading price history…' : chartError === chartKey() ? `No price history returned for ${esc(selected)} over ${esc(chartSpan)}. Check the ticker, or try a longer range.` : 'Select a range to load price history.'}</p>`);
    patch($('#metrics'), '');
    return;
  }

  const w = 850, h = 225, left = 8, right = 78, top = 10, bottom = 24;
  const lo = Math.min(...bars.map(b => b.l)), hi = Math.max(...bars.map(b => b.h));
  const spread = Math.max(hi - lo, hi * .0001);
  const y = p => top + (hi - p) / spread * (h - top - bottom);
  const dx = (w - left - right) / bars.length;
  const rising = bars.at(-1).c >= bars[0].c;
  let svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(selected)} ${effectiveType(bars.length) === 'line' ? 'closing price line' : 'candlestick'} chart, ${esc(source.label || chartSpan)}">`;
  for (let i = 0; i < 4; i++) {
    const p = lo + spread * i / 3;
    svg += `<line class="grid" x1="0" x2="${w-right}" y1="${y(p)}" y2="${y(p)}"/><text x="${w-right+10}" y="${y(p)+4}">${tick(p)}</text>`;
  }
  const style = effectiveType(bars.length);
  $$('[data-chart-type]').forEach(b => b.classList.toggle('active', b.dataset.chartType === style));
  if (style === 'line') {
    // Candles become unreadable slivers at high density, so a close line is the default there.
    const points = bars.map((b, i) => `${(left + dx * (i + .5)).toFixed(2)},${y(b.c).toFixed(2)}`);
    svg += `<path class="area ${rising ? '' : 'down'}" d="M${left},${y(lo)} L${points.join(' L')} L${w-right},${y(lo)} Z"/>`;
    svg += `<polyline class="line ${rising ? '' : 'down'}" points="${points.join(' ')}"/>`;
  } else {
    bars.forEach((b, i) => {
      const x = left + dx * (i + .5), cls = b.c >= b.o ? 'up' : 'down', body = Math.max(1, Math.abs(y(b.o) - y(b.c)));
      svg += `<g><title>${esc(barLabel(b.t, chartSpan))} O ${b.o.toFixed(2)} H ${b.h.toFixed(2)} L ${b.l.toFixed(2)} C ${b.c.toFixed(2)}</title><line class="candle ${cls}" x1="${x}" x2="${x}" y1="${y(b.h)}" y2="${y(b.l)}"/><rect class="candle ${cls}" x="${x-dx*.27}" y="${Math.min(y(b.o), y(b.c))}" width="${Math.max(.6, dx*.54)}" height="${body}"/></g>`;
    });
  }
  // The latest price, marked on the axis so it is readable straight off the chart.
  const last = bars.at(-1).c, first = bars[0].c;
  const lastY = y(last), tagText = tick(last), tagWidth = Math.max(44, tagText.length * 6 + 12);
  svg += `<line class="last-price" x1="0" x2="${w-right}" y1="${lastY}" y2="${lastY}"/>`;
  svg += `<rect class="price-tag ${rising ? '' : 'down'}" x="${w-right+4}" y="${lastY-9}" width="${tagWidth}" height="18" rx="3"/>`;
  svg += `<text class="price-tag-text" x="${w-right+10}" y="${lastY+4}">${esc(tagText)}</text>`;
  svg += `<line class="crosshair" x1="0" x2="0" y1="${top}" y2="${h-bottom}"/>`;
  svg += `<text x="8" y="222">${esc(barLabel(bars[0].t, chartSpan))}</text><text x="${w-right-90}" y="222">${esc(barLabel(bars.at(-1).t, chartSpan))}</text></svg>`;
  patch($('#chart'), svg);
  chartGeometry = {bars, left, dx, w, span: chartSpan};

  const label = live ? 'Latest session · 1-minute bars (live)' : source.label || chartSpan;
  setText($('#chart-caption'), `${selected} · ${label} · ${source.feed || ''} · last bar ${barLabel(bars.at(-1).t, chartSpan)}`
    + (live && source.direction && source.direction !== 'wait' ? ' · ' + source.direction : ''));
  const change = first ? (last / first - 1) * 100 : 0, moved = last - first;
  const sign = change >= 0 ? 'positive' : 'negative';
  if (chartHoverIndex === null) patch($('#chart-price'), `<strong>${money(last)}</strong><span class="delta ${sign}">${moved >= 0 ? '+' : '−'}${money(Math.abs(moved))} (${change >= 0 ? '+' : ''}${change.toFixed(2)}%)</span><span class="muted">over ${esc(spanWords(chartSpan))}</span>`);
  patch($('#chart-range-summary'), `${bars.length} bars · high ${money(hi)} · low ${money(lo)}${closedNote ? ` · <span class="chart-note">${esc(closedNote)}</span>` : ''}`);

  const m = live ? live.metrics : {};
  if (patch($('#metrics'), m.price ? `<span>25m rolling VWAP <b>${money(m.vwap)}</b></span><span>ATR(14) <b>${money(m.atr)}</b></span><span>Relative volume <b>${m.relvol.toFixed(2)}×</b></span><span>5m change <b>${m.change.toFixed(2)}%</b></span>` : ''))
    $$('#metrics > span').forEach((node, i) => addHelp(node, METRIC_HELP[i], node.firstChild.textContent.trim()));
}

function renderDiscovery(s) {
  const rows = s.discoveries || [];
  if (patch($('#discovery-list'), rows.length ? rows.map(d => `<article class="evidence"><div class="section-title"><h3>${esc(d.symbol)}</h3><button type="button" class="text-button" data-discovery-chart="${esc(d.symbol)}">View chart</button></div><p>${esc(d.reason)}</p><p class="hint">${esc(d.status)}</p>${d.sources.map(e => `<p class="hint"><a href="${url(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a> · ${esc(e.lane)} · ${esc(dateTime(e.published_at) || 'date unverified')}</p>`).join('')}</article>`).join('')
    : '<div class="empty"><h3>No current discovery leads</h3><p>Leads appear after a sourced research cycle. Enable AI web research to search beyond your watchlist.</p></div>'))
    $$('[data-discovery-chart]').forEach(b => b.onclick = () => { selectChart(b.dataset.discoveryChart); $('.chart-panel').scrollIntoView({behavior:'smooth', block:'center'}); });
}

function renderPlanning(s) {
  $('#closed-market-ideas').checked = !!s.config.closed_market_ideas;
  const plans = s.next_session_ideas || [];
  const blocker = !s.running ? 'Start a session to build plans.' : !s.config.closed_market_ideas ? 'Enable the checkbox above.' : null;
  $('#regenerate-planning').disabled = !!blocker || s.scanning || s.market_refresh_requested;
  $('#regenerate-planning').title = blocker || 'Fetch fresh daily bars and rebuild these plans now.';
  setText($('#planning-state'), s.scanning || s.market_refresh_requested ? 'Rebuilding…'
    : blocker ? blocker : plans.length ? `${plans.length} plan${plans.length === 1 ? '' : 's'} · rebuilt ${time(s.last_scan)}` : '');
  patch($('#planning-results'), !s.config.closed_market_ideas ? '' : plans.length ? plans.map(p => `<article class="evidence"><h3>${esc(p.symbol)} · ${esc(p.instruments.join(' / '))}</h3><span class="pill">NEXT-SESSION PLAN · RECHECK AT OPEN</span><p>${esc(p.summary)}</p><p class="hint">${esc(p.research_status)}<br>Last completed close: ${money(p.reference_close)} · ${esc(new Date(p.reference_at).toLocaleDateString())}<br>5-session change: ${p.five_session_change}%</p><ul>${p.conditions.map(c => `<li>${esc(c)}</li>`).join('')}</ul>${p.option_note ? `<p class="hint">${esc(p.option_note)}</p>` : ''}${sourceLinks(p.sources)}</article>`).join('')
    : `<p class="hint">${s.running ? 'No next-session plans yet. Plans need the US market to be closed, at least 20 completed daily bars and a clear 5/20-session trend; conflicting current research can withhold them.' : 'Start monitoring to build plans while the US stock market is closed.'}</p>`);
}
$('#closed-market-ideas').onchange = e => post('/api/planning', {enabled:e.target.checked}).catch(err => { toast(err.message); e.target.checked = !!state?.config.closed_market_ideas; });
// Plans are rebuilt from the latest scan, so regenerating means fetching fresh daily bars now
// rather than waiting for the next scheduled scan. Market data only.
$('#regenerate-planning').onclick = async () => {
  $('#regenerate-planning').disabled = true;
  try { await post('/api/market/refresh'); toast('Rebuilding next-session plans from fresh daily bars. No AI requests.'); }
  catch (e) { toast(e.message); }
  finally { if (state) renderPlanning(state); }
};

// What changed + alerts (browser preferences; the server only records the events).
let lastChangeId = null;
const alertPreference = () => store.get('daylight-alerts', 'on') !== 'off';
const desktopPreference = () => store.get('daylight-desktop-alerts', 'off') === 'on';
$('#opportunity-alerts').checked = alertPreference();
$('#opportunity-alerts').onchange = e => store.set('daylight-alerts', e.target.checked ? 'on' : 'off');
$('#desktop-alerts').onclick = async () => {
  if (!('Notification' in window)) { toast('Desktop notifications are not supported by this browser.'); return; }
  if (desktopPreference()) { store.set('daylight-desktop-alerts', 'off'); renderChanges(state); return; }
  try {
    const permission = await Notification.requestPermission();
    if (permission === 'granted') store.set('daylight-desktop-alerts', 'on'); else toast('Desktop alerts were not enabled. In-page alerts still work.');
  } catch { toast('Desktop alerts could not be enabled.'); }
  renderChanges(state);
};
function renderChanges(s) {
  if (!s) return;
  setText($('#desktop-alerts'), desktopPreference() ? 'Disable desktop alerts' : 'Enable desktop alerts');
  const events = s.changes || [], latest = events.at(-1)?.id || 0;
  const pending = lastChangeId === null ? [] : events.filter(e => e.id > lastChangeId);
  lastChangeId = latest;
  const active = pending.filter(e => e.notify && Date.now() - Date.parse(e.at) < 60000 && s.running && s.ideas.some(i => i.symbol === e.symbol && i.direction === e.direction && Date.parse(i.expires_at) > Date.now() && Date.now() - Date.parse(i.quote.t) < 45000));
  if (alertPreference() && active.length) {
    const message = active.map(e => `${e.symbol}: ${e.direction}`).join(' · ');
    toast('New qualifying ideas: ' + message + '. Review current prices on Market desk.');
    if (desktopPreference() && 'Notification' in window && Notification.permission === 'granted') {
      try { const n = new Notification('Daylight: new qualifying ideas', {body:message, tag:'daylight-opportunities'}); n.onclick = () => { window.focus(); n.close(); showTab('overview'); }; setTimeout(() => n.close(), 30000); } catch {}
    }
  }
  patch($('#change-events'), [...events].reverse().slice(0, 50).map(e => `<article class="scan-change"><div><strong>${esc(e.symbol)} · ${esc(e.direction)}</strong><span class="muted">${esc(e.kind)} · ${time(e.at)}</span></div><p>${esc(e.message)}</p></article>`).join('') || '<p class="hint">Changes will appear after market scans produce or withdraw ideas.</p>');
  setText($('#change-summary'), events.length ? `Latest: ${events.at(-1).symbol} · ${events.at(-1).kind} · ${time(events.at(-1).at)}` : 'No idea changes recorded yet.');
}

// Paper open/close buttons live inside rendered cards.
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-open],[data-close]');
  if (!b) return;
  b.disabled = true;
  try {
    await post(b.dataset.open ? '/api/paper/open' : '/api/paper/close', {id:b.dataset.open || b.dataset.close});
    toast(b.dataset.open ? 'Tracking a hypothetical position. No trade placed.' : 'Paper estimate closed. No trade placed.');
  } catch (err) { toast(err.message); } finally { b.disabled = false; }
});

// ---------- Idea check: thesis research or a check against the live scan ----------
const IDEA_MODES = {
  thesis: {
    heading:'Research a trade thesis', label:'What trade are you considering?',
    intro:'Describe any trade thesis and timeframe, for example “NVDA calls over the next month.” Fresh web research returns a graded verdict, risks, what would change the view, and a specific contract candidate when market data permits. Works with monitoring stopped or markets closed; your watchlist does not limit it.',
    button:'Research my thesis', running:'Researching thesis…',
    hint:'One AI request with web search, counted toward your shared daily allowance. Stock and option contract checks use Alpaca separately; missing quotes or entitlements are reported, never guessed.',
    legend:'Favorable → Cautiously favorable → Mixed → Unfavorable. Insufficient evidence means there is no defensible assessment. These are research judgments, not win probabilities. Contract suitability is checked separately against live quotes.',
    placeholder:'I’m considering NVDA calls over the next month. What catalysts, price trends and options risks matter?',
    prompts:[['NVDA calls · 1 month', 'I’m considering NVDA calls over the next month. Research upcoming catalysts, recent performance, time decay and volatility risks, and identify a contract candidate if data supports it.'],
             ['MSFT shares · 1 year', 'I’m considering MSFT shares over the next year. Assess valuation, growth drivers and what would invalidate this thesis.'],
             ['SPY puts · 6 weeks', 'I’m considering SPY puts over the next six weeks as a hedge. Assess macro catalysts, timing and option costs.']],
  },
  live: {
    heading:'Check an idea against the live scan', label:'What are you thinking of doing right now?',
    intro:'A quick second opinion on an intraday idea for your watchlist or Bitcoin. One AI request interprets your proposal against the current scan and existing research; a separate deterministic check then enforces quote freshness, eligibility, sizing and scope before a Yes can appear. Needs a running session with a scan in the last two minutes.',
    button:'Check my idea', running:'Checking market context…',
    hint:'Limited to 10 checks per session, in addition to the shared daily allowance. Conditional entries (“if it breaks out”) and custom prices, strikes or expirations are not validated yet and receive No.',
    legend:'YES means the idea matches an idea that currently passes every market, research and risk filter, with fresh quotes and room in your paper budget. NO lists the exact checks that failed. No verdict is issued if the AI service fails. A Yes expires with the underlying idea.',
    placeholder:'Should I buy SPY right now given the current market?',
    prompts:[['Buy SPY now?', 'Should I buy SPY shares right now given the current scan?'],
             ['NVDA call now?', 'Is a long NVDA call supported by the current market context?'],
             ['$500 of Bitcoin?', 'Should I buy $500 of Bitcoin right now?']],
  },
  outlook: {
    heading:'Long-term outlook', label:'What longer-term question should the research answer?',
    intro:'A 6 to 18 month view on a company, sector or asset, built from filings, valuation context, rates and dated catalysts. Returns a thesis, risks, what would invalidate it and source links. Reports without accepted sources return no conclusion rather than a guess.',
    button:'Research longer-term outlook', running:'Researching outlook…',
    hint:'One AI request with web search, counted toward your shared daily allowance. Requires a running session and a provider with web search. Specific LEAPS contracts are not valued here.',
    legend:'Bullish, bearish or mixed describe the direction the cited evidence supports over the horizon. No conclusion means the sources could not support a defensible view. None of these are entry signals or price forecasts.',
    placeholder:'Could Microsoft LEAPS make sense over the next year given valuation, macro conditions and catalysts?',
    prompts:[['MSFT · next year', 'Could Microsoft make sense as a longer-term holding over the next year given valuation, macro conditions and catalysts?'],
             ['Semis · 12–18 months', 'What is the 12 to 18 month outlook for semiconductor stocks given capital spending, export rules and demand?'],
             ['Bitcoin · 12 months', 'What is the 12 month outlook for Bitcoin given ETF flows, regulation and macro liquidity?']],
  },
};
let ideaMode = store.get('daylight-idea-mode', 'thesis');
function setIdeaMode(mode) {
  ideaMode = IDEA_MODES[mode] ? mode : 'thesis';
  store.set('daylight-idea-mode', ideaMode);
  const m = IDEA_MODES[ideaMode];
  $$('[data-idea-mode]').forEach(b => { b.classList.toggle('active', b.dataset.ideaMode === ideaMode); b.setAttribute('aria-selected', b.dataset.ideaMode === ideaMode); });
  setText($('#idea-heading'), m.heading); setText($('#idea-label'), m.label); setText($('#idea-legend'), m.legend);
  // setText clears children, so the tooltip is re-attached with this mode's own explanation.
  addHelp($('#idea-heading'), m.intro + ' ' + m.hint, m.heading);
  $('#idea-text').placeholder = m.placeholder;
  patch($('#idea-prompts'), m.prompts.map(([label, text]) => `<button type="button" data-proposal="${esc(text)}">${esc(label)}</button>`).join(''));
  $$('[data-proposal]').forEach(b => b.onclick = () => { $('#idea-text').value = b.dataset.proposal; $('#idea-text').focus(); });
  $('#thesis-history-wrap').hidden = ideaMode !== 'thesis';
  $('#review-history-wrap').hidden = ideaMode !== 'live';
  $('#outlook-history-wrap').hidden = ideaMode !== 'outlook';
  $('#idea-error').textContent = '';
  if (state) renderIdeaCheck(state);
}
$$('[data-idea-mode]').forEach(b => b.onclick = () => setIdeaMode(b.dataset.ideaMode));
setIdeaMode(ideaMode);
const IDEA_ENDPOINTS = {live:'/api/idea/check', thesis:'/api/thesis', outlook:'/api/outlook'};
$('#idea-form').onsubmit = async e => {
  e.preventDefault(); $('#idea-error').textContent = ''; $('#check-idea').disabled = true;
  try { await post(IDEA_ENDPOINTS[ideaMode], {proposal:$('#idea-text').value}); }
  catch (err) { $('#idea-error').textContent = err.message; }
  finally { if (state) renderIdeaCheck(state); }
};
function liveCheckBlocker(s) {
  if (!s.running) return 'Start a session first: live checks compare your idea with the current scan.';
  if (!s.last_scan || Date.now() - Date.parse(s.last_scan) > 120000) return 'Waiting for a market scan from the last two minutes.';
  if (s.ai_paused) return 'AI is paused. Resume AI to run a check.';
  if (!s.ai_settings?.configured) return 'Configure an AI provider under AI settings.';
  if ((s.review_calls || 0) >= 10) return 'The 10-check limit for this session has been reached.';
  return null;
}
function thesisBlocker(s) {
  if (s.ai_paused) return 'AI is paused. Resume AI to research a thesis.';
  if (!s.ai_settings?.configured) return 'Configure an AI provider under AI settings.';
  if (!s.ai_settings?.web_research) return 'The selected local provider has no web search; choose a cloud provider for thesis research.';
  return null;
}
function outlookBlocker(s) {
  if (s.ai_paused) return 'AI is paused. Resume AI to request an outlook.';
  if (!s.ai_settings?.configured) return 'Configure an AI provider under AI settings.';
  if (!s.ai_settings?.web_research) return 'Long-term research requires a provider with web search.';
  if (!s.running) return 'Start a session before requesting a long-term outlook.';
  return null;
}
function renderIdeaCheck(s) {
  const m = IDEA_MODES[ideaMode], button = $('#check-idea');
  if (ideaMode === 'outlook') {
    const o = s.outlook || {status:'idle'}, blocker = outlookBlocker(s);
    setText($('#review-count'), '6–18 month research · 1 AI request');
    button.disabled = o.status === 'running' || !!blocker;
    button.firstChild.textContent = (o.status === 'running' ? m.running : m.button) + ' ';
    button.title = blocker || '';
    showIdeaBlocker(o.status === 'running' ? null : blocker);
    renderOutlook(o);
    patch($('#outlook-history-list'), (s.outlook_history || []).filter(h => h.kind !== 'thesis').map(h => `<article class="evidence"><h3>${esc(h.question)}</h3><p class="hint">${esc(new Date(h.checked_at).toLocaleString())} · ${esc(h.outlook)} · historical assessment</p><p>${esc(h.summary)}</p>${sourceLinks(h.sources)}</article>`).join('') || '<p class="hint">Saved outlooks appear here across restarts.</p>');
    return;
  }
  if (ideaMode === 'live') {
    const review = s.idea_review || {status:'idle'}, blocker = liveCheckBlocker(s);
    setText($('#review-count'), `${s.review_calls || 0} / 10 checks this session`);
    button.disabled = review.status === 'running' || !!blocker;
    button.firstChild.textContent = (review.status === 'running' ? m.running : m.button) + ' ';
    button.title = blocker || '';
    showIdeaBlocker(review.status === 'running' ? null : blocker);
    renderLiveReview(s, review);
    patch($('#review-history'), (s.review_history || []).map(h => `<article class="evidence"><div class="meta"><span class="meta-item"><span class="meta-key">Checked</span><b>${esc(new Date(h.result.checked_at).toLocaleString())}</b></span><span class="meta-item ${h.result.verdict === 'yes' ? 'bullish' : 'warn'}"><span class="meta-key">Verdict then</span><b>${esc(h.result.verdict.toUpperCase())}</b></span></div><p>${esc(h.proposal)}</p><p class="hint">${esc(h.result.summary)}</p><p class="hint">${h.result.checks.map(esc).join(' · ')}<br>Historical assessment only; submit again for a current check.</p></article>`).join('') || '<p class="hint">Completed live checks are saved here across restarts.</p>');
  } else {
    const r = s.thesis_report || {status:'idle'}, blocker = thesisBlocker(s);
    setText($('#review-count'), 'Open-ended research · 1 AI request');
    button.disabled = r.status === 'running' || !!blocker;
    button.firstChild.textContent = (r.status === 'running' ? m.running : m.button) + ' ';
    button.title = blocker || '';
    showIdeaBlocker(r.status === 'running' ? null : blocker);
    renderThesis(r);
    patch($('#thesis-history'), (s.outlook_history || []).filter(h => h.kind === 'thesis').map(h => `<article class="evidence"><h3>${esc(h.question)}</h3><p>${esc(h.verdict.replaceAll('-', ' '))} · ${esc(h.summary)}</p><p class="hint">${esc(new Date(h.checked_at).toLocaleString())} · historical assessment</p></article>`).join('') || '<p class="hint">Completed thesis assessments are saved locally.</p>');
  }
}
function showIdeaBlocker(message) {
  let element=$('#idea-blocker');
  if(!element){element=document.createElement('p');element.id='idea-blocker';element.className='hint';element.setAttribute('role','status');$('#idea-error').before(element);}
  setText(element,message||'');element.hidden=!message;
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-copy-trade]');if(!button)return;
  try{await navigator.clipboard.writeText(button.dataset.copyTrade);toast('Trade symbol copied.');}
  catch{toast('Clipboard unavailable. Select the displayed trade symbol to copy it.');}
});
function renderLiveReview(s, review) {
  const result = $('#idea-result');
  if (review.status === 'idle') { patch(result, '<div class="empty"><h3>A second look, grounded in your market.</h3><p>Your yes/no assessment and the main factors will appear here.</p></div>'); return; }
  if (review.status === 'running') { patch(result, '<div class="panel review-result"><h3>Checking your proposal…</h3><p class="hint">Reading the current scan and research, then rechecking freshness and risk limits. Follow the request under Activity.</p></div>'); return; }
  if (review.status !== 'complete') { patch(result, `<div class="alert">${esc(review.message || 'Assessment unavailable. No verdict issued.')}</div>`); return; }
  const r = review.result, expired = r.verdict === 'yes' && (!s.running || Date.parse(r.expires_at) <= Date.now());
  const reference = r.reference_idea ? `<p class="hint">Matching reference: ${esc(r.reference_idea.symbol)} · up to ${esc(r.reference_idea.quantity)} units · ${money(r.reference_idea.notional)} capital · ${money(r.reference_idea.risk)} reserved risk.</p>` : '';
  const sources = r.sources.length ? '<h3>Sources considered</h3>' + sourceLinks(r.sources) : '';
  patch(result, `<article class="panel review-result">
    <div class="review-verdict ${r.verdict === 'yes' && !expired ? 'verdict-yes' : 'verdict-no'}">${expired ? 'EXPIRED' : esc(r.verdict.toUpperCase())}<span>${expired ? 'Recheck against fresh market data' : esc(r.label)}</span></div>
    <p class="hint">Your idea: ${esc(review.proposal)}</p><p>${esc(r.summary)}</p>
    <div class="review-columns"><div><h3>Market & risk checks</h3>${list(r.checks, 'No checks returned.')}<h3>Supporting observations</h3>${list(r.supporting, 'No supporting observations returned.')}</div>
    <div><h3>Risks & missing information</h3>${list(r.risks, 'No additional risks identified by this assessment; this is not an assurance of safety.')}</div></div>
    <p class="hint">Interpreted as ${esc(r.interpretation.direction)} · ${esc(r.interpretation.symbol || 'unclear asset')} · ${esc(r.interpretation.asset)}. Check that this matches your intent.<br>Context ${time(review.context_at)} · checked ${time(r.checked_at)}${r.expires_at ? ' · valid until ' + time(r.expires_at) : ''}</p>${reference}${sources}</article>`);
}
function renderOutlook(o) {
  const result = $('#idea-result');
  if (o.status === 'running') { patch(result, '<div class="alert">Researching the longer-term question across current macro, company and catalyst sources.</div>'); return; }
  if (o.status === 'error') { patch(result, `<div class="alert">${esc(o.message || 'Outlook unavailable; no conclusion issued.')}</div>`); return; }
  if (o.status !== 'complete') { patch(result, '<div class="empty"><h3>A longer view on the same market.</h3><p>Ask about a company, sector or asset over the next 6 to 18 months.<br>The thesis, risks and sources will appear here.</p></div>'); return; }
  patch(result, `<article class="panel review-result"><div class="review-verdict ${o.outlook === 'bullish' ? 'verdict-yes' : o.outlook === 'bearish' ? 'verdict-no' : ''}">${esc((o.outlook || 'no-conclusion').replaceAll('-', ' ').toUpperCase())}<span>${esc(o.horizon || 'Long-term view')} · ${time(o.checked_at)}</span></div>
    ${o.question ? `<p class="hint">Your question: ${esc(o.question)}</p>` : ''}<p>${esc(o.summary || 'No conclusion provided.')}</p>
    <div class="review-columns"><div><h3>Thesis</h3>${list(o.thesis, 'No thesis factors returned.')}<h3>What would change this view</h3>${list(o.invalidation, 'None returned.')}</div>
    <div><h3>Risks</h3>${list(o.risks, 'No risks returned.')}${(o.instruments || []).length ? `<h3>Instruments to investigate</h3>${list(o.instruments, '')}` : ''}</div></div>
    <h3>Specific trade plan</h3>${tradePlanCard(o.specific_trade)}<h3>Sources</h3>${sourceLinks(o.sources)}<p class="hint">Assessed ${esc(new Date(o.checked_at).toLocaleString())}. Source dates and interpretations require review. This is a thesis assessment, not a current entry signal.</p></article>`);
}
function tradePlanCard(trade = {}) {
  if (trade.status !== 'candidate') return `<p><strong>None.</strong> ${esc(trade.reason || 'No specific trade was supported.')}</p>`;
  const option = ['call', 'put'].includes(trade.instrument);
  const label = option ? `Buy ${trade.underlying} ${trade.expiration} $${trade.strike} ${trade.instrument === 'call' ? 'Call' : 'Put'}` : `Buy ${trade.symbol}${trade.instrument === 'stock' ? ' shares' : ''}`;
  const plan = trade.plan || {};
  return `<h3>${esc(label)}</h3><p class="hint">${esc(trade.symbol)} · Quote ${esc(dateTime(trade.quote_at))}${Date.now() - Date.parse(trade.quote_at) > 45000 ? ' · Quote stale — recheck before trading' : ''}</p><p>${option ? `Reference ask ${money(trade.premium_per_share)} · ${money(trade.cost_per_contract)} per contract` : `Reference ask ${money(trade.entry_reference)} per ${trade.instrument === 'bitcoin' ? 'BTC' : 'share'}`}</p><p>${esc(trade.selection_reason || '')}</p><dl class="trade-plan"><dt>Entry condition</dt><dd>${esc(plan.entry || 'Recheck the thesis and current quote before considering entry.')}</dd><dt>Exit / take profit</dt><dd>${esc(plan.exit || 'No evidence-based exit was established; define it before entering.')}</dd><dt>Exit if invalidated</dt><dd>${esc(plan.invalidation || 'Close or reassess if the supporting thesis fails.')}</dd><dt>Review by</dt><dd>${esc(trade.review_by || 'No review date established.')}${option ? ` · Close or reassess before expiration on ${esc(trade.expiration)}; do not rely on automatic exercise.` : ''}</dd></dl><p class="hint">${esc(trade.reason || '')} ${esc(trade.risk || '')}</p><button type="button" class="text-button" data-copy-trade="${esc(trade.symbol)}">Copy trade symbol</button>`;
}
function renderThesis(r) {
  const result = $('#idea-result');
  if (r.status === 'idle') { patch(result, '<div class="empty"><h3>Research an idea or find a trade</h3><p>Describe a theme, ask for an opportunity, or provide a specific trade. The agent can choose the ticker and instrument.</p></div>'); return; }
  if (r.status === 'running') { patch(result, '<div class="alert">Researching price context, dated catalysts and risks for your proposed trade. Specific contracts will be checked against market data where available.</div>'); return; }
  if (r.status !== 'complete') { patch(result, `<div class="alert">${esc(r.message || 'No assessment available.')}</div>`); return; }
  const trade = r.specific_trade || {};
  const concrete = tradePlanCard(trade);
  const verdictClass = r.verdict === 'favorable' || r.verdict === 'cautiously-favorable' ? 'verdict-yes' : r.verdict === 'unfavorable' ? 'verdict-no' : '';
  patch(result, `<article class="panel review-result"><div class="review-verdict ${verdictClass}">${esc(r.verdict.replaceAll('-', ' ').toUpperCase())}<span>${esc(r.horizon)}</span></div><p>${esc(r.summary)}</p><p class="hint">Interpreted as: ${esc(r.interpretation)}</p><div class="review-columns"><div><h3>Supporting research</h3>${list(r.thesis, 'None returned.')}<h3>What would change this view</h3>${list(r.invalidation, 'None returned.')}</div><div><h3>Risks</h3>${list(r.risks, 'None returned.')}<h3>Missing information</h3>${list(r.missing, 'None noted.')}</div></div><h3>Specific trade candidate</h3>${concrete}<h3>Research sources</h3>${sourceLinks(r.sources)}<p class="hint">Assessed ${esc(new Date(r.checked_at).toLocaleString())}. The verdict assesses the thesis, not a probability of profit or approval to execute.</p></article>`);
}

// ---------- Research room ----------
const LANES = ['News & filings', 'Social sentiment', 'Macro & catalysts'];
function evidenceCard(e) {
  const published = dateTime(e.published_at);
  const sentiment = {bullish:'Bullish', bearish:'Bearish', mixed:'Mixed'}[e.sentiment] || 'Not established';
  const sentimentClass = ['bullish', 'bearish', 'mixed'].includes(e.sentiment) ? e.sentiment : 'dim';
  return `<article class="evidence"><div class="meta">
    <span class="meta-item"><span class="meta-key">Agent</span><b>${esc(e.lane)}</b></span>
    <span class="meta-item ${e.symbol === 'MARKET' ? 'dim' : ''}"><span class="meta-key">Applies to</span><b>${e.symbol === 'MARKET' ? 'Market-wide' : esc(e.symbol)}</b></span>
    <span class="meta-item ${sentimentClass}"><span class="meta-key">Sentiment</span><b>${sentiment}</b></span>
    <span class="meta-item ${published ? '' : 'warn'}"><span class="meta-key">Published</span><b>${published ? esc(published) : 'Unverified'}</b></span>
    ${e.event_risk ? '<span class="meta-item warn"><span class="meta-key">Flag</span><b>Event risk</b></span>' : ''}</div>
    <a href="${url(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)} ↗</a><p>${esc(e.summary)}</p>
    ${!e.fresh ? '<p class="hint">Shown for context only: the publication time could not be verified as within the last 24 hours, so this source does not feed live signals.</p>' : ''}</article>`;
}
function renderResearch(s) {
  const researchBlocker = s.ai_paused ? 'AI is paused.' : !s.running ? 'Start a session first.' : !s.config.research ? 'AI research is disabled for this session.' : !s.ai_settings?.web_research ? 'Requires a provider with web search.' : s.calls_used + 3 > s.config.research_calls ? 'This session’s AI request cap is reached.' : null;
  $('#refresh-research').disabled = s.researching || !!researchBlocker;
  $('#refresh-research').title = researchBlocker || 'Starts a cycle now; the automatic schedule is unchanged.';
  setText($('#research-state'), s.researching ? 'Agents researching…' : researchBlocker && !s.reports.length ? researchBlocker : `${s.reports.length} live research reports`);
  patch($('#agents'), LANES.map(name => {
    const r = s.reports.find(r => r.lane === name), stale = r && Date.now() - Date.parse(r.at) > 1200000;
    const status = stale ? 'STALE — EXCLUDED' : r ? r.status : s.researching ? 'Researching' : 'Awaiting research';
    const pillClass = stale || r?.status === 'unavailable' ? 'bad' : r?.status === 'no verified evidence' ? 'warn' : '';
    return `<div class="agent"><h3>${name}</h3><span class="pill ${pillClass}">${esc(status)}</span><p class="hint">${esc(r ? r.coverage : 'Runs autonomously when AI research is enabled.')}</p>${r ? `<p class="hint">Report ${time(r.at)}</p>
      <p class="agent-conclusion"><strong>Intraday</strong>${esc(r.conclusion || 'No conclusion: this lane did not support a defensible synthesis.')}</p>
      <p class="agent-conclusion"><strong>Weeks to months</strong>${esc(r.long_term_conclusion || 'No longer-term conclusion returned by this report.')}</p>` : ''}</div>`;
  }).join(''));
  const evidence = s.reports.flatMap(r => r.evidence || []);
  setText($('#source-count'), String(evidence.length));
  const filter = $('#evidence-filter'), symbols = [...new Set(evidence.map(e => e.symbol))].sort((a, b) => (a === 'MARKET') - (b === 'MARKET') || a.localeCompare(b));
  const current = filter.value;
  if (patch(filter, '<option value="">All symbols</option>' + symbols.map(x => `<option value="${esc(x)}">${x === 'MARKET' ? 'Market-wide' : esc(x)}</option>`).join('')))
    filter.value = symbols.includes(current) ? current : '';
  const shown = filter.value ? evidence.filter(e => e.symbol === filter.value) : evidence;
  patch($('#evidence'), shown.length ? shown.map(evidenceCard).join('') : evidence.length ? '<p class="hint">No evidence for this symbol in the current reports.</p>' : '<div class="empty"><h3>No sourced evidence yet.</h3><p>Configure an AI provider and start a session with research enabled.<br>Unavailable social channels will be reported explicitly.</p></div>');
  renderLongResearch(s);
  renderNews(s);
}
$('#evidence-filter').onchange = () => state && renderResearch(state);

// Manual research: starts a cycle now without changing the automatic schedule.
$('#refresh-research').onclick = () => post('/api/research/refresh').then(() => toast('Research cycle started. Reports update as each agent finishes.')).catch(e => toast(e.message));
let longBriefDirty = false;
const LONG_PRESETS = {
  overlooked: 'Look beyond the most popular stocks for overlooked beneficiaries of economic, policy and industry changes. Compare several sectors and include ETFs where appropriate.',
  catalysts: 'Find companies with verifiable upcoming catalysts within my horizon. Explain timing, what is already priced in, downside if the catalyst disappoints and what confirmation to wait for.',
  resilient: 'Find financially resilient businesses or diversified ETFs with durable demand. Compare balance sheets, valuation and downside; avoid leveraged products and binary-event bets.'
};
$$('[data-long-preset]').forEach(button => button.onclick = () => {
  $('#long-focus').value = LONG_PRESETS[button.dataset.longPreset];
  $('#long-focus').dispatchEvent(new Event('input'));
  $('#long-focus').focus();
});
document.addEventListener('click', e => {
  const button = e.target.closest('[data-explore-long]');
  if (!button) return;
  const report = state?.long_research?.[0];
  const view = report?.views?.[Number(button.dataset.exploreLong)];
  if (!view) return;
  $('[data-tab="idea-check"]').click();
  setIdeaMode('thesis');
  $('#idea-text').value = `Evaluate a ${view.direction} thesis for ${view.symbol} over ${view.horizon || `${report.brief?.long_months || 6} months`}, with ${report.brief?.long_risk || 'balanced'} risk tolerance. Research premise: ${view.summary}\nVerify the premise against current sources, assess alternatives and suggest a specific trade only if supported.`;
  $('#idea-text').focus();
  toast('Idea prepared. Review it, then submit when ready.');
});
for (const id of ['long-risk', 'long-months', 'long-focus']) $( '#' + id).addEventListener('input', () => {
  longBriefDirty = true;
  setText($('#long-brief-status'), 'Unsaved changes');
});
async function saveLongBrief() {
  await post('/api/research/long/settings', {long_risk:$('#long-risk').value, long_months:Number($('#long-months').value), long_focus:$('#long-focus').value});
  longBriefDirty = false;
  setText($('#long-brief-status'), 'Saved');
}
$('#save-long-brief').onclick = () => saveLongBrief().catch(e => toast(e.message));
$('#refresh-long-research').onclick = async () => {
  $('#refresh-long-research').disabled = true;
  try {
    if (longBriefDirty) await saveLongBrief();
    await post('/api/research/long');
    toast('Long-horizon research started.');
  } catch (e) { toast(e.message); }
};
$('#long-term-toggle').onchange = e => post('/api/research/long/toggle', {enabled:e.target.checked}).catch(err => { toast(err.message); e.target.checked = !!state?.config.long_term_research; });

function renderLongResearch(s) {
  if (!longBriefDirty) {
    $('#long-risk').value = s.config.long_risk || 'balanced';
    $('#long-months').value = s.config.long_months || 6;
    $('#long-focus').value = s.config.long_focus || '';
  }
  $('#save-long-brief').disabled = !!s.long_researching;
  $('#long-term-toggle').checked = !!s.config.long_term_research;
  const report = (s.long_research || [])[0];
  const blocker = s.ai_paused ? 'AI is paused.' : !s.running ? 'Start a session first.' : !s.ai_settings?.web_research ? 'Requires a provider with web search.' : !s.config.research ? 'AI research is disabled for this session.' : null;
  $('#refresh-long-research').disabled = s.long_researching || !!blocker;
  $('#refresh-long-research').title = blocker || '';
  setText($('#long-research-state'), s.long_researching ? 'Researching…' : report ? `Last report ${time(report.at)} · ${report.status}` : blocker || 'No long-horizon report yet.');
  let html = '';
  if (report?.views?.length) {
    html = report.views.map((v, index) => `<article class="long-view"><h3>${v.symbol === 'MARKET' ? 'Market-wide' : esc(v.symbol)} · <span class="${v.direction === 'bullish' ? 'verdict-yes' : v.direction === 'bearish' ? 'verdict-no' : ''}">${esc(v.direction.replaceAll('-', ' '))}</span></h3><p class="hint">${esc(v.horizon || 'Multi-week horizon')}</p><p>${esc(v.summary)}</p><div class="columns"><div><h4 class="hint">Catalysts to watch</h4>${list(v.catalysts, 'None dated.')}</div><div><h4 class="hint">What would undermine it</h4>${list(v.risks, 'None returned.')}</div></div>${sourceLinks(v.sources || [])}<button type="button" class="text-button" data-explore-long="${index}">Explore in Idea Check →</button></article>`).join('');
    html += report.views.map(v => `<article class="long-view"><h3>${esc(v.symbol)} · Specific trade plan</h3>${tradePlanCard(v.specific_trade)}</article>`).join('');
    html += `<p class="hint">${esc(report.coverage)}</p>` + sourceLinks(report.evidence);
    html += '<p class="hint">Long-horizon views are informational. They never unlock, veto or size an intraday entry.</p>';
  } else if (report) {
    html = `<p class="hint">${esc(report.coverage || 'No long-horizon view was supported by the available sources.')}</p>`;
  } else if (s.config.long_term_research) {
    html = '<p class="hint">The next research cycle will include one long-horizon request, or run it now with the button above.</p>';
  }
  if (report?.brief) html = `<p class="hint">Report brief: ${esc(report.brief.long_risk)} · ${esc(report.brief.long_months)} months${report.brief.long_focus ? ` · ${esc(report.brief.long_focus)}` : ' · Open-ended discovery'}</p>` + html;
  patch($('#long-research-results'), html);
}

// News feed: each headline can be handed to an agent on demand. Nothing runs automatically.
function renderNews(s) {
  const analysis = s.news_analysis || {};
  patch($('#news'), s.news.length ? s.news.map((n, index) => {
    const a = analysis[n.headline];
    const busyElsewhere = s.news_analyzing && s.news_analyzing !== n.headline;
    let body = '';
    if (a?.status === 'running') body = '<div class="news-analysis"><p class="hint">An agent is reading this story and related sources…</p></div>';
    else if (a?.status === 'error') body = `<div class="news-analysis"><p class="hint">${esc(a.message)}</p></div>`;
    else if (a?.status === 'complete') body = `<div class="news-analysis">
      <h4>What this establishes · ${esc(a.confidence)} confidence</h4><p>${esc(a.takeaway)}</p>
      ${a.already_priced ? `<h4>Already reflected?</h4><p class="hint">${esc(a.already_priced)}</p>` : ''}
      ${a.affected.length ? `<h4>Who it affects</h4>${a.affected.map(x => `<p class="hint"><b>${esc(x.symbol)}</b> · ${esc(x.effect)} · ${esc(x.why)}</p>`).join('')}` : ''}
      ${a.angles.length ? `<h4>Angles worth researching</h4>${a.angles.map(x => `<div class="angle"><b>${esc(x.idea)}</b>${x.horizon ? ` · ${esc(x.horizon)}` : ''}${x.conditions ? `<br>Needs: ${esc(x.conditions)}` : ''}${x.risks ? `<br>Risks: ${esc(x.risks)}` : ''}</div>`).join('')}` : '<h4>Angles worth researching</h4><p class="hint">The agent did not identify a researchable angle from this story.</p>'}
      ${a.missing.length ? `<h4>Missing information</h4>${list(a.missing, '')}` : ''}
      ${a.sources.length ? `<h4>Sources read</h4>${sourceLinks(a.sources)}` : ''}
      <p class="hint">Advisory research on one headline, assessed ${time(a.checked_at)}. Not an entry signal, price forecast or order.</p></div>`;
    return `<article class="news-item"><div class="news-head"><div class="meta"><span class="meta-item"><span class="meta-key">Source</span><b>${esc(n.source)}</b></span><span class="meta-item"><span class="meta-key">Published</span><b>${esc(dateTime(n.created_at) || n.created_at)}</b></span>${(n.symbols || []).length ? `<span class="meta-item"><span class="meta-key">Tagged</span><b>${esc((n.symbols || []).slice(0, 4).join(', '))}</b></span>` : ''}</div>
      <button type="button" class="text-button" data-analyze="${index}" ${a?.status === 'running' || busyElsewhere ? 'disabled' : ''}>${a?.status === 'running' ? 'Agent working…' : a?.status === 'complete' ? 'Re-run agent · 1 AI request ↗' : 'Ask an agent · 1 AI request ↗'}</button></div>
      <a href="${url(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.headline)} ↗</a>${body}</article>`;
  }).join('') : '<p class="hint">No headlines retrieved. This does not mean there is no relevant news.</p>');
}
document.addEventListener('click', async e => {
  const button = e.target.closest('[data-analyze]');
  if (!button) return;
  const article = state?.news?.[Number(button.dataset.analyze)];
  if (!article) return;
  button.disabled = true;
  try { await post('/api/news/analyze', {article}); }
  catch (err) { toast(err.message); button.disabled = false; }
});

// ---------- Paper journal ----------
function renderJournal(s) {
  const j = s.journal_summary || {closed:0, wins:0, net:0, open:0, reserved:0, unrealized:null};
  setText($('#trade-count'), String(j.open || 0));
  patch($('#journal-summary'), `<div><span>Open paper positions</span><strong>${j.open || 0}</strong></div><div><span>Capital reserved</span><strong>${money(j.reserved || 0)}</strong></div><div><span>Open estimated P/L</span><strong class="${j.unrealized === null ? '' : pnlClass(j.unrealized)}">${j.unrealized === null ? (j.open ? 'awaiting fresh quotes' : '—') : signedMoney(j.unrealized)}</strong></div><div><span>Closed estimates · realized</span><strong class="${pnlClass(j.net)}">${j.closed} · ${signedMoney(j.net)}</strong></div>`);
  patch($('#trades'), s.trades.length ? s.trades.map(t => `<article class="trade"><div class="trade-head"><div><strong>${esc(t.symbol)}</strong> <span class="pill">${esc(t.status.toUpperCase())}${t.asset === 'option' ? ' · OPTION' : ''}</span></div>${t.status === 'open' ? `<button class="secondary" data-close="${esc(t.id)}">Close paper estimate</button>` : ''}</div>
    <p class="hint">${esc(t.quantity)} × ${money(t.entry)} ${t.asset === 'option' ? '× 100' : ''} · capital ${money(t.notional)} · risk reserved ${money(t.risk)} · stop ${money(t.stop)} · target ${money(t.target)}<br>Opened ${esc(new Date(t.opened_at).toLocaleString())}${t.closed_at ? ' · closed ' + esc(new Date(t.closed_at).toLocaleString()) : ''}</p>
    <p>${t.status === 'closed' ? `Estimated P/L <span class="pnl ${pnlClass(t.pnl)}">${signedMoney(t.pnl)}</span> · exit ${money(t.exit)}` : `${esc(t.mark_status)} · ${t.marked_at && Date.now() - Date.parse(t.marked_at) < 45000 ? 'estimated open P/L <span class="pnl ' + pnlClass(t.unrealized) + '">' + signedMoney(t.unrealized) + '</span> at ' + money(t.mark) : 'fresh valuation unavailable'}`}</p></article>`).join('')
    : '<div class="empty"><h3>No paper ideas are being tracked.</h3><p>Select Track on paper on an idea to monitor its hypothetical outcome.</p></div>');
  const wins = j.closed ? ` · ${(j.wins / j.closed * 100).toFixed(0)}% positive` : '';
  setText($('#export'), `Export research & journal${wins} ↗`);
}
$('#export').onclick = async () => {
  try {
    const response = await fetch('/api/export');
    if (!response.ok) throw Error('Export failed');
    const blob = new Blob([JSON.stringify(await response.json(), null, 2)], {type:'application/json'});
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'daylight-journal.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (e) { toast(e.message); }
};

// ---------- My positions ----------
let lastDraft = '', fillSubmission = crypto.randomUUID();
$('#auto-positions').onchange = async e => { try { await post('/api/positions/auto', {enabled:e.target.checked}); } catch (err) { toast(err.message); e.target.checked = state.config.auto_position_review; } };
$('#review-positions').onclick = () => post('/api/positions/review').catch(e => toast(e.message));
$('#translate-form').onsubmit = async e => { e.preventDefault(); $('#translate-message').textContent = ''; try { await post('/api/fills/preview', {text:$('#fill-text').value}); } catch (err) { $('#translate-message').textContent = err.message; } };
$('#fill-form').onsubmit = async e => {
  e.preventDefault(); $('#fill-error').textContent = ''; $('#save-fill').disabled = true;
  const fill = {asset:$('#fill-asset').value, side:$('#fill-side').value, symbol:$('#fill-symbol').value.toUpperCase(), quantity:Number($('#fill-quantity').value), price:Number($('#fill-price').value), stop:$('#fill-stop').value ? Number($('#fill-stop').value) : null, executed_at:$('#fill-time').value || null, notes:$('#fill-notes').value};
  try { await post('/api/fills', {fill, request_id:fillSubmission}); fillSubmission = crypto.randomUUID(); $('#fill-form').reset(); toast('Reported fill saved. No brokerage order sent.'); }
  catch (err) { $('#fill-error').textContent = err.message; } finally { $('#save-fill').disabled = false; }
};
function renderPositions(s) {
  $('#auto-positions').checked = !!s.config.auto_position_review;
  $('#review-positions').disabled = s.ai_paused || s.position_review?.status === 'running';
  $('#translate-fill').disabled = s.ai_paused || s.fill_draft?.status === 'running';
  const draft = s.fill_draft || {status:'idle'};
  if (draft.status === 'running') setText($('#translate-message'), 'Translating. The fields below remain editable for manual entry.');
  else if (draft.status === 'error') setText($('#translate-message'), draft.message);
  else if (draft.status === 'ready' && draft.id !== lastDraft) {
    lastDraft = draft.id; fillSubmission = crypto.randomUUID();
    for (const [key, id] of Object.entries({symbol:'symbol', asset:'asset', side:'side', quantity:'quantity', price:'price', stop:'stop', executed_at:'time', notes:'notes'})) $('#fill-' + id).value = draft.draft[key] ?? '';
    setText($('#translate-message'), 'Review the translated fields and fill in anything missing before confirming.');
  }
  const sum = s.manual_summary;
  patch($('#positions-summary'), sum && (sum.open || sum.realized) ? `<div><span>Open logged positions</span><strong>${sum.open}</strong></div><div><span>Cost basis</span><strong>${money(sum.cost_basis)}</strong></div><div><span>Unrealized · fresh marks only</span><strong class="${sum.unrealized === null ? '' : pnlClass(sum.unrealized)}">${sum.unrealized === null ? (sum.open ? 'awaiting fresh quotes' : '—') : signedMoney(sum.unrealized)}</strong></div><div><span>Realized from logged sales</span><strong class="${pnlClass(sum.realized)}">${signedMoney(sum.realized)}</strong></div>` : '');
  const positions = s.manual_positions || [];
  patch($('#manual-positions'), positions.length ? positions.map(p => `<article class="trade"><div class="trade-head"><strong>${esc(p.symbol)}</strong><span class="pill ${p.mark_state === 'unusable' ? 'bad' : p.mark_state === 'reference' ? 'warn' : ''}">USER-REPORTED · ${esc(p.asset)}${p.mark_state === 'reference' ? ' · LAST CLOSE' : ''}</span></div><p>${p.quantity.toLocaleString('en-US', {maximumFractionDigits:8})} units · average fill ${money(p.average_price)}${p.asset === 'option' ? ' × 100 shares/contract' : ''}</p><p class="hint">Cost basis ${money(p.cost_basis)} · ${p.mark === null ? 'Valuation unavailable' : (p.mark_state === 'reference' ? 'last-close mark ' : 'mark ') + money(p.mark) + ' · unrealized <span class="pnl ' + pnlClass(p.unrealized_pnl) + '">' + signedMoney(p.unrealized_pnl) + '</span>'}<br>Planned stop ${p.stop ? money(p.stop) : 'not logged'} · ${esc(p.data_status)}</p></article>`).join('')
    : '<div class="empty"><h3>Your real trades, in one place.</h3><p>Translate a fill or enter its fields manually.<br>Nothing here places an order.</p></div>');
  const review = s.position_review || {status:'idle'};
  let html = review.message ? `<p class="hint">${esc(review.message)}</p>` : '';
  if (review.status === 'running') html = '<div class="alert">Position analyst and risk reviewer are assessing your positions. Follow them under Activity.</div>';
  else if (review.status === 'complete') html = review.results.map(r => {
    // A last-close reference price is expected outside market hours and does not invalidate the review.
    const reference = r.quote_state === 'reference';
    const stale = !s.running || Date.now() - Date.parse(r.checked_at) > 120000
      || (!reference && Date.now() - Date.parse(r.quote_at) > 45000) || r.quote_state === 'unusable';
    const label = stale ? 'assessment or quote is stale' : reference ? 'closed session · last-close reference' : r.agreement ? 'agents agree' : 'no consensus';
    return `<article class="panel review-result"><div class="review-verdict ${r.action === 'HOLD' ? 'verdict-yes' : 'verdict-no'}">${stale ? 'RECHECK' : esc(r.action)} <span>${esc(r.symbol)} · ${label}</span></div><p class="hint">${time(r.checked_at)} · advisory only; verify in your broker before acting.</p>${r.checks.map(c => `<p class="hint">${esc(c)}</p>`).join('')}<div class="review-columns">${r.votes.map(v => `<div><h3>${esc(v.agent)} · ${esc(v.action)}</h3><p class="hint">${esc(v.summary)}</p>${list(v.factors, 'No factors returned.')}</div>`).join('')}</div></article>`;
  }).join('');
  patch($('#position-review'), html);
  patch($('#fill-history'), (s.manual_fills || []).map(f => `<div class="reject plain"><strong>${esc(f.side.toUpperCase())} ${esc(f.symbol)}</strong><span>${f.quantity} at ${money(f.price)} · ${f.executed_at ? esc(f.executed_at) : 'fill time not provided'}<br>Recorded ${esc(new Date(f.recorded_at).toLocaleString())}</span></div>`).join('') || '<p class="hint">No fills logged yet.</p>');
}

// ---------- Activity / debug ----------
let debugPaused = false, debugSnapshot = null;
$('#debug-pause').onclick = () => { debugPaused = !debugPaused; setText($('#debug-pause'), debugPaused ? 'Resume view' : 'Pause view'); renderDebug(state); };
$('#debug-filter').onchange = () => renderDebug(state);
$('#debug-collapse').onchange = () => renderDebug(state);
$('#debug-export').onclick = () => {
  const blob = new Blob([JSON.stringify(debugSnapshot || {events:[], workers:{}}, null, 2)], {type:'application/json'});
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'daylight-activity.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
};
const RESEARCH_COMPONENTS = ['Research', ...LANES, 'Long-term outlook', 'Thesis review'];
const DECISION_COMPONENTS = ['Price action', 'Decision', 'Signal checks', 'Market session'];
function renderDebug(s) {
  if (!s) return;
  if (!debugPaused) debugSnapshot = s.debug || {events:[], workers:{}};
  const d = debugSnapshot || {events:[], workers:{}};
  setText($('#debug-status'), debugPaused ? 'View paused · session continues' : 'Live summaries · refreshes every 3 seconds');
  patch($('#category-health'), Object.values(s.data_categories || {}).map(c => `<div class="category-item ${esc(c.status)}"><strong>${esc(c.label)}</strong><span>${esc(c.status)}</span><small>${esc(c.detail)}</small></div>`).join(''));
  patch($('#debug-workers'), Object.entries(d.workers).map(([name, w]) => `<article class="debug-worker"><div><strong>${esc(name)}</strong><span class="pill ${w.status === 'error' ? 'bad' : w.status === 'warning' ? 'warn' : ''}">${esc(w.status)}</span></div><p>${esc(w.message)}</p><small>${time(w.at)}</small></article>`).join(''));
  const feeds = s.feed_health || [];
  patch($('#feed-health'), feeds.length ? feeds.map(f => `<div class="reject plain"><strong>${esc(f.symbol)}</strong><span>${esc(f.feed)} · ${esc(f.status)} · quote ${f.quote_age === null ? 'age unknown' : f.quote_age + 's old'} · ${f.candles} candles<br>${esc(f.detail)}</span></div>`).join('') : '<p class="hint">No feed observations yet.</p>');
  const filter = $('#debug-filter').value;
  let events = [...d.events].reverse().filter(e => filter === 'all' || (filter === 'research' && RESEARCH_COMPONENTS.includes(e.component)) || (filter === 'decisions' && DECISION_COMPONENTS.includes(e.component)) || (filter === 'problems' && ['error', 'warning'].includes(e.status)));
  if ($('#debug-collapse').checked) {
    // Identical component/status/message repeats collapse into the newest occurrence with a count.
    const groups = new Map();
    for (const e of events) {
      const key = `${e.component}|${e.status}|${e.message}`;
      if (groups.has(key)) { const g = groups.get(key); g.count++; g.since = e.at; } else groups.set(key, {...e, count:1, since:e.at});
    }
    events = [...groups.values()];
  }
  const open = new Set($$('.debug-event details[open]').map(e => e.dataset.event));
  patch($('#debug-events'), events.length ? events.map(e => `<article class="debug-event ${e.status === 'error' ? 'debug-error' : e.status === 'warning' ? 'debug-warning' : ''}"><div class="debug-meta"><time datetime="${esc(e.at)}">${time(e.at)}</time><strong>${esc(e.component)}</strong>${e.count > 1 ? `<span class="repeat">×${e.count} since ${time(e.since)}</span>` : ''}<span class="status">${esc(e.status)}</span></div><p>${esc(e.message)}</p>${Object.keys(e.details || {}).length ? `<details data-event="${e.id}" ${open.has(String(e.id)) ? 'open' : ''}><summary>Details</summary><pre>${esc(JSON.stringify(e.details, null, 2))}</pre></details>` : ''}</article>`).join('')
    : '<div class="empty"><h3>No matching activity yet.</h3><p>Start a session to see requests and decision summaries here.</p></div>');
}

// ---------- Connections (sidebar lights) ----------
function renderConnections(s) {
  const markup = Object.values(s.data_categories || {}).map(c => {
    const meaning = c.status === 'available' ? 'Green: usable data received' : c.status === 'waiting' ? 'Yellow: waiting, paused or not currently applicable' : 'Red: unavailable or failed freshness checks';
    return `<div class="connection-feed"><span>${esc(c.label)}</span><button type="button" class="feed-light ${esc(c.status)}" aria-label="${esc(c.label + ': ' + meaning)}" data-help="${esc(meaning + '. ' + c.detail)}"></button></div>`;
  }).join('');
  if (patch($('#connection-feeds'), markup))
    $$('#connection-feeds [data-help]').forEach(b => { b.onmouseenter = () => openHelp(b); b.onmouseleave = () => { if (document.activeElement !== b) closeHelp(); }; b.onfocus = () => openHelp(b); b.onblur = closeHelp; b.onclick = () => openHelp(b); });
}

// ---------- AI settings ----------
let providerLoaded = false;
function providerFields() {
  const p = state?.ai_settings?.providers.find(p => p.id === $('#provider').value);
  $('#provider-model').value = p?.model || ''; $('#provider-key').value = ''; $('#provider-clear').checked = false;
  $('#local-url-row').hidden = $('#provider').value !== 'local';
  $('#provider-capability').textContent = p?.web_research
    ? `Native web research is available only on compatible models with the required account access. ${p.key_configured ? 'A key is saved for this provider.' : 'No key is saved for this provider yet.'} Model IDs and entitlements are checked on the next AI request, or use the test below.`
    : 'Local inference can assess collected market data. This adapter has no autonomous web search, so required research can withhold entry ideas.';
}
$('#provider').onchange = providerFields;
$('#provider-form').onsubmit = async e => {
  e.preventDefault(); $('#provider-message').textContent = '';
  try {
    await post('/api/settings/ai', {provider:$('#provider').value, model:$('#provider-model').value, api_key:$('#provider-key').value, clear_key:$('#provider-clear').checked, local_url:$('#provider-url').value});
    $('#provider-key').value = ''; $('#provider-clear').checked = false;
    $('#provider-message').textContent = 'Saved. Resume AI when ready, or send a test request below to verify the credential now.';
  } catch (err) { $('#provider-message').textContent = err.message; }
};
$('#test-provider').onclick = async () => { $('#ai-test-result').textContent = ''; try { await post('/api/settings/ai/test'); } catch (err) { $('#ai-test-result').textContent = err.message; } };
function renderSettings(s) {
  const settings = s.ai_settings;
  if (!settings) return;
  setText($('#provider-current'), settings.label + ' · ' + settings.model);
  if (!providerLoaded) { $('#provider').value = settings.provider; $('#provider-url').value = settings.local_url; providerFields(); providerLoaded = true; }
  patch($('#provider-list'), settings.providers.map(p => `<div class="provider-item ${p.id === settings.provider ? 'selected' : ''}"><strong>${esc(p.label)}${p.id === settings.provider ? ' · selected' : ''}</strong><span class="muted">Model: ${esc(p.model || 'not set')}</span><span class="muted">${p.id === 'local' ? 'Key optional' : p.key_configured ? 'Key saved' : 'No key saved'} · ${p.web_research ? 'web search' : 'no web search'}</span></div>`).join(''));
  $$('#provider option').forEach(o => { const p = settings.providers.find(p => p.id === o.value); if (p) o.textContent = `${p.label}${p.key_configured ? ' · key saved' : ''}`; });
  const canSave = s.ai_paused && !s.ai_inflight;
  $('#save-provider').disabled = !canSave;
  setText($('#save-hint'), canSave ? '' : s.ai_paused ? 'Waiting for in-flight AI requests to finish before changes can be saved.' : 'Use “Pause all AI” in the header to unlock saving; this prevents a provider switch mid-request.');
  const t = s.ai_test || {status:'idle'};
  $('#test-provider').disabled = t.status === 'running' || !settings.configured;
  setText($('#test-provider'), t.status === 'running' ? 'Testing…' : 'Send test request');
  const result = $('#ai-test-result');
  if (t.status === 'running') setText(result, `Contacting ${t.provider} · ${t.model}…`);
  else if (t.status === 'ok' || t.status === 'error') { setText(result, `${t.status === 'ok' ? '✓ ' : '✗ '}${t.message} (${time(t.checked_at)})`); result.style.color = t.status === 'ok' ? 'var(--accent)' : 'var(--red)'; }
  else if (!settings.configured) setText(result, 'Save a provider, model and credential first.');
  else if (!result.textContent.startsWith('✓') && !result.textContent.startsWith('✗')) setText(result, '');
}

// ---------- Help popups (hover/focus to read, tap to toggle, Escape to dismiss) ----------
const HELP = {
  risk:'Limit 1 of 3. The most a single idea is allowed to lose, as a percentage of your budget, which decides how many shares, contracts or BTC an idea is sized for. A stock or Bitcoin idea uses the entry-to-stop distance, so a $2 stop on a 1% / $10,000 budget allows 50 shares. A long option reserves its entire premium, because the premium is what you can lose, so one $1.20 contract needs $120 of this allowance. Set it too low and option ideas can never be sized at all. Conventional discretionary risk is 1-2%; the range here goes to 25% because small accounts cannot fund a single contract otherwise. Actual losses can exceed a stop estimate.',
  loss:"Limit 2 of 3. The total this session may lose across every idea: this percentage of your budget, minus today's realized paper losses and the risk already reserved by open paper positions. At 3% of $10,000 it starts at $300, so three ideas risking $100 each exhaust it. It must be at least the per-idea risk. The day resets at midnight UTC. Reaching it stops new ideas; it never closes an open position for you.",
  'max-position':'Limit 3 of 3, and the one most often responsible for an idea that never appears. No single position may use more than this percentage of your budget in cash, regardless of how tight its stop is. At 25% of $10,000 that is $2,500, so a $650 share price allows at most 3 shares even if your risk allowance would permit more. Lower it to force diversification; raise it toward 100% if you deliberately want to concentrate on one position at a time.',
  budget:'The amount you choose to allocate to this workspace. Used for suggestion sizing; it is not your broker balance or verified buying power.',
  symbols:'Stock tickers to monitor. For options, an underlying is the stock the contract is based on. Option discovery scans the first three tickers; Bitcoin is selected separately.',
  'research-interval':'How often the three web-research agents run while the session is active. Market scans run separately, about once per minute. Optional automatic position reviews use this cadence too.',
  'research-calls':'Maximum background web-research requests per session. Each cycle uses three requests, so 36 allows 12 cycles. Other AI features have separate limits and all share the 120-request UTC-day cap. Requests are not tokens or dollars; failures count.',
  'fill-symbol':'Stocks use a ticker such as AAPL; Bitcoin uses BTC/USD. Options require the full contract identifier, including underlying, expiration, call/put and strike, as shown by your broker.',
  'fill-quantity':'Number of shares, Bitcoin units, or option contracts you actually traded. Options must be whole contracts; one standard contract represents 100 shares.',
  'fill-price':'Actual execution price for one share or one Bitcoin. For options, enter premium per share: a $2.50 fill costs $250 per standard contract.',
  'fill-stop':'Your planned exit price if the trade goes against you. This is only a planning reference; saving it does not place a stop order.',
  'provider-model':'The exact API model identifier from the selected provider, or the loaded model ID in LM Studio. A product subscription name is not a model ID.',
};
const helpPopup = document.createElement('div');
helpPopup.id = 'help-popup'; helpPopup.className = 'help-popup'; helpPopup.role = 'tooltip'; helpPopup.hidden = true;
document.body.append(helpPopup);
let helpOwner = null;
function closeHelp() { helpOwner?.removeAttribute('aria-describedby'); helpOwner = null; helpPopup.hidden = true; }
function openHelp(button) {
  closeHelp(); helpOwner = button; button.setAttribute('aria-describedby', helpPopup.id);
  helpPopup.textContent = button.dataset.help; helpPopup.hidden = false;
  const box = button.getBoundingClientRect();
  helpPopup.style.left = Math.max(8, Math.min(box.left, innerWidth - helpPopup.offsetWidth - 8)) + 'px';
  helpPopup.style.top = Math.max(8, box.bottom + helpPopup.offsetHeight + 10 < innerHeight ? box.bottom + 8 : box.top - helpPopup.offsetHeight - 8) + 'px';
}
function addHelp(target, text, name) {
  if (!target || !text || target.querySelector('.help-button')) return;
  const button = document.createElement('button'); button.type = 'button'; button.className = 'help-button';
  button.textContent = '?'; button.dataset.help = text; button.setAttribute('aria-label', 'Help: ' + name);
  button.onmouseenter = () => openHelp(button);
  button.onmouseleave = () => { if (document.activeElement !== button && !helpPopup.matches(':hover')) closeHelp(); };
  button.onfocus = () => openHelp(button); button.onblur = closeHelp;
  button.onpointerdown = e => { button.wasOpen = e.pointerType === 'touch' && helpOwner === button && document.activeElement === button; };
  button.onclick = e => { e.preventDefault(); button.wasOpen ? closeHelp() : openHelp(button); button.wasOpen = false; };
  target.append(button);
}
Object.entries(HELP).forEach(([id, text]) => { const label = document.querySelector(`label[for="${id}"]`); addHelp(label, text, label?.firstChild?.textContent.trim() || id); });
// Any heading or label carrying data-tip gets the same "?" affordance, so caveats live one hover
// away instead of as a paragraph above every section.
$$('[data-tip]').forEach(el => {
  const text = el.dataset.tip;
  el.removeAttribute('data-tip');
  addHelp(el, text, el.textContent.trim());
});
[['available', "Paper capital left after reserving open paper positions and subtracting today's paper losses. Reported actual trades are a separate ledger; verify your broker buying power."],
 ['risk-left', HELP.loss], ['calls', HELP['research-calls']]].forEach(([id, text]) => { const label = document.getElementById(id)?.previousElementSibling; addHelp(label, text, label?.textContent); });
helpPopup.onmouseleave = () => { if (document.activeElement !== helpOwner) closeHelp(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHelp(); });
document.addEventListener('click', e => { if (!e.target.closest('.help-button, .help-popup, .feed-light')) closeHelp(); });
window.addEventListener('resize', closeHelp);
document.addEventListener('scroll', closeHelp, true);

// ---------- Render everything, then poll ----------
function render() {
  const s = state;
  renderHeader(s);
  renderConnections(s);
  renderDesk(s);
  renderIdeaCheck(s);
  renderResearch(s);
  renderChat(s);
  renderJournal(s);
  renderPositions(s);
  renderDebug(s);
  renderSettings(s);
}
setInterval(() => {
  setText($('#clock'), new Date().toLocaleTimeString('en-US', {timeZone:'America/New_York', hour:'2-digit', minute:'2-digit', second:'2-digit'}) + ' ET');
  if (!state || !connected) return;
  if (state.running) setText($('#next-scan'), `Next scan ${relative(state.next_scan) || time(state.next_scan)} · ${interval(state.scan_interval || 60)}`);
  $$('[data-open]').forEach(b => {
    const i = state.ideas.find(x => x.id === b.dataset.open);
    if (i && (Date.parse(i.expires_at) <= Date.now() || Date.now() - Date.parse(i.quote.t) > 45000)) { b.disabled = true; b.textContent = 'Expired · awaiting next scan'; }
  });
}, 1000);
refresh();
setInterval(refresh, 3000);
