"""heardos-cli adopt: taking over a HeardOS that was set up without the installer (no servers involved)."""
import json
from types import SimpleNamespace

import pytest

from heardos_installer import adopt as A
from heardos_installer.engine.step import StepStatus
from heardos_installer.manage.backend import ManageError, RealBackend
from heardos_installer.session import Installation
from heardos_installer.vault import Vault


def _vault():
    v = Vault()
    v.create("correct horse battery staple!")
    return v


def _plan(**kw):
    p = {"DOMAIN": "example.net", "HOST_LAN_IP": "192.168.1.9", "ADMIN_USER": "alice", "ADMIN_EMAIL": "a@example.net",
         "apps": ["mealie", "homebox"], "provider": "byo", "byo_ip": "203.0.113.5", "NEBULA_LH_IP": "203.0.113.5"}
    p.update(kw)
    return p


def test_spec_validation(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"install_id": "x", "plan": {"DOMAIN": "e.net"}}))
    with pytest.raises(ValueError, match="HOST_LAN_IP"):
        A.load_spec(str(f))
    f.write_text(json.dumps({"install_id": "x", "plan": _plan()}))
    assert A.load_spec(str(f))["plan"]["provider"] == "byo"


def test_read_kv_keeps_everything_after_the_first_equals(tmp_path):
    f = tmp_path / "v.env"
    f.write_text("# c\nA=1=2\nB='q'\n\nnoequals\n")
    assert A.read_kv(str(f)) == {"A": "1=2", "B": "q"} and A.read_kv(None) == {}


def test_mark_adopted_records_every_step_but_the_real_ones():
    inst = Installation("adopt-test", _vault(), _plan())
    marked = A.mark_adopted(inst, lambda *_: None)
    ids = {s.id for s in inst.steps()}
    assert set(marked) == ids - A.REAL_STEPS and "app.mealie" in marked and "verify" in marked
    for sid in A.REAL_STEPS:
        assert inst.state.status(sid) == StepStatus.pending
    assert inst.state.status("app.mealie") == StepStatus.done and inst.state.step("app.mealie")["message"] == A.ADOPTED_MSG
    assert inst.plan["adopted_pending"] == ["traefik", "authentik", "mealie", "homebox"]
    assert inst.state.handle("adopted")["pending"] == inst.plan["adopted_pending"]


def test_dry_run_writes_nothing(capsys):
    spec = {"install_id": "dry", "plan": _plan(), "ssh": {"host": "root@h"}}
    out = []
    assert A.adopt(spec, None, {"AUTHENTIK_TOKEN": "t"}, {"X": "1"}, apply=False, log=out.append) is None
    assert any("[dry-run]" in l for l in out) and any("AUTHENTIK_TOKEN" in l for l in out)
    from heardos_installer.engine import InstallState
    assert InstallState.list_installs() == []


class _Sess:
    def __init__(self):
        self.cmds = []

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        return SimpleNamespace(ok=True, stdout="", stderr="", rc=0)


def test_backend_refuses_changes_on_a_pending_stack_but_restarts_it_by_label():
    inst = Installation("adopt-be", _vault(), _plan())
    A.mark_adopted(inst, lambda *_: None)
    sess = _Sess()
    inst.sessions._host = sess   # noqa: SLF001
    b = RealBackend(inst)
    with pytest.raises(ManageError, match="old layout"):
        b.set_setting("mealie", "LOG_LEVEL", "DEBUG")
    with pytest.raises(ManageError, match="migrate adopt-be mealie"):
        b.remove("mealie")
    with pytest.raises(ManageError, match="old layout"):
        b.lifecycle("mealie", "update")
    assert b.lifecycle("mealie", "restart") == "mealie: restart done"
    assert sess.cmds[-1] == "docker compose -p mealie restart"            # no cd into a folder that does not exist yet
    inst.plan["adopted_pending"] = []
    b.lifecycle("mealie", "restart")
    assert sess.cmds[-1].startswith("cd /opt/heardos/stacks/mealie && ")
