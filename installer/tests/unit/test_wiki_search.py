"""2026-10-18 kit additions: offline Wikipedia (Kiwix, gated) and SearXNG (public)."""
from pathlib import Path

from heardos_installer.kit import catalog


def _root():
    r = Path(catalog.__file__).resolve()
    while not (r / "generic" / "catalog.json").exists():
        r = r.parent
    return r


def test_wiki_search_in_catalog():
    cat = catalog.apps()
    assert cat["kiwix"].available and cat["searxng"].available
    assert {"wiki", "search"} <= set(catalog.public_hosts(["kiwix", "searxng"]))


def test_kiwix_never_passes_port():
    # the image's start.sh already adds --port=$PORT; a second --port makes kiwix-serve print usage and exit
    compose = (_root() / "generic" / "stacks" / "kiwix" / "compose.yml").read_text()
    cmd = next(l for l in compose.splitlines() if l.strip().startswith("command:"))
    assert "--port" not in cmd and "${KIWIX_ZIM}" in cmd
    assert "authentik-fwd@file" in compose   # gated


def test_searxng_public_and_config_templates():
    r = _root()
    compose = (r / "generic" / "stacks" / "searxng" / "compose.yml").read_text()
    assert "authentik-fwd" not in compose and "SEARXNG_SECRET" in compose
    lim = (r / "apps" / "searxng" / "limiter.toml").read_text()
    assert "${PROXY_SUBNET}" in lim and "${OVERLAY_CIDR}" in lim and "${LAN_SUBNET}" in lim
    app = (r / "generic" / "50-app.sh").read_text()
    assert "  kiwix)" in app and "  searxng)" in app and "chmod 644" in app
