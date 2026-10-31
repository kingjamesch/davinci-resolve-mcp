"""Status and project tools: resolve_status, open_page, project listing/open/save, project settings."""

import subprocess

from . import READ_ONLY, WRITE, WRITE_IDEMPOTENT, lua_tool, register
from .. import transport
from ..transport import BridgeError

STATUS_DESCRIPTION = (
    "Report whether the Resolve bridge is running and what Resolve is doing (edition, version, current "
    "page, project and timeline). This is the first call to make in any session. If the bridge is not "
    "running, ask the user to click Workspace > Scripts > Claude_MCP_Bridge in DaVinci Resolve (with a "
    "project open) and call this again."
)

_RESOLVE_PROCESS = "DaVinci Resolve.app/Contents/MacOS/Resolve"


def _resolve_running():
    try:
        out = subprocess.run(["pgrep", "-f", _RESOLVE_PROCESS], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.returncode == 0 and bool(out.stdout.strip())


@register("resolve_status", STATUS_DESCRIPTION, {"type": "object", "properties": {}, "additionalProperties": False},
          READ_ONLY, timeout=10)
def resolve_status(client, args):
    live = client.liveness()
    running = _resolve_running()
    if not live["running"]:
        return {"bridge": {"running": False, "reason": live["reason"]}, "resolveRunning": running,
                "hint": transport.BRIDGE_NOT_RUNNING}
    try:
        p = client.ping(timeout=5)
    except BridgeError as exc:
        return {"bridge": {"running": False, "reason": str(exc)}, "resolveRunning": running,
                "hint": transport.BRIDGE_NOT_RUNNING}
    product = p.get("product")
    return {
        "bridge": {"running": True, "session": p.get("session"), "uptime_s": p.get("uptime_s"),
                   "version": p.get("bridge"), "requests": p.get("requests")},
        "resolveRunning": True,
        "product": product,
        "version": p.get("version"),
        "studio": "Studio" in (product or ""),
        "page": p.get("page"),
        "project": p.get("project"),
        "timeline": p.get("timeline"),
    }


def _schema(props=None, required=None):
    s = {"type": "object", "properties": props or {}, "additionalProperties": False}
    if required:
        s["required"] = required
    return s


lua_tool(
    "open_page",
    "Switch DaVinci Resolve to a workspace page (media, cut, edit, fusion, color, fairlight or deliver) "
    "and return the page now showing. Safe to repeat.",
    _schema({"page": {"type": "string", "enum": ["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"],
                      "description": "Page to open."}}, ["page"]),
    WRITE_IDEMPOTENT,
    lua=r'''
if not resolve:OpenPage(args.page) then fail("Resolve refused to open the " .. tostring(args.page) .. " page.") end
return { ok = true, page = resolve:GetCurrentPage() }
''',
    timeout=15,
)

lua_tool(
    "list_projects",
    "List the projects and sub-folders in the current Resolve project-manager folder, plus the open "
    "project's name and the current folder path. Use it to find the exact name for open_project.",
    _schema(),
    READ_ONLY,
    lua=r'''
local pm = resolve:GetProjectManager()
local cur = pm:GetCurrentProject()
return {
  current = cur and cur:GetName() or nil,
  folder = pm:GetCurrentFolder(),
  projects = pm:GetProjectListInCurrentFolder() or {},
  folders = pm:GetFolderListInCurrentFolder() or {},
}
''',
)

lua_tool(
    "open_project",
    "Open a project by exact name from the current project-manager folder, saving the open project first. "
    "Returns alreadyOpen=true without doing anything if that project is already open. Fails if the name "
    "is not in the current folder (see list_projects).",
    _schema({"name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Exact project name."}},
            ["name"]),
    WRITE,
    lua=r'''
local pm = resolve:GetProjectManager()
local cur = pm:GetCurrentProject()
if cur and cur:GetName() == args.name then
  return { ok = true, project = args.name, alreadyOpen = true }
end
if cur then pm:SaveProject() end
local p = pm:LoadProject(args.name)
if not p then fail("No project named '" .. tostring(args.name) .. "' in the current project folder.") end
return { ok = true, project = p:GetName() }
''',
    timeout=60,
)

lua_tool(
    "save_project",
    "Save the open DaVinci Resolve project now. Safe to repeat.",
    _schema(),
    WRITE_IDEMPOTENT,
    lua=r'''
local p = need_project()
if not resolve:GetProjectManager():SaveProject() then fail("Resolve could not save the project.") end
return { ok = true, project = p:GetName() }
''',
    timeout=60,
)

lua_tool(
    "get_project_info",
    "Summarize the open project: name, timeline frame rate and resolution (strings as Resolve stores "
    "them), timeline names, current timeline, queued render job count and current page.",
    _schema(),
    READ_ONLY,
    lua=r'''
local p = need_project()
local names = {}
for i = 1, p:GetTimelineCount() or 0 do
  local tl = p:GetTimelineByIndex(i)
  names[#names + 1] = tl and tl:GetName() or ""
end
local cur = p:GetCurrentTimeline()
return {
  name = p:GetName(),
  frameRate = p:GetSetting("timelineFrameRate"),
  width = p:GetSetting("timelineResolutionWidth"),
  height = p:GetSetting("timelineResolutionHeight"),
  timelineCount = p:GetTimelineCount(),
  timelines = names,
  currentTimeline = cur and cur:GetName() or nil,
  renderJobs = #(p:GetRenderJobList() or {}),
  page = resolve:GetCurrentPage(),
}
''',
)

lua_tool(
    "get_project_setting",
    "Read one project setting by key (for example timelineFrameRate or timelineResolutionWidth), or "
    "every setting when no key is given. Values are strings exactly as Resolve returns them; the full "
    "list is large.",
    _schema({"key": {"type": "string", "maxLength": 100,
                     "description": "Setting key. Omit to return all settings."}}),
    READ_ONLY,
    lua=r'''
local p = need_project()
if args.key ~= nil then
  return { key = args.key, value = p:GetSetting(args.key) }
end
return { settings = p:GetSetting() }
''',
    max_kb=128,
)

lua_tool(
    "set_project_setting",
    "Set one project setting by key. Resolve stores setting values as strings (booleans become \"1\"/\"0\", "
    "numbers their text form) and silently refuses wrong keys or invalid values, so the tool fails "
    "when refused; look up keys and current values with get_project_setting first. Returns the value "
    "read back after the change.",
    _schema({"key": {"type": "string", "minLength": 1, "maxLength": 100, "description": "Setting key."},
             "value": {"type": ["string", "number", "boolean"], "description": "New value."}},
            ["key", "value"]),
    WRITE_IDEMPOTENT,
    lua=r'''
local p = need_project()
local v = args.value
if type(v) == "boolean" then v = v and "1" or "0"
elseif type(v) == "number" then v = tostring(v) end
if not p:SetSetting(args.key, v) then
  fail("Resolve rejected SetSetting('" .. tostring(args.key) .. "'); check the key and value with get_project_setting.")
end
return { ok = true, key = args.key, value = p:GetSetting(args.key) }
''',
)
