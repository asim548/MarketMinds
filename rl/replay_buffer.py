"""Experience replay: uniform and prioritized (simplified proportional sampling)."""

from __future__ import annotations

from collections import deque
import numpy as np


class ReplayBuffer:
    def __init__(self, capacity: int = 50_000, prioritized: bool = False):
        self.capacity = capacity
        self.prioritized = prioritized
        self._storage = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done) -> None:
        self._storage.append(
            (
                np.asarray(state, dtype=np.float32),
                int(action),
                float(reward),
                np.asarray(next_state, dtype=np.float32),
                float(done),
            )
        )

    def sample(self, batch_size: int):
        idx = np.random.choice(len(self._storage), size=batch_size, replace=False)
        batch = [self._storage[i] for i in idx]
        states = np.stack([b[0] for b in batch], axis=0).astype(np.float32)
        actions = np.array([b[1] for b in batch], dtype=np.int64)
        rewards = np.array([b[2] for b in batch], dtype=np.float32)
        next_states = np.stack([b[3] for b in batch], axis=0).astype(np.float32)
        dones = np.array([b[4] for b in batch], dtype=np.float32)
        return states, actions, rewards, next_states, dones

    def __len__(self) -> int:
        return len(self._storage)


class PrioritizedReplayBuffer:
    """
    Ring buffer with proportional sampling (priorities ** alpha).
    TD-error updates after each train step (Schaul et al. style, without sum-tree).
    """

    def __init__(self, state_dim: int, capacity: int = 50_000, alpha: float = 0.6, eps: float = 1e-6):
        self.state_dim = int(state_dim)
        self.capacity = int(capacity)
        self.alpha = float(alpha)
        self.eps = float(eps)
        self._pos = 0
        self._size = 0
        self.states = np.zeros((capacity, state_dim), dtype=np.float32)
        self.next_states = np.zeros((capacity, state_dim), dtype=np.float32)
        self.actions = np.zeros(capacity, dtype=np.int64)
        self.rewards = np.zeros(capacity, dtype=np.float32)
        self.dones = np.zeros(capacity, dtype=np.float32)
        self.priorities = np.ones(capacity, dtype=np.float32)

    def push(self, state, action, reward, next_state, done) -> None:
        i = self._pos
        self.states[i] = np.asarray(state, dtype=np.float32)
        self.next_states[i] = np.asarray(next_state, dtype=np.float32)
        self.actions[i] = int(action)
        self.rewards[i] = float(reward)
        self.dones[i] = float(done)
        max_prio = float(self.priorities[: max(1, self._size)].max()) if self._size > 0 else 1.0
        self.priorities[i] = max(max_prio, 1.0)
        self._pos = (self._pos + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def sample(self, batch_size: int):
        n = len(self)
        if n < batch_size:
            raise ValueError("buffer underrun")
        prios = self.priorities[:n] ** self.alpha
        probs = (prios + self.eps) / (prios.sum() + self.eps * n + 1e-8)
        probs = probs / probs.sum()
        idx = np.random.choice(n, size=batch_size, replace=False, p=probs)
        states = self.states[idx].copy()
        next_states = self.next_states[idx].copy()
        actions = self.actions[idx].copy()
        rewards = self.rewards[idx].copy()
        dones = self.dones[idx].copy()
        # Importance-sampling weights (normalize to max weight ≈ 1)
        w = (n * probs[idx]) ** (-0.4)
        w = w / (w.max() + 1e-8)
        weights = w.astype(np.float32)
        return states, actions, rewards, next_states, dones, idx, weights

    def update_priorities(self, idx: np.ndarray, td_errors: np.ndarray) -> None:
        for i, err in zip(idx, td_errors):
            self.priorities[int(i)] = float(abs(err)) + self.eps

    def __len__(self) -> int:
        return int(self._size)
