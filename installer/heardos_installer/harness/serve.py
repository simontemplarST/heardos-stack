"""heardos-serve: the harness behind the chat protocols that existing LLM front ends already speak, so Open WebUI,
LibreChat, Lobe Chat, Hollama, Chatbox, Enchanted, an IDE plug-in or a plain `curl` can drive it. Nothing new to
learn: add http://127.0.0.1:11440 as an OpenAI-compatible or Ollama connection and talk to the model called `heardos`.

  heardos-serve --sim                                   # simulated install: nothing real is touched
  heardos-serve --install ID                            # your install (unlocks the vault)
  HEARDOS_SERVE_TOKEN=… heardos-serve --install ID --bind 10.42.0.5:11440   # reachable from another machine

Protocols:  POST /v1/chat/completions (+ GET /v1/models)           OpenAI, streaming or not
            POST /api/chat (+ GET /api/tags, /api/version, POST /api/show)   Ollama, NDJSON streaming

The chat UI only ever sees text. Each user message is one task for the harness; progress streams back as it happens,
then the final report. When the harness needs a yes, the reply ends with the question and a reference such as
"(ref a1b2)", and the *next* message in that conversation answers it: yes approves a change, the app's name approves a
removal, anything else refuses (and, as everywhere in the harness, a refusal ends the run). The reference ties the
answer to the right question even though the protocols are stateless: the client resends the whole conversation,
and the harness looks for the reference in the last assistant message.

One run at a time: the harness is built for a small model on modest hardware, and the operator is one person.

Safety, because whoever reaches this port can change the install:
  * A bearer token is always required, on 127.0.0.1 too (any web page in a browser on this machine could otherwise
    drive it). Without --token / HEARDOS_SERVE_TOKEN a random one is made at start and printed once.
  * No CORS unless --cors-origin names the one browser front end allowed; requests from any other Origin are refused.
    POSTs must be application/json, and the Host header must be this server's own address (no DNS rebinding).
  * Everything the model or a tool wrote is escaped before it reaches the chat (redact.md_text), so it can neither
    render an image that sends data elsewhere nor imitate an approval question.
  * --allow-changes is refused for a model outside this machine and the home network; add_custom_app always asks.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlsplit

from .. import __version__
from . import llm as L
from .agent import Agent, Policy
from .redact import md_code, md_text

MODEL_NAME = "heardos"
APPROVE_WORDS = {"y", "yes", "yes.", "ok", "okay", "approve", "approved", "go ahead", "do it", "sure", "yep", "confirm"}
PAUSE, END = object(), object()        # queue sentinels: the run waits for an answer / the run is over
WAIT_FOR_ANSWER = 15 * 60              # seconds a question stays open before it counts as refused
MAX_BODY = 4 * 1024 * 1024
LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
HELP = ("I am the HeardOS assistant. Tell me what you want in plain words, one request per message: for example which apps "
        "are installed, something to keep track of the houseplants, or that the inventory app is broken. Every change is "
        "shown to you first and waits for your yes.")


@dataclass
class Pending:
    ref: str
    tool: str
    args: dict
    preview: str
    risk: str
    answered: threading.Event = field(default_factory=threading.Event)
    yes: bool = False

    @property
    def question(self) -> str:
        if self.risk == "danger":
            want = md_text(str(self.args.get("app", "yes")), one_line=True)
            how = f"Reply with the app's name, **{want}**, to approve. Anything else refuses."
        else:
            how = "Reply **yes** to approve. Anything else refuses."
        return f"\n\n**The assistant wants to:** {md_text(self.preview, one_line=True)}\n\n{how} (ref {self.ref})"

    def answer(self, text: str) -> bool:
        t = text.strip().lower().rstrip(".!")
        if self.risk == "danger":
            return t == str(self.args.get("app", "yes")).lower()
        return t in APPROVE_WORDS


@dataclass
class Run:
    task: str
    chunks: list = field(default_factory=list)          # str | PAUSE | END, appended by the harness thread
    pending: Optional[Pending] = None
    cursor: int = 0                                     # how much of `chunks` a response has already delivered
    ready: threading.Condition = field(default_factory=threading.Condition)
    finished: bool = False

    def put(self, item) -> None:
        with self.ready:
            self.chunks.append(item)
            self.ready.notify_all()

    def take(self, timeout: float = 1.0):
        """Next undelivered item, or None after `timeout` with nothing new (keeps the HTTP stream alive)."""
        with self.ready:
            if self.cursor >= len(self.chunks):
                self.ready.wait(timeout)
            if self.cursor >= len(self.chunks):
                return None
            item = self.chunks[self.cursor]
            self.cursor += 1
            return item


class Assistant:
    """The harness as a conversation partner: `reply(messages)` yields text for one assistant turn."""

    def __init__(self, backend, llm, allow_changes: bool = False, max_steps: int = 12):
        self.b, self.llm, self.allow_changes, self.max_steps = backend, llm, allow_changes, max_steps
        self.run: Optional[Run] = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ harness side
    def _start(self, task: str) -> Run:
        run = Run(task)

        def confirm(tool, args, preview, risk) -> bool:
            p = Pending(secrets.token_hex(4), tool, args, preview, risk)
            run.pending = p
            run.put(p.question)
            run.put(PAUSE)
            p.answered.wait(WAIT_FOR_ANSWER)
            run.pending = None
            run.put(f"\n\n{'Approved' if p.yes else 'Refused'}: {md_text(tool)}.\n")
            return p.yes

        def event(kind, d) -> None:      # model and tool text is escaped: the chat renders Markdown
            if kind == "intent":
                run.put(f"- request type: {md_text(d['intent'])}\n")
            elif kind == "pick":
                run.put(f"- {md_text(d['thought'], one_line=True)} → {md_text(d['app'] or 'none of the catalogue apps')}\n")
            elif kind == "thought":
                run.put(f"- {md_text(d['thought'], one_line=True)} → {md_text(d['tool'])}\n")
            elif kind == "result":
                t = d["turn"]
                head = t.text.splitlines()[0] if t.text else ""
                args = ", ".join(f"{k}={v}" for k, v in t.args.items())
                run.put(f"- {'✓' if t.ok else '✗'} `{md_code(f'{t.tool}({args})')[:300]}`: {md_text(head[:220], one_line=True)}\n")
            elif kind == "retry":
                run.put("- the model's answer was unusable; asking again\n")
            elif kind == "notice":
                run.put(f"- {md_text(d['text'])}\n")

        def go() -> None:
            try:
                agent = Agent(self.b, self.llm, Policy(allow_changes=self.allow_changes, confirm=confirm), max_steps=self.max_steps, on_event=event)
                res = agent.run(task)
                run.put("\n" + md_text(res.report()) + "\n")
            except Exception as e:  # noqa: BLE001 — the person gets the reason, the server stays up
                run.put(f"\nStopped: {md_text(f'{type(e).__name__}: {e}')}\n")
            run.finished = True
            run.put(END)

        threading.Thread(target=go, daemon=True, name="heardos-serve-run").start()
        return run

    # ------------------------------------------------------------------ chat side
    def reply(self, messages: list[dict]):
        """Generator of text pieces for the assistant's turn after `messages` (OpenAI/Ollama shape)."""
        user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        text = _content(user).strip() if user else ""
        prev = next((_content(m) for m in reversed(messages) if m.get("role") == "assistant"), "")
        with self.lock:                     # decide under the lock, stream outside it
            run = self.run
            if run and run.pending and f"(ref {run.pending.ref})" in prev:
                p = run.pending
                p.yes = p.answer(text)
                p.answered.set()
                head = ""
            elif run and not run.finished:
                what = ("a change is waiting for a yes or no in another conversation" if run.pending
                        else f"still working on: {md_text(run.task, one_line=True)}")
                run, head = None, f"One thing at a time: {what}. Send your request again in a moment."
            elif text.startswith("### Task") or text.startswith("### Chat History"):
                # a chat app's own housekeeping prompt (title, tags, follow-ups) sent to this "model" by mistake: never a task
                run, head = None, "Chat"
            elif not text or text.lower() in ("help", "?", "hi", "hello"):
                run, head = None, HELP
            else:
                head = ""
                if run and run.cursor < len(run.chunks):     # the last answer was never read (client went away): show it first
                    lost = "".join(c for c in run.chunks[run.cursor:] if isinstance(c, str))
                    head = f"Earlier, about *{md_text(run.task, one_line=True)}*:\n{lost}\n\n---\n\n"
                self.run = run = self._start(text)
        if head:
            yield head
        if run:
            yield from self._stream(run)

    def _stream(self, run: Run):
        while True:
            item = run.take()
            if item is None:
                yield ""          # keep-alive: an empty chunk every second while the model thinks
                continue
            if item is PAUSE or item is END:
                return
            yield item


def _content(m: dict) -> str:
    c = m.get("content", "")
    if isinstance(c, list):      # OpenAI content parts
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c or "")


# ---------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"heardos-serve/{__version__}"
    assistant: Assistant
    token: str = ""
    cors_origin: Optional[str] = None          # the one browser origin allowed to call (None: no browser front ends)
    hosts: frozenset = frozenset()             # acceptable Host header names
    any_ip_host: bool = False                  # bound to every address: any IP literal is an acceptable Host

    def log_message(self, fmt, *args):      # one quiet line per request
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _auth(self) -> bool:
        h = self.headers.get("Authorization", "")
        return bool(self.token) and h.startswith("Bearer ") and secrets.compare_digest(h[7:].strip().encode(), self.token.encode())

    def _host_ok(self) -> bool:
        """The Host header names this server (a DNS-rebinding page sends its own domain name)."""
        h = (self.headers.get("Host") or "").strip().lower()
        if not h:
            return True
        name = h[1:].split("]")[0] if h.startswith("[") else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
        if name in self.hosts:
            return True
        if self.any_ip_host:
            import ipaddress
            try:
                ipaddress.ip_address(name)
                return True
            except ValueError:
                return False
        return False

    def _origin_ok(self) -> bool:
        o = self.headers.get("Origin")
        return o is None or (self.cors_origin is not None and o == self.cors_origin)

    def _cors(self) -> None:
        if self.cors_origin and self.headers.get("Origin") == self.cors_origin:
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
            self.send_header("Vary", "Origin")

    def _guard(self) -> bool:
        """Host, Origin and token checks; answers the request itself and returns False when one fails."""
        if not self._host_ok():
            self._json(421, {"error": {"message": "wrong Host header"}})
            return False
        if not self._origin_ok():
            self._json(403, {"error": {"message": "this origin may not call the HeardOS assistant"}})
            return False
        if not self._auth():
            self._json(401, {"error": {"message": "bearer token required"}})
            return False
        return True

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Optional[dict]:
        """The JSON body, or None after answering 413/415/400 (only application/json, at most MAX_BODY bytes: a
        cross-site form or text/plain post never gets here)."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._json(415, {"error": {"message": "Content-Type must be application/json"}})
            return None
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            self._json(413, {"error": {"message": f"request body must be 0-{MAX_BODY} bytes"}})
            return None
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._json(400, {"error": {"message": "the body must be a JSON object"}})
            return None
        return body

    def do_OPTIONS(self):       # a browser front end's preflight: only for the one --cors-origin
        if not self._host_ok() or not self.cors_origin or self.headers.get("Origin") != self.cors_origin:
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/":
            if not self._host_ok():
                return self._json(421, {"error": {"message": "wrong Host header"}})
            data = b"Ollama is running (HeardOS assistant)"       # what Ollama clients probe for
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)
            return
        if not self._guard():
            return
        now = int(time.time())
        if path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "created": now, "owned_by": "heardos"}]})
        if path == "/api/tags":
            return self._json(200, {"models": [_ollama_model()]})
        if path == "/api/version":
            return self._json(200, {"version": __version__})
        if path == "/api/ps":
            return self._json(200, {"models": []})
        self._json(404, {"error": {"message": f"no such path {path}"}})

    def do_POST(self):
        path = urlsplit(self.path).path.rstrip("/")
        if not self._guard():
            return
        body = self._body()
        if body is None:
            return
        if path == "/v1/chat/completions":
            return self._openai(body)
        if path == "/api/chat":
            return self._ollama(body)
        if path == "/api/show":
            return self._json(200, {"modelfile": "", "parameters": "", "template": "", "details": _ollama_model()["details"],
                                    "model_info": {}, "capabilities": ["completion"]})
        self._json(404, {"error": {"message": f"no such path {path}"}})

    def _begin_stream(self, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()

    def _openai(self, body: dict) -> None:
        rid, now = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
        pieces = self.assistant.reply(body.get("messages") or [])
        if not body.get("stream"):
            text = "".join(pieces)
            return self._json(200, {"id": rid, "object": "chat.completion", "created": now, "model": MODEL_NAME,
                                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
        self._begin_stream("text/event-stream")

        def chunk(delta: dict, finish=None) -> bytes:
            return ("data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "created": now, "model": MODEL_NAME,
                                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()
        try:
            self.wfile.write(chunk({"role": "assistant", "content": ""}))
            for p in pieces:
                self.wfile.write(chunk({"content": p}) if p else b": keep-alive\n\n")
                self.wfile.flush()
            self.wfile.write(chunk({}, "stop") + b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                      # the client left; the run goes on and its text is shown with the next message

    def _ollama(self, body: dict) -> None:
        pieces = self.assistant.reply(body.get("messages") or [])

        def line(content: str, done: bool) -> bytes:
            d = {"model": body.get("model") or MODEL_NAME, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "message": {"role": "assistant", "content": content}, "done": done}
            if done:
                d.update({"done_reason": "stop", "total_duration": 0, "eval_count": 0, "prompt_eval_count": 0})
            return (json.dumps(d) + "\n").encode()
        if body.get("stream") is False:
            text = "".join(pieces)
            d = json.loads(line(text, True))
            return self._json(200, d)
        self._begin_stream("application/x-ndjson")
        try:
            for p in pieces:
                if p:
                    self.wfile.write(line(p, False))
                    self.wfile.flush()
            self.wfile.write(line("", True))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _ollama_model() -> dict:
    return {"name": MODEL_NAME, "model": MODEL_NAME, "modified_at": "2026-01-01T00:00:00Z", "size": 0, "digest": "heardos",
            "details": {"family": "heardos", "families": ["heardos"], "parameter_size": "", "quantization_level": "", "format": "harness"}}


def _split_bind(bind: str) -> tuple[str, int]:
    host, _, port = bind.rpartition(":")
    return (host.strip("[]") or "127.0.0.1"), int(port or 11440)


def make_server(assistant: Assistant, bind: str = "127.0.0.1:11440", token: str = "", cors_origin: Optional[str] = None,
                allowed_hosts: tuple[str, ...] = ()) -> ThreadingHTTPServer:
    if not token or len(token) < 16:
        raise ValueError("heardos-serve needs a bearer token of at least 16 characters")
    host, port = _split_bind(bind)
    wildcard = host in ("0.0.0.0", "::")
    hosts = frozenset({*LOCAL_NAMES, host.lower(), *(h.strip().lower() for h in allowed_hosts if h.strip())})
    cls = type("HeardOSHandler", (Handler,), {"assistant": assistant, "token": token, "cors_origin": (cors_origin or "").rstrip("/") or None,
                                               "hosts": hosts, "any_ip_host": wildcard})
    srv = ThreadingHTTPServer((host, port), cls)
    srv.daemon_threads = True
    return srv


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="heardos-serve", description="the HeardOS model harness behind the OpenAI and Ollama chat protocols, "
                                                                   "for chat front ends you already use")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--install", metavar="ID", help="an install made by this app (heardos-cli list)")
    src.add_argument("--sim", action="store_true", help="a simulated install (demo, practice)")
    ap.add_argument("--api", choices=["ollama", "openai"], default="ollama", help="the model server the harness itself talks to")
    ap.add_argument("--model", default=L.DEFAULT_MODEL)
    ap.add_argument("--endpoint")
    ap.add_argument("--bind", default="127.0.0.1:11440", help="address:port to listen on (default: this machine only)")
    ap.add_argument("--token", default=os.environ.get("HEARDOS_SERVE_TOKEN"),
                    help="bearer token clients must send, 16+ characters; better HEARDOS_SERVE_TOKEN in the environment, which keeps it "
                         "out of the process list. Without one a random token is made and printed at start")
    ap.add_argument("--cors-origin", metavar="URL", help="the one browser origin allowed to call directly (e.g. https://hollama.example); "
                                                         "not needed for front ends with a server of their own, such as Open WebUI")
    ap.add_argument("--allowed-host", action="append", default=[], metavar="NAME",
                    help="another name clients use for this server in the Host header (repeatable)")
    ap.add_argument("--allow-changes", action="store_true", help="approve installs/restarts/settings without asking (removals and "
                                                                 "custom apps still ask; refused for a model outside the home network)")
    ap.add_argument("--max-steps", type=int, default=12)
    a = ap.parse_args(argv)
    if a.token is not None and len(a.token) < 16:
        sys.exit("the token must be at least 16 characters (python3 -c 'import secrets; print(secrets.token_urlsafe(24))')")
    try:
        model = L.make(a.api, a.model, a.endpoint)
    except L.LLMError as e:
        sys.exit(str(e))
    if a.allow_changes and model.external:
        sys.exit("--allow-changes is only for a model on this machine or the home network: a third party's model must not "
                 "change the install without a person saying yes")
    made = not a.token
    token = a.token or secrets.token_urlsafe(24)
    from .cli import backend_for
    b = backend_for(a)
    asst = Assistant(b, model, allow_changes=a.allow_changes, max_steps=a.max_steps)
    srv = make_server(asst, a.bind, token, a.cors_origin, tuple(a.allowed_host))
    print(f"HeardOS assistant for {b.domain} on http://{a.bind}  (model '{MODEL_NAME}'; OpenAI: /v1, Ollama: /api; bearer token required)"
          + (f"\nToken for this run (set HEARDOS_SERVE_TOKEN to keep one): {token}" if made else "")
          + ("\nThe model runs outside your home network: it sees scrubbed data, error lines of logs only, and every change asks."
             if model.external else ""), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
