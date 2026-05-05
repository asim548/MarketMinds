"""Train Double DQN on unified MarketMinds feature data."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from rl import RL_MODELS_DIR
from rl.dqn_agent import DoubleDQNAgent
from rl.production import (
    is_full_training_confirmed,
    publish_latest_aliases,
    resolve_checkpoint_paths,
    training_requires_confirmation,
    write_current_agent_meta,
)
from rl.replay_buffer import PrioritizedReplayBuffer, ReplayBuffer
from rl.trading_env import MarketTradingEnv


def _log(msg: str, log_path: Path | None) -> None:
    print(msg, flush=True)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


def _plot_equity_curve_png(
    portfolio_hist: list[float],
    trade_marks: list[tuple[int, str]],
    initial_capital: float,
    out_path: Path,
    log_path: Path | None,
) -> None:
    """Two-panel chart: equity path with buy/sell markers + drawdown % (saved next to checkpoints)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        _log("[WARN] matplotlib not available; skipping rl_equity_curve.png", log_path)
        return

    ph = np.asarray(portfolio_hist, dtype=np.float64)
    if ph.size < 2:
        _log("[WARN] portfolio history too short; skipping rl_equity_curve.png", log_path)
        return

    x = np.arange(ph.size, dtype=np.int32)
    peak = np.maximum.accumulate(ph)
    dd_pct = (peak - ph) / (peak + 1e-12) * 100.0

    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(11, 7.2), gridspec_kw={"height_ratios": [2.1, 1.0]})
    fig.patch.set_facecolor("#0b1220")
    for ax in (ax0, ax1):
        ax.set_facecolor("#111827")

    ax0.plot(x, ph, color="#2dd4bf", linewidth=1.25, label="Portfolio")
    ax0.axhline(initial_capital, color="#64748b", linestyle="--", linewidth=0.9, alpha=0.85)
    for mi, kind in trade_marks:
        if 0 <= mi < ph.size:
            if kind == "buy":
                ax0.scatter(mi, ph[mi], c="#4ade80", s=42, marker="^", zorder=5, edgecolors="#052e16", linewidths=0.4)
            elif kind == "sell":
                ax0.scatter(mi, ph[mi], c="#f87171", s=42, marker="v", zorder=5, edgecolors="#450a0a", linewidths=0.4)
    ax0.set_ylabel("Portfolio value ($)", color="#e2e8f0")
    ax0.tick_params(colors="#94a3b8")
    ax0.grid(True, alpha=0.15)
    ax0.set_title("Validation equity & trade markers", color="#f1f5f9", fontsize=12)

    neg_dd = -dd_pct
    ax1.fill_between(x, 0.0, neg_dd, where=(neg_dd < 0), color="#b91c1c", alpha=0.35, interpolate=True)
    ax1.plot(x, neg_dd, color="#f87171", linewidth=1.0)
    ax1.axhline(-8.0, color="#fbbf24", linestyle="--", linewidth=0.9, alpha=0.9, label="-8% guide")
    ax1.set_ylabel("Drawdown (%)", color="#e2e8f0")
    ax1.set_xlabel("Step (validation window)", color="#94a3b8")
    ax1.tick_params(colors="#94a3b8")
    ax1.grid(True, alpha=0.15)
    ax1.legend(loc="lower right", fontsize=8, facecolor="#1e293b", edgecolor="#334155", labelcolor="#e2e8f0")

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    _log(f"Equity chart saved: {out_path.name}", log_path)


def train_rl_agent(
    X_path: str | Path,
    price_path: str | Path,
    output_dir: str | Path | None = None,
    n_episodes: int = 10,
    seed: int = 42,
    log_file: str | Path | None = None,
    use_existing: bool = False,
    max_rows: int | None = None,
    full_training_acknowledged: bool = False,
    dueling: bool = False,
    prioritized: bool = False,
) -> dict:
    np.random.seed(seed)

    out = Path(output_dir) if output_dir else RL_MODELS_DIR
    out.mkdir(parents=True, exist_ok=True)
    log_path = Path(log_file) if log_file else out / "training_log.txt"

    X_df = pd.read_csv(X_path)
    if X_df.shape[1] != 34:
        raise ValueError(f"Expected 34 feature columns in X CSV, found {X_df.shape[1]}")
    X_all = X_df.values.astype(np.float32)

    price_df = pd.read_csv(price_path)
    if "close" not in price_df.columns:
        raise ValueError("price CSV must contain a 'close' column")
    price_df = price_df.reset_index(drop=True)
    prices_all = price_df["close"].astype(np.float64).values

    n = min(len(X_all), len(prices_all))
    if max_rows is not None and max_rows > 0:
        n = min(n, int(max_rows))
    X_all = X_all[:n]
    prices_all = prices_all[:n]

    if training_requires_confirmation(n, max_rows) and not is_full_training_confirmed(
        full_training_acknowledged
    ):
        raise RuntimeError(
            "Full unified dataset training requires explicit acknowledgement (PDF §8.2). "
            "Options: pass full_training_acknowledged=True from code, use CLI "
            "`--i-confirm-full-training`, or set environment variable RL_TRAINING_APPROVED=1."
        )

    split = int(0.8 * n)
    X_train, X_val = X_all[:split], X_all[split:]
    p_train, p_val = prices_all[:split], prices_all[split:]

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train).astype(np.float32)
    X_val = scaler.transform(X_val).astype(np.float32)
    joblib.dump(scaler, out / "rl_scaler.pkl")

    agent = DoubleDQNAgent(dueling=dueling)
    state_dim = 37
    if prioritized and agent._sk is None:
        buffer: PrioritizedReplayBuffer | ReplayBuffer = PrioritizedReplayBuffer(
            state_dim=state_dim, capacity=50_000
        )
    else:
        buffer = ReplayBuffer(capacity=50_000)
        if prioritized and agent._sk is not None:
            _log(
                "[WARN] Prioritized replay requires PyTorch; using uniform replay for sklearn agent.",
                log_path,
            )

    ag_path, _ = resolve_checkpoint_paths(out)
    if use_existing and ag_path is not None and ag_path.is_file():
        agent.load(str(ag_path))

    last_loss = 0.0
    for ep in range(1, n_episodes + 1):
        env = MarketTradingEnv(X_train, p_train)
        obs, _ = env.reset()
        done = False
        while not done:
            action = agent.select_action(obs, greedy=False)
            next_obs, reward, terminated, _, _info = env.step(action)
            buffer.push(obs, action, reward, next_obs, float(terminated))
            last_loss = agent.train_step(buffer)
            obs = next_obs
            done = terminated

        m = env.get_metrics()
        te = int(m.get("n_trade_events", m["n_trades"]))
        # Readable per-episode line (trends: return, trade count, epsilon decay)
        msg = (
            f"Episode {ep}/{n_episodes}  | Return: {m['total_return_pct']:+.1f}%  | "
            f"Sharpe: {m['sharpe_ratio']:.2f}  | Trades: {te}  | Epsilon: {agent.epsilon:.2f}"
        )
        try:
            lv = float(last_loss)
        except (TypeError, ValueError):
            lv = 0.0
        if lv > 0:
            msg += f"  | Loss: {lv:.4f}"
        _log(msg, log_path)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    versioned_weights = out / f"rl_agent_{ts}.pth"
    versioned_scaler = out / f"rl_scaler_{ts}.pkl"
    agent.save(str(versioned_weights))
    shutil.copy2(out / "rl_scaler.pkl", versioned_scaler)
    write_current_agent_meta(
        versioned_weights.name,
        versioned_scaler.name,
        dueling=bool(getattr(agent, "_dueling", False)),
        prioritized=prioritized and agent._sk is None,
        base=out,
    )
    publish_latest_aliases(versioned_weights, versioned_scaler, base=out)

    # --- validation (greedy) ---
    agent.epsilon = 0.0
    if hasattr(agent, "_sk") and agent._sk is not None:
        agent._sk.epsilon = 0.0
    venv = MarketTradingEnv(X_val, p_val)
    obs, _ = venv.reset()
    done = False
    while not done:
        a = agent.select_action(obs, greedy=True)
        obs, _r, terminated, _, _ = venv.step(a)
        done = terminated

    val_metrics = venv.get_metrics()
    portfolio_hist = [float(x) for x in venv.portfolio_history]
    marks = [(int(i), str(k)) for i, k in getattr(venv, "trade_marks", [])]
    curve_png = out / "rl_equity_curve.png"
    _plot_equity_curve_png(portfolio_hist, marks, float(venv.initial_capital), curve_png, log_path)

    summary = {
        "validation": val_metrics,
        "portfolio_history_val": portfolio_hist,
        "val_trade_marks": marks,
        "equity_curve_png": curve_png.name,
        "n_train_rows": int(split),
        "n_val_rows": int(n - split),
        "metrics_generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train_episodes_completed": int(n_episodes),
        "epsilon": float(agent.epsilon),
        "checkpoint": {
            "weights": versioned_weights.name,
            "scaler": versioned_scaler.name,
            "dueling": bool(getattr(agent, "_dueling", False)),
            "prioritized_training": prioritized and agent._sk is None,
        },
    }
    with open(out / "rl_metrics.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    _log(
        f"--- Validation --- | Return: {val_metrics['total_return_pct']:+.2f}%  | "
        f"Sharpe: {val_metrics['sharpe_ratio']:.2f}  | MaxDD: {val_metrics['max_drawdown_pct']:.2f}%  | "
        f"Win%: {val_metrics.get('win_rate_pct', 0):.1f}  | PF: {val_metrics.get('profit_factor', 0):.2f}  | "
        f"Trades(val): {val_metrics.get('n_trade_events', val_metrics['n_trades'])}",
        log_path,
    )
    _log(f"Checkpoint saved: {versioned_weights.name} (+ current_agent.json)", log_path)
    return summary


def _default_paths():
    root = Path(__file__).resolve().parent.parent
    return (
        root / "X_features_unified.csv",
        root / "unified_training_data.csv",
        RL_MODELS_DIR,
    )


if __name__ == "__main__":
    d_x, d_p, d_out = _default_paths()
    parser = argparse.ArgumentParser(description="Train MarketMinds RL (Double DQN)")
    parser.add_argument("--X_path", type=str, default=str(d_x))
    parser.add_argument("--price_path", type=str, default=str(d_p))
    parser.add_argument("--output_dir", type=str, default=str(d_out))
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--use_existing", action="store_true")
    parser.add_argument(
        "--max_rows",
        type=int,
        default=None,
        help="Cap rows after alignment (for smoke tests). Default: use full dataset.",
    )
    parser.add_argument(
        "--i-confirm-full-training",
        action="store_true",
        help="Required when training on the full unified row count (≥50k rows without --max_rows).",
    )
    parser.add_argument("--dueling", action="store_true", help="Use dueling Q architecture (PyTorch).")
    parser.add_argument("--per", action="store_true", help="Prioritized experience replay (PyTorch).")
    args = parser.parse_args()

    train_rl_agent(
        args.X_path,
        args.price_path,
        output_dir=args.output_dir,
        n_episodes=args.episodes,
        seed=args.seed,
        use_existing=args.use_existing,
        max_rows=args.max_rows,
        full_training_acknowledged=args.i_confirm_full_training,
        dueling=args.dueling,
        prioritized=args.per,
    )
