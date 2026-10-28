"""The one place that manages stacks on an install. The GUI (ui/stacks.py, ui/assistant.py), the CLI (`heardos-cli tool`),
the model harness and the MCP server all go through a Backend, so a person and a model get exactly the same behaviour,
checks and messages.

RealBackend works on an installer-made install (session.Installation) over SSH and the kit scripts; SimBackend
(manage/sim.py) is an in-memory stand-in for demo mode, tests and model evals. Every method is blocking and returns plain
data or a one-line message; failures raise ManageError with a sentence a person (or a small model) can act on.
"""
from __future__ import annotations

import json
import re
import shlex
import threading
from typing import Optional, Protocol

import yaml

from ..kit import catalog
from ..kit.locate import kit_path
from .spec import GENERATE, CustomStack, check_setting, render_compose, render_override, validate

LIFECYCLE = ("start", "stop", "restart", "update")
DOCKER_HUB = "https://hub.docker.com/v2/search/repositories/"


class ManageError(RuntimeError):
    """A failure explained in one plain sentence (shown in the GUI, returned to the model)."""


class Backend(Protocol):
    domain: str
    custom: dict

    def inventory(self, with_state: bool = True) -> list[dict]: ...
    def containers(self, stack: str) -> list[dict]: ...
    def logs(self, stack: str, lines: int = 100, service: str = "") -> str: ...
    def compose_text(self, stack: str) -> str: ...
    def settings(self, stack: str) -> dict: ...
    def set_setting(self, stack: str, key: str, value: Optional[str], service: str = "") -> str: ...
    def lifecycle(self, stack: str, verb: str) -> str: ...
    def install_app(self, stack: str) -> str: ...
    def complete_spec(self, spec: dict) -> tuple[CustomStack, list[str]]: ...
    def add_custom(self, spec: dict) -> str: ...
    def remove(self, stack: str) -> str: ...
    def inspect_image(self, image: str) -> dict: ...
    def search_images(self, query: str) -> list[dict]: ...
    def health(self) -> list: ...
    def people(self) -> list[dict]: ...
    def give_access(self, username: str, stack: str) -> str: ...
    def secret_values(self) -> tuple[str, ...]: ...


# ---------------------------------------------------------------- pure helpers
def catalog_rows(installed: set[str]) -> list[dict]:
    rows = []
    for a in catalog.apps().values():
        if a.core or not a.available:
            continue
        rows.append({"stack": a.stack, "name": a.name, "kind": "app", "installed": a.stack in installed, "host": (a.hosts or [""])[0],
                     "blurb": a.blurb, "depends_on": list(a.depends_on), "slug": a.slug})
    return rows


def custom_rows(custom: dict) -> list[dict]:
    return [{"stack": n, "name": c.get("title") or n, "kind": "custom", "installed": True, "host": c.get("host") or n,
             "blurb": f"Your own app ({c.get('image')})", "depends_on": [], "slug": n, "access": c.get("access", "admins")}
            for n, c in sorted(custom.items())]


def catalog_compose(stack: str) -> str:
    p = kit_path("generic", "stacks", stack, "compose.yml")
    return p.read_text() if p.is_file() else ""


def compose_env(text: str) -> dict[str, dict[str, str]]:
    """{service: {KEY: value as written in the compose file}} (list or mapping form)."""
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for svc, body in (doc.get("services") or {}).items():
        env = (body or {}).get("environment") or {}
        if isinstance(env, list):
            env = dict((x.split("=", 1) + [""])[:2] for x in env)
        out[svc] = {str(k): "" if v is None else str(v) for k, v in env.items()}
    return out


def mask_value(key: str, value: str) -> str:
    from .spec import SECRETISH
    if SECRETISH.search(key) and value and not value.startswith("${"):
        return "(hidden)"
    return value


def taken(rows: list[dict], custom: dict, but: str = "") -> tuple[set[str], set[str]]:
    """Names and subdomains already in use (catalogue apps count even when not installed: they may be later)."""
    names = {r["stack"] for r in rows if r["stack"] != but}
    hosts = {r["host"] for r in rows if r["host"] and r["stack"] != but}
    for a in catalog.apps().values():
        names.add(a.stack)
        hosts.update(a.hosts)
    for n, c in custom.items():
        if n != but:
            names.add(n)
            hosts.add(c.get("host") or n)
    return names, hosts


def search_docker_hub(query: str, limit: int = 6) -> list[dict]:
    import httpx
    q = (query or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9 ._/-]{2,60}", q):
        raise ManageError("search words: 2-60 letters, digits, spaces, dots, dashes or slashes")
    try:
        r = httpx.get(DOCKER_HUB, params={"query": q, "page_size": 15}, timeout=20, headers={"User-Agent": "heardos-installer"})
    except httpx.HTTPError as e:
        raise ManageError(f"Docker Hub search failed ({type(e).__name__}); give the image name directly")
    if r.status_code != 200:
        raise ManageError(f"Docker Hub search failed (HTTP {r.status_code}); try again later or give the image name directly")
    res = r.json().get("results") or []
    res.sort(key=lambda x: (not x.get("is_official"), -(x.get("star_count") or 0)))
    return [{"image": x.get("repo_name"), "stars": x.get("star_count", 0), "official": bool(x.get("is_official")),
             "about": (x.get("short_description") or "")[:120]} for x in res[:limit]]


def image_facts(config: dict) -> dict:
    """docker image inspect .Config -> what a custom stack needs: listening ports, data folders, documented variables."""
    ports = sorted({int(p.split("/")[0]) for p in (config.get("ExposedPorts") or {}) if p.split("/")[0].isdigit()
                    and p.endswith("/tcp")})
    vols = sorted((config.get("Volumes") or {}).keys())
    env = [e.split("=", 1)[0] for e in (config.get("Env") or [])
           if not e.startswith(("PATH=", "HOME=", "LANG=", "LC_", "TERM=", "HOSTNAME=", "GPG_KEY=", "PYTHON_", "NODE_VERSION=", "YARN_VERSION="))]
    return {"ports": ports, "volumes": vols, "env": env[:20], "user": config.get("User") or ""}


WEB_PORTS = (80, 8080, 3000, 8000, 5000, 8081, 9000)


def fill_from_image(spec: CustomStack, facts: dict) -> list[str]:
    """Use the image's own metadata for the parts left empty. Returns notes about what was filled in."""
    notes = []
    if not spec.port:
        web = [p for p in facts["ports"] if p in WEB_PORTS]
        if len(facts["ports"]) == 1:
            spec.port = facts["ports"][0]
            notes.append(f"port {spec.port} taken from the image")
        elif len(web) == 1:
            spec.port = web[0]
            notes.append(f"port {spec.port} chosen from the image's ports {facts['ports']}")
    if not spec.volumes and facts["volumes"]:
        spec.volumes = list(facts["volumes"])
        notes.append("keeps " + ", ".join(spec.volumes) + " (the image's data folders)")
    return notes


def env_list(spec: CustomStack) -> list[str]:
    return [f"{k}={'(generated secret)' if v == GENERATE else v}" for k, v in spec.env.items()]


# ---------------------------------------------------------------- shared by both backends
class _Common:
    """Everything that only reads the plan, so previews, the settings view and spec checks behave the same on both."""
    plan: dict

    @property
    def domain(self) -> str:
        return self.plan.get("DOMAIN", "")

    def secret_values(self) -> tuple[str, ...]:
        """Literal secrets that must never reach a log view or a model (masked wherever they appear)."""
        return ()

    @property
    def custom(self) -> dict:
        return self.plan.setdefault("custom_stacks", {})

    @property
    def stack_settings(self) -> dict:
        return self.plan.setdefault("stack_settings", {})

    def _known(self, stack: str, installed: bool = True) -> dict:
        rows = {r["stack"]: r for r in self.inventory(with_state=False)}
        r = rows.get(stack)
        if not r:
            raise ManageError(f"there is no app called '{stack}'. Installed: "
                              f"{', '.join(n for n, x in rows.items() if x['installed']) or 'none'}")
        if installed and not r["installed"]:
            raise ManageError(f"{r['name']} ({stack}) is not installed")
        return r

    def _base_compose(self, stack: str) -> str:
        if stack in self.custom:
            return render_compose(CustomStack.from_dict(self.custom[stack]))
        return catalog_compose(stack)

    def compose_text(self, stack: str) -> str:
        self._known(stack, installed=False)
        ov = render_override(self.stack_settings.get(stack) or {})
        return self._base_compose(stack) + (f"\n# ---- compose.override.yml\n{ov}" if ov else "")

    def settings(self, stack: str) -> dict:
        self._known(stack, installed=False)
        base = compose_env(self._base_compose(stack))
        return {"services": {s: {k: mask_value(k, v) for k, v in kv.items()} for s, kv in base.items()},
                "overrides": self.stack_settings.get(stack) or {}, "main": stack if stack in base else next(iter(base), "")}

    def _record_setting(self, stack: str, key: str, value: Optional[str], service: str) -> tuple[str, Optional[str]]:
        """Validate and store one override in the plan; returns (service, old value)."""
        self._known(stack)
        errs = check_setting(key, value)
        if errs:
            raise ManageError(" ".join(errs))
        info = self.settings(stack)
        service = service or info["main"]
        if service not in info["services"]:
            raise ManageError(f"{stack} has no service '{service}'; services: {', '.join(info['services'])}")
        svc = self.stack_settings.setdefault(stack, {}).setdefault(service, {})
        old = svc.get(key)
        if value is None:
            svc.pop(key, None)
        else:
            svc[key] = value
        for s in [s for s, kv in self.stack_settings[stack].items() if not kv]:
            del self.stack_settings[stack][s]
        if not self.stack_settings[stack]:
            del self.stack_settings[stack]
        return service, old

    def complete_spec(self, spec: dict) -> tuple[CustomStack, list[str]]:
        """Parse a spec, fill port/volumes from the image when left out, and validate.
        Returns (spec, messages): problems first, then lines starting with 'note: ' about what was filled in."""
        s = CustomStack.from_dict(spec)
        notes: list[str] = []
        if (not s.port or not s.volumes) and s.image and ":" in s.image.rsplit("/", 1)[-1]:
            try:
                notes = fill_from_image(s, self.inspect_image(s.image))
            except ManageError as e:
                return s, [str(e)]
        updating = s.name in self.custom
        names, hosts = taken(self.inventory(with_state=False), self.custom, but=s.name if updating else "")
        return s, validate(s, taken_names=names, taken_hosts=hosts, updating=updating) + [f"note: {n}" for n in notes]


# ---------------------------------------------------------------- the real thing
class RealBackend(_Common):
    def __init__(self, inst):
        self.inst = inst
        self._lock = threading.Lock()     # one change at a time from this process
        self._people: Optional[tuple[float, list]] = None
        self._secrets: Optional[tuple[float, tuple[str, ...]]] = None

    @property
    def plan(self) -> dict:
        return self.inst.plan

    def secret_values(self) -> tuple[str, ...]:
        """The vault's values plus the secrets the home server generated itself (secrets/secrets.env and the secret-looking
        entries of each stack's .env), which the vault never sees. Read over SSH, cached for ten minutes."""
        import time
        from .spec import SECRETISH
        if self._secrets and time.time() - self._secrets[0] < 600:
            return self._secrets[1]
        vals = {v for v in self.inst.vault.values() if v}
        root = shlex.quote(self.plan.get("HEARDOS_ROOT", "/opt/heardos"))
        try:
            r = self._host().run(f"cat {root}/secrets/secrets.env 2>/dev/null; echo '#--stacks'; cat {root}/stacks/*/.env 2>/dev/null",
                                 sudo=True, timeout=60)
            own, _, stacks = r.stdout.partition("#--stacks")
            for block, every in ((own, True), (stacks, False)):
                for line in block.splitlines():
                    k, eq, v = line.partition("=")
                    v = v.strip().strip("'\"")
                    if eq and len(v) >= 8 and (every or SECRETISH.search(k.strip())):
                        vals.add(v)
        except Exception:  # noqa: BLE001 — unreachable host: the vault's values still apply; try again next time
            return tuple(vals)
        self._secrets = (time.time(), tuple(sorted(vals, key=len, reverse=True)))
        return self._secrets[1]

    def _host(self):
        return self.inst.sessions.host()

    def _dir(self, stack: str) -> str:
        return f"{self.plan.get('HEARDOS_ROOT', '/opt/heardos')}/stacks/{stack}"

    def _pending(self, stack: str) -> bool:
        """Adopted but still in its pre-installer layout (heardos-cli adopt): no stack folder on the host yet."""
        return stack in (self.plan.get("adopted_pending") or [])

    def _need_migrated(self, stack: str, what: str) -> None:
        if self._pending(stack):
            raise ManageError(f"{stack} still runs in its old layout, so it cannot be {what} from here yet; "
                              f"move it into the kit layout first (heardos-cli migrate {self.inst.install_id} {stack})")

    def _redact(self, text: str) -> str:
        from ..remote.ssh import mask_output_line
        out = "\n".join(mask_output_line(l) for l in text.splitlines())
        for v in self.secret_values():
            if v and len(v) >= 8:
                out = out.replace(v, "***")
        return out

    def _steps(self, ids: set[str], label: str) -> str:
        """Run install steps (and what they depend on) to the end; raise with the first failure."""
        if self.inst.running:
            raise ManageError("something else is running on this install; wait for it to finish")
        result: dict = {}
        ev = threading.Event()
        self.inst.run_async(only=ids, done=lambda ok: (result.setdefault("ok", ok), ev.set()))
        ev.wait()
        if result.get("ok"):
            return f"{label}: done"
        failed = [f"{sid}: {s.get('message', '')}" for sid, s in self.inst.state.data["steps"].items() if s.get("status") == "failed"]
        raise ManageError(f"{label} failed. " + ("; ".join(failed)[:400] or "See the Logs tab."))

    # ---- read
    def inventory(self, with_state: bool = True) -> list[dict]:
        from .. import ops
        rows = catalog_rows(set(self.plan.get("apps") or [])) + custom_rows(self.custom)
        state = ops.app_status(self.inst) if with_state else {}
        for r in rows:
            r["address"] = f"https://{r['host']}.{self.domain}" if r["host"] else ""
            r["state"] = (state.get(r["stack"], "not running") if r["installed"] else "not installed") if with_state else ""
        return rows

    def containers(self, stack: str) -> list[dict]:
        self._known(stack)
        r = self._host().run(f"docker ps -a --filter label=com.docker.compose.project={shlex.quote(stack)} --format '{{{{json .}}}}'",
                             sudo=True, timeout=60)
        out = []
        for line in r.stdout.splitlines():
            try:
                c = json.loads(line)
            except ValueError:
                continue
            out.append({"name": c.get("Names", ""), "image": c.get("Image", ""), "state": c.get("State", ""), "status": c.get("Status", ""),
                        "service": (re.search(r"com\.docker\.compose\.service=([^,]+)", c.get("Labels", "")) or [None, ""])[1]})
        return out

    def logs(self, stack: str, lines: int = 100, service: str = "") -> str:
        self._known(stack)   # a pending (not yet migrated) stack has no folder; compose still finds its containers by label
        lines = max(5, min(int(lines or 100), 2000))
        if service and not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", service):
            raise ManageError("service: a service name from the stack, e.g. the stack name itself")
        svc = (" " + shlex.quote(service)) if service else ""
        r = self._host().run(f"cd {shlex.quote(self._dir(stack))} 2>/dev/null; docker compose -p {shlex.quote(stack)} logs --no-color "
                             f"--tail {lines}{svc} 2>&1 | tail -c 300000", sudo=True, timeout=90)
        return self._redact(r.stdout) or "(no log output)"

    # ---- change
    def set_setting(self, stack: str, key: str, value: Optional[str], service: str = "") -> str:
        self._need_migrated(stack, "configured")
        with self._lock:
            service, old = self._record_setting(stack, key, value, service)
            self.inst.state.save()
            self._push_and_redeploy(stack)
        what = f"{key} removed" if value is None else f"{key}={value}"
        return f"{stack}: {what} (was {old if old is not None else 'unset'}); redeployed"

    def _push_and_redeploy(self, stack: str) -> None:
        from ..steps.custom import custom_files, push_stack_files
        from .. import ops
        if stack in self.custom:
            push_stack_files(self._host(), self.plan, stack, custom_files(self.plan, stack))
        else:
            push_stack_files(self._host(), self.plan, stack,
                             {"compose.override.yml": render_override(self.stack_settings.get(stack) or {})})
        r = ops._kit(self.inst, f"redeploy.{stack}", "52-redeploy.sh", [stack], timeout=1800)
        if not r.ok:
            raise ManageError(f"redeploy failed: {r.message}")

    def lifecycle(self, stack: str, verb: str) -> str:
        if verb not in LIFECYCLE:
            raise ManageError(f"action must be one of {', '.join(LIFECYCLE)}")
        self._known(stack)
        if verb == "update":
            self._need_migrated(stack, "updated")
        cmd = {"update": "docker compose -p {s} pull -q && docker compose -p {s} up -d --remove-orphans",
               "restart": "docker compose -p {s} restart", "stop": "docker compose -p {s} stop", "start": "docker compose -p {s} up -d"}[verb]
        if self._pending(stack):   # no stack folder yet: compose finds the project's containers by label (start = `start`, not `up`)
            cmd = {"restart": "docker compose -p {s} restart", "stop": "docker compose -p {s} stop", "start": "docker compose -p {s} start"}[verb]
            prefix = ""
        else:
            prefix = f"cd {shlex.quote(self._dir(stack))} && "
        r = self._host().run(prefix + cmd.format(s=shlex.quote(stack)), sudo=True, timeout=1800,
                             on_line=self.inst.logs.logger(f"ops.app.{stack}").info)
        if not r.ok:
            tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"{verb} failed"]
            raise ManageError(f"{stack}: {verb} failed: {self._redact(tail[0])[:200]}")
        return f"{stack}: {verb} done"

    def install_app(self, stack: str) -> str:
        from .. import ops
        r = self._known(stack, installed=False)
        if r["kind"] != "app":
            raise ManageError(f"{stack} is your own app; it is already installed")
        if r["installed"]:
            return f"{r['name']} is already installed at {r['address']}"
        with self._lock:
            ids = ops.add_apps(self.inst, [stack])
            msg = self._steps(ids, f"Installing {r['name']}")
        return f"{msg}. Open it at https://{r['host']}.{self.domain}" if r["host"] else msg

    def add_custom(self, spec: dict) -> str:
        s, msgs = self.complete_spec(spec)
        errs = [m for m in msgs if not m.startswith("note: ")]
        if errs:
            raise ManageError(" ".join(errs))
        with self._lock:
            self.custom[s.name] = s.to_dict()
            from ..steps import apply_defaults
            apply_defaults(self.plan)
            self.inst.state.save()
            ids = {f"custom.{s.name}", "dns.publish", "vps.caddy", "verify"}
            for sid in ids:
                self.inst.state.reset_step(sid)
            msg = self._steps(ids, f"Adding {s.title}")
        return f"{msg}. Open it at https://{s.host}.{self.domain} (sign-in: {s.access})"

    def remove(self, stack: str) -> str:
        self._need_migrated(stack, "removed")
        r = self._known(stack)
        if r["kind"] == "app":
            others = [a for a in self.plan.get("apps", []) if a != stack]
            if stack in catalog.closure(others):
                users = [catalog.apps()[a].name for a in others if stack in catalog.closure([a])]
                raise ManageError(f"{r['name']} is needed by {', '.join(users)}; remove those first")
        with self._lock:
            h = self._host().run(f"cd {shlex.quote(self._dir(stack))} && docker compose -p {shlex.quote(stack)} down --remove-orphans",
                                 sudo=True, timeout=600)
            if not h.ok:
                raise ManageError(f"could not stop {stack}: {self._redact((h.stderr or h.stdout).strip()[-200:])}")
            if r["kind"] == "app":
                self.plan["apps"] = [a for a in self.plan.get("apps", []) if a != stack]
                if not self.plan["apps"]:
                    self.plan.pop("profile", None)     # else apply_defaults would refill the list from the profile
                self.inst.state.reset_step(f"app.{stack}")
            else:
                self.custom.pop(stack, None)
                self.inst.state.reset_step(f"custom.{stack}")
            self.plan.pop("PUBLIC_HOSTS", None)
            from ..steps import apply_defaults
            apply_defaults(self.plan)
            self.inst.state.save()
            for sid in ("vps.caddy", "verify"):
                self.inst.state.reset_step(sid)
            self._steps({"vps.caddy"}, f"Removing {r['name']}'s web address")
        return (f"{r['name']} stopped and removed; its data is kept in {self.plan.get('HEARDOS_ROOT', '/opt/heardos')}/data. "
                f"The DNS record {r['host']}.{self.domain} still exists (harmless; delete it at your DNS host if you like)")

    def inspect_image(self, image: str) -> dict:
        from .spec import IMAGE
        if not IMAGE.match(image or ""):
            raise ManageError("image: give a Docker image like 'louislam/uptime-kuma:1'")
        q = shlex.quote(image)
        r = self._host().run(f"docker pull -q {q} >/dev/null 2>&1 || {{ echo PULL_FAILED; exit 0; }}; docker image inspect {q} --format '{{{{json .Config}}}}'",
                             sudo=True, timeout=900)
        if "PULL_FAILED" in r.stdout:
            raise ManageError(f"the home server could not download '{image}': check the name and tag (search_images can help)")
        try:
            facts = image_facts(json.loads(r.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            raise ManageError(f"could not read the image metadata of {image}")
        return {"image": image, **facts}

    def search_images(self, query: str) -> list[dict]:
        return search_docker_hub(query)

    def health(self) -> list:
        from .. import ops
        return ops.health(self.inst)

    def load(self) -> dict:
        """The home server's load and disk/cpu stall time (ops.parse_load); cached 30 s — app_status asks on every look."""
        import time
        from .. import ops
        if getattr(self, "_load", None) and time.monotonic() - self._load[0] < 30:
            return self._load[1]
        self._load = (time.monotonic(), ops.host_load(self.inst))
        return self._load[1]

    def people(self) -> list[dict]:
        """Cached for a minute: the harness builds its tool menu (people are an enum) every turn, and each read is an
        SSH round trip plus an Authentik API call."""
        import time
        from .. import ops
        if self._people and time.time() - self._people[0] < 60:
            return self._people[1]
        ppl = ops.people(self.inst)
        self._people = (time.time(), ppl)
        return ppl

    def give_access(self, username: str, stack: str) -> str:
        from .. import ops
        r = self._known(stack)
        if not r.get("slug"):
            raise ManageError(f"{r['name']} has no sign-in of its own; everyone who can reach it can use it")
        if r.get("access") == "public":
            raise ManageError(f"{r['name']} is public (no sign-in); there is no access to give")
        res = ops._kit(self.inst, "people", "42-ak-app.py", ["user", "access", username, r["slug"]], timeout=120)
        self._people = None
        if not res.ok:
            raise ManageError(res.message)
        return f"{username} can now open {r['name']}"

