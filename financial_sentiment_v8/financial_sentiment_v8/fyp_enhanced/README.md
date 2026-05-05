# FinancialPulse v7 — AI-Powered Financial Sentiment Platform

## What's New in v7

### 🔬 Full Backtesting Engine
- **6 Interactive Charts**: Equity Curve, Cumulative P&L, Drawdown, Rolling Accuracy/Win-Rate, Monthly Returns Heatmap, Trade P&L Distribution
- **Complete Risk Metrics**: Sharpe, Sortino, Calmar, Max Drawdown, Profit Factor, Win Rate
- **Walk-Forward Validation**: 70% train / 30% test split, out-of-sample evaluation
- **Kelly Position Sizing**: Conviction-weighted trade sizing
- **Trade Log**: Full per-trade breakdown with win/loss tracking
- **Confusion Matrix**: Visual prediction accuracy by direction
- **Streak Analysis**: Best win streak, worst loss streak
- **Monthly P&L Heatmap**: Aggregate returns by calendar month

### 📂 CSV Dataset Training
- Upload your `data.csv` (100k+ rows supported) directly in the Backtest tab
- Trains a **Hybrid ML Model** (GBM + RandomForest + Logistic Regression VotingClassifier)
- Real-time training progress bar with live status polling
- Feature importances visualization
- Per-class accuracy breakdown (bearish / neutral / bullish)
- Once trained, the dataset model blends with the live GBM for better predictions

### Expected CSV Format (`data.csv`)
```
row_id, date, split, is_real_headline, source, headline, full_text,
word_count, asset_tags, primary_sentiment,
sentiment_crypto, sentiment_forex, sentiment_dxy, sentiment_gold,
sentiment_commodity, sentiment_oil, sentiment_stock,
label_primary, label_crypto, label_forex, label_dxy, label_gold,
label_commodity, label_oil, label_stock
```
- **Labels**: 0=bearish, 1=neutral, 2=bullish
- **Sentiments**: bearish / neutral / bullish

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env with your API keys

# 3. Train models once (offline, on your machine)
python train_models.py --csv data/data.csv --target label_primary

# 4. Run app (load-only, no startup training)
python app.py
```

Open http://localhost:5000

---

## Backtest Usage

### Live DB Mode
1. Go to **🔬 Backtest** tab
2. Select asset, days, capital, position size
3. Ensure mode = "🔴 Live DB Data"
4. Click **Run Full Backtest**

### CSV Dataset Mode
1. In the **📂 Dataset Training** panel, click "Upload data.csv"
2. Click **🚀 Upload & Train Hybrid Model** — wait for training to complete (1-3 min for 100k rows)
3. Switch Backtest Mode to "📁 CSV Dataset"
4. Select target asset (primary / crypto / gold / etc.)
5. Click **Run Full Backtest** — uses your CSV + trained hybrid model

### Output Charts
| Chart | What it shows |
|-------|--------------|
| Equity Curve | Portfolio value over time |
| Cumulative P&L | Running profit/loss |
| Drawdown | % loss from peak |
| Rolling Accuracy | 20-period ML prediction accuracy + win rate |
| Monthly P&L | Bar chart of monthly returns |
| Trade Distribution | Histogram of trade P&L outcomes |

---

## Architecture

```
FinancialPulse v7
├── app.py                    — Flask API + WebSocket server
├── models.py                 — SQLAlchemy DB models
├── utils/
│   ├── backtesting.py        — FullBacktestEngine (v7 — all charts)
│   ├── dataset_trainer.py    — CSV → Hybrid ML trainer
│   ├── ml_engine.py          — GBM meta-learner + feature engineering
│   ├── sentiment_engine.py   — VADER + TextBlob + FinBERT pipeline
│   ├── news_fetcher.py       — RSS + NewsAPI fetcher
│   ├── price_fetcher.py      — Price data fetcher
│   ├── analytics.py          — Correlation + momentum analytics
│   ├── evaluation.py         — Benchmark evaluation
│   └── alert_system.py       — WhatsApp/Telegram/Email alerts
├── static/js/app.js          — Full frontend (backtesting UI + charts)
└── templates/index.html      — Dashboard HTML
```

## API Endpoints (New in v7)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/backtest` | GET | Run live DB backtest with full metrics |
| `/api/backtest/csv` | POST | Run backtest on uploaded CSV |
| `/api/dataset/upload` | POST | Upload CSV & start hybrid model training |
| `/api/dataset/status` | GET | Training progress (poll every 2s) |
| `/api/dataset/meta` | GET | Last trained model metadata |
| `/api/signals/generated` | GET | Stored generated signals (pending/evaluated) |
| `/api/auto-backtest/run` | POST | Manually run pending 24h evaluations |
| `/api/auto-backtest/summary` | GET | PnL, equity, win ratio, accuracy, drawdown, profitability |

## Automated 24h Backtesting Flow

1. Live data is collected (news + prices).
2. Per-asset signals are generated and saved to `generated_signals`.
3. Each signal stores timestamp, asset, direction, confidence, and entry price.
4. After 24 hours, the evaluator fetches fresh market prices.
5. Predicted direction is compared with realized move.
6. Results are written to `signal_backtests` with movement %, correctness, and PnL.
7. Summary metrics and curves are available from `/api/auto-backtest/summary`.

