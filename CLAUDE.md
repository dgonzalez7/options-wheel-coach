# Options Wheel Coach — Claude Context

This file is read automatically at the start of every Cowork session in this project. It bootstraps you (Claude) with the context you need to be useful without re-reading the full conversation history.

## What this project is

A position-management app for Dave's Options Wheel Strategy trading. It tracks open cash-secured puts (CSPs) and covered calls (CCs), evaluates rules-based alerts daily after market close, and posts CRITICAL + ACTION alerts to Dave's `#all-personal-workspace` Slack channel. Dashboard is a single HTML file. Data lives in JSON.

The app does NOT pick trades. The trade-picking comes from the weekly "Wheel Strategy" Substack newsletter, which produces a separate trade-brief HTML each Monday. This app's job is what comes after: managing the positions Dave actually placed.

## Read these for full context

Read in order if unfamiliar with the project:

1. `docs/REQUIREMENTS.md` — what the app does, scope, and decisions
2. `docs/ARCHITECTURE.md` — file structure, data flow, daily run sequence
3. `docs/ALERTS.md` — the alert taxonomy (Conservative Advice / Consider format)
4. `docs/DATA_MODEL.md` — position and portfolio schemas
5. `docs/RULE_CLARIFICATIONS.md` — how inconsistencies in the Wheel docs are resolved
6. `docs/DASHBOARD.md` — what each tab displays

The Wheel Strategy reference docs are in `WheelStrategyRules/` (gitignored, paid Substack content). Read those if the user asks about the underlying strategy.

## Claude-in-the-loop principle

This is the most important behavior to internalize.

- **Position changes happen through chat with you, not through a form.** Dave tells you "I placed TSLA $370 put, $4.20 premium, exp 5/30" — you update `data/positions_open.json`. He tells you "TSLA got assigned" — you move it to `data/positions_closed.json` and (if he sold a CC) open a new position there.
- **The daily Python script does NOT modify positions.** It only computes metrics, evaluates rules, writes snapshots, and posts alerts. Position state is human-curated through chat.
- **Slack alerts include "ping Claude for the walkthrough" hints.** When an alert fires, Dave is expected to come back to chat to work through the three-check decision framework (fundamentals broken? sizing safe? still want the stock?). Be ready to walk him through it using the rule docs.
- **You never execute trades.** Dave executes in E-Trade himself, then tells you what filled.

## Daily run cadence

- Schedule: **7pm CT, weekdays only**, via Cowork Scheduled Task
- Script: `scripts/daily_update.py`
- Catch-up-aware: if the PC was off, the next run backfills missed market days
- Alert routing: CRITICAL + ACTION → Slack push; CAUTION + INFO → dashboard only

## Conventions

- All JSON files use **snake_case** keys
- Money values stored as **numbers** (no `"$"` prefix, no comma formatting)
- Dates use **ISO format** `YYYY-MM-DD`
- DTE uses the **NYSE market calendar** (skips weekends + holidays)
- Cron uses **America/Chicago**; market-time logic uses **America/New_York**
- One position per UUID; rolls produce a new position linked by `linked_roll_id`

## Key file paths

| Purpose | Path |
|---|---|
| Workspace root | `C:\Users\dgonz\OneDrive\Documents\Claude\Projects\Options Wheen Coach\Options Wheel Coach\` |
| Portfolio config | `data/portfolio.json` |
| Open positions | `data/positions_open.json` |
| Closed positions | `data/positions_closed.json` |
| Daily snapshots | `data/snapshots/YYYY-MM-DD.json` |
| Alert history | `data/alerts_history.json` |
| Reference docs (NOT in repo) | `WheelStrategyRules/` |

## What NOT to do

- **Never commit `data/`.** It contains Dave's actual financial positions. The `.gitignore` excludes it; respect that.
- **Never commit `WheelStrategyRules/`.** It's paid Substack content and would violate the publisher's terms on a public repo.
- **Never modify open positions without Dave explicitly telling you to.** Don't auto-update on price moves, don't speculatively close positions, don't "fix" a stale entry without confirmation.
- **Never execute trades.** Dave places orders in E-Trade. Your job is to update the JSON after he confirms the fill.
- **Never post to Slack without an actual rule-engine alert firing.** No "FYI just checking in" messages.

## User context

The user is Dave (dgonzalez@thosegonzos.com). He's a retail options trader using the Wheel Strategy. He uses E-Trade and follows the wheelstrategy.substack.com newsletter. The auto-memory has more on his trading style and preferences.

When updating positions through chat, mirror Dave's E-Trade field order in any confirmations: Symbol → Action → Quantity → Expiration → Strike → Type → Limit Price.
