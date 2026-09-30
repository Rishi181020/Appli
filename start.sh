#!/usr/bin/env bash
# Appli for Mac/Linux (Windows: start.bat). The first time it installs everything it needs (a few minutes).
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "First time: fill in the 3 lines at the top of .env, then run ./start.sh again."
  echo
  (command -v open >/dev/null && open -t .env) || (command -v xdg-open >/dev/null && xdg-open .env) || true
  exit 0
fi

command -v python3 >/dev/null || { echo "Python 3.11+ is required: https://www.python.org/downloads/"; exit 1; }
command -v npm >/dev/null || { echo "Node.js 18+ is required: https://nodejs.org/"; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python (first run only)..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi

if [ ! -d dashboard/node_modules ]; then
  echo "Installing dashboard dependencies (first run only)..."
  (cd dashboard && npm install --no-audit --no-fund)
fi
echo "Building dashboard..."
(cd dashboard && npm run build)
.venv/bin/python -m runner serve
