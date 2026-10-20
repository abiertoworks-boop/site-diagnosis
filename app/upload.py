"""持ち込みモード: お客様が自分のブラウザで保存した HTML とスクリーンショットから診断する。

- サイトにはアクセスしない(ボット対策のあるサイトでも使える)。
- HTML は、ブラウザで実行せず、文字として解析するだけ(アップロードされたスクリプトは動かさない)。
- スクリーンショットは、縦長のものを画面1枚分ずつに分割し、1枚目をファーストビューとして扱う。
- 画面の計測が必要な項目(ファーストビューのCTA・固定表示・コントラストなど)は、判定しない。
- 改善イメージは、スクリーンショットの上に『連絡バー』などを描き足した例(実際の状態は確認できない)。
"""
import io
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

import checks
import collect
import mockup
import pipeline

Image.MAX_IMAGE_PIXELS = 120_000_000
MAX_HTML = 3_000_000
MAX_SHOTS = 8
MAX_TILES = 14
PHONE_RE = r"0\d{1,4}[-ー－‐]\d{1,4}[-ー－‐]\d{3,4}"


def decode_html(raw):
    """文字コード: BOM → meta → UTF-8 → Shift_JIS系 → EUC-JP の順で試す。"""
    head = raw[:4096].decode("ascii", errors="ignore")
    cands = []
    if raw.startswith(b"\xef\xbb\xbf"):
        cands.append("utf-8-sig")
    m = re.search(r"charset=[\"']?([\w\-]+)", head, re.I)
    if m:
        cands.append(m.group(1))
    cands += ["utf-8", "cp932", "euc-jp"]
    for enc in cands:
        try:
            return raw.decode("shift_jis" if enc.lower() == "shift_jis" else enc, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def validate_html(raw):
    if len(raw) > MAX_HTML:
        raise ValueError("HTMLファイルが大きすぎます(3MBまで)")
    html = decode_html(raw)
    if not re.search(r"<\s*(html|body|head)\b", html, re.I):
        raise ValueError("HTMLファイルとして読み取れませんでした。『ページのソースを表示』から保存したものを選んでください。")
    return html


def dom_facts(html):
    """ブラウザなしで分かる範囲の、電話リンク・電話番号(テキスト)。"""
    soup = BeautifulSoup(html, "lxml")
    tel_links = [a.get("href") for a in soup.select('a[href^="tel:"]')]
    for a in soup.select('a[href^="tel:"]'):
        a.decompose()
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    phones = sorted(set(re.findall(PHONE_RE, soup.get_text(" ", strip=True))))
    return tel_links, phones


def load_shots(raws):
    """画像を検証し、PC/スマホに振り分けて、縦長のものを画面1枚分ずつに分割する。"""
    if not raws:
        raise ValueError("スクリーンショットを1枚以上選んでください")
    if len(raws) > MAX_SHOTS:
        raise ValueError(f"スクリーンショットは{MAX_SHOTS}枚までです")
    shots = []
    for item in raws:
        name, raw = item[0], item[1]
        kind = item[2] if len(item) > 2 else "auto"
        try:
            im = Image.open(io.BytesIO(raw))
            fmt = im.format
            im.load()
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"『{name[:30]}』は画像として読み取れませんでした") from e
        if fmt not in ("PNG", "JPEG", "WEBP"):
            raise ValueError(f"『{name[:30]}』は対応していない形式です(PNG・JPEG・WebPのみ)")
        if im.width < 300 or im.height < 300 or im.width > 5000 or im.height > 60000:
            raise ValueError(f"『{name[:30]}』の大きさが対応範囲外です(幅300〜5000px、高さ60000pxまで)")
        im = im.convert("RGB")
        # 『自動』は幅で判定する。固定幅のサイトのスマホ画面は幅が大きくなるため、画像ごとに指定できる
        view = kind if kind in ("pc", "sp") else ("sp" if im.width <= 700 else "pc")
        shots.append({"name": name, "view": view, "im": im})
    return shots


def tile(im, view):
    w, h = im.size
    th = round(w * (844 / 390)) if view == "sp" else round(w * (900 / 1366))
    return [im.crop((0, y, w, min(h, y + th))) for y in range(0, h, th)][:MAX_TILES]


def save_tiles(shots, outdir):
    """各ビューで一番縦長の画像を、画面1枚分ずつに分割して保存。1枚目を {view}_fv.png にする。"""
    out = {}
    for view in ("pc", "sp"):
        cand = [s for s in shots if s["view"] == view]
        if not cand:
            continue
        best = max(cand, key=lambda s: s["im"].height)
        tiles = tile(best["im"], view)
        paths = []
        for i, t in enumerate(tiles, 1):
            p = f"{outdir}/up_{view}_{i:02d}.png"
            t.save(p)
            paths.append(p)
        tiles[0].save(f"{outdir}/{view}_fv.png")
        out[view] = {"fv": f"{outdir}/{view}_fv.png", "tiles": paths, "size": best["im"].size, "name": best["name"]}
    return out


def _font(size):
    for p in mockup.FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _dashed(d, box, color="#ff6a00", width=4, dash=14):
    x0, y0, x1, y1 = box
    for x in range(int(x0), int(x1), dash * 2):
        d.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=width)
        d.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=width)
    for y in range(int(y0), int(y1), dash * 2):
        d.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=width)
        d.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=width)


def _badge(d, x, y, scale):
    f = _font(int(13 * scale))
    d.rectangle([x, y - int(18 * scale), x + int(52 * scale), y], fill="#ff6a00")
    d.text((x + int(6 * scale), y - int(16 * scale)), "改善案", fill="white", font=f)


def overlay_sp(path, out, buttons):
    """スマホの1画面目の下に、固定の連絡バーを描き足す(例)。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = w / 390
    bar_h = int(78 * s)
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle([0, h - bar_h, w, h], fill=(255, 255, 255, 245))
    n = len(buttons)
    gap, pad = int(8 * s), int(12 * s)
    bw = (w - pad * 2 - gap * (n - 1)) // n
    f = _font(int(15 * s))
    for i, (label, color) in enumerate(buttons):
        x0 = pad + i * (bw + gap)
        d.rounded_rectangle([x0, h - bar_h + pad, x0 + bw, h - pad], radius=int(8 * s), fill=color)
        tw = d.textlength(label, font=f)
        d.text((x0 + (bw - tw) / 2, h - bar_h + bar_h / 2 - int(9 * s)), label, fill="white", font=f)
    _dashed(d, (2, h - bar_h, w - 2, h - 2), width=max(2, int(3 * s)), dash=int(10 * s))
    _badge(d, 6, h - bar_h, s)
    im.save(out)


def overlay_pc(path, out, label, tel):
    """PCの1画面目の右下に、問い合わせボタン(と電話)を描き足す(例)。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = w / 1366
    d = ImageDraw.Draw(im, "RGBA")
    f = _font(int(19 * s))
    items = [(f"{label} →", "#c62828")] + ([(f"電話 {tel}", "#1565c0")] if tel else [])
    y = h - int(36 * s)
    for text, color in reversed(items):
        tw = d.textlength(text, font=f)
        bw, bh = tw + int(44 * s), int(54 * s)
        x1 = w - int(36 * s)
        d.rounded_rectangle([x1 - bw, y - bh, x1, y], radius=int(27 * s), fill=color)
        d.text((x1 - bw + int(22 * s), y - bh + int(14 * s)), text, fill="white", font=f)
        _dashed(d, (x1 - bw - 6, y - bh - 6, x1 + 6, y + 6), width=max(2, int(3 * s)), dash=int(10 * s))
        _badge(d, x1 - bw - 6, y - bh - 6, s)
        y -= bh + int(16 * s)
    im.save(out)


def run_upload_job(job_dir, html_raw, shot_raws, base_url, progress):
    os.makedirs(job_dir, exist_ok=True)
    progress(8, "HTMLを読み取っています")
    html = validate_html(html_raw)
    base = pipeline.normalize(base_url) if base_url else "https://example.invalid/"
    facts = collect.extract(html, base)
    tel_links, phones = dom_facts(html)
    progress(25, "スクリーンショットを整理しています")
    shots = load_shots(shot_raws)
    views = save_tiles(shots, job_dir)

    empty = {"fvCtas": [], "fvButtons": [], "lowContrast": [], "fixedCtas": [], "smallTargets": 0, "overflowX": False,
             "heroInfo": {"has_jp_block": False, "bottom": None}, "telLinks": tel_links, "phones": phones}
    top = {"url": base, "facts": facts, "fetch": {"final_url": base, "status": None, "https": None, "side_files": {}}}
    site = {"url": base, "pages": [top], "links": facts["links"], "cap": {"pc": dict(empty), "sp": dict(empty)}}
    progress(50, "事実をチェックしています")
    findings, _, scores, signals = checks.run(site, live=False)

    progress(70, "改善イメージの例を作っています")
    mocks = []
    cta = signals["cta"]
    tel = (tel_links[0].replace("tel:", "") if tel_links else (phones[0] if phones else None))
    line = signals["line"]
    label = (cta or {"label": "お問い合わせ"})["label"]
    if cta or tel or line:
        if "sp" in views:
            btns = ([("電話する", "#1565c0")] if tel else []) + ([("LINEで相談", "#06a94d")] if line else []) + [(label, "#c62828")]
            overlay_sp(views["sp"]["fv"], f"{job_dir}/sp_after.png", btns)
            c = f"{job_dir}/sp_compare.png"
            mockup.compose(views["sp"]["fv"], f"{job_dir}/sp_after.png", c, "スマホ(1画面目)")
            mocks.append({"view": "sp", "title": "スマホの1画面目(例)", "image": c,
                          "changes": ["画面下に、固定の連絡バー(電話・LINE・問い合わせ)を描き足した例。現状に固定バーがあるかは、スクリーンショット1枚では確認できません"]})
        if "pc" in views:
            overlay_pc(views["pc"]["fv"], f"{job_dir}/pc_after.png", label, tel)
            c = f"{job_dir}/pc_compare.png"
            mockup.compose(views["pc"]["fv"], f"{job_dir}/pc_after.png", c, "PC(1画面目)")
            mocks.append({"view": "pc", "title": "PCの1画面目(例)", "image": c,
                          "changes": ["画面の右下に、問い合わせ(と電話)のボタンを描き足した例。位置は目安で、実際の画面の要素との重なりは確認していません"]})

    progress(88, "PDFを作っています")
    today = time.strftime("%Y-%m-%d")
    sizes = "、".join(f"{v}: {d['size'][0]}×{d['size'][1]}px → {len(d['tiles'])}分割" for v, d in views.items())
    intake = (f"{today} に、提供されたHTMLファイル(約{len(html_raw)//1024}KB)とスクリーンショット{len(shots)}枚から診断した。サイトにはアクセスしていない。"
              f"HTMLはブラウザで実行せず、文字として解析した。スクリーンショットの扱い: {sizes}(縦長の画像は、画面1枚分ずつに分割し、1枚目をファーストビューとして扱った)。"
              "提供資料の取得日時・取得元は提供者の申告に基づき、真正性は確認していない。改善イメージは、スクリーンショットの上に描き足した例で、実際の制作物ではない。")
    images = {v: d["fv"] for v, d in views.items()}
    rep = pipeline.build_report(site, findings, {}, scores, signals, mocks, images, [], mode="upload", intake=intake)
    if not (shots and any(s["view"] == "sp" for s in shots)):
        rep["unverified"] = rep["unverified"] + ["スマホ画面のスクリーンショットが提供されなかったため、スマホの見え方は確認していません"]
    if not any(s["view"] == "pc" for s in shots):
        rep["unverified"] = rep["unverified"] + ["PC画面のスクリーンショットが提供されなかったため、PCの見え方は確認していません"]
    rep["tiles"] = {v: [os.path.basename(p) for p in d["tiles"]] for v, d in views.items()}
    import json
    with open(f"{job_dir}/report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    pipeline.make_pdf.build(rep, f"{job_dir}/report.pdf")
    progress(100, "完了")
    return rep
