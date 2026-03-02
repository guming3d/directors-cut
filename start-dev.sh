#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_REQUIRE_AUTH="${SORA_WORKFLOW_REQUIRE_AUTH:-}"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required but was not found in PATH." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "python/python3 is required but was not found in PATH." >&2
    exit 1
  fi
fi

BACKEND_PID=""
FRONTEND_PID=""

if [[ -z "$BACKEND_REQUIRE_AUTH" ]] \
  && [[ -z "${SORA_WORKFLOW_API_TOKEN:-}" ]] \
  && [[ -z "${SORA_WORKFLOW_USERNAME:-}" ]] \
  && [[ -z "${SORA_WORKFLOW_PASSWORD:-}" ]]; then
  BACKEND_REQUIRE_AUTH="0"
  echo "No backend auth env found; using SORA_WORKFLOW_REQUIRE_AUTH=0 for local dev."
fi

cleanup() {
  local code=$?
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  wait >/dev/null 2>&1 || true
  exit "$code"
}

trap cleanup EXIT INT TERM

echo "Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "$ROOT_DIR"
  if [[ -n "$BACKEND_REQUIRE_AUTH" ]]; then
    export SORA_WORKFLOW_REQUIRE_AUTH="$BACKEND_REQUIRE_AUTH"
  fi
  exec "$PYTHON_BIN" -m uvicorn video_agent_workflow:fastapi_app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

echo "Starting frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT}"
(
  cd "$ROOT_DIR/frontend"
  exec npm run dev -- --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

set +e
wait -n "$BACKEND_PID" "$FRONTEND_PID"
status=$?
set -e
exit "$status"
