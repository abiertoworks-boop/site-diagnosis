"""改善後のレイアウトのイメージ画像を作る。

実在のページを開き、診断で見つかった問題に対応する変更を、ブラウザ上だけで重ねて撮影する。
- 文言はサイト自身の文言(meta description など)だけを使い、新しい主張を作らない。
- ページの作り方(絶対配置のサイトビルダーなど)に左右されないよう、追加する要素は
  文書座標に重ねる(既存の要素を動かさない)。
- 変更した箇所には「改善案」の目印をつける。

これは『イメージ』であり、実際の制作物ではない。
"""
import os

from PIL import Image, ImageDraw, ImageFont

from capture import PHONE_RE, stable_screenshot

FONT_PATHS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "ipag.ttf"),
              "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
              "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
              "C:/Windows/Fonts/meiryo.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"]

PATCH_JS = r"""
(P) => {
  const doc = document.documentElement;
  const badge = (r) => { const b = document.createElement('div'); b.textContent = '改善案';
    b.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${Math.max(r.top + scrollY - 13, 0)}px;background:#ff6a00;color:#fff;font:700 11px/1 sans-serif;padding:3px 6px;border-radius:3px;z-index:2147483600;pointer-events:none`;
    document.body.appendChild(b); };
  const mark = (e) => { e.style.setProperty('outline', '3px dashed #ff6a00', 'important'); e.style.setProperty('outline-offset', '2px', 'important'); badge(e.getBoundingClientRect()); };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

  // 1) 電話番号をタップできるリンクにする
  if (P.telify) {
    const re = new RegExp(P.phoneRe, 'g'), nodes = [];
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) { const p = n.parentElement;
      if (p && !['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(p.tagName) && !p.closest('a') && re.test(n.nodeValue)) nodes.push(n); re.lastIndex = 0; }
    for (const n of nodes) { const span = document.createElement('span');
      span.innerHTML = n.nodeValue.replace(re, (m) => `<a href="tel:${m.replace(/\D/g, '')}" style="text-decoration:underline;font-weight:700">${m}</a>`);
      n.replaceWith(span); }
  }
  // 2) コントラスト不足のボタンを、読める濃さにする(背景を上から暗く/明るくする)
  for (const it of (P.contrast || [])) {
    const e = document.querySelector(`[data-sd-lc="${it.idx}"]`); if (!e) continue;
    const fgLight = lum(it.fg) > lum(it.bg), tone = fgLight ? 0 : 255; let a = 0.1;
    for (; a <= 0.9; a += 0.05) { const bg = it.bg.map(v => v * (1 - a) + tone * a); if (ratio(it.fg, bg) >= 4.6) break; }
    e.style.setProperty('box-shadow', `inset 0 0 0 999px rgba(${tone},${tone},${tone},${a.toFixed(2)})`, 'important'); mark(e);
  }
  // 3) ファーストビューへの追加(既存の要素の上に重ねる)
  const H = P.hero || {}, vw = innerWidth, vh = innerHeight;
  let top = H.bottom != null ? H.bottom + 14 : vh * 0.5;
  const width = Math.min(Math.max(H.width || vw * 0.7, 260), vw - 32);
  const center = (H.align || '') === 'center';
  const left = center ? Math.max(16, (H.cx || vw / 2) - width / 2) : Math.max(16, H.left || 16);
  const need = (P.tagline ? 70 : 0) + (P.fvCta ? 70 : 0), limit = vh - (P.stickyBar ? 100 : 20);
  if (top + need > limit) top = Math.max(limit - need, 60);
  const box = document.createElement('div');
  box.style.cssText = `position:absolute;left:${left}px;top:${top + scrollY}px;width:${width}px;text-align:${center ? 'center' : 'left'};z-index:2147483000`;
  if (P.tagline) { const t = document.createElement('p');
    t.textContent = P.tagline;
    t.style.cssText = 'margin:0 0 10px;padding:8px 12px;font:500 16px/1.6 sans-serif;color:#1a1a1a;background:rgba(255,255,255,.88);border-radius:8px;display:inline-block';
    box.appendChild(t); }
  if (P.fvCta) { const wrap = document.createElement('div'); const a = document.createElement('a');
    a.href = P.ctaUrl || '#'; a.textContent = P.ctaLabel + ' →';
    a.style.cssText = 'display:inline-block;padding:14px 26px;border-radius:999px;background:#c62828;color:#fff;font:700 16px/1.2 sans-serif;text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.25)';
    wrap.appendChild(a); box.appendChild(wrap); }
  if (box.childNodes.length) { document.body.appendChild(box); mark(box); }
  // 4) スマホの固定連絡バー
  if (P.stickyBar) {
    const bar = document.createElement('div');
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483200;display:flex;gap:8px;padding:10px 12px;background:rgba(255,255,255,.96);box-shadow:0 -2px 10px rgba(0,0,0,.18)';
    const add = (txt, href, bg) => { const a = document.createElement('a'); a.textContent = txt; a.href = href;
      a.style.cssText = `flex:1;text-align:center;padding:13px 6px;border-radius:8px;font:700 15px/1.2 sans-serif;color:#fff;text-decoration:none;background:${bg}`; bar.appendChild(a); };
    if (P.tel) add('電話する', 'tel:' + String(P.tel).replace(/\D/g, ''), '#1565c0');
    if (P.line) add('LINEで相談', P.line, '#06a94d');
    add(P.ctaLabel || 'お問い合わせ', P.ctaUrl || '#', '#c62828');
    document.body.appendChild(bar); mark(bar);
  }
  window.scrollTo(0, 0);
}
"""


def _font(size):
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def compose(before, after, out, title):
    """左: 現状 / 右: 改善イメージ を並べる。"""
    a, b = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
    h = min(a.height, b.height)
    a, b = a.crop((0, 0, a.width, h)), b.crop((0, 0, b.width, h))
    pad, bar = 24, 56
    img = Image.new("RGB", (a.width + b.width + pad * 3, h + bar + pad * 2), "#f3f5f9")
    d, f = ImageDraw.Draw(img), _font(24)
    d.text((pad, 14), f"現状  ー {title}", fill="#14284b", font=f)
    d.text((pad * 2 + a.width, 14), "改善イメージ(自動生成・実際の制作物ではありません)", fill="#c62828", font=f)
    img.paste(a, (pad, bar + pad // 2))
    img.paste(b, (pad * 2 + a.width, bar + pad // 2))
    if img.width > 2400:  # 画面で見やすく、ファイルも大きくなりすぎない大きさにする
        img = img.resize((2400, round(img.height * 2400 / img.width)))
    img.save(out, optimize=True)


def make_mockups(session, plan):
    """plan = {pc:{...patch}, sp:{...patch}}。適用できる変更があるビューだけ作る。
    計測に使ったのと同じ画面(Session)の上に変更を重ねるので、現状と同じ条件で比べられる。"""
    results = []
    for view, label in (("sp", "スマホ(390px)"), ("pc", "PC(1366px)")):
        patch = plan.get(view)
        page, m = session.pages.get(view), session.measures.get(view)
        if not patch or not patch.get("changes") or page is None:
            continue
        args = dict(patch, phoneRe=PHONE_RE, hero=m["heroInfo"],
                    contrast=m["lowContrast"][:4] if patch.get("contrastFix") else [])
        if patch.get("tagline") and m["heroInfo"]["has_jp_block"]:
            args["tagline"] = None  # すでに日本語の説明文がある場合は足さない
        page.evaluate("window.scrollTo(0,0)")
        page.wait_for_timeout(700)
        page.evaluate(PATCH_JS, args)
        after = f"{session.outdir}/{view}_after.png"
        stable_screenshot(page, after, tries=3, gap_ms=500)
        out = f"{session.outdir}/{view}_compare.png"
        compose(f"{session.outdir}/{view}_fv.png", after, out, label)
        changes = [c for c in patch["changes"] if not (c.startswith("見出しの下") and not args.get("tagline"))]
        results.append({"view": view, "title": f"{label}のファーストビュー", "image": out, "changes": changes})
    return results
