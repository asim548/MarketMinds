#!/usr/bin/env bash
# Render / production entrypoint — threaded Gunicorn + Flask-SocketIO (threading async_mode).
# Do NOT use geventwebsocket / GeventWebSocketWorker here: those packages are not in root requirements.txt.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
# Hydrate RL CSVs before workers fork if RL_*_URL env vars are set (optional).
python scripts/ensure_rl_training_data.py || true
PORT="${PORT:-5000}"
exec gunicorn \
  --workers 1 \
  --threads 8 \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
  --keep-alive 5 \
  --bind "0.0.0.0:${PORT}" \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  app:app
