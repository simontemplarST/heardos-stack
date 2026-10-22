"""Stack management: custom-stack specs, settings overrides, the tool registry and both backends (no servers involved)."""
import json
from types import SimpleNamespace

import yaml

from heardos_installer.manage import tools as T
from heardos_installer.manage.backend import RealBackend, compose_env, image_facts
from heardos_installer.manage.sim import SimBackend
from heardos_installer.manage.spec import CustomStack, check_setting, render_compose, render_override, validate


def _spec(**kw):
    d = {"name": "uptime", "image": "louislam/uptime-kuma:1", "port": 3001, "access": "admins"}
    d.update(kw)
    return CustomStack.from_dict(d)


def test_custom_compose_follows_kit_conventions():
    s = _spec(volumes=["/app/data"], env=["API_SECRET=generate", "GREETING=cost $5"], memory="512m")
    doc = yaml.safe_load(render_compose(s))
    svc = doc["services"]["uptime"]
    assert svc["networks"] == ["proxy"] and doc["networks"]["proxy"]["external"] is True
    assert "ports" not in svc                                            # never published on the host
    assert svc["volumes"] == ["${HEARDOS_ROOT}/data/uptime/app-data:/app/data"]
    assert svc["environment"]["API_SECRET"] == "${API_SECRET}"          # generated on the host, never in the plan
    assert svc["environment"]["GREETING"] == "cost $$5"                  # literal $ escaped for compose
    lab = svc["labels"]
    assert lab["traefik.http.routers.custom-uptime.middlewares"] == "xfp-https@file,authentik-fwd@file"
    assert lab["traefik.http.services.custom-uptime.loadbalancer.server.port"] == "3001"
    assert svc["mem_limit"] == "512m" and s.secrets == ["API_SECRET"]


def test_public_custom_stack_has_no_sign_in_gate():
    lab = yaml.safe_load(render_compose(_spec(access="public")))["services"]["uptime"]["labels"]
    assert lab["traefik.http.routers.custom-uptime.middlewares"] == "xfp-https@file"


def test_validate_explains_each_problem():
    errs = validate(CustomStack.from_dict({"name": "Bad Name", "image": "Foo Bar", "port": "x", "access": "everyone",
                                           "volumes": ["relative", "/etc"], "env": {"DB_PASSWORD": "hunter2", "bad-key": "1"}}),
                    taken_names=set(), taken_hosts=set())
    text = " ".join(errs)
    for field in ("name:", "image:", "port:", "host:", "access:", "volumes:", "env: DB_PASSWORD looks like a secret", "env: 'bad-key'"):
        assert field in text, field
    assert validate(_spec(), taken_names={"uptime"}, taken_hosts=set())[0].startswith("name: 'uptime' is already used")
    assert "add a version tag" in " ".join(validate(_spec(image="louislam/uptime-kuma"), taken_names=set(), taken_hosts=set()))
    assert validate(_spec(), taken_names=set(), taken_hosts=set()) == []


def test_settings_override_and_secret_names():
    ov = yaml.safe_load(render_override({"mealie": {"LOG_LEVEL": "debug", "X": "a$b"}}))
    assert ov["services"]["mealie"]["environment"] == {"LOG_LEVEL": "debug", "X": "a$$b"}
    assert render_override({"mealie": {}}) is None
    assert check_setting("ALLOW_PASSWORD_LOGIN", "false") == []          # a switch, not a secret
    assert check_setting("DB_PASSWORD", "x") and check_setting("OIDC_CLIENT_SECRET", "x") and check_setting("API_KEY", "x")
    assert check_setting("log_level", "x") and check_setting("LOG_LEVEL", "a\nb")


def test_image_facts_and_compose_env():
    f = image_facts({"ExposedPorts": {"3001/tcp": {}, "53/udp": {}}, "Volumes": {"/app/data": {}}, "Env": ["PATH=/bin", "NODE_ENV=prod"]})
    assert f == {"ports": [3001], "volumes": ["/app/data"], "env": ["NODE_ENV"], "user": ""}
    assert compose_env("services:\n  a:\n    environment: [X=1, Y]\n") == {"a": {"X": "1", "Y": ""}}


def test_sim_custom_stack_fills_from_image_and_guards_names():
    b = SimBackend()
    s, msgs = b.complete_spec({"name": "uptime", "image": "louislam/uptime-kuma:1", "access": "family"})
    assert s.port == 3001 and s.volumes == ["/app/data"] and all(m.startswith("note: ") for m in msgs)
    assert "Open it at https://uptime.smithfamily.example" in b.add_custom({"name": "uptime", "image": "louislam/uptime-kuma:1"})
    _, msgs = b.complete_spec({"name": "photos", "image": "louislam/uptime-kuma:1"})   # catalogue host + taken name
    assert any("already used" in m for m in msgs)
    _, msgs = b.complete_spec({"name": "x1", "image": "made/up:1"})
    assert "could not download" in msgs[0]


def test_sim_remove_refuses_a_dependency():
    b = SimBackend()
    r = T.call(b, "remove_app", {"app": "onlyoffice"})                    # Documents needs the office editor
    assert not r.ok and "needed by" in r.text and not b.journal


def test_tool_enums_follow_the_install():
    b = SimBackend()
    inst = T.BY_NAME["control_app"].schema(b)["properties"]["app"]["enum"]
    miss = T.BY_NAME["install_app"].schema(b)["properties"]["app"]["enum"]
    assert "mealie" in inst and "mealie" not in miss and "hortusfox" in miss
    assert T.BY_NAME["give_access"].schema(b)["properties"]["person"]["enum"] == ["alice", "bob"]
    r = T.call(b, "install_app", {"app": "mealie"})
    assert not r.ok and "must be one of" in r.text                       # already installed: not in the enum
    r = T.call(b, "add_custom_app", {"name": "x", "image": "bad image", "access": "admins"})
    assert not r.ok and r.text.startswith("Cannot add it yet") and not b.journal


def test_tool_results_are_short():
    b = SimBackend()
    for name in ("overview", "list_apps", "health_check", "list_people"):
        r = T.call(b, name, {})
        assert r.ok and len(r.text) <= T.MAX_TEXT
    assert len(T.call(b, "app_logs", {"app": "homebox", "lines": 100}).text) <= 2400


class _Host:
    def __init__(self, stdout=""):
        self.cmds, self.files, self.stdout = [], {}, stdout

    def run(self, cmd, **k):
        self.cmds.append(cmd)
        return SimpleNamespace(ok=True, stdout=self.stdout, stderr="")

    def tempdir(self):
        return "/tmp/x"

    def put_text(self, text, path, mode=0o600):
        self.files[path] = text


def _real(tmp_path, monkeypatch, host):
    from heardos_installer.session import Installation
    from heardos_installer.vault import Vault
    monkeypatch.setenv("HEARDOS_HOME", str(tmp_path))
    v = Vault(tmp_path / "v.json"); v.create("x" * 12)
    inst = Installation("m-1", v, {"DOMAIN": "example.com", "ADMIN_USER": "alice", "ADMIN_EMAIL": "a@example.com",
                                   "HOST_LAN_IP": "192.168.10.50", "provider": "cloudzy", "profile": "family"})
    inst.sessions._host = host
    return RealBackend(inst)


def test_real_set_setting_pushes_override_and_redeploys(tmp_path, monkeypatch):
    from heardos_installer import ops
    from heardos_installer.engine.step import StepResult
    host = _Host()
    b = _real(tmp_path, monkeypatch, host)
    calls = []
    monkeypatch.setattr(ops, "_kit", lambda inst, name, script, args=(), **k: calls.append((script, list(args))) or StepResult(True))
    msg = b.set_setting("mealie", "LOG_LEVEL", "debug")
    assert "redeployed" in msg and calls == [("52-redeploy.sh", ["mealie"])]
    assert yaml.safe_load(host.files["/tmp/x/compose.override.yml"])["services"]["mealie"]["environment"] == {"LOG_LEVEL": "debug"}
    assert b.plan["stack_settings"] == {"mealie": {"mealie": {"LOG_LEVEL": "debug"}}}
    b.set_setting("mealie", "LOG_LEVEL", None)
    assert b.plan["stack_settings"] == {} and any("rm -f" in c and "compose.override.yml" in c for c in host.cmds)


def test_real_containers_parse_docker_json(tmp_path, monkeypatch):
    line = json.dumps({"Names": "mealie", "Image": "ghcr.io/mealie:v3", "State": "running", "Status": "Up 2 hours",
                       "Labels": "a=b,com.docker.compose.service=mealie,c=d"})
    b = _real(tmp_path, monkeypatch, _Host(line + "\nnot json\n"))
    assert b.containers("mealie") == [{"name": "mealie", "image": "ghcr.io/mealie:v3", "state": "running", "status": "Up 2 hours", "service": "mealie"}]


def test_custom_stacks_join_the_plan_and_the_dag(tmp_path, monkeypatch):
    from heardos_installer.steps import apply_defaults, build_steps
    from heardos_installer.steps.custom import custom_files
    plan = apply_defaults({"DOMAIN": "example.com", "HOST_LAN_IP": "192.168.10.50", "profile": "essentials",
                           "custom_stacks": {"uptime": _spec(host="status", env={"API_SECRET": "generate"}).to_dict()}})
    assert "status" in plan["PUBLIC_HOSTS"]
    steps = {s.id: s for s in build_steps(plan)}
    assert "custom.uptime" in steps and "custom.uptime" in steps["verify"].deps
    files = custom_files(plan, "uptime")
    meta = json.loads(files["custom.json"])
    assert meta == {"title": "Uptime", "host": "status", "access": "admins", "secrets": ["API_SECRET"], "dirs": []}
    assert files["compose.override.yml"] is None
