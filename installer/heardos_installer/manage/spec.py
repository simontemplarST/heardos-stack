"""Custom stacks ("your own app"): a stack that is not in the catalogue, described by a small spec and rendered into a compose file that
follows the kit's conventions (external `proxy` network, Traefik labels, Authentik forward auth, data under
$HEARDOS_ROOT/data/<name>, no published host ports). The same spec comes from the GUI form, the CLI and the model
harness, so validation messages are written for whoever filled it in: each one says what to change.

Settings ("environment overrides") for any stack, catalogue or custom, are rendered into compose.override.yml, which
50-app.sh never overwrites and deploy_stack merges in.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import yaml

NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
HOST = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
# registry/namespace/repo[:tag][@sha256:digest] — nothing a shell or YAML would interpret
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,200}(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?(@sha256:[a-f0-9]{64})?$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,60}$")
# names whose value is a secret: they END in the secret word (ALLOW_PASSWORD_LOGIN is a switch, DB_PASSWORD is a secret)
SECRETISH = re.compile(r"(PASS|PASSWD|PASSWORD|SECRET|TOKEN|KEY|CREDENTIALS?|SALT|PRIVATE|PSK|HASH)(_?[0-9]+)?$|^SECRET", re.I)
MEMORY = re.compile(r"^\d{2,6}[mg]$")
USER = re.compile(r"^\d{1,6}:\d{1,6}$")
ACCESS = ("admins", "family", "public")
ACCESS_LABEL = {"admins": "Only administrators (sign-in required)", "family": "Everyone in the family (sign-in required)",
                "public": "Anyone on the internet (no sign-in)"}
GENERATE = "generate"       # env value meaning "make a random secret once and keep it"
# names and hosts the kit itself uses: never handed to a custom stack
RESERVED_NAMES = {"traefik", "authentik", "portainer", "proxy", "heardos", "nebula", "caddy", "custom", "data", "secrets", "stacks"}
RESERVED_HOSTS = {"auth", "cloud", "www", "mail", "portainer", "traefik"}
# Variables a model may not set (manage/tools.py; a person still can, in the GUI): they load code into the app, send its
# traffic elsewhere or switch off certificate checks, so one approved "setting" would hand the app to whoever chose it.
RISKY_ENV = re.compile(r"^(LD_[A-Z_]+|NODE_OPTIONS|NODE_PATH|NODE_EXTRA_CA_CERTS|NODE_TLS_REJECT_UNAUTHORIZED|PYTHON[A-Z_]*|PERL5?(LIB|OPT)"
                       r"|RUBY(LIB|OPT)|JAVA_TOOL_OPTIONS|_JAVA_OPTIONS|JDK_JAVA_OPTIONS|JAVA_OPTS|CATALINA_OPTS|DOTNET_STARTUP_HOOKS"
                       r"|CORECLR_[A-Z_]+|BASH_ENV|ENV|PROMPT_COMMAND|PATH|GCONV_PATH|MALLOC_[A-Z_]+|GIT_[A-Z_]+|PHP_INI_SCAN_DIR"
                       r"|(HTTPS?|ALL|NO|FTP)_PROXY|SSL_CERT_(FILE|DIR)|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE|GODEBUG|ENTRYPOINT|COMMAND)$", re.I)
# Registries a model may pull from (inspect_image, add_custom_app). Pulling from any other host would tell its owner the
# home server's address and fetch whatever they serve.
TRUSTED_REGISTRIES = ("docker.io", "ghcr.io", "lscr.io", "quay.io", "gcr.io", "registry.gitlab.com", "codeberg.org", "mcr.microsoft.com",
                      "public.ecr.aws")


def registry(image: str) -> str:
    """The registry host of an image reference ('docker.io' when none is named)."""
    first = image.split("/", 1)[0]
    return first.lower() if "/" in image and ("." in first or ":" in first or first == "localhost") else "docker.io"


def risky_env(key: str) -> bool:
    return bool(RISKY_ENV.match(key or ""))


@dataclass
class CustomStack:
    name: str                                   # stack / container / Authentik slug, e.g. "uptime"
    image: str                                  # e.g. "louislam/uptime-kuma:1"
    port: int = 0                               # port the app listens on inside the container (0 = take it from the image)
    title: str = ""                             # human name, e.g. "Uptime Kuma"
    host: str = ""                              # subdomain; default = name
    access: str = "admins"                      # admins | family | public
    volumes: list[str] = field(default_factory=list)   # container paths to keep, e.g. ["/app/data"]
    env: dict[str, str] = field(default_factory=dict)  # KEY -> value, or "generate" for a random secret
    user: str = ""                              # "1000:1000" to run as that uid:gid (data folders are given to it)
    memory: str = ""                            # "512m" / "2g" limit

    @classmethod
    def from_dict(cls, d: dict) -> "CustomStack":
        d = dict(d or {})
        if isinstance(d.get("env"), list):        # ["KEY=value", ...] — the form small models fill most reliably
            d["env"] = dict(_split_kv(x) for x in d["env"] if str(x).strip())
        if isinstance(d.get("volumes"), str):
            d["volumes"] = [v for v in re.split(r"[,\s]+", d["volumes"]) if v]
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and d[k] is not None}
        try:
            known["port"] = int(known.get("port") or 0)
        except (TypeError, ValueError):
            known["port"] = -1
        s = cls(**known)
        s.name = str(s.name).strip().lower()
        s.host = (str(s.host).strip().lower() or s.name)
        s.title = str(s.title).strip() or s.name.replace("-", " ").title()
        s.access = str(s.access).strip().lower()
        s.image = str(s.image).strip()
        s.volumes = [str(v).strip() for v in s.volumes if str(v).strip()]
        s.env = {str(k).strip(): str(v) for k, v in (s.env or {}).items()}
        return s

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def secrets(self) -> list[str]:
        return [k for k, v in self.env.items() if v == GENERATE]

    def dirs(self) -> list[dict]:
        return [{"path": volume_dir(v), "owner": self.user} for v in self.volumes]


def _split_kv(x: str) -> tuple[str, str]:
    k, _, v = str(x).partition("=")
    return k.strip(), v.strip()


def volume_dir(container_path: str) -> str:
    """/app/data -> app-data: the folder under data/<name> that holds this path."""
    s = re.sub(r"[^a-z0-9]+", "-", container_path.lower()).strip("-")
    return (s or "data")[:40]


def validate(s: CustomStack, *, taken_names: set[str], taken_hosts: set[str], updating: bool = False) -> list[str]:
    """Plain sentences, each naming the field and what would be accepted. Empty list = fine."""
    e: list[str] = []
    if not NAME.match(s.name):
        e.append("name: use 2-31 lowercase letters, digits or dashes, starting with a letter (e.g. uptime-kuma).")
    elif s.name in RESERVED_NAMES or (s.name in taken_names and not updating):
        e.append(f"name: '{s.name}' is already used; pick another name.")
    if not IMAGE.match(s.image):
        e.append("image: give a Docker image like 'louislam/uptime-kuma:1' or 'ghcr.io/owner/app:1.2' (lowercase, no spaces).")
    elif ":" not in s.image.rsplit("/", 1)[-1] and "@" not in s.image:
        e.append(f"image: add a version tag, e.g. '{s.image}:latest' or better a fixed version like '{s.image}:1.2.3'.")
    if not (1 <= s.port <= 65535):
        e.append("port: the port the app listens on inside its container, a number 1-65535 (check the image's documentation, often 80, 3000 or 8080).")
    if not HOST.match(s.host):
        e.append("host: a plain subdomain like 'uptime' (lowercase letters, digits, dashes).")
    elif s.host in RESERVED_HOSTS or (s.host in taken_hosts and not updating):
        e.append(f"host: {s.host} is already used by another app; pick another subdomain.")
    if s.access not in ACCESS:
        e.append("access: one of admins, family, public.")
    if not s.title or len(s.title) > 60 or any(c in s.title for c in "\"'`$\\\n"):
        e.append("title: a short name without quotes, $ or backslashes (max 60 characters).")
    for v in s.volumes:
        if not v.startswith("/") or ".." in v or not re.fullmatch(r"/[A-Za-z0-9._/-]{1,120}", v) or v in ("/", "/etc", "/usr", "/bin", "/proc", "/sys", "/dev"):
            e.append(f"volumes: '{v}' must be an absolute folder inside the container, like /app/data.")
    if len({volume_dir(v) for v in s.volumes}) != len(s.volumes):
        e.append("volumes: two paths map to the same data folder; remove the duplicate.")
    for k, v in s.env.items():
        if not ENV_KEY.match(k):
            e.append(f"env: '{k}' must be UPPER_CASE letters, digits and underscores.")
        elif SECRETISH.search(k) and v not in (GENERATE, ""):
            e.append(f"env: {k} looks like a secret; set its value to 'generate' and HeardOS makes and keeps a random one.")
        if "\n" in v or len(v) > 500:
            e.append(f"env: the value of {k} must be one line (max 500 characters).")
    if s.user and not USER.match(s.user):
        e.append("user: write it as uid:gid, e.g. 1000:1000, or leave it empty.")
    if s.memory and not MEMORY.match(s.memory):
        e.append("memory: like 512m or 2g, or leave it empty.")
    return e


def _dollar(v: str) -> str:
    return v.replace("$", "$$")     # compose interpolates ${...}; a literal value must not


def render_compose(s: CustomStack) -> str:
    """compose.yml for a custom stack. ${HEARDOS_ROOT} ${DOMAIN} ${TZ} and the generated secrets come from its .env."""
    mw = "xfp-https@file" + ("" if s.access == "public" else ",authentik-fwd@file")
    env: dict[str, str] = {"TZ": "${TZ}"}
    for k, v in s.env.items():
        env[k] = "${%s}" % k if v == GENERATE else _dollar(v)
    svc: dict = {"image": s.image, "container_name": s.name, "restart": "unless-stopped", "environment": env}
    if s.volumes:
        svc["volumes"] = [f"${{HEARDOS_ROOT}}/data/{s.name}/{volume_dir(v)}:{v}" for v in s.volumes]
    if s.user:
        svc["user"] = s.user
    if s.memory:
        svc["mem_limit"] = s.memory
    svc["networks"] = ["proxy"]
    svc["security_opt"] = ["no-new-privileges:true"]
    r = f"custom-{s.name}"
    svc["labels"] = {
        "traefik.enable": "true",
        f"traefik.http.routers.{r}.rule": f"Host(`{s.host}.${{DOMAIN}}`)",
        f"traefik.http.routers.{r}.entrypoints": "web",
        f"traefik.http.routers.{r}.middlewares": mw,
        f"traefik.http.services.{r}.loadbalancer.server.port": str(s.port),
        "heardos.custom": "true",
    }
    doc = {"services": {s.name: svc}, "networks": {"proxy": {"external": True}}}
    head = (f"# {s.title}: added with the HeardOS installer (\"your own app\"). Sign-in: {ACCESS_LABEL[s.access]}.\n"
            f"# Rendered from the installer's spec; edit the app in the installer, not here (changes here are overwritten).\n")
    return head + yaml.safe_dump(doc, sort_keys=False, width=200)


def render_override(settings: dict[str, dict[str, str]]) -> Optional[str]:
    """{service: {KEY: value}} -> compose.override.yml text, or None when there is nothing to override."""
    services = {svc: {"environment": {k: _dollar(str(v)) for k, v in kv.items()}} for svc, kv in sorted(settings.items()) if kv}
    if not services:
        return None
    return ("# Settings changed in the HeardOS installer (Stacks > Settings). Managed by the installer; edit them there.\n"
            + yaml.safe_dump({"services": services}, sort_keys=False, width=200))


def check_setting(key: str, value: Optional[str]) -> list[str]:
    e = []
    if not ENV_KEY.match(key or ""):
        e.append("key: UPPER_CASE letters, digits and underscores, e.g. LOG_LEVEL.")
    elif SECRETISH.search(key):
        e.append(f"key: {key} looks like a password or token. Secrets are not stored as settings (they would be readable in "
                 "the stack file); the app's own setup manages them.")
    if value is not None and ("\n" in value or len(value) > 500):
        e.append("value: one line, max 500 characters.")
    return e
