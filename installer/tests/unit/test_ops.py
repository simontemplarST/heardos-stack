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
    monkeypatch.setattr(ops, "run_kit", lambda ctx, script, args, **k: calls.append(args) or StepResult(True))
    pw = ops.add_person(inst, "bob", "Bob", "b@x.org", ["immich", "nextcloud"], ["flow-self-service"], False)
    assert inst.vault.get("PERSON_BOB_FIRST_PASSWORD") == pw and len(pw) == 23
    assert calls[1] == ["user", "access", "bob", "immich,nextcloud"] and calls[2] == ["user", "join", "bob", "flow-self-service"]


def test_portscan_finds_listener():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(); port = srv.getsockname()[1]
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    r = ops.portscan("127.0.0.1", ports=[port, port + 1 if port < 65535 else port - 1], timeout=1)
    srv.close()
    assert r["open"] == [port] and r["unexpected"] == [port]
