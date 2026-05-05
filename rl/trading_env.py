"""Gymnasium trading environment: 34 market features + 3 portfolio features (37-dim state)."""

from __future__ import annotations

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces

    _EnvBase = gym.Env
except ImportError:  # pragma: no cover
    try:
        import gym
        from gym import spaces

        _EnvBase = gym.Env
    except ImportError:

        class spaces:  # minimal shim
            class Discrete:
                def __init__(self, n: int):
                    self.n = n

            class Box:
                def __init__(self, low, high, shape, dtype):
                    self.low = low
                    self.high = high
                    self.shape = shape
                    self.dtype = dtype

        _EnvBase = object


class MarketTradingEnv(_EnvBase):
    """Single-asset RL environment aligned with MarketMinds unified feature rows."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        X: np.ndarray,
        prices: np.ndarray,
        initial_capital: float = 10_000.0,
        transaction_cost: float = 0.001,
        max_position_pct: float = 1.0,
    ):
        if _EnvBase is not object:
            super().__init__()
        if len(X) != len(prices):
            n = min(len(X), len(prices))
            X = X[:n]
            prices = prices[:n]
        if len(prices) < 2:
            raise ValueError("MarketTradingEnv requires at least 2 timesteps (X and prices).")

        self.X = np.asarray(X, dtype=np.float32)
        self.prices = np.asarray(prices, dtype=np.float64)
        if self.X.shape[1] != 34:
            raise ValueError(f"Expected 34 feature columns, got {self.X.shape[1]}")

        self.initial_capital = float(initial_capital)
        self.transaction_cost = float(transaction_cost)
        self.max_position_pct = float(max_position_pct)

        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(37,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(3)

        self.t = 0
        self.capital = self.initial_capital
        self.shares = 0.0
        self.entry_price = 0.0
        self.peak_value = self.initial_capital
        self.portfolio_history: list[float] = []
        self._buy_count = 0
        self._sell_count = 0
        self.closed_trade_returns: list[float] = []
        # (index into portfolio_history after a step, "buy" | "sell") for charts
        self.trade_marks: list[tuple[int, str]] = []

    def reset(self, seed=None, options=None):
        if _EnvBase is not object:
            super().reset(seed=seed)
        self.t = 0
        self.capital = self.initial_capital
        self.shares = 0.0
        self.entry_price = 0.0
        self.peak_value = self.initial_capital
        self.portfolio_history = [self.initial_capital]
        self._buy_count = 0
        self._sell_count = 0
        self.closed_trade_returns = []
        self.trade_marks = []
        return self._get_obs(), {}

    def _portfolio_value(self, price: float) -> float:
        return float(self.capital + self.shares * price)

    def _get_obs(self) -> np.ndarray:
        market = self.X[self.t].astype(np.float32, copy=False)
        price = float(self.prices[self.t])
        if self.shares > 0:
            position_flag = np.float32(1.0)
            unrealised = np.float32((price - self.entry_price) / (self.entry_price + 1e-8))
        else:
            position_flag = np.float32(0.0)
            unrealised = np.float32(0.0)
        pv = self._portfolio_value(price)
        drawdown = np.float32((self.peak_value - pv) / (self.peak_value + 1e-8))
        port = np.array([position_flag, unrealised, drawdown], dtype=np.float32)
        return np.concatenate([market, port], axis=0).astype(np.float32)

    def step(self, action):
        price = float(self.prices[self.t])
        portfolio_before = self._portfolio_value(price)
        is_invalid = False
        did_buy = False
        did_sell = False

        if action == 1:  # BUY
            if self.shares == 0.0:
                invest = self.capital * self.max_position_pct
                cost = invest * self.transaction_cost
                spend = invest
                self.shares = (invest - cost) / (price + 1e-8)
                self.capital -= spend
                self.entry_price = price
                self._buy_count += 1
                did_buy = True
            else:
                is_invalid = True
        elif action == 2:  # SELL
            if self.shares > 0.0:
                entry = self.entry_price
                proceeds = self.shares * price * (1.0 - self.transaction_cost)
                if entry > 0:
                    self.closed_trade_returns.append(float((price - entry) / (entry + 1e-8)))
                self.capital += proceeds
                self.shares = 0.0
                self.entry_price = 0.0
                self._sell_count += 1
                did_sell = True
            else:
                is_invalid = True

        self.t += 1
        next_price = float(self.prices[self.t])
        portfolio_after = self._portfolio_value(next_price)
        self.peak_value = max(self.peak_value, portfolio_after)
        self.portfolio_history.append(portfolio_after)
        mi = len(self.portfolio_history) - 1
        if did_buy:
            self.trade_marks.append((mi, "buy"))
        if did_sell:
            self.trade_marks.append((mi, "sell"))

        step_return = (portfolio_after - portfolio_before) / (portfolio_before + 1e-8)
        trade_penalty = 0.001 if action in (1, 2) else 0.0
        invalid_penalty = 0.002 if is_invalid else 0.0
        reward = float(step_return * 100.0 - trade_penalty - invalid_penalty)
        reward = float(np.clip(reward, -10.0, 10.0))

        terminated = bool(self.t >= len(self.prices) - 1)
        info = {"portfolio_value": portfolio_after, "is_invalid": is_invalid}
        return self._get_obs(), reward, terminated, False, info

    def get_metrics(self) -> dict:
        ph = np.asarray(self.portfolio_history, dtype=np.float64)
        final_value = float(ph[-1]) if len(ph) else self.initial_capital
        total_return_pct = float((final_value - self.initial_capital) / (self.initial_capital + 1e-8) * 100.0)

        if len(ph) > 1:
            peak = np.maximum.accumulate(ph)
            dd = (peak - ph) / (peak + 1e-8)
            max_drawdown_pct = float(np.max(dd) * 100.0)
        else:
            max_drawdown_pct = 0.0

        if len(ph) > 2:
            rets = np.diff(ph) / (ph[:-1] + 1e-8)
            sharpe_ratio = float(
                np.mean(rets) / (np.std(rets) + 1e-8) * np.sqrt(252 * 6.5)
            )
        else:
            sharpe_ratio = 0.0

        ctr = self.closed_trade_returns
        if ctr:
            arr = np.asarray(ctr, dtype=np.float64)
            win_rate_pct = float(np.mean(arr > 0.0) * 100.0)
            pos_sum = float(arr[arr > 0.0].sum())
            neg_sum = float(-arr[arr < 0.0].sum())
            profit_factor = float(pos_sum / neg_sum) if neg_sum > 1e-12 else (99.0 if pos_sum > 0 else 0.0)
        else:
            win_rate_pct = 0.0
            profit_factor = 0.0

        n_trade_events = int(self._buy_count + self._sell_count)

        return {
            "final_portfolio_value": final_value,
            "total_return_pct": total_return_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "sharpe_ratio": sharpe_ratio,
            "n_trades": int(self._buy_count),
            "n_sells": int(self._sell_count),
            "n_trade_events": n_trade_events,
            "win_rate_pct": win_rate_pct,
            "profit_factor": profit_factor,
        }
