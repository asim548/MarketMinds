"""Production controls: versioned checkpoints, paper-trade log, circuit breaker (PDF §8.2)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rl import RL_MODELS_DIR

PAPER_TRADES_FILE = "paper_trades.json"
CIRCUIT_STATE_FILE = "circuit_state.json"
CURRENT_AGENT_FILE = "current_agent.json"

# Trip circuit if max drawdown over sim_equity in the last N hours exceeds this (%)
CIRCUIT_MAX_DD_PCT = 5.0
CIRCUIT_LOOKBACK_HOURS = 24
PAPER_TRADES_MAX = 10_000


def models_dir(base: Path | None = None) -> Path:
    return Path(base) if base else RL_MODELS_DIR


def paper_trades_path(base: Path | None = None) -> Path:
    return models_dir(base) / PAPER_TRADES_FILE


def circuit_state_path(base: Path | None = None) -> Path:
    return models_dir(base) / CIRCUIT_STATE_FILE


def current_agent_meta_path(base: Path | None = None) -> Path:
    return models_dir(base) / CURRENT_AGENT_FILE


def default_circuit_state() -> dict[str, Any]:
    return {
        "halt_buy": False,
        "reason": "",
        "updated_at_utc": None,
        "manual_hold": False,
    }


def load_circuit_state(base: Path | None = None) -> dict[str, Any]:
    p = circuit_state_path(base)
    if not p.is_file():
        return default_circuit_state()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        out = default_circuit_state()
        out.update({k: d.get(k, out[k]) for k in out})
        return out
    except (json.JSONDecodeError, OSError):
        return default_circuit_state()


def save_circuit_state(state: dict[str, Any], base: Path | None = None) -> None:
    p = circuit_state_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def reset_circuit(base: Path | None = None) -> dict[str, Any]:
    st = default_circuit_state()
    save_circuit_state(st, base)
    return st


def trip_circuit_halt_buy(reason: str, base: Path | None = None) -> None:
    st = load_circuit_state(base)
    st["halt_buy"] = True
    st["reason"] = reason
    st["manual_hold"] = False
    save_circuit_state(st, base)


def set_manual_buy_halt(enabled: bool, reason: str = "", base: Path | None = None) -> None:
    st = load_circuit_state(base)
    st["manual_hold"] = bool(enabled)
    st["halt_buy"] = bool(enabled) or st.get("halt_buy", False)
    if reason:
        st["reason"] = reason
    save_circuit_state(st, base)


def load_paper_trades(base: Path | None = None) -> list[dict[str, Any]]:
    p = paper_trades_path(base)
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def append_paper_trade(entry: dict[str, Any], base: Path | None = None) -> None:
    p = paper_trades_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = load_paper_trades(base)
    row = dict(entry)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    rows.append(row)
    if len(rows) > PAPER_TRADES_MAX:
        rows = rows[-PAPER_TRADES_MAX:]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def _parse_ts(s: str) -> datetime | None:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _max_drawdown_pct(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    arr = np.asarray(values, dtype=np.float64)
    peak = np.maximum.accumulate(arr)
    dd = (peak - arr) / (peak + 1e-8) * 100.0
    return float(np.max(dd))


def refresh_circuit_from_paper_trades(base: Path | None = None) -> bool:
    """
    If any `sim_equity` samples in the lookback window have peak-to-trough DD > threshold,
    set halt_buy (unless user cleared with manual_hold false and halt_buy false — we only auto-trip).
    Returns True if circuit is now halting BUYs.
    """
    rows = load_paper_trades(base)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=CIRCUIT_LOOKBACK_HOURS)
    series: list[tuple[datetime, float]] = []
    for r in rows:
        if "sim_equity" not in r:
            continue
        ts = _parse_ts(str(r.get("ts", "")))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            try:
                series.append((ts, float(r["sim_equity"])))
            except (TypeError, ValueError):
                continue
    series.sort(key=lambda x: x[0])
    values = [v for _, v in series]
    if len(values) < 2:
        return load_circuit_state(base).get("halt_buy", False)

    mdd = _max_drawdown_pct(values)
    if mdd > CIRCUIT_MAX_DD_PCT:
        trip_circuit_halt_buy(
            f"auto: {CIRCUIT_LOOKBACK_HOURS}h sim_equity max drawdown {mdd:.2f}% > {CIRCUIT_MAX_DD_PCT}%",
            base,
        )
        return True
    return load_circuit_state(base).get("halt_buy", False)


def circuit_halt_buy(base: Path | None = None) -> bool:
    st = load_circuit_state(base)
    return bool(st.get("halt_buy", False))


def write_current_agent_meta(
    weights_name: str,
    scaler_name: str,
    *,
    dueling: bool = False,
    prioritized: bool = False,
    base: Path | None = None,
    version_label: str | None = None,
) -> None:
    root = models_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    if version_label is not None:
        ver = version_label
    elif weights_name == "rl_agent.pth":
        ver = "canonical"
    else:
        ver = weights_name.replace("rl_agent_", "").replace(".pth", "")
    meta = {
        "weights": weights_name,
        "scaler": scaler_name,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "dueling": dueling,
        "prioritized_training": prioritized,
        "version": ver,
    }
    with open(current_agent_meta_path(base), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def resolve_checkpoint_paths(base: Path | None = None) -> tuple[Path | None, Path | None]:
    """Return (agent_path, scaler_path) preferring current_agent.json; fallback legacy names."""
    root = models_dir(base)
    meta_p = current_agent_meta_path(base)
    if meta_p.is_file():
        try:
            with open(meta_p, encoding="utf-8") as f:
                meta = json.load(f)
            w = root / meta.get("weights", "rl_agent.pth")
            s = root / meta.get("scaler", "rl_scaler.pkl")
            if w.is_file() and s.is_file():
                return w, s
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    w, s = root / "rl_agent.pth", root / "rl_scaler.pkl"
    if w.is_file() and s.is_file():
        return w, s
    return None, None


def publish_latest_aliases(versioned_weights: Path, versioned_scaler: Path, base: Path | None = None) -> None:
    """Copy versioned files to rl_agent.pth / rl_scaler.pkl for backward-compatible loaders."""
    root = models_dir(base)
    shutil.copy2(versioned_weights, root / "rl_agent.pth")
    shutil.copy2(versioned_scaler, root / "rl_scaler.pkl")


def training_requires_confirmation(n_rows: int, max_rows: int | None) -> bool:
    """Full unified dataset runs require explicit acknowledgement (PDF §8.2)."""
    if max_rows is not None:
        return False
    return n_rows >= 50_000


def is_full_training_confirmed(env_confirmed: bool) -> bool:
    import os

    if env_confirmed:
        return True
    return os.environ.get("RL_TRAINING_APPROVED", "").strip().lower() in ("1", "true", "yes")
