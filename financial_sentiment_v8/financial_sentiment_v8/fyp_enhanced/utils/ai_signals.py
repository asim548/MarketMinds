"""
FinancialPulse v5 — AI Signal Generator (Industrial ML Edition)
Zero hardcoded rules. GBM ensemble + FinBERT + social + VIP detection + ATR signals.
"""
from __future__ import annotations
import logging, time
from datetime import datetime
import numpy as np
logger = logging.getLogger(__name__)


def generate_ai_recommendation(asset_key, news, prices, db_session=None):
    from .sentiment_engine import ASSET_KEYWORDS, get_signal_label
    from .ml_engine import (get_gbm, extract_features,
        _vader_score, _finbert_score, _textblob_score,
        detect_vip_person, apply_vip_boost, generate_trade_signals)

    info     = ASSET_KEYWORDS.get(asset_key, {})
    now_ts   = time.time()
    asset_items = [n for n in news if any(a["key"]==asset_key for a in n["sentiment"].get("assets",[]))]

    if not asset_items:
        return _no_data(asset_key, info)

    gbm = get_gbm()
    scored, all_vips = [], []

    for item in asset_items:
        s     = item["sentiment"]
        title = item.get("title",""); desc = item.get("description","")
        text  = f"{title} {desc}"
        vs    = s.get("vader") or _vader_score(text)
        ts    = s.get("textblob") or _textblob_score(text)
        fs    = s.get("finbert") or _finbert_score(text[:1500])
        try:
            pub = datetime.fromisoformat(item.get("published_at","").replace("Z","+00:00"))
            hod, dow = pub.hour, pub.weekday()
        except Exception:
            hod, dow = 12, 2
        feats = extract_features(title=title, description=desc, vader=vs,
            textblob=ts, finbert=fs, distilrob=fs,
            price_change_pct=prices.get(asset_key,{}).get("change_pct",0.0),
            hour_of_day=hod, day_of_week=dow)
        res   = gbm.predict_proba(feats)
        sc, cf = res["signal_score"], 1.0 - res["flat"]
        vips  = detect_vip_person(text); all_vips.extend(vips)
        if vips: sc, cf = apply_vip_boost(sc, cf, vips)
        age_h = max(0,(now_ts - item.get("timestamp",now_ts))/3600)
        w     = np.exp(-age_h/12)
        scored.append({"title":title,"url":item.get("url","#"),"source":item.get("source",""),
                        "score":sc,"confidence":cf,"weight":w,"vips":vips,
                        "gbm_up":res["up"],"gbm_down":res["down"],"model":res["model"],
                        "label":s.get("label","neutral")})

    wts   = np.array([s["weight"] for s in scored])
    scs   = np.array([s["score"]  for s in scored])
    cfs   = np.array([s["confidence"] for s in scored])
    if wts.sum()==0: wts = np.ones(len(wts))
    sent  = float(np.average(scs, weights=wts))
    conf  = float(np.average(cfs, weights=wts))

    social_score, social_data = 0.0, {}
    try:
        from .social_sentiment import get_social_sentiment_summary
        social_data  = get_social_sentiment_summary(asset_key)
        social_score = social_data.get("weighted_score", 0.0)
        if social_data.get("post_count",0) > 3:
            conf = conf*0.8 + social_data.get("confidence",conf)*0.2
    except Exception: pass

    rec_sc = [s["score"] for s in scored if s["weight"]>0.7]
    bas_sc = [s["score"] for s in scored if s["weight"]<=0.7]
    mom    = float(np.mean(rec_sc)-np.mean(bas_sc)) if rec_sc and bas_sc else 0.0
    pd_    = prices.get(asset_key,{})
    ptrend = max(-1.,min(1., pd_.get("change_pct",0.0)/5.0))
    cp     = pd_.get("price",0.0)
    has_soc = social_data.get("post_count",0) > 3
    if has_soc:
        comp = 0.45*sent + 0.20*mom + 0.15*ptrend + 0.15*social_score + 0.05*bool(all_vips)*sent
    else:
        comp = 0.55*sent + 0.25*mom + 0.20*ptrend
    comp = max(-1.,min(1.,comp))
    sig  = get_signal_label(comp)
    wp   = float(np.mean([s["gbm_up"] for s in scored]))

    trade_sig = {}
    if cp > 0 and db_session:
        try:
            from .price_fetcher import get_price_history
            hist = get_price_history(asset_key,"30d",db_session=db_session)
            trade_sig = generate_trade_signals(asset_key=asset_key, current_price=cp,
                signal_score=comp, confidence=conf, price_history=hist, win_prob=wp)
        except Exception as e:
            logger.debug(f"[Signal] Trade sig err {asset_key}: {e}")

    seen = set(); uvips = []
    for v in all_vips:
        if v["key"] not in seen: uvips.append(v); seen.add(v["key"])

    top3 = sorted(scored, key=lambda x: abs(x["score"])*x["confidence"], reverse=True)[:3]
    sup  = [{"title":s["title"],"url":s["url"],"source":s["source"],
              "impact":round(abs(s["score"])*s["confidence"],3),"label":s["label"],
              "gbm_up":s["gbm_up"],"gbm_down":s["gbm_down"],
              "vip_persons":[v["name"] for v in s.get("vips",[])]} for s in top3]

    return {"asset_key":asset_key,"label":info.get("label",asset_key),"icon":info.get("icon","📊"),
            "symbol":info.get("symbol",""),
            "signal":sig["signal"],"signal_class":sig["signal_class"],"signal_color":sig["signal_color"],
            "action":trade_sig.get("action","HOLD"),
            "confidence":round(conf,4),"composite_score":round(comp,4),
            "sentiment_score":round(sent,4),"momentum":round(mom,4),
            "price_trend_score":round(ptrend,4),"social_score":round(social_score,4),
            "trade_signal":trade_sig,"current_price":cp,
            "vip_persons":uvips,"coverage":len(asset_items),
            "social_coverage":social_data.get("post_count",0),
            "fear_greed":social_data.get("fear_greed",{}),
            "win_probability":round(wp,4),"supporting_articles":sup,
            "generated_at":datetime.utcnow().isoformat()}


def generate_portfolio_recommendation(news, prices, db_session=None, method="risk_parity"):
    from .sentiment_engine import ASSET_KEYWORDS
    from .ml_engine import optimize_portfolio
    from .price_fetcher import get_price_history
    all_recs = {}
    for key in list(ASSET_KEYWORDS.keys()):
        try: all_recs[key] = generate_ai_recommendation(key, news, prices, db_session)
        except Exception: pass
    signal_scores = {k:v["composite_score"] for k,v in all_recs.items()}
    confidences   = {k:v["confidence"]      for k,v in all_recs.items()}
    price_histories = {}
    for key in list(ASSET_KEYWORDS.keys())[:10]:
        try: price_histories[key] = get_price_history(key,"30d",db_session=db_session)
        except Exception: price_histories[key] = []
    portfolio = optimize_portfolio(list(ASSET_KEYWORDS.keys()), signal_scores,
                                   confidences, price_histories, method)
    portfolio["asset_recommendations"] = {
        k:{"signal":all_recs[k]["signal"],"score":all_recs[k]["composite_score"],
           "confidence":all_recs[k]["confidence"],"weight":portfolio["weights"].get(k,0.0)}
        for k in all_recs if k in portfolio.get("weights",{})}
    return portfolio


def _no_data(asset_key, info):
    return {"asset_key":asset_key,"label":info.get("label",asset_key),"icon":info.get("icon","📊"),
            "symbol":info.get("symbol",""),"signal":"HOLD","signal_class":"hold",
            "signal_color":"#94a3b8","action":"HOLD","confidence":0.0,"composite_score":0.0,
            "sentiment_score":0.0,"momentum":0.0,"price_trend_score":0.0,"social_score":0.0,
            "trade_signal":{},"vip_persons":[],"coverage":0,"social_coverage":0,
            "supporting_articles":[],"reason":"No recent news coverage",
            "generated_at":datetime.utcnow().isoformat()}
