"""Human-readable reinforcement summary from paper trades, circuit state, and validation metrics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rl.production import (
    CIRCUIT_LOOKBACK_HOURS,
    CIRCUIT_MAX_DD_PCT,
    load_circuit_state,
    load_paper_trades,
    models_dir,
    paper_trades_path,
)

DIGEST_FILENAME = "rl_reinforcement_digest.json"


def digest_path(base: Path | None = None) -> Path:
    return models_dir(base) / DIGEST_FILENAME


def _parse_ts(s: str) -> datetime | None:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _recent_paper_rows(rows: list[dict[str, Any]], hours: int = 48) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = _parse_ts(str(r.get("ts", "")))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            out.append(r)
    return out


def _action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        per = r.get("per_symbol")
        if not isinstance(per, list):
            continue
        for sym in per:
            if not isinstance(sym, dict):
                continue
            a = str(sym.get("action", "HOLD")).upper()
            counts[a] = counts.get(a, 0) + 1
    return counts


def _equity_trend_note(rows: list[dict[str, Any]]) -> str | None:
    series: list[tuple[datetime, float]] = []
    for r in rows:
        if "sim_equity" not in r:
            continue
        ts = _parse_ts(str(r.get("ts", "")))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            series.append((ts, float(r["sim_equity"])))
        except (TypeError, ValueError):
            continue
    series.sort(key=lambda x: x[0])
    if len(series) < 3:
        return None
    first, last = series[0][1], series[-1][1]
    if last < first * 0.97:
        pct = (last / first - 1.0) * 100.0
        return (
            f"Simulated equity over recent paper snapshots is down about {abs(pct):.1f}% "
            f"from the start of that window — scheduled retraining nudges the policy "
            f"toward paths that scored better on held-out validation."
        )
    if last > first * 1.02:
        pct = (last / first - 1.0) * 100.0
        return (
            f"Simulated equity in recent snapshots is up about {pct:.1f}% in that window — "
            f"the agent still retrains on schedule to adapt when conditions change."
        )
    return None


def build_reinforcement_digest(
    base: Path | None = None,
    *,
    validation_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a short list of 'why / what next' strings for the RL dashboard.
    Does not mutate training data — display + ops only.
    """
    root = models_dir(base)
    rows = load_paper_trades(base)
    recent = _recent_paper_rows(rows, hours=72)
    counts = _action_counts(recent)
    circuit = load_circuit_state(base)
    lessons: list[str] = []

    if circuit.get("halt_buy"):
        reason = str(circuit.get("reason") or "").strip()
        if reason.startswith("auto:"):
            reason = reason[5:].strip()
        if reason:
            lessons.append(
                f"Buys are paused ({reason}). The next automatic training pass still refines "
                f"sell/hold behaviour on history; clear the pause when you accept risk again."
            )
        else:
            lessons.append(
                "Buys are paused. Automatic retraining continues to refine the policy on "
                "historical data; reset the pause from Safeguards when appropriate."
            )

    n_recent = len(recent)
    if n_recent:
        lessons.append(
            f"In the last ~72h the system logged {n_recent} RL snapshot(s) from live feature rows "
            f"(paper mode). Retraining uses the unified historical dataset so the agent is not "
            f"dependent on you clicking train."
        )
        if counts:
            parts = [f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])]
            lessons.append("Recent suggested mix across symbols: " + ", ".join(parts) + ".")
        eq_note = _equity_trend_note(recent)
        if eq_note:
            lessons.append(eq_note)
    elif rows:
        lessons.append(
            "No very recent paper snapshots; the schedule still refreshes the model on the "
            "unified CSV so the agent keeps up as new bars are added."
        )
    else:
        lessons.append(
            "No paper-trade log yet. As the app records RL snapshots, summaries here will "
            "call out drawdowns and action skew. Training still runs on the unified dataset automatically."
        )

    if validation_metrics:
        sharpe = validation_metrics.get("sharpe_ratio")
        ret = validation_metrics.get("total_return_pct")
        mdd = validation_metrics.get("max_drawdown_pct")
        try:
            if sharpe is not None and float(sharpe) < 0:
                lessons.append(
                    f"Last validation Sharpe was negative ({float(sharpe):.2f}). "
                    f"Scheduled retraining explores policies that improve risk-adjusted return on hold-out steps."
                )
        except (TypeError, ValueError):
            pass
        try:
            if ret is not None and float(ret) < 0:
                lessons.append(
                    f"Last validation total return was {float(ret):.1f}%. "
                    f"The reward signal in training penalizes weak paths so future episodes favour better outcomes."
                )
        except (TypeError, ValueError):
            pass
        try:
            if mdd is not None and float(mdd) > 8.0:
                lessons.append(
                    f"Validation max drawdown was about {float(mdd):.1f}% — high drawdowns are "
                    f"expensive in the RL reward; retraining pushes toward smoother equity curves."
                )
        except (TypeError, ValueError):
            pass

    if not lessons:
        lessons.append(
            "Automatic retraining keeps the Double DQN aligned with the latest unified features; "
            f"circuit watch uses a {CIRCUIT_LOOKBACK_HOURS}h window and {CIRCUIT_MAX_DD_PCT}% drawdown guard."
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "paper_trades_file": paper_trades_path(base).name,
        "paper_trades_total": len(rows),
        "paper_trades_recent_72h": n_recent,
        "lessons": lessons[:12],
    }


def save_reinforcement_digest(
    payload: dict[str, Any],
    base: Path | None = None,
) -> None:
    p = digest_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def read_reinforcement_digest(base: Path | None = None) -> dict[str, Any] | None:
    p = digest_path(base)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def refresh_reinforcement_digest(
    base: Path | None = None,
    *,
    validation_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blob = build_reinforcement_digest(base, validation_metrics=validation_metrics)
    save_reinforcement_digest(blob, base)
    return blob
