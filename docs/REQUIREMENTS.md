# Requirements

## Purpose

The Options Wheel Coach manages the lifecycle of open Wheel Strategy positions. It is not a trade picker — Dave gets weekly trade ideas from the wheelstrategy.substack.com newsletter, selects five each Monday, and places them through E-Trade. This app's job is everything that happens after the order fills:

- Track open positions
- Recompute P&L and metrics each market day
- Evaluate rules-based alerts deterministically
- Surface required actions to Slack and to a dashboard
- Maintain historical performance records
- Help Dave manage risk (sizing, sector concentration, cash reserves)

## Functional requirements

### Strategy fidelity

The app must follow the Wheel Strategy as documented in the Substack reference articles. The strategy is rendered as mechanical as possible via position management rules. Clear alerts and indicators of required action are defined and triggered. See `ALERTS.md` for the taxonomy and `RULE_CLARIFICATIONS.md` for the reconciled thresholds.

### Phase 1: position input and dashboard

Positions are entered manually through chat with Claude after each fill — opens, closes, rolls, and assignments all flow through conversation. Position state persists across sessions in JSON files. The main dashboard is a single HTML file that reads the JSON and renders the current state. A separate closed-positions view shows historical performance.

### Phase 2: daily data refresh

Once per market day (after close), a Python script pulls closing prices and option metrics, updates all open positions, evaluates the alert rules, writes a daily snapshot to disk, and posts CRITICAL + ACTION alerts to `#all-personal-workspace` in Slack. The script is catch-up-aware: if the run is missed (PC off, app not running), the next run backfills missed market days from yfinance closing prices.

### Phase 3: performance and risk views

The dashboard gains tabs for long-term performance (monthly P&L, win rate, ROC, leg breakdowns) and risk analysis (position sizing vs limits, sector concentration, cash reserve health, stress test for simultaneous assignment).

## Inputs

- **The Wheel Strategy rules** — Read once into the project; embedded in alert logic.
- **Positions** — Entered via chat as opened, closed, rolled, or assigned.
- **Portfolio config** — `data/portfolio.json` holds account total, cash available, market regime, rule toggles, sector limits.
- **Market data** — Pulled programmatically from Yahoo Finance via yfinance. Daily closing prices and current option chains.

## Out of scope

- **Trade selection.** The newsletter does this; the app does not.
- **Order execution.** Dave places orders in E-Trade manually. The app never executes trades.
- **Real-time price feeds.** The app uses daily closing prices. Intraday is not needed.
- **Tax reporting.** Closed positions log enough data for year-end review, but tax forms are out of scope.
- **Multi-account support.** Single account for now. The current E-Trade account is mixed-use; Dave plans to carve out a dedicated Wheel sub-account within weeks.

## Key decisions and rationale

| Decision | Choice | Rationale |
|---|---|---|
| Storage | JSON files in `data/` | Simple, diffable, both Python and HTML consume it; no DB to maintain |
| Daily scheduling | Cowork Scheduled Task | Native Slack posting, Claude-in-the-loop, script runs in workspace sandbox |
| Market data | yfinance | Free, no API key, closing prices and Greeks, sufficient for daily cadence |
| Track Delta | Yes, lightly | Entry delta for trade grading; current delta only when ITM |
| Track Theta | No | Not needed; DTE is a cleaner proxy |
| Position entry UX | Chat with Claude | Judgment-rich loop; reasoning and pattern recognition where it matters |
| Dashboard format | Single HTML file | Portable, no server needed, reads JSON via fetch |
| Excel tracker | Retired (file kept on disk) | New app is single source of truth for position state |
| Schedule | 7pm CT weekdays | ~3 hours after market close, plenty of margin for data to settle |
| Slack channel | `#all-personal-workspace` | Dave's personal alerts channel |
| Severity → Slack | CRITICAL + ACTION push to Slack; CAUTION + INFO dashboard only | Avoid alert fatigue |

## Open questions resolved during planning

1. **Where does daily data collection run?** → Cowork Scheduled Task running Python.
2. **Where is position data stored?** → JSON files (`data/`).
3. **Preferred market data source?** → yfinance.
4. **Track Delta?** → Yes, but lightly.
5. **Track Theta?** → No.
6. **Excel tracker fate?** → Retired for position management; kept on disk for other purposes.
7. **What if PC is off at 7pm CT?** → Script is catch-up-aware; backfills on next run.
8. **CSP 50% profit close rule?** → Replaced with 80% threshold (more disciplined). 50% kept as dashboard-only INFO milestone.
9. **GitHub visibility?** → Public. Data and paid Substack content gitignored.

## Phasing

**Phase 0 — Specification** ✅ shipped 2026-05-14. Documentation, schemas, seeded data files, no executable code.

**Phase 1 — Foundation** ✅ shipped 2026-05-14. `scripts/data_model.py` (pydantic schemas), `dashboard/index.html` (Today tab, empty state), `scripts/serve.py`.

**Phase 1.5 — File:// dashboard** ✅ shipped 2026-05-14. Dashboard reads from generated `dashboard/data.js` rather than fetching JSON, so double-clicking `index.html` works without a server.

**Phase 2 — Daily updater** ✅ shipped 2026-05-14. `scripts/market_data.py` (yfinance wrapper, ask-priced), `scripts/alerts_engine.py` (all 16 rules), `scripts/slack_notify.py` (formatter), `scripts/daily_update.py` (orchestrator). Expanded dashboard table with DTE / cushion / captured / position-% / P&L / alert-badge columns. Cowork Scheduled Task `options-wheel-daily-update` created at 10pm CT weekdays.

**Phase 2 — known automation gap:** yfinance is blocked from inside the Cowork sandbox (HTTP 403 proxy). The Cowork scheduled task can post `data/slack_message.txt` to Slack, but can't fetch fresh prices. Daily runs are currently triggered manually on Dave's Windows machine (full network access). Three candidate fixes parked for later: hybrid (WTS + Cowork), Claude Code Desktop /schedule, or remain manual.

**Phase 3 — Performance and Risk views** ⏳ not yet started. Add Performance tab (monthly P&L, win rate, ROC, leg breakdowns), Risk tab (sector concentration, sizing vs limits, cash reserve, stress test visualization), and History tab (closed positions table with charts).

**Phase 4 — Optional polish** ⏳ probably skipped. Manual HTML entry form if chat-paste workflow becomes annoying.
