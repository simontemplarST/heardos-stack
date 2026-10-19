"""The edge lets only the HeardOS desktop (and Nextcloud, for OnlyOffice) frame the apps; sign-in and vault are never framed."""
import importlib.util
from pathlib import Path

from heardos_installer.kit import catalog


def _render():
    root = Path(catalog.__file__).resolve()
    while not (root / "generic" / "lib" / "caddyfile.py").exists():
        root = root.parent
    spec = importlib.util.spec_from_file_location("cf", root / "generic" / "lib" / "caddyfile.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.render({"DOMAIN": "example.org", "ACME_EMAIL": "a@example.org", "HOST_OVERLAY": "10.42.0.11",
                       "PUBLIC_HOSTS": "auth dash office docs vault photos", "CADDY_FRONT_DOOR": "1"})


def test_framing_rule_rendered_with_quotes():
    out = _render()
    assert "header_down -X-Frame-Options" in out
    fa = [l for l in out.splitlines() if "header_down" in l and "frame-ancestors" in l]
    assert len(fa) == 6                      # default handle + office (Nextcloud embeds OnlyOffice)
    # unquoted self/none would be read as a host named "self" and break apps framing themselves
    assert all("frame-ancestors 'self'" in l or "frame-ancestors 'none'" in l for l in fa)
    assert all("https://dash.example.org" in l for l in fa)
    assert "frame-ancestors self" not in out
    # only the desktop may frame apps: no wildcard over the family's sites, no browser-extension schemes
    assert "*.example.org" not in out and "extension:" not in out
    office = [l for l in fa if "https://docs.example.org" in l]
    assert len(office) == 3


def test_sign_in_and_vault_keep_their_own_framing_policy():
    out = _render()
    assert "@noframe host auth.example.org dash.example.org vault.example.org" in out
    blk = out.split("handle @noframe {", 1)[1].split("\n\t}", 1)[0]
    assert "reverse_proxy 10.42.0.11:80" in blk and "header_down" not in blk
    # the noframe handle must come before the catch-all handle that widens framing
    assert out.index("handle @noframe") < out.index("\thandle {\n")


def test_edge_hardening():
    out = _render()
    assert "admin localhost:2019" in out
    assert out.count('Strict-Transport-Security "max-age=31536000; includeSubDomains"') == 2   # apps + front door
    assert "replace code REDACTED" in out and "request>headers>Authorization delete" in out
    assert "prox.lan" not in out
