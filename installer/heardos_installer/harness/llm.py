"""Model clients that return one JSON object per call, constrained to a schema by the server's grammar sampler:

  ollama   POST /api/chat  with  format=<schema>      (Ollama >= 0.5; llama.cpp grammar underneath)
  openai   POST /v1/chat/completions  with  response_format json_schema  (llama.cpp server, vLLM, LM Studio, LocalAI…)

Constrained decoding is the main reason a 3-8B model can drive the harness: it cannot emit a tool name that does not
exist, an app that is not installed, or broken JSON. Temperature is 0 so the same state gives the same choice.

A model server that is not on this machine or the private network (redact.is_local) is a third party: `external` is set,
the address must be https (the API key and the household's data travel to it), and the agent shares less with it.
Messages are scrubbed by the agent before they reach a client here (agent._ask).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional, Protocol

import httpx

from .redact import is_local

DEFAULT_MODEL = os.environ.get("HEARDOS_AGENT_MODEL", "qwen2.5:7b")
DEFAULT_ENDPOINT = os.environ.get("HEARDOS_AGENT_ENDPOINT", "http://127.0.0.1:11434")


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str
    external: bool

    def json(self, messages: list[dict], schema: dict) -> dict: ...


def _parse(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        v = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)          # servers without grammar support: take the first object
        if not m:
            raise LLMError(f"model did not answer with JSON: {text[:120]!r}")
        v = json.loads(m.group(0))
    if not isinstance(v, dict):
        raise LLMError("model answered with JSON that is not an object")
    return v


def _api_key(api_key: Optional[str]) -> Optional[str]:
    return api_key or os.environ.get("HEARDOS_AGENT_API_KEY") or None


def _endpoint(endpoint: str) -> tuple[str, bool]:
    """(endpoint, external); refuses plain http to a third party."""
    endpoint = endpoint.strip().rstrip("/")
    external = not is_local(endpoint)
    if external and not endpoint.lower().startswith("https://"):
        raise LLMError(f"{endpoint} is not on this machine or the home network: use its https:// address "
                       "(over http the API key and the household's data would cross the internet unencrypted)")
    return endpoint, external


class Ollama:
    def __init__(self, model: str = DEFAULT_MODEL, endpoint: str = DEFAULT_ENDPOINT, num_ctx: int = 8192, timeout: float = 600,
                 api_key: Optional[str] = None):
        self.endpoint, self.external = _endpoint(endpoint)
        self.model, self.num_ctx = model, num_ctx
        key = _api_key(api_key)                       # hosted Ollama (ollama.com) and proxies in front of one want a bearer key
        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {key}"} if key else None)
        self.name = f"ollama:{model}"

    def json(self, messages: list[dict], schema: dict) -> dict:
        body = {"model": self.model, "messages": messages, "stream": False, "format": schema, "keep_alive": "15m",
                "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": 700}}
        try:
            r = self.client.post(f"{self.endpoint}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"cannot reach Ollama at {self.endpoint}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LLMError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
        return _parse(r.json().get("message", {}).get("content", ""))


class OpenAICompat:
    def __init__(self, model: str = DEFAULT_MODEL, endpoint: str = "http://127.0.0.1:8080", api_key: Optional[str] = None, timeout: float = 600):
        endpoint, self.external = _endpoint(endpoint)
        self.model, self.endpoint = model, endpoint.removesuffix("/v1")
        self.headers = {"Authorization": f"Bearer {_api_key(api_key) or 'none'}"}
        self.client = httpx.Client(timeout=timeout)
        self.name = f"openai:{model}"

    def json(self, messages: list[dict], schema: dict) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 700,
                "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema, "strict": True}}}
        try:
            r = self.client.post(f"{self.endpoint}/v1/chat/completions", json=body, headers=self.headers)
        except httpx.HTTPError as e:
            raise LLMError(f"cannot reach {self.endpoint}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        return _parse(r.json()["choices"][0]["message"].get("content") or "")


def make(api: str = "ollama", model: str = DEFAULT_MODEL, endpoint: Optional[str] = None) -> LLM:
    if api == "ollama":
        return Ollama(model, endpoint or DEFAULT_ENDPOINT)
    if api == "openai":
        return OpenAICompat(model, endpoint or "http://127.0.0.1:8080")
    raise ValueError("api must be ollama or openai")
