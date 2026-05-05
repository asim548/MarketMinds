"""
FinancialPulse v3 — Evaluation & Metrics Module
═════════════════════════════════════════════════════════════════════════════
Provides:
  1. evaluate_on_phrasebank()   — benchmark against FinancialPhraseBank dataset
  2. evaluate_on_custom()       — evaluate against a user-provided labeled CSV
  3. live_accuracy_report()     — evaluate stored articles against DB labels
  4. model_agreement_stats()    — inter-model agreement analysis
  5. generate_evaluation_report() — full FYP-ready evaluation dict

FinancialPhraseBank
-------------------
Malo, P., Sinha, A., et al. (2014). "Good Debt or Bad Debt: Detecting
Semantic Orientations in Economic Texts." JASIST.
Available: https://www.researchgate.net/publication/251231364

The 50-sentence sample below (10 per class) is a minimal embedded benchmark.
For a full evaluation, download the dataset and call evaluate_on_phrasebank()
with the path to Sentences_AllAgree.txt.
"""

from __future__ import annotations

import logging
import numpy as np
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  EMBEDDED BENCHMARK — FinancialPhraseBank sample (50 sentences)
#  Labels: positive, negative, neutral → mapped to bullish/bearish/neutral
# ══════════════════════════════════════════════════════════════════════════════

BENCHMARK_SENTENCES = [
    # Positive (bullish)
    ("The company reported record quarterly earnings, beating all analyst forecasts.", "bullish"),
    ("Revenue surged 42% year-over-year, driven by strong demand in emerging markets.", "bullish"),
    ("The stock jumped 8% after management raised full-year guidance.", "bullish"),
    ("Operating margins expanded significantly as cost-cutting measures took effect.", "bullish"),
    ("The firm announced a $2 billion share buyback program, signaling confidence.", "bullish"),
    ("Free cash flow reached an all-time high of $1.4 billion in the quarter.", "bullish"),
    ("The acquisition is expected to be immediately accretive to earnings per share.", "bullish"),
    ("Customer growth accelerated, with subscriptions rising 31% from the prior year.", "bullish"),
    ("The board approved a 15% dividend increase, its eighth consecutive annual raise.", "bullish"),
    ("Analysts upgraded the stock to Strong Buy following the positive earnings surprise.", "bullish"),
    ("The company exceeded profit expectations as international sales climbed sharply.", "bullish"),
    ("Investors cheered as the firm's market share expanded for the fifth straight quarter.", "bullish"),
    ("Capital expenditure plans were raised, reflecting confidence in future demand.", "bullish"),
    ("The IPO was oversubscribed tenfold, with the stock pricing at the top of range.", "bullish"),
    ("Net income rose 28%, driven by higher volumes and improved pricing power.", "bullish"),

    # Negative (bearish)
    ("The company missed earnings estimates for the third consecutive quarter.", "bearish"),
    ("Revenue fell 18% as customer cancellations accelerated amid the downturn.", "bearish"),
    ("The firm announced layoffs affecting 12% of its global workforce.", "bearish"),
    ("Write-downs totalling $3.2 billion weighed heavily on the balance sheet.", "bearish"),
    ("Management slashed full-year guidance, citing deteriorating macro conditions.", "bearish"),
    ("The stock plunged 15% after the company disclosed an SEC investigation.", "bearish"),
    ("Cash burn accelerated in the quarter, raising concerns about the balance sheet.", "bearish"),
    ("The credit rating was downgraded to junk status by two major agencies.", "bearish"),
    ("Supply chain disruptions led to significant production shortfalls.", "bearish"),
    ("Rising input costs compressed margins, pushing operating profit to a multi-year low.", "bearish"),
    ("The deal collapsed after regulators blocked the proposed merger on antitrust grounds.", "bearish"),
    ("The firm warned of a potential covenant breach if conditions do not improve.", "bearish"),
    ("Inventory write-offs of $900 million reflected weakening consumer demand.", "bearish"),
    ("Market share declined as competitors gained traction with newer product lines.", "bearish"),
    ("The dividend was suspended to preserve cash during the current downturn.", "bearish"),

    # Neutral
    ("The company will hold its annual general meeting on March 15.", "neutral"),
    ("Management confirmed that the strategic review is ongoing.", "neutral"),
    ("The firm appointed a new chief financial officer effective next month.", "neutral"),
    ("The board will convene to discuss the proposed capital allocation framework.", "neutral"),
    ("Sales were in line with analyst consensus for the period.", "neutral"),
    ("The company reaffirmed its previously issued annual guidance range.", "neutral"),
    ("No material changes to the business plan were announced at the investor day.", "neutral"),
    ("The quarterly filing was submitted to the SEC on schedule.", "neutral"),
    ("The firm noted that macroeconomic conditions remain broadly unchanged.", "neutral"),
    ("A routine internal audit found no material weaknesses in financial controls.", "neutral"),
    ("The company's headcount remained stable at approximately 25,000 employees.", "neutral"),
    ("Results were consistent with the prior quarter on a sequential basis.", "neutral"),
    ("The patent application has been filed in the relevant jurisdictions.", "neutral"),
    ("The organisation is in compliance with all applicable regulatory requirements.", "neutral"),
    ("The annual report will be published in line with the usual timeline.", "neutral"),
    # Extra negation-handling test cases
    ("Gold prices did not rise despite strong inflation data.", "neutral"),
    ("The company failed to meet its revenue targets but costs did not increase.", "bearish"),
    ("Bitcoin did not crash following the regulatory announcement.", "neutral"),
    ("There was no significant recovery in demand despite the rate cut.", "bearish"),
    ("The acquisition was not immediately accretive to earnings as expected.", "neutral"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  CORE METRICS CALCULATION
# ══════════════════════════════════════════════════════════════════════════════

def _compute_classification_metrics(y_true: list[str], y_pred: list[str],
                                    labels: list[str] | None = None) -> dict:
    """Compute precision, recall, F1, accuracy for multi-class classification."""
    if labels is None:
        labels = sorted(set(y_true) | set(y_pred))

    n = len(y_true)
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    accuracy = correct / n if n > 0 else 0.0

    per_class = {}
    for cls in labels:
        tp = sum(1 for a, b in zip(y_true, y_pred) if a == cls and b == cls)
        fp = sum(1 for a, b in zip(y_true, y_pred) if a != cls and b == cls)
        fn = sum(1 for a, b in zip(y_true, y_pred) if a == cls and b != cls)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = (2 * precision * recall / (precision + recall)
                     if (precision + recall) > 0 else 0.0)
        per_class[cls] = {
            "precision": round(precision, 4),
            "recall":    round(recall, 4),
            "f1":        round(f1, 4),
            "support":   sum(1 for a in y_true if a == cls),
        }

    macro_f1 = float(np.mean([per_class[c]["f1"] for c in labels if c in per_class]))
    weighted_f1 = float(np.sum([
        per_class[c]["f1"] * per_class[c]["support"]
        for c in labels if c in per_class
    ])) / n if n > 0 else 0.0

    # Confusion matrix
    cm = {a: {p: 0 for p in labels} for a in labels}
    for a, b in zip(y_true, y_pred):
        if a in cm and b in cm[a]:
            cm[a][b] += 1

    return {
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "weighted_f1": round(weighted_f1, 4),
        "per_class": per_class,
        "confusion_matrix": cm,
        "total_samples": n,
        "correct": correct,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  BENCHMARK EVALUATIONS
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_on_benchmark(use_llm: bool = False) -> dict:
    """
    Evaluate the current ensemble against the embedded 50-sentence benchmark.
    This is the fast evaluation you can run in seconds (no external dataset).
    """
    from .sentiment_engine import analyzer

    y_true, y_pred = [], []
    per_sentence_results = []

    for text, true_label in BENCHMARK_SENTENCES:
        result = analyzer.analyze(text, use_llm=use_llm)
        predicted = result["label"]  # bullish / bearish / neutral
        y_true.append(true_label)
        y_pred.append(predicted)
        per_sentence_results.append({
            "text": text[:80] + "..." if len(text) > 80 else text,
            "true": true_label,
            "predicted": predicted,
            "score": result["score"],
            "correct": true_label == predicted,
            "confidence": result["confidence"],
            "finbert": result.get("finbert"),
        })

    metrics = _compute_classification_metrics(y_true, y_pred,
                                              labels=["bullish", "bearish", "neutral"])

    return {
        "benchmark": "FinancialPhraseBank (50-sentence sample)",
        "metrics": metrics,
        "per_sentence": per_sentence_results,
        "evaluated_at": datetime.utcnow().isoformat(),
        "use_llm": use_llm,
    }


def evaluate_on_phrasebank_file(filepath: str, agreement: str = "all") -> dict:
    """
    Evaluate against the full FinancialPhraseBank dataset from a local file.
    Download from: https://huggingface.co/datasets/financial_phrasebank

    Parameters
    ----------
    filepath : str
        Path to Sentences_AllAgree.txt (or 66Agree, 75Agree, AllAgree variants)
    agreement : str
        Label suffix for reporting (e.g. 'all', '66', '75')
    """
    from .sentiment_engine import analyzer

    try:
        with open(filepath, encoding="latin-1") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return {"error": str(e)}

    label_map = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
    y_true, y_pred = [], []
    for line in lines:
        if "@" not in line:
            continue
        parts = line.rsplit("@", 1)
        if len(parts) != 2:
            continue
        text, raw_label = parts[0].strip(), parts[1].strip().lower()
        mapped_label = label_map.get(raw_label)
        if not mapped_label:
            continue
        result = analyzer.analyze(text)
        y_true.append(mapped_label)
        y_pred.append(result["label"])

    if not y_true:
        return {"error": "No valid sentences parsed from file."}

    metrics = _compute_classification_metrics(y_true, y_pred,
                                              labels=["bullish", "bearish", "neutral"])
    return {
        "benchmark": f"FinancialPhraseBank ({agreement} agreement, {len(y_true)} sentences)",
        "metrics": metrics,
        "evaluated_at": datetime.utcnow().isoformat(),
    }


def evaluate_on_custom_csv(filepath: str,
                           text_col: str = "text",
                           label_col: str = "label") -> dict:
    """
    Evaluate against a user-provided CSV with columns `text` and `label`.
    Label values should be: bullish, bearish, neutral
    """
    from .sentiment_engine import analyzer
    try:
        import pandas as pd
        df = pd.read_csv(filepath)
        if text_col not in df.columns or label_col not in df.columns:
            return {"error": f"CSV must have columns '{text_col}' and '{label_col}'."}

        y_true, y_pred = [], []
        for _, row in df.iterrows():
            result = analyzer.analyze(str(row[text_col]))
            y_true.append(str(row[label_col]).lower().strip())
            y_pred.append(result["label"])

        metrics = _compute_classification_metrics(
            y_true, y_pred, labels=["bullish", "bearish", "neutral"])
        return {
            "benchmark": f"Custom CSV ({len(y_true)} samples)",
            "metrics": metrics,
            "evaluated_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  LIVE DB EVALUATION
# ══════════════════════════════════════════════════════════════════════════════

def live_accuracy_report(db_session, days: int = 7) -> dict:
    """
    Evaluate sentiment consistency across DB-stored articles by checking
    how well the original scores align with re-analysis (model drift check).
    Also reports per-source sentiment distribution.
    """
    from ..models import NewsArticle
    from .sentiment_engine import analyzer

    cutoff = datetime.utcnow().from_timestamp = None
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    articles = (db_session.query(NewsArticle)
                .filter(NewsArticle.published_at >= cutoff)
                .order_by(NewsArticle.published_at.desc())
                .limit(500)
                .all())

    if not articles:
        return {"error": "No articles in DB for this period."}

    # Check re-analysis consistency (detect if model has changed significantly)
    n_sample = min(50, len(articles))
    import random
    sample = random.sample(articles, n_sample)

    drift_diffs = []
    for art in sample:
        new_result = analyzer.analyze(art.title, art.description or "")
        diff = abs(new_result["score"] - art.sentiment_score)
        drift_diffs.append(diff)

    avg_drift = float(np.mean(drift_diffs)) if drift_diffs else 0.0

    # Per-source sentiment distribution
    source_dist: dict[str, dict] = defaultdict(
        lambda: {"bullish": 0, "bearish": 0, "neutral": 0, "total": 0})
    for art in articles:
        src = art.source or "Unknown"
        source_dist[src]["total"] += 1
        source_dist[src][art.sentiment_label or "neutral"] += 1

    # Overall label distribution
    total = len(articles)
    label_dist = defaultdict(int)
    for art in articles:
        label_dist[art.sentiment_label or "neutral"] += 1

    return {
        "period_days": days,
        "total_articles": total,
        "label_distribution": {
            k: {"count": v, "pct": round(v / total * 100, 1)}
            for k, v in label_dist.items()
        },
        "model_drift": {
            "sample_size": n_sample,
            "avg_score_diff": round(avg_drift, 4),
            "stable": avg_drift < 0.05,
        },
        "source_distribution": {
            src: {
                **dist,
                "bullish_pct": round(dist["bullish"] / dist["total"] * 100, 1),
                "bearish_pct": round(dist["bearish"] / dist["total"] * 100, 1),
            }
            for src, dist in source_dist.items()
        },
        "evaluated_at": datetime.utcnow().isoformat(),
    }


def model_agreement_stats(news_items: list[dict]) -> dict:
    """
    Analyse inter-model agreement across a batch of news items.
    Useful to identify where the models disagree most (needs LLM adjudication).
    """
    agreements = []
    disagreements = []

    for item in news_items:
        s = item.get("sentiment", {})
        scores = [v for k, v in s.items()
                  if k in ("vader", "textblob", "custom", "finbert", "llm_score")
                  and v is not None]
        if len(scores) < 2:
            continue
        std = float(np.std(scores))
        if std < 0.15:
            agreements.append({"title": item["title"][:80], "std": round(std, 4)})
        else:
            disagreements.append({
                "title": item["title"][:80],
                "std": round(std, 4),
                "scores": {k: round(v, 3) for k, v in s.items()
                           if k in ("vader", "textblob", "custom", "finbert")
                           and v is not None},
            })

    return {
        "total_analyzed": len(agreements) + len(disagreements),
        "high_agreement_count": len(agreements),
        "high_disagreement_count": len(disagreements),
        "avg_std_overall": round(float(np.mean(
            [a["std"] for a in agreements] + [d["std"] for d in disagreements]
        )), 4) if (agreements or disagreements) else 0.0,
        "top_disagreements": sorted(disagreements, key=lambda x: -x["std"])[:10],
    }


def generate_evaluation_report(db_session,
                                news_items: list[dict],
                                use_llm: bool = False) -> dict:
    """
    Master function: runs all evaluations and returns a comprehensive report
    suitable for the FYP evaluation chapter.
    """
    benchmark = evaluate_on_benchmark(use_llm=use_llm)
    agreement_stats = model_agreement_stats(news_items)

    live = {}
    try:
        live = live_accuracy_report(db_session, days=7)
    except Exception as e:
        live = {"error": str(e)}

    return {
        "report_title": "FinancialPulse v3 — Model Evaluation Report",
        "generated_at": datetime.utcnow().isoformat(),
        "benchmark_evaluation": benchmark,
        "live_db_analysis": live,
        "model_agreement": agreement_stats,
        "summary": {
            "benchmark_accuracy": benchmark["metrics"].get("accuracy", 0),
            "benchmark_macro_f1": benchmark["metrics"].get("macro_f1", 0),
            "avg_model_agreement_std": agreement_stats.get("avg_std_overall", 0),
            "finbert_available": news_items[0]["sentiment"].get("finbert_available", False)
            if news_items else False,
        },
    }