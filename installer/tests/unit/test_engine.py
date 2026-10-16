import threading, pytest
from heardos_installer.engine import Step, StepResult, StepStatus, Runner, InstallState, topo_layers
from heardos_installer.engine.log import LogHub
from heardos_installer.engine.step import Ctx

class FakeVault:
    def __init__(self): self.d = {}; self.unlocked = True
    def update(self, v): self.d.update(v)
    def get(self, k, default=None): return self.d.get(k, default)
    def values(self): return list(self.d.values())

def mk(steps, state, vault):
    hub = LogHub(state.install_id, vault.values)
    return Runner(steps, lambda s: Ctx(state.plan, vault, None, hub.logger(s.id), state, None, None), state, vault)

def test_layers_and_cycle():
    a = Step("a", "local", lambda c: StepResult(True)); b = Step("b", "local", lambda c: StepResult(True), deps=["a"])
    c = Step("c", "local", lambda c: StepResult(True), deps=["a"]); d = Step("d", "local", lambda c: StepResult(True), deps=["b", "c"])
    assert [[s.id for s in l] for l in topo_layers([d, c, b, a])] == [["a"], ["b", "c"], ["d"]]
    x = Step("x", "local", lambda c: StepResult(True), deps=["y"]); y = Step("y", "local", lambda c: StepResult(True), deps=["x"])
    with pytest.raises(ValueError): topo_layers([x, y])

def test_run_outputs_secrets_failure_and_resume():
    st = InstallState("t1"); v = FakeVault(); calls = []
    def ok(c): calls.append("ok"); return StepResult(True, outputs={"IP": "1.2.3.4"}, secrets={"TOKEN": "s3cretvalue"})
    def bad(c): calls.append("bad"); return StepResult(False, message="boom")
    steps = [Step("ok", "local", ok), Step("bad", "local", bad, deps=["ok"]), Step("after", "local", lambda c: StepResult(True), deps=["bad"])]
    r = mk(steps, st, v)
    assert r.run() is False
    assert st.status("ok") == StepStatus.done and st.status("bad") == StepStatus.failed and st.status("after") == StepStatus.skipped
    assert st.plan["IP"] == "1.2.3.4" and v.d["TOKEN"] == "s3cretvalue"
    assert "s3cretvalue" not in st.path.read_text()
    # resume: ok is done (no check) -> not re-run
    steps[1] = Step("bad", "local", lambda c: StepResult(True), deps=["ok"])
    calls.clear(); assert mk(steps, InstallState("t1"), v).run() is True and calls == []

def test_serial_group_never_overlaps():
    st = InstallState("t2"); v = FakeVault(); live = []; peak = [0]; lock = threading.Lock()
    def work(c):
        with lock: live.append(1); peak[0] = max(peak[0], len(live))
        import time; time.sleep(0.05)
        with lock: live.pop()
        return StepResult(True)
    steps = [Step(f"s{i}", "host", work, serial_group="apt") for i in range(4)]
    assert mk(steps, st, v).run() and peak[0] == 1

def test_redaction_in_log_file():
    st = InstallState("t3"); v = FakeVault(); v.update({"K": "supersecret123"})
    hub = LogHub("t3", v.values); hub.logger("x").info("token is supersecret123 ok")
    for h in hub.logger("x").handlers: h.flush()
    assert "supersecret123" not in hub.path("x").read_text() and "***" in hub.path("x").read_text()
