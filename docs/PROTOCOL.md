# Bridge protocol v1 — Python server ⇄ Lua script inside DaVinci Resolve

Both sides derive every path from `HOME`:

```
STATE_DIR = ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/ClaudeMCP
INBOX     = STATE_DIR/inbox
PENDING   = INBOX/PENDING                      (zero-byte flag file)
PREFS     = ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Profiles/*/Fusion.prefs  (newest)
```

The Lua side can only (a) test file existence (`bmd.fileexists`), (b) list subfolder names
(`resolve:GetMediaStorage():GetSubFolderList(dir)` → full paths) and (c) persist strings with
`fu:SetData(key, value)`, which rewrites `Fusion.prefs` synchronously. Everything below follows
from that.

## Request (server → bridge): a folder of folders

1. `id` = 6–32 chars of `[a-z0-9]`, unique per request (`<base36 time><random>`).
2. `payload` = UTF-8 bytes of a **Lua table literal without `return`**, e.g.
   `{ v = 1, id = "k3f9x2a1", ts = 1793437964, op = "run", timeout = 30, max_kb = 64,
      code = [==[ return resolve:GetVersionString() ]==], args = { name = "Timeline 1" } }`
   Strings use long brackets `[=*[ ... ]=*]` with a level that does not occur in the content;
   numbers are plain; booleans `true/false`; JSON arrays become `{a, b}`; JSON objects become
   `{ ["key"] = value }`; `null` becomes `nil` (omitted).
3. Server creates `INBOX/<id>.tmp/`, then one subfolder per 120-byte slice of `payload`, named
   `NNNN-<hex>` (`NNNN` zero-padded index from `0000`, lowercase hex of the slice), then renames
   `INBOX/<id>.tmp` → `INBOX/<id>` (atomic), then creates `PENDING`.
4. One request in flight at a time across every server process on the machine: each server takes an
   `flock` on `STATE_DIR/lock` for the whole request (several Claude Code sessions share one bridge).
   Max payload 256 KB (2200 folders).

Ops: `ping` (no code) · `run` (`code` required; `args` optional table) · `stop`.

## Bridge loop (inside Resolve, script thread)

At start the bridge records every request folder already in the inbox as seen and never runs it: a
request posted to a previous bridge instance (restart or take-over) times out on the server side
instead of executing twice. The bridge refuses to parse or run anything if `setfenv` is unavailable.

```
every TICK (30 ms):
  if GetData("ClaudeMCP.CMCP_Stop") ~= "" or GetData("ClaudeMCP.CMCP_Owner") ~= session → exit
  if bmd.fileexists(PENDING):
    for dir in GetSubFolderList(INBOX): name = basename(dir)
      skip if name ends ".tmp" or not id-shaped or already seen
      chunks = GetSubFolderList(dir) → parse "NNNN-hex", sort, require contiguous 0..n-1
      payload = hexdecode(concat) ; req = loadstring("return " .. payload) with empty env
      validate v==1, id==name, |os.time()-ts| ≤ 120, op known, code string ≤ 256 KB
      execute (see below) ; respond ; seen[name]=true
  every 15 s: SetData("ClaudeMCP.CMCP_Beat", os.time()); 10 s after a response: SetData("ClaudeMCP.CMCP_Resp", "")
  if PENDING exists but nothing new is behind it: scan only 4x per second (a server died mid-request)
```

Execution of `run`: `fn = loadstring(code, "=claude")`, `setfenv(fn, env)` where `env` has
`print` (captured), `resolve`, `fusion`, `fu`, `app`, `bmd`, `args`, plus fresh helpers
`project` (current project or nil), `timeline` (current timeline or nil), `mediaPool`,
`projectManager`, and `__index = _G`. `xpcall(fn)`; first return value is the result.

## Response (bridge → server): one SetData

`fu:SetData("ClaudeMCP.CMCP_Resp", id .. ":" .. hex(json))` where `json` is

```
{"v":1,"id":"…","session":"…","op":"run","ok":true,"ms":12,
 "result":<json>,"error":null,"prints":["…"],"truncated":false,"result_bytes":123,"bridge":"0.1.0"}
```

JSON encoding of Lua values: `nil`→`null`; tables with keys exactly `1..n` → arrays; other tables →
objects with string keys (numeric keys stringified); userdata → `"<object …>"`; cycles → `"<cycle>"`;
depth > 16 → `"<depth>"`. Result capped at `max_kb` KB (default 64, ceiling 256): over the cap the
result becomes a string preview and `truncated=true`, `result_bytes` = full size.

Other keys (same `ClaudeMCP.*` namespace in `Fusion.prefs` under `Global.Script.GlobalData`):

| key | writer | value |
|---|---|---|
| `CMCP_Session` | bridge at start/stop | hex(json `{v,session,pid,started,state:"running"|"stopped",product,version,bridge}`) |
| `CMCP_Owner` | bridge at start | session id — a second start takes over; the old loop exits |
| `CMCP_Beat` | bridge every 15 s idle | unix seconds |
| `CMCP_Stop` | stop script | `"1"`; bridge clears it on exit |

## Server wait

After creating `PENDING`, the server polls `PREFS` every 15 ms (stat first; parse on change) for
`CMCP_Resp = "<id>:<hex>"` whose id matches, decodes hex → JSON, then deletes `INBOX/<id>` and
`PENDING`. Timeout (default 30 s, per-call) → error; the folder is still deleted. On startup the
server removes request folders older than 5 minutes (under the lock) and PENDING only when none remain.
On exit the bridge clears `CMCP_Resp`, except the acknowledgement of an `op=stop` request. Liveness: `CMCP_Session.state == "running"` and (`CMCP_Beat` within 45 s
or a 3 s `ping` succeeds); the bridge's `pid` must be a live process.

## Why these constants

`TICK` 30 ms + prefs flush ≈ 0.3 s observed → typical round trip 0.1–0.4 s. Stale window 120 s
guards against replaying a folder left by a crashed server. `PENDING` keeps the idle loop to one
`stat` per tick instead of a directory listing.
