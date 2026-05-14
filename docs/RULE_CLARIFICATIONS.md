# Rule Clarifications

The eight Wheel Strategy reference articles in `WheelStrategyRules/` (gitignored) drift slightly on certain thresholds. This document records the values we lock in for this app, why each was chosen, and where the conflicts exist in the source material.

## Threshold reconciliations

| Rule | Source A | Source B | Locked value | Rationale |
|---|---|---|---|---|
| Min market cap | $2B (Stock Selection Framework) | $10B (CSP Mechanical Checklist) | **$10B** | The CSP checklist is the more recent, stricter formulation. Beginners and disciplined practitioners both benefit from the larger-cap filter. |
| Min average daily volume | 500K shares (Stock Framework) | 1M shares (CSP Checklist) | **1M** | Same as above. Tighter liquidity threshold prevents wider spreads. |
| Min open interest | 100 contracts (Stock Framework) | 500 contracts (CSP Checklist) | **500** | Ensures meaningful market depth to exit cleanly. |
| Share price band | $10–$150 (Stock Framework) | $10–$250 (CSP Checklist) | **$10–$250** | Higher ceiling supports a broader watchlist. The 10% sizing rule provides a separate guard against expensive stocks. |
| Max per position | 5–10% (mentioned everywhere) | "≤10%, preferred 6–8%" (Sizing doc) | **10% hard ceiling, 8% warning** | The 10% is the hard rule; flagging at 8% gives Dave a heads-up before he's at the line. |
| Cash reserve | 20–30% (bull) / 40–50% (bear) (FAQ + Sizing + Bear doc) | — | **25% default, regime-aware** | Configurable per `market_regime` in portfolio.json. |
| Active deployment | 50–60% (bull) / 30–50% (bear) | — | **60% bull, 50% bear** | Configurable per `market_regime`. |
| CSP delta | 0.25–0.35 (bull, multiple docs) | 0.10–0.20 (Bear Market doc) | **regime-aware** | Bull: 0.25–0.35. Bear: 0.10–0.20. Documented in the alert engine. |
| Default DTE | 21 (both leg checklists) | range 14–30 | **21 default, range 14–30** | Consistent across the docs. |
| 50% buyback rule | Mentioned for CC in newsletter management plans; not explicit for CSPs in the docs | — | **CC: 50% (newsletter convention); CSP: 80% (disciplined practice)** | See below. |

## The 50% buyback question

The weekly newsletter's "Management Plan" section uses a "buy back the option at 50% of original premium" rule. This rule applies to the **covered call (CC), not the cash-secured put (CSP)**.

**Verification:** TSLA CC originally sold for $7.17 in one newsletter; the management plan said "buy back at $3.59 or less," which is 50% of $7.17. The rule applies to whichever leg the trade is currently in.

For this app, we generalize:

- **CC profit threshold:** 50% (matches newsletter convention) — set in `portfolio.json` as `cc_profit_threshold_pct: 50`.
- **CSP profit threshold:** 80% (more disciplined Wheel practice — captures most of the premium decay before paying transaction costs to close, while freeing collateral earlier than holding to expiration) — set as `csp_profit_threshold_pct: 80`.
- **CSP INFO milestone:** 50% (dashboard only, no Slack push) — set as `csp_info_milestone_pct: 50`. Provides early visibility into how decay is tracking without forcing an action.
- **Minimum DTE for profit-close alerts:** 14. Below this, you're already in the accept-assignment-vs-close decision zone where DTE and ITM-state matter more than profit %.

All four values are configurable per-account in `portfolio.json`.

## Simplifications applied

The reference docs spend significant content on stock *selection* (quality filters, liquidity tests, watchlist construction). This app does not implement selection — the newsletter does that job. The selection criteria appear in the alerts engine only as **portfolio-level guardrails on positions Dave actually opened** (e.g., "earnings inside DTE" or "position >10%"), not as a screening pipeline.

The "Strategic Stock Picking" content (5 quality tests, 3 liquidity tests) is treated as **reference material**, not as runtime logic. If Dave wants to validate that a newsletter setup passes the rules before placing it, that's a conversation in chat, not an automated check.

## Market regime — what it controls

`portfolio.market_regime` accepts `bull` | `neutral` | `bear` and adjusts:

| Setting | Bull | Neutral | Bear |
|---|---|---|---|
| Deployment cap | 60% | 55% | 50% |
| Cash reserve target | 25% | 30% | 40% |
| CSP delta band (target for new positions, not enforced) | 0.25–0.35 | 0.20–0.30 | 0.10–0.20 |
| Default DTE | 21 | 21 | 21–35 (volatility-adjusted, see Bear doc) |
| Auto-sell CC after CSP assignment? | Yes | Yes | No — flag for review |

The regime is a manual setting Dave updates when his read on the market shifts. It's not auto-detected. Default is `bull`.

## Bear market caveat

The "How to Run the Wheel Strategy in a Bear Market" article recommends not auto-selling covered calls after an assignment in a declining market — instead, evaluate and possibly skip. The alert engine respects this: in bear regime, an assignment event surfaces an INFO alert ("evaluate whether to sell CC immediately or wait") rather than implicitly assuming the next CC is coming.

## Position selection grading (reference only, not enforced)

The CSP checklist defines A+ / A / Pass grades using thresholds on delta, DTE, spread, OI, ROC, cushion, and earnings clearance. The app records `entry_delta` so the grade is computable in retrospect, but does not block trade entry on grade. Dave chooses which newsletter setups to place each Monday using cushion % as the primary filter; the grading framework is available for retrospective analysis if needed.

## Inconsistencies left unresolved

A few stylistic disagreements between the docs are flagged here for transparency but not "fixed" because they don't affect logic:

- The articles vary on whether to call premium "premium" or "credit." The app uses `premium`.
- "Cushion" and "distance from strike" are used interchangeably. The app uses `cushion_pct`.
- Some docs say "delta 25" (meaning 0.25), others say "0.25 delta." The app stores delta as a decimal (`0.25`).

If a future Wheel doc revision changes any of the reconciled values above, update this file and bump the relevant `portfolio.json` defaults.
