#!/usr/bin/env python3
"""
Pre-start hook (Render): fetch RL unified CSVs from RL_X_FEATURES_URL / RL_UNIFIED_TRAINING_URL.

Safe to run multiple times; exits 0 even if files remain missing so Gunicorn still boots.
Run from repo root: python scripts/ensure_rl_training_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from rl.dataset_fetch import log_startup_dataset_status, training_csv_paths

    log_startup_dataset_status(ROOT)
    miss = [p.name for p in training_csv_paths(ROOT) if not p.exists()]
    if miss:
        print(f"[ensure_rl_training_data] Still missing: {', '.join(miss)} — set URLs or copy CSVs (see MM_RL_DATA_DIR).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
