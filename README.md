# Daylight — day trade suggester

A local research dashboard for **US stocks, long calls/puts, and spot Bitcoin**. Enter a budget and press **Go**. It scans completed minute candles, checks market context and research, and presents ideas or explicit reasons to wait. There is **no order execution integration**. Place any real trades yourself in Robinhood, thinkorswim, or another brokerage.

## Run on Windows

From this folder in PowerShell:

```powershell
.\run.ps1
```

Open **http://127.0.0.1:8765**, set a budget, and press Go. The server must stay running. Closing the browser does not stop an active session; use **Stop session** to stop polling/research. Ctrl+C stops the server. Settings, paper trades and idea-check history survive restarts; monitoring stays stopped until Go is pressed again.

If the initial background preview is already running, use `.\stop.ps1` before `.\run.ps1` to restart it after editing `.env`. Subsequent foreground runs stop with Ctrl+C.

After the environment is installed, `.\start.ps1` launches a background server with no console window. Useful local controls:

```powershell
.\.venv\Scripts\python.exe manage.py status
.\.venv\Scripts\python.exe manage.py start --live
.\.venv\Scripts\python.exe manage.py stop
.\.venv\Scripts\python.exe manage.py shutdown
```

`stop` pauses the session; `shutdown` gracefully exits the server. Restart after edits or credential changes with `shutdown`, then `.\start.ps1`. Neither command sends brokerage orders. Session settings are remembered, but starting the server alone does not start paid research.

If PowerShell blocks the script, use these commands directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Runtime requirements: Python 3.11+ and `tzdata` (needed for New York market time on Windows). No Node build, cloud deployment, or database setup is required.

## Enable live data and autonomous research

1. Create an **Alpaca** account and API key. This app uses its market-data endpoints and read-only market clock. Set `ALPACA_ACCOUNT=paper` for paper-account keys or `live` for live-account keys. No brokerage positions or orders are accessed.
2. Confirm your market-data entitlements. **SIP** covers consolidated US equity data; **IEX** is a limited single-exchange view. **OPRA** is required for option ideas. The app deliberately rejects indicative options data and does not silently downgrade the configured feeds. Check current pricing and agreements in Alpaca before purchasing.
3. Create an **OpenAI API** key with billing enabled. The app uses the Responses API with web search. Configure billing controls in your account and use the app's request cap; the request cap is **not an exact dollar spending limit**.
4. Copy `.env.example` to `.env`, fill in the keys locally, and restart the server. Never paste keys into the chat or commit `.env`.

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
notepad .env
```

The optional model setting defaults to `gpt-5-mini`; set it to a model available to your API project that supports Responses web search. The connection indicators mean a key is configured, **not** that authentication/entitlements have been verified. Starting Live makes the actual requests and displays errors.

Press **Go**. The application always requests live Alpaca market data. By default, live ideas require current relevant, sourced news or macro evidence. You can explicitly disable both AI research and its requirement to explore technical-only ideas.

## What runs

**Next-session ideas:** enable the Market desk checkbox to build stock and long-call/put scenarios while Alpaca's clock confirms the US stock market is closed. The next scan fetches up to 60 calendar days of split-adjusted daily bars for up to eight monitored symbols. Plans require at least 20 completed bars, a recent close (within seven days), and a clear 5/20-session trend. Existing current research provides catalyst context and may veto conflicting plans; absent research is labeled technical-only. Plans carry dated historical references and opening-session confirmation conditions, not executable prices, quantities or option contracts. They never enter the live opportunity-alert or paper-entry pipeline. This setting persists and adds market-data requests only; ordinary AI research still follows its existing setting and limits.

Connections lights now show a tooltip on hover, keyboard focus or click. Yellow AI research explicitly distinguishes disabled session research, global AI pause, research in progress, and no current verified evidence. Red indicates failed/unavailable research; green indicates current usable sourced evidence.

**What changed? and alerts:** Market desk now compares successive scans and explains newly qualifying ideas, withdrawals (including specific rejection reasons where available), and entry-reference moves of at least 0.5%. This uses existing scan results and makes no AI requests. In-page opportunity alerts are enabled by default, with a 10-minute cooldown per symbol and direction that survives Stop/Go within the same server process. Desktop notifications are optional and require browser permission and an open dashboard page. Both controls are browser preferences; disabling Opportunity alerts mutes both types. Notifications are only issued for fresh, currently eligible ideas. Opening/reloading the page does not replay historical alerts. Up to 100 events are held in server memory (the UI shows the latest 50); a server restart clears this history and cooldowns. Multiple open dashboard tabs can each show alerts. Background browser throttling can delay delivery. Scan history is descriptive, not a substitute for checking the current idea and quote.

**Discovery beyond your watchlist:** the existing three research agents also search for new US stock/ETF leads in company news and public social discussion. Up to six current, cited candidates are shown under **Beyond your watchlist** on Market desk and their stock data is checked during subsequent scans when stocks are enabled. Your watchlist is unchanged. A discovered idea requires current company-specific non-social evidence even when technical-only watchlist ideas are allowed, plus the normal quote, candle, trend and sizing checks. Qualified cards are labeled **Discovered idea**. Search coverage is partial; this is not a measured Reddit trending feed. Leads expire with their research after 20 minutes. Discovery adds no separate AI request, but shares the existing agents' bounded search allowance. Option discovery remains limited to the first three watchlist underlyings.

**Long-term outlook:** on-demand web research now validates report fields and checks source URLs against returned search citations. Results include thesis factors, risks, invalidation conditions and source links. Reports without accepted sources return no conclusion. The latest 20 assessments survive restarts under Saved outlooks; they are historical research, not current entry signals. Specific LEAPS contracts require independently verified contract data; this feature does not provide an options-chain valuation model. Local models without web search cannot run this workflow. Global AI pause, the shared request allowance, and provider-switch protection include outlook requests.

**Charts and connections:** enter any supported ticker or use the quick picks. Six ranges are available: 1D (1-minute bars), 5D (15-minute), 1M (hourly), 6M and 1Y (daily) and 5Y (weekly). Charts read market data only, so they work with the session stopped and the market closed; an intraday range shows the most recent real session rather than an empty window on a weekend or holiday. The latest price is shown above the chart with its change over the visible range, and marked on the right axis at its own level; hovering any point reports that bar's open, high, low, close and volume. A **Candles / Line** toggle switches style and is remembered; before you choose, dense ranges default to a line because candles become unreadable slivers. The range strip reports the move across the window, its high and low, and the US session state including the next open. A monitored symbol's 1D view comes from the live scan and refreshes itself without extra requests; every other range is fetched history. Connections uses colored lights with details on hover or focus; Activity shows category details. Green market-data status requires fresh observations; stopped, unselected or closed-session categories show a waiting state.

**AI settings:** choose OpenAI, Anthropic, Google Gemini, or a local LM Studio server, enter a model ID supported by that provider, and save its credential. Pause AI and wait for active requests to finish before switching; resume when ready. Blank credentials retain the existing key; Remove key deletes it. Settings and keys are saved in the local `.env` file as plaintext, never returned in dashboard responses. Other providers require their own credentials and a compatible model; their live authentication has not been verified here. LM Studio requires its local server running with the selected model loaded (default URL `http://127.0.0.1:1234/v1`); authentication is optional. Local models assess supplied data but do not autonomously search the web. Google domain restrictions are prompt instructions rather than an enforced search filter.

**Pause all AI:** the header control blocks new research, idea checks, trade translations and position reviews, and persists across restarts. Market polling and manual entry continue. Already dispatched requests may finish and incur charges. This is separate from pausing the Debug display.

**My positions:** describe one executed stock, Bitcoin or standard long-option fill, translate it, then review/edit the fields and explicitly confirm it. Manual entry works without AI. The ledger supports buys and closing sells, average cost and partial exits; it rejects sales exceeding recorded holdings. It does not place trades or reconcile broker accounts. Positions receive live quote marks while monitoring runs. Click **Ask 2 agents** for independent market and risk assessments plus an agreement-based suggestion. Reviews are on demand by default; automatic reviews are opt-in. Disagreement or stale data produces REVIEW; ADD also requires a currently eligible entry and budget/risk headroom. Reported positions are separate from paper estimates; average-cost P/L excludes fees and is not a tax calculation.

The shared 120-request daily allowance covers all providers, including fill translation (one request) and position review (two requests). Prompts, market context and relevant reported positions are sent to the selected provider; choosing local keeps model inference on the configured localhost server.

**Quiet closed markets:** the market clock is requested only once its own boundary has passed, so a closed session is fetched once and reused until the published next open rather than polled every minute. A closed or final-15-minutes session is shown as state on Market desk and in the session banner, and traced once when it changes instead of repeating a warning on every scan. When nothing is live (stocks closed and Bitcoin not selected) scans fall back to a ten-minute cadence, which still refreshes news and next-session plans; any live instrument keeps the 60-second cadence. Repeated data warnings are traced once and again when they clear.

**Watchlist status:** replaces the old rejection list. One row per monitored symbol shows whether it produced an idea, is waiting, or is blocked, with the scanner's own reason plus a plain-language explanation of what that reason means and what would clear it.

**On-demand research:** Research & sources has **Refresh now**, which starts a research cycle immediately without changing the automatic schedule; the session request cap and the shared daily cap still apply. **Long-horizon research** is a separate opt-in agent covering weeks to quarters from filings, guidance, policy and dated catalysts. It is off by default so day-trading sessions spend nothing on it, and adds one AI request per research cycle when enabled or when run on demand. Its views are informational: they never unlock, veto or size an intraday entry. Each of the three intraday agents also reports an intraday conclusion and a weeks-to-months conclusion when its sources support one.

**Headline agent:** any entry in the market news feed can be handed to an agent with one AI request. It reads the story and related sources and reports what the article actually establishes, what appears already reflected in prices, which symbols are affected, angles worth researching with their conditions and risks, and what information is missing. Nothing runs automatically and no headline is analysed unless you ask.

**AI connection test:** AI settings can send one small request to the saved provider and model to confirm the credential, model ID and account access actually work, reporting provider HTTP errors verbatim. It counts toward the daily allowance and runs even while AI is paused, because you trigger it explicitly.

**Idea check:** three modes share one form. *Research a thesis* takes any ticker and timeframe and returns a graded verdict with a verified contract candidate where market data permits. *Check against the live scan* is the intraday yes/no assessment described below. *Long-term outlook* asks a 6–18 month question and returns a sourced thesis, risks and invalidation conditions. Each mode explains in the form why it cannot run when a precondition is missing.

Thesis verdicts assess the user's proposed trade: favorable, cautiously favorable, mixed, unfavorable, or insufficient evidence. They are not probabilities. One web-research request is reserved from the daily limit, and the result is saved locally. A favorable company outlook alone is not sufficient support for buying its calls. For stock/call/put proposals, a separate Alpaca check may return a concrete candidate. Option candidates require OPRA access, current underlying and option quotes, a 40–65 absolute delta, spread at most 8%, and a full one-contract premium within the entered workspace budget. The sample is bounded to 100 contracts within 10% of the stock price, expiring between the proposed horizon and 21 days later. This ranks a candidate by delta proximity and spread; it is not an options pricing model or proof of the best trade. A missing entitlement, stale quote, or no suitable contract leaves the sourced thesis report intact and explicitly withholds a specific trade. No position quantity or brokerage order is generated.

**Live idea check:** enter a natural-language proposal for your watchlist or Bitcoin. One AI request interprets and assesses it against the current scan and existing research. A separate deterministic check enforces live eligibility, freshness, position sizing and scope before a Yes can appear. The result includes its interpretation, concise assessment, supporting factors, risks, source links and expiry. An AI service failure produces **no verdict**, not a fabricated No. Conditional future entries and custom entry/stop/target prices, strikes or expirations are not independently validated yet and receive No. Review the interpretation for ambiguity. Completed checks are saved locally; historical assessments are not current trade signals.

**Provider quirks:** models that search the web cannot also be pinned to a strict JSON response format, so replies arrive wrapped in prose or code fences and are recovered rather than rejected. Free-form labels such as “Cautiously Bullish” are mapped onto the fixed set of accepted outcomes; genuinely unrecognisable values are refused and the error names the offending field. Google's thinking models spend part of their output budget on internal reasoning, so Gemini requests receive extra headroom. When Gemini returns no grounding metadata it cites signed Google redirect links instead of publisher URLs; these are accepted as citations and labelled **Source via Google Search**, because the publisher is only visible after clicking.

**Usage controls:** idea checks are limited to 10 requests per session in addition to the background research cap. A shared **120 AI requests per UTC day** cap covers research, idea checks, fill translation and position reviews and survives session/server restarts. Requests are reserved atomically in SQLite before they are sent; failures count too. Debug viewing and ordinary market polling do not consume AI requests. This is a request cap, not an exact dollar budget. Source text and user proposals are sent to the selected AI provider for assessment; credentials remain server-side.

**Feed health and journal:** the overview counts feeds with fresh quotes; Debug expands timestamps, feed labels and candle counts. Paper-journal summaries show completed estimates, realized estimated P/L and positive-outcome percentage separately for the selected mode. These describe your tracked sample and do not establish strategy performance.

**Debug mode:** open the **Debug · activity** tab for a live timeline of data requests, agent starts/completions, source-coverage summaries, price checks, risk sizing, rejection reasons, errors and paper-journal actions. Filter activity, expand details, pause the view, or export its JSON. Pausing this view does not pause the session. The server keeps the latest 300 events in memory (cleared by a server restart). These are operational summaries and validated-report metadata, never private model reasoning, raw responses or credentials. Debug viewing makes no extra AI requests; research progress is shown at request start/completion, not as an invented token-by-token narrative.

| Component | Behavior |
|---|---|
| Price scanner | Polls completed 1-minute OHLCV candles and current bid/ask quotes about every 60 seconds after the previous scan finishes. This is polling, not a tick stream. |
| Technical analysis | 5/20-bar moving averages, 25-minute rolling typical-price VWAP, 14-bar ATR, relative volume, 5-minute return, candle continuity and spread checks. Stocks/options require aligned SPY context. |
| News & filings agent | Autonomously searches fresh company reports, earnings, guidance, SEC filings and primary announcements. |
| Social sentiment agent | Searches public Reddit, X/Twitter and Truth Social pages for the watchlist and market-moving discussion; reports unavailable coverage. Social sentiment is context, not independent permission to trade. |
| Macro & catalysts agent | Searches Fed/BLS releases, rates, material events, regulation and Bitcoin catalysts. Flags sourced material-event risk. |
| Risk filter | Three limits decide size. **Risk / idea %** caps the planned loss on one idea, using entry-to-stop distance for stocks and Bitcoin and the full premium × 100 for long options. **Loss allowance %** caps the total at risk for the session. **Max position size %** caps the cash in any single position, 25% by default. The sidebar shows all three in dollars and warns when one of them makes option ideas impossible. |
| Paper journal | User-selected hypothetical positions, observed bid-based marks and manually closed estimates, persisted in SQLite. No automatic fills or exits. |

The three web agents run concurrently every 10 minutes by default (configurable 5–15 minutes). Each cycle uses three API requests with up to five web tool calls each, and a maximum of 5,000 output tokens per request. The default cap is 36 requests per start (12 cycles); failures also consume the cap. Once exhausted, no more research runs until you explicitly restart. Research older than 20 minutes cannot satisfy the live research gate. Stop prevents further work and discards in-flight results; requests already sent may still complete and be billed.

Web output is treated as untrusted data. The engine accepts evidence URLs only when they also appear in returned search sources/citations; unknown/old publication times are excluded from signals. This verifies source provenance, **not the truth of a model's interpretation or extracted date**. Review the linked sources. Conflicting news or macro sentiment and material event flags veto ideas; social disagreement appears as a caution. Search coverage is never a complete sentiment census or a real-time firehose.

## Scope and limits

- **Watchlist:** up to eight user-selected stock symbols, plus SPY for context and BTC/USD. This version does not discover or scan every listed security automatically.
- **Options:** scans the first three stock underlyings, sampling up to 100 near-the-money contracts per underlying, 7–21 calendar days to expiration, absolute delta 0.35–0.65, current two-sided quotes and at most 8% spread. Standard 100-share contracts only; no short options, spreads, adjusted contracts or 0DTE. All long call/put ideas are intraday. No exercise handling or automatic end-of-day closure.
- **Stock hours:** live market clock handles closed sessions and holidays. New entries stop during the final 15 minutes. Need 25 consecutive regular-session candles after opening. Existing paper positions continue requesting quotes while the session runs, even if entry filters are closed. Bitcoin runs 24/7.
- **Freshness:** quotes older than 45 seconds, malformed/crossed quotes, missing sizes and stale/gapped/unfinished candles withhold ideas. Research and quotes have independent timestamps. With 60-second polling, ideas can expire before the next scan. A continuously fresh quote/alert display requires a future streaming adapter.
- **Data gaps:** web-indexed social posts can be delayed or inaccessible. This is not authenticated Reddit/X/Truth Social ingestion. No guaranteed comprehensive earnings calendar, exchange halt feed, option open-interest/volume screen, order-book depth, crypto funding/open-interest, or on-chain feed yet. Alpaca Bitcoin quotes are venue-specific and may differ from your broker's.
- **Budget:** this is your entered *paper* budget, not verified buying power. Ideas are alternatives; capital is reserved only when you click Track on paper. One open position per underlying prevents simultaneously tracking a stock and its option. Paper losses for the current UTC day reduce available capital and risk allowance across restarts, and gains do not expand the allowance. Reported actual trades are tracked separately in My positions; broker buying power and account restrictions are not verified.
- **Evaluation:** the strategy is a transparent heuristic, not backtested or proven profitable. Paper estimates use ask entries and bid exits without fees, slippage, depth or actual fills; stop/target crossings are observations. Positions are not automatically liquidated. A fresh quote is required for a paper close.
- **Deployment:** single-user localhost service; no public hosting or multi-user authentication. Keep the computer awake for continuous scanning. API keys stay server-side; journal exports contain research/settings/trades, never keys.

## Files and checks

Working on the code? **`AGENTS.md`** is the orientation document: architecture, the scan and research loops, frontend conventions, provider quirks and testing. Open work is in `TODO.md`; completed work in `CHANGELOG.md`.

Long-horizon research accepts a saved conservative/balanced/aggressive risk profile, a 1–24 month horizon, and optional interests or exclusions. It searches beyond the watchlist, with starter briefs and per-idea source links. “Explore in Idea Check” prepares a follow-up question without sending it. Preferences do not change intraday sizing; scheduled long-horizon calls remain opt-in.

Session chat answers questions using bounded session context: settings, positions, ideas, research, feed health and recent candles. It uses the selected AI provider, including local models, and reserves one request against the shared daily cap per message. Pause all AI blocks new messages. Chat does not browse or execute actions; source links are restricted to supplied context. It keeps the last twelve messages as conversation context and clears on server restart. Larger collections are summarized or omitted to limit input size.

Price action separates history range from candle interval: 1m, 5m, 15m and 1h are available for 1D/5D history. Longer ranges use automatic intervals. Charts show at most 750 bars. Hovering shows the historical close and date in the main price readout. “Analyze chart” uses one AI request for the exact displayed candle snapshot, with trend, levels, scenarios and limitations; it does not browse or fetch execution quotes. Global AI pause and the shared cap apply. Historical analysis is labelled with the submitted ticker, interval and dates.

`market.py` contains read-only data providers and the cached market-session clock; `research.py` orchestrates the web agents, the long-horizon and headline agents, and the evidence gates; `strategy.py` computes deterministic signals and sizing; `app.py` owns the session, journal and localhost API; `static/` contains the dashboard (`index.html`, `app.js`, `style.css`). The journal is `data/journal.sqlite3`. The dashboard exports the latest 1,000 scans and 100 trades as JSON; the database retains all records.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Optional desktop/mobile browser workflow check (uses installed Microsoft Edge):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tests/browser_smoke.py
```

Tests cover data freshness/integrity, option risk, evidence provenance and aging, missing research, Bitcoin outside stock hours, budget reservation, duplicate/expired ideas, restart persistence, failures and local API access. Live provider authentication, subscription entitlements and actual AI responses require configured credentials and are not exercised by mocked tests.

## Provider references

- [Alpaca stock bars](https://docs.alpaca.markets/us/reference/stockbars) and [market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca option chain / OPRA vs indicative](https://docs.alpaca.markets/us/reference/optionchain)
- [Alpaca crypto data](https://docs.alpaca.markets/us/v1.1/docs/real-time-crypto-pricing-data)
- [Alpaca news](https://docs.alpaca.markets/us/reference/news-3)
- [OpenAI web search, sources and domain filtering](https://developers.openai.com/api/docs/guides/tools-web-search)
