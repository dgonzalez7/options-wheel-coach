"""Pydantic schemas for Options Wheel Coach.

Single source of truth for the shape of data on disk. Every load and save of
portfolio.json, positions_open.json, and positions_closed.json passes through
these models, so invalid data fails fast at the boundary.

Run as a script to validate the seeded JSON files:

    python scripts/data_model.py
"""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# --- Repo paths -------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
POSITIONS_OPEN_PATH = DATA_DIR / "positions_open.json"
POSITIONS_CLOSED_PATH = DATA_DIR / "positions_closed.json"
ALERTS_HISTORY_PATH = DATA_DIR / "alerts_history.json"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


# --- Enums ------------------------------------------------------------------


class AccountType(str, Enum):
    TAXABLE = "taxable"
    ROTH_IRA = "roth_ira"
    TRADITIONAL_IRA = "traditional_ira"


class MarketRegime(str, Enum):
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


class Leg(str, Enum):
    CSP = "CSP"
    CC = "CC"


class Intent(str, Enum):
    BASIS_REPAIR = "basis_repair"


class Outcome(str, Enum):
    EXPIRED_WORTHLESS = "expired_worthless"
    CLOSED_AT_PROFIT = "closed_at_profit"
    CLOSED_EARLY_LOSS = "closed_early_loss"
    ASSIGNED = "assigned"
    ROLLED = "rolled"
    CALLED_AWAY = "called_away"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    ACTION = "ACTION"
    CAUTION = "CAUTION"
    INFO = "INFO"
    OK = "OK"


class AlertScope(str, Enum):
    POSITION = "position"
    PORTFOLIO = "portfolio"


# --- Portfolio --------------------------------------------------------------


class PortfolioRules(BaseModel):
    csp_profit_threshold_pct: float = 80
    csp_info_milestone_pct: float = 50
    cc_profit_threshold_pct: float = 50
    min_dte_for_profit_close: int = 14
    commission_per_contract: float = 0.55


class SectorLimits(BaseModel):
    max_per_sector_pct: float = 25
    max_positions_per_sector: int = 3


class Portfolio(BaseModel):
    as_of: date
    account_name: str
    account_type: AccountType = AccountType.TAXABLE
    total_account_value: float = Field(gt=0)
    cash_available: float = Field(ge=0)
    market_regime: MarketRegime = MarketRegime.BULL
    wheel_buffer_pct: float = Field(default=0, ge=0, le=100)
    rules: PortfolioRules = Field(default_factory=PortfolioRules)
    sector_limits: SectorLimits = Field(default_factory=SectorLimits)
    notes: str = ""

    @model_validator(mode="after")
    def cash_le_total(self) -> "Portfolio":
        if self.cash_available > self.total_account_value:
            raise ValueError(
                f"cash_available ({self.cash_available}) cannot exceed "
                f"total_account_value ({self.total_account_value})"
            )
        return self


# --- Positions --------------------------------------------------------------


class OpenPosition(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    ticker: str
    leg: Leg
    strike: float = Field(gt=0)
    contracts: int = Field(gt=0)
    entry_premium_per_share: float = Field(gt=0)
    open_date: date
    expiration_date: date
    entry_stock_price: float = Field(gt=0)
    entry_delta: Optional[float] = None
    sector: Optional[str] = None
    sector_override: Optional[str] = None
    intent: Optional[Intent] = None
    cost_basis: Optional[float] = None
    earnings_date: Optional[date] = None
    linked_assignment_id: Optional[UUID] = None
    notes: str = ""

    # Daily-updated fields (None until first daily run touches the position)
    current_stock_price: Optional[float] = None
    current_option_price: Optional[float] = None
    current_delta: Optional[float] = None
    last_updated: Optional[datetime] = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, v: str) -> str:
        return v.upper().strip()

    @model_validator(mode="after")
    def expiration_after_open(self) -> "OpenPosition":
        if self.expiration_date <= self.open_date:
            raise ValueError(
                f"expiration_date ({self.expiration_date}) must be after "
                f"open_date ({self.open_date})"
            )
        return self

    @model_validator(mode="after")
    def cc_must_have_cost_basis(self) -> "OpenPosition":
        if self.leg == Leg.CC:
            if self.cost_basis is None:
                raise ValueError("CC position must have cost_basis set")
            if self.linked_assignment_id is None:
                raise ValueError(
                    "CC position must have linked_assignment_id "
                    "(UUID of the CSP that got assigned)"
                )
        return self

    @model_validator(mode="after")
    def csp_must_not_have_cost_basis(self) -> "OpenPosition":
        if self.leg == Leg.CSP:
            if self.cost_basis is not None:
                raise ValueError(
                    "CSP positions should not have cost_basis "
                    "(that field is for CCs only)"
                )
        return self


class ClosedPosition(OpenPosition):
    close_date: date
    close_premium_per_share: float = Field(ge=0)
    close_stock_price: float = Field(gt=0)
    outcome: Outcome
    linked_roll_id: Optional[UUID] = None
    days_held: int = Field(ge=0)
    commissions_total: float = Field(ge=0)
    net_premium: float
    realized_pnl_total: float
    return_on_capital: float
    annualized_roc: float
    pct_premium_captured_final: float
    stock_return_pct_over_hold: float
    relative_outperformance: float


class OpenPositionsFile(BaseModel):
    positions: List[OpenPosition] = []


class ClosedPositionsFile(BaseModel):
    positions: List[ClosedPosition] = []


# --- Alerts -----------------------------------------------------------------


class Alert(BaseModel):
    alert_id: UUID = Field(default_factory=uuid4)
    fired_at: datetime
    rule_id: str
    severity: Severity
    scope: AlertScope
    position_id: Optional[UUID] = None
    ticker: Optional[str] = None
    computed_state: dict = Field(default_factory=dict)
    conservative_advice: str
    consider: str
    slack_posted: bool = False


class AlertsHistoryFile(BaseModel):
    alerts: List[Alert] = []


# --- Snapshot ---------------------------------------------------------------


class SnapshotComputed(BaseModel):
    csp_collateral_total: float = 0
    share_value_total: float = 0
    deployment_pct: float = 0
    cash_reserve_pct: float = 0


class Snapshot(BaseModel):
    as_of: date
    portfolio: Portfolio
    positions: List[OpenPosition]
    computed: SnapshotComputed
    alerts_fired: List[Alert] = []


# --- Load / save helpers ----------------------------------------------------


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
        fh.write("\n")


def load_portfolio(path: Path = PORTFOLIO_PATH) -> Portfolio:
    return Portfolio.model_validate(_read_json(path))


def save_portfolio(portfolio: Portfolio, path: Path = PORTFOLIO_PATH) -> None:
    _write_json(path, portfolio.model_dump(mode="json"))


def load_open_positions(path: Path = POSITIONS_OPEN_PATH) -> OpenPositionsFile:
    return OpenPositionsFile.model_validate(_read_json(path))


def save_open_positions(
    file: OpenPositionsFile, path: Path = POSITIONS_OPEN_PATH
) -> None:
    _write_json(path, file.model_dump(mode="json"))


def load_closed_positions(
    path: Path = POSITIONS_CLOSED_PATH,
) -> ClosedPositionsFile:
    return ClosedPositionsFile.model_validate(_read_json(path))


def save_closed_positions(
    file: ClosedPositionsFile, path: Path = POSITIONS_CLOSED_PATH
) -> None:
    _write_json(path, file.model_dump(mode="json"))


# --- Smoke test -------------------------------------------------------------


def _smoke_test() -> None:
    """Validate the seeded JSON files. Exits non-zero on failure."""
    import sys

    print(f"Repo root: {REPO_ROOT}")
    print()

    errors = []

    for label, loader, path in [
        ("Portfolio", load_portfolio, PORTFOLIO_PATH),
        ("Open positions", load_open_positions, POSITIONS_OPEN_PATH),
        ("Closed positions", load_closed_positions, POSITIONS_CLOSED_PATH),
    ]:
        try:
            obj = loader(path)
            count = len(obj.positions) if hasattr(obj, "positions") else 1
            print(f"  [OK]   {label:20s} {path.name}  ({count} records)")
        except FileNotFoundError:
            print(f"  [MISS] {label:20s} {path.name}  (not found)")
        except Exception as e:
            print(f"  [FAIL] {label:20s} {path.name}")
            print(f"         {type(e).__name__}: {e}")
            errors.append((label, e))

    print()
    if errors:
        print(f"FAILED: {len(errors)} validation error(s)")
        sys.exit(1)
    else:
        print("All seeded data files validate against the schema.")


if __name__ == "__main__":
    _smoke_test()
