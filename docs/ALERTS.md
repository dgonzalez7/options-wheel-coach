# Alerts

Every alert is a deterministic function of position state. The alerts engine evaluates each rule in priority order and emits at most one alert per position per run (the highest-priority match wins).

Each alert carries:

- **Severity tier** — CRITICAL / ACTION / CAUTION / INFO
- **Rule ID** — stable identifier for the rule that fired (e.g., `csp_earnings_in_cycle`)
- **Conservative Advice** — the default action when no special condition applies
- **Consider** — the alternative action(s) when judgment in chat suggests otherwise

## Severity tiers

| Tier | Slack? | Meaning |
|---|---|---|
| CRITICAL | ✅ posted | Rule violation requiring immediate decision. Position is out of bounds. |
| ACTION | ✅ posted | A decision is needed within the next day or two. |
| CAUTION | dashboard only | Monitoring signal. No action required today. |
| INFO | dashboard only | Status note. Helps pattern recognition. |
| OK | dashboard only | Position is operating within all rules. |

Slack post format:

> **TSLA $370 PUT — ACTION: ITM at 9 DTE**
> Conservative Advice: Accept assignment at expiration.
> Consider: Roll down-and-out for credit, or close if fundamentals broke.
> *(Ping Claude for the 3-check walkthrough.)*

## CSP alerts

Evaluated in priority order. First match wins.

### 1. CRITICAL — Earnings inside DTE
**Rule ID:** `csp_earnings_in_cycle`
**Trigger:** `earnings_date < expiration_date`
**Conservative Advice:** Close the position now to eliminate earnings risk.
**Consider:** Roll out to a date past earnings for a net credit if fundamentals are intact and the new strike still works as a buy price.

### 2. CRITICAL — Position >10% of portfolio
**Rule ID:** `csp_overweight`
**Trigger:** `collateral / total_account_value > 0.10`
**Conservative Advice:** Close the position to bring concentration back inside the rule.
**Consider:** Hold to expiration and don't add to this name, if the 3-check framework passes and the stress test still covers simultaneous assignment.

### 3. ACTION — Deep ITM (>8% below strike) at >7 DTE
**Rule ID:** `csp_deep_itm`
**Trigger:** `(stock_price - strike) / strike < -0.08 AND dte > 7`
**Conservative Advice:** Hold and reassess at 14 DTE if all three checks pass (fundamentals intact, sizing safe, still want the stock).
**Consider:** Roll down-and-out for a net credit if a lower strike that still works as a buy price pays enough; close if any check fails.

### 4. ACTION — ITM at 7–14 DTE
**Rule ID:** `csp_itm_decision_window`
**Trigger:** `stock_price < strike AND 7 <= dte <= 14`
**Conservative Advice:** Hold to expiration and accept assignment (this is Phase 2 activating, not failure).
**Consider:** Roll down-and-out for a net credit if a better entry price is available; close immediately if fundamentals have broken.

### 5. ACTION — <7 DTE and ITM
**Rule ID:** `csp_itm_imminent`
**Trigger:** `stock_price < strike AND dte < 7`
**Conservative Advice:** Accept assignment; rolling this late rarely justifies the friction.
**Consider:** Close to take the loss only if the company materially changed or sizing has shifted into the danger zone.

### 6. ACTION — Premium threshold captured at >14 DTE
**Rule ID:** `csp_profit_threshold`
**Trigger:** `pct_premium_captured >= portfolio.rules.csp_profit_threshold_pct AND dte > portfolio.rules.min_dte_for_profit_close`
**Conservative Advice:** Close to lock the credit and free the collateral for the next setup.
**Consider:** Let it run to expiration only if remaining premium is meaningful relative to time left and you don't need the cash elsewhere.
**Default threshold:** 80% (set in `portfolio.json`).

### 7. CAUTION — Within 3% of strike
**Rule ID:** `csp_near_the_money`
**Trigger:** `0 <= (stock_price - strike) / strike <= 0.03`
**Conservative Advice:** No action today; monitor on the next daily update.
**Consider:** Pre-answer the 3 checks now so the next alert finds you ready.

### 7.5. CAUTION — Cushion narrowed 50%+ from entry
**Rule ID:** `csp_cushion_narrowing`
**Trigger:** `entry_cushion_pct > 0 AND current_cushion_pct > 3 AND current_cushion_pct <= entry_cushion_pct / 2`. Where `entry_cushion_pct = (entry_stock_price - strike) / strike * 100`. The `current_cushion_pct > 3` guard prevents double-firing with `csp_near_the_money`.
**Conservative Advice:** No action; cushion has been reduced by 50%+ from where you opened. Watch tomorrow's update.
**Consider:** Roll down-and-out for a credit if you'd like to widen the cushion proactively, especially if DTE > 14. Or revisit the 3-check framework — what's the company doing that's moved the stock this far?
**Why ratio (not absolute pp):** Scale-invariant. A 3pp drop means very different things from +5% (cushion almost gone) vs from +18% (cushion barely moved). The "halved" framing is consistent across entry cushion levels.

### 8. INFO — Premium milestone captured
**Rule ID:** `csp_profit_milestone`
**Trigger:** `pct_premium_captured >= portfolio.rules.csp_info_milestone_pct AND dte > portfolio.rules.min_dte_for_profit_close`
**Conservative Advice:** No action; useful for tracking decay rhythm.
**Consider:** Note the position; if it continues toward the action threshold, the close decision is ready.
**Default milestone:** 50%.

### 9. INFO — 14 DTE mid-cycle check
**Rule ID:** `csp_14_dte_check`
**Trigger:** `14 <= dte <= 16`
**Conservative Advice:** No action if cushion and sizing are fine.
**Consider:** Roll forward early for credit if a cleaner setup is available at better terms.

### 10. OK
**Rule ID:** `csp_ok`
Default state when no other rule matches.

## CC alerts

### 1. CRITICAL — Strike below cost basis without intent flag
**Rule ID:** `cc_below_basis_no_intent`
**Trigger:** `strike < cost_basis AND intent != "basis_repair"`
**Conservative Advice:** Buy back the call to restore exit flexibility — selling below basis without intent locks a loss.
**Consider:** Hold and re-tag the position `intent=basis_repair` if you deliberately chose this per the Basis-Strike Matrix in the CC doc.

### 2. ACTION — Premium threshold captured
**Rule ID:** `cc_profit_threshold`
**Trigger:** `pct_premium_captured >= portfolio.rules.cc_profit_threshold_pct`
**Conservative Advice:** Buy back the call to bank the credit and reset to a fresh CC.
**Consider:** Let it run only if shares are well below strike, time is short, and remaining premium isn't worth the round-trip.
**Default threshold:** 50% (set in `portfolio.json`).

### 3. ACTION — ITM at <7 DTE
**Rule ID:** `cc_itm_imminent`
**Trigger:** `stock_price > strike AND dte < 7`
**Conservative Advice:** Let shares be called away at the agreed strike — this was the planned exit.
**Consider:** Roll up-and-out for a net credit only if the new strike is above cost basis AND you still want to hold the shares longer.

### 4. CAUTION — Within 3% of strike from below
**Rule ID:** `cc_near_the_money`
**Trigger:** `0 <= (strike - stock_price) / stock_price <= 0.03`
**Conservative Advice:** No action; let the planned exit play out.
**Consider:** Roll up-and-out for credit if you genuinely want to extend the holding period.

### 5. INFO — 14 DTE mid-cycle check
**Rule ID:** `cc_14_dte_check`
**Trigger:** `14 <= dte <= 16`
**Conservative Advice:** No action.
**Consider:** Roll forward for credit if better terms are available.

### 6. OK
**Rule ID:** `cc_ok`

## Portfolio-level alerts

Evaluated once per run, independent of individual positions.

### 1. ACTION — Cash reserve <20%
**Rule ID:** `portfolio_cash_low`
**Trigger:** `cash_available / total_account_value < 0.20`
**Conservative Advice:** Don't open new positions until the reserve is rebuilt to ≥25%.
**Consider:** Close the lowest-conviction position to restore reserve faster if a fresh setup is worth the swap.

### 2. ACTION — Total deployment over cap
**Rule ID:** `portfolio_overdeployed`
**Trigger:** `(CSP_collateral + share_value) / total_account_value > 0.60` (bull) or `> 0.50` (bear)
**Conservative Advice:** Stop opening new positions; let existing positions expire or close naturally.
**Consider:** Trim the position with the smallest cushion if a clearly better setup needs the capital.

### 3. ACTION — Sector concentration
**Rule ID:** `portfolio_sector_overweight`
**Trigger:** Any sector's total collateral + share value > 25% of `total_account_value`
**Conservative Advice:** No new positions in this sector.
**Consider:** Close the most-profitable position in the sector to rebalance, if it's near a natural exit anyway.

### 4. CRITICAL — Stress test fail
**Rule ID:** `portfolio_stress_test_fail`
**Trigger:** `sum(all_open_CSP_collateral) > effective_cash`, where `effective_cash = cash_available + (wheel_buffer_pct% × non_cash_holdings)`. With `wheel_buffer_pct = 0` (default), this is strict cash-secured semantics. Raise the buffer when the account holds other liquid assets you'd accept as assignment backup.
**Conservative Advice:** Close the lowest-conviction CSP to restore coverable assignment capacity.
**Consider:** Add outside cash to the account before the next assignment-risk window if a roll-rather-than-close path is materially better.

## Alert record schema

Every fired alert is appended to `data/alerts_history.json`:

```json
{
  "alert_id": "uuid",
  "fired_at": "2026-05-14T19:00:00-05:00",
  "rule_id": "csp_itm_decision_window",
  "severity": "ACTION",
  "scope": "position",
  "position_id": "uuid-of-position",
  "ticker": "TSLA",
  "computed_state": {
    "dte": 9,
    "stock_price": 358.20,
    "strike": 370,
    "cushion_pct": -3.18,
    "position_pct_of_portfolio": 9.1
  },
  "conservative_advice": "Hold to expiration and accept assignment.",
  "consider": "Roll down-and-out for a net credit if a better entry price is available; close immediately if fundamentals have broken.",
  "slack_posted": true
}
```

This log doubles as the dataset for retrospective analysis — "which alerts did I act on, which did I ignore, and what was the outcome?"
