#!/usr/bin/env python3
"""63-post-install.py TASK — finish an app's first-run setup over its API instead of in a browser. Run ON THE HOST as root.

  ha-onboard        create the Home Assistant owner (ADMIN_USER), finish onboarding, mint a long-lived token -> HA_TOKEN
                    (valid 365 days; to rotate: delete HA_TOKEN from secrets.env, revoke "HeardOS installer" in the owner's HA
                    profile > Security, and mint a new one there — onboarding only runs once)
  mealie-token      log in as Mealie's built-in admin and create an API token for the family dashboard -> MEALIE_TOKEN
  nextcloud-admin   put ADMIN_USER in Nextcloud's admin group (exit 3 = they have not signed in to Nextcloud yet)
  baikal-user       (exit 3 = Baïkal wizard not finished) create the Baïkal user for ADMIN_USER -> BAIKAL_ADMIN_PASSWORD
  paperless-gpt-token  service user `paperless-gpt` in Paperless (NOT a superuser: view/change documents, view/add tags and
                    correspondents, view document types + custom fields) + its API token -> PAPERLESS_GPT_TOKEN
  paperless-gpt     pull the Ollama model, add the Paperless workflow that tags new documents paperless-gpt-auto,
                    and queue every existing document once
  listmonk-config   Listmonk root URL, sender (MAIL_FROM), SMTP (MAIL_MODE=resend + RESEND_API_KEY, or SMTP_*), OIDC
  hortusfox-cron    HortusFox cronjob password + an hourly root cron entry (recurring tasks, calendar reminders)

Results are stored in $HEARDOS_ROOT/secrets/secrets.env and printed ONLY as HEARDOS_OUTPUT lines for the installer's vault
(the installer masks those in its log); no other output carries a secret value.
Idempotent: a value already in secrets.env is reused. stdlib only.
"""
import base64, json, os, secrets, socket, struct, subprocess, sys, time, urllib.parse, urllib.request, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import load_env  # noqa: E402

env = load_env()
ROOT = env.get("HEARDOS_ROOT", "/opt/heardos")
SECRETS = f"{ROOT}/secrets/secrets.env"
LAN, IP, USER, EMAIL = env["LAN_DOMAIN"], env["HOST_LAN_IP"], env["ADMIN_USER"], env["ADMIN_EMAIL"]
NAME = env.get("ADMIN_NAME") or USER


def have(k):
    try:
        for line in open(SECRETS):
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def keep(k, v):   # the one place a secret value is printed: a HEARDOS_OUTPUT line, which the installer masks
    lines = [l for l in open(SECRETS)] if os.path.exists(SECRETS) else []
    lines = [l for l in lines if not l.startswith(k + "=")] + [f"{k}={v}\n"]
    with open(SECRETS, "w") as f:
        f.writelines(lines)
    os.chmod(SECRETS, 0o600)
    print(f"HEARDOS_OUTPUT {k}={v}")


def http(method, url, data=None, headers=None, form=False, timeout=60):
    h = dict(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[", b'"') else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:400]


def wait(url, secs=600):
    t = time.time()
    while time.time() - t < secs:
        try:
            urllib.request.urlopen(url, timeout=5); return
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(5)
    sys.exit(f"{url} did not answer within {secs}s")


# ------------------------------------------------------------------ Home Assistant
def ws_call(host, port, path, token, msg):
    """Minimal websocket client (enough for HA's auth + one command)."""
    s = socket.create_connection((host, port), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        raise SystemExit("HA websocket upgrade refused")
    rest = buf.split(b"\r\n\r\n", 1)[1]

    def recv_exact(n):
        nonlocal rest
        while len(rest) < n:
            chunk = s.recv(65536)
            if not chunk:
                raise SystemExit("HA websocket closed")
            rest += chunk
        out, rest = rest[:n], rest[n:]
        return out

    def recv():
        b1, b2 = recv_exact(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", recv_exact(8))[0]
        return json.loads(recv_exact(n))

    def send(obj):
        data = json.dumps(obj).encode(); mask = os.urandom(4); n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    assert recv()["type"] == "auth_required"
    send({"type": "auth", "access_token": token})
    if recv()["type"] != "auth_ok":
        raise SystemExit("HA websocket auth failed")
    send({"id": 1, **msg})
    while True:
        m = recv()
        if m.get("id") == 1:
            s.close()
            if not m.get("success"):
                raise SystemExit(f"HA command failed: {m.get('error')}")
            return m["result"]


HA_PORT = 8209   # host-only port (127.0.0.1) from stacks/homeassistant


def ha_onboard():
    base = f"http://127.0.0.1:{HA_PORT}"
    if have("HA_TOKEN"):
        st, _ = http("GET", f"{base}/api/", headers={"Authorization": "Bearer " + have("HA_TOKEN")})
        if st == 200:
            print("HA already onboarded; token works"); print(f"HEARDOS_OUTPUT HA_TOKEN={have('HA_TOKEN')}"); return
    wait(f"{base}/api/onboarding", 900)
    st, steps = http("GET", f"{base}/api/onboarding")
    done = {s["step"] for s in steps if s.get("done")} if isinstance(steps, list) else set()
    if "user" in done:
        sys.exit("Home Assistant already has an owner that this installer did not create; mint a token in its profile and set HA_TOKEN")
    cid = f"{base}/"
    pw = have("HA_OWNER_PASSWORD") or secrets.token_urlsafe(18)
    keep("HA_OWNER_PASSWORD", pw)
    st, r = http("POST", f"{base}/api/onboarding/users", {"client_id": cid, "name": NAME, "username": USER, "password": pw, "language": "en"})
    if st != 200:
        sys.exit(f"HA onboarding (user) failed: {st} {r}")
    st, t = http("POST", f"{base}/auth/token", {"grant_type": "authorization_code", "code": r["auth_code"], "client_id": cid}, form=True)
    if st != 200:
        sys.exit(f"HA token exchange failed: {st} {t}")
    auth = {"Authorization": "Bearer " + t["access_token"]}
    http("POST", f"{base}/api/onboarding/core_config", {}, headers=auth)
    http("POST", f"{base}/api/onboarding/analytics", {}, headers=auth)
    http("POST", f"{base}/api/onboarding/integration", {"client_id": cid, "redirect_uri": f"{base}/?auth_callback=1"}, headers=auth)
    tok = ws_call("127.0.0.1", HA_PORT, "/api/websocket", t["access_token"],
                  {"type": "auth/long_lived_access_token", "client_name": "HeardOS installer", "lifespan": 365})
    keep("HA_TOKEN", tok)
    print(f"Home Assistant onboarded: owner {USER}; long-lived token stored as HA_TOKEN")


# ------------------------------------------------------------------ Mealie
def mealie_token():
    if have("MEALIE_TOKEN"):
        print(f"HEARDOS_OUTPUT MEALIE_TOKEN={have('MEALIE_TOKEN')}"); return
    base = f"http://mealie.{LAN}"
    wait(f"{base}/api/app/about", 600)
    st, t = http("POST", f"{base}/api/auth/token", {"username": EMAIL, "password": have("MEALIE_ADMIN_PASSWORD")}, form=True)
    if st != 200:
        sys.exit(f"Mealie admin login failed: {st} {t}")
    st, r = http("POST", f"{base}/api/users/api-tokens", {"name": "heardos-dash"}, headers={"Authorization": "Bearer " + t["access_token"]})
    if st not in (200, 201):
        sys.exit(f"Mealie API token failed: {st} {r}")
    keep("MEALIE_TOKEN", r["token"])
    print("Mealie API token created for the dashboard")


# ------------------------------------------------------------------ Nextcloud
def occ(*args):
    return subprocess.run(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", *args], capture_output=True, text=True)


def nextcloud_admin():
    if occ("user:info", USER).returncode != 0:
        print(f"{USER} has not signed in to Nextcloud yet"); sys.exit(3)
    r = occ("group:adduser", "admin", USER)
    print(f"{USER} is a Nextcloud admin" if r.returncode == 0 or "already" in (r.stdout + r.stderr) else f"occ: {r.stderr.strip()}")


# ------------------------------------------------------------------ Baïkal
def baikal_user():
    db = f"{ROOT}/data/baikal/specific/db/db.sqlite"
    if not (os.path.exists(db) and os.path.exists(f"{ROOT}/data/baikal/config/baikal.yaml")):
        print("Baïkal wizard not finished yet"); sys.exit(3)
    pw = have("BAIKAL_ADMIN_PASSWORD") or secrets.token_urlsafe(14)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "60-baikal.py"), "user", USER, EMAIL, NAME, "-"],
                       input=pw + "\n", capture_output=True, text=True)   # password on stdin, never on argv
    if r.returncode:
        sys.exit(r.stderr.strip() or r.stdout.strip())
    keep("BAIKAL_ADMIN_PASSWORD", pw)
    print(f"Baïkal user {USER} ready (calendar password stored as BAIKAL_ADMIN_PASSWORD)")


# ------------------------------------------------------------------ helpers for container-internal APIs
def cip(name, net="proxy"):
    return subprocess.check_output(["docker", "inspect", name, "--format", "{{(index .NetworkSettings.Networks \"%s\").IPAddress}}" % net], text=True).strip()


# ------------------------------------------------------------------ paperless-gpt
# model permissions for the paperless-gpt service user (Paperless "documents" app). Documents owned by someone else also
# need object permissions: the AI filing workflow grants view+change on every new document, and `paperless-gpt` grants
# them once on the existing ones.
GPT_PERMS = ["view_document", "change_document", "view_tag", "add_tag", "view_correspondent", "add_correspondent",
             "view_documenttype", "view_customfield"]


def paperless_gpt_token():
    # always (re)applied, so an older install's superuser service account is demoted; the token is reused if it exists
    code = ("from django.contrib.auth.models import User, Permission\nfrom rest_framework.authtoken.models import Token\n"
            "u,_=User.objects.get_or_create(username='paperless-gpt')\n"
            "u.set_unusable_password(); u.is_staff=False; u.is_superuser=False; u.save()\n"
            f"u.user_permissions.set(Permission.objects.filter(content_type__app_label='documents', codename__in={GPT_PERMS!r}))\n"
            "print('TOKEN='+Token.objects.get_or_create(user=u)[0].key)")
    out = subprocess.check_output(["docker", "exec", "paperless-webserver", "python3", "manage.py", "shell", "-c", code], text=True)
    tok = [l for l in out.splitlines() if l.startswith("TOKEN=")][-1].split("=", 1)[1]
    if tok != have("PAPERLESS_GPT_TOKEN"):
        keep("PAPERLESS_GPT_TOKEN", tok)
    print("paperless-gpt service user: not a superuser; permissions " + ", ".join(GPT_PERMS))


def paperless_gpt():
    model = env.get("PAPERLESS_GPT_MODEL") or "llama3.2:3b"
    subprocess.run(["docker", "exec", "ollama", "ollama", "pull", model], check=True, stdout=subprocess.DEVNULL)
    B = f"http://{cip('paperless-webserver')}:8000/api"
    # setup (tag, workflow, bulk edits) runs as the Paperless admin; the service user's token only gets what it needs
    admin = base64.b64encode(f"{USER}:{have('PAPERLESS_ADMIN_PASSWORD')}".encode()).decode()
    H = {"Authorization": "Basic " + admin, "Accept": "application/json; version=9"}
    st, us = http("GET", B + "/users/?username__iexact=paperless-gpt", headers=H)
    if st != 200 or not us.get("results"):
        sys.exit(f"paperless-gpt user not found ({st}); run: 63-post-install.py paperless-gpt-token")
    uid = us["results"][0]["id"]
    _, tags = http("GET", B + "/tags/?page_size=500", headers=H)
    ids = {t["name"]: t["id"] for t in tags["results"]}
    if "paperless-gpt-auto" not in ids:
        _, t = http("POST", B + "/tags/", {"name": "paperless-gpt-auto", "matching_algorithm": 0}, headers=H); ids["paperless-gpt-auto"] = t["id"]
    grant = {"assign_view_users": [uid], "assign_change_users": [uid]}
    _, wfs = http("GET", B + "/workflows/?page_size=100", headers=H)
    wf = next((w for w in wfs["results"] if w["name"] == "AI filing (paperless-gpt)"), None)
    if not wf:
        http("POST", B + "/workflows/", {"name": "AI filing (paperless-gpt)", "order": 99, "enabled": True,
             "triggers": [{"type": 2, "sources": [1, 2, 3], "filter_filename": "*"}],
             "actions": [{"type": 1, "assign_tags": [ids["paperless-gpt-auto"]], **grant}]}, headers=H)
    elif wf.get("actions") and uid not in (wf["actions"][0].get("assign_change_users") or []):
        acts = [dict(wf["actions"][0], **grant)] + wf["actions"][1:]
        st, r = http("PATCH", B + f"/workflows/{wf['id']}/", {"actions": acts}, headers=H)
        if st != 200:
            print(f"warning: could not add the paperless-gpt grant to the workflow ({st}); add view+change for paperless-gpt in its action by hand")
    _, docs = http("GET", B + "/documents/?page_size=1000&fields=id", headers=H)
    ds = [d["id"] for d in docs["results"]]
    if ds:
        http("POST", B + "/documents/bulk_edit/", {"documents": ds, "method": "set_permissions", "parameters": {
             "set_permissions": {"view": {"users": [uid], "groups": []}, "change": {"users": [uid], "groups": []}}, "merge": True}}, headers=H)
        http("POST", B + "/documents/bulk_edit/", {"documents": ds, "method": "add_tag", "parameters": {"tag": ids["paperless-gpt-auto"]}}, headers=H)
    print(f"paperless-gpt: model {model} pulled, workflow in place, {len(ds)} existing documents queued (~30-60 s each)")


# ------------------------------------------------------------------ Listmonk
def listmonk_config():
    import http.cookiejar, re
    B = f"http://{cip('listmonk')}:9000"
    wait(B + "/health", 300)
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = op.open(B + "/admin/login").read().decode()          # v6: admin API needs the browser session, not basic auth
    n = re.search(r'name="nonce" value="([^"]+)"', page)
    op.open(urllib.request.Request(B + "/admin/login", data=urllib.parse.urlencode(
        {"username": USER, "password": have("LISTMONK_ADMIN_PASSWORD"), **({"nonce": n.group(1)} if n else {})}).encode()))
    s = json.load(op.open(B + "/api/settings"))["data"]
    domain = env["DOMAIN"]
    s["app.root_url"] = f"https://news.{domain}"
    s["app.from_email"] = os.environ.get("MAIL_FROM") or env.get("MAIL_FROM") or f"{env.get('BRAND_TITLE') or 'HeardOS'} <noreply@{domain}>"
    mode = os.environ.get("MAIL_MODE") or env.get("MAIL_MODE") or ""
    smtp = s["smtp"][0]
    if mode == "resend":
        smtp.update({"enabled": True, "host": "smtp.resend.com", "port": 587, "auth_protocol": "login", "username": "resend",
                     "password": os.environ.get("RESEND_API_KEY") or have("RESEND_API_KEY"), "tls_type": "STARTTLS"})
    elif mode == "smtp":
        smtp.update({"enabled": True, "host": os.environ["SMTP_HOST"], "port": int(os.environ.get("SMTP_PORT", 587)), "auth_protocol": "login",
                     "username": os.environ["SMTP_USER"], "password": os.environ["SMTP_PASSWORD"], "tls_type": "STARTTLS"})
    else:
        print("listmonk: MAIL_MODE not set — SMTP left unconfigured"); smtp["password"] = ""
    s["smtp"] = [smtp]
    cid, csec = have("AUTHENTIK_LISTMONK_SSO_CLIENT_ID"), have("AUTHENTIK_LISTMONK_SSO_CLIENT_SECRET")
    # GET masks secrets; a PUT must re-supply the real SMTP password AND OIDC client secret or the mask gets stored
    s["security.oidc"].update({"enabled": bool(cid), "provider_url": f"https://auth.{domain}/application/o/listmonk-sso/",
                               "provider_name": env.get("BRAND_TITLE") or "HeardOS", "client_id": cid, "client_secret": csec, "auto_create_users": False})
    op.open(urllib.request.Request(B + "/api/settings", data=json.dumps(s).encode(), headers={"Content-Type": "application/json"}, method="PUT"))
    time.sleep(5)
    print(f"listmonk configured: https://news.{domain} (sign in to /admin with your family account)")


# ------------------------------------------------------------------ HortusFox
def hortusfox_cron():
    pw = have("HORTUS_CRON_PW") or secrets.token_urlsafe(24)
    # the value goes in as hex (no quoting/injection possible), the SQL over stdin and the DB password as MYSQL_PWD in the
    # exec environment (`-e MYSQL_PWD` copies it from ours) — neither appears on any command line
    q = f"UPDATE AppModel SET cronjob_pw = CONVERT(UNHEX('{pw.encode().hex()}') USING utf8mb4);\n"
    subprocess.run(["docker", "exec", "-i", "-e", "MYSQL_PWD", "hortusfox-db", "mariadb", "-uhortusfox", "hortusfox"], input=q, text=True,
                   env={**os.environ, "MYSQL_PWD": have("HORTUS_DB_PASSWORD")}, check=True)
    keep("HORTUS_CRON_PW", pw)
    os.makedirs("/usr/local/sbin", exist_ok=True)
    with open("/usr/local/sbin/hortusfox-cron.sh", "w") as f:
        f.write("#!/bin/sh\n# HortusFox cron endpoints: recurring tasks reset + calendar reminders (password from secrets.env)\n"
                f"PW=$(grep '^HORTUS_CRON_PW=' {SECRETS} | cut -d= -f2-)\n"
                '# the password is posted from stdin (--data-binary @-), so it is never in a process list\n'
                'for ep in "$@"; do printf \'cronpw=%s\' "$PW" | docker exec -i hortusfox curl -fsS -m 60 -X POST --data-binary @- "http://127.0.0.1/cronjob/$ep" >/dev/null || echo "hortusfox cron $ep failed" >&2; done\n')
    os.chmod("/usr/local/sbin/hortusfox-cron.sh", 0o700)
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    lines = [l for l in cur.splitlines() if "hortusfox-cron.sh" not in l] + ["7 * * * * /usr/local/sbin/hortusfox-cron.sh tasks/recurring calendar/reminder"]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
    print("hortusfox: hourly cron installed (recurring tasks reset, calendar reminders)")


TASKS = {"ha-onboard": ha_onboard, "mealie-token": mealie_token, "nextcloud-admin": nextcloud_admin, "baikal-user": baikal_user,
         "paperless-gpt-token": paperless_gpt_token, "paperless-gpt": paperless_gpt, "listmonk-config": listmonk_config, "hortusfox-cron": hortusfox_cron}
if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        sys.exit(__doc__)
    if os.environ.get("HEARDOS_DRY_RUN"):
        print(f"[dry-run] would run post-install task {sys.argv[1]}"); sys.exit(0)
    TASKS[sys.argv[1]]()
