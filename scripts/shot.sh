#!/usr/bin/env bash
# 使い方: shot.sh <URL or file:// > <出力ディレクトリ>
# PC(1366x900)とスマホ(390x844)のファーストビュー画像を作る。
set -euo pipefail
URL="$1"; OUT="$2"; mkdir -p "$OUT"
CHROME="$(ls /opt/pw-browsers/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1 || true)"
[ -z "$CHROME" ] && CHROME="$(command -v chromium chromium-browser google-chrome | head -1)"
run() { "$CHROME" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --virtual-time-budget=6000 --window-size="$1" --screenshot="$2" "$URL" >/dev/null 2>&1; }
run 1366,900 "$OUT/pc.png"
run 390,844  "$OUT/sp.png"
ls -l "$OUT"/*.png
