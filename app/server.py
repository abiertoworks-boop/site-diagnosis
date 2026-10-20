"""URLを入れると診断できる Web アプリ(ローカル/自社サーバー用)。

起動:  python3 app/server.py   → http://127.0.0.1:8000
環境変数:
  SD_PORT      ポート(既定 8000)
  SD_HOST      待ち受けアドレス(既定 127.0.0.1。外部公開するときだけ 0.0.0.0)
  SD_PASSWORD  設定すると、画面とAPIに Basic 認証(ユーザー名は何でも可)がかかる
  SD_JOBS_DIR  結果の保存先(既定 app/jobs)。24時間で自動削除
"""
import hmac
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, request, send_from_directory

import capture
import pipeline
import upload

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.environ.get("SD_JOBS_DIR", os.path.join(HERE, "jobs"))
PASSWORD = os.environ.get("SD_PASSWORD")
JOB_TTL_S = 24 * 3600
SAME_SITE_COOLDOWN_S = 30 * 60   # 同じサイトを短時間に何度も取得しない(ボット対策に止められるため)
RATE_PER_HOUR = 6                # 同じ接続元からの診断回数の上限
ALLOWED_FILES = re.compile(r"^(report\.pdf|(pc|sp)_(fv|compare)\.png|up_(pc|sp)_\d{2}\.png)$")

app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 持ち込み資料の合計上限
pool = ThreadPoolExecutor(max_workers=2)
jobs, recent, hits = {}, {}, defaultdict(deque)
lock = threading.Lock()
os.makedirs(JOBS_DIR, exist_ok=True)


@app.before_request
def auth():
    if not PASSWORD:
        return None
    a = request.authorization
    if a and hmac.compare_digest(a.password or "", PASSWORD):
        return None
    return Response("認証が必要です", 401, {"WWW-Authenticate": 'Basic realm="site-diagnosis"'})


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def cleanup():
    now = time.time()
    with lock:
        for jid in [j for j, v in jobs.items() if now - v["created"] > JOB_TTL_S]:
            shutil.rmtree(os.path.join(JOBS_DIR, jid), ignore_errors=True)
            jobs.pop(jid, None)
        for host in [h for h, (_, t) in recent.items() if now - t > SAME_SITE_COOLDOWN_S]:
            recent.pop(host, None)


def run(jid, url):
    job = jobs[jid]
    job.update(status="running")

    def progress(pct, msg):
        job.update(progress=pct, message=msg)
    try:
        rep = pipeline.run_job(os.path.join(JOBS_DIR, jid), url, progress)
        job.update(status="done", progress=100, message="完了", report=rep)
    except capture.BlockedError as e:
        job.update(status="blocked", error=str(e))
    except ValueError as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        app.logger.exception("診断に失敗")
        job.update(status="error", error=f"診断中にエラーが起きました({type(e).__name__})。URLを確認して、もう一度お試しください。")


def run_upload(jid, html_raw, shot_raws, base_url):
    job = jobs[jid]
    job.update(status="running")

    def progress(pct, msg):
        job.update(progress=pct, message=msg)
    try:
        rep = upload.run_upload_job(os.path.join(JOBS_DIR, jid), html_raw, shot_raws, base_url, progress)
        job.update(status="done", progress=100, message="完了", report=rep)
    except ValueError as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        app.logger.exception("持ち込み診断に失敗")
        job.update(status="error", error=f"診断中にエラーが起きました({type(e).__name__})。ファイルを確認して、もう一度お試しください。")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/diagnose")
def diagnose():
    cleanup()
    data = request.get_json(silent=True) or {}
    if not data.get("agree"):
        return jsonify(error="診断対象が、ご自身のサイトであるか、診断の許可を得たサイトであることの確認が必要です。"), 400
    raw = str(data.get("url", ""))[:300]
    try:
        url = pipeline.normalize(raw)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    host = urlparse(url).netloc
    ip = request.remote_addr or "?"
    now = time.time()
    with lock:
        if host in recent and now - recent[host][1] < SAME_SITE_COOLDOWN_S and recent[host][0] in jobs:
            return jsonify(job_id=recent[host][0], cached=True)
        q = hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= RATE_PER_HOUR:
            return jsonify(error="診断の回数が上限に達しました。しばらくしてからお試しください。"), 429
        q.append(now)
        jid = uuid.uuid4().hex[:16]
        jobs[jid] = dict(status="queued", progress=0, message="順番を待っています", created=now, url=url)
        recent[host] = (jid, now)
    pool.submit(run, jid, url)
    return jsonify(job_id=jid)


@app.post("/api/upload")
def upload_diagnose():
    cleanup()
    if request.form.get("agree") != "1":
        return jsonify(error="提供する資料が、ご自身のサイト、または診断の許可を得たサイトのものであることの確認が必要です。"), 400
    f = request.files.get("html")
    kinds = request.form.getlist("kinds")
    shots = [(x.filename or "image", x.read(), kinds[i] if i < len(kinds) else "auto")
             for i, x in enumerate(request.files.getlist("shots")) if x and x.filename]
    if not f or not f.filename:
        return jsonify(error="HTMLファイルを選んでください。"), 400
    if not shots:
        return jsonify(error="スクリーンショットを1枚以上選んでください。"), 400
    html_raw = f.read(upload.MAX_HTML + 1)
    base_url = (request.form.get("url") or "").strip()[:300]
    if base_url:
        try:
            base_url = pipeline.normalize(base_url)
        except ValueError as e:
            return jsonify(error=str(e)), 400
    ip = request.remote_addr or "?"
    now = time.time()
    with lock:
        q = hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= RATE_PER_HOUR:
            return jsonify(error="診断の回数が上限に達しました。しばらくしてからお試しください。"), 429
        q.append(now)
        jid = uuid.uuid4().hex[:16]
        jobs[jid] = dict(status="queued", progress=0, message="順番を待っています", created=now, url=base_url or "(持ち込み資料)")
    pool.submit(run_upload, jid, html_raw, shots, base_url)
    return jsonify(job_id=jid)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="ファイルの合計サイズが大きすぎます(合計60MBまで)。"), 413


@app.get("/api/jobs/<jid>")
def job(jid):
    j = jobs.get(jid)
    if not j:
        abort(404)
    out = {k: j.get(k) for k in ("status", "progress", "message", "error", "url")}
    if j["status"] == "done":
        r = j["report"]
        out["report"] = {
            "site_name": r["site_name"], "url": r["url"], "date": r["date"], "summary": r["summary"],
            "scorecard": r["scorecard"], "improvements": r["improvements"], "minor": r.get("minor", []),
            "unverified": r["unverified"], "method": r["method"],
            "mockups": [{"title": m["title"], "changes": m["changes"], "image": f"/files/{jid}/{os.path.basename(m['image'])}"} for m in r["mockups"]],
            "pdf": f"/files/{jid}/report.pdf",
            "mode": "upload" if r.get("tiles") else "url",
            "tiles": {v: [f"/files/{jid}/{n}" for n in names] for v, names in (r.get("tiles") or {}).items()},
        }
    return jsonify(out)


@app.get("/files/<jid>/<name>")
def files(jid, name):
    if jid not in jobs or not ALLOWED_FILES.match(name):
        abort(404)
    return send_from_directory(os.path.join(JOBS_DIR, jid), name, as_attachment=(name == "report.pdf"),
                               download_name="site-diagnosis.pdf" if name == "report.pdf" else None)


if __name__ == "__main__":
    host = os.environ.get("SD_HOST", "127.0.0.1")
    if host != "127.0.0.1" and not PASSWORD:
        raise SystemExit("外部に公開するときは、SD_PASSWORD を設定してください(誰でも診断を実行できてしまいます)。")
    app.run(host=host, port=int(os.environ.get("SD_PORT", "8000")), threaded=True)
