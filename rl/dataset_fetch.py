"""Unified RL training CSV paths + optional download from env URLs (Render / local)."""

from __future__ import annotations

import os
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def training_data_dir(app_root: Path | None = None) -> Path:
    """
    Directory containing X_features_unified.csv and unified_training_data.csv.

    Override with MM_RL_DATA_DIR or RL_TRAINING_DATA_DIR (e.g. Render persistent disk mount).
    """
    override = (os.environ.get("MM_RL_DATA_DIR") or os.environ.get("RL_TRAINING_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    root = app_root if app_root is not None else Path(__file__).resolve().parent.parent
    return root.resolve()


def training_csv_paths(app_root: Path | None = None) -> tuple[Path, Path]:
    base = training_data_dir(app_root)
    return base / "X_features_unified.csv", base / "unified_training_data.csv"


def configured_dataset_url(*env_keys: str) -> str:
    for key in env_keys:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return ""


def _download_timeout_sec() -> int:
    raw = (os.environ.get("RL_DOWNLOAD_TIMEOUT_SEC") or "").strip()
    if raw.isdigit():
        return max(30, min(7200, int(raw)))
    return 300


def download_if_missing(target_path: Path, *env_keys: str) -> bool:
    """Hydrate a missing file from the first configured URL in env_keys."""
    if target_path.exists():
        return True
    url = configured_dataset_url(*env_keys)
    if not url:
        return False
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "MarketMinds-RL-dataset-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=_download_timeout_sec()) as resp:  # nosec B310
            code = getattr(resp, "status", 200) or 200
            if code >= 400:
                raise OSError(f"HTTP {code}")
            with open(target_path, "wb") as out:
                shutil.copyfileobj(resp, out)
        if not target_path.exists() or target_path.stat().st_size == 0:
            raise OSError("Downloaded file is empty")
        print(f"[RL data] Downloaded {target_path.name} from configured URL.")
        return True
    except Exception as e:
        print(f"[RL data] Failed to download {target_path.name}: {e}")
        return target_path.exists()


def missing_training_inputs(app_root: Path | None = None) -> list[str]:
    """Try URL downloads (parallel when both needed), then return basenames still missing."""
    x_path, p_path = training_csv_paths(app_root)
    futures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        if not x_path.exists():
            futures.append(
                pool.submit(
                    download_if_missing,
                    x_path,
                    "RL_X_FEATURES_URL",
                    "X_FEATURES_UNIFIED_URL",
                )
            )
        if not p_path.exists():
            futures.append(
                pool.submit(
                    download_if_missing,
                    p_path,
                    "RL_UNIFIED_TRAINING_URL",
                    "UNIFIED_TRAINING_DATA_URL",
                )
            )
        for fut in as_completed(futures):
            fut.result()
    missing: list[str] = []
    if not x_path.exists():
        missing.append(x_path.name)
    if not p_path.exists():
        missing.append(p_path.name)
    return missing


def log_startup_dataset_status(app_root: Path | None = None) -> None:
    """Log presence/missing + whether URLs are set (runs at app import and Render pre-start)."""
    root = app_root if app_root is not None else Path(__file__).resolve().parent.parent
    x_path, p_path = training_csv_paths(root)
    x_url = configured_dataset_url("RL_X_FEATURES_URL", "X_FEATURES_UNIFIED_URL")
    p_url = configured_dataset_url("RL_UNIFIED_TRAINING_URL", "UNIFIED_TRAINING_DATA_URL")
    missing_training_inputs(root)
    print(
        "[RL data] startup | "
        f"{x_path.name}: {'present' if x_path.exists() else 'missing'} "
        f"(url={'set' if x_url else 'unset'}) | "
        f"{p_path.name}: {'present' if p_path.exists() else 'missing'} "
        f"(url={'set' if p_url else 'unset'}) | "
        f"dir={training_data_dir(root)}"
    )
