-- Claude_MCP_Bridge_Stop.lua  v0.1.0
-- Stops a running Claude_MCP_Bridge from inside DaVinci Resolve:
--   Workspace > Scripts > Claude_MCP_Bridge_Stop
-- It raises the stop flag the bridge polls (fu:SetData, shared in-process and persisted to
-- Fusion.prefs). The bridge clears the flag when it exits. Nothing else is touched.
local F = rawget(_G, "fusion") or rawget(_G, "fu") or rawget(_G, "app")
if F == nil then error("Claude_MCP_Bridge_Stop: no fusion object") end
local ok, err = pcall(function() F:SetData("ClaudeMCP.CMCP_Stop", "1") end)
if not ok then error("Claude_MCP_Bridge_Stop: SetData failed: " .. tostring(err)) end
