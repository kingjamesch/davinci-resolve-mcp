"""Per-platform locations of DaVinci Resolve's folders and of this project's state directory.

macOS is the original target; Windows support follows the same layout under %APPDATA% /
%PROGRAMDATA% / %PROGRAMFILES%. Python 3.9 compatible, stdlib only.
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"


def _env_dir(name: str, fallback: str) -> str:
    value = os.environ.get(name)
    return value if value else os.path.expanduser(fallback)


if IS_WINDOWS:
    # %APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\{Profiles,Scripts}
    RESOLVE_SUPPORT = os.path.join(_env_dir("APPDATA", "~\\AppData\\Roaming"),
                                   "Blackmagic Design", "DaVinci Resolve", "Support")
    # On Windows the bridge can only list folders inside a Resolve Media Storage location, so the
    # state dir lives in its own folder that the user adds once under Preferences > Media Storage.
    STATE_DIR = os.path.join(_env_dir("LOCALAPPDATA", "~\\AppData\\Local"), "ClaudeMCP")
    DOCS_DIR = os.path.join(_env_dir("PROGRAMDATA", "C:\\ProgramData"),
                            "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting")
    FUSCRIPT = os.path.join(_env_dir("PROGRAMFILES", "C:\\Program Files"),
                            "Blackmagic Design", "DaVinci Resolve", "fuscript.exe")
    RESOLVE_CONFIG = os.path.join(_env_dir("APPDATA", "~\\AppData\\Roaming"),
                                  "Blackmagic Design", "DaVinci Resolve", "Preferences", "config.dat")
    CLAUDE_DESKTOP_CONFIG = os.path.join(_env_dir("APPDATA", "~\\AppData\\Roaming"),
                                         "Claude", "claude_desktop_config.json")
    PYTHON = sys.executable
else:
    RESOLVE_SUPPORT = os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve")
    STATE_DIR = os.path.join(RESOLVE_SUPPORT, "ClaudeMCP")
    DOCS_DIR = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"
    RESOLVE_CONFIG = None
    CLAUDE_DESKTOP_CONFIG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    PYTHON = "/usr/bin/python3"

PROFILES_DIR = os.path.join(RESOLVE_SUPPORT, "Fusion", "Profiles")
SCRIPTS_UTILITY = os.path.join(RESOLVE_SUPPORT, "Fusion", "Scripts", "Utility")


def media_storage_roots(config_path: str = None):
    """Media Storage locations from Resolve's config.dat (``Site.N.FS.M.Root = <path>``), or None
    when the file is unavailable. Only meaningful on Windows."""
    path = config_path or RESOLVE_CONFIG
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    roots = []
    for line in lines:
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key.startswith("Site.") and ".FS." in key and key.endswith(".Root"):
            roots.append(value.strip())
    return roots


def is_inside_media_storage(state_dir: str, roots) -> bool:
    """True when ``state_dir`` equals or sits under one of ``roots`` (case-insensitive on Windows)."""
    target = os.path.normcase(os.path.abspath(state_dir))
    for root in roots or ():
        if not root or not os.path.isabs(root):
            continue
        base = os.path.normcase(os.path.abspath(root))
        if target == base or target.startswith(base.rstrip("\\/") + os.sep):
            return True
    return False
