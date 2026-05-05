from __future__ import annotations

"""
Offline training script for hybrid models.

This script is intentionally separate from app.py.
Run it manually on your machine before starting the app:
    python train_models.py --csv data/data.csv --target label_primary
"""

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

MODEL_DIR = Path(__file__).resolve().parent / "ml_models"
MODEL_DIR.mkdir(exist_ok=True)

SENTIMENT_MODEL_PATH = MODEL_DIR / "news_sentiment_model.pkl"
TECHNICAL_MODEL_PATH = MODEL_DIR / "technical_analysis_model.pkl"
HYBRID_MODEL_PATH = MODEL_DIR / "hybrid_signal_model.pkl"
SCALER_PATH = MODEL_DIR / "hybrid_feature_scaler.pkl"

SENTIMENT_MAP = {"bearish": -1.0, "neutral": 0.0, "bullish": 1.0}


def _s(v) -> float:
    return SENTIMENT_MAP.get(str(v).strip().lower(), 0.0)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["primary_sent"] = df.get("primary_sentiment", "neutral").map(_s).fillna(0.0)
    out["sent_crypto"] = df.get("sentiment_crypto", "neutral").map(_s).fillna(0.0)
    out["sent_forex"] = df.get("sentiment_forex", "neutral").map(_s).fillna(0.0)
    out["sent_dxy"] = df.get("sentiment_dxy", "neutral").map(_s).fillna(0.0)
    out["sent_gold"] = df.get("sentiment_gold", "neutral").map(_s).fillna(0.0)
    out["sent_commodity"] = df.get("sentiment_commodity", "neutral").map(_s).fillna(0.0)
    out["sent_oil"] = df.get("sentiment_oil", "neutral").map(_s).fillna(0.0)
    out["sent_stock"] = df.get("sentiment_stock", "neutral").map(_s).fillna(0.0)
    out["word_count"] = pd.to_numeric(df.get("word_count", 0), errors="coerce").fillna(0.0).clip(0, 5000)
    out["headline_len"] = df.get("headline", "").astype(str).str.len().clip(0, 400)

    out["sent_mean"] = out[[
        "sent_crypto",
        "sent_forex",
        "sent_dxy",
        "sent_gold",
        "sent_commodity",
        "sent_oil",
        "sent_stock",
    ]].mean(axis=1)
    out["sent_std"] = out[[
        "sent_crypto",
        "sent_forex",
        "sent_dxy",
        "sent_gold",
        "sent_commodity",
        "sent_oil",
        "sent_stock",
    ]].std(axis=1).fillna(0.0)

    if "date" in df.columns:
        dt = pd.to_datetime(df["date"], errors="coerce")
        out["dow"] = dt.dt.dayofweek.fillna(0.0)
    else:
        out["dow"] = 0.0

    base = out[[
        "primary_sent",
        "sent_mean",
        "sent_std",
        "word_count",
        "headline_len",
        "dow",
        "sent_crypto",
        "sent_forex",
        "sent_gold",
        "sent_stock",
    ]].astype(float)
    return base


def train(csv_path: Path, target_col: str):
    df = pd.read_csv(csv_path, low_memory=False)
    if target_col not in df.columns:
        raise ValueError(f"Target column not found: {target_col}")

    df = df.dropna(subset=[target_col]).copy()
    y = pd.to_numeric(df[target_col], errors="coerce").fillna(1).astype(int)
    y = y.clip(0, 2)

    X = build_features(df)

    x_train, x_test, y_train, y_test = train_test_split(
        X.values, y.values, test_size=0.2, random_state=42, stratify=y.values
    )

    scaler = StandardScaler()
    x_train_s = scaler.fit_transform(x_train)
    x_test_s = scaler.transform(x_test)

    sentiment_model = LogisticRegression(max_iter=1000, multi_class="auto")
    sentiment_model.fit(x_train_s, y_train)

    technical_model = RandomForestClassifier(
        n_estimators=300,
        max_depth=10,
        min_samples_split=5,
        random_state=42,
        n_jobs=-1,
    )
    technical_model.fit(x_train_s, y_train)

    sent_train_p = sentiment_model.predict_proba(x_train_s)
    tech_train_p = technical_model.predict_proba(x_train_s)
    hybrid_train_x = np.hstack([x_train_s, sent_train_p, tech_train_p])

    hybrid_model = LogisticRegression(max_iter=1200, multi_class="auto")
    hybrid_model.fit(hybrid_train_x, y_train)

    sent_test_p = sentiment_model.predict_proba(x_test_s)
    tech_test_p = technical_model.predict_proba(x_test_s)
    hybrid_test_x = np.hstack([x_test_s, sent_test_p, tech_test_p])
    pred = hybrid_model.predict(hybrid_test_x)

    acc = accuracy_score(y_test, pred)
    f1 = f1_score(y_test, pred, average="macro")

    joblib.dump(sentiment_model, SENTIMENT_MODEL_PATH)
    joblib.dump(technical_model, TECHNICAL_MODEL_PATH)
    joblib.dump(hybrid_model, HYBRID_MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)

    print("Training complete")
    print(f"Rows: {len(df):,}")
    print(f"Accuracy: {acc:.4f}")
    print(f"Macro F1: {f1:.4f}")
    print(f"Saved: {SENTIMENT_MODEL_PATH.name}, {TECHNICAL_MODEL_PATH.name}, {HYBRID_MODEL_PATH.name}, {SCALER_PATH.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, default="data/data.csv")
    parser.add_argument("--target", type=str, default="label_primary")
    args = parser.parse_args()

    train(Path(args.csv), args.target)
