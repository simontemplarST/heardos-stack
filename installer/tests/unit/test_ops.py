"""Day-two operations against fakes: no servers involved."""
import socket, threading
from types import SimpleNamespace

from heardos_installer import ops
from heardos_installer.session import Installation
from heardos_installer.vault import Vault

PLAN = {"DOMAIN": "example.com", "ADMIN_USER": "alice", "ADMIN_EMAIL": "a@example.com", "HOST_LAN_IP": "192.168.10.50",
        "provider": "cloudzy", "profile": "essentials", "TZ": "America/Chicago"}


def _inst(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARDOS_HOME", str(tmp_path))
    v = Vault(tmp_path / "v.json"); v.create("x" * 12)
    return Installation("ops-1", v, dict(PLAN))


def test_add_apps_plans_dns_caddy_and_post_steps(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    inst.state.set_status("vps.caddy", __import__("heardos_installer.engine", fromlist=["StepStatus"]).StepStatus.done)
    ids = ops.add_apps(inst, ["dash"])
    assert {"app.dash", "app.mealie", "app.homebox", "post.mealie-token", "dns.publish", "vps.caddy"} <= ids
    assert "dash" in inst.plan["apps"] and "mealie" in inst.plan["apps"]
    assert "dash" in inst.plan["PUBLIC_HOSTS"] and "vps.caddy" not in inst.state.data["steps"]   # will re-run for the new host
    assert ids <= {s.id for s in inst.steps()}


def test_app_status_worst_state_wins(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    out = "immich\trunning\tUp 2 hours\nimmich\trunning\tUp 2 hours (unhealthy)\nmealie\texited\tExited (1)\n\tcreated\tx\n"
    inst.sessions._host = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout=out, ok=True))
    assert ops.app_status(inst) == {"immich": "unhealthy", "mealie": "exited"}


def test_people_parses_json(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    from heardos_installer.engine.step import StepResult
    monkeypatch.setattr(ops, "run_kit", lambda *a, **k: StepResult(True, stdout='noise\nHEARDOS_JSON [{"username": "bob"}]\n'))
    assert ops.people(inst) == [{"username": "bob"}]


def test_add_person_password_goes_to_vault(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    calls = []
    from heardos_installer.engine.step import StepResult
    envs, puts, cmds = [], [], []
    monkeypatch.setattr(ops, "run_kit", lambda ctx, script, args, **k: calls.append(args) or envs.append(k.get("env")) or StepResult(True))
    inst.sessions._host = SimpleNamespace(tempdir=lambda: "/root/.heardos-x", put_text=lambda t, p, m=0o600: puts.append((t, p, m)),
                                          run=lambda c, **k: cmds.append(c))
    pw = ops.add_person(inst, "bob", "Bob", "b@x.org", ["immich", "nextcloud"], ["flow-self-service"], False)
    assert inst.vault.get("PERSON_BOB_FIRST_PASSWORD") == pw and len(pw) == 23
    assert pw not in " ".join(calls[0]) and "--password" not in calls[0]          # never on argv
    assert puts == [(pw, "/root/.heardos-x/password", 0o600)] and envs[0] == {"HEARDOS_PASSWORD_FILE": "/root/.heardos-x/password"}
    assert cmds == ["rm -rf /root/.heardos-x"]
    assert calls[1] == ["user", "access", "bob", "immich,nextcloud"] and calls[2] == ["user", "join", "bob", "flow-self-service"]


def test_portscan_finds_listener():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(); port = srv.getsockname()[1]
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    r = ops.portscan("127.0.0.1", ports=[port, port + 1 if port < 65535 else port - 1], timeout=1)
    srv.close()
    assert r["open"] == [port] and r["unexpected"] == [port]


NGFW_STATUS = """\
music.example.com                dns=ok   sign-in=ok   cert=ok    
books.example.com                dns=ok   sign-in=warn cert=skip  no answer: timeout
old.example.com                  dns=fail sign-in=ok   cert=ok    no A record
edge1        current  last pull 2026-01-01T00:00:30.123456789Z
apps         NOT current  last pull 2026-01-01T00:00:00Z
edge2        current  last pull -
"""


def test_ngfw_status_parses_hosts_and_pulls():
    now = __import__("datetime").datetime(2026, 1, 1, 0, 1, tzinfo=__import__("datetime").timezone.utc).timestamp()
    st = ops.parse_ngfw_status(NGFW_STATUS, now=now)
    assert st["hosts"]["books.example.com"] == ("ok", "warn", "skip", "no answer: timeout")
    assert st["pulls"]["edge1"][0] is True and abs(st["pulls"]["edge1"][1] - 29.876543) < 1e-3
    assert st["pulls"]["apps"] == (False, 60.0) and st["pulls"]["edge2"] == (True, None)


def test_ngfw_checks_fail_on_failing_host_or_stuck_edge_warn_is_information():
    st = ops.parse_ngfw_status(NGFW_STATUS, now=__import__("datetime").datetime(2026, 1, 1, 0, 1, tzinfo=__import__("datetime").timezone.utc).timestamp())
    by = {c.name: c.ok for c in ops.ngfw_checks(st)}
    assert by["edge1 up to date"] is True and by["apps up to date"] is False and by["edge2 up to date"] is False
    assert by["music.example.com"] is True and by["books.example.com"] is None and by["old.example.com"] is False
    st["pulls"]["edge1"] = (True, ops.NGFW_PULL_STALE_S + 1)
    assert {c.name: c.ok for c in ops.ngfw_checks(st)}["edge1 up to date"] is False


def test_health_on_ngfw_install_uses_firewall_hosts_and_beacon_unit(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    inst.plan.update(NGFW_URL="https://fw.example", PUBLIC_HOSTS=["stale"])
    ran = []

    class S:
        def run(self, cmd, **kw):
            ran.append(cmd)
            out = NGFW_STATUS if "ngfw-publish.py status" in cmd else ""
            return SimpleNamespace(stdout=out, stderr="", code=0)
    inst.sessions = SimpleNamespace(vps=lambda: S(), host=lambda: S())
    monkeypatch.setattr(ops, "host_load", lambda i: {"busy": None})
    checked = []
    monkeypatch.setattr(ops, "_https_check", lambda h: checked.append(h) or ops.Check("Web addresses", h, True, "ok"))
    ops.health(inst)
    assert "ngfw-lighthouse" in ran[0] and " nebula " not in ran[0]
    assert sorted(checked) == ["books.example.com", "music.example.com", "old.example.com"]
