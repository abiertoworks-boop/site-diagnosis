#!/usr/bin/env python3
"""Search Console のエクスポート(CSV・日本語UI)を読み、診断レポート用の
「検索での見つかり方」データ(JSON)を作る。AI は使わず、計算だけを行う。

使い方:
  python3 gsc_analyze.py PERF_DIR [--ai AI_DIR] [--cov COV_DIR] [--titles] -o out.json

  PERF_DIR : 「検索パフォーマンス」のZIPを展開したフォルダ(クエリ.csv, ページ.csv など)
  --ai     : 「検索パフォーマンス(生成AI機能)」のZIPを展開したフォルダ
  --cov    : 「ページのインデックス登録」のZIPを展開したフォルダ
  --titles : 伸ばす候補ページの title / h1 を実サイトから取得して並べる

方針(診断の原則):
  - 数字はすべてCSVから計算し、推測で補わない。
  - 表に出ないクエリ(匿名化など)の割合を必ず示す。
  - しきい値はサイトの規模に合わせて引数で変える。
"""
import argparse
import csv
import json
import os
import sys
from urllib.parse import unquote


def rd(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def num(v, cast=float):
    v = (v or "").replace(",", "").replace("%", "").strip()
    return cast(v) if v else None


def chart_window(rows, a, b):
    s = rows[a:b]
    if not s:
        return None
    c = sum(num(r["クリック数"], int) or 0 for r in s)
    i = sum(num(r["表示回数"], int) or 0 for r in s)
    return {"from": s[0]["日付"], "to": s[-1]["日付"], "clicks": c, "impressions": i,
            "ctr": round(c / i * 100, 2) if i else None}


def short(url):
    u = unquote(url)
    for pre in ("https://", "http://"):
        if u.startswith(pre):
            u = u[len(pre):]
    return u


def fetch_title(url):
    try:
        import requests
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        s = BeautifulSoup(r.text, "lxml")
        return {"title": s.title.string.strip() if s.title and s.title.string else None,
                "h1": [h.get_text(" ", strip=True)[:60] for h in s.find_all("h1")][:3]}
    except Exception as e:  # 取得できない場合は空欄にする(推測で埋めない)
        return {"title": None, "h1": [], "error": str(e)[:80]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("perf")
    ap.add_argument("--ai")
    ap.add_argument("--cov")
    ap.add_argument("--titles", action="store_true")
    ap.add_argument("--push-min-imp", type=int, default=20, help="伸ばす候補: 表示回数の下限")
    ap.add_argument("--push-max-pos", type=float, default=10.0, help="伸ばす候補: 平均掲載順位の上限")
    ap.add_argument("--push-max-ctr", type=float, default=3.0, help="伸ばす候補: CTR(%)の上限")
    ap.add_argument("--zero-min-imp", type=int, default=10, help="クリック0のクエリ: 表示回数の下限")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    P = lambda n: os.path.join(a.perf, n)
    chart = rd(P("平均読み込み時間のチャート.csv")) or rd(P("グラフ.csv"))
    queries = rd(P("クエリ.csv"))
    pages = rd(P("ページ.csv"))
    devices = rd(P("デバイス.csv"))
    countries = rd(P("国.csv"))
    if not chart:
        sys.exit("日別のCSV(平均読み込み時間のチャート.csv)が見つかりません")

    tc = sum(num(r["クリック数"], int) or 0 for r in chart)
    ti = sum(num(r["表示回数"], int) or 0 for r in chart)
    pos_w = sum((num(r["掲載順位"]) or 0) * (num(r["表示回数"], int) or 0) for r in chart if r["掲載順位"])
    total = {"from": chart[0]["日付"], "to": chart[-1]["日付"], "days": len(chart),
             "clicks": tc, "impressions": ti, "ctr": round(tc / ti * 100, 2) if ti else None,
             "position": round(pos_w / ti, 1) if ti else None}

    monthly = {}
    for r in chart:
        k = r["日付"][:7]
        m = monthly.setdefault(k, {"clicks": 0, "impressions": 0})
        m["clicks"] += num(r["クリック数"], int) or 0
        m["impressions"] += num(r["表示回数"], int) or 0

    qc = sum(num(r["クリック数"], int) or 0 for r in queries)
    qi = sum(num(r["表示回数"], int) or 0 for r in queries)
    hidden = {"query_rows": len(queries), "clicks_in_table": qc, "impressions_in_table": qi,
              "clicks_hidden_pct": round((tc - qc) / tc * 100, 1) if tc else None,
              "impressions_hidden_pct": round((ti - qi) / ti * 100, 1) if ti else None}

    def prow(r):
        return {"page": short(r["上位のページ"]), "url": r["上位のページ"],
                "clicks": num(r["クリック数"], int), "impressions": num(r["表示回数"], int),
                "ctr": num(r["CTR"]), "position": num(r["掲載順位"])}

    prows = [prow(r) for r in pages]
    protect = sorted([p for p in prows if (p["clicks"] or 0) > 0], key=lambda p: -p["clicks"])[:3]
    push = [p for p in prows if (p["impressions"] or 0) >= a.push_min_imp
            and p["position"] is not None and p["position"] <= a.push_max_pos
            and (p["ctr"] or 0) <= a.push_max_ctr and p not in protect]
    push = sorted(push, key=lambda p: -p["impressions"])[:3]
    if a.titles:
        for p in push + protect:
            p.update(fetch_title(p["url"]))

    qrows = [{"query": r["上位のクエリ"], "clicks": num(r["クリック数"], int),
              "impressions": num(r["表示回数"], int), "ctr": num(r["CTR"]),
              "position": num(r["掲載順位"])} for r in queries]
    zero = [q for q in qrows if (q["clicks"] or 0) == 0 and (q["impressions"] or 0) >= a.zero_min_imp]

    out = {"source": "Google Search Console(エクスポートCSV)", "search_type": "ウェブ",
           "total": total, "last28": chart_window(chart, -28, len(chart)),
           "prev28": chart_window(chart, -56, -28), "monthly": monthly, "hidden": hidden,
           "queries": qrows[:12], "zero_click_queries": zero, "pages": prows[:12],
           "protect": protect, "push": push,
           "devices": [{"device": r["デバイス"], "clicks": num(r["クリック数"], int),
                        "impressions": num(r["表示回数"], int), "position": num(r["掲載順位"])} for r in devices],
           "countries": [{"country": r["国"], "clicks": num(r["クリック数"], int),
                          "impressions": num(r["表示回数"], int)} for r in countries[:5]],
           "thresholds": {"push_min_imp": a.push_min_imp, "push_max_pos": a.push_max_pos,
                          "push_max_ctr": a.push_max_ctr, "zero_min_imp": a.zero_min_imp}}

    if a.ai:
        ach = rd(os.path.join(a.ai, "平均読み込み時間のチャート.csv"))
        ap_ = rd(os.path.join(a.ai, "ページ.csv"))
        am = {}
        for r in ach:
            am[r["日付"][:7]] = am.get(r["日付"][:7], 0) + (num(r["表示回数"], int) or 0)
        out["ai_features"] = {"impressions": sum(am.values()), "monthly": am,
                              "pages": [{"page": short(r["上位のページ"]), "impressions": num(r["表示回数"], int)} for r in ap_[:6]],
                              "note": "生成AI機能(AIによる概要など)の表示回数。エクスポートにクリック数は含まれていない。"}
    if a.cov:
        cch = rd(os.path.join(a.cov, "平均読み込み時間のチャート.csv"))
        last = [r for r in cch if r.get("登録済み")]
        crit = rd(os.path.join(a.cov, "重大な問題.csv"))
        out["coverage"] = {"date": last[-1]["日付"] if last else None,
                           "indexed": num(last[-1]["登録済み"], int) if last else None,
                           "not_indexed": num(last[-1]["未登録"], int) if last else None,
                           "issues": [{"reason": r["理由"], "pages": r["ページ"], "validation": r["確認"]} for r in crit]}

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
