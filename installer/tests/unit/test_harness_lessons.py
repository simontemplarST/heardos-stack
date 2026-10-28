"""Harness behaviour learned from real incidents (2026-10-28): changes are checked, not assumed; loosening a protection needs a
person; repeated log noise is collapsed; a busy server is named as the cause; provider errors that echo keys are scrubbed."""
import io
import json

from heardos_installer.harness import agent as A
from heardos_installer.harness.mcp import serve
from heardos_installer.harness.redact import scrub
from heardos_installer.manage import tools as T
from heardos_installer.manage.sim import SimBackend
from heardos_installer.manage.spec import weakens_security
from heardos_installer.ops import parse_load

from test_harness import Script


class Recording(Script):
    """Script that also counts the harness's "is the task done?" questions."""
    def json(self, messages, schema):
        if "done" in schema["properties"] and "missing" in schema["properties"]:
            self.done_questions = getattr(self, "done_questions", 0) + 1
        return super().json(messages, schema)


class Clock:
    """A fake clock: sleeping advances it, so waits in the harness cost no real time."""
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


def agent(b, llm, pol, **kw):
    c = Clock()
    return A.Agent(b, llm, pol, sleep=c.sleep, clock=c.now, **kw), c


# ---------------------------------------------------------------- checked, not assumed
def test_a_change_that_fixes_the_app_is_checked_and_marked():
    b = SimBackend()                                          # homebox starts unhealthy; a restart makes it running
    a, _ = agent(b, Script("fix_app", [("control_app", {"app": "homebox", "action": "restart"})]), A.Policy(allow_changes=True))
    res = a.run("restart homebox")
    t = next(t for t in res.turns if t.tool == "control_app")
    assert t.verified is True and "Checked afterwards: homebox is running" in t.text
    assert "[checked: as expected]" in res.report()


def test_a_change_that_leaves_the_app_down_is_never_done():
    b = SimBackend()                                          # a setting does not cure homebox: it stays unhealthy
    llm = Recording("setting", [("change_setting", {"app": "homebox", "key": "LOG_LEVEL", "value": "debug"})], done="yes")
    a, clock = agent(b, llm, A.Policy(allow_changes=True))
    res = a.run("turn on debug logging for homebox")
    t = next(t for t in res.turns if t.tool == "change_setting")
    assert t.verified is False and "is NOT running" in t.text and "app_logs" in t.text
    assert clock.t >= A.VERIFY_WAIT                            # it waited for the app to settle before saying so
    assert getattr(llm, "done_questions", 0) == 0               # a failed check skips "done?": the model can't call it finished
    assert "NOT as expected" in res.report()


def test_stopping_an_app_is_checked_against_stopped():
    b = SimBackend()
    a, _ = agent(b, Script("start_stop", [("control_app", {"app": "mealie", "action": "stop"})]), A.Policy(allow_changes=True))
    t = next(t for t in a.run("stop mealie").turns if t.tool == "control_app")
    assert t.verified is True and "mealie is exited" in t.text


# ---------------------------------------------------------------- loosening a protection needs a person
def test_security_settings_are_classified_with_a_plain_reason():
    for key in ("ALLOW_PRIVATE_IP_ADDRESS", "PAPERLESS_WEBHOOKS_ALLOW_INTERNAL_REQUESTS", "OC_INSECURE", "ALLOW_SIGNUP",
                "TRUSTED_PROXIES", "PAPERLESS_ALLOWED_HOSTS", "DJANGO_DEBUG", "ADMIN_PASSWORD"):
        assert weakens_security(key), key
    for key in ("LOG_LEVEL", "TZ", "DEBUG_LEVEL", "MAX_UPLOAD_SIZE", "BASE_URL", "REGISTRY_URL", "SESSION_TIMEOUT"):
        assert not weakens_security(key), key
    assert "home network" in weakens_security("ALLOW_PRIVATE_IP_ADDRESS")


def test_loosening_a_protection_asks_even_with_changes_allowed():
    b = SimBackend()
    asked = []
    pol = A.Policy(allow_changes=True, confirm=lambda tool, args, prev, risk: asked.append(prev) or False)
    llm = Script("setting", [("change_setting", {"app": "mealie", "key": "ALLOW_SIGNUP", "value": "true"})])
    res = agent(b, llm, pol)[0].run("let people sign up to mealie")
    assert len(asked) == 1 and asked[0].startswith("SECURITY: ALLOW_SIGNUP changes who can sign in")
    assert not b.journal and res.stopped == "refused"
    # an ordinary setting still runs unattended
    b, asked = SimBackend(), []
    llm = Script("setting", [("change_setting", {"app": "mealie", "key": "LOG_LEVEL", "value": "debug"})])
    agent(b, llm, pol)[0].run("debug logging for mealie")
    assert asked == [] and b.journal


def test_resetting_a_protection_also_asks():
    assert T.needs_person("reset_setting", {"app": "mealie", "key": "ALLOW_SIGNUP"})
    assert not T.needs_person("reset_setting", {"app": "mealie", "key": "LOG_LEVEL"})
    assert not T.needs_person("control_app", {"app": "mealie", "action": "restart"})


def test_mcp_never_loosens_a_protection_even_with_changes_allowed():
    b = SimBackend()
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "change_setting", "arguments": {"app": "mealie", "key": "ALLOW_SIGNUP", "value": "true"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "change_setting", "arguments": {"app": "mealie", "key": "LOG_LEVEL", "value": "debug"}}}]
    out = io.StringIO()
    serve(b, allow_changes=True, inp=io.StringIO("\n".join(json.dumps(r) for r in reqs)), outp=out)
    res = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}
    assert res[1]["result"]["isError"] and "only a person" in res[1]["result"]["content"][0]["text"]
    assert not res[2]["result"]["isError"]
    assert [j for j in b.journal if j[1].get("key") == "ALLOW_SIGNUP"] == []


# ---------------------------------------------------------------- logs: repeated noise collapsed
def test_log_digest_surfaces_the_one_real_error_under_noise():
    noise = [f'{{"level":"error","service":"postprocessing","time":"2026-10-28T19:{i // 60:02d}:{i % 60:02d}Z",'
             f'"message":"cannot get upload","uploadID":"{i:08x}{i:08x}"}}' for i in range(1600)]
    real = '{"level":"error","service":"proxy","time":"2026-10-28T19:45:19Z","message":"Could not load roles","code":408}'
    body, distinct, errs = T.log_digest("\n".join(noise[:800] + [real] + noise[800:]), 20)
    assert distinct == 2 and errs == 2
    assert "1600x" in body and "Could not load roles" in body
    assert len(body.splitlines()) <= 6                         # not 1,601 lines


def test_app_logs_reports_distinct_messages():
    r = T.call(SimBackend(), "app_logs", {"app": "homebox", "lines": 20})
    assert r.ok and "different messages" in r.text.splitlines()[0]


# ---------------------------------------------------------------- a busy server is named
def test_load_parsing_flags_a_saturated_disk():
    busy = parse_load("28.67 26.74 20.04 3/900 123\n8\nsome avg10=94.04 avg60=94.80 avg300=93.20 total=1\n"
                      "some avg10=5.00 avg60=4.10 avg300=3.00 total=1\n")
    calm = parse_load("0.40 0.30 0.20 1/200 9\n8\nsome avg10=0.00 avg60=0.50 avg300=0.40 total=1\n")
    assert busy["busy"] and busy["io_pct"] > 90 and not calm["busy"]
    assert parse_load("garbage")["busy"] is None


def test_app_status_names_a_busy_server_as_the_likely_cause():
    b = SimBackend()
    b.load = lambda: {"busy": True, "summary": "load 28.7 on 8 cores; disk-waits 95% and cpu-waits 4% of the last minute"}
    r = T.call(b, "app_status", {"app": "mealie"})
    assert "disk-waits 95%" in r.text and "ANY app" in r.text
    r = T.call(SimBackend(), "app_status", {"app": "mealie"})        # a backend without load() still works
    assert r.ok and "Server:" not in r.text


# ---------------------------------------------------------------- provider errors that echo a key
def test_a_provider_error_echoing_the_key_is_scrubbed():
    msg = ("Sorry, ability to send URLs via text is limited to verified accounts. Please go to "
           "https://textbelt.com/whitelist?key=0f1e2d3c4b5a69780f1e2d3c4b5a69780f1e2d3c4b5a69780f1e2d3c4b5a6978X or email support")
    out = scrub(msg, ())
    assert "0f1e2d3c" not in out and "whitelist?key=" in out


# ---------------------------------------------------------------- picks are checked against facts (qwen2.5:7b evals, 2026-10-28)
class NoFit(Script):
    """A model that picks the nearest app, then admits it is not what was asked for."""
    def json(self, messages, schema):
        if "fits" in schema["properties"]:
            return {"thought": "a blog is not a game server", "fits": "no"}
        return super().json(messages, schema)


def test_a_nearest_but_wrong_catalogue_app_is_not_installed():
    b = SimBackend()
    llm = NoFit("add_app", [("search_images", {"query": "minecraft"})], pick="blog")
    res = agent(b, llm, A.Policy(allow_changes=True))[0].run("Install a Minecraft server for the kids.")
    assert not [j for j in b.journal if j[0] == "install_app"]
    assert any(t.tool == "search_images" for t in res.turns)           # went on to look for an image instead


def test_an_app_named_in_the_task_skips_the_fit_question():
    b = SimBackend()
    llm = NoFit("add_app", [], pick="navidrome")                      # would say "no" if it were asked
    agent(b, llm, A.Policy(allow_changes=True))[0].run("Install navidrome")
    assert ("install_app", {"stack": "navidrome"}) in [(o, {"stack": a.get("stack")}) for o, a in b.journal]


class Reclassify(Script):
    """Sorts the request as start_stop first; asked again (without the app-focused kinds), says add_app."""
    def json(self, messages, schema):
        p = schema["properties"]
        if "intent" in p:
            return {"thought": "sort", "intent": "start_stop" if "start_stop" in p["intent"]["enum"] else "add_app"}
        return super().json(messages, schema)


def test_a_request_about_no_installed_app_is_sorted_again_and_never_touches_one():
    b = SimBackend()
    llm = Reclassify("unused", [("control_app", {"app": "homebox", "action": "restart"})], pick="hortusfox")
    res = agent(b, llm, A.Policy(allow_changes=True))[0].run("We keep forgetting to water the plants. Set something up.")
    assert [o for o, _ in b.journal] == ["install_app"] and b.journal[0][1]["stack"] == "hortusfox"
    assert not any(t.tool == "control_app" for t in res.turns)


def test_access_is_decided_from_the_people_list_not_the_model():
    b = SimBackend()                                                   # bob has immich + nextcloud, not mealie
    res = agent(b, Script("access", [], pick="mealie"), A.Policy(allow_changes=True))[0].run("Let Bob use the recipe app.")
    assert b.journal == [("give_access", {"username": "bob", "stack": "mealie"})]
    b = SimBackend()
    res = agent(b, Script("access", [], pick="immich"), A.Policy(allow_changes=True))[0].run("Let Bob Smith use the photos.")
    assert not b.journal and "already" in res.answer


def test_named_person_matches_username_first_or_full_name_once():
    ppl = SimBackend().people()
    assert A._named_person("let Bob use it", ppl) == "bob"
    assert A._named_person("give alice smith the recipes", ppl) == "alice"
    assert A._named_person("let bob and alice use it", ppl) is None    # two people: the model has to work it out
    assert A._named_person("let my brother use it", ppl) is None
