"""Shared Lua prelude and helpers for Lua-backed tools.

Every Lua-backed tool's chunk is ``PRELUDE + chunk``. Inside Resolve the bridge binds these names
before the chunk runs (see docs/PROTOCOL.md "Execution of run"): ``resolve``, ``fusion``/``fu``/``app``,
``bmd``, ``args`` (the tool's validated JSON arguments as a Lua table), ``projectManager``,
``project`` (current project or nil), ``timeline`` (current timeline or nil), ``mediaPool``.
Resolve's Lua API is the Python API with colon calls and 1-based lists:
``timeline:GetItemListInTrack("video", 1)``.

The prelude adds (all raise a plain error sentence on failure, which the server surfaces as
"Lua error: <sentence>"):

- ``fail(msg)`` — raise a user-facing sentence.
- ``need_project()`` — current project or fail "No project is open in DaVinci Resolve."
- ``need_timeline(name)`` — current timeline (name nil) or the timeline named ``name`` (searched by
  index), else fail "No timeline named '<name>'." / "No timeline is open in the current project."
- ``find_timeline(name)`` — timeline or nil.
- ``walk_clips(mp, fn)`` — depth-first over every MediaPoolItem; ``fn(clip, folder)`` returns true to stop.
- ``find_clip(name, id)`` — MediaPoolItem by unique id (whole pool) or by name (current folder first,
  then whole pool); fail "No clip named '<name>'." / "No clip with id '<id>'."
- ``find_folder(path)`` — media-pool Folder by slash path like "Master/B-roll" (root aliases: "",
  "Master", "/"); fail "No media pool folder '<path>'."
- ``page_slice(list, offset, limit, cap)`` — returns slice, total, truncated (offset 0-based).
- ``str(v)`` — tostring that maps nil to nil (not "nil").
- ``require_studio()`` — fail "This needs DaVinci Resolve Studio; the free edition cannot run it."
  unless GetProductName contains "Studio". Call it BEFORE any Studio-only API: calling one on the
  free edition opens an upgrade dialog that wedges menu scripts.
"""

PRELUDE = r'''
local function fail(msg) error(tostring(msg), 0) end
local function str(v) if v == nil then return nil end return tostring(v) end
local function need_project()
  local pm = resolve:GetProjectManager()
  local p = pm and pm:GetCurrentProject() or nil
  if not p then fail("No project is open in DaVinci Resolve.") end
  return p
end
local function find_timeline(name)
  local p = need_project()
  if name == nil or name == "" then return p:GetCurrentTimeline() end
  local n = p:GetTimelineCount() or 0
  for i = 1, n do
    local tl = p:GetTimelineByIndex(i)
    if tl and tl:GetName() == name then return tl end
  end
  return nil
end
local function need_timeline(name)
  local tl = find_timeline(name)
  if not tl then
    if name == nil or name == "" then fail("No timeline is open in the current project.") end
    fail("No timeline named '" .. tostring(name) .. "'.")
  end
  return tl
end
local function walk_clips(mp, fn)
  local root = mp:GetRootFolder()
  local stack = { root }
  while #stack > 0 do
    local folder = table.remove(stack)
    for _, clip in ipairs(folder:GetClipList() or {}) do
      if fn(clip, folder) then return true end
    end
    for _, sub in ipairs(folder:GetSubFolderList() or {}) do stack[#stack + 1] = sub end
  end
  return false
end
local function find_clip(name, id)
  local mp = need_project():GetMediaPool()
  local found
  if id and id ~= "" then
    walk_clips(mp, function(c) if c:GetUniqueId() == id then found = c; return true end end)
    if not found then fail("No clip with id '" .. tostring(id) .. "'.") end
    return found
  end
  if not name or name == "" then fail("Provide a clip name or id.") end
  local cur = mp:GetCurrentFolder()
  if cur then
    for _, c in ipairs(cur:GetClipList() or {}) do if c:GetName() == name then return c end end
  end
  walk_clips(mp, function(c) if c:GetName() == name then found = c; return true end end)
  if not found then fail("No clip named '" .. tostring(name) .. "'.") end
  return found
end
local function find_folder(path)
  local mp = need_project():GetMediaPool()
  local folder = mp:GetRootFolder()
  if path == nil or path == "" or path == "/" or path == "Master" then return folder end
  for part in tostring(path):gmatch("[^/]+") do
    if part ~= "Master" or folder ~= mp:GetRootFolder() then
      local nextf
      for _, sub in ipairs(folder:GetSubFolderList() or {}) do
        if sub:GetName() == part then nextf = sub; break end
      end
      if not nextf then fail("No media pool folder '" .. tostring(path) .. "'.") end
      folder = nextf
    end
  end
  return folder
end
local function page_slice(list, offset, limit, cap)
  list = list or {}
  offset = math.max(0, math.floor(tonumber(offset) or 0))
  limit = math.max(1, math.min(math.floor(tonumber(limit) or 50), cap or 200))
  local out = {}
  for i = offset + 1, math.min(#list, offset + limit) do out[#out + 1] = list[i] end
  return out, #list, (offset + limit) < #list
end
local function require_studio()
  local name = resolve:GetProductName() or ""
  if not name:find("Studio") then fail("This needs DaVinci Resolve Studio; the free edition cannot run it.") end
end
'''
