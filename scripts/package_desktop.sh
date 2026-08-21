#!/usr/bin/env bash
# Build a distributable desktop application.
set -euo pipefail
cd "$(dirname "$0")/../apps/desktop"

echo "Checking prerequisites"
command -v node >/dev/null || { echo "Node.js is required"; exit 1; }
if ! command -v cargo >/dev/null; then
  cat <<'MSG'
Rust is required to build the native desktop application.

  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

You can still run the UI in a browser without it:  npm run dev
MSG
  exit 1
fi

if [ ! -f src-tauri/icons/icon.icns ] && [ ! -f src-tauri/icons/32x32.png ]; then
  echo "Icons are missing. Generate them first:"
  echo "  npm run tauri icon path/to/privia-1024.png"
  exit 1
fi

echo "Installing dependencies"
npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

echo "Type checking and testing"
npm run typecheck
npm run test

echo "Building"
npm run tauri build

echo ""
echo "Artefacts are in apps/desktop/src-tauri/target/release/bundle/"
