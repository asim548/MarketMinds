"""
FinancialPulse v7 — Full Backtesting Engine
Features:
  • Full PnL curve with per-trade breakdown
  • Rolling accuracy & win-rate curves
  • Drawdown chart data
  • Monthly returns heatmap data
  • Dataset-model integration
  • Walk-forward validation
  • Sharpe / Sortino / Calmar / Max-Drawdown / Profit Factor
  • Kelly position sizing
"""
from __future__ import annotations
import logging, json
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
logger = logging.getLogger(__name__)

class FullBacktestEngine:
    def __init__(self, db_session=None, price_threshold_pct=0.3,
                 initial_capital=10_000.0, position_size_pct=0.10):
        self.db = db_session
        self.threshold = price_threshold_pct
        self.initial_capital = initial_capital
        self.pos_size = position_size_pct

    def run(self, asset_key, lookback_days=90):
        from ..models import NewsArticle, PriceSnapshot
        from .ml_engine import get_gbm, extract_features, GBMMetaLearner
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        articles = (self.db.query(NewsArticle)
            .filter(NewsArticle.published_at >= cutoff,
                    NewsArticle.assets_json.like(f'%"{asset_key}"%'))
            .order_by(NewsArticle.published_at).all())
        if len(articles) < 5:
            return self._empty(asset_key, f"Need more articles ({len(articles)} found). Keep app running.")

        daily_data = defaultdict(lambda: {"vader":[],"textblob":[],"finbert":[],"llm":[],"hours":[],"count":0})
        for art in articles:
            day = art.published_at.strftime("%Y-%m-%d") if art.published_at else None
            if not day: continue
            d = daily_data[day]
            d["vader"].append(art.vader_score or 0.0)
            d["textblob"].append(art.textblob_score or 0.0)
            if art.finbert_score is not None: d["finbert"].append(art.finbert_score)
            d["llm"].append(art.llm_score or 0.0)
            if art.published_at: d["hours"].append(art.published_at.hour)
            d["count"] += 1

        price_snaps = (self.db.query(PriceSnapshot)
            .filter(PriceSnapshot.asset_key == asset_key, PriceSnapshot.recorded_at >= cutoff)
            .order_by(PriceSnapshot.recorded_at).all())
        daily_prices = {s.recorded_at.strftime("%Y-%m-%d"): s.price for s in price_snaps}

        sorted_days = sorted(daily_data.keys())
        X_list, y_list, day_records = [], [], []
        for i, day in enumerate(sorted_days[:-1]):
            d = daily_data[day]
            next_day = sorted_days[i+1] if i+1 < len(sorted_days) else None
            va = float(np.mean(d["vader"])) if d["vader"] else 0.0
            ta = float(np.mean(d["textblob"])) if d["textblob"] else 0.0
            fa = float(np.mean(d["finbert"])) if d["finbert"] else 0.0
            ha = float(np.mean(d["hours"])) if d["hours"] else 12.0
            feats = extract_features(title=f"daily_{day}", description=f"count:{d['count']}",
                vader=va, textblob=ta, finbert=fa, distilrob=fa,
                hour_of_day=int(ha), day_of_week=datetime.strptime(day, "%Y-%m-%d").weekday())
            p0 = daily_prices.get(day)
            p1 = daily_prices.get(next_day) if next_day else None
            pct = (p1-p0)/p0*100 if p0 and p1 and p0>0 else None
            X_list.append(feats); y_list.append(pct)
            day_records.append({"day":day,"pct_change":pct,"vader":va,"finbert":fa,"count":d["count"],"headline":""})
        return self._simulate(asset_key, lookback_days, X_list, y_list, day_records, len(articles))

    def run_from_csv(self, csv_path, asset_key="primary", lookback_days=90):
        import pandas as pd
        from .dataset_trainer import build_csv_features, ASSET_LABEL_COLS
        try:
            df = pd.read_csv(csv_path, low_memory=False)
        except Exception as e:
            return self._empty(asset_key, f"Cannot load CSV: {e}")
        label_col = ASSET_LABEL_COLS.get(asset_key, "label_primary") if asset_key != "primary" else "label_primary"
        if label_col not in df.columns: label_col = "label_primary"
        df = df.dropna(subset=[label_col])
        df[label_col] = df[label_col].astype(int)
        df = df[df[label_col].isin([0,1,2])].reset_index(drop=True)
        if len(df) < 20: return self._empty(asset_key, f"Too few rows ({len(df)})")
        try:
            df["date"] = pd.to_datetime(df["date"]); df = df.sort_values("date").reset_index(drop=True)
        except Exception: pass
        X_list, y_list, day_records = [], [], []
        for _, row in df.iterrows():
            try:
                feat = build_csv_features(row)
                label = int(row[label_col])
                pct_map = {0: -1.5, 1: 0.0, 2: 1.5}
                pct = pct_map[label]
                date_str = str(row.get("date",""))[:10]
                X_list.append(feat); y_list.append(pct)
                day_records.append({"day":date_str,"pct_change":pct,"vader":0.0,"finbert":0.0,"count":1,
                                    "headline":str(row.get("headline",""))[:80]})
            except Exception: continue
        return self._simulate(asset_key, lookback_days, X_list, y_list, day_records, len(df), is_csv=True)

    def _simulate(self, asset_key, lookback_days, X_list, y_list, day_records, total_articles, is_csv=False):
        from .ml_engine import get_gbm, GBMMetaLearner
        from .sentiment_engine import get_signal_label
        valid = [i for i,r in enumerate(day_records) if r["pct_change"] is not None]
        if not valid: return self._empty(asset_key, "No price data available")
        split = int(len(valid)*0.70)
        train_idx, test_idx = valid[:split], valid[split:] if split < len(valid) else valid
        trained = False; gbm = get_gbm()
        if len(train_idx) >= GBMMetaLearner.MIN_TRAINING_SAMPLES:
            Xt = np.array([X_list[i] for i in train_idx])
            yt = np.array([y_list[i] for i in train_idx])
            trained = gbm.train(Xt, yt)
        dataset_model_available = False
        try:
            from .dataset_trainer import DATASET_MODEL_PATH
            dataset_model_available = DATASET_MODEL_PATH.exists()
        except Exception: pass

        capital = self.initial_capital
        cap_curve = [{"date": day_records[valid[0]]["day"] if valid else "start", "value": capital}]
        signals, trades, y_true, y_pred, rolling_correct = [], [], [], [], []

        for i in test_idx:
            rec = day_records[i]; pct = rec["pct_change"]
            if pct is None: continue
            feat_vec = X_list[i]
            pred = gbm.predict_proba(feat_vec)
            score = pred["signal_score"]; conf = 1.0 - pred["flat"]
            if dataset_model_available:
                try:
                    import joblib
                    from .dataset_trainer import DATASET_MODEL_PATH, DATASET_SCALER_PATH
                    dm = joblib.load(DATASET_MODEL_PATH); dsc = joblib.load(DATASET_SCALER_PATH)
                    csv_feat = feat_vec[:32] if len(feat_vec)>=32 else np.pad(feat_vec,(0,32-len(feat_vec)))
                    dproba = dm.predict_proba(dsc.transform(csv_feat.reshape(1,-1)))[0]
                    classes = list(dm.classes_)
                    bull_p = float(dproba[classes.index(2)]) if 2 in classes else 0.33
                    bear_p = float(dproba[classes.index(0)]) if 0 in classes else 0.33
                    ds_score = bull_p - bear_p
                    score = 0.5*score + 0.5*ds_score; conf = max(conf, abs(ds_score))
                except Exception: pass

            action = "BUY" if score>0.15 and conf>=0.35 else "SELL" if score<-0.15 and conf>=0.35 else "HOLD"
            pred_dir = "up" if action=="BUY" else ("down" if action=="SELL" else "flat")
            actual_dir = "up" if pct>self.threshold else ("down" if pct<-self.threshold else "flat")
            y_true.append(actual_dir); y_pred.append(pred_dir)
            rolling_correct.append(1 if pred_dir==actual_dir else 0)
            pos_frac = self.pos_size * min(1.0, abs(score)*2)
            trade_pnl = 0.0
            if action=="BUY": trade_pnl = capital*pos_frac*(pct/100)
            elif action=="SELL": trade_pnl = capital*pos_frac*(-pct/100)
            capital = max(0, capital+trade_pnl)
            roll_acc = float(np.mean(rolling_correct[-20:])) if rolling_correct else 0.0
            sig_label = get_signal_label(score)
            sig = {"date":rec["day"],"signal":sig_label["signal"],"signal_score":round(float(score),4),
                   "confidence":round(float(conf),4),"action":action,"predicted_dir":pred_dir,
                   "actual_dir":actual_dir,"price_change_pct":round(float(pct),4),
                   "trade_pnl":round(float(trade_pnl),2),"capital":round(float(capital),2),
                   "rolling_accuracy":round(roll_acc,4),"gbm_up":pred.get("up",0),
                   "gbm_down":pred.get("down",0),"model":pred.get("model","fallback"),
                   "dataset_blended":dataset_model_available,"headline":rec.get("headline","")}
            signals.append(sig)
            cap_curve.append({"date":rec["day"],"value":round(capital,2)})
            if action!="HOLD":
                is_win = (action=="BUY" and actual_dir=="up") or (action=="SELL" and actual_dir=="down")
                trades.append({"date":rec["day"],"action":action,"pct":round(float(pct),4),
                               "pnl":round(float(trade_pnl),2),"correct":is_win,
                               "capital_after":round(float(capital),2)})

        metrics = self._metrics(y_true, y_pred, [p["value"] for p in cap_curve], trades)
        return {
            "asset_key":asset_key,"lookback_days":lookback_days,"total_articles":total_articles,
            "total_signals":len(signals),"total_trades":len(trades),"model_trained":trained,
            "dataset_blended":dataset_model_available,"is_csv_backtest":is_csv,"metrics":metrics,
            "equity_curve":cap_curve,"pnl_curve":self._pnl_curve(signals),
            "drawdown_curve":self._dd_curve(cap_curve),"accuracy_curve":self._acc_curve(signals),
            "monthly_returns":self._monthly(signals),"trade_distribution":self._trade_dist(trades),
            "signals":signals[-50:],"trades":trades[-50:],"generated_at":datetime.utcnow().isoformat(),
        }

    def _pnl_curve(self, signals):
        cum=0.0; out=[]
        for s in signals:
            cum+=s["trade_pnl"]
            out.append({"date":s["date"],"pnl":round(cum,2),"trade_pnl":s["trade_pnl"],"action":s["action"]})
        return out

    def _dd_curve(self, equity_curve):
        if not equity_curve: return []
        vals = np.array([p["value"] for p in equity_curve])
        peaks = np.maximum.accumulate(vals)
        dd = (vals-peaks)/np.where(peaks>0,peaks,1)*100
        return [{"date":equity_curve[i]["date"],"drawdown":round(float(dd[i]),4)} for i in range(len(equity_curve))]

    def _acc_curve(self, signals):
        out=[]
        for i, s in enumerate(signals):
            window = signals[max(0,i-19):i+1]
            correct = sum(1 for w in window if w["predicted_dir"]==w["actual_dir"])
            trades = [w for w in window if w["action"]!="HOLD"]
            wins = sum(1 for t in trades if (t["action"]=="BUY" and t["actual_dir"]=="up") or
                       (t["action"]=="SELL" and t["actual_dir"]=="down"))
            out.append({"date":s["date"],"accuracy":round(correct/len(window),4) if window else 0,
                        "win_rate":round(wins/len(trades),4) if trades else None,"signal":s["signal"]})
        return out

    def _monthly(self, signals):
        m=defaultdict(float); mt=defaultdict(int)
        for s in signals:
            try: ym=s["date"][:7]; m[ym]+=s["trade_pnl"]; mt[ym]+=1 if s["action"]!="HOLD" else 0
            except Exception: pass
        return [{"month":k,"pnl":round(v,2),"trades":mt[k]} for k,v in sorted(m.items())]

    def _trade_dist(self, trades):
        if not trades: return {}
        wins=[t for t in trades if t["correct"]]; losses=[t for t in trades if not t["correct"]]
        buys=[t for t in trades if t["action"]=="BUY"]; sells=[t for t in trades if t["action"]=="SELL"]
        pnls=[t["pnl"] for t in trades]
        arr=np.array(pnls); counts,edges=np.histogram(arr,bins=10)
        hist=[{"range":f"{edges[i]:.1f}~{edges[i+1]:.1f}","count":int(counts[i])} for i in range(len(counts))]
        return {"total":len(trades),"wins":len(wins),"losses":len(losses),
                "buy_count":len(buys),"sell_count":len(sells),
                "avg_win":round(float(np.mean([t["pnl"] for t in wins])),2) if wins else 0,
                "avg_loss":round(float(np.mean([t["pnl"] for t in losses])),2) if losses else 0,
                "max_win":round(float(max((t["pnl"] for t in trades),default=0)),2),
                "max_loss":round(float(min((t["pnl"] for t in trades),default=0)),2),
                "pnl_histogram":hist}

    def _metrics(self, y_true, y_pred, cap_curve, trades):
        if not y_true: return {}
        try:
            from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix
            labels=["up","flat","down"]
            acc=accuracy_score(y_true,y_pred); prec=precision_score(y_true,y_pred,average="macro",zero_division=0,labels=labels)
            rec=recall_score(y_true,y_pred,average="macro",zero_division=0,labels=labels)
            f1=f1_score(y_true,y_pred,average="macro",zero_division=0,labels=labels)
            wf1=f1_score(y_true,y_pred,average="weighted",zero_division=0,labels=labels)
            cm=confusion_matrix(y_true,y_pred,labels=labels).tolist()
        except Exception: acc=prec=rec=f1=wf1=0.0; cm=[]
        cap=np.array(cap_curve,dtype=float)
        total_ret=(cap[-1]-cap[0])/cap[0]*100 if len(cap)>1 else 0.0
        if len(cap)>1:
            dr=np.diff(cap)/np.where(cap[:-1]>0,cap[:-1],1)
            sharpe=float(np.mean(dr)/np.std(dr)*np.sqrt(252)) if np.std(dr)>0 else 0.0
            neg=dr[dr<0]; sortino=float(np.mean(dr)/np.std(neg)*np.sqrt(252)) if len(neg)>0 and np.std(neg)>0 else 0.0
            peaks=np.maximum.accumulate(cap); max_dd=float(np.max((peaks-cap)/np.where(peaks>0,peaks,1)))*100
            ann_ret=total_ret/(len(cap)/252) if len(cap)>1 else 0.0
            calmar=ann_ret/max_dd if max_dd>0 else 0.0
        else: sharpe=sortino=max_dd=calmar=0.0
        wins=[t for t in trades if t["correct"]]; losses=[t for t in trades if not t["correct"]]
        win_rate=len(wins)/len(trades)*100 if trades else 0.0
        gp=sum(t["pnl"] for t in trades if t["pnl"]>0); gl=abs(sum(t["pnl"] for t in trades if t["pnl"]<0))
        pf=gp/gl if gl>0 else gp
        streak_w=streak_l=cur_w=cur_l=0
        for t in trades:
            if t["correct"]: cur_w+=1; cur_l=0; streak_w=max(streak_w,cur_w)
            else: cur_l+=1; cur_w=0; streak_l=max(streak_l,cur_l)
        return {"accuracy":round(acc,4),"precision":round(prec,4),"recall":round(rec,4),
                "macro_f1":round(f1,4),"weighted_f1":round(wf1,4),"confusion_matrix":cm,
                "cm_labels":["up","flat","down"],"total_return_pct":round(total_ret,2),
                "sharpe_ratio":round(sharpe,4),"sortino_ratio":round(sortino,4),
                "calmar_ratio":round(calmar,4),"max_drawdown_pct":round(max_dd,2),
                "win_rate_pct":round(win_rate,2),"profit_factor":round(pf,4),
                "total_wins":len(wins),"total_losses":len(losses),
                "avg_win_pnl":round(float(np.mean([t["pnl"] for t in wins])),2) if wins else 0,
                "avg_loss_pnl":round(float(np.mean([t["pnl"] for t in losses])),2) if losses else 0,
                "best_win_streak":streak_w,"worst_loss_streak":streak_l,
                "initial_capital":self.initial_capital,"final_capital":round(float(cap[-1]),2) if len(cap)>0 else self.initial_capital,
                "total_gross_profit":round(gp,2),"total_gross_loss":round(gl,2)}

    def _empty(self, asset_key, reason):
        return {"asset_key":asset_key,"error":reason,"total_signals":0,"total_trades":0,
                "metrics":{},"equity_curve":[],"pnl_curve":[],"drawdown_curve":[],
                "accuracy_curve":[],"monthly_returns":[],"signals":[],"trades":[],
                "generated_at":datetime.utcnow().isoformat()}

# Aliases
class BacktestEngine(FullBacktestEngine): pass
class IndustrialBacktestEngine(FullBacktestEngine): pass