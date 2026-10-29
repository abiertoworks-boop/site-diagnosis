#!/usr/bin/env python3
"""診断結果 JSON から納品用 PDF を作る。

使い方: python3 make_pdf.py report.json out.pdf
JSON の形は ../skill/SKILL.md の「レポート JSON」を参照。
"""
import json
import os
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Line, Polygon, String
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

FONT_PATHS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "fonts", "ipag.ttf"),
              "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
              "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"]
FONT = next((p for p in FONT_PATHS if os.path.exists(p)), None)
if not FONT:
    sys.exit("日本語フォントが見つかりません(IPAGothic など)")
pdfmetrics.registerFont(TTFont("JP", FONT))

NAVY, ACCENT, GRAY = colors.HexColor("#14284b"), colors.HexColor("#2a6df4"), colors.HexColor("#5b6472")
LIGHT = colors.HexColor("#eef3fc")


def st(name, size, lead=None, color=colors.black, **kw):
    return ParagraphStyle(name, fontName="JP", fontSize=size, leading=lead or size * 1.6,
                          textColor=color, alignment=TA_LEFT, wordWrap="CJK", **kw)


H1 = st("h1", 20, color=NAVY, spaceAfter=6)
H2 = st("h2", 13.5, color=NAVY, spaceBefore=10, spaceAfter=5)
BODY = st("body", 9.5)
SMALL = st("small", 8.2, color=GRAY)
CELL = st("cell", 9)
CELLB = st("cellb", 9, color=NAVY)


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def P(text, style=BODY):
    """診断文は必ずエスケープする(サイトの文言に < > が含まれても落ちない)。"""
    return Paragraph(esc(text).replace("\n", "<br/>"), style)


def R(markup, style=BODY):
    """装飾つき(自前のタグを含む)。中の可変部分は esc() を通すこと。"""
    return Paragraph(markup, style)


def stars(n):
    if n is None:
        return "—"
    return "●" * int(n) + "○" * (5 - int(n))


def page_footer(c, doc):
    c.saveState()
    c.setFont("JP", 7.5)
    c.setFillColor(GRAY)
    c.drawString(18 * mm, 10 * mm, "Web サイト診断レポート")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc.page}")
    c.restoreState()


def card(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=0)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#c9d3e6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dfe6f2")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def shrink(path, max_w=1500, quality=82):
    """PDFが大きくなりすぎないよう、画像を縮小して JPEG にして埋め込む。"""
    import io
    from PIL import Image as _PI
    im = _PI.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return buf, im.size


def fv_images(imgs, W, who):
    """PC/スマホのファーストビュー画像を横に並べる。"""
    if not imgs:
        return []
    cells = []
    for key, label in (("pc", f"{who} PC(1366px)"), ("sp", f"{who} スマホ(390px)")):
        if imgs.get(key) and os.path.exists(imgs[key]):
            h = 62 * mm if key == "pc" else 80 * mm
            buf, _ = shrink(imgs[key], 1100)
            im = Image(buf, height=h, width=h * (1366 / 900 if key == "pc" else 390 / 844))
            cells.append([im, P(label, SMALL)])
    if not cells:
        return []
    t = Table([[c[0] for c in cells], [c[1] for c in cells]],
              colWidths=[W * 0.6, W * 0.4] if len(cells) == 2 else None)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [Spacer(1, 6), t]


def radar(items, compare=None, size=300):
    """項目名と点数(1〜5)のレーダーチャート。items=[(名前, 点数)]、compare=(名前, {項目: 点数})。"""
    import math
    n = len(items)
    d = Drawing(size + 160, size + 40)
    cx, cy, rad = (size + 160) / 2, size / 2 + 20, size / 2 - 28

    def pt(i, v):
        a = math.pi / 2 - 2 * math.pi * i / n
        return cx + rad * v / 5 * math.cos(a), cy + rad * v / 5 * math.sin(a)
    for lv in range(1, 6):
        pts = [c for i in range(n) for c in pt(i, lv)]
        d.add(Polygon(pts, strokeColor=colors.HexColor("#c9d3e6"), strokeWidth=0.5, fillColor=None))
        d.add(String(cx + 2, cy + rad * lv / 5 - 3, str(lv), fontName="JP", fontSize=6, fillColor=GRAY))
    for i, (name, _) in enumerate(items):
        x, y = pt(i, 5)
        d.add(Line(cx, cy, x, y, strokeColor=colors.HexColor("#c9d3e6"), strokeWidth=0.4))
        lx, ly = pt(i, 5.9)
        anchor = "middle" if abs(lx - cx) < 12 else ("start" if lx > cx else "end")
        d.add(String(lx, ly - 3, name, fontName="JP", fontSize=7.5, fillColor=NAVY, textAnchor=anchor))
    if compare:
        pts = [c for i, (name, _) in enumerate(items) for c in pt(i, compare[1].get(name, 0))]
        d.add(Polygon(pts, strokeColor=colors.HexColor("#e07b00"), strokeWidth=1.2, fillColor=None, strokeDashArray=[3, 2]))
    pts = [c for i, (_, v) in enumerate(items) for c in pt(i, v)]
    d.add(Polygon(pts, strokeColor=ACCENT, strokeWidth=1.6, fillColor=colors.Color(0.16, 0.43, 0.96, alpha=0.2)))
    for i, (_, v) in enumerate(items):
        x, y = pt(i, v)
        d.add(String(x, y - 2.5, "●", fontName="JP", fontSize=6, fillColor=ACCENT, textAnchor="middle"))
    return d


def rubric_section(W, scored):
    import rubric
    s = [PageBreak(), P("点数の見方(採点基準)", H1),
         P("この基準は、本診断ツールの診断者の定義です。統計や公的な基準ではありません。", SMALL), Spacer(1, 4)]
    rows = [[P("点数", CELLB), P("意味", CELLB), P("対応の目安", CELLB)]]
    rows += [[P(a, CELL), P(b, CELL), P(c, CELL)] for a, b, c in rubric.SCALE]
    t = Table(rows, colWidths=[W * 0.1, W * 0.25, W * 0.65], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dfe6f2"))]))
    s += [t, Spacer(1, 6)] + [P("・" + x, BODY) for x in rubric.NOTES] + [Spacer(1, 6), P("項目ごとの目安", H2)]
    rows = [[P("項目", CELLB), P("5点の状態", CELLB), P("3点の状態", CELLB), P("1点の状態", CELLB)]]
    for name, (a, b, c) in rubric.ITEMS.items():
        mark = "★ " if name in scored else ""
        rows.append([P(mark + name, CELL), P(a, CELL), P(b, CELL), P(c, CELL)])
    t = Table(rows, colWidths=[W * 0.15, W * 0.31, W * 0.27, W * 0.27], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dfe6f2")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    s += [t, P("★ = 今回採点した項目", SMALL)]
    return s


def build(rep, out):
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Webサイト診断レポート {rep.get('site_name', '')}")
    W = A4[0] - 36 * mm
    s = []

    # 表紙・総評
    s += [P("Web サイト診断レポート", H1),
          R(f"{esc(rep.get('site_name', ''))}　<font color='#5b6472'>{esc(rep['url'])}</font>", BODY),
          P(f"診断日: {rep['date']}　/　プラン: {rep.get('plan', 'ライト診断(改善点10個)')}", SMALL),
          Spacer(1, 6), P("総評", H2), P(rep["summary"])]

    s += fv_images(rep.get("images"), W, "診断対象")

    # レーダーチャート(採点した項目のみ)
    scored = [(r["item"], r["score"]) for r in rep["scorecard"] if r["score"] is not None]
    if len(scored) >= 3:
        cmp_ = rep.get("radar_compare")
        s += [KeepTogether([P("スコアのレーダーチャート", H2),
                            radar(scored, (cmp_["name"], cmp_["scores"]) if cmp_ else None),
                            P("外側ほど良好(5点)、内側ほど要対応(1点)。点数の意味は巻末『点数の見方』を参照。"
                              + (f"　橙の点線=" + esc(cmp_["name"]) if cmp_ else ""), SMALL)])]

    # スコアカード
    s += [P("診断スコアカード(5点満点)", H2)]
    rows = [[P("項目", CELLB), P("評価", CELLB), P("所見", CELLB)]]
    for r in rep["scorecard"]:
        rows.append([P(r["item"], CELL), (R(f"{stars(r['score'])}<br/>{int(r['score'])}/5", CELL) if r["score"] is not None else R("—<br/>要確認", CELL)), P(r["comment"], CELL)])
    t = Table(rows, colWidths=[W * 0.2, W * 0.15, W * 0.65], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dfe6f2")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    s += [t]

    bench = rep.get("benchmark")
    if bench:
        s += [PageBreak(), P(f"ベンチマーク比較: {esc(bench['name'])}", H1),
              R(f"<font color='#5b6472'>{esc(bench['url'])}</font>", SMALL),
              P(bench.get("caveat", ""), BODY), Spacer(1, 4)]
        rows = [[P("項目", CELLB), P(rep.get("short_name", "診断対象"), CELLB),
                 P(bench.get("short_name", "基準"), CELLB), P("読み取り(事実ベース)", CELLB)]]
        for c in rep["comparison"]:
            rows.append([P(c["item"], CELL), R(f"{int(c['target'])}/5", CELL),
                         R(f"{int(c['bench'])}/5", CELL), P(c["reading"], CELL)])
        t2 = Table(rows, colWidths=[W * 0.17, W * 0.1, W * 0.1, W * 0.63], repeatRows=1)
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dfe6f2")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        s += [t2]
        s += fv_images(bench.get("images"), W, bench.get("short_name", "基準"))
        s += [P("ベンチマークの6つの問い", H2)]
        for q in bench["six_questions"]:
            s.append(KeepTogether([
                R(f"{esc(q['q'])}　<font color='#2a6df4'>【{esc(q['verdict'])}】</font>", CELLB),
                P(q["answer"] + "\n根拠: " + q["evidence"], CELL), Spacer(1, 3)]))
        for title, key in (("取り入れる点(ベンチマークから学べること)", "adopt"),
                           ("取り入れない点・注意(ベンチマーク側の課題)", "avoid")):
            if bench.get(key):
                s.append(KeepTogether([P(title, H2)] + [P(f"・{x}", BODY) for x in bench[key]]))
    s += [PageBreak()]

    # 6つの問い
    s += [P("6つの問いへの診断", H1)]
    for q in rep["six_questions"]:
        s.append(KeepTogether([
            R(f"{esc(q['q'])}　<font color='#2a6df4'>【{esc(q['verdict'])}】</font>", H2),
            card([[P("診断", CELLB), P(q["answer"], CELL)],
                  [P("根拠", CELLB), P(q["evidence"], CELL)]], [W * 0.13, W * 0.87])]))
    s.append(PageBreak())

    # 改善点10
    s += [P("改善点 10 個(優先度順)", H1)]
    for i in rep["improvements"]:
        meta = f"効果: {i.get('impact', '-')}　/　工数: {i.get('effort', '-')}"
        s.append(KeepTogether([
            P(f"{i['rank']}. {i['title']}", H2),
            card([[P("問題", CELLB), P(i["issue"], CELL)],
                  [P("根拠", CELLB), P(i["evidence"], CELL)],
                  [P("改善案", CELLB), P(i["fix"], CELL)],
                  [P("目安", CELLB), P(meta, CELL)]], [W * 0.13, W * 0.87])]))

    if rep.get("mockups"):
        s.append(PageBreak())
        s.append(P("改善後のイメージ(自動生成)", H1))
        s.append(P("診断で見つかった点を、実際のページ上で仮に直した画像です。オレンジの枠が変更箇所です。実際の制作物・仕様ではなく、デザインや文言は制作時に決めます。", SMALL))
        for m in rep["mockups"]:
            if not os.path.exists(m["image"]):
                continue
            buf, (iw, ih) = shrink(m["image"], 1500)
            w = W
            s.append(KeepTogether([P(m["title"], H2), Image(buf, width=w, height=w * ih / iw),
                                   P("変更内容: " + " / ".join(m["changes"]), SMALL), Spacer(1, 6)]))

    if rep.get("minor"):
        s.append(KeepTogether([P("その他の軽微な指摘", H2)] + [P(f"・{x}", BODY) for x in rep["minor"]]))

    # 未検証・方法
    s += [PageBreak(), P("この診断で確認できていないこと", H1),
          P("以下は公開情報(URL・ソース・画面表示)からは判断できないため、診断に含めていません。", BODY)]
    s += [P("・" + u, BODY) for u in rep["unverified"]]
    s += [P("診断の方法", H2), P(rep["method"], BODY), Spacer(1, 8),
          P("本レポートは公開されている情報に基づく診断であり、売上・集客などの成果を保証するものではありません。", SMALL)]
    s += rubric_section(W, {n for n, _ in scored})
    doc.build(s, onFirstPage=page_footer, onLaterPages=page_footer)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(json.load(open(sys.argv[1], encoding="utf-8")), sys.argv[2])
    print("wrote", sys.argv[2])
