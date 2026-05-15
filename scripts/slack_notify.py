"""Slack formatter — converts an alert list into a single Slack message.

The Python script does NOT call Slack directly. It writes the formatted
message to `data/slack_message.txt`. The Cowork Scheduled Task that
invokes this script then reads that file and posts it via the Slack MCP.

This separation keeps Slack credentials out of code and lets Claude add
any context-specific framing before the post goes out.

Severity routing:
- CRITICAL + ACTION are included in the Slack message
- CAUTION + INFO + OK live on the dashboard only

Run as a script to preview the Slack message for the current state:

    python scripts/slack_notify.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

# Make sibling modules importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_model import Alert, AlertScope, Severity, REPO_ROOT


SLACK_MESSAGE_PATH = REPO_ROOT / "data" / "slack_message.txt"

SLACK_WORTHY = {Severity.CRITICAL, Severity.ACTION}

SEVERITY_HEADER = {
    Severity.CRITICAL: ":rotating_light: *CRITICAL*",
    Severity.ACTION: ":warning: *ACTION*",
}

SEVERITY_ORDER = [Severity.CRITICAL, Severity.ACTION]


def _format_position_label(alert: Alert) -> str:
    """E.g., 'TSLA $390 PUT' or 'PORTFOLIO'."""
    if alert.scope == AlertScope.PORTFOLIO:
        return "PORTFOLIO"
    state = alert.computed_state or {}
    strike = state.get("strike")
    leg = "PUT"  # Phase 2 supports CSPs only against real positions; CC label could be added when CC alerts appear
    # We don't have leg in computed_state; infer from rule_id prefix
    if alert.rule_id and alert.rule_id.startswith("cc_"):
        leg = "CALL"
    if alert.ticker and strike is not None:
        return f"{alert.ticker} ${strike:g} {leg}"
    return alert.ticker or "POSITION"


def _format_context(alert: Alert) -> str:
    """A compact one-line summary of the state."""
    s = alert.computed_state or {}
    bits = []
    if alert.scope == AlertScope.POSITION:
        if (d := s.get("dte")) is not None:
            bits.append(f"{d} DTE")
        if (c := s.get("cushion_pct")) is not None:
            sign = "+" if c >= 0 else ""
            bits.append(f"cushion {sign}{c:.1f}%")
        if (p := s.get("pct_premium_captured")) is not None:
            bits.append(f"captured {p:+.0f}%")
        if (pp := s.get("position_pct_of_portfolio")) is not None:
            bits.append(f"{pp:.1f}% of portfolio")
    else:  # portfolio
        if "sector" in s:
            bits.append(f"{s['sector']} at {s['sector_pct']:.1f}%")
        if "shortfall" in s:
            bits.append(f"shortfall ${s['shortfall']:,.0f}")
        if "deployment_pct" in s and "deployment_cap" in s:
            bits.append(f"deployed {s['deployment_pct']:.1f}% vs {s['deployment_cap']:.0f}% cap")
        if "cash_pct" in s and alert.rule_id == "portfolio_cash_low":
            bits.append(f"cash reserve {s['cash_pct']:.1f}%")
    return " · ".join(bits)


def format_message(
    alerts: Iterable[Alert],
    *,
    as_of: Optional[date] = None,
    catchup_days: Optional[list[date]] = None,
) -> str:
    """Build the full consolidated Slack message text.

    Returns an empty string if no Slack-worthy alerts fired (script will
    skip the post).
    """
    if as_of is None:
        as_of = date.today()

    slack_alerts = [a for a in alerts if a.severity in SLACK_WORTHY]
    if not slack_alerts:
        return ""

    # Header
    n_crit = sum(1 for a in slack_alerts if a.severity == Severity.CRITICAL)
    n_act = sum(1 for a in slack_alerts if a.severity == Severity.ACTION)
    parts: list[str] = []
    date_str = as_of.strftime("%A, %B %-d, %Y") if sys.platform != "win32" else as_of.strftime("%A, %B %#d, %Y")
    parts.append(f":dart: *Options Wheel Coach — {date_str}*")
    summary = []
    if n_crit:
        summary.append(f"{n_crit} CRITICAL")
    if n_act:
        summary.append(f"{n_act} ACTION")
    parts.append(" · ".join(summary) + " requiring attention")

    if catchup_days:
        days_str = ", ".join(d.strftime("%b %-d" if sys.platform != "win32" else "%b %#d") for d in catchup_days)
        parts.append(f"_Catching up missed market days: {days_str}_")

    parts.append("")

    # Group by severity, in priority order
    by_sev: dict[Severity, list[Alert]] = {Severity.CRITICAL: [], Severity.ACTION: []}
    for a in slack_alerts:
        by_sev[a.severity].append(a)

    for sev in SEVERITY_ORDER:
        if not by_sev[sev]:
            continue
        parts.append(SEVERITY_HEADER[sev])
        for a in by_sev[sev]:
            label = _format_position_label(a)
            context = _format_context(a)
            heading = f"*{label} — {a.rule_id}*"
            if context:
                heading += f"  _({context})_"
            parts.append(f"• {heading}")
            parts.append(f"    *Conservative Advice:* {a.conservative_advice}")
            if a.consider:
                parts.append(f"    _Consider:_ {a.consider}")
        parts.append("")

    parts.append("_Ping Claude in chat for the 3-check walkthrough on any item._")
    return "\n".join(parts)


def write_message(text: str, path: Path = SLACK_MESSAGE_PATH) -> None:
    """Write the message to disk for the Cowork scheduled task to pick up.

    Always overwrites — there's only ever one current message to post.
    An empty `text` clears the file (signals "no Slack post needed today").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- Smoke test -------------------------------------------------------------


def _smoke_test() -> None:
    """Format a Slack message from a fresh evaluation against real positions."""
    import logging
    from data_model import load_portfolio, load_open_positions
    import market_data
    import alerts_engine

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    portfolio = load_portfolio()
    positions = load_open_positions().positions
    for p in positions:
        refresh = market_data.refresh_position_market_data(p.model_dump(mode="json"))
        p.current_stock_price = refresh.get("current_stock_price")
        p.current_option_price = refresh.get("current_option_price")
        if p.earnings_date is None:
            p.earnings_date = market_data.get_next_earnings_date(p.ticker)

    alerts = alerts_engine.evaluate(portfolio, positions)
    message = format_message(alerts)

    print("=" * 70)
    if message:
        print(message)
    else:
        print("(no Slack-worthy alerts — nothing would be posted today)")
    print("=" * 70)
    print()
    print(f"Length: {len(message)} chars")
    print(f"Would write to: {SLACK_MESSAGE_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    _smoke_test()
