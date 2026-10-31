# davinci-resolve-mcp

[![test](https://github.com/kingjamesch/davinci-resolve-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/kingjamesch/davinci-resolve-mcp/actions/workflows/test.yml)

Let Claude Code drive the free edition of DaVinci Resolve 21.1.

Resolve 21.1 free removed Python scripts from the Workspace > Scripts menu and blocks external scripting. Lua menu scripts still run. This project uses that gap. A Lua bridge runs inside Resolve and a tiny Python MCP server runs outside. The server sends Lua to the bridge, the bridge runs it against the live `resolve` object and sends the answer back. A call takes about 80 to 95 ms as measured. There are 33 tools, plus `run_lua` for anything the named tools do not cover.

## Requirements

- macOS
- DaVinci Resolve 21.1.2, free or Studio
- Claude Code
- Python 3.9 or newer at `/usr/bin/python3`
- No pip installs. The server uses the standard library only.

## Install

```bash
git clone https://github.com/kingjamesch/davinci-resolve-mcp.git
cd davinci-resolve-mcp
./install.sh
```

The installer does four things:

1. Copies the two Lua scripts into `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`. It stamps the state directory path into the bridge.
2. Creates the state directory `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/ClaudeMCP` with mode 0700.
3. Registers the server with Claude Code at user scope as `davinci`.
4. Prints the next steps.

Next steps, as the installer prints them:

```
NEXT STEPS
1) Quit and reopen DaVinci Resolve once (it scans the Scripts menu only at startup).
2) In Resolve: Workspace > Scripts > Claude_MCP_Bridge (do this once per Resolve session; Claude_MCP_Bridge_Stop stops it).
3) In Claude Code run /mcp and check that 'davinci' is connected, then ask for resolve_status.
```

The Resolve restart is needed once, after the first install. Resolve only reads the Scripts folder at startup.

Other flags:

| Flag | Effect |
|---|---|
| `--claude-desktop` | Also adds `davinci` to the Claude Desktop config. Optional. An existing config is backed up first. |
| `--no-claude` | Skip the Claude Code registration. |
| `--check` | Report what is installed. |
| `--uninstall` | Remove the Lua scripts and the Claude Code registration. Add `--purge` to delete the state directory too. `./uninstall.sh` does the same. |

## Install as a Claude Code plugin

If you only use Claude Code, skip the clone. Inside Claude Code:

```
/plugin marketplace add kingjamesch/davinci-resolve-mcp
/plugin install davinci-resolve@davinci-resolve-mcp
/davinci-resolve:setup
```

The plugin registers the `davinci` server itself; `/davinci-resolve:setup` copies the two Lua
scripts into Resolve's Scripts folder. Then restart Resolve once and click Workspace > Scripts >
Claude_MCP_Bridge. The plugin also ships a `resolve` skill with the conventions Claude needs
(absolute frames, marker rules, Lua idioms, Studio-only traps).

Use either the plugin or `./install.sh`, not both, or Claude Code sees two `davinci` servers
(`claude mcp remove davinci -s user` removes the clone-based one).

## Daily use

1. Open Resolve with a project.
2. Click Workspace > Scripts > Claude_MCP_Bridge. Do this once per Resolve session.
3. In Claude Code, ask for `resolve_status` first. It tells you whether the bridge is alive.
4. To stop the bridge, click Workspace > Scripts > Claude_MCP_Bridge_Stop.

Example prompts:

- "Call resolve_status, then list my timelines."
- "Add a red marker named 'fix audio' at 00:00:10:00 on the current timeline."
- "Import everything in /Users/me/Footage/day1 and make a timeline called Day 1 from it."
- "Queue an mp4 render of the current timeline into /Users/me/Exports, start it and tell me when it finishes."
- "Use run_lua to list the clip colors on video track 1."

## Tools

The full reference, with every argument, is in [docs/TOOLS.md](docs/TOOLS.md). It is generated from the code.

- **Status and project:** `resolve_status`, `list_projects`, `get_project_info`, `get_project_setting`, `open_project`, `save_project`, `set_project_setting`, `open_page`
- **Timelines:** `list_timelines`, `get_timeline_info`, `get_timeline_items`, `get_timeline_markers`, `set_current_timeline`, `set_timecode`, `create_timeline`, `add_marker`, `delete_markers`, `insert_timeline_element`, `delete_timeline_items`, `export_timeline`
- **Media pool:** `list_media_pool`, `get_clip_properties`, `create_media_pool_folder`, `import_media`, `append_clips_to_timeline`
- **Render:** `get_render_status`, `list_render_options`, `add_render_job`, `start_render`, `stop_render`, `delete_render_job`
- **Escape hatch:** `run_lua` runs any Lua 5.1 chunk inside Resolve. `scripting_api_docs` searches Blackmagic's shipped API reference so Claude can write that Lua correctly.

Conventions: paths are absolute, frames are integers in the timeline's own numbering, timecodes are `HH:MM:SS:FF`, and track and item indexes start at 1.

## How it works

- Claude Code starts `server.py` over stdio. The server speaks MCP and holds no network socket.
- To send a request, the server creates folders under the state directory. The request is hex-encoded into the folder names.
- The Lua bridge lists those folders every 30 ms, decodes the request and runs it with the `resolve` object bound.
- The bridge returns the answer with `fu:SetData`, which Resolve writes to `Fusion.prefs`. The server polls that file.
- The last response stays in `Fusion.prefs` and is cleared about 10 seconds after it was written; the bridge also clears it when it stops.
- Several Claude Code sessions can share one Resolve: every server takes a lock in the state directory, so requests run one at a time.

The wire format is in [docs/PROTOCOL.md](docs/PROTOCOL.md). The design record is in [docs/MCP-RECORD.md](docs/MCP-RECORD.md).

## Troubleshooting

- **Tools say the bridge is not running.** Open Resolve with a project and click Workspace > Scripts > Claude_MCP_Bridge, then retry.
- **Workspace > Scripts shows no Claude_MCP_Bridge.** Resolve was not restarted after the install. Quit and reopen it. Run `./install.sh --check` to confirm the files are in place.
- **A tool times out.** Resolve is probably showing a dialog, such as the Studio upsell or a frame-rate question after an import. Dismiss it in Resolve. Long operations also block the bridge until they finish.
- **A Studio-only feature fails.** Do not use Studio-only features on the free edition. They fail or open an upgrade dialog that blocks the bridge.
- **The old davinci-resolve-lite-mcp Python scripts do nothing.** They cannot work on 21.1 or later, because Python scripts no longer appear in the Scripts menu and external scripting is blocked.

## Not included yet

Color-page node tools, gallery and stills, Fairlight, compound and Fusion clips, keyboard and preset import or export, project archive and restore, automatic bridge start when Resolve launches, a Claude Desktop `.mcpb` package, and Windows. `run_lua` covers most of these in the meantime. Set `DAVINCI_MCP_DISABLE_RUN_LUA=1` in the server environment to remove `run_lua` from the tool list.

## Security posture

- Local only. The server opens no network port and sends no telemetry.
- There are no credentials. The identity is your macOS user.
- Anyone who can write to the state directory can run Lua inside Resolve as you. The directory is created with mode 0700.
- Destructive tools need `confirm: true`. `run_lua` is open-ended, so Claude is told to ask you before running code that deletes or overwrites.
- Responses are size-capped. Over-cap results come back marked `truncated`.
- `Fusion.prefs` is readable by your user and holds the last response until it is cleared.
- To shut it down, run the Stop script and delete the state directory.
- The server logs only the tool name, status and duration to stderr. It never logs arguments or results.

## Development

Run the tests:

```bash
/usr/bin/python3 -m unittest discover -s tests
```

Regenerate the tool reference after changing any tool:

```bash
/usr/bin/python3 scripts/gen_tools_doc.py
```

A test fails if `docs/TOOLS.md` is stale or if a tool is renamed.

Layout:

```
server.py                  stdio entry point
install.py, install.sh     installer, uninstaller (uninstall.sh)
bridge/                    Lua scripts that run inside Resolve
davinci_mcp/               server, transport, prefs parser, Lua encoder, docs index
davinci_mcp/tools/         one module per tool group
docs/                      MCP-RECORD.md, PROTOCOL.md, TOOLS.md (generated)
scripts/gen_tools_doc.py   generates docs/TOOLS.md
tests/                     unit tests and the offline bridge round trip
```

## Credits

- The JSON encoder and several measured host facts are adapted from [saadk408/davinci-resolve-lua-mcp](https://github.com/saadk408/davinci-resolve-lua-mcp), MIT.
- The idea of using Lua over the preferences file as a channel comes from AutoSubs.

## License

MIT. See [LICENSE](LICENSE).
