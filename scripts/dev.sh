#!/usr/bin/env bash
# Start the whole app for local development: the API and the Vite dev server
# for the UI, wired together by ui/vite.config.ts's proxy (frontend on :5173,
# backend on :8787 by default).
#
# Note: `crucible serve --reload` currently errors (uvicorn needs an import
# string for autoreload, and `crucible serve` passes an app object), so this
# runs the API without autoreload — restart the script after backend changes.
#
#   scripts/dev.sh
#
# Ctrl-C stops both. Needs the API extra installed once:
#   pip install -e '.[api]'
# and UI deps installed once:
#   npm --prefix ui install
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${CRUCIBLE_API_PORT:-8787}"
API_HOST="${CRUCIBLE_API_HOST:-127.0.0.1}"

pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "crucible api    http://${API_HOST}:${API_PORT}  (docs: /docs)"
crucible serve --host "$API_HOST" --port "$API_PORT" --no-ui &
pids+=("$!")

echo "crucible ui     http://localhost:5173"
npm --prefix ui run dev &
pids+=("$!")

wait -n "${pids[@]}" 2>/dev/null || wait "${pids[@]}"
