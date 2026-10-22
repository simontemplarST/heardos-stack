"""The tool registry: every operation a model (or a script) may run on an install, with a JSON schema whose enums are
filled from the live install (only installed apps can be restarted, only missing ones installed, only real people get
access). That is most of what makes a small model reliable: it chooses from lists instead of inventing names.

Each tool has a risk level:
  read    looks only; runs without asking
  change  installs, reconfigures or restarts something; the harness asks the operator first (unless allowed up front)
  danger  removes something; always asks, and never runs from a blanket "allow changes"
`always_ask` marks a change that always asks too: add_custom_app runs an image nobody vetted, so a blanket "allow
changes" does not cover it. Models also only pull from well-known registries (spec.TRUSTED_REGISTRIES) and cannot set
variables that load code or redirect traffic (spec.RISKY_ENV); a person can still do both in the GUI.
and a preview(): the exact effect in plain words, shown for the confirmation and, when the request is invalid, returned
to the model as the error instead of asking a person to approve something that cannot work.

Tool output is short on purpose (results are summaries, logs are tails). Hints about a next step appear only inside a
workflow that needs them (search -> inspect -> add); hints that suggest extra work ("now give access…") make small
models do more than they were asked, so there are none of those.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..kit import catalog
from .backend import Backend, ManageError, env_list
from .spec import ACCESS, ACCESS_LABEL, TRUSTED_REGISTRIES, registry, risky_env

MAX_TEXT = 1800
ERROR_WORDS = ("error", "err ", "fatal", "panic", "exception", "denied", "refused", "fail", "warn", "timeout", "unhealthy")


def looks_like_error(line: str) -> bool:
    return any(w in line.lower() for w in ERROR_WORDS)
LOG_LINES = [20, 50, 100]


@dataclass
class ToolResult:
    ok: bool
    text: str
    data: Any = None


@dataclass
class Tool:
    name: str
    summary: str
    risk: str                                            # read | change | danger
    params: Callable[[Backend], dict]                    # JSON-schema properties (enums from the live install)
    required: list[str]
    run: Callable[[Backend, dict], ToolResult]
    preview: Optional[Callable[[Backend, dict], tuple[bool, str]]] = None
    example: dict = field(default_factory=dict)
    always_ask: bool = False                             # ask even when changes are allowed up front

    def schema(self, b: Backend) -> dict:
        return {"type": "object", "properties": self.params(b), "required": list(self.required), "additionalProperties": False}

    def usable(self, b: Backend) -> bool:
        """False when an enum this tool needs is empty (e.g. nothing left to install)."""
        for p in self.params(b).values():
            if "enum" in p and not p["enum"]:
                return False
        return True


def _clip(text: str, n: int = MAX_TEXT) -> str:
    return text if len(text) <= n else text[: n - 40].rstrip() + "\n... (cut; ask for less)"


def _installed(b: Backend) -> list[str]:
    return [r["stack"] for r in b.inventory(with_state=False) if r["installed"]]


def _missing(b: Backend) -> list[str]:
    return [r["stack"] for r in b.inventory(with_state=False) if not r["installed"]]


def _gated(b: Backend) -> list[str]:
    return [r["stack"] for r in b.inventory(with_state=False) if r["installed"] and r.get("slug") and r.get("access") != "public"]


def _app(enum_fn, what="the app") -> Callable[[Backend], dict]:
    return lambda b: {"app": {"type": "string", "enum": enum_fn(b), "description": what}}


def _row(b: Backend, stack: str) -> dict:
    return next(r for r in b.inventory(with_state=False) if r["stack"] == stack)


# ---------------------------------------------------------------- read tools
def t_overview(b: Backend, a: dict) -> ToolResult:
    rows = b.inventory()
    inst = [r for r in rows if r["installed"]]
    bad = [r for r in inst if r["state"] != "running"]
    lines = [f"Cloud: {b.domain}. {len(inst)} apps installed, {len(rows) - len(inst)} more available (list_apps shows them)."]
    for r in inst:
        lines.append(f"- {r['stack']}: {r['name']}, {r['state']}" + (f", {r['address']}" if r["address"] else "")
                     + (" [your own app]" if r["kind"] == "custom" else ""))
    return ToolResult(True, _clip("\n".join(lines)), rows)


def t_list_apps(b: Backend, a: dict) -> ToolResult:
    rows = [r for r in b.inventory(with_state=False) if not r["installed"]]
    if not rows:
        return ToolResult(True, "Every catalogue app is installed. For anything else use add_custom_app.")
    lines = ["Catalogue apps not installed yet (install_app installs one; anything else: add_custom_app):"]
    lines += [f"- {r['stack']}: {r['name']}. {r['blurb']}" for r in rows]
    return ToolResult(True, _clip("\n".join(lines)), rows)


def t_app_status(b: Backend, a: dict) -> ToolResult:
    r = _row(b, a["app"])
    cs = b.containers(a["app"])
    st = next((x["state"] for x in b.inventory() if x["stack"] == a["app"]), "?")
    lines = [f"{r['name']} ({r['stack']}): {st}" + (f", {r['address']}" if r.get("host") else ", no web address")]
    lines += [f"- container {c['name']}: {c['status']} ({c['image']})" for c in cs] or ["- no containers found"]
    if st != "running":
        lines.append("Hint: app_logs shows the errors.")
    return ToolResult(True, _clip("\n".join(lines)), cs)


def t_app_logs(b: Backend, a: dict) -> ToolResult:
    n = int(a.get("lines") or 50)
    text = b.logs(a["app"], n)
    tail = text.splitlines()[-n:]
    errs = [l for l in tail if looks_like_error(l)]
    head = f"Last {len(tail)} log lines of {a['app']}" + (f"; {len(errs)} look like errors" if errs else "") + ":"
    return ToolResult(True, _clip(head + "\n" + "\n".join(tail), 2400), text)


def t_app_settings(b: Backend, a: dict) -> ToolResult:
    s = b.settings(a["app"])
    lines = [f"Settings of {a['app']} (environment variables per service; main service: {s['main']}):"]
    for svc, kv in s["services"].items():
        lines.append(f"[{svc}] " + (", ".join(f"{k}={v}" for k, v in kv.items()) or "(none)"))
    if s["overrides"]:
        lines.append("Changed by you: " + "; ".join(f"[{svc}] " + ", ".join(f"{k}={v}" for k, v in kv.items()) for svc, kv in s["overrides"].items()))
    lines.append("change_setting sets one; reset_setting puts one back.")
    return ToolResult(True, _clip("\n".join(lines)), s)


def t_search_images(b: Backend, a: dict) -> ToolResult:
    hits = b.search_images(a["query"])
    if not hits:
        return ToolResult(True, f"No images found for '{a['query']}'. Try once with just the app's name; if that finds nothing "
                                "either, the app is not available: finish and say so.")
    lines = [f"Docker images for '{a['query']}' (most trusted first):"]
    lines += [f"- {h['image']}{' (official)' if h['official'] else ''}, {h['stars']} stars: {h['about']}" for h in hits]
    lines.append("Next: inspect_image with the right one plus a tag (:latest if you know no version).")
    return ToolResult(True, _clip("\n".join(lines)), hits)


def _untrusted(image: str) -> str:
    """Why a model may not pull this image, or ''."""
    reg = registry(image or "")
    if reg in TRUSTED_REGISTRIES or reg == "index.docker.io":
        return ""
    return (f"{reg} is not a registry the assistant may download from ({', '.join(TRUSTED_REGISTRIES)}). Use an image from one of "
            "those, or add this app yourself in the installer's Stacks screen.")


def t_inspect_image(b: Backend, a: dict) -> ToolResult:
    why = _untrusted(a["image"])
    if why:
        return ToolResult(False, why)
    f = b.inspect_image(a["image"])
    lines = [f"{f['image']}: listens on port(s) {', '.join(map(str, f['ports'])) or 'none declared'}; "
             f"keeps data in {', '.join(f['volumes']) or 'no declared folders'}"
             + (f"; documented variables: {', '.join(f['env'][:10])}" if f["env"] else "")]
    if len(f["ports"]) == 1:
        lines.append(f"Next: add_custom_app with image={f['image']} (port {f['ports'][0]} and the folders are filled in automatically).")
    elif f["ports"]:
        lines.append(f"Next: add_custom_app with image={f['image']} and port = the web port (one of {f['ports']}).")
    else:
        lines.append("The image declares no port: give port to add_custom_app (see the image's documentation).")
    return ToolResult(True, "\n".join(lines), f)


def t_health(b: Backend, a: dict) -> ToolResult:
    checks = b.health()
    bad = [c for c in checks if c.ok is False]
    lines = ["All checks pass." if not bad else f"{len(bad)} check(s) need attention:"]
    lines += [f"- {'OK ' if c.ok else ('-- ' if c.ok is None else 'BAD')} {c.area}: {c.name}: {c.detail}" for c in checks]
    return ToolResult(True, _clip("\n".join(lines)), checks)


def t_people(b: Backend, a: dict) -> ToolResult:
    ppl = b.people()
    lines = [f"- {u['username']}: {u['name']}{' (admin)' if u.get('admin') else ''}{'' if u.get('active', True) else ' SUSPENDED'}; "
             f"apps: {', '.join(u['apps']) or 'none'}" for u in ppl]
    return ToolResult(True, _clip("People:\n" + "\n".join(lines)), ppl)


# ---------------------------------------------------------------- change tools
def p_install(b: Backend, a: dict) -> tuple[bool, str]:
    app = catalog.apps()[a["app"]]
    extra = [catalog.apps()[s].name for s in catalog.closure([a["app"]]) if s != a["app"] and s not in _installed(b)]
    return True, (f"Install {app.name} ({app.stack}): {app.blurb}"
                  + (f" Also installs what it needs: {', '.join(extra)}." if extra else "")
                  + (f" New web address: https://{app.hosts[0]}.{b.domain}." if app.hosts else "")
                  + " Takes a few minutes; existing apps keep running.")


def t_install(b: Backend, a: dict) -> ToolResult:
    return ToolResult(True, b.install_app(a["app"]))


def _spec_args(a: dict) -> dict:
    keys = ("name", "image", "port", "title", "host", "access", "volumes", "env")
    return {k: a[k] for k in keys if k in a and a[k] not in (None, "", [])}


def p_custom(b: Backend, a: dict) -> tuple[bool, str]:
    why = _untrusted(a.get("image", ""))
    if why:
        return False, why
    risky = [k for k in (str(x).partition("=")[0].strip() for x in a.get("env") or []) if risky_env(k)]
    if risky:
        return False, (f"Cannot add it: {', '.join(risky)} change how the app's code runs or where its traffic goes; the assistant "
                       "may not set them. Leave them out (a person can add them in the Stacks screen).")
    s, msgs = b.complete_spec(_spec_args(a))
    errs = [m for m in msgs if not m.startswith("note: ")]
    if errs:
        return False, "Cannot add it yet: " + " ".join(errs)
    notes = [m[6:] for m in msgs if m.startswith("note: ") and not m.startswith("note: keeps")]   # folders are listed anyway
    return True, (f"Add your own app '{s.title}' ({s.name}) from image {s.image}, listening on port {s.port}, at https://{s.host}.{b.domain}. "
                  f"Who can open it: {ACCESS_LABEL[s.access]}. "
                  + (f"Keeps data folders {', '.join(s.volumes)}. " if s.volumes else "Keeps no data (nothing survives an update). ")
                  + (f"Environment: {', '.join(env_list(s))}. " if s.env else "")
                  + (f"({'; '.join(notes)}.) " if notes else "")
                  + "Creates a DNS record and an HTTPS certificate; takes a few minutes.")


def t_custom(b: Backend, a: dict) -> ToolResult:
    msg = b.add_custom(_spec_args(a))
    return ToolResult(True, msg)


def p_control(b: Backend, a: dict) -> tuple[bool, str]:
    what = {"start": "Start", "stop": "Stop (it stays down until started again)", "restart": "Restart",
            "update": "Download the newest images for and recreate"}[a["action"]]
    return True, f"{what} {_row(b, a['app'])['name']} ({a['app']}). People using it see a short interruption."


def t_control(b: Backend, a: dict) -> ToolResult:
    return ToolResult(True, b.lifecycle(a["app"], a["action"]))


def p_setting(b: Backend, a: dict) -> tuple[bool, str]:
    from .spec import check_setting
    errs = check_setting(a["key"], a.get("value", ""))
    if not errs and risky_env(a["key"]):
        errs = [f"key: {a['key']} changes how the app's code runs or where its traffic goes; the assistant may not set it "
                "(a person can, in the Stacks screen)."]
    if errs:
        return False, " ".join(errs)
    cur = b.settings(a["app"])
    old = next((kv.get(a["key"]) for kv in cur["services"].values() if a["key"] in kv), None)
    return True, (f"Set {a['key']}={a['value']} on {a['app']} (now {old if old is not None else 'not set'}), then redeploy it "
                  "(short interruption). Survives updates and reinstalls.")


def t_setting(b: Backend, a: dict) -> ToolResult:
    return ToolResult(True, b.set_setting(a["app"], a["key"], a["value"]))


def p_reset(b: Backend, a: dict) -> tuple[bool, str]:
    over = b.settings(a["app"])["overrides"]
    if not any(a["key"] in kv for kv in over.values()):
        return False, f"{a['key']} was never changed on {a['app']}; changed settings: " + (
            ", ".join(k for kv in over.values() for k in kv) or "none")
    return True, f"Put {a['key']} on {a['app']} back to the app's default, then redeploy it."


def t_reset(b: Backend, a: dict) -> ToolResult:
    over = b.settings(a["app"])["overrides"]
    svc = next((s for s, kv in over.items() if a["key"] in kv), "")
    return ToolResult(True, b.set_setting(a["app"], a["key"], None, svc))


def p_access(b: Backend, a: dict) -> tuple[bool, str]:
    return True, f"Let {a['person']} open {_row(b, a['app'])['name']} (adds them to its sign-in group)."


def t_access(b: Backend, a: dict) -> ToolResult:
    return ToolResult(True, b.give_access(a["person"], a["app"]))


def p_remove(b: Backend, a: dict) -> tuple[bool, str]:
    r = _row(b, a["app"])
    return True, (f"REMOVE {r['name']} ({a['app']}): stop and delete its containers and its web address. Its data folder is kept "
                  "and can be reused by installing it again.")


def t_remove(b: Backend, a: dict) -> ToolResult:
    return ToolResult(True, b.remove(a["app"]))


def _people_enum(b: Backend) -> list[str]:
    try:
        return [u["username"] for u in b.people()]
    except Exception:  # noqa: BLE001 — Authentik unreachable: the tool just isn't offered
        return []


TOOLS: list[Tool] = [
    Tool("overview", "What is installed, where, and whether it runs. Start here.", "read", lambda b: {}, [], t_overview),
    Tool("list_apps", "Catalogue apps that can still be installed, with what each is for.", "read", lambda b: {}, [], t_list_apps),
    Tool("app_status", "Containers and state of one installed app.", "read", _app(_installed), ["app"], t_app_status, example={"app": "mealie"}),
    Tool("app_logs", "The last log lines of one installed app (to find out why it fails).", "read",
         lambda b: {**_app(_installed)(b), "lines": {"type": "integer", "enum": LOG_LINES}}, ["app"], t_app_logs,
         example={"app": "homebox", "lines": 50}),
    Tool("app_settings", "Environment settings of one installed app, and which ones were changed.", "read", _app(_installed), ["app"],
         t_app_settings, example={"app": "mealie"}),
    Tool("search_images", "Find the Docker image for an app that is not in the catalogue.", "read",
         lambda b: {"query": {"type": "string", "description": "the app's name, one or two words", "maxLength": 60}}, ["query"],
         t_search_images, example={"query": "uptime kuma"}),
    Tool("inspect_image", "Download an image on the home server and read its port and data folders.", "read",
         lambda b: {"image": {"type": "string", "description": "owner/name:tag, from search results or the task", "maxLength": 200}}, ["image"],
         t_inspect_image, example={"image": "louislam/uptime-kuma:1"}),
    Tool("health_check", "Check the public server, the home server and every web address.", "read", lambda b: {}, [], t_health),
    Tool("list_people", "Family accounts and the apps each can open.", "read", lambda b: {}, [], t_people),
    Tool("install_app", "Install an app from the catalogue (sign-in, web address and HTTPS are set up automatically).", "change",
         _app(_missing, "a catalogue app that is not installed yet"), ["app"], t_install, p_install, example={"app": "paperless"}),
    Tool("add_custom_app", "Add an app that is not in the catalogue, from its Docker image.", "change",
         lambda b: {"name": {"type": "string", "description": "short lowercase name of the app", "maxLength": 31},
                    "image": {"type": "string", "description": "Docker image with tag", "maxLength": 200},
                    "access": {"type": "string", "enum": list(ACCESS), "description": "admins, family, or public (no sign-in)"},
                    "port": {"type": "integer", "description": "web port inside the container; leave out to read it from the image"},
                    "title": {"type": "string", "description": "human name", "maxLength": 60},
                    "env": {"type": "array", "items": {"type": "string", "maxLength": 300},
                            "description": "KEY=value lines; KEY=generate makes a random secret"}},
         ["name", "image", "access"], t_custom, p_custom, example={"name": "uptime", "image": "louislam/uptime-kuma:1", "access": "admins"},
         always_ask=True),
    Tool("control_app", "Start, stop, restart or update one installed app.", "change",
         lambda b: {**_app(_installed)(b), "action": {"type": "string", "enum": ["restart", "start", "stop", "update"]}}, ["app", "action"],
         t_control, p_control, example={"app": "homebox", "action": "restart"}),
    Tool("change_setting", "Set one environment setting of an installed app and redeploy it.", "change",
         lambda b: {**_app(_installed)(b), "key": {"type": "string", "description": "the setting's UPPER_CASE name from the task", "maxLength": 61},
                    "value": {"type": "string", "maxLength": 500}}, ["app", "key", "value"], t_setting, p_setting,
         example={"app": "mealie", "key": "LOG_LEVEL", "value": "debug"}),
    Tool("reset_setting", "Undo a changed setting (back to the app's default) and redeploy.", "change",
         lambda b: {**_app(_installed)(b), "key": {"type": "string", "maxLength": 61}}, ["app", "key"], t_reset, p_reset,
         example={"app": "mealie", "key": "LOG_LEVEL"}),
    Tool("give_access", "Let a person open an installed app.", "change",
         lambda b: {"person": {"type": "string", "enum": _people_enum(b)}, **_app(_gated)(b)}, ["person", "app"], t_access, p_access,
         example={"person": "bob", "app": "mealie"}),
    Tool("remove_app", "Remove an installed app (its data is kept).", "danger", _app(_installed), ["app"], t_remove, p_remove,
         example={"app": "homebox"}),
]
BY_NAME = {t.name: t for t in TOOLS}


def call(b: Backend, name: str, args: dict) -> ToolResult:
    """Run a tool without any confirmation (callers decide that). Errors come back as ok=False with the reason."""
    t = BY_NAME.get(name)
    if not t:
        return ToolResult(False, f"no tool '{name}'; tools: {', '.join(BY_NAME)}")
    from ..harness.schema import check
    errs = check(t.schema(b), args)
    if errs:
        return ToolResult(False, "Wrong arguments: " + "; ".join(errs))
    if t.preview:
        ok, why = t.preview(b, args)
        if not ok:
            return ToolResult(False, why)
    try:
        return t.run(b, args)
    except ManageError as e:
        return ToolResult(False, str(e))
