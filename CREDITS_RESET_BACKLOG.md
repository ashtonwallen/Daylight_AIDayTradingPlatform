# Credits-reset backlog

These are the next improvements to consider when AI credits are available again. They are intentionally recorded separately from the personal tool's current workflow.

## Implemented in 0.4.0

- Bounded discovery beyond the watchlist from cited company news and public social research; six stock leads, corroboration gates, and labeled qualified ideas.
- Validated long-term outlooks with checked citation URLs, thesis invalidation conditions, and locally saved history.
- Chart requests for arbitrary supported stock tickers or BTC/USD, persistent selection and clear timestamps.
- Connections spacing and status lights; neutral workspace heading.

Remaining items below include live-provider validation and future expansion; do not infer that all integrations are verified.

## Research and outlook

- Validate the new research-lane conclusions against real OpenAI web-search responses and confirm that “no conclusion” is shown when evidence is missing or contradictory.
- Test the longer-term outlook workspace with stock, ETF, Bitcoin, and options questions. Add source links and freshness labels directly to its result when provider responses support them.
- Extend saved outlook history with comparisons between successive thesis assessments.
- Add a scheduled refresh option for long-term outlooks, with a separate low-frequency request budget.

## Data and providers

- Verify Anthropic web search, Google grounding, and LM Studio responses using real credentials or a local server.
- Investigate a streaming market-data adapter for fresher quotes than one-minute polling.
- Evaluate additional macro, options-flow, short-interest, earnings-calendar, crypto funding, and on-chain data sources.

## Product quality

- Consider automatic refreshing for chart-only symbols (currently reloaded on demand).
- Add source-level citations to every outlook conclusion and agent summary.
- Add compact “why this matters” explanations for more technical metrics and rejection reasons.
- Improve mobile chart controls and add an optional alert sound/browser notification for newly qualified ideas.

## Safety and operations

- Add encrypted-at-rest secrets for any future remote deployment; keep the current local `.env` behavior for this personal installation.
- Add an explicit model-cost estimate before expensive research requests.
- Keep all suggestions advisory and continue to block broker order execution.
