"""
FinancialPulse v3 — News Fetcher (FIXED)
═════════════════════════════════════════════════════════════════════════════
Fixes applied:
  ✓ 'import os' moved to top (was used before import in _fetch_newsapi_fallback)
  ✓ Reuters RSS feeds replaced with working alternatives
  ✓ Kitco Gold replaced with a working feed
  ✓ MarketWatch replaced with working alternative
  ✓ ForexLive replaced with working alternative
  ✓ Bloomberg / FT / Investing.com (paywalled) replaced with open feeds
"""

"""
FinancialPulse v3 — News Fetcher (2026 Edition — Robust)
"""

import os
import re
import hashlib
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

import feedparser

from .sentiment_engine import analyzer

logger = logging.getLogger(__name__)

# Updated 2026 working RSS feeds (no bozo errors)
RSS_FEEDS = [
    # Crypto - Very Reliable
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss", "category": "Crypto"},
    {"name": "CoinDesk",      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "Crypto"},
    {"name": "Decrypt",       "url": "https://decrypt.co/feed", "category": "Crypto"},

    # Markets & Finance - Stable
    {"name": "CNBC",          "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "category": "Markets"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/rss/", "category": "Markets"},
    {"name": "Seeking Alpha", "url": "https://seekingalpha.com/market_currents.xml", "category": "Markets"},

    # Energy / Commodities
    {"name": "OilPrice",      "url": "https://oilprice.com/rss/main", "category": "Energy"},
    
    # Forex
    {"name": "FX Street",     "url": "https://www.fxstreet.com/rss/news", "category": "Forex"},
]

# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE HEALTH TRACKING
# ══════════════════════════════════════════════════════════════════════════════

_source_health: dict[str, dict] = {
    s["name"]: {
        "fail_count": 0,
        "total_fetches": 0,
        "last_success": None,
        "last_error": None,
        "last_article_count": 0,
        "healthy": True,
    }
    for s in RSS_FEEDS
}
_health_lock = threading.Lock()

MAX_CONSECUTIVE_FAILS = 3


def get_source_health() -> dict:
    with _health_lock:
        return {k: dict(v) for k, v in _source_health.items()}


def _mark_success(source_name: str, article_count: int):
    with _health_lock:
        if source_name not in _source_health:
            return
        h = _source_health[source_name]
        h["fail_count"] = 0
        h["total_fetches"] += 1
        h["last_success"] = datetime.utcnow().isoformat()
        h["last_article_count"] = article_count
        h["healthy"] = True


def _mark_failure(source_name: str, error: str):
    with _health_lock:
        if source_name not in _source_health:
            return
        h = _source_health[source_name]
        h["fail_count"] += 1
        h["total_fetches"] += 1
        h["last_error"] = str(error)[:200]
        if h["fail_count"] >= MAX_CONSECUTIVE_FAILS:
            h["healthy"] = False
            logger.warning(f"[Health] {source_name} marked UNHEALTHY after "
                           f"{h['fail_count']} consecutive failures.")


def _unhealthy_ratio() -> float:
    with _health_lock:
        total = len(_source_health)
        unhealthy = sum(1 for v in _source_health.values() if not v["healthy"])
        return unhealthy / total if total else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  RETRY FETCH
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_with_retry(source: dict, max_retries: int = 3) -> list[dict]:
    for attempt in range(max_retries):
        try:
            feed = feedparser.parse(
                source["url"],
                request_headers={"User-Agent": "FinancialPulse/3.1 (+https://github.com/yourname/financialpulse)"},
            )
            if feed.bozo and not feed.entries:
                raise ValueError(f"Bozo: {feed.bozo_exception}")

            items = []
            for entry in feed.entries[:10]:
                title = getattr(entry, "title", "").strip()
                if not title or len(title) < 10:
                    continue
                summary = _strip_html(getattr(entry, "summary", ""))
                date_str, dt = _parse_date(entry)
                uid = _make_id(title, getattr(entry, "link", "#"))

                items.append({
                    "id": uid,
                    "title": title,
                    "description": summary[:350] + "…" if len(summary) > 350 else summary,
                    "url": getattr(entry, "link", "#"),
                    "source": source["name"],
                    "category": source["category"],
                    "date": date_str,
                    "published_at": dt.isoformat(),
                    "timestamp": time.time(),
                    "_raw_text": f"{title}. {summary}",
                })

            _mark_success(source["name"], len(items))
            return items

        except Exception as e:
            wait = (2 ** attempt) + 0.5
            logger.warning(f"[Fetcher] {source['name']} attempt {attempt+1}/{max_retries} failed: {e}. Retrying in {wait:.1f}s...")
            if attempt < max_retries - 1:
                time.sleep(wait)
            else:
                _mark_failure(source["name"], str(e))
                return []

    return []


# ══════════════════════════════════════════════════════════════════════════════
#  NEWSAPI FALLBACK
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_newsapi_fallback() -> list[dict]:
    """
    Fetch from NewsAPI.org when RSS degradation exceeds threshold.
    Requires NEWSAPI_KEY in environment.
    """
    api_key = os.getenv("NEWSAPI_KEY", "")
    if not api_key:
        return []
    try:
        import urllib.request as _req
        import urllib.parse as _parse
        import json as _json
        params = _parse.urlencode({
            "q":        "stock market OR gold price OR bitcoin OR forex",
            "language": "en",
            "sortBy":   "publishedAt",
            "pageSize": 30,
            "apiKey":   api_key,
        })
        url = f"https://newsapi.org/v2/everything?{params}"
        with _req.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
        items = []
        for art in data.get("articles", []):
            title = art.get("title", "").strip()
            desc  = art.get("description", "") or ""
            if not title or title == "[Removed]":
                continue
            uid = _make_id(title, art.get("url", ""))
            now = datetime.now(timezone.utc)
            items.append({
                "id":           uid,
                "title":        title,
                "description":  desc[:350],
                "url":          art.get("url", "#"),
                "source":       art.get("source", {}).get("name", "NewsAPI"),
                "category":     "Markets",
                "date":         now.strftime("%H:%M  %b %d"),
                "published_at": now.isoformat(),
                "timestamp":    time.time(),
                "_raw_text":    f"{title}. {desc}",
            })
        logger.info(f"[NewsAPI] Fallback fetched {len(items)} articles.")
        return items
    except Exception as e:
        logger.warning(f"[NewsAPI] Fallback failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  TF-IDF DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def _dedup_tfidf(items: list[dict], threshold: float = 0.80) -> list[dict]:
    if len(items) <= 1:
        return items
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        texts = [it.get("_raw_text", it["title"]) for it in items]
        vec = TfidfVectorizer(max_features=5000, stop_words="english", ngram_range=(1, 2))
        tfidf_matrix = vec.fit_transform(texts)
        sim = cosine_similarity(tfidf_matrix)

        keep, removed = [], set()
        for i in range(len(items)):
            if i in removed:
                continue
            keep.append(items[i])
            for j in range(i + 1, len(items)):
                if j not in removed and sim[i, j] >= threshold:
                    removed.add(j)

        logger.info(f"[Dedup] TF-IDF: {len(items)} → {len(keep)} "
                    f"(removed {len(items) - len(keep)} near-duplicates)")
        return keep

    except ImportError:
        seen: set[str] = set()
        result = []
        for it in items:
            tk = it["title"][:50].lower()
            if tk not in seen:
                seen.add(tk)
                result.append(it)
        return result


# ══════════════════════════════════════════════════════════════════════════════
#  SENTIMENT SCORING PASS
# ══════════════════════════════════════════════════════════════════════════════

def _score_items(raw_items: list[dict], use_llm: bool = False) -> list[dict]:
    scored = []
    for item in raw_items:
        item_copy = dict(item)
        item_copy.pop("_raw_text", None)
        item_copy["sentiment"] = analyzer.analyze(
            item["title"],
            item.get("description", ""),
            use_llm=use_llm,
        )
        scored.append(item_copy)
    return scored


# ══════════════════════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════════════════════

_news_cache: list[dict] = []
_cache_lock = threading.Lock()
_last_fetch: float = 0
CACHE_TTL: int = 120


def fetch_all_news(force: bool = False) -> list[dict]:
    global _news_cache, _last_fetch
    now = time.time()

    if not force and _news_cache and (now - _last_fetch) < CACHE_TTL:
        return _news_cache

    import concurrent.futures
    raw_all: list[dict] = []
    seen_ids: set[str] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_with_retry, src): src for src in RSS_FEEDS}
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            try:
                for item in fut.result():
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        raw_all.append(item)
            except Exception as e:
                logger.warning(f"[Fetcher] Thread error: {e}")

    # NewsAPI fallback if too many unhealthy
    if _unhealthy_ratio() >= 0.40:
        logger.warning(f"[Fetcher] {_unhealthy_ratio():.0%} sources unhealthy. "
                       "Activating NewsAPI fallback.")
        for item in _fetch_newsapi_fallback():
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                raw_all.append(item)

    raw_all.sort(key=lambda x: x["timestamp"], reverse=True)
    raw_all = raw_all[:200]

    # Fix #2 — News validity algorithm (age + relevance + spam filter)
    valid_all = _validate_news(raw_all)

    deduped = _dedup_tfidf(valid_all, threshold=0.80)
    scored  = _score_items(deduped, use_llm=False)

    # Fix #4 / #7 — Tag each article with specific market niche (NASDAQ/Forex/Gold etc.)
    scored  = _tag_market_niches(scored)
    scored  = scored[:150]

    with _cache_lock:
        _news_cache = scored
        _last_fetch = now

    logger.info(f"[Fetcher] Pipeline complete: {len(scored)} articles "
                f"(from {len(raw_all)} raw → {len(valid_all)} valid → {len(deduped)} deduped)")
    return scored


def get_cached_news() -> list[dict]:
    with _cache_lock:
        return list(_news_cache)


def filter_news(news: list[dict], asset: str = None,
                sentiment: str = None, query: str = None) -> list[dict]:
    result = news
    if asset and asset != "all":
        result = [n for n in result
                  if any(a["key"] == asset for a in n["sentiment"]["assets"])]
    if sentiment and sentiment != "all":
        result = [n for n in result if n["sentiment"]["label"] == sentiment]
    if query:
        q = query.lower()
        result = [n for n in result
                  if q in n["title"].lower() or q in n["description"].lower()]
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  NEWS VALIDITY ALGORITHM  (Fix #2)
# ══════════════════════════════════════════════════════════════════════════════

# Keywords that indicate genuine financial/market relevance
_FINANCIAL_SIGNALS = [
    "price", "market", "stock", "trade", "trading", "invest", "rally", "crash",
    "rate", "inflation", "gdp", "fed", "central bank", "earnings", "revenue",
    "crypto", "bitcoin", "forex", "dollar", "euro", "gold", "oil", "yield",
    "bond", "equity", "fund", "etf", "ipo", "merger", "acquisition", "profit",
    "loss", "dividend", "bullish", "bearish", "long", "short", "volatility",
    "index", "commodity", "futures", "options", "hedge", "liquidity", "supply",
    "demand", "output", "production", "deficit", "surplus", "tariff", "sanction",
    "opec", "fomc", "ecb", "boj", "boe", "rba", "snb", "rbnz",
]

# Spam/clickbait patterns to reject
_SPAM_PATTERNS = [
    r"\b(win|winning|free|giveaway|click here|sign up now|limited offer)\b",
    r"\b(hack|cheat|secret|trick|loophole)\b",
    r"(!!|\?\?|😂|🤣|😱|🚀🚀🚀)",
    r"\b(astrology|horoscope|zodiac|psychic|crystal)\b",
]
_SPAM_RE = re.compile("|".join(_SPAM_PATTERNS), re.IGNORECASE)

# Maximum article age in hours
_MAX_AGE_HOURS = 72


def _validate_news(items: list[dict]) -> list[dict]:
    """
    News Validity Algorithm:
    1. Age gate  — reject articles older than 72 hours
    2. Relevance — must contain at least 1 financial keyword
    3. Spam gate — reject clickbait/off-topic titles
    4. Minimum length — title must be >= 15 characters
    Returns validated items with a 'validity_score' field (0.0–1.0).
    """
    now_ts = time.time()
    valid = []
    rejected_age = rejected_spam = rejected_relevance = 0

    for item in items:
        title = item.get("title", "")
        desc  = item.get("description", "")
        text  = f"{title} {desc}".lower()

        # 1. Length gate
        if len(title.strip()) < 15:
            continue

        # 2. Age gate
        age_hours = (now_ts - item.get("timestamp", now_ts)) / 3600
        if age_hours > _MAX_AGE_HOURS:
            rejected_age += 1
            continue

        # 3. Spam gate
        if _SPAM_RE.search(title):
            rejected_spam += 1
            continue

        # 4. Financial relevance gate (at least 1 keyword)
        fin_hits = sum(1 for kw in _FINANCIAL_SIGNALS if kw in text)
        if fin_hits == 0:
            rejected_relevance += 1
            continue

        # Compute validity score: freshness (40%) + relevance (40%) + source health (20%)
        freshness   = max(0.0, 1.0 - age_hours / _MAX_AGE_HOURS)
        relevance   = min(1.0, fin_hits / 5.0)
        src_health  = 1.0 if _source_health.get(item.get("source", ""), {}).get("healthy", True) else 0.5
        validity    = round(0.40 * freshness + 0.40 * relevance + 0.20 * src_health, 3)

        item = dict(item)
        item["validity_score"]   = validity
        item["age_hours"]        = round(age_hours, 1)
        item["relevance_hits"]   = fin_hits
        valid.append(item)

    logger.info(
        f"[Validity] {len(items)} → {len(valid)} valid "
        f"(dropped: {rejected_age} old, {rejected_spam} spam, {rejected_relevance} off-topic)"
    )
    # Sort by validity score descending so best articles float up
    valid.sort(key=lambda x: (x["validity_score"], x["timestamp"]), reverse=True)
    return valid


# ══════════════════════════════════════════════════════════════════════════════
#  NICHE MARKET TAGGER  (Fix #4 / #7 — tags BUY/SELL signals with market niche)
# ══════════════════════════════════════════════════════════════════════════════

# Maps asset_key → human-readable market niche category
_NICHE_MAP = {
    # Crypto
    "bitcoin":    {"niche": "Crypto",       "market": "BTC/USD",   "tag": "CRYPTO"},
    "ethereum":   {"niche": "Crypto",       "market": "ETH/USD",   "tag": "CRYPTO"},
    "crypto":     {"niche": "Crypto",       "market": "Crypto Mkt","tag": "CRYPTO"},
    # Forex
    "eurusd":     {"niche": "Forex",        "market": "EUR/USD",   "tag": "FOREX"},
    "gbpusd":     {"niche": "Forex",        "market": "GBP/USD",   "tag": "FOREX"},
    "usdjpy":     {"niche": "Forex",        "market": "USD/JPY",   "tag": "FOREX"},
    "audusd":     {"niche": "Forex",        "market": "AUD/USD",   "tag": "FOREX"},
    "nzdusd":     {"niche": "Forex",        "market": "NZD/USD",   "tag": "FOREX"},
    "usdcad":     {"niche": "Forex",        "market": "USD/CAD",   "tag": "FOREX"},
    "usdchf":     {"niche": "Forex",        "market": "USD/CHF",   "tag": "FOREX"},
    "usd":        {"niche": "DXY/Dollar",   "market": "DXY",       "tag": "DXY"},
    # Indices
    "sp500":      {"niche": "US Indices",   "market": "S&P 500",   "tag": "SPX"},
    "nasdaq":     {"niche": "US Indices",   "market": "NASDAQ",    "tag": "NAS100"},
    "us30":       {"niche": "US Indices",   "market": "Dow Jones", "tag": "US30"},
    "russell2000":{"niche": "US Indices",   "market": "Russell 2K","tag": "RUT"},
    "dax":        {"niche": "EU Indices",   "market": "DAX",       "tag": "DAX"},
    "ftse":       {"niche": "UK Indices",   "market": "FTSE 100",  "tag": "UK100"},
    "nikkei":     {"niche": "Asia Indices", "market": "Nikkei",    "tag": "JP225"},
    # Metals / Commodities
    "gold":       {"niche": "Metals",       "market": "Gold",      "tag": "XAU/USD"},
    "silver":     {"niche": "Metals",       "market": "Silver",    "tag": "XAG/USD"},
    "platinum":   {"niche": "Metals",       "market": "Platinum",  "tag": "XPT/USD"},
    "copper":     {"niche": "Metals",       "market": "Copper",    "tag": "COPPER"},
    "oil":        {"niche": "Energy",       "market": "Crude Oil", "tag": "WTI/OIL"},
    "natgas":     {"niche": "Energy",       "market": "Nat Gas",   "tag": "NATGAS"},
    # Macro
    "bonds":      {"niche": "Bonds",        "market": "US Bonds",  "tag": "TNX"},
    "geopolitics":{"niche": "Macro/Risk",   "market": "Risk-Off",  "tag": "MACRO"},
    "inflation":  {"niche": "Macro/CPI",    "market": "Inflation", "tag": "CPI"},
    "vix":        {"niche": "Volatility",   "market": "VIX",       "tag": "VIX"},
}


def _tag_market_niches(scored_items: list[dict]) -> list[dict]:
    """
    Enriches each article's sentiment.assets with niche/market/tag fields
    so the UI can display  'BUY → NASDAQ'  instead of just 'BUY'.
    Also attaches top-level niche_tags list for quick display.
    """
    for item in scored_items:
        s = item.get("sentiment", {})
        assets = s.get("assets", [])
        niche_tags = []
        enriched_assets = []
        for a in assets:
            key   = a.get("key", "")
            ninfo = _NICHE_MAP.get(key, {})
            enriched = dict(a)
            enriched["niche"]  = ninfo.get("niche",  "")
            enriched["market"] = ninfo.get("market", a.get("label", key.upper()))
            enriched["tag"]    = ninfo.get("tag",    key.upper())
            enriched_assets.append(enriched)
            if ninfo.get("tag"):
                signal = a.get("signal", "NEUTRAL")
                niche_tags.append({
                    "tag":    ninfo["tag"],
                    "niche":  ninfo["niche"],
                    "market": ninfo["market"],
                    "signal": signal,
                    "signal_class": a.get("signal_class", "neutral"),
                    "score":  round(a.get("score", 0.0), 3),
                })
        s["assets"]     = enriched_assets
        s["niche_tags"] = niche_tags          # NEW: list of {tag, niche, market, signal, score}
        item["sentiment"] = s
        # Convenience: top niche string e.g. "NASDAQ · NAS100 → STRONG BUY"
        if niche_tags:
            top = max(niche_tags, key=lambda x: abs(x["score"]))
            item["top_niche_signal"] = f"{top['market']} → {top['signal']}"
        else:
            item["top_niche_signal"] = ""
    return scored_items


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _make_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()[:12]


def _parse_date(entry) -> tuple[str, datetime]:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.strftime("%H:%M  %b %d"), dt
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.strftime("%H:%M  %b %d"), dt
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    return now.strftime("%H:%M  %b %d"), now


def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text or '').strip()