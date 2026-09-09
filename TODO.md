# TODO

Open work only. Completed items move to `CHANGELOG.md`. New here? Read `AGENTS.md` first.

**Format.** One item per bullet:

```
- [ ] **Short title** — what to do and why it matters.
      Notes: constraints, prior findings, or where to look.
```

Use `[ ]` open, `[~]` in progress, `[x]` done this session (then move it to `CHANGELOG.md`).
Tag with `(area)` — `market`, `research`, `ui`, `positions`, `infra` — so scanning is quick.

---

## Priority

- [ ] **(research) Identify intraday setups and name the likely outcome** — the tool currently
      reports what is true right now; the harder and more useful thing is recognising a repeating
      shape as it forms and saying what usually follows.
      Notes: needs a written definition of the setup before any detector is worth building, a
      deterministic implementation in `strategy.py`, and honest framing — this is a heuristic, not a
      prediction, and it must not become a confident-sounding guess.

- [ ] **(feature) "Generate a position" on any research output** — every panel that produces
      research should offer to turn it into a concrete play, and say plainly when there isn't one
      worth taking.
      Notes: `trade_plan.py` already assembles this for a researched thesis; the work is extending
      it to long-horizon views, headline analyses and discovery leads without inventing a trade
      where the evidence does not support one.

- [ ] **(ui) Move session chat into a persistent right panel** — so it stays available while you
      navigate, mirroring the session panel on the left.

- [ ] **(feature) Accept file uploads in chat and My positions** — PDFs, images, CSVs and text as
      context. A broker screenshot should be usable to pre-fill logged positions.
      Notes: sizeable piece of work. Uploaded files are untrusted input like any other external
      text, and image parsing needs a provider that supports it. Skip if the lift is large.

## Data feeds

- [ ] **(market) Consider making the spread gate feed-aware** — a single-exchange feed such as IEX
      publishes only its own book, so quotes outside heavily traded names can be far wider than the
      real national best bid/offer, and the scanner rejects them on spread.
      Notes: the rejection is correct as it stands. The entry reference uses the ask, so accepting a
      fake-wide quote would plan entries at prices that do not exist — **do not simply raise the
      tolerance.** A defensible improvement would detect the feed and explain the cause, or compare
      against the day's traded range before rejecting.

- [ ] **(market) Decide whether delayed option quotes are usable for research** — the app refuses
      indicative option data everywhere, which is right for live intraday ideas but arguably too
      strict for a thesis assessed over weeks.
      Notes: the grading pattern already exists. `strategy.quote_state` and `contracts.last_close`
      classify a price as fresh / last-close reference / unusable and label which basis was used;
      delayed data could join that scale rather than being refused outright.
