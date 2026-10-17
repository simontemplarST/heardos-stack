"""Steps on the home server: first contact, kit upload, Docker bootstrap, overlay node, Traefik, Authentik."""
from __future__ import annotations

import ipaddress
import secrets
import string

from ..engine.step import Ctx, Step, StepResult
from ..keys import DEFAULT_KEY, ensure_key
from ..remote.push import push_kit
from ..remote.ssh import Session, SshError
from .common import KIT_DEST, run_kit


def host_probe(ctx: Ctx) -> StepResult:
    """Key login if it already works; otherwise password once, install our key, then insist on key login."""
    p = ctx.plan
    ip, user, port = p["HOST_LAN_IP"], p.get("HOST_SSH_USER", "root"), int(p.get("HOST_SSH_PORT", 22))
    _, pub = ensure_key(DEFAULT_KEY)
    try:
        s = Session(ip, user, port, key_path=DEFAULT_KEY).connect()
        ctx.log("key login works")
    except SshError:
        pw = ctx.vault.get("HOST_SSH_PASSWORD")
        if not pw:
            return StepResult(False, message=f"cannot log in to {user}@{ip} with the installer key and no password was given")
        Session(ip, user, port, password=pw).connect().install_pubkey(pub)
        ctx.log("installed the installer key using the password")
        s = Session(ip, user, port, key_path=DEFAULT_KEY).connect()
    if not s.can_sudo():
        return StepResult(False, message=f"{user} cannot use sudo without a password; use root or allow passwordless sudo")
    facts = s.run(". /etc/os-release; echo \"$ID $VERSION_ID\"; uname -m; "
                  "dev=$(ip -4 route get 1.1.1.1 | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p'); ip -4 -o addr show dev $dev | awk '{print $4}' | head -1; "
                  "test -e /dev/net/tun && echo tun || echo notun; nproc; free -m | awk '/Mem:/{print $2}'", check=True).stdout.split()
    os_id, ver, arch, cidr, tun, cpus, mem = facts[:7]
    ctx.log(f"home server: {os_id} {ver}, {arch}, {cidr}, {cpus} CPU, {mem} MB RAM, /dev/net/tun {'present' if tun == 'tun' else 'MISSING'}")
    if os_id != "ubuntu" or not ver.startswith("24.04"):
        if not p.get("allow_other_os"):
            return StepResult(False, message=f"home server runs {os_id} {ver}; HeardOS needs Ubuntu 24.04")
    if tun != "tun":
        return StepResult(False, message="the home server has no /dev/net/tun (for a Proxmox container, enable the TUN device)")
    out = {"HOST_ARCH": arch, "HOST_CPUS": int(cpus), "HOST_MEM_MB": int(mem)}
    if not p.get("LAN_SUBNET"):
        out["LAN_SUBNET"] = str(ipaddress.ip_interface(cidr).network)
    ctx.sessions._host = s  # noqa: SLF001
    return StepResult(True, outputs=out, message=f"home server ready ({os_id} {ver}, {cpus} CPU, {int(mem)//1024} GB)")


def host_push_kit(ctx: Ctx) -> StepResult:
    s = ctx.sessions.host()
    push_kit(s, ctx.kit_root, KIT_DEST, sudo=s.user != "root")
    return StepResult(True, message="setup scripts copied to the home server")


def host_bootstrap(ctx: Ctx) -> StepResult:
    return run_kit(ctx, "10-host-bootstrap.sh", timeout=2400)


def portainer_init(ctx: Ctx) -> StepResult:
    """Portainer locks itself if no admin is created within 5 minutes of first start; do it right away via its API."""
    pw = ctx.vault.get("PORTAINER_ADMIN_PASSWORD") or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
    s = ctx.sessions.host()
    url = f"https://{ctx.plan['HOST_LAN_IP']}:9443"
    body = '{"Username":"admin","Password":"%s"}' % pw
    # Portainer >= 2.45 also wants the one-time setup token it prints in its log at start-up (X-Setup-Token)
    r = s.run(f"for i in $(seq 1 30); do st=$(docker logs portainer 2>&1 | grep -o 'setup_token=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2); "
              f"code=$(curl -sk -o /tmp/pinit -w '%{{http_code}}' -X POST {url}/api/users/admin/init "
              f"-H \"X-Setup-Token: $st\" -H 'Content-Type: application/json' -d {repr(body)}); [ \"$code\" = 200 ] && echo created && break; "
              f"[ \"$code\" = 409 ] && echo exists && break; sleep 2; done; rm -f /tmp/pinit", sudo=True, timeout=120)
    state = r.stdout.strip().splitlines()[-1:] or [""]
    if state[0] == "created":
        return StepResult(True, secrets={"PORTAINER_ADMIN_PASSWORD": pw}, message="Portainer admin created")
    if state[0] == "exists":
        return StepResult(True, message="Portainer admin already set")
    return StepResult(False, message="Portainer did not answer; it may have locked itself (restart the portainer container and retry)")


def host_nebula(ctx: Ctx) -> StepResult:
    from ..nebula import CA, install_node, render_config
    s = ctx.sessions.host()
    ca = CA.open(ctx.state.install_id, log=ctx.log)
    cfg = render_config(ctx.kit_root, "host", ctx.plan, lighthouse=False, ingress=True)
    install_node(s, ca, "host", cfg, ctx.plan.get("HOST_ARCH", "x86_64"), ctx.kit_root, ctx.log)
    r = s.run(f"ping -c 3 -W 3 {ctx.plan['NEBULA_LH_OVERLAY']} >/dev/null && echo ok || echo fail", timeout=60)
    if "ok" not in r.stdout:
        return StepResult(False, message="home server cannot reach the VPS over the private network yet")
    return StepResult(True, message="home server joined the private network")


def host_edge(ctx: Ctx) -> StepResult:
    return run_kit(ctx, "31-edge-stack.sh", timeout=900)


def host_authentik(ctx: Ctx) -> StepResult:
    res = run_kit(ctx, "40-authentik-stack.sh", timeout=1500)
    if res.ok:
        tok = res.outputs.pop("AUTHENTIK_TOKEN", None)
        if tok:
            res.secrets["AUTHENTIK_TOKEN"] = tok
        res.message = "sign-in service (Authentik) running"
    return res


def authentik_bootstrap(ctx: Ctx) -> StepResult:
    pw_file = "/root/.heardos-admin-password"
    res = run_kit(ctx, "41-authentik-bootstrap.py", env={"HEARDOS_ADMIN_PW_FILE": pw_file}, timeout=600)
    if not res.ok:
        return res
    s = ctx.sessions.host()
    r = s.run(f"cat {pw_file} 2>/dev/null && rm -f {pw_file}", sudo=True)
    if r.stdout.strip():
        res.secrets["ADMIN_PASSWORD"] = r.stdout.strip()
    res.outputs.pop("ADMIN_PASSWORD_FILE", None)
    res.message = "admin account, passkeys and brute-force protection set up"
    return res


def authentik_flows(ctx: Ctx) -> StepResult:
    env = {"AUTHENTIK_URL": ctx.plan["AUTHENTIK_URL"], "AUTHENTIK_TOKEN": ctx.vault.get("AUTHENTIK_TOKEN") or ""}
    for script in ("recovery-flow.py", "business-flows.py"):
        r = run_kit(ctx, script, env=env, cwd_dir="authentik", timeout=600)
        if not r.ok:
            return r
    return StepResult(True, message="password recovery and group-based sign-in rules set up")


def steps() -> list[Step]:
    return [
        Step("host.probe", "host", host_probe, deps=["ssh.keygen"], title="Connect to the home server", inputs=["HOST_SSH_PASSWORD"], timeout=120),
        Step("host.push_kit", "host", host_push_kit, deps=["host.probe"], title="Copy the setup scripts to the home server"),
        Step("host.bootstrap", "host", host_bootstrap, deps=["host.push_kit"], title="Install Docker and secure the home server",
             serial_group="host-apt", timeout=2400, retries=2),
        Step("portainer.init", "host", portainer_init, deps=["host.bootstrap"], title="Lock down the container manager",
             inputs=["PORTAINER_ADMIN_PASSWORD"], timeout=180),
        Step("host.nebula", "host", host_nebula, deps=["host.bootstrap", "nebula.ca", "vps.nebula"], title="Join the home server to the private network",
             serial_group="host-apt", retries=2),
        Step("host.edge", "host", host_edge, deps=["host.bootstrap"], title="Start the traffic router", serial_group="host-apps"),
        Step("host.authentik", "host", host_authentik, deps=["host.edge"], title="Start the sign-in service", serial_group="host-apps",
             inputs=["AUTHENTIK_TOKEN"], timeout=1800),
        Step("authentik.bootstrap", "host", authentik_bootstrap, deps=["host.authentik"], title="Create the admin account",
             inputs=["AUTHENTIK_TOKEN"], timeout=900),
        Step("authentik.flows", "host", authentik_flows, deps=["authentik.bootstrap"], title="Set up recovery and sign-in rules",
             inputs=["AUTHENTIK_TOKEN"], optional=True),
    ]
