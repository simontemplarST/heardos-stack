"""What leaves this machine, and what a model's words may do on the way back.

scrub()       applied to every message sent to a model (and every MCP tool result): masks tokens, passwords in URLs,
              signed-URL parameters, key=secret pairs, e-mail addresses, public IP addresses and long random strings.
              Private addresses, image digests and app names stay: the model needs them and they identify nothing outside.
md_text()     model or tool text going into a chat UI that renders Markdown/HTML: no links, images, HTML or formatting, so
              a prompt-injected model cannot make the browser fetch https://attacker/?data=… or fake an approval prompt.
md_code()     the same for text shown inside `code` spans.
term_text()   model or tool text going to a terminal: no escape sequences or other control characters.
is_local()    whether a model endpoint is on this machine or the private network (anything else is a third party).
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_MASK = "***"
_SECRET_WORDS = r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|authorization|credentials?|private[_-]?key|session(?:[_-]?id)?|sid|cookie|sig(?:nature)?|salt|psk)"
# a name with one of those as a whole part: DB_PASSWORD, api-key, x.sig, sessionid (not bypass, signal, AUTHENTIK_HOST)
_SECRET_NAME = r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_.-])*" + _SECRET_WORDS + r"(?:[_.-][A-Za-z0-9]+)*(?![A-Za-z0-9])"
_RULES: list[tuple[re.Pattern, str]] = [
    # PEM blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "[private key]"),
    # JSON web tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[token]"),
    # Authorization headers
    (re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 " + _MASK),
    # user:password@ in URLs
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1" + _MASK + "@"),
    # ?token=…  &sig=…  (signed URLs, OAuth codes, session ids)
    (re.compile(r"([?&](?:" + _SECRET_NAME + r"|code|state|key|X-Amz-[A-Za-z]+)=)[^&\s\"'#]+", re.I),
     r"\1" + _MASK),
    # password=…, "api_key": "…", SECRET_KEY: …
    (re.compile(r"""(""" + _SECRET_NAME + r"""["']?\s*[:=]\s*["']?)(?!\*\*\*|\(hidden\)|\$\{)[^\s"',;&]{3,}""", re.I),
     r"\1" + _MASK),
    # well-known key formats
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\b(?:ghp|gho|ghs|ghu|github_pat|glpat|xox[abpr])[-_][A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b"), "[key]"),
    # e-mail addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
]
# long random-looking strings: mixed-case-plus-digits (generated passwords, base64 keys) or long hex (tokens, keys).
# Not "sha256:<hex>" (image digests: models copy image references) and nothing with a slash or dot in it (paths, images).
_WORD = re.compile(r"(?<![A-Za-z0-9+_=-])[A-Za-z0-9+_=-]{24,}(?![A-Za-z0-9+_=-])")


def _random(m: re.Match, text: str) -> str:
    w = m.group(0)
    if text[max(0, m.start() - 7):m.start()].endswith("sha256:"):
        return w
    hexish = len(w) >= 32 and re.fullmatch(r"[0-9a-fA-F]+", w) is not None
    mixed = re.search(r"[a-z]", w) and re.search(r"[A-Z]", w) and re.search(r"\d", w)
    return _MASK if hexish or mixed else w


_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![0-9A-Fa-f:])")   # ipaddress decides


def _ip(m: re.Match) -> str:
    try:
        a = ipaddress.ip_address(m.group(0))
    except ValueError:
        return m.group(0)
    if a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified or a in ipaddress.ip_network("100.64.0.0/10"):
        return m.group(0)                 # home-network addresses help diagnose and identify nobody
    return "[ip]"


def scrub(text: str, extra: tuple[str, ...] = ()) -> str:
    """Mask secrets and personal data in `text`; `extra` are literal values that must never appear (known secrets)."""
    if not text:
        return text
    out = str(text)
    for v in sorted((v for v in extra if v and len(v) >= 6), key=len, reverse=True):
        out = out.replace(v, _MASK)
    for rx, rep in _RULES:
        out = rx.sub(rep, out)
    src = out
    out = _WORD.sub(lambda m: _random(m, src), src)
    out = _IPV4.sub(_ip, out)
    return _IPV6.sub(_ip, out)


def scrub_messages(messages: list[dict], extra: tuple[str, ...] = ()) -> list[dict]:
    return [{**m, "content": scrub(m["content"], extra)} if isinstance(m.get("content"), str) else m for m in messages]


# ---------------------------------------------------------------- output to people
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>!|~#])")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def md_text(text: str, one_line: bool = False) -> str:
    """Model/tool text as inert Markdown: every character that could start a link, image, HTML tag, code span, heading,
    table or emphasis is backslash-escaped (CommonMark then shows it literally)."""
    t = _CONTROL.sub("", str(text or ""))
    if one_line:
        t = " ".join(t.split())
    return _MD_SPECIAL.sub(r"\\\1", t)


def md_code(text: str) -> str:
    """Text for inside a single-backtick code span: no backticks or line breaks, so it cannot end the span."""
    return " ".join(_CONTROL.sub("", str(text or "")).replace("`", "'").split())


def term_text(text: str) -> str:
    """Text for a terminal: control characters (ANSI escapes, carriage returns, bidi overrides) removed; newlines kept."""
    return _CONTROL.sub("", str(text or "").replace("\r\n", "\n"))


# ---------------------------------------------------------------- where the model runs
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain", ".sslip.io", ".nip.io")


def is_local(endpoint: str) -> bool:
    """True for a model server on this machine or a private network; False for anything that is a third party's API."""
    host = (urlsplit(endpoint if "//" in endpoint else f"http://{endpoint}").hostname or "").lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or "." not in host:
        return True
    try:
        a = ipaddress.ip_address(host)
        return a.is_private or a.is_loopback or a.is_link_local or a in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        pass
    if host.endswith((".sslip.io", ".nip.io")):          # these encode an address in the name: local only if that is
        m = re.search(r"(\d{1,3}[.-]\d{1,3}[.-]\d{1,3}[.-]\d{1,3})", host)
        return bool(m) and is_local(m.group(1).replace("-", "."))
    return host.endswith(_LOCAL_SUFFIXES)
