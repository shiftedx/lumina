#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
HOST="${LUMINA_HOST:-127.0.0.1}"
PORT="${LUMINA_PORT:-8765}"

# Locked installs only: scripts/bootstrap.py creates backend/.venv from backend/requirements.lock (and runs npm ci).
if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
elif [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/Scripts/python.exe"
else
  python3 "$ROOT_DIR/scripts/bootstrap.py"
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
fi

cd "$BACKEND_DIR"
exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --no-proxy-headers --no-access-log --timeout-keep-alive 30
