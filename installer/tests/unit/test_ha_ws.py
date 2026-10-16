"""63-post-install.py's minimal websocket client against a real websocket server speaking HA's auth handshake."""
import asyncio, importlib.util, json, sys, threading, types
from pathlib import Path

import websockets

from heardos_installer.kit.locate import kit_path


def _load():
    gen = kit_path("generic")
    sys.modules.setdefault("ak", types.SimpleNamespace(load_env=lambda: {"LAN_DOMAIN": "x", "HOST_LAN_IP": "127.0.0.1",
                                                                         "ADMIN_USER": "a", "ADMIN_EMAIL": "a@x"}))
    spec = importlib.util.spec_from_file_location("post", gen / "63-post-install.py"); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m


def test_ws_call_long_lived_token():
    m = _load()
    big = "t" * 300                      # > 125 bytes: exercises the 16-bit length frames both ways
    port_box, ready = {}, threading.Event()

    async def handler(ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        assert json.loads(await ws.recv())["access_token"] == "acc"
        await ws.send(json.dumps({"type": "auth_ok"}))
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth/long_lived_access_token" and msg["client_name"] == "c" * 200
        await ws.send(json.dumps({"id": msg["id"], "type": "result", "success": True, "result": big}))

    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port_box["p"] = srv.sockets[0].getsockname()[1]; ready.set()
            await asyncio.sleep(5)

    t = threading.Thread(target=lambda: asyncio.run(main()), daemon=True); t.start(); ready.wait(5)
    assert m.ws_call("127.0.0.1", port_box["p"], "/api/websocket", "acc",
                     {"type": "auth/long_lived_access_token", "client_name": "c" * 200, "lifespan": 3650}) == big
