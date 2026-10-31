"""Search over Blackmagic's shipped scripting reference (.pyi stub + README.md)."""

import os
import re
from typing import Dict, List, Optional

from .tools import ToolError

DOCS_DIR = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
PYI_NAME = "DaVinciResolveScript.pyi"
README_NAME = "README.md"
DOC_MAX = 600

_CACHE = {}  # type: Dict[str, List[dict]]

_CLASS_RE = re.compile(r"^class\s+(\w+)")
_DEF_RE = re.compile(r"^\s+def\s+(\w+)\s*\((.*)\)\s*(?:->\s*(.*?))?\s*:\s*$")
_FIELD_RE = re.compile(r"^\t(\w+)\s*:\s*(.+?)\s*$")
_HEADER_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > DOC_MAX:
        text = text[:DOC_MAX - 3].rstrip() + "..."
    return text


def _entry(kind, cls, name, signature, doc, source):
    return {"kind": kind, "class": cls, "name": name, "signature": signature,
            "doc": _clip(doc), "source": source}


def _parse_pyi(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out = []  # type: List[dict]
    cur = ""
    pending = None  # entry awaiting a docstring
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            body = stripped[3:]
            parts = []
            if quote in body:
                parts.append(body.split(quote, 1)[0])
            else:
                parts.append(body)
                i += 1
                while i < n:
                    if quote in lines[i]:
                        parts.append(lines[i].split(quote, 1)[0])
                        break
                    parts.append(lines[i])
                    i += 1
            if pending is not None:
                pending["doc"] = _clip(" ".join(parts))
                pending = None
            i += 1
            continue
        m = _CLASS_RE.match(line)
        if m:
            cur = m.group(1)
            pending = _entry("class", "", cur, "class " + cur, "", "pyi")
            out.append(pending)
            i += 1
            continue
        if line and not line[0].isspace():
            pending = None
            if stripped and not stripped.startswith("#"):
                pass
            i += 1
            continue
        m = _DEF_RE.match(line)
        if m and cur:
            args = [a.strip() for a in m.group(2).split(",")]
            args = [a for a in args if a and a != "self"]
            ret = m.group(3) or ""
            sig = "%s.%s(%s)" % (cur, m.group(1), ", ".join(args))
            if ret:
                sig += " -> " + ret
            pending = _entry("method", cur, m.group(1), sig, "", "pyi")
            out.append(pending)
            i += 1
            continue
        m = _FIELD_RE.match(line)
        if m and cur:
            pending = _entry("field", cur, m.group(1), "%s.%s: %s" % (cur, m.group(1), m.group(2)), "", "pyi")
            out.append(pending)
        i += 1
    return out


def _parse_readme(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out = []  # type: List[dict]
    title = None
    level = ""
    body = []  # type: List[str]

    def flush():
        if title is not None:
            out.append(_entry("section", "", title, "%s %s" % (level, title), " ".join(body), "readme"))

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            flush()
            level, title, body = m.group(1), m.group(2), []
        elif title is not None:
            body.append(line)
    flush()
    return out


def build_index(docs_dir: str = DOCS_DIR) -> List[dict]:
    pyi = os.path.join(docs_dir, PYI_NAME)
    readme = os.path.join(docs_dir, README_NAME)
    have_pyi, have_readme = os.path.isfile(pyi), os.path.isfile(readme)
    if not (have_pyi or have_readme):
        raise ToolError("Resolve's scripting reference was not found at %s." % docs_dir)
    entries = []  # type: List[dict]
    if have_pyi:
        entries.extend(_parse_pyi(pyi))
    if have_readme:
        entries.extend(_parse_readme(readme))
    return entries


def _index(docs_dir: str) -> List[dict]:
    if docs_dir not in _CACHE:
        _CACHE[docs_dir] = build_index(docs_dir)
    return _CACHE[docs_dir]


def search(query: str, limit: int = 10, class_filter: Optional[str] = None, docs_dir: str = DOCS_DIR) -> dict:
    entries = _index(docs_dir)
    tokens = (query or "").lower().split()
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(30, limit))
    cf = class_filter.strip().lower() if class_filter else None
    scored = []
    for pos, e in enumerate(entries):
        if cf and e["class"].lower() != cf:
            continue
        name = e["name"].lower()
        sig = e["signature"].lower()
        cls = e["class"].lower()
        doc = e["doc"].lower()
        if not tokens:
            continue
        if not all(t in name or t in sig or t in cls or t in doc for t in tokens):
            continue
        score = 0
        for t in tokens:
            if t in name:
                score += 10
            if t in sig or t in cls:
                score += 5
            if t in doc:
                score += 1
        if name == (query or "").strip().lower():
            score += 20
        scored.append((-score, 0 if e["kind"] == "method" else 1, pos, e))
    scored.sort(key=lambda s: s[:3])
    matches = [dict(s[3]) for s in scored[:limit]]
    return {"matches": matches, "total": len(scored), "truncated": len(scored) > limit}
