#!/usr/bin/env python3
"""calibre-web-setup.py — first-run configuration of Calibre-Web, done in its own app.db (no web forms, no CSRF).

Run ON THE HOST after the container's first start (50-app.sh calibre-web does this). Idempotent:
  * creates the Calibre library in /books (calibredb inside the container; needs the universal-calibre mod to finish)
  * library path, uploads on, converter binaries in /usr/bin
  * header login: X-authentik-username from Authentik forward auth, trusted only from Traefik's fixed address
    TRAEFIK_PROXY_IP (/32, also in its IPv4-mapped IPv6 form; the server is dual-stack and sees Traefik as ::ffff:172.x)
  * the local break-glass 'admin' gets the password CALIBREWEB_ADMIN_PASSWORD (default admin/admin123 is removed)
  * a Calibre-Web user for ADMIN_USER with every role (users must pre-exist for header login; family members are added
    day-two with the same 'add' subcommand:  calibre-web-setup.py add USER EMAIL [--admin])
Env: HEARDOS_ROOT, TRAEFIK_PROXY_IP, ADMIN_USER, ADMIN_EMAIL, CALIBREWEB_ADMIN_PASSWORD.
"""
import hashlib, ipaddress, os, secrets, sqlite3, subprocess, sys, time
from pathlib import Path

DB = Path(os.environ.get("HEARDOS_ROOT", "/opt/heardos")) / "data/calibre-web/app.db"
C = "calibre-web"
ROLE_ALL, ROLE_USER = 1 | 2 | 4 | 8 | 16 | 64 | 128 | 256, 2 | 4 | 16 | 64 | 256   # admin+download+upload+edit+passwd+shelf+delete+viewer; family: no admin/edit/delete


def pwhash(pw: str) -> str:  # werkzeug's pbkdf2 format, which Calibre-Web's check_password_hash accepts
    salt, it = secrets.token_urlsafe(12)[:16], 600000
    return f"pbkdf2:sha256:{it}${salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), it).hex()}"


def wait(pred, what, secs=600):
    t = time.time()
    while not pred():
        if time.time() - t > secs:
            sys.exit(f"timed out waiting for {what}")
        time.sleep(5)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def add_user(db, name, email, admin=False):
    if db.execute("select 1 from user where name=?", (name,)).fetchone():
        return False
    cols = [r[1] for r in db.execute("pragma table_info(user)") if r[1] not in ("id", "name", "email", "role", "password", "kindle_mail")]
    db.execute(f"insert into user (name,email,role,password,kindle_mail,{','.join(cols)}) select ?,?,?,?,'',{','.join(cols)} from user where name='admin'",
               (name, email, ROLE_ALL if admin else ROLE_USER, pwhash(secrets.token_urlsafe(24))))  # random password: header login needs none
    return True


def trusted_ips(subnet: str) -> str:
    n = ipaddress.ip_network(subnet, strict=False)
    return f"{n},::ffff:{n.network_address}/{96 + n.prefixlen}"


def setup():
    wait(DB.exists, "Calibre-Web to create app.db")
    wait(lambda: sh("docker", "exec", C, "sh", "-c", "command -v calibredb").returncode == 0, "the Calibre tools (universal-calibre mod)", 900)
    if sh("docker", "exec", C, "test", "-f", "/books/metadata.db").returncode:
        r = sh("docker", "exec", "-u", "abc", C, "calibredb", "--with-library", "/books", "list")
        print("library created in /books" if r.returncode == 0 else f"calibredb: {r.stderr.strip()[:300]}")
    db = sqlite3.connect(DB)
    db.execute("""update settings set config_calibre_dir='/books', config_uploading=1, config_binariesdir='/usr/bin', config_kepubifypath='',
                  config_allow_reverse_proxy_header_login=1, config_reverse_proxy_login_header_name='X-authentik-username',
                  config_reverse_proxy_trusted_ips=?, config_public_reg=0, config_anonbrowse=0""", (trusted_ips(os.environ["TRAEFIK_PROXY_IP"] + "/32"),))
    db.execute("update user set password=? where name='admin'", (pwhash(os.environ["CALIBREWEB_ADMIN_PASSWORD"]),))
    made = add_user(db, os.environ["ADMIN_USER"], os.environ["ADMIN_EMAIL"], admin=True)
    db.commit(); db.close()
    sh("docker", "restart", C)
    print(f"calibre-web configured (header login on; user {os.environ['ADMIN_USER']} {'created' if made else 'exists'})")


if __name__ == "__main__":
    if sys.argv[1:2] == ["add"]:
        db = sqlite3.connect(DB); ok = add_user(db, sys.argv[2], sys.argv[3], "--admin" in sys.argv); db.commit()
        print(("created " if ok else "exists ") + sys.argv[2])
    else:
        setup()
