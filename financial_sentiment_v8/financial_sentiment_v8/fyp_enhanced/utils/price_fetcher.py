"""
FinancialPulse v4 — Live Price Fetcher
──────────────────────────────────────
Replaced yfinance with free, reliable public APIs:
  • CoinGecko  → Crypto prices (BTC, ETH)
  • Frankfurter → Forex rates (EUR/USD, GBP/USD, etc.)
  • Yahoo Finance v8 JSON (direct HTTP, no yfinance lib) → Stocks/indices/commodities
  • Hardcoded fallback → last resort, never crashes
"""

from __future__ import annotations

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request

logger = logging.getLogger(__name__)

PRICE_SYMBOLS = {
    "bitcoin":     {"label": "Bitcoin",      "icon": "₿",  "group": "Crypto",     "symbol": "BTCUSD",  "coingecko": "bitcoin"},
    "ethereum":    {"label": "Ethereum",     "icon": "⟠",  "group": "Crypto",     "symbol": "ETHUSD",  "coingecko": "ethereum"},
    "eurusd":      {"label": "EUR/USD",      "icon": "💶", "group": "Forex",      "symbol": "EURUSD",  "fx_from": "EUR", "fx_to": "USD"},
    "gbpusd":      {"label": "GBP/USD",      "icon": "💷", "group": "Forex",      "symbol": "GBPUSD",  "fx_from": "GBP", "fx_to": "USD"},
    "usdjpy":      {"label": "USD/JPY",      "icon": "💴", "group": "Forex",      "symbol": "USDJPY",  "fx_from": "USD", "fx_to": "JPY"},
    "usdcad":      {"label": "USD/CAD",      "icon": "🍁", "group": "Forex",      "symbol": "USDCAD",  "fx_from": "USD", "fx_to": "CAD"},
    "audusd":      {"label": "AUD/USD",      "icon": "🦘", "group": "Forex",      "symbol": "AUDUSD",  "fx_from": "AUD", "fx_to": "USD"},
    "usdchf":      {"label": "USD/CHF",      "icon": "🇨🇭", "group": "Forex",     "symbol": "USDCHF",  "fx_from": "USD", "fx_to": "CHF"},
    "nzdusd":      {"label": "NZD/USD",      "icon": "🥝", "group": "Forex",      "symbol": "NZDUSD",  "fx_from": "NZD", "fx_to": "USD"},
    "sp500":       {"label": "S&P 500",      "icon": "📈", "group": "Indices",    "symbol": "SPX",     "yf": "^GSPC",   "alt_yf": "SPY"},
    "nasdaq":      {"label": "NASDAQ",       "icon": "💻", "group": "Indices",    "symbol": "NAS100",  "yf": "^IXIC",   "alt_yf": "QQQ"},
    "us30":        {"label": "Dow Jones",    "icon": "🏛️", "group": "Indices",    "symbol": "US30",    "yf": "^DJI",    "alt_yf": "DIA"},
    "russell2000": {"label": "Russell 2000", "icon": "📊", "group": "Indices",    "symbol": "RUT",     "yf": "^RUT",    "alt_yf": "IWM"},
    "dax":         {"label": "DAX",          "icon": "🇩🇪", "group": "Indices",   "symbol": "DAX",     "yf": "^GDAXI"},
    "ftse":        {"label": "FTSE 100",     "icon": "🇬🇧", "group": "Indices",   "symbol": "FTSE",    "yf": "^FTSE"},
    "nikkei":      {"label": "Nikkei",       "icon": "🗾", "group": "Indices",    "symbol": "NKY",     "yf": "^N225"},
    "gold":        {"label": "Gold",         "icon": "🥇", "group": "Metals",     "symbol": "XAUUSD",  "yf": "GC=F",    "alt_yf": "IAU"},
    "silver":      {"label": "Silver",       "icon": "🥈", "group": "Metals",     "symbol": "XAGUSD",  "yf": "SI=F",    "alt_yf": "SLV"},
    "copper":      {"label": "Copper",       "icon": "🪙", "group": "Metals",     "symbol": "COPPER",  "yf": "HG=F",    "alt_yf": "CPER"},
    "oil":         {"label": "Crude Oil",    "icon": "🛢️", "group": "Energy",     "symbol": "USOIL",   "yf": "CL=F",    "alt_yf": "USO"},
    "natgas":      {"label": "Natural Gas",  "icon": "🔥", "group": "Energy",     "symbol": "NATGAS",  "yf": "NG=F",    "alt_yf": "UNG"},
    "vix":         {"label": "VIX",          "icon": "😨", "group": "Volatility", "symbol": "VIX",     "yf": "^VIX",    "alt_yf": "VIXY"},
    "bonds":       {"label": "US 10Y Yield", "icon": "📜", "group": "Bonds",      "symbol": "US10Y",   "yf": "^TNX",    "alt_yf": "TLT"},
    "usd":         {"label": "USD Index",    "icon": "💵", "group": "Forex",      "symbol": "DXY",     "yf": "DX-Y.NYB","alt_yf": "UUP"},
}

DEFAULT_PRICES = {
    "bitcoin": 82000.0, "ethereum": 4100.0,
    "gold": 3100.0, "silver": 34.0, "copper": 4.5, "oil": 71.0, "natgas": 3.8,
    "sp500": 5650.0, "nasdaq": 19800.0, "us30": 43500.0, "russell2000": 2100.0,
    "dax": 22500.0, "ftse": 8600.0, "nikkei": 39000.0,
    "eurusd": 1.085, "gbpusd": 1.265, "usdjpy": 149.5, "usdcad": 1.365,
    "audusd": 0.635, "usdchf": 0.898, "nzdusd": 0.584, "usd": 104.0,
    "vix": 16.0, "bonds": 4.35,
}

_price_cache: dict = {}
_price_lock = threading.Lock()
_last_price_fetch: float = 0
PRICE_TTL: int = 60


def _http_get(url: str, timeout: int = 8):
    try:
        req = Request(url, headers={"User-Agent": "FinancialPulse/4.0", "Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"[HTTP] {url[:80]} -> {e}")
        return None


def _fetch_coingecko():
    ids = ",".join(v["coingecko"] for v in PRICE_SYMBOLS.values() if "coingecko" in v)
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    data = _http_get(url)
    if not data:
        return {}
    result = {}
    for key, info in PRICE_SYMBOLS.items():
        cg_id = info.get("coingecko")
        if not cg_id or cg_id not in data:
            continue
        price = data[cg_id].get("usd", 0.0)
        chg = data[cg_id].get("usd_24h_change", 0.0)
        if price and price > 0:
            result[key] = (float(price), float(chg or 0.0))
    return result


def _fetch_frankfurter():
    yesterday = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    bases = set(v["fx_from"] for v in PRICE_SYMBOLS.values() if "fx_from" in v)
    today_rates = {}
    yest_rates = {}
    for base in bases:
        t = _http_get(f"https://api.frankfurter.app/latest?from={base}")
        y = _http_get(f"https://api.frankfurter.app/{yesterday}?from={base}")
        if t and "rates" in t:
            today_rates[base] = t["rates"]
        if y and "rates" in y:
            yest_rates[base] = y["rates"]

    result = {}
    for key, info in PRICE_SYMBOLS.items():
        fx_from = info.get("fx_from")
        fx_to = info.get("fx_to")
        if not fx_from or not fx_to:
            continue
        try:
            price = today_rates.get(fx_from, {}).get(fx_to)
            prev = yest_rates.get(fx_from, {}).get(fx_to)
            if not price:
                continue
            price = float(price)
            chg = ((price - float(prev)) / float(prev) * 100) if prev and float(prev) != 0 else 0.0
            result[key] = (price, chg)
        except Exception:
            pass
    return result


def _yf_quote(symbol: str):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&range=5d&includePrePost=false")
    data = _http_get(url)
    if not data:
        return None
    try:
        result = data["chart"]["result"]
        if not result:
            return None
        meta = result[0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if not price or price <= 0:
            return None
        chg = ((price - prev) / prev * 100) if prev and prev != 0 else 0.0
        return (float(price), float(chg))
    except Exception:
        return None


def _yf_history(symbol: str, days: int = 30):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&range={max(days, 30)}d&includePrePost=false")
    data = _http_get(url)
    if not data:
        return []
    try:
        res = data["chart"]["result"][0]
        timestamps = res["timestamp"]
        ohlcv = res["indicators"]["quote"][0]
        out = []
        for i, ts in enumerate(timestamps):
            c = ohlcv["close"][i]
            if c is None:
                continue
            out.append({
                "date":   datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                "open":   round(float(ohlcv["open"][i] or c), 4),
                "high":   round(float(ohlcv["high"][i] or c), 4),
                "low":    round(float(ohlcv["low"][i] or c), 4),
                "close":  round(float(c), 4),
                "volume": int((ohlcv.get("volume") or [0])[i] or 0),
            })
        return out
    except Exception:
        return []


def fetch_prices(force: bool = False) -> dict:
    global _price_cache, _last_price_fetch
    now = time.time()
    if not force and _price_cache and (now - _last_price_fetch) < PRICE_TTL:
        return dict(_price_cache)

    cg_data = _fetch_coingecko()
    fx_data = _fetch_frankfurter()
    logger.info(f"[Prices] CoinGecko={len(cg_data)} Frankfurter={len(fx_data)}")

    result = {}
    for asset_key, info in PRICE_SYMBOLS.items():
        price, chg = None, 0.0

        if asset_key in cg_data:
            price, chg = cg_data[asset_key]
        elif asset_key in fx_data:
            price, chg = fx_data[asset_key]
        elif "yf" in info:
            q = _yf_quote(info["yf"])
            if not q and "alt_yf" in info:
                q = _yf_quote(info["alt_yf"])
            if q:
                price, chg = q

        if not price or price <= 0:
            price = DEFAULT_PRICES.get(asset_key, 100.0)
            chg = 0.0

        prev = price / (1 + chg / 100) if chg != 0 else price

        result[asset_key] = {
            "symbol":     info["symbol"],
            "asset_key":  asset_key,
            "label":      info["label"],
            "icon":       info["icon"],
            "group":      info.get("group", ""),
            "price":      round(float(price), 6),
            "change_pct": round(float(chg), 4),
            "prev_close": round(float(prev), 6),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    live = sum(1 for k, v in result.items() if v["price"] != DEFAULT_PRICES.get(k, 0))
    logger.info(f"[Prices] {len(result)} total — {live} live, {len(result)-live} fallback")

    with _price_lock:
        _price_cache = result
        _last_price_fetch = now
    return result


def get_cached_prices() -> dict:
    with _price_lock:
        return dict(_price_cache)


def get_price_history(asset_key: str, period: str = "7d", db_session=None) -> list[dict]:
    info = PRICE_SYMBOLS.get(asset_key)
    if not info:
        return []
    days = _period_to_days(period)

    if db_session is not None:
        try:
            from ..models import PriceHistory
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = (db_session.query(PriceHistory)
                    .filter(PriceHistory.asset_key == asset_key, PriceHistory.date >= cutoff)
                    .order_by(PriceHistory.date).all())
            if rows and len(rows) >= max(3, days - 5):
                return [r.to_dict() for r in rows]
        except Exception as e:
            logger.debug(f"[PriceHistory] DB: {e}")

    if "coingecko" in info:
        hist = _coingecko_history(info["coingecko"], days)
        if hist:
            if db_session:
                _persist_price_history(db_session, asset_key, info["symbol"], hist)
            return hist

    if "yf" in info:
        hist = _yf_history(info["yf"], max(days * 2, 30))
        if not hist and "alt_yf" in info:
            hist = _yf_history(info["alt_yf"], max(days * 2, 30))
        if hist:
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            hist = [r for r in hist if r["date"] >= cutoff]
            if db_session and hist:
                _persist_price_history(db_session, asset_key, info["symbol"], hist)
            return hist

    return []


def _coingecko_history(cg_id: str, days: int) -> list[dict]:
    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days={max(days, 7)}"
    data = _http_get(url)
    if not data or not isinstance(data, list):
        return []
    seen = {}
    for candle in data:
        try:
            ts, o, h, l, c = candle
            d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            seen[d] = {"date": d, "open": round(float(o), 4), "high": round(float(h), 4),
                       "low": round(float(l), 4), "close": round(float(c), 4), "volume": 0}
        except Exception:
            pass
    return sorted(seen.values(), key=lambda x: x["date"])


def _period_to_days(period: str) -> int:
    period = period.strip().lower()
    if period.endswith("mo"):
        return int(period[:-2]) * 30
    if period.endswith("y"):
        return int(period[:-1]) * 365
    return int(period.replace("d", "") or "7")


def _persist_price_history(db_session, asset_key, symbol, records):
    try:
        from ..models import PriceHistory
        for rec in records:
            existing = db_session.query(PriceHistory).filter_by(
                asset_key=asset_key, date=rec["date"]).first()
            if existing:
                existing.close = rec["close"]
            else:
                db_session.add(PriceHistory(
                    asset_key=asset_key, symbol=symbol, date=rec["date"],
                    open=rec.get("open"), high=rec.get("high"),
                    low=rec.get("low"), close=rec["close"], volume=rec.get("volume", 0)))
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        logger.debug(f"[PriceHistory] Persist: {e}")