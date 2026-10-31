"""Render queue tools: status, options, add/start/stop/delete jobs (current project)."""

from . import lua_tool, READ_ONLY, WRITE, WRITE_IDEMPOTENT, DESTRUCTIVE

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

# -- get_render_status -----------------------------------------------------------------------------------

STATUS_DESC = (
    "List the render queue of the current project with each job's status, completion percentage, "
    "error and timing (milliseconds), plus whether Resolve is rendering right now. Poll this after start_render."
)

STATUS_LUA = r'''
local p = need_project()
local jobs = p:GetRenderJobList() or {}
local out = {}
for _, j in ipairs(jobs) do
  local st = {}
  local ok, res = pcall(function() return p:GetRenderJobStatus(j.JobId) end)
  if ok and type(res) == "table" then st = res end
  out[#out + 1] = {
    id = j.JobId, name = j.RenderJobName, timeline = j.TimelineName, targetDir = j.TargetDir,
    outputFilename = j.OutputFilename, preset = j.PresetName, format = j.VideoFormat,
    codec = j.VideoCodec, markIn = j.MarkIn, markOut = j.MarkOut,
    status = st.JobStatus, completionPercentage = st.CompletionPercentage, error = st.Error,
    timeTakenMs = st.TimeTakenToRenderInMs, estimatedRemainingMs = st.EstimatedTimeRemainingInMs,
  }
end
return { isRendering = p:IsRenderingInProgress(), count = #jobs, jobs = out }
'''

lua_tool("get_render_status", STATUS_DESC, EMPTY, READ_ONLY, lua=STATUS_LUA, timeout=30)

# -- list_render_options ---------------------------------------------------------------------------------

OPTIONS_DESC = (
    "List render presets, quick-export presets, render formats (name and file extension) and the current render mode. "
    "Call once without format to see the formats, then again with a format to list that format's codecs."
)

OPTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "maxLength": 60,
                   "description": "Render format name from a previous call (for example 'mp4'); adds that format's codecs to the result."},
    },
    "additionalProperties": False,
}

OPTIONS_LUA = r'''
local p = need_project()
local function sorted_pairs(map, k1, k2, by)
  local keys = {}
  for k in pairs(map or {}) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  local out = {}
  for _, k in ipairs(keys) do out[#out + 1] = { [k1] = k, [k2] = map[k] } end
  return out
end
local formats = sorted_pairs(p:GetRenderFormats() or {}, "name", "extension")
local codecs
if args.format then
  codecs = sorted_pairs(p:GetRenderCodecs(args.format) or {}, "description", "codec")
end
-- GetCurrentRenderMode switches Resolve to the Deliver page; a read must not leave the UI changed.
local page0 = resolve:GetCurrentPage()
local mode = p:GetCurrentRenderMode()
if page0 and resolve:GetCurrentPage() ~= page0 then resolve:OpenPage(page0) end
return {
  presets = p:GetRenderPresetList() or {},
  quickExportPresets = p:GetQuickExportRenderPresets() or {},
  formats = formats,
  codecs = codecs,
  currentRenderMode = mode,
}
'''

lua_tool("list_render_options", OPTIONS_DESC, OPTIONS_SCHEMA, READ_ONLY, lua=OPTIONS_LUA, timeout=30)

# -- add_render_job --------------------------------------------------------------------------------------

ADD_DESC = (
    "Queue a render job for the current (or a named) timeline into an existing absolute folder, optionally applying a "
    "render preset, a format and codec, and a mark-in/mark-out range in absolute timeline frames. Non-blocking: "
    "follow with start_render and poll get_render_status."
)

ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "targetDir": {"type": "string", "minLength": 1, "maxLength": 1000,
                      "description": "Absolute path of an existing folder to render into."},
        "customName": {"type": "string", "maxLength": 200,
                       "description": "Output file name without extension (default: Resolve's own name)."},
        "preset": {"type": "string", "maxLength": 100,
                   "description": "Render preset name to load first (see list_render_options)."},
        "format": {"type": "string", "maxLength": 60,
                   "description": "Render format name, for example 'mp4' (see list_render_options)."},
        "codec": {"type": "string", "maxLength": 60,
                  "description": "Codec value for the format; requires format. Default: the format's first codec."},
        "markIn": {"type": "integer", "minimum": 0,
                   "description": "First frame to render, absolute timeline frame (a 01:00:00:00 timeline at 24 fps starts at 86400)."},
        "markOut": {"type": "integer", "minimum": 0,
                    "description": "Last frame to render, absolute timeline frame."},
        "timeline": {"type": "string", "maxLength": 200,
                     "description": "Timeline name to render; it becomes the current timeline. Default: the current timeline."},
    },
    "required": ["targetDir"],
    "additionalProperties": False,
}

ADD_LUA = r'''
local p = need_project()
local dir = args.targetDir
if not bmd.direxists(dir) then fail("Folder does not exist: " .. tostring(dir) .. ".") end
if args.codec and not args.format then fail("codec requires format.") end
if args.timeline and args.timeline ~= "" then
  local tl = need_timeline(args.timeline)
  p:SetCurrentTimeline(tl)
end
if args.preset then
  if not p:LoadRenderPreset(args.preset) then fail("No render preset named '" .. tostring(args.preset) .. "'.") end
end
if args.format then
  local codec = args.codec
  if not codec then
    local keys = {}
    local map = p:GetRenderCodecs(args.format) or {}
    for k in pairs(map) do keys[#keys + 1] = k end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    if keys[1] then codec = map[keys[1]] end
  end
  if not codec or not p:SetCurrentRenderFormatAndCodec(args.format, codec) then
    fail("Resolve rejected format '" .. tostring(args.format) .. "' / codec '" .. tostring(codec or args.codec) .. "'; see list_render_options.")
  end
end
if args.customName ~= nil and (args.customName:find("/", 1, true) or args.customName:sub(1, 1) == "." or args.customName:find("%.%.")) then
  fail("customName must be a plain file name: no slashes, no leading dot, no '..'.")
end
local settings = { TargetDir = dir, CustomName = args.customName, MarkIn = args.markIn, MarkOut = args.markOut }
if not p:SetRenderSettings(settings) then
  fail("Resolve rejected the render settings (check markIn/markOut are inside the timeline).")
end
local id = p:AddRenderJob()
if id == nil or id == "" then fail("Resolve did not create a render job (is a timeline open?).") end
local cur = p:GetCurrentTimeline()
return { ok = true, jobId = id, targetDir = dir, customName = args.customName, timeline = cur and cur:GetName() or nil }
'''

lua_tool("add_render_job", ADD_DESC, ADD_SCHEMA, WRITE, lua=ADD_LUA, timeout=60)

# -- start_render ----------------------------------------------------------------------------------------

START_DESC = (
    "Start rendering the given job ids, or every queued job when jobIds is omitted. Returns immediately; "
    "rendering continues inside Resolve, so poll get_render_status."
)

START_SCHEMA = {
    "type": "object",
    "properties": {
        "jobIds": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 100},
                   "description": "Job ids from get_render_status or add_render_job. Omit to render the whole queue."},
    },
    "additionalProperties": False,
}

START_LUA = r'''
local p = need_project()
local ids = args.jobIds or {}
local ok
if #ids > 0 then ok = p:StartRendering(ids, false) else ok = p:StartRendering(false) end
if not ok then fail("Resolve could not start rendering (are there jobs in the queue?).") end
return { ok = true, isRendering = p:IsRenderingInProgress(), jobs = #(p:GetRenderJobList() or {}) }
'''

lua_tool("start_render", START_DESC, START_SCHEMA, WRITE, lua=START_LUA, timeout=30)

# -- stop_render -----------------------------------------------------------------------------------------

STOP_DESC = "Stop any render in progress in the current project; harmless when nothing is rendering."

STOP_LUA = r'''
local p = need_project()
p:StopRendering()
return { ok = true, isRendering = p:IsRenderingInProgress() }
'''

lua_tool("stop_render", STOP_DESC, EMPTY, WRITE_IDEMPOTENT, lua=STOP_LUA, timeout=30)

# -- delete_render_job -----------------------------------------------------------------------------------

DELETE_DESC = "Permanently remove one job from the render queue by id. Requires confirm=true; rendered files are not deleted."

DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "jobId": {"type": "string", "minLength": 1, "maxLength": 100,
                  "description": "Render job id from get_render_status."},
        "confirm": {"type": "boolean", "enum": [True],
                    "description": "Must be true to confirm the deletion."},
    },
    "required": ["jobId", "confirm"],
    "additionalProperties": False,
}

DELETE_LUA = r'''
local p = need_project()
if not p:DeleteRenderJob(args.jobId) then fail("No render job with id '" .. tostring(args.jobId) .. "'.") end
return { ok = true, jobId = args.jobId, remaining = #(p:GetRenderJobList() or {}) }
'''

lua_tool("delete_render_job", DELETE_DESC, DELETE_SCHEMA, DESTRUCTIVE, lua=DELETE_LUA, timeout=30)
