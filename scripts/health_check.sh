#!/usr/bin/env bash
# Check a running PRIVIA instance. Exits non-zero if it is unhealthy.
set -euo pipefail

HOST="${PRIVIA_HOST:-127.0.0.1}"
PORT="${PRIVIA_PORT:-8756}"
BASE="http://${HOST}:${PORT}"

if ! command -v curl >/dev/null; then
  echo "curl is required"; exit 2
fi

echo "Checking ${BASE}"
if ! RESPONSE=$(curl -fsS --max-time 5 "${BASE}/health" 2>/dev/null); then
  echo "  UNREACHABLE - is the backend running? (make dev-api)"
  exit 1
fi

echo "  ${RESPONSE}"
case "$RESPONSE" in
  *'"status":"ok"'*)       echo "  healthy"; exit 0 ;;
  *'"status":"degraded"'*) echo "  degraded but usable"; exit 0 ;;
  *)                       echo "  unhealthy"; exit 1 ;;
esac
