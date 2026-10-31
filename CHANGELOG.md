# Changelog

## 0.2.0 — 2026-10-31

- Claude Code plugin: the repository is now its own plugin marketplace. `/plugin marketplace add kingjamesch/davinci-resolve-mcp` then `/plugin install davinci-resolve@davinci-resolve-mcp` registers the `davinci` MCP server without cloning.
- Bundled skill `davinci-resolve` with the measured Resolve conventions (frame spaces, marker rules, Lua idioms, Studio-only traps) so Claude drives Resolve correctly from the first call.
- `/davinci-resolve:setup` command that copies the Lua bridge into Resolve's Scripts folder from inside Claude Code.
- Bridge and stop script report version 0.2.0 (no protocol change; a 0.1.0 bridge keeps working).

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
