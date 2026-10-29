#!/usr/bin/env python3
"""サイト診断用: URL または HTML ファイルから「事実」だけを抽出して JSON で出力する。

判断(強み・ベネフィット・競合差など)はここでは行わない。診断エージェントが
この JSON と画面キャプチャを根拠に判断する。

使い方:
  python3 collect.py https://example.com  > facts.json
  python3 collect.py ./samples/site/index.html > facts.json
"""
import ipaddress
import os
import json
import re
import socket
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; SiteDiagnosisBot/0.1)"
MAX_BYTES = 3_000_000

CTA_WORDS = [
    "お問い合わせ", "お問合せ", "問い合わせ", "資料請求", "無料相談", "無料体験", "無料",
    "申し込", "申込", "予約", "購入", "カートに", "見積", "相談", "診断", "体験", "ダウンロード",
    "contact", "buy", "book", "sign up", "get started", "free", "apply", "reserve",
]
TRUST = {
    "実績・事例": ["実績", "導入事例", "事例", "導入社数", "累計"],
    "お客様の声": ["お客様の声", "利用者の声", "お客さまの声", "レビュー", "口コミ", "testimonial"],
    "運営者情報": ["会社概要", "運営会社", "代表", "所在地", "住所", "about"],
    "法的表記": ["プライバシー", "個人情報", "特定商取引", "利用規約"],
    "料金の明示": ["料金", "価格", "プラン", "円", "税込", "pricing"],
    "FAQ": ["よくある質問", "FAQ", "Q&A", "Q."],
    "保証・安心": ["保証", "返金", "キャンセル", "サポート", "安心"],
    "メディア・資格": ["メディア掲載", "受賞", "認定", "資格", "掲載"],
}
SEC_FILES = ["/robots.txt", "/sitemap.xml"]


def guard_url(url):
    """診断対象の URL が内部ネットワークを指していないか確認(SSRF 対策)。

    プロキシ経由でしか名前解決できない環境では、ローカルの名前解決が失敗する。
    その場合はプロキシ側の制限に任せ、IP 直指定と localhost だけをここで遮断する。
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("http/https の URL のみ診断できます")
    host = p.hostname.lower()
    if os.environ.get("SD_ALLOW_PRIVATE") == "1":  # 開発・テスト用(本番では設定しない)
        return
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("内部ネットワークの URL は診断できません")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        infos = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("内部ネットワークの URL は診断できません")


def decode_html(body, resp):
    """文字コードを、ヘッダー → BOM → HTML内の meta → UTF-8 → 推定 の順で決める。
    requests の既定は、ヘッダーに指定がないと ISO-8859-1 とみなして文字化けする。"""
    m = re.search(r"charset=([\w\-]+)", resp.headers.get("content-type", ""), re.I)
    candidates = []
    if m:
        candidates.append(m.group(1))
    if body.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")
    head = body[:4096].decode("ascii", errors="ignore")
    m = re.search(r"<meta[^>]+charset=[\"']?([\w\-]+)", head, re.I)
    if m:
        candidates.append(m.group(1))
    candidates += ["utf-8"]
    for enc in candidates:
        try:
            return body.decode(enc.replace("shift_jis", "cp932").replace("Shift_JIS", "cp932"), errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode(resp.apparent_encoding or "cp932", errors="replace")


def load(target):
    """target を (html, meta) にする。ローカルファイルも可(テスト用)。"""
    meta = {"source": target}
    if not re.match(r"^https?://", target):
        with open(target, encoding="utf-8", errors="replace") as f:
            html = f.read()
        meta.update(kind="local_file", https=None, status=None)
        return html, meta
    guard_url(target)
    t0 = time.time()
    r = requests.get(target, headers={"User-Agent": UA}, timeout=20, stream=True)
    body = r.raw.read(MAX_BYTES, decode_content=True)
    guard_url(r.url)  # リダイレクト先が内部ネットワークでないことも確認する
    html = decode_html(body, r)
    meta.update(
        kind="url", final_url=r.url, status=r.status_code,
        https=r.url.startswith("https://"),
        redirects=[h.url for h in r.history],
        seconds=round(time.time() - t0, 2), bytes=len(body),
        truncated=len(body) >= MAX_BYTES,
    )
    return html, meta


def text_file(base, path, limit=20000):
    """robots.txt や llms.txt の本文(取得できなければ None)。"""
    try:
        u = urljoin(base, path)
        guard_url(u)
        r = requests.get(u, headers={"User-Agent": UA}, timeout=10)
        return r.text[:limit] if r.status_code == 200 and "text" in r.headers.get("content-type", "text") else None
    except Exception:  # noqa: BLE001
        return None


def side_files(base):
    out = {}
    for path in SEC_FILES:
        try:
            u = urljoin(base, path)
            guard_url(u)
            r = requests.get(u, headers={"User-Agent": UA}, timeout=10)
            out[path] = r.status_code
        except Exception as e:  # noqa: BLE001 - 取得失敗も事実として残す
            out[path] = f"error: {type(e).__name__}"
    return out


def text_of(el):
    """テキスト。ロゴ画像だけの見出し/リンクは img の alt で補う。"""
    t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
    if not t:
        t = " ".join((im.get("alt") or "").strip() for im in el.find_all("img")).strip()
    return t


def form_stats(fm):
    """入力項目の数。ラジオボタンは質問ごとに1項目、送信・非表示・ボタンは数えない。"""
    names, fields, required = set(), 0, 0
    for c in fm.find_all(["input", "textarea", "select"]):
        t = (c.get("type") or "text").lower()
        if c.name == "input" and t in ("hidden", "submit", "button", "image", "reset"):
            continue
        if t == "radio":
            key = c.get("name") or id(c)
            if key in names:
                continue
            names.add(key)
        fields += 1
        required += 1 if c.has_attr("required") else 0
    return {"fields": fields, "required": required, "action": (fm.get("action") or "")[:120]}


def extract(html, base):
    soup = BeautifulSoup(html, "lxml")
    f = {}

    def meta_of(**attrs):
        t = soup.find("meta", attrs=attrs)
        return t.get("content", "").strip() if t else None

    title = soup.title.string.strip() if soup.title and soup.title.string else None
    desc = meta_of(name="description")
    f["head"] = {
        "lang": (soup.html or {}).get("lang") if soup.html else None,
        "title": title, "title_len": len(title) if title else 0,
        "description": desc, "description_len": len(desc) if desc else 0,
        "canonical": (soup.find("link", rel="canonical") or {}).get("href"),
        "robots": meta_of(name="robots"),
        "viewport": meta_of(name="viewport"),
        "og_title": meta_of(property="og:title"),
        "og_description": meta_of(property="og:description"),
        "og_image": meta_of(property="og:image"),
        "twitter_card": meta_of(name="twitter:card"),
        "favicon": bool(soup.find("link", rel=re.compile("icon", re.I))),
    }
    ld_types, ld_summary = [], []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
            items = data if isinstance(data, list) else data.get("@graph", [data])
            for d in items:
                ld_types.append(d.get("@type"))
                addr = d.get("address")
                ld_summary.append({
                    "type": d.get("@type"), "keys": sorted(k for k in d if not k.startswith("@")),
                    "address_empty": isinstance(addr, dict) and not any(k for k in addr if not k.startswith("@")),
                })
        except Exception:  # noqa: BLE001
            ld_types.append("parse_error")
    f["structured_data_types"] = ld_types
    f["structured_data"] = ld_summary

    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    body = soup.body or soup
    full_text = text_of(body)

    f["headings"] = {
        "h1": [text_of(h) for h in soup.find_all("h1")],
        "h2": [text_of(h) for h in soup.find_all("h2")][:20],
        "h3_count": len(soup.find_all("h3")),
    }
    f["text"] = {
        "chars": len(full_text),
        "first_600": full_text[:600],  # ファーストビュー周辺の推定材料
        "full": full_text[:30000],      # 文言の有無を調べる用(返信目安・FAQ・実績など)
    }
    f["links"] = [{"text": text_of(a)[:40], "href": a["href"][:200]}
                  for a in body.find_all("a", href=True)][:300]

    # CTA(リンク/ボタン)。出現順の番号を付け、ページのどこに現れるかを見る。
    ctas, links_total = [], 0
    for i, a in enumerate(body.find_all(["a", "button"])):
        label = text_of(a)
        href = a.get("href", "")
        if a.name == "a":
            links_total += 1
        hit = any(w.lower() in label.lower() for w in CTA_WORDS)
        if hit or href.startswith(("tel:", "mailto:")) or "line.me" in href or "lin.ee" in href:
            ctas.append({"order": i, "label": label[:60], "href": href[:160]})
    f["cta"] = {
        "count": len(ctas), "items": ctas[:25],
        "tel": sorted({c["href"] for c in ctas if c["href"].startswith("tel:")}),
        "mailto": sorted({c["href"] for c in ctas if c["href"].startswith("mailto:")}),
        "line": any(("line.me" in c["href"] or "lin.ee" in c["href"]) for c in ctas),
        "forms": [form_stats(fm) for fm in body.find_all("form")],
    }
    f["navigation"] = {
        "nav_links": [text_of(a) for n in body.find_all("nav") for a in n.find_all("a")][:20],
        "links_total": links_total,
    }
    f["trust_signals"] = {
        k: sum(full_text.count(w) for w in ws) for k, ws in TRUST.items()
    }
    f["numbers_in_text"] = len(re.findall(r"\d[\d,\.]*\s*(?:%|％|件|社|名|人|年|円|倍)", full_text))

    imgs = body.find_all("img")
    f["images"] = {
        "count": len(imgs),
        "alt_missing": sum(1 for im in imgs if not (im.get("alt") or "").strip()),
        "lazy": sum(1 for im in imgs if im.get("loading") == "lazy"),
    }
    f["external_links"] = sorted({
        urlparse(a["href"]).netloc for a in body.find_all("a", href=True)
        if a["href"].startswith("http") and urlparse(a["href"]).netloc
        and urlparse(a["href"]).netloc != urlparse(base or "").netloc
    })[:20]
    return f


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    html, meta = load(sys.argv[1])
    out = {"fetch": meta, "facts": extract(html, meta.get("final_url") or meta["source"])}
    if meta.get("kind") == "url":
        out["fetch"]["side_files"] = side_files(meta["final_url"])
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
