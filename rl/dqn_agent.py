"""Double DQN agent (PyTorch) with sklearn MLP fallback."""

from __future__ import annotations

import copy
import os
from typing import Optional

import numpy as np

from rl.replay_buffer import PrioritizedReplayBuffer, ReplayBuffer

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    _TORCH = True
except ImportError:  # pragma: no cover
    _TORCH = False
    torch = None  # type: ignore
    nn = None  # type: ignore
    F = None  # type: ignore

if _TORCH:

    class QNetwork(nn.Module):
        def __init__(self, state_dim: int = 37, action_dim: int = 3, hidden_dims=None):
            super().__init__()
            if hidden_dims is None:
                hidden_dims = [256, 128, 64]
            h1, h2, h3 = hidden_dims
            self.net = nn.Sequential(
                nn.Linear(state_dim, h1),
                nn.LayerNorm(h1),
                nn.ReLU(),
                nn.Linear(h1, h2),
                nn.LayerNorm(h2),
                nn.ReLU(),
                nn.Linear(h2, h3),
                nn.ReLU(),
                nn.Linear(h3, action_dim),
            )

        def forward(self, x):
            return self.net(x)

    class DuelingQNetwork(nn.Module):
        """Dueling architecture (Wang et al.) — value + advantage streams."""

        def __init__(self, state_dim: int = 37, action_dim: int = 3, hidden_dims=None):
            super().__init__()
            if hidden_dims is None:
                hidden_dims = [256, 128, 64]
            h1, h2, h3 = hidden_dims
            self.body = nn.Sequential(
                nn.Linear(state_dim, h1),
                nn.LayerNorm(h1),
                nn.ReLU(),
                nn.Linear(h1, h2),
                nn.LayerNorm(h2),
                nn.ReLU(),
            )
            self.value = nn.Sequential(
                nn.Linear(h2, h3),
                nn.ReLU(),
                nn.Linear(h3, 1),
            )
            self.advantage = nn.Sequential(
                nn.Linear(h2, h3),
                nn.ReLU(),
                nn.Linear(h3, action_dim),
            )

        def forward(self, x):
            z = self.body(x)
            v = self.value(z)
            a = self.advantage(z)
            return v + (a - a.mean(dim=1, keepdim=True))


class _SklearnDoubleDQN:
    """Minimal Double-DQN-style learner using two MLPRegressors (online / target)."""

    def __init__(
        self,
        state_dim: int = 37,
        action_dim: int = 3,
        lr: float = 1e-3,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_min: float = 0.05,
        epsilon_decay: float = 0.9995,
        target_update_freq: int = 500,
        buffer_size: int = 50_000,
        batch_size: int = 256,
    ):
        from sklearn.neural_network import MLPRegressor

        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lr = lr
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.target_update_freq = target_update_freq
        self.batch_size = batch_size
        self.step_count = 0
        hidden = (256, 128, 64)
        kwargs = dict(
            hidden_layer_sizes=hidden,
            activation="relu",
            solver="adam",
            learning_rate_init=lr,
            max_iter=1,
            warm_start=True,
            alpha=1e-5,
        )
        self._online = MLPRegressor(**kwargs)
        self._target = MLPRegressor(**kwargs)
        self._fitted = False

    def _predict_q(self, model, states: np.ndarray) -> np.ndarray:
        if not self._fitted:
            return np.zeros((states.shape[0], self.action_dim), dtype=np.float32)
        return model.predict(states).astype(np.float32)

    def select_action(self, state: np.ndarray, greedy: bool = False) -> int:
        s = state.reshape(1, -1).astype(np.float32)
        if not greedy and np.random.random() < self.epsilon:
            a = int(np.random.randint(0, self.action_dim))
        else:
            q = self._predict_q(self._online, s)[0]
            a = int(np.argmax(q))
        if not greedy:
            self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        return a

    def train_step(self, buffer: ReplayBuffer) -> float:
        if len(buffer) < self.batch_size:
            return 0.0
        states, actions, rewards, next_states, dones = buffer.sample(self.batch_size)
        if not self._fitted:
            self._online.partial_fit(
                states, np.zeros((self.batch_size, self.action_dim), dtype=np.float64)
            )
            self._target = copy.deepcopy(self._online)
            self._fitted = True

        q_on = self._predict_q(self._online, states)
        q_next_on = self._predict_q(self._online, next_states)
        q_next_tgt = self._predict_q(self._target, next_states)
        best = np.argmax(q_next_on, axis=1)
        target_q = rewards + self.gamma * q_next_tgt[np.arange(self.batch_size), best] * (1.0 - dones)
        target_mat = q_on.copy()
        target_mat[np.arange(self.batch_size), actions] = target_q

        self._online.partial_fit(states, target_mat)
        loss = float(getattr(self._online, "loss_", 0.0))

        self.step_count += 1
        if self.step_count % self.target_update_freq == 0:
            self._target = copy.deepcopy(self._online)
        return loss

    def save(self, path: str) -> None:
        import joblib

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump(
            {
                "backend": "sklearn",
                "online": self._online,
                "target": self._target,
                "epsilon": self.epsilon,
                "fitted": self._fitted,
            },
            path,
        )

    def load(self, path: str, data: Optional[dict] = None) -> None:
        import joblib

        blob = data if data is not None else joblib.load(path)
        self._online = blob["online"]
        self._target = blob["target"]
        self.epsilon = float(blob.get("epsilon", self.epsilon_min))
        self._fitted = bool(blob.get("fitted", True))


class DoubleDQNAgent:
    def __init__(
        self,
        state_dim: int = 37,
        action_dim: int = 3,
        lr: float = 1e-3,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_min: float = 0.05,
        epsilon_decay: float = 0.9995,
        target_update_freq: int = 500,
        buffer_size: int = 50_000,
        batch_size: int = 256,
        dueling: bool = False,
    ):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lr = lr
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.target_update_freq = target_update_freq
        self.batch_size = batch_size
        self.step_count = 0
        self._torch = _TORCH
        self._sk: Optional[_SklearnDoubleDQN] = None
        self._device = torch.device("cpu") if _TORCH else None
        self._dueling = bool(dueling)

        if _TORCH:
            cls = DuelingQNetwork if self._dueling else QNetwork
            self.online_net = cls(state_dim, action_dim).to(self._device)
            self.target_net = cls(state_dim, action_dim).to(self._device)
            self.target_net.load_state_dict(self.online_net.state_dict())
            self.optimizer = torch.optim.Adam(self.online_net.parameters(), lr=lr)
        else:
            self._dueling = False
            self._sk = _SklearnDoubleDQN(
                state_dim=state_dim,
                action_dim=action_dim,
                lr=lr,
                gamma=gamma,
                epsilon=epsilon,
                epsilon_min=epsilon_min,
                epsilon_decay=epsilon_decay,
                target_update_freq=target_update_freq,
                buffer_size=buffer_size,
                batch_size=batch_size,
            )
            self.online_net = None
            self.target_net = None
            self.optimizer = None

    def select_action(self, state: np.ndarray, greedy: bool = False) -> int:
        if self._sk is not None:
            return self._sk.select_action(state, greedy=greedy)
        if self.online_net is None:
            raise RuntimeError("RL agent is not loaded or initialized.")
        assert self.online_net is not None
        if not greedy and np.random.random() < self.epsilon:
            a = int(np.random.randint(0, self.action_dim))
        else:
            with torch.no_grad():
                t = torch.as_tensor(state, dtype=torch.float32, device=self._device).unsqueeze(0)
                q = self.online_net(t)
                a = int(q.argmax(dim=1).item())
        if not greedy:
            self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        return a

    def train_step(self, buffer: ReplayBuffer | PrioritizedReplayBuffer) -> float:
        if self._sk is not None:
            if isinstance(buffer, PrioritizedReplayBuffer):
                return 0.0
            return self._sk.train_step(buffer)
        if self.online_net is None:
            return 0.0
        assert self.online_net is not None and self.target_net is not None and self.optimizer is not None
        if len(buffer) < self.batch_size:
            return 0.0

        if isinstance(buffer, PrioritizedReplayBuffer):
            states, actions, rewards, next_states, dones, idx, weights = buffer.sample(self.batch_size)
            weights_t = torch.as_tensor(weights, dtype=torch.float32, device=self._device)
        else:
            states, actions, rewards, next_states, dones = buffer.sample(self.batch_size)
            weights_t = None
            idx = None

        states_t = torch.as_tensor(states, dtype=torch.float32, device=self._device)
        actions_t = torch.as_tensor(actions, dtype=torch.int64, device=self._device)
        rewards_t = torch.as_tensor(rewards, dtype=torch.float32, device=self._device)
        next_states_t = torch.as_tensor(next_states, dtype=torch.float32, device=self._device)
        dones_t = torch.as_tensor(dones, dtype=torch.float32, device=self._device)

        with torch.no_grad():
            next_q_online = self.online_net(next_states_t)
            next_actions = next_q_online.argmax(dim=1, keepdim=True)
            next_q_target = self.target_net(next_states_t).gather(1, next_actions).squeeze(1)
            target_q = rewards_t + self.gamma * next_q_target * (1.0 - dones_t)

        current_q = self.online_net(states_t).gather(1, actions_t.unsqueeze(1)).squeeze(1)
        if weights_t is not None:
            per_el = F.smooth_l1_loss(current_q, target_q.detach(), reduction="none")
            loss = (weights_t * per_el).mean()
        else:
            loss = F.smooth_l1_loss(current_q, target_q)

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.online_net.parameters(), 1.0)
        self.optimizer.step()

        if isinstance(buffer, PrioritizedReplayBuffer) and idx is not None:
            td = (current_q - target_q).detach().cpu().numpy()
            buffer.update_priorities(idx, td)

        self.step_count += 1
        if self.step_count % self.target_update_freq == 0:
            self.target_net.load_state_dict(self.online_net.state_dict())

        return float(loss.item())

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if self._sk is not None:
            self._sk.epsilon = self.epsilon
            self._sk.save(path)
            return
        if self.online_net is None or self.target_net is None:
            raise RuntimeError("Cannot save: PyTorch networks not initialized.")
        assert self.online_net is not None and self.target_net is not None
        torch.save(
            {
                "backend": "torch",
                "online": self.online_net.state_dict(),
                "target": self.target_net.state_dict(),
                "epsilon": self.epsilon,
                "dueling": self._dueling,
            },
            path,
        )

    def load(self, path: str) -> None:
        import joblib

        try:
            blob = joblib.load(path)
            if isinstance(blob, dict) and blob.get("backend") == "sklearn":
                self.online_net = None
                self.target_net = None
                self.optimizer = None
                self._sk = _SklearnDoubleDQN(
                    state_dim=self.state_dim,
                    action_dim=self.action_dim,
                    lr=self.lr,
                    gamma=self.gamma,
                    epsilon=self.epsilon_min,
                    epsilon_min=self.epsilon_min,
                    epsilon_decay=self.epsilon_decay,
                    target_update_freq=self.target_update_freq,
                    batch_size=self.batch_size,
                )
                self._sk.load(path, data=blob)
                self.epsilon = self._sk.epsilon
                return
        except Exception:
            pass

        if not _TORCH:
            raise RuntimeError("Cannot load PyTorch RL checkpoint: torch is not installed.")

        try:
            payload = torch.load(path, map_location="cpu", weights_only=False)
        except TypeError:
            payload = torch.load(path, map_location="cpu")

        self._sk = None
        duel = bool(payload.get("dueling", False))
        self._dueling = duel
        cls = DuelingQNetwork if duel else QNetwork
        if self.online_net is None or not isinstance(self.online_net, cls):
            assert self._device is not None
            self.online_net = cls(self.state_dim, self.action_dim).to(self._device)
            self.target_net = cls(self.state_dim, self.action_dim).to(self._device)
            self.optimizer = torch.optim.Adam(self.online_net.parameters(), lr=self.lr)
        assert self.online_net is not None and self.target_net is not None
        self.online_net.load_state_dict(payload["online"])
        self.target_net.load_state_dict(payload["target"])
        self.epsilon = float(payload.get("epsilon", self.epsilon_min))
