import respx
from heardos_installer.dns import CloudflareDNS, Record, ManualDNS, public_hosts_records

@respx.mock
def test_cloudflare_zone_lookup_and_upsert():
    b = "https://api.cloudflare.com/client/v4"
    respx.get(f"{b}/user/tokens/verify").respond(json={"success": True})
    respx.get(f"{b}/zones", params={"name": "example.com"}).respond(json={"success": True, "result": [{"id": "z1"}]})
    respx.get(f"{b}/zones/z1").respond(json={"success": True, "result": {"name": "example.com", "status": "active"}})
    respx.get(f"{b}/zones/z1/dns_records", params={"name": "auth.example.com", "type": "A"}).respond(json={"success": True, "result": [{"id": "r1", "content": "1.1.1.1"}]})
    respx.get(f"{b}/zones/z1/dns_records", params={"name": "photos.example.com", "type": "A"}).respond(json={"success": True, "result": []})
    put = respx.put(f"{b}/zones/z1/dns_records/r1").respond(json={"success": True}); post = respx.post(f"{b}/zones/z1/dns_records").respond(json={"success": True})
    cf = CloudflareDNS("t", domain="example.com"); assert "example.com" in cf.validate() and cf.zone_id == "z1"
    lines = []; cf.publish(public_hosts_records("example.com", ["auth", "photos"], "203.0.113.10"), lines.append)
    assert put.called and post.called and any("updated" in l for l in lines)

def test_manual_instructions():
    t = ManualDNS.instructions([Record("A", "auth.example.com", "203.0.113.10"), Record("MX", "send.example.com", "feedback.example", 10)])
    assert "auth.example.com" in t and "priority 10" in t
