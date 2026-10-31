import os
import subprocess
import tempfile
import unittest

from davinci_mcp.tools import TOOLS, ToolError, validate_args
from davinci_mcp.tools import render  # noqa: F401  (registers the tools)

FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"
NAMES = {
    "get_render_status": "read", "list_render_options": "read", "add_render_job": "write",
    "start_render": "write", "stop_render": "write", "delete_render_job": "destructive",
}
GOOD = {
    "get_render_status": {}, "list_render_options": {"format": "mp4"},
    "add_render_job": {"targetDir": "/tmp", "markIn": 0},
    "start_render": {"jobIds": ["a"]}, "stop_render": {},
    "delete_render_job": {"jobId": "x", "confirm": True},
}
BAD = {
    "get_render_status": {"x": 1},
    "list_render_options": {"format": "x" * 61},
    "add_render_job": {"targetDir": "/tmp", "markIn": -1},
    "start_render": {"jobIds": ["a"] * 51},
    "stop_render": {"x": 1},
    "delete_render_job": {"jobId": "x", "confirm": False},
}
MISSING = {"add_render_job": {}, "delete_render_job": {"jobId": "x"}}
WRONG_TYPE = {"add_render_job": {"targetDir": 5}, "start_render": {"jobIds": "a"}}


class RenderToolsTest(unittest.TestCase):
    def test_registered_with_annotations(self):
        for name, kind in NAMES.items():
            self.assertIn(name, TOOLS)
            ann = TOOLS[name].annotations
            self.assertEqual(ann["readOnlyHint"], kind == "read", name)
            self.assertEqual(ann["destructiveHint"], kind == "destructive", name)

    def test_schemas_valid(self):
        for name in NAMES:
            s = TOOLS[name].input_schema
            self.assertEqual(s["type"], "object")
            self.assertIs(s["additionalProperties"], False)
            for prop, spec in s["properties"].items():
                self.assertTrue(spec.get("description"), "%s.%s" % (name, prop))
            for req in s.get("required", []):
                self.assertIn(req, s["properties"])

    def test_validation(self):
        for name in NAMES:
            validate_args(name, GOOD[name], TOOLS[name].input_schema)
            with self.assertRaises(ToolError):
                validate_args(name, BAD[name], TOOLS[name].input_schema)
        for name, a in MISSING.items():
            with self.assertRaises(ToolError):
                validate_args(name, a, TOOLS[name].input_schema)
        for name, a in WRONG_TYPE.items():
            with self.assertRaises(ToolError):
                validate_args(name, a, TOOLS[name].input_schema)

    @unittest.skipUnless(os.path.exists(FUSCRIPT), "fuscript not installed")
    def test_lua_compiles(self):
        for name in NAMES:
            code = TOOLS[name].lua
            self.assertTrue(code, name)
            with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as fh:
                fh.write(code)
            try:
                out = subprocess.run(
                    [FUSCRIPT, "-l", "lua", "-x", "assert(loadfile('%s')); print('ok')" % fh.name],
                    capture_output=True, text=True, timeout=60)
            finally:
                os.unlink(fh.name)
            self.assertIn("ok", out.stdout, "%s: %s%s" % (name, out.stdout, out.stderr))


if __name__ == "__main__":
    unittest.main()
