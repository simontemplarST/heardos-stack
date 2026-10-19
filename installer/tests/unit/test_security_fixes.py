"""Regression tests for the security review fixes (H-5, H-6, M-7, M-8, M-18, M-6, L-14) — fakes only, no servers."""
import json, subprocess, time
from types import SimpleNamespace

import httpx, paramiko, pytest, respx

from heardos_installer.engine import InstallState, Runner, Step, StepResult, StepStatus
from heardos_installer.engine.log import LogHub
from heardos_installer.engine.step import Ctx
from heardos_installer.remote import ssh
from heardos_installer.remote.ssh import HostKeyError, Session, SshError
from heardos_installer.vault.redact import redact_text


class FakeVault:
    def __init__(self): self.d = {}
    def update(self, v): self.d.update(v)
    def get(self, k, default=None): return self.d.get(k, default)
    def values(self): return list(self.d.values())


# ------------------------------------------------------------------ a scripted paramiko channel
class FakeChan:
    def __init__(self, respond, log):
        self.respond, self.log = respond, log
        self.out = self.err = b""
        self.rc = 0

    def settimeout(self, t): pass

    def exec_command(self, cmd):
        self.log.append(cmd)
        out, err, self.rc = self.respond(cmd)
        self.out, self.err = out.encode(), err.encode()

    def recv_ready(self): return bool(self.out)
    def recv(self, n): b, self.out = self.out[:n], self.out[n:]; return b
    def recv_stderr_ready(self): return bool(self.err)
    def recv_stderr(self, n): b, self.err = self.err[:n], self.err[n:]; return b
    def exit_status_ready(self): return True
    def recv_exit_status(self): return self.rc
    def close(self): pass


def fake_session(respond, user="root"):
    cmds, puts = [], []
    s = Session("192.0.2.10", user)
    transport = SimpleNamespace(is_active=lambda: True, open_session=lambda: FakeChan(respond, cmds), set_keepalive=lambda n: None)
    s._client = SimpleNamespace(get_transport=lambda: transport)
    s.put_text = lambda text, remote, mode=0o600: puts.append((text, remote, mode))
    return s, cmds, puts


def responder(main_out="", main_err="", rc=0):
    def respond(cmd):
        if "mktemp -d" in cmd and "set -a" not in cmd:
            return "/root/.heardos-AbCd1234\n", "", 0
        return main_out, main_err, rc
    return respond


# ------------------------------------------------------------------ H-5: HEARDOS_OUTPUT values never reach the log
def test_output_lines_masked_in_stream_but_kept_for_outputs(tmp_path):
    tok = "ak-bootstrap-token-0123456789"
    s, _, _ = fake_session(responder(f"starting\nHEARDOS_OUTPUT AUTHENTIK_TOKEN={tok}\ndone\n"))
    seen = []
    res = s.run("./40-authentik-stack.sh", on_line=seen.append)
    assert res.outputs()["AUTHENTIK_TOKEN"] == tok                 # the installer still gets the value
    assert "HEARDOS_OUTPUT AUTHENTIK_TOKEN=***" in seen and not any(tok in l for l in seen)


def test_value_only_added_to_vault_afterwards_is_not_in_the_log_file():
    tok = "fresh-token-the-vault-has-not-seen"
    v = FakeVault(); hub = LogHub("h5", v.values); lg = hub.logger("host.authentik")
    s, _, _ = fake_session(responder(f"HEARDOS_OUTPUT AUTHENTIK_TOKEN={tok}\n"))
    s.run("./40-authentik-stack.sh", on_line=lg.info)
    v.update({"AUTHENTIK_TOKEN": tok})                              # what the runner does after the step
    for h in lg.handlers: h.flush()
    assert tok not in hub.path("host.authentik").read_text()


# ------------------------------------------------------------------ H-6: env values via a 0600 file, never argv
def test_env_goes_through_a_private_file_not_the_command_line(tmp_path):
    secret = "pa ss'wo\"rd $(reboot) `x`\nline2"
    s, cmds, puts = fake_session(responder("ok\n"), user="admin")
    s.run("./script.sh", sudo=True, env={"AUTHENTIK_TOKEN": secret, "HEARDOS_ENV": "/opt/heardos-kit/generic/heardos.env"})
    main = cmds[-1]
    assert secret not in main and "AUTHENTIK_TOKEN" not in main and "env " not in main.split("bash -lc")[0]
    assert main.startswith("sudo -n -H bash -lc ")
    (text, path, mode), = puts
    assert mode == 0o600 and path == "/root/.heardos-AbCd1234/env"
    assert "rm -rf /root/.heardos-AbCd1234" in main and "set -a; . /root/.heardos-AbCd1234/env; set +a" in main
    f = tmp_path / "env"; f.write_text(text)                        # the file sources back to exactly the value
    got = subprocess.run(["bash", "-c", f'set -a; . "{f}"; set +a; printf %s "$AUTHENTIK_TOKEN"'], capture_output=True, text=True).stdout
    assert got == secret


def test_env_names_are_validated():
    s, _, _ = fake_session(responder())
    with pytest.raises(ValueError):
        s.run("true", env={"BAD;NAME": "x"})


def test_portainer_password_only_in_a_json_file(monkeypatch):
    from heardos_installer.steps import host
    s, cmds, puts = fake_session(responder("created\n"))
    v = FakeVault(); v.update({"PORTAINER_ADMIN_PASSWORD": "pw'with\"quotes"})
    ctx = SimpleNamespace(vault=v, plan={"HOST_LAN_IP": "192.168.1.5"}, sessions=SimpleNamespace(host=lambda: s))
    res = host.portainer_init(ctx)
    assert res.ok and not any("pw'with" in c for c in cmds)
    body, = [t for t, p, m in puts if p.endswith("body.json") and m == 0o600]
    assert json.loads(body) == {"Username": "admin", "Password": "pw'with\"quotes"}
    assert "-d @/root/.heardos-AbCd1234/body.json" in cmds[-1] and "/tmp/" not in cmds[-1]


# ------------------------------------------------------------------ M-18: failure messages redacted before the state file
def test_failed_step_message_is_redacted_in_state_and_listeners():
    st = InstallState("m18"); v = FakeVault(); v.update({"CF_API_TOKEN": "cf-token-1234567890"})
    heard = []
    def bad(c):
        return StepResult(False, secrets={"NEW_PW": "brand-new-password-xyz"},
                          message="boom cf-token-1234567890 / HEARDOS_OUTPUT AUTHENTIK_TOKEN=tok-abcdefghijkl / brand-new-password-xyz")
    hub = LogHub("m18", v.values)
    r = Runner([Step("bad", "local", bad)], lambda s: Ctx(st.plan, v, None, hub.logger(s.id), st, None, None), st, v,
               listeners=[lambda sid, status, msg: heard.append(msg)])
    assert r.run() is False and st.status("bad") == StepStatus.failed
    text = st.path.read_text()
    for leak in ("cf-token-1234567890", "tok-abcdefghijkl", "brand-new-password-xyz"):
        assert leak not in text and not any(leak in m for m in heard)
    assert "AUTHENTIK_TOKEN=***" in st.step("bad")["message"]


def test_ssh_error_has_no_raw_stderr():
    s, _, _ = fake_session(responder("", "line1\nfailed with token=envsecret-1234\nHEARDOS_OUTPUT X=abcdefgh\n", rc=3))
    with pytest.raises(SshError) as e:
        s.run("./x.sh", env={"TOK": "envsecret-1234"}, check=True)
    assert "envsecret-1234" not in str(e.value) and "abcdefgh" not in str(e.value) and "X=***" in str(e.value)


def test_redact_masks_ovh_credential_fields():
    creds = json.dumps({"endpoint": "us", "app_key": "appkey-123456", "app_secret": "appsecret-abcdef", "consumer_key": "ck-0123456789"})
    out = redact_text("secret=appsecret-abcdef ck=ck-0123456789", [creds])
    assert "appsecret-abcdef" not in out and "ck-0123456789" not in out


# ------------------------------------------------------------------ M-8: Vultr root password goes to the vault
@respx.mock
def test_vultr_root_password_routed_to_vault_not_state(monkeypatch):
    from heardos_installer.providers import Vultr
    from heardos_installer.steps import local
    b = "https://api.vultr.com/v2"
    respx.get(f"{b}/ssh-keys").respond(json={"ssh_keys": [{"id": "k1", "ssh_key": "ssh-ed25519 AAAAabc x"}], "meta": {"links": {"next": ""}}})
    respx.get(f"{b}/os").respond(json={"os": [{"id": 2284, "name": "Ubuntu 24.04 LTS x64"}], "meta": {"links": {"next": ""}}})
    respx.post(f"{b}/instances").respond(json={"instance": {"id": "i-1", "label": "lh", "default_password": "vultr-root-pw-XYZ123"}})
    respx.get(f"{b}/instances/i-1").respond(json={"instance": {"status": "active", "main_ip": "203.0.113.9"}})
    monkeypatch.setattr(local, "provider_for", lambda ctx: Vultr("t"))
    monkeypatch.setattr(local, "ensure_key", lambda p: (p, "ssh-ed25519 AAAAabc x"))
    st = InstallState("m8", {"provider": "vultr", "DOMAIN": "example.com", "vps_region": "ord", "vps_plan": "vc2-1c-1gb"})
    v = FakeVault(); hub = LogHub("m8", v.values)
    r = Runner([Step("vps.create", "local", local.vps_create)], lambda s: Ctx(st.plan, v, None, hub.logger(s.id), st, None, None), st, v)
    assert r.run()
    assert v.get("VPS_ROOT_PASSWORD") == "vultr-root-pw-XYZ123"
    assert "vultr-root-pw-XYZ123" not in st.path.read_text() and st.handle("vps")["id"] == "i-1"


# ------------------------------------------------------------------ M-7: host keys
def test_wait_ssh_does_not_retry_a_changed_host_key(monkeypatch):
    calls = []
    def connect(self):
        calls.append(1)
        raise HostKeyError("host key for x changed")
    monkeypatch.setattr(Session, "connect", connect)
    t0 = time.time()
    with pytest.raises(HostKeyError):
        ssh.wait_ssh("192.0.2.1", timeout=600, interval=5)
    assert len(calls) == 1 and time.time() - t0 < 2


def test_ensure_host_key_pins_only_after_confirmation(monkeypatch):
    key = paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(ssh, "fetch_host_key", lambda host, port=22, timeout=10: key)
    asked = []
    with pytest.raises(HostKeyError):
        ssh.ensure_host_key("192.0.2.7", 22, lambda h, fp: asked.append(fp) or False)
    assert ssh.pinned_host_key("192.0.2.7") is None and asked[0].startswith("SHA256:")
    ssh.ensure_host_key("192.0.2.7", 22, lambda h, fp: asked.append(fp) or True)
    assert ssh.pinned_host_key("192.0.2.7").asbytes() == key.asbytes()
    ssh.ensure_host_key("192.0.2.7", 22, lambda h, fp: pytest.fail("already pinned: must not ask again"))


def test_plan_fingerprint_is_compared_without_asking():
    from heardos_installer.steps.common import host_key_confirm
    ctx = SimpleNamespace(plan={"HOST_SSH_FINGERPRINT": "SHA256:abc"}, log=lambda l: None,
                          checklist=lambda *a: pytest.fail("should not prompt"))
    c = host_key_confirm(ctx, "home server", "HOST_SSH_FINGERPRINT")
    assert c("h", "SHA256:abc") is True and c("h", "SHA256:other") is False


def test_plan_value_validation():
    from heardos_installer.steps.common import overlay_ip, ssh_user
    assert overlay_ip({"OVERLAY_CIDR": "10.42.0.0/24", "X": "10.42.0.1"}, "X") == "10.42.0.1"
    for bad in ("10.42.0.1; reboot", "10.43.0.1"):
        with pytest.raises(ValueError):
            overlay_ip({"OVERLAY_CIDR": "10.42.0.0/24", "X": bad}, "X")
    assert ssh_user({"HOST_SSH_USER": "ubuntu"}) == "ubuntu"
    with pytest.raises(ValueError):
        ssh_user({"HOST_SSH_USER": "root; id"})


# ------------------------------------------------------------------ M-6: Nebula downloads are checksummed
def test_nebula_download_checksum_and_no_latest(monkeypatch):
    from heardos_installer import nebula
    monkeypatch.setenv("HEARDOS_NEBULA_VERSION", "latest")
    with pytest.raises(RuntimeError, match="no longer supported"):
        nebula._latest_version()
    monkeypatch.delenv("HEARDOS_NEBULA_VERSION")

    class R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(nebula.urllib.request, "urlopen", lambda url, timeout=0: R(b"tampered tarball"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        nebula.fetch("linux", "amd64", None, log=lambda l: None)
    assert not (nebula.bin_dir(nebula.PINNED_VERSION) / "linux-amd64" / "nebula").exists()


# ------------------------------------------------------------------ L-14: no secret values on argv
def test_cli_secret_refuses_argv_value_and_reads_stdin(monkeypatch):
    from heardos_installer import __main__ as m
    stored = {}
    monkeypatch.setattr(m, "_vault", lambda: SimpleNamespace(set=lambda k, v: stored.__setitem__(k, v)))
    with pytest.raises(SystemExit):
        m.cli(["secret", "CF_API_TOKEN", "on-the-command-line"])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("from-stdin-value\n"))
    m.cli(["secret", "CF_API_TOKEN", "--value-stdin"])
    assert stored == {"CF_API_TOKEN": "from-stdin-value"}


def test_nebula_render_per_role_rules_and_blocklist():
    # M-9: the home host takes admins on any port + the lighthouse on 80/tcp; other nodes only ICMP; revoked certs blocked
    from pathlib import Path
    from heardos_installer import nebula
    from heardos_installer.kit import catalog
    root = Path(catalog.__file__).resolve()
    while not (root / "generic" / "lib" / "nebula-node.yml.tmpl").exists():
        root = root.parent
    plan = {"OVERLAY_CIDR": "10.42.0.0/24", "NEBULA_LH_OVERLAY": "10.42.0.1", "NEBULA_LH_IP": "203.0.113.10",
            "NEBULA_BLOCKLIST": "abc123 def456"}
    host = nebula.render_config(root, "host", plan, lighthouse=False, ingress=True)
    laptop = nebula.render_config(root, "laptop", plan, lighthouse=False, ingress=False)
    assert "group: admins" in host and "group: lighthouse" in host
    assert "group: admins" not in laptop.split("inbound:")[-1] and "group: lighthouse" not in laptop.split("inbound:")[-1]
    assert "  blocklist:\n    - abc123\n    - def456" in host
