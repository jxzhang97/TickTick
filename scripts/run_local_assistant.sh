#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${REPO_ROOT}/src"

mkdir -p logs

DATABASE_URL_VALUE="${DATABASE_URL:-}"
if [[ -z "$DATABASE_URL_VALUE" ]] && [[ -f .env ]]; then
  DATABASE_URL_VALUE="$(grep '^DATABASE_URL=' .env | tail -1 | cut -d= -f2-)"
fi

if [[ -n "$DATABASE_URL_VALUE" ]]; then
  export DATABASE_URL="$DATABASE_URL_VALUE"
fi

if [[ "$DATABASE_URL_VALUE" == postgresql* ]]; then
  if command -v docker >/dev/null 2>&1; then
    docker compose up -d postgres
  else
    echo "DATABASE_URL points to Postgres but docker is unavailable." >&2
    echo "Set DATABASE_URL=sqlite:///./assistant.db for Docker-free local hosting." >&2
    exit 1
  fi
fi

.venv/bin/alembic upgrade head

.venv/bin/uvicorn ticktick_telegram_assistant.app:create_app \
  --factory \
  --host 127.0.0.1 \
  --port 8000 \
  >> logs/api.stdout.log 2>> logs/api.stderr.log &
API_PID=$!

.venv/bin/python -m ticktick_telegram_assistant.runner \
  >> logs/runner.stdout.log 2>> logs/runner.stderr.log &
RUNNER_PID=$!

cleanup() {
  kill "$RUNNER_PID" >/dev/null 2>&1 || true
  kill "$API_PID" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

while true; do
  if ! kill -0 "$API_PID" >/dev/null 2>&1; then
    wait "$API_PID" >/dev/null 2>&1 || true
    echo "API process exited unexpectedly." >&2
    exit 1
  fi
  if ! kill -0 "$RUNNER_PID" >/dev/null 2>&1; then
    wait "$RUNNER_PID" >/dev/null 2>&1 || true
    echo "Runner process exited unexpectedly." >&2
    exit 1
  fi
  sleep 2
done
