"""Daily update — the entry point for the Cowork scheduled task.

End-to-end orchestration of one daily run:

  1. Load portfolio + open positions
  2. Refresh market data for each position (yfinance)
  3. Run the alert engine
  4. Write today's snapshot
  5. Append fired alerts to alerts_history
  6. Format the Slack message and write to data/slack_message.txt
  7. Regenerate dashboard/data.js so the dashboard sees the new state

Catch-up behavior: if snapshots are missing for prior market days, the
script backfills stock-based metrics for each missed day (option prices
are best-effort and only stored for today, per market_data.py).

Usage:

    python scripts/daily_update.py             # normal run for today
    python scripts/daily_update.py --dry-run   # evaluate but don't write
    python scripts/daily_update.py --no-slack  # skip writing slack message

The Cowork scheduled task invokes this with no flags. After the script
completes, the task reads data/slack_message.txt and posts it (if
non-empty) to #all-personal-workspace via the Slack MCP.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Make sibling modules importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alerts_engine
import build_dashboard_data
import market_data
import slack_notify
from data_model import (
    ALERTS_HISTORY_PATH,
    REPO_ROOT,
    SNAPSHOTS_DIR,
    Alert,
    AlertsHistoryFile,
    OpenPosition,
    Portfolio,
    Severity,
    Snapshot,
    SnapshotComputed,
    load_open_positions,
    load_portfolio,
)
from alerts_engine import (
    collateral as compute_collateral,
    share_value as compute_share_value,
)


log = logging.getLogger("daily_update")


# --- Catch-up helpers -------------------------------------------------------


def _latest_snapshot_date() -> Optional[date]:
    if not SNAPSHOTS_DIR.exists():
        return None
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"))
    if not snaps:
        return None
    try:
        return date.fromisoformat(snaps[-1].stem)
    except ValueError:
        return None


def _trading_days_between(start: date, end: date) -> list[date]:
    """List of NYSE trading dates strictly between `start` and `end` (inclusive of end)."""
    try:
        import pandas_market_calendars as mcal  # type: ignore
        cal = mcal.get_calendar("NYSE")
        schedule = cal.schedule(
            start_date=(start + timedelta(days=1)).isoformat(),
            end_date=end.isoformat(),
        )
        return [d.date() for d in schedule.index]
    except Exception:
        # Fallback: Mon-Fri days, no holiday adjustment.
        out: list[date] = []
        d = start + timedelta(days=1)
        while d <= end:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out


def _missed_days(today: date) -> list[date]:
    """Trading days since the last snapshot, excluding today."""
    last = _latest_snapshot_date()
    if last is None or last >= today:
        return []
    days = _trading_days_between(last, today)
    return [d for d in days if d < today]


# --- Position enrichment ----------------------------------------------------


def _enrich_position(p: OpenPosition, as_of: Optional[date] = None) -> OpenPosition:
    """Fetch market data for a position and merge into the model.

    If `as_of` is None, fetches the latest close + current option price.
    If `as_of` is a past date, fetches the historical close for the stock
    only (option chain history isn't reliable in yfinance).
    """
    if as_of is None:
        refresh = market_data.refresh_position_market_data(p.model_dump(mode="json"))
        p.current_stock_price = refresh.get("current_stock_price")
        p.current_option_price = refresh.get("current_option_price")
        p.last_updated = datetime.now()
    else:
        # Historical: stock only, no option price
        stock = market_data.get_stock_close(p.ticker, as_of)
        p.current_stock_price = stock
        p.current_option_price = None
        p.last_updated = datetime.now()

    if p.earnings_date is None:
        p.earnings_date = market_data.get_next_earnings_date(p.ticker)

    # Normalize sector to yfinance canonical name on first enrichment.
    canonical = market_data.get_sector(p.ticker)
    if canonical and p.sector != canonical:
        # Keep an override if the user explicitly set one
        if not p.sector_override:
            p.sector = canonical

    return p


# --- Snapshot writing -------------------------------------------------------


def _compute_snapshot_totals(
    portfolio: Portfolio, positions: list[OpenPosition]
) -> SnapshotComputed:
    csp = sum(compute_collateral(p) for p in positions)
    share = sum(compute_share_value(p) for p in positions)
    deployed = csp + share
    total = portfolio.total_account_value
    return SnapshotComputed(
        csp_collateral_total=csp,
        share_value_total=share,
        deployment_pct=(deployed / total * 100) if total else 0,
        cash_reserve_pct=(portfolio.cash_available / total * 100) if total else 0,
    )


def _write_snapshot(
    portfolio: Portfolio,
    positions: list[OpenPosition],
    alerts: list[Alert],
    as_of: date,
) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snap = Snapshot(
        as_of=as_of,
        portfolio=portfolio,
        positions=positions,
        computed=_compute_snapshot_totals(portfolio, positions),
        alerts_fired=alerts,
    )
    path = SNAPSHOTS_DIR / f"{as_of.isoformat()}.json"
    path.write_text(json.dumps(snap.model_dump(mode="json"), indent=2, default=str), encoding="utf-8")
    return path


# --- Alerts history ---------------------------------------------------------


def _append_alerts_history(alerts: list[Alert]) -> None:
    """Append today's alerts to the history file."""
    if not alerts:
        return
    if ALERTS_HISTORY_PATH.exists():
        try:
            existing = AlertsHistoryFile.model_validate(
                json.loads(ALERTS_HISTORY_PATH.read_text(encoding="utf-8"))
            )
        except Exception:
            existing = AlertsHistoryFile()
    else:
        existing = AlertsHistoryFile()
    existing.alerts.extend(alerts)
    ALERTS_HISTORY_PATH.write_text(
        json.dumps(existing.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )


# --- Backfill -------------------------------------------------------------


def _backfill_missed_day(
    portfolio: Portfolio, positions: list[OpenPosition], target_day: date
) -> Optional[Path]:
    """Write a snapshot for a missed market day using historical stock prices only.

    Option prices aren't available historically through yfinance, so the
    snapshot for past days records stock-based metrics only. No alerts are
    evaluated for past days — the alert engine acts on today's state only.
    """
    # Deep copy positions so we don't pollute today's run with historical values
    historical = [p.model_copy(deep=True) for p in positions]
    for p in historical:
        _enrich_position(p, as_of=target_day)
    return _write_snapshot(portfolio, historical, alerts=[], as_of=target_day)


# --- Main run ---------------------------------------------------------------


def run(*, dry_run: bool = False, write_slack: bool = True) -> None:
    today = date.today()
    log.info(f"Daily update run for {today}")

    portfolio = load_portfolio()
    positions = load_open_positions().positions
    log.info(f"Loaded {len(positions)} open positions")

    # Catch-up: backfill snapshots for missed market days.
    missed = _missed_days(today)
    if missed:
        log.info(f"Backfilling {len(missed)} missed market day(s): "
                 f"{', '.join(d.isoformat() for d in missed)}")
        for d in missed:
            if not dry_run:
                _backfill_missed_day(portfolio, positions, d)

    # Refresh current market data on the live position records.
    for p in positions:
        _enrich_position(p)

    # Evaluate alerts against today's state.
    alerts = alerts_engine.evaluate(portfolio, positions, today)
    n_critical = sum(1 for a in alerts if a.severity == Severity.CRITICAL)
    n_action = sum(1 for a in alerts if a.severity == Severity.ACTION)
    n_caution = sum(1 for a in alerts if a.severity == Severity.CAUTION)
    n_info = sum(1 for a in alerts if a.severity == Severity.INFO)
    n_ok = sum(1 for a in alerts if a.severity == Severity.OK)
    log.info(
        f"Alerts: {n_critical} CRITICAL · {n_action} ACTION · "
        f"{n_caution} CAUTION · {n_info} INFO · {n_ok} OK"
    )

    # Format the Slack message (non-OK / non-CAUTION / non-INFO get included)
    slack_text = slack_notify.format_message(alerts, as_of=today, catchup_days=missed or None)

    if dry_run:
        log.info("Dry run — not writing files")
        print()
        print("=== Slack message that WOULD be sent ===")
        print(slack_text if slack_text else "(nothing — no CRITICAL/ACTION alerts today)")
        return

    # Persist everything.
    snap_path = _write_snapshot(portfolio, positions, alerts, today)
    log.info(f"Wrote snapshot {snap_path.relative_to(REPO_ROOT)}")

    _append_alerts_history(alerts)
    log.info(f"Appended {len(alerts)} alerts to history")

    if write_slack:
        slack_notify.write_message(slack_text)
        if slack_text:
            log.info(f"Wrote Slack message ({len(slack_text)} chars). "
                     f"Read data/slack_message.txt and post to Slack.")
        else:
            log.info("No Slack-worthy alerts. Slack message cleared.")

    # Rebuild the dashboard data file with today's enriched positions and alerts baked in.
    payload = build_dashboard_data.build(
        alerts=[a.model_dump(mode="json") for a in alerts],
        positions=positions,
    )
    log.info(
        f"Rebuilt dashboard/data.js ({len(payload['positions_open'])} open, "
        f"{len(payload['alerts'])} alerts)"
    )

    log.info("Daily update complete.")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Daily update for Options Wheel Coach.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate but don't write any files.")
    parser.add_argument("--no-slack", action="store_true",
                        help="Skip writing the Slack message file.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    run(dry_run=args.dry_run, write_slack=not args.no_slack)


if __name__ == "__main__":
    _cli()
