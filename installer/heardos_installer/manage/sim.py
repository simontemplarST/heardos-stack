"""SimBackend: an in-memory install with the same interface and the same checks as RealBackend, for the GUI's demo mode,
unit tests and model evals (harness/evals.py). Nothing leaves the process. `journal` records every change call so an
eval can assert what the model actually did.

Image metadata comes from a small table of well-known images; anything else "cannot be downloaded", like a misspelt or
invented image name on a real server, which is exactly what an eval wants a model to run into and recover from.
"""
from __future__ import annotations

import time
from typing import Optional

from ..kit import catalog
from .backend import LIFECYCLE, ManageError, _Common, catalog_rows, custom_rows
from .spec import IMAGE

IMAGES = {
    "louislam/uptime-kuma": ({"ports": [3001], "volumes": ["/app/data"], "env": ["UPTIME_KUMA_PORT"], "user": ""},
                             "A fancy self-hosted monitoring tool", 5200),
    "freshrss/freshrss": ({"ports": [80], "volumes": ["/var/www/FreshRSS/data", "/var/www/FreshRSS/extensions"], "env": ["CRON_MIN"], "user": ""},
                          "A free, self-hostable RSS feed aggregator", 900),
    "jellyfin/jellyfin": ({"ports": [8096], "volumes": ["/cache", "/config"], "env": [], "user": ""}, "The Free Software Media System", 2500),
    "sissbruecker/linkding": ({"ports": [9090], "volumes": ["/etc/linkding/data"], "env": ["LD_SUPERUSER_NAME"], "user": ""},
                              "Self-hosted bookmark manager", 300),
    "dgtlmoon/changedetection.io": ({"ports": [5000], "volumes": ["/datastore"], "env": ["BASE_URL"], "user": ""},
                                    "Website change detection and notification", 700),
    "corentinth/it-tools": ({"ports": [80], "volumes": [], "env": [], "user": ""}, "Handy tools for developers", 400),
    "gitea/gitea": ({"ports": [22, 3000], "volumes": ["/data"], "env": ["USER_UID", "USER_GID"], "user": ""}, "Git with a cup of tea", 1100),
    "excalidraw/excalidraw": ({"ports": [80], "volumes": [], "env": [], "user": ""}, "Virtual whiteboard for sketching diagrams", 450),
    "ghcr.io/gethomepage/homepage": ({"ports": [3000], "volumes": ["/app/config"], "env": ["HOMEPAGE_ALLOWED_HOSTS"], "user": ""},
                                     "A highly customizable homepage", 0),
    "nginx": ({"ports": [80], "volumes": [], "env": ["NGINX_VERSION"], "user": ""}, "Official build of Nginx", 21000),
}
LOGS = {
    "homebox": ["INF starting server on :7745", "ERR database is locked", "ERR database is locked", "WRN health check failing"],
}
DEFAULT_APPS = ["immich", "onlyoffice", "nextcloud", "vaultwarden", "baikal", "mealie", "homebox", "paperless"]


class SimBackend(_Common):
    def __init__(self, plan: Optional[dict] = None, delay: float = 0.0):
        self.plan = plan if plan is not None else {"DOMAIN": "smithfamily.example", "HEARDOS_ROOT": "/opt/heardos",
                                                   "apps": [a for a in DEFAULT_APPS if a in catalog.apps()]}
        self.delay = delay
        self.state: dict[str, str] = {a: "running" for a in self.plan.get("apps", [])}
        if "homebox" in self.state:
            self.state["homebox"] = "unhealthy"
        self.users = [{"username": "alice", "name": "Alice Smith", "email": "alice@example.net", "active": True, "admin": True,
                       "apps": ["immich", "nextcloud", "vaultwarden", "baikal", "mealie"], "flows": ["flow-mfa-required"]},
                      {"username": "bob", "name": "Bob Smith", "email": "bob@example.net", "active": True, "admin": False,
                       "apps": ["immich", "nextcloud"], "flows": ["flow-recovery-allowed", "flow-self-service"]}]
        self.journal: list[tuple[str, dict]] = []

    def _wait(self, n: float = 1.0) -> None:
        if self.delay:
            time.sleep(self.delay * n)

    def _log(self, op: str, **kw) -> None:
        self.journal.append((op, kw))

    # ---- read
    def inventory(self, with_state: bool = True) -> list[dict]:
        rows = catalog_rows(set(self.plan.get("apps") or [])) + custom_rows(self.custom)
        for r in rows:
            r["address"] = f"https://{r['host']}.{self.domain}" if r["host"] else ""
            r["state"] = (self.state.get(r["stack"], "not running") if r["installed"] else "not installed") if with_state else ""
        return rows

    def containers(self, stack: str) -> list[dict]:
        r = self._known(stack)
        st = self.state.get(stack, "running")
        services = list(self.settings(stack)["services"]) or [stack]
        return [{"name": s, "service": s, "image": (self.custom.get(stack) or {}).get("image") or f"{s}:sim",
                 "state": "running" if st in ("running", "unhealthy") else st,
                 "status": {"running": "Up 3 days (healthy)", "unhealthy": "Up 3 days (unhealthy)", "exited": "Exited (0) 5 minutes ago"}.get(st, st)}
                for s in services] if r else []

    def logs(self, stack: str, lines: int = 100, service: str = "") -> str:
        self._known(stack)
        lines = max(5, min(int(lines or 100), 2000))
        base = LOGS.get(stack) or [f"INF {stack} started", "INF listening", "INF request GET / 200"]
        out = (base * (lines // len(base) + 1))[-lines:]
        return "\n".join(f"2026-10-21T{9 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z {l}" for i, l in enumerate(out))

    # ---- change
    def set_setting(self, stack: str, key: str, value: Optional[str], service: str = "") -> str:
        service, old = self._record_setting(stack, key, value, service)
        self._log("set_setting", stack=stack, key=key, value=value, service=service)
        self._wait()
        what = f"{key} removed" if value is None else f"{key}={value}"
        return f"{stack}: {what} (was {old if old is not None else 'unset'}); redeployed"

    def lifecycle(self, stack: str, verb: str) -> str:
        if verb not in LIFECYCLE:
            raise ManageError(f"action must be one of {', '.join(LIFECYCLE)}")
        self._known(stack)
        self._log(verb, stack=stack)
        self._wait()
        self.state[stack] = "exited" if verb == "stop" else "running"
        return f"{stack}: {verb} done"

    def install_app(self, stack: str) -> str:
        r = self._known(stack, installed=False)
        if r["kind"] != "app":
            raise ManageError(f"{stack} is your own app; it is already installed")
        if r["installed"]:
            return f"{r['name']} is already installed at {r['address']}"
        new = [s for s in catalog.closure([stack]) if s not in self.plan["apps"]]
        self._log("install_app", stack=stack)
        self._wait(3)
        self.plan["apps"] = catalog.closure(self.plan["apps"] + new)
        for s in new:
            self.state[s] = "running"
        extra = [catalog.apps()[s].name for s in new if s != stack]
        return (f"Installing {r['name']}: done" + (f" (also installed {', '.join(extra)})" if extra else "")
                + (f". Open it at https://{r['host']}.{self.domain}" if r["host"] else ""))

    def add_custom(self, spec: dict) -> str:
        s, msgs = self.complete_spec(spec)
        errs = [m for m in msgs if not m.startswith("note: ")]
        if errs:
            raise ManageError(" ".join(errs))
        self._log("add_custom", spec=s.to_dict())
        self._wait(3)
        self.custom[s.name] = s.to_dict()
        self.state[s.name] = "running"
        return f"Adding {s.title}: done. Open it at https://{s.host}.{self.domain} (sign-in: {s.access})"

    def remove(self, stack: str) -> str:
        r = self._known(stack)
        if r["kind"] == "app":
            others = [a for a in self.plan.get("apps", []) if a != stack]
            if stack in catalog.closure(others):
                users = [catalog.apps()[a].name for a in others if stack in catalog.closure([a])]
                raise ManageError(f"{r['name']} is needed by {', '.join(users)}; remove those first")
            self.plan["apps"] = others
        else:
            self.custom.pop(stack, None)
        self.state.pop(stack, None)
        self.stack_settings.pop(stack, None)
        self._log("remove", stack=stack)
        self._wait()
        return f"{r['name']} stopped and removed; its data is kept in {self.plan.get('HEARDOS_ROOT')}/data"

    def inspect_image(self, image: str) -> dict:
        if not IMAGE.match(image or ""):
            raise ManageError("image: give a Docker image like 'louislam/uptime-kuma:1'")
        repo = image.split("@")[0].rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image.split("@")[0]
        repo = repo.removeprefix("docker.io/").removeprefix("library/")
        if repo not in IMAGES:
            raise ManageError(f"the home server could not download '{image}': check the name and tag (search_images can help)")
        self._wait(0.5)
        return {"image": image, **IMAGES[repo][0]}

    def search_images(self, query: str) -> list[dict]:
        words = [w for w in (query or "").lower().replace("-", " ").split() if len(w) > 1]
        if not words:
            raise ManageError("search words: 2-60 letters, digits, spaces, dots, dashes or slashes")
        hits = []
        for repo, (_, about, stars) in IMAGES.items():
            text = f"{repo} {about}".lower().replace("-", " ")
            score = sum(w in text for w in words)
            if score:
                hits.append((score, stars, {"image": repo, "stars": stars, "official": "/" not in repo, "about": about}))
        hits.sort(key=lambda x: (-x[0], -x[1]))
        return [h[2] for h in hits[:6]]

    def health(self) -> list:
        from ..ops import Check
        bad = [s for s, v in self.state.items() if v not in ("running",)]
        return [Check("Home server", "Apps running", not bad, f"{len(self.state) - len(bad)} of {len(self.state)} fine"
                      + (f"; trouble: {', '.join(bad)}" if bad else "")),
                Check("Home server", "Disk space", True, "48% used"),
                Check("Public server", "Firewall on", True, "Status: active")]

    def people(self) -> list[dict]:
        return [dict(u) for u in self.users]

    def give_access(self, username: str, stack: str) -> str:
        r = self._known(stack)
        if r.get("access") == "public":
            raise ManageError(f"{r['name']} is public (no sign-in); there is no access to give")
        u = next((u for u in self.users if u["username"] == username), None)
        if not u:
            raise ManageError(f"no person '{username}'; people: {', '.join(x['username'] for x in self.users)}")
        if r["slug"] not in u["apps"]:
            u["apps"].append(r["slug"])
        self._log("give_access", username=username, stack=stack)
        return f"{username} can now open {r['name']}"
