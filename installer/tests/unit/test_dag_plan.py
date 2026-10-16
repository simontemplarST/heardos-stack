import json, pytest
from heardos_installer.kit import catalog
from heardos_installer.steps import build_steps, apply_defaults, missing_inputs
from heardos_installer.engine import topo_layers
from heardos_installer.vault import Vault

def test_catalog_closure_and_hosts():
    assert catalog.closure(["nextcloud"]) == ["onlyoffice", "nextcloud"]
    assert catalog.added_by_dependency(["dash"]) == ["mealie", "homebox"]
    with pytest.raises(ValueError): catalog.closure(["nope"])
    assert catalog.public_hosts(["nextcloud"]) == ["auth", "office", "docs"]
    assert set(catalog.profile_apps("essentials")) == {"immich", "onlyoffice", "nextcloud", "vaultwarden", "baikal"}

PLAN = {"DOMAIN": "example.com", "ADMIN_USER": "alice", "ADMIN_EMAIL": "a@example.com", "HOST_LAN_IP": "192.168.10.50",
        "provider": "cloudzy", "profile": "essentials", "TZ": "America/Chicago"}

def test_defaults_and_layers():
    p = apply_defaults(dict(PLAN))
    assert p["LAN_DOMAIN"] == "192-168-10-50.sslip.io" and p["HOST_OVERLAY"] == "10.42.0.11" and p["NEBULA_LH_OVERLAY"] == "10.42.0.1"
    assert "cloud" in p["PUBLIC_HOSTS"] and p["apps"].index("onlyoffice") < p["apps"].index("nextcloud")
    layers = [[s.id for s in l] for l in topo_layers(build_steps(p))]
    flat = [s for l in layers for s in l]
    pos = {s: i for i, l in enumerate(layers) for s in l}
    assert pos["vps.create"] == pos["host.push_kit"]                     # VPS creation overlaps home-server work
    assert pos["dns.publish"] < pos["vps.caddy"] < pos["verify"]
    assert pos["authentik.bootstrap"] < pos["app.immich"] and pos["app.onlyoffice"] < pos["app.nextcloud"]
    assert pos["vps.caddy"] <= pos["host.authentik"]                     # certificates are not waiting for apps
    assert flat[-1] == "verify"
    assert "app.vaultwarden" in flat and "post.baikal-user" in flat and "mail.configure" in flat
    assert pos["mail.domain"] <= 2                                        # Resend DNS propagates while the rest installs
    assert pos["vps.caddy"] < pos["post.nextcloud-admin"]                 # guided steps only once the public URL works

def test_family_profile_order():
    p = apply_defaults(dict(PLAN, profile="family"))
    steps = {s.id: s for s in build_steps(p)}
    assert "post.mealie-token" in steps["app.dash"].deps                  # dashboard waits for Mealie's API token
    assert "post.ha-onboard" in steps and "app.homepage" in steps
    assert all(catalog.apps()[a].available for a in p["apps"])            # every catalogue app now has a stack

def test_missing_inputs(tmp_path):
    v = Vault(tmp_path / "v.json"); v.create("x" * 12)
    p = apply_defaults(dict(PLAN))
    need = missing_inputs(p, v)
    assert "a Cloudzy API token" in need and any("Cloudflare" in n for n in need)
    v.update({"CLOUDZY_API_TOKEN": "t", "CF_API_TOKEN": "c"})
    assert missing_inputs(p, v) == []

def test_full_dry_run(tmp_path, monkeypatch):
    from heardos_installer.session import Installation
    v = Vault(tmp_path / "v.json"); v.create("x" * 12); v.update({"CLOUDZY_API_TOKEN": "t", "CF_API_TOKEN": "c"})
    seen = []
    inst = Installation("dry-1", v, dict(PLAN))
    inst.listeners.append(lambda sid, st, msg: seen.append((sid, st.value)))
    assert inst.run(dry_run=True)
    done = {s for s, st in seen if st == "done"}
    assert {"vps.create", "host.bootstrap", "app.nextcloud", "verify"} <= done
