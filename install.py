"""Installer / uninstaller for the DaVinci Resolve MCP bridge (stdlib only)."""
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys

TOKEN = "@@STATE_DIR@@"
BRIDGE = "Claude_MCP_Bridge.lua"
STOP = "Claude_MCP_Bridge_Stop.lua"
PYTHON = "/usr/bin/python3"
SERVER_NAME = "davinci"

_SUPPORT = os.path.expanduser("~/Library/Application Support")
_RESOLVE = os.path.join(_SUPPORT, "Blackmagic Design", "DaVinci Resolve")
SCRIPTS_UTILITY = os.path.join(_RESOLVE, "Fusion", "Scripts", "Utility")
STATE_DIR = os.path.join(_RESOLVE, "ClaudeMCP")
DESKTOP_CONFIG = os.path.join(_SUPPORT, "Claude", "claude_desktop_config.json")

NEXT_STEPS = (
    "NEXT STEPS\n"
    "1) Quit and reopen DaVinci Resolve once (it scans the Scripts menu only at startup).\n"
    "2) In Resolve: Workspace > Scripts > Claude_MCP_Bridge (do this once per Resolve session; "
    "Claude_MCP_Bridge_Stop stops it).\n"
    "3) In Claude Code run /mcp and check that 'davinci' is connected, then ask for resolve_status."
)


def _server_path(repo_dir):
    return os.path.join(os.path.abspath(repo_dir), "server.py")


def _add_command(repo_dir, scope="user"):
    return ["claude", "mcp", "add", "--scope", scope, SERVER_NAME, "--", PYTHON, _server_path(repo_dir)]


def install(repo_dir, scripts_dir, state_dir):
    actions = []
    state_dir = os.path.abspath(state_dir).rstrip("/") or "/"
    os.makedirs(scripts_dir, exist_ok=True)
    actions.append("ensured " + scripts_dir)
    inbox = os.path.join(state_dir, "inbox")
    os.makedirs(inbox, exist_ok=True)
    os.chmod(state_dir, 0o700)
    os.chmod(inbox, 0o700)
    actions.append("ensured %s (0700) and %s (0700)" % (state_dir, inbox))

    with open(os.path.join(repo_dir, "bridge", BRIDGE), "r", encoding="utf-8") as fh:
        src = fh.read()
    if src.count(TOKEN) != 1:
        raise RuntimeError("bridge source must contain %s exactly once" % TOKEN)
    dest = os.path.join(scripts_dir, BRIDGE)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(src.replace(TOKEN, state_dir))
    actions.append("wrote " + dest)

    stop_dest = os.path.join(scripts_dir, STOP)
    shutil.copyfile(os.path.join(repo_dir, "bridge", STOP), stop_dest)
    actions.append("wrote " + stop_dest)

    with open(dest, "r", encoding="utf-8") as fh:
        written = fh.read()
    if TOKEN in written or state_dir not in written:
        raise RuntimeError("verification failed: stamp missing or token left in " + dest)
    actions.append("verified stamp in " + dest)
    return actions


def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, timeout=60)


def register_claude_code(repo_dir, scope="user"):
    cmd = _add_command(repo_dir, scope)
    if shutil.which("claude") is None:
        return (False, "claude CLI not found on PATH. Run this yourself: " + " ".join(cmd))
    try:
        _run(["claude", "mcp", "remove", "--scope", scope, SERVER_NAME])
    except Exception:
        pass
    try:
        res = _run(cmd)
    except Exception as exc:
        return (False, "claude mcp add failed: %s" % exc)
    out = (res.stdout or "").strip()
    if res.returncode != 0:
        return (False, "claude mcp add failed (exit %d): %s" % (res.returncode, out))
    return (True, out or "registered 'davinci' with Claude Code")


def configure_claude_desktop(config_path, repo_dir):
    msgs = []
    cfg = {}
    if os.path.exists(config_path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = "%s.bak-%s" % (config_path, stamp)
        shutil.copyfile(config_path, backup)
        msgs.append("backup " + backup)
        with open(config_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if text.strip():
            cfg = json.loads(text)
        if not isinstance(cfg, dict):
            raise ValueError("config root is not a JSON object")
    else:
        parent = os.path.dirname(config_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        cfg["mcpServers"] = servers
    servers[SERVER_NAME] = {"command": PYTHON, "args": [_server_path(repo_dir)]}
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    msgs.append("set mcpServers.davinci in " + config_path)
    return "; ".join(msgs)


def check(repo_dir, scripts_dir, state_dir):
    state_dir = os.path.abspath(state_dir).rstrip("/") or "/"
    report = {}
    bridge = os.path.join(scripts_dir, BRIDGE)
    report["bridge_installed"] = os.path.isfile(bridge)
    stamp_ok = False
    if report["bridge_installed"]:
        with open(bridge, "r", encoding="utf-8") as fh:
            body = fh.read()
        stamp_ok = state_dir in body and TOKEN not in body
    report["stamp_matches"] = stamp_ok
    report["stop_script_present"] = os.path.isfile(os.path.join(scripts_dir, STOP))
    if os.path.isdir(state_dir):
        report["state_dir_mode"] = oct(stat.S_IMODE(os.stat(state_dir).st_mode))
    else:
        report["state_dir_mode"] = None
    report["inbox_present"] = os.path.isdir(os.path.join(state_dir, "inbox"))
    report["server_py_present"] = os.path.isfile(_server_path(repo_dir))
    if shutil.which("claude") is None:
        report["claude_registered"] = None
    else:
        try:
            res = _run(["claude", "mcp", "list"])
            report["claude_registered"] = SERVER_NAME in (res.stdout or "")
        except Exception:
            report["claude_registered"] = None
    return report


def uninstall(scripts_dir, state_dir, purge=False):
    actions = []
    for name in (BRIDGE, STOP):
        path = os.path.join(scripts_dir, name)
        if os.path.isfile(path):
            os.remove(path)
            actions.append("removed " + path)
    if purge and os.path.isdir(state_dir):
        shutil.rmtree(state_dir)
        actions.append("removed " + state_dir)
    if shutil.which("claude") is not None:
        try:
            res = _run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME])
            actions.append("claude mcp remove: " + (res.stdout or "").strip())
        except Exception as exc:
            actions.append("claude mcp remove failed: %s" % exc)
    return actions


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    known = {"--no-claude", "--claude-desktop", "--check", "--uninstall", "--purge"}
    bad = [a for a in args if a not in known]
    if bad:
        print("unknown arguments: %s\nusage: install.py [--no-claude] [--claude-desktop] "
              "[--check] [--uninstall [--purge]]" % " ".join(bad), file=sys.stderr)
        return 2
    repo = os.path.dirname(os.path.abspath(__file__))
    if "--check" in args:
        rep = check(repo, SCRIPTS_UTILITY, STATE_DIR)
        for key in sorted(rep):
            print("%-22s %s" % (key, rep[key]))
        return 0
    if "--uninstall" in args:
        for line in uninstall(SCRIPTS_UTILITY, STATE_DIR, purge="--purge" in args):
            print(line)
        return 0
    for line in install(repo, SCRIPTS_UTILITY, STATE_DIR):
        print(line)
    if "--claude-desktop" in args:
        print(configure_claude_desktop(DESKTOP_CONFIG, repo))
    if "--no-claude" not in args:
        ok, msg = register_claude_code(repo)
        print(("OK: " if ok else "NOT REGISTERED: ") + msg)
    print()
    print(NEXT_STEPS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
