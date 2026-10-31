-- Claude_MCP_Bridge.lua  v0.1.0
-- The in-Resolve half of davinci-resolve-mcp. Launch from DaVinci Resolve:
--   Workspace > Scripts > Claude_MCP_Bridge      (start / take over)
--   Workspace > Scripts > Claude_MCP_Bridge_Stop (stop)
-- It polls a request folder (docs/PROTOCOL.md), runs the Lua chunk a request carries against
-- the live `resolve` object, and answers through fu:SetData, which Resolve persists to
-- Fusion.prefs. It runs on Resolve's script thread (the UI stays responsive), needs no
-- dependencies, never opens UI, never deletes files, and exits on stop or take-over.
-- JSON encoder and several measured host facts adapted from saadk408/davinci-resolve-lua-mcp (MIT).

local VERSION = "0.1.0"
local BRIDGE = "davinci-resolve-mcp bridge v" .. VERSION
local CFG = rawget(_G, "CMCP_CONFIG") or {}          -- test harness overrides (see tests/lua_harness.lua)
local STATE_DIR_STAMP = [==[@@STATE_DIR@@]==]         -- install.py stamps the absolute state dir here
local PREFIX = "ClaudeMCP."                           -- fu:SetData namespace -> Global.Script.GlobalData.ClaudeMCP.*
local TICK = CFG.tick or 0.03                         -- seconds between PENDING polls
local BEAT_EVERY = CFG.beat_every or 15               -- idle heartbeat period (s)
local RESP_CLEAR_AFTER = 10                           -- clear a consumed response from prefs after this many idle seconds
local STALE_S = 120                                   -- requests older than this are refused
local MAX_PAYLOAD = 256 * 1024
local MAX_KB_DEFAULT, MAX_KB_CEILING = 64, 256
local PRINT_MAX_LINES, PRINT_MAX_BYTES, PRINT_LINE_MAX = 200, 16384, 2048
local ERROR_MAX = 2048
local DEPTH_MAX = 16
local SEEN_MAX = 2000
local ID_MIN, ID_MAX = 6, 32

-- Primitives -----------------------------------------------------------------------------------

local function tos(v)
  local ok, s = pcall(tostring, v)
  if ok and type(s) == "string" then return s end
  return "<" .. type(v) .. ">"
end

local function bmd_call(name, ...)
  if type(bmd) == "table" and type(bmd[name]) == "function" then
    local ok, v = pcall(bmd[name], ...)
    if ok then return v end
  end
  return nil
end

local function now()
  local t = bmd_call("gettime")
  if type(t) == "number" then return t end
  return os.clock()
end
local function wall() local ok, t = pcall(os.time); return (ok and type(t) == "number") and t or 0 end
local function wait(s) if not bmd_call("wait", s) then local t0 = now(); while now() - t0 < s do end end end
local function file_exists(path) return bmd_call("fileexists", path) == true end
local function ms_since(t0) return math.floor((now() - t0) * 1000 + 0.5) end
local function pack(...) return { n = select("#", ...), ... } end
local function clamp(n, lo, hi) if n ~= n or n < lo then return lo elseif n > hi then return hi end return n end
local function basename(p) return (tos(p):gsub("[/\\]+$", "")):match("([^/\\]+)$") or "" end

-- Hex ------------------------------------------------------------------------------------------

local HEX = {}
for i = 0, 255 do HEX[string.char(i)] = string.format("%02x", i) end
local function hex(s) return (s:gsub(".", HEX)) end
local function unhex(h)
  if type(h) ~= "string" or #h % 2 ~= 0 or h:find("[^%x]") then return nil end
  return (h:gsub("%x%x", function(cc) return string.char(tonumber(cc, 16)) end))
end

-- JSON encoder ----------------------------------------------------------------------------------

local function json_string(s)
  local out = s:gsub('[%c"\\]', function(c)
    if c == '"' then return '\\"'
    elseif c == '\\' then return '\\\\'
    elseif c == '\n' then return '\\n'
    elseif c == '\r' then return '\\r'
    elseif c == '\t' then return '\\t'
    end
    return string.format('\\u%04x', c:byte())
  end)
  return '"' .. out .. '"'
end

local function json_number(n)
  if n ~= n or n == math.huge or n == -math.huge then return "null" end
  if n == 0 then return "0" end
  if n == math.floor(n) and math.abs(n) < 9007199254740992 then return string.format("%.0f", n) end
  local s = string.format("%.14g", n)
  if tonumber(s) ~= n then s = string.format("%.17g", n) end
  return s
end

-- A table is a JSON array when its keys (ignoring Resolve's __flags) are exactly 1..n; an empty table is [].
local function array_shape(t)
  local n, flagged = 0, false
  for k in pairs(t) do
    if k == "__flags" then flagged = true
    elseif type(k) ~= "number" or k ~= math.floor(k) or k < 1 then return false, 0, flagged
    else n = n + 1 end
  end
  for i = 1, n do if t[i] == nil then return false, n, flagged end end
  return true, n, flagged
end

local function key_less(a, b)
  local ta, tb = type(a), type(b)
  if ta ~= tb then return ta < tb end
  if ta == "number" then return a < b end
  return tos(a) < tos(b)
end

local function json_key(k)
  if type(k) == "number" then return json_string(json_number(k)) end
  return json_string(tos(k))
end

local BUDGET_SENTINEL = {}
local json_value
json_value = function(v, depth, seen)
  if seen.__budget then
    seen.__bytes = (seen.__bytes or 0) + 1
    if seen.__bytes > seen.__budget then error(BUDGET_SENTINEL) end
  end
  local tv = type(v)
  if v == nil then return "null"
  elseif tv == "boolean" then return v and "true" or "false"
  elseif tv == "number" then return json_number(v)
  elseif tv == "string" then return json_string(v)
  elseif tv ~= "table" then return json_string("<" .. tv .. " " .. tos(v) .. ">")
  end
  if getmetatable(v) ~= nil then return json_string("<object " .. tos(v) .. ">") end
  if seen[v] then return json_string("<cycle>") end
  if depth > DEPTH_MAX then return json_string("<depth>") end
  seen[v] = true
  if seen.__budget then
    local n = 0
    for _ in pairs(v) do n = n + 1; if n > 50000 then error(BUDGET_SENTINEL) end end
    seen.__bytes = seen.__bytes + n
  end
  local parts = {}
  local arr, n, flagged = array_shape(v)
  if arr then                      -- includes the empty table: our outputs are lists far more often than empty objects
    for i = 1, n do parts[i] = json_value(v[i], depth + 1, seen) end
    seen[v] = nil
    return "[" .. table.concat(parts, ",") .. "]"
  end
  local keys = {}
  for k in pairs(v) do if k ~= "__flags" then keys[#keys + 1] = k end end
  table.sort(keys, key_less)
  for i = 1, #keys do
    parts[i] = json_key(keys[i]) .. ":" .. json_value(v[keys[i]], depth + 1, seen)
  end
  seen[v] = nil
  return "{" .. table.concat(parts, ",") .. "}"
end

local function json(v) return json_value(v, 0, {}) end
-- Encode with a value-count budget (roughly proportional to bytes); over it the result is dropped.
local function json_budgeted(v, max_values)
  local ok, out = pcall(json_value, v, 0, { __budget = max_values, __bytes = 0 })
  if ok then return out end
  if out == BUDGET_SENTINEL then return nil, "too large" end
  error(out, 0)
end

-- Response envelope ----------------------------------------------------------------------------

local function envelope_json(f, result_json)
  local parts = {
    '"v":1',
    '"id":' .. json_string(tos(f.id)),
    '"session":' .. json_string(tos(f.session or "")),
    '"op":' .. json_string(tos(f.op or "")),
    '"ok":' .. (f.ok and "true" or "false"),
    '"ms":' .. json_number(f.ms or 0),
    '"error":' .. (f.error ~= nil and json(f.error) or "null"),
    '"prints":' .. json(f.prints or {}),
    '"truncated":' .. (f.truncated and "true" or "false"),
  }
  if f.result_bytes then parts[#parts + 1] = '"result_bytes":' .. json_number(f.result_bytes) end
  if f.prints_dropped then parts[#parts + 1] = '"prints_dropped":' .. json_number(f.prints_dropped) end
  parts[#parts + 1] = '"result":' .. (result_json or "null")
  parts[#parts + 1] = '"bridge":' .. json_string(VERSION)
  return "{" .. table.concat(parts, ",") .. "}"
end

local function utf8_cut(s, cut)
  while cut > 0 and cut < #s do
    local b = s:byte(cut + 1)
    if b < 0x80 or b >= 0xC0 then break end
    cut = cut - 1
  end
  return cut
end

-- Envelope JSON at most max_kb KB: over the cap the result becomes a string preview of its own
-- JSON (truncated=true, result_bytes=full size); then prints go; then the error text is cut.
local function fit_response(f, result, max_kb)
  local max = clamp(tonumber(max_kb) or MAX_KB_DEFAULT, 1, MAX_KB_CEILING) * 1024
  local okj, rj, why = pcall(json_budgeted, result, max * 4)
  if okj and rj == nil then
    rj = json_string("<result over " .. math.floor(max / 1024) .. " KB: return fewer fields or paginate>")
    f.truncated = true
  elseif not okj then
    rj = json_string("<encode error: " .. tos(rj) .. ">")
  end
  f.result_bytes = #rj
  local s = envelope_json(f, rj)
  if #s <= max then f.result_bytes = nil; return envelope_json(f, rj) end
  f.truncated = true
  local overhead = #s - #rj
  local cut = math.min(#rj, math.max(0, max - overhead - 32))
  for _ = 1, 8 do
    if cut <= 0 then break end
    cut = utf8_cut(rj, cut)
    local preview = json_string(rj:sub(1, cut))
    s = envelope_json(f, preview)
    if #s <= max then return s end
    local budget = max - (#s - #preview) - 16
    local next_cut = math.floor(cut * budget / #preview)
    cut = (next_cut < cut) and next_cut or (cut - 1)
  end
  f.prints_dropped = (f.prints_dropped or 0) + #(f.prints or {})
  f.prints = {}
  s = envelope_json(f, '""')
  if #s <= max then return s end
  if type(f.error) == "string" then f.error = f.error:sub(1, 256) else f.error = nil end
  return envelope_json(f, '""')
end

-- print capture ---------------------------------------------------------------------------------

local function new_capture()
  local st = { prints = {}, dropped = 0, bytes = 0 }
  st.print = function(...)
    local n = select("#", ...)
    local parts = {}
    for i = 1, n do parts[i] = tos((select(i, ...))) end
    local line = table.concat(parts, "\t")
    if #line > PRINT_LINE_MAX then line = line:sub(1, PRINT_LINE_MAX) .. "..." end
    if #st.prints >= PRINT_MAX_LINES or st.bytes + #line > PRINT_MAX_BYTES then
      st.dropped = st.dropped + 1
    else
      st.prints[#st.prints + 1] = line
      st.bytes = st.bytes + #line
    end
  end
  return st
end

-- Host objects -----------------------------------------------------------------------------------

local function is_api_object(obj) return type(obj) == "userdata" or type(obj) == "table" end
local function has_method(obj, name)
  local ok, t = pcall(function() return type(obj[name]) end)
  return ok and t == "function"
end
local function call_string(obj, method)
  local ok, v = pcall(function() return obj[method](obj) end)
  if ok and type(v) == "string" then return v end
  return nil
end

local function acquire_resolve()
  local r = CFG.resolve or rawget(_G, "resolve")
  if is_api_object(r) and call_string(r, "GetProductName") then return r end
  local Rf = rawget(_G, "Resolve")
  if type(Rf) == "function" then
    local ok, v = pcall(Rf)
    if ok and is_api_object(v) and call_string(v, "GetProductName") then return v end
  end
  return nil, "no resolve object (is a project open?)"
end

local function acquire_fusion()
  if CFG.fusion then return CFG.fusion end
  for _, name in ipairs({ "fusion", "fu", "app" }) do
    local obj = rawget(_G, name)
    if is_api_object(obj) and has_method(obj, "SetData") and has_method(obj, "GetData") then return obj end
  end
  return nil, "no fusion object with SetData/GetData"
end

local function resolve_state_dir()
  if CFG.state_dir then return CFG.state_dir end
  if #STATE_DIR_STAMP > 0 and STATE_DIR_STAMP:sub(1, 2) ~= "@@" then return (STATE_DIR_STAMP:gsub("[/\\]+$", "")) end
  local home = os.getenv("HOME")
  if type(home) == "string" and #home > 0 then
    return home .. "/Library/Application Support/Blackmagic Design/DaVinci Resolve/ClaudeMCP"
  end
  return nil, "cannot locate the state directory (HOME unset and no stamp)"
end

-- Prefs channel (the only SetData/GetData call sites) ---------------------------------------------

local function set_data(st, key, value)
  local ok, err = pcall(function() return st.fusion:SetData(PREFIX .. key, value) end)
  if not ok then st.last_error = "SetData " .. key .. ": " .. tos(err); return false end
  return true
end

local function get_data(st, key)
  local ok, v = pcall(function() return st.fusion:GetData(PREFIX .. key) end)
  if ok and v ~= nil then return tos(v) end
  return ""
end

local function session_json(st, state, extra)
  local t = { v = 1, session = st.session, pid = st.pid, started = st.started, state = state,
              product = st.product, version = st.version, bridge = VERSION, state_dir = st.state_dir }
  if extra then for k, v in pairs(extra) do t[k] = v end end
  return hex(json(t))
end

-- Requests -----------------------------------------------------------------------------------------

local function valid_id(id)
  return type(id) == "string" and #id >= ID_MIN and #id <= ID_MAX and id:match("^[a-z0-9]+$") ~= nil
end

-- Reassemble a request folder: subfolders "NNNN-<hex>" sorted by NNNN, contiguous from 0.
local function read_payload(st, dir)
  local ok, list = pcall(function() return st.media_storage:GetSubFolderList(dir) end)
  if not ok or type(list) ~= "table" then return nil, "cannot list request folder" end
  local chunks, count = {}, 0
  for _, p in ipairs(list) do
    local idx, h = basename(p):match("^(%d+)%-(%x+)$")
    if idx then
      local i = tonumber(idx)
      if chunks[i] ~= nil then return nil, "duplicate chunk " .. idx end
      chunks[i] = h
      count = count + 1
    end
  end
  if count == 0 then return nil, "empty request folder" end
  local parts, total = {}, 0
  for i = 0, count - 1 do
    if chunks[i] == nil then return nil, "missing chunk " .. i end
    parts[#parts + 1] = chunks[i]
    total = total + #chunks[i]
    if total > MAX_PAYLOAD * 2 then return nil, "payload over " .. MAX_PAYLOAD .. " bytes" end
  end
  local payload = unhex(table.concat(parts))
  if not payload then return nil, "payload is not valid hex" end
  return payload
end

-- payload is a Lua table literal (no `return`); evaluated with an empty environment.
local function parse_request(payload)
  local fn, lerr = loadstring("return " .. payload, "=request")
  if not fn then return nil, "request parse: " .. tos(lerr) end
  if type(setfenv) ~= "function" or not pcall(setfenv, fn, {}) then
    return nil, "request refused: this Lua host has no setfenv, so requests cannot be sandboxed"
  end
  local ok, req = pcall(fn)
  if not ok then return nil, "request eval: " .. tos(req) end
  if type(req) ~= "table" then return nil, "request is not a table" end
  return req
end

local OPS = { run = true, ping = true, stop = true }

local function validate_request(req, name)
  if req.v ~= 1 then return false, "unsupported protocol version " .. tos(req.v) end
  if req.id ~= name then return false, "request id does not match its folder" end
  if type(req.ts) ~= "number" then return false, "missing ts" end
  if math.abs(wall() - req.ts) > STALE_S then return false, "stale request (age " .. tos(wall() - req.ts) .. " s)" end
  if type(req.op) ~= "string" or not OPS[req.op] then return false, "unknown op " .. tos(req.op) end
  if req.op == "run" then
    if type(req.code) ~= "string" then return false, "run without code" end
    if #req.code > MAX_PAYLOAD then return false, "code over " .. MAX_PAYLOAD .. " bytes" end
    if req.args ~= nil and type(req.args) ~= "table" then return false, "args must be a table" end
  end
  return true
end

-- Running a chunk ----------------------------------------------------------------------------------

-- Names that must never fall through to Resolve's stale script-start globals.
local SHADOW = { project = true, timeline = true, mediaPool = true, projectManager = true, args = true }

local function make_env(st, cap, args)
  local env = { print = cap.print, resolve = st.resolve, fusion = st.fusion, fu = st.fusion, app = st.fusion,
                bmd = bmd, args = args or {} }
  pcall(function()
    local pm = st.resolve:GetProjectManager()
    env.projectManager = pm
    local project = pm and pm:GetCurrentProject() or nil
    env.project = project
    if project then
      env.timeline = project:GetCurrentTimeline()
      env.mediaPool = project:GetMediaPool()
    end
  end)
  return setmetatable(env, { __index = function(_, k) if SHADOW[k] then return nil end return _G[k] end })
end

local function run_chunk(st, code, args)
  local r = { ok = false, ms = 0 }
  local cap = new_capture()
  r.prints = cap.prints
  local fn, lerr = loadstring(code, "=claude")
  if not fn then
    r.error = "Lua syntax error: " .. tos(lerr)
    return r
  end
  if type(setfenv) ~= "function" or not pcall(setfenv, fn, make_env(st, cap, args)) then
    r.error = "run refused: this Lua host has no setfenv, so chunks cannot get their environment"
    return r
  end
  local handler = function(e) if type(e) == "table" then return e end return tos(e) end
  local orig_print = rawget(_G, "print")
  rawset(_G, "print", cap.print)
  local t0 = now()
  local res = pack(xpcall(function() return fn() end, handler))
  r.ms = ms_since(t0)
  rawset(_G, "print", orig_print)
  if cap.dropped > 0 then r.prints_dropped = cap.dropped end
  if res[1] then
    r.ok = true
    r.result = res[2]
  else
    r.error = type(res[2]) == "table" and res[2] or tos(res[2]):sub(1, ERROR_MAX)
  end
  return r
end

-- Dispatch -----------------------------------------------------------------------------------------

local function ping_result(st)
  local out = { ok = true, product = st.product, version = call_string(st.resolve, "GetVersionString"),
                pid = st.pid, session = st.session, uptime_s = wall() - st.started, bridge = VERSION,
                state_dir = st.state_dir, requests = st.requests, last_error = st.last_error }
  pcall(function()
    out.page = st.resolve:GetCurrentPage()
    local pm = st.resolve:GetProjectManager()
    local project = pm and pm:GetCurrentProject()
    out.project = project and project:GetName() or nil
    local tl = project and project:GetCurrentTimeline()
    out.timeline = tl and tl:GetName() or nil
  end)
  return out
end

local function respond(st, id, text)
  if not set_data(st, "CMCP_Resp", id .. ":" .. hex(text)) then
    local minimal = envelope_json({ id = id, session = st.session, ok = false, error = "SetData failed: " .. tos(st.last_error) }, "null")
    set_data(st, "CMCP_Resp", id .. ":" .. hex(minimal))
  end
  st.resp_written_at = now()
  st.resp_dirty = true
end

-- Returns "stopped" when the request asked us to exit, else nil.
local function handle(st, name, dir)
  local f = { id = name, session = st.session, op = "" }
  local payload, perr = read_payload(st, dir)
  local req, rerr
  if payload then req, rerr = parse_request(payload) end
  local text
  if not payload then
    f.ok, f.error = false, perr
    text = envelope_json(f, "null")
  elseif not req then
    f.ok, f.error = false, rerr
    text = envelope_json(f, "null")
  else
    f.op = type(req.op) == "string" and req.op or ""
    local ok, verr = validate_request(req, name)
    if not ok then
      f.ok, f.error = false, verr
      text = envelope_json(f, "null")
    elseif req.op == "ping" then
      f.ok = true
      text = fit_response(f, ping_result(st), req.max_kb)
    elseif req.op == "stop" then
      f.ok = true
      text = fit_response(f, { ok = true, session = st.session }, req.max_kb)
    else
      local r = run_chunk(st, req.code, req.args)
      f.ok, f.ms, f.error, f.prints, f.prints_dropped = r.ok, r.ms, r.error, r.prints, r.prints_dropped
      text = fit_response(f, r.result, req.max_kb)
    end
  end
  st.requests = (st.requests or 0) + 1
  respond(st, name, text)
  if req and req.op == "stop" and f.ok then return "stopped" end
  return nil
end

-- Start / loop ---------------------------------------------------------------------------------------

local function remember(st, name)
  if st.seen_n >= SEEN_MAX then st.seen = {}; st.seen_n = 0 end
  st.seen[name] = true
  st.seen_n = st.seen_n + 1
end

local function start()
  local st = { started = wall(), requests = 0, seen = {}, seen_n = 0 }
  local err
  st.fusion, err = acquire_fusion()
  if not st.fusion then return nil, err end
  st.resolve, err = acquire_resolve()
  if not st.resolve then
    set_data(st, "CMCP_Session", session_json(st, "error", { error = err }))
    return nil, err
  end
  st.state_dir, err = resolve_state_dir()
  if not st.state_dir then
    set_data(st, "CMCP_Session", session_json(st, "error", { error = err }))
    return nil, err
  end
  st.inbox = st.state_dir .. "/inbox"
  st.pending = st.inbox .. "/PENDING"
  st.pid = tonumber(bmd_call("getpid")) or -1
  local uuid = bmd_call("createuuid")
  if type(uuid) == "string" then uuid = uuid:gsub("[^%w]", ""):lower() end
  if type(uuid) ~= "string" or #uuid < ID_MIN then uuid = tos(st.started) .. tos(st.pid) end
  st.session = uuid:sub(1, ID_MAX)
  st.product = call_string(st.resolve, "GetProductName")
  st.version = call_string(st.resolve, "GetVersionString")
  local okms, ms = pcall(function() return st.resolve:GetMediaStorage() end)
  if not okms or not is_api_object(ms) then
    set_data(st, "CMCP_Session", session_json(st, "error", { error = "no MediaStorage object" }))
    return nil, "no MediaStorage object"
  end
  st.media_storage = ms
  -- Requests already in the inbox belong to a previous bridge (restart or take-over). They are
  -- remembered, never run: a write must not execute twice. The waiting server times out instead.
  pcall(function()
    for _, dir in ipairs(st.media_storage:GetSubFolderList(st.inbox) or {}) do
      local name = basename(dir)
      if valid_id(name) then remember(st, name) end
    end
  end)
  set_data(st, "CMCP_Stop", "")
  set_data(st, "CMCP_Owner", st.session)
  set_data(st, "CMCP_Resp", "")
  set_data(st, "CMCP_Beat", tos(wall()))
  set_data(st, "CMCP_Session", session_json(st, "running"))
  st.last_beat = now()
  return st
end

local function scan(st)
  local ok, list = pcall(function() return st.media_storage:GetSubFolderList(st.inbox) end)
  if not ok or type(list) ~= "table" then return nil end
  for _, dir in ipairs(list) do
    local name = basename(dir)
    if valid_id(name) and not st.seen[name] then
      remember(st, name)
      local okh, action = pcall(handle, st, name, dir)
      if not okh then
        st.last_error = "handle: " .. tos(action)
        pcall(respond, st, name, envelope_json({ id = name, session = st.session, ok = false, error = "bridge error: " .. tos(action) }, "null"))
      elseif action then
        return action
      end
    end
  end
  return nil
end

local function run_loop(st, max_ticks)
  local ticks = 0
  while true do
    if max_ticks and ticks >= max_ticks then return "ticks" end
    ticks = ticks + 1
    wait(TICK)
    if get_data(st, "CMCP_Stop") ~= "" then return "stop-flag" end
    if get_data(st, "CMCP_Owner") ~= st.session then return "takeover" end
    if file_exists(st.pending) and now() >= (st.next_scan_at or 0) then
      local before = st.requests
      local action = scan(st)
      if action then return action end
      -- A PENDING flag with nothing new behind it (a server died mid-request) must not turn the idle
      -- loop into a directory listing every tick: back off to 4 scans per second until it is removed.
      st.next_scan_at = (st.requests == before) and (now() + 0.25) or 0
    end
    if st.resp_dirty and st.resp_written_at and now() - st.resp_written_at > RESP_CLEAR_AFTER then
      st.resp_dirty = false
      set_data(st, "CMCP_Resp", "")
    end
    if now() - st.last_beat >= BEAT_EVERY then
      set_data(st, "CMCP_Beat", tos(wall()))
      st.last_beat = now()
    end
  end
end

local function finish(st, why)
  if why == "takeover" then return end           -- the new owner has the prefs now
  -- Clear a lingering data response on exit, but keep the acknowledgement of an op=stop request:
  -- the server is still waiting to read it.
  if st.resp_dirty and why ~= "stopped" then set_data(st, "CMCP_Resp", "") end
  set_data(st, "CMCP_Session", session_json(st, "stopped", { stopped = wall(), reason = why }))
  if why == "stop-flag" then set_data(st, "CMCP_Stop", "") end
end

local function main()
  local st, err = start()
  if not st then return nil, err end
  local why = run_loop(st, CFG.max_ticks)
  finish(st, why)
  return why
end

local M = {
  VERSION = VERSION, BRIDGE = BRIDGE, PREFIX = PREFIX, TICK = TICK, STALE_S = STALE_S,
  MAX_PAYLOAD = MAX_PAYLOAD, MAX_KB_DEFAULT = MAX_KB_DEFAULT, MAX_KB_CEILING = MAX_KB_CEILING,
  hex = hex, unhex = unhex, json = json, array_shape = array_shape, envelope_json = envelope_json,
  fit_response = fit_response, new_capture = new_capture, valid_id = valid_id, parse_request = parse_request,
  validate_request = validate_request, read_payload = read_payload, run_chunk = run_chunk, make_env = make_env,
  ping_result = ping_result, handle = handle, scan = scan, start = start, run_loop = run_loop, main = main,
}

if CFG.return_module then return M end
return main()
