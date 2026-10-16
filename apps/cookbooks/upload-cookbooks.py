#!/usr/bin/env python3
"""Runs ON CT 108. For each book in /root/cookbooks.json: upload the PDF to Paperless (tag Cookbook, type Book)
and to Calibre-Web (PDF = new book, then EPUB added as a 2nd format, then title/author/pubdate/publisher set).
Env: PAPERLESS_AUTH=user:pass  CW_USER / CW_PASS. Idempotent via /root/cookbooks/state.json."""
import json, os, re, sys, uuid, base64, urllib.request, urllib.parse, http.cookiejar, mimetypes, time
PAPERLESS="http://192.168.10.20:8012"; CW="http://books.192-168-10-20.sslip.io"
TAG_COOKBOOK=18; TYPE_BOOK=17
MAN=sys.argv[1] if len(sys.argv)>1 else "/root/cookbooks.json"; books=json.load(open(MAN)); st_p="/root/cookbooks/state-"+os.path.basename(MAN)
state=json.load(open(st_p)) if os.path.exists(st_p) else {}
def save(): json.dump(state, open(st_p,"w"), indent=1)
def multipart(fields, files):
    b="----cb"+uuid.uuid4().hex; out=b""
    for k,v in fields.items(): out+=f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k,(fn,data,ct) in files.items(): out+=f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: {ct}\r\n\r\n".encode()+data+b"\r\n"
    return out+f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"
# --- Paperless
PA="Basic "+base64.b64encode(os.environ["PAPERLESS_AUTH"].encode()).decode()
def pl(method, path, data=None, ct="application/json"):
    req=urllib.request.Request(PAPERLESS+path, method=method, data=data, headers={"Authorization":PA, **({"Content-Type":ct} if data else {})})
    with urllib.request.urlopen(req, timeout=300) as r: b=r.read(); return json.loads(b) if b.startswith(b"{") or b.startswith(b"[") else b.decode()
# --- Calibre-Web
cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def cw_get(p):
    with op.open(CW+p, timeout=120) as r: return r.read().decode()
def cw_post(p, data=None, body=None, ct=None, xhr=False):
    if data is not None: body=urllib.parse.urlencode(data).encode(); ct="application/x-www-form-urlencoded"
    h={"Content-Type":ct,"Referer":CW+p}; 
    if xhr: h["X-Requested-With"]="XMLHttpRequest"
    with op.open(urllib.request.Request(CW+p, data=body, headers=h), timeout=600) as r: return r.geturl(), r.read().decode()
def csrf(html): return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
def form_vals(html):
    f=[x for x in re.findall(r'<form.*?</form>', html, re.S) if 'name="title"' in x][0]; d={}
    for tag in re.findall(r'<input\b[^>]*>', f):
        n=re.search(r'name="([^"]+)"', tag); t=(re.search(r'type="([^"]+)"', tag) or [None,"text"])[1]
        if not n or t in ("submit","button","file"): continue
        if t=="checkbox":
            if re.search(r'\bchecked\b', tag): d[n.group(1)]="on"
        else: v=re.search(r'value="([^"]*)"', tag); d[n.group(1)]=v.group(1) if v else ""
    for n,body in re.findall(r'<select\b[^>]*name="([^"]+)"[^>]*>(.*?)</select>', f, re.S):
        o=re.search(r'<option[^>]*\bselected\b[^>]*value="([^"]*)"', body) or re.search(r'<option[^>]*value="([^"]*)"[^>]*\bselected\b', body); d[n]=o.group(1) if o else ""
    for n,body in re.findall(r'<textarea\b[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', f, re.S): d[n]=body
    return d
u,b=cw_post("/login", {"csrf_token":csrf(cw_get("/login")),"username":os.environ["CW_USER"],"password":os.environ["CW_PASS"],"next":"/","remember_me":"on"})
assert "Logout" in b, "calibre-web login failed"
for bk in books:
    s=state.setdefault(bk["id"], {}); d=f"/root/cookbooks/{bk['id']}"; pdf=open(f"{d}/{bk['pdf']}","rb").read()
    fn=re.sub(r'[^A-Za-z0-9 ,.&:-]','', bk["title"])+".pdf"
    if "paperless_task" not in s:
        body,ct=multipart({"title":bk["title"],"tags":str(TAG_COOKBOOK),"document_type":str(TYPE_BOOK),"created":f"{bk['year']}-01-01"}, {"document":(fn,pdf,"application/pdf")})
        s["paperless_task"]=pl("POST","/api/documents/post_document/",body,ct).strip('"\n'); save(); print(bk["id"],"paperless task",s["paperless_task"])
    if "cw_book" not in s:
        body,ct=multipart({"csrf_token":csrf(cw_get("/"))}, {"btn-upload":(fn,pdf,"application/pdf")})
        u,r=cw_post("/upload", body=body, ct=ct, xhr=True); s["cw_book"]=int(re.search(r'/admin/book/(\d+)', json.loads(r)["location"]).group(1)); save(); print(bk["id"],"calibre book",s["cw_book"])
    bid=s["cw_book"]
    if bk["epub"] and not s.get("cw_epub"):
        html=cw_get(f"/admin/book/{bid}"); body,ct=multipart({"csrf_token":csrf(html),"book_id":str(bid)}, {"btn-upload-format":(os.path.basename(bk["epub"]),open(f"{d}/{bk['epub']}","rb").read(),"application/epub+zip")})
        u,r=cw_post("/upload", body=body, ct=ct, xhr=True); s["cw_epub"]=True; save(); print(bk["id"],"epub added", r[:80])
    if not s.get("cw_meta"):
        html=cw_get(f"/admin/book/{bid}"); f=form_vals(html)
        f.update({"title":bk["title"],"authors":bk["authors"],"publisher":bk["publisher"],"pubdate":f"{bk['year']}-01-01","tags":"Cookbook","languages":"English","comments":f"From archive.org item {bk['id']} (https://archive.org/details/{bk['id']}).","detail_view":"on"})
        u,r=cw_post(f"/admin/book/{bid}", f); s["cw_meta"]=True; save(); print(bk["id"],"meta set ->",u)
print("ALL UPLOADED")
