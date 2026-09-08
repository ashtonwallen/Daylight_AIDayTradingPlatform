# Changelog

Newest first. Development is cross-model, so every session adds an entry here describing what
actually changed. Open work lives in `TODO.md`; orientation lives in `AGENTS.md`.

Format: `## <version or date> — <headline>`, then grouped bullets. Note anything verified against a
live API, and how many AI requests it cost.

---

## Unreleased

### Themes and UI
- Four themes, each with a light and a dark mode: Daylight (the original green), Slate, Ember and
  Mono. A theme declares about a dozen base values and the rest derive with color-mix, so adding one
  no longer means hunting eighty hardcoded colours. `data-theme` selects the palette, `data-mode` the
  light/dark variant, and both persist per browser.
- Introduced --up and --down. Prices, P/L and verdicts stay green/red in every theme, because those
  are read by convention; --accent now only drives interactive chrome. Removed every hardcoded colour
  outside the token block and every [data-theme=light] override.
- "Analyze chart" is a proper button rather than a text link, with its request cost as a quiet
  sub-label. Same treatment for the new regenerate control.
- Next-session ideas can be regenerated on demand instead of waiting for the next scan, matching the
  Research room and long-horizon panels. Market data only, no AI requests.
- Deleted the orphaned `static/extra.js`, dead since the frontend was consolidated into `app.js`.

### Live-session readiness
- An open paper position no longer pins research settings. It now pins only the values its reserved
  capital and risk were sized against (budget, risk, loss allowance, max position, mode), so research
  cadence and request caps stay editable mid-position. Previously a single open trade silently forced
  the whole session back to whatever settings it was opened under, even across restarts.
- Fixed truncated Gemini thesis and outlook replies. Measured on gemini-3.8-flash: a grounded thesis
  spent 5,422 thinking tokens against a 1,717-token answer, so the previous 4,096-token headroom left
  the answer to be cut off mid-JSON. Headroom raised to 12,288 via ai.GOOGLE_THINKING_HEADROOM; the
  ceiling only bills what is generated.
- Researched theses now produce a trade candidate outside market hours. `strategy.quote_state()` is
  shared by position review and contract selection, grading a price as fresh / last-close reference /
  unusable; when even the closing quote is unusable, `contracts.last_close()` falls back to the last
  completed daily close. Every candidate carries a `quote_state` and states which basis it used.
  Verified live: NVDA and AAPL fall back to the daily close because their closing quotes were
  one-sided (ask 0) or 10% wide, while SPY uses its valid closing quote.
- Daily AI request cap is now configurable (`daily_ai_limit`, default unchanged at 120, range
  10-2000) instead of hardcoded, with a sidebar preview showing what a full session costs at the
  chosen cadence and how much budget is left today.
- A missing OPRA entitlement is reported once as a single actionable line and not re-requested for
  the rest of the session, instead of failing three times per scan for the whole day.
- Confirmed against the live account: options are indicative-only (OPRA 403) and stocks are IEX
  (SIP 403). Both recorded in `TODO.md`.

### Documentation
- Added `AGENTS.md`: cross-model orientation covering hard rules, architecture, both loops, frontend
  conventions, provider quirks, persistence and testing.
- Converted `todo.txt` to `TODO.md` with a single documented format, and split completed work into
  this changelog. Working agreements moved into `AGENTS.md`.
- Recorded that `static/extra.js` is orphaned.

## 0.5.0

### Charts
- Six ranges (1D, 5D, 1M, 6M, 1Y, 5Y), each with its conventional bar size. Charts read market data
  only, so they work with the session stopped and the market closed.
- Intraday ranges show the most recent *real* session rather than an empty window on a holiday.
- Candles/Line toggle, persisted. Dense ranges default to a line until the user chooses.
- Price readout, last-price tag on the axis, hover crosshair with OHLC and volume.
- Followed Alpaca pagination; Bitcoin intraday ranges previously stopped days early.
- Fixed: Bitcoin volumes are fractional, so rounding displayed "Vol 0".

### Research
- Manual "Refresh now" starts a cycle immediately without changing the automatic schedule.
- Opt-in long-horizon agent covering weeks to quarters, with risk/horizon/focus preferences. Its
  views never gate intraday entries.
- Per-headline agent: reads one story on demand and reports what it establishes, who it affects,
  angles worth researching and what is missing.
- Each intraday agent now reports both an intraday and a weeks-to-months conclusion.
- Long-term outlook moved to Idea check as a third mode beside thesis research and the live check.
- Session chat over a bounded, credential-free snapshot.

### Provider handling
- `ai.extract_json()` recovers JSON from prose- and fence-wrapped replies. Grounded models cannot be
  pinned to strict JSON mode, so this is the normal case, not an edge case.
- `ai.normalize_choice()` maps free-form enum labels onto the fixed accepted set.
- Gemini: added output-token headroom for thinking tokens, and accepted inline signed grounding
  redirect links as citations when `groundingMetadata` comes back empty. Before this, every sourced
  workflow on Gemini could only return "no conclusion".
- Outlook validation now names the offending field instead of a blanket "malformed".
- AI settings can send one small live test request and report the provider's real error.

### Market session
- Market clock is cached until its own published boundary, so a closed market is not polled every
  minute. Closed/closing state is shown as state and traced once per transition.
- Scan cadence backs off to ~10 minutes when nothing is live.
- Repeated data warnings are traced once, and again when they clear.

### Risk and positions
- Surfaced the previously hidden concentration cap as **Max position size %**, and raised the
  ceilings on risk per idea and loss allowance. All three limits now show live dollar amounts, with
  a warning naming whichever one will bind first.
- Position reviews work outside market hours: a coherent quote from before the close is used as a
  labelled last-close reference, and the agents receive daily history and session state. Bitcoin
  still treats a stale quote as a genuine failure.

### UI
- Consolidated the frontend into one `app.js`; removed the header market chip and the panel "01".
- "Reasons to wait" became **Watchlist status**: one row per monitored symbol with a state and a
  plain-language explanation.
- Replaced every explanatory paragraph with a `?` tooltip on the relevant heading or label, and
  deleted text for self-evident features. The browser test now enforces this.

### Earlier in 0.5.0
- Long-horizon risk/horizon/preferences, broad discovery, starter briefs and Idea Check handoff.
- Market news label spacing.
- Chart candle intervals, on-demand technical analysis and visible hover price/date.
- Prominent "Run long horizon research now" button; one AI request.
- Manual market-data refresh beside session status; no AI requests.

## 0.4.0

- Bounded discovery beyond the watchlist from cited company news and social research.
- Validated long-term outlooks with checked citation URLs and locally saved history.
- Chart requests for arbitrary supported tickers, persistent selection and clear timestamps.
- Connections status lights.
