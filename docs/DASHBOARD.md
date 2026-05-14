# Dashboard

The dashboard is a single HTML file (`dashboard/index.html`) that reads the JSON files in `data/` and renders a four-tab view. No server, no build step.

## Top-level structure

- **Header strip** (always visible at top): account state at a glance
- **Tab navigation:** Today · Performance · Risk · History
- **Footer:** "Snapshot as of" timestamp, link to the alerts log

## Header strip (always visible)

| Metric | Notes |
|---|---|
| Total account value | With delta vs the previous snapshot |
| Cash available | $ and % of total |
| CSP collateral | $ locked across N open puts |
| Share value | $ across N held positions |
| Capital deployed | (CSP collateral + share value) ÷ total, color-coded against the 60% bull cap |
| Active position count | One number, click to jump to Today tab |
| Market regime badge | Bull / Neutral / Bear |
| Snapshot timestamp | "As of 2026-05-14 19:00 CT" |

## Today tab

### Alerts panel — sorted by severity, highest first

- Each alert renders: severity tag · position label · **Conservative Advice** · *Consider* line · "ping Claude" hint
- Filter chips along the top: CRITICAL · ACTION · CAUTION · INFO · OK
- Counter at top: "3 ACTION, 1 CAUTION, 11 OK"
- Color coding: CRITICAL red, ACTION orange, CAUTION yellow, INFO grey, OK green

### Open positions table — one row per position

| Column | Notes |
|---|---|
| Ticker | With sector tag inline |
| Leg | "CSP" or "CC" badge |
| Strike | |
| Contracts | |
| Open · Expiration · DTE | Three small columns or one combined |
| Entry premium (per share) | |
| Current option price | |
| % premium captured | Color-coded against the configured thresholds |
| Entry stock price · Current · Move % | Stock movement since open |
| Cushion % | CSP: above strike; CC: below strike |
| Collateral (CSP) or Share value (CC) | $ |
| Position % of portfolio | Color-coded against 8% warning and 10% ceiling |
| Unrealized P&L | $ |
| Alert badge | Highest-priority alert currently active |

**Row expand** reveals: full management plan, delta-at-entry, earnings date, intent flag, notes, link to original newsletter setup if known.

## Performance tab

### Top stat tiles

- Total realized P&L (lifetime)
- Total premium collected (lifetime)
- Win rate (% of closed positions with positive P&L)
- Average return on capital per trade
- Average annualized ROC
- Total trades closed
- Average days held
- Premium captured per $ of collateral-days (capital efficiency)

### Monthly P&L chart

- Bar chart, one bar per month
- Each bar stacked by leg: CSP premium · CC premium · stock gain from call-aways
- Net P&L line overlaid
- Filter chips: Last 12 months · YTD · All-time
- Click a bar to see the closed positions that contributed to that month

### Outcome breakdown

- Stacked bar or pie: expired_worthless · closed_at_profit · rolled · closed_early_loss · assigned (CSP) · called_away (CC)
- Counts and % share of each
- Average ROC per outcome type

### Leg comparison

- Side-by-side: CSP stats vs CC stats
- Count · Avg premium · Avg DTE held · Win rate · Avg ROC

### Best and worst

- Top 5 trades by realized $
- Bottom 5 trades by realized $
- Highest annualized ROC
- Longest-held position

## Risk tab

### Position sizing panel

- Horizontal bar per open position
  - Width: % of portfolio
  - Threshold lines at 8% (warning) and 10% (ceiling)
  - Sorted largest-first
  - Any bar crossing 10% gets a red badge

### Sector concentration

- Bar chart, one bar per sector vs the 25% line
- Counts: positions per sector vs the 3-per-sector limit
- "Sectors deployed: 4 of 11" readout

### Cash and deployment health

- Deployment gauge: current % vs 60% cap (or regime-adjusted)
- Cash reserve gauge: current % vs 25% target and 20% floor
- Stress test: total CSP collateral vs available cash, PASS / FAIL with the dollar gap shown

### ITM exposure

- Count of currently ITM positions
- Total $ ITM exposure
- DTE distribution of ITM positions (to spot near-expiration clustering)

### Calendar landmines

- List of positions with earnings inside their DTE
- List of positions within 14 DTE (heads-up window)

## History tab

### Filter bar

- Date range · Ticker · Sector · Leg · Outcome
- Search box

### Closed positions table

| Column | Notes |
|---|---|
| Ticker · Leg | |
| Open date · Close date · Days held | |
| Strike · Contracts | |
| Premium in · Premium out · Net premium | |
| Outcome | Tag |
| Stock price at open · close · return % over hold | |
| Return on capital · Annualized ROC | |
| % premium captured at close | |
| Relative outperformance | Your ROC minus the underlying's same-period return |
| Realized P&L | $, includes commissions |

**Row expand** reveals: full lifecycle (open record, any rolls, close record), link to the alerts that fired during the hold.

### Aggregate footer

- Sum of net premium across the filtered set
- Sum of realized P&L
- Count
- Average ROC

## Visual conventions

- Money in USD, no decimals on totals (e.g., `$445,456`), two decimals on per-share figures (e.g., `$4.20`)
- Percentages to one decimal (e.g., `9.1%`)
- Dates as `Mon DD` (e.g., `May 14`) on hover, full ISO available
- Color coding:
  - Green: within rules, positive P&L
  - Yellow/Amber: warning, approaching limit
  - Red: critical, rule violation, negative P&L
  - Grey: informational, not actionable

## Rendering technology

- **Phase 1:** Vanilla JS, no framework. `fetch()` for JSON files, DOM manipulation for rendering.
- **Phase 3:** Add Chart.js (loaded from CDN) for the Performance and Risk charts.
- **No build step.** Open the file in Chrome and it works.

## Local serving

Because most browsers block `file://` URLs from fetching local JSON files for security reasons, the dashboard is served via a one-line local web server. Two options:

```bash
# From the workspace folder:
python -m http.server 8000

# Then open: http://localhost:8000/dashboard/index.html
```

Or, for a more permanent solution, a `scripts/serve.py` helper that opens the browser automatically.

## What the dashboard does NOT do

- It does not modify state. All writes go through chat with Claude.
- It does not pull live data on its own. Refresh = `daily_update.py` re-runs.
- It is not a trading interface. No buy/sell buttons. No links to brokerages.
