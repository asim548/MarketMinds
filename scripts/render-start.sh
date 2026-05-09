#!/usr/bin/env bash
# Render / production entrypoint — threaded Gunicorn + Flask-SocketIO (threading async_mode).
# Do NOT use geventwebsocket / GeventWebSocketWorker here: those packages are not in root requirements.txt.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
# Hydrate RL CSVs before Gunicorn imports app (skip with MM_RL_PREFETCH_AT_BOOT=0 if using persistent disk only).
if [ "${MM_RL_PREFETCH_AT_BOOT:-1}" != "0" ]; then
  python scripts/ensure_rl_training_data.py || true
fi
PORT="${PORT:-5000}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"
exec gunicorn \
  --workers 1 \
  --threads 8 \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
  --keep-alive 5 \
  --bind "0.0.0.0:${PORT}" \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  app:app
