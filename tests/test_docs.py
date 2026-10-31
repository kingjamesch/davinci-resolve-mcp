import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from davinci_mcp import docs  # noqa: E402
from davinci_mcp.tools import ToolError  # noqa: E402

PYI = '''"""Fake stub."""
from typing import TypedDict

Color = str
"""Alias doc ignored."""

class Timeline:
\t"""A timeline."""
\tdef GetName(self) -> str:
\t\t"""Returns the timeline name."""
\t\t...
\tdef AddMarker(self, frameId: float, color: str) -> bool:
\t\t"""Adds a marker to the timeline at frameId."""
\t\t...

class MediaPool:
\tdef AddSubFolder(self, folder: Folder, name: str) -> Folder:
\t\t"""Creates a sub folder in the media pool."""
\t\t...

class MarkerInfo(TypedDict, total=False):
\tcolor: str
\t"""Marker color"""
'''

README = """# Title

## Overview

Intro text about markers and the timeline.

### Markers

Marker handling notes.
"""


class DocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        with open(os.path.join(cls.dir, docs.PYI_NAME), "w") as fh:
            fh.write(PYI)
        with open(os.path.join(cls.dir, docs.README_NAME), "w") as fh:
            fh.write(README)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_index_kinds_and_signatures(self):
        idx = docs.build_index(self.dir)
        sigs = {e["signature"]: e for e in idx}
        self.assertEqual(sigs["class Timeline"]["kind"], "class")
        self.assertEqual(sigs["class Timeline"]["doc"], "A timeline.")
        self.assertEqual(sigs["Timeline.GetName() -> str"]["kind"], "method")
        self.assertEqual(sigs["Timeline.AddMarker(frameId: float, color: str) -> bool"]["doc"],
                         "Adds a marker to the timeline at frameId.")
        self.assertEqual(sigs["MarkerInfo.color: str"]["kind"], "field")
        self.assertEqual(sigs["MarkerInfo.color: str"]["doc"], "Marker color")
        self.assertEqual(sigs["## Overview"]["kind"], "section")
        self.assertEqual(sigs["### Markers"]["source"], "readme")

    def test_and_matching(self):
        r = docs.search("marker timeline", docs_dir=self.dir)
        names = [m["name"] for m in r["matches"]]
        self.assertIn("AddMarker", names)
        self.assertNotIn("GetName", names)
        self.assertEqual(docs.search("marker zzzz", docs_dir=self.dir)["total"], 0)

    def test_class_filter(self):
        r = docs.search("folder", class_filter="mediapool", docs_dir=self.dir)
        self.assertEqual([m["name"] for m in r["matches"]], ["AddSubFolder"])
        self.assertEqual(docs.search("folder", class_filter="Timeline", docs_dir=self.dir)["total"], 0)

    def test_scoring_exact_name_first_and_method_over_section(self):
        r = docs.search("addmarker", docs_dir=self.dir)
        self.assertEqual(r["matches"][0]["name"], "AddMarker")
        r = docs.search("marker", docs_dir=self.dir)
        kinds = [m["kind"] for m in r["matches"]]
        self.assertEqual(kinds[0], "method")

    def test_limit_and_truncated(self):
        r = docs.search("marker", limit=2, docs_dir=self.dir)
        self.assertEqual(len(r["matches"]), 2)
        self.assertTrue(r["truncated"])
        self.assertGreater(r["total"], 2)
        r = docs.search("marker", limit=999, docs_dir=self.dir)
        self.assertFalse(r["truncated"])
        self.assertEqual(len(docs.search("marker", limit=0, docs_dir=self.dir)["matches"]), 1)

    def test_doc_cut(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, docs.README_NAME), "w") as fh:
                fh.write("## Big\n\n" + ("word " * 400))
            e = docs.build_index(d)[0]
            self.assertEqual(len(e["doc"]), 600)
            self.assertTrue(e["doc"].endswith("..."))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_dir(self):
        with self.assertRaises(ToolError) as cm:
            docs.search("x", docs_dir="/nonexistent/davinci-docs")
        self.assertIn("was not found at /nonexistent/davinci-docs", str(cm.exception))

    @unittest.skipUnless(os.path.isdir(docs.DOCS_DIR), "Resolve scripting docs not installed")
    def test_live(self):
        r = docs.search("GetItemListInTrack")
        self.assertTrue(any(m["class"] == "Timeline" and m["kind"] == "method" for m in r["matches"]))


if __name__ == "__main__":
    unittest.main()
