"""The wizard screens. Each function draws one screen per frame and starts background tasks for anything slow."""
from __future__ import annotations

import copy
import time
from pathlib import Path

from imgui_bundle import imgui

from ..engine import InstallState, StepStatus
from ..kit import catalog
from ..vault import WrongPassphrase
from ..vault import keyring_
from .state import DOMAIN, EMAIL, IPV4, USERNAME, WIZARD, UIState
from .theme import C, ICON_BAD, ICON_OK, ICON_RUN, ICON_SKIP, ICON_TODO
from . import theme
from . import widgets as w

# rough steady-state memory per app (MB), used to warn when the home server is too small
RAM_MB = {"immich": 2500, "nextcloud": 800, "onlyoffice": 1500, "paperless": 1000, "mealie": 400, "homeassistant": 500, "homebox": 120,
          "baikal": 60, "navidrome": 150, "vaultwarden": 60, "wallos": 60, "actual": 150, "invidious": 600, "homepage": 150,
          "calibre-web": 200, "librespeed": 50, "dash": 200, "ocr": 800,
          "hortusfox": 300, "archivebox": 1000, "listmonk": 200, "paperless-gpt": 3500}
BASE_RAM_MB = 1600  # Authentik, its database, Traefik, Portainer, the OS
PROVIDERS = [("cloudzy", "Cloudzy"), ("linode", "Linode (Akamai)"), ("vultr", "Vultr"), ("ovh", "OVHcloud"), ("byo", "I already have a server")]
TOKEN_NAMES = {"cloudzy": "CLOUDZY_API_TOKEN", "linode": "LINODE_TOKEN", "vultr": "VULTR_API_KEY", "ovh": "OVH_CREDENTIALS"}
TOKEN_HELP = {
    "cloudzy": "Cloudzy dashboard > Account > API > create a token. It needs permission to manage instances and SSH keys.",
    "linode": "Linode Cloud Manager > your profile > API Tokens > Create a Personal Access Token with Read/Write on Linodes.",
    "vultr": "Vultr dashboard > Account > API > enable the API and copy the key. Allow access from this computer's IP.",
    "ovh": "Open the key page for your OVHcloud region (link below), sign in, set validity to Unlimited and add these rights: "
           "GET /me, GET /me/order/*, GET /order/*, POST /order/cart, POST /order/cart/*, GET /vps, GET /vps/*, "
           "POST /vps/*/rebuild, POST /vps/*/terminate, POST /vps/*/confirmTermination. Paste the three keys it shows. "
           "OVH bills monthly, so a server costs the full month even if removed early; set a default payment method first.",
}
OVH_FIELDS = [("app_key", "Application key"), ("app_secret", "Application secret"), ("consumer_key", "Consumer key")]


def _nav(st: UIState, can_next: bool = True, next_label: str = "Continue") -> None:
    imgui.spacing(); imgui.separator(); imgui.spacing()
    if st.screen != "welcome" and w.secondary("Back"):
        st.back()
    imgui.same_line()
    if w.primary(next_label, can_next):
        st.next()


def _issue(text: str) -> None:
    w.status_line(False, text)


# ---------------------------------------------------------------- welcome / vault
def welcome(st: UIState) -> None:
    w.title("Set up your family cloud")
    w.help_text("This app builds a private cloud for your family: photos, documents, passwords, calendars and more, on a "
                "computer in your home, reachable from anywhere through a small rented server. It takes about an hour, "
                "most of it waiting. You can close the app at any time and pick up where you left off.")
    imgui.spacing()
    v = st.vault
    if not v.exists():
        w.heading("Create your secrets vault", "Every password and API key you enter is kept in one encrypted file on this computer. "
                  "Choose a passphrase you will remember; it cannot be recovered.")
        st.inputs["pw1"] = w.field("Passphrase (12 characters or more)", st.inputs.get("pw1", ""), password=True, key="pw1")
        st.inputs["pw2"] = w.field("Type it again", st.inputs.get("pw2", ""), password=True, key="pw2")
        if keyring_.available():
            _, st.inputs["remember"] = imgui.checkbox("Remember it on this computer (system keyring)", bool(st.inputs.get("remember", True)))
        ok = len(st.inputs.get("pw1", "")) >= 12 and st.inputs.get("pw1") == st.inputs.get("pw2")
        if st.inputs.get("pw1") and not ok:
            w.help_text("The two entries must match and be at least 12 characters.")
        if w.primary("Create vault", ok):
            v.create(st.inputs["pw1"])
            if st.inputs.get("remember") and keyring_.available():
                keyring_.remember(st.inputs["pw1"])
            st.inputs.pop("pw1"); st.inputs.pop("pw2")
        return
    if not v.unlocked:
        if not st.inputs.get("_tried_keyring"):
            st.inputs["_tried_keyring"] = "1"
            pw = keyring_.recall()
            if pw:
                try:
                    v.unlock(pw)
                    return
                except WrongPassphrase:
                    pass
        w.heading("Unlock your vault")
        st.inputs["pw"] = w.field("Passphrase", st.inputs.get("pw", ""), password=True, key="pwu")
        if w.primary("Unlock", bool(st.inputs.get("pw"))):
            try:
                v.unlock(st.inputs.pop("pw"))
                st.messages.pop("unlock", None)
            except WrongPassphrase:
                st.messages["unlock"] = (False, "That passphrase is not right.")
        if "unlock" in st.messages:
            w.status_line(*st.messages["unlock"])
        return

    installs = InstallState.list_installs()
    if installs:
        w.heading("Your installs", "Open one to continue it or to manage it.")
        for iid in installs:
            s = InstallState(iid)
            done = sum(1 for x in s.data["steps"].values() if x["status"] == "done")
            total = max(len(s.data["steps"]), 1)
            if imgui.selectable(f"{s.plan.get('DOMAIN', iid)}   ·   {iid}   ·   {done}/{total} steps done##{iid}", False)[0]:
                _open_install(st, iid)
        imgui.spacing()
    w.heading("Start a new install")
    if w.primary("Start"):
        st.next()
    imgui.same_line()
    from pathlib import Path
    cred = Path.home() / ".config" / "credentials.env"
    if cred.exists() and w.secondary("Import keys from credentials.env"):
        from ..vault.migrate import import_credentials_env
        names = import_credentials_env(v, cred)
        st.messages["import"] = (True, f"imported {len(names)} value(s)" if names else "nothing new to import")
    if "import" in st.messages:
        w.status_line(*st.messages["import"])


def _open_install(st: UIState, iid: str) -> None:
    from ..session import Installation
    st.inst = Installation(iid, st.vault, prompt=st.prompt)
    st.inst.logs.ring.subscribe(st.logbuf.add)
    st.inst.listeners.append(st.on_step)
    st.step_status = {sid: (StepStatus(x["status"]), x.get("message", "")) for sid, x in st.inst.state.data["steps"].items()}
    st.draft.update({k: v for k, v in st.inst.plan.items() if not isinstance(v, (dict,))})
    all_done = st.step_status and all(s == StepStatus.done for s, _ in st.step_status.values())
    st.go("done" if all_done else "install")


def _initial(d: dict) -> str:
    from ..brand.render import initials
    return initials(d.get("BRAND_TITLE") or "H")


def _brand_sample(d: dict) -> None:
    """A button drawn in the chosen accent with the text colour the emails and sign-in page will use."""
    from ..brand.theme import derive
    try:
        pal = derive(d.get("BRAND_PRIMARY", "#f2a35e"))
    except ValueError:
        return
    imgui.push_style_color(imgui.Col_.button, theme.hex4(pal.primary))
    imgui.push_style_color(imgui.Col_.button_hovered, theme.hex4(pal.light))
    imgui.push_style_color(imgui.Col_.button_active, theme.hex4(pal.deep))
    imgui.push_style_color(imgui.Col_.text, theme.hex4(pal.on_primary))
    imgui.button(" Sign in ")
    imgui.pop_style_color(4)


# ---------------------------------------------------------------- family
def family(st: UIState) -> None:
    d = st.draft
    w.title("Your family")
    w.help_text("A name for your cloud, the web address it will live at, and the first administrator (that is you).")
    imgui.spacing()
    d["BRAND_TITLE"] = w.field("Name of your cloud", d.get("BRAND_TITLE", ""), "e.g. The Smith Family Cloud", key="brand")
    d["DOMAIN"] = w.field("Your domain", d.get("DOMAIN", ""), "example.com", key="domain").strip().lower()
    w.help_text("A domain you own. Apps get addresses like photos.example.com. If you do not have one, buy one from any "
                "registrar first (Cloudflare Registrar is the simplest with this app).")
    col = [int(d.get("BRAND_PRIMARY", "#f2a35e")[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    imgui.text("Accent colour")
    changed, col = imgui.color_edit3("##accent", col, imgui.ColorEditFlags_.no_inputs)
    if changed:
        d["BRAND_PRIMARY"] = "#%02x%02x%02x" % tuple(int(c * 255) for c in col)
    imgui.same_line()
    _brand_sample(d)
    if d.get("BRAND_PRIMARY", "#f2a35e") != "#f2a35e":
        imgui.same_line()
        if w.secondary("Use the HeardOS orange"):
            d["BRAND_PRIMARY"] = "#f2a35e"
    d["BRAND_LOGO"] = w.field("Logo (optional)", d.get("BRAND_LOGO", ""), "path to a PNG or JPG; leave empty for a monogram", width=520, key="logo").strip()
    w.help_text("Used on the sign-in page, in emails and as the app icon. A square logo works best. Without one, "
                + ("the HeardOS artwork is used." if (d.get("BRAND_TITLE") or "HeardOS") == "HeardOS"
                   else f"a '{_initial(d)}' monogram in your accent colour is drawn."))
    imgui.spacing()
    w.heading("Administrator")
    d["ADMIN_NAME"] = w.field("Your full name", d.get("ADMIN_NAME", ""), key="aname")
    d["ADMIN_USER"] = w.field("Username", d.get("ADMIN_USER", ""), "lowercase, no spaces", key="auser").strip().lower()
    d["ADMIN_EMAIL"] = w.field("Email address", d.get("ADMIN_EMAIL", ""), "you@example.net", key="aemail").strip()
    w.help_text("Password resets and sign-in codes go to this address, so use one you check.")
    issues = []
    if d["DOMAIN"] and not DOMAIN.match(d["DOMAIN"]):
        issues.append("The domain does not look right (example: example.com).")
    if d["ADMIN_USER"] and not USERNAME.match(d["ADMIN_USER"]):
        issues.append("Usernames are 2–31 lowercase letters, digits, dots, dashes or underscores, starting with a letter.")
    if d["ADMIN_USER"] in ("admin", "akadmin", "root"):
        issues.append("Pick a personal username, not admin/akadmin/root.")
    if d["ADMIN_EMAIL"] and not EMAIL.match(d["ADMIN_EMAIL"]):
        issues.append("The email address does not look right.")
    if d.get("BRAND_LOGO"):
        lp = Path(d["BRAND_LOGO"]).expanduser()
        if not lp.is_file():
            issues.append("The logo file was not found.")
        elif lp.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            issues.append("The logo must be a PNG, JPG or WebP image.")
    for i in issues:
        _issue(i)
    ok = not issues and all(d.get(k) for k in ("BRAND_TITLE", "DOMAIN", "ADMIN_NAME", "ADMIN_USER", "ADMIN_EMAIL"))
    _nav(st, ok)


# ---------------------------------------------------------------- VPS
def vps(st: UIState) -> None:
    from .. import providers
    from ..keys import DEFAULT_KEY, ensure_key
    d = st.draft
    w.title("Public server")
    w.help_text("A small rented server (about $5–12 a month) is the only part of your cloud on the open internet. It holds "
                "the web certificates and forwards traffic to your home over a private, encrypted network. Nothing is "
                "stored on it.")
    imgui.spacing()
    for key, label in PROVIDERS:
        if imgui.radio_button(label, d.get("provider") == key):
            if d.get("provider") != key:
                for k in ("vps_region", "vps_plan", "vps_monthly", "vps_region_name", "vps_plan_name"):
                    d.pop(k, None)
                st.tasks.clear("vps.check"); st.tasks.clear("vps.regions")
            d["provider"] = key
        imgui.same_line()
    imgui.new_line(); imgui.spacing()
    prov = d.get("provider", "cloudzy")

    if prov == "byo":
        _, pub = ensure_key(DEFAULT_KEY)
        w.heading("Your server", "It must run Ubuntu 24.04 with a public IPv4 address. Add this key to root's "
                  "~/.ssh/authorized_keys on it, then test the connection.")
        imgui.push_text_wrap_pos(0); imgui.text_colored(C["MUTED"], pub); imgui.pop_text_wrap_pos()
        if w.secondary("Copy key"):
            imgui.set_clipboard_text(pub)
        d["byo_ip"] = w.field("Server address (IPv4)", d.get("byo_ip", ""), "203.0.113.10", key="byoip").strip()
        if w.secondary("Test connection", IPV4.match(d["byo_ip"] or "") is not None and not st.tasks.running("vps.check")):
            st.tasks.start("vps.check", lambda: providers.make("byo", ip=d["byo_ip"], key_path=DEFAULT_KEY).validate_token().label)
        _task_status(st, "vps.check", "connecting...")
        t = st.tasks.get("vps.check")
        d["vps_monthly"] = 0
        _nav(st, bool(t and t.ok))
        return

    tok_name = TOKEN_NAMES[prov]
    have = st.vault.get(tok_name)
    if prov == "ovh":
        import json as _json
        from ..providers.ovh import CREATE_TOKEN_URL, ENDPOINTS
        w.heading("OVHcloud API keys", TOKEN_HELP[prov])
        eps = sorted(ENDPOINTS)
        cur = d.get("ovh_endpoint", "us")
        imgui.set_next_item_width(220)
        changed, i = imgui.combo("Account region", eps.index(cur), [{"us": "OVHcloud US", "ca": "OVHcloud Canada", "eu": "OVHcloud Europe"}[e] for e in eps])
        if changed:
            d["ovh_endpoint"] = cur = eps[i]
        imgui.text_colored(C["MUTED"], CREATE_TOKEN_URL[cur])
        imgui.same_line()
        if w.secondary("Copy link"):
            imgui.set_clipboard_text(CREATE_TOKEN_URL[cur])
        vals = {}
        for k, label in OVH_FIELDS:
            st.inputs[f"ovh_{k}"] = w.field(label, st.inputs.get(f"ovh_{k}", ""), "already saved" if have else "paste it here",
                                            password=k != "app_key", key=f"ovh_{k}")
            vals[k] = st.inputs[f"ovh_{k}"].strip()
        typed = all(vals.values())
        token = _json.dumps({"endpoint": cur, **vals}) if typed else (have or "")
        if typed:
            st.inputs[tok_name] = token
    else:
        w.heading(f"{dict(PROVIDERS)[prov]} API token", TOKEN_HELP[prov])
        st.inputs[tok_name] = w.field("API token", st.inputs.get(tok_name, ""), "already saved" if have else "paste it here",
                                      password=True, key=tok_name)
        token = st.inputs.get(tok_name) or have or ""
    if w.secondary("Check token", bool(token) and not st.tasks.running("vps.check")):
        def check():
            acct = providers.make(prov, token).validate_token()
            st.vault.set(tok_name, token)
            st.inputs.pop(tok_name, None)
            for k, _ in OVH_FIELDS:
                st.inputs.pop(f"ovh_{k}", None)
            return acct.label
        st.tasks.start("vps.check", check)
        st.tasks.clear("vps.regions")
    _task_status(st, "vps.check", "checking with the provider...")
    t = st.tasks.get("vps.check")
    if not (t and t.ok):
        if have and not t:
            w.help_text("A token is already saved; press Check token to load locations and prices.")
        _nav(st, False)
        return

    if not st.tasks.get("vps.regions"):
        def load():
            from ..providers import smart_default
            p = providers.make(prov, st.vault.get(tok_name))
            regions = [r for r in p.regions() if r.available]
            plans_cache: dict = {}
            def plans_for(rid):
                if rid not in plans_cache:
                    plans_cache[rid] = sorted([x for x in p.plans(rid) if x.eligible and x.monthly_usd > 0], key=lambda x: x.monthly_usd)
                return plans_cache[rid]
            choice = smart_default(regions, plans_for, tz=d.get("TZ"))
            return {"regions": regions, "plans": plans_cache, "choice": choice, "plans_for": plans_for}
        st.tasks.start("vps.regions", load)
    rt = st.tasks.get("vps.regions")
    if rt.running:
        w.spinner_text("finding the nearest location and the best price...")
        _nav(st, False)
        return
    if rt.error:
        w.status_line(False, rt.error)
        _nav(st, False)
        return
    data = rt.result
    regions = data["regions"]
    if not d.get("vps_region") and data["choice"]:
        ch = data["choice"]
        d.update(vps_region=ch.region.id, vps_region_name=ch.region.name, vps_plan=ch.plan.id, vps_plan_name=ch.plan.name,
                 vps_monthly=ch.plan.monthly_usd)
    w.heading("Location and size", "We picked the closest location and the cheapest size that runs the private network "
              "hub and web entrance comfortably. Change them if you like.")
    names = [r.name for r in regions]
    idx = next((i for i, r in enumerate(regions) if r.id == d.get("vps_region")), 0)
    imgui.set_next_item_width(320)
    changed, idx = imgui.combo("Location", idx, names)
    if changed:
        r = regions[idx]
        d.update(vps_region=r.id, vps_region_name=r.name)
        d.pop("vps_plan", None)
        st.tasks.start("vps.plans", lambda: data["plans_for"](r.id))
    pt = st.tasks.get("vps.plans")
    plans = data["plans"].get(d.get("vps_region")) if not (pt and pt.running) else None
    if pt and pt.running:
        w.spinner_text("loading sizes...")
    elif plans is not None:
        if plans and not d.get("vps_plan"):
            d.update(vps_plan=plans[0].id, vps_plan_name=plans[0].name, vps_monthly=plans[0].monthly_usd)
        if not plans:
            _issue("No suitable size is in stock at this location; pick another.")
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.sizing_stretch_prop
        if plans and imgui.begin_table("plans", 4, flags):
            for h in ("Size", "Memory", "Disk", "Per month"):
                imgui.table_setup_column(h)
            imgui.table_headers_row()
            for pl in plans[:8]:
                imgui.table_next_row(); imgui.table_next_column()
                sel = pl.id == d.get("vps_plan")
                if imgui.selectable(f"{pl.vcpu} vCPU##{pl.id}", sel, imgui.SelectableFlags_.span_all_columns)[0]:
                    d.update(vps_plan=pl.id, vps_plan_name=pl.name, vps_monthly=pl.monthly_usd)
                imgui.table_next_column(); imgui.text(f"{pl.ram_mb // 1024} GB" if pl.ram_mb >= 1024 else f"{pl.ram_mb} MB")
                imgui.table_next_column(); imgui.text(f"{pl.disk_gb} GB")
                imgui.table_next_column(); imgui.text(f"${pl.monthly_usd:.2f}")
            imgui.end_table()
    if d.get("vps_plan"):
        imgui.spacing()
        w.chip(f"{d.get('vps_region_name')}  ·  {d.get('vps_plan_name')}  ·  about ${d.get('vps_monthly', 0):.2f} / month" + ("  ·  billed monthly" if prov == "ovh" else ""), "SLATE", "LIGHT")
    _nav(st, bool(d.get("vps_plan")))


def _task_status(st: UIState, name: str, running_text: str) -> None:
    t = st.tasks.get(name)
    if not t:
        return
    if t.running:
        w.spinner_text(running_text)
    elif t.error:
        w.status_line(False, t.error)
    else:
        w.status_line(True, str(t.result))


# ---------------------------------------------------------------- home server
def home(st: UIState) -> None:
    from ..keys import DEFAULT_KEY
    from ..remote.ssh import Session, SshError
    d = st.draft
    w.title("Home server")
    w.help_text("The computer at home that will run the apps and keep the data: a mini PC, an old desktop, or a virtual "
                "machine. It needs a fresh Ubuntu 24.04 install, a fixed address on your home network, and enough disk "
                "for your photos. Everything else on it may be changed, so do not use a computer you use for other things.")
    imgui.spacing()
    d["HOST_LAN_IP"] = w.field("Its address on your home network", d.get("HOST_LAN_IP", ""), "192.168.1.50", key="hip").strip()
    w.help_text("Give it a reserved (static) address in your router so it never changes.")
    d["HOST_SSH_USER"] = w.field("Login name", d.get("HOST_SSH_USER", "root"), "root", width=220, key="huser").strip() or "root"
    has_pw = bool(st.vault.get("HOST_SSH_PASSWORD"))
    st.inputs["HOST_SSH_PASSWORD"] = w.field("Login password (only used once, to install this app's key)", st.inputs.get("HOST_SSH_PASSWORD", ""),
                                             "already saved" if has_pw else "", password=True, key="hpw")
    ok_ip = bool(IPV4.match(d["HOST_LAN_IP"] or ""))
    if w.secondary("Test connection", ok_ip and not st.tasks.running("home.test")):
        pw = st.inputs.get("HOST_SSH_PASSWORD") or st.vault.get("HOST_SSH_PASSWORD")
        if st.inputs.get("HOST_SSH_PASSWORD"):
            st.vault.set("HOST_SSH_PASSWORD", st.inputs.pop("HOST_SSH_PASSWORD"))
        def test():
            try:
                s = Session(d["HOST_LAN_IP"], d["HOST_SSH_USER"], key_path=DEFAULT_KEY if DEFAULT_KEY.exists() else None,
                            password=None if DEFAULT_KEY.exists() else pw, connect_timeout=10).connect()
            except SshError:
                if not pw:
                    raise
                s = Session(d["HOST_LAN_IP"], d["HOST_SSH_USER"], password=pw, connect_timeout=10).connect()
            out = s.run(". /etc/os-release; echo \"$PRETTY_NAME\"; nproc; free -m | awk '/Mem:/{print $2}'; "
                        "df -BG --output=avail / | tail -1 | tr -dc 0-9; echo; test -e /dev/net/tun && echo tun || echo notun; "
                        "[ \"$(id -u)\" = 0 ] && echo root || (sudo -n true 2>/dev/null && echo sudo || echo nosudo)", timeout=30).stdout.splitlines()
            s.close()
            return {"os": out[0], "cpus": int(out[1]), "mem": int(out[2]), "disk": int(out[3] or 0), "tun": out[4] == "tun", "priv": out[5]}
        st.tasks.start("home.test", test)
    t = st.tasks.get("home.test")
    problems = []
    if t and t.running:
        w.spinner_text("connecting...")
    elif t and t.error:
        w.status_line(False, f"Could not connect: {t.error}")
    elif t and t.ok:
        f = t.result
        need_mb = BASE_RAM_MB + sum(RAM_MB.get(a, 200) for a in catalog.closure(_selected_apps(d)))
        w.status_line(True, f"{f['os']}, {f['cpus']} CPU, {f['mem'] // 1024} GB memory, {f['disk']} GB free disk")
        if "24.04" not in f["os"]:
            problems.append(f"It runs {f['os']}; HeardOS needs Ubuntu 24.04.")
        if not f["tun"]:
            problems.append("The tunnel device /dev/net/tun is missing (for a Proxmox container, enable TUN in its options).")
        if f["priv"] == "nosudo":
            problems.append("This login cannot use sudo without a password. Use root, or allow passwordless sudo.")
        if f["mem"] < need_mb:
            w.status_line(False, f"The apps you picked need about {need_mb // 1024 + 1} GB of memory; this server has {f['mem'] // 1024} GB. "
                                 "It may work but will be slow. Pick fewer apps or add memory.")
        if f["disk"] < 50:
            w.status_line(False, f"Only {f['disk']} GB free; photos and documents will need much more.")
    for p in problems:
        _issue(p)
    _nav(st, bool(t and t.ok and not problems))


def _selected_apps(d: dict) -> list[str]:
    if d.get("profile") and d["profile"] != "custom":
        return catalog.profile_apps(d["profile"])
    return list(d.get("apps") or [])


# ---------------------------------------------------------------- DNS
def dns(st: UIState) -> None:
    from ..dns import CloudflareDNS
    d = st.draft
    w.title("Domain")
    w.help_text(f"Each app gets an address under {d.get('DOMAIN') or 'your domain'}. Those addresses must point at the "
                "public server. With a Cloudflare token we set them up for you; otherwise we show you exactly what to "
                "create at your domain provider and wait until it works.")
    imgui.spacing()
    if imgui.radio_button("My domain uses Cloudflare (recommended)", d.get("dns_mode") == "cloudflare"):
        d["dns_mode"] = "cloudflare"
    if imgui.radio_button("I will create the records myself", d.get("dns_mode") == "manual"):
        d["dns_mode"] = "manual"
    imgui.spacing()
    if d.get("dns_mode") == "manual":
        w.help_text("During the install you will see a list of records to add. The install pauses until the internet can see them, "
                    "usually a few minutes.")
        _nav(st, True)
        return
    have = st.vault.get("CF_API_TOKEN")
    w.heading("Cloudflare API token", "Cloudflare dashboard > My Profile > API Tokens > Create Token > use the 'Edit zone DNS' "
              f"template, limited to the zone {d.get('DOMAIN') or 'for your domain'}.")
    st.inputs["CF_API_TOKEN"] = w.field("API token", st.inputs.get("CF_API_TOKEN", ""), "already saved" if have else "", password=True, key="cft")
    token = st.inputs.get("CF_API_TOKEN") or have or ""
    if w.secondary("Check token", bool(token) and not st.tasks.running("dns.check")):
        def check():
            cf = CloudflareDNS(token, domain=d["DOMAIN"])
            msg = cf.validate()
            st.vault.set("CF_API_TOKEN", token); st.inputs.pop("CF_API_TOKEN", None)
            d["CF_ZONE_ID"] = cf.zone_id
            return msg
        st.tasks.start("dns.check", check)
    _task_status(st, "dns.check", "checking with Cloudflare...")
    t = st.tasks.get("dns.check")
    _nav(st, bool(t and t.ok))


# ---------------------------------------------------------------- apps
def apps(st: UIState) -> None:
    d = st.draft
    w.title("Apps")
    w.help_text("Pick a starting set. You can add more apps later from the Apps tab.")
    imgui.spacing()
    profs = catalog.profiles()
    for key in ("essentials", "family", "everything"):
        if imgui.radio_button(f"{profs[key]['name']}##p{key}", d.get("profile") == key):
            d["profile"] = key
            d["apps"] = catalog.profile_apps(key)
        imgui.same_line(); imgui.text_colored(C["MUTED"], profs[key]["blurb"])
    if imgui.radio_button("Let me choose##pcustom", d.get("profile") == "custom"):
        if d.get("profile") != "custom":
            d["apps"] = _selected_apps(d)
        d["profile"] = "custom"
    imgui.spacing()
    selected = set(_selected_apps(d))
    full = catalog.closure(sorted(selected))
    custom = d.get("profile") == "custom"
    if imgui.begin_table("apps", 3, imgui.TableFlags_.row_bg | imgui.TableFlags_.sizing_stretch_prop):
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, 32)
        imgui.table_setup_column("App", imgui.TableColumnFlags_.width_fixed, 190)
        imgui.table_setup_column("What it does")
        for a in catalog.apps().values():
            if a.core or (not a.profiles and a.stack not in selected):
                continue
            imgui.table_next_row(); imgui.table_next_column()
            on = a.stack in full and a.available
            dep_only = on and a.stack not in selected
            if not a.available or not custom:
                imgui.begin_disabled()
            changed, val = imgui.checkbox(f"##{a.stack}", on)
            if not a.available or not custom:
                imgui.end_disabled()
            if changed and custom:
                apps_ = set(d.get("apps") or [])
                (apps_.add if val else apps_.discard)(a.stack)
                d["apps"] = sorted(apps_)
            imgui.table_next_column()
            imgui.text(a.name) if a.available else imgui.text_colored(C["MUTED"], a.name)
            imgui.table_next_column()
            note = "  (coming soon)" if not a.available else ("  (needed by another app)" if dep_only else "")
            imgui.push_text_wrap_pos(0); imgui.text_colored(C["MUTED"], a.blurb + note); imgui.pop_text_wrap_pos()
        imgui.end_table()
    imgui.spacing()
    _, d["CADDY_FRONT_DOOR"] = imgui.checkbox(f"Also give everything one easy address: cloud.{d.get('DOMAIN') or 'example.com'}/photos, /recipes, ...",
                                              bool(d.get("CADDY_FRONT_DOOR", True)))
    _, d["CROWDSEC"] = imgui.checkbox("Block known attackers at the front door (CrowdSec, free)", bool(d.get("CROWDSEC", True)))
    if d.get("CROWDSEC", True):
        have_key = st.vault.get("CROWDSEC_ENROLL_KEY")
        st.inputs["CROWDSEC_ENROLL_KEY"] = w.field("Console enrolment key (optional)", st.inputs.get("CROWDSEC_ENROLL_KEY", ""),
                                                   "already saved" if have_key else "from app.crowdsec.net > Security Engines > Add",
                                                   password=True, key="csk")
        if st.inputs.get("CROWDSEC_ENROLL_KEY"):
            st.vault.set("CROWDSEC_ENROLL_KEY", st.inputs.pop("CROWDSEC_ENROLL_KEY"))
    need_mb = BASE_RAM_MB + sum(RAM_MB.get(a, 200) for a in full)
    imgui.spacing()
    w.chip(f"{len([a for a in full if catalog.apps()[a].available])} apps  ·  needs about {need_mb // 1024 + 1} GB of memory on the home server")
    _nav(st, any(catalog.apps()[a].available for a in full))


# ---------------------------------------------------------------- mail
def mail(st: UIState) -> None:
    d = st.draft
    w.title("Email")
    w.help_text("Your cloud sends a few emails: password resets, sign-in codes, invitations for family members. "
                "It needs a sending service; Resend has a free tier that is plenty for a family.")
    imgui.spacing()
    for key, label in (("resend", "Resend (recommended)"), ("smtp", "Another email provider (SMTP)"), ("none", "Not now")):
        if imgui.radio_button(label, d.get("mail_mode") == key):
            d["mail_mode"] = key
    imgui.spacing()
    ok = True
    if d.get("mail_mode") == "resend":
        have = st.vault.get("RESEND_API_KEY")
        w.heading("Resend API key", f"resend.com > API Keys > Create. A 'Sending access' key is enough; a 'Full access' key also lets "
                  f"us add {d.get('DOMAIN') or 'your domain'} to Resend and publish its DNS records for you.")
        st.inputs["RESEND_API_KEY"] = w.field("API key", st.inputs.get("RESEND_API_KEY", ""), "already saved" if have else "re_...",
                                              password=True, key="rk")
        if st.inputs.get("RESEND_API_KEY"):
            st.vault.set("RESEND_API_KEY", st.inputs.pop("RESEND_API_KEY"))
        ok = bool(st.vault.get("RESEND_API_KEY"))
    elif d.get("mail_mode") == "smtp":
        d["SMTP_HOST"] = w.field("Server", d.get("SMTP_HOST", ""), "smtp.example.net", key="sh")
        d["SMTP_PORT"] = w.field("Port", str(d.get("SMTP_PORT", "587")), "587", width=120, key="sp")
        d["SMTP_USER"] = w.field("Username", d.get("SMTP_USER", ""), key="su")
        st.inputs["SMTP_PASSWORD"] = w.field("Password", st.inputs.get("SMTP_PASSWORD", ""),
                                             "already saved" if st.vault.get("SMTP_PASSWORD") else "", password=True, key="spw")
        if st.inputs.get("SMTP_PASSWORD"):
            st.vault.set("SMTP_PASSWORD", st.inputs.pop("SMTP_PASSWORD"))
        ok = all(d.get(k) for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER")) and bool(st.vault.get("SMTP_PASSWORD"))
    if d.get("mail_mode") != "none":
        d["MAIL_FROM"] = w.field("Send as", d.get("MAIL_FROM") or (f"{d.get('BRAND_TITLE', 'HeardOS')} <noreply@{d.get('DOMAIN', 'example.com')}>"),
                                 key="mf")
    else:
        w.help_text("Without email, forgotten passwords must be reset by you in the admin screens.")
    _nav(st, ok)


# ---------------------------------------------------------------- review
def review(st: UIState) -> None:
    from ..steps import apply_defaults, missing_inputs
    d = st.draft
    w.title("Review")
    plan = apply_defaults(copy.deepcopy({k: v for k, v in d.items() if v not in (None, "")}))
    need = missing_inputs(plan, st.vault)
    rows = [("Cloud", f"{d.get('BRAND_TITLE')}  ·  {d.get('DOMAIN')}"),
            ("Administrator", f"{d.get('ADMIN_NAME')} ({d.get('ADMIN_USER')}, {d.get('ADMIN_EMAIL')})"),
            ("Public server", "your own server " + d.get("byo_ip", "") if d.get("provider") == "byo" else
             f"{dict(PROVIDERS)[d['provider']]}, {d.get('vps_region_name', '?')}, {d.get('vps_plan_name', '?')}"),
            ("Home server", f"{d.get('HOST_SSH_USER')}@{d.get('HOST_LAN_IP')}"),
            ("Domain records", "created automatically (Cloudflare)" if d.get("dns_mode") == "cloudflare" else "you create them (instructions follow)"),
            ("Apps", ", ".join(catalog.apps()[a].name for a in plan["apps"] if catalog.apps()[a].available)),
            ("Web addresses", ", ".join(f"{h}.{d.get('DOMAIN')}" for h in plan["PUBLIC_HOSTS"])),
            ("Email", {"resend": "Resend", "smtp": f"SMTP via {d.get('SMTP_HOST')}", "none": "not set up"}[d.get("mail_mode", "none")])]
    if imgui.begin_table("review", 2, imgui.TableFlags_.row_bg | imgui.TableFlags_.sizing_stretch_prop):
        imgui.table_setup_column("k", imgui.TableColumnFlags_.width_fixed, 150); imgui.table_setup_column("v")
        for k, v in rows:
            imgui.table_next_row(); imgui.table_next_column(); imgui.text_colored(C["MUTED"], k)
            imgui.table_next_column(); imgui.push_text_wrap_pos(0); imgui.text(v); imgui.pop_text_wrap_pos()
        imgui.end_table()
    imgui.spacing()
    for n in need:
        _issue(f"Still needed: {n}")
    monthly = float(d.get("vps_monthly") or 0)
    if d.get("provider") != "byo":
        w.heading("Cost", "The public server is billed by the hour by your provider and keeps running until you delete it "
                  "(Settings, Delete public server).")
        _, st.cost_ack = imgui.checkbox(f"I understand this creates a server costing about ${monthly:.2f} per month", st.cost_ack)
    else:
        st.cost_ack = True
    w.help_text("Installing takes 30–60 minutes. Keep this computer awake and on your home network until it finishes.")
    imgui.spacing(); imgui.separator(); imgui.spacing()
    if w.secondary("Back"):
        st.back()
    imgui.same_line()
    if w.primary("Install", not need and st.cost_ack):
        _start_install(st, plan)


def _start_install(st: UIState, plan: dict) -> None:
    from ..session import Installation, new_install_id
    iid = st.draft.get("install_id") or new_install_id(plan["DOMAIN"])
    st.draft["install_id"] = iid
    plan["install_id"] = iid
    st.inst = Installation(iid, st.vault, plan, prompt=st.prompt)
    st.inst.logs.ring.subscribe(st.logbuf.add)
    st.inst.listeners.append(st.on_step)
    st.install_done = None
    st.inst.run_async(dry_run=getattr(st, "demo_dry_run", False), done=lambda ok: setattr(st, "install_done", ok))
    st.go("install")


# ---------------------------------------------------------------- install
ICON = {StepStatus.done: (ICON_OK, "GOOD"), StepStatus.failed: (ICON_BAD, "BAD"), StepStatus.running: (ICON_RUN, "ORANGE"),
        StepStatus.skipped: (ICON_SKIP, "MUTED"), StepStatus.pending: (ICON_TODO, "MUTED")}


def install(st: UIState) -> None:
    inst = st.inst
    if inst is None:
        st.go("review")
        return
    steps = inst.steps()
    done = sum(1 for s in steps if st.step_status.get(s.id, (StepStatus.pending,))[0] == StepStatus.done)
    w.title("Installing" if inst.running else ("Install finished" if st.install_done else "Install paused"))
    imgui.progress_bar(done / max(len(steps), 1), imgui.ImVec2(-1, 0), f"{done} of {len(steps)} steps")
    imgui.spacing()
    avail = imgui.get_content_region_avail()
    imgui.begin_child("steps", imgui.ImVec2(avail.x * 0.42, avail.y - 64))
    for s in steps:
        status, msg = st.step_status.get(s.id, (StepStatus.pending, ""))
        icon, col = ICON.get(status, (ICON_TODO, "MUTED"))
        imgui.text_colored(C[col], icon); imgui.same_line()
        if imgui.selectable(f"{s.title}##{s.id}", st.selected_step == s.id)[0]:
            st.selected_step = s.id
        if msg and status in (StepStatus.failed, StepStatus.done) and status == StepStatus.failed:
            imgui.indent(24); imgui.push_text_wrap_pos(0); imgui.text_colored(C["BAD"], msg); imgui.pop_text_wrap_pos(); imgui.unindent(24)
    imgui.end_child()
    imgui.same_line()
    imgui.begin_child("log", imgui.ImVec2(0, avail.y - 64))
    lines = st.logbuf.snapshot(st.selected_step)
    if not lines and st.selected_step and inst.log_path(st.selected_step).exists():
        lines = [(st.selected_step, l) for l in inst.log_path(st.selected_step).read_text(errors="replace").splitlines()[-400:]]
    for name, line in lines[-600:]:
        if st.selected_step is None:
            x0 = imgui.get_cursor_pos_x()
            imgui.text_colored(C["MUTED"], name); imgui.same_line(); imgui.set_cursor_pos_x(x0 + 150)
        imgui.push_text_wrap_pos(0); imgui.text_unformatted(line); imgui.pop_text_wrap_pos()
    if inst.running and imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 40:
        imgui.set_scroll_here_y(1.0)
    imgui.end_child()
    imgui.spacing()
    failed = [sid for sid, (s, _) in st.step_status.items() if s == StepStatus.failed]
    if inst.running:
        if w.secondary("Stop after the current step"):
            inst.cancel()
    else:
        if failed and w.primary(f"Try again from: {next(s.title for s in steps if s.id == failed[0])}"):
            inst.retry(failed[0])
            for sid in list(st.step_status):
                if st.step_status[sid][0] in (StepStatus.failed, StepStatus.skipped):
                    st.step_status.pop(sid)
            st.install_done = None
            inst.run_async(done=lambda ok: setattr(st, "install_done", ok))
        elif not failed and st.install_done is None and w.primary("Continue install"):
            inst.run_async(done=lambda ok: setattr(st, "install_done", ok))
        imgui.same_line()
        if st.install_done and w.primary("Finish"):
            st.go("done")
        imgui.same_line()
        if w.secondary("Show all logs" if st.selected_step else "Back to start"):
            if st.selected_step:
                st.selected_step = None
            else:
                st.go("welcome")
    _prompt_modal(st)


def _prompt_modal(st: UIState) -> None:
    p = st.prompt.pending
    if not p:
        return
    imgui.open_popup("Action needed")
    vp = imgui.get_main_viewport()
    imgui.set_next_window_pos(vp.get_center(), imgui.Cond_.always, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size(imgui.ImVec2(min(760, vp.size.x - 80), 0))
    if imgui.begin_popup_modal("Action needed", None, imgui.WindowFlags_.no_resize | imgui.WindowFlags_.always_auto_resize)[0]:
        w.heading(p.title)
        imgui.push_text_wrap_pos(0); imgui.text_unformatted(p.instructions); imgui.pop_text_wrap_pos()
        if w.secondary("Copy"):
            imgui.set_clipboard_text(p.instructions)
        imgui.spacing()
        ago = int(time.time() - p.last_check) if p.last_check else None
        w.spinner_text(f"Checking every {st.prompt.interval} seconds" + (f"; last check {ago}s ago: not yet" if ago is not None else ""))
        if w.primary("I've done it, check now"):
            p.confirmed.set()
        imgui.end_popup()


# ---------------------------------------------------------------- done
def done(st: UIState) -> None:
    d = st.draft
    inst = st.inst
    plan = inst.plan if inst else d
    w.title("Your cloud is ready")
    dom = plan.get("DOMAIN")
    w.help_text("Sign in once with the account below; every app uses the same sign-in. Set up a passkey on your phone the "
                "first time you log in, so you never need the password again.")
    imgui.spacing()
    w.heading("Sign in")
    imgui.text(f"Address:   https://auth.{dom}")
    imgui.text(f"Username:  {plan.get('ADMIN_USER')}")
    pw = st.vault.get("ADMIN_PASSWORD")
    if pw and not plan.get("admin_pw_acknowledged"):
        if st.inputs.get("show_pw"):
            imgui.text("Password:  "); imgui.same_line(); imgui.text_colored(C["ORANGE"], pw)
        else:
            imgui.text("Password:  ••••••••••••")
        if w.secondary("Show" if not st.inputs.get("show_pw") else "Hide"):
            st.inputs["show_pw"] = "" if st.inputs.get("show_pw") else "1"
        imgui.same_line()
        if w.secondary("Copy password"):
            imgui.set_clipboard_text(pw)
        imgui.same_line()
        if w.primary("I've saved it"):
            st.inputs.pop("show_pw", None)
            if inst:
                inst.state.set_plan(admin_pw_acknowledged=True)
        w.help_text("This is the only time the password is shown here. Store it in a password manager.")
    else:
        imgui.text_colored(C["MUTED"], "Password:  shown once at the end of the install (it stays in your vault)")
    imgui.spacing()
    w.heading("Your apps")
    for a in plan.get("apps", []):
        app = catalog.apps().get(a)
        if app and app.hosts and app.available:
            imgui.text(f"{app.name:24}  https://{app.hosts[0]}.{dom}")
    if plan.get("CADDY_FRONT_DOOR"):
        imgui.spacing(); imgui.text_colored(C["MUTED"], f"Or open https://cloud.{dom}/ and pick from the list.")
    imgui.spacing()
    if w.primary("Manage my cloud"):
        st.mode = "day2"; st.screen = "dashboard"


SCREENS = {"welcome": welcome, "family": family, "vps": vps, "home": home, "dns": dns, "apps": apps, "mail": mail,
           "review": review, "install": install, "done": done}
