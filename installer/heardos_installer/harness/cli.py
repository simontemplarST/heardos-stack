"""heardos-agent: run the model harness from a terminal.

  heardos-agent --sim "install something for our houseplants"          # a simulated install, nothing real touched
  heardos-agent --install smith-ab12 "why is the inventory app down?"   # a real install (unlocks the vault)
  heardos-agent --install smith-ab12                                    # interactive: one task per line
  heardos-agent --eval [--only plants,uptime] [--json]                  # score a model on the built-in scenarios

Model: --api ollama (default, http://127.0.0.1:11434) or --api openai (any OpenAI-compatible server: llama.cpp, vLLM,
LM Studio), --model NAME, --endpoint URL; or HEARDOS_AGENT_MODEL / HEARDOS_AGENT_ENDPOINT.
Changes are shown and need a yes on the terminal; --allow-changes approves them up front (removals and custom apps still
ask; refused for a model outside this machine and the home network). Model and tool text is printed without control
characters, so a log line or a model cannot send escape sequences to the terminal.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import llm as L
from .redact import term_text as _t


def backend_for(a):
    if a.sim:
        from ..manage.sim import SimBackend
        return SimBackend(delay=0.2)
    if not a.install:
        sys.exit("give --install ID (see `heardos-cli list`) or --sim")
    from ..__main__ import _prompt, _vault
    from ..manage.backend import RealBackend
    from ..session import Installation
    return RealBackend(Installation(a.install, _vault(), prompt=_prompt))


def tty_confirm(tool: str, args: dict, preview: str, risk: str) -> bool:
    print(f"\n  ┌ the assistant wants to: {tool}\n  │ {_t(preview)}")
    if not sys.stdin.isatty():
        print("  └ no terminal to approve on: refused")
        return False
    if risk == "danger":
        want = str(args.get("app", "yes"))
        ok = input(f"  └ type {want} to approve, anything else refuses: ").strip() == want
    else:
        ok = input("  └ approve? [y/N] ").strip().lower() in ("y", "yes")
    return ok


def printer(kind: str, d: dict) -> None:
    if kind == "thought":
        print(_t(f"  · {d['thought']}  → {d['tool']}"), flush=True)
    elif kind == "result":
        t = d["turn"]
        head = t.text.splitlines()[0] if t.text else ""
        print(_t(f"  {'✓' if t.ok else '✗'} {t.tool}({', '.join(f'{k}={v}' for k, v in t.args.items())}): {head[:150]}"), flush=True)
    elif kind == "intent":
        print(f"  ▸ request type: {d['intent']} ({d['about']})", flush=True)
    elif kind == "pick":
        print(_t(f"  ▸ {d['question']} → {d['app'] or 'none'}  ({d['thought']})"), flush=True)
    elif kind == "check":
        print(f"  ▸ task complete: {'yes' if d['done'] else 'no, continuing'}", flush=True)
    elif kind == "retry":
        print(_t(f"  ! model answer unusable, asking again ({d['error'][:80]})"), flush=True)
    elif kind == "notice":
        print(f"  ▸ {d['text']}", flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="heardos-agent", description="HeardOS model harness: a small local model installs and configures stacks")
    ap.add_argument("task", nargs="*")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--install", metavar="ID", help="an install made by this app (heardos-cli list)")
    src.add_argument("--sim", action="store_true", help="a simulated install (demo, practice)")
    ap.add_argument("--api", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--model", default=L.DEFAULT_MODEL)
    ap.add_argument("--endpoint")
    ap.add_argument("--allow-changes", action="store_true", help="approve installs/restarts/settings without asking (removals and custom "
                                                                 "apps still ask; refused for a model outside the home network)")
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--eval", action="store_true", help="run the built-in scenarios against a simulated install and score the model")
    ap.add_argument("--only", help="comma-separated scenario ids (with --eval)")
    ap.add_argument("--json", action="store_true", help="machine-readable output (with --eval)")
    a = ap.parse_args(argv)
    try:
        model = L.make(a.api, a.model, a.endpoint)
    except L.LLMError as e:
        sys.exit(str(e))
    if a.allow_changes and model.external:
        sys.exit("--allow-changes is only for a model on this machine or the home network: a third party's model must not "
                 "change the install without a person saying yes")
    if a.eval:
        from . import evals
        res = evals.run(model, a.only.split(",") if a.only else None, verbose=not a.json, max_steps=a.max_steps)
        if a.json:
            print(json.dumps(res, indent=1))
        sys.exit(0 if res["passed"] == res["total"] else 1)
    from .agent import Agent, Policy
    b = backend_for(a)
    agent = Agent(b, model, Policy(allow_changes=a.allow_changes, confirm=tty_confirm), max_steps=a.max_steps, on_event=printer)
    tasks = [" ".join(a.task)] if a.task else None
    if tasks is None:
        if not sys.stdin.isatty():
            tasks = [l.strip() for l in sys.stdin if l.strip()]
        else:
            print(f"HeardOS assistant ({model.name}) on {b.domain}. One task per line; empty line or Ctrl+D quits.")
    while True:
        if tasks is not None:
            if not tasks:
                break
            task = tasks.pop(0)
        else:
            try:
                task = input("\ntask> ").strip()
            except EOFError:
                break
            if not task:
                break
        res = agent.run(task)
        print("\n" + _t(res.report()))


if __name__ == "__main__":
    main()
