"""Kit helpers that run on the host but are plain Python: hub generator, Caddyfile front door, Resend records, stacks."""
import importlib.util, json, os, subprocess, sys
from pathlib import Path

import httpx, respx, yaml

from heardos_installer.kit.locate import kit_path
from heardos_installer.mail import Resend, dmarc_record, records_for

GEN = kit_path("generic")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_every_stack_is_valid_compose():
    for d in sorted((GEN / "stacks").iterdir()):
        f = d / "compose.yml"
        if f.exists():
            doc = yaml.safe_load(f.read_text())
            assert "services" in doc, d.name
            for svc in doc["services"].values():
                assert svc.get("image"), f"{d.name}: every service needs a prebuilt image (Portainer has no build context)"


def test_every_catalog_app_has_an_install_case():
    script = (GEN / "50-app.sh").read_text()
    for a in json.loads((GEN / "catalog.json").read_text())["apps"]:
        if not a.get("core"):
            assert f"\n  {a['stack']})" in script, a["stack"]


def test_homepage_config(tmp_path):
    root = tmp_path / "root"
    for s in ("immich", "librespeed"):
        (root / "stacks" / s).mkdir(parents=True); (root / "stacks" / s / "compose.yml").write_text("x")
    env = dict(os.environ, DOMAIN="smith.example", HEARDOS_ROOT=str(root), BRAND_TITLE="Smith's cloud")
    subprocess.run([sys.executable, str(GEN / "lib/homepage-config.py"), str(tmp_path / "out")], env=env, check=True, capture_output=True)
    svc = yaml.safe_load((tmp_path / "out/services.yaml").read_text())
    tiles = {k: v for g in svc for grp in g.values() for t in grp for k, v in t.items()}
    assert tiles["Photos"]["href"] == "https://cloud.smith.example/immich"
    assert tiles["Speed test"]["href"] == "https://speed.smith.example/"
    assert "Documents" not in tiles                                     # not installed, no tile
    assert yaml.safe_load((tmp_path / "out/settings.yaml").read_text())["title"] == "Smith's cloud"


def test_calibre_trusted_ips_and_hash():
    m = _load("cw", GEN / "lib/calibre-web-setup.py")
    assert m.trusted_ips("172.18.0.0/16") == "172.18.0.0/16,::ffff:172.18.0.0/112"
    assert m.pwhash("pw").startswith("pbkdf2:sha256:600000$")


def test_resend_records_and_access():
    with respx.mock(base_url="https://api.resend.com") as r:
        r.get("/domains").mock(return_value=httpx.Response(401, json={"name": "restricted_api_key", "message": "This API key is restricted"}))
        assert Resend("re_x").access() == "sending"
    with respx.mock(base_url="https://api.resend.com") as r:
        r.get("/domains").mock(return_value=httpx.Response(200, json={"data": []}))
        r.post("/domains").mock(return_value=httpx.Response(201, json={"id": "d1"}))
        r.get("/domains/d1").mock(return_value=httpx.Response(200, json={"id": "d1", "status": "not_started", "records": [
            {"type": "TXT", "name": "resend._domainkey", "value": "p=abc"},
            {"type": "MX", "name": "send", "value": "feedback-smtp.us-east-1.amazonses.com", "priority": 10}]}))
        rs = Resend("re_y"); assert rs.access() == "full"
        recs = records_for("smith.example", rs.ensure_domain("smith.example"))
    assert [(x.type, x.name, x.priority) for x in recs] == [("TXT", "resend._domainkey.smith.example", None), ("MX", "send.smith.example", 10)]
    assert dmarc_record("smith.example", "a@b.c").content.endswith("rua=mailto:a@b.c")
