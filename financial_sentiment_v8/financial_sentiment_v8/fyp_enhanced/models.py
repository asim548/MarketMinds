"""
FinancialPulse v3 — Database Models (Fixed)
"""

from __future__ import annotations
import json
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, text

db = SQLAlchemy()


def enable_wal_mode(engine):
    if "sqlite" in str(engine.url):
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            conn.execute(text("PRAGMA cache_size=-64000"))
            conn.execute(text("PRAGMA temp_store=MEMORY"))
            conn.commit()
        print("[DB] SQLite WAL mode enabled.")


class NewsArticle(db.Model):
    __tablename__ = "news_articles"

    id                   = db.Column(db.String(16), primary_key=True)
    title                = db.Column(db.Text, nullable=False)
    description          = db.Column(db.Text, default="")
    url                  = db.Column(db.Text, default="#")
    source               = db.Column(db.String(100), default="", index=True)
    category             = db.Column(db.String(50), default="")
    published_at         = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    fetched_at           = db.Column(db.DateTime, default=datetime.utcnow)

    # Sentiment
    sentiment_label      = db.Column(db.String(20), default="neutral", index=True)
    sentiment_score      = db.Column(db.Float, default=0.0)
    sentiment_confidence = db.Column(db.Float, default=0.0)
    vader_score          = db.Column(db.Float, default=0.0)
    textblob_score       = db.Column(db.Float, default=0.0)
    custom_score         = db.Column(db.Float, default=0.0)
    finbert_score        = db.Column(db.Float, nullable=True)   # Fixed
    llm_score            = db.Column(db.Float, default=0.0)
    llm_reasoning        = db.Column(db.Text, default="")
    llm_key_signal       = db.Column(db.Text, default="")       
    llm_adjudication     = db.Column(db.String(30), default="") 

    assets_json = db.Column(db.Text, default="[]")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "source": self.source,
            "category": self.category,
            "date": self.published_at.strftime("%H:%M  %b %d") if self.published_at else "",
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else "",
            "sentiment": {
                "label": self.sentiment_label,
                "score": self.sentiment_score,
                "confidence": self.sentiment_confidence,
                "vader": self.vader_score,
                "textblob": self.textblob_score,
                "custom": self.custom_score,
                "finbert": self.finbert_score,
                "llm_score": self.llm_score,
                "llm_reasoning": self.llm_reasoning,
                "llm_key_signal": self.llm_key_signal,
                "assets": json.loads(self.assets_json or "[]"),
                "color": ("green" if self.sentiment_label == "bullish" else "red" if self.sentiment_label == "bearish" else "gray"),
            },
            "timestamp": self.fetched_at.timestamp() if self.fetched_at else 0,
        }


# Keep all other models exactly as they were (PriceSnapshot, PriceHistory, etc.)
# ... (no changes needed in other classes)

class PriceSnapshot(db.Model):
    __tablename__ = "price_snapshots"
    __table_args__ = (db.Index("ix_price_asset_recorded", "asset_key", "recorded_at"),)

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol      = db.Column(db.String(20), nullable=False)
    asset_key   = db.Column(db.String(30), nullable=False)
    price       = db.Column(db.Float, nullable=False)
    change_pct  = db.Column(db.Float, default=0.0)
    volume      = db.Column(db.Float, default=0.0)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "asset_key": self.asset_key,
            "price": self.price,
            "change_pct": round(self.change_pct, 4),
            "volume": self.volume,
            "recorded_at": self.recorded_at.isoformat(),
        }


# ... (copy all remaining models from your original models.py: PriceHistory, SentimentAlert, AlertLog, SentimentTrend, BacktestResult, EvaluationSnapshot)
# They remain unchanged.


class PriceHistory(db.Model):
    """
    Incrementally stored daily OHLCV data.
    Replaces on-demand yfinance calls for the correlation and backtest engines.
    """
    __tablename__ = "price_history"
    __table_args__ = (
        db.UniqueConstraint("asset_key", "date", name="uq_asset_date"),
        db.Index("ix_price_history_asset_date", "asset_key", "date"),
    )

    id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_key = db.Column(db.String(30), nullable=False)
    symbol    = db.Column(db.String(20), nullable=False)
    date      = db.Column(db.String(10), nullable=False)   # "YYYY-MM-DD"
    open      = db.Column(db.Float)
    high      = db.Column(db.Float)
    low       = db.Column(db.Float)
    close     = db.Column(db.Float, nullable=False)
    volume    = db.Column(db.BigInteger, default=0)
    stored_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class SentimentAlert(db.Model):
    __tablename__ = "sentiment_alerts"

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_key   = db.Column(db.String(30), nullable=False, index=True)
    asset_label = db.Column(db.String(50), default="")
    direction   = db.Column(db.String(20), default="bullish")
    threshold   = db.Column(db.Float, default=0.6)
    channel     = db.Column(db.String(20), default="telegram")
    destination = db.Column(db.String(200), default="")
    active      = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    last_fired  = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "asset_key": self.asset_key,
            "asset_label": self.asset_label,
            "direction": self.direction,
            "threshold": self.threshold,
            "channel": self.channel,
            "destination": self.destination,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
            "last_fired": self.last_fired.isoformat() if self.last_fired else None,
        }


class AlertLog(db.Model):
    __tablename__ = "alert_logs"

    id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    alert_id  = db.Column(db.Integer, db.ForeignKey("sentiment_alerts.id"))
    asset_key = db.Column(db.String(30))
    message   = db.Column(db.Text)
    fired_at  = db.Column(db.DateTime, default=datetime.utcnow)
    success   = db.Column(db.Boolean, default=True)


class SentimentTrend(db.Model):
    __tablename__ = "sentiment_trends"
    __table_args__ = (
        db.UniqueConstraint("asset_key", "hour_bucket", name="uq_asset_hour"),
        db.Index("ix_trend_asset_hour", "asset_key", "hour_bucket"),
    )

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_key     = db.Column(db.String(30), nullable=False)
    hour_bucket   = db.Column(db.DateTime, nullable=False)
    bullish_count = db.Column(db.Integer, default=0)
    bearish_count = db.Column(db.Integer, default=0)
    neutral_count = db.Column(db.Integer, default=0)
    avg_score     = db.Column(db.Float, default=0.0)
    article_count = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            "asset_key": self.asset_key,
            "hour": self.hour_bucket.isoformat(),
            "bullish": self.bullish_count,
            "bearish": self.bearish_count,
            "neutral": self.neutral_count,
            "avg_score": self.avg_score,
            "total": self.article_count,
        }


class GeneratedSignal(db.Model):
    """Stores every generated signal for delayed (24h) backtesting."""
    __tablename__ = "generated_signals"
    __table_args__ = (
        db.Index("ix_signal_asset_created", "asset_key", "created_at"),
        db.Index("ix_signal_status_due", "status", "evaluation_due_at"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    signal_uid = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    evaluation_due_at = db.Column(db.DateTime, nullable=False)

    asset_key = db.Column(db.String(30), nullable=False, index=True)
    asset_name = db.Column(db.String(80), default="")
    predicted_direction = db.Column(db.String(16), nullable=False)  # bullish/bearish
    confidence_score = db.Column(db.Float, default=0.0)
    signal_score = db.Column(db.Float, default=0.0)

    entry_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="pending", index=True)  # pending/evaluated/expired/error
    metadata_json = db.Column(db.Text, default="{}")

    def to_dict(self):
        return {
            "id": self.id,
            "signal_uid": self.signal_uid,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "evaluation_due_at": self.evaluation_due_at.isoformat() if self.evaluation_due_at else None,
            "asset_key": self.asset_key,
            "asset_name": self.asset_name,
            "predicted_direction": self.predicted_direction,
            "confidence_score": self.confidence_score,
            "signal_score": self.signal_score,
            "entry_price": self.entry_price,
            "status": self.status,
        }


class SignalBacktest(db.Model):
    """Per-signal realized market outcome after the waiting window."""
    __tablename__ = "signal_backtests"
    __table_args__ = (
        db.UniqueConstraint("signal_id", name="uq_signal_backtest_signal_id"),
        db.Index("ix_signal_backtest_asset_eval", "asset_key", "evaluated_at"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    signal_id = db.Column(db.Integer, db.ForeignKey("generated_signals.id"), nullable=False, index=True)
    signal_uid = db.Column(db.String(64), nullable=False, index=True)

    asset_key = db.Column(db.String(30), nullable=False, index=True)
    evaluated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    actual_direction = db.Column(db.String(16), nullable=False)  # bullish/bearish/flat
    actual_price = db.Column(db.Float, nullable=False)
    movement_pct = db.Column(db.Float, default=0.0)
    correct = db.Column(db.Boolean, default=False)

    pnl_pct = db.Column(db.Float, default=0.0)
    pnl_value = db.Column(db.Float, default=0.0)
    drawdown_pct = db.Column(db.Float, default=0.0)

    def to_dict(self):
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "signal_uid": self.signal_uid,
            "asset_key": self.asset_key,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
            "actual_direction": self.actual_direction,
            "actual_price": self.actual_price,
            "movement_pct": self.movement_pct,
            "correct": self.correct,
            "pnl_pct": self.pnl_pct,
            "pnl_value": self.pnl_value,
            "drawdown_pct": self.drawdown_pct,
        }


class BacktestResult(db.Model):
    """Persisted backtest run metadata for history and caching."""
    __tablename__ = "backtest_results"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_key     = db.Column(db.String(30), nullable=False, index=True)
    lookback_days = db.Column(db.Integer, nullable=False)
    total_signals = db.Column(db.Integer, default=0)
    accuracy      = db.Column(db.Float, default=0.0)
    macro_f1      = db.Column(db.Float, default=0.0)
    result_json   = db.Column(db.Text, default="{}")   # full result dict
    run_at        = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "asset_key": self.asset_key,
            "lookback_days": self.lookback_days,
            "total_signals": self.total_signals,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "run_at": self.run_at.isoformat(),
        }


class EvaluationSnapshot(db.Model):
    """Stores periodic benchmark evaluation results for FYP tracking."""
    __tablename__ = "evaluation_snapshots"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    benchmark     = db.Column(db.String(100), default="")
    accuracy      = db.Column(db.Float, default=0.0)
    macro_f1      = db.Column(db.Float, default=0.0)
    weighted_f1   = db.Column(db.Float, default=0.0)
    result_json   = db.Column(db.Text, default="{}")
    run_at        = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "benchmark": self.benchmark,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "weighted_f1": self.weighted_f1,
            "run_at": self.run_at.isoformat(),
        }