#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_ROOT="/Users/jiaxin/doc_unsyn/TickTick_Codex"

rsync -az \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude 'assistant.db' \
  --exclude 'logs/' \
  --exclude '.pytest_cache' \
  "${REPO_ROOT}/" "studio:${REMOTE_ROOT}/"
