"""The Python half of the bridge protocol (docs/PROTOCOL.md).

Requests become a folder of hex-named subfolders under ``STATE_DIR/inbox/<id>/`` plus a
``PENDING`` flag; responses are read back from ``Fusion.prefs`` where the Lua bridge persisted
them with ``fu:SetData``. One request in flight at a time. Python 3.9 compatible, stdlib only.
"""

import json
import os
import random
import shutil
import string
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import luafmt, paths, prefs

if paths.IS_WINDOWS:
    import ctypes
    import msvcrt
else:
    import fcntl

STATE_DIR_DEFAULT = os.environ.get("DAVINCI_MCP_STATE_DIR") or paths.STATE_DIR
PROFILES_DIR_DEFAULT = os.environ.get("DAVINCI_MCP_PROFILES_DIR") or None   # None -> prefs.PROFILES_DIR
CHUNK_BYTES = 120            # 240 hex chars + "NNNN-" prefix stays well under the 255-byte name limit
WINDOWS_MAX_PATH = 247       # CreateDirectory allows MAX_PATH - 12 = 248 characters; keep one spare
MIN_CHUNK_BYTES = 16
POLL_S = 0.015
PING_TIMEOUT_S = 3.0
BEAT_STALE_S = 45.0
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 600.0
STALE_REQUEST_S = 300.0      # startup cleanup removes only request folders older than this
LOCK_WAIT_S = 660.0          # another server may hold the lock for a whole long request
RESOLVE_NOT_RUNNING = "DaVinci Resolve is not running."
BRIDGE_NOT_RUNNING = ("The Resolve bridge is not running. In DaVinci Resolve click Workspace > "
                      "Scripts > Claude_MCP_Bridge (Resolve must be open with a project), then retry.")


class BridgeError(Exception):
    """Base: message is already a user-facing sentence."""


class BridgeNotRunning(BridgeError):
    pass


class BridgeTimeout(BridgeError):
    pass


class ProtocolError(BridgeError):
    pass


class LuaError(BridgeError):
    """The chunk raised. ``prints`` carries captured print output."""

    def __init__(self, message: str, prints=None):
        super().__init__(message)
        self.prints = prints or []


def _default_log(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


class BridgeClient:
    def __init__(self, state_dir: str = None, profiles_dir: str = None,
                 log: Callable[[str], None] = None, poll_s: float = POLL_S):
        self.state_dir = state_dir or STATE_DIR_DEFAULT
        self.inbox = os.path.join(self.state_dir, "inbox")
        self.pending = os.path.join(self.inbox, "PENDING")
        self.lock_path = os.path.join(self.state_dir, "lock")
        self.profiles_dir = profiles_dir or PROFILES_DIR_DEFAULT
        self.log = log or _default_log
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._rand = random.SystemRandom()

    # -- setup -------------------------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        os.makedirs(self.inbox, mode=0o700, exist_ok=True)
        for d in (self.state_dir, self.inbox):
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass

    def startup_cleanup(self) -> int:
        """Remove request folders left by a crashed server. Several Claude Code sessions run their own
        server against the same bridge, so only folders older than STALE_REQUEST_S are removed, under
        the cross-process lock, and PENDING is removed only when no request folder remains."""
        self.ensure_dirs()
        removed = 0
        now = time.time()
        with self._process_lock(self):
            remaining = 0
            for name in os.listdir(self.inbox):
                path = os.path.join(self.inbox, name)
                if not os.path.isdir(path):
                    continue
                try:
                    age = now - os.stat(path).st_mtime
                except OSError:
                    continue
                if age > STALE_REQUEST_S:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
                else:
                    remaining += 1
            if remaining == 0:
                try:
                    os.remove(self.pending)
                except OSError:
                    pass
        return removed

    class _process_lock(object):
        """Exclusive lock on STATE_DIR/lock (flock, or msvcrt.locking on Windows): one request in
        flight across every server process on this machine."""

        def __init__(self, client):
            self.client = client
            self.fh = None

        def __enter__(self):
            self.fh = open(self.client.lock_path, "a")
            deadline = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    _lock_nb(self.fh)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fh.close()
                        self.fh = None
                        raise BridgeTimeout("Another Claude session is holding the Resolve bridge; try again shortly.")
                    time.sleep(0.02)

        def __exit__(self, *exc):
            if self.fh is not None:
                try:
                    _unlock(self.fh)
                finally:
                    self.fh.close()
                    self.fh = None
            return False

    # -- prefs side ---------------------------------------------------------------------------------

    def prefs_path(self) -> Optional[str]:
        return prefs.find_prefs_file(self.profiles_dir)

    def read_keys(self) -> Dict[str, str]:
        path = self.prefs_path()
        if not path:
            return {}
        text = prefs.read_text(path)
        return prefs.extract_keys(text) if text else {}

    def session(self) -> Optional[dict]:
        """The bridge's CMCP_Session record, decoded, or None."""
        keys = self.read_keys()
        raw = keys.get("CMCP_Session", "")
        if not raw:
            return None
        try:
            return prefs.decode_hex_json(raw)
        except ValueError:
            return None

    def liveness(self) -> Dict[str, Any]:
        """A cheap, no-request view: {running: bool, reason: str, session: dict|None, beat_age_s}."""
        keys = self.read_keys()
        sess = None
        raw = keys.get("CMCP_Session", "")
        if raw:
            try:
                sess = prefs.decode_hex_json(raw)
            except ValueError:
                sess = None
        beat_age = None
        try:
            beat = float(keys.get("CMCP_Beat", "") or 0)
            if beat:
                beat_age = max(0.0, time.time() - beat)
        except ValueError:
            pass
        if sess is None:
            return {"running": False, "reason": "no bridge session recorded", "session": None, "beat_age_s": beat_age}
        if sess.get("state") != "running":
            return {"running": False, "reason": "bridge session is %s" % sess.get("state"), "session": sess, "beat_age_s": beat_age}
        pid = sess.get("pid")
        if isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
            return {"running": False, "reason": "the Resolve process that ran the bridge has exited", "session": sess, "beat_age_s": beat_age}
        if beat_age is not None and beat_age > BEAT_STALE_S:
            return {"running": False, "reason": "bridge heartbeat is %d s old" % int(beat_age), "session": sess, "beat_age_s": beat_age}
        return {"running": True, "reason": "", "session": sess, "beat_age_s": beat_age}

    # -- requests ----------------------------------------------------------------------------------

    def _new_id(self) -> str:
        t = _base36(int(time.time() * 1000))[-7:]
        r = "".join(self._rand.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        return (t + r).lower()

    def _write_request(self, req_id: str, payload: bytes) -> str:
        self.ensure_dirs()
        final = os.path.join(self.inbox, req_id)
        tmp = final + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        os.mkdir(tmp, 0o700)
        size = chunk_bytes_for(tmp)
        for i in range(0, len(payload), size):
            chunk = payload[i:i + size]
            os.mkdir(os.path.join(tmp, "%04d-%s" % (i // size, chunk.hex())), 0o700)
        os.rename(tmp, final)
        with open(self.pending, "a"):
            pass
        return final

    def _cleanup(self, req_id: str) -> None:
        shutil.rmtree(os.path.join(self.inbox, req_id), ignore_errors=True)
        shutil.rmtree(os.path.join(self.inbox, req_id + ".tmp"), ignore_errors=True)
        try:
            os.remove(self.pending)
        except OSError:
            pass

    def _wait_response(self, req_id: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        path = self.prefs_path()
        if not path:
            raise BridgeNotRunning(BRIDGE_NOT_RUNNING)
        last_sig = None
        while True:
            sig = prefs.file_signature(path)
            if sig is None:
                path = self.prefs_path() or path
                sig = prefs.file_signature(path)
            if sig is not None and sig != last_sig:
                last_sig = sig
                text = prefs.read_text(path)
                if text:
                    keys = prefs.extract_keys(text)
                    rid, hexbody = prefs.split_resp(keys.get("CMCP_Resp", ""))
                    if rid == req_id and hexbody:
                        env = prefs.decode_hex_json(hexbody)
                        if env.get("v") != 1 or env.get("id") != req_id:
                            raise ProtocolError("The bridge answered with a malformed envelope.")
                        return env
            if time.monotonic() >= deadline:
                raise BridgeTimeout(
                    "The bridge did not answer within %d s; Resolve may be showing a dialog or running a "
                    "long operation. The outcome of this call is unknown: check the Resolve window and "
                    "read the current state before retrying a write." % int(round(timeout)))
            time.sleep(self.poll_s)

    def call(self, op: str, code: str = None, args: Any = None, timeout: float = DEFAULT_TIMEOUT_S,
             max_kb: int = 64, precheck: bool = True) -> dict:
        """Send one request and return the decoded envelope. Raises BridgeError subclasses."""
        timeout = max(1.0, min(float(timeout), MAX_TIMEOUT_S))
        with self._lock, self._process_lock(self):
            if precheck:
                live = self.liveness()
                if not live["running"] and live["reason"] != "no bridge session recorded" and \
                        live["reason"] != "bridge heartbeat is %d s old" % int(live["beat_age_s"] or 0):
                    raise BridgeNotRunning(BRIDGE_NOT_RUNNING)
                if live["session"] is None:
                    raise BridgeNotRunning(BRIDGE_NOT_RUNNING)
            req_id = self._new_id()
            payload = luafmt.build_request(req_id, op, int(time.time()), code=code, args=args,
                                           timeout=timeout, max_kb=max_kb)
            started = time.monotonic()
            try:
                self._write_request(req_id, payload)
                env = self._wait_response(req_id, timeout)
            finally:
                self._cleanup(req_id)
            ms = int((time.monotonic() - started) * 1000)
            self.log("bridge %s %s %s %dms" % (op, req_id, "ok" if env.get("ok") else "error", ms))
            return env

    def run(self, code: str, args: Any = None, timeout: float = DEFAULT_TIMEOUT_S, max_kb: int = 64) -> dict:
        """Run a chunk; returns the full envelope (result, prints, truncated...). Raises LuaError."""
        env = self.call("run", code=code, args=args, timeout=timeout, max_kb=max_kb)
        if not env.get("ok"):
            err = env.get("error")
            if isinstance(err, (dict, list)):
                err = json.dumps(err, ensure_ascii=False)
            raise LuaError("Lua error: %s" % (str(err or "unknown error").splitlines() or ["unknown error"])[0][:2000],
                           prints=env.get("prints") or [])
        return env

    def ping(self, timeout: float = PING_TIMEOUT_S) -> dict:
        env = self.call("ping", timeout=timeout, precheck=False)
        if not env.get("ok"):
            raise ProtocolError("The bridge refused the ping: %s" % env.get("error"))
        return env.get("result") or {}

    def stop(self, timeout: float = PING_TIMEOUT_S) -> bool:
        try:
            env = self.call("stop", timeout=timeout, precheck=False)
            return bool(env.get("ok"))
        except BridgeError:
            return False


def chunk_bytes_for(parent: str, windows: bool = None) -> int:
    """Payload bytes per chunk folder under ``parent``. On Windows the whole path must stay within
    MAX_PATH, so the chunk shrinks to fit what the parent path leaves (index up to 5 digits + "-")."""
    if not (paths.IS_WINDOWS if windows is None else windows):
        return CHUNK_BYTES
    room = WINDOWS_MAX_PATH - len(os.path.abspath(parent)) - 1 - len("NNNNN-")
    if room < 2 * MIN_CHUNK_BYTES:
        raise BridgeError("The state folder path is too long for Windows (%d characters); set "
                          "DAVINCI_MCP_STATE_DIR to a shorter folder." % len(os.path.abspath(parent)))
    return min(CHUNK_BYTES, room // 2)


def _lock_nb(fh) -> None:
    """Take an exclusive, non-blocking lock on ``fh``; raise OSError when another process holds it."""
    if paths.IS_WINDOWS:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)   # locks byte 0, even past EOF
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh) -> None:
    if paths.IS_WINDOWS:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid: int) -> bool:
    if paths.IS_WINDOWS:
        # Never os.kill(pid, 0) here: on Windows that calls TerminateProcess and would kill Resolve.
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED   # exists but belongs to someone else
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _base36(n: int) -> str:
    digits = string.digits + string.ascii_lowercase
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(digits[r])
    return "".join(reversed(out))
