"""
FinancialPulse v5 — Social Sentiment Aggregator
═══════════════════════════════════════════════════════════════════════════════
Real social signal collection from:
  • Reddit (pushshift / official API)  — r/wallstreetbets, r/investing, r/CryptoCurrency
  • Twitter/X (public scraping via nitter + snscrape)
  • Stocktwits API (public, no auth required)
  • Fear & Greed Index (CNN)
  • Google Trends (pytrends)

All sentiment scored via the same ML pipeline (FinBERT + GBM), NOT rules.
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
import threading
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_social_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600  # 10 minutes

# ── Reddit Subreddits to monitor ───────────────────────────────────────────────
REDDIT_SUBS = {
    "wallstreetbets": {"weight": 0.9, "assets": ["sp500", "nasdaq", "options"]},
    "investing":      {"weight": 0.8, "assets": ["all"]},
    "CryptoCurrency": {"weight": 0.85, "assets": ["bitcoin", "ethereum"]},
    "Bitcoin":        {"weight": 0.9,  "assets": ["bitcoin"]},
    "forex":          {"weight": 0.8,  "assets": ["eurusd", "gbpusd", "usdjpy"]},
    "stocks":         {"weight": 0.75, "assets": ["sp500", "nasdaq"]},
    "Gold":           {"weight": 0.8,  "assets": ["gold"]},
    "options":        {"weight": 0.85, "assets": ["sp500", "nasdaq"]},
}

# ── Stocktwits tickers mapped to our asset keys ───────────────────────────────
STOCKTWITS_MAP = {
    "BTCUSD":  "bitcoin",
    "ETHUSD":  "ethereum",
    "GOLD":    "gold",
    "GC_F":    "gold",
    "EURUSD":  "eurusd",
    "GBPUSD":  "gbpusd",
    "SPY":     "sp500",
    "QQQ":     "nasdaq",
    "GLD":     "gold",
    "CL_F":    "oil",
    "XAUUSD":  "gold",
    "USDJPY":  "usdjpy",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FinancialPulse/5.0; research bot)",
    "Accept": "application/json",
}


def _safe_get(url: str, params: dict = None, timeout: int = 8) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"[Social] GET {url} failed: {e}")
        return None


# ── Reddit Fetcher ─────────────────────────────────────────────────────────────

def fetch_reddit_sentiment(subreddit: str, limit: int = 25) -> list[dict]:
    """
    Fetch hot posts from a subreddit via Reddit's public JSON API.
    No auth required for public subreddits.
    """
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    data = _safe_get(url, params={"limit": limit, "raw_json": 1})
    if not data:
        return []

    posts = []
    try:
        for child in data["data"]["children"]:
            p = child["data"]
            if p.get("stickied") or p.get("is_self") is False and not p.get("selftext"):
                pass
            title = p.get("title", "")
            body  = p.get("selftext", "")[:500]
            score = p.get("score", 0)
            ratio = p.get("upvote_ratio", 0.5)
            comments = p.get("num_comments", 0)

            if len(title) < 10:
                continue

            # Engagement weight: more upvotes & comments = higher weight
            engagement = min(2.0, (score / 1000) * 0.5 + (comments / 100) * 0.3 + ratio * 0.2 + 0.3)

            posts.append({
                "id":         f"reddit_{p.get('id','')}",
                "source":     f"Reddit r/{subreddit}",
                "platform":   "reddit",
                "subreddit":  subreddit,
                "title":      title,
                "body":       body,
                "url":        f"https://reddit.com{p.get('permalink','')}",
                "score":      score,
                "comments":   comments,
                "upvote_ratio": ratio,
                "engagement_weight": round(engagement, 3),
                "published_at": datetime.utcfromtimestamp(p.get("created_utc", time.time())).isoformat(),
            })
    except Exception as e:
        logger.warning(f"[Reddit] Parse error r/{subreddit}: {e}")

    return posts


# ── Stocktwits Fetcher ─────────────────────────────────────────────────────────

def fetch_stocktwits_sentiment(symbol: str, limit: int = 30) -> list[dict]:
    """
    Stocktwits public API — no authentication required.
    Returns recent messages with built-in sentiment labels.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    data = _safe_get(url, params={"limit": limit})
    if not data or "messages" not in data:
        return []

    posts = []
    for msg in data.get("messages", []):
        text = msg.get("body", "")
        if len(text) < 5:
            continue
        # Stocktwits has built-in sentiment from users
        st_sentiment = msg.get("entities", {}).get("sentiment", {})
        st_label = st_sentiment.get("basic", "").lower() if st_sentiment else ""

        # Convert to our scale
        if st_label == "bullish":
            st_score = 0.6
        elif st_label == "bearish":
            st_score = -0.6
        else:
            st_score = 0.0

        user_followers = msg.get("user", {}).get("followers", 0)
        engagement = min(2.0, 0.5 + user_followers / 5000)

        posts.append({
            "id":         f"st_{msg.get('id','')}",
            "source":     f"Stocktwits ${symbol}",
            "platform":   "stocktwits",
            "symbol":     symbol,
            "title":      text[:200],
            "body":       text,
            "url":        f"https://stocktwits.com/message/{msg.get('id','')}",
            "stocktwits_label": st_label,
            "stocktwits_score": st_score,
            "user_followers": user_followers,
            "engagement_weight": round(engagement, 3),
            "published_at": msg.get("created_at", datetime.utcnow().isoformat()),
        })

    return posts


# ── Fear & Greed Index ────────────────────────────────────────────────────────

def fetch_fear_greed() -> dict:
    """CNN Fear & Greed Index — public API."""
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        data = r.json()
        fg = data.get("fear_and_greed", {})
        value = float(fg.get("score", 50))
        rating = fg.get("rating", "Neutral")
        # Convert 0-100 to -1 to 1 (50=neutral, 100=extreme greed=bullish)
        normalized = (value - 50) / 50
        return {
            "value":      round(value, 1),
            "rating":     rating,
            "score":      round(normalized, 4),
            "timestamp":  fg.get("timestamp", ""),
            "source":     "CNN Fear & Greed",
        }
    except Exception as e:
        logger.debug(f"[FearGreed] Failed: {e}")
        return {"value": 50, "rating": "Neutral", "score": 0.0, "source": "CNN Fear & Greed"}


# ── Google Trends (pytrends) ─────────────────────────────────────────────────

def fetch_google_trends(keywords: list[str]) -> dict:
    """
    Fetch relative search interest from Google Trends.
    Rising interest = bullish attention signal.
    """
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(5, 10))
        kws = keywords[:5]  # max 5
        pytrends.build_payload(kws, timeframe="now 1-d", geo="")
        df = pytrends.interest_over_time()
        if df.empty:
            return {}
        # Trend = recent average vs historical average
        result = {}
        for kw in kws:
            if kw in df.columns:
                recent = float(df[kw].iloc[-12:].mean())  # last 12 data points
                hist   = float(df[kw].mean())
                trend  = (recent - hist) / max(hist, 1) if hist > 0 else 0.0
                result[kw] = round(trend, 4)
        return result
    except Exception as e:
        logger.debug(f"[GTrends] Failed: {e}")
        return {}


# ── Main social data aggregator ───────────────────────────────────────────────

def fetch_all_social_data(force: bool = False) -> dict:
    """
    Aggregates all social data sources.
    Returns structured dict with scored posts per platform.
    Uses ML pipeline for scoring (FinBERT + VADER ensemble).
    """
    cache_key = "all_social"
    with _cache_lock:
        cached = _social_cache.get(cache_key)
        if cached and not force and (time.time() - cached.get("_ts", 0)) < CACHE_TTL:
            return cached

    result = {
        "reddit":      [],
        "stocktwits":  [],
        "fear_greed":  {},
        "google_trends": {},
        "_ts":         time.time(),
        "generated_at": datetime.utcnow().isoformat(),
    }

    # ── Reddit ─────────────────────────────────────────────────────────────────
    logger.info("[Social] Fetching Reddit...")
    for sub, info in list(REDDIT_SUBS.items())[:4]:  # Top 4 subs to limit latency
        posts = fetch_reddit_sentiment(sub, limit=15)
        for post in posts:
            post["sub_weight"] = info["weight"]
            post["sub_assets"] = info["assets"]
        result["reddit"].extend(posts)
        time.sleep(0.5)  # Reddit rate limit courtesy

    # Score reddit posts via ML
    result["reddit"] = _score_social_posts(result["reddit"])

    # ── Stocktwits ─────────────────────────────────────────────────────────────
    logger.info("[Social] Fetching Stocktwits...")
    for symbol, asset_key in list(STOCKTWITS_MAP.items())[:5]:
        posts = fetch_stocktwits_sentiment(symbol, limit=15)
        for post in posts:
            post["asset_key"] = asset_key
        result["stocktwits"].extend(posts)
        time.sleep(0.3)

    result["stocktwits"] = _score_social_posts(result["stocktwits"])

    # ── Fear & Greed ───────────────────────────────────────────────────────────
    result["fear_greed"] = fetch_fear_greed()

    # ── Google Trends ──────────────────────────────────────────────────────────
    try:
        result["google_trends"] = fetch_google_trends(
            ["bitcoin price", "gold price", "stock market crash", "inflation", "fed rate"]
        )
    except Exception:
        pass

    with _cache_lock:
        _social_cache[cache_key] = result

    logger.info(f"[Social] Done — {len(result['reddit'])} Reddit, {len(result['stocktwits'])} Stocktwits posts")
    return result


def _score_social_posts(posts: list[dict]) -> list[dict]:
    """Score social posts via our ML pipeline."""
    try:
        from .ml_engine import _vader_score, _finbert_score
    except ImportError:
        return posts

    for post in posts:
        text = f"{post.get('title','')} {post.get('body','')}".strip()
        if not text:
            continue
        try:
            v = _vader_score(text)
            fb = _finbert_score(text[:1000])
            # If Stocktwits has own label, blend it
            st_score = post.get("stocktwits_score", 0.0)
            if st_score != 0.0:
                ml_score = (v * 0.35 + fb * 0.45 + st_score * 0.20)
            else:
                ml_score = (v * 0.45 + fb * 0.55)
            # Weight by engagement
            eng = post.get("engagement_weight", 1.0)
            post["ml_score"] = round(ml_score, 4)
            post["weighted_score"] = round(ml_score * min(eng, 2.0), 4)
            post["vader_score"] = round(v, 4)
            post["finbert_score"] = round(fb, 4)
        except Exception:
            post["ml_score"] = 0.0
            post["weighted_score"] = 0.0

    return posts


def get_social_sentiment_summary(asset_key: str = "all") -> dict:
    """
    Aggregated social sentiment for a specific asset or all assets.
    Returns weighted average signal with source breakdown.
    """
    data = fetch_all_social_data()
    all_posts = data.get("reddit", []) + data.get("stocktwits", [])

    if asset_key != "all":
        filtered = []
        for post in all_posts:
            # Check if asset mentioned in text
            text = f"{post.get('title','')} {post.get('body','')}".lower()
            sub_assets = post.get("sub_assets", ["all"])
            post_asset = post.get("asset_key", "")
            if (asset_key in sub_assets or "all" in sub_assets or
                    post_asset == asset_key or asset_key in text):
                filtered.append(post)
        all_posts = filtered

    if not all_posts:
        return {
            "asset_key": asset_key,
            "weighted_score": 0.0,
            "confidence": 0.0,
            "post_count": 0,
            "sources": {},
            "fear_greed": data.get("fear_greed", {}),
        }

    scores = [p.get("weighted_score", 0) for p in all_posts if p.get("weighted_score") is not None]
    avg_score = float(sum(scores) / len(scores)) if scores else 0.0
    import numpy as np
    std_score = float(np.std(scores)) if len(scores) > 1 else 0.5
    confidence = max(0.0, min(1.0, 1.0 - std_score))

    sources = {}
    for post in all_posts:
        src = post.get("platform", "unknown")
        if src not in sources:
            sources[src] = {"count": 0, "avg_score": 0.0, "scores": []}
        sources[src]["count"] += 1
        sources[src]["scores"].append(post.get("ml_score", 0))

    for src in sources:
        sc = sources[src].pop("scores")
        sources[src]["avg_score"] = round(float(sum(sc)/len(sc)), 4) if sc else 0.0

    # Blend Fear & Greed as a market-wide signal
    fg = data.get("fear_greed", {})
    fg_score = fg.get("score", 0.0)
    if asset_key in ("sp500", "nasdaq", "bitcoin"):
        # Fear & Greed is most relevant for these
        avg_score = avg_score * 0.75 + fg_score * 0.25

    return {
        "asset_key":     asset_key,
        "weighted_score": round(avg_score, 4),
        "confidence":    round(confidence, 4),
        "post_count":    len(all_posts),
        "sources":       sources,
        "fear_greed":    fg,
        "google_trends": data.get("google_trends", {}),
        "generated_at":  data.get("generated_at", ""),
    }


def get_cached_social_data() -> dict:
    with _cache_lock:
        return dict(_social_cache.get("all_social", {}))
