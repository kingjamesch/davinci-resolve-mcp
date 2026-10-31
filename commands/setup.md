---
description: One-time setup — copy the Claude MCP bridge into DaVinci Resolve's Scripts menu
allowed-tools: Bash(/usr/bin/python3 *), Bash(find *), Bash(ls *)
---

Install the DaVinci Resolve Lua bridge for this plugin. The plugin already registers the `davinci`
MCP server; this step only places the two Lua scripts where Resolve finds them.

1. Run the installer from the plugin directory, skipping the Claude Code registration (the plugin
   handles that):

   ```
   /usr/bin/python3 "${CLAUDE_PLUGIN_ROOT}/install.py" --no-claude
   ```

   If `${CLAUDE_PLUGIN_ROOT}` did not expand, locate the installer with
   `find ~/.claude/plugins -name install.py -path '*davinci-resolve*' 2>/dev/null | head -1`
   and run that path with `--no-claude`.

2. Report the installer's output to the user, then give exactly these next steps:
   - Quit and reopen DaVinci Resolve once (it scans the Scripts menu only at startup).
   - In Resolve, with a project open: Workspace > Scripts > Claude_MCP_Bridge. Do this once per
     Resolve session; Claude_MCP_Bridge_Stop stops it.
   - Then ask Claude for `resolve_status`.

3. If the user previously ran `./install.sh` from a clone, tell them they now have two `davinci`
   servers (user scope and plugin) and should remove one: `claude mcp remove davinci -s user`.

Do not run anything else and do not modify Resolve beyond the installer.
