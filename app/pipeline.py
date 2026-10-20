"""診断の全工程: 取得 → 画面計測 → 自動チェック → 改善イメージ → PDF"""
import datetime
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import collect  # noqa: E402
import make_pdf  # noqa: E402
import capture  # noqa: E402
import checks  # noqa: E402
import mockup  # noqa: E402

SUBPAGE_KEYS = [  # 優先して見るページ(URLとリンク文言から判定)
    r"contact|inquiry|お問い?合わせ|問合", r"price|fee|料金|費用|価格", r"faq|よくある|qa",
    r"company|about|会社|概要|office|運営", r"service|事業|サービス|療育|商品|product",
    r"flow|guide|利用|案内|流れ", r"privacy|プライバシー|個人情報",
]
MAX_SUBPAGES = 5
PAGE_GAP_S = 2.5
UNVERIFIED = [
    "競合サイトとの比較(競合URLが必要)",
    "『誰向けか』『強み』『ベネフィット』『導線』『ファーストビュー』の評価(担当者が画面を見て判断する項目)",
    "表示速度・Core Web Vitals(未計測)",
    "検索順位・検索需要・アクセス数・離脱率・問い合わせ数・売上(解析データが必要)",
    "Google ビジネスプロフィール・口コミ(サイト外の情報)",
    "フォーム送信後の動作(送信は行っていない)",
    "法令への適合(広告表現・個人情報・特定商取引法など。専門家の確認が必要)",
    "取得したページ以外のページ(診断は主要ページのみ)",
]


UNVERIFIED_UPLOAD = [
    "提供された資料の真正性・取得日時(提供者の申告による。実際のサイトの現在の状態と異なる可能性がある)",
    "HTMLとスクリーンショットの取得時点のずれ(別々の日時に取得されていると、内容が一致しない)",
    "ブラウザ上の動作: ファーストビューのCTAの有無、タップできるか、固定表示、色のコントラスト、小さいタップ対象(スクリーンショット1枚では計測できない)",
    "サイトマップ・robots.txt・https・表示速度(サイトにアクセスしていないため未確認)",
    "『誰向けか』『強み』『ベネフィット』『導線』『ファーストビュー』の評価(担当者がスクリーンショットを見て判断する項目)",
    "競合サイトとの比較(競合URLが必要)",
    "検索順位・アクセス数・問い合わせ数・売上(解析データが必要)",
    "法令への適合(広告表現・個人情報・特定商取引法など。専門家の確認が必要)",
]


def normalize(url):
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    p = urlparse(url)
    if not p.hostname or "." not in p.hostname:
        raise ValueError("URLの形式が正しくありません")
    return url


def pick_subpages(top_url, links):
    host = urlparse(top_url).netloc
    seen, chosen = {top_url.rstrip("/")}, []
    for pat in SUBPAGE_KEYS:
        for l in links:
            href = urljoin(top_url, l["href"]).split("#")[0]
            if urlparse(href).netloc != host or href.rstrip("/") in seen:
                continue
            if href.lower().endswith((".pdf", ".jpg", ".png", ".zip")):
                continue
            if re.search(pat, (l["text"] + " " + href), re.I):
                seen.add(href.rstrip("/")); chosen.append(href); break
        if len(chosen) >= MAX_SUBPAGES:
            break
    return chosen


def fetch_page(url):
    html, fetch = collect.load(url)
    if fetch.get("status") in (401, 403, 429, 503) or capture.BLOCK_PATTERNS.search(html[:3000]):
        raise capture.BlockedError(f"サイトのボット対策またはアクセス制限により、ページを取得できませんでした(応答: {fetch.get('status')})")
    return {"url": fetch.get("final_url") or url, "facts": collect.extract(html, fetch.get("final_url") or url), "fetch": fetch}


def build_report(site, findings, plan, scores, signals, mockups, images, notes, mode="live", intake=None):
    top = site["pages"][0]
    tf = top["facts"]
    today = datetime.date.today().isoformat()
    ranked = findings[:10]
    for i, f in enumerate(ranked, 1):
        f["rank"] = i
    sp, pc = site["cap"]["sp"], site["cap"]["pc"]
    top3 = "\n".join(f"{n} {f['title']}" for n, f in zip("①②③", ranked[:3]))
    src = "公開情報" if mode == "live" else "持ち込み資料(HTML・スクリーンショット)"
    summary = (f"自動診断の結論: 事実のチェックで{len(findings)}件の改善候補が見つかりました。優先度の高いものは次の3点です。\n{top3}\n"
               f"※この結果は、{src}の事実を自動でチェックしたものです。『誰向けか』『強み』『ベネフィット』など判断が必要な項目は、"
               "担当者が画面を見て確認します(スコアカードで『要確認』と表示)。成果を保証するものではありません。") if findings else \
              "自動チェックの範囲では、大きな問題は見つかりませんでした。判断が必要な項目は担当者が画面を見て確認します。"
    h1 = signals["h1"][:1] or ["(なし)"]
    live = mode == "live"
    cta_hint = (f"ファーストビュー内のCTA: スマホ{len(sp['fvCtas'])}件・PC{len(pc['fvCtas'])}件。固定表示のCTA(スマホ): {len(sp['fixedCtas'])}件。"
                if live else "ファーストビューのCTA・固定表示は、持ち込み資料では計測できません(スクリーンショットで目視確認)。")
    hints = {
        "ファーストビュー": f"参考: H1={h1[0]!r}。" + ("" if not live else f"ファーストビュー内のCTA: スマホ{len(sp['fvCtas'])}件・PC{len(pc['fvCtas'])}件。"),
        "誰向けか": "参考(最初の文言): " + tf["text"]["first_600"][:120],
        "強み": "参考(見出し): " + " / ".join(tf["headings"]["h2"][:5]),
        "ベネフィット": "参考(見出し): " + " / ".join(tf["headings"]["h2"][:5]),
        "導線": "参考(ナビ): " + " / ".join(tf["navigation"]["nav_links"][:8]),
    }
    card = []
    for item in checks.ALL_ITEMS:
        if scores.get(item) is not None:
            fs = [f["title"] for f in findings if f["item"] == item]
            card.append({"item": item, "score": scores[item],
                         "comment": ("自動チェックで見つかった点: " + "、".join(fs[:3])) if fs else "自動チェックの範囲では、問題は見つかりませんでした。"})
        else:
            card.append({"item": item, "score": None, "comment": "自動診断の対象外(担当者が画面を見て判断)。" + hints.get(item, "")})
    six = [{"q": q, "verdict": "要確認", "answer": "自動診断では判定しません。担当者が記入します。", "evidence": ev} for q, ev in (
        ("誰向けなのか", hints["誰向けか"]), ("何が強みなのか", hints["強み"]), ("なぜ選ぶべきなのか", "競合比較・実績・認証などは、担当者が確認します。"),
        ("CTAが分かりやすいか", cta_hint),
        ("顧客の不安を解消できているか", "FAQ・実績・個人情報の記載などは、改善点の自動チェック結果を参照。"),
        ("競合との差が伝わっているか", "競合URLが提供されていないため、診断していません。"))]
    for s_ in six:
        if s_["q"].startswith("競合"):
            s_["verdict"] = "判定不能"
    rep = {
        "site_name": (tf["head"]["title"] or urlparse(site["url"]).netloc)[:60], "short_name": urlparse(site["url"]).netloc,
        "url": site["url"], "date": today, "plan": "自動ライト診断(事実チェック + 改善イメージ)" if live else "持ち込み診断(HTML + スクリーンショット)",
        "summary": summary, "images": images, "scorecard": card, "six_questions": six, "improvements": ranked,
        "minor": [f"{f['title']}: {f['evidence']}" for f in findings[10:]] or None,
        "mockups": mockups, "unverified": UNVERIFIED if live else UNVERIFIED_UPLOAD,
        "method": (intake or "") if not live else (f"{today} に、公開ページを取得。トップと主要{len(site['pages'])-1}ページのHTMLを解析し、Chromium で PC(1366px)・スマホ(iPhone 相当・390px)の画面を計測した。"
                   f"取得ページ: {', '.join(p['url'] for p in site['pages'])}。ページ間は{PAGE_GAP_S}秒以上空けた。改善イメージは、ブラウザ上でページに変更を差し込んだ自動生成画像で、実際の制作物ではない。"
                   + (" 取得できなかったページ: " + ", ".join(notes) + "。" if notes else "")),
    }
    if not rep["minor"]:
        rep.pop("minor")
    return rep


def run_job(job_dir, url, progress):
    os.makedirs(job_dir, exist_ok=True)
    url = normalize(url)
    progress(5, "トップページを取得しています")
    top = fetch_page(url)
    site = {"url": top["url"], "pages": [top], "links": top["facts"]["links"]}
    top["fetch"]["side_files"] = collect.side_files(top["url"])
    subs = pick_subpages(top["url"], site["links"])
    notes = []
    for i, u in enumerate(subs):
        progress(10 + i * 6, f"主要ページを取得しています({i+1}/{len(subs)})")
        time.sleep(PAGE_GAP_S)
        try:
            site["pages"].append(fetch_page(u))
        except Exception as e:  # noqa: BLE001
            notes.append(f"{u}({type(e).__name__})")
    progress(45, "画面を計測しています(PC・スマホ)")
    with capture.Session(top["url"], job_dir) as session:
        site["cap"] = session.measure_all()
        progress(65, "自動チェックをしています")
        findings, plan, scores, signals = checks.run(site)
        progress(72, "改善イメージを作っています")
        mocks = mockup.make_mockups(session, plan)
    images = {"pc": f"{job_dir}/pc_fv.png", "sp": f"{job_dir}/sp_fv.png"}
    progress(88, "PDFを作っています")
    rep = build_report(site, findings, plan, scores, signals, mocks, images, notes)
    with open(f"{job_dir}/report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    make_pdf.build(rep, f"{job_dir}/report.pdf")
    progress(100, "完了")
    return rep
