import httpx, respx, pytest
from heardos_installer.providers import Cloudzy, Linode, Vultr, ProviderError, VpsSpec, smart_default

PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc heardos"

@respx.mock
def test_cloudzy_flow():
    b = "https://api.cloudzy.com/developers"
    respx.get(f"{b}/v1/instances").respond(json={"data": {"instances": []}})
    respx.get(f"{b}/v1/regions").respond(json={"data": [{"id": "dtx", "name": "Dallas", "isActive": True, "isOutOfStock": False},
                                                        {"id": "ams", "name": "Amsterdam", "isActive": True, "isOutOfStock": True}]})
    respx.get(f"{b}/v1/products").respond(json={"data": [{"id": "p1", "plan": {"name": "s", "specification": {"cpu": 1, "ramInMB": 512, "storage": 10}}, "priceItems": [{"hourlyPrice": 0.004}]},
                                                         {"id": "p2", "plan": {"name": "m", "specification": {"cpu": 1, "ramInMB": 1024, "storage": 25}}, "priceItems": [{"hourlyPrice": 0.0095}]}]})
    respx.get(f"{b}/v1/os").respond(json={"data": {"os": [{"id": 7, "family": "ubuntu", "name": "Ubuntu 22.04", "isActive": True},
                                                          {"id": 9, "family": "ubuntu", "name": "Ubuntu 24.04", "isActive": True},
                                                          {"id": 10, "family": "ubuntu", "name": "Ubuntu 25.10", "isActive": True}]}})
    keys = respx.get(f"{b}/v1/ssh-keys").mock(side_effect=[httpx.Response(200, json={"data": {"sshKeys": []}}),
                                                           httpx.Response(200, json={"data": {"sshKeys": [{"id": 55, "publicKey": PUB}]}})])
    respx.post(f"{b}/v1/ssh-keys").respond(json={"code": "OKAY"})
    respx.post(f"{b}/v1/instances").respond(json={"data": {"instances": [{"data": {"id": "inst-1"}}]}})
    respx.get(f"{b}/v1/instances/inst-1").respond(json={"data": {"mainIp": "203.0.113.10"}})
    c = Cloudzy("tok")
    assert "0 instances" in c.validate_token().label
    regs = c.regions(); assert [r.available for r in regs] == [True, False]
    assert c.ubuntu_image() == "9"
    assert c.ensure_ssh_key("heardos", PUB) == "55" and keys.call_count == 2
    ch = smart_default(regs, c.plans, tz="America/Chicago"); assert ch.region.id == "dtx" and ch.plan.id == "p2" and ch.plan.monthly_usd == round(0.0095 * 730, 2)
    h = c.create(VpsSpec("lh", "dtx", "p2", "9", "55")); assert h.id == "inst-1"
    assert c.wait_ip(h, timeout=5) == "203.0.113.10"

@respx.mock
def test_linode_and_bad_token():
    b = "https://api.linode.com/v4"
    respx.get(f"{b}/profile").respond(401, json={"errors": [{"reason": "Invalid Token"}]})
    with pytest.raises(ProviderError, match="rejected"): Linode("bad").validate_token()
    respx.get(f"{b}/linode/types").respond(json={"data": [{"id": "g6-nanode-1", "class": "nanode", "vcpus": 1, "memory": 1024, "disk": 25600, "price": {"monthly": 5.0, "hourly": 0.0075}},
                                                         {"id": "g6-dedicated-2", "class": "dedicated", "vcpus": 2, "memory": 4096, "disk": 81920, "price": {"monthly": 36}}], "pages": 1})
    respx.get(f"{b}/images").respond(json={"data": [{"id": "linode/ubuntu24.04", "is_public": True}], "pages": 1})
    respx.get(f"{b}/linode/instances/9").respond(json={"status": "running", "ipv4": ["198.51.100.4"]})
    l = Linode("t"); plans = l.plans("us-central")
    assert [p.id for p in plans] == ["g6-nanode-1"] and plans[0].disk_gb == 25
    assert l.ubuntu_image() == "linode/ubuntu24.04"
    from heardos_installer.providers import VpsHandle
    assert l.wait_ip(VpsHandle("linode", "9"), timeout=5) == "198.51.100.4"

@respx.mock
def test_vultr_plans_filter_and_os():
    b = "https://api.vultr.com/v2"
    respx.get(f"{b}/plans").respond(json={"plans": [{"id": "vc2-1c-1gb", "vcpu_count": 1, "ram": 1024, "disk": 25, "monthly_cost": 5, "locations": ["ord", "ewr"]},
                                                    {"id": "vc2-1c-2gb", "vcpu_count": 1, "ram": 2048, "disk": 55, "monthly_cost": 10, "locations": ["ewr"]}], "meta": {"links": {"next": ""}}})
    respx.get(f"{b}/os").respond(json={"os": [{"id": 1743, "name": "Ubuntu 22.04 LTS x64"}, {"id": 2284, "name": "Ubuntu 24.04 LTS x64"}, {"id": 5, "name": "Debian 12 x64"}], "meta": {"links": {"next": ""}}})
    v = Vultr("t"); assert [p.id for p in v.plans("ord")] == ["vc2-1c-1gb"]; assert v.ubuntu_image() == "2284"
