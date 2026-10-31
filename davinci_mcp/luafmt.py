"""Lua 5.1 literal encoding for bridge requests (docs/PROTOCOL.md, "Request").

The bridge evaluates ``"return " .. payload`` in an empty environment, so the payload must be a
single table constructor built only from literals. Strings are emitted as quoted Lua strings with
exact escapes (never long brackets, which normalise line endings). Python 3.9 compatible.
"""

import math
import re
from typing import Any

ID_RE = re.compile(r"^[a-z0-9]{6,32}$")
MAX_PAYLOAD = 256 * 1024
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in",
    "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while",
}


def lua_string(s: str) -> str:
    """A quoted Lua string literal that decodes to exactly ``s`` (as UTF-8 bytes)."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append("\\%03d" % o)  # three digits: unambiguous before a following digit
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def lua_number(n: float) -> str:
    if isinstance(n, bool):
        raise TypeError("bool is not a number here")
    if isinstance(n, int):
        return str(n)
    if math.isnan(n) or math.isinf(n):
        raise ValueError("NaN/Infinity cannot be sent to Lua")
    r = repr(float(n))
    return r


def lua_key(k: Any) -> str:
    if isinstance(k, str):
        if _IDENT_RE.match(k) and k not in _LUA_KEYWORDS:
            return k
        return "[" + lua_string(k) + "]"
    if isinstance(k, bool):
        raise TypeError("bool keys are not supported")
    if isinstance(k, int):
        return "[" + str(k) + "]"
    raise TypeError("unsupported Lua table key type: %r" % type(k).__name__)


def lua_value(v: Any, _depth: int = 0) -> str:
    """Encode JSON-like Python data as a Lua literal. ``None`` -> ``nil``; lists -> arrays;
    dicts -> tables (``None`` values are omitted, mirroring JSON null -> absent)."""
    if _depth > 32:
        raise ValueError("value nested too deeply")
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return lua_number(v)
    if isinstance(v, str):
        return lua_string(v)
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_value(x, _depth + 1) for x in v) + "}"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if x is None:
                continue
            parts.append(lua_key(k) + " = " + lua_value(x, _depth + 1))
        return "{" + ", ".join(parts) + "}"
    raise TypeError("cannot encode %r as a Lua literal" % type(v).__name__)


def build_request(req_id: str, op: str, ts: int, code: str = None, args: Any = None,
                  timeout: float = 30.0, max_kb: int = 64) -> bytes:
    """The request payload (a Lua table literal without ``return``) as UTF-8 bytes."""
    if not ID_RE.match(req_id):
        raise ValueError("request id must match [a-z0-9]{6,32}")
    if op not in ("ping", "run", "stop"):
        raise ValueError("unknown op %r" % op)
    fields = {"v": 1, "id": req_id, "ts": int(ts), "op": op,
              "timeout": float(timeout), "max_kb": int(max_kb)}
    if op == "run":
        if not isinstance(code, str) or not code:
            raise ValueError("run needs code")
        fields["code"] = code
        if args is not None:
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            fields["args"] = args
    payload = lua_value(fields).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("request payload over %d bytes" % MAX_PAYLOAD)
    return payload
