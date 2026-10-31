"""Timeline tools: list/inspect/switch timelines, playhead, markers, items, elements, export."""

from . import lua_tool, READ_ONLY, WRITE, WRITE_IDEMPOTENT, DESTRUCTIVE

_TIMELINE = {"type": "string", "maxLength": 200,
             "description": "Timeline name. Omit to use the current timeline."}
_TRACK_TYPES = ["video", "audio", "subtitle"]
_MARKER_COLORS = ["Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose",
                  "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream"]
_CONFIRM = {"type": "boolean", "enum": [True], "description": "Must be true to confirm this irreversible action."}
_EXPORT_TYPES = ["AAF", "DRT", "EDL", "FCP_7_XML", "FCPXML_1_8", "FCPXML_1_9", "FCPXML_1_10", "OTIO",
                 "TEXT_CSV", "TEXT_TAB", "ALE", "ALE_CDL", "HDR_10_PROFILE_A", "HDR_10_PROFILE_B",
                 "DOLBY_VISION_VER_2_9", "DOLBY_VISION_VER_4_0", "DOLBY_VISION_VER_5_1"]


def _schema(props, required=None):
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


# 1. list_timelines
lua_tool(
    "list_timelines",
    "List every timeline in the current project with its index, name, absolute start/end frames and frame "
    "rate, and which one is current. Returns {count, current, timelines}.",
    _schema({}), READ_ONLY, timeout=30, lua=r'''
local p = need_project()
local cur = p:GetCurrentTimeline()
local curName = cur and cur:GetName() or nil
local out = {}
local n = p:GetTimelineCount() or 0
for i = 1, n do
  local tl = p:GetTimelineByIndex(i)
  if tl then
    local name = tl:GetName()
    out[#out + 1] = {
      index = i, name = name,
      isCurrent = (curName ~= nil and curName == name) or false,
      startFrame = tl:GetStartFrame(), endFrame = tl:GetEndFrame(),
      frameRate = tl:GetSetting("timelineFrameRate"),
    }
  end
end
return { count = #out, current = curName, timelines = out }
''')

# 2. get_timeline_info
lua_tool(
    "get_timeline_info",
    "Describe a timeline (default: current): name, start/current timecode, frames, frame rate, track counts "
    "and marker count. Two frame spaces exist: startFrame/endFrame, markers and timecodes use ABSOLUTE "
    "timeline frames (a 24 fps timeline starting at 01:00:00:00 has startFrame 86400); timeline item start/end "
    "are absolute on Resolve 21.1 but were 0-based offsets on older builds, so prefer the startAbs/endAbs "
    "fields of get_timeline_items.",
    _schema({"timeline": _TIMELINE}), READ_ONLY, timeout=30, lua=r'''
local tl = need_timeline(args.timeline)
local p = need_project()
local cur = p:GetCurrentTimeline()
local name = tl:GetName()
local s, e = tl:GetStartFrame(), tl:GetEndFrame()
local markers = tl:GetMarkers() or {}
local mc = 0
for _ in pairs(markers) do mc = mc + 1 end
return {
  name = name,
  isCurrent = (cur ~= nil and cur:GetName() == name) or false,
  startTimecode = tl:GetStartTimecode(),
  currentTimecode = tl:GetCurrentTimecode(),
  startFrame = s, endFrame = e,
  durationFrames = (s and e) and (e - s) or nil,
  frameRate = tl:GetSetting("timelineFrameRate"),
  tracks = {
    video = tl:GetTrackCount("video"),
    audio = tl:GetTrackCount("audio"),
    subtitle = tl:GetTrackCount("subtitle"),
  },
  markerCount = mc,
}
''')

# 3. set_current_timeline
lua_tool(
    "set_current_timeline",
    "Make the named timeline the current one in Resolve. Returns {ok, timeline}.",
    _schema({"name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Exact timeline name."}},
            ["name"]),
    WRITE_IDEMPOTENT, timeout=30, lua=r'''
local tl = need_timeline(args.name)
local ok = need_project():SetCurrentTimeline(tl)
if not ok then fail("Resolve could not switch to timeline '" .. tostring(args.name) .. "'.") end
return { ok = true, timeline = tl:GetName() }
''')

# 4. set_timecode
lua_tool(
    "set_timecode",
    "Move the playhead of a timeline (default: current) to a timecode formatted HH:MM:SS:FF (use ';' before "
    "FF for drop-frame). The timecode is absolute, so a timeline starting at 01:00:00:00 needs 01:... values. "
    "Returns the resulting {ok, timecode}.",
    _schema({
        "timecode": {"type": "string", "maxLength": 11, "pattern": "^[0-9]{2}:[0-9]{2}:[0-9]{2}[:;][0-9]{2}$",
                     "patternHint": "HH:MM:SS:FF", "description": "Target timecode, HH:MM:SS:FF."},
        "timeline": _TIMELINE,
    }, ["timecode"]),
    WRITE_IDEMPOTENT, timeout=30, lua=r'''
local tl = need_timeline(args.timeline)
local ok = tl:SetCurrentTimecode(args.timecode)
if not ok then
  fail("Timecode " .. tostring(args.timecode) .. " is outside timeline '" .. tostring(tl:GetName()) ..
       "' (it runs " .. tostring(tl:GetStartTimecode()) .. " to the end frame).")
end
return { ok = true, timecode = tl:GetCurrentTimecode() }
''')

# 5. create_timeline
lua_tool(
    "create_timeline",
    "Create a timeline in the current project: empty, or built from the named media pool clips appended in "
    "order (up to 100). Fails if the name is taken. Returns {ok, timeline, startFrame, endFrame}.",
    _schema({
        "name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Name for the new timeline."},
        "clipNames": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 200},
                      "description": "Optional media pool clip names to put on the timeline, in order."},
    }, ["name"]),
    WRITE, timeout=60, lua=r'''
local mp = need_project():GetMediaPool()
local tl
if args.clipNames and #args.clipNames > 0 then
  local clips = {}
  for _, cn in ipairs(args.clipNames) do clips[#clips + 1] = find_clip(cn) end
  tl = mp:CreateTimelineFromClips(args.name, clips)
else
  tl = mp:CreateEmptyTimeline(args.name)
end
if not tl then
  fail("Resolve refused to create timeline '" .. tostring(args.name) .. "' (a timeline with that name may already exist).")
end
return { ok = true, timeline = tl:GetName(), startFrame = tl:GetStartFrame(), endFrame = tl:GetEndFrame() }
''')

# 6. get_timeline_items
lua_tool(
    "get_timeline_items",
    "List the items on one track of a timeline (default: current), paged (limit 1-200, default 50). "
    "start/end are as Resolve reports them (absolute frames on 21.1, 0-based offsets on older builds); "
    "startAbs/endAbs are normalised absolute timeline frames "
    "usable for markers and timecodes. index is the 1-based position on the track (use it with "
    "delete_timeline_items). Returns {timeline, track, total, offset, limit, truncated, items}.",
    _schema({
        "trackType": {"type": "string", "enum": _TRACK_TYPES, "description": "Track type."},
        "trackIndex": {"type": "integer", "minimum": 1, "description": "1-based track number."},
        "offset": {"type": "integer", "minimum": 0, "description": "Items to skip (default 0)."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Max items to return (default 50)."},
        "timeline": _TIMELINE,
    }, ["trackType", "trackIndex"]),
    READ_ONLY, timeout=60, lua=r'''
local tl = need_timeline(args.timeline)
local ttype, tidx = args.trackType, args.trackIndex
local count = tl:GetTrackCount(ttype) or 0
if tidx > count then
  fail("Track " .. ttype .. tidx .. " does not exist; the timeline has " .. count .. " " .. ttype .. " track(s).")
end
local offset = args.offset or 0
local limit = args.limit or 50
local items = tl:GetItemListInTrack(ttype, tidx) or {}
local slice, total, truncated = page_slice(items, offset, limit, 200)
local base = tl:GetStartFrame() or 0
local trackName = tl:GetTrackName(ttype, tidx)
local out = {}
for i, it in ipairs(slice) do
  local s, e = it:GetStart(), it:GetEnd()
  local row = {
    index = offset + i,
    name = it:GetName(),
    start = s, ["end"] = e,
    duration = it:GetDuration(),
    -- Resolve 21.1 returns absolute frames (start >= timeline start); older builds returned 0-based offsets.
    startAbs = s and ((s >= base) and s or (base + s)) or nil,
    endAbs = e and ((e >= base and (s == nil or s >= base)) and e or (base + e)) or nil,
    trackName = trackName,
  }
  local okL, lo = pcall(function() return it:GetLeftOffset() end)
  if okL then row.leftOffset = lo end
  local okR, ro = pcall(function() return it:GetRightOffset() end)
  if okR then row.rightOffset = ro end
  local okE, en = pcall(function() return it:GetIsEnabled() end)
  if okE and en ~= nil then row.enabled = en end
  local okM, mn = pcall(function()
    local m = it:GetMediaPoolItem()
    return m and m:GetName() or nil
  end)
  if okM then row.mediaPoolItem = mn end
  out[#out + 1] = row
end
return {
  timeline = tl:GetName(),
  track = { type = ttype, index = tidx, name = trackName },
  total = total, offset = offset, limit = limit, truncated = truncated,
  items = out,
}
''')

# 7. get_timeline_markers
lua_tool(
    "get_timeline_markers",
    "List the markers of a timeline (default: current) sorted by frame, paginated with offset/limit (max 200). "
    "Marker frames are ABSOLUTE timeline frames. Returns {timeline, total, offset, limit, truncated, "
    "markers:[{frame, color, name, note, duration, customData}]}.",
    _schema({
        "timeline": _TIMELINE,
        "offset": {"type": "integer", "minimum": 0, "description": "Skip this many markers (default 0)."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Markers per page (default 50)."},
    }), READ_ONLY, timeout=30, lua=r'''
local tl = need_timeline(args.timeline)
local markers = tl:GetMarkers() or {}
local keys = {}
for k in pairs(markers) do
  if type(k) == "number" then keys[#keys + 1] = k end
end
table.sort(keys)
local page, total, truncated = page_slice(keys, args.offset, args.limit, 200)
local out = {}
for _, k in ipairs(page) do
  local m = markers[k]
  out[#out + 1] = { frame = k, color = m.color, name = m.name, note = m.note,
                    duration = m.duration, customData = m.customData }
end
return { timeline = tl:GetName(), total = total, offset = args.offset or 0, limit = args.limit or 50,
         truncated = truncated, count = #out, markers = out }
''')

# 8. add_marker
lua_tool(
    "add_marker",
    "Add a marker at an ABSOLUTE timeline frame (see get_timeline_info for startFrame). The frame must be "
    "strictly after the timeline start frame and before the end frame, on clip content, with no existing "
    "marker. Duration is in frames (default 1). Returns {ok, frame, color, name}.",
    _schema({
        "frame": {"type": "integer", "minimum": 0, "description": "Absolute timeline frame for the marker."},
        "color": {"type": "string", "enum": _MARKER_COLORS, "description": "Marker color."},
        "name": {"type": "string", "maxLength": 200, "description": "Marker name (default empty)."},
        "note": {"type": "string", "maxLength": 2000, "description": "Marker note (default empty)."},
        "duration": {"type": "integer", "minimum": 1, "maximum": 100000, "description": "Duration in frames (default 1)."},
        "customData": {"type": "string", "maxLength": 200, "description": "Custom data string (default empty)."},
        "timeline": _TIMELINE,
    }, ["frame", "color"]),
    WRITE, timeout=30, lua=r'''
local tl = need_timeline(args.timeline)
local frame = args.frame
local s, e = tl:GetStartFrame(), tl:GetEndFrame()
if frame <= s or frame >= e then
  fail("Frame " .. frame .. " is outside the markable range of timeline '" .. tostring(tl:GetName()) ..
       "' (strictly after startFrame " .. s .. " and before endFrame " .. e ..
       "; Resolve rejects the exact start frame).")
end
local name = args.name or ""
local ok = tl:AddMarker(frame, args.color, name, args.note or "", args.duration or 1, args.customData or "")
if not ok then
  fail("AddMarker at frame " .. frame .. " failed: a marker already exists at that frame or the frame is not on clip content.")
end
return { ok = true, frame = frame, color = args.color, name = name }
''')

# 9. delete_markers
lua_tool(
    "delete_markers",
    "Delete markers from a timeline (default: current) by exactly one of frame (absolute) or color. "
    "Irreversible; color \"All\" removes every marker on the timeline. Requires confirm:true. "
    "Returns {ok, deleted, remaining}.",
    _schema({
        "confirm": _CONFIRM,
        "frame": {"type": "integer", "minimum": 0, "description": "Absolute frame of the one marker to delete."},
        "color": {"type": "string", "enum": ["All"] + _MARKER_COLORS,
                  "description": "Delete all markers of this color, or \"All\" for every marker."},
        "timeline": _TIMELINE,
    }, ["confirm"]),
    DESTRUCTIVE, timeout=30, lua=r'''
if (args.frame ~= nil) == (args.color ~= nil) then fail("Give exactly one of frame or color.") end
local tl = need_timeline(args.timeline)
local function count()
  local n = 0
  for _ in pairs(tl:GetMarkers() or {}) do n = n + 1 end
  return n
end
local before = count()
local ok, what
if args.frame ~= nil then
  ok = tl:DeleteMarkerAtFrame(args.frame)
  what = "frame " .. tostring(args.frame)
else
  ok = tl:DeleteMarkersByColor(args.color)
  what = "color " .. tostring(args.color)
end
if not ok then fail("No marker matched " .. what .. " on timeline '" .. tostring(tl:GetName()) .. "'.") end
local after = count()
return { ok = true, deleted = before - after, remaining = after }
''')

# 10. insert_timeline_element
lua_tool(
    "insert_timeline_element",
    "Insert a title or generator by its exact Effects Library name (e.g. \"Text+\", \"Solid Color\") at the "
    "playhead on the current timeline; move the playhead first with set_timecode. Returns {ok, kind, item}.",
    _schema({
        "kind": {"type": "string", "enum": ["title", "fusion_title", "generator", "fusion_generator", "ofx_generator"],
                 "description": "Which kind of element to insert."},
        "name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Exact Effects Library name."},
    }, ["kind", "name"]),
    WRITE, timeout=60, lua=r'''
local tl = need_timeline(nil)
local it
if args.kind == "title" then it = tl:InsertTitleIntoTimeline(args.name)
elseif args.kind == "fusion_title" then it = tl:InsertFusionTitleIntoTimeline(args.name)
elseif args.kind == "generator" then it = tl:InsertGeneratorIntoTimeline(args.name)
elseif args.kind == "fusion_generator" then it = tl:InsertFusionGeneratorIntoTimeline(args.name)
elseif args.kind == "ofx_generator" then it = tl:InsertOFXGeneratorIntoTimeline(args.name)
else fail("Unknown element kind '" .. tostring(args.kind) .. "'.") end
if not it then
  fail("Resolve could not insert " .. args.kind .. " '" .. args.name .. "' at the playhead; check the exact Effects Library name.")
end
return { ok = true, kind = args.kind,
         item = { name = it:GetName(), start = it:GetStart(), ["end"] = it:GetEnd(), duration = it:GetDuration() } }
''')

# 11. delete_timeline_items
lua_tool(
    "delete_timeline_items",
    "Delete items from one track by their 1-based positions as returned by get_timeline_items (up to 50). "
    "Pass expectedNames (same order) so the delete is refused if the track changed since you listed it. "
    "Non-ripple: leaves gaps. Irreversible except via Resolve's own Undo. Requires confirm:true after the "
    "user agreed. Returns {ok, deleted, remaining}.",
    _schema({
        "confirm": _CONFIRM,
        "trackType": {"type": "string", "enum": _TRACK_TYPES, "description": "Track type."},
        "trackIndex": {"type": "integer", "minimum": 1, "description": "1-based track number."},
        "itemIndexes": {"type": "array", "minItems": 1, "maxItems": 50,
                        "items": {"type": "integer", "minimum": 1},
                        "description": "1-based item positions on the track, as returned by get_timeline_items."},
        "expectedNames": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 500},
                          "description": "Item names in the same order as itemIndexes; the delete is refused on any mismatch."},
        "timeline": _TIMELINE,
    }, ["confirm", "trackType", "trackIndex", "itemIndexes"]),
    DESTRUCTIVE, timeout=60, lua=r'''
local tl = need_timeline(args.timeline)
local ttype, tidx = args.trackType, args.trackIndex
local count = tl:GetTrackCount(ttype) or 0
if tidx > count then
  fail("Track " .. ttype .. tidx .. " does not exist; the timeline has " .. count .. " " .. ttype .. " track(s).")
end
local items = tl:GetItemListInTrack(ttype, tidx) or {}
local expected = args.expectedNames
if expected ~= nil and #expected ~= #args.itemIndexes then
  fail("expectedNames must list one name per entry of itemIndexes.")
end
local chosen, seen = {}, {}
for n, i in ipairs(args.itemIndexes) do
  if i < 1 or i > #items then
    fail("No item #" .. i .. " on " .. ttype .. tidx .. "; the track has " .. #items .. " item(s).")
  end
  if expected ~= nil then
    local actual = items[i]:GetName()
    if actual ~= expected[n] then
      fail("Item #" .. i .. " is now '" .. tostring(actual) .. "', not '" .. tostring(expected[n]) .. "'; the track changed, re-run get_timeline_items.")
    end
  end
  if not seen[i] then
    seen[i] = true
    chosen[#chosen + 1] = items[i]
  end
end
local ok = tl:DeleteClips(chosen, false)
if not ok then fail("Resolve could not delete the selected items.") end
return { ok = true, deleted = #chosen, remaining = #(tl:GetItemListInTrack(ttype, tidx) or {}) }
''')

# 12. export_timeline
lua_tool(
    "export_timeline",
    "Export a timeline (default: current) to an interchange file at an absolute path (AAF, EDL, FCPXML, "
    "OTIO, DRT, CSV, ALE, HDR/Dolby Vision metadata). The parent folder must exist and the file name may "
    "not start with a dot. An existing file is replaced only with overwrite:true AND confirm:true, after "
    "the user agreed. Returns {ok, path, type}.",
    _schema({
        "path": {"type": "string", "minLength": 1, "maxLength": 1000, "description": "Absolute output file path."},
        "type": {"type": "string", "enum": _EXPORT_TYPES, "description": "Export format."},
        "overwrite": {"type": "boolean", "description": "Replace an existing file (default false); needs confirm:true too."},
        "confirm": {"type": "boolean", "enum": [True], "description": "Required (true) only when overwriting an existing file."},
        "timeline": _TIMELINE,
    }, ["path", "type"]),
    DESTRUCTIVE, timeout=120, lua=r'''
local path = args.path
if path:sub(1, 1) ~= "/" then fail("Path must be absolute.") end
local parent = path:match("^(.*)/[^/]+$")
if parent == nil or parent == "" then parent = "/" end
local base = path:match("([^/]+)$") or ""
if base:sub(1, 1) == "." or base:find("/", 1, true) then fail("The file name may not start with a dot.") end
if not bmd.direxists(parent) then fail("Folder does not exist: " .. parent .. ".") end
if bmd.fileexists(path) then
  if not args.overwrite then fail("File exists: " .. path .. ". Pass overwrite:true (and confirm:true after the user agrees) to replace it.") end
  if args.confirm ~= true then fail("Replacing " .. path .. " needs confirm:true after the user agrees.") end
end
local tl = need_timeline(args.timeline)
local exportType = resolve["EXPORT_" .. args.type]
if exportType == nil then fail("This Resolve build has no export type " .. args.type .. ".") end
local subtype = (args.type == "AAF") and resolve.EXPORT_AAF_NEW or resolve.EXPORT_NONE
local ok = tl:Export(path, exportType, subtype)
if not ok then
  fail("Resolve could not export timeline '" .. tostring(tl:GetName()) .. "' as " .. args.type .. " to " .. path .. ".")
end
return { ok = true, path = path, type = args.type }
''')
