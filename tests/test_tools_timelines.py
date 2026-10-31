import os
import subprocess
import tempfile
import unittest

from davinci_mcp.tools import TOOLS, ToolError, validate_args
from davinci_mcp.tools import timelines  # noqa: F401  (registers the tools)

FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"

# name -> (destructive, readOnly, good args, bad args list)
EXPECTED = {
    "list_timelines": (False, True, {}, [{"x": 1}]),
    "get_timeline_info": (False, True, {"timeline": "T"}, [{"timeline": 5}]),
    "set_current_timeline": (False, False, {"name": "T"}, [{}, {"name": 3}, {"name": ""}]),
    "set_timecode": (False, False, {"timecode": "01:00:00:10"},
                     [{}, {"timecode": "1:00"}, {"timecode": 5}]),
    "create_timeline": (False, False, {"name": "N", "clipNames": ["a"]},
                        [{}, {"name": "N", "clipNames": ["a"] * 101}, {"name": "N", "clipNames": "a"}]),
    "get_timeline_items": (False, True, {"trackType": "video", "trackIndex": 1},
                           [{"trackType": "video"}, {"trackType": "film", "trackIndex": 1},
                            {"trackType": "video", "trackIndex": 0},
                            {"trackType": "video", "trackIndex": 1, "limit": 201}]),
    "get_timeline_markers": (False, True, {}, [{"timeline": "x" * 201}]),
    "add_marker": (False, False, {"frame": 100, "color": "Blue"},
                   [{"color": "Blue"}, {"frame": 100, "color": "Black"}, {"frame": -1, "color": "Blue"},
                    {"frame": 1, "color": "Blue", "duration": 100001}]),
    "delete_markers": (True, False, {"confirm": True, "color": "All"},
                       [{}, {"confirm": False}, {"confirm": True, "color": "Teal"}]),
    "insert_timeline_element": (False, False, {"kind": "title", "name": "Text+"},
                                [{"name": "Text+"}, {"kind": "bogus", "name": "x"}, {"kind": "title", "name": ""}]),
    "delete_timeline_items": (True, False,
                              {"confirm": True, "trackType": "video", "trackIndex": 1, "itemIndexes": [1]},
                              [{"confirm": True, "trackType": "video", "trackIndex": 1},
                               {"confirm": True, "trackType": "video", "trackIndex": 1, "itemIndexes": []},
                               {"confirm": True, "trackType": "video", "trackIndex": 1, "itemIndexes": ["a"]},
                               {"confirm": True, "trackType": "video", "trackIndex": 1, "itemIndexes": list(range(1, 52))}]),
    "export_timeline": (True, False, {"path": "/tmp/x.edl", "type": "EDL"},
                        [{"path": "/tmp/x.edl"}, {"path": "/tmp/x.edl", "type": "PDF"},
                         {"path": "", "type": "EDL"}, {"path": "/tmp/x", "type": "EDL", "overwrite": "yes"}]),
}


class TimelineToolsTest(unittest.TestCase):
    def test_registration_and_annotations(self):
        for name, (destructive, read_only, _good, _bad) in EXPECTED.items():
            self.assertIn(name, TOOLS)
            ann = TOOLS[name].annotations
            self.assertEqual(ann["destructiveHint"], destructive, name)
            self.assertEqual(ann["readOnlyHint"], read_only, name)

    def test_schemas(self):
        for name in EXPECTED:
            s = TOOLS[name].input_schema
            self.assertEqual(s["type"], "object")
            self.assertIs(s["additionalProperties"], False)
            self.assertTrue(TOOLS[name].description)
            for prop, spec in s["properties"].items():
                self.assertTrue(spec.get("description"), "%s.%s" % (name, prop))
            for req in s["required"]:
                self.assertIn(req, s["properties"], name)

    def test_validate_args(self):
        for name, (_d, _r, good, bad) in EXPECTED.items():
            schema = TOOLS[name].input_schema
            validate_args(name, good, schema)
            for b in bad:
                with self.assertRaises(ToolError, msg="%s %r" % (name, b)):
                    validate_args(name, b, schema)

    @unittest.skipUnless(os.path.exists(FUSCRIPT), "fuscript not installed")
    def test_lua_compiles(self):
        for name in EXPECTED:
            code = TOOLS[name].lua
            self.assertTrue(code, name)
            with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
                f.write(code)
                path = f.name
            try:
                out = subprocess.run(
                    [FUSCRIPT, "-l", "lua", "-x", "assert(loadfile('%s')); print('ok')" % path],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
                self.assertIn("ok", out.stdout, "%s: %s %s" % (name, out.stdout, out.stderr))
            finally:
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
