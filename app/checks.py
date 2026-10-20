"""事実にもとづく自動チェック。

各チェックは「どのページの・何が・どうだったか」の根拠つきで結果を返す。
判断が必要な項目(誰向けか・強み・ベネフィットなど)は自動では評価しない(人が確認する)。
"""
import re
from urllib.parse import urljoin

IMPACT = {"高": 3, "中": 2, "低": 1}
EFFORT = {"小": 1, "中": 2, "大": 3}
AUTO_ITEMS = ["CTA", "信頼性", "スマホ表示", "SEO", "問い合わせまでの流れ"]
ALL_ITEMS = ["ファーストビュー", "誰向けか", "強み", "ベネフィット", "CTA", "信頼性",
             "導線", "スマホ表示", "SEO", "問い合わせまでの流れ"]
CTA_PRIORITY = ["資料請求", "見学", "予約", "無料相談", "相談", "体験", "お問い合わせ", "お問合せ", "問い合わせ", "メール", "contact"]
JP = re.compile("[぀-ヿ一-鿿]")


def finding(fid, item, title, issue, evidence, fix, impact, effort, weight=1.0):
    return dict(id=fid, item=item, title=title, issue=issue, evidence=evidence, fix=fix,
                impact=impact, effort=effort, weight=weight)


def pick_cta(top_url, links):
    for word in CTA_PRIORITY:
        for l in links:
            href = l.get("href", "")
            if word.lower() in l.get("text", "").lower() and href and not href.startswith(("#", "javascript:", "tel:", "mailto:")):
                return {"label": l["text"][:16] or word, "url": urljoin(top_url, href)}
    for l in links:  # 文言がなくても、リンク先が問い合わせページ・フォームなら入口とみなす
        href = l.get("href", "")
        if re.search(r"contact|inquiry|toiawase|enquiry|form", href, re.I) and not href.startswith(("tel:", "mailto:", "javascript:")):
            return {"label": l["text"][:16] or "お問い合わせ", "url": urljoin(top_url, href)}
    return None


def run(site, live=True):
    """site: {url, pages:[{url, facts, fetch}], links, cap:{pc,sp}} → (findings, plan, scores, signals)
    live=False は、持ち込み資料(HTML+スクショ)の診断。ブラウザでの計測が必要なチェックは行わない。"""
    top = site["pages"][0]
    tf, tfe = top["facts"], top["fetch"]
    cap, pages = site["cap"], site["pages"]
    sp, pc = cap["sp"], cap["pc"]
    out = []
    text_all = " ".join(p["facts"]["text"].get("full", "") for p in pages)
    agg = lambda key: sum(p["facts"]["trust_signals"].get(key, 0) for p in pages)
    names = lambda ps: "、".join(p["url"].replace(site["url"].rstrip("/"), "") or "/" for p in ps)
    h = tf["head"]

    # ---- SEO・基本設定 ----
    if tfe.get("https") is False and (tfe.get("final_url") or "").split("/")[2].split(":")[0] not in ("127.0.0.1", "localhost"):
        out.append(finding("https", "SEO", "サイトを https で配信する", "http のままだと、ブラウザが『保護されていない通信』と表示し、信頼を損なう。",
                           f"取得したURL: {tfe.get('final_url')}", "サーバーで https を有効にし、http から https へ転送する。", "高", "小", 2))
    if not h.get("viewport"):
        out.append(finding("viewport", "スマホ表示", "viewport の指定を追加する", "スマホで PC 版を縮小した表示になり、読みにくい。",
                           "トップページの <meta name=\"viewport\"> が確認できない。", "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> を head に追加する。", "高", "小", 2))
    vp = h.get("viewport") or ""
    if vp and re.search(r"width\s*=\s*\d+", vp) and "device-width" not in vp:
        out.append(finding("viewportfixed", "スマホ表示", "スマホ対応にする(viewport の固定幅をやめる)", "スマホで PC 用のページが縮小表示され、文字が小さく、操作しにくい。",
                           f"viewport が固定幅: <meta name=\"viewport\" content=\"{vp}\">", "viewport を『width=device-width, initial-scale=1』にし、1カラムで読めるレイアウト(CSSのメディアクエリ)に作り替える。サイト全体の作り替えが必要になる場合がある。", "高", "大", 4))
    if not h.get("description"):
        out.append(finding("desc", "SEO", "meta description を設定する", "検索結果に出る説明文を自分で決められず、クリックされにくい。",
                           f"トップページに meta description がない(title: {h.get('title')})。", "サービス内容と地域を含む、全体で120字前後の説明文を設定する(字数は診断者の目安)。", "中", "小", 1))
    if not h.get("title") or h.get("title_len", 0) < 8:
        out.append(finding("title", "SEO", "title を具体的にする", "ページの主題が検索結果で伝わらない。", f"title: {h.get('title')!r}", "『サービス名 + 地域 + 屋号』のように、検索する人が使う語を入れる。", "高", "小", 1.5))
    h1s = tf.get("headings", {}).get("h1", [])
    nonempty = [x for x in h1s if x.strip()]
    if len(nonempty) != 1:
        out.append(finding("h1", "SEO", "H1(大見出し)を1ページに1つにする", "ページの主題が検索エンジンと読み上げ環境に伝わりにくい。",
                           f"トップの H1 は{len(nonempty)}個: {nonempty[:3]}", "H1 は1つにし、サービスと地域が分かる文言にする。手順などは H2/H3 にする。", "中", "小", 1))
    multi = [p for p in pages[1:] if len([x for x in p["facts"]["headings"]["h1"] if x.strip()]) > 2]
    if multi:
        out.append(finding("h1multi", "SEO", "H1 が多すぎるページを直す", "見出しの階層が崩れ、検索エンジンにページの主題が伝わりにくい。",
                           "H1 が3個以上のページ: " + "、".join(f"{p['url']}({len([x for x in p['facts']['headings']['h1'] if x.strip()])}個)" for p in multi[:3]),
                           "手順の見出しなどを H2/H3 に変更し、H1 は1つにする。", "中", "小", 1))
    missing = [name for name, ok in (("canonical", h.get("canonical")), ("OGP画像(og:image)", h.get("og_image")),
                                     ("twitter:card", h.get("twitter_card")), ("favicon", h.get("favicon")),
                                     ("構造化データ", tf.get("structured_data_types"))) if not ok]
    if missing:
        out.append(finding("meta", "SEO", "共有・検索表示の基本設定を整える", "SNS共有時の画像や、検索エンジンへの正規URLの伝達が不足する。",
                           "トップページで確認できなかった設定: " + "、".join(missing), "不足している設定を追加する。OGP画像は 1200×630px 前後が目安(診断者の目安。各SNSの公式情報で確認)。", "低", "小", 0.7))
    sf = tfe.get("side_files", {})
    sm = sf.get("/sitemap.xml")
    if sf and sm in (404, 410):  # 429(アクセス制限)や通信エラーは、サイトの不備ではないため指摘しない
        out.append(finding("sitemap", "SEO", "サイトマップを用意する", "検索エンジンがページを見つけにくくなる。",
                           f"/sitemap.xml の取得結果: {sm}、/robots.txt: {sf.get('/robots.txt')}", "サイトマップを作成し、robots.txt に Sitemap の行を書く(CMS の機能・プラグインで自動生成できる)。", "低", "小", 0.7))
    imgs = tf["images"]
    if imgs["count"] >= 5 and imgs["alt_missing"] / imgs["count"] > 0.3:
        out.append(finding("alt", "SEO", "画像の代替テキスト(alt)を確認する", "画像の内容が検索エンジン・読み上げに伝わらない。ただし装飾画像は空でよい。",
                           f"トップの画像{imgs['count']}枚中{imgs['alt_missing']}枚で alt が空(装飾画像かどうかは未確認)。", "内容を伝える画像には、何が写っているかの説明を入れる。", "低", "中", 0.7))

    # ---- CTA・スマホ ----
    cta = pick_cta(site["url"], site.get("links", []))
    tel = (sp["telLinks"] + pc["telLinks"] or [None])[0]
    phones = sorted(set(sp["phones"] + pc["phones"]))
    line = next((l["href"] for l in site.get("links", []) if re.search(r"line\.me|lin\.ee", l["href"])), None)
    if phones:
        out.append(finding("tel", "CTA", "電話番号を、タップで発信できるリンクにする", "スマホで番号を見ても、そのまま電話できない(コピーして貼る手間が出る)。",
                           f"タップできない電話番号: {', '.join(phones[:4])}。tel: リンクは{len(set(sp['telLinks']+pc['telLinks']))}件。", "<a href=\"tel:0312345678\">03-1234-5678</a> の形にする。受付時間も併記する。", "高", "小", 1.5))
    elif not tel:
        out.append(finding("notel", "信頼性", "電話番号またはメールなど、フォーム以外の連絡先を載せる", "連絡手段がフォームだけだと、緊急時や不安なときに連絡しにくく、実在性の確認もしにくい。",
                           "取得したページ(" + str(len(pages)) + "ページ)で電話番号の記載を確認できなかった。", "電話番号・メール・受付時間を、会社概要とフッターに載せる(電話を受けない方針なら、その理由と代替手段を明記)。", "中", "小", 1))
    no_fv = [label for view, m, label in (("sp", sp, "スマホ"), ("pc", pc, "PC")) if not m["fvCtas"] and not m["fvButtons"]]
    if live and no_fv:
        out.append(finding("fvcta", "CTA", "ファーストビューに、主なCTAを置く", "最初の画面に『次の一歩』のボタンがないと、迷って離脱しやすい。",
                           f"{'・'.join(no_fv)}のファーストビュー(画面に最初に出る範囲)に、問い合わせ・予約・資料請求・電話・LINEのリンクも、ボタン状のリンクも確認できない(メニューを開く前の状態)。",
                           "見出しの下に、行動を示す文言のボタンを1つ置く。例:「" + (cta["label"] if cta else "無料で相談する") + "」。", "高", "小", 1.5))
    if live and not sp["fixedCtas"] and (cta or tel or line):
        out.append(finding("sticky", "スマホ表示", "スマホで固定表示される連絡ボタンを置く", "長いページを読んだ後に、連絡するためにメニューを開く・上へ戻る手間が出る。",
                           "スクロール後に固定表示される、問い合わせ・電話・LINEのボタンは確認できなかった。", "画面下に『電話』『LINE』『予約/問い合わせ』の固定バーを置く。本文の最後が隠れないよう余白も確保する。", "中", "小", 1))
    if live and sp["lowContrast"]:
        w = sp["lowContrast"][0]
        out.append(finding("contrast", "スマホ表示", "ボタンの文字を読みやすい色の組み合わせにする", "文字と背景の差が小さいと、読みにくく、押されにくい。",
                           f"「{w['label']}」の文字と背景のコントラスト比は約{w['ratio']}:1(画面の色から計算した推定値)。WCAG 2.x の基準は通常の文字で4.5:1。他に{len(sp['lowContrast'])-1}件。", "背景色を濃くするか、文字色を変えて、4.5:1 以上にする。", "中", "小", 1))
    elif live and pc["lowContrast"]:
        w = pc["lowContrast"][0]
        out.append(finding("contrast", "スマホ表示", "ボタンの文字を読みやすい色の組み合わせにする", "文字と背景の差が小さいと、読みにくく、押されにくい。",
                           f"PCで「{w['label']}」のコントラスト比は約{w['ratio']}:1(推定)。WCAG 2.x の基準は通常の文字で4.5:1。", "背景色を濃くするか、文字色を変えて、4.5:1 以上にする。", "中", "小", 1))
    if live and sp["overflowX"]:
        out.append(finding("overflow", "スマホ表示", "スマホの横スクロールをなくす", "画面からはみ出す要素があり、操作しにくい。", "幅390pxで、ページの幅が画面幅を超えている。", "はみ出している要素(画像・表・固定幅)を特定し、幅を画面内に収める。", "高", "中", 2))
    if live and sp["smallTargets"] > 8:
        out.append(finding("tap", "スマホ表示", "小さいボタン・メニューのタップ対象を大きくする", "押し間違いが増え、操作しにくい。",
                           f"スマホで、幅または高さが24px未満のボタン・ナビのリンクが{sp['smallTargets']}個(WCAG 2.2 の達成基準2.5.8の最小サイズは24×24 CSSピクセル。本文中のリンクは除外)。", "余白を加えて、押せる範囲を24px以上(できれば44px前後が目安)にする。", "低", "中", 0.7))

    # ---- 信頼性・不安の解消 ----
    if agg("FAQ") == 0 and not any(re.search(r"faq|よくある|qa", (l["href"] + l["text"]).lower()) for l in site.get("links", [])):
        out.append(finding("faq", "信頼性", "よくある質問(FAQ)を作る", "料金・手順・対象などの疑問に答える場所がないと、問い合わせ前に不安が残る。",
                           f"取得した{len(pages)}ページで『FAQ』『よくある質問』に当たる記載・リンクを確認できなかった。", "問い合わせでよく聞かれる5〜10問から始める。回答は実際の運用に合わせる。", "中", "中", 1))
    if agg("実績・事例") == 0 and agg("お客様の声") == 0:
        out.append(finding("proof", "信頼性", "実績・お客様の声・事例を載せる", "『選ぶ理由』の裏づけがなく、強みが主張で止まる。",
                           f"取得した{len(pages)}ページで『実績』『事例』『お客様の声』に当たる語を確認できなかった。", "許可を得た範囲で、利用者の声・事例・数字を載せる。業種によっては表現の規制があるため、所管の確認が必要。", "中", "中", 1))
    has_form = any(p["facts"]["cta"]["forms"] for p in pages) or bool(re.search(r"(お名前|氏名).{0,40}(メール)", text_all)) or bool(re.search(r"送信", text_all) and re.search(r"メールアドレス", text_all))
    if has_form:
        privacy = agg("法的表記") > 0 or any(re.search(r"プライバシー|個人情報|privacy", l["text"] + l["href"], re.I) for l in site.get("links", []))
        if not privacy:
            out.append(finding("privacy", "問い合わせまでの流れ", "フォームの近くに個人情報の取扱いを示す", "氏名・連絡先を集めるフォームに取扱いの説明がないと、不安と離脱につながる。",
                               "フォームが確認できるが、『プライバシーポリシー』『個人情報』の記載・リンクを確認できなかった。", "プライバシーポリシーのページを作り、送信ボタンの上にリンクと同意文を置く。記載事項の適否は専門家に確認。", "高", "中", 1.5))
        if not re.search(r"営業日|日以内|以内に|折り返し|翌営業|当日中", text_all):
            out.append(finding("reply", "問い合わせまでの流れ", "返信の目安を明記する", "送信後いつ連絡が来るか分からず、送信をためらう要因になる。",
                               "取得したページに、返信・折り返しの目安(『◯営業日以内』など)の記載を確認できなかった。", "フォームの直上に『通常◯営業日以内にご連絡します』を追加する(実際の運用に合う日数で)。", "中", "小", 1))
        big = [(p["url"], f) for p in pages for f in p["facts"]["cta"]["forms"] if f["required"] >= 6 or f["fields"] >= 12]
        if big:
            u, f = big[0]
            out.append(finding("formlong", "問い合わせまでの流れ", "フォームの入力項目を減らす", "入力が多いほど、途中で離脱されやすい。",
                               f"{u} のフォーム: 入力欄{f['fields']}個、必須{f['required']}個。", "最初の連絡に必須でない項目を任意にする、または後の面談で聞く。", "中", "中", 1))
    if not cta:
        out.append(finding("nocta", "CTA", "問い合わせ・予約への入口を、メニューに明示する", "次の一歩が分からず、離脱しやすい。",
                           "トップページのリンクに、問い合わせ・予約・資料請求などの文言を確認できなかった。", "ヘッダーに『お問い合わせ』などのボタンを常設する。", "高", "小", 2))

    # ---- スコア ----
    scores = {}
    scored = AUTO_ITEMS if live else ["信頼性", "SEO", "問い合わせまでの流れ"]  # 持ち込みでは、画面の計測が要る項目は採点しない
    for item in AUTO_ITEMS:
        pen = sum(f["weight"] for f in out if f["item"] == item)
        scores[item] = max(1, min(5, round(5 - pen))) if item in scored else None

    # ---- 改善イメージの計画 ----
    weak_h1 = (not nonempty) or all((not JP.search(t)) or len(t) < 8 for t in nonempty)
    desc = (h.get("description") or "").strip().replace("\n", " ")
    tagline = re.split(r"(?<=。)", desc)[0][:70] if desc and weak_h1 else None
    plan = {}
    for view, m in ((("sp", sp), ("pc", pc)) if live else ()):
        changes = []
        patch = {"changes": changes}
        if not m["fvCtas"] and not m["fvButtons"] and cta:
            patch.update(fvCta=True, ctaLabel=cta["label"], ctaUrl=cta["url"]); changes.append(f"ファーストビューに「{cta['label']}」のボタンを追加")
        if m["lowContrast"]:
            patch["contrastFix"] = True; changes.append("コントラスト不足のボタンの背景を調整")
        if tagline and not m["heroInfo"]["has_jp_block"]:
            patch["tagline"] = tagline; changes.append("見出しの下に、サイト自身の説明文を1文追加")
        if view == "sp" and not m["fixedCtas"] and (cta or tel or line):
            patch.update(stickyBar=True, tel=tel or (phones[0] if phones else None), line=line,
                         ctaLabel=(cta or {"label": "お問い合わせ"})["label"], ctaUrl=(cta or {"url": "#"})["url"])
            changes.append("画面下に固定の連絡バー(電話・LINE・問い合わせ)を追加")
        if phones:
            patch["telify"] = True
            if changes:
                changes.append("電話番号をタップで発信できるリンクに変更")
        plan[view] = patch

    if not live:  # 持ち込みでは、サイトから『取得』していない
        for f in out:
            f["evidence"] = f["evidence"].replace("取得した", "提供された")
    out.sort(key=lambda f: (-IMPACT[f["impact"]], EFFORT[f["effort"]], -f["weight"]))
    signals = {"cta": cta, "tel": tel, "phones": phones, "line": line, "h1": nonempty, "tagline": tagline}
    return out, plan, scores, signals
