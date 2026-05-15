"""Alert engine — evaluates the rules in docs/ALERTS.md against positions.

This is the heart of the app. Given a portfolio, a list of open positions
(with current market data merged in), and today's date, it returns the list
of Alert records that should fire.

Two kinds of evaluation:

- Per-position: rules evaluated in priority order, highest-priority match
  wins. Each position emits at most one alert.
- Portfolio-level: each rule evaluated independently. Multiple alerts can
  fire.

The engine has no I/O of its own. The daily updater is responsible for
loading data, calling this, persisting alerts to alerts_history.json, and
posting CRITICAL + ACTION alerts to Slack.

Run as a script to evaluate Dave's currently-open positions against live
market data:

    python scripts/alerts_engine.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

# Make sibling modules importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_model import (
    Alert,
    AlertScope,
    Leg,
    OpenPosition,
    Portfolio,
    Severity,
)


# --- Regime-aware thresholds ------------------------------------------------

DEPLOYMENT_CAPS = {"bull": 60.0, "neutral": 55.0, "bear": 50.0}
CASH_RESERVE_TARGETS = {"bull": 25.0, "neutral": 30.0, "bear": 40.0}


# --- DTE — NYSE trading days ------------------------------------------------

_nyse_cal = None


def _trading_days_between(start: date, end: date) -> int:
    """Number of NYSE trading days from `start` (exclusive) through `end` (inclusive).

    Uses pandas-market-calendars when available, falls back to a Mon-Fri
    count (which over-counts by ~9 holidays per year, acceptable for DTE
    alerts where the floor/ceiling are integer days apart).
    """
    if end <= start:
        return 0
    global _nyse_cal
    try:
        if _nyse_cal is None:
            import pandas_market_calendars as mcal  # type: ignore
            _nyse_cal = mcal.get_calendar("NYSE")
        # Calendar is inclusive on both ends; we want exclusive start.
        from datetime import timedelta
        schedule = _nyse_cal.schedule(
            start_date=(start + timedelta(days=1)).isoformat(),
            end_date=end.isoformat(),
        )
        return len(schedule)
    except Exception:
        # Fallback: Mon-Fri count, no holiday adjustment.
        from datetime import timedelta
        count = 0
        d = start
        while d < end:
            d += timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return count


def dte(expiration: date, today: date) -> int:
    """Days to expiration, counted as NYSE trading days."""
    return _trading_days_between(today, expiration)


# --- Computed metrics -------------------------------------------------------


def collateral(position: OpenPosition) -> float:
    if position.leg == Leg.CSP:
        return position.strike * 100 * position.contracts
    return 0.0


def share_value(position: OpenPosition) -> float:
    if position.leg == Leg.CC and position.current_stock_price is not None:
        return position.current_stock_price * 100 * position.contracts
    return 0.0


def cushion_pct(position: OpenPosition) -> Optional[float]:
    """For CSPs: (stock - strike) / strike. Positive = above strike (safe).
    For CCs: (strike - stock) / stock. Positive = below strike (safe).
    Returns None if current_stock_price isn't set.
    """
    if position.current_stock_price is None:
        return None
    if position.leg == Leg.CSP:
        return (position.current_stock_price - position.strike) / position.strike * 100
    else:
        return (position.strike - position.current_stock_price) / position.current_stock_price * 100


def pct_premium_captured(position: OpenPosition) -> Optional[float]:
    """100% means option price has decayed to zero (max profit captured)."""
    if position.current_option_price is None:
        return None
    if position.entry_premium_per_share <= 0:
        return None
    return (
        (position.entry_premium_per_share - position.current_option_price)
        / position.entry_premium_per_share
        * 100
    )


def position_pct_of_portfolio(
    position: OpenPosition, portfolio: Portfolio
) -> float:
    """For CSPs: collateral as a share of total. For CCs: share value as a share."""
    if portfolio.total_account_value <= 0:
        return 0.0
    if position.leg == Leg.CSP:
        return collateral(position) / portfolio.total_account_value * 100
    return share_value(position) / portfolio.total_account_value * 100


def is_itm(position: OpenPosition) -> Optional[bool]:
    if position.current_stock_price is None:
        return None
    if position.leg == Leg.CSP:
        return position.current_stock_price < position.strike
    return position.current_stock_price > position.strike


# --- Alert factory ----------------------------------------------------------


def _make_alert(
    *,
    rule_id: str,
    severity: Severity,
    scope: AlertScope,
    conservative_advice: str,
    consider: str,
    position: Optional[OpenPosition] = None,
    computed_state: Optional[dict] = None,
) -> Alert:
    return Alert(
        alert_id=uuid4(),
        fired_at=datetime.now(),
        rule_id=rule_id,
        severity=severity,
        scope=scope,
        position_id=position.id if position else None,
        ticker=position.ticker if position else None,
        computed_state=computed_state or {},
        conservative_advice=conservative_advice,
        consider=consider,
        slack_posted=False,
    )


# --- CSP rules --------------------------------------------------------------


def _csp_rules(
    p: OpenPosition, portfolio: Portfolio, today: date
) -> Alert:
    """Evaluate CSP rules in priority order. Returns the first match.

    Computes a single shared state dict, attaches it to whichever alert fires.
    """
    d = dte(p.expiration_date, today)
    cush = cushion_pct(p)
    capt = pct_premium_captured(p)
    pos_pct = position_pct_of_portfolio(p, portfolio)
    itm = is_itm(p)

    state = {
        "dte": d,
        "stock_price": p.current_stock_price,
        "strike": p.strike,
        "cushion_pct": round(cush, 2) if cush is not None else None,
        "pct_premium_captured": round(capt, 2) if capt is not None else None,
        "position_pct_of_portfolio": round(pos_pct, 2),
        "is_itm": itm,
    }

    rules = portfolio.rules

    # 1. CRITICAL — Earnings inside DTE
    if p.earnings_date and p.earnings_date < p.expiration_date and p.earnings_date >= today:
        return _make_alert(
            rule_id="csp_earnings_in_cycle",
            severity=Severity.CRITICAL,
            scope=AlertScope.POSITION,
            position=p,
            computed_state={**state, "earnings_date": p.earnings_date.isoformat()},
            conservative_advice="Close the position now to eliminate earnings risk.",
            consider="Roll out to a date past earnings for a net credit if fundamentals are intact and the new strike still works as a buy price.",
        )

    # 2. CRITICAL — Position >10% of portfolio
    if pos_pct > 10:
        return _make_alert(
            rule_id="csp_overweight",
            severity=Severity.CRITICAL,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Close the position to bring concentration back inside the rule.",
            consider="Hold to expiration and don't add to this name, if the 3-check framework passes and the stress test still covers simultaneous assignment.",
        )

    # 3. ACTION — Deep ITM (>8% below strike) at >7 DTE
    if cush is not None and cush < -8 and d > 7:
        return _make_alert(
            rule_id="csp_deep_itm",
            severity=Severity.ACTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Hold and reassess at 14 DTE if all three checks pass (fundamentals intact, sizing safe, still want the stock).",
            consider="Roll down-and-out for a net credit if a lower strike that still works as a buy price pays enough; close if any check fails.",
        )

    # 4. ACTION — ITM at 7–14 DTE
    if itm and 7 <= d <= 14:
        return _make_alert(
            rule_id="csp_itm_decision_window",
            severity=Severity.ACTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Hold to expiration and accept assignment (this is Phase 2 activating, not failure).",
            consider="Roll down-and-out for a net credit if a better entry price is available; close immediately if fundamentals have broken.",
        )

    # 5. ACTION — <7 DTE and ITM
    if itm and d < 7:
        return _make_alert(
            rule_id="csp_itm_imminent",
            severity=Severity.ACTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Accept assignment; rolling this late rarely justifies the friction.",
            consider="Close to take the loss only if the company materially changed or sizing has shifted into the danger zone.",
        )

    # 6. ACTION — Premium threshold captured at >min_dte_for_profit_close
    if (
        capt is not None
        and capt >= rules.csp_profit_threshold_pct
        and d > rules.min_dte_for_profit_close
    ):
        return _make_alert(
            rule_id="csp_profit_threshold",
            severity=Severity.ACTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Close to lock the credit and free the collateral for the next setup.",
            consider="Let it run to expiration only if remaining premium is meaningful relative to time left and you don't need the cash elsewhere.",
        )

    # 7. CAUTION — Within 3% of strike (CSP: stock just above strike)
    if cush is not None and 0 <= cush <= 3:
        return _make_alert(
            rule_id="csp_near_the_money",
            severity=Severity.CAUTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="No action today; monitor on the next daily update.",
            consider="Pre-answer the 3 checks now so the next alert finds you ready.",
        )

    # 8. INFO — Premium milestone captured
    if (
        capt is not None
        and capt >= rules.csp_info_milestone_pct
        and d > rules.min_dte_for_profit_close
    ):
        return _make_alert(
            rule_id="csp_profit_milestone",
            severity=Severity.INFO,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="No action; useful for tracking decay rhythm.",
            consider="Note the position; if it continues toward the action threshold, the close decision is ready.",
        )

    # 9. INFO — 14 DTE mid-cycle check
    if 14 <= d <= 16:
        return _make_alert(
            rule_id="csp_14_dte_check",
            severity=Severity.INFO,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="No action if cushion and sizing are fine.",
            consider="Roll forward early for credit if a cleaner setup is available at better terms.",
        )

    # 10. OK
    return _make_alert(
        rule_id="csp_ok",
        severity=Severity.OK,
        scope=AlertScope.POSITION,
        position=p,
        computed_state=state,
        conservative_advice="No action.",
        consider="",
    )


# --- CC rules ---------------------------------------------------------------


def _cc_rules(
    p: OpenPosition, portfolio: Portfolio, today: date
) -> Alert:
    d = dte(p.expiration_date, today)
    cush = cushion_pct(p)
    capt = pct_premium_captured(p)
    pos_pct = position_pct_of_portfolio(p, portfolio)
    itm = is_itm(p)

    state = {
        "dte": d,
        "stock_price": p.current_stock_price,
        "strike": p.strike,
        "cost_basis": p.cost_basis,
        "cushion_pct": round(cush, 2) if cush is not None else None,
        "pct_premium_captured": round(capt, 2) if capt is not None else None,
        "position_pct_of_portfolio": round(pos_pct, 2),
        "is_itm": itm,
    }

    rules = portfolio.rules

    # 1. CRITICAL — Strike below cost basis without intent
    from data_model import Intent
    if (
        p.cost_basis is not None
        and p.strike < p.cost_basis
        and p.intent != Intent.BASIS_REPAIR
    ):
        return _make_alert(
            rule_id="cc_below_basis_no_intent",
            severity=Severity.CRITICAL,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Buy back the call to restore exit flexibility — selling below basis without intent locks a loss.",
            consider="Hold and re-tag the position intent=basis_repair if you deliberately chose this per the Basis-Strike Matrix in the CC doc.",
        )

    # 2. ACTION — Premium threshold captured
    if capt is not None and capt >= rules.cc_profit_threshold_pct:
        return _make_alert(
            rule_id="cc_profit_threshold",
            severity=Severity.ACTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Buy back the call to bank the credit and reset to a fresh CC.",
            consider="Let it run only if shares are well below strike, time is short, and remaining premium isn't worth the round-trip.",
        )

    # 3. ACTION — ITM at <7 DTE
    if itm and d < 7:
        return _make_alert(
            rule_id="cc_itm_imminent",
            severity=Severity.ACTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="Let shares be called away at the agreed strike — this was the planned exit.",
            consider="Roll up-and-out for a net credit only if the new strike is above cost basis AND you still want to hold the shares longer.",
        )

    # 4. CAUTION — Within 3% of strike from below (CC: stock approaching strike)
    if cush is not None and 0 <= cush <= 3:
        return _make_alert(
            rule_id="cc_near_the_money",
            severity=Severity.CAUTION,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="No action; let the planned exit play out.",
            consider="Roll up-and-out for credit if you genuinely want to extend the holding period.",
        )

    # 5. INFO — 14 DTE mid-cycle check
    if 14 <= d <= 16:
        return _make_alert(
            rule_id="cc_14_dte_check",
            severity=Severity.INFO,
            scope=AlertScope.POSITION,
            position=p,
            computed_state=state,
            conservative_advice="No action.",
            consider="Roll forward for credit if better terms are available.",
        )

    # 6. OK
    return _make_alert(
        rule_id="cc_ok",
        severity=Severity.OK,
        scope=AlertScope.POSITION,
        position=p,
        computed_state=state,
        conservative_advice="No action.",
        consider="",
    )


# --- Portfolio-level rules --------------------------------------------------


def _portfolio_rules(
    portfolio: Portfolio, positions: list[OpenPosition], today: date
) -> list[Alert]:
    alerts: list[Alert] = []
    total = portfolio.total_account_value
    cash = portfolio.cash_available

    csp_collateral_total = sum(collateral(p) for p in positions if p.leg == Leg.CSP)
    share_value_total = sum(share_value(p) for p in positions if p.leg == Leg.CC)
    deployed = csp_collateral_total + share_value_total
    deployment_pct = deployed / total * 100 if total > 0 else 0
    cash_pct = cash / total * 100 if total > 0 else 0

    regime = portfolio.market_regime.value
    deployment_cap = DEPLOYMENT_CAPS.get(regime, 60.0)

    base_state = {
        "total_account_value": total,
        "cash_available": cash,
        "csp_collateral_total": csp_collateral_total,
        "share_value_total": share_value_total,
        "deployment_pct": round(deployment_pct, 2),
        "cash_pct": round(cash_pct, 2),
        "regime": regime,
    }

    # 1. ACTION — Cash reserve <20%
    if cash_pct < 20:
        alerts.append(_make_alert(
            rule_id="portfolio_cash_low",
            severity=Severity.ACTION,
            scope=AlertScope.PORTFOLIO,
            computed_state=base_state,
            conservative_advice="Don't open new positions until the reserve is rebuilt to ≥25%.",
            consider="Close the lowest-conviction position to restore reserve faster if a fresh setup is worth the swap.",
        ))

    # 2. ACTION — Total deployment over cap
    if deployment_pct > deployment_cap:
        alerts.append(_make_alert(
            rule_id="portfolio_overdeployed",
            severity=Severity.ACTION,
            scope=AlertScope.PORTFOLIO,
            computed_state={**base_state, "deployment_cap": deployment_cap},
            conservative_advice="Stop opening new positions; let existing positions expire or close naturally.",
            consider="Trim the position with the smallest cushion if a clearly better setup needs the capital.",
        ))

    # 3. ACTION — Sector concentration
    sector_totals: dict[str, float] = defaultdict(float)
    for p in positions:
        sector = p.sector_override or p.sector or "Unknown"
        if p.leg == Leg.CSP:
            sector_totals[sector] += collateral(p)
        else:
            sector_totals[sector] += share_value(p)

    max_pct = portfolio.sector_limits.max_per_sector_pct
    for sector, sector_value in sector_totals.items():
        sector_pct = sector_value / total * 100 if total > 0 else 0
        if sector_pct > max_pct:
            alerts.append(_make_alert(
                rule_id="portfolio_sector_overweight",
                severity=Severity.ACTION,
                scope=AlertScope.PORTFOLIO,
                computed_state={
                    **base_state,
                    "sector": sector,
                    "sector_pct": round(sector_pct, 2),
                    "max_per_sector_pct": max_pct,
                },
                conservative_advice=f"No new positions in {sector}.",
                consider=f"Close the most-profitable position in {sector} to rebalance, if it's near a natural exit anyway.",
            ))

    # 4. CRITICAL — Stress test fail
    if csp_collateral_total > cash:
        gap = csp_collateral_total - cash
        alerts.append(_make_alert(
            rule_id="portfolio_stress_test_fail",
            severity=Severity.CRITICAL,
            scope=AlertScope.PORTFOLIO,
            computed_state={**base_state, "shortfall": gap},
            conservative_advice="Close the lowest-conviction CSP to restore coverable assignment capacity.",
            consider="Add outside cash to the account before the next assignment-risk window if a roll-rather-than-close path is materially better.",
        ))

    return alerts


# --- Public API -------------------------------------------------------------


def evaluate(
    portfolio: Portfolio,
    positions: list[OpenPosition],
    today: Optional[date] = None,
) -> list[Alert]:
    """Run every rule. Return all alerts that fire.

    Per-position: highest-priority match per position.
    Portfolio-level: every rule that triggers.
    """
    if today is None:
        today = date.today()

    alerts: list[Alert] = []

    for p in positions:
        if p.leg == Leg.CSP:
            alerts.append(_csp_rules(p, portfolio, today))
        else:
            alerts.append(_cc_rules(p, portfolio, today))

    alerts.extend(_portfolio_rules(portfolio, positions, today))

    # Sort by severity for stable output
    severity_order = {
        Severity.CRITICAL: 0,
        Severity.ACTION: 1,
        Severity.CAUTION: 2,
        Severity.INFO: 3,
        Severity.OK: 4,
    }
    alerts.sort(key=lambda a: (severity_order[a.severity], a.rule_id))
    return alerts


# --- Smoke test -------------------------------------------------------------


def _smoke_test() -> None:
    """End-to-end: load positions, refresh with live market data, evaluate, print."""
    import logging
    from data_model import load_portfolio, load_open_positions
    import market_data

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    portfolio = load_portfolio()
    positions_file = load_open_positions()
    positions = positions_file.positions

    if not positions:
        print("No open positions. Add some through chat to test the engine.")
        return

    print(f"Refreshing market data for {len(positions)} positions...")
    for p in positions:
        refresh = market_data.refresh_position_market_data(p.model_dump(mode="json"))
        p.current_stock_price = refresh.get("current_stock_price")
        p.current_option_price = refresh.get("current_option_price")
        # Pull earnings date if not already set
        if p.earnings_date is None:
            p.earnings_date = market_data.get_next_earnings_date(p.ticker)

    today = date.today()
    print(f"Evaluating against today = {today}\n")

    # Per-position summary
    print("PER-POSITION:")
    print(f"{'TICKER':<8} {'LEG':<4} {'DTE':>4} {'POS%':>6} {'CUSH%':>7} "
          f"{'CAPT%':>7} {'SEV':<8} {'RULE':<30}")
    print("-" * 84)
    alerts = evaluate(portfolio, positions, today)
    pos_alerts = [a for a in alerts if a.scope == AlertScope.POSITION]
    port_alerts = [a for a in alerts if a.scope == AlertScope.PORTFOLIO]

    # Build a lookup by position_id for pretty-printing
    by_pid = {a.position_id: a for a in pos_alerts}
    for p in positions:
        a = by_pid.get(p.id)
        if a is None:
            continue
        s = a.computed_state
        d_str = str(s.get("dte"))
        pp = s.get("position_pct_of_portfolio")
        pp_str = f"{pp:.1f}" if pp is not None else "—"
        cu = s.get("cushion_pct")
        cu_str = f"{cu:+.1f}" if cu is not None else "—"
        ca = s.get("pct_premium_captured")
        ca_str = f"{ca:+.1f}" if ca is not None else "—"
        print(f"{p.ticker:<8} {p.leg.value:<4} {d_str:>4} {pp_str:>6} "
              f"{cu_str:>7} {ca_str:>7} {a.severity.value:<8} {a.rule_id:<30}")

    print()
    print("PORTFOLIO-LEVEL:")
    if not port_alerts:
        print("  (none)")
    for a in port_alerts:
        print(f"  [{a.severity.value}] {a.rule_id}")
        print(f"    Conservative Advice: {a.conservative_advice}")
        if a.consider:
            print(f"    Consider: {a.consider}")

    print()
    n_critical = sum(1 for a in alerts if a.severity == Severity.CRITICAL)
    n_action = sum(1 for a in alerts if a.severity == Severity.ACTION)
    n_caution = sum(1 for a in alerts if a.severity == Severity.CAUTION)
    n_info = sum(1 for a in alerts if a.severity == Severity.INFO)
    n_ok = sum(1 for a in alerts if a.severity == Severity.OK)
    print(
        f"Summary: {n_critical} CRITICAL · {n_action} ACTION · "
        f"{n_caution} CAUTION · {n_info} INFO · {n_ok} OK"
    )
    print()
    print(f"Slack-worthy (CRITICAL + ACTION): {n_critical + n_action}")


if __name__ == "__main__":
    _smoke_test()
