"""The version is defined once in davinci_mcp/__init__.py; the bridge and changelog must match."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionTest(unittest.TestCase):
    def test_bridge_and_changelog_match_package_version(self):
        import sys
        sys.path.insert(0, ROOT)
        from davinci_mcp import __version__
        lua = open(os.path.join(ROOT, "bridge", "Claude_MCP_Bridge.lua"), encoding="utf-8").read()
        self.assertIn('local VERSION = "%s"' % __version__, lua)
        self.assertTrue(lua.startswith("-- Claude_MCP_Bridge.lua  v%s" % __version__), lua.splitlines()[0])
        stop = open(os.path.join(ROOT, "bridge", "Claude_MCP_Bridge_Stop.lua"), encoding="utf-8").read()
        self.assertTrue(stop.startswith("-- Claude_MCP_Bridge_Stop.lua  v%s" % __version__), stop.splitlines()[0])
        changelog = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
        first = re.search(r"^## ([0-9]+\.[0-9]+\.[0-9]+)", changelog, re.M)
        self.assertIsNotNone(first)
        self.assertEqual(first.group(1), __version__)


if __name__ == "__main__":
    unittest.main()
