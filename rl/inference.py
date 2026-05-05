"""Live RL signal inference using the same feature pipeline as AI Picks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from rl import RL_MODELS_DIR
from rl.dqn_agent import DoubleDQNAgent
from rl.production import resolve_checkpoint_paths

ACTION_NAMES = {0: "HOLD", 1: "BUY", 2: "SELL"}


def _confidence_from_q(q: np.ndarray) -> float:
    q = np.asarray(q, dtype=np.float64).ravel()
    denom = np.sum(np.abs(q)) + 1e-8
    raw = float(np.max(q) / denom * 100.0)
    return float(np.clip(raw, 50.0, 99.0))


def _q_value_margin(q: np.ndarray) -> float:
    """Top Q minus second-best — wide gap ⇒ more decisive policy at this state."""
    q = np.asarray(q, dtype=np.float64).ravel()
    if q.size < 2:
        return 0.0
    s = np.sort(q)
    return float(s[-1] - s[-2])


def rl_paths(base_dir: Path | None = None) -> dict[str, Path]:
    root = Path(base_dir) if base_dir else RL_MODELS_DIR
    return {
        "models_dir": root,
        "agent": root / "rl_agent.pth",
        "scaler": root / "rl_scaler.pkl",
        "metrics": root / "rl_metrics.json",
        "log": root / "training_log.txt",
        "current_meta": root / "current_agent.json",
    }


def load_agent_and_scaler(
    models_dir: Path | None = None,
) -> tuple[DoubleDQNAgent | None, Any | None]:
    root = Path(models_dir) if models_dir else RL_MODELS_DIR
    ag_path, sc_path = resolve_checkpoint_paths(root)
    if ag_path is None or sc_path is None:
        return None, None
    agent = DoubleDQNAgent()
    agent.load(str(ag_path))
    agent.epsilon = 0.0
    if agent._sk is not None:
        agent._sk.epsilon = 0.0
    scaler = joblib.load(sc_path)
    return agent, scaler


def apply_circuit_buy_halt(signals: list[dict[str, Any]], halt: bool) -> tuple[list[dict[str, Any]], bool]:
    """Downgrade BUY to HOLD when circuit requests (PDF §8.2)."""
    if not halt or not signals:
        return signals, False
    out: list[dict[str, Any]] = []
    changed = False
    for s in signals:
        row = dict(s)
        if row.get("action") == "BUY" or row.get("action_id") == 1:
            row["action"] = "HOLD"
            row["action_id"] = 0
            row["circuit_breaker_applied"] = True
            changed = True
        out.append(row)
    return out, changed


def merge_shadow_signals(
    ai_predictor,
    market_data: list,
    live_economic_event: dict,
    rl_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Run GBM hybrid stack alongside RL (shadow / audit mode).
    UI should prefer GBM unless you explicitly surface RL (PDF §8.2 shadow mode).
    """
    gbm: list[dict[str, Any]] = []
    if getattr(ai_predictor, "is_ready", False):
        try:
            gbm = ai_predictor.predict_signals(
                market_data,
                live_economic_event,
                debug=False,
                features_only=False,
            )
        except Exception:
            gbm = []
    return {
        "display_policy": "gbm_primary",
        "gbm_signals": gbm,
        "rl_signals": rl_signals,
    }


def compute_rl_signals(
    ai_predictor,
    market_data: list,
    live_economic_event: dict,
    agent: DoubleDQNAgent,
    scaler,
) -> list[dict[str, Any]]:
    """Per-symbol HOLD/BUY/SELL from Double DQN on 37-dim state (34 scaled + flat portfolio)."""
    if not market_data:
        return []

    X = ai_predictor.predict_signals(
        market_data,
        live_economic_event,
        debug=True,
        features_only=True,
    )
    if not isinstance(X, pd.DataFrame) or X.empty:
        return []

    cols = list(ai_predictor.feature_cols)
    if "symbol" not in X.columns:
        return []
    symbols = X["symbol"].astype(str).tolist()
    X_mat = X[cols].astype(np.float32).values
    X_scaled = scaler.transform(X_mat).astype(np.float32)
    live_by_sym = {str(d.get("symbol")): d for d in market_data if d.get("symbol")}

    out: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols):
        market34 = X_scaled[i]
        port3 = np.zeros(3, dtype=np.float32)
        state = np.concatenate([market34, port3], axis=0).astype(np.float32)

        if agent._sk is not None:
            q = agent._sk._predict_q(agent._sk._online, state.reshape(1, -1))[0]
        else:
            import torch

            if agent.online_net is None:
                continue
            with torch.no_grad():
                t = torch.as_tensor(state, dtype=torch.float32).unsqueeze(0)
                q = agent.online_net(t).cpu().numpy().ravel()

        action_id = int(np.argmax(q))
        name = ACTION_NAMES.get(action_id, "HOLD")
        conf = _confidence_from_q(q)
        q_margin = _q_value_margin(q)
        live = live_by_sym.get(sym, {})
        out.append(
            {
                "symbol": sym,
                "name": live.get("name", sym),
                "category": live.get("category", "Unknown"),
                "price": live.get("price"),
                "action": name,
                "action_id": action_id,
                "q_values": [float(q[0]), float(q[1]), float(q[2])],
                "q_gap": q_margin,
                "confidence": round(conf, 1),
            }
        )
    return out


def read_metrics_json(models_dir: Path | None = None) -> dict | None:
    p = rl_paths(models_dir)["metrics"]
    if not p.is_file():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read_current_agent_meta(models_dir: Path | None = None) -> dict | None:
    p = rl_paths(models_dir)["current_meta"]
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
