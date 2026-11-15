"""lib/ngfw-publish.py: how a hostname is described to the ngfw firewall (ngfw ADR 0104)."""
import importlib.util
from pathlib import Path

KIT = Path(__file__).resolve().parents[3] / "generic" / "lib"
spec = importlib.util.spec_from_file_location("ngfw_publish", KIT / "ngfw-publish.py")
np = importlib.util.module_from_spec(spec)
spec.loader.exec_module(np)

ENV = {"DOMAIN": "example.com", "HOST_OVERLAY": "100.64.0.11", "NGFW_EDGES": "edge1", "NGFW_ROUTER": "apps"}
NO_FRAME, EXTRA = ("auth", "vault", "dash", "hub"), {"office": ("docs",)}


def container(name, labels, nets=("proxy",)):
    return {"Name": "/" + name, "Config": {"Labels": {"traefik.enable": "true", **labels}},
            "NetworkSettings": {"Networks": {n: {} for n in nets}}}


GATED = container("navidrome", {
    "traefik.http.routers.music.rule": "Host(`music.example.com`)",
    "traefik.http.routers.music.middlewares": "xfp-https@file,authentik-fwd@file",
    "traefik.http.routers.music.service": "music",
    "traefik.http.routers.music-outpost.rule": "Host(`music.example.com`) && PathPrefix(`/outpost.goauthentik.io/`)",
    "traefik.http.routers.music-outpost.service": "authentik-outpost@file",
    "traefik.http.routers.music-lan.rule": "Host(`music.lan.example`)",
    "traefik.http.services.music.loadbalancer.server.port": "4533",
})
PATHS = container("listmonk", {
    "traefik.http.routers.news.rule": "Host(`news.example.com`)",
    "traefik.http.routers.news-admin.rule": "Host(`news.example.com`) && PathPrefix(`/admin`)",
    "traefik.http.routers.news-admin.middlewares": "authentik-fwd@file",
    "traefik.http.services.news.loadbalancer.server.port": "9000",
})
ISOLATED = container("office", {
    "traefik.http.routers.office.rule": "Host(`office.example.com`)",
    "traefik.http.services.office.loadbalancer.server.port": "80",
}, nets=("office-edge",))
FILES = {"http": {"routers": {
    "pub-git": {"rule": "Host(`git.example.com`)", "middlewares": ["xfp-https"], "service": "git"},
    "pub-pve": {"rule": "Host(`prox.example.com`)", "middlewares": ["authentik-fwd"], "service": "pve"}},
    "services": {"git": {"loadBalancer": {"servers": [{"url": "http://192.168.10.60:3000"}]}},
                 "pve": {"loadBalancer": {"servers": [{"url": "https://192.168.10.25:8006"}], "serversTransport": "pve-selfsigned"}}}}}


def routers():
    return np.routers_from_labels([GATED, PATHS, ISOLATED]) + np.routers_from_files([FILES])


def test_simple_gated_app_is_routed_with_its_gate():
    svc, note = np.desired("music", ENV, routers(), {"proxy"}, None, NO_FRAME, EXTRA)
    assert svc["route"] == {"router": "apps", "backend": "http://navidrome:4533", "priority": 50}
    assert svc["auth"] == "forward_auth"
    assert svc["hostname"] == "music.example.com" and svc["upstream"] == "100.64.0.11:80" and svc["edges"] == ["edge1"]
    assert svc["frame_ancestors"] == ["https://dash.example.com"]
    assert "routed" in note


def test_path_rules_keep_the_host_edge_only():
    svc, note = np.desired("news", ENV, routers(), {"proxy"}, None, NO_FRAME, EXTRA)
    assert "route" not in svc and "more than one router" in note


def test_custom_transport_and_unreachable_network_are_edge_only():
    svc, note = np.desired("prox", ENV, routers(), {"proxy"}, None, NO_FRAME, EXTRA)
    assert "route" not in svc and "serversTransport" in note and svc["auth"] == "forward_auth"
    svc, note = np.desired("office", ENV, routers(), {"proxy"}, None, NO_FRAME, EXTRA)
    assert "route" not in svc and "no network" in note
    assert svc["frame_ancestors"] == ["https://dash.example.com", "https://docs.example.com"]


def test_file_router_is_routed():
    svc, _ = np.desired("git", ENV, routers(), {"proxy"}, None, NO_FRAME, EXTRA)
    assert svc["route"]["backend"] == "http://192.168.10.60:3000" and svc["auth"] == "none"


def test_existing_firewall_values_are_kept_but_a_gate_is_never_hidden():
    cur = {"name": "git", "hostname": "git.example.com", "auth": "oidc", "app": "gitea", "max_body_mb": 512, "comment": "hand note"}
    svc, _ = np.desired("git", ENV, routers(), {"proxy"}, cur, NO_FRAME, EXTRA)
    assert svc["auth"] == "oidc" and svc["app"] == "gitea" and svc["max_body_mb"] == 512 and svc["comment"] == "hand note"
    cur = {"name": "music", "hostname": "music.example.com", "auth": "oidc"}
    svc, _ = np.desired("music", ENV, routers(), {"proxy"}, cur, NO_FRAME, EXTRA)
    assert svc["auth"] == "forward_auth"


def test_no_frame_apps_and_apex():
    svc, _ = np.desired("vault", ENV, routers(), {"proxy"}, {"frame_ancestors": ["https://x.example.com"]}, NO_FRAME, EXTRA)
    assert "frame_ancestors" not in svc
    assert np.fqdn("@", "example.com") == "example.com"
    svc, note = np.desired("@", ENV, routers(), {"proxy"}, None, NO_FRAME, EXTRA)
    assert svc["hostname"] == "example.com" and "no Traefik router" in note


def test_lan_router_does_not_count_as_a_public_one():
    mine = [r for r in routers() if "music.example.com" in np._hosts_in(r["rule"])]
    assert {r["name"] for r in mine} == {"music@docker", "music-outpost@docker"}


def test_vps_caddy_writes_nothing_when_a_firewall_publishes_the_edge():
    from types import SimpleNamespace
    from heardos_installer.steps.vps import vps_caddy
    r = vps_caddy(SimpleNamespace(plan={"NGFW_URL": "https://fw.example", "DOMAIN": "example.com"}))
    assert r.ok and "firewall" in r.message


def test_ngfw_settings_reach_the_host_plan_file():
    from heardos_installer.remote.plan import render_plan
    text = render_plan({"DOMAIN": "example.com", "NGFW_URL": "https://fw.example", "NGFW_EDGES": ["edge1", "edge2"],
                        "NGFW_ROUTER": "apps"}, {}, "host")
    assert "NGFW_URL=https://fw.example" in text and "NGFW_EDGES='edge1 edge2'" in text and "NGFW_ROUTER=apps" in text
