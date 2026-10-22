"""The harness: lets a small local model (3-8B, e.g. llama3.2:3b or qwen2.5:7b in Ollama) install and configure stacks
through manage/tools.py, safely.

Design, aimed at models that plan badly but follow instructions and lists:
  * Two constrained calls per turn. First the model picks the tool from an enum (with a one-line thought before it),
    then fills that tool's arguments against its own schema, whose enums are the live install's real app and person
    names. It cannot name a tool or app that does not exist, and it never sees more than one tool's arguments at once.
  * No long chat history. Every turn is rebuilt from: the task, a numbered list of what was done (older results cut to
    a line), and the last result in full. Small models lose the thread in long transcripts; this keeps the prompt short
    and the current state explicit.
  * `overview` runs before the first turn, so the model starts from facts instead of guesses.
  * Intent first. The model classifies the request (question, add an app, fix an app, ...) and from then on is offered
    only that intent's tools. A request about houseplants cannot drift into giving people access or installing music.
  * Playbooks for the common requests. "Add an app" first asks which catalogue app fits (a numbered pick, or "none of
    these"); a pick is installed directly, "none" leads to search -> inspect -> add_custom_app. "Fix", "setting" and
    "start/stop" ask which installed app it is about and fetch its status, logs or settings before the model decides.
  * Questions: after each read, "can it be answered from what is above?"; at most four reads, then it answers.
  * A "done?" check after every successful change (a yes/no question, which small models answer well), and at most
    three changes per request. Without these, small models keep "improving" things after the task is complete.
  * Arguments are repaired (schema.coerce) and checked; a tool's preview() rejects requests that cannot work and the
    reason goes back to the model, which usually fixes it on the next turn.
  * Changes need the operator's yes (or --allow-changes); removals always ask. A refusal ends the run: a small model
    told "no" otherwise tries a neighbouring change (an eval saw it move on to removing a different app).
  * Repeating a call that already succeeded is not executed again, and that tool is left out of the next choice;
    three such loops end the run.
  * The final answer is followed by what the harness itself recorded as done, so a model that claims success it did
    not achieve is visibly contradicted.

Privacy and trust (see harness/redact.py):
  * Every message is scrubbed before it is sent (tokens, passwords in URLs, e-mail and public IP addresses, long random
    strings, and every secret the install knows of).
  * A model outside this machine and the home network (llm.external) is a third party: it sees only the error lines of
    a log and people's usernames (not their names), and "allow changes" does not apply to it: every change asks.
  * add_custom_app always asks (tools.Tool.always_ask).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..manage import tools as T
from ..manage.backend import Backend, ManageError
from .llm import LLM, LLMError
from .redact import scrub_messages
from .schema import check, coerce

FINISH = "finish"
MAX_CHANGES = 3
CHANGE_CAP = {"add_app": 2, "add_custom": 2, "remove_app": 1}      # per request type; the rest share MAX_CHANGES
READ_TOOLS = ["overview", "list_apps", "app_status", "app_logs", "app_settings", "health_check", "list_people"]
INTENTS = {
    "question": ("Answer a question about the apps, people or servers. Changes nothing.", READ_TOOLS),
    "add_app": ("Install or add an app: from the catalogue, or any other app from a Docker image.",
                ["list_apps", "install_app", "search_images", "inspect_image", "add_custom_app"]),
    "fix_app": ("Something is broken, down or misbehaving: find out why and fix it.", ["app_status", "app_logs", "control_app", "health_check"]),
    "start_stop": ("Start, stop, restart or update a whole app (not a setting or feature inside it).", ["app_status", "control_app"]),
    "setting": ("Change or undo a setting inside an app: an option, a feature or logging turned on or off, a variable "
                "such as LOG_LEVEL=DEBUG.", ["app_settings", "change_setting", "reset_setting"]),
    "access": ("Let a person use an app.", ["list_people", "give_access"]),
    "remove_app": ("Remove an app.", ["remove_app"]),
    "other": ("Anything else.", None),
}
IMAGE_IN_TASK = __import__("re").compile(r"\b[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9._-]+")
# not offered to the model as a kind: the harness switches add_app to this when no catalogue app fits
ADD_CUSTOM = ("Add an app that is not in the catalogue, from a Docker image.", ["search_images", "inspect_image", "add_custom_app"])
# read tools the harness runs itself right after classifying, so the model starts with the facts this kind of request
# needs (small models skip "look it up first" steps, then invent names)
GROUNDING = {"access": ["list_people"]}
# "which app is this about?": a pick from a labelled list (small models are good at that, bad at choosing the right tool).
# The harness then fetches what that kind of request needs for the picked app itself.
FOCUS_READS = {"fix_app": ["app_status", "app_logs"], "setting": ["app_settings"], "start_stop": ["app_status"]}
SYSTEM = """You are the HeardOS assistant. HeardOS is a family's self-hosted cloud: apps such as photos, documents and \
recipes run on a home server, each at its own web address with one family sign-in.
You work only through the tools below, ONE tool per turn. Each turn shows the task and everything done so far.

Tools:
{tools}
- finish: stop and tell the user the result in plain words.

Rules:
1. Never guess names. App names come from the overview or list_apps; image names from search_images.
2. To add an app, first look in the catalogue (list_apps) for one that does the job, even under another name, and use
   install_app. Only if none fits: search_images, then inspect_image, then add_custom_app.
3. An app that misbehaves: app_logs to see why, then control_app with action restart.
4. Do not repeat a tool that already succeeded; use its result. If a tool failed, read why and fix the arguments.
5. When the task is done, or cannot be done, use finish. Do not do more than the task asks."""


@dataclass
class Turn:
    n: int
    tool: str
    args: dict
    ok: bool
    text: str
    thought: str = ""
    risk: str = "read"
    approved: Optional[bool] = None
    seen: Optional[str] = None       # what the model was shown, when less than `text` (an external model)

    @property
    def for_model(self) -> str:
        return self.text if self.seen is None else self.seen


@dataclass
class Policy:
    allow_changes: bool = False                                         # run `change` tools without asking
    confirm: Optional[Callable[[str, dict, str, str], bool]] = None    # (tool, args, preview, risk) -> yes?


@dataclass
class RunResult:
    answer: str
    turns: list[Turn] = field(default_factory=list)
    stopped: str = "finished"        # finished | refused | max_steps | stuck | model_error

    @property
    def changes(self) -> list[Turn]:
        return [t for t in self.turns if t.risk != "read" and t.ok]

    def report(self) -> str:
        done = [f"- {t.tool}({_args_text(t.args)}): {t.text.splitlines()[0][:200]}" for t in self.changes]
        refused = [f"- {t.tool}({_args_text(t.args)}): not approved" for t in self.turns if t.approved is False]
        out = self.answer.strip()
        out += "\n\nWhat was actually changed:\n" + ("\n".join(done) if done else "- nothing")
        if refused:
            out += "\nNot approved:\n" + "\n".join(refused)
        if self.stopped not in ("finished", "refused"):
            out += f"\n(stopped: {self.stopped})"
        return out


def _args_text(a: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in a.items())


def _words(text: str) -> set[str]:
    import re
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", text.lower())} - {"the", "and", "for", "our", "can", "you", "app", "thi", "that",
                                                                              "with", "some", "set", "something", "want", "need", "please"}


def _overlap(task: str, text: str) -> int:
    """Shared words, counting a word inside a longer one too (houseplants ~ plants)."""
    a, b = _words(task), _words(text)
    return sum(1 for x in a for y in b if x == y or (len(y) > 3 and y in x) or (len(x) > 3 and x in y))


def _key(tool: str, args: dict) -> str:
    return tool + json.dumps(args, sort_keys=True)


class Agent:
    def __init__(self, backend: Backend, llm: LLM, policy: Optional[Policy] = None, max_steps: int = 12,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.b, self.llm, self.policy, self.max_steps = backend, llm, policy or Policy(), max_steps
        self.on_event = on_event or (lambda kind, data: None)
        self.external = bool(getattr(llm, "external", False))
        if self.external and self.policy.allow_changes:
            self.policy = Policy(allow_changes=False, confirm=self.policy.confirm)
            self.on_event("notice", {"text": "The model runs outside your home network, so every change asks first."})

    def _for_model(self, tool: str, res: "T.ToolResult") -> Optional[str]:
        """What an external model may see of a result (None = all of it)."""
        if not self.external or not res.ok:
            return None
        if tool == "app_logs":
            lines = res.text.splitlines()
            errs = [l for l in lines[1:] if T.looks_like_error(l)][-20:]
            return "\n".join([lines[0] if lines else "Log lines:"] + (errs or ["(no error lines; the whole log is shown to the owner only)"]))
        if tool == "list_people" and isinstance(res.data, list):
            return "People:\n" + "\n".join(f"- {u['username']}{' (admin)' if u.get('admin') else ''}: apps: {', '.join(u.get('apps') or []) or 'none'}"
                                           for u in res.data)
        return None

    # ---------------------------------------------------------------- prompt pieces
    def _tools(self, intent: str = "other", changes: int = 0) -> list[T.Tool]:
        allowed = ADD_CUSTOM[1] if intent == "add_custom" else INTENTS.get(intent, INTENTS["other"])[1]
        out = [t for t in T.TOOLS if (allowed is None or t.name in allowed) and t.usable(self.b)]
        if changes >= CHANGE_CAP.get(intent, MAX_CHANGES):
            out = [t for t in out if t.risk == "read"]
        return out

    def _intent(self, task: str, overview: str) -> str:
        names = list(INTENTS)
        schema = {"type": "object", "properties": {"thought": {"type": "string", "maxLength": 200}, "intent": {"type": "string", "enum": names}},
                  "required": ["thought", "intent"]}
        text = (f"Task: {task}\n\nThe cloud right now:\n{overview}\n\nWhat kind of request is the task? Kinds:\n"
                + "\n".join(f"- {k}: {d}" for k, (d, _) in INTENTS.items()) + "\nThink in one short sentence, then choose.")
        r = self._ask([{"role": "system", "content": "You sort requests for the HeardOS assistant."}, {"role": "user", "content": text}], schema)
        intent = coerce(schema, r).get("intent")
        return intent if intent in INTENTS else "other"

    def _pick(self, task: str, question: str, rows: list[dict]) -> Optional[str]:
        """Ask which of `rows` the task means; returns its stack id, or None for 'none of these'.
        A numbered list, answered with the number: measured on llama3.2:3b, numbers picked the right app 5 of 5 times
        where app ids, human names or whole lines as the choices got it wrong 2-3 times (a familiar-looking id such as
        'homeassistant' wins on looks). Most likely first, by word overlap with the task."""
        rows = sorted(rows, key=lambda r: -_overlap(task, f"{r['name']} {r['stack']} {r.get('blurb', '')}"))
        lines = [f"{r['name']}: {r.get('blurb', '')}" for r in rows] + ["None of these: the task asks for something not in this list"]
        schema = {"type": "object", "properties": {"thought": {"type": "string", "maxLength": 200},
                                                   "number": {"type": "integer", "enum": list(range(1, len(lines) + 1))}},
                  "required": ["thought", "number"]}
        text = (f"Task: {task}\n\n{question}\n" + "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1))
                + "\n\nThink in one short sentence about what the task needs, then give the number.")
        r = self._ask([{"role": "system", "content": "You match requests to apps."}, {"role": "user", "content": text}], schema)
        n = coerce(schema, r).get("number")
        app = rows[n - 1]["stack"] if isinstance(n, int) and 1 <= n <= len(rows) else None
        self.on_event("pick", {"question": question, "app": app, "thought": str(r.get("thought", ""))[:200]})
        return app

    def _answerable(self, task: str, turns: list) -> bool:
        """Questions: do the results so far answer it? (Small models otherwise keep reading.)"""
        schema = {"type": "object", "properties": {"thought": {"type": "string", "maxLength": 200}, "answerable": {"type": "string", "enum": ["yes", "no"]}},
                  "required": ["thought", "answerable"]}
        text = self._state(task, turns) + ("\n\nCan the task be answered from the results above? Say yes if they contain the "
                                           "facts it asks about, even if not every detail.")
        r = self._ask([{"role": "system", "content": "You decide whether enough is known."}, {"role": "user", "content": text}], schema)
        return coerce(schema, r).get("answerable") != "no"

    def _complete(self, task: str, turns: list) -> bool:
        """Is the task done? Asked as 'what is missing' first: small models answer a bare yes/no about 'everything' with no."""
        schema = {"type": "object", "properties": {"missing": {"type": "string", "maxLength": 160}, "done": {"type": "string", "enum": ["yes", "no"]}},
                  "required": ["missing", "done"]}
        changed = "\n".join(f"- {t.tool}({_args_text(t.args)}): {(t.for_model.splitlines() or [''])[0][:160]}" for t in turns if t.risk != "read" and t.ok)
        text = ("Compare a task with the changes made for it.\n\nExamples:\n"
                "Task: Install the photo app.\nChanged:\n- install_app(app='immich'): done\n"
                '{"missing": "nothing", "done": "yes"}\n'
                "Task: Install the music app and let Bob use it.\nChanged:\n- install_app(app='navidrome'): done\n"
                '{"missing": "Bob\'s access to the music app", "done": "no"}\n\n'
                f"Now:\nTask: {task}\nChanged:\n{changed}\n\nmissing = what the task explicitly names that the changes do not cover "
                "(or nothing). Extra improvements the task did not ask for are NOT missing.")
        r = self._ask([{"role": "system", "content": "You check whether a task is finished."}, {"role": "user", "content": text}], schema)
        missing = str(r.get("missing", "")).strip().lower().strip(".")
        if missing in ("", "nothing", "none", "n/a", "no", "nothing is missing"):
            return True
        return coerce(schema, r).get("done") != "no"

    def _system(self, tools: list[T.Tool]) -> str:
        lines = []
        for t in tools:
            args = ", ".join(t.params(self.b).keys())
            ask = {"change": " [asks the owner first]", "danger": " [asks the owner first; removes things]"}.get(t.risk, "")
            lines.append(f"- {t.name}{f'({args})' if args else ''}{ask}: {t.summary}")
        return SYSTEM.format(tools="\n".join(lines))

    def _state(self, task: str, turns: list[Turn]) -> str:
        out = [f"Task: {task}", "", "Done so far:"]
        if not turns:
            out.append("(nothing yet)")
        for t in turns[:-1]:
            first = t.for_model.splitlines()[0] if t.for_model else ""
            out.append(f"{t.n}. {t.tool}({_args_text(t.args)}) -> {'OK' if t.ok else 'FAILED'}: {first[:160]}")
        if turns:
            t = turns[-1]
            out.append(f"{t.n}. {t.tool}({_args_text(t.args)}) -> {'OK' if t.ok else 'FAILED'}, full result:")
            out.append(t.for_model)
        return "\n".join(out)

    def _arg_help(self, tool: T.Tool) -> str:
        lines = [f"Now give the arguments for {tool.name}: {tool.summary}", "Fields:"]
        req = set(tool.required)
        for k, p in tool.params(self.b).items():
            desc = p.get("description", "")
            if "enum" in p:
                desc = (desc + "; " if desc else "") + "one of: " + ", ".join(map(str, p["enum"]))
            lines.append(f"- {k} ({p.get('type', 'string')}, {'required' if k in req else 'optional'}): {desc}")
        # Never a concrete example: small models copy example values verbatim (an eval once installed the example app).
        lines.append("Use only values that come from the task and the results above.")
        return "\n".join(lines)

    # ---------------------------------------------------------------- model calls
    def _ask(self, messages: list[dict], schema: dict) -> dict:
        messages = scrub_messages(messages, self.b.secret_values())
        try:
            return self.llm.json(messages, schema)
        except (LLMError, ValueError) as e:
            self.on_event("retry", {"error": str(e)})
            return self.llm.json(messages + [{"role": "user", "content": "Answer with one JSON object only."}], schema)

    def _choose(self, system: str, state: str, names: list[str]) -> tuple[str, str, list[dict]]:
        schema = {"type": "object", "properties": {"thought": {"type": "string", "maxLength": 200}, "tool": {"type": "string", "enum": names}},
                  "required": ["thought", "tool"]}
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": state + "\n\nWhich tool next? Think in one short sentence, then choose."}]
        r = self._ask(msgs, schema)
        tool = coerce(schema, r).get("tool")
        if tool not in names:
            tool = FINISH
        return tool, str(r.get("thought", ""))[:200], msgs + [{"role": "assistant", "content": json.dumps(r)}]

    def _fill(self, msgs: list[dict], tool: T.Tool) -> dict:
        schema = tool.schema(self.b)
        r = self._ask(msgs + [{"role": "user", "content": self._arg_help(tool)}], schema)
        return coerce(schema, r)

    def _answer(self, msgs: list[dict]) -> str:
        schema = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 800}}, "required": ["answer"]}
        r = self._ask(msgs + [{"role": "user", "content": "Write the answer for the user: what was done, or why it could not be "
                                                          "done, in two or three plain sentences. Only claim what the results above show."}], schema)
        return str(r.get("answer", "")).strip() or "Done."

    # ---------------------------------------------------------------- one tool call
    def _execute(self, n: int, tool: T.Tool, args: dict, thought: str, done: dict, refused: set) -> Turn:
        key = _key(tool.name, args)
        t = Turn(n, tool.name, args, False, "", thought, tool.risk)
        errs = check(tool.schema(self.b), args)
        if errs:
            t.text = "Wrong arguments: " + "; ".join(errs) + ". Try again with corrected arguments."
            return t
        if key in done:
            t.ok, t.text = True, f"Already done in step {done[key]}; nothing new. Use that result, or finish."
            return t
        if key in refused:
            t.text = "The owner already said no to exactly this. Choose something else or finish."
            return t
        if tool.preview:
            try:
                ok, preview = tool.preview(self.b, args)
            except ManageError as e:
                ok, preview = False, str(e)
            if not ok:
                t.text = preview
                return t
            ask = tool.risk == "danger" or tool.always_ask or (tool.risk == "change" and not self.policy.allow_changes)
            if ask:
                self.on_event("confirm", {"tool": tool.name, "args": args, "preview": preview, "risk": tool.risk})
                yes = bool(self.policy.confirm and self.policy.confirm(tool.name, args, preview, tool.risk))
                t.approved = yes
                if not yes:
                    refused.add(key)
                    t.text = "The owner did not approve this, so nothing was changed. Do not ask again; choose something else or finish."
                    return t
        self.on_event("call", {"tool": tool.name, "args": args})
        try:
            res = tool.run(self.b, args)
        except ManageError as e:
            res = T.ToolResult(False, str(e))
        except Exception as e:  # noqa: BLE001 — the model gets the reason; the run goes on
            res = T.ToolResult(False, f"{type(e).__name__}: {e}"[:300])
        t.ok, t.text = res.ok, res.text
        t.seen = self._for_model(tool.name, res)
        if res.ok:
            done[key] = n
        return t

    # ---------------------------------------------------------------- the loop
    def run(self, task: str) -> RunResult:
        turns: list[Turn] = []
        done: dict[str, int] = {}
        refused: set[str] = set()
        loops = 0
        first = self._execute(1, T.BY_NAME["overview"], {}, "look first", done, refused)   # ground the model in facts
        turns.append(first)
        self.on_event("result", {"turn": first})
        try:
            intent = self._intent(task, first.for_model)
        except (LLMError, ValueError) as e:
            return RunResult(f"The model failed to answer: {e}", turns, "model_error")
        self.on_event("intent", {"intent": intent, "about": INTENTS[intent][0]})
        def harness_call(name: str, args: dict, why: str) -> Turn:
            t = self._execute(len(turns) + 1, T.BY_NAME[name], args, why, done, refused)
            turns.append(t)
            self.on_event("result", {"turn": t})
            return t
        for name in GROUNDING.get(intent, []):
            if T.BY_NAME[name].usable(self.b):
                harness_call(name, {}, "look it up first")
        try:
            rows = self.b.inventory(with_state=False)
            if intent == "add_app":
                missing = [r for r in rows if not r["installed"] and r["kind"] == "app"]
                named_image = IMAGE_IN_TASK.search(task)          # "add X (image owner/x:1)": not a catalogue app
                app = (self._pick(task, "Which of these catalogue apps (not installed yet) does the task ask for?", missing)
                       if missing and not named_image else None)
                if named_image:
                    harness_call("inspect_image", {"image": named_image.group(0)}, "the task names this image")
                if app:
                    t = harness_call("install_app", {"app": app}, f"the catalogue app {app} fits the task")
                    if t.approved is False:
                        return RunResult(self._answer([{"role": "system", "content": self._system(self._tools(intent))},
                                                       {"role": "user", "content": self._state(task, turns)}]), turns, "refused")
                    if t.ok and self._complete(task, turns):
                        return RunResult(self._answer([{"role": "system", "content": self._system(self._tools(intent))},
                                                       {"role": "user", "content": self._state(task, turns)}]), turns)
                else:
                    intent = "add_custom"
            elif intent in FOCUS_READS:
                inst = [r for r in rows if r["installed"]]
                app = self._pick(task, "Which of these installed apps is the task about?", inst)
                for name in FOCUS_READS[intent] if app else []:
                    harness_call(name, {"app": app}, f"look at {app}")
        except (LLMError, ValueError) as e:
            return RunResult(f"The model failed to answer: {e}", turns, "model_error")
        reads = 0
        for n in range(len(turns) + 1, self.max_steps + len(turns) + 1):
            if intent == "question":
                try:
                    enough = reads >= 4 or self._answerable(task, turns)
                except (LLMError, ValueError):
                    enough = True
                if enough:
                    return RunResult(self._answer([{"role": "system", "content": self._system(self._tools(intent))},
                                                   {"role": "user", "content": self._state(task, turns)}]), turns)
                reads += 1
            tools = self._tools(intent, sum(1 for t in turns if t.risk != "read" and t.ok))
            if turns[-1].text.startswith("Already done") and len(tools) > 1:
                tools = [x for x in tools if x.name != turns[-1].tool]     # repeated itself: that tool is off the menu once
            system = self._system(tools)
            state = self._state(task, turns)
            try:
                name, thought, msgs = self._choose(system, state, [t.name for t in tools] + [FINISH])
                self.on_event("thought", {"n": n, "tool": name, "thought": thought})
                if name == FINISH:
                    return RunResult(self._answer(msgs), turns)
                tool = T.BY_NAME[name]
                args = self._fill(msgs, tool) if tool.params(self.b) else {}
            except (LLMError, ValueError) as e:
                return RunResult(f"The model failed to answer: {e}", turns, "model_error")
            t = self._execute(n, tool, args, thought, done, refused)
            turns.append(t)
            self.on_event("result", {"turn": t})
            if t.approved is False:      # the owner said no: stop, instead of letting the model try something else
                return RunResult(self._answer([{"role": "system", "content": self._system(tools)},
                                               {"role": "user", "content": self._state(task, turns)}]), turns, "refused")
            if t.ok and t.risk != "read" and not t.text.startswith("Already done"):
                try:
                    finished = self._complete(task, turns)
                except (LLMError, ValueError):
                    finished = True
                self.on_event("check", {"done": finished})
                if finished:
                    msgs = [{"role": "system", "content": self._system(tools)}, {"role": "user", "content": self._state(task, turns)}]
                    return RunResult(self._answer(msgs), turns)
            if t.text.startswith("Already done in step"):
                loops += 1
                if loops >= 3:
                    return RunResult("I kept repeating the same step, so I stopped.", turns, "stuck")
        msgs = [{"role": "system", "content": self._system(self._tools(intent))}, {"role": "user", "content": self._state(task, turns)}]
        try:
            ans = self._answer(msgs)
        except (LLMError, ValueError):
            ans = "I ran out of steps."
        return RunResult(ans, turns, "max_steps")
