import os
import subprocess
import tempfile
import unittest

from davinci_mcp.tools import TOOLS, ToolError, validate_args
from davinci_mcp.tools import status  # noqa: F401  (registers the tools)

FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"

EXPECTED = {
    "resolve_status": True, "open_page": False, "list_projects": True, "open_project": False,
    "save_project": False, "get_project_info": True, "get_project_setting": True, "set_project_setting": False,
}
LUA_TOOLS = sorted(n for n in EXPECTED if n != "resolve_status")

GOOD = {
    "resolve_status": {}, "open_page": {"page": "edit"}, "list_projects": {}, "open_project": {"name": "X"},
    "save_project": {}, "get_project_info": {}, "get_project_setting": {"key": "timelineFrameRate"},
    "set_project_setting": {"key": "timelineFrameRate", "value": 24},
}
BAD = {
    "resolve_status": {"x": 1}, "open_page": {"page": "nope"}, "list_projects": {"x": 1},
    "open_project": {"name": "a" * 201}, "save_project": {"x": 1}, "get_project_info": {"x": 1},
    "get_project_setting": {"key": "a" * 101}, "set_project_setting": {"key": "k", "value": [1]},
}
MISSING = {"open_page": {}, "open_project": {}, "set_project_setting": {"key": "k"}}


class StatusToolsTest(unittest.TestCase):
    def test_registration(self):
        for name, read_only in EXPECTED.items():
            self.assertIn(name, TOOLS)
            ann = TOOLS[name].annotations
            self.assertEqual(ann["readOnlyHint"], read_only, name)
            self.assertFalse(ann["destructiveHint"], name)

    def test_schemas(self):
        for name in EXPECTED:
            s = TOOLS[name].input_schema
            self.assertEqual(s["type"], "object")
            self.assertIs(s["additionalProperties"], False, name)
            for prop, spec in s["properties"].items():
                self.assertTrue(spec.get("description"), "%s.%s" % (name, prop))
            for req in s.get("required", []):
                self.assertIn(req, s["properties"])

    def test_validation(self):
        for name in EXPECTED:
            validate_args(name, GOOD[name], TOOLS[name].input_schema)
            with self.assertRaises(ToolError, msg=name):
                validate_args(name, BAD[name], TOOLS[name].input_schema)
        for name, args in MISSING.items():
            with self.assertRaises(ToolError, msg=name):
                validate_args(name, args, TOOLS[name].input_schema)
        with self.assertRaises(ToolError):
            validate_args("set_project_setting", {"value": "1"}, TOOLS["set_project_setting"].input_schema)
        for v in ("a", 1.5, True):
            validate_args("set_project_setting", {"key": "k", "value": v}, TOOLS["set_project_setting"].input_schema)

    @unittest.skipUnless(os.path.exists(FUSCRIPT), "fuscript not installed")
    def test_lua_compiles(self):
        for name in LUA_TOOLS:
            code = TOOLS[name].lua
            self.assertTrue(code, name)
            with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as fh:
                fh.write(code)
                path = fh.name
            try:
                out = subprocess.run([FUSCRIPT, "-l", "lua", "-x", "assert(loadfile('%s')); print('ok')" % path],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     universal_newlines=True, timeout=60)
                self.assertIn("ok", out.stdout, "%s: %s %s" % (name, out.stdout, out.stderr))
            finally:
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
