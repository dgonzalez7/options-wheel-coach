# Data Model

All persistent state is JSON. Schemas are validated at read and write time by pydantic models in `scripts/data_model.py`.

## Conventions

- **Keys:** `snake_case`
- **Money:** stored as `number`, no currency prefix, no commas (e.g., `445456` not `"$445,456"`)
- **Dates:** ISO `YYYY-MM-DD`
- **Timestamps:** ISO 8601 with timezone (e.g., `2026-05-14T19:00:00-05:00`)
- **IDs:** UUID4 strings
- **Percentages:** as numbers in their natural units (`9.1` means 9.1%, not 0.091)
- **Booleans:** explicit `true`/`false`, never strings

## Portfolio (`data/portfolio.json`)

Single document. The denominator for all sizing checks.

```json
{
  "as_of": "2026-05-14",
  "account_name": "ETrade Main",
  "account_type": "taxable",
  "total_account_value": 445456,
  "cash_available": 157246,
  "market_regime": "bull",
  "rules": {
    "csp_profit_threshold_pct": 80,
    "csp_info_milestone_pct": 50,
    "cc_profit_threshold_pct": 50,
    "min_dte_for_profit_close": 14,
    "commission_per_contract": 0.55
  },
  "sector_limits": {
    "max_per_sector_pct": 25,
    "max_positions_per_sector": 3
  },
  "notes": "Optional free text"
}
```

### Field reference

| Field | Type | Notes |
|---|---|---|
| `as_of` | date | When the cash + total figures were last refreshed |
| `account_name` | string | Human-readable label (e.g., "ETrade Main") |
| `account_type` | enum | `taxable` \| `roth_ira` \| `traditional_ira` |
| `total_account_value` | number | Net Liquidation Value across the account |
| `cash_available` | number | Settled cash, the denominator for the stress test |
| `market_regime` | enum | `bull` \| `neutral` \| `bear` — adjusts deployment caps and CSP delta band |
| `wheel_buffer_pct` | number | 0-100. Portion of non-cash holdings (`total - cash`) counted as backup for the stress test. `0` enforces strict cash-secured semantics. Raise to 20-50 for a mixed-use account where you'd accept liquidating other holdings to meet assignment. |
| `rules.csp_profit_threshold_pct` | number | % premium captured that fires the CSP close-ACTION alert |
| `rules.csp_info_milestone_pct` | number | % premium captured that fires the dashboard-only INFO milestone |
| `rules.cc_profit_threshold_pct` | number | % premium captured that fires the CC close-ACTION alert |
| `rules.min_dte_for_profit_close` | number | Days; below this DTE, profit-close alerts suppress |
| `rules.commission_per_contract` | number | Used for closed-position P&L |
| `sector_limits.max_per_sector_pct` | number | Default 25 |
| `sector_limits.max_positions_per_sector` | number | Default 3 |

## Open positions (`data/positions_open.json`)

Array of position records.

```json
{
  "positions": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "ticker": "TSLA",
      "leg": "CSP",
      "strike": 370,
      "contracts": 1,
      "entry_premium_per_share": 4.20,
      "open_date": "2026-05-12",
      "expiration_date": "2026-05-30",
      "entry_stock_price": 392.50,
      "entry_delta": 0.28,
      "sector": "Consumer Discretionary",
      "sector_override": null,
      "intent": null,
      "cost_basis": null,
      "earnings_date": "2026-07-23",
      "linked_assignment_id": null,
      "notes": "",
      "current_stock_price": 358.20,
      "current_option_price": 13.40,
      "current_delta": 0.52,
      "last_updated": "2026-05-14T19:00:00-05:00"
    }
  ]
}
```

### Field reference

#### Entry fields (set once, by chat with Claude)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Stable identifier for this position |
| `ticker` | string | E.g., `"TSLA"` |
| `leg` | enum | `"CSP"` (cash-secured put) or `"CC"` (covered call) |
| `strike` | number | Strike price |
| `contracts` | number | Number of contracts (each = 100 shares) |
| `entry_premium_per_share` | number | Per-share premium collected at open |
| `open_date` | date | Trade date |
| `expiration_date` | date | Contract expiration |
| `entry_stock_price` | number | Stock price at open, for retrospective comparison |
| `entry_delta` | number | Informational; helps grade trades A+/A/Pass |
| `sector` | string | Auto-populated from yfinance |
| `sector_override` | string \| null | Manual override if yfinance is wrong |
| `intent` | enum \| null | `null` default; `"basis_repair"` for CCs sold below basis |
| `cost_basis` | number \| null | CCs only — per-share cost basis after CSP premium reduction |
| `earnings_date` | date \| null | Next earnings; auto-fetched, can be overridden |
| `linked_assignment_id` | UUID \| null | CCs only — UUID of the CSP that got assigned |
| `notes` | string | Free text |

#### Daily-updated fields (written by the script)

| Field | Type | Notes |
|---|---|---|
| `current_stock_price` | number | Latest close |
| `current_option_price` | number | Latest option **ask** price — the realizable cost to close a short option position. Fallback: mid, then last. |
| `current_delta` | number | Latest delta; useful when ITM for roll decisions |
| `last_updated` | timestamp | When the script last touched this record |

#### Computed (not stored, derived per run)

| Field | Formula |
|---|---|
| `dte` | `expiration_date - today` (NYSE trading days) |
| `collateral` | CSP: `strike × 100 × contracts`; CC: 0 |
| `share_value` | CC only: `current_stock_price × 100 × contracts` |
| `cushion_pct` | CSP: `(stock - strike) / strike × 100`; CC: `(strike - stock) / stock × 100` |
| `pct_premium_captured` | `(entry_premium - current_option_price) / entry_premium × 100` |
| `unrealized_pnl` | `(entry_premium - current_option_price) × 100 × contracts` |
| `position_pct_of_portfolio` | CSP: `collateral / total_account_value × 100`; CC: `share_value / total_account_value × 100` |

## Closed positions (`data/positions_closed.json`)

Same shape as open, plus close-out fields. When a position closes (expires, gets bought back, gets assigned, or gets rolled), its record moves from `positions_open.json` to `positions_closed.json`.

```json
{
  "positions": [
    {
      "id": "...",
      "ticker": "AAPL",
      "leg": "CSP",
      "strike": 180,
      "contracts": 2,
      "entry_premium_per_share": 2.10,
      "open_date": "2026-04-01",
      "expiration_date": "2026-04-25",
      "entry_stock_price": 185.40,
      "entry_delta": 0.26,
      "sector": "Technology",
      "sector_override": null,
      "intent": null,
      "cost_basis": null,
      "earnings_date": null,
      "linked_assignment_id": null,
      "notes": "",
      "close_date": "2026-04-25",
      "close_premium_per_share": 0,
      "close_stock_price": 192.10,
      "outcome": "expired_worthless",
      "linked_roll_id": null,
      "days_held": 24,
      "commissions_total": 0.55,
      "net_premium": 419.45,
      "realized_pnl_total": 419.45,
      "return_on_capital": 1.17,
      "annualized_roc": 17.74,
      "pct_premium_captured_final": 100,
      "stock_return_pct_over_hold": 3.61,
      "relative_outperformance": -2.44
    }
  ]
}
```

### Close-out field reference

| Field | Type | Notes |
|---|---|---|
| `close_date` | date | When the position closed |
| `close_premium_per_share` | number | Per-share price paid to buy back; `0` if expired worthless |
| `close_stock_price` | number | Stock price at close |
| `outcome` | enum | `expired_worthless` \| `closed_at_profit` \| `closed_early_loss` \| `assigned` \| `rolled` \| `called_away` |
| `linked_roll_id` | UUID \| null | If rolled, UUID of the replacement position in `positions_open.json` |
| `days_held` | number | Calendar days between open and close |
| `commissions_total` | number | `commission_per_contract × contracts × legs_traded` |
| `net_premium` | number | `(entry_premium - close_premium) × 100 × contracts - commissions` |
| `realized_pnl_total` | number | `net_premium` + stock gain/loss if applicable |
| `return_on_capital` | number | `realized_pnl / collateral × 100` |
| `annualized_roc` | number | `return_on_capital × 365 / days_held` |
| `pct_premium_captured_final` | number | Final captured % at close |
| `stock_return_pct_over_hold` | number | `(close_stock_price / open_stock_price - 1) × 100` |
| `relative_outperformance` | number | `return_on_capital - stock_return_pct_over_hold` — answers "did the Wheel beat buy-and-hold for this period?" |

## Daily snapshot (`data/snapshots/YYYY-MM-DD.json`)

A frozen complete picture of position state at the end of a market day. Written by `daily_update.py`. Used for audit, retrospective analysis, and catch-up reconstruction.

```json
{
  "as_of": "2026-05-14",
  "portfolio": { /* portfolio.json contents at this date */ },
  "positions": [ /* full position records as of this date */ ],
  "computed": {
    "csp_collateral_total": 42500,
    "share_value_total": 18500,
    "deployment_pct": 13.7,
    "cash_reserve_pct": 35.3
  },
  "alerts_fired": [ /* alerts that fired on this date */ ]
}
```

## Alert history (`data/alerts_history.json`)

Append-only log of every alert that fires. Schema is documented in `ALERTS.md`.

## Validation rules (enforced by pydantic)

- `cash_available <= total_account_value`
- `entry_premium_per_share > 0`
- `contracts >= 1`
- `expiration_date > open_date`
- `leg in {"CSP", "CC"}`
- `outcome in {valid_outcomes}` for closed positions
- `intent in {null, "basis_repair"}`
- CCs must have non-null `cost_basis` and `linked_assignment_id` (since they arise from an assignment)
- `id` must be unique across `positions_open.json` and `positions_closed.json`
- `linked_roll_id` must reference an existing position when set
