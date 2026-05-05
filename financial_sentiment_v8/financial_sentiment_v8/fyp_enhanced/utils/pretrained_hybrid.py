from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "ml_models"
SENTIMENT_MODEL_PATH = MODEL_DIR / "news_sentiment_model.pkl"
TECHNICAL_MODEL_PATH = MODEL_DIR / "technical_analysis_model.pkl"
HYBRID_MODEL_PATH = MODEL_DIR / "hybrid_signal_model.pkl"
SCALER_PATH = MODEL_DIR / "hybrid_feature_scaler.pkl"

_LOADED = {
    "sentiment": None,
    "technical": None,
    "hybrid": None,
    "scaler": None,
    "ready": False,
}


def load_pretrained_models() -> dict:
    """Load pre-trained .pkl models from disk. No training is performed here."""
    MODEL_DIR.mkdir(exist_ok=True)
    _LOADED["ready"] = False

    try:
        if SENTIMENT_MODEL_PATH.exists():
            _LOADED["sentiment"] = joblib.load(SENTIMENT_MODEL_PATH)
        if TECHNICAL_MODEL_PATH.exists():
            _LOADED["technical"] = joblib.load(TECHNICAL_MODEL_PATH)
        if HYBRID_MODEL_PATH.exists():
            _LOADED["hybrid"] = joblib.load(HYBRID_MODEL_PATH)
        if SCALER_PATH.exists():
            _LOADED["scaler"] = joblib.load(SCALER_PATH)
    except Exception as exc:
        logger.warning("[HybridModels] Failed loading .pkl files: %s", exc)

    _LOADED["ready"] = all(
        [
            _LOADED["sentiment"] is not None,
            _LOADED["technical"] is not None,
            _LOADED["hybrid"] is not None,
            _LOADED["scaler"] is not None,
        ]
    )

    return {
        "ready": _LOADED["ready"],
        "has_sentiment_model": _LOADED["sentiment"] is not None,
        "has_technical_model": _LOADED["technical"] is not None,
        "has_hybrid_model": _LOADED["hybrid"] is not None,
        "has_scaler": _LOADED["scaler"] is not None,
    }


def build_live_features(asset_key: str, news: list[dict], prices: dict) -> np.ndarray:
    """Builds the same 10-feature vector expected by the offline trainer."""
    asset_news = []
    for item in news:
        assets = item.get("sentiment", {}).get("assets", [])
        if any(a.get("key") == asset_key for a in assets):
            asset_news.append(item)

    if asset_news:
        scores = [float(n.get("sentiment", {}).get("score", 0.0)) for n in asset_news]
        confs = [float(n.get("sentiment", {}).get("confidence", 0.0)) for n in asset_news]
        vader = [float(n.get("sentiment", {}).get("vader", 0.0)) for n in asset_news]
        textblob = [float(n.get("sentiment", {}).get("textblob", 0.0)) for n in asset_news]
    else:
        scores, confs, vader, textblob = [0.0], [0.0], [0.0], [0.0]

    p = prices.get(asset_key, {})
    change_pct = float(p.get("change_pct", 0.0))
    price = float(p.get("price", 0.0))

    feats = np.array(
        [
            float(np.mean(scores)),
            float(np.std(scores)),
            float(np.mean(confs)),
            float(np.mean(vader)),
            float(np.mean(textblob)),
            float(len(asset_news)),
            change_pct,
            abs(change_pct),
            np.sign(change_pct),
            np.log1p(max(price, 0.0)),
        ],
        dtype=np.float32,
    )
    return feats


def predict_signal(asset_key: str, news: list[dict], prices: dict) -> dict | None:
    """Returns model-driven signal if .pkl files are available, else None."""
    if not _LOADED.get("ready"):
        return None

    feats = build_live_features(asset_key, news, prices)
    x = _LOADED["scaler"].transform(feats.reshape(1, -1))

    sent_probs = _LOADED["sentiment"].predict_proba(x)[0]
    tech_probs = _LOADED["technical"].predict_proba(x)[0]

    stacked = np.hstack([x, sent_probs.reshape(1, -1), tech_probs.reshape(1, -1)])
    hybrid_probs = _LOADED["hybrid"].predict_proba(stacked)[0]
    classes = list(_LOADED["hybrid"].classes_)

    class_map = {int(c): float(p) for c, p in zip(classes, hybrid_probs)}
    bearish = class_map.get(0, 0.0)
    neutral = class_map.get(1, 0.0)
    bullish = class_map.get(2, 0.0)

    direction = "bullish" if bullish >= bearish else "bearish"
    confidence = max(bullish, bearish)
    score = bullish - bearish

    return {
        "direction": direction,
        "confidence": round(confidence, 4),
        "signal_score": round(score, 4),
        "probabilities": {
            "bearish": round(bearish, 4),
            "neutral": round(neutral, 4),
            "bullish": round(bullish, 4),
        },
        "model": "hybrid_pretrained",
    }
