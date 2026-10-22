"""heardos-cli migrate: moving an adopted stack into the kit layout, against a scripted home server."""
import json
from types import SimpleNamespace

import pytest

from heardos_installer import adopt as A
from heardos_installer import migrate as M
from heardos_installer.session import Installation
from heardos_installer.vault import Vault


class FakeHost:
    """Answers the preflight probe from a dict of existing paths; records every command."""

    def __init__(self, existing=(), projects=("mealie",), secrets=True):
        self.existing, self.projects, self.secrets = set(existing), set(projects), secrets
        self.cmds = []

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        out = []
        for part in cmd.split("; "):
            if part.startswith("test -e ") and "MISSING" in part:
                p = part.split("test -e ", 1)[1].split(" ||")[0].strip("'")
                if p not in self.existing:
                    out.append(f"MISSING {p}")
            elif part.startswith("test -e ") and "EXISTS" in part:
                p = part.split("test -e ", 1)[1].split(" &&")[0].strip("'")
                if p in self.existing:
                    out.append(f"EXISTS {p}")
            elif "NOPROJECT" in part:
                proj = part.split("project=", 1)[1].split(" ")[0].strip("'")
                if proj not in self.projects:
                    out.append("NOPROJECT")
            elif "NOSECRETS" in part and not self.secrets:
                out.append("NOSECRETS")
        return SimpleNamespace(ok=True, stdout="\n".join(out), stderr="", rc=0)

    def tempdir(self):
        return "/tmp/x"

    def put_text(self, *a, **k):
        pass


def _inst(tmp_path):
    v = Vault(); v.create("correct horse battery staple!")
    plan = {"DOMAIN": "example.net", "HOST_LAN_IP": "192.168.1.9", "ADMIN_USER": "alice", "ADMIN_EMAIL": "a@example.net",
            "apps": ["mealie", "homebox"], "provider": "byo", "byo_ip": "203.0.113.5", "NEBULA_LH_IP": "203.0.113.5"}
    inst = Installation("mig-test", v, plan)
    A.mark_adopted(inst, lambda *_: None)
    return inst


SPEC = {"stacks": {"mealie": {"moves": [["/old/mealie", "data/mealie"]], "after": ["echo done"]},
                   "homebox": {"hold": "not yet"}}}


def test_entry_defaults_and_plan_text(tmp_path):
    m = M.Migrator(_inst(tmp_path), SPEC, log=lambda *_: None)
    e = m.entry("mealie")
    assert e["step"] == "app.mealie" and e["before"] == [] and m.dst("data/mealie") == "/opt/heardos/data/mealie"
    txt = m.plan_text("mealie")
    assert "move /old/mealie -> /opt/heardos/data/mealie" in txt and "run install step app.mealie" in txt and "for: mealie" in txt
    with pytest.raises(ValueError, match="no migration spec"):
        m.entry("nothing")


def test_preflight_finds_what_blocks_a_move(tmp_path):
    inst = _inst(tmp_path)
    host = FakeHost(existing={"/opt/heardos/data/mealie"}, projects=set(), secrets=False)
    inst.sessions._host = host   # noqa: SLF001
    m = M.Migrator(inst, SPEC, log=lambda *_: None)
    probs = m.preflight("mealie")
    assert any("old folder missing: /old/mealie" in p for p in probs)
    assert any("destination already exists" in p for p in probs)
    assert not any("compose project" in p for p in probs)          # a stack stopped through Portainer has no containers: not a blocker
    assert any("secrets.env" in p for p in probs)
    assert m.preflight("homebox")[0].startswith("on hold")
    host = FakeHost(existing={"/old/mealie"})
    inst.sessions._host = host   # noqa: SLF001
    assert m.preflight("mealie") == []


def test_migrate_moves_runs_the_step_and_releases_the_stack(tmp_path, monkeypatch):
    inst = _inst(tmp_path)
    host = FakeHost(existing={"/old/mealie"})
    inst.sessions._host = host   # noqa: SLF001
    ran = []

    def fake_run(only=None, dry_run=False):
        ran.append(set(only))
        from heardos_installer.engine.step import StepStatus
        inst.state.set_status("app.mealie", StepStatus.done, "Recipes installed")
        return True
    monkeypatch.setattr(inst, "run", fake_run)
    m = M.Migrator(inst, SPEC, log=lambda *_: None)
    rec = m.migrate("mealie")
    assert ran == [{"app.mealie"}]
    assert any(c == "docker compose -p mealie stop" for c in host.cmds)
    assert any("mv /old/mealie /opt/heardos/data/mealie && ln -s /opt/heardos/data/mealie /old/mealie" in c for c in host.cmds)
    assert host.cmds[-1] == "echo done"
    assert rec["moves"] == [["/old/mealie", "/opt/heardos/data/mealie"]] and rec["finished"]
    assert inst.plan["adopted_pending"] == ["traefik", "authentik", "homebox"]
    assert inst.state.handle("migrated.mealie")["stack"] == "mealie"
    with pytest.raises(RuntimeError, match="not pending"):
        m.migrate("mealie")


def test_failed_step_keeps_the_record_for_rollback(tmp_path, monkeypatch):
    inst = _inst(tmp_path)
    host = FakeHost(existing={"/old/mealie"})
    inst.sessions._host = host   # noqa: SLF001

    def fake_run(only=None, dry_run=False):
        from heardos_installer.engine.step import StepStatus
        inst.state.set_status("app.mealie", StepStatus.failed, "50-app.sh failed: boom")
        return False
    monkeypatch.setattr(inst, "run", fake_run)
    m = M.Migrator(inst, SPEC, log=lambda *_: None)
    with pytest.raises(RuntimeError, match="boom"):
        m.migrate("mealie")
    assert "mealie" in inst.plan["adopted_pending"] and inst.state.handle("migrated.mealie")["moves"]
    m.rollback("mealie")
    assert any("mv /opt/heardos/data/mealie /old/mealie" in c for c in host.cmds)
    assert inst.state.handle("migrated.mealie") is None and "mealie" in inst.plan["adopted_pending"]


def test_spec_path_and_loading(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"stacks": {}}))
    assert M.load_spec(p) == {"stacks": {}}
    assert M.spec_path("x", str(p)) == p and M.spec_path("x").name == "x.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        M.load_spec(p)
