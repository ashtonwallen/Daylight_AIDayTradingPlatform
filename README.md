# Daylight

A local research dashboard for US stocks, long options and spot Bitcoin. It
watches market data, runs AI research agents with cited sources, and shows
trade ideas with the evidence behind them. When there's no good reason to act,
it says why it's waiting.

**Demo:** <https://ashtonwilliamallen.com/daylight-demo> (synthetic data)

**It never places a trade.** There's no broker execution code, and every
output is advisory. It runs on your machine with no account or telemetry.

## How it works

- **Code checks outrank the model.** Quote freshness, trend, spread, sizing and
  budget are enforced after the AI answers. A failed check turns an AI "yes"
  into a "no" and names the check.
- **Evidence needs a source.** A claim counts only if its URL is in the
  provider's returned citations. Social chatter alone can't unlock an idea.
- **Sizing is explicit.** You set risk per idea, the session loss allowance and
  the maximum position, all in dollars.
- **There's a journal.** It tracks paper positions against live prices and
  keeps a separate ledger of real trades you placed elsewhere.
- **It shows its work.** An activity view logs every request, decision and
  rejection.

## Quickstart

Needs Python 3.11+, an [Alpaca](https://alpaca.markets) key (market data only,
and the free tier works), and optionally an OpenAI, Anthropic, Gemini or local
model key for research.

```bash
git clone https://github.com/ashtonwallen/Daylight_AIDayTradingPlatform
cd Daylight_AIDayTradingPlatform
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip.exe
cp .env.example .env                        # add your keys
.venv/bin/python app.py
```

Open <http://127.0.0.1:8765>, set a budget and press **Start session**. On
Windows, `.\run.ps1` does the setup and runs it.

## Data feeds

- The free IEX feed has wide spreads outside heavily traded names, and the
  scanner rejects those on purpose. Use SIP, or stick to liquid tickers.
- Options need OPRA data. Delayed indicative data is refused.

## Safety

- There's no order execution and no dependency that could add it.
- AI spending is reserved against a daily cap before each request.
- Fetched text is treated as data, never as instructions.
- Keys never reach the browser or the activity log.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests   # no network
.venv/bin/python tests/browser_smoke.py
```

## Limits

This is a personal research tool. The strategy is a heuristic that hasn't been
backtested. Paper results ignore fees and slippage. Options can lose the whole
premium. **Not financial advice.**

MIT License.
