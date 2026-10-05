#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
MODE="${1:-dev}"
HOST="${LUMINA_HOST:-127.0.0.1}"
BACKEND_PORT="${LUMINA_PORT:-8765}"
FRONTEND_PORT="${VITE_PORT:-5173}"
BACKEND_PUBLIC_URL="http://$HOST:$BACKEND_PORT"
FRONTEND_PUBLIC_URL="http://$HOST:$FRONTEND_PORT"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

open_url() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v start >/dev/null 2>&1; then
    start "$url" >/dev/null 2>&1 || true
  fi
}

require_cmd python3
require_cmd node
require_cmd npm

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "warning: ffmpeg is not installed; some formats and post-processing features will fail." >&2
fi

if [[ "$MODE" == "--prod" || "$MODE" == "prod" ]]; then
  export LUMINA_FRONTEND_PUBLIC_URL="${LUMINA_FRONTEND_PUBLIC_URL:-$BACKEND_PUBLIC_URL}"
else
  export LUMINA_FRONTEND_PUBLIC_URL="${LUMINA_FRONTEND_PUBLIC_URL:-$FRONTEND_PUBLIC_URL}"
fi
export VITE_API_BASE_URL="${VITE_API_BASE_URL:-$BACKEND_PUBLIC_URL}"

cleanup() {
  if [[ -n "${FRONTEND_PID:-}" ]]; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${BACKEND_PID:-}" ]]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

"$ROOT_DIR/scripts/run-backend.sh" &
BACKEND_PID=$!

cd "$FRONTEND_DIR"
[[ -d node_modules ]] || npm ci

if [[ "$MODE" == "--prod" || "$MODE" == "prod" ]]; then
  npm run build
  sleep 2
  open_url "http://$HOST:$BACKEND_PORT"
  wait "$BACKEND_PID"
else
  npm run dev -- --host "$HOST" --port "$FRONTEND_PORT" &
  FRONTEND_PID=$!
  sleep 3
  open_url "http://$HOST:$FRONTEND_PORT"
  wait "$FRONTEND_PID"
fi
