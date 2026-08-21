#!/usr/bin/env bash
# Check whether a local model is available, and say what to do if not.
set -euo pipefail

BASE="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
MODEL="${LOCAL_LLM_MODEL:-llama3.1:8b}"

if ! curl -fsS --max-time 3 "${BASE}/api/tags" >/dev/null 2>&1; then
  cat <<MSG
Ollama is not reachable at ${BASE}.

PRIVIA still works: it falls back to a deterministic offline planner that can
run every tool. You lose conversational phrasing, not capability.

To install a local model:
  1. Install Ollama from https://ollama.com
  2. ollama pull ${MODEL}
  3. ollama serve
MSG
  exit 1
fi

if curl -fsS "${BASE}/api/tags" | grep -q "\"${MODEL%%:*}"; then
  echo "Ollama is running and '${MODEL}' is installed."
else
  echo "Ollama is running but '${MODEL}' is not installed."
  echo "  ollama pull ${MODEL}"
  exit 1
fi
