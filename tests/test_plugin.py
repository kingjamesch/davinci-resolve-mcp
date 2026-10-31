"""The Claude Code plugin manifests must stay consistent with the package and the files they reference."""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from davinci_mcp import __version__  # noqa: E402


def _frontmatter(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "%s has no frontmatter" % path
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


class PluginManifestTest(unittest.TestCase):
    def test_plugin_json(self):
        p = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8"))
        self.assertEqual(p["name"], "davinci-resolve")
        self.assertTrue(re.match(r"^[a-z][a-z0-9-]*$", p["name"]))
        self.assertEqual(p["version"], __version__)
        self.assertEqual(p["license"], "MIT")
        server = p["mcpServers"]["davinci"]
        self.assertEqual(server["command"], "/usr/bin/python3")
        self.assertEqual(server["args"], ["${CLAUDE_PLUGIN_ROOT}/server.py"])
        self.assertTrue(os.path.exists(os.path.join(ROOT, "server.py")))

    def test_marketplace_json(self):
        m = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8"))
        self.assertEqual(m["name"], "davinci-resolve-mcp")
        self.assertIn("name", m["owner"])
        entry = m["plugins"][0]
        self.assertEqual(entry["name"], "davinci-resolve")
        self.assertEqual(entry["source"], "./")
        self.assertEqual(entry["version"], __version__)

    def test_registry_server_json(self):
        r = json.load(open(os.path.join(ROOT, "server.json"), encoding="utf-8"))
        self.assertEqual(r["name"], "io.github.kingjamesch/davinci-resolve-mcp")
        self.assertEqual(r["version"], __version__)
        self.assertEqual(r["repository"]["url"], "https://github.com/kingjamesch/davinci-resolve-mcp")
        self.assertLessEqual(len(r["description"]), 100)  # registry limit
        self.assertTrue(r["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/"))

    def test_skill_and_command(self):
        skill = _frontmatter(os.path.join(ROOT, "skills", "resolve", "SKILL.md"))
        self.assertEqual(skill["name"], "resolve")
        self.assertTrue(len(skill["description"]) > 40)
        cmd = _frontmatter(os.path.join(ROOT, "commands", "setup.md"))
        self.assertTrue(cmd["description"])
        body = open(os.path.join(ROOT, "commands", "setup.md"), encoding="utf-8").read()
        self.assertIn("install.py", body)
        self.assertIn("--no-claude", body)


if __name__ == "__main__":
    unittest.main()
