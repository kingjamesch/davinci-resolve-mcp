# MCP record — DaVinci Resolve (free) ⇄ Claude Code

Status: v0.1 design record, 2026-10-31. Owner: main model. Mode: PROVIDE-MCP (local stdio).

## 0. Where it runs, as whom

- **Local stdio server** on the user's Mac, started by Claude Code (`claude mcp add --scope user`).
  Runs as the macOS user. No network listener of any kind. No credentials: the identity is the
  logged-in macOS user, and the trust boundary is the state directory under the user's own
  `~/Library/Application Support` (mode 0700).
- **Why a bridge:** DaVinci Resolve 21.1.2 (free, direct-download build, bundle
  `com.blackmagic-design.DaVinciResolve`) blocks *external* scripting (`scriptapp("Resolve")` returns
  None from every Python) and hides `.py` files from Workspace > Scripts, but still loads and runs
  **Lua** menu scripts on a background script thread with the `resolve` object. Measured on this
  machine (2026-10-31, build 21.1.2.10):

  | Inside the Lua menu-script sandbox | Status |
  |---|---|
  | `resolve`, `fusion`/`fu`/`app`, `bmd`, `comp` globals | present |
  | `io`, `dofile`, `loadfile`, `require`, `package`, `ffi`, `debug`, `os.execute/remove/rename` | **nil** |
  | `loadstring`, `load`, `setfenv`, `pcall/xpcall`, `coroutine`, `bit`, `lpeg` | present |
  | `bmd.fileexists / direxists / wait / gettime (seconds) / getpid / createuuid` | present |
  | `fusion:SavePrefs` | **nil** (removed vs 21.1.0) |
  | `fu:SetData(key, v)` | present — **writes Fusion.prefs to disk synchronously** (~0.3 s observed) |
  | `fu:GetData(key)`, `fusion:GetPrefs/SetPrefs` (in-memory, shared across script runs) | present |
  | `resolve:GetMediaStorage():GetSubFolderList(dir)` | present — full paths of every subfolder (25 in 18 ms) |
  | `resolve:GetMediaStorage():GetFileList(dir)` | media files only |
  | `print` | muted |
  | `fu.UIManager` | **never touch** — raises the Studio upsell modal which swallows all menu clicks |
  | `fusion:RunScript`, `comp:RunScript` | **never call** — opened an "Open File" modal |
  | Long-running loop in a menu script | does NOT block the UI (AX queries answered in 0.25 s during a 6 s loop) |

## 1. Consumers

Claude Code (this user, user scope). Claude Desktop optional via its `mcpServers` config. One
user, one Resolve instance, one bridge.

## 2. Transport & identity

- Claude Code ⇄ **`/usr/bin/python3 <repo>/server.py`** over stdio (newline-delimited JSON-RPC, MCP
  `2025-06-18`, negotiated down if the client offers older).
- Python ⇄ Resolve: file/prefs channel, spec in `PROTOCOL.md`. Requests are hex-encoded folder
  names under `STATE_DIR/inbox/<id>/`; responses are `fu:SetData` values persisted in
  `Fusion.prefs`.
- Identity: none beyond the macOS user. **Anyone who can create folders in `STATE_DIR` can run
  Lua inside Resolve as this user** (same posture as Resolve's own Scripts folder). `STATE_DIR` is
  created 0700. Leak response: none needed (no secret); abuse response: stop the bridge
  (Workspace > Scripts > Claude_MCP_Bridge_Stop) and delete `STATE_DIR`.
- Last response stays in `Fusion.prefs` (user-readable file, Fusion writes it 0666 by default).
  Responses are therefore capped and the user is told about it in the README.

## 3. Tool surface

All tools: one JSON object per answer; absolute paths only; frame numbers are timeline-absolute
integers; timecodes `HH:MM:SS:FF`; 1-based track and item indexes (Resolve's own convention).
Annotations are MCP `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
`openWorldHint:false`).

### Reads (readOnlyHint: true)

| Tool | Purpose | Inputs (caps) | Output |
|---|---|---|---|
| `resolve_status` | Is the bridge alive; product, version, page, project, timeline, bridge session/uptime | — | `{bridge:{running,session,uptime_s,version}, product, version, studio, page, project, timeline}` |
| `list_projects` | Project names in the current project-manager folder, current project | — | `{current, projects:[name], folders:[name]}` |
| `get_project_info` | Current project: name, fps, resolution, timeline count/names, current timeline, render job count | — | `{name, frameRate, width, height, timelines:[...], currentTimeline, renderJobs}` |
| `list_timelines` | Every timeline: index, name, isCurrent, startFrame, endFrame, fps | — | `{count, current, timelines:[{index,name,isCurrent,startFrame,endFrame,frameRate}]}` |
| `get_timeline_info` | One timeline (current or by name): name, timecodes, frames, fps, track counts, marker count, playhead | `timeline?` | `{name,startTimecode,currentTimecode,startFrame,endFrame,durationFrames,frameRate,tracks:{video,audio,subtitle},markerCount}` |
| `get_timeline_items` | Items on one track, paginated | `trackType ∈ video/audio/subtitle`, `trackIndex ≥1`, `offset ≥0`, `limit 1–200 (default 50)`, `timeline?` | `{track, total, offset, limit, truncated, items:[{index,name,start,end,startAbs,endAbs,duration,leftOffset,rightOffset,enabled,mediaPoolItem}]}` (start/end as Resolve reports them, absolute on 21.1; startAbs/endAbs normalised absolute) |
| `get_timeline_markers` | Markers of a timeline, paginated | `timeline?`, `offset`, `limit 1–200` | `{total, offset, limit, truncated, markers:[{frame,color,name,note,duration,customData}]}` |
| `list_media_pool` | Folder tree + clips of one folder, paginated | `folder?` (path like `Master/B-roll`), `offset`, `limit 1–200` | `{folder, subfolders:[name], total, truncated, clips:[{name,id,type,duration,fps,resolution,filePath}]}` |
| `get_clip_properties` | One media-pool clip's properties and metadata | `name?` or `id?` | `{name,id,properties:{allowlisted},metadata:{...}}` |
| `get_render_status` | Render queue and job status | — | `{isRendering, jobs:[{id,name,timeline,status,completionPercentage,targetDir,outputFilename,error}]}` |
| `list_render_options` | Render presets, formats, codecs for a format, quick-export presets | `format?` | `{presets:[...], formats:[...], codecs:[...], quickExportPresets:[...]}` |
| `scripting_api_docs` | Search Blackmagic's shipped API reference (README + .pyi) | `query (1–120 chars)`, `limit 1–30` | `{matches:[{kind,class,signature,doc,source}], truncated}` |

### Writes (readOnlyHint: false)

| Tool | Purpose | Inputs (caps) | Annotations |
|---|---|---|---|
| `open_page` | Switch UI page | `page ∈ media/cut/edit/fusion/color/fairlight/deliver` | idempotent |
| `open_project` | Load a project by name (saves current first) | `name` | — |
| `save_project` | Save current project | — | idempotent |
| `set_current_timeline` | Make a timeline current | `name` | idempotent |
| `set_timecode` | Move playhead | `timecode HH:MM:SS:FF` | idempotent |
| `create_timeline` | Empty timeline (optionally from clips) | `name`, `clipNames? ≤100` | — |
| `add_marker` | Timeline marker | `frame ≥0`, `color ∈ Resolve colors`, `name ≤200`, `note ≤2000`, `duration 1–100000` | — |
| `delete_markers` | Delete by frame or by color (`All`) | `frame?` xor `color?`, `confirm:true` required | **destructive** |
| `import_media` | Import files/folders to current media-pool folder | `paths ≤100 absolute` | — |
| `append_clips_to_timeline` | Append media-pool clips (optional in/out) to current timeline | `clips ≤100 [{name|id,startFrame?,endFrame?,trackIndex?,recordFrame? (absolute),mediaType?}]`, `timeline?` | — |
| `insert_timeline_element` | Title / Fusion title / generator at the playhead of the current timeline | `kind ∈ title/fusion_title/generator/fusion_generator/ofx_generator`, `name` | — |
| `delete_timeline_items` | Delete items by track + indexes | `trackType`, `trackIndex`, `itemIndexes ≤50`, `expectedNames?` (refused on mismatch), `confirm:true`; never ripple (broken on this edition) | **destructive** |
| `add_render_job` | Queue a render | `targetDir abs`, `customName?`, `preset?`, `format?`, `codec?`, `markIn?/markOut?` | — |
| `start_render` | Start rendering (non-blocking); without `jobIds` the whole queue | `jobIds? ≤50` | — |
| `stop_render` | Stop rendering | — | idempotent |
| `export_timeline` | Export timeline file | `path abs` (no dot-file), `type` enumerated, `overwrite?` + `confirm:true` to replace an existing file | **destructive** when overwriting |
| `create_media_pool_folder` | New bin under a parent path | `name`, `parent?` | — |
| `delete_render_job` | Remove one render job | `jobId`, `confirm:true` | **destructive** |
| `get_project_setting` / `set_project_setting` | Read/write one project setting by key | `key`, `value` | read / idempotent |
| `run_lua` | Run a Lua 5.1 chunk inside Resolve with `resolve`, `fusion`, `project`, `timeline`, `mediaPool` bound; returns first return value + `print` output | `code ≤48 KB`, `timeout_s 1–600`, `max_kb 1–256` | **destructive** (open-ended) |

Non-goals (deliberately not tools): anything Studio-only (noise reduction, transcription,
scene-cut AI, UIManager), file deletion on disk, project deletion, database management, Fusion
comp editing beyond `run_lua`, batch "do everything" tools.

### Decisions (resolving the review's ambiguities)

- Answers are MCP `text` content holding one JSON object (no `structuredContent`/`outputSchema` yet).
- `add_marker.duration` is in frames. `endFrame` is exclusive (`durationFrames = endFrame - startFrame`).
- `open_project` saves the current project first; a Resolve dialog, if any, blocks the bridge until dismissed (timeout sentence).
- Parallel `tools/call`s from one or several Claude sessions are serialised by the `flock`; queue time counts toward the timeout.
- Every request folder with a valid id gets an envelope, including rejections (`ok:false`, `error` sentence); rejected ids are marked seen.
- "Resolve is not running" = the session's `pid` is dead, confirmed by `pgrep` for the Resolve binary.
- Liveness = session state `running` AND pid alive AND beat < 45 s; a ping decides when they disagree. `resolve_status` queues behind a long-running request like any other call.
- `truncated:true` means: use `offset`/`limit` (every list is paginated) or return fewer fields from `run_lua`.
- `prints` are returned only by `run_lua`; they do not count toward `max_kb`.
- `run_lua.timeout_s` is a server-side wait only (the Lua host cannot pre-empt a chunk); the description says so.
- Version skew: the envelope carries `bridge`; the server does not refuse, the bridge version is reported by `resolve_status`.
- `scripting_api_docs` reads Blackmagic's shipped reference at `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting` and fails with a sentence if it is absent.

## 4. Data caps

`limit ≤ 200` items per page; response JSON ≤ 64 KB default (`max_kb` ≤ 256 on `run_lua`);
request code ≤ 48 KB; ≤ 100 paths / clips per write; ≤ 50 items per delete; prints ≤ 200 lines /
16 KB. Over-cap responses return `truncated:true` plus the byte count, never a silent cut.

## 5. Error contract (one sentence each)

- Bridge not running → "The Resolve bridge is not running. In DaVinci Resolve click Workspace >
  Scripts > Claude_MCP_Bridge (Resolve must be open with a project), then retry."
- Resolve not running → "DaVinci Resolve is not running."
- Timeout → "The bridge did not answer within N s; Resolve may be showing a dialog or running a
  long operation. The outcome of this call is unknown: check the Resolve window and read the current
  state before retrying a write."
- Lua runtime error → "Lua error: <first line of message>" (message ≤ 2 KB, no values echoed).
- Validation → "<tool>: <argument> must be <rule>."
- Studio-only → "This needs DaVinci Resolve Studio; the free edition cannot run it."
- Not found → "No timeline/clip/project named '<name>'."

## 6. Logging

stderr only: `ts tool id ok|error ms` — never code, arguments, results, names or notes; internal
errors log the exception class only. No telemetry. Optional `DAVINCI_MCP_DEBUG=1` adds byte counts
and tracebacks. Several Claude Code sessions may each run a server: an `flock` in `STATE_DIR` serialises
their requests, and a restarted bridge never re-runs a request it finds waiting.

## 7. Cost hooks

None: no paid work. Render jobs consume local CPU/GPU only; `start_render` is non-blocking.

## 8. Tests

- Unit (stdlib `unittest`): Lua literal encoder, hex chunking/decoding, prefs parser, request
  id/validation, docs index, tool schema validation, tools/list contract snapshot.
- Offline round trip: the real `Claude_MCP_Bridge.lua` run under Resolve's bundled `fuscript -l lua`
  with a stub `resolve`/`fu`/`bmd` harness, driven by the real Python transport.
- Live smoke (this session, against the running Resolve): ping, status, list/get reads, `run_lua`,
  then a self-cleaning write cycle on a scratch timeline ("Claude MCP smoke": create → insert
  generator → add marker → read → delete marker → delete timeline → restore current timeline).

## 9. Registration & distribution

`./install.sh` copies the two Lua files into `~/Library/Application Support/Blackmagic
Design/DaVinci Resolve/Fusion/Scripts/Utility/`, creates `STATE_DIR` (0700), and runs
`claude mcp remove davinci; claude mcp add --scope user davinci -- /usr/bin/python3 <repo>/server.py`.
Server name `davinci`, version from `davinci_mcp/__init__.py`. Tool names are the API: renames keep
an alias for one minor version. Resolve must be restarted once after install for the menu entry to
appear (it scans Scripts only at startup); after that, one click per Resolve session.

## 10. Deferred

Color-page node tools, gallery/stills, Fairlight, compound/Fusion clip creation, keyboard/preset
import-export, project archive/restore, auto-start of the bridge at Resolve launch (no startup
hook measured), Claude Desktop `.mcpb` packaging, Windows paths.
