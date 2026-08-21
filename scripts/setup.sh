#!/usr/bin/env bash
# One-command setup for a fresh clone.
set -euo pipefail

cd "$(dirname "$0")/.."
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/6  Checking prerequisites"
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')
[ "$PY_OK" = "1" ] || { echo "Python 3.10 or newer is required"; exit 1; }
python3 --version
if command -v node >/dev/null; then node --version; else
  echo "  note: Node.js is not installed, so the desktop UI cannot be built."
fi

say "2/6  Creating the virtual environment"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip setuptools wheel

say "3/6  Installing Python dependencies"
./.venv/bin/pip install --quiet -e ".[dev]"

say "4/6  Preparing configuration"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  created .env from the example (every value is optional)"
else
  echo "  .env already exists, leaving it alone"
fi

say "5/6  Applying database migrations"
./.venv/bin/python -m privia_storage.cli up

say "6/6  Installing UI dependencies"
if command -v npm >/dev/null; then
  (cd apps/desktop && npm install --no-audit --no-fund --silent)
else
  echo "  skipped: npm is not installed"
fi

say "Done."
cat <<'NEXT'
Next steps:

  make doctor      check the installation
  make dev         run the backend and UI together

Optional, for a local language model:

  ollama pull llama3.1:8b
  ollama serve

PRIVIA works without it, using a deterministic offline planner.
NEXT
