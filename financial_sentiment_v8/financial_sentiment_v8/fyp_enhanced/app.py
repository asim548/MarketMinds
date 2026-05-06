"""
FinancialPulse v4 — Main Flask Application
Upgrades: Socket.IO WebSockets, AI signals, priority news, backtesting UI,
          WhatsApp/Telegram/Email alerts, no yfinance, modern UI.
"""

import os, json, logging, threading, time
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

try:
    from flask_cors import CORS
except Exception:  # pragma: no cover - optional hardening for partial envs
    def CORS(_app, **_kwargs):
        return _app

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover - optional hardening for partial envs
    class BackgroundScheduler:  # type: ignore[override]
        def __init__(self, *args, **kwargs):
            self._jobs = []

        def add_job(self, *args, **kwargs):
            self._jobs.append((args, kwargs))

        def start(self):
            return None

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from .models import (
    db,
    enable_wal_mode,
    NewsArticle,
    PriceSnapshot,
    SentimentAlert,
    AlertLog,
    SentimentTrend,
    BacktestResult,
    EvaluationSnapshot,
    GeneratedSignal,
    SignalBacktest,
)
from .utils.news_fetcher import fetch_all_news, get_cached_news, filter_news, get_source_health
from .utils.price_fetcher import fetch_prices, get_cached_prices, get_price_history, PRICE_SYMBOLS
from .utils.alert_system import fire_alert
from .utils.analytics import (
    compute_sentiment_price_correlation,
    compute_sentiment_momentum,
    update_sentiment_trends,
)
from .utils.automated_backtesting import (
    persist_generated_signals,
    evaluate_pending_signals,
    get_backtesting_summary,
)
from .utils.pretrained_hybrid import load_pretrained_models

# ── Pre-load ML models in background ─────────────────────────────────────────
from .utils.ml_engine import startup_load_models
startup_load_models()
hybrid_load_state = load_pretrained_models()
logger.info(f"[HybridModels] Load status: {hybrid_load_state}")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

DB_PATH = os.path.join(os.path.dirname(__file__), "financialpulse.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "fp4-secret-2026")

# ── CRITICAL: Guard against double init_app when MarketMinds parent
#    app also registers the same db instance. Only init once per app.
if 'sqlalchemy' not in app.extensions:
    db.init_app(app)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    logger=False, engineio_logger=False)

with app.app_context():
    db.create_all()
    enable_wal_mode(db.engine)


# ── Convenience: run a callable inside this app's context ────────────────────
def _with_ctx(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) inside app.app_context() and return the result."""
    with app.app_context():
        return fn(*args, **kwargs)


# ── Persist helpers ───────────────────────────────────────────────────────────

def _persist_news(news_items):
    with app.app_context():
        for item in news_items:
            try:
                if NewsArticle.query.get(item["id"]):
                    continue
                s = item["sentiment"]
                art = NewsArticle(
                    id=item["id"], title=item["title"],
                    description=item.get("description", ""),
                    url=item.get("url", "#"), source=item.get("source", ""),
                    category=item.get("category", ""),
                    sentiment_label=s.get("label", "neutral"),
                    sentiment_score=s.get("score", 0.0),
                    sentiment_confidence=s.get("confidence", 0.0),
                    vader_score=s.get("vader", 0.0),
                    textblob_score=s.get("textblob", 0.0),
                    custom_score=s.get("custom", 0.0),
                    finbert_score=s.get("finbert"),
                    llm_score=s.get("llm_score", 0.0),
                    llm_reasoning=s.get("llm_reasoning", ""),
                    llm_key_signal=s.get("llm_key_signal", ""),
                    llm_adjudication=s.get("llm_adjudication", ""),
                    assets_json=json.dumps(s.get("assets", [])),
                )
                try:
                    pub = datetime.fromisoformat(item.get("published_at","").replace("Z","+00:00"))
                    art.published_at = pub.replace(tzinfo=None)
                except Exception:
                    pass
                db.session.add(art)
            except Exception:
                db.session.rollback()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


def _persist_prices(prices):
    with app.app_context():
        for key, p in prices.items():
            try:
                db.session.add(PriceSnapshot(
                    symbol=p["symbol"], asset_key=key,
                    price=p["price"], change_pct=p["change_pct"], volume=0.0))
            except Exception:
                pass
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


def _check_alerts(news_items):
    with app.app_context():
        alerts = SentimentAlert.query.filter_by(active=True).all()
        if not alerts:
            return
        for alert in alerts:
            if alert.last_fired:
                if (datetime.utcnow() - alert.last_fired).total_seconds() < 1800:
                    continue
            asset_scores, trigger_article = [], ""
            for item in news_items:
                for a in item["sentiment"]["assets"]:
                    if a["key"] == alert.asset_key:
                        asset_scores.append(a.get("score", item["sentiment"]["score"]))
                        if not trigger_article:
                            trigger_article = item["title"]
                        break
            if not asset_scores:
                continue
            avg_score = sum(asset_scores) / len(asset_scores)
            score_threshold = (alert.threshold - 0.5) * 2
            should_fire = (
                (alert.direction == "bullish" and avg_score >= score_threshold) or
                (alert.direction == "bearish" and avg_score <= -abs(score_threshold))
            )
            if should_fire:
                from .utils.sentiment_engine import ASSET_KEYWORDS, get_signal_label
                info = ASSET_KEYWORDS.get(alert.asset_key, {})
                sig = get_signal_label(avg_score)
                success = fire_alert(
                    alert_config=alert.to_dict(),
                    asset_label=info.get("label", alert.asset_label),
                    asset_icon=info.get("icon", "📊"),
                    score=avg_score, signal=sig["signal"],
                    article_title=trigger_article,
                )
                alert.last_fired = datetime.utcnow()
                db.session.add(AlertLog(
                    alert_id=alert.id, asset_key=alert.asset_key,
                    message=f"Score {avg_score:.3f} crossed {alert.threshold:.0%} {alert.direction}",
                    success=success))
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()


_last_pushed_prices = {}
_last_pushed_news_ids = set()

SOURCE_AUTHORITY = {
    "CoinTelegraph": 0.9, "CoinDesk": 0.95, "CNBC": 1.0,
    "Yahoo Finance": 0.85, "OilPrice": 0.8, "FX Street": 0.85,
    "Decrypt": 0.75, "Seeking Alpha": 0.8,
}


def _full_refresh():
    global _last_pushed_prices, _last_pushed_news_ids
    logger.info("[Scheduler] Running full refresh...")
    news   = fetch_all_news(force=True)
    prices = fetch_prices(force=True)
    _persist_news(news)
    _persist_prices(prices)
    with app.app_context():
        update_sentiment_trends(db.session, news)
    _check_alerts(news)

    with app.app_context():
        created   = persist_generated_signals(db.session, news, prices, wait_hours=24)
        evaluated = evaluate_pending_signals(db.session, wait_hours=24)
    logger.info(f"[AutoBacktest] Signals created={created} evaluated={evaluated}")

    # WebSocket: push changed prices
    changed = {k: v for k, v in prices.items()
               if abs(v.get("price",0) - _last_pushed_prices.get(k,{}).get("price",0)) > 0.001}
    if changed:
        socketio.emit("price_update", {"prices": changed})
        _last_pushed_prices = dict(prices)

    # WebSocket: push new high-impact news
    now_ts = time.time()
    new_arts = []
    for item in news:
        if item["id"] not in _last_pushed_news_ids:
            _last_pushed_news_ids.add(item["id"])
            s = item["sentiment"]
            age_h = max(0, (now_ts - item.get("timestamp", now_ts)) / 3600)
            recency = max(0.1, 1.0 - age_h / 48)
            authority = SOURCE_AUTHORITY.get(item.get("source",""), 0.7)
            impact = abs(s.get("score",0)) * s.get("confidence",0.5) * recency * authority
            if impact > 0.12:
                clean = {k: v for k, v in item.items() if k != "_raw_text"}
                clean["_impact"] = round(impact, 4)
                new_arts.append(clean)
    if new_arts:
        new_arts.sort(key=lambda x: x["_impact"], reverse=True)
        socketio.emit("news_update", {"articles": new_arts[:10]})

    logger.info(f"[Scheduler] Refresh done — {len(news)} articles, {len(prices)} prices")


def _scheduled_auto_backtest_eval():
    with app.app_context():
        result = evaluate_pending_signals(db.session, wait_hours=24)
    logger.info(f"[AutoBacktest] Scheduled evaluator result: {result}")


scheduler = BackgroundScheduler()
scheduler.add_job(func=_full_refresh, trigger="interval", seconds=90,
                  id="full_refresh", replace_existing=True)
scheduler.add_job(
    func=_scheduled_auto_backtest_eval,
    trigger="interval",
    minutes=30,
    id="auto_backtest_eval",
    replace_existing=True,
)
scheduler.start()
threading.Thread(target=lambda: (time.sleep(2), _full_refresh()), daemon=True).start()


# ── Socket.IO ─────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    prices = get_cached_prices()
    if prices:
        emit("price_update", {"prices": prices})
    news = get_cached_news()
    if news:
        top = sorted(news,
                     key=lambda n: abs(n["sentiment"].get("score",0))*n["sentiment"].get("confidence",0.5),
                     reverse=True)[:8]
        clean = [{k: v for k, v in item.items() if k != "_raw_text"} for item in top]
        emit("news_update", {"articles": clean})

@socketio.on("disconnect")
def on_disconnect():
    pass

@socketio.on("request_refresh")
def on_request_refresh():
    threading.Thread(target=_full_refresh, daemon=True).start()
    emit("refresh_started", {"message": "Refreshing..."})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/recommendations")
def api_recommendations():
    """AI-powered BUY/SELL/HOLD per asset using ensemble ML model."""
    asset  = request.args.get("asset", "all")
    news   = get_cached_news() or fetch_all_news(force=True)
    prices = get_cached_prices()
    from .utils.sentiment_engine import ASSET_KEYWORDS
    from .utils.ai_signals import generate_ai_recommendation
    targets = [asset] if asset != "all" else list(ASSET_KEYWORDS.keys())
    results = {}
    with app.app_context():
        for key in targets:
            try:
                results[key] = generate_ai_recommendation(key, news, prices, db.session)
            except Exception as e:
                logger.warning(f"[Rec] {key}: {e}")
    return jsonify({"success": True, "recommendations": results})


@app.route("/api/priority_news")
def api_priority_news():
    """News ranked by AI impact score — no hard-coded keywords."""
    limit  = int(request.args.get("limit", 15))
    asset  = request.args.get("asset", "all")
    news   = get_cached_news() or fetch_all_news(force=True)
    now_ts = time.time()
    scored = []
    for item in news:
        s = item["sentiment"]
        if asset != "all" and not any(a["key"] == asset for a in s.get("assets",[])):
            continue
        age_h     = max(0, (now_ts - item.get("timestamp", now_ts)) / 3600)
        recency   = max(0.1, 1.0 - age_h / 48)
        authority = SOURCE_AUTHORITY.get(item.get("source",""), 0.7)
        impact    = abs(s.get("score",0)) * s.get("confidence",0.5) * recency * authority
        clean = {k: v for k, v in item.items() if k != "_raw_text"}
        clean["_impact"] = round(impact, 4)
        scored.append(clean)
    scored.sort(key=lambda x: x["_impact"], reverse=True)
    return jsonify({"success": True, "total": len(scored), "items": scored[:limit]})


@app.route("/api/top_predictions")
def api_top_predictions():
    limit = int(request.args.get("limit", 6))
    asset = request.args.get("asset", "all")
    news  = get_cached_news() or fetch_all_news(force=True)
    with_assets = [n for n in news if n["sentiment"].get("assets")]
    if asset != "all":
        with_assets = [n for n in with_assets
                       if any(a["key"] == asset for a in n["sentiment"]["assets"])]
    ranked = sorted(with_assets,
                    key=lambda n: abs(n["sentiment"].get("score",0))*n["sentiment"].get("confidence",0.5),
                    reverse=True)
    top = [{k: v for k, v in item.items() if k != "_raw_text"} for item in ranked[:limit]]
    return jsonify({"success": True, "total": len(ranked), "items": top})


@app.route("/api/news")
def api_news():
    asset     = request.args.get("asset", "all")
    sentiment = request.args.get("sentiment", "all")
    query     = request.args.get("q", "")
    limit     = int(request.args.get("limit", 50))
    page      = int(request.args.get("page", 1))
    source    = request.args.get("source", "cache")
    if source == "db":
        with app.app_context():
            q = NewsArticle.query.order_by(NewsArticle.published_at.desc())
            days   = int(request.args.get("days", 1))
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            q = q.filter(NewsArticle.published_at >= cutoff)
            if sentiment and sentiment != "all":
                q = q.filter(NewsArticle.sentiment_label == sentiment)
            if asset and asset != "all":
                q = q.filter(NewsArticle.assets_json.like(f'%"{asset}"%'))
            total = q.count()
            items = [a.to_dict() for a in q.offset((page-1)*limit).limit(limit).all()]
    else:
        news     = get_cached_news() or fetch_all_news(force=True)
        filtered = filter_news(news, asset=asset, sentiment=sentiment, query=query)
        total    = len(filtered)
        items    = [{k: v for k, v in item.items() if k != "_raw_text"}
                    for item in filtered[(page-1)*limit:(page-1)*limit+limit]]
    return jsonify({"success": True, "total": total, "page": page,
                    "limit": limit, "items": items})


@app.route("/api/stats")
def api_stats():
    news   = get_cached_news() or fetch_all_news(force=True)
    prices = get_cached_prices()
    total   = len(news)
    bullish = sum(1 for n in news if n["sentiment"]["label"] == "bullish")
    bearish = sum(1 for n in news if n["sentiment"]["label"] == "bearish")
    neutral = total - bullish - bearish
    asset_counts = {}
    for item in news:
        for a in item["sentiment"]["assets"]:
            k = a["key"]
            if k not in asset_counts:
                asset_counts[k] = {"bullish":0,"bearish":0,"neutral":0,
                                   "label":a["label"],"icon":a["icon"],
                                   "group":a.get("group",""),"symbol":a.get("symbol",""),
                                   "avg_score":0.0,"scores":[]}
            sl = a["sentiment"].lower()
            if sl in asset_counts[k]:
                asset_counts[k][sl] += 1
            asset_counts[k]["scores"].append(a.get("score",0.0))
    from .utils.sentiment_engine import get_signal_label
    for k in asset_counts:
        sc = asset_counts[k].pop("scores",[])
        asset_counts[k]["avg_score"] = round(sum(sc)/len(sc),4) if sc else 0.0
        asset_counts[k].update(get_signal_label(asset_counts[k]["avg_score"]))
    sources = {}
    for item in news:
        sources[item["source"]] = sources.get(item["source"],0) + 1
    return jsonify({"success":True,"total":total,
                    "sentiment_breakdown":{"bullish":bullish,"bearish":bearish,"neutral":neutral},
                    "asset_counts":asset_counts,"sources":sources,"prices":prices})


@app.route("/api/prices")
def api_prices():
    force  = request.args.get("force","0") == "1"
    prices = fetch_prices(force=force) if force else get_cached_prices()
    if not prices:
        prices = fetch_prices(force=True)
    return jsonify({"success":True,"prices":prices})


@app.route("/api/prices/<asset_key>/history")
def api_price_history(asset_key):
    period = request.args.get("period","7d")
    with app.app_context():
        history = get_price_history(asset_key, period, db_session=db.session)
    return jsonify({"success":True,"asset_key":asset_key,"period":period,"data":history})


@app.route("/api/trends")
def api_trends():
    asset  = request.args.get("asset","bitcoin")
    days   = int(request.args.get("days",7))
    cutoff = datetime.utcnow() - timedelta(days=days)
    with app.app_context():
        trends = SentimentTrend.query.filter(
            SentimentTrend.asset_key==asset, SentimentTrend.hour_bucket>=cutoff
        ).order_by(SentimentTrend.hour_bucket).all()
        data = [t.to_dict() for t in trends]
    return jsonify({"success":True,"asset":asset,"days":days,"data":data})


@app.route("/api/signal_history")
def api_signal_history():
    asset  = request.args.get("asset","bitcoin")
    days   = int(request.args.get("days",14))
    cutoff = datetime.utcnow() - timedelta(days=days)
    with app.app_context():
        trends = SentimentTrend.query.filter(
            SentimentTrend.asset_key==asset, SentimentTrend.hour_bucket>=cutoff
        ).order_by(SentimentTrend.hour_bucket).all()
        from collections import defaultdict
        import numpy as np
        daily = defaultdict(list)
        for t in trends:
            daily[t.hour_bucket.strftime("%Y-%m-%d")].append(t.avg_score)
        daily_avg = {d: float(np.mean(sc)) for d,sc in daily.items()}
        prices    = get_price_history(asset, f"{days}d", db_session=db.session)
    price_map = {p["date"]:p for p in prices}
    from .utils.sentiment_engine import get_signal_label
    result = []
    for day in sorted(daily_avg):
        score = daily_avg[day]
        sig   = get_signal_label(score)
        pd_   = price_map.get(day,{})
        pct   = 0.0
        if pd_:
            idx = prices.index(pd_) if pd_ in prices else -1
            if idx > 0:
                prev = prices[idx-1]["close"]; curr = pd_["close"]
                if prev and prev != 0:
                    pct = ((curr-prev)/prev)*100
        result.append({"date":day,"avg_score":round(score,4),
                        "signal":sig["signal"],"signal_class":sig["signal_class"],
                        "price_close":pd_.get("close"),"price_change_pct":round(pct,4)})
    return jsonify({"success":True,"asset":asset,"days":days,"data":result})


@app.route("/api/backtest")
def api_backtest():
    asset   = request.args.get("asset", "bitcoin")
    days    = int(request.args.get("days", 60))
    capital = float(request.args.get("capital", 10000))
    pos_sz  = float(request.args.get("pos_size", 0.10))
    from .utils.backtesting import FullBacktestEngine
    with app.app_context():
        result = FullBacktestEngine(db.session, initial_capital=capital,
                                     position_size_pct=pos_sz).run(asset_key=asset, lookback_days=days)
        if not result.get("error") and result.get("metrics"):
            m = result["metrics"]
            try:
                db.session.add(BacktestResult(
                    asset_key=asset, lookback_days=days,
                    total_signals=result["total_signals"],
                    accuracy=m.get("accuracy", 0), macro_f1=m.get("macro_f1", 0),
                    result_json=json.dumps({
                        "metrics": m, "equity_curve": result["equity_curve"],
                        "pnl_curve": result.get("pnl_curve", []),
                        "signals": result.get("signals", []),
                        "generated_at": result["generated_at"]
                    })))
                db.session.commit()
            except Exception:
                db.session.rollback()
    return jsonify({"success": True, **result})


@app.route("/api/backtest/csv", methods=["POST"])
def api_backtest_csv():
    """Run backtest on uploaded CSV dataset."""
    import os, tempfile
    asset   = request.form.get("asset", "primary")
    days    = int(request.form.get("days", 90))
    capital = float(request.form.get("capital", 10000))
    pos_sz  = float(request.form.get("pos_size", 0.10))
    csv_file = request.files.get("csv_file")
    csv_path = request.form.get("csv_path", "")

    if csv_file:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        csv_file.save(tf.name)
        csv_path = tf.name
    elif not csv_path:
        default = os.path.join(os.path.dirname(__file__), "data.csv")
        if os.path.exists(default):
            csv_path = default
        else:
            return jsonify({"success": False, "error": "No CSV file provided."})

    from .utils.backtesting import FullBacktestEngine
    result = FullBacktestEngine(None, initial_capital=capital,
                                 position_size_pct=pos_sz).run_from_csv(
        csv_path=csv_path, asset_key=asset, lookback_days=days)
    return jsonify({"success": True, **result})


@app.route("/api/dataset/upload", methods=["POST"])
def api_dataset_upload():
    """Upload CSV and train the hybrid ML model."""
    import os, tempfile
    asset    = request.form.get("target_asset", "primary")
    csv_file = request.files.get("csv_file")
    csv_path = request.form.get("csv_path", "")

    if csv_file:
        dest = os.path.join(os.path.dirname(__file__), "data.csv")
        csv_file.save(dest)
        csv_path = dest
    elif not csv_path:
        default = os.path.join(os.path.dirname(__file__), "data.csv")
        if os.path.exists(default):
            csv_path = default
        else:
            return jsonify({"success": False, "error": "No CSV provided"})

    from .utils.dataset_trainer import start_training_async
    result = start_training_async(csv_path, target_asset=asset)
    return jsonify({"success": True, "csv_path": csv_path, **result})


@app.route("/api/dataset/status")
def api_dataset_status():
    from .utils.dataset_trainer import get_training_state, get_dataset_model_meta
    state = get_training_state()
    meta  = get_dataset_model_meta()
    return jsonify({"success": True, "training": state, "last_model": meta})


@app.route("/api/dataset/meta")
def api_dataset_meta():
    from .utils.dataset_trainer import get_dataset_model_meta
    meta = get_dataset_model_meta()
    if not meta:
        return jsonify({"success": False, "error": "No dataset model trained yet"})
    return jsonify({"success": True, **meta})


@app.route("/api/backtest/history")
def api_backtest_history():
    asset = request.args.get("asset")
    with app.app_context():
        q = BacktestResult.query.order_by(BacktestResult.run_at.desc())
        if asset:
            q = q.filter_by(asset_key=asset)
        results = [r.to_dict() for r in q.limit(50).all()]
    return jsonify({"success":True,"results":results})


@app.route("/api/signals/generated")
def api_generated_signals():
    try:
        asset  = request.args.get("asset")
        status = request.args.get("status")
        limit  = int(request.args.get("limit", 200))
        with app.app_context():
            q = GeneratedSignal.query.order_by(GeneratedSignal.created_at.desc())
            if asset:
                q = q.filter(GeneratedSignal.asset_key == asset)
            if status:
                q = q.filter(GeneratedSignal.status == status)
            items = [s.to_dict() for s in q.limit(limit).all()]
        return jsonify({"success": True, "items": items})
    except Exception as e:
        app.logger.error(f"[api_generated_signals] {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "items": []}), 200


@app.route("/api/auto-backtest/run", methods=["POST"])
def api_auto_backtest_run():
    try:
        payload    = request.get_json(silent=True) or {}
        wait_hours = int(payload.get("wait_hours", 24))
        with app.app_context():
            result = evaluate_pending_signals(db.session, wait_hours=wait_hours)
        return jsonify({"success": True, **result})
    except Exception as e:
        app.logger.error(f"[api_auto_backtest_run] {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 200


@app.route("/api/auto-backtest/summary")
def api_auto_backtest_summary():
    try:
        asset           = request.args.get("asset")
        initial_capital = float(request.args.get("initial_capital", 10000))
        with app.app_context():
            summary = get_backtesting_summary(db.session, initial_capital=initial_capital, asset_key=asset)
        return jsonify({"success": True, **summary})
    except Exception as e:
        app.logger.error(f"[api_auto_backtest_summary] {e}", exc_info=True)
        # Fallback: raw SQLite so the page still gets data
        try:
            import sqlite3 as _sq
            _asset = request.args.get("asset")
            _cap   = float(request.args.get("initial_capital", 10000))
            con = _sq.connect(DB_PATH)
            cur = con.cursor()
            if _asset:
                cur.execute("SELECT COUNT(*) FROM generated_signals WHERE asset_key=?", (_asset,))
            else:
                cur.execute("SELECT COUNT(*) FROM generated_signals")
            total_generated = cur.fetchone()[0]
            if _asset:
                cur.execute("SELECT pnl_value, correct FROM signal_backtests WHERE asset_key=?", (_asset,))
            else:
                cur.execute("SELECT pnl_value, correct FROM signal_backtests")
            rows = cur.fetchall()
            con.close()
            evaluated = len(rows)
            wins      = sum(1 for r in rows if r[1])
            losses    = evaluated - wins
            total_pnl = round(sum((r[0] or 0) for r in rows), 2)
            win_ratio = round(wins / evaluated, 4) if evaluated else 0.0
            total_return_pct = round((total_pnl / _cap) * 100, 4) if _cap else 0.0
            return jsonify({"success": True, "asset_key": _asset or "all",
                "total_signals_generated": total_generated, "total_signals_evaluated": evaluated,
                "pending_signals": max(total_generated - evaluated, 0),
                "wins": wins, "losses": losses, "win_ratio": win_ratio,
                "total_pnl": total_pnl, "total_return_pct": total_return_pct,
                "equity_curve": [], "pnl_curve": [], "drawdown_curve": []})
        except Exception as e2:
            app.logger.error(f"[api_auto_backtest_summary] fallback failed: {e2}", exc_info=True)
            return jsonify({"success": False, "error": str(e), "fallback_error": str(e2)}), 200


@app.route("/api/auth_status")
def api_auth_status():
    return jsonify({"success": True, "authenticated": False})


@app.route("/api/evaluate")
def api_evaluate():
    use_llm = request.args.get("llm","0") == "1"
    from .utils.evaluation import evaluate_on_benchmark
    result = evaluate_on_benchmark(use_llm=use_llm)
    m      = result.get("metrics",{})
    with app.app_context():
        db.session.add(EvaluationSnapshot(
            benchmark=result.get("benchmark",""),
            accuracy=m.get("accuracy",0), macro_f1=m.get("macro_f1",0),
            weighted_f1=m.get("weighted_f1",0), result_json=json.dumps(result)))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return jsonify({"success":True,**result})


@app.route("/api/evaluate/history")
def api_evaluate_history():
    with app.app_context():
        snaps = EvaluationSnapshot.query.order_by(EvaluationSnapshot.run_at.desc()).limit(20).all()
        data  = [s.to_dict() for s in snaps]
    return jsonify({"success":True,"results":data})


@app.route("/api/evaluate/live")
def api_evaluate_live():
    from .utils.evaluation import live_accuracy_report
    with app.app_context():
        result = live_accuracy_report(db.session, days=int(request.args.get("days",7)))
    return jsonify({"success":True,**result})


@app.route("/api/source_health")
def api_source_health():
    health = get_source_health()
    total  = len(health)
    hcount = sum(1 for v in health.values() if v["healthy"])
    return jsonify({"success":True,"total_sources":total,"healthy_count":hcount,
                    "unhealthy_count":total-hcount,
                    "health_ratio":round(hcount/total,4) if total else 1.0,
                    "sources":health})


@app.route("/api/correlation")
def api_correlation():
    news   = get_cached_news()
    assets = ["bitcoin","gold","sp500","nasdaq","oil","eurusd","gbpusd"]
    with app.app_context():
        phist = {a: h for a in assets if (h := get_price_history(a,"30d",db_session=db.session))}
        corr  = compute_sentiment_price_correlation(db.session, phist)
    for a in assets:
        mom = compute_sentiment_momentum(news, a, hours=4)
        corr.setdefault(a,{})["momentum"] = mom
    return jsonify({"success":True,"correlations":corr})


@app.route("/api/momentum")
def api_momentum():
    news = get_cached_news()
    from .utils.sentiment_engine import ASSET_KEYWORDS
    result = {k: m for k in ASSET_KEYWORDS
               if (m := compute_sentiment_momentum(news, k, hours=2))["recent_count"] > 0}
    return jsonify({"success":True,"momentum":result})


@app.route("/api/history")
def api_history():
    days   = int(request.args.get("days",7))
    asset  = request.args.get("asset","all")
    limit  = int(request.args.get("limit",100))
    cutoff = datetime.utcnow() - timedelta(days=days)
    with app.app_context():
        q = NewsArticle.query.filter(NewsArticle.published_at>=cutoff).order_by(NewsArticle.published_at.desc())
        if asset != "all":
            q = q.filter(NewsArticle.assets_json.like(f'%"{asset}"%'))
        data = {"total": q.count(), "items": [a.to_dict() for a in q.limit(limit).all()]}
    return jsonify({"success":True,**data})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    threading.Thread(target=_full_refresh, daemon=True).start()
    return jsonify({"success":True,"message":"Refresh started"})


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    with app.app_context():
        alerts = [a.to_dict() for a in SentimentAlert.query.order_by(SentimentAlert.created_at.desc()).all()]
    return jsonify({"success":True,"alerts":alerts})


@app.route("/api/alerts", methods=["POST"])
def create_alert():
    data = request.get_json() or {}
    from .utils.sentiment_engine import ASSET_KEYWORDS
    key  = data.get("asset_key","bitcoin")
    info = ASSET_KEYWORDS.get(key,{})
    with app.app_context():
        alert = SentimentAlert(asset_key=key, asset_label=info.get("label",key),
                               direction=data.get("direction","bullish"),
                               threshold=float(data.get("threshold",0.65)),
                               channel=data.get("channel","telegram"),
                               destination=data.get("destination",""), active=True)
        db.session.add(alert)
        db.session.commit()
        result = alert.to_dict()
    return jsonify({"success":True,"alert":result})


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    with app.app_context():
        a = SentimentAlert.query.get_or_404(alert_id)
        db.session.delete(a)
        db.session.commit()
    return jsonify({"success":True})


@app.route("/api/alerts/<int:alert_id>/toggle", methods=["POST"])
def toggle_alert(alert_id):
    with app.app_context():
        a = SentimentAlert.query.get_or_404(alert_id)
        a.active = not a.active
        db.session.commit()
        active = a.active
    return jsonify({"success":True,"active":active})


@app.route("/api/article/<article_id>/reanalyze", methods=["POST"])
def api_reanalyze(article_id):
    with app.app_context():
        art = NewsArticle.query.get_or_404(article_id)
        from .utils.sentiment_engine import analyzer
        r   = analyzer.analyze(art.title, art.description or "", use_llm=True)
        art.sentiment_score=r["score"]; art.sentiment_label=r["label"]
        art.sentiment_confidence=r["confidence"]; art.llm_score=r["llm_score"]
        art.llm_reasoning=r["llm_reasoning"]; art.llm_key_signal=r.get("llm_key_signal","")
        art.llm_adjudication=r.get("llm_adjudication","")
        try:
            db.session.commit()
            return jsonify({"success":True,"result":r})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success":False,"error":str(e)}), 500


@app.route("/api/portfolio")
def api_portfolio():
    method = request.args.get("method", "risk_parity")
    news   = get_cached_news() or fetch_all_news(force=True)
    prices = get_cached_prices()
    from .utils.ai_signals import generate_portfolio_recommendation
    try:
        with app.app_context():
            result = generate_portfolio_recommendation(news, prices, db.session, method=method)
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/social")
def api_social():
    asset = request.args.get("asset", "all")
    force = request.args.get("force", "0") == "1"
    try:
        from .utils.social_sentiment import fetch_all_social_data, get_social_sentiment_summary
        if force:
            fetch_all_social_data(force=True)
        summary = get_social_sentiment_summary(asset)
        return jsonify({"success": True, "asset": asset, **summary})
    except Exception as e:
        logger.warning(f"[Social] {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/vip_signals")
def api_vip_signals():
    news = get_cached_news() or fetch_all_news(force=True)
    from .utils.ml_engine import detect_vip_person
    vip_hits = []
    for item in news:
        text = f"{item.get('title','')} {item.get('description','')}"
        vips = detect_vip_person(text)
        if vips:
            s = item["sentiment"]
            vip_hits.append({
                "title":      item.get("title",""),
                "url":        item.get("url","#"),
                "source":     item.get("source",""),
                "vip_persons": vips,
                "score":      s.get("score",0),
                "label":      s.get("label","neutral"),
                "assets":     s.get("assets",[]),
                "published_at": item.get("published_at",""),
            })
    vip_hits.sort(key=lambda x: max((v["impact_multiplier"] for v in x["vip_persons"]), default=1), reverse=True)
    return jsonify({"success": True, "total": len(vip_hits), "items": vip_hits[:20]})


@app.route("/api/trade_signals")
def api_trade_signals():
    asset  = request.args.get("asset", "bitcoin")
    news   = get_cached_news() or fetch_all_news(force=True)
    prices = get_cached_prices()
    from .utils.ai_signals import generate_ai_recommendation
    try:
        with app.app_context():
            rec = generate_ai_recommendation(asset, news, prices, db.session)
        return jsonify({"success": True, "asset": asset,
                        "signal": rec.get("signal"), "action": rec.get("action"),
                        "composite_score": rec.get("composite_score"),
                        "confidence": rec.get("confidence"),
                        "trade_signal": rec.get("trade_signal", {}),
                        "vip_persons": rec.get("vip_persons", []),
                        "win_probability": rec.get("win_probability", 0),
                        "fear_greed": rec.get("fear_greed", {})})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/ml_retrain", methods=["POST"])
def api_ml_retrain():
    asset = request.args.get("asset", "bitcoin")
    days  = int(request.args.get("days", 60))
    try:
        from .utils.backtesting import IndustrialBacktestEngine
        with app.app_context():
            result = IndustrialBacktestEngine(db.session).run(asset_key=asset, lookback_days=days)
        return jsonify({"success": True,
                        "model_trained": result.get("model_trained", False),
                        "metrics": result.get("metrics", {}),
                        "asset": asset,
                        "total_samples": result.get("total_articles", 0)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


if __name__ == "__main__":
    socketio.run(app, debug=False, host="0.0.0.0", port=5000,
                 use_reloader=False, log_output=False)


# ════════════════════════════════════════════════════════════════════════════
# ██  ENHANCEMENT BLOCK — FYP v8 additions (safe, non-breaking)
# ════════════════════════════════════════════════════════════════════════════

# ── 1. Live Signal Tracker  (last 7 days signals vs actual market) ────────

@app.route("/api/live_tracker")
def api_live_tracker():
    """Real-world signal accuracy over the last N days."""
    days  = int(request.args.get("days", 7))
    asset = request.args.get("asset")
    from .models import SignalBacktest, GeneratedSignal
    cutoff = datetime.utcnow() - timedelta(days=days)
    with app.app_context():
        q = db.session.query(SignalBacktest, GeneratedSignal)\
            .join(GeneratedSignal, SignalBacktest.signal_id == GeneratedSignal.id)\
            .filter(SignalBacktest.evaluated_at >= cutoff)\
            .order_by(SignalBacktest.evaluated_at.desc())
        if asset:
            q = q.filter(SignalBacktest.asset_key == asset)
        rows = q.limit(200).all()

        items = []
        for bt, sig in rows:
            items.append({
                "signal_uid":    sig.signal_uid,
                "asset_key":     sig.asset_key,
                "asset_name":    sig.asset_name,
                "predicted":     sig.predicted_direction,
                "confidence":    round(sig.confidence_score, 3),
                "entry_price":   sig.entry_price,
                "actual_price":  bt.actual_price,
                "actual_dir":    bt.actual_direction,
                "movement_pct":  round(bt.movement_pct, 3),
                "correct":       bt.correct,
                "pnl_pct":       round(bt.pnl_pct, 3),
                "pnl_value":     round(bt.pnl_value, 2),
                "created_at":    sig.created_at.strftime("%b %d %H:%M"),
                "evaluated_at":  bt.evaluated_at.strftime("%b %d %H:%M"),
            })

    total   = len(items)
    correct = sum(1 for i in items if i["correct"])
    accuracy = round(correct / total * 100, 1) if total else 0
    total_pnl = round(sum(i["pnl_value"] for i in items), 2)
    win_streak = 0
    for i in items:
        if i["correct"]: win_streak += 1
        else: break

    return jsonify({
        "success": True, "days": days, "items": items,
        "summary": {
            "total": total, "correct": correct,
            "accuracy_pct": accuracy,
            "total_pnl": total_pnl,
            "win_streak": win_streak,
        }
    })


# ── 2. Trade Simulation Engine (slippage + spread + latency) ─────────────

@app.route("/api/trade_sim", methods=["POST"])
def api_trade_sim():
    """Simulate a trade with realistic execution modelling."""
    data        = request.get_json(silent=True) or {}
    asset       = data.get("asset", "bitcoin")
    direction   = data.get("direction", "bullish")
    capital     = float(data.get("capital", 1000))
    risk_pct    = float(data.get("risk_pct", 2))
    tp_ratio    = float(data.get("tp_ratio", 2.0))
    sl_pct      = float(data.get("sl_pct", 1.5))
    asset_class = data.get("asset_class", "crypto")

    MICRO = {
        "crypto":    {"spread_pct": 0.05, "slippage_pct": 0.03, "latency_ms": 80},
        "forex":     {"spread_pct": 0.008,"slippage_pct": 0.005,"latency_ms": 20},
        "stock":     {"spread_pct": 0.02, "slippage_pct": 0.01, "latency_ms": 50},
        "commodity": {"spread_pct": 0.04, "slippage_pct": 0.02, "latency_ms": 60},
    }
    micro = MICRO.get(asset_class, MICRO["crypto"])

    prices    = get_cached_prices()
    raw_price = float((prices.get(asset) or {}).get("price") or 0)
    if raw_price <= 0:
        return jsonify({"success": False, "error": "Price unavailable for asset"})

    spread_cost = raw_price * (micro["spread_pct"] / 100)
    slippage    = raw_price * (micro["slippage_pct"] / 100)
    entry_price = raw_price + spread_cost + slippage if direction == "bullish" \
                  else raw_price - spread_cost - slippage

    sl_price = entry_price * (1 - sl_pct / 100) if direction == "bullish" \
               else entry_price * (1 + sl_pct / 100)
    tp_price = entry_price * (1 + (sl_pct * tp_ratio) / 100) if direction == "bullish" \
               else entry_price * (1 - (sl_pct * tp_ratio) / 100)

    risk_amount    = capital * (risk_pct / 100)
    price_risk     = abs(entry_price - sl_price)
    position_units = risk_amount / price_risk if price_risk > 0 else 0
    position_value = position_units * entry_price

    gross_profit     = position_units * abs(tp_price - entry_price)
    gross_loss       = position_units * abs(sl_price - entry_price)
    transaction_cost = position_value * (micro["spread_pct"] + micro["slippage_pct"]) / 100
    net_profit       = gross_profit - transaction_cost
    net_loss         = gross_loss   + transaction_cost

    try:
        news = get_cached_news() or []
        from .utils.ai_signals import generate_ai_recommendation
        with app.app_context():
            rec = generate_ai_recommendation(asset, news, prices, db.session)
        win_prob = float(rec.get("win_probability", 0.5))
    except Exception:
        win_prob = 0.5

    expected_value = win_prob * net_profit - (1 - win_prob) * net_loss
    kelly_fraction = (win_prob - (1 - win_prob) / tp_ratio) if tp_ratio > 0 else 0
    kelly_fraction = max(0, min(kelly_fraction, 0.25))

    return jsonify({
        "success": True, "asset": asset, "direction": direction,
        "market_microstructure": {
            "spread_pct":  micro["spread_pct"], "slippage_pct": micro["slippage_pct"],
            "latency_ms":  micro["latency_ms"], "spread_cost":  round(spread_cost, 6),
            "slippage":    round(slippage, 6),
        },
        "execution": {
            "raw_price":    round(raw_price, 6),    "entry_price": round(entry_price, 6),
            "sl_price":     round(sl_price, 6),     "tp_price":    round(tp_price, 6),
            "position_units": round(position_units, 6),
            "position_value": round(position_value, 2),
            "transaction_cost": round(transaction_cost, 2),
        },
        "risk": {
            "capital": capital, "risk_amount": round(risk_amount, 2),
            "risk_pct": risk_pct, "sl_pct": sl_pct, "tp_ratio": tp_ratio, "rr_ratio": tp_ratio,
        },
        "outcomes": {
            "gross_profit": round(gross_profit, 2), "gross_loss":  round(gross_loss, 2),
            "net_profit":   round(net_profit, 2),   "net_loss":    round(net_loss, 2),
            "expected_value": round(expected_value, 2),
            "win_probability": round(win_prob, 3),
            "kelly_fraction": round(kelly_fraction, 4),
            "recommended_size_pct": round(kelly_fraction * 100, 1),
        },
    })


# ── 3. Data Quality / Noise Filtering ─────────────────────────────────────

@app.route("/api/data_quality")
def api_data_quality():
    news = get_cached_news() or fetch_all_news(force=True)
    source_stats = {}
    for item in news:
        src = item.get("source", "Unknown")
        s   = item["sentiment"]
        if src not in source_stats:
            source_stats[src] = {
                "count": 0, "total_conf": 0, "scores": [],
                "has_description": 0, "freshness_sum": 0
            }
        st = source_stats[src]
        st["count"] += 1
        st["total_conf"] += s.get("confidence", 0)
        st["scores"].append(abs(s.get("score", 0)))
        st["has_description"] += 1 if item.get("description", "") else 0
        age_h = max(0, (time.time() - item.get("timestamp", time.time())) / 3600)
        st["freshness_sum"] += max(0, 1 - age_h / 48)

    import statistics
    quality_report = []
    for src, st in source_stats.items():
        n = st["count"]
        avg_conf   = st["total_conf"] / n
        avg_score  = sum(st["scores"]) / n
        desc_ratio = st["has_description"] / n
        avg_fresh  = st["freshness_sum"] / n
        score_std  = statistics.stdev(st["scores"]) if n > 1 else 0
        noise_score = min(1.0, score_std * 2) * (1 - avg_conf)
        quality     = avg_conf * 0.4 + avg_fresh * 0.3 + desc_ratio * 0.2 + (1 - noise_score) * 0.1
        quality_report.append({
            "source":      src,
            "article_count": n,
            "avg_confidence": round(avg_conf, 3),
            "avg_signal_strength": round(avg_score, 3),
            "description_coverage": round(desc_ratio, 3),
            "freshness_score": round(avg_fresh, 3),
            "noise_score":   round(noise_score, 3),
            "quality_score": round(quality, 3),
            "grade": "A" if quality > 0.75 else "B" if quality > 0.55 else "C" if quality > 0.35 else "D",
            "recommended": quality > 0.45,
        })

    quality_report.sort(key=lambda x: x["quality_score"], reverse=True)
    overall_quality = round(sum(r["quality_score"] for r in quality_report) / len(quality_report), 3) if quality_report else 0

    return jsonify({
        "success": True,
        "overall_quality": overall_quality,
        "total_sources": len(quality_report),
        "high_quality_sources": sum(1 for r in quality_report if r["grade"] in ["A","B"]),
        "noisy_sources": sum(1 for r in quality_report if r["noise_score"] > 0.4),
        "sources": quality_report,
    })


# ── 4. SHAP-style Signal Explainability ───────────────────────────────────

@app.route("/api/explain_signal")
def api_explain_signal():
    asset  = request.args.get("asset", "bitcoin")
    news   = get_cached_news() or fetch_all_news(force=True)
    prices = get_cached_prices()

    try:
        from .utils.ai_signals import generate_ai_recommendation
        with app.app_context():
            rec = generate_ai_recommendation(asset, news, prices, db.session)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

    asset_news  = [n for n in news if any(a["key"] == asset for a in n["sentiment"].get("assets", []))]
    news_scores = [n["sentiment"]["score"] for n in asset_news]
    news_mean   = sum(news_scores) / len(news_scores) if news_scores else 0
    conf_weighted = sum(n["sentiment"]["score"] * n["sentiment"].get("confidence", 0.5)
                        for n in asset_news) / max(len(asset_news), 1)
    now_ts  = time.time()
    recent  = [n for n in asset_news if (now_ts - n.get("timestamp", now_ts)) / 3600 < 6]
    older   = [n for n in asset_news if 6 <= (now_ts - n.get("timestamp", now_ts)) / 3600 < 24]
    momentum = (sum(n["sentiment"]["score"] for n in recent) / max(len(recent), 1)
              - sum(n["sentiment"]["score"] for n in older)  / max(len(older), 1))
    auth_score = sum(
        n["sentiment"]["score"] * SOURCE_AUTHORITY.get(n.get("source",""), 0.7)
        for n in asset_news
    ) / max(len(asset_news), 1)
    p            = prices.get(asset, {})
    price_change = float(p.get("change_pct", 0))
    llm_score    = sum(n["sentiment"].get("llm_score", 0) for n in asset_news) / max(len(asset_news), 1)

    composite = rec.get("composite_score", 0)
    raw_vals  = {
        "News Sentiment (avg)":        news_mean,
        "Confidence-Weighted Score":   conf_weighted,
        "Recency Momentum":            momentum,
        "Source Authority Score":      auth_score,
        "Price Trend Context":         price_change / 100,
        "LLM Adjudication Score":      llm_score,
    }
    raw_attrs = {k: abs(v) * w for (k, v), w in zip(raw_vals.items(), [1.0, 1.2, 0.8, 1.1, 1.0, 1.0])}
    total_raw = sum(raw_attrs.values()) or 1
    attrs = [
        {
            "feature":      k,
            "contribution": round(raw_attrs[k] / total_raw * 100, 1),
            "raw_value":    round(raw_vals[k], 4),
            "direction":    "positive" if raw_vals[k] >= 0 else "negative",
        }
        for k in raw_attrs
    ]
    attrs.sort(key=lambda x: x["contribution"], reverse=True)

    top_headlines = sorted(asset_news, key=lambda n: abs(n["sentiment"]["score"]), reverse=True)[:5]
    supporting = [{"title": n["title"], "score": round(n["sentiment"]["score"], 3),
                   "label": n["sentiment"]["label"], "source": n.get("source","")} for n in top_headlines]

    return jsonify({
        "success":         True,
        "asset":           asset,
        "signal":          rec.get("signal"),
        "action":          rec.get("action"),
        "composite_score": round(composite, 4),
        "confidence":      round(rec.get("confidence", 0), 3),
        "win_probability": round(rec.get("win_probability", 0), 3),
        "feature_attribution": attrs,
        "reasoning_chain": [
            f"📰 {len(asset_news)} articles analyzed for {asset}",
            f"📊 Average sentiment score: {news_mean:+.3f}",
            f"⚡ 6h momentum vs 6-24h: {momentum:+.3f}",
            f"🏆 Authority-weighted score: {auth_score:+.3f}",
            f"🤖 LLM adjudication avg: {llm_score:+.3f}",
            f"💹 Price change context: {price_change:+.2f}%",
        ],
        "llm_reasoning":  rec.get("llm_reasoning", ""),
        "top_headlines":  supporting,
    })


# ── 5. Risk Control System ─────────────────────────────────────────────────

@app.route("/api/risk_control")
def api_risk_control():
    capital  = float(request.args.get("capital", 10000))
    max_risk = float(request.args.get("max_risk_pct", 2.0))
    max_dd   = float(request.args.get("max_dd_pct", 15.0))
    assets   = request.args.get("assets", "bitcoin,gold,eurusd,oil,sp500").split(",")

    news   = get_cached_news() or []
    prices = get_cached_prices()

    from .utils.ai_signals import generate_ai_recommendation
    from .utils.sentiment_engine import ASSET_KEYWORDS

    ASSET_CLASS_MAP = {
        "bitcoin":"crypto","ethereum":"crypto","solana":"crypto","xrp":"crypto",
        "gold":"commodity","silver":"commodity","oil":"commodity",
        "eurusd":"forex","gbpusd":"forex","usdjpy":"forex",
        "sp500":"stock","nasdaq":"stock","dowjones":"stock",
    }

    allocation      = []
    total_allocated = 0
    total_risk      = 0

    for asset in assets:
        info  = ASSET_KEYWORDS.get(asset, {})
        p     = prices.get(asset, {})
        price = float(p.get("price", 0))
        if price <= 0:
            continue
        try:
            with app.app_context():
                rec = generate_ai_recommendation(asset, news, prices, db.session)
        except Exception:
            continue

        signal     = rec.get("signal", "HOLD")
        confidence = float(rec.get("confidence", 0.5))
        win_prob   = float(rec.get("win_probability", 0.5))

        sl_pct   = 1.5 if ASSET_CLASS_MAP.get(asset,"crypto") == "crypto" else 0.8
        tp_ratio = 2.0
        kelly    = max(0, win_prob - (1 - win_prob) / tp_ratio)
        kelly    = min(kelly, 0.25)

        risk_budget   = capital * (max_risk / 100) * confidence
        position_size = risk_budget / (price * sl_pct / 100) if price > 0 else 0
        position_val  = min(position_size * price, capital * 0.20)
        alloc_pct     = position_val / capital * 100

        volatility = abs(float(p.get("change_pct", 1)))
        dynamic_sl = max(sl_pct, volatility * 1.5)
        sl_price   = price * (1 - dynamic_sl / 100) if signal in ["BUY","STRONG BUY"] \
                     else price * (1 + dynamic_sl / 100)
        tp_price   = price * (1 + dynamic_sl * tp_ratio / 100) if signal in ["BUY","STRONG BUY"] \
                     else price * (1 - dynamic_sl * tp_ratio / 100)

        allocation.append({
            "asset":        asset,
            "asset_name":   info.get("label", asset),
            "asset_class":  ASSET_CLASS_MAP.get(asset, "other"),
            "signal":       signal,
            "confidence":   round(confidence, 3),
            "current_price": round(price, 4),
            "position_value": round(position_val, 2),
            "allocation_pct": round(alloc_pct, 2),
            "kelly_fraction": round(kelly, 4),
            "stop_loss":    round(sl_price, 6),
            "take_profit":  round(tp_price, 6),
            "dynamic_sl_pct": round(dynamic_sl, 2),
            "risk_per_trade": round(risk_budget, 2),
            "max_loss":     round(position_val * dynamic_sl / 100, 2),
        })
        total_allocated += position_val
        total_risk      += position_val * dynamic_sl / 100

    cash_reserve     = capital - total_allocated
    portfolio_risk   = total_risk / capital * 100
    diversification  = len([a for a in allocation if a["signal"] not in ["HOLD","NEUTRAL"]]) / max(len(allocation), 1)
    risk_budget_used = total_risk / (capital * max_dd / 100) * 100

    warnings = []
    if portfolio_risk > max_dd * 0.8:
        warnings.append(f"⚠️ Portfolio risk {portfolio_risk:.1f}% approaching max drawdown limit {max_dd:.0f}%")
    if total_allocated / capital > 0.85:
        warnings.append("⚠️ Over 85% of capital deployed — consider reducing exposure")
    if diversification < 0.3:
        warnings.append("⚠️ Low signal diversity — portfolio may be overconcentrated")
    if not warnings:
        warnings.append("✅ Risk parameters within acceptable bounds")

    return jsonify({
        "success": True,
        "portfolio_summary": {
            "total_capital":       capital,
            "total_allocated":     round(total_allocated, 2),
            "cash_reserve":        round(cash_reserve, 2),
            "utilization_pct":     round(total_allocated / capital * 100, 1),
            "portfolio_risk_pct":  round(portfolio_risk, 2),
            "max_allowed_dd_pct":  max_dd,
            "risk_budget_used_pct":round(risk_budget_used, 1),
            "diversification_score": round(diversification, 3),
        },
        "allocation": sorted(allocation, key=lambda x: x["allocation_pct"], reverse=True),
        "risk_warnings": warnings,
    })