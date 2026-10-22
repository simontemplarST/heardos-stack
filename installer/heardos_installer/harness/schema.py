"""Just enough JSON Schema for tool arguments: type, enum, required, maxLength, additionalProperties: false, arrays of
strings. coerce() repairs the mistakes small models make most (a number sent as a string, an enum value in the wrong
case or with extra words, stray fields, empty strings for optional fields); check() then reports what is still wrong in
words the model can act on."""
from __future__ import annotations

from typing import Any


def _match_enum(v: Any, enum: list) -> Any:
    if v in enum:
        return v
    s = str(v).strip().lower()
    for e in enum:
        if str(e).lower() == s:
            return e
    hits = [e for e in enum if isinstance(e, str) and (s.startswith(e.lower()) or e.lower() in s.split())]
    if len(hits) == 1:   # "mealie app", "restart it" -> the one enum value it contains
        return hits[0]
    if isinstance(v, str) and v.strip().isdigit() and int(v) in enum:
        return int(v)
    return v


def coerce(schema: dict, args: Any) -> dict:
    if not isinstance(args, dict):
        return {}
    props = schema.get("properties") or {}
    req = set(schema.get("required") or [])
    out: dict = {}
    for k, v in args.items():
        p = props.get(k)
        if p is None:
            continue                                   # stray field: drop instead of failing
        if v in ("", None, []) and k not in req:
            continue
        t = p.get("type")
        if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            v = int(v.strip())
        elif t == "integer" and isinstance(v, float) and v.is_integer():
            v = int(v)
        elif t == "string" and isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        elif t == "string" and isinstance(v, str):
            v = v.strip()
        elif t == "array" and isinstance(v, str):
            v = [x.strip() for x in v.replace(";", "\n").splitlines() if x.strip()]
        if "enum" in p:
            v = _match_enum(v, p["enum"])
        out[k] = v
    return out


def check(schema: dict, args: Any) -> list[str]:
    if not isinstance(args, dict):
        return ["arguments must be a JSON object"]
    errs = []
    props = schema.get("properties") or {}
    for k in schema.get("required") or []:
        if k not in args or args[k] in ("", None):
            errs.append(f"'{k}' is required")
    if schema.get("additionalProperties") is False:
        errs += [f"'{k}' is not an argument of this tool (arguments: {', '.join(props) or 'none'})" for k in args if k not in props]
    for k, v in args.items():
        p = props.get(k)
        if not p:
            continue
        t = p.get("type")
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "array": isinstance(v, list), "boolean": isinstance(v, bool), "object": isinstance(v, dict)}.get(t, True)
        if not ok:
            errs.append(f"'{k}' must be a {t}")
            continue
        if "enum" in p and v not in p["enum"]:
            choices = ", ".join(map(str, p["enum"][:30]))
            errs.append(f"'{k}' must be one of: {choices} (got {v!r})")
        if t == "string" and p.get("maxLength") and len(v) > p["maxLength"]:
            errs.append(f"'{k}' is too long (max {p['maxLength']} characters)")
        if t == "array":
            it = (p.get("items") or {}).get("type")
            if it == "string" and not all(isinstance(x, str) for x in v):
                errs.append(f"'{k}' must be a list of strings")
    return errs
