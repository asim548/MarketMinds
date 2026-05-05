from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta

from ..models import GeneratedSignal, SignalBacktest
from .price_fetcher import fetch_prices

logger = logging.getLogger(__name__)


def _build_signal_uid(asset_key: str, direction: str, created_at: datetime) -> str:
    raw = f"{asset_key}|{direction}|{created_at.strftime('%Y-%m-%d-%H')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _price_direction(movement_pct: float, flat_band_pct: float = 0.05) -> str:
    if movement_pct > flat_band_pct:
        return "bullish"
    if movement_pct < -flat_band_pct:
        return "bearish"
    return "flat"


def persist_generated_signals(db_session, news: list[dict], prices: dict, wait_hours: int = 24) -> dict:
    """Generate and store one signal per asset snapshot run."""
    from .sentiment_engine import ASSET_KEYWORDS
    from .ai_signals import generate_ai_recommendation
    from .pretrained_hybrid import predict_signal

    created_at = datetime.utcnow()
    created = 0
    skipped = 0

    for asset_key, info in ASSET_KEYWORDS.items():
        model_pred = None
        try:
            model_pred = predict_signal(asset_key, news, prices)
        except Exception as exc:
            logger.debug("[AutoBacktest] Pretrained predict failed for %s: %s", asset_key, exc)

        rec = {}
        if model_pred is None:
            try:
                rec = generate_ai_recommendation(asset_key, news, prices, db_session)
            except Exception as exc:
                logger.warning("[AutoBacktest] Recommendation failed for %s: %s", asset_key, exc)
                continue

            signal = str(rec.get("signal", "HOLD")).upper()
            if signal not in {"BUY", "SELL"}:
                skipped += 1
                continue
            direction = "bullish" if signal == "BUY" else "bearish"
        else:
            direction = model_pred["direction"]

        entry_price = float((rec.get("current_price") if rec else 0.0) or prices.get(asset_key, {}).get("price") or 0.0)
        if entry_price <= 0:
            skipped += 1
            continue

        uid = _build_signal_uid(asset_key, direction, created_at)
        if db_session.query(GeneratedSignal).filter_by(signal_uid=uid).first():
            skipped += 1
            continue

        row = GeneratedSignal(
            signal_uid=uid,
            created_at=created_at,
            evaluation_due_at=created_at + timedelta(hours=wait_hours),
            asset_key=asset_key,
            asset_name=info.get("label", asset_key),
            predicted_direction=direction,
            confidence_score=float((model_pred or {}).get("confidence", rec.get("confidence", 0.0))),
            signal_score=float((model_pred or {}).get("signal_score", rec.get("composite_score", 0.0))),
            entry_price=entry_price,
            status="pending",
            metadata_json=json.dumps(
                {
                    "signal": rec.get("signal"),
                    "action": rec.get("action"),
                    "supporting_articles": rec.get("supporting_articles", []),
                    "win_probability": rec.get("win_probability", 0.0),
                    "model_prediction": model_pred,
                }
            ),
        )
        db_session.add(row)
        created += 1

    try:
        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        logger.error("[AutoBacktest] Failed to persist signals: %s", exc)
        return {"created": 0, "skipped": skipped, "error": str(exc)}

    return {"created": created, "skipped": skipped}


def evaluate_pending_signals(db_session, wait_hours: int = 24, notional_per_signal: float = 1000.0) -> dict:
    """Evaluate pending signals once their 24h window is reached."""
    now = datetime.utcnow()
    due = (
        db_session.query(GeneratedSignal)
        .filter(GeneratedSignal.status == "pending", GeneratedSignal.evaluation_due_at <= now)
        .order_by(GeneratedSignal.created_at.asc())
        .all()
    )

    if not due:
        return {"evaluated": 0, "pending": 0}

    prices    = fetch_prices(force=True)
    evaluated = 0

    for sig in due:
        # ── FIX: skip if a backtest record already exists for this signal ──
        existing = db_session.query(SignalBacktest).filter_by(signal_id=sig.id).first()
        if existing:
            sig.status = "evaluated"   # keep state consistent
            continue

        latest_price = float(prices.get(sig.asset_key, {}).get("price") or 0.0)
        if latest_price <= 0 or sig.entry_price <= 0:
            sig.status = "error"
            continue

        movement_pct     = ((latest_price - sig.entry_price) / sig.entry_price) * 100.0
        actual_direction = _price_direction(movement_pct)
        correct          = sig.predicted_direction == actual_direction

        pnl_pct   = movement_pct if sig.predicted_direction == "bullish" else -movement_pct
        pnl_value = notional_per_signal * (pnl_pct / 100.0)

        bt = SignalBacktest(
            signal_id=sig.id,
            signal_uid=sig.signal_uid,
            asset_key=sig.asset_key,
            evaluated_at=now,
            actual_direction=actual_direction,
            actual_price=latest_price,
            movement_pct=movement_pct,
            correct=correct,
            pnl_pct=pnl_pct,
            pnl_value=pnl_value,
            drawdown_pct=0.0,
        )
        db_session.add(bt)
        sig.status = "evaluated"
        evaluated += 1

    try:
        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        logger.error("[AutoBacktest] Failed to evaluate pending signals: %s", exc)
        return {"evaluated": 0, "pending": len(due), "error": str(exc)}

    _recompute_drawdowns(db_session)
    return {"evaluated": evaluated, "pending": len(due) - evaluated}


def _recompute_drawdowns(db_session):
    rows = db_session.query(SignalBacktest).order_by(SignalBacktest.evaluated_at.asc(), SignalBacktest.id.asc()).all()
    if not rows:
        return

    equity = 0.0
    peak   = 0.0
    for row in rows:
        equity += float(row.pnl_value or 0.0)
        peak    = max(peak, equity)
        dd      = 0.0 if peak <= 0 else ((equity - peak) / peak) * 100.0
        row.drawdown_pct = dd

    try:
        db_session.commit()
    except Exception:
        db_session.rollback()


def get_backtesting_summary(db_session, initial_capital: float = 10000.0, asset_key: str | None = None) -> dict:
    q = db_session.query(SignalBacktest).order_by(SignalBacktest.evaluated_at.asc(), SignalBacktest.id.asc())
    if asset_key:
        q = q.filter(SignalBacktest.asset_key == asset_key)
    rows = q.all()

    total_generated_q = db_session.query(GeneratedSignal)
    if asset_key:
        total_generated_q = total_generated_q.filter(GeneratedSignal.asset_key == asset_key)
    total_generated = total_generated_q.count()

    equity       = initial_capital
    peak         = initial_capital
    wins         = 0
    losses       = 0
    total_profit = 0.0
    total_loss   = 0.0

    equity_curve   = [{"t": "start", "equity": round(equity, 2)}]
    pnl_curve      = []
    drawdown_curve = []

    for row in rows:
        pnl    = float(row.pnl_value or 0.0)
        equity += pnl
        peak   = max(peak, equity)
        dd     = 0.0 if peak <= 0 else ((equity - peak) / peak) * 100.0

        if pnl >= 0:
            wins         += 1
            total_profit += pnl
        else:
            losses    += 1
            total_loss += abs(pnl)

        t = row.evaluated_at.isoformat()
        pnl_curve.append({"t": t, "pnl": round(pnl, 2), "pnl_pct": round(float(row.pnl_pct or 0.0), 4)})
        equity_curve.append({"t": t, "equity": round(equity, 2)})
        drawdown_curve.append({"t": t, "drawdown_pct": round(dd, 4)})

    evaluated        = len(rows)
    accuracy         = (sum(1 for r in rows if r.correct) / evaluated) if evaluated else 0.0
    win_ratio        = (wins / evaluated) if evaluated else 0.0
    profit_factor    = (total_profit / total_loss) if total_loss > 0 else (total_profit if total_profit > 0 else 0.0)
    max_drawdown     = min((p["drawdown_pct"] for p in drawdown_curve), default=0.0)
    total_return_pct = ((equity - initial_capital) / initial_capital * 100.0) if initial_capital > 0 else 0.0

    return {
        "asset_key":               asset_key or "all",
        "total_signals_generated": total_generated,
        "total_signals_evaluated": evaluated,
        "pending_signals":         max(total_generated - evaluated, 0),
        "accuracy":                round(accuracy, 4),
        "win_ratio":               round(win_ratio, 4),
        "wins":                    wins,
        "losses":                  losses,
        "total_pnl":               round(equity - initial_capital, 2),
        "total_return_pct":        round(total_return_pct, 4),
        "profitability":           round((total_profit - total_loss), 2),
        "profit_factor":           round(profit_factor, 4),
        "max_drawdown_pct":        round(max_drawdown, 4),
        "equity_curve":            equity_curve,
        "pnl_curve":               pnl_curve,
        "drawdown_curve":          drawdown_curve,
    }