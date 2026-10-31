---
name: resolve
description: Drive DaVinci Resolve (free or Studio, 21.1+) through the `davinci` MCP server — bridge start/stop, frame and timecode conventions, marker rules, how to write run_lua chunks against Blackmagic's scripting API, and the Studio-only traps that block the bridge. Use whenever a task mentions DaVinci Resolve, Resolve timelines, clips, markers, grades, renders or the Workspace > Scripts bridge.
---

# Working with DaVinci Resolve through the `davinci` MCP server

## Before anything else

1. Call `resolve_status`. If `bridge.running` is false, tell the user to open Resolve with a project
   and click **Workspace > Scripts > Claude_MCP_Bridge**, then call it again. Nothing works without it.
2. Read before you write: `list_timelines`, `get_timeline_info`, `get_timeline_items`,
   `list_media_pool` first. Never guess names, ids or indexes.
3. Destructive tools (`delete_markers`, `delete_timeline_items`, `delete_render_job`,
   `export_timeline` with `overwrite`, and `run_lua` code that deletes or overwrites) need the
   user's explicit yes first; only then pass `confirm: true`. Pass `expectedNames` to
   `delete_timeline_items` so a changed track is refused.
4. A timeout means the outcome is unknown. Re-read state before retrying a write.

## Conventions that bite

- **Frames are absolute timeline frames.** A 24 fps timeline starting at 01:00:00:00 has
  `startFrame` 86400. `get_timeline_info` gives `startFrame`/`endFrame` (`endFrame` is exclusive).
- **Markers**: `add_marker.frame` is absolute and must be strictly inside the clip content;
  Resolve rejects the exact start frame and anything past the end.
- **Timeline items**: on Resolve 21.1 `start`/`end` are absolute too; always use the
  `startAbs`/`endAbs` fields, which are normalised for every build.
- **Track and item indexes are 1-based.** Timecodes are `HH:MM:SS:FF`.
- **Stills ignore source in/out** when appended: they land at the default still duration
  (usually 5 s). Trim afterwards with `SetRightOffset` and move with `SetPosition` (see below).
- **No razor/split API.** To split a clip: place copies, trim each with `SetRightOffset`, then
  `SetPosition` them back to back. `DeleteClips(items, true)` (ripple) wipes the whole track on
  the free edition; the tools never use it.
- **Property values are strings** as Resolve returns them (`"1920"`, `"24"`), except a few like
  `timelineFrameRate` which come back as numbers. Pass them through.
- **Read tools must not change the UI.** `GetCurrentRenderMode` switches to the Deliver page;
  the `list_render_options` tool restores the page, your own Lua must too.

## Writing `run_lua`

The API is Blackmagic's scripting API with colon calls and 1-based lists. Bound names:
`resolve`, `projectManager`, `project`, `timeline`, `mediaPool`, `fusion`, `bmd`. Look up
signatures with `scripting_api_docs` (it searches the shipped `.pyi` and README). Pattern:

```lua
local items = timeline:GetItemListInTrack("video", 1) or {}
local out = {}
for i, it in ipairs(items) do
  out[#out + 1] = { index = i, name = it:GetName(), start = it:GetStart(), ["end"] = it:GetEnd() }
end
return { count = #items, items = out }
```

- Always `return` a table; it becomes the JSON result. `print()` output comes back in `prints`.
- Check every API return (`false`/`nil` means refused) and `error("one plain sentence")`.
- `["end"]` is a keyword: quote it as a table key.
- Keep loops bounded. A chunk that never returns blocks the bridge until Resolve restarts.
- `io`, `dofile`, `loadfile`, `require`, `debug`, `ffi` and `fusion:SavePrefs` do not exist in
  the sandbox. `bmd.fileexists` and `bmd.direxists` do.
- Color by numbers: `item:SetCDL({NodeIndex=1, Slope="1.06 1 0.92", Offset="0.01 0 -0.01",
  Power="1 1 1", Saturation=1.05})`, `item:CopyGrades({others})`, `graph:ResetAllGrades()`,
  `graph:SetLUT(1, "/path/to.cube")`.

## Never do this on the free edition

Studio-only features (noise reduction, Magic Mask, SuperScale, transcription, AI scene cut,
speech generation) and `fu.UIManager` open an upgrade dialog that freezes the bridge until the
user dismisses it. `fusion:RunScript` and `comp:RunScript` open a file picker. If a call times
out, ask the user to look at Resolve for a dialog.

## Long operations

Renders and exports block the bridge. Queue with `add_render_job`, start with `start_render`,
poll `get_render_status`. Raise `timeout_s` on `run_lua` for anything slow.
