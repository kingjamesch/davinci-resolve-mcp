# Changelog

## Unreleased

- Windows support (tested on Windows 11 with DaVinci Resolve 21.2 free). `py install.py` installs the bridge into `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility`, uses `%LOCALAPPDATA%\ClaudeMCP` as the state directory and checks that it is a Resolve Media Storage location (required on Windows for `GetSubFolderList`).
- New `davinci_mcp/paths.py` holds the per-platform Resolve folders.
- Transport: `msvcrt.locking` replaces `fcntl.flock` on Windows; request chunks shrink to keep folder paths within `MAX_PATH`; the liveness check uses `OpenProcess` on Windows instead of `os.kill(pid, 0)`, which would terminate Resolve there.
- Bridge: joins the inbox path with the state directory's own separator (Windows MediaStorage only lists backslash paths); falls back to `%LOCALAPPDATA%\ClaudeMCP` when no stamp is present.
- `import_media` and `export_timeline` accept Windows absolute paths (`C:\`, `C:/`, UNC) through a new `is_abs_path` prelude helper.
- Tests run on Windows (including the fuscript-backed bridge round trip); CI adds `windows-latest`.

## 0.2.0 — 2026-10-31

- Claude Code plugin: the repository is now its own plugin marketplace. `/plugin marketplace add kingjamesch/davinci-resolve-mcp` then `/plugin install davinci-resolve@davinci-resolve-mcp` registers the `davinci` MCP server without cloning.
- Bundled skill `davinci-resolve` with the measured Resolve conventions (frame spaces, marker rules, Lua idioms, Studio-only traps) so Claude drives Resolve correctly from the first call.
- `/davinci-resolve:setup` command that copies the Lua bridge into Resolve's Scripts folder from inside Claude Code.
- Bridge and stop script report version 0.2.0 (no protocol change; a 0.1.0 bridge keeps working).
- Listed on the MCP Registry as `io.github.kingjamesch/davinci-resolve-mcp` (metadata-only `server.json`).

## 0.1.0 — 2026-10-31

Initial release.

- Lua bridge (`Claude_MCP_Bridge`) that runs inside DaVinci Resolve 21.1 and answers requests through `Fusion.prefs`.
- Stop script (`Claude_MCP_Bridge_Stop`).
- Python stdio MCP server named `davinci`, standard library only, Python 3.9 compatible.
- 33 tools: 13 read tools, 19 write tools and the open-ended `run_lua`.
- Destructive tools require `confirm: true`. Responses are size-capped.
- `scripting_api_docs` searches Blackmagic's shipped scripting API reference.
- Installer (`install.sh`) with `--no-claude`, `--claude-desktop`, `--check` and `--uninstall [--purge]`, plus `uninstall.sh`.
- Generated tool reference in `docs/TOOLS.md`.
- Unit tests, an offline bridge round-trip test, and a contract test for the tool list.
