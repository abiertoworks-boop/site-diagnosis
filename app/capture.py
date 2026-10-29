"""実ブラウザ(Chromium)で、画面の事実を計測する。

- ファーストビューの画像(PC/スマホ)。アニメーションが落ち着くまで待つ
- ファーストビュー内のCTA、固定表示されるCTA、コントラスト不足のボタン(画面の色を実測)
- 電話番号(テキストのみでタップ不可のもの)、小さいタップ対象、横スクロール
- ファーストビューの文言の位置(改善イメージを重ねる場所の決定に使う)

判断はしない。数値と要素の事実だけを返す。
"""
import glob
import io
import ipaddress
import os
import re
import socket
import statistics
from urllib.parse import urlparse

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

SP_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
VIEWS = {
    "pc": dict(viewport={"width": 1366, "height": 900}),
    "sp": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2,
               is_mobile=True, has_touch=True, user_agent=SP_UA),
}
CTA_RE = ("お問い合わせ|お問合せ|問い合わせ|資料請求|無料相談|無料体験|申し込|申込|予約|見学|購入|"
          "カートに|見積|相談|診断|体験|ダウンロード|メール|contact|buy|book|sign up|get started|apply|reserve")
PHONE_RE = r"0\d{1,4}[-ー－‐]\d{1,4}[-ー－‐]\d{3,4}"
JP = re.compile("[぀-ヿ一-鿿]")

MEASURE_JS = r"""
([ctaRe, phoneRe]) => {
  const cta = new RegExp(ctaRe, 'i'), ph = new RegExp(phoneRe);
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.05; };
  const inVP = (e) => { const r = e.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
  const hit = (e) => { const r = e.getBoundingClientRect();
    const x = Math.min(Math.max(r.left + r.width / 2, 1), innerWidth - 1), y = Math.min(Math.max(r.top + r.height / 2, 1), innerHeight - 1);
    const t = document.elementFromPoint(x, y); return !!t && (e === t || e.contains(t) || t.contains(e)); };
  const label = (e) => (e.innerText || e.getAttribute('aria-label') || e.title || '').replace(/\s+/g, ' ').trim().slice(0, 40);
  const isCta = (e) => { const h = e.getAttribute('href') || '';
    return cta.test(label(e)) || h.startsWith('tel:') || h.startsWith('mailto:') || /line\.me|lin\.ee/.test(h); };
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return {c: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1}; };
  const all = [...document.querySelectorAll('a,button,[role=button]')].filter(vis);
  const fvCtas = all.filter(e => inVP(e) && hit(e) && isCta(e)).map(e => ({label: label(e), href: e.getAttribute('href') || ''}));
  // 文言に関係なく、ファーストビューにあるボタン状のリンク(ヘッダー・ナビ・フッター以外)
  const fvButtons = all.filter(e => inVP(e) && hit(e) && !e.closest('header,nav,footer,[role=navigation]')).filter(e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e), bg = parse(s.backgroundColor);
    return label(e) && r.height >= 34 && r.width >= 90 && ((bg && bg.a > 0.5) || parseFloat(s.borderRadius) >= 6 || parseFloat(s.borderTopWidth) > 0); })
    .map(e => ({label: label(e), href: e.getAttribute('href') || ''}));

  // コントラスト検査の候補(背景色は画面の画素から実測する)
  document.querySelectorAll('[data-sd-lc]').forEach(e => e.removeAttribute('data-sd-lc'));
  let idx = 0; const boxes = [];
  for (const e of all) {
    if (!inVP(e) || !hit(e) || !label(e)) continue;
    const r = e.getBoundingClientRect(); if (r.height < 22 || r.width < 50 || boxes.length >= 40) continue;
    const fg = parse(getComputedStyle(e).color); if (!fg || fg.a < 0.95) continue;
    e.setAttribute('data-sd-lc', String(idx));
    boxes.push({idx: idx++, label: label(e), x: r.left, y: r.top, w: r.width, h: r.height, fg: fg.c,
                fs: parseFloat(getComputedStyle(e).fontSize), bold: parseInt(getComputedStyle(e).fontWeight) >= 700});
  }
  // ファーストビューの文言(ヘッダー・ナビ・フッター・ボタンの中は除く)
  const hero = [];
  for (const e of document.body.querySelectorAll('h1,h2,h3,h4,p,div,span,li')) {
    if (hero.length >= 80 || !vis(e) || !inVP(e)) continue;
    if (e.closest('header,nav,footer,[role=navigation],a,button,[role=button]')) continue;
    let own = ''; for (const n of e.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    own = own.replace(/\s+/g, ' ').trim(); if (own.length < 6) continue;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    if (s.position === 'fixed' || r.top < 40 || r.bottom > innerHeight * 1.0) continue;
    hero.push({text: own.slice(0, 40), len: own.length, x: r.left, y: r.top, w: r.width, h: r.height,
               fs: parseFloat(s.fontSize), align: s.textAlign, color: s.color});
  }
  // タップできない電話番号
  const phones = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    const m = n.nodeValue.match(ph); if (!m) continue;
    const p = n.parentElement; if (!p || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(p.tagName)) continue;
    if (p.closest('a[href^="tel:"]')) continue; phones.push(m[0]);
  }
  const telLinks = [...document.querySelectorAll('a[href^="tel:"]')].map(a => a.getAttribute('href'));
  const small = [...document.querySelectorAll('button,[role=button],nav a,header a')].filter(vis)
    .filter(e => { const r = e.getBoundingClientRect(); return r.width < 24 || r.height < 24; }).length;
  const h1 = [...document.querySelectorAll('h1')].map(h => (h.innerText || (h.querySelector('img') || {}).alt || '').trim());
  return {fvCtas, fvButtons, boxes, hero, phones: [...new Set(phones)], telLinks, smallTargets: small, h1,
    overflowX: document.documentElement.scrollWidth > innerWidth + 1, vw: innerWidth, vh: innerHeight,
    textLen: (document.body.innerText || '').length};
}
"""

# 実際に見えていて押せる、固定表示のCTAだけを返す
FIXED_JS = r"""
([ctaRe]) => { const cta = new RegExp(ctaRe, 'i'), out = [];
  for (const a of document.querySelectorAll('a,button,[role=button]')) {
    const r = a.getBoundingClientRect(), s = getComputedStyle(a);
    if (r.width <= 0 || r.height <= 0 || s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) < 0.05) continue;
    if (r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) continue;
    const x = Math.min(Math.max(r.left + r.width / 2, 1), innerWidth - 1), y = Math.min(Math.max(r.top + r.height / 2, 1), innerHeight - 1);
    const t = document.elementFromPoint(x, y); if (!t || !(a === t || a.contains(t) || t.contains(a))) continue;
    const h = a.getAttribute('href') || '', txt = (a.innerText || '').trim();
    if (!(cta.test(txt) || /^(tel:|mailto:)|line\.me|lin\.ee/.test(h))) continue;
    for (let n = a; n && n !== document.documentElement; n = n.parentElement) {
      const p = getComputedStyle(n).position; if (p === 'fixed' || p === 'sticky') { out.push(txt.slice(0, 20) || h.slice(0, 20)); break; } }
  }
  return [...new Set(out)];
}
"""


def chromium_path():
    return (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") or [None])[0]


class BlockedError(Exception):
    """サイト側のボット対策・アクセス制限で、診断対象のページを表示できないとき。回避はしない。"""


BLOCK_PATTERNS = re.compile(
    r"verify you are human|checking your (browser|request)|just a moment|attention required|access denied|"
    r"are you a robot|captcha|ddos protection|人間であることを確認|ロボットではありません|アクセスが制限|アクセスを拒否", re.I)


def check_blocked(page, response):
    status = response.status if response else None
    title = page.title() or ""
    body = (page.evaluate("document.body ? document.body.innerText.slice(0, 600) : ''") or "")
    if status in (401, 403, 429, 503) or BLOCK_PATTERNS.search(title + " " + body):
        raise BlockedError(f"サイトのボット対策またはアクセス制限により、ページを表示できませんでした(応答: {status}、表示: {body[:60]!r})")


def host_is_private(host):
    if os.environ.get("SD_ALLOW_PRIVATE") == "1":  # 開発・テスト用(本番では設定しない)
        return False
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except socket.gaierror:
        return False  # プロキシ経由でしか解決できない環境。プロキシ側の制限に任せる
    return False


def launch(p):
    exe = chromium_path()
    return p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()


def new_page(browser, view):
    ctx = browser.new_context(**VIEWS[view])
    page = ctx.new_page()

    def guard(route):  # 内部ネットワークへの読み込みを遮断
        host = urlparse(route.request.url).hostname or ""
        if route.request.url.startswith(("http://", "https://")) and host and host_is_private(host):
            return route.abort()
        route.continue_()
    page.route("**/*", guard)
    return page


def open_page(browser, view, url, wait_s=5):
    page = new_page(browser, view)
    resp = page.goto(url, wait_until="load", timeout=45000)
    page.wait_for_timeout(int(wait_s * 1000))
    check_blocked(page, resp)
    return page


def stable_screenshot(page, path, tries=7, gap_ms=900, tol=0.4):
    """演出が落ち着く(2枚の差が小さくなる)まで待ってから撮る。"""
    prev = None
    for _ in range(tries):
        img = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        if prev is not None:
            diff = ImageChops.difference(img, prev).convert("L")
            if statistics.fmean(diff.resize((64, 64)).getdata()) < tol:
                break
        prev = img
        page.wait_for_timeout(gap_ms)
    img.save(path)
    return img


def _lum(c):
    f = lambda v: (v / 255 / 12.92) if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def sample_bg(img, box, scale):
    """ボタンの内側の縁の画素から背景色を実測する。ばらつきが大きい(画像・グラデーション)ときは None。"""
    x, y, w, h = (v * scale for v in (box["x"], box["y"], box["w"], box["h"]))
    ins = 4 * scale
    pts = [(x + ins, y + ins), (x + w - ins, y + ins), (x + ins, y + h - ins), (x + w - ins, y + h - ins),
           (x + w / 2, y + ins), (x + w / 2, y + h - ins), (x + ins, y + h / 2), (x + w - ins, y + h / 2)]
    px = [img.getpixel((int(min(max(a, 0), img.width - 1)), int(min(max(b, 0), img.height - 1)))) for a, b in pts]
    med = tuple(int(statistics.median(p[i] for p in px)) for i in range(3))
    spread = max(max(abs(p[i] - med[i]) for i in range(3)) for p in px)
    return None if spread > 28 else med


def contrast_findings(measure, img, scale):
    out = []
    for b in measure["boxes"]:
        bg = sample_bg(img, b, scale)
        if bg is None:
            continue
        r = ratio(b["fg"], bg)
        big = b["fs"] >= 24 or (b["fs"] >= 18.66 and b["bold"])
        if r < (3 if big else 4.5):
            out.append({"idx": b["idx"], "label": b["label"], "ratio": round(r, 2), "bg": list(bg), "fg": b["fg"]})
    return sorted(out, key=lambda d: d["ratio"])


def hero_info(measure, gap=70):
    """ファーストビューの最初の文言のかたまりの位置。改善案を重ねる場所と、日本語の説明文の有無を返す。
    縦の間隔が gap(px) を超えたところで、次のセクションとみなして打ち切る。"""
    texts = sorted((t for t in measure["hero"] if t["fs"] >= 13), key=lambda t: t["y"])
    if not texts:
        return {"has_jp_block": False, "bottom": None}
    cluster = [texts[0]]
    for t in texts[1:]:
        if t["y"] - max(c["y"] + c["h"] for c in cluster) > gap:
            break
        cluster.append(t)
    biggest = max(cluster, key=lambda t: t["fs"])
    return {
        "has_jp_block": any(JP.search(t["text"]) and t["len"] >= 12 for t in cluster),
        "bottom": max(t["y"] + t["h"] for t in cluster),
        "left": biggest["x"], "width": max(t["w"] for t in cluster), "align": biggest["align"],
        "cx": biggest["x"] + biggest["w"] / 2, "color": biggest["color"],
    }


def measure_view(page, view, img):
    m = page.evaluate(MEASURE_JS, [CTA_RE, PHONE_RE])
    scale = VIEWS[view].get("device_scale_factor", 1)
    m["lowContrast"] = contrast_findings(m, img, scale)
    m["heroInfo"] = hero_info(m)
    m.pop("boxes", None); m.pop("hero", None)
    fixed = []
    for y in (1500, 1250):  # ヘッダーが下スクロールで隠れ、上に戻すと出る型に備え、少し戻して再確認
        page.evaluate(f"window.scrollTo(0,{y})")
        page.wait_for_timeout(1200)
        fixed = list(set(fixed + page.evaluate(FIXED_JS, [CTA_RE])))
    m["fixedCtas"] = fixed
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(500)
    return m


class Session:
    """1回のブラウザ起動で、PC・スマホを1回ずつだけ開く(サイトへの負荷を減らす)。
    計測 → 自動チェック → 改善イメージ を、同じ画面の上で続ける。"""

    def __init__(self, url, outdir, wait_s=5):
        self.url, self.outdir, self.wait_s = url, outdir, wait_s
        self.pages, self.measures = {}, {}

    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = launch(self._pw)
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def measure_all(self):
        for view in VIEWS:
            page = open_page(self.browser, view, self.url, self.wait_s)
            console = []
            page.on("console", lambda msg, c=console: c.append(msg.text) if msg.type == "error" else None)
            img = stable_screenshot(page, f"{self.outdir}/{view}_fv.png")
            m = measure_view(page, view, img)
            m["consoleErrors"] = len(console)
            self.pages[view], self.measures[view] = page, m
            page.wait_for_timeout(1500)  # サイトに続けてアクセスしすぎない
        return self.measures
