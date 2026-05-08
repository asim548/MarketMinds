"""
Shared Hugging Face sentiment pipelines for FinancialPulse.

- One FinBERT instance for both sentiment_engine and ml_engine (avoids duplicate RAM).
- Lazy loading: models load on first scoring call, not at import time (faster port bind on Render).
- On Render, FinBERT is skipped by default unless MARKETMINDS_ENABLE_FINBERT=1 (512Mi OOMs).
- DistilRoBERTa skipped on Render unless MARKETMINDS_ENABLE_DISTIL_ROBERTA=1.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_finbert_pipe: Any = None
_distil_pipe: Any = None
_finbert_failed = False
_distil_failed = False
_finbert_skip_logged = False


def skip_finbert() -> bool:
    if (os.getenv("MARKETMINDS_DISABLE_FINBERT") or "").strip().lower() in ("1", "true", "yes"):
        return True
    if (os.getenv("MARKETMINDS_ENABLE_FINBERT") or "").strip().lower() in ("1", "true", "yes"):
        return False
    # 512Mi Render tiers cannot hold FinBERT + scipy/sklearn/Flask/YOLO stacks reliably.
    return bool(os.getenv("RENDER"))


def skip_distilroberta() -> bool:
    if (os.getenv("MARKETMINDS_SKIP_DISTIL_ROBERTA") or "").strip().lower() in ("1", "true", "yes"):
        return True
    if (os.getenv("MARKETMINDS_ENABLE_DISTIL_ROBERTA") or "").strip().lower() in ("1", "true", "yes"):
        return False
    return bool(os.getenv("RENDER"))


def get_finbert_pipeline() -> Optional[Any]:
    global _finbert_pipe, _finbert_failed, _finbert_skip_logged
    if skip_finbert():
        if not _finbert_skip_logged:
            _finbert_skip_logged = True
            logger.info(
                "[HF] FinBERT skipped on Render by default (RAM). "
                "Set MARKETMINDS_ENABLE_FINBERT=1 after moving to a larger instance (e.g. >= 2 GiB RAM)."
            )
        return None
    if _finbert_failed:
        return None
    with _lock:
        if _finbert_pipe is None and not _finbert_failed:
            try:
                from transformers import pipeline as hf_pipeline

                logger.info("[HF] Loading ProsusAI/finbert (shared pipeline)...")
                _finbert_pipe = hf_pipeline(
                    "text-classification",
                    model="ProsusAI/finbert",
                    top_k=None,
                    device=-1,
                    truncation=True,
                    max_length=512,
                )
                logger.info("[HF] FinBERT ready.")
            except Exception as e:
                _finbert_failed = True
                logger.warning("[HF] FinBERT unavailable: %s", e)
        return _finbert_pipe


def get_distilroberta_pipeline() -> Optional[Any]:
    global _distil_pipe, _distil_failed
    if skip_distilroberta():
        return None
    if _distil_failed:
        return None
    with _lock:
        if _distil_pipe is None and not _distil_failed:
            try:
                from transformers import pipeline as hf_pipeline

                logger.info("[HF] Loading DistilRoBERTa financial (shared pipeline)...")
                _distil_pipe = hf_pipeline(
                    "text-classification",
                    model="mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis",
                    top_k=None,
                    device=-1,
                    truncation=True,
                    max_length=512,
                )
                logger.info("[HF] DistilRoBERTa ready.")
            except Exception as e:
                _distil_failed = True
                logger.warning("[HF] DistilRoBERTa unavailable: %s", e)
        return _distil_pipe


def finbert_score(text: str) -> float:
    pipe = get_finbert_pipeline()
    if pipe is None:
        return 0.0
    try:
        res = pipe(text[:1500])[0]
        lmap = {r["label"].lower(): r["score"] for r in res}
        return round(lmap.get("positive", 0) - lmap.get("negative", 0), 4)
    except Exception:
        return 0.0


def distilroberta_score(text: str) -> float:
    pipe = get_distilroberta_pipeline()
    if pipe is None:
        return 0.0
    try:
        res = pipe(text[:1500])[0]
        lmap = {r["label"].lower(): r["score"] for r in res}
        return round(lmap.get("positive", 0) - lmap.get("negative", 0), 4)
    except Exception:
        return 0.0
