"""The model harness with a scripted stand-in model: intent routing, confirmations, guards, the MCP server."""
import io
import json

from heardos_installer.harness import agent as A
from heardos_installer.harness.mcp import serve
from heardos_installer.harness.schema import check, coerce
from heardos_installer.manage.sim import SimBackend


class Script:
    """Answers by schema: intent / tool choice / arguments (in order) / done? / final answer."""

    def __init__(self, intent, steps, done="yes", pick="none"):
        self.intent, self.steps, self.done, self.pick, self.name = intent, list(steps), done, pick, "script"
        self.seen_tool_enums = []
        self._args = None

    def json(self, messages, schema):
        p = schema["properties"]
        if "intent" in p:
            return {"thought": "sort", "intent": self.intent}
        if "number" in p:       # a numbered pick: find our app's line in the prompt
            prompt = messages[-1]["content"]
            name = next((r["name"] for r in SimBackend().inventory(False) if r["stack"] == self.pick), None)
            for line in prompt.splitlines():
                if name and line.split(". ", 1)[-1].startswith(name + ":"):
                    return {"thought": "match", "number": int(line.split(".", 1)[0])}
            return {"thought": "none", "number": p["number"]["enum"][-1]}
        if "tool" in p:
            self.seen_tool_enums.append(p["tool"]["enum"])
            if not self.steps:
                return {"thought": "done", "tool": "finish"}
            tool, self._args = self.steps.pop(0)
            return {"thought": f"use {tool}", "tool": tool}
        if "fits" in p:          # "is the picked app what the task asks for?"
            return {"thought": "check", "fits": "yes"}
        if "answerable" in p:
            self.asked_answerable = getattr(self, "asked_answerable", 0) + 1
            return {"thought": "check", "answerable": "no" if self.asked_answerable == 1 else "yes"}
        if "done" in p:
            return {"missing": "nothing" if self.done == "yes" else "the other apps", "done": self.done}
        if "answer" in p:
            return {"answer": "All done."}
        return self._args


def test_schema_coerce_repairs_small_model_mistakes():
    s = {"type": "object", "properties": {"app": {"type": "string", "enum": ["mealie", "homebox"]}, "lines": {"type": "integer", "enum": [20, 50]},
                                          "env": {"type": "array", "items": {"type": "string"}}}, "required": ["app"], "additionalProperties": False}
    a = coerce(s, {"app": "Mealie app", "lines": "50", "env": "A=1; B=2", "junk": 1})
    assert a == {"app": "mealie", "lines": 50, "env": ["A=1", "B=2"]} and check(s, a) == []
    assert "'app' must be one of" in check(s, {"app": "nope"})[0]
    assert check(s, {}) == ["'app' is required"]


def test_add_app_playbook_installs_the_picked_catalogue_app():
    b = SimBackend()
    llm = Script("add_app", [], pick="hortusfox")
    res = A.Agent(b, llm, A.Policy(allow_changes=True)).run("plants")
    assert [t.tool for t in res.turns] == ["overview", "install_app"]       # both run by the harness, no free tool choice
    assert b.journal == [("install_app", {"stack": "hortusfox"})] and res.stopped == "finished" and not llm.seen_tool_enums
    assert "What was actually changed:\n- install_app" in res.report()


YES = lambda tool, args, preview, risk: True     # noqa: E731 — the operator approves whatever is asked


def test_add_app_playbook_none_leads_to_the_custom_path():
    b = SimBackend()
    llm = Script("add_app", [("add_custom_app", {"name": "uptime", "image": "louislam/uptime-kuma:1", "access": "admins"})])
    A.Agent(b, llm, A.Policy(allow_changes=True, confirm=YES)).run("add uptime kuma")
    assert set(llm.seen_tool_enums[0]) == {"search_images", "inspect_image", "add_custom_app", "finish"}
    assert b.journal[0][0] == "add_custom" and b.custom["uptime"]["port"] == 3001


def test_a_named_image_skips_the_catalogue_pick():
    b = SimBackend()
    llm = Script("add_app", [("add_custom_app", {"name": "uptime", "image": "louislam/uptime-kuma:1", "access": "admins"})], pick="librespeed")
    A.Agent(b, llm, A.Policy(allow_changes=True, confirm=YES)).run("Add Uptime Kuma (image louislam/uptime-kuma:1) for the admins")
    assert [o for o, _ in b.journal] == ["add_custom"]                    # the pick (librespeed) was never asked


def test_fix_playbook_reads_logs_of_the_picked_app():
    b = SimBackend()
    llm = Script("fix_app", [("control_app", {"app": "homebox", "action": "restart"})], pick="homebox")
    res = A.Agent(b, llm, A.Policy(allow_changes=True)).run("inventory is broken")
    assert [t.tool for t in res.turns] == ["overview", "app_status", "app_logs", "control_app"]
    assert b.journal == [("restart", {"stack": "homebox"})]


def test_an_app_named_in_the_task_skips_the_model_pick():
    rows = [{"stack": "homebox", "name": "Home inventory"}, {"stack": "homeassistant", "name": "Smart home"},
            {"stack": "paperless", "name": "Paperwork"}, {"stack": "paperless-gpt", "name": "AI filing for Paperless"}]
    assert A._named_in_task("Restart homebox.", rows) == "homebox"
    assert A._named_in_task("is the smart home up?", rows) == "homeassistant"
    assert A._named_in_task("logs of paperless-gpt please", rows) == "paperless-gpt"   # longest of paperless / paperless-gpt
    assert A._named_in_task("my home stuff is slow", rows) is None
    b = SimBackend()
    llm = Script("fix_app", [("control_app", {"app": "homebox", "action": "restart"})], pick="homeassistant")
    res = A.Agent(b, llm, A.Policy(allow_changes=True)).run("restart homebox")
    assert [t.args.get("app") for t in res.turns if t.tool == "app_status"] == ["homebox"]   # the scripted wrong pick is never asked


def test_intent_limits_tools():
    b = SimBackend()
    llm = Script("question", [("install_app", {"app": "hortusfox"})])   # not offered for a question -> treated as finish
    res = A.Agent(b, llm).run("what is installed?")
    assert not b.journal and "install_app" not in llm.seen_tool_enums[0] and res.answer == "All done."


def test_changes_wait_for_approval_and_refusal_is_final():
    b = SimBackend()
    asked = []
    pol = A.Policy(confirm=lambda tool, args, prev, risk: asked.append((tool, prev)) or False)
    llm = Script("fix_app", [("control_app", {"app": "homebox", "action": "restart"})] * 2)
    res = A.Agent(b, llm, pol).run("fix homebox")
    assert len(asked) == 1 and "Restart Home inventory" in asked[0][1]
    assert not b.journal and res.stopped == "refused"                    # a no ends the run: no second try, no workaround
    assert [t.approved for t in res.turns if t.tool == "control_app"] == [False]
    assert "Not approved" in res.report()
    b = SimBackend()                                                      # same for the playbook's own install
    res = A.Agent(b, Script("add_app", [], pick="hortusfox"), A.Policy(confirm=lambda *a: False)).run("plants")
    assert not b.journal and res.stopped == "refused" and [t.tool for t in res.turns] == ["overview", "install_app"]


def test_removal_asks_even_with_allow_changes():
    b = SimBackend()
    asked = []
    pol = A.Policy(allow_changes=True, confirm=lambda tool, args, prev, risk: asked.append(risk) or True)
    A.Agent(b, Script("remove_app", [("remove_app", {"app": "homebox"})]), pol).run("remove homebox")
    assert asked == ["danger"] and b.journal == [("remove", {"stack": "homebox"})]


def test_invalid_request_goes_back_to_the_model_not_the_operator():
    b = SimBackend()
    asked = []
    pol = A.Policy(confirm=lambda *a: asked.append(a) or True)
    llm = Script("add_app", [("add_custom_app", {"name": "kuma", "image": "invented/thing:1", "access": "admins"})])
    res = A.Agent(b, llm, pol).run("add kuma")
    t = res.turns[-1]
    assert not asked and not t.ok and "could not download" in t.text


def test_repeat_and_change_cap():
    b = SimBackend()
    llm = Script("other", [("app_status", {"app": "mealie"})] * 5)
    res = A.Agent(b, llm).run("look at mealie")
    assert sum(t.text.startswith("Already done") for t in res.turns) == 1   # then app_status is off the menu for a turn
    assert "app_status" in llm.seen_tool_enums[1] and "app_status" not in llm.seen_tool_enums[2]
    b = SimBackend()
    steps = [("control_app", {"app": a, "action": "restart"}) for a in ("mealie", "homebox", "immich", "baikal")]
    llm = Script("start_stop", steps, done="no")
    A.Agent(b, llm, A.Policy(allow_changes=True)).run("restart everything")
    assert len(b.journal) == A.MAX_CHANGES and "control_app" not in llm.seen_tool_enums[-1]


def test_mcp_lists_read_only_tools_by_default_and_calls_them():
    b = SimBackend()
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "app_status", "arguments": {"app": "Mealie"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "install_app", "arguments": {"app": "hortusfox"}}}]
    out = io.StringIO()
    serve(b, inp=io.StringIO("\n".join(json.dumps(r) for r in reqs)), outp=out)
    res = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}
    assert res[1]["result"]["serverInfo"]["name"] == "heardos"
    names = {t["name"] for t in res[2]["result"]["tools"]}
    assert "overview" in names and "install_app" not in names and "remove_app" not in names
    assert not res[3]["result"]["isError"] and "Recipes" in res[3]["result"]["content"][0]["text"]
    assert res[4]["result"]["isError"] and not b.journal
