#!/usr/bin/env python3
"""PC/スマホのファーストビュー画像を撮る。演出(アニメーション)が落ち着くまで待つ。

使い方: python3 shot.py <URL or file:// or path> <出力ディレクトリ> [待機秒=5]
"""
import glob
import os
import sys

from playwright.sync_api import sync_playwright

target, out = sys.argv[1], sys.argv[2]
wait_s = float(sys.argv[3]) if len(sys.argv) > 3 else 5
if not target.startswith(("http://", "https://", "file://")):
    target = "file://" + os.path.abspath(target)
os.makedirs(out, exist_ok=True)
exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") or [None])[0]

VIEWS = {"pc": dict(viewport={"width": 1366, "height": 900}),
         "sp": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                    is_mobile=True, has_touch=True,
                    # サイトによっては UA でスマホ版/PC版を出し分ける。UA を実機相当にしないと
                    # PC 版を縮小した画面を撮ってしまい、誤診につながる(実際に起きた)。
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
    for name, opts in VIEWS.items():
        pg = b.new_context(**opts).new_page()
        console = []
        pg.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        pg.goto(target, wait_until="load", timeout=30000)
        pg.wait_for_timeout(int(wait_s * 1000))
        pg.screenshot(path=f"{out}/{name}_fv.png")               # ファーストビュー
        # スクロールで表示される要素(遅延読み込み・アニメーション)を先に描画させる。
        # しないと全体画像に空白が写り、「コンテンツがない」と誤診する(実際に起きた)。
        total = pg.evaluate("document.documentElement.scrollHeight")
        y = 0
        while y < total:
            pg.evaluate(f"window.scrollTo(0,{y})")
            pg.wait_for_timeout(350)
            y += opts["viewport"]["height"] // 2
            total = pg.evaluate("document.documentElement.scrollHeight")
        pg.evaluate("window.scrollTo(0,0)")
        pg.wait_for_timeout(800)
        pg.screenshot(path=f"{out}/{name}_full.png", full_page=True)
        h = pg.evaluate("document.documentElement.scrollHeight")
        overflow = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        small = pg.evaluate("""() => [...document.querySelectorAll('a,button')]
            .filter(e => {const r=e.getBoundingClientRect(); return r.width>0 && (r.height<32||r.width<32)}).length""")
        print("  errors:", console[:4]); print(f"{name}: page_height={h}px horizontal_overflow={overflow} small_tap_targets={small} console_errors={len(console)}")
    b.close()
