"""2026-10-18: the edge lets only the family's own sites frame the apps (HeardOS desktop, Nextcloud -> OnlyOffice)."""
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
                       "PUBLIC_HOSTS": "auth dash", "CADDY_FRONT_DOOR": "1"})


def test_framing_rule_rendered_with_quotes():
    out = _render()
    assert "header_down -X-Frame-Options" in out
    fa = [l for l in out.splitlines() if "header_down" in l and "frame-ancestors" in l]
    assert len(fa) == 3
    # unquoted self/none would be read as a host named "self" and break apps framing themselves
    assert all("frame-ancestors 'self'" in l or "frame-ancestors 'none'" in l for l in fa)
    assert all("https://*.example.org" in l for l in fa)
    assert "frame-ancestors self" not in out
