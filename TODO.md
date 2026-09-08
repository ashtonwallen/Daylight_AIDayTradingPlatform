# TODO

Open work only. Completed items move to `CHANGELOG.md`. Ideas that are not yet committed work live
in `CREDITS_RESET_BACKLOG.md`. New here? Read `AGENTS.md` first.

**Format.** One item per bullet, newest concerns first within a section:

```
- [ ] **Short title** — what to do and why it matters.
      Notes: constraints, prior findings, or where to look.
```

Use `[ ]` open, `[~]` in progress, `[x]` done this session (then move it to `CHANGELOG.md`).
Tag with `(area)` — `market`, `research`, `ui`, `positions`, `infra` — so scanning is quick.

---

## Priority

- [ ] **(market) Decide what to do about options** — the Alpaca account has options data on the
      **indicative** feed only; OPRA returns HTTP 403 "feed entitlement or permission missing".
      The app deliberately refuses indicative quotes as unactionable, so **no option idea can ever
      appear** on this plan.
      Notes: verified 2026-09-07 by probing every options endpoint on both feeds. OPRA is denied
      account-wide (403); indicative works even with the market closed, so this is an entitlement
      gap, not a market-hours effect. Either add the paid OPRA subscription or uncheck Options.
      The scan now reports one clear line and stops re-requesting for the session.

- [ ] **(market) Stock data is IEX, not SIP** — SIP also returns 403, so quotes are one exchange's
      book rather than the consolidated NBBO.
      Notes: fine for liquid names (SPY spread measured at 0.018%), but expect more
      "spread exceeds the liquidity limit" rejections on thinner symbols than SIP would produce.

- [ ] **(market) Decide whether indicative option quotes are usable for research** — the account
      has options data on the indicative feed only, and the app refuses that feed everywhere, so a
      thesis can never name a contract without an OPRA subscription.
      Notes: refusing indicative for *live intraday ideas* is right, since those quotes are modified
      and delayed. Whether a clearly-labelled indicative quote is acceptable for a *researched
      thesis* candidate is a judgement call for the user. The stock path already has the pattern:
      `strategy.quote_state` and `contracts.last_close` grade a price as fresh / reference / close
      and label which basis was used.

- [ ] **(research) Identify intraday setups and call the likely outcome** — the point of this
      tool is helping the user make money on timely decisions.


- [ ] **(feature) Generate Positions Button**  — Everything that has produces research should have a little button on that research that says something like "generate positions" and it comes up with an actual play based on the info. If theres no good suggestion, indicate that.  
      
- [ ] **(feature) Regenerate buttons on all items that do research on an interval**  — The Next-session ideas panel, when enabled, will generate a next session idea on an interval but it should also have a button to regenerate whats it there so the user doesnt have to wait. Maybe some additional options to customize this sections research. All places that generate a report like this on an interval should have a button to regenerate on demand

- [ ] **(feature) Move session chat to a right panel**  — The chatbot interface should be sticked as the user navigates the product, similar to the "Your Session" panel. It can be on the right side while your session is on the left.

- [ ] **(feature) Add multiple themes and a theme selector in the settings. I am kind of tired of the green but want to keep it around. Ensure light/dark mode for each theme**  

- [ ] **(tweak) The Analyze chart · 1 AI request should be slightly more apparent**  

## Housekeeping

- [ ] **(infra) Remove or reinstate `static/extra.js`** — 239 lines, not loaded by `index.html` and
      not served by `app.py`. Dead since the frontend was consolidated into `app.js`.

## Future

- [ ] Earlier ideas are in `CREDITS_RESET_BACKLOG.md`. **Do not start them automatically.**
