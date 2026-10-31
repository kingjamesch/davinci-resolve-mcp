"""run_lua — the open-ended tool: run a Lua 5.1 chunk inside DaVinci Resolve."""

from . import DESTRUCTIVE, register

DESCRIPTION = (
    "Run a Lua 5.1 chunk inside DaVinci Resolve and return its first return value (tables become "
    "JSON) plus captured print() output. Bound names: resolve, fusion, projectManager, project "
    "(current or nil), timeline (current or nil), mediaPool, bmd. The API is Blackmagic's scripting "
    "API (look signatures up with scripting_api_docs) with colon calls and 1-based lists, e.g. "
    "timeline:GetItemListInTrack(\"video\", 1). Open-ended: it can modify or delete anything in the "
    "open project, so read first and ask before destructive changes. Avoid Studio-only calls on the "
    "free edition (they fail or open an upgrade dialog that blocks the bridge). Long calls block the "
    "bridge until they finish; raise timeout_s for them. The timeout is enforced by the server only: a "
    "chunk that never returns (an unbounded loop, a modal dialog) blocks the bridge until Resolve is "
    "restarted, so keep loops bounded. Set DAVINCI_MCP_DISABLE_RUN_LUA=1 in the server environment to "
    "remove this tool."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "minLength": 1, "maxLength": 49152,
                 "description": "Lua 5.1 source. Use `return <value>` to get data back."},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600,
                      "description": "Seconds to wait for the chunk (default 30)."},
        "max_kb": {"type": "integer", "minimum": 1, "maximum": 256,
                   "description": "Result size cap in KB (default 64); larger results return truncated with a preview."},
    },
    "required": ["code"],
    "additionalProperties": False,
}


def _run_lua(client, args):
    if len(args["code"].encode("utf-8")) > 49152:
        from . import ToolError
        raise ToolError("run_lua: code must be at most 48 KB of UTF-8.")
    env = client.run(args["code"], args=None, timeout=args.get("timeout_s", 30), max_kb=args.get("max_kb", 64))
    out = {"result": env.get("result"), "prints": env.get("prints") or [], "ms": env.get("ms")}
    if env.get("truncated"):
        out["truncated"] = True
        out["result_bytes"] = env.get("result_bytes")
    if env.get("prints_dropped"):
        out["prints_dropped"] = env.get("prints_dropped")
    return out


import os as _os  # noqa: E402

if _os.environ.get("DAVINCI_MCP_DISABLE_RUN_LUA") != "1":
    register("run_lua", DESCRIPTION, SCHEMA, DESTRUCTIVE, timeout=600, group="lua")(_run_lua)
