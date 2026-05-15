"""Market data layer — wraps yfinance for daily refresh.

Pure functions that take simple inputs (ticker, strike, expiration, date) and
return prices and metadata. No knowledge of the position schema — that lives
in data_model.py. The daily updater orchestrates between the two.

Every external call is wrapped defensively. yfinance can return empty frames,
NaN values, missing keys, and HTTP errors. The functions return None when
data isn't available rather than raising — the alert engine treats missing
data as "skip price-based rules for this position" rather than as a failure.

Per-run caching: a yf.Ticker object is cached per ticker for the lifetime of
the process, so repeated lookups within a daily run don't hit yfinance twice.

Run as a script to test against Dave's currently-open positions:

    python scripts/market_data.py
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf

# Make sibling modules importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

log = logging.getLogger(__name__)

# --- Per-run cache ----------------------------------------------------------

_ticker_cache: dict[str, yf.Ticker] = {}
_sector_cache: dict[str, Optional[str]] = {}
_earnings_cache: dict[str, Optional[date]] = {}


def _get_ticker(ticker: str) -> yf.Ticker:
    ticker = ticker.upper().strip()
    if ticker not in _ticker_cache:
        _ticker_cache[ticker] = yf.Ticker(ticker)
    return _ticker_cache[ticker]


def clear_cache() -> None:
    """Reset the per-run cache. Useful for tests."""
    _ticker_cache.clear()
    _sector_cache.clear()
    _earnings_cache.clear()


# --- Stock data -------------------------------------------------------------


def get_stock_close(
    ticker: str, as_of: Optional[date] = None
) -> Optional[float]:
    """Closing price for `ticker` on `as_of` (or latest if None).

    yfinance returns daily candles. If `as_of` is None, we pull the last 5
    days and take the most recent close. If `as_of` is a past date, we pull
    a window around it and select the matching row.
    """
    try:
        t = _get_ticker(ticker)
        if as_of is None:
            hist = t.history(period="5d", auto_adjust=False)
            if hist.empty:
                log.warning(f"{ticker}: no recent history returned")
                return None
            return float(hist["Close"].iloc[-1])

        # Historical fetch — pull a small window around the target date
        start = as_of - timedelta(days=4)
        end = as_of + timedelta(days=1)
        hist = t.history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
        )
        if hist.empty:
            log.warning(f"{ticker}: no history for {as_of}")
            return None

        # Find the exact date or the closest prior trading day
        hist.index = hist.index.date  # convert to date for comparison
        matches = hist[hist.index <= as_of]
        if matches.empty:
            return None
        return float(matches["Close"].iloc[-1])
    except Exception as e:
        log.error(f"{ticker}: get_stock_close failed: {e}")
        return None


def get_sector(ticker: str) -> Optional[str]:
    """Canonical sector name from yfinance.

    Values come from Yahoo's classification (e.g., 'Technology',
    'Consumer Cyclical', 'Communication Services'). Cached per run.
    """
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    try:
        t = _get_ticker(ticker)
        info = t.info or {}
        sector = info.get("sector")
        _sector_cache[ticker] = sector
        return sector
    except Exception as e:
        log.error(f"{ticker}: get_sector failed: {e}")
        _sector_cache[ticker] = None
        return None


def get_next_earnings_date(ticker: str) -> Optional[date]:
    """Next earnings announcement date, or None if not available.

    yfinance exposes earnings via .calendar (dict) and .earnings_dates
    (DataFrame). We try both, prefer the soonest future date.
    """
    if ticker in _earnings_cache:
        return _earnings_cache[ticker]

    result: Optional[date] = None
    today = date.today()

    try:
        t = _get_ticker(ticker)

        # Try .calendar first (simpler shape)
        try:
            cal = t.calendar
        except Exception:
            cal = None

        if cal and isinstance(cal, dict):
            earnings = cal.get("Earnings Date")
            if earnings:
                if isinstance(earnings, list) and earnings:
                    result = _to_date(earnings[0])
                else:
                    result = _to_date(earnings)

        # Fall back to .earnings_dates
        if result is None:
            try:
                df = t.earnings_dates
                if df is not None and not df.empty:
                    future = df[df.index.date >= today]
                    if not future.empty:
                        result = future.index.date[-1]
            except Exception:
                pass

    except Exception as e:
        log.error(f"{ticker}: get_next_earnings_date failed: {e}")

    _earnings_cache[ticker] = result
    return result


def _to_date(value) -> Optional[date]:
    """Coerce various date-ish values to a `date`."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


# --- Options data -----------------------------------------------------------


def get_option_quote(
    ticker: str,
    strike: float,
    expiration: date,
    right: str,  # 'put' or 'call'
) -> Optional[dict]:
    """Latest quote for a specific options contract.

    Returns a dict with keys: bid, ask, mid, last, volume, open_interest, iv,
    or None if the contract isn't available in the chain.

    `right` must be either 'put' or 'call' (case-insensitive).
    """
    right = right.lower().strip()
    if right not in ("put", "call"):
        raise ValueError(f"right must be 'put' or 'call', got {right!r}")

    try:
        t = _get_ticker(ticker)
        exp_str = expiration.isoformat()

        # yfinance .options is a tuple of expiration strings
        try:
            available = t.options
        except Exception as e:
            log.error(f"{ticker}: could not fetch options list: {e}")
            return None

        if exp_str not in available:
            log.warning(
                f"{ticker} {expiration}: expiration not in chain; "
                f"available: {available[:5]}..."
            )
            return None

        chain = t.option_chain(exp_str)
        df = chain.puts if right == "put" else chain.calls

        # Match strike. Float equality is brittle; round to handle Yahoo's
        # occasional 369.99999 weirdness.
        matches = df[df["strike"].round(2) == round(strike, 2)]
        if matches.empty:
            log.warning(
                f"{ticker} {strike} {right} {expiration}: strike not in chain"
            )
            return None

        row = matches.iloc[0]

        bid = _safe_float(row.get("bid"))
        ask = _safe_float(row.get("ask"))
        last = _safe_float(row.get("lastPrice"))
        mid = None
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        elif last is not None and last > 0:
            mid = last

        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": last,
            "volume": _safe_int(row.get("volume")),
            "open_interest": _safe_int(row.get("openInterest")),
            "iv": _safe_float(row.get("impliedVolatility")),
        }
    except Exception as e:
        log.error(
            f"{ticker} {strike} {right} {expiration}: get_option_quote failed: {e}"
        )
        return None


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    try:
        i = int(v)
        return i
    except (TypeError, ValueError):
        return None


# --- High-level convenience -------------------------------------------------


def refresh_position_market_data(
    position: dict, today: Optional[date] = None
) -> dict:
    """Return the daily-update fields for a position.

    Takes a position dict (raw JSON-shaped), fetches current_stock_price,
    current_option_price, and last_updated. Returns a dict suitable for
    merging into the position record.

    **current_option_price uses the ASK price**, not the mid. For a short
    option position (every CSP and CC in the Wheel), closing means buying
    to close — you pay the ask. Using ask everywhere gives realistic
    realizable values for both display ("what's my position worth right
    now?") and decisions ("should I take the 80%-captured close?").

    Fallback order: ask → mid → last. Mid and last are stored in the
    snapshot's option_quote field for reference but not used as the price
    of record.

    Does NOT fetch delta — yfinance doesn't reliably expose Greeks, and
    the alert engine doesn't depend on real-time delta.
    """
    ticker = position["ticker"]
    strike = position["strike"]
    expiration = _to_date(position["expiration_date"])
    right = "put" if position["leg"] == "CSP" else "call"

    out = {
        "current_stock_price": None,
        "current_option_price": None,
        "current_delta": None,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }

    stock = get_stock_close(ticker, today)
    if stock is not None:
        out["current_stock_price"] = stock

    quote = get_option_quote(ticker, strike, expiration, right)
    if quote:
        # Ask is the price to close a short option position.
        # Fall back to mid, then last, if ask isn't quoted today.
        price = (
            quote.get("ask")
            if quote.get("ask") is not None and quote.get("ask") > 0
            else None
        )
        if price is None and quote.get("mid") is not None and quote.get("mid") > 0:
            price = quote["mid"]
        if price is None and quote.get("last") is not None and quote.get("last") > 0:
            price = quote["last"]
        out["current_option_price"] = price

    return out


# --- Smoke test -------------------------------------------------------------


def _smoke_test() -> None:
    """Run market data lookups against Dave's currently-open positions."""
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo_root = Path(__file__).resolve().parent.parent
    positions_path = repo_root / "data" / "positions_open.json"

    with positions_path.open("r", encoding="utf-8") as fh:
        positions = json.load(fh)["positions"]

    if not positions:
        print("No open positions to test against.")
        return

    unique_tickers = sorted({p["ticker"] for p in positions})
    print(f"Testing market data for {len(unique_tickers)} tickers, "
          f"{len(positions)} positions.\n")

    # 1. Stock close + sector + earnings per ticker
    print(f"{'TICKER':<8} {'CLOSE':>10}  {'SECTOR':<30} {'EARNINGS':<12}")
    print("-" * 64)
    for ticker in unique_tickers:
        close = get_stock_close(ticker)
        sector = get_sector(ticker)
        earnings = get_next_earnings_date(ticker)
        close_s = f"${close:.2f}" if close is not None else "—"
        sector_s = sector or "—"
        earnings_s = earnings.isoformat() if earnings else "—"
        print(f"{ticker:<8} {close_s:>10}  {sector_s:<30} {earnings_s:<12}")

    # 2. Option price per position
    print()
    print(f"{'TICKER':<8} {'STRIKE':>8} {'EXP':<12} {'RIGHT':<5} "
          f"{'BID':>7} {'ASK':>7} {'MID':>7} {'LAST':>7} {'IV':>7}")
    print("-" * 80)
    for p in positions:
        right = "put" if p["leg"] == "CSP" else "call"
        exp = _to_date(p["expiration_date"])
        q = get_option_quote(p["ticker"], p["strike"], exp, right)
        bid = f"{q['bid']:.2f}" if q and q.get("bid") is not None else "—"
        ask = f"{q['ask']:.2f}" if q and q.get("ask") is not None else "—"
        mid = f"{q['mid']:.2f}" if q and q.get("mid") is not None else "—"
        last = f"{q['last']:.2f}" if q and q.get("last") is not None else "—"
        iv = f"{q['iv']:.2%}" if q and q.get("iv") is not None else "—"
        print(
            f"{p['ticker']:<8} {p['strike']:>8} {exp.isoformat():<12} "
            f"{right:<5} {bid:>7} {ask:>7} {mid:>7} {last:>7} {iv:>7}"
        )

    print()
    print("If everything above looks reasonable, the market data layer is ready.")


if __name__ == "__main__":
    _smoke_test()
