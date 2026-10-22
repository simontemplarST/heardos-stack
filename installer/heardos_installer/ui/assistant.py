"""Assistant: type what you want in plain words; a local model does it through the harness (harness/agent.py) with the
same tools and checks as the Stacks screen. Every change is shown first and waits for Approve; removals always do."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from imgui_bundle import imgui

from . import widgets as w
from .stacks import _need, backend
from .theme import C, ICON_BAD, ICON_OK

EXAMPLES = ["Which apps are installed, and are they all running?", "Install something to keep track of our houseplants",
            "Add Uptime Kuma for the admins", "The home inventory app is broken, please fix it",
            "Let bob use the recipe app", "Turn on debug logging for Mealie (LOG_LEVEL=DEBUG)"]


@dataclass
class Pending:
    tool: str
    args: dict
    preview: str
    risk: str
    answered: threading.Event = field(default_factory=threading.Event)
    yes: bool = False


@dataclass
class AssistantState:
    lines: list[tuple[str, str]] = field(default_factory=list)     # (colour key, text)
    pending: Optional[Pending] = None
    thread: Optional[threading.Thread] = None
    scroll: bool = False

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def add(self, col: str, text: str) -> None:
        self.lines.append((col, text))
        self.scroll = True


def _state(st) -> AssistantState:
    if getattr(st, "assistant", None) is None:
        st.assistant = AssistantState()
    return st.assistant


def _start(st, task: str) -> None:
    from ..harness import llm as L
    from ..harness.agent import Agent, Policy
    a = _state(st)
    b = backend(st)
    d = st.draft
    a.add("ORANGE", f"> {task}")

    def confirm(tool, args, preview, risk) -> bool:
        p = Pending(tool, args, preview, risk)
        a.pending = p
        p.answered.wait()
        a.pending = None
        a.add("GOOD" if p.yes else "WARN", f"    {'approved' if p.yes else 'refused'}: {tool}")
        return p.yes

    def event(kind, data) -> None:
        if kind == "intent":
            a.add("MUTED", f"    request type: {data['intent']}")
        elif kind == "pick":
            a.add("MUTED", f"    {data['thought']}  -> {data['app'] or 'none of the catalogue apps'}")
        elif kind == "thought":
            a.add("MUTED", f"    {data['thought']}  -> {data['tool']}")
        elif kind == "result":
            t = data["turn"]
            head = t.text.splitlines()[0] if t.text else ""
            a.add("GOOD" if t.ok else "BAD", f"    {ICON_OK if t.ok else ICON_BAD} {t.tool}: {head[:220]}")
        elif kind == "retry":
            a.add("WARN", "    the model's answer was unusable; asking again")
        elif kind == "notice":
            a.add("WARN", f"    {data['text']}")

    def go() -> None:
        try:
            model = L.make(d.get("agent_api", "ollama"), d.get("agent_model") or L.DEFAULT_MODEL, d.get("agent_endpoint") or None)
            agent = Agent(b, model, Policy(allow_changes=bool(d.get("agent_auto")), confirm=confirm), on_event=event)
            res = agent.run(task)
            for line in res.report().splitlines():
                a.add("LIGHT", line)
        except Exception as e:  # noqa: BLE001
            a.add("BAD", f"stopped: {e}")
        st.tasks.clear("inv")      # the Stacks screen reloads what changed

    a.thread = threading.Thread(target=go, daemon=True, name="assistant")
    a.thread.start()


def assistant(st) -> None:
    if _need(st):
        return
    a = _state(st)
    d = st.draft
    w.title("Assistant")
    w.help_text("Ask in plain words. A model running on your own hardware (Ollama, llama.cpp, LM Studio…) does the work with "
                "the same tools as the Stacks screen. Every change is shown to you first; removals always ask.")
    if imgui.collapsing_header("Model", imgui.TreeNodeFlags_.default_open if not d.get("agent_model") else 0):
        apis = ["ollama", "openai"]
        imgui.set_next_item_width(160)
        ch, i = imgui.combo("API##agentapi", apis.index(d.get("agent_api", "ollama")), ["Ollama", "OpenAI-compatible"])
        if ch:
            d["agent_api"] = apis[i]; st.save_draft()
        imgui.same_line()
        imgui.set_next_item_width(300)
        ch, v = imgui.input_text_with_hint("address##agentep", "http://127.0.0.1:11434", d.get("agent_endpoint", ""))
        if ch:
            d["agent_endpoint"] = v.strip(); st.save_draft()
        imgui.same_line()
        imgui.set_next_item_width(180)
        ch, v = imgui.input_text_with_hint("model##agentmodel", "llama3.2:3b", d.get("agent_model", ""))
        if ch:
            d["agent_model"] = v.strip(); st.save_draft()
        ch, v = imgui.checkbox("Approve installs, restarts and setting changes without asking (removals and your own apps still ask; not for a hosted model)", bool(d.get("agent_auto")))
        if ch:
            d["agent_auto"] = v; st.save_draft()
        w.help_text("Small models work: the assistant offers them only the tools that fit the request and only real app names. "
                    "Try a model with heardos-agent --eval first.")
    imgui.spacing()
    h = imgui.get_content_region_avail().y - (240 if a.pending else 70)
    imgui.begin_child("assistant_log", imgui.ImVec2(0, max(h, 120)), imgui.ChildFlags_.borders)
    if not a.lines:
        imgui.text_colored(C["MUTED"], "For example:")
        for ex in EXAMPLES:
            if imgui.selectable(f"   {ex}", False)[0]:
                st.inputs["ask"] = ex
    for col, text in a.lines:
        imgui.push_text_wrap_pos(0)
        imgui.text_colored(C[col], text)
        imgui.pop_text_wrap_pos()
    if a.running and not a.pending:
        w.spinner_text("Thinking")
    if a.scroll:
        imgui.set_scroll_here_y(1.0)
        a.scroll = False
    imgui.end_child()
    p = a.pending
    if p:
        imgui.push_style_color(imgui.Col_.child_bg, C["SLATE"])
        imgui.begin_child("confirm", imgui.ImVec2(0, 0), imgui.ChildFlags_.always_use_window_padding | imgui.ChildFlags_.auto_resize_y)
        imgui.text_colored(C["BAD"] if p.risk == "danger" else C["ORANGE"], "The assistant wants to:")
        imgui.push_text_wrap_pos(0)
        imgui.text(p.preview)
        imgui.pop_text_wrap_pos()
        if w.primary("Approve"):
            p.yes = True
            p.answered.set()
        imgui.same_line()
        if w.secondary("Refuse"):
            p.yes = False
            p.answered.set()
        imgui.end_child()
        imgui.pop_style_color()
    imgui.set_next_item_width(-110)
    entered, st.inputs["ask"] = imgui.input_text_with_hint("##ask", "What should I do?", st.inputs.get("ask", ""),
                                                            imgui.InputTextFlags_.enter_returns_true)
    imgui.same_line()
    can = bool(st.inputs["ask"].strip()) and not a.running
    if (w.primary("Send", can) or (entered and can)):
        task = st.inputs.pop("ask").strip()
        _start(st, task)
    if a.lines and not a.running:
        imgui.same_line()
        if w.secondary("Clear"):
            a.lines.clear()
