"""TV Remote: one page that drives Apple TVs (pyatv) and Android TVs (Android TV Remote protocol) through Home Assistant.

Home Assistant owns pairing and the device protocols; this app only drives the remote.* entities listed in TV_DEVICES
and sends them a fixed set of commands. TV_DEVICES is explicit (not discovered) because HA's template API is admin-only
and the apple_tv integration also exposes HomePods. It trusts the Authentik gate in front of it and never forwards an
entity or command that is not configured or defined here.

TV_DEVICES: semicolon-separated `entity_id=kind=Name`, kind apple_tv or android_tv, e.g.
  remote.living_room=apple_tv=Apple TV;remote.lounge_tv=android_tv=Lounge TV
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HA_URL: Final = os.environ["HA_URL"].rstrip("/")
HA_TOKEN: Final = os.environ["HA_TOKEN"]
HA_TIMEOUT: Final = float(os.environ.get("HA_TIMEOUT", "10"))
STATIC: Final = Path(__file__).parent / "static"

KINDS: Final = ("apple_tv", "android_tv")


def _parse_devices(raw: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for item in filter(None, (i.strip() for i in raw.split(";"))):
        entity_id, kind, name = (part.strip() for part in item.split("=", 2))
        if not entity_id.startswith("remote.") or kind not in KINDS:
            raise SystemExit(f"TV_DEVICES: bad entry {item!r}")
        found[entity_id] = {"entity_id": entity_id, "kind": kind, "name": name or entity_id}
    return found


DEVICES: Final = _parse_devices(os.environ.get("TV_DEVICES", ""))

# UI command -> key name per protocol. pyatv remote_control names for Apple TV; Android keycodes for the Remote protocol.
KEYS: Final[dict[str, dict[str, str]]] = {
    "up": {"apple_tv": "up", "android_tv": "DPAD_UP"},
    "down": {"apple_tv": "down", "android_tv": "DPAD_DOWN"},
    "left": {"apple_tv": "left", "android_tv": "DPAD_LEFT"},
    "right": {"apple_tv": "right", "android_tv": "DPAD_RIGHT"},
    "select": {"apple_tv": "select", "android_tv": "DPAD_CENTER"},
    "back": {"apple_tv": "menu", "android_tv": "BACK"},
    "home": {"apple_tv": "home", "android_tv": "HOME"},
    "menu": {"apple_tv": "top_menu", "android_tv": "MENU"},
    "play_pause": {"apple_tv": "play_pause", "android_tv": "MEDIA_PLAY_PAUSE"},
    "next": {"apple_tv": "next", "android_tv": "MEDIA_NEXT"},
    "previous": {"apple_tv": "previous", "android_tv": "MEDIA_PREVIOUS"},
    "volume_up": {"apple_tv": "volume_up", "android_tv": "VOLUME_UP"},
    "volume_down": {"apple_tv": "volume_down", "android_tv": "VOLUME_DOWN"},
}
POWER: Final = "power"

app = FastAPI(title="TV Remote", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Press(BaseModel):
    repeats: int = Field(default=1, ge=1, le=10)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=HA_URL, headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=HA_TIMEOUT)


async def _ha(method: str, path: str, **kwargs: object) -> httpx.Response:
    try:
        async with _client() as c:
            r = await c.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Home Assistant unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise HTTPException(502, f"Home Assistant answered {r.status_code} for {path}")
    return r


async def _state(entity_id: str) -> str:
    try:
        return (await _ha("GET", f"/api/states/{entity_id}")).json().get("state", "unknown")
    except (HTTPException, ValueError):
        return "unavailable"


@app.get("/api/devices")
async def devices() -> list[dict[str, str]]:
    return [{**d, "state": await _state(d["entity_id"])} for d in DEVICES.values()]


@app.post("/api/devices/{entity_id}/{command}")
async def press(entity_id: str, command: str, body: Press | None = None) -> dict[str, bool]:
    if command != POWER and command not in KEYS:
        raise HTTPException(400, "unknown command")
    device = DEVICES.get(entity_id)
    if device is None:
        raise HTTPException(404, "unknown device")
    if command == POWER:
        await _ha("POST", "/api/services/remote/toggle", json={"entity_id": entity_id})
        return {"ok": True}
    await _ha(
        "POST",
        "/api/services/remote/send_command",
        json={"entity_id": entity_id, "command": KEYS[command][device["kind"]], "num_repeats": (body or Press()).repeats},
    )
    return {"ok": True}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
