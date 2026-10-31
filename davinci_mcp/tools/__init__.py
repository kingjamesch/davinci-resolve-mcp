"""Tool registry for the davinci MCP server.

Two kinds of tools:

* **Lua-backed** — ``lua_tool(...)``: a static Lua chunk (``_lua.PRELUDE`` + ``lua``) runs inside
  Resolve with the validated arguments bound to ``args``. The chunk's first return value becomes the
  tool result. Arguments are never interpolated into code.
* **Python-backed** — ``@register(...)``: ``handler(client, args) -> dict`` runs in the server process
  (status, docs search, run_lua).

``TOOLS`` keeps registration order; ``ordered_tools()`` puts reads first, then writes, then run_lua.
"""

import re
from typing import Any, Callable, Dict, List, Optional

from ._lua import PRELUDE
from ..transport import BridgeClient, DEFAULT_TIMEOUT_S

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
WRITE_IDEMPOTENT = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}


class ToolError(Exception):
    """A user-facing failure sentence (validation, not found, refused)."""


class ToolSpec:
    def __init__(self, name: str, description: str, input_schema: dict, annotations: dict,
                 handler: Callable[[BridgeClient, dict], Any], timeout: float, group: str):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.annotations = dict(annotations)
        self.annotations.setdefault("title", name.replace("_", " "))
        self.handler = handler
        self.timeout = timeout
        self.group = group  # "read" | "write" | "lua"
        self.lua = None  # the Lua chunk (PRELUDE + tool code) for Lua-backed tools; tests compile it

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema, "annotations": self.annotations}


TOOLS: Dict[str, ToolSpec] = {}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def register(name: str, description: str, schema: Optional[dict], annotations: dict,
             timeout: float = DEFAULT_TIMEOUT_S, group: str = None):
    """Decorator for Python-backed tools: ``handler(client, args) -> JSON-able``."""
    if not _NAME_RE.match(name):
        raise ValueError("bad tool name %r" % name)
    if name in TOOLS:
        raise ValueError("duplicate tool %r" % name)
    grp = group or ("read" if annotations.get("readOnlyHint") else "write")

    def decorator(fn):
        TOOLS[name] = ToolSpec(name, description, schema, annotations, fn, timeout, grp)
        return fn

    return decorator


def lua_tool(name: str, description: str, schema: Optional[dict], annotations: dict, lua: str,
             timeout: float = DEFAULT_TIMEOUT_S, max_kb: int = 64, group: str = None) -> None:
    """Register a Lua-backed tool. ``lua`` runs after ``PRELUDE`` with ``args`` bound."""
    code = PRELUDE + "\n" + lua

    def handler(client: BridgeClient, args: dict):
        env = client.run(code, args=args or {}, timeout=timeout, max_kb=max_kb)
        result = env.get("result")
        if env.get("truncated"):
            result = {"truncated": True, "result_bytes": env.get("result_bytes"), "preview": result}
        return result

    register(name, description, schema, annotations, timeout=timeout, group=group)(handler)
    TOOLS[name].lua = code


def ordered_tools() -> List[ToolSpec]:
    rank = {"read": 0, "write": 1, "lua": 2}
    return sorted(TOOLS.values(), key=lambda t: (rank.get(t.group, 1), list(TOOLS).index(t.name)))


# -- argument validation (required / type / enum / bounds) --------------------------------------------

def _type_ok(value, json_type: str) -> bool:
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "array":
        return isinstance(value, list)
    if json_type == "object":
        return isinstance(value, dict)
    if json_type == "null":
        return value is None
    return True


def validate_args(name: str, args: dict, schema: dict, _path: str = "") -> None:
    """Raise ToolError("<tool>: <argument> must be <rule>.") on the first violation."""
    if not isinstance(args, dict):
        raise ToolError("%s: arguments must be an object." % name)
    props = schema.get("properties", {}) or {}
    for req in schema.get("required", []) or []:
        if req not in args:
            raise ToolError("%s: missing required argument '%s%s'." % (name, _path, req))
    if schema.get("additionalProperties") is False:
        for key in args:
            if key not in props:
                raise ToolError("%s: unknown argument '%s%s'." % (name, _path, key))
    for key, val in args.items():
        spec = props.get(key)
        if not spec:
            continue
        label = _path + key
        types = spec.get("type")
        if types is not None:
            allowed = types if isinstance(types, list) else [types]
            if not any(_type_ok(val, t) for t in allowed):
                raise ToolError("%s: argument '%s' must be %s." % (name, label, "/".join(allowed)))
        if "enum" in spec and val not in spec["enum"]:
            raise ToolError("%s: argument '%s' must be one of %s." % (name, label, ", ".join(map(str, spec["enum"]))))
        if isinstance(val, float) and (val != val or val in (float("inf"), float("-inf"))):
            raise ToolError("%s: argument '%s' must be a finite number." % (name, label))
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if "minimum" in spec and val < spec["minimum"]:
                raise ToolError("%s: argument '%s' must be >= %s." % (name, label, spec["minimum"]))
            if "maximum" in spec and val > spec["maximum"]:
                raise ToolError("%s: argument '%s' must be <= %s." % (name, label, spec["maximum"]))
        if isinstance(val, str):
            if "maxLength" in spec and len(val) > spec["maxLength"]:
                raise ToolError("%s: argument '%s' must be at most %d characters." % (name, label, spec["maxLength"]))
            if "minLength" in spec and len(val) < spec["minLength"]:
                raise ToolError("%s: argument '%s' must be at least %d characters." % (name, label, spec["minLength"]))
            if "pattern" in spec and not re.search(spec["pattern"], val):
                raise ToolError("%s: argument '%s' must match %s." % (name, label, spec.get("patternHint") or spec["pattern"]))
        if isinstance(val, list):
            if "maxItems" in spec and len(val) > spec["maxItems"]:
                raise ToolError("%s: argument '%s' must have at most %d items." % (name, label, spec["maxItems"]))
            if "minItems" in spec and len(val) < spec["minItems"]:
                raise ToolError("%s: argument '%s' must have at least %d items." % (name, label, spec["minItems"]))
            items = spec.get("items")
            if isinstance(items, dict):
                for i, item in enumerate(val):
                    item_type = items.get("type")
                    if item_type is not None:
                        allowed = item_type if isinstance(item_type, list) else [item_type]
                        if not any(_type_ok(item, t) for t in allowed):
                            raise ToolError("%s: argument '%s[%d]' must be %s." % (name, label, i, "/".join(allowed)))
                    if "enum" in items and item not in items["enum"]:
                        raise ToolError("%s: argument '%s[%d]' must be one of %s." % (name, label, i, ", ".join(map(str, items["enum"]))))
                    if isinstance(item, (int, float)) and not isinstance(item, bool):
                        if "minimum" in items and item < items["minimum"]:
                            raise ToolError("%s: argument '%s[%d]' must be >= %s." % (name, label, i, items["minimum"]))
                        if "maximum" in items and item > items["maximum"]:
                            raise ToolError("%s: argument '%s[%d]' must be <= %s." % (name, label, i, items["maximum"]))
                    if isinstance(item, str) and "maxLength" in items and len(item) > items["maxLength"]:
                        raise ToolError("%s: argument '%s[%d]' must be at most %d characters." % (name, label, i, items["maxLength"]))
                    if isinstance(item, dict) and items.get("properties"):
                        validate_args(name, item, items, _path="%s[%d]." % (label, i))
        if isinstance(val, dict) and spec.get("properties"):
            validate_args(name, val, spec, _path=label + ".")


__all__ = ["TOOLS", "ToolError", "ToolSpec", "register", "lua_tool", "ordered_tools", "validate_args",
           "READ_ONLY", "WRITE", "WRITE_IDEMPOTENT", "DESTRUCTIVE", "PRELUDE"]


# -- module loading ---------------------------------------------------------------------------------

TOOL_MODULES = ("status", "timelines", "mediapool", "render", "docs_tool", "run_lua")


def load_all(strict: bool = True) -> List[str]:
    """Import every tool module for its registrations; returns the module names that loaded.

    With ``strict=False`` a module that does not exist yet is skipped (used while the package is
    still being built); any other import error is always raised."""
    import importlib
    loaded = []
    for mod in TOOL_MODULES:
        try:
            importlib.import_module("." + mod, __name__)
            loaded.append(mod)
        except ModuleNotFoundError as exc:
            if strict or exc.name != __name__ + "." + mod:
                raise
    return loaded


__all__ += ["load_all", "TOOL_MODULES"]
