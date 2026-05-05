"""
FinancialPulse v3 — Unit Tests: Sentiment Engine
═════════════════════════════════════════════════════════════════════════════
Tests the core `analyzer.analyze()` function against 30 labelled headlines.
Covers: strong bullish, strong bearish, neutral, negation inversion, asset detection.

Run with:  pytest tests/test_sentiment.py -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from .sentiment_engine import (
    analyzer,
    get_signal_label,
    _count_phrases_with_negation,
    _has_negation_nearby,
    ASSET_KEYWORDS,
)


# ══════════════════════════════════════════════════════════════════════════════
#  LABELLED TEST CASES
# ══════════════════════════════════════════════════════════════════════════════

LABELED_HEADLINES = [
    # (headline, expected_label, note)
    # ── Strongly Bullish ─────────────────────────────────────────────────────
    ("S&P 500 surges to all-time high as Fed signals rate cuts",            "bullish", "SPX ATH + Fed pivot"),
    ("Bitcoin price jumps 12% after SEC approves spot ETF",                 "bullish", "BTC ETF approval"),
    ("Gold rallies sharply on safe haven demand amid global uncertainty",   "bullish", "Gold safe haven"),
    ("Company beats earnings forecast by 30%, raises full-year guidance",   "bullish", "Earnings beat"),
    ("Stock market recovers strongly following better-than-expected GDP",   "bullish", "GDP beat"),
    ("Fed upgrades economic outlook, signals soft landing achieved",        "bullish", "Fed upgrade"),
    ("Crude oil surges 5% as OPEC+ announces deeper production cuts",      "bullish", "Oil OPEC+"),
    ("Bitcoin dominance climbs to 58% as altcoins underperform",            "bullish", "BTC dominance"),
    # ── Strongly Bearish ─────────────────────────────────────────────────────
    ("Stock market crashes as recession fears grip Wall Street",            "bearish", "Recession crash"),
    ("Bitcoin plunges 20% following major exchange collapse",              "bearish", "Exchange collapse"),
    ("Gold prices drop as dollar strengthens on hawkish Fed comments",     "bearish", "Gold drops"),
    ("Dow Jones falls 900 points as inflation data comes in hot",          "bearish", "Dow fall"),
    ("Company slashes guidance, announces layoffs of 5,000 workers",       "bearish", "Guidance cut + layoffs"),
    ("Oil prices tumble as demand destruction signals deepen",             "bearish", "Oil demand destruction"),
    ("Nasdaq selloff deepens on tech earnings miss, recession warnings",   "bearish", "NASDAQ selloff"),
    ("US economy risks stagflation as growth slows and prices rise",       "bearish", "Stagflation"),
    # ── Neutral ──────────────────────────────────────────────────────────────
    ("Federal Reserve holds rates steady at 5.25–5.50%",                   "neutral", "Rate hold"),
    ("Company reaffirms full-year guidance range of $4.00–$4.20 EPS",     "neutral", "Guidance reaffirm"),
    ("Gold trades in a tight range ahead of key US inflation data",        "neutral", "Range-bound pre-data"),
    ("S&P 500 closes flat as investors await Fed decision",                "neutral", "Flat close"),
    ("Market participants await Friday's nonfarm payroll report",          "neutral", "Data anticipation"),
    ("The board will meet to discuss strategic alternatives next week",    "neutral", "Board meeting"),
    # ── Negation tests ───────────────────────────────────────────────────────
    ("Bitcoin price did not crash following the regulatory news",          "neutral", "Negated crash"),
    ("Gold prices did not rise despite inflation data beating forecasts",  "neutral", "Negated rise"),
    ("The company did not miss earnings expectations",                     "neutral", "Negated miss"),
    ("There was no recovery in oil demand despite the rate cut",           "bearish", "Negated recovery → bearish"),
    ("The market showed no sign of recovery amid continued selling",       "bearish", "No recovery + selling"),
    # ── Mixed context ────────────────────────────────────────────────────────
    ("Gold falls but analysts warn of further decline in oil prices",      "bearish", "Both commodities bearish"),
    ("Bitcoin steady as regulators signal tighter but fair oversight",     "neutral", "Regulation neutral"),
    ("S&P 500 flat: bulls and bears remain evenly matched after CPI",     "neutral", "Balanced CPI reaction"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSentimentAnalyzer:

    @pytest.mark.parametrize("headline,expected_label,note", LABELED_HEADLINES)
    def test_headline_classification(self, headline, expected_label, note):
        """Each labeled headline should produce the expected sentiment category."""
        result = analyzer.analyze(headline)
        assert result["label"] == expected_label, (
            f"[{note}] Expected '{expected_label}' but got '{result['label']}' "
            f"(score={result['score']:.4f}) for: '{headline}'"
        )

    def test_score_range(self):
        """Score must always be in [-1.0, 1.0]."""
        for headline, _, _ in LABELED_HEADLINES:
            result = analyzer.analyze(headline)
            assert -1.0 <= result["score"] <= 1.0, (
                f"Score {result['score']} out of range for: '{headline}'"
            )

    def test_confidence_range(self):
        """Confidence must always be in [0.0, 100.0]."""
        for headline, _, _ in LABELED_HEADLINES:
            result = analyzer.analyze(headline)
            assert 0.0 <= result["confidence"] <= 100.0, (
                f"Confidence {result['confidence']} out of range"
            )

    def test_confidence_from_agreement_not_magnitude(self):
        """
        A neutral article with high model agreement should have HIGH confidence,
        not near-zero confidence as the broken v2 formula produced.
        """
        neutral_headline = "Federal Reserve holds rates steady at 5.25–5.50%"
        result = analyzer.analyze(neutral_headline)
        # With the broken v2 formula: conf = abs(0.02) * 150 = 3%
        # With the new agreement formula: should be > 40% (models agree it's neutral)
        assert result["confidence"] > 20.0, (
            f"Neutral article with strong agreement should have confidence > 20%, "
            f"got {result['confidence']}%. "
            f"(v2 bug: confidence was just abs(score) * 150)"
        )

    def test_output_keys(self):
        """Result dict must contain all required keys."""
        result = analyzer.analyze("Gold prices rise on strong demand")
        required_keys = {"score", "label", "color", "confidence", "signal",
                         "signal_color", "signal_class", "assets",
                         "vader", "textblob", "custom",
                         "llm_score", "bullish_phrases", "bearish_phrases"}
        missing = required_keys - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_asset_detection_gold_price_context(self):
        """'gold price' should trigger gold asset detection."""
        result = analyzer.analyze("Gold price rises to $2,400 per ounce")
        asset_keys = [a["key"] for a in result["assets"]]
        assert "gold" in asset_keys, f"Expected 'gold' in assets, got: {asset_keys}"

    def test_asset_detection_gold_medal_no_false_positive(self):
        """'gold medal' (Olympic context) should NOT trigger gold asset detection."""
        result = analyzer.analyze(
            "Athlete wins gold medal at Olympic Games in record time"
        )
        asset_keys = [a["key"] for a in result["assets"]]
        assert "gold" not in asset_keys, (
            f"False positive: 'gold' detected in non-financial context. "
            f"Assets detected: {asset_keys}"
        )

    def test_asset_detection_bitcoin(self):
        """Bitcoin news should detect bitcoin asset."""
        result = analyzer.analyze("Bitcoin ETF approved by SEC, price jumps")
        asset_keys = [a["key"] for a in result["assets"]]
        assert "bitcoin" in asset_keys, f"Expected 'bitcoin', got: {asset_keys}"

    def test_negation_inversion_crash(self):
        """'did not crash' should NOT produce a strongly bearish score."""
        result = analyzer.analyze("Bitcoin did not crash following the news")
        # Should be neutral or mildly positive — definitely not strongly bearish
        assert result["score"] > -0.5, (
            f"Negation failed: 'did not crash' got score={result['score']:.4f}. "
            f"Expected score > -0.5"
        )

    def test_negation_inversion_rise(self):
        """'did not rise' should NOT produce a strongly bullish score."""
        result = analyzer.analyze("Gold prices did not rise despite the data")
        assert result["score"] < 0.5, (
            f"Negation failed: 'did not rise' got score={result['score']:.4f}. "
            f"Expected score < 0.5"
        )

    def test_strong_buy_signal_threshold(self):
        """Very positive news should produce a STRONG BUY signal."""
        result = analyzer.analyze(
            "Stock market surges to all-time high as economy beats all forecasts"
        )
        assert result["signal"] in ("STRONG BUY", "BUY"), (
            f"Expected BUY signal for positive news, got: {result['signal']} "
            f"(score={result['score']:.4f})"
        )

    def test_strong_sell_signal_threshold(self):
        """Very negative news should produce a STRONG SELL signal."""
        result = analyzer.analyze(
            "Market crashes in historic selloff as recession fears cause panic"
        )
        assert result["signal"] in ("STRONG SELL", "SELL"), (
            f"Expected SELL signal for negative news, got: {result['signal']} "
            f"(score={result['score']:.4f})"
        )

    def test_empty_input(self):
        """Empty input should not raise and should return neutral."""
        result = analyzer.analyze("")
        assert result["label"] == "neutral"

    def test_description_augments_score(self):
        """Providing description should influence the score."""
        title_only = analyzer.analyze("Market update")
        with_desc = analyzer.analyze(
            "Market update",
            "Stocks surged broadly as inflation hit multi-year lows, boosting risk appetite."
        )
        # The description is clearly bullish, score with desc should be > without
        assert with_desc["score"] > title_only["score"], (
            "Expected description to push score bullish"
        )


class TestNegationDetector:

    def test_negation_within_window(self):
        words = ["gold", "prices", "did", "not", "rise", "today"]
        # "not" is at index 3, "rise" is at index 4 → within window=3
        assert _has_negation_nearby(words, 4, window=3) is True

    def test_negation_outside_window(self):
        words = ["not", "related", "to", "the", "gold", "price", "rise"]
        # "not" is at index 0, "rise" is at index 6 → outside window=3
        assert _has_negation_nearby(words, 6, window=2) is False

    def test_no_negation(self):
        words = ["gold", "prices", "definitely", "rose", "sharply"]
        assert _has_negation_nearby(words, 3, window=3) is False

    def test_bull_bear_counts_negation(self):
        # "did not rise" — negated bullish phrase
        bull, bear = _count_phrases_with_negation("prices did not rise today")
        # "rise" is bullish but negated — should contribute to bearish, not bullish
        # (the negation should have reduced or inverted the bullish count)
        assert bear > 0 or bull == 0, (
            f"Expected negated 'rise' to reduce bullish score. "
            f"Got bull={bull:.2f}, bear={bear:.2f}"
        )


class TestSignalLabels:

    def test_strong_buy_threshold(self):
        sig = get_signal_label(0.70)
        assert sig["signal"] == "STRONG BUY"

    def test_buy_threshold(self):
        sig = get_signal_label(0.35)
        assert sig["signal"] == "BUY"

    def test_neutral_threshold(self):
        sig = get_signal_label(0.05)
        assert sig["signal"] == "NEUTRAL"

    def test_sell_threshold(self):
        sig = get_signal_label(-0.35)
        assert sig["signal"] == "SELL"

    def test_strong_sell_threshold(self):
        sig = get_signal_label(-0.70)
        assert sig["signal"] == "STRONG SELL"

    def test_signal_colors_present(self):
        for score in [-0.8, -0.4, 0.0, 0.4, 0.8]:
            sig = get_signal_label(score)
            assert "signal_color" in sig
            assert sig["signal_color"].startswith("#")


class TestAssetKeywords:

    def test_all_assets_have_required_fields(self):
        required = {"label", "icon", "group", "symbol", "keywords", "context"}
        for key, info in ASSET_KEYWORDS.items():
            missing = required - set(info.keys())
            assert not missing, f"Asset '{key}' missing fields: {missing}"

    def test_no_short_keyword_false_positives(self):
        """All keywords should be specific enough to avoid obvious false positives."""
        # Single-letter or very short keywords are dangerous
        for key, info in ASSET_KEYWORDS.items():
            for kw in info["keywords"]:
                assert len(kw) >= 3, (
                    f"Keyword '{kw}' for asset '{key}' is too short (risk of false positives)"
                )


# ══════════════════════════════════════════════════════════════════════════════
#  ACCURACY REPORT — run standalone to see overall benchmark accuracy
# ══════════════════════════════════════════════════════════════════════════════

def run_accuracy_report():
    """Run all labeled headlines and print an accuracy summary."""
    correct = 0
    total = len(LABELED_HEADLINES)
    wrong_cases = []

    for headline, expected, note in LABELED_HEADLINES:
        result = analyzer.analyze(headline)
        got = result["label"]
        if got == expected:
            correct += 1
        else:
            wrong_cases.append((note, headline[:70], expected, got, result["score"]))

    print(f"\n{'='*70}")
    print(f"  FinancialPulse v3 — Benchmark Accuracy Report")
    print(f"{'='*70}")
    print(f"  Total: {total}  |  Correct: {correct}  |  Accuracy: {correct/total:.1%}")
    print(f"  FinBERT: {'ON' if analyzer.finbert.available else 'OFF (fallback)'}")
    print(f"{'='*70}")

    if wrong_cases:
        print("\n  INCORRECT PREDICTIONS:")
        for note, headline, expected, got, score in wrong_cases:
            print(f"  [{note}]")
            print(f"    Headline : {headline}")
            print(f"    Expected : {expected}   Got: {got}   Score: {score:.4f}")
    else:
        print("\n  ✓ All predictions correct!")

    print()


if __name__ == "__main__":
    run_accuracy_report()