#!/usr/bin/env python3
"""heardos-ocr: a self-hosted OCR endpoint for Paperless-ngx.

Paperless-ngx (>= 3.0) can hand OCR to a "remote engine", but the only engine it speaks is the
Azure AI Document Intelligence REST API (model prebuilt-read, output=pdf). This server implements
exactly the slice of that API Paperless uses and backs it with OCRmyPDF/Tesseract running locally,
so documents never leave the host. Standard library only; runs on the jbarlow83/ocrmypdf image.

  POST /documentintelligence/documentModels/{model}:analyze        -> 202 + Operation-Location
  GET  /documentintelligence/documentModels/{model}/analyzeResults/{id}      -> status / analyzeResult
  GET  /documentintelligence/documentModels/{model}/analyzeResults/{id}/pdf  -> searchable PDF
  GET  /health

Auth: header Ocp-Apim-Subscription-Key (or Authorization: Bearer) must equal OCR_API_KEY.
"""
import base64
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

API_VERSION = "2024-11-30"
API_KEY = os.environ.get("OCR_API_KEY", "")
LANGS = os.environ.get("OCR_LANGS", "eng")
EXTRA_ARGS = os.environ.get(
    "OCR_ARGS", "--rotate-pages --deskew --skip-text --optimize 1 --output-type pdfa --image-dpi 300"
).split()
WORKERS = int(os.environ.get("OCR_WORKERS", "2"))          # documents OCR'd concurrently
JOBS = int(os.environ.get("OCR_JOBS", "2"))                # tesseract threads per document
PORT = int(os.environ.get("OCR_PORT", "8000"))
WORKDIR = Path(os.environ.get("OCR_WORKDIR", "/data/jobs"))
TTL = int(os.environ.get("OCR_TTL_SECONDS", "3600"))       # keep finished jobs this long
TIMEOUT = int(os.environ.get("OCR_TIMEOUT", "1800"))       # per-document OCR timeout
MAX_BODY = int(os.environ.get("OCR_MAX_BODY_MB", "512")) * 1024 * 1024
MAX_QUEUE = int(os.environ.get("OCR_MAX_QUEUE", "32"))     # jobs notStarted+running; more -> 503 + Retry-After
# Operation-Location is built from OCR_PUBLIC_BASE, never from client-supplied X-Forwarded-Host. Without it the
# request's own Host is used, but only when it is in OCR_ALLOWED_HOSTS.
PUBLIC_BASE = os.environ.get("OCR_PUBLIC_BASE", "").rstrip("/")
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("OCR_ALLOWED_HOSTS", f"heardos-ocr:{PORT},heardos-ocr,localhost:{PORT},127.0.0.1:{PORT}").split(",") if h.strip()}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("heardos-ocr")

JOBS_LOCK = threading.Lock()
JOB_STATE = {}  # id -> dict(status, created, updated, model, error, result)
POOL = ThreadPoolExecutor(max_workers=WORKERS)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_sizes(pdf_path):
    """[(width_in, height_in), ...] via pikepdf (ships with ocrmypdf); falls back to Letter."""
    try:
        import pikepdf
        out = []
        with pikepdf.open(pdf_path) as pdf:
            for p in pdf.pages:
                box = [float(x) for x in p.mediabox]
                out.append((round((box[2] - box[0]) / 72, 4), round((box[3] - box[1]) / 72, 4)))
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("page size probe failed: %s", e)
        return []


def run_job(job_id, model):
    d = WORKDIR / job_id
    inp, out, sidecar = d / "input", d / "output.pdf", d / "sidecar.txt"
    with JOBS_LOCK:
        JOB_STATE[job_id].update(status="running", updated=now_iso())
    cmd = ["ocrmypdf", "-l", LANGS, "--jobs", str(JOBS), "--sidecar", str(sidecar), *EXTRA_ARGS, str(inp), str(out)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        # 0 = ok; 10 = PDF produced but PDF/A validation failed (still a good PDF); 6 = input already had text
        if proc.returncode not in (0, 10) or not out.exists():
            raise RuntimeError(f"ocrmypdf exit {proc.returncode}: {proc.stderr.strip()[-800:]}")
        if proc.returncode == 10:
            log.warning("job %s: PDF/A conversion failed, keeping plain PDF", job_id)
        raw = sidecar.read_text(errors="replace") if sidecar.exists() else ""
        pages_text = raw.split("\f")
        if pages_text and pages_text[-1].strip() == "":
            pages_text = pages_text[:-1]
        sizes = page_sizes(out)
        n = max(len(sizes), len(pages_text), 1)
        content, pages, offset = "", [], 0
        for i in range(n):
            txt = pages_text[i].strip() if i < len(pages_text) else ""
            w, h = sizes[i] if i < len(sizes) else (8.5, 11.0)
            pages.append({"pageNumber": i + 1, "angle": 0, "width": w, "height": h, "unit": "inch",
                          "words": [], "lines": [], "spans": [{"offset": offset, "length": len(txt)}]})
            content += txt + ("\n\n" if i < n - 1 else "")
            offset = len(content)
        result = {"apiVersion": API_VERSION, "modelId": model, "stringIndexType": "textElements",
                  "contentFormat": "text", "content": content, "pages": pages, "paragraphs": [], "styles": []}
        (d / "result.json").write_text(json.dumps(result))
        with JOBS_LOCK:
            JOB_STATE[job_id].update(status="succeeded", updated=now_iso())
        log.info("job %s: %d page(s), %d chars, %.1fs", job_id, n, len(content), time.time() - t0)
    except Exception as e:  # noqa: BLE001
        log.error("job %s failed: %s", job_id, e)
        with JOBS_LOCK:
            JOB_STATE[job_id].update(status="failed", updated=now_iso(), error=str(e)[:2000])
    finally:
        for f in (inp, sidecar):
            try:
                f.unlink()
            except FileNotFoundError:
                pass


def janitor():
    while True:
        time.sleep(300)
        cutoff = time.time() - TTL
        for d in WORKDIR.iterdir() if WORKDIR.exists() else []:
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    with JOBS_LOCK:
                        st = JOB_STATE.get(d.name, {}).get("status")
                    if st in (None, "succeeded", "failed"):
                        shutil.rmtree(d, ignore_errors=True)
                        with JOBS_LOCK:
                            JOB_STATE.pop(d.name, None)
            except Exception as e:  # noqa: BLE001
                log.warning("janitor: %s", e)


ANALYZE = re.compile(r"^/documentintelligence/documentModels/([^/:]+):analyze$")
RESULT = re.compile(r"^/documentintelligence/documentModels/([^/:]+)/analyzeResults/([0-9a-f]{32})(/pdf)?$")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "heardos-ocr/1.0"

    def log_message(self, fmt, *args):  # quieter access log
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, code, msg, ecode="InvalidRequest"):
        self._send(code, {"error": {"code": ecode, "message": msg}})

    def _authed(self):
        if not API_KEY:
            return True
        key = self.headers.get("Ocp-Apim-Subscription-Key")
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            key = key or auth[7:].strip()
        return hmac.compare_digest((key or "").encode(), API_KEY.encode())

    def _base_url(self):
        if PUBLIC_BASE:
            return PUBLIC_BASE
        host = (self.headers.get("Host") or "").strip().lower()
        return f"http://{host if host in ALLOWED_HOSTS else f'heardos-ocr:{PORT}'}"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            with JOBS_LOCK:
                counts = {}
                for j in JOB_STATE.values():
                    counts[j["status"]] = counts.get(j["status"], 0) + 1
            return self._send(200, {"status": "ok", "engine": "ocrmypdf", "langs": LANGS, "jobs": counts})
        if not self._authed():
            return self._error(401, "Access denied due to invalid subscription key.", "401")
        m = RESULT.match(path)
        if not m:
            return self._error(404, "Resource not found.", "NotFound")
        model, job_id, want_pdf = m.group(1), m.group(2), bool(m.group(3))
        with JOBS_LOCK:
            st = dict(JOB_STATE.get(job_id) or {})
        if not st:
            return self._error(404, "Analyze result not found.", "NotFound")
        if want_pdf:
            pdf = WORKDIR / job_id / "output.pdf"
            if st["status"] != "succeeded" or not pdf.exists():
                return self._error(404, "PDF output is not available for this result.", "NotFound")
            data = pdf.read_bytes()
            return self._send(200, data, "application/pdf")
        body = {"status": st["status"], "createdDateTime": st["created"], "lastUpdatedDateTime": st["updated"]}
        if st["status"] == "succeeded":
            body["analyzeResult"] = json.loads((WORKDIR / job_id / "result.json").read_text())
        elif st["status"] == "failed":
            body["error"] = {"code": "InternalServerError", "message": st.get("error", "OCR failed")}
        return self._send(200, body, extra={"Retry-After": "2"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._authed():
            return self._error(401, "Access denied due to invalid subscription key.", "401")
        m = ANALYZE.match(path)
        if not m:
            return self._error(404, "Resource not found.", "NotFound")
        model = m.group(1)
        with JOBS_LOCK:
            pending = sum(1 for j in JOB_STATE.values() if j["status"] in ("notStarted", "running"))
        if pending >= MAX_QUEUE:
            return self._send(503, {"error": {"code": "TooManyRequests", "message": "OCR queue is full; retry later."}},
                              extra={"Retry-After": "30"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "Bad Content-Length.")
        if length <= 0 or length > MAX_BODY:
            return self._error(400, f"Body required and must be <= {MAX_BODY} bytes.")
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "application/json":
            try:
                req = json.loads(raw)
            except ValueError:
                return self._error(400, "Malformed JSON body.")
            if req.get("urlSource"):
                return self._error(400, "urlSource is not supported by this engine; send base64Source.")
            try:
                data = base64.b64decode(req.get("base64Source") or "", validate=False)
            except Exception:  # noqa: BLE001
                return self._error(400, "base64Source is not valid base64.")
            if not data:
                return self._error(400, "base64Source is required.")
        else:
            data = raw
        job_id = uuid.uuid4().hex
        d = WORKDIR / job_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "input").write_bytes(data)
        with JOBS_LOCK:
            JOB_STATE[job_id] = {"status": "notStarted", "created": now_iso(), "updated": now_iso(), "model": model}
        POOL.submit(run_job, job_id, model)
        log.info("job %s queued: model=%s bytes=%d", job_id, model, len(data))
        loc = f"{self._base_url()}/documentintelligence/documentModels/{model}/analyzeResults/{job_id}?api-version={API_VERSION}"
        return self._send(202, b"", extra={"Operation-Location": loc, "apim-request-id": job_id})


def main():
    WORKDIR.mkdir(parents=True, exist_ok=True)
    if not API_KEY:
        log.warning("OCR_API_KEY is empty: endpoint is unauthenticated")
    threading.Thread(target=janitor, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    log.info("heardos-ocr listening on :%d (langs=%s workers=%d jobs=%d args=%s)", PORT, LANGS, WORKERS, JOBS, " ".join(EXTRA_ARGS))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
