# MarketMinds.ai

MarketMinds.ai is an AI-powered market intelligence and trading decision platform built with Flask.  
It combines real-time market data, machine learning predictions, financial sentiment analysis, and reinforcement learning workflows into a single application.

## Core Features

- Unified dashboard for equities, crypto, forex, and market overview.
- User authentication system with profile management and optional Google OAuth.
- AI Picks module for directional buy/hold signal generation from hybrid ML scoring.
- Chart AI and pattern recognition workflows for image-based chart analysis.
- Reinforcement Learning (Double DQN) training, inference, monitoring, and production safety controls.
- Full FinancialPulse integration (news, sentiment, analytics, WebSocket updates, and API bridging).

## AI and Model Components

### 1) Hybrid Prediction Stack (`utils/ai_predictor.py`)
- Uses pre-trained unified models from `models_unified/`.
- Produces confidence-weighted market signals.
- Incorporates economic surprise features (CPI, NFP, FOMC, etc.) into scoring.

### 2) Pattern Recognition Model (`utils/pattern_recognition.py`)
- Detects chart patterns from uploaded chart images.
- Supports common image formats (`png`, `jpg`, `jpeg`, `gif`).

### 3) Reinforcement Learning Agent (`rl/`)
- Double DQN-based agent with training/inference pipeline:
  - `rl/train.py`
  - `rl/inference.py`
  - `rl/dqn_agent.py`
  - `rl/trading_env.py`
- Supports:
  - Manual and scheduled auto-training
  - Model/scaler checkpoint loading
  - Signal generation APIs
  - Production circuit-breaker and paper-trade safeguards

### 4) Financial Sentiment + Backtesting Engine (`financial_sentiment_v8/.../fyp_enhanced`)
- Sentiment and signal infrastructure:
  - `sentiment_engine.py`
  - `ml_engine.py`
  - `dataset_trainer.py`
  - `backtesting.py`
  - `automated_backtesting.py`
- Includes:
  - Dataset-driven hybrid model training
  - Walk-forward and 24h evaluation workflows
  - Metrics/analytics and alerting components

## Project Structure

```text
MarketMinds.ai/
├── app.py
├── requirements.txt
├── database/
├── rl/
├── utils/
├── templates/
├── static/
└── financial_sentiment_v8/
```

## Main Application Routes

- `GET /dashboard` - Main authenticated dashboard.
- `GET /ai_picks` - AI-generated market picks.
- `GET /chart_ai` - Chart analysis interface.
- `GET /pattern_recognition` - Pattern recognition UI.
- `GET /rl_trading` - RL agent control and monitoring dashboard.
- `GET /financialpulse` - FinancialPulse sentiment platform UI.

## RL API Highlights

- `GET /api/rl/status` - RL model status, validation metrics, training state.
- `POST /api/rl/train` - Start RL training run.
- `POST /api/rl/signal` - Generate RL signal payloads.
- `GET /api/rl/production` - Circuit and paper-trade status.
- `POST /api/rl/circuit/reset` - Reset production circuit state.

## Quick Start

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables in `.env` (example keys: `SECRET_KEY`, optional Google OAuth keys).
4. Run the app:

```bash
python app.py
```

5. Open:
- `http://localhost:5000/dashboard`
- `http://localhost:5000/financialpulse`
- `http://localhost:5000/rl_trading`

## Deployment Readiness

- Keep secrets in environment variables (never commit `.env`).
- Ensure model folders/artifacts are present on the target host (`models_unified`, RL models where required).
- Use a production process manager and configure persistent storage for DB/model state.
- Confirm WebSocket compatibility in the target runtime for FinancialPulse updates.

### Render.com (Web service)

In the Render dashboard, set **Start Command** to match the repo (do not use `GeventWebSocketWorker` unless you add `gevent-websocket` yourself):

```bash
bash scripts/render-start.sh
```

Equivalent one-liner:

```bash
gunicorn --workers 1 --threads 8 --bind 0.0.0.0:$PORT app:app
```

Socket.IO runs in **threading** mode on Render by default (`SOCKETIO_ASYNC_MODE` optional). Using `--worker-class geventwebsocket...` without installing `gevent-websocket` produces `ModuleNotFoundError: No module named 'geventwebsocket'`.

**502 Bad Gateway** usually means the web process never listened on `$PORT` or crashed at boot. Check **Logs** for tracebacks (database URL / `create_all`, import errors, OOM). Confirm **Root Directory** points at the folder that contains `app.py`. Probe **`/health`** (must return `{"ok":true}`). Postgres on Render often needs `sslmode=require` in the URL.

## Repository

GitHub: [asim548/MarketMinds](https://github.com/asim548/MarketMinds.git)
