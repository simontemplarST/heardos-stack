from heardos_installer.remote.plan import render_plan
from heardos_installer.remote.ssh import Result

def test_render_plan_quotes_and_sides():
    p = {"DOMAIN": "example.com", "PUBLIC_HOSTS": ["auth", "photos"], "TZ": "America/Chicago", "UNKNOWN": "x"}
    s = {"CF_API_TOKEN": "cf tok", "AUTHENTIK_TOKEN": "ak$tok"}
    local = render_plan(p, s, "local"); host = render_plan(p, s, "host")
    assert "PUBLIC_HOSTS='auth photos'" in local and "CF_API_TOKEN='cf tok'" in local and "UNKNOWN" not in local
    assert "CF_API_TOKEN" not in host and "AUTHENTIK_TOKEN='ak$tok'" in host

def test_result_outputs():
    r = Result(0, "noise\nHEARDOS_OUTPUT AUTHENTIK_TOKEN=abc=def\nHEARDOS_OUTPUT PROXY_SUBNET=172.20.0.0/16\n", "")
    assert r.outputs() == {"AUTHENTIK_TOKEN": "abc=def", "PROXY_SUBNET": "172.20.0.0/16"}
