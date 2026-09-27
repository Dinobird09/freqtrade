"""CSV trade journal: one row per ``Trade``, readable by humans and exactly re-loadable.

Layout (binding for every writer and reader of the journal):
- ``JOURNAL_COLUMNS`` holds one column per ``Trade`` field except ``features``, in field order.
  Each timestamp column (``signal_ts``, ``entry_ts``, ``exit_ts``; int ms UTC) is followed by
  an ISO-8601 UTC helper column (``signal_time_utc``, ...) for human review. The helper
  columns are write-only: ``read_journal`` always uses the ms columns and ignores them.
- ``Trade.features`` is flattened into ``f_<name>`` columns appended after
  ``JOURNAL_COLUMNS``: the union of feature keys over all written trades, sorted, so the
  header is stable. A trade lacking a feature leaves that cell empty.
- ``None`` is written as the empty string and read back as ``None``. Floats are written with
  ``repr`` (shortest exact round-trip), so ``read_journal(write_journal(x))`` reproduces the
  ``Trade`` objects exactly. ``exit_reason`` must therefore never be the empty string.

Importing the EXISTING bot's journal (:func:`import_external`). The live bot keeps its own
CSV with its own column names; ``import_external`` maps them onto ``Trade`` fields so the
adoption check, ``journal_rules`` and ``invariants.cross_trade_violations`` can audit it:

- ``mapping`` is ``{external column: Trade field}``. The mapping MUST be written for the
  user's real journal columns (there is no universal default, and a wrong mapping converts
  silently wrong numbers, so compare a few converted rows with the bot's own figures).
- Required fields: ``pair``, ``entry_ts``, ``entry_price``, ``stop``, ``target``, ``qty``,
  ``exit_ts``, ``exit_price``, ``exit_reason`` (a column, or a ``defaults`` value). Every row
  must be a CLOSED trade. ``exit_reason`` values go through ``exit_reason_map`` (external
  value -> ``SL`` / ``TP`` / ``END``, matched case-insensitively; ``SL``/``TP``/``END``
  themselves are always accepted).
- ``risk_pct`` needs a column, or an ``equity`` column/default (account equity at entry,
  quote currency) from which ``risk_pct = risk_amount / equity * 100`` is derived.
- Derived when not mapped: ``risk_amount = qty * L_u`` (CONTRACT.md v2 A1 all-in loss per
  unit with ``cfg.fee_rate`` and ``cfg.slippage_pct``: pass the user's REAL costs);
  ``fees = fee_rate * qty * (entry + exit)``; ``pnl = qty * (exit - entry) - fees`` (a
  mapped ``pnl`` must be NET of fees); ``signal_ts`` = the open of the 4H candle before the
  one the entry filled in; ``trade_id`` = the 1-based data row number; ``variant`` and
  ``stop_method`` = ``"external"`` (set ``defaults["variant"]`` to the adopted variant id
  for the adoption testnet check); ``notes`` empty. ``r_multiple = pnl / risk_amount``
  ALWAYS (it cannot be mapped).
- ``time_format``: ``"iso"`` (ISO-8601, naive = UTC), ``"ms"`` (epoch ms) or ``"s"`` (epoch
  seconds). Pairs are normalised to ``BASE/QUOTE`` (``BTCUSDT``, ``BTC-USDT``, ``btc_usdt``
  -> ``BTC/USDT``).

CLI (writes a ``write_journal`` CSV)::

    python -m research.trendbot.journal convert --in bot.csv --map map.json \
        --out journal.csv [--fee-rate 0.001] [--slippage-pct 0.05]

``map.json``: ``{"columns": {"<your column>": "<Trade field>", ...},
"exit_reason_values": {"<your value>": "SL" | "TP" | "END", ...},
"time_format": "iso" | "ms" | "s", "defaults": {"<Trade field>": value, ...}}`` (only
``columns`` is required).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import operator
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import ConfigError, StrategyConfig
from .models import EXIT_END, EXIT_SL, EXIT_TP, Trade
from .sizing import loss_per_unit


FEATURE_PREFIX = "f_"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)

# (Trade field, parser, optional). Order = Trade field order; features are handled apart.
_FIELDS: tuple[tuple[str, Callable[[str], object], bool], ...] = (
    ("trade_id", int, False),
    ("pair", str, False),
    ("variant", str, False),
    ("signal_ts", int, False),
    ("entry_ts", int, False),
    ("entry_price", float, False),
    ("stop", float, False),
    ("target", float, False),
    ("qty", float, False),
    ("risk_amount", float, False),
    ("risk_pct", float, False),
    ("stop_method", str, False),
    ("exit_ts", int, True),
    ("exit_price", float, True),
    ("exit_reason", str, True),
    ("fees", float, False),
    ("pnl", float, True),
    ("r_multiple", float, True),
    ("ml_prob", float, True),
    ("notes", str, False),
)

# ms timestamp column -> ISO-8601 UTC helper column written right after it.
TIME_HELPER_COLUMNS = {
    "signal_ts": "signal_time_utc",
    "entry_ts": "entry_time_utc",
    "exit_ts": "exit_time_utc",
}


def _build_columns() -> tuple[str, ...]:
    cols: list[str] = []
    for name, _parser, _optional in _FIELDS:
        cols.append(name)
        if name in TIME_HELPER_COLUMNS:
            cols.append(TIME_HELPER_COLUMNS[name])
    return tuple(cols)


JOURNAL_COLUMNS: tuple[str, ...] = _build_columns()


# ---------------------------------------------------------------------------- time helpers
def ms_to_iso(ts: int) -> str:
    """Epoch ms -> ``"2024-03-12T12:30:00Z"`` (milliseconds shown only when non-zero)."""
    dt = _EPOCH + timedelta(milliseconds=ts)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    ms = ts % 1000
    return f"{base}.{ms:03d}Z" if ms else f"{base}Z"


def iso_to_ms(text: str) -> int:
    """ISO-8601 (``Z``/offset aware; naive means UTC) or an integer ms string -> epoch ms."""
    s = text.strip()
    if s.lstrip("-").isdigit():
        return int(s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt - _EPOCH) // _MS


# ---------------------------------------------------------------------------- writing
def journal_header(trades: Iterable[Trade]) -> tuple[str, ...]:
    """Full header for ``trades``: ``JOURNAL_COLUMNS`` + sorted ``f_<name>`` feature columns."""
    keys: set[str] = set()
    for t in trades:
        keys.update(t.features)
    return JOURNAL_COLUMNS + tuple(FEATURE_PREFIX + k for k in sorted(keys))


def _format(value: object, parser: Callable[[str], object]) -> str:
    if value is None:
        return ""
    if parser is float:
        return repr(float(value))  # type: ignore[arg-type]
    if parser is int:
        return str(operator.index(value))  # type: ignore[arg-type]  # refuses floats
    return str(value)


def _row(trade: Trade, feature_keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for name, parser, _optional in _FIELDS:
        value = getattr(trade, name)
        out.append(_format(value, parser))
        if name in TIME_HELPER_COLUMNS:
            out.append("" if value is None else ms_to_iso(value))
    for key in feature_keys:
        value = trade.features.get(key)
        out.append(_format(value, float))
    return out


def write_journal(trades: Iterable[Trade], path: str | Path) -> None:
    """Write one CSV row per trade (order preserved); creates parent directories."""
    rows = list(trades)
    header = journal_header(rows)
    feature_keys = tuple(c[len(FEATURE_PREFIX) :] for c in header[len(JOURNAL_COLUMNS) :])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for t in rows:
            writer.writerow(_row(t, feature_keys))


# ---------------------------------------------------------------------------- reading
def _parse_cell(raw: str, name: str, parser: Callable[[str], object], optional: bool) -> object:
    if raw == "":
        if optional:
            return None
        if parser is str:
            return ""
        raise ValueError(f"column {name!r} may not be empty")
    try:
        return parser(raw)
    except ValueError:
        raise ValueError(f"column {name!r}: cannot parse {raw!r} as {parser.__name__}")


def _parse_row(row: dict[str, str], feature_cols: list[str]) -> Trade:
    kwargs = {
        name: _parse_cell(row[name], name, parser, optional) for name, parser, optional in _FIELDS
    }
    features = {
        col[len(FEATURE_PREFIX) :]: _parse_cell(row[col], col, float, False)
        for col in feature_cols
        if row[col] != ""
    }
    return Trade(**kwargs, features=features)  # type: ignore[arg-type]


def read_journal(path: str | Path) -> list[Trade]:
    """Load a journal written by ``write_journal`` (exact round-trip).

    Uses the ms timestamp columns only; ISO helper columns and unknown extra columns are
    ignored. Raises ``ValueError`` (with the CSV line number) on missing columns or bad cells.
    """
    p = Path(path)
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [name for name, _p, _o in _FIELDS if name not in header]
        if missing:
            raise ValueError(f"{p}: journal is missing required columns {missing}")
        feature_cols = [c for c in header if c.startswith(FEATURE_PREFIX)]
        trades: list[Trade] = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{p}: line {reader.line_num}: wrong number of cells")
            try:
                trades.append(_parse_row(row, feature_cols))
            except ValueError as exc:
                raise ValueError(f"{p}: line {reader.line_num}: {exc}")
    return trades


# ---------------------------------------------------------------------------- import
EXTERNAL_TIME_FORMATS = ("iso", "ms", "s")
EQUITY_FIELD = "equity"  # pseudo-field: account equity at entry, derives risk_pct
DEFAULT_EXTERNAL_LABEL = "external"  # variant / stop_method of imported trades by default
IMPORT_REQUIRED_FIELDS: tuple[str, ...] = (
    "pair",
    "entry_ts",
    "entry_price",
    "stop",
    "target",
    "qty",
    "exit_ts",
    "exit_price",
    "exit_reason",
)
IMPORT_FIELDS: tuple[str, ...] = (
    *IMPORT_REQUIRED_FIELDS,
    "trade_id",
    "variant",
    "signal_ts",
    "risk_amount",
    "risk_pct",
    "stop_method",
    "fees",
    "pnl",
    "ml_prob",
    "notes",
    EQUITY_FIELD,
)
_EXIT_REASONS = (EXIT_SL, EXIT_TP, EXIT_END)
# Quote suffixes tried (longest first) for separator-less symbols such as "BTCUSDT".
_QUOTE_SUFFIXES = ("FDUSD", "USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH", "BNB")
_MAP_KEYS = ("columns", "exit_reason_values", "time_format", "defaults")


def normalize_pair(raw: str) -> str:
    """``"BTCUSDT"`` / ``"BTC-USDT"`` / ``"btc_usdt"`` / ``"BTC/USDT"`` -> ``"BTC/USDT"``."""
    s = raw.strip().upper().split(":", 1)[0]
    for sep in ("-", "_"):
        s = s.replace(sep, "/")
    if "/" in s:
        base, _sep, quote = s.partition("/")
        if base and quote and "/" not in quote:
            return f"{base}/{quote}"
    else:
        for quote in _QUOTE_SUFFIXES:
            if s.endswith(quote) and len(s) > len(quote):
                return f"{s[: -len(quote)]}/{quote}"
    raise ValueError(f"pair {raw!r} is neither BASE/QUOTE nor BASE+known quote (e.g. BTCUSDT)")


def _parse_time(text: str, time_format: str, name: str) -> int:
    try:
        if time_format == "ms":
            return int(text)
        if time_format == "s":
            seconds = float(text)
            if not math.isfinite(seconds):
                raise ValueError
            return round(seconds * 1000)
        if text.lstrip("-").isdigit():
            raise ValueError
        return iso_to_ms(text)
    except (ValueError, OverflowError):
        raise ValueError(f"{name} {text!r} is not a {time_format!r} time") from None


class _ExternalRow:
    """Typed access to one external CSV row through the mapping and the defaults."""

    def __init__(
        self,
        cells: Mapping[str, str],
        columns: Mapping[str, str],
        defaults: Mapping[str, object],
        time_format: str,
    ) -> None:
        self.cells, self.columns, self.defaults = cells, columns, defaults
        self.time_format = time_format

    def text(self, name: str) -> str | None:
        if name in self.columns:
            value = self.cells[self.columns[name]].strip()
            if value:
                return value
        if name in self.defaults and self.defaults[name] is not None:
            return str(self.defaults[name]).strip() or None
        return None

    def required(self, name: str) -> str:
        value = self.text(name)
        if value is None:
            raise ValueError(f"{name} is empty")
        return value

    def number(self, name: str, required: bool = True) -> float | None:
        text = self.required(name) if required else self.text(name)
        if text is None:
            return None
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{name} {text!r} is not a number") from None
        if not math.isfinite(value):
            raise ValueError(f"{name} {text!r} is not finite")
        return value

    def time(self, name: str) -> int | None:
        text = self.text(name)
        return None if text is None else _parse_time(text, self.time_format, name)


def _exit_reason(raw: str, folded: Mapping[str, str]) -> str:
    """``raw`` through the value map (keys already stripped + casefolded), or SL/TP/END."""
    key = raw.strip().casefold()
    if key in folded:
        return folded[key]
    if raw.strip().upper() in _EXIT_REASONS:
        return raw.strip().upper()
    raise ValueError(
        f"exit reason {raw!r} is not in the exit_reason value map {sorted(folded)} (map every "
        f"value your bot writes to one of {', '.join(_EXIT_REASONS)})"
    )


def _risk(
    row: _ExternalRow, entry: float, stop: float, qty: float, cfg: StrategyConfig
) -> tuple[float, float]:
    """``(risk_amount, risk_pct)``: mapped, or A1 ``qty * L_u`` and ``/ equity * 100``."""
    risk_amount = row.number("risk_amount", required=False)
    if risk_amount is None:
        risk_amount = qty * loss_per_unit(entry, stop, cfg)
    if not risk_amount > 0:
        raise ValueError(
            f"risk_amount {risk_amount!r} is not positive (for a long the stop must be below "
            "the entry), so R cannot be computed"
        )
    risk_pct = row.number("risk_pct", required=False)
    if risk_pct is None:
        equity = row.number(EQUITY_FIELD)
        if not equity > 0:  # type: ignore[operator]
            raise ValueError(f"equity {equity!r} must be positive to derive risk_pct")
        risk_pct = risk_amount / equity * 100  # type: ignore[operator]
    return risk_amount, risk_pct


def _external_trade(
    row: _ExternalRow, number: int, folded_reasons: Mapping[str, str], cfg: StrategyConfig
) -> Trade:
    entry_ts, exit_ts = row.time("entry_ts"), row.time("exit_ts")
    if entry_ts is None or exit_ts is None:
        raise ValueError("entry_ts and exit_ts may not be empty (import closed trades only)")
    entry, stop, target, qty, exit_price = (
        row.number(n) for n in ("entry_price", "stop", "target", "qty", "exit_price")
    )
    if not qty > 0:  # type: ignore[operator]
        raise ValueError(f"qty {qty!r} must be positive (long positions only)")
    risk_amount, risk_pct = _risk(row, entry, stop, qty, cfg)  # type: ignore[arg-type]
    fees = row.number("fees", required=False)
    if fees is None:
        fees = cfg.fee_rate * qty * (entry + exit_price)  # type: ignore[operator]
    pnl = row.number("pnl", required=False)
    if pnl is None:
        pnl = qty * (exit_price - entry) - fees  # type: ignore[operator]
    signal_ts = row.time("signal_ts")
    if signal_ts is None:  # the candle before the one the entry filled in
        signal_ts = entry_ts // cfg.timeframe_ms * cfg.timeframe_ms - cfg.timeframe_ms
    trade_id = row.text("trade_id")
    try:
        tid = number if trade_id is None else int(trade_id)
    except ValueError:
        raise ValueError(f"trade_id {trade_id!r} is not an integer") from None
    return Trade(
        trade_id=tid,
        pair=normalize_pair(row.required("pair")),
        variant=row.text("variant") or DEFAULT_EXTERNAL_LABEL,
        signal_ts=signal_ts,
        entry_ts=entry_ts,
        entry_price=entry,  # type: ignore[arg-type]
        stop=stop,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        qty=qty,  # type: ignore[arg-type]
        risk_amount=risk_amount,
        risk_pct=risk_pct,
        stop_method=row.text("stop_method") or DEFAULT_EXTERNAL_LABEL,
        exit_ts=exit_ts,
        exit_price=exit_price,
        exit_reason=_exit_reason(row.required("exit_reason"), folded_reasons),
        fees=fees,
        pnl=pnl,
        r_multiple=pnl / risk_amount,
        ml_prob=row.number("ml_prob", required=False),
        notes=row.text("notes") or "",
    )


def _import_columns(
    mapping: Mapping[str, str],
    defaults: Mapping[str, object],
    reasons: Mapping[str, str],
    time_format: str,
) -> dict[str, str]:
    """Validate the mapping; return ``{Trade field: external column}``."""
    if time_format not in EXTERNAL_TIME_FORMATS:
        raise ValueError(f"time_format {time_format!r} must be one of {EXTERNAL_TIME_FORMATS}")
    columns: dict[str, str] = {}
    for column, name in mapping.items():
        if name not in IMPORT_FIELDS:
            raise ValueError(
                f"mapping {column!r} -> {name!r}: not an importable field (one of "
                f"{', '.join(IMPORT_FIELDS)}; r_multiple is always pnl / risk_amount)"
            )
        if name in columns:
            raise ValueError(f"columns {columns[name]!r} and {column!r} both map to {name!r}")
        columns[name] = column
    unknown = sorted(set(defaults) - set(IMPORT_FIELDS))
    if unknown:
        raise ValueError(f"defaults {unknown} are not importable fields")
    bad = sorted(k for k, v in reasons.items() if v not in _EXIT_REASONS)
    if bad:
        raise ValueError(f"exit_reason values {bad} must map to one of {', '.join(_EXIT_REASONS)}")
    given = set(columns) | set(defaults)
    missing = [n for n in IMPORT_REQUIRED_FIELDS if n not in given]
    if missing:
        raise ValueError(f"the mapping has no column (or default) for required fields {missing}")
    if not given & {"risk_pct", EQUITY_FIELD}:
        raise ValueError(
            "risk_pct cannot be derived: map a risk_pct column or an equity column, or give "
            "defaults['equity'] (account equity at entry)"
        )
    return columns


def import_external(
    path: str | Path,
    mapping: Mapping[str, str],
    *,
    time_format: str = "iso",
    defaults: Mapping[str, object] | None = None,
    exit_reason_map: Mapping[str, str] | None = None,
    cfg: StrategyConfig | None = None,
) -> list[Trade]:
    """Convert the existing bot's CSV journal into ``Trade`` objects (see the module docstring).

    ``mapping`` is ``{external column: Trade field}`` and must be written for the user's
    real journal columns. ``cfg`` supplies ``fee_rate`` / ``slippage_pct`` (A1 risk, default
    fees) and the 4H timeframe; default ``StrategyConfig()``. Raises ``ValueError`` naming the
    file and line number of the first problem (unknown field or column, empty required cell,
    unparseable number or time, unmapped exit reason, non-positive risk, duplicate trade id).
    """
    cfg = cfg or StrategyConfig()
    defaults = dict(defaults or {})
    reasons = dict(exit_reason_map or {})
    columns = _import_columns(mapping, defaults, reasons, time_format)
    folded = {k.strip().casefold(): v for k, v in reasons.items()}
    p = Path(path)
    trades: list[Trade] = []
    seen: dict[int, int] = {}
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        absent = [c for c in columns.values() if c not in header]
        if absent:
            raise ValueError(f"{p}: mapped columns {absent} are not in the header {header}")
        for number, cells in enumerate(reader, start=1):
            where = f"{p}: line {reader.line_num}"
            if None in cells or any(v is None for v in cells.values()):
                raise ValueError(f"{where}: wrong number of cells")
            try:
                trade = _external_trade(
                    _ExternalRow(cells, columns, defaults, time_format), number, folded, cfg
                )
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from None
            if trade.trade_id in seen:
                raise ValueError(
                    f"{where}: trade_id {trade.trade_id} repeats line {seen[trade.trade_id]}"
                )
            seen[trade.trade_id] = reader.line_num
            trades.append(trade)
    return trades


def load_import_map(path: str | Path) -> dict[str, object]:
    """Read a ``map.json`` for ``convert``: keyword arguments of :func:`import_external`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the map must be a JSON object")
    unknown = sorted(set(data) - set(_MAP_KEYS))
    if unknown:
        raise ValueError(f"{path}: unknown keys {unknown} (expected {list(_MAP_KEYS)})")
    for key in ("columns", "exit_reason_values", "defaults"):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f"{path}: {key} must be a JSON object")
    if not data.get("columns"):
        raise ValueError(f"{path}: columns (external column -> Trade field) is required")
    return {
        "mapping": data["columns"],
        "exit_reason_map": data.get("exit_reason_values", {}),
        "time_format": data.get("time_format", "iso"),
        "defaults": data.get("defaults", {}),
    }


# ---------------------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.trendbot.journal",
        description="Trade journal tools.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser(
        "convert",
        help="convert the existing bot's CSV journal into this package's journal format",
        epilog=(
            "The map must be written for YOUR bot's real column names and exit-reason values; "
            "compare a few converted rows with the bot's own figures before relying on them."
        ),
    )
    convert.add_argument(
        "--in", dest="in_path", metavar="BOT_CSV", required=True, type=Path, help="bot's CSV"
    )
    convert.add_argument("--map", required=True, type=Path, help="map.json (see module docs)")
    convert.add_argument("--out", required=True, type=Path, help="journal CSV to write")
    convert.add_argument(
        "--fee-rate",
        type=float,
        default=None,
        help="fee per side (fraction) the bot really paid; used for A1 risk and default fees",
    )
    convert.add_argument(
        "--slippage-pct",
        type=float,
        default=None,
        help="stop slippage in percent assumed by the A1 all-in risk (default: config default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    changes = {
        k: v
        for k, v in (("fee_rate", args.fee_rate), ("slippage_pct", args.slippage_pct))
        if v is not None
    }
    try:
        cfg = StrategyConfig().with_changes(**changes)
    except ConfigError as exc:
        parser.error(str(exc))
    try:
        kwargs = load_import_map(args.map)
        trades = import_external(args.in_path, cfg=cfg, **kwargs)  # type: ignore[arg-type]
        write_journal(trades, args.out)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Converted {len(trades)} trades from {args.in_path} to {args.out} (fee_rate "
        f"{cfg.fee_rate:g}, slippage {cfg.slippage_pct:g}%; risk_amount per CONTRACT v2 A1 where "
        "not mapped, r_multiple = pnl / risk_amount)."
    )
    print(
        "Check a few rows against the bot's own figures: the map must describe your journal's "
        "real columns, and a wrong map converts silently wrong numbers."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
