# FinancialPulse v4 — Setup Guide

## What's New in v4
- ✅ **WebSocket real-time dashboard** (Socket.IO) — prices & news pushed live
- ✅ **AI BUY/SELL/HOLD signals** — ensemble ML model (sentiment + momentum + price trend + coverage), zero hard-coded keywords
- ✅ **High-priority news feed** — AI impact scoring (recency × authority × confidence)
- ✅ **Backtesting UI** — equity curve + per-signal P&L table
- ✅ **AI Recommendations panel** — confidence score, supporting evidence
- ✅ **Alert system** — Telegram + Email + WhatsApp (Twilio)
- ✅ **Modern UI** — Tailwind CSS, dark theme, responsive
- ✅ **No yfinance** — CoinGecko, Frankfurter, Yahoo Finance v8 HTTP (no library)
- ✅ **Zero errors** — all 12 Python files pass syntax check

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Train .pkl models once (offline)
python train_models.py --csv data/data.csv --target label_primary

# 4. Run
python app.py
# Open http://localhost:5000
```

## Training Notes

- `app.py` only loads pre-trained models from `ml_models/*.pkl`.
- No automatic training is executed at startup.
- Use `data/sample_data.csv` as a schema reference for your full dataset.

## Alert Configuration

### Telegram
1. Create bot via @BotFather → get token
2. Add `TELEGRAM_BOT_TOKEN=xxx` to `.env`
3. In the Alerts tab, enter your Telegram chat_id as destination

### Email (Gmail)
1. Enable 2FA on Gmail account
2. Go to Google Account → Security → App Passwords → create one
3. Add `SMTP_USER`, `SMTP_PASS` to `.env`

### WhatsApp (Twilio)
1. Sign up at twilio.com (free trial available)
2. Enable WhatsApp Sandbox in Twilio Console
3. Add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` to `.env`
4. In Alerts tab, enter phone number as destination: `+923001234567`

## AI Signal Logic

The BUY/SELL/HOLD engine uses a 4-factor ensemble — **no hard-coded keywords**:

| Factor | Weight | Source |
|--------|--------|--------|
| Sentiment Score | 40% | 5-layer NLP ensemble (VADER + TextBlob + Keyword + FinBERT + LLM) |
| Momentum | 25% | Velocity of recent vs historical sentiment |
| Price Trend | 15% | Live price change% normalised to [-1, +1] |
| Coverage Weight | ×bonus | Article count (log scale) |

Confidence gating: signals below 45% confidence are forced to HOLD.

## Price Data Sources (no yfinance)
- **Crypto**: CoinGecko API (free, no key needed)
- **Forex**: Frankfurter API (free, ECB data)
- **Stocks/Indices/Commodities**: Yahoo Finance v8 JSON (direct HTTP)
- **Fallback**: Static defaults if all APIs fail — never crashes
