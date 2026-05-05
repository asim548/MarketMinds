"""Offline evaluation metrics and equity curve plots for the RL agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from rl.dqn_agent import DoubleDQNAgent
from rl.trading_env import MarketTradingEnv


def evaluate_agent(
    agent: DoubleDQNAgent,
    env: MarketTradingEnv,
    n_episodes: int = 1,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run greedy episodes and return risk/return metrics; optional equity PNG."""
    initial = float(env.initial_capital)
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    round_trips = 0

    all_closed: list[float] = []
    for _ in range(n_episodes):
        obs, _ = env.reset()
        done = False
        while not done:
            a = agent.select_action(obs, greedy=True)
            obs, _r, terminated, _, _info = env.step(a)
            done = terminated
        all_closed.extend(env.closed_trade_returns)

    for pnl_pct in all_closed:
        round_trips += 1
        if pnl_pct > 0:
            wins += 1
            gross_profit += pnl_pct
        else:
            losses += 1
            gross_loss += abs(pnl_pct)

    ph = np.asarray(env.portfolio_history, dtype=np.float64)
    final_value = float(ph[-1]) if len(ph) else initial
    total_return_pct = float((final_value - initial) / (initial + 1e-8) * 100.0)

    if len(ph) > 1:
        peak = np.maximum.accumulate(ph)
        dd = (peak - ph) / (peak + 1e-8)
        max_drawdown_pct = float(np.max(dd) * 100.0)
    else:
        max_drawdown_pct = 0.0

    rets = np.diff(ph) / (ph[:-1] + 1e-8) if len(ph) > 2 else np.array([])
    if len(rets) > 1:
        ann = np.sqrt(252 * 6.5)
        sharpe_ratio = float(np.mean(rets) / (np.std(rets) + 1e-8) * ann)
        downside = rets[rets < 0]
        if len(downside) > 0:
            ds = float(np.std(downside))
            sortino_ratio = float(np.mean(rets) / (ds + 1e-8) * ann)
        else:
            sortino_ratio = float(sharpe_ratio)
    else:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0

    calmar_ratio = float(
        total_return_pct / (max_drawdown_pct + 1e-8) if max_drawdown_pct > 0 else total_return_pct
    )
    win_rate = float(wins / (wins + losses + 1e-8)) if (wins + losses) else 0.0
    profit_factor = float(gross_profit / (gross_loss + 1e-8)) if gross_loss > 0 else float(gross_profit)

    metrics = {
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "n_trades": int(round_trips),
    }

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        _save_equity_plot(env, out / "rl_equity_curve.png")

    return metrics


def _save_equity_plot(env: MarketTradingEnv, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ph = np.asarray(env.portfolio_history, dtype=np.float64)
    t = np.arange(len(ph))
    peak = np.maximum.accumulate(ph)
    dd = (peak - ph) / (peak + 1e-8) * 100.0

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(t, ph, color="#c9a227", linewidth=1.2, label="Portfolio")
    ax1.set_ylabel("Value")
    ax1.set_title("RL Agent — Equity & Drawdown")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    ax2.fill_between(t, 0, dd, color="#c62828", alpha=0.35)
    ax2.plot(t, dd, color="#c62828", linewidth=0.8)
    ax2.set_ylabel("Drawdown %")
    ax2.set_xlabel("Step")
    ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close(fig)
