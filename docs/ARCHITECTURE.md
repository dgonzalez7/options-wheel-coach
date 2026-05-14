# Architecture

## Overview

The Options Wheel Coach is a small, deliberately boring system. JSON files on disk store state. A Python script refreshes data once per market day. A single HTML file renders the dashboard. Slack gets the alerts. There is no database, no server, no frontend framework, and no real-time anything.

## Stack

| Layer | Choice |
|---|---|
| Storage | JSON files in `data/` |
| Market data | yfinance (Yahoo Finance) |
| Data validation | pydantic |
| Daily scheduler | Cowork Scheduled Task |
| Notifications | Slack via Cowork's Slack MCP |
| Dashboard | Single-file HTML, vanilla JS, Chart.js (Phase 3) |
| Version control | git, public GitHub repo |

## File structure

```
options-wheel-coach/
├── README.md
├── CLAUDE.md
├── .gitignore
├── requirements.txt
├── docs/
│   ├── REQUIREMENTS.md
│   ├── ARCHITECTURE.md           ← this file
│   ├── ALERTS.md
│   ├── DATA_MODEL.md
│   ├── RULE_CLARIFICATIONS.md
│   └── DASHBOARD.md
├── data/                          gitignored, except *.example.json
│   ├── portfolio.json             your account state
│   ├── portfolio.example.json     committed template
│   ├── positions_open.json        currently active positions
│   ├── positions_closed.json      historical record
│   ├── alerts_history.json        log of every alert that fired
│   └── snapshots/
│       └── YYYY-MM-DD.json        daily audit snapshot
├── scripts/
│   ├── data_model.py              pydantic schemas
│   ├── market_data.py             yfinance wrapper
│   ├── alerts_engine.py           rule evaluation
│   ├── slack_notify.py            Slack post formatting
│   └── daily_update.py            entry point for the scheduled task
└── dashboard/
    └── index.html                 single-file dashboard
```

## Data flow

```
Monday morning (existing workflow, outside this app)
   │
   │  Newsletter URL → trade-brief HTML (Claude in Chrome reads paywalled content)
   │  Dave picks 5, places orders in E-Trade
   ▼
Position entry (chat with Claude)
   │
   │  "Opened TSLA $370 put, $4.20 premium, exp 5/30, 1 contract"
   ▼
data/positions_open.json (appended)

Daily, 7pm CT weekdays (Cowork Scheduled Task)
   │
   ▼
scripts/daily_update.py
   │
   ├── Read data/positions_open.json + data/portfolio.json
   ├── For each ticker: yfinance.get(close, chain, sector, earnings)
   ├── Compute metrics: DTE, cushion%, %-premium-captured, position%
   ├── Evaluate alert rules (scripts/alerts_engine.py)
   ├── Write data/snapshots/YYYY-MM-DD.json
   ├── Append fired alerts to data/alerts_history.json
   └── Post CRITICAL + ACTION alerts to Slack via Cowork MCP

User reviews Slack alert
   │
   │  "TSLA $370 put ITM, 9 DTE, position 9% of portfolio"
   ▼
Chat with Claude (the judgment loop)
   │
   │  Three checks: fundamentals? sizing? still want it?
   │  Pick the conservative path or the "consider" alternative
   ▼
User executes in E-Trade, confirms fill back to Claude
   │
   ▼
Claude updates data/positions_open.json (and data/positions_closed.json if applicable)
```

## Daily run sequence

1. **Determine the run set.** Read `data/snapshots/` to find the latest snapshot date. Walk forward through NYSE trading days to today; produce a list of dates to process. Usually `[today]`. After a long PC-off stretch, could be `[Mon, Tue, Wed, Thu]`.

2. **For each date in the run set:**
   - Fetch closing stock prices for every ticker in `positions_open.json` (yfinance historical for past dates, latest close for today).
   - Recompute derived metrics per position.
   - Write `data/snapshots/{date}.json` — a complete frozen record of position state on that date.

3. **Evaluate alerts against today's state only.** (Past-day snapshots are for the audit trail. Alert decisions act on the present.) Get current option chain prices, compute pct_premium_captured, evaluate every alert rule, emit alert records.

4. **Persist alerts.** Append every fired alert to `data/alerts_history.json`. This is the chronological log used for future learning and pattern review.

5. **Post to Slack.** Filter to CRITICAL + ACTION severity. Format the consolidated message (one post per run, header noting any catch-up days). Send via Cowork's Slack MCP to `#all-personal-workspace`.

6. **Done.** Dashboard, on next browser refresh, will read the updated JSON and render the new state.

## Claude-in-the-loop pattern

The Python script is deterministic and limited in scope:

- It pulls data.
- It computes metrics.
- It fires alerts.
- It writes snapshots.

It does **not** modify positions, decide whether to roll vs close, or update the portfolio config.

Claude (in chat) handles everything that requires judgment:

- Walking through the 3-check framework when an ITM alert fires.
- Validating roll terms against the rule clarifications.
- Updating `positions_open.json` and `positions_closed.json` after fills.
- Refreshing `portfolio.json` cash/total when Dave provides updated E-Trade balances.
- Explaining what's happening when Dave asks "why did this alert fire?"

This split keeps the script simple and idempotent while letting the user benefit from reasoning where it adds value.

## Catch-up behavior

If the PC is off or Cowork isn't running at 7pm CT, the scheduled task doesn't fire. On the next run, the script:

- Finds the gap by comparing the latest snapshot date to today.
- Walks forward through NYSE trading days, writing a snapshot for each.
- Only evaluates alerts and posts to Slack for **today's** state (no stale alert burst).
- The Slack post header mentions the catch-up: `"Catching up Mon–Wed. 3 snapshots backfilled. Current state below."`

**Limitations:**
- Historical closing stock prices via yfinance: ✅ available for any past date.
- Historical option chain prices via yfinance: ❌ not reliably available. yfinance only exposes the current chain.
- Consequence: stock-based metrics (current price, cushion %, position %) backfill correctly for past days. Option-based metrics (current option price, % premium captured) only exist as of today. The snapshot schema reflects this honestly — past-day snapshots leave option fields null.

## Why these choices

**Why JSON over a database.** Diffable in git. Inspectable in a text editor. Trivial for Python and JS to consume. No schema migrations. No daemon to run. The total dataset will be small enough that performance is irrelevant.

**Why yfinance.** Free. No API key. Has Greeks. Closing prices for any past date. The Wheel doesn't need real-time, so rate limits are moot. If yfinance ever degrades, drop-in alternatives include Tiingo and Alpha Vantage.

**Why a single HTML file.** Opens in any browser. Refreshes by re-reading JSON. No build step, no server, no dependencies to install for the consumer. Chart.js loads from CDN.

**Why Cowork Scheduled Tasks over Windows Task Scheduler.** Slack posting is native via the Cowork MCP. The script runs in the workspace sandbox with the right paths and dependencies pre-installed. Claude can re-run it on demand from chat. The Python environment is the same one that handles position updates.

**Why public GitHub.** Open spec, public learning. Account data and paid newsletter content are gitignored; the repository contains only schemas, code, and documentation.

## Future considerations

The architecture allows for, but does not currently implement:

- A dedicated Wheel sub-account inside E-Trade. The `portfolio.json` schema is ready for this; just update the values.
- Broker API integration to auto-pull position state. Currently positions are manually maintained through chat — this is intentional for the judgment loop, but an automated read-only sync could supplement.
- A second user. No identity tracking exists; the app is single-user.
- Mobile dashboard. The HTML renders responsively but isn't optimized for phones.
- Algorithmic backtesting. Out of scope; the app is operational, not analytical.
