#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

docker compose up -d postgres
.venv/bin/alembic upgrade head

.venv/bin/uvicorn ticktick_telegram_assistant.app:create_app \
  --factory \
  --host 127.0.0.1 \
  --port 8000 \
  >> logs/api.stdout.log 2>> logs/api.stderr.log &
API_PID=$!

cleanup() {
  kill "$API_PID" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

.venv/bin/python -m ticktick_telegram_assistant.runner
