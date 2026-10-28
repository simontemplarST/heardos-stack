#!/usr/bin/env python3
"""eml2pdf.py — turn an emailed document (.eml) into a PDF document at the end of Paperless's workflow. stdlib only.

Paperless renders every .eml to an archive PDF (Tika + Gotenberg) but keeps the email as the document's file. A Paperless
workflow ("Email → PDF", Document Updated, original filename *.eml, after AI filing) calls this service's webhook:
    POST /convert   header X-Hook-Secret: <EML2PDF_SECRET>   json {"doc_id": "<id>"}   (workflow param "{{ doc_id }}" — Paperless 3 templates are Jinja)
It downloads the rendered PDF and adds it as a NEW VERSION of the same document (Paperless >= 3 document versions), labelled
"PDF": same id, title, tags, notes, owner; the PDF is what people see and download; the original email stays in the version
history. Idempotent: a document whose latest version is already a PDF is left alone (the workflow fires on every update).
Runs on the paperless stack's internal network only, as the Paperless user `eml2pdf` (view/change on the documents the
email workflow grants it). Env: PAPERLESS_URL, EML2PDF_TOKEN, EML2PDF_SECRET.
"""
import hmac, json, os, sys, threading, time, urllib.error, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

URL = os.environ.get("PAPERLESS_URL", "http://paperless-webserver:8000").rstrip("/")
TOKEN, SECRET = os.environ.get("EML2PDF_TOKEN", ""), os.environ.get("EML2PDF_SECRET", "")
LOCK = threading.Lock()
busy = set()


def log(*a):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), *a, flush=True)


def api(method, path, data=None, ctype=None, raw=False):
    h = {"Authorization": f"Token {TOKEN}", "Accept": "application/json; version=9"}
    if ctype: h["Content-Type"] = ctype
    r = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    with urllib.request.urlopen(r, timeout=120) as x:
        body = x.read()
        return (x.headers.get("Content-Type", ""), body) if raw else json.loads(body or b"null")


def latest_mime(doc_id):
    """(document, effective type): "application/pdf" once a version labelled PDF exists (versions come newest first)."""
    d = api("GET", f"/api/documents/{doc_id}/?fields=id,mime_type,title,versions")
    vs = d.get("versions") or []
    if any((v.get("version_label") or "") == "PDF" for v in vs):
        return d, "application/pdf"
    return d, d.get("mime_type") or ""


def convert(doc_id):
    d, mime = latest_mime(doc_id)
    if mime == "application/pdf":
        log(f"doc {doc_id}: latest version is already a PDF — nothing to do"); return "skip"
    if mime != "message/rfc822":
        log(f"doc {doc_id}: {mime!r} is not an email — nothing to do"); return "skip"
    ctype, pdf = api("GET", f"/api/documents/{doc_id}/download/?original=false", raw=True)
    if not pdf.startswith(b"%PDF"):
        log(f"doc {doc_id}: no rendered PDF yet ({ctype})"); return "no-archive"
    name = "".join(c if c.isalnum() or c in " ._-()" else "-" for c in (d.get("title") or f"document {doc_id}"))[:120] + ".pdf"
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"version_label\"\r\n\r\nPDF\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{name}\"\r\nContent-Type: application/pdf\r\n\r\n").encode() \
        + pdf + f"\r\n--{b}--\r\n".encode()
    task = api("POST", f"/api/documents/{doc_id}/update_version/", body, f"multipart/form-data; boundary={b}")
    log(f"doc {doc_id}: PDF ({len(pdf)} bytes) queued as a new version, task {task}"); return "queued"


class Hook(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/convert" or not SECRET or not hmac.compare_digest(self.headers.get("X-Hook-Secret", ""), SECRET):
            self.send_response(403); self.end_headers(); return
        try:
            doc_id = int(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("doc_id"))
        except Exception:
            self.send_response(400); self.end_headers(); return
        with LOCK:
            if doc_id in busy:
                self.send_response(202); self.end_headers(); return
            busy.add(doc_id)
        self.send_response(202); self.end_headers()      # answer the webhook at once; convert in the background

        def work():
            try:
                time.sleep(3)                               # let the triggering save finish
                convert(doc_id)
            except urllib.error.HTTPError as e:
                log(f"doc {doc_id}: Paperless {e.code} {e.read()[:200]!r}")
            except Exception as e:  # noqa: BLE001
                log(f"doc {doc_id}: {e}")
            finally:
                with LOCK: busy.discard(doc_id)
        threading.Thread(target=work, daemon=True).start()

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404); self.end_headers()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--convert":      # manual: eml2pdf.py --convert DOC_ID
        print(convert(int(sys.argv[2]))); sys.exit(0)
    if not TOKEN or not SECRET:
        log("EML2PDF_TOKEN / EML2PDF_SECRET not set — idle"); 
        while True: time.sleep(3600)
    log(f"eml2pdf listening on :8080 -> {URL}")
    ThreadingHTTPServer(("0.0.0.0", 8080), Hook).serve_forever()
