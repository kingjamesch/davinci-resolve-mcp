"""Media pool tools: list_media_pool, get_clip_properties, create_media_pool_folder, import_media,
append_clips_to_timeline (all Lua-backed)."""

from . import lua_tool, READ_ONLY, WRITE

FOLDER_HELP = ('Slash path to a media pool folder, e.g. "Master/B-roll" ("Master" or "/" is the root; '
               'a leading "Master/" is optional).')

# -- list_media_pool ---------------------------------------------------------------------------------

LIST_DESCRIPTION = (
    "List the clips and subfolders of one media pool folder. Address nested folders with a slash path "
    "like \"Master/B-roll\"; omit folder for the root. Returns paged clips (offset default 0, limit "
    "default 50, max 200) with name, id, type, duration, frames, fps, resolution and filePath; property "
    "values are strings exactly as Resolve reports them."
)

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "folder": {"type": "string", "maxLength": 500,
                   "description": FOLDER_HELP + " Default: the root folder."},
        "offset": {"type": "integer", "minimum": 0, "description": "Clips to skip (default 0)."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                  "description": "Maximum clips to return (default 50)."},
    },
    "additionalProperties": False,
}

LIST_LUA = r'''
local offset = args.offset or 0
local limit = args.limit or 50
local folder = find_folder(args.folder)
local mp = need_project():GetMediaPool()
local subfolders = {}
for _, s in ipairs(folder:GetSubFolderList() or {}) do
  local ok, n = pcall(function() return s:GetName() end)
  if ok and n then subfolders[#subfolders + 1] = n end
end
local clips = folder:GetClipList() or {}
local slice, total, truncated = page_slice(clips, offset, limit, 200)
local function prop(c, key)
  local ok, v = pcall(function() return c:GetClipProperty(key) end)
  if ok then return str(v) end
  return nil
end
local out = {}
for _, c in ipairs(slice) do
  local okn, name = pcall(function() return c:GetName() end)
  local oki, id = pcall(function() return c:GetUniqueId() end)
  out[#out + 1] = {
    name = okn and str(name) or nil,
    id = oki and str(id) or nil,
    type = prop(c, "Type"),
    duration = prop(c, "Duration"),
    frames = prop(c, "Frames"),
    fps = prop(c, "FPS"),
    resolution = prop(c, "Resolution"),
    filePath = prop(c, "File Path"),
  }
end
local cur = mp:GetCurrentFolder()
return {
  folder = folder:GetName(),
  currentFolder = cur and cur:GetName() or nil,
  subfolders = subfolders,
  total = total,
  offset = offset,
  limit = limit,
  truncated = truncated,
  clips = out,
}
'''

lua_tool("list_media_pool", LIST_DESCRIPTION, LIST_SCHEMA, READ_ONLY, lua=LIST_LUA, timeout=30)

# -- get_clip_properties -----------------------------------------------------------------------------

PROPS_DESCRIPTION = (
    "Get the properties, metadata and marker count of one media pool clip, found by id (preferred, "
    "whole pool) or by name (current folder first, then whole pool). Provide name or id. Returns a "
    "curated set of clip properties (type, codec, fps, timecodes, color, flags and more) as strings "
    "exactly as Resolve reports them, plus the clip metadata table."
    " metadata is Resolve's full metadata table for the clip (user-entered fields, unfiltered)."
)

PROPS_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "maxLength": 500, "description": "Clip name (use id when names repeat)."},
        "id": {"type": "string", "maxLength": 100, "description": "Clip unique id from list_media_pool."},
    },
    "additionalProperties": False,
}

PROPS_LUA = r'''
local c = find_clip(args.name, args.id)
local all = c:GetClipProperty() or {}
local wanted = {
  "Type", "File Name", "File Path", "Duration", "Frames", "FPS", "Resolution", "Format",
  "Video Codec", "Audio Codec", "Audio Ch", "Sample Rate", "Start TC", "End TC", "Start", "End",
  "In", "Out", "Clip Color", "Flags", "Keyword", "Comments", "Description", "Date Created",
  "Date Modified", "Reel Name", "Scene", "Shot", "Take", "Angle", "Usage", "Bit Depth",
  "Data Level", "PAR", "Drop frame", "Field dominance", "H-FLIP", "V-FLIP", "Alpha mode",
  "Super Scale", "Proxy", "Proxy Media Path", "Online Status", "Camera #", "Audio Bit Depth",
}
local properties = {}
for _, key in ipairs(wanted) do
  if all[key] ~= nil then properties[key] = all[key] end
end
local metadata = c:GetMetadata() or {}
local markerCount = 0
for _ in pairs(c:GetMarkers() or {}) do markerCount = markerCount + 1 end
return {
  name = c:GetName(),
  id = c:GetUniqueId(),
  properties = properties,
  metadata = metadata,
  markerCount = markerCount,
}
'''

lua_tool("get_clip_properties", PROPS_DESCRIPTION, PROPS_SCHEMA, READ_ONLY, lua=PROPS_LUA, timeout=30)

# -- create_media_pool_folder ------------------------------------------------------------------------

FOLDER_DESCRIPTION = (
    "Create a new media pool folder (bin) under a parent folder. The parent is a slash path like "
    "\"Master/B-roll\" and defaults to the root. Returns the new folder's name and its parent's name."
)

FOLDER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Name of the new folder."},
        "parent": {"type": "string", "maxLength": 500,
                   "description": "Parent folder slash path (default: the root folder)."},
    },
    "required": ["name"],
    "additionalProperties": False,
}

FOLDER_LUA = r'''
local mp = need_project():GetMediaPool()
local parent = find_folder(args.parent)
local f = mp:AddSubFolder(parent, args.name)
if not f then
  fail("Resolve could not create folder '" .. tostring(args.name) .. "' under '" .. tostring(parent:GetName()) .. "'.")
end
return { ok = true, folder = f:GetName(), parent = parent:GetName() }
'''

lua_tool("create_media_pool_folder", FOLDER_DESCRIPTION, FOLDER_SCHEMA, WRITE, lua=FOLDER_LUA, timeout=30)

# -- import_media ------------------------------------------------------------------------------------

IMPORT_DESCRIPTION = (
    "Import media files or folders from disk into the media pool (1 to 100 absolute paths). Optionally "
    "target a media pool folder by slash path; otherwise the current folder is used, and the target "
    "becomes the current folder. Returns the imported clip names and counts. WARNING: importing into a "
    "project with no timeline may make Resolve show a frame-rate dialog that must be answered in "
    "Resolve itself; the tool times out until it is."
)

IMPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "paths": {"type": "array", "minItems": 1, "maxItems": 100,
                  "items": {"type": "string", "maxLength": 1000},
                  "description": "Absolute paths of files or folders to import."},
        "folder": {"type": "string", "maxLength": 500,
                   "description": FOLDER_HELP + " Default: the current media pool folder."},
    },
    "required": ["paths"],
    "additionalProperties": False,
}

IMPORT_LUA = r'''
for _, p in ipairs(args.paths) do
  if p:sub(1, 1) ~= "/" then fail("Path must be absolute: " .. p .. ".") end
  if not (bmd.fileexists(p) or bmd.direxists(p)) then fail("Path not found: " .. p .. ".") end
end
local mp = need_project():GetMediaPool()
if args.folder and args.folder ~= "" then
  mp:SetCurrentFolder(find_folder(args.folder))
end
local items = mp:ImportMedia(args.paths) or {}
local names = {}
for _, it in ipairs(items) do
  local ok, n = pcall(function() return it:GetName() end)
  if ok and n then names[#names + 1] = n end
end
local cur = mp:GetCurrentFolder()
return {
  imported = names,
  count = #items,
  requested = #args.paths,
  folder = cur and cur:GetName() or nil,
}
'''

lua_tool("import_media", IMPORT_DESCRIPTION, IMPORT_SCHEMA, WRITE, lua=IMPORT_LUA, timeout=120)

# -- append_clips_to_timeline ------------------------------------------------------------------------

APPEND_DESCRIPTION = (
    "Append media pool clips to the end of a timeline (the current one unless timeline is given). Each "
    "entry names a clip by name or id and may set a source range (startFrame/endFrame, source frames), "
    "trackIndex (1-based), recordFrame (timeline frame) and mediaType (1 video only, 2 audio only). 1 to "
    "100 entries. Returns the appended items with start, end and duration."
)

APPEND_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array", "minItems": 1, "maxItems": 100,
            "description": "Clips to append, in order.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 500, "description": "Clip name."},
                    "id": {"type": "string", "maxLength": 100, "description": "Clip unique id (preferred over name)."},
                    "startFrame": {"type": "integer", "minimum": 0, "description": "First source frame to use."},
                    "endFrame": {"type": "integer", "minimum": 0, "description": "Last source frame to use."},
                    "trackIndex": {"type": "integer", "minimum": 1, "description": "Target track number (1-based)."},
                    "recordFrame": {"type": "integer", "minimum": 0, "description": "Timeline frame to place the clip at."},
                    "mediaType": {"type": "integer", "enum": [1, 2],
                                  "description": "1 = video only, 2 = audio only (default both)."},
                },
                "additionalProperties": False,
            },
        },
        "timeline": {"type": "string", "maxLength": 500,
                     "description": "Timeline name to switch to first (default: the current timeline)."},
    },
    "required": ["clips"],
    "additionalProperties": False,
}

APPEND_LUA = r'''
local proj = need_project()
if args.timeline and args.timeline ~= "" then
  local tl = need_timeline(args.timeline)
  proj:SetCurrentTimeline(tl)
end
local mp = proj:GetMediaPool()
local keys = { "startFrame", "endFrame", "trackIndex", "recordFrame", "mediaType" }
local dict_form = false
for _, entry in ipairs(args.clips) do
  for _, k in ipairs(keys) do
    if entry[k] ~= nil then dict_form = true end
  end
end
local list = {}
for _, entry in ipairs(args.clips) do
  local clip = find_clip(entry.name, entry.id)
  if dict_form then
    local d = { mediaPoolItem = clip }
    for _, k in ipairs(keys) do
      if entry[k] ~= nil then d[k] = entry[k] end
    end
    list[#list + 1] = d
  else
    list[#list + 1] = clip
  end
end
local items = mp:AppendToTimeline(list) or {}
if #items == 0 then
  fail("Resolve appended nothing; check that a timeline is open and the clip names/ids exist.")
end
local out = {}
for _, it in ipairs(items) do
  local entry = {}
  pcall(function() entry.name = it:GetName() end)
  pcall(function() entry.start = it:GetStart() end)
  pcall(function() entry["end"] = it:GetEnd() end)
  pcall(function() entry.duration = it:GetDuration() end)
  out[#out + 1] = entry
end
local cur = proj:GetCurrentTimeline()
return {
  ok = true,
  appended = #items,
  items = out,
  timeline = cur and cur:GetName() or nil,
}
'''

lua_tool("append_clips_to_timeline", APPEND_DESCRIPTION, APPEND_SCHEMA, WRITE, lua=APPEND_LUA, timeout=60)
