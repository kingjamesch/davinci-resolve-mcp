# Changelog

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
