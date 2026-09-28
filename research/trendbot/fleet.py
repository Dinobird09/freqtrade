"""Fleet: run up to 10 bots from one dashboard, with operator approval beyond 5.

``fleet.json``::

    {
      "approval_threshold": 5,        # starting a bot while this many run needs approval
      "max_bots": 10,                 # hard ceiling, never exceeded
      "bots": [
        {"name": "binance-core", "settings": "bots/binance-core.json"},
        {"name": "coinbase-btc", "settings": "bots/coinbase-btc.json"}
      ]
    }

Every bot is an independent ``live_bot`` process with its own settings file and state dir
(journal, learnings, layers, candles). The dashboard (``--fleet fleet.json``) lists them
all, starts / stops / pauses them, and shows each one's full detail view.

Approval: when ``approval_threshold`` bots are already running, a start request does not
start anything. It creates a pending approval (valid for 2 minutes) that the dashboard shows
as a pop-up listing the bots and their modes. The operator must type ``APPROVE`` in it.
Every approval is logged to ``fleet_approvals.jsonl`` next to ``fleet.json``.

Safety checks at load time (a violating fleet is refused with the reason):

- at most ``max_bots`` bots (10), unique names, unique state dirs;
- bots on the same exchange + mode (the same account) must trade disjoint pairs. Two bots
  on one pair would fight over the same balance and each would think it owns the position;
- the correlated cluster (BTC/ETH/BNB by default) must not be split across bots on one
  account, because R6's shared risk budget is enforced inside one bot.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import base_of


APPROVAL_WORD = "APPROVE"
APPROVAL_TTL_S = 120
HARD_MAX_BOTS = 10


class FleetError(ValueError):
    """The fleet file violates a safety rule."""


@dataclass
class FleetBot:
    name: str
    settings_path: Path
    settings: Any  # live_bot.BotSettings

    @property
    def state_dir(self) -> Path:
        return Path(self.settings.state_dir)

    @property
    def account(self) -> tuple[str, str]:
        return (self.settings.exchange, self.settings.mode)


@dataclass
class Approval:
    id: str
    bots: list[str]
    running: int
    created: float
    expires: float


@dataclass
class Fleet:
    path: Path | None
    bots: dict[str, FleetBot]
    approval_threshold: int = 5
    max_bots: int = HARD_MAX_BOTS
    pending: dict[str, Approval] = field(default_factory=dict)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path) -> Fleet:
        from .live_bot import BotSettings

        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        max_bots = int(raw.get("max_bots", HARD_MAX_BOTS))
        threshold = int(raw.get("approval_threshold", 5))
        if not 1 <= max_bots <= HARD_MAX_BOTS:
            raise FleetError(f"max_bots must be between 1 and {HARD_MAX_BOTS}")
        if not 0 <= threshold <= max_bots:
            raise FleetError("approval_threshold must be between 0 and max_bots")
        entries = raw.get("bots") or []
        if len(entries) > max_bots:
            raise FleetError(f"{len(entries)} bots configured; the maximum is {max_bots}")
        bots: dict[str, FleetBot] = {}
        for e in entries:
            name = str(e["name"]).strip()
            if not name or name in bots:
                raise FleetError(f"bot names must be unique and non-empty (got {name!r})")
            sp = Path(e["settings"])
            sp = sp if sp.is_absolute() else (p.parent / sp)
            bots[name] = FleetBot(name, sp.resolve(), BotSettings.load(sp))
        fleet = cls(p, bots, threshold, max_bots)
        fleet.check()
        return fleet

    @classmethod
    def single(cls, settings_path: Path | None, settings: Any, name: str = "bot") -> Fleet:
        bot = FleetBot(name, settings_path or Path(), settings)
        return cls(None, {name: bot}, approval_threshold=HARD_MAX_BOTS, max_bots=1)

    def check(self) -> None:
        dirs: dict[Path, str] = {}
        owners: dict[tuple[str, str, str], str] = {}
        for b in self.bots.values():
            d = b.state_dir.resolve()
            if d in dirs:
                raise FleetError(f"bots {dirs[d]} and {b.name} share the state dir {d}")
            dirs[d] = b.name
            for pair in b.settings.pairs:
                key = (*b.account, pair)
                if key in owners:
                    raise FleetError(
                        f"bots {owners[key]} and {b.name} both trade {pair} on "
                        f"{b.account[0]}/{b.account[1]}: one pair, one bot per account"
                    )
                owners[key] = b.name
        self._check_cluster()

    def _check_cluster(self) -> None:
        by_account: dict[tuple[str, str], dict[str, str]] = {}
        for b in self.bots.values():
            cluster = set(b.settings.strategy_config().correlated_cluster)
            for pair in b.settings.pairs:
                if base_of(pair) in cluster:
                    by_account.setdefault(b.account, {})[pair] = b.name
        for account, owner_of in by_account.items():
            names = sorted(set(owner_of.values()))
            if len(names) > 1:
                raise FleetError(
                    f"the correlated BTC/ETH/BNB cluster is split across bots {names} on "
                    f"{account[0]}/{account[1]}; its shared R6 risk budget only works inside "
                    "one bot, so keep those pairs together"
                )

    # ------------------------------------------------------------------ approvals
    def needs_approval(self, running: int, starting: int = 1) -> bool:
        return running + starting > self.approval_threshold

    def request_approval(self, names: list[str], running: int) -> Approval:
        self._expire()
        now = time.time()
        a = Approval(secrets.token_urlsafe(12), list(names), running, now, now + APPROVAL_TTL_S)
        self.pending[a.id] = a
        return a

    def take_approval(self, approval_id: str, confirm: str) -> Approval:
        self._expire()
        a = self.pending.get(approval_id)
        if a is None:
            raise FleetError("this approval expired or does not exist; start again")
        if confirm.strip() != APPROVAL_WORD:
            raise FleetError(f"type {APPROVAL_WORD} to approve")
        del self.pending[approval_id]
        self._log(a)
        return a

    def _expire(self) -> None:
        now = time.time()
        for k in [k for k, a in self.pending.items() if a.expires < now]:
            del self.pending[k]

    def _log(self, a: Approval) -> None:
        if self.path is None:
            return
        rec = {
            "approved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bots": a.bots,
            "running_before": a.running,
            "approval_id": a.id,
        }
        with (self.path.parent / "fleet_approvals.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def describe(self, names: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "mode": self.bots[n].settings.mode,
                "exchange": self.bots[n].settings.exchange,
                "pairs": list(self.bots[n].settings.pairs),
            }
            for n in names
        ]


def summary_row(name: str, snap: Mapping[str, Any]) -> dict[str, Any]:
    """One line of the fleet table from a bot's dashboard snapshot."""
    st, meta, bot = snap.get("stats", {}), snap.get("meta", {}), snap.get("bot") or {}
    return {
        "name": name,
        "mode": meta.get("mode"),
        "exchange": meta.get("exchange"),
        "running": bool(bot.get("running")),
        "heartbeat_age_s": bot.get("heartbeat_age_s"),
        "paused": bool((snap.get("control") or {}).get("entries_paused")),
        "equity": st.get("equity"),
        "return_pct": st.get("return_pct"),
        "closed_trades": st.get("closed_trades"),
        "avg_r": st.get("avg_r"),
        "open_positions": len(snap.get("open_positions") or []),
        "pairs": sorted((snap.get("pairs") or {}).keys()),
    }
