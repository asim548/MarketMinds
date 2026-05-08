"""
FinancialPulse v3 — Advanced Sentiment Engine
═══════════════════════════════════════════════════════════════════════════════
5-Layer Ensemble:
  Layer 1 — VADER          (augmented financial lexicon)          weight 0.28
  Layer 2 — TextBlob        (subjectivity-weighted)               weight 0.12
  Layer 3 — Keyword Scorer  (phrase-level, with negation guard)   weight 0.22
  Layer 4 — FinBERT         (ProsusAI/finbert, graceful fallback) weight 0.30
  Layer 5 — Claude LLM      (context-aware adjudicator)           weight 0.08

Confidence = f(inter-model agreement) — NOT magnitude of final score.
Negation handling: "not", "no", "never", "without" within a 3-word window
  inverts bullish/bearish phrase weights.
Asset detection uses sentence-level context matching to avoid false positives
  (e.g. "gold medal" will not trigger XAUUSD unless price context is present).
"""

from __future__ import annotations

import re
import os
import json
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from textblob import TextBlob

from .hf_pipelines import get_finbert_pipeline

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  ASSET DICTIONARY — 30 instruments with context guards
# ══════════════════════════════════════════════════════════════════════════════

ASSET_KEYWORDS = {
    "gold": {
        "label": "Gold", "icon": "🥇", "group": "Metals", "symbol": "XAUUSD",
        "yf_symbol": "GC=F",
        "keywords": ["gold price", "gold futures", "spot gold", "xau", "bullion",
                     "gold demand", "gold miners", "comex gold", "gold rally",
                     "gold reserves", "gold etf", "gold ounce"],
        # context_required: at least one of these must appear nearby to confirm the asset
        "context": ["price", "market", "futures", "etf", "ounce", "oz", "invest",
                    "trade", "rally", "plunge", "rise", "fall", "miners"],
    },
    "silver": {
        "label": "Silver", "icon": "🥈", "group": "Metals", "symbol": "XAGUSD",
        "yf_symbol": "SI=F",
        "keywords": ["silver price", "silver futures", "spot silver", "xag",
                     "silver demand", "silver rally", "comex silver"],
        "context": ["price", "market", "futures", "ounce", "invest", "rally"],
    },
    "platinum": {
        "label": "Platinum", "icon": "💍", "group": "Metals", "symbol": "XPTUSD",
        "yf_symbol": "PL=F",
        "keywords": ["platinum price", "platinum futures", "xpt", "pgm",
                     "platinum group metal", "platinum demand"],
        "context": ["price", "market", "futures", "invest"],
    },
    "copper": {
        "label": "Copper", "icon": "🪙", "group": "Metals", "symbol": "COPPER",
        "yf_symbol": "HG=F",
        "keywords": ["copper price", "copper futures", "dr copper",
                     "copper demand", "copper supply", "copper market"],
        "context": ["price", "futures", "demand", "supply", "market"],
    },
    "oil": {
        "label": "Crude Oil", "icon": "🛢️", "group": "Energy", "symbol": "USOIL",
        "yf_symbol": "CL=F",
        "keywords": ["crude oil", "brent crude", "wti crude", "oil price",
                     "petroleum", "opec", "oil barrel", "oil supply",
                     "oil demand", "oil futures", "opec+", "oil market"],
        "context": ["price", "barrel", "futures", "opec", "supply", "demand"],
    },
    "natgas": {
        "label": "Natural Gas", "icon": "🔥", "group": "Energy", "symbol": "NATGAS",
        "yf_symbol": "NG=F",
        "keywords": ["natural gas", "lng", "gas price", "henry hub",
                     "gas supply", "gas demand", "ttf gas", "europe gas"],
        "context": ["price", "supply", "demand", "futures"],
    },
    "usd": {
        "label": "USD / DXY", "icon": "💵", "group": "Forex", "symbol": "DXY",
        "yf_symbol": "DX-Y.NYB",
        "keywords": ["dollar index", "dxy", "us dollar", "greenback",
                     "federal reserve rate", "fed rate", "fomc decision",
                     "powell speech", "us treasury yield", "us cpi",
                     "nonfarm payroll", "us gdp", "dollar strength"],
        "context": ["rate", "yield", "strength", "weakness", "hike", "cut", "index"],
    },
    "eurusd": {
        "label": "EUR/USD", "icon": "💶", "group": "Forex", "symbol": "EURUSD",
        "yf_symbol": "EURUSD=X",
        "keywords": ["euro dollar", "eurusd", "eur/usd", "ecb rate",
                     "european central bank decision", "eurozone inflation",
                     "lagarde speech", "euro weakness", "euro strength"],
        "context": ["rate", "exchange", "currency", "forex"],
    },
    "gbpusd": {
        "label": "GBP/USD", "icon": "💷", "group": "Forex", "symbol": "GBPUSD",
        "yf_symbol": "GBPUSD=X",
        "keywords": ["pound dollar", "gbpusd", "gbp/usd", "bank of england rate",
                     "boe decision", "uk inflation", "sterling weakness",
                     "sterling strength", "bailey speech"],
        "context": ["rate", "exchange", "currency", "forex"],
    },
    "usdjpy": {
        "label": "USD/JPY", "icon": "💴", "group": "Forex", "symbol": "USDJPY",
        "yf_symbol": "JPY=X",
        "keywords": ["yen dollar", "usdjpy", "usd/jpy", "bank of japan rate",
                     "boj policy", "japan inflation", "yen weakness",
                     "yen strength", "ueda speech"],
        "context": ["rate", "exchange", "currency", "forex", "intervention"],
    },
    "usdcad": {
        "label": "USD/CAD", "icon": "🍁", "group": "Forex", "symbol": "USDCAD",
        "yf_symbol": "CAD=X",
        "keywords": ["canadian dollar", "loonie", "usdcad", "usd/cad",
                     "bank of canada rate", "boc decision"],
        "context": ["rate", "exchange", "currency", "forex"],
    },
    "audusd": {
        "label": "AUD/USD", "icon": "🦘", "group": "Forex", "symbol": "AUDUSD",
        "yf_symbol": "AUDUSD=X",
        "keywords": ["aussie dollar", "audusd", "aud/usd",
                     "reserve bank of australia rate", "rba decision"],
        "context": ["rate", "exchange", "currency", "forex"],
    },
    "usdchf": {
        "label": "USD/CHF", "icon": "🇨🇭", "group": "Forex", "symbol": "USDCHF",
        "yf_symbol": "CHF=X",
        "keywords": ["swiss franc", "swissy", "usdchf", "usd/chf",
                     "snb decision", "swiss national bank"],
        "context": ["rate", "exchange", "currency", "forex"],
    },
    "nzdusd": {
        "label": "NZD/USD", "icon": "🥝", "group": "Forex", "symbol": "NZDUSD",
        "yf_symbol": "NZDUSD=X",
        "keywords": ["kiwi dollar", "nzdusd", "nzd/usd", "rbnz decision"],
        "context": ["rate", "exchange", "currency", "forex"],
    },
    "bitcoin": {
        "label": "Bitcoin", "icon": "₿", "group": "Crypto", "symbol": "BTCUSD",
        "yf_symbol": "BTC-USD",
        "keywords": ["bitcoin price", "btc price", "bitcoin etf", "spot bitcoin",
                     "btc/usd", "btcusd", "bitcoin market", "bitcoin rally",
                     "bitcoin crash", "bitcoin dominance", "satoshi"],
        "context": ["price", "market", "etf", "exchange", "wallet"],
    },
    "ethereum": {
        "label": "Ethereum", "icon": "⟠", "group": "Crypto", "symbol": "ETHUSD",
        "yf_symbol": "ETH-USD",
        "keywords": ["ethereum price", "eth price", "ether price",
                     "defi protocol", "smart contract", "ethereum network",
                     "eth/usd", "ethusd", "ethereum upgrade"],
        "context": ["price", "market", "network", "protocol"],
    },
    "crypto": {
        "label": "Crypto Market", "icon": "🔗", "group": "Crypto", "symbol": "CRYPTO",
        "yf_symbol": "BTC-USD",
        "keywords": ["crypto market", "cryptocurrency market", "altcoin season",
                     "crypto rally", "crypto crash", "digital asset",
                     "coinbase exchange", "binance exchange", "crypto regulation",
                     "stablecoin", "solana price", "ripple xrp", "dogecoin price"],
        "context": ["market", "price", "exchange", "regulation"],
    },
    "sp500": {
        "label": "S&P 500", "icon": "📈", "group": "Indices", "symbol": "SPX",
        "yf_symbol": "^GSPC",
        "keywords": ["s&p 500", "s&p500", "spx index", "spy etf",
                     "us stock market", "wall street", "us equities",
                     "s&p gains", "s&p falls", "stock market rally"],
        "context": ["index", "market", "etf", "rally", "sell", "gain"],
    },
    "nasdaq": {
        "label": "NASDAQ", "icon": "💻", "group": "Indices", "symbol": "NAS100",
        "yf_symbol": "^IXIC",
        "keywords": ["nasdaq 100", "nasdaq composite", "qqq etf",
                     "tech stocks", "technology index", "nasdaq rally",
                     "nasdaq falls", "ndx index"],
        "context": ["index", "market", "etf", "rally", "tech"],
    },
    "us30": {
        "label": "Dow Jones", "icon": "🏛️", "group": "Indices", "symbol": "US30",
        "yf_symbol": "^DJI",
        "keywords": ["dow jones", "djia", "dow 30",
                     "dow industrial average", "blue chip stocks"],
        "context": ["index", "market", "rally", "points"],
    },
    "russell2000": {
        "label": "Russell 2000", "icon": "📊", "group": "Indices", "symbol": "RUT",
        "yf_symbol": "^RUT",
        "keywords": ["russell 2000", "small cap index", "rut index"],
        "context": ["index", "market", "rally"],
    },
    "dax": {
        "label": "DAX", "icon": "🇩🇪", "group": "Indices", "symbol": "DAX",
        "yf_symbol": "^GDAXI",
        "keywords": ["dax index", "german stock market", "frankfurt stocks",
                     "german equities"],
        "context": ["index", "market", "rally"],
    },
    "ftse": {
        "label": "FTSE 100", "icon": "🇬🇧", "group": "Indices", "symbol": "UK100",
        "yf_symbol": "^FTSE",
        "keywords": ["ftse 100", "ftse index", "london stock market",
                     "uk equities"],
        "context": ["index", "market", "rally"],
    },
    "nikkei": {
        "label": "Nikkei", "icon": "🗾", "group": "Indices", "symbol": "JP225",
        "yf_symbol": "^N225",
        "keywords": ["nikkei 225", "japan stock market", "tokyo stocks",
                     "topix index"],
        "context": ["index", "market", "rally"],
    },
    "bonds": {
        "label": "US Bonds", "icon": "📜", "group": "Bonds", "symbol": "TNX",
        "yf_symbol": "^TNX",
        "keywords": ["us treasury yield", "10-year yield", "bond market",
                     "fixed income", "bond prices", "yield curve",
                     "rate hike impact", "rate cut impact", "t-bill yield",
                     "bund yield", "gilt yield"],
        "context": ["yield", "market", "prices", "rate"],
    },
    "geopolitics": {
        "label": "Geopolitics", "icon": "🌍", "group": "Macro", "symbol": "VIX",
        "yf_symbol": "^VIX",
        "keywords": ["military conflict", "trade war", "economic sanctions",
                     "nato summit", "ukraine war", "russia sanctions",
                     "iran nuclear", "israel conflict", "middle east war",
                     "taiwan strait", "china tariffs", "tariff escalation",
                     "geopolitical risk"],
        "context": ["risk", "market", "impact", "conflict", "war"],
    },
    "inflation": {
        "label": "Inflation", "icon": "📉", "group": "Macro", "symbol": "CPI",
        "yf_symbol": "^TNX",
        "keywords": ["inflation rate", "cpi data", "ppi data",
                     "consumer price index", "core inflation",
                     "inflation expectations", "deflation risk",
                     "stagflation fears", "price pressures"],
        "context": ["rate", "data", "index", "expectations", "risk"],
    },
    "vix": {
        "label": "VIX / Fear", "icon": "😨", "group": "Macro", "symbol": "VIX",
        "yf_symbol": "^VIX",
        "keywords": ["vix index", "volatility index", "market fear",
                     "fear gauge", "risk-off sentiment", "safe haven demand",
                     "market volatility surge"],
        "context": ["index", "market", "sentiment", "demand"],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL THRESHOLDS — raised to reduce false positives
# ══════════════════════════════════════════════════════════════════════════════

SIGNAL_THRESHOLDS = {
    "STRONG_BUY":  0.65,   # was 0.55 — raised to reduce noise
    "BUY":         0.22,
    "NEUTRAL":    -0.22,
    "SELL":       -0.65,   # was -0.55
}

# ══════════════════════════════════════════════════════════════════════════════
#  LEXICONS — multi-word phrases scored first to avoid partial matches
# ══════════════════════════════════════════════════════════════════════════════

BULLISH_PHRASES = [
    # Multi-word (checked first, higher weight)
    "all-time high", "record high", "52-week high", "beats expectations",
    "exceeds forecast", "strong buy", "upgrade to buy", "price target raised",
    "bullish outlook", "positive surprise", "better than expected",
    "strong demand", "supply shortage", "capacity constraints",
    # Single-word
    "rises", "rise", "surges", "surge", "jumps", "jump", "gains", "gain",
    "rallies", "rally", "climbs", "climb", "strengthens", "strengthen",
    "higher", "outperforms", "beats", "exceeds", "bullish", "optimistic",
    "recovery", "upside", "breakout", "boom", "rebound", "momentum",
    "inflow", "upgrade", "oversold", "hawkish",
]

BEARISH_PHRASES = [
    # Multi-word (higher weight)
    "all-time low", "record low", "52-week low", "misses expectations",
    "below forecast", "strong sell", "downgrade to sell", "price target cut",
    "bearish outlook", "negative surprise", "worse than expected",
    "weak demand", "supply glut", "demand destruction",
    "margin call", "credit event", "default risk",
    # Single-word (only clearly bearish, not generic)
    "falls", "fall", "drops", "drop", "plunges", "plunge", "slides", "slide",
    "declines", "decline", "weakens", "weaken", "lower", "underperforms",
    "misses", "bearish", "pessimistic", "recession", "downside", "breakdown",
    "outflow", "downgrade", "overbought", "dovish",
    "crash", "selloff", "downturn", "slump", "tumble",
]

NEGATION_WORDS = {"not", "no", "never", "without", "neither", "nor",
                  "barely", "hardly", "scarcely", "failed", "failed to"}


# ══════════════════════════════════════════════════════════════════════════════
#  NEGATION DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def _has_negation_nearby(words: list[str], phrase_idx: int, window: int = 3) -> bool:
    """Return True if any negation word appears within `window` tokens of phrase_idx."""
    start = max(0, phrase_idx - window)
    end = min(len(words), phrase_idx + window + 1)
    return any(w in NEGATION_WORDS for w in words[start:end])


def _count_phrases_with_negation(text_lower: str) -> tuple[float, float]:
    """
    Count bullish and bearish phrase hits, subtracting negated occurrences.
    Returns (bullish_score, bearish_score) in range [-N, +N].
    """
    words = re.findall(r'\b\w+\b', text_lower)
    bullish_score = 0.0
    bearish_score = 0.0

    # Multi-word phrases first (score 0.5 each)
    for phrase in BULLISH_PHRASES:
        if " " in phrase and phrase in text_lower:
            idx = text_lower.find(phrase)
            word_idx = len(re.findall(r'\b\w+\b', text_lower[:idx]))
            weight = 0.5
            if _has_negation_nearby(words, word_idx):
                bearish_score += weight  # negated bullish = bearish signal
            else:
                bullish_score += weight

    for phrase in BEARISH_PHRASES:
        if " " in phrase and phrase in text_lower:
            idx = text_lower.find(phrase)
            word_idx = len(re.findall(r'\b\w+\b', text_lower[:idx]))
            weight = 0.5
            if _has_negation_nearby(words, word_idx):
                bullish_score += weight  # negated bearish = bullish signal
            else:
                bearish_score += weight

    # Single-word phrases (score 0.3 each)
    for i, word in enumerate(words):
        if word in BULLISH_PHRASES:
            weight = 0.3
            if _has_negation_nearby(words, i):
                bearish_score += weight
            else:
                bullish_score += weight
        elif word in BEARISH_PHRASES:
            weight = 0.3
            if _has_negation_nearby(words, i):
                bullish_score += weight
            else:
                bearish_score += weight

    return bullish_score, bearish_score


# ══════════════════════════════════════════════════════════════════════════════
#  FINBERT LAYER — graceful fallback to enhanced VADER if unavailable
# ══════════════════════════════════════════════════════════════════════════════

class FinBERTLayer:
    """
    ProsusAI/finbert via shared lazy pipeline (see hf_pipelines.py).
    Loads on first use — avoids duplicate FinBERT RAM and huge boot spike on small hosts.
    """

    def __init__(self):
        self._pipeline_checked = False
        self._pipeline = None

    def _ensure(self) -> None:
        if self._pipeline_checked:
            return
        self._pipeline_checked = True
        self._pipeline = get_finbert_pipeline()

    @property
    def available(self) -> bool:
        self._ensure()
        return self._pipeline is not None

    def analyze(self, text: str) -> Optional[float]:
        """
        Returns score in [-1.0, 1.0], or None if FinBERT is unavailable.
        """
        self._ensure()
        if self._pipeline is None:
            return None
        try:
            trunc = text[:1500]
            results = self._pipeline(trunc)[0]
            label_map = {r["label"].lower(): r["score"] for r in results}
            pos = label_map.get("positive", 0.0)
            neg = label_map.get("negative", 0.0)
            return round(pos - neg, 4)
        except Exception as e:
            logger.warning(f"[FinBERT] Inference error: {e}")
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_signal_label(score: float) -> dict:
    if score >= SIGNAL_THRESHOLDS["STRONG_BUY"]:
        return {"signal": "STRONG BUY",  "signal_color": "#00ff88", "signal_class": "strong-buy"}
    elif score >= SIGNAL_THRESHOLDS["BUY"]:
        return {"signal": "BUY",         "signal_color": "#4ade80", "signal_class": "buy"}
    elif score > SIGNAL_THRESHOLDS["NEUTRAL"]:
        return {"signal": "NEUTRAL",     "signal_color": "#94a3b8", "signal_class": "neutral"}
    elif score > SIGNAL_THRESHOLDS["SELL"]:
        return {"signal": "SELL",        "signal_color": "#f87171", "signal_class": "sell"}
    else:
        return {"signal": "STRONG SELL", "signal_color": "#ff2d55", "signal_class": "strong-sell"}


# ══════════════════════════════════════════════════════════════════════════════
#  ML LAYER — improved model confidence from agreement
# ══════════════════════════════════════════════════════════════════════════════

def _compute_confidence(scores: list[float]) -> float:
    """
    Confidence derived from inter-model agreement (low variance = high confidence).
    scores: list of individual model outputs in [-1, 1]
    Returns 0–100.
    """
    if len(scores) < 2:
        return 50.0
    arr = np.array(scores)
    # Agreement = 1 - normalized std
    # std in [-1,1] space: max possible std ~ 1.0
    std = float(np.std(arr))
    agreement = max(0.0, 1.0 - std)  # 1.0 = perfect agreement, 0.0 = max disagreement
    # Weight by average magnitude (strong signals with agreement = more confidence)
    magnitude = float(np.mean(np.abs(arr)))
    # Return 0.0–1.0 so the frontend can do `conf * 100` for display
    confidence = (0.7 * agreement + 0.3 * magnitude)
    return round(min(1.0, max(0.0, confidence)), 4)


# ══════════════════════════════════════════════════════════════════════════════
#  LLM LAYER — Claude as context-aware adjudicator
# ══════════════════════════════════════════════════════════════════════════════

def llm_analyze(title: str, description: str,
                vader: float, textblob: float, keyword_bull: float,
                keyword_bear: float, finbert: Optional[float] = None) -> dict:
    """
    Calls Claude with FULL ensemble context so it can adjudicate disagreements.
    Only fires if ANTHROPIC_API_KEY is set.
    """
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return {"score": 0.0, "reasoning": "", "key_signal": "", "used": False}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)

        finbert_line = (f"- FinBERT score: {finbert:+.3f}\n" if finbert is not None
                        else "- FinBERT: not available\n")

        prompt = f"""You are a senior financial analyst with CFA certification. \
Analyze the market sentiment of this news article for trading signal generation.

HEADLINE: {title}
SUMMARY: {description[:400]}

PRELIMINARY MODEL SCORES (your job is to adjudicate these):
- VADER (augmented lexicon): {vader:+.3f}
- TextBlob polarity: {textblob:+.3f}
- Keyword bull phrases: {keyword_bull:.1f}, bear phrases: {keyword_bear:.1f} net: {keyword_bull - keyword_bear:+.1f}
{finbert_line}
Note: Scores are in [-1.0, 1.0]. Models disagree when signs differ or magnitudes vary widely.
Your job: give the CORRECT financial sentiment score accounting for context the models miss \
(e.g. sarcasm, forward-looking statements, revision context, market already pricing this in).

Respond ONLY with this exact JSON (no markdown, no preamble):
{{"score": <float -1.0 to 1.0>, "reasoning": "<1 concise sentence>", "key_signal": "<key phrase from headline>", "adjudication": "<agree|override_bullish|override_bearish>"}}"""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = re.sub(r"```(?:json)?|```", "", msg.content[0].text).strip()
        r = json.loads(raw)
        return {
            "score": max(-1.0, min(1.0, float(r.get("score", 0.0)))),
            "reasoning": r.get("reasoning", ""),
            "key_signal": r.get("key_signal", ""),
            "adjudication": r.get("adjudication", "agree"),
            "used": True,
        }
    except Exception as e:
        logger.warning(f"[LLM] Error: {e}")
        return {"score": 0.0, "reasoning": "", "key_signal": "", "used": False}


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

class FinancialSentimentAnalyzer:
    """
    5-layer ensemble sentiment analyzer for financial news.
    Thread-safe singleton — use the module-level `analyzer` instance.
    """

    def __init__(self):
        self.vader = SentimentIntensityAnalyzer()
        self._augment_vader()
        self.finbert = FinBERTLayer()
        self._llm_on = bool(os.getenv("ANTHROPIC_API_KEY"))
        logger.info(
            "[Analyzer] FinBERT=lazy-shared (loads on first sentiment job) LLM=%s",
            "ON" if self._llm_on else "OFF",
        )

    def _augment_vader(self):
        """Add financial domain terms to VADER's lexicon."""
        self.vader.lexicon.update({
            "bullish": 3.2, "rally": 2.8, "surge": 3.0, "soar": 3.2,
            "breakout": 2.4, "outperform": 2.6, "upgrade": 2.2,
            "recovery": 2.2, "rebound": 2.4, "upside": 1.9, "hawkish": 2.0,
            "inflow": 1.8, "momentum": 1.6, "resilient": 1.8, "robust": 1.6,
            "undervalued": 1.4, "oversold": 1.4, "support": 1.2,
            "beat": 1.8, "exceeds": 1.6, "topline": 1.2,
            "bearish": -3.2, "plunge": -3.2, "crash": -3.6, "recession": -3.2,
            "selloff": -3.0, "breakdown": -2.6, "downgrade": -2.2, "dovish": -1.6,
            "stagflation": -3.0, "default": -3.2, "downside": -1.9,
            "conflict": -2.2, "war": -2.8, "sanctions": -2.2,
            "slump": -2.2, "tumble": -2.6, "outflow": -1.8, "overbought": -1.4,
            "vulnerable": -1.4, "overvalued": -1.4,
            "stable": 1.2, "steadies": 1.4, "consolidates": 0.4,
        })

    def analyze(self, title: str, description: str = "",
                use_llm: bool = False) -> dict:
        """
        Full 5-layer analysis. Returns a rich sentiment dict.

        Parameters
        ----------
        title : str
            Article headline.
        description : str
            Article body / summary.
        use_llm : bool
            Whether to call Claude (adds ~1–2 sec latency).
        """
        text = f"{title}. {description}".strip()
        text_lower = text.lower()

        # ── Layer 1: VADER ─────────────────────────────────────────────
        vader_score = float(self.vader.polarity_scores(text)["compound"])

        # ── Layer 2: TextBlob ──────────────────────────────────────────
        tb = TextBlob(text)
        tb_score = float(tb.sentiment.polarity)
        # Weight TextBlob by its own subjectivity (more subjective = less reliable)
        tb_weighted = tb_score * (1.0 - 0.4 * float(tb.sentiment.subjectivity))

        # ── Layer 3: Keyword scorer with negation guard ────────────────
        bull_score, bear_score = _count_phrases_with_negation(text_lower)
        net_kw = bull_score - bear_score
        custom_score = max(-1.0, min(1.0, net_kw * 0.25))

        # ── Layer 4: FinBERT ───────────────────────────────────────────
        finbert_score = self.finbert.analyze(text)

        # ── Layer 5: LLM (optional) ────────────────────────────────────
        llm_result = {"score": 0.0, "reasoning": "", "key_signal": "",
                      "adjudication": "", "used": False}
        if use_llm and self._llm_on:
            llm_result = llm_analyze(
                title, description,
                vader=vader_score, textblob=tb_weighted,
                keyword_bull=bull_score, keyword_bear=bear_score,
                finbert=finbert_score,
            )

        # ── Weighted ensemble ──────────────────────────────────────────
        scores_for_ensemble = [vader_score, tb_weighted, custom_score]
        layer_weights = [0.28, 0.12, 0.22]

        if finbert_score is not None:
            scores_for_ensemble.append(finbert_score)
            layer_weights.append(0.30)
            # Reduce VADER weight when FinBERT available
            layer_weights[0] = 0.18
            layer_weights[2] = 0.12

        if llm_result["used"]:
            scores_for_ensemble.append(llm_result["score"])
            layer_weights.append(0.08)

        # Normalize weights
        total_w = sum(layer_weights)
        weights = [w / total_w for w in layer_weights]
        final_score = sum(s * w for s, w in zip(scores_for_ensemble, weights))
        final_score = round(max(-1.0, min(1.0, final_score)), 4)

        # ── Confidence from model agreement ───────────────────────────
        confidence = _compute_confidence(scores_for_ensemble)

        # ── Label ─────────────────────────────────────────────────────
        if final_score >= 0.10:
            label, color = "bullish", "green"
        elif final_score <= -0.10:
            label, color = "bearish", "red"
        else:
            label, color = "neutral", "gray"

        sig = get_signal_label(final_score)

        # ── Asset detection ────────────────────────────────────────────
        assets = self._detect_assets(text_lower)

        return {
            "score": final_score,
            "label": label,
            "color": color,
            "confidence": confidence,
            "signal": sig["signal"],
            "signal_color": sig["signal_color"],
            "signal_class": sig["signal_class"],
            "assets": assets,
            # Per-layer breakdown
            "vader": round(vader_score, 4),
            "textblob": round(tb_weighted, 4),
            "custom": round(custom_score, 4),
            "finbert": round(finbert_score, 4) if finbert_score is not None else None,
            "finbert_available": self.finbert.available,
            "llm_score": round(llm_result["score"], 4),
            "llm_reasoning": llm_result.get("reasoning", ""),
            "llm_key_signal": llm_result.get("key_signal", ""),
            "llm_adjudication": llm_result.get("adjudication", ""),
            "bullish_phrases": int(bull_score / 0.3),
            "bearish_phrases": int(bear_score / 0.3),
            "ml_adj": 0.0,  # kept for DB backwards compatibility
        }

    def _detect_assets(self, text_lower: str) -> list[dict]:
        """
        Context-aware asset detection.
        For each asset: at least one keyword must match AND at least one context
        word must appear within the same sentence/paragraph.
        """
        out = []
        sentences = re.split(r'[.!?\n]', text_lower)

        for key, info in ASSET_KEYWORDS.items():
            matched_sentences = []
            for sent in sentences:
                kw_hit = any(kw in sent for kw in info["keywords"])
                if not kw_hit:
                    continue
                ctx_hit = any(c in sent for c in info.get("context", []))
                # For well-defined multi-word keywords, context is implied
                multi_kw_hit = any(kw in sent for kw in info["keywords"] if " " in kw)
                if ctx_hit or multi_kw_hit:
                    matched_sentences.append(sent)

            if not matched_sentences:
                continue

            asset_text = " ".join(matched_sentences)
            asset_sentiment = self._sentence_level_sentiment(asset_text)
            sig = get_signal_label(asset_sentiment["score"])

            out.append({
                "key": key,
                "label": info["label"],
                "icon": info["icon"],
                "group": info["group"],
                "symbol": info["symbol"],
                "sentiment": asset_sentiment["label"],
                "score": asset_sentiment["score"],
                "color": asset_sentiment["color"],
                "signal": sig["signal"],
                "signal_class": sig["signal_class"],
                "signal_color": sig["signal_color"],
            })
        return out

    def _sentence_level_sentiment(self, text: str) -> dict:
        """Quick sentiment for an asset-relevant sentence fragment."""
        bull, bear = _count_phrases_with_negation(text)
        v = float(self.vader.polarity_scores(text)["compound"])
        kw_score = max(-1.0, min(1.0, (bull - bear) * 0.25))
        combined = v * 0.6 + kw_score * 0.4
        if combined >= 0.08:
            return {"label": "Bullish", "color": "green", "score": round(combined, 4)}
        elif combined <= -0.08:
            return {"label": "Bearish", "color": "red",  "score": round(combined, 4)}
        return {"label": "Neutral", "color": "gray", "score": round(combined, 4)}


# Module-level singleton
analyzer = FinancialSentimentAnalyzer()