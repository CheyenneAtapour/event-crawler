#!/usr/bin/env bash
set -e

echo "=== San Diego Event Crawler setup ==="

# Python deps
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install Playwright browsers (Chromium only — for Meetup scraper)
playwright install chromium

echo ""
echo "=== Setup complete ==="
echo "Run:  cd backend && source .venv/bin/activate && uvicorn main:app --reload"
echo "Open: http://localhost:8000"
