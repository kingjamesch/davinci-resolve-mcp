import os
import subprocess
import tempfile
import unittest

from davinci_mcp.tools import TOOLS, ToolError, validate_args
from davinci_mcp.tools import mediapool  # noqa: F401  (registers the tools)

NAMES = ["list_media_pool", "get_clip_properties", "create_media_pool_folder", "import_media",
         "append_clips_to_timeline"]
READ = {"list_media_pool", "get_clip_properties"}
FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"

GOOD = {
    "list_media_pool": {"folder": "Master/B-roll", "offset": 0, "limit": 50},
    "get_clip_properties": {"name": "clip.mov"},
    "create_media_pool_folder": {"name": "Day 1", "parent": "Master"},
    "import_media": {"paths": ["/tmp/a.mov"], "folder": "Master"},
    "append_clips_to_timeline": {"clips": [{"name": "a", "startFrame": 0, "mediaType": 1}]},
}
BAD = {
    "list_media_pool": [{"limit": 201}, {"offset": -1}, {"folder": 3}, {"x": 1}],
    "get_clip_properties": [{"name": 5}, {"id": "x" * 101}],
    "create_media_pool_folder": [{}, {"name": ""}, {"name": "a", "parent": "p" * 501}],
    "import_media": [{}, {"paths": []}, {"paths": ["/a"] * 101}, {"paths": "/a"}],
    "append_clips_to_timeline": [{}, {"clips": []}, {"clips": [{"mediaType": 3}]},
                                 {"clips": [{"trackIndex": 0}]}, {"clips": [{"bogus": 1}]}],
}


class MediaPoolTools(unittest.TestCase):
    def test_registration(self):
        for n in NAMES:
            self.assertIn(n, TOOLS)
            ann = TOOLS[n].annotations
            self.assertEqual(ann["readOnlyHint"], n in READ)
            self.assertFalse(ann["destructiveHint"])

    def test_schemas(self):
        for n in NAMES:
            s = TOOLS[n].input_schema
            self.assertEqual(s["type"], "object")
            self.assertIs(s["additionalProperties"], False)
            self.assertTrue(TOOLS[n].description)
            for p, spec in s["properties"].items():
                self.assertTrue(spec.get("description"), "%s.%s" % (n, p))
            for r in s.get("required", []):
                self.assertIn(r, s["properties"])
        item = TOOLS["append_clips_to_timeline"].input_schema["properties"]["clips"]["items"]
        self.assertIs(item["additionalProperties"], False)
        for p, spec in item["properties"].items():
            self.assertTrue(spec.get("description"), p)

    def test_validation(self):
        for n in NAMES:
            validate_args(n, GOOD[n], TOOLS[n].input_schema)
            for bad in BAD[n]:
                with self.assertRaises(ToolError, msg="%s %r" % (n, bad)):
                    validate_args(n, bad, TOOLS[n].input_schema)

    @unittest.skipUnless(os.path.exists(FUSCRIPT), "fuscript not installed")
    def test_lua_compiles(self):
        for n in NAMES:
            code = TOOLS[n].lua
            self.assertTrue(code)
            with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
                f.write(code)
                path = f.name
            try:
                out = subprocess.run([FUSCRIPT, "-l", "lua", "-x",
                                      "assert(loadfile('%s')); print('ok')" % path],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     universal_newlines=True, timeout=60)
                self.assertIn("ok", out.stdout, "%s: %s" % (n, out.stderr))
            finally:
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
