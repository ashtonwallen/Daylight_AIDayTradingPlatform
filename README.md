# Daylight

A local research dashboard for US stocks, long options and spot Bitcoin. It polls market data, runs
bounded AI web-research agents, and shows you trade ideas with the evidence behind them — or tells
you plainly why it is waiting.

**It never places a trade.** There is no broker execution path in the codebase and there is not
meant to be one. Every output is advisory. You place any real order yourself.

It runs entirely on your machine. No account, no hosting, no telemetry. Your API keys stay in a
local `.env` and your journal in a local SQLite file.

---

## Why it exists

Most tools in this space are confident. This one is built around the opposite instinct: the
interesting question is not "what should I buy" but **"is there actually a reason to act right
now, and can you show me?"**

Three ideas follow from that:

- **Deterministic checks outrank the model.** An AI can interpret your question and read the news,
  but it cannot approve a trade. Quote freshness, trend, spread, position sizing and budget are
  enforced in code *after* the model answers. An AI "yes" that fails a check becomes a "no", with
  the failing check named.
- **Evidence must be traceable to a source.** A claim only counts if its URL appears in the
  provider's own returned citations. Research that cannot be traced is shown as context and
  explicitly excluded from live signals.
- **"Nothing to do" is a real answer.** The dashboard spends more design effort explaining why it is
  waiting than on presenting ideas, because the main risk of a tool like this is nudging you into
  trades that were never there.

## What it does

| | |
|---|---|
| **Scans** | Completed one-minute candles and live quotes for a watchlist plus Bitcoin, about once a minute while anything is live. Backs off automatically when nothing is. |
| **Researches** | Three parallel web-research agents — company news and filings, social sentiment, macro and catalysts — on your chosen cadence. An opt-in fourth covers a multi-week horizon. |
| **Gates** | A live idea needs current, sourced, non-social evidence. Conflicting sentiment or a flagged material event vetoes it. Social chatter alone can never unlock a trade. |
| **Sizes** | Three explicit limits — risk per idea, session loss allowance, and max position size — each shown in dollars as you set them. |
| **Explains** | Every monitored symbol shows its state and what would have to change, in plain language. |
| **Charts** | Six ranges from one session to five years, candles or line, with a hover crosshair. Works with the market closed. |
| **Journals** | Paper positions tracked against live marks, plus a separate ledger for trades you actually placed elsewhere. |
| **Shows its work** | An activity view of every request, decision and rejection as it happens. |

Also: natural-language idea checks against the live scan, open-ended thesis research with a
broker-verified contract candidate, per-headline analysis on demand, and discovery of tickers beyond
your watchlist from cited research.

## Requirements

- **Python 3.11+**. No Node, no build step, no bundler — standard library plus `tzdata`.
- **An [Alpaca](https://alpaca.markets) API key** for market data. Read-only endpoints only; no
  brokerage positions or orders are ever accessed. The free tier works, with caveats below.
- **An AI provider key** for the research features: OpenAI, Anthropic, Google Gemini, or any
  OpenAI-compatible local server such as LM Studio. The dashboard works without one — you simply get
  technical signals with no research layer.

## Quickstart

```bash
git clone https://github.com/ashtonwallen/AIDayTradingPlatform
cd AIDayTradingPlatform
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip.exe
cp .env.example .env                           # then add your keys
.venv/bin/python app.py                        # Windows: .venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:8765**, set a budget, and press **Start session**.

On Windows there are convenience scripts: `.\run.ps1` sets up and runs in the foreground,
`.\start.ps1` runs in the background, and `manage.py status|start|stop|shutdown` controls a running
server.

The server must stay running. Closing the browser does not stop a session.

## Configuration

Everything sensitive lives in `.env`, which is git-ignored. See `.env.example`.

| Setting | Notes |
|---|---|
| `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` | Alpaca market data. |
| `ALPACA_STOCK_FEED` | `sip` (consolidated) or `iex` (single exchange, free tier). |
| `ALPACA_OPTION_FEED` | `opra`. Indicative data is deliberately refused — see below. |
| `ALPACA_ACCOUNT` | `paper` or `live`, choosing which read-only market clock to use. |
| `AI_PROVIDER` | `openai`, `anthropic`, `google` or `local`, each with its own key and model ID. |

Provider and model can also be changed from the AI settings tab, which writes the same `.env`. A
one-request connection test confirms the credential and model actually work before you rely on them.

### A note on data feeds

Feed quality is the single biggest determinant of how useful this is.

**IEX (free) publishes only its own order book.** For heavily traded names the quotes are excellent.
For everything else the displayed spread can be far wider than the real national best bid/offer, and
the scanner will reject those symbols on spread. That rejection is correct: the entry reference uses
the ask, so accepting a fake-wide quote would plan an entry, stop and target at prices that do not
exist. **Raising the spread tolerance is the wrong fix.** Either use SIP or keep the watchlist to
names with genuine depth on your feed.

**Options require OPRA.** Indicative option data is modified and delayed, so the app refuses it
rather than silently downgrading. Without an OPRA entitlement, option ideas will not appear.

## Safety model

The parts that are deliberately hard to change:

- No order execution anywhere, and no dependency that could add one.
- AI requests are reserved against a shared daily cap *before* they are sent, so failures still
  count and a runaway loop cannot spend your money.
- All fetched text — web pages, headlines, your own notes — is treated as untrusted data, never as
  instructions.
- Credentials are never returned to the browser, and never appear in the activity log, which is
  redacted at source.
- Paper positions reserve capital against the same limits as real sizing, and a stop or target
  crossing is reported as an observation, never an automatic exit.

## Testing

```bash
.venv/bin/python -m unittest discover -s tests    # 131 tests, no network
.venv/bin/python tests/browser_smoke.py           # real browser, desktop + mobile
```

Everything is mocked; the suite never makes a live API call. The browser test drives the real UI end
to end and asserts behavioural invariants, not snapshots.

## Limitations

This is a personal research tool, not a product. The strategy is a transparent heuristic that has not
been backtested or validated. Paper results use ask entries and bid exits with no fees, slippage or
depth modelling, and describe a small sample rather than any measure of performance. Options can lose
the entire premium. Stock and Bitcoin losses can exceed a stop estimate.

Nothing here is financial advice.

## License

MIT — see [LICENSE](LICENSE).
