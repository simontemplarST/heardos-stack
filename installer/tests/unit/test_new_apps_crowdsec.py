"""2026-10-17 kit additions: plants/newsletters/saved pages/AI filing, CrowdSec at the VPS, Caddy access log."""
import importlib.util
from pathlib import Path

from heardos_installer.kit import catalog
from heardos_installer.steps import build_steps, apply_defaults
from heardos_installer.engine import topo_layers

PLAN = {"DOMAIN": "example.com", "ADMIN_USER": "alice", "ADMIN_EMAIL": "a@example.com", "HOST_LAN_IP": "192.168.10.50",
        "provider": "cloudzy", "TZ": "America/Chicago", "apps": ["hortusfox", "archivebox", "listmonk", "paperless-gpt"]}


def test_new_apps_in_catalog():
    cat = catalog.apps()
    for s in ("hortusfox", "archivebox", "listmonk", "paperless-gpt"):
        assert cat[s].available, s
    assert catalog.closure(["paperless-gpt"]) == ["paperless", "paperless-gpt"]      # AI filing needs Paperless
    hosts = catalog.public_hosts(["hortusfox", "archivebox", "listmonk", "paperless-gpt"])
    assert {"plants", "archive", "news"} <= set(hosts) and "paperless" in hosts
    assert cat["listmonk"].post_install == ["listmonk-config"] and cat["hortusfox"].post_install == ["hortusfox-cron"]


def test_new_app_steps_wiring():
    p = apply_defaults(dict(PLAN))
    steps = {s.id: s for s in build_steps(p)}
    pos = {s: i for i, l in enumerate(topo_layers(list(steps.values()))) for s in (x.id for x in l)}
    assert pos["app.paperless"] < pos["app.paperless-gpt"] < pos["post.paperless-gpt"]
    lm = steps["post.listmonk-config"]
    assert "mail.configure" in lm.deps and {"RESEND_API_KEY", "SMTP_PASSWORD"} <= set(lm.inputs)   # sends mail like Authentik
    assert "RESEND_API_KEY" not in steps["post.hortusfox-cron"].inputs                          # least privilege elsewhere
    assert pos["post.listmonk-config"] < pos["verify"]


def test_crowdsec_step():
    p = apply_defaults(dict(PLAN))
    assert p["CROWDSEC"] is True
    steps = {s.id: s for s in build_steps(p)}
    cs = steps["vps.crowdsec"]
    assert cs.optional and "vps.caddy" in cs.deps and cs.inputs == ["CROWDSEC_ENROLL_KEY"]


def test_caddyfile_has_access_log():
    root = Path(catalog.__file__).resolve()
    while not (root / "generic" / "lib" / "caddyfile.py").exists():
        root = root.parent
    spec = importlib.util.spec_from_file_location("cf", root / "generic" / "lib" / "caddyfile.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    out = mod.render({"DOMAIN": "example.com", "ACME_EMAIL": "a@example.com", "HOST_OVERLAY": "10.42.0.11",
                      "PUBLIC_HOSTS": "auth plants news", "CADDY_FRONT_DOOR": "1"})
    assert "(accesslog) {" in out and out.count("import accesslog") == 2   # main site + front door
    assert "plants.example.com" in out and '(?:hortusfox|plants)' in out
