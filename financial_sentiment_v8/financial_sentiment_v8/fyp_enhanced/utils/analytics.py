"""
FinancialPulse v3 — Analytics Engine
═════════════════════════════════════════════════════════════════════════════
Improvements over v2:
  ✓ Multi-component Fear & Greed index (5 components, weighted)
      • Sentiment score (30%) — news sentiment average
      • VIX level (25%)      — volatility implies fear
      • Price momentum (20%) — % above/below 20-day MA
      • Bond-stock spread (15%) — safe haven demand proxy
      • Article volume (10%) — high volume = uncertainty
  ✓ Sentiment momentum with spike classification
  ✓ Correlation engine unchanged (Pearson, correct)
  ✓ Trend update logic improved
"""

from __future__ import annotations

import logging
import numpy as np
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  FEAR & GREED INDEX — 5-component model
# ══════════════════════════════════════════════════════════════════════════════

def compute_fear_greed_index(news_items: list[dict],
                             price_cache: dict | None = None) -> dict:
    """
    Compute a weighted Fear & Greed index (0 = Extreme Fear, 100 = Extreme Greed).

    Components
    ----------
    1. Sentiment score      30% — avg news sentiment mapped to [0, 100]
    2. VIX level            25% — inverted VIX (high VIX = fear)
    3. Price momentum       20% — S&P 500 vs 20-day MA direction
    4. Bond-stock spread    15% — bearish bond articles vs bullish stock articles
    5. Article volume ratio 10% — ratio of bullish to total articles

    Falls back to sentiment-only if price_cache is None.
    """
    if not news_items:
        return {"index": 50, "label": "Neutral", "color": "#94a3b8",
                "components": {}, "avg_score": 0.0, "volatility": 0.0}

    scores_arr = np.array([item["sentiment"]["score"] for item in news_items])
    avg_score = float(np.mean(scores_arr))
    std_score = float(np.std(scores_arr))

    components = {}

    # ── Component 1: Sentiment average ────────────────────────────────
    # Map [-1, 1] → [0, 100]
    c1 = (avg_score + 1) / 2 * 100
    # Penalize for high news volatility (high std = mixed signals = fear)
    c1 = max(0.0, c1 - std_score * 15)
    components["sentiment"] = {
        "value": round(c1, 1),
        "weight": 0.30,
        "label": "News Sentiment",
        "raw": round(avg_score, 4),
    }

    # ── Component 2: VIX level ─────────────────────────────────────────
    c2 = 50.0  # neutral default
    if price_cache:
        vix_data = price_cache.get("vix", {})
        vix_price = vix_data.get("price", 0)
        if vix_price > 0:
            # VIX > 30 = extreme fear (0), VIX < 12 = extreme greed (100)
            # Linear interpolation: 12 → 100, 30 → 0, clamp
            c2 = max(0.0, min(100.0, (30 - vix_price) / (30 - 12) * 100))
    components["vix"] = {
        "value": round(c2, 1),
        "weight": 0.25,
        "label": "Market Volatility (VIX)",
        "raw": round(price_cache.get("vix", {}).get("price", 0), 2) if price_cache else 0,
    }

    # ── Component 3: Price momentum ────────────────────────────────────
    c3 = 50.0  # neutral default
    if price_cache:
        sp500 = price_cache.get("sp500", {})
        chg = sp500.get("change_pct", 0)
        if chg is not None:
            # chg > +1% = greed, chg < -1% = fear, linear between -3% and +3%
            c3 = max(0.0, min(100.0, (chg + 3) / 6 * 100))
    components["momentum"] = {
        "value": round(c3, 1),
        "weight": 0.20,
        "label": "S&P 500 Momentum",
        "raw": round(price_cache.get("sp500", {}).get("change_pct", 0), 4) if price_cache else 0,
    }

    # ── Component 4: Bond-stock spread (safe haven demand) ─────────────
    bond_bearish = sum(
        1 for n in news_items
        if any(a["key"] == "bonds" for a in n["sentiment"]["assets"])
        and n["sentiment"]["label"] == "bearish"
    )
    stock_bullish = sum(
        1 for n in news_items
        if any(a["key"] in ("sp500", "nasdaq", "us30") for a in n["sentiment"]["assets"])
        and n["sentiment"]["label"] == "bullish"
    )
    total_ref = max(1, bond_bearish + stock_bullish)
    # More stock bullish articles relative to bond flight = greed
    c4 = stock_bullish / total_ref * 100
    components["safe_haven"] = {
        "value": round(c4, 1),
        "weight": 0.15,
        "label": "Safe Haven Demand",
        "raw": {"bond_bearish": bond_bearish, "stock_bullish": stock_bullish},
    }

    # ── Component 5: Article volume ratio ──────────────────────────────
    total = len(news_items)
    bullish_count = sum(1 for n in news_items if n["sentiment"]["label"] == "bullish")
    c5 = (bullish_count / total) * 100 if total else 50.0
    components["volume_ratio"] = {
        "value": round(c5, 1),
        "weight": 0.10,
        "label": "Bullish Article Ratio",
        "raw": {"bullish": bullish_count, "total": total},
    }

    # ── Weighted aggregate ─────────────────────────────────────────────
    index = int(
        c1 * 0.30 +
        c2 * 0.25 +
        c3 * 0.20 +
        c4 * 0.15 +
        c5 * 0.10
    )
    index = max(0, min(100, index))

    # ── Label ─────────────────────────────────────────────────────────
    if index >= 75:
        label, color = "Extreme Greed", "#00ff88"
    elif index >= 60:
        label, color = "Greed", "#4ade80"
    elif index >= 45:
        label, color = "Neutral", "#94a3b8"
    elif index >= 25:
        label, color = "Fear", "#f87171"
    else:
        label, color = "Extreme Fear", "#ff2d55"

    return {
        "index": index,
        "label": label,
        "color": color,
        "components": components,
        "avg_score": round(avg_score, 4),
        "volatility": round(std_score, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SENTIMENT MOMENTUM
# ══════════════════════════════════════════════════════════════════════════════

def compute_sentiment_momentum(news_items: list[dict],
                               asset_key: str,
                               hours: int = 4) -> dict:
    """
    Compute recent sentiment momentum for an asset over the last N hours.
    Detects sentiment spikes useful for alert triggering.
    """
    now = datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(hours=hours)).timestamp()

    recent, older = [], []

    for item in news_items:
        assets = item.get("sentiment", {}).get("assets", [])
        if not any(a["key"] == asset_key for a in assets):
            continue
        ts = item.get("timestamp", 0)
        score = item["sentiment"]["score"]
        (recent if ts >= cutoff_ts else older).append(score)

    if not recent:
        return {"momentum": 0.0, "recent_avg": 0.0, "older_avg": 0.0,
                "spike": False, "recent_count": 0, "older_count": 0,
                "direction": "stable"}

    recent_avg = float(np.mean(recent))
    older_avg  = float(np.mean(older)) if older else 0.0
    momentum   = recent_avg - older_avg
    spike      = abs(momentum) > 0.20

    if momentum > 0.20:
        direction = "bullish spike"
    elif momentum < -0.20:
        direction = "bearish spike"
    else:
        direction = "stable"

    return {
        "momentum": round(momentum, 4),
        "recent_avg": round(recent_avg, 4),
        "older_avg": round(older_avg, 4),
        "recent_count": len(recent),
        "older_count": len(older),
        "spike": spike,
        "direction": direction,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SENTIMENT-PRICE CORRELATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_sentiment_price_correlation(db_session, price_history: dict) -> dict:
    """
    Pearson correlation between daily avg sentiment and actual % price change.
    Returns {asset_key: {correlation, direction, strength, data_points, ...}}
    """
    from ..models import SentimentTrend

    results = {}

    for asset_key, prices in price_history.items():
        if len(prices) < 3:
            continue
        try:
            # Build price change series keyed by date
            price_changes = {}
            for i in range(1, len(prices)):
                d = prices[i]["date"]
                prev = prices[i - 1]["close"]
                curr = prices[i]["close"]
                if prev != 0:
                    price_changes[d] = ((curr - prev) / prev) * 100

            # Get hourly sentiment trends for this asset
            cutoff = datetime.utcnow() - timedelta(days=30)
            trends = db_session.query(SentimentTrend).filter(
                SentimentTrend.asset_key == asset_key,
                SentimentTrend.hour_bucket >= cutoff,
            ).order_by(SentimentTrend.hour_bucket).all()

            if len(trends) < 3:
                continue

            # Aggregate to daily avg sentiment
            daily_sentiment: dict[str, list] = defaultdict(list)
            for t in trends:
                day = t.hour_bucket.strftime("%Y-%m-%d")
                daily_sentiment[day].append(t.avg_score)

            daily_avg = {d: float(np.mean(sc)) for d, sc in daily_sentiment.items()}

            common_dates = sorted(set(daily_avg.keys()) & set(price_changes.keys()))
            if len(common_dates) < 3:
                continue

            sent_values  = [daily_avg[d] for d in common_dates]
            price_values = [price_changes[d] for d in common_dates]

            corr = float(np.corrcoef(sent_values, price_values)[0, 1])
            if np.isnan(corr):
                corr = 0.0

            abs_corr = abs(corr)
            if abs_corr >= 0.7:
                strength = "Strong"
            elif abs_corr >= 0.4:
                strength = "Moderate"
            elif abs_corr >= 0.2:
                strength = "Weak"
            else:
                strength = "None"

            results[asset_key] = {
                "correlation": round(corr, 4),
                "direction": "Positive" if corr > 0 else "Negative",
                "strength": strength,
                "data_points": len(common_dates),
                "dates": common_dates[-7:],
                "sentiment_values": [round(v, 4) for v in sent_values[-7:]],
                "price_change_values": [round(v, 4) for v in price_values[-7:]],
            }

        except Exception as e:
            logger.warning(f"[Correlation] {asset_key}: {e}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  SENTIMENT TREND UPDATE
# ══════════════════════════════════════════════════════════════════════════════

def update_sentiment_trends(db_session, news_items: list[dict]):
    """
    Aggregate news sentiment into hourly buckets per asset.
    Upserts SentimentTrend rows — safe to call repeatedly.
    """
    from ..models import SentimentTrend

    bucket_data: dict[tuple, dict] = defaultdict(
        lambda: {"bullish": 0, "bearish": 0, "neutral": 0, "scores": [], "articles": 0}
    )

    for item in news_items:
        try:
            ts_raw = item.get("published_at") or item.get("timestamp")
            if isinstance(ts_raw, (int, float)):
                dt = datetime.utcfromtimestamp(ts_raw)
            elif isinstance(ts_raw, str):
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                dt = dt.replace(tzinfo=None)
            else:
                dt = datetime.utcnow()

            hour = dt.replace(minute=0, second=0, microsecond=0)
            assets = item.get("sentiment", {}).get("assets", [])
            sent_score = item.get("sentiment", {}).get("score", 0.0)
            sent_label = item.get("sentiment", {}).get("label", "neutral")

            for asset in assets:
                key = (asset["key"], hour)
                bucket_data[key]["articles"] += 1
                bucket_data[key]["scores"].append(sent_score)
                lbl = sent_label.lower()
                if lbl in bucket_data[key]:
                    bucket_data[key][lbl] += 1
        except Exception:
            continue

    for (asset_key, hour), data in bucket_data.items():
        try:
            avg = float(np.mean(data["scores"])) if data["scores"] else 0.0
            existing = db_session.query(SentimentTrend).filter_by(
                asset_key=asset_key, hour_bucket=hour
            ).first()

            if existing:
                existing.bullish_count = data["bullish"]
                existing.bearish_count = data["bearish"]
                existing.neutral_count = data["neutral"]
                existing.avg_score = round(avg, 4)
                existing.article_count = data["articles"]
            else:
                t = SentimentTrend(
                    asset_key=asset_key, hour_bucket=hour,
                    bullish_count=data["bullish"], bearish_count=data["bearish"],
                    neutral_count=data["neutral"], avg_score=round(avg, 4),
                    article_count=data["articles"],
                )
                db_session.add(t)

            db_session.commit()
        except Exception as e:
            db_session.rollback()
            logger.debug(f"[Trends] {asset_key} @ {hour}: {e}")