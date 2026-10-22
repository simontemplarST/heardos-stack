"""What the harness sends to a model and what a model may do: scrubbing, the external-model view, always-ask, registry
and variable limits, MCP output, model-client endpoint rules."""
import io
import json

import pytest

from heardos_installer.harness import agent as A
from heardos_installer.harness import cli
from heardos_installer.harness import llm as L
from heardos_installer.harness.mcp import serve as mcp_serve
from heardos_installer.harness.redact import is_local, md_text, scrub, term_text
from heardos_installer.manage import tools as T
from heardos_installer.manage.sim import SimBackend

from test_harness import Script

SECRET = "Zq8sV2kLmN4pR7tX"


class Recorder(Script):
    """The scripted model, keeping every message it was sent (what a model provider would see)."""

    def __init__(self, *a, external=False, **kw):
        super().__init__(*a, **kw)
        self.external, self.sent = external, []

    def json(self, messages, schema):
        self.sent += [m["content"] for m in messages]
        return super().json(messages, schema)


class Leaky(SimBackend):
    """A backend whose logs hold the kinds of things real logs hold."""

    def secret_values(self):
        return (SECRET,)

    def logs(self, stack, lines=100, service=""):
        return "\n".join(["INF started for alice@example.net from 8.8.8.8", f"INF db password is {SECRET}",
                          "INF GET /share?token=abc123def&page=2", "ERR database is locked (192.168.10.5)"])


def _asked(log):
    return lambda tool, args, preview, risk: (log.append(tool), True)[1]


# ---------------------------------------------------------------- scrubbing
def test_scrub_masks_secrets_and_personal_data_but_keeps_what_a_model_needs():
    t = scrub(f"DB_PASSWORD=hunter2xyz postgres://u:pw@db/x Bearer abcdefghijkl ?sig=zzz&x=1 bob@example.com "
              f"8.8.8.8 192.168.10.5 {SECRET} {'ab12' * 10} {'Xk3mP9qL2vN8rT5wY7zA1bC4'} "
              "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN", (SECRET,))     # short secrets: known values only
    for gone in ("hunter2xyz", ":pw@", "abcdefghijkl", "zzz", "bob@example.com", "8.8.8.8", SECRET, "ab12" * 10, "Xk3mP9qL2vN8rT5wY7zA1bC4", "eyJ"):
        assert gone not in t
    assert "192.168.10.5" in t and "&x=1" in t
    keep = "ghcr.io/open-webui/open-webui@sha256:" + "a" * 64 + " linuxserver/calibre-web-automated-book-downloader:latest bypass=true"
    assert scrub(keep) == keep
    assert scrub("x", ("supersecret",)) == "x" and "supersecret" not in scrub("a supersecret b", ("supersecret",))


def test_output_helpers():
    assert md_text("![a](http://x) <b>") == "\\!\\[a\\](http://x) \\<b\\>"
    assert term_text("ok\x1b[2J\x1b]0;pwned\x07 done\r") == "ok[2J]0;pwned done"


@pytest.mark.parametrize("endpoint,local", [("http://127.0.0.1:11434", True), ("http://10.42.0.11:11434", True), ("http://ollama:11434", True),
                                            ("http://box.lan:8080", True), ("https://api.openai.com/v1", False),
                                            ("https://openrouter.ai/api", False), ("https://1-2-3-4.sslip.io", False)])
def test_is_local(endpoint, local):
    assert is_local(endpoint) is local


# ---------------------------------------------------------------- the model clients
def test_clients_refuse_plain_http_to_a_third_party_and_send_keys():
    with pytest.raises(L.LLMError, match="https"):
        L.OpenAICompat("m", "http://api.example.com")
    with pytest.raises(L.LLMError, match="https"):
        L.Ollama("m", "http://ollama.example.com")
    assert L.OpenAICompat("m", "https://api.example.com/v1").external is True
    assert L.Ollama("m", "http://127.0.0.1:11434").external is False
    assert L.Ollama("m", "https://ollama.com", api_key="k-123").client.headers["Authorization"] == "Bearer k-123"
    assert "authorization" not in L.Ollama("m", "http://127.0.0.1:11434").client.headers


def test_cli_refuses_unattended_changes_for_a_third_party_model():
    with pytest.raises(SystemExit, match="allow-changes"):
        cli.main(["--sim", "--api", "openai", "--endpoint", "https://api.example.com", "--allow-changes", "hi"])


# ---------------------------------------------------------------- what the agent sends
def test_every_message_is_scrubbed():
    llm = Recorder("fix_app", [], pick="homebox")
    A.Agent(Leaky(), llm).run("inventory is broken")
    sent = "\n".join(llm.sent)
    assert "database is locked" in sent and "192.168.10.5" in sent                      # the diagnosis still gets through
    assert SECRET not in sent and "alice@example.net" not in sent and "8.8.8.8" not in sent and "abc123def" not in sent


def test_an_external_model_sees_error_lines_and_usernames_only():
    local, ext = Recorder("fix_app", [], pick="homebox"), Recorder("fix_app", [], pick="homebox", external=True)
    A.Agent(Leaky(), local).run("inventory is broken")
    A.Agent(Leaky(), ext).run("inventory is broken")
    assert "INF started" in "\n".join(local.sent) and "INF started" not in "\n".join(ext.sent)
    assert "ERR database is locked" in "\n".join(ext.sent)
    local, ext = Recorder("access", []), Recorder("access", [], external=True)
    A.Agent(SimBackend(), local).run("let bob use mealie")
    A.Agent(SimBackend(), ext).run("let bob use mealie")
    assert "Alice Smith" in "\n".join(local.sent)
    assert "Alice Smith" not in "\n".join(ext.sent) and "- alice (admin): apps:" in "\n".join(ext.sent)


def test_allow_changes_does_not_apply_to_an_external_model():
    b, asked, events = SimBackend(), [], []
    llm = Recorder("fix_app", [("control_app", {"app": "homebox", "action": "restart"})], pick="homebox", external=True)
    A.Agent(b, llm, A.Policy(allow_changes=True, confirm=_asked(asked)), on_event=lambda k, d: events.append(k)).run("inventory is broken")
    assert asked == ["control_app"] and "notice" in events and b.journal == [("restart", {"stack": "homebox"})]


def test_a_custom_app_always_asks():
    b = SimBackend()
    llm = Script("add_app", [("add_custom_app", {"name": "uptime", "image": "louislam/uptime-kuma:1", "access": "public"})])
    res = A.Agent(b, llm, A.Policy(allow_changes=True, confirm=lambda *a: False)).run("add uptime kuma")
    assert not b.journal and res.stopped == "refused"


# ---------------------------------------------------------------- what a model may do
def test_models_only_pull_from_known_registries():
    b = SimBackend()
    r = T.call(b, "inspect_image", {"image": "registry.evil.example/x:1"})
    assert not r.ok and "not a registry" in r.text
    r = T.call(b, "add_custom_app", {"name": "x", "image": "registry.evil.example:5000/x:1", "access": "admins"})
    assert not r.ok and "not a registry" in r.text and not b.journal
    assert T._untrusted("ghcr.io/gethomepage/homepage:1") == "" and T._untrusted("louislam/uptime-kuma:1") == ""


def test_models_cannot_set_variables_that_load_code_or_redirect_traffic():
    b = SimBackend()
    for key in ("LD_PRELOAD", "NODE_OPTIONS", "PYTHONSTARTUP", "HTTPS_PROXY", "JAVA_TOOL_OPTIONS", "NODE_TLS_REJECT_UNAUTHORIZED"):
        r = T.call(b, "change_setting", {"app": "mealie", "key": key, "value": "x"})
        assert not r.ok and "may not set" in r.text, key
    r = T.call(b, "add_custom_app", {"name": "uptime", "image": "louislam/uptime-kuma:1", "access": "admins", "env": ["NODE_OPTIONS=--require /x"]})
    assert not r.ok and "NODE_OPTIONS" in r.text
    assert T.call(b, "change_setting", {"app": "mealie", "key": "LOG_LEVEL", "value": "debug"}).ok
    assert not [j for j in b.journal if j[1].get("key") != "LOG_LEVEL"]


def test_mcp_results_are_scrubbed():
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "app_logs", "arguments": {"app": "homebox"}}}]
    out = io.StringIO()
    mcp_serve(Leaky(), inp=io.StringIO("\n".join(json.dumps(r) for r in reqs)), outp=out)
    text = json.loads(out.getvalue())["result"]["content"][0]["text"]
    assert "database is locked" in text and SECRET not in text and "alice@example.net" not in text and "8.8.8.8" not in text
