import json

import httpx, respx, pytest
from heardos_installer.providers import ProviderError, VpsHandle, VpsSpec, make, smart_default
from heardos_installer.providers.ovh import Ovh, signature
from heardos_installer.vault.migrate import import_credentials_env

B = "https://api.us.ovhcloud.com/1.0"
TOK = json.dumps({"endpoint": "us", "app_key": "ak", "app_secret": "as", "consumer_key": "ck"})
PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc heardos"
P = lambda usd: int(usd * 1e8)
CATALOG = {
    "plans": [
        {"planCode": "vps-2027-model2.LZ", "invoiceName": "VPS-2 LZ 2027", "product": "p-lz", "family": "vps-2025-lz",
         "configurations": [{"name": "vps_datacenter", "values": ["US-EAST-LZ-DAL", "US-EAST-LZ-MIA"]},
                            {"name": "vps_os", "values": ["Debian 12", "Ubuntu 24.04"]}],
         "addonFamilies": [{"name": "automatedBackup", "mandatory": True, "addons": ["bk-premium", "bk-standard"]},
                           {"name": "os", "mandatory": True, "addons": ["option-linux"]},
                           {"name": "snapshot", "mandatory": False, "addons": ["snap"]}],
         "pricings": [{"capacities": ["installation"], "mode": "default", "interval": 0, "price": 0},
                      {"capacities": ["renew"], "mode": "default", "interval": 1, "price": P(10)},
                      {"capacities": ["renew"], "mode": "upfront12", "interval": 1, "price": P(8.5)}]},
        {"planCode": "vps-2027-model1", "invoiceName": "VPS-1 2027", "product": "p-1", "family": "vps-2025",
         "configurations": [{"name": "vps_datacenter", "values": ["US-EAST-VA"]}], "addonFamilies": [],
         "pricings": [{"capacities": ["renew"], "mode": "default", "interval": 1, "price": P(5.35)}]},
        {"planCode": "vps-2027-model1-degressivity12", "product": "p-1", "family": "vps-2025", "configurations": [{"name": "vps_datacenter", "values": ["US-EAST-VA"]}],
         "pricings": [{"capacities": ["renew"], "mode": "default", "interval": 1, "price": P(4)}]},
        {"planCode": "vps-value-1-2-40-vps-2025-model1", "product": "p-1", "family": "vps-value-1-2-40-vps-2025-model1",
         "configurations": [{"name": "vps_datacenter", "values": ["US-EAST-VA"]}],
         "pricings": [{"capacities": ["renew"], "mode": "default", "interval": 1, "price": P(3)}]},
    ],
    "products": [{"name": "p-lz", "description": "VPS 4 vCPU 8 GB RAM 75 GB disk"}, {"name": "p-1", "description": "VPS 2 vCPU 4 GB RAM 40 GB disk"}],
    "addons": [{"planCode": "bk-premium", "pricings": [{"capacities": ["renew"], "mode": "default", "interval": 1, "price": P(2.4)}]},
               {"planCode": "bk-standard", "pricings": [{"capacities": ["renew"], "mode": "default", "interval": 1, "price": P(1.2)}]},
               {"planCode": "option-linux", "pricings": [{"capacities": ["renew"], "mode": "default", "interval": 1, "price": 0}]}],
}


def stock(code, rows):
    respx.get(f"{B}/vps/order/rule/datacenter", params={"ovhSubsidiary": "US", "planCode": code}).respond(
        json={"datacenters": [{"datacenter": dc, "linuxStatus": "available" if ok else "out-of-stock"} for dc, ok in rows]})


def test_signature_vector():
    # OVH's documented formula: "$1$" + sha1(AS+CK+METHOD+URL+BODY+TS joined with "+")
    assert signature("as", "ck", "get", f"{B}/me", "", 1700000000) == "$1$980c9451acb4171b75f21623adf5bc225e12553c"


def test_bad_credentials_json():
    with pytest.raises(ProviderError, match="missing app_secret"):
        Ovh(json.dumps({"app_key": "a", "consumer_key": "c"}))


@respx.mock
def test_requests_are_signed_and_rejection_is_readable():
    respx.get(f"{B}/auth/time").respond(text="1700000100")
    me = respx.get(f"{B}/me").mock(side_effect=[httpx.Response(200, json={"email": "op@example.org", "nichandle": "ab1-ovh"}),
                                                httpx.Response(403, json={"message": "This call has not been granted"})])
    o = make("ovh", TOK)
    assert o.validate_token().label == "OVHcloud US: op@example.org"
    h = me.calls[0].request.headers
    assert h["X-Ovh-Application"] == "ak" and h["X-Ovh-Consumer"] == "ck" and h["X-Ovh-Signature"].startswith("$1$")
    assert h["X-Ovh-Signature"] == signature("as", "ck", "GET", f"{B}/me", "", int(h["X-Ovh-Timestamp"]))
    with pytest.raises(ProviderError, match="rejected or lack a right"):
        o.validate_token()


@respx.mock
def test_catalog_regions_plans_and_default():
    respx.get(f"{B}/order/catalog/public/vps").respond(json=CATALOG)
    stock("vps-2027-model2.LZ", [("US-EAST-LZ-DAL", True), ("US-EAST-LZ-MIA", False)])
    stock("vps-2027-model1", [("US-EAST-VA", True)])
    o = Ovh(TOK)
    regs = {r.id: r for r in o.regions()}
    assert set(regs) == {"US-EAST-LZ-DAL", "US-EAST-LZ-MIA", "US-EAST-VA"}
    assert regs["US-EAST-LZ-DAL"].name == "Dallas (Local Zone)" and regs["US-EAST-LZ-DAL"].country == "US"
    assert not regs["US-EAST-LZ-MIA"].available
    (pl,) = o.plans("US-EAST-LZ-DAL")
    assert (pl.vcpu, pl.ram_mb, pl.disk_gb) == (4, 8192, 75)
    assert pl.monthly_usd == 11.2        # $10 plan + the cheapest mandatory backup; the optional snapshot is not added
    assert o.plans("US-EAST-LZ-MIA") == []                                       # out of stock
    assert [p.id for p in o.plans("US-EAST-VA")] == ["vps-2027-model1"]          # degressivity variant and legacy code skipped
    ch = smart_default(list(regs.values()), o.plans, tz="America/Chicago")
    assert (ch.region.id, ch.plan.id) == ("US-EAST-LZ-DAL", "vps-2027-model2.LZ")   # first US region by name with stock


@respx.mock
def test_order_rebuild_and_root_login(monkeypatch):
    respx.get(f"{B}/auth/time").respond(text="1700000100")
    respx.get(f"{B}/order/catalog/public/vps").respond(json=CATALOG)
    vps = respx.get(f"{B}/vps").mock(side_effect=[httpx.Response(200, json=["old.vps.ovh.us"]),
                                                  httpx.Response(200, json=["old.vps.ovh.us"]),
                                                  httpx.Response(200, json=["old.vps.ovh.us", "vps-new.vps.ovh.us"])])
    respx.post(f"{B}/order/cart").respond(json={"cartId": "c1"})
    respx.post(f"{B}/order/cart/c1/vps").respond(json={"itemId": 7})
    conf = respx.post(f"{B}/order/cart/c1/item/7/configuration").respond(json={"id": 1})
    opts = respx.post(f"{B}/order/cart/c1/vps/options").respond(json={"itemId": 8})
    respx.post(f"{B}/order/cart/c1/assign").respond(json=None)
    co = respx.post(f"{B}/order/cart/c1/checkout").respond(json={"orderId": 99, "url": "https://pay", "prices": {"withTax": {"text": "$11.20"}}})
    respx.get(f"{B}/me/order/99/status").respond(json="delivering")
    respx.get(f"{B}/vps/vps-new.vps.ovh.us").respond(json={"state": "running"})
    respx.get(f"{B}/vps/vps-new.vps.ovh.us/images/available").respond(json=["i-deb", "i-ubu"])
    respx.get(f"{B}/vps/vps-new.vps.ovh.us/images/available/i-deb").respond(json={"id": "i-deb", "name": "Debian 12"})
    respx.get(f"{B}/vps/vps-new.vps.ovh.us/images/available/i-ubu").respond(json={"id": "i-ubu", "name": "Ubuntu 24.04"})
    rb = respx.post(f"{B}/vps/vps-new.vps.ovh.us/rebuild").respond(json={"id": 5, "state": "todo"})
    respx.get(f"{B}/vps/vps-new.vps.ovh.us/tasks/5").respond(json={"id": 5, "state": "done"})
    respx.get(f"{B}/vps/vps-new.vps.ovh.us/ips").respond(json=["2001:db8::1", "198.51.100.7"])
    monkeypatch.setattr("time.sleep", lambda s: None)
    roots = []
    monkeypatch.setattr(Ovh, "_root_login", lambda self, ip, os_label, on_line=None: roots.append((ip, os_label)))

    o = Ovh(TOK)
    key = o.ensure_ssh_key("heardos", PUB)
    h = o.create(VpsSpec("lh", "US-EAST-LZ-DAL", "vps-2027-model2.LZ", o.ubuntu_image(), key))
    assert h.id == "order-99" and h.extra["total"] == "$11.20"
    assert [json.loads(c.request.content) for c in conf.calls] == [{"label": "vps_datacenter", "value": "US-EAST-LZ-DAL"},
                                                                   {"label": "vps_os", "value": "Ubuntu 24.04"}]
    assert sorted(json.loads(c.request.content)["planCode"] for c in opts.calls) == ["bk-standard", "option-linux"]
    assert json.loads(co.calls[0].request.content)["autoPayWithPreferredPaymentMethod"] is True
    assert o.wait_ip(h, timeout=60) == "198.51.100.7"
    assert h.id == "vps-new.vps.ovh.us"
    assert json.loads(rb.calls[0].request.content) == {"imageId": "i-ubu", "publicSshKey": PUB, "doNotSendPassword": True}
    assert roots == [("198.51.100.7", "Ubuntu 24.04")]
    # the handle survives a restart: a second wait does not rebuild again
    h2 = VpsHandle.from_json(h.to_json()); o.wait_ip(h2, timeout=60); assert rb.call_count == 1


@respx.mock
def test_unpaid_order_and_destroy_note(monkeypatch):
    respx.get(f"{B}/auth/time").respond(text="1700000100")
    respx.get(f"{B}/vps").respond(json=[])
    respx.get(f"{B}/me/order/5/status").respond(json="notPaid")
    o = Ovh(TOK)
    with pytest.raises(ProviderError, match="not paid.*https://pay"):
        o._service(VpsHandle("ovh", "order-5", extra={"orderId": 5, "before": [], "pay_url": "https://pay"}))
    t = respx.post(f"{B}/vps/vps-x/terminate").respond(json="ok")
    o.destroy(VpsHandle("ovh", "vps-x"))
    assert t.called and "confirmation" in o.destroy_note
    c = respx.post(f"{B}/vps/vps-x/confirmTermination").respond(json="ok")
    o.confirm_destroy("vps-x", "tok123")
    assert json.loads(c.calls[0].request.content)["token"] == "tok123"


def test_credentials_env_import(tmp_path):
    class V(dict):
        def names(self): return list(self)
        def update(self, d): super().update(d)
    env = tmp_path / "c.env"
    env.write_text("OVH_ENDPOINT=us\nOVH_APP_KEY=a\nOVH_APP_SECRET='s'\nOVH_CONSUMER_KEY=c\n")
    v = V(); import_credentials_env(v, env)
    assert json.loads(v["OVH_CREDENTIALS"]) == {"endpoint": "us", "app_key": "a", "app_secret": "s", "consumer_key": "c"}
