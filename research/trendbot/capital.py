"""Capital protection: daily and peak drawdown limits, a kill switch, a losing-streak stop.

All limits use MARK-TO-MARKET equity: realized equity plus open positions at the live bid.

=====================================  ================================================
condition                              what the bot does
=====================================  ================================================
day's drawdown > 2%                    halves the risk of every new entry for the rest
                                       of the UTC day
day's drawdown > 3%                    sells every open position and freezes new entries
                                       for 24 hours
drawdown from the equity peak > 10%    KILL SWITCH: sells everything, writes
                                       ``trading_halted.lock`` and stops. The bot will not
                                       start again until a person deletes the lock file
N losing trades in a row today (3)     no new entries for the rest of the UTC day
fee rate above 0.1%                    warns (config and every real fill)
=====================================  ================================================

These only ever REDUCE risk; the per-trade 1% limit (R7) and the circuit breakers (R9)
still apply underneath. Settings: ``"capital": {...}`` in the bot settings.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .journal import ms_to_iso


LOCK_FILE = "trading_halted.lock"
RULE_ID = "X_capital_guard"


@dataclass
class CapitalSettings:
    enabled: bool = True
    daily_reduce_pct: float = 2.0
    reduce_factor: float = 0.5
    daily_flatten_pct: float = 3.0
    freeze_hours: float = 24.0
    peak_kill_pct: float = 10.0
    max_consecutive_losses: int = 3
    max_fee_rate: float = 0.001

    def __post_init__(self) -> None:
        if not 0 < self.reduce_factor <= 1:
            raise ValueError("capital.reduce_factor must be in (0, 1]: it can only reduce risk")
        if not 0 < self.daily_reduce_pct <= self.daily_flatten_pct:
            raise ValueError("capital: daily_reduce_pct must be > 0 and <= daily_flatten_pct")
        if self.max_consecutive_losses < 1 or self.peak_kill_pct <= 0:
            raise ValueError("capital: max_consecutive_losses >= 1 and peak_kill_pct > 0")


@dataclass
class Verdict:
    risk_scale: float = 1.0
    block: str | None = None  # reason new entries are refused, if they are
    flatten: str | None = None  # reason to sell everything now
    kill: str | None = None  # reason to stop the bot and write the lock
    daily_dd_pct: float = 0.0
    peak_dd_pct: float = 0.0


def day_start(now_ms: int) -> int:
    d = datetime.fromtimestamp(now_ms / 1000, UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(d.timestamp() * 1000)


def lock_path(state_dir: Path) -> Path:
    return Path(state_dir) / LOCK_FILE


def read_lock(state_dir: Path) -> dict[str, Any] | None:
    p = lock_path(state_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reason": "trading_halted.lock exists"}


def write_lock(state_dir: Path, reason: str, now_ms: int, equity: float, peak: float) -> Path:
    p = lock_path(state_dir)
    p.write_text(
        json.dumps(
            {
                "halted_utc": ms_to_iso(now_ms),
                "reason": reason,
                "equity": round(equity, 2),
                "peak_equity": round(peak, 2),
                "how_to_restart": f"review what happened, then delete {p.name} by hand",
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return p


class CapitalGuard:
    """Evaluated every bot step; its state lives in the bot's state.json ("capital")."""

    def __init__(self, settings: CapitalSettings, state: dict[str, Any]) -> None:
        self.s = settings
        self.st = state  # mutated in place: persisted with the bot state

    def evaluate(self, now_ms: int, equity: float, closed: list[Any]) -> Verdict:
        s, st = self.s, self.st
        if not s.enabled:
            return Verdict()
        today = day_start(now_ms)
        if st.get("day") != today:  # a new UTC day: new reference, day stops lifted
            st.update(day=today, day_start_equity=equity, day_reduced=False, day_stopped=None)
        st["peak_equity"] = max(float(st.get("peak_equity") or equity), equity)
        start = float(st.get("day_start_equity") or equity)
        peak = float(st["peak_equity"])
        v = Verdict(
            daily_dd_pct=max(0.0, (start - equity) / start * 100) if start else 0.0,
            peak_dd_pct=max(0.0, (peak - equity) / peak * 100) if peak else 0.0,
        )
        st["equity_mtm"] = round(equity, 2)
        st["daily_dd_pct"], st["peak_dd_pct"] = round(v.daily_dd_pct, 3), round(v.peak_dd_pct, 3)
        if v.peak_dd_pct >= s.peak_kill_pct:
            v.kill = (
                f"drawdown from the equity peak {v.peak_dd_pct:.2f}% >= {s.peak_kill_pct:g}% "
                f"(peak {peak:,.2f}, now {equity:,.2f})"
            )
            return v
        if v.daily_dd_pct >= s.daily_flatten_pct and not st.get("frozen_until", 0) > now_ms:
            st["frozen_until"] = now_ms + int(s.freeze_hours * 3_600_000)
            v.flatten = f"today's drawdown {v.daily_dd_pct:.2f}% >= {s.daily_flatten_pct:g}%"
        if st.get("frozen_until", 0) > now_ms:
            v.block = (
                f"entries frozen until {ms_to_iso(int(st['frozen_until']))} after a "
                f"{s.daily_flatten_pct:g}% daily drawdown"
            )
            return v
        losses = 0
        for t in sorted((t for t in closed if (t.exit_ts or 0) >= today), key=lambda t: t.exit_ts):
            losses = losses + 1 if (t.r_multiple or 0) < 0 else 0
        if losses >= s.max_consecutive_losses:
            st["day_stopped"] = st.get("day_stopped") or now_ms
            v.block = f"{losses} losing trades in a row today: no new entries until 00:00 UTC"
            return v
        if v.daily_dd_pct >= s.daily_reduce_pct or st.get("day_reduced"):
            st["day_reduced"] = True
            v.risk_scale = s.reduce_factor
        return v

    def status(self) -> dict[str, Any]:
        return {"settings": asdict(self.s), **{k: v for k, v in self.st.items() if k != "day"}}


def fee_warning(fee_rate: float, s: CapitalSettings) -> str | None:
    if fee_rate > s.max_fee_rate:
        return (
            f"fee rate {fee_rate * 100:.3f}% is above {s.max_fee_rate * 100:.2f}% per trade: fee "
            "drag eats the edge; use a lower fee tier or maker orders"
        )
    return None
