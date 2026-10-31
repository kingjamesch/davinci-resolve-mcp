"""Contract snapshot of the tool surface and a sync check for docs/TOOLS.md.

Python 3.9 compatible, standard library only.
"""
import importlib.util
import os
import tempfile
import unittest

from davinci_mcp import tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# Contract snapshot: tool names are the API. A rename or removal must fail this test.
EXPECTED_TOOLS = [
    "add_marker",
    "add_render_job",
    "append_clips_to_timeline",
    "create_media_pool_folder",
    "create_timeline",
    "delete_markers",
    "delete_render_job",
    "delete_timeline_items",
    "export_timeline",
    "get_clip_properties",
    "get_project_info",
    "get_project_setting",
    "get_render_status",
    "get_timeline_info",
    "get_timeline_items",
    "get_timeline_markers",
    "import_media",
    "insert_timeline_element",
    "list_media_pool",
    "list_projects",
    "list_render_options",
    "list_timelines",
    "open_page",
    "open_project",
    "resolve_status",
    "run_lua",
    "save_project",
    "scripting_api_docs",
    "set_current_timeline",
    "set_project_setting",
    "set_timecode",
    "start_render",
    "stop_render",
]


def _load_generator():
    path = os.path.join(ROOT, "scripts", "gen_tools_doc.py")
    spec = importlib.util.spec_from_file_location("gen_tools_doc", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tools.load_all(strict=True)

    def test_registered_tool_names_match_snapshot(self):
        self.assertEqual(sorted(tools.TOOLS), EXPECTED_TOOLS)

    def test_snapshot_is_sorted_and_unique(self):
        self.assertEqual(EXPECTED_TOOLS, sorted(set(EXPECTED_TOOLS)))

    def test_every_tool_has_the_four_hints(self):
        for tool in tools.ordered_tools():
            for hint in HINTS:
                self.assertIn(hint, tool.annotations, "%s lacks %s" % (tool.name, hint))
                self.assertIsInstance(tool.annotations[hint], bool, "%s %s" % (tool.name, hint))

    def test_destructive_tools_require_confirm(self):
        destructive = [t for t in tools.ordered_tools() if t.annotations.get("destructiveHint")]
        self.assertTrue(destructive)
        # export_timeline is destructive only when overwriting; its confirm is required by the Lua chunk
        # in that case (documented in docs/MCP-RECORD.md), so it is exempt from the "required" check only.
        conditional = {"export_timeline"}
        for tool in destructive:
            if tool.name == "run_lua":
                continue
            schema = tool.input_schema
            if tool.name not in conditional:
                self.assertIn("confirm", schema.get("required", []), tool.name)
            self.assertEqual(schema["properties"]["confirm"].get("enum"), [True], tool.name)

    def test_run_lua_is_destructive_without_confirm(self):
        spec = tools.TOOLS["run_lua"]
        self.assertTrue(spec.annotations["destructiveHint"])
        self.assertNotIn("confirm", spec.input_schema.get("properties", {}))

    def test_read_only_tools_are_flagged_read_only(self):
        reads = [t for t in tools.ordered_tools() if t.group == "read"]
        self.assertTrue(reads)
        for tool in reads:
            self.assertIs(tool.annotations["readOnlyHint"], True, tool.name)
            self.assertIs(tool.annotations["destructiveHint"], False, tool.name)

    def test_non_read_tools_are_not_flagged_read_only(self):
        for tool in tools.ordered_tools():
            if tool.group != "read":
                self.assertIs(tool.annotations["readOnlyHint"], False, tool.name)

    def test_tools_doc_is_in_sync(self):
        generator = _load_generator()
        committed = os.path.join(ROOT, "docs", "TOOLS.md")
        self.assertTrue(os.path.isfile(committed), "docs/TOOLS.md is missing")
        with tempfile.TemporaryDirectory() as tmp:
            fresh = generator.write(os.path.join(tmp, "TOOLS.md"))
            with open(fresh, "r", encoding="utf-8") as fh:
                expected = fh.read()
        with open(committed, "r", encoding="utf-8") as fh:
            actual = fh.read()
        self.assertEqual(actual, expected, "docs/TOOLS.md is stale; run scripts/gen_tools_doc.py")


if __name__ == "__main__":
    unittest.main()
