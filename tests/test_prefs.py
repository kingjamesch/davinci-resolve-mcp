import json
import os
import tempfile
import unittest

from davinci_mcp import prefs


class ExtractKeysTests(unittest.TestCase):
    def test_last_occurrence_wins_and_other_keys_ignored(self):
        text = ('CMCP_Resp = "old:aa",\nOther = "zz",\nCMCP_Beat = "123",\n'
                'CMCP_Resp = "new:bb",\nNotCMCP_Resp = "x",\n')
        got = prefs.extract_keys(text)
        self.assertEqual(got["CMCP_Resp"], "new:bb")
        self.assertEqual(got["CMCP_Beat"], "123")
        self.assertNotIn("Other", got)
        self.assertNotIn("CMCP_Stop", got)

    def test_empty_value_present(self):
        self.assertEqual(prefs.extract_keys('CMCP_Stop = "",')["CMCP_Stop"], "")


class SplitRespTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(prefs.split_resp("abc:00ff"), ("abc", "00ff"))
        self.assertEqual(prefs.split_resp(""), ("", ""))
        self.assertEqual(prefs.split_resp("abc"), ("abc", ""))


class DecodeTests(unittest.TestCase):
    def test_odd_length(self):
        with self.assertRaises(ValueError):
            prefs.decode_hex("abc")

    def test_non_hex(self):
        with self.assertRaises(ValueError):
            prefs.decode_hex("zz")

    def test_non_json(self):
        with self.assertRaises(ValueError):
            prefs.decode_hex_json(b"not json".hex())

    def test_non_object(self):
        with self.assertRaises(ValueError):
            prefs.decode_hex_json(b"[1,2]".hex())

    def test_good(self):
        self.assertEqual(prefs.decode_hex_json(json.dumps({"a": 1}).encode().hex()), {"a": 1})


class FindPrefsTests(unittest.TestCase):
    def test_newest_profile_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("A", "B"):
                os.makedirs(os.path.join(tmp, name))
                p = os.path.join(tmp, name, "Fusion.prefs")
                with open(p, "w"):
                    pass
                paths.append(p)
            os.utime(paths[0], (2000, 2000))
            os.utime(paths[1], (1000, 1000))
            self.assertEqual(prefs.find_prefs_file(tmp), paths[0])
            os.utime(paths[1], (3000, 3000))
            self.assertEqual(prefs.find_prefs_file(tmp), paths[1])

    def test_missing_dir(self):
        self.assertIsNone(prefs.find_prefs_file("/nonexistent-dir-xyz"))


if __name__ == "__main__":
    unittest.main()
