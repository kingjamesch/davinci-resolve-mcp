-- Offline harness: runs bridge/Claude_MCP_Bridge.lua against fake resolve/fusion objects.
-- usage: fuscript -l lua tests/lua_harness.lua <state_dir> <prefs_file> <max_ticks> <bridge_path>
local state_dir, prefs_file, max_ticks, bridge_path = arg[1], arg[2], arg[3], arg[4]

local function shell_quote(s) return "'" .. tostring(s):gsub("'", "'\\''") .. "'" end

local PREFIX = "ClaudeMCP."
local store, order = {}, {}

local function write_prefs()
  local lines = { "{", "\tGlobal = {", "\t\tScript = {", "\t\t\tGlobalData = {", "\t\t\t\tClaudeMCP = {" }
  for _, k in ipairs(order) do
    lines[#lines + 1] = "\t\t\t\t\t" .. k .. ' = "' .. store[k] .. '",'
  end
  lines[#lines + 1] = "\t\t\t\t}"
  lines[#lines + 1] = "\t\t\t}"
  lines[#lines + 1] = "\t\t}"
  lines[#lines + 1] = "\t}"
  lines[#lines + 1] = "}"
  local tmp = prefs_file .. ".tmp"
  local fh = assert(io.open(tmp, "w"))
  fh:write(table.concat(lines, "\n"), "\n")
  fh:close()
  assert(os.rename(tmp, prefs_file))
end

local FU = {}
function FU.SetData(self, key, value)
  local short = tostring(key):gsub("^" .. PREFIX:gsub("%.", "%%."), "")
  if store[short] == nil then order[#order + 1] = short end
  store[short] = tostring(value)
  write_prefs()
end
function FU.GetData(self, key)
  local short = tostring(key):gsub("^" .. PREFIX:gsub("%.", "%%."), "")
  return store[short]
end

local media_storage = {}
function media_storage.GetSubFolderList(self, dir)
  local out = {}
  local p = io.popen('find ' .. shell_quote(dir) .. ' -mindepth 1 -maxdepth 1 -type d 2>/dev/null')
  if not p then return out end
  for line in p:lines() do out[#out + 1] = line end
  p:close()
  return out
end

local timeline = { GetName = function() return "Timeline 1" end }
local project = {
  GetName = function() return "Fake Project" end,
  GetTimelineCount = function() return 1 end,
  GetCurrentTimeline = function() return timeline end,
  GetMediaPool = function() return {} end,
}
local project_manager = { GetCurrentProject = function() return project end }
local fake_resolve = {
  GetProductName = function() return "DaVinci Resolve" end,
  GetVersionString = function() return "21.1.2.10" end,
  GetCurrentPage = function() return "edit" end,
  GetProjectManager = function() return project_manager end,
  GetMediaStorage = function() return media_storage end,
}

write_prefs()

-- fuscript runs the main chunk in a private environment; the bridge reads the real _G.
rawset(_G, "CMCP_CONFIG", { state_dir = state_dir, resolve = fake_resolve, fusion = FU, tick = 0.01, max_ticks = tonumber(max_ticks) })

local r1, r2 = dofile(bridge_path)
print(tostring(r1))
if r2 ~= nil then print(tostring(r2)) end
