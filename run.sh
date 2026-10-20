#!/usr/bin/env bash
# macOS / Linux 用の起動スクリプト
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi
echo "http://127.0.0.1:8000 をブラウザで開いてください(停止は Ctrl+C)"
.venv/bin/python app/server.py
