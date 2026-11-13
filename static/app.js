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
// Locked while running: these sized any open position. Everything else can be retuned live.
const CAPITAL_FIELDS = ['#budget', '#risk', '#loss', '#max-position', '#symbols'];
const LIVE_FIELDS = ['#research', '#require-research', '#research-interval', '#research-calls', '#daily-ai-limit'];
function readLiveSettings() {
  return {research:$('#research').checked, require_research:$('#require-research').checked,
          research_interval:Number($('#research-interval').value), research_calls:Number($('#research-calls').value),
          daily_ai_limit:Number($('#daily-ai-limit').value)};
}
// While a session runs these apply immediately; while stopped they are just form state for Start.
let liveSettingsTimer = null;
function applyLiveSettings() {
  budgetPreview();
  if (!state?.running) return;
  clearTimeout(liveSettingsTimer);
  liveSettingsTimer = setTimeout(async () => {
    try { await post('/api/settings/research', readLiveSettings()); toast('Research settings updated. The session keeps running.'); }
    catch (err) { toast(err.message); if (state) fillConfig(state.config); }
  }, 700);
}
LIVE_FIELDS.forEach(id => $(id).addEventListener('change', applyLiveSettings));

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
  else if (maxPosition < perIdea) note = `Max position size (${money(maxPosition)}) is below your per-idea risk (${money(perIdea)}), so it binds first. Raise it to ${position < 100 ? Math.ceil(risk) + '%' : '100%'} to let one idea actually use that much.`;
  else if (risk >= 100) note = 'One idea may risk the entire budget. There is no diversification left at this setting.';
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
// Stopping withdraws live ideas, so it asks first. Research, plans, the journal and logged
// positions all survive a stop, and the confirmation says so.
$('#stop').onclick = () => { $('#stop-confirm').hidden = false; $('#stop-yes').focus(); };
$('#stop-cancel').onclick = () => { $('#stop-confirm').hidden = true; $('#stop').focus(); };
$('#stop-yes').onclick = async () => {
  $('#stop-yes').disabled = true;
  try { await post('/api/stop'); $('#stop-confirm').hidden = true; }
  catch (e) { toast(e.message); }
  finally { $('#stop-yes').disabled = false; }
};
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#stop-confirm').hidden) $('#stop-cancel').click(); });
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
  $$('[data-tab]').forEach(x => {
    const active = x.dataset.tab === name;
    x.classList.toggle('active', active);
    // Without these a screen reader hears eight unrelated buttons and no current view.
    x.setAttribute('aria-selected', String(active));
    x.setAttribute('tabindex', active ? '0' : '-1');
  });
  $$('.tab-content').forEach(x => x.hidden = x.id !== name);
  store.set('daylight-tab', name);
}
// Arrow keys move between tabs, which is what the tab role promises.
$('.tabs').addEventListener('keydown', e => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  const tabs = $$('[data-tab]'), current = tabs.findIndex(t => t.dataset.tab === store.get('daylight-tab', 'overview'));
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1
    : (current + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  e.preventDefault();
  showTab(tabs[next].dataset.tab);
  tabs[next].focus();
});
$$('[data-tab]').forEach(b => b.onclick = () => showTab(b.dataset.tab));
showTab(store.get('daylight-tab', 'overview'));

// ---------- Header, status and stats ----------
function renderHeader(s) {
  const c = s.config;
  if (firstRender || s.running) { fillConfig(c); firstRender = false; }
  $('#go').hidden = s.running;
  $('#stop-wrap').hidden = !s.running;
  if (!s.running) $('#stop-confirm').hidden = true;
  $('#go').disabled = s.scanning || s.researching;
  // Only the settings an open position was sized against lock while running. Research settings stay
  // editable, because those are exactly what you want to tune while watching it work.
  CAPITAL_FIELDS.forEach(id => { const el = $(id); if (el) el.disabled = s.running; });
  $('#capital-locked').hidden = !s.running;
  setText($('#status'), s.running ? (s.scanning ? 'Scanning the market…' : 'Session running') : 'Session stopped');
  $('#refresh-market').disabled = !s.running || s.scanning || s.market_refresh_requested;
  setText($('#refresh-market'), s.scanning || s.market_refresh_requested ? 'Refreshing market data…' : 'Refresh market data');
  $('#refresh-market').title = !s.running ? 'Start a session to refresh market data.' : 'Refresh quotes, candles, headlines and position marks. Uses existing research; no AI requests.';
  $('#status-dot').classList.toggle('live', s.running);
  setText($('#available'), money(s.available)); setText($('#risk-left'), money(s.risk_left));
  setText($('#last-scan'), time(s.last_scan));
  // One unit everywhere: how many requests remain today. Session and per-button costs are secondary.
  const usage = s.ai_usage || {requests:0, limit:0};
  const remaining = Math.max(0, usage.limit - usage.requests);
  setText($('#calls'), String(remaining));
  setText($('#calls-note'), `of ${usage.limit} today · ${s.calls_used} used this session`);
  renderStatusBanner(s);
  setText($('#ai-status'), s.ai_settings?.configured ? `${s.ai_settings.label} · ${s.ai_settings.model}` : 'Not configured');
  const feeds = s.feed_health || [], fresh = feeds.filter(f => f.status === 'fresh').length;
  setText($('#feed-summary'), `${fresh} / ${feeds.length} feeds with fresh quotes · ${s.running ? 'monitoring' : 'session stopped'}`);
  setText($('#scan-cadence'), s.running ? `Scans ${interval(s.scan_interval || 60)}` : '');
  setText($('#daily-usage'), s.ai_paused ? 'AI paused' : `Budget resets midnight UTC`);
  // AI pause
  setText($('#pause-ai'), s.ai_paused ? 'Resume AI' : 'Pause all AI');
  $('#pause-ai').classList.toggle('paused', !!s.ai_paused);
  $('#ai-pause-note').hidden = !s.ai_paused;
  setText($('#ai-pause-note'), 'AI paused · no new AI requests. Market scans and manual logging continue.' + (s.ai_inflight ? ' Already-sent requests are still finishing and may be billed.' : ''));
}

// The most prominent element on the page has to be the most truthful. It used to be a constant
// green string that stayed green through dead feeds and a stopped session.
function renderStatusBanner(s) {
  const banner = $('#mode-banner'), feeds = s.feed_health || [];
  const fresh = feeds.filter(f => f.status === 'fresh').length;
  const feedNote = `stocks and options from ${SOURCE_NAMES[s.connections.data_source] || 'Alpaca'}`;
  const broken = Object.values(s.data_categories || {})
    .filter(cat => cat.status === 'unavailable').map(cat => cat.label);
  let level, text;
  if (!s.running) {
    level = 'idle';
    text = `Session stopped · nothing is being scanned · ${feedNote}`;
  } else if (!s.last_scan) {
    level = 'idle';
    text = `Session starting · waiting for the first scan · ${feedNote}`;
  } else if (!feeds.length || !fresh) {
    level = 'bad';
    text = `No fresh market data · ${broken.length ? broken.join(', ') + ' unavailable' : 'nothing is reporting'} · ideas are withheld`;
  } else if (fresh < feeds.length || broken.length) {
    level = 'warn';
    text = `Partial data · ${fresh} of ${feeds.length} feeds fresh${broken.length ? ' · ' + broken.join(', ') + ' unavailable' : ''}`;
  } else {
    level = 'live';
    text = `Live market data · ${fresh} of ${feeds.length} feeds fresh · ${feedNote}`;
  }
  if (s.running && !c_requireResearch(s)) text += ' · technical-only ideas permitted';
  banner.className = 'banner ' + level;
  setText(banner, text);
}
const c_requireResearch = s => s.config.require_research;

// ---------- Market desk ----------
const REASONS = [
  ['Quote older than 45 seconds', 'The latest bid/ask is more than 45 seconds old. Ideas are only built on prices that are current right now; this clears when a fresh quote arrives.'],
  ['Missing or invalid bid/ask', 'No usable two-sided quote is available, so there is no reliable price to plan an entry from.'],
  ['No displayed two-sided liquidity', 'Bid or ask size is zero. Without visible liquidity on both sides an entry estimate would be unreliable.'],
  ['Bid/ask spread exceeds', 'The gap between bid and ask is too wide to plan an entry from. On the IEX feed this is usually the feed rather than the stock: IEX publishes only its own order book, so outside heavily-traded names the quote can be far wider than the real national best bid/offer. The entry reference uses the ask, so a fake-wide quote would produce a fake entry price. SIP data would show the consolidated quote.'],
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
    `<div class="empty"><div class="empty-icon">⌁</div><h3>${s.running ? 'No ideas pass the checks yet' : 'No session running'}</h3><p>${s.running ? 'Research, price action and risk limits need to align.<br>The list below explains what each symbol is waiting for.' : 'Set a budget and watchlist, then start a session.<br>Ideas appear here when the checks pass.'}</p></div>`);
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
    hint:'One AI request with web search, counted toward your shared daily allowance. Stock and option contract checks use your market data source separately; missing quotes or entitlements are reported, never guessed.',
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
    setText($('#review-count'), `${Math.max(0, 10 - (s.review_calls || 0))} live checks left this session`);
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
</article>`;
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
  // Sources that feed live signals come first. The rest carried an identical disclaimer per card,
  // which buried the few that matter, so they collapse behind one count instead.
  const verified = shown.filter(e => e.fresh), unverified = shown.filter(e => !e.fresh);
  patch($('#evidence'), shown.length ? `
    ${verified.length ? verified.map(evidenceCard).join('')
      : '<p class="hint">No source in this set has a verifiable publication time within the last 24 hours, so none can feed a live signal.</p>'}
    ${unverified.length ? `<details class="unverified"><summary>${unverified.length} source${unverified.length === 1 ? '' : 's'} shown for context only · publication time not verified within 24 hours, so ${unverified.length === 1 ? 'it does' : 'they do'} not feed live signals</summary>${unverified.map(evidenceCard).join('')}</details>` : ''}`
    : evidence.length ? '<p class="hint">No evidence for this symbol in the current reports.</p>'
    : '<div class="empty"><h3>No sourced evidence yet.</h3><p>Configure an AI provider and start a session with research enabled.<br>Unavailable social channels will be reported explicitly.</p></div>');
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
      <button type="button" class="text-button" data-analyze="${index}" ${a?.status === 'running' || busyElsewhere ? 'disabled' : ''}>${a?.status === 'running' ? 'Agent working…' : a?.status === 'complete' ? 'Analyze again · 1 AI request' : 'Analyze · 1 AI request'}</button></div>
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
  $('[data-tab="journal"]').title = `${j.open || 0} open paper position${j.open === 1 ? '' : 's'} · ${j.closed || 0} closed`;
  patch($('#journal-summary'), `<div><span>Open paper positions</span><strong>${j.open || 0}</strong></div><div><span>Capital reserved</span><strong>${money(j.reserved || 0)}</strong></div><div><span>Open estimated P/L</span><strong class="${j.unrealized === null ? '' : pnlClass(j.unrealized)}">${j.unrealized === null ? (j.open ? 'awaiting fresh quotes' : '—') : signedMoney(j.unrealized)}</strong></div><div><span>Closed estimates · realized</span><strong class="${pnlClass(j.net)}">${j.closed} · ${signedMoney(j.net)}</strong><small class="stat-note">${j.closed ? `${j.wins} of ${j.closed} positive — too small a sample to mean anything` : 'No closed estimates yet'}</small></div>`);
  patch($('#trades'), s.trades.length ? s.trades.map(t => `<article class="trade"><div class="trade-head"><div><strong>${esc(t.symbol)}</strong> <button type="button" class="text-button" data-replay-trade="${esc(t.id)}">Replay</button> <span class="pill">${esc(t.status.toUpperCase())}${t.asset === 'option' ? ' · OPTION' : ''}</span></div>${t.status === 'open' ? `<button class="secondary" data-close="${esc(t.id)}">Close paper estimate</button>` : ''}</div>
    <p class="hint">${esc(t.quantity)} × ${money(t.entry)} ${t.asset === 'option' ? '× 100' : ''} · capital ${money(t.notional)} · risk reserved ${money(t.risk)} · stop ${money(t.stop)} · target ${money(t.target)}<br>Opened ${esc(new Date(t.opened_at).toLocaleString())}${t.closed_at ? ' · closed ' + esc(new Date(t.closed_at).toLocaleString()) : ''}</p>
    <p>${t.status === 'closed' ? `Estimated P/L <span class="pnl ${pnlClass(t.pnl)}">${signedMoney(t.pnl)}</span> · exit ${money(t.exit)}` : `${esc(t.mark_status)} · ${t.marked_at && Date.now() - Date.parse(t.marked_at) < 45000 ? 'estimated open P/L <span class="pnl ' + pnlClass(t.unrealized) + '">' + signedMoney(t.unrealized) + '</span> at ' + money(t.mark) : 'fresh valuation unavailable'}`}</p></article>`).join('')
    : '<div class="empty"><h3>No paper trades</h3><p>Choose Track on paper on an idea to follow it here.</p></div>');
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
// A journal you can only append to gets abandoned. Closing means logging the sale you actually
// made, so the form is pre-filled rather than a price being invented for you.
document.addEventListener('click', async e => {
  const closeBtn = e.target.closest('[data-close-position]');
  if (closeBtn) {
    const position = (state?.manual_positions || []).find(p => p.symbol === closeBtn.dataset.closePosition);
    if (!position) return;
    $('#fill-asset').value = position.asset;
    $('#fill-side').value = 'sell';
    $('#fill-symbol').value = position.symbol;
    $('#fill-quantity').value = position.quantity;
    $('#fill-price').value = '';
    $('#fill-stop').value = '';
    setText($('#translate-message'), `Enter the price you actually sold ${position.symbol} at, then confirm. Nothing is sent to a broker.`);
    $('#fill-form').scrollIntoView({behavior:'smooth', block:'center'});
    $('#fill-price').focus();
    return;
  }
  const removeBtn = e.target.closest('[data-remove-fill]');
  if (!removeBtn) return;
  removeBtn.disabled = true;
  try { await post('/api/fills/delete', {id:removeBtn.dataset.removeFill}); toast('Logged fill removed.'); }
  catch (err) { toast(err.message); removeBtn.disabled = false; }
});

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
  patch($('#manual-positions'), positions.length ? positions.map(p => `<article class="trade"><div class="trade-head"><strong>${esc(p.symbol)}</strong><span class="pill ${p.mark_state === 'unusable' ? 'bad' : p.mark_state === 'reference' ? 'warn' : ''}">USER-REPORTED · ${esc(p.asset)}${p.mark_state === 'reference' ? ' · LAST CLOSE' : ''}</span></div><p>${p.quantity.toLocaleString('en-US', {maximumFractionDigits:8})} units · average fill ${money(p.average_price)}${p.asset === 'option' ? ' × 100 shares/contract' : ''}</p><p class="hint">Cost basis ${money(p.cost_basis)} · ${p.mark === null ? 'Valuation unavailable' : (p.mark_state === 'reference' ? 'last-close mark ' : 'mark ') + money(p.mark) + ' · unrealized <span class="pnl ' + pnlClass(p.unrealized_pnl) + '">' + signedMoney(p.unrealized_pnl) + '</span>'}<br>Planned stop ${p.stop ? money(p.stop) : 'not logged'} · ${esc(p.data_status)}</p><div class="position-actions"><button type="button" class="text-button" data-close-position="${esc(p.symbol)}">Log a closing sale</button></div></article>`).join('')
    : '<div class="empty"><h3>No trades logged</h3><p>Describe a trade above or fill in the form.<br>Nothing here places an order.</p></div>');
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
  patch($('#fill-history'), (s.manual_fills || []).map(f => `<div class="reject plain"><strong>${esc(f.side.toUpperCase())} ${esc(f.symbol)}</strong><span>${f.quantity} at ${money(f.price)} · ${f.executed_at ? esc(f.executed_at) : 'fill time not provided'}<br>Recorded ${esc(new Date(f.recorded_at).toLocaleString())}</span><span class="why"><button type="button" class="text-button" data-remove-fill="${esc(f.id)}">Remove this entry</button></span></div>`).join('') || '<p class="hint">No fills logged yet.</p>');
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
  risk:'Limit 1 of 3. The most a single idea is allowed to lose, as a percentage of your budget, which decides how many shares, contracts or BTC an idea is sized for. A stock or Bitcoin idea uses the entry-to-stop distance, so a $2 stop on a 1% / $10,000 budget allows 50 shares. A long option reserves its entire premium, because the premium is what you can lose, so one $1.20 contract needs $120 of this allowance. Set it too low and option ideas can never be sized at all. Conventional discretionary risk is 1-2%, but the range goes to 100% so a small speculative account can put its whole budget behind one idea. At 100% you are accepting that a single position can lose everything, and Max position size % must be raised to match or it will bind first. Actual losses can exceed a stop estimate.',
  loss:"Limit 2 of 3. The total this session may lose across every idea: this percentage of your budget, minus today's realized paper losses and the risk already reserved by open paper positions. At 3% of $10,000 it starts at $300, so three ideas risking $100 each exhaust it. It must be at least the per-idea risk. The day resets at midnight UTC. Reaching it stops new ideas; it never closes an open position for you.",
  'max-position':'Limit 3 of 3, and the one most often responsible for an idea that never appears. No single position may use more than this percentage of your budget in cash, regardless of how tight its stop is. At 25% of $10,000 that is $2,500, so a $650 share price allows at most 3 shares even if your risk allowance would permit more. Lower it to force diversification; raise it toward 100% if you deliberately want to concentrate on one position at a time.',
  budget:'The amount you want Daylight to size ideas against. Used for suggestion sizing; it is not your broker balance or verified buying power.',
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

// ---------- Options, order book and market data source ----------
// These three views ask the server directly and do not depend on a running session.
async function ask(path, data = {}) {
  const res = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify(data)});
  const result = await res.json();
  if (!res.ok) throw Error(result.error || 'Request failed');
  return result;
}
const whole = n => Number(n || 0).toLocaleString('en-US', {maximumFractionDigits:0});
const compact = n => n >= 1e9 ? '$' + (n / 1e9).toFixed(1) + 'B' : n >= 1e6 ? '$' + (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? '$' + (n / 1e3).toFixed(0) + 'K' : '$' + whole(n);
const price = n => n == null ? '–' : Number(n).toFixed(2);
const count = n => n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e4 ? (n / 1e3).toFixed(0) + 'K' : whole(n);
const shortDate = d => new Date(d + 'T12:00:00').toLocaleDateString('en-US', {month:'short', day:'numeric'});
const picks = (el, current) => patch(el, (state?.config.symbols || []).map(s => `<button type="button" class="chart-span ${s === current ? 'active' : ''}" data-pick="${esc(s)}">${esc(s)}</button>`).join(''));

let optSymbol = store.get('daylight-options-symbol', ''), optView = null, optLoading = false, unusualScan = null;
async function loadOptions(expiration) {
  if (!optSymbol || optLoading) return;
  optLoading = true;
  setText($('#options-error'), ''); setText($('#options-status'), 'Loading…');
  try {
    optView = await ask('/api/options', {symbol:optSymbol, expiration:expiration || null});
    store.set('daylight-options-symbol', optSymbol);
  } catch (e) { optView = null; setText($('#options-error'), e.message); }
  optLoading = false;
  renderOptions();
}
function unusualTable(rows, empty) {
  if (!rows.length) return `<p class="hint">${esc(empty)}</p>`;
  return `<table class="data"><thead><tr><th>Contract</th><th>Volume</th><th>Open interest</th><th>Volume ÷ OI</th><th>Value traded</th><th>Strike vs price</th><th>Implied vol</th><th>Days flagged</th></tr></thead><tbody>${rows.map(r =>
    `<tr><td><b>${esc(r.underlying)}</b> ${price(r.strike)} ${esc(r.type)} · ${shortDate(r.expiration)} <span class="muted">${r.days}d</span></td><td>${whole(r.volume)}</td><td>${whole(r.open_interest)}</td><td>${r.ratio}×</td><td>${r.premium == null ? '–' : compact(r.premium)}</td><td>${r.distance_pct == null ? '–' : (r.distance_pct > 0 ? '+' : '') + r.distance_pct + '%'}</td><td>${r.iv == null ? '–' : r.iv.toFixed(0) + '%'}</td><td>${r.days_flagged || '–'}</td></tr>`).join('')}</tbody></table>`;
}
function smile(view) {
  const pts = kind => view.rows.filter(r => r[kind]?.iv != null).map(r => [r.strike, r[kind].iv]);
  const calls = pts('call'), puts = pts('put'), all = [...calls, ...puts];
  if (all.length < 3) return '<p class="hint">Not enough quotes to draw this expiration.</p>';
  const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys) * 0.95, y1 = Math.max(...ys) * 1.05;
  const W = 900, H = 220, L = 44, B = 24;
  const x = v => L + (v - x0) / (x1 - x0 || 1) * (W - L - 8), y = v => 8 + (1 - (v - y0) / (y1 - y0 || 1)) * (H - B - 8);
  const line = p => p.map(([a, b], i) => (i ? 'L' : 'M') + x(a).toFixed(1) + ',' + y(b).toFixed(1)).join('');
  const ticks = [y0, (y0 + y1) / 2, y1].map(v => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(0)}%</text><line class="grid" x1="${L}" x2="${W - 8}" y1="${y(v)}" y2="${y(v)}"/>`).join('');
  const xt = [x0, (x0 + x1) / 2, x1].map(v => `<text x="${x(v)}" y="${H - 6}" text-anchor="middle">${price(v)}</text>`).join('');
  const spot = view.underlying >= x0 && view.underlying <= x1 ? `<line class="spot" x1="${x(view.underlying)}" x2="${x(view.underlying)}" y1="8" y2="${H - B}"/><text class="spot-label" x="${x(view.underlying) + 5}" y="18">share price ${price(view.underlying)}</text>` : '';
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Implied volatility by strike">${ticks}${xt}${spot}<path class="calls" d="${line(calls)}"/><path class="puts" d="${line(puts)}"/></svg><p class="legend"><span class="key calls"></span>Calls <span class="key puts"></span>Puts</p>`;
}
function renderOptions() {
  picks($('#options-picks'), optSymbol);
  if ($('#options-symbol') !== document.activeElement && !$('#options-symbol').value) $('#options-symbol').value = optSymbol;
  const v = optView;
  $('#options-summary').hidden = $('#options-smile-panel').hidden = !v;
  if (!v) { patch($('#options-chain'), optSymbol || $('#options-error').textContent ? '' : '<p class="hint">Enter a ticker to see its option chain.</p>'); setText($('#options-status'), ''); }
  else {
    setText($('#options-status'), `${v.symbol} · ${v.rows.length} strikes`);
    const sel = $('#options-expiration');
    const opts = v.expirations.map(e => `<option value="${e}" ${e === v.expiration ? 'selected' : ''}>${shortDate(e)}</option>`).join('');
    if (lastHTML.get(sel) !== opts) { sel.innerHTML = opts; lastHTML.set(sel, opts); }
    sel.disabled = false;
    const t = v.totals, ratio = t.call_volume ? (t.put_volume / t.call_volume).toFixed(2) : '–';
    patch($('#options-summary'), `<div><span>Share price</span><strong>${price(v.underlying)}</strong></div><div><span>Expires</span><strong>${shortDate(v.expiration)}</strong><small class="stat-note">${v.days === 1 ? '1 day' : v.days + ' days'}</small></div><div><span>Call / put volume</span><strong>${count(t.call_volume)} / ${count(t.put_volume)}</strong></div><div><span>Puts per call</span><strong>${ratio}</strong></div>`);
    patch($('#options-smile'), smile(v));
    const atm = v.rows.reduce((best, r) => Math.abs(r.strike - v.underlying) < Math.abs(best.strike - v.underlying) ? r : best, v.rows[0]);
    const cells = (o, order) => order.map(k => `<td>${o == null ? '–' : k === 'iv' ? (o.iv == null ? '–' : o.iv.toFixed(0) + '%') : k === 'delta' ? (o.delta == null ? '–' : o.delta.toFixed(2)) : k === 'bid' || k === 'ask' ? price(o[k]) : whole(o[k])}</td>`).join('');
    const callOrder = ['open_interest', 'volume', 'iv', 'delta', 'bid', 'ask'], putOrder = ['bid', 'ask', 'delta', 'iv', 'volume', 'open_interest'];
    patch($('#options-chain'), `<table class="data chain"><thead><tr><th colspan="6">Calls</th><th></th><th colspan="6">Puts</th></tr><tr><th>Open int.</th><th>Volume</th><th>Impl. vol</th><th>Delta</th><th>Bid</th><th>Ask</th><th>Strike</th><th>Bid</th><th>Ask</th><th>Delta</th><th>Impl. vol</th><th>Volume</th><th>Open int.</th></tr></thead><tbody>${v.rows.map(r =>
      `<tr class="${r === atm ? 'atm' : ''}">${cells(r.call, callOrder).replace(/<td>/g, `<td class="${r.strike < v.underlying ? 'itm' : ''}">`)}<td class="strike">${price(r.strike)}</td>${cells(r.put, putOrder).replace(/<td>/g, `<td class="${r.strike > v.underlying ? 'itm' : ''}">`)}</tr>`).join('')}</tbody></table>`);
  }
  const scan = unusualScan;
  $('#unusual-repeats-wrap').hidden = !scan?.repeats?.length;
  if (scan?.repeats?.length) patch($('#unusual-repeats'), unusualTable(scan.repeats, ''));
  setText($('#unusual-rule'), scan?.rule ? scan.rule + (scan.errors.length ? ' · ' + scan.errors.join(' · ') : '') : '');
  patch($('#unusual-list'), scan ? unusualTable(scan.rows, `Nothing unusual across ${scan.symbols.join(', ')}.`)
    : v ? unusualTable(v.unusual, `Nothing unusual in ${v.symbol} for the next 60 days.`) : '');
}
$('#options-form').onsubmit = e => { e.preventDefault(); const s = $('#options-symbol').value.trim().toUpperCase(); if (!s) return; optSymbol = s; unusualScan = null; loadOptions(); };
$('#options-expiration').onchange = e => loadOptions(e.target.value);
$('#options-picks').onclick = e => { const b = e.target.closest('[data-pick]'); if (!b) return; optSymbol = b.dataset.pick; $('#options-symbol').value = optSymbol; unusualScan = null; loadOptions(); };
$('#options-scan').onclick = async () => {
  const b = $('#options-scan'); b.disabled = true; setText(b, 'Scanning…'); setText($('#options-error'), '');
  try { unusualScan = await ask('/api/options/scan'); } catch (e) { setText($('#options-error'), e.message); }
  b.disabled = false; setText(b, 'Scan watchlist'); renderOptions();
};

let bookSymbol = store.get('daylight-book-symbol', ''), bookView = null, bookBusy = false;
async function pollBook() {
  if (!bookSymbol || bookBusy || document.hidden || store.get('daylight-tab', 'overview') !== 'book' || !connected) return;
  bookBusy = true;
  try { bookView = await ask('/api/book', {symbol:bookSymbol}); setText($('#book-error'), ''); store.set('daylight-book-symbol', bookSymbol); }
  catch (e) { bookView = null; setText($('#book-error'), e.message); }
  bookBusy = false;
  renderBook();
}
function renderBook() {
  picks($('#book-picks'), bookSymbol);
  if ($('#book-symbol') !== document.activeElement && !$('#book-symbol').value) $('#book-symbol').value = bookSymbol;
  const v = bookView;
  $('#book-summary').hidden = !v || !(v.bids.length || v.asks.length);
  if (!v) { patch($('#book-wall'), bookSymbol || $('#book-error').textContent ? '' : '<p class="hint">Enter a ticker to see its order book.</p>'); setText($('#book-status'), ''); return; }
  const waiting = {connecting:'Connecting to the stream…', waiting:'Waiting for the first update…', error:v.detail || 'Stream unavailable'}[v.status];
  setText($('#book-status'), waiting || `${v.symbol} · updated ${v.age_seconds < 2 ? 'just now' : Math.round(v.age_seconds) + ' s ago'}`);
  if (!v.bids.length && !v.asks.length) { patch($('#book-wall'), `<p class="hint">${esc(waiting || 'No resting orders reported. Books are thin or empty outside market hours.')}</p>`); return; }
  const rows = 15, bids = v.bids.slice(0, rows), asks = v.asks.slice(0, rows);
  const sum = l => l.reduce((a, b) => a + b.size, 0), bidTotal = sum(bids), askTotal = sum(asks);
  patch($('#book-summary'), `<div><span>Best bid</span><strong class="positive">${price(bids[0]?.price)}</strong></div><div><span>Best ask</span><strong class="negative">${price(asks[0]?.price)}</strong></div><div><span>Spread</span><strong>${v.spread == null ? '–' : v.spread.toFixed(2)}</strong></div><div><span>Shares bid / offered</span><strong>${whole(bidTotal)} / ${whole(askTotal)}</strong><small class="stat-note">top ${rows} prices</small></div>`);
  const max = Math.max(...bids.map(b => b.size), ...asks.map(a => a.size), 1);
  const W = 1000, RH = 26, mid = W / 2, gap = 6, bar = mid - 150;
  const row = (l, i, side) => {
    if (!l) return '';
    const w = Math.max(2, l.size / max * bar), y = 30 + i * RH;
    return side === 'bid'
      ? `<rect class="bid" x="${mid - gap - w}" y="${y}" width="${w}" height="${RH - 5}"/><text class="px" x="${mid - gap - 8}" y="${y + 15}" text-anchor="end">${price(l.price)}</text><text x="8" y="${y + 15}">${whole(l.size)} <tspan class="venues">${esc(l.venues.slice(0, 3).join(' '))}</tspan></text>`
      : `<rect class="ask" x="${mid + gap}" y="${y}" width="${w}" height="${RH - 5}"/><text class="px" x="${mid + gap + 8}" y="${y + 15}">${price(l.price)}</text><text x="${W - 8}" y="${y + 15}" text-anchor="end"><tspan class="venues">${esc(l.venues.slice(0, 3).join(' '))}</tspan> ${whole(l.size)}</text>`;
  };
  const n = Math.max(bids.length, asks.length);
  patch($('#book-wall'), `<svg viewBox="0 0 ${W} ${30 + n * RH + 6}" role="img" aria-label="Order book for ${esc(v.symbol)}"><text class="head" x="8" y="16">Shares · venues</text><text class="head" x="${mid - gap - 8}" y="16" text-anchor="end">Bid</text><text class="head" x="${mid + gap + 8}" y="16">Ask</text><text class="head" x="${W - 8}" y="16" text-anchor="end">Venues · shares</text>${Array.from({length:n}, (_, i) => row(bids[i], i, 'bid') + row(asks[i], i, 'ask')).join('')}</svg>`);
}
$('#book-form').onsubmit = e => { e.preventDefault(); const s = $('#book-symbol').value.trim().toUpperCase(); if (!s) return; bookSymbol = s; bookView = null; renderBook(); pollBook(); };
$('#book-picks').onclick = e => { const b = e.target.closest('[data-pick]'); if (!b) return; bookSymbol = b.dataset.pick; $('#book-symbol').value = bookSymbol; bookView = null; renderBook(); pollBook(); };
setInterval(pollBook, 1200);

const SOURCE_NAMES = {alpaca:'Alpaca', schwab:'Schwab', bridge:'Schwab (local service)'};
function renderDataSource(s) {
  const c = s.connections, sw = c.schwab || {};
  setText($('#data-source'), SOURCE_NAMES[c.data_source] || '');
  setText($('#alpaca-status'), SOURCE_NAMES[c.data_source] + (c.data_source === 'alpaca' && !c.alpaca ? ' · key missing' : ''));
  const hours = sw.login_expires_in_hours, left = hours == null ? '' : hours >= 48 ? Math.floor(hours / 24) + ' days' : Math.max(0, Math.floor(hours)) + ' hours';
  let text;
  if (c.data_source === 'bridge') text = 'Stocks and options come from the local market-data service set in MARKET_DATA_URL. Bitcoin and news come from Alpaca.';
  else if (sw.connected) text = `Stocks and options come from Schwab. The login expires in ${left}; Schwab requires a new login every 7 days. Bitcoin and news come from Alpaca.`;
  else if (sw.configured) text = 'A Schwab app key is saved but not logged in, so data comes from Alpaca. Log in to switch to Schwab.';
  else text = 'Data comes from Alpaca. To use Schwab instead, create an app at developer.schwab.com with the Market Data product and callback address ' + (sw.callback_url || 'https://127.0.0.1') + ', then add SCHWAB_APP_KEY and SCHWAB_APP_SECRET to the .env file and restart.';
  setText($('#schwab-state'), text);
  const canConnect = c.data_source !== 'bridge' && sw.configured && !sw.connected;
  $('#schwab-connect').hidden = !canConnect;
  if (canConnect) { $('#schwab-link').href = sw.authorize_url; setText($('#schwab-callback'), sw.callback_url); }
  $('#schwab-disconnect').hidden = !(c.data_source !== 'bridge' && sw.connected);
}
$('#schwab-form').onsubmit = async e => {
  e.preventDefault();
  try { await post('/api/schwab/connect', {url:$('#schwab-url').value}); $('#schwab-url').value = ''; setText($('#schwab-message'), 'Connected. Schwab is used from the next session start.'); }
  catch (err) { setText($('#schwab-message'), err.message); }
};
$('#schwab-disconnect').onclick = async () => { try { await post('/api/schwab/disconnect'); setText($('#schwab-message'), 'Disconnected.'); } catch (err) { setText($('#schwab-message'), err.message); } };


// ---------- Live prices, brief, replay, what-if ----------
const pct = n => n == null ? '–' : (n > 0 ? '+' : '') + Number(n).toFixed(2) + '%';
let liveBusy = false;
async function pollLive() {
  if (liveBusy || document.hidden || !connected || !state || state.connections.data_source === 'alpaca' || store.get('daylight-tab', 'overview') !== 'overview') return;
  liveBusy = true;
  try {
    const d = await ask('/api/live');
    patch($('#live-strip'), Object.entries(d.quotes).map(([s, q]) => `<button type="button" class="live-chip" data-live="${esc(s)}"><b>${esc(s)}</b> ${price(q.last)} <span class="${pnlClass(q.change_pct)}">${pct(q.change_pct)}</span></button>`).join(''));
  } catch { patch($('#live-strip'), ''); }
  liveBusy = false;
}
$('#live-strip').onclick = e => { const b = e.target.closest('[data-live]'); if (b) selectChart(b.dataset.live); };
setInterval(pollLive, 3000);

let briefLoading = false;
async function loadBrief() {
  if (briefLoading || !connected) return;
  briefLoading = true; setText($('#brief-error'), ''); setText($('#brief-status'), 'Loading…');
  try {
    const b = await ask('/api/brief');
    const sess = b.session;
    const when = !sess ? '' : sess.is_open ? `Market open · closes ${etTime(sess.next_close)}` : `Market closed · opens ${etTime(sess.next_open, true)}`;
    setText($('#brief-status'), `${when}${when ? ' · ' : ''}as of ${time(b.at)}${b.missing.length ? ' · no quote for ' + b.missing.join(', ') : ''}`);
    patch($('#brief-moves'), b.moves.length ? `<table class="data"><thead><tr><th>Symbol</th><th>Last</th><th>Previous close</th><th>Change</th><th>Last 5 sessions</th></tr></thead><tbody>${b.moves.map(m =>
      `<tr><td><b>${esc(m.symbol)}</b></td><td>${price(m.last)}</td><td>${price(m.prev_close)}</td><td class="${pnlClass(m.gap_pct)}">${pct(m.gap_pct)}</td><td class="${pnlClass(m.five_day_pct)}">${pct(m.five_day_pct)}</td></tr>`).join('')}</tbody></table>` : '<p class="hint">No quotes returned.</p>');
    setText($('#brief-unusual-title'), b.unusual_day ? `Unusual options activity · ${shortDate(b.unusual_day)}` : 'Unusual options activity');
    patch($('#brief-unusual'), b.unusual_day ? unusualTable(b.unusual, 'Nothing was flagged.') : '<p class="hint">Nothing saved yet. Flags are saved when you open a chain or scan the watchlist on the Options tab.</p>');
  } catch (e) { setText($('#brief-status'), ''); setText($('#brief-error'), e.message); }
  briefLoading = false;
}
$('#brief-refresh').onclick = loadBrief;
$('[data-tab="brief"]').addEventListener('click', loadBrief);

function replayChart(r) {
  const bars = r.bars;
  if (bars.length < 2) return '<p class="hint">No minute bars for that period.</p>';
  const lv = r.levels ? Object.values(r.levels) : [];
  const lo = Math.min(...bars.map(b => b.l), ...lv), hi = Math.max(...bars.map(b => b.h), ...lv), pad = (hi - lo) * 0.06 || 1;
  const W = 900, H = 260, L = 56, B = 22, t0 = Date.parse(bars[0].t), t1 = Date.parse(bars.at(-1).t);
  const x = t => L + (Date.parse(t) - t0) / (t1 - t0 || 1) * (W - L - 70), y = v => 8 + (1 - (v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - B - 8);
  const line = bars.map((b, i) => (i ? 'L' : 'M') + x(b.t).toFixed(1) + ',' + y(b.c).toFixed(1)).join('');
  const level = (name, v, cls) => v == null ? '' : `<line class="level ${cls}" x1="${L}" x2="${W - 70}" y1="${y(v)}" y2="${y(v)}"/><text class="${cls}" x="${W - 66}" y="${y(v) + 4}">${name} ${price(v)}</text>`;
  const start = Date.parse(r.start) >= t0 && Date.parse(r.start) <= t1 ? `<line class="spot" x1="${x(r.start)}" x2="${x(r.start)}" y1="8" y2="${H - B}"/><text class="spot-label" x="${x(r.start) + 5}" y="18">idea</text>` : '';
  const hit = r.outcome?.at ? `<circle class="hit ${r.outcome.result}" cx="${x(r.outcome.at)}" cy="${y(r.outcome.result === 'target' ? r.levels.target : r.levels.stop)}" r="5"/>` : '';
  const ticks = [lo, (lo + hi) / 2, hi].map(v => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${price(v)}</text>`).join('');
  const xt = [bars[0].t, bars.at(-1).t].map((t, i) => `<text x="${x(t)}" y="${H - 6}" text-anchor="${i ? 'end' : 'start'}">${etTime(t)}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Replay for ${esc(r.underlying)}">${ticks}${xt}${r.levels ? level('Target', r.levels.target, 'target') + level('Entry', r.levels.entry, 'entry') + level('Stop', r.levels.stop, 'stop') : ''}${start}<path class="calls" d="${line}"/>${hit}</svg>`;
}
async function runReplay(data, label) {
  setText($('#replay-error'), ''); setText($('#replay-status'), 'Loading…');
  try {
    const r = await ask('/api/replay', data);
    const o = r.outcome, words = {target:'Target reached first', stop:'Stop reached first', both:'Stop and target were both touched in the same minute', neither:'Neither level reached in this period'};
    setText($('#replay-status'), label);
    setText($('#replay-summary'), `${r.symbol} · ${r.direction || ''} · started ${etTime(r.start, true)}` + (o ? ` · ${words[o.result]}${o.minutes != null ? ' after ' + o.minutes + ' min' : ''}` : '') + (r.note ? ' · ' + r.note : ''));
    patch($('#replay-chart'), replayChart(r));
    $('#replay-panel').hidden = false;
    $('#replay-panel').scrollIntoView({block:'nearest'});
  } catch (e) { setText($('#replay-status'), ''); setText($('#replay-error'), e.message); }
}
$('#trades').addEventListener('click', e => { const b = e.target.closest('[data-replay-trade]'); if (b) runReplay({trade_id:b.dataset.replayTrade}, 'Paper trade'); });
$('#past-ideas').onclick = e => { const b = e.target.closest('[data-replay-idea]'); if (b) runReplay({idea_id:b.dataset.replayIdea}, 'Past idea'); };
$('#past-ideas-wrap').addEventListener('toggle', async e => {
  if (!e.target.open) return;
  try {
    const d = await ask('/api/replay/list');
    patch($('#past-ideas'), d.ideas.length ? `<table class="data"><thead><tr><th>Idea</th><th>When</th><th>Entry</th><th>Stop</th><th>Target</th><th></th></tr></thead><tbody>${d.ideas.map(i =>
      `<tr><td><b>${esc(i.symbol)}</b> ${esc(i.direction || '')}</td><td>${etTime(i.created_at, true)}</td><td>${price(i.entry)}</td><td>${price(i.stop)}</td><td>${price(i.target)}</td><td><button type="button" class="text-button" data-replay-idea="${esc(i.id)}">Replay</button></td></tr>`).join('')}</tbody></table>` : '<p class="hint">No ideas saved yet.</p>');
  } catch (err) { setText($('#replay-error'), err.message); }
});

$('#risk-run').onclick = async () => {
  const b = $('#risk-run'); b.disabled = true; setText($('#risk-error'), '');
  try {
    const d = await ask('/api/risk');
    const cell = v => `<td class="${pnlClass(v)}">${signedMoney(v)}</td>`;
    patch($('#risk-table'), d.rows.length ? `<table class="data"><thead><tr><th rowspan="2">Position</th><th rowspan="2">Value now</th><th colspan="3">Today, if shares move</th><th colspan="3">In ${d.later_days} days, if shares move</th></tr><tr><th>−5%</th><th>0%</th><th>+5%</th><th>−5%</th><th>0%</th><th>+5%</th></tr></thead><tbody>${d.rows.map(r =>
      `<tr><td><b>${esc(r.symbol)}</b> <span class="muted">${r.source === 'paper' ? 'paper' : 'logged'}${r.implied_vol_pct ? ' · implied vol ' + r.implied_vol_pct + '% · ' + r.days_left + ' days left' : ''}</span></td>` +
      (r.scenarios ? `<td>${money(r.value)}</td>${r.scenarios.map(s => cell(s.today)).join('')}${r.scenarios.map(s => cell(s.later)).join('')}` : `<td colspan="7" class="note">${esc(r.note)}</td>`) + '</tr>').join('')}</tbody></table>` : '<p class="hint">No open positions.</p>');
    setText($('#risk-note'), d.rows.some(r => r.implied_vol_pct) ? d.assumptions : '');
  } catch (e) { setText($('#risk-error'), e.message); }
  b.disabled = false;
};


// ---------- Signals ----------
let signalData = null, signalBusy = false;
const VERDICT = {continues:['lean', 'Tends to keep going'], fades:['lean', 'Tends to reverse'], ordinary:['', 'No different from an ordinary day'], few:['', 'Too few past cases'], note:['', 'Activity note']};
const STANCE = {act:'Worth acting on', watch:'Watch', ignore:'Ignore'};
async function pollSignals(force) {
  if (signalBusy || !connected || !state || state.connections.data_source === 'alpaca' || (document.hidden && !force)) return;
  signalBusy = true;
  if (!signalData) setText($('#signals-status'), 'Loading history. The first scan of the day takes up to a minute…');
  try { signalData = await ask('/api/signals'); setText($('#signals-error'), ''); }
  catch (e) { setText($('#signals-error'), e.message); setText($('#signals-status'), ''); }
  signalBusy = false;
  renderSignals();
}
function signalCard(e, read) {
  const s = e.stats, [cls, verdict] = VERDICT[e.verdict] || ['', ''];
  const dirWord = e.direction > 0 ? 'higher' : 'lower';
  const facts = !s.cases ? 'No past cases in this stock.' :
    `${s.cases} past case${s.cases === 1 ? '' : 's'} in ${s.sessions} sessions` + (e.direction ? ` · ended the day ${dirWord} ${s.continued_pct}% of the time (ordinary day from this time: ${s.usual_pct ?? '–'}%)` : '') +
    ` · median move to the close ${pct(s.median_to_close_pct)} (ordinary ${pct(s.usual_median_pct)})` + (s.median_half_hour_pct != null ? ` · next 30 min ${pct(s.median_half_hour_pct)}` : '');
  const p = e.plan;
  const plan = !p ? '' : p.entry ? `<div class="levels"><div><span>Buy</span><strong>${whole(p.quantity)} sh at ${price(p.entry)}</strong></div><div><span>Stop</span><strong>${price(p.stop)}</strong></div><div><span>Target</span><strong>${price(p.target)}</strong></div><div><span>Cost · at risk</span><strong>${money(p.cost)} · ${money(p.risk)}</strong></div></div>`
    : `<p class="hint">${esc(p.note)}</p>`;
  const ai = !read ? '' : `<div class="ai-read"><span class="pill ${read.stance === 'act' ? 'good' : read.stance === 'ignore' ? '' : 'warn'}">AI: ${STANCE[read.stance]}</span><p>${esc(read.read)}</p><p class="hint">Supports it: ${esc(read.confirm)}<br>Wrong if: ${esc(read.wrong)}${read.lowered ? '<br>' + esc(read.lowered) : ''}</p></div>`;
  return `<article class="signal ${cls}"><div class="signal-head"><div><strong>${esc(e.symbol)}</strong> ${esc(e.what)}${e.level != null ? ' <span class="muted">(' + price(e.level) + ')</span>' : ''}</div><span class="muted">${esc(e.time)} ET</span></div>
    <p class="verdict ${cls}">${verdict}${e.verdict === 'continues' || e.verdict === 'fades' ? ' · ' + esc(e.lean) : ''}</p><p class="hint">${facts}</p>${plan}${ai}</article>`;
}
function tradeCards(trades) {
  const head = '<div class="section-title"><h2 data-tip="Proposed by the AI from the signals and the wider picture, then checked by code: re-priced against the live quote, the option contract chosen by rule, and sized to your budget and risk settings. A suggestion that fails a check is shown with the reason. You decide and place any trade yourself.">Trade suggestions</h2><span class="muted">Proposed, then tested against the evidence</span></div>';
  if (!trades.length) return head + '<p class="hint">The AI suggested no trade. That is the usual answer.</p>';
  const WORDS = {shares:'Buy shares', call:'Buy a call', put:'Buy a put'};
  return head + trades.map(t => {
    const c = t.check || {}, ok = c.status === 'ok';
    const closed = ok && c.quote_state && c.quote_state !== 'fresh' ? '<p class="hint">Priced from the last quote before the close. Recheck at the open.</p>' : '';
    const detail = !ok ? `<p class="hint rejected">Did not pass the checks: ${esc(c.reason || 'unknown reason')}</p>`
      : t.instrument === 'shares' ? `<div class="levels"><div><span>Buy</span><strong>${whole(c.quantity)} sh at ${price(c.entry)}</strong></div><div><span>Stop</span><strong>${price(c.stop)}</strong></div><div><span>Target</span><strong>${price(c.target)}</strong></div><div><span>Cost · at risk</span><strong>${money(c.cost)} · ${money(c.risk)}</strong></div></div>${closed}`
      : `<div class="levels"><div><span>Contract</span><strong>${price(c.strike)} ${t.instrument} · ${shortDate(c.expiration)}</strong></div><div><span>Premium</span><strong>${price(c.premium)}</strong></div><div><span>Quantity</span><strong>${whole(c.quantity)}</strong></div><div><span>Cost · all at risk</span><strong>${money(c.cost)}</strong></div></div>${closed}`;
    const test = t.test || {}, kept = ok && test.verdict === 'keep';
    const label = !ok ? 'Rejected by checks' : test.verdict === 'keep' ? 'Kept after testing' + (test.confidence ? ' · ' + test.confidence + ' confidence' : '') : test.verdict === 'drop' ? 'Dropped after testing' : 'Not tested';
    const j = t.joint, joint = j ? `<p class="hint">These signals together: ${j.cases} past case${j.cases === 1 ? '' : 's'} in ${j.sessions} sessions${j.cases ? ` · price then went the trade's way ${j.went_the_trade_way_pct}% of the time · median move to the close ${pct(j.median_to_close_pct)}` : ''}${j.enough ? '' : ' · too few to rely on'}</p>` : '';
    const links = (test.sources || []).map(s => `<a href="${url(s)}" target="_blank" rel="noopener noreferrer">${esc(sourceHost(s) || 'source')}</a>`).join(' · ');
    const tested = test.tested ? `<div class="ai-read"><p>${esc(test.tested)}</p>${links ? `<p class="hint">Sources named by the AI, not verified by Daylight: ${links}</p>` : ''}</div>` : '';
    return `<article class="signal ${kept ? 'lean' : ''}"><div class="signal-head"><div><strong>${esc(t.symbol)}</strong> ${WORDS[t.instrument]}</div><span class="pill ${kept ? 'good' : !ok || test.verdict === 'drop' ? 'bad' : 'warn'}">${label}</span></div><p class="ai-reason">${esc(t.reason)}</p>${detail}${joint}${tested}</article>`;
  }).join('');
}
function renderSignals() {
  const d = signalData, review = state?.signal_review || {};
  const b = $('#signals-review');
  b.disabled = !d || !d.events.length || state?.ai_paused || state?.signal_reviewing;
  if (state?.signal_reviewing) setText(b, 'Reviewing…'); else if (b.textContent === 'Reviewing…') b.innerHTML = 'Review with AI <span class="cost">1–2 AI requests</span>';
  const reads = review.status === 'complete' && d && review.date === d.date ? Object.fromEntries(review.reads.map(r => [r.id, r])) : {};
  patch($('#signals-review-result'), review.status === 'complete' ? `<section class="panel ai-overview"><h3>AI review · ${time(review.at)}${review.web ? ' · with web search' : ''}</h3><p>${esc(review.overview)}</p>${review.reads.length ? '' : '<p class="hint">It singled out no individual signal.</p>'}</section>${tradeCards(review.trades || [])}`
    : review.status === 'error' ? `<div class="alert">${esc(review.message)}</div>` : review.status === 'running' ? '<div class="alert">The AI is reading the signals against the wider market. This takes up to a minute.</div>' : '');
  if (!d) return;
  const leaning = d.events.filter(e => e.verdict === 'continues' || e.verdict === 'fades');
  setText($('#signals-count'), String(leaning.length));
  setText($('#signals-status'), `${shortDate(d.date)} · ${d.events.length} events, ${leaning.length} with a lean · ${d.symbols.length} stocks · ${d.sessions} sessions of history${d.errors.length ? ' · ' + d.errors.join(' · ') : ''}`);
  const only = $('#signals-leaning').checked;
  const order = e => ({act:0, watch:1, ignore:2})[reads[e.id]?.stance] ?? 3;
  const shown = (only ? leaning : d.events).slice().sort((a, c) => order(a) - order(c));
  patch($('#signals-list'), shown.length ? shown.map(e => signalCard(e, reads[e.id])).join('') : `<p class="hint">${only ? 'No signal today has a historical lean.' : 'No events yet today.'}</p>`);
  const top = leaning.slice(0, 3);
  patch($('#signals-top'), top.length ? `<div class="section-title"><h2>Signals with a lean</h2><button type="button" class="text-button" data-goto="signals">All signals</button></div>${top.map(e => signalCard(e, reads[e.id])).join('')}` : '');
}
$('#signals-leaning').onchange = renderSignals;
$('#signals-top').onclick = e => { if (e.target.closest('[data-goto]')) showTab('signals'); };
$('#signals-review').onclick = async () => { setText($('#signals-error'), ''); try { await post('/api/signals/review'); } catch (e) { setText($('#signals-error'), e.message); } };
$('[data-tab="signals"]').addEventListener('click', () => pollSignals(true));
setInterval(pollSignals, 60000);
setTimeout(pollSignals, 2500);

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
  renderDataSource(s);
  renderOptions();
  renderBook();
  renderSignals();
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
