"""
FinancialPulse v7 — CSV Dataset Trainer
═══════════════════════════════════════════════════════════════════════════════
Trains the hybrid ML model using the user's historical news sentiment CSV.

Expected CSV columns (from data.csv):
  row_id, date, split, is_real_headline, source, headline, full_text,
  word_count, asset_tags, primary_sentiment,
  sentiment_crypto, sentiment_forex, sentiment_dxy, sentiment_gold,
  sentiment_commodity, sentiment_oil, sentiment_stock,
  label_primary, label_crypto, label_forex, label_dxy, label_gold,
  label_commodity, label_oil, label_stock

Labels: 0=bearish, 1=neutral, 2=bullish
Sentiments: bearish, neutral, bullish
"""

from __future__ import annotations

import os
import json
import logging
import threading
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent / "ml_models"
MODEL_DIR.mkdir(exist_ok=True)

DATASET_MODEL_PATH  = MODEL_DIR / "dataset_hybrid_model.joblib"
DATASET_SCALER_PATH = MODEL_DIR / "dataset_scaler.joblib"
DATASET_META_PATH   = MODEL_DIR / "dataset_meta.json"

# ── Asset columns in CSV ──────────────────────────────────────────────────────
ASSET_LABEL_COLS = {
    "crypto":     "label_crypto",
    "forex":      "label_forex",
    "dxy":        "label_dxy",
    "gold":       "label_gold",
    "commodity":  "label_commodity",
    "oil":        "label_oil",
    "stock":      "label_stock",
}

ASSET_SENT_COLS = {
    "crypto":    "sentiment_crypto",
    "forex":     "sentiment_forex",
    "dxy":       "sentiment_dxy",
    "gold":      "sentiment_gold",
    "commodity": "sentiment_commodity",
    "oil":       "sentiment_oil",
    "stock":     "sentiment_stock",
}

LABEL_MAP = {0: "bearish", 1: "neutral", 2: "bullish"}
SENT_MAP  = {"bearish": 0, "neutral": 1, "bullish": 2}


# ── Training state (singleton) ───────────────────────────────────────────────

_training_lock  = threading.Lock()
_training_state = {
    "status": "idle",      # idle | running | done | error
    "progress": 0,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "result": None,
}


def get_training_state() -> dict:
    return dict(_training_state)


def _set_state(status, progress, message, result=None):
    _training_state.update(
        status=status, progress=progress, message=message,
        result=result,
        finished_at=datetime.utcnow().isoformat() if status in ("done", "error") else None,
    )


# ── Feature engineering from CSV row ─────────────────────────────────────────

def _sent_to_score(s: str) -> float:
    """Convert sentiment string to numeric score."""
    s = str(s).strip().lower()
    return {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}.get(s, 0.0)


def _urgency_flags(text: str) -> dict:
    text = text.lower()
    return {
        "is_urgent":   float(any(w in text for w in {"breaking", "alert", "urgent", "flash", "just in"})),
        "is_fed":      float(any(w in text for w in {"powell", "fomc", "federal reserve", "rate hike", "rate cut"})),
        "is_geopolit": float(any(w in text for w in {"war", "sanctions", "invasion", "military", "nato", "nuclear"})),
        "is_trump":    float(any(w in text for w in {"trump", "tariff", "white house", "executive order"})),
        "has_pct":     float("%" in text),
    }


def _vader_approx(text: str) -> float:
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        sia = SentimentIntensityAnalyzer()
        return sia.polarity_scores(text)["compound"]
    except Exception:
        return 0.0


def build_csv_features(row: pd.Series) -> np.ndarray:
    """
    Build a feature vector from a CSV row.
    Returns a 1D float32 numpy array of length 32.
    """
    headline  = str(row.get("headline", ""))
    full_text = str(row.get("full_text", ""))
    combined  = f"{headline} {full_text}"

    # Primary sentiment score
    prim_sent = _sent_to_score(row.get("primary_sentiment", "neutral"))

    # Per-asset sentiment scores
    asset_scores = [_sent_to_score(row.get(col, "neutral")) for col in ASSET_SENT_COLS.values()]

    # Mean, std, range across assets
    sc = np.array(asset_scores)
    sc_mean  = float(np.mean(sc))
    sc_std   = float(np.std(sc))
    sc_range = float(np.max(sc) - np.min(sc))

    # Text features
    wc          = min(1.0, int(row.get("word_count", 20)) / 200)
    headline_len= min(1.0, len(headline) / 120)
    excl        = min(1.0, headline.count("!") / 3)
    has_q       = float("?" in headline)
    upper_ratio = sum(1 for c in headline if c.isupper()) / max(1, len(headline))
    emoji_flag  = float(any(ord(c) > 127 for c in headline))

    # Urgency flags
    flags = _urgency_flags(combined)

    # VADER approximation on headline
    vader_h = _vader_approx(headline[:300])
    vader_f = _vader_approx(full_text[:300])

    # Source encoding (simple hash bucket 0-1)
    source_hash = hash(str(row.get("source", ""))) % 50 / 50.0

    # Date features
    try:
        dt = pd.to_datetime(row.get("date", "2019-01-01"))
        dow     = dt.dayofweek / 6.0
        month   = (dt.month - 1) / 11.0
        dow_sin = np.sin(2 * np.pi * dt.dayofweek / 7)
        dow_cos = np.cos(2 * np.pi * dt.dayofweek / 7)
    except Exception:
        dow = month = dow_sin = dow_cos = 0.0

    # Interaction features
    fx_sent_vader = prim_sent * vader_h
    fx_urgent_sent = flags["is_urgent"] * prim_sent

    feat = np.array([
        prim_sent,                # f00
        sc_mean,                  # f01
        sc_std,                   # f02
        sc_range,                 # f03
        asset_scores[0],          # f04 crypto
        asset_scores[1],          # f05 forex
        asset_scores[2],          # f06 dxy
        asset_scores[3],          # f07 gold
        asset_scores[4],          # f08 commodity
        asset_scores[5],          # f09 oil
        asset_scores[6],          # f10 stock
        wc,                       # f11
        headline_len,             # f12
        excl,                     # f13
        has_q,                    # f14
        upper_ratio,              # f15
        emoji_flag,               # f16
        flags["is_urgent"],       # f17
        flags["is_fed"],          # f18
        flags["is_geopolit"],     # f19
        flags["is_trump"],        # f20
        flags["has_pct"],         # f21
        vader_h,                  # f22
        vader_f,                  # f23
        source_hash,              # f24
        dow,                      # f25
        month,                    # f26
        dow_sin,                  # f27
        dow_cos,                  # f28
        fx_sent_vader,            # f29
        fx_urgent_sent,           # f30
        float(row.get("is_real_headline", 1)),  # f31
    ], dtype=np.float32)
    return feat


# ── Main training pipeline ────────────────────────────────────────────────────

def train_from_csv(csv_path: str, target_asset: str = "primary") -> dict:
    """
    Train hybrid GBM model from CSV dataset.
    target_asset: 'primary' or one of crypto/forex/dxy/gold/commodity/oil/stock
    Returns result dict with metrics.
    """
    _set_state("running", 2, f"Loading CSV from {csv_path}...")

    try:
        df = pd.read_csv(csv_path, low_memory=False)
        logger.info(f"[DatasetTrainer] Loaded {len(df)} rows")
        _set_state("running", 10, f"Loaded {len(df):,} rows. Engineering features...")
    except Exception as e:
        _set_state("error", 0, f"Cannot read CSV: {e}")
        return {"error": str(e)}

    # ── Select label column ───────────────────────────────────────────────────
    if target_asset == "primary":
        label_col = "label_primary"
    else:
        label_col = ASSET_LABEL_COLS.get(target_asset, "label_primary")

    if label_col not in df.columns:
        msg = f"Label column '{label_col}' not found in CSV"
        _set_state("error", 0, msg)
        return {"error": msg}

    # Drop rows with missing labels
    df = df.dropna(subset=[label_col])
    df[label_col] = df[label_col].astype(int)

    # Only keep valid labels 0,1,2
    df = df[df[label_col].isin([0, 1, 2])].reset_index(drop=True)
    logger.info(f"[DatasetTrainer] {len(df)} rows with valid labels")

    if len(df) < 100:
        msg = f"Too few valid rows ({len(df)}) — need at least 100"
        _set_state("error", 0, msg)
        return {"error": msg}

    # ── Feature engineering ───────────────────────────────────────────────────
    _set_state("running", 15, "Engineering features (this may take a minute)...")
    features = []
    labels   = []

    for idx, row in df.iterrows():
        try:
            feat  = build_csv_features(row)
            label = int(row[label_col])
            features.append(feat)
            labels.append(label)
        except Exception as e:
            logger.debug(f"[DatasetTrainer] row {idx} skip: {e}")
            continue

        if idx % 5000 == 0 and idx > 0:
            pct = 15 + int(30 * idx / len(df))
            _set_state("running", pct, f"Features: {idx:,}/{len(df):,} rows...")

    X = np.array(features, dtype=np.float32)
    y = np.array(labels, dtype=np.int32)

    _set_state("running", 48, f"Features done: {X.shape}. Splitting train/test...")

    # ── Train / Test split (use 'split' column if present, else 80/20) ────────
    if "split" in df.columns:
        split_vals = df.iloc[[i for i in range(len(features))]]["split"].values
        tr_idx = np.where(split_vals == "train")[0]
        te_idx = np.where(split_vals != "train")[0]
        if len(tr_idx) < 50 or len(te_idx) < 10:
            # Fallback to 80/20
            tr_idx = np.arange(int(len(X) * 0.8))
            te_idx = np.arange(int(len(X) * 0.8), len(X))
    else:
        tr_idx = np.arange(int(len(X) * 0.8))
        te_idx = np.arange(int(len(X) * 0.8), len(X))

    X_train, X_test = X[tr_idx], X[te_idx]
    y_train, y_test = y[tr_idx], y[te_idx]

    logger.info(f"[DatasetTrainer] Train: {len(X_train)}, Test: {len(X_test)}")
    _set_state("running", 52, f"Training on {len(X_train):,} samples...")

    # ── Model training ────────────────────────────────────────────────────────
    try:
        from sklearn.preprocessing import StandardScaler
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, VotingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.metrics import (accuracy_score, precision_score,
                                     recall_score, f1_score, confusion_matrix,
                                     classification_report)

        scaler  = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s  = scaler.transform(X_test)

        _set_state("running", 58, "Training GBM (gradient boosting)...")
        gbm = GradientBoostingClassifier(
            n_estimators=300, max_depth=5,
            learning_rate=0.05, subsample=0.8,
            min_samples_split=4, random_state=42,
        )
        gbm.fit(X_train_s, y_train)

        _set_state("running", 72, "Training RandomForest ensemble...")
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=8,
            min_samples_split=5, random_state=42, n_jobs=-1,
        )
        rf.fit(X_train_s, y_train)

        _set_state("running", 82, "Training meta-learner (VotingClassifier)...")
        lr = LogisticRegression(max_iter=1000, C=1.0, random_state=42)
        hybrid = VotingClassifier(
            estimators=[("gbm", gbm), ("rf", rf), ("lr", lr)],
            voting="soft",
        )
        hybrid.fit(X_train_s, y_train)

        _set_state("running", 90, "Evaluating model on test set...")

        y_pred = hybrid.predict(X_test_s)
        acc    = float(accuracy_score(y_test, y_pred))
        prec   = float(precision_score(y_test, y_pred, average="macro", zero_division=0))
        rec    = float(recall_score(y_test, y_pred, average="macro", zero_division=0))
        f1     = float(f1_score(y_test, y_pred, average="macro", zero_division=0))
        wf1    = float(f1_score(y_test, y_pred, average="weighted", zero_division=0))
        cm     = confusion_matrix(y_test, y_pred, labels=[0, 1, 2]).tolist()

        # Per-class accuracy
        per_class = {}
        for cls_id, cls_name in LABEL_MAP.items():
            mask = y_test == cls_id
            if mask.sum() > 0:
                per_class[cls_name] = float(np.mean(y_pred[mask] == cls_id))

        # Confusion matrix labels
        cm_labels = ["bearish", "neutral", "bullish"]

        # Feature importances (from GBM)
        feat_names = [
            "primary_sent", "asset_mean", "asset_std", "asset_range",
            "crypto", "forex", "dxy", "gold", "commodity", "oil", "stock",
            "word_count", "headline_len", "exclamation", "question",
            "upper_ratio", "emoji", "is_urgent", "is_fed", "is_geopolit",
            "is_trump", "has_pct", "vader_headline", "vader_fulltext",
            "source_hash", "dow", "month", "dow_sin", "dow_cos",
            "sent_x_vader", "urgent_x_sent", "is_real",
        ]
        importances = dict(zip(feat_names, gbm.feature_importances_.tolist()))

        # Save model artifacts
        joblib.dump(hybrid, DATASET_MODEL_PATH)
        joblib.dump(scaler, DATASET_SCALER_PATH)

        meta = {
            "trained_at":    datetime.utcnow().isoformat(),
            "csv_rows":      len(df),
            "train_samples": len(X_train),
            "test_samples":  len(X_test),
            "target_asset":  target_asset,
            "label_col":     label_col,
            "accuracy":      round(acc, 4),
            "precision":     round(prec, 4),
            "recall":        round(rec, 4),
            "macro_f1":      round(f1, 4),
            "weighted_f1":   round(wf1, 4),
            "confusion_matrix": cm,
            "cm_labels":     cm_labels,
            "per_class_acc": per_class,
            "feature_importances": importances,
            "label_distribution": {
                "bearish": int((y == 0).sum()),
                "neutral": int((y == 1).sum()),
                "bullish": int((y == 2).sum()),
            },
        }
        with open(DATASET_META_PATH, "w") as f:
            json.dump(meta, f, indent=2)

        _set_state("done", 100, "Training complete ✓", result=meta)
        logger.info(f"[DatasetTrainer] Done. Accuracy={acc:.3f} F1={f1:.3f}")
        return meta

    except Exception as e:
        logger.exception("[DatasetTrainer] Training failed")
        _set_state("error", 0, f"Training error: {e}")
        return {"error": str(e)}


def get_dataset_model_meta() -> Optional[dict]:
    """Load saved training metadata."""
    if DATASET_META_PATH.exists():
        try:
            with open(DATASET_META_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def predict_with_dataset_model(features: np.ndarray) -> dict:
    """
    Run inference with the dataset-trained hybrid model.
    Returns {"bearish": p, "neutral": p, "bullish": p, "label": str, "confidence": float}
    """
    if not DATASET_MODEL_PATH.exists() or not DATASET_SCALER_PATH.exists():
        return {"error": "Dataset model not trained yet"}
    try:
        model  = joblib.load(DATASET_MODEL_PATH)
        scaler = joblib.load(DATASET_SCALER_PATH)
        Xs     = scaler.transform(features.reshape(1, -1))
        proba  = model.predict_proba(Xs)[0]
        # Classes order: [0=bearish, 1=neutral, 2=bullish]
        classes = model.classes_
        prob_map = {LABEL_MAP[int(c)]: float(p) for c, p in zip(classes, proba)}
        label = max(prob_map, key=prob_map.get)
        conf  = prob_map[label]
        return {**prob_map, "label": label, "confidence": round(conf, 4)}
    except Exception as e:
        return {"error": str(e)}


# ── Async training wrapper ────────────────────────────────────────────────────

def start_training_async(csv_path: str, target_asset: str = "primary"):
    """Kick off training in a background thread."""
    if _training_state["status"] == "running":
        return {"error": "Training already in progress"}
    _training_state.update(status="running", progress=0,
                           message="Starting...", started_at=datetime.utcnow().isoformat(),
                           finished_at=None, result=None)
    t = threading.Thread(target=train_from_csv, args=(csv_path, target_asset), daemon=True)
    t.start()
    return {"started": True}
