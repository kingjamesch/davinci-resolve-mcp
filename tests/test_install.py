import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import install  # noqa: E402


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.scripts = os.path.join(self.tmp, "Scripts", "Utility")
        self.state = os.path.join(self.tmp, "ClaudeMCP")

    def test_install_stamps_and_modes(self):
        install.install(ROOT, self.scripts, self.state)
        with open(os.path.join(self.scripts, install.BRIDGE)) as fh:
            body = fh.read()
        self.assertIn(self.state, body)
        self.assertNotIn(install.TOKEN, body)
        with open(os.path.join(ROOT, "bridge", install.STOP)) as a, \
                open(os.path.join(self.scripts, install.STOP)) as b:
            self.assertEqual(a.read(), b.read())
        for d in (self.state, os.path.join(self.state, "inbox")):
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def test_desktop_config_preserves_keys_and_backs_up(self):
        path = os.path.join(self.tmp, "cfg.json")
        orig = {"zeta": 1, "mcpServers": {"other": {"command": "x"}}, "alpha": [1, 2]}
        with open(path, "w") as fh:
            json.dump(orig, fh)
        install.configure_claude_desktop(path, ROOT)
        with open(path) as fh:
            raw = fh.read()
        cfg = json.loads(raw)
        self.assertEqual(list(cfg.keys()), ["zeta", "mcpServers", "alpha"])
        self.assertEqual(cfg["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(cfg["mcpServers"]["davinci"]["command"], "/usr/bin/python3")
        self.assertEqual(cfg["mcpServers"]["davinci"]["args"], [os.path.join(ROOT, "server.py")])
        backups = [f for f in os.listdir(self.tmp) if f.startswith("cfg.json.bak-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmp, backups[0])) as fh:
            self.assertEqual(json.load(fh), orig)

    def test_desktop_config_created_when_missing(self):
        path = os.path.join(self.tmp, "sub", "cfg.json")
        install.configure_claude_desktop(path, ROOT)
        with open(path) as fh:
            self.assertEqual(list(json.load(fh).keys()), ["mcpServers"])

    def test_uninstall_and_purge(self):
        install.install(ROOT, self.scripts, self.state)
        with mock.patch("shutil.which", return_value=None):
            install.uninstall(self.scripts, self.state)
            self.assertFalse(os.path.exists(os.path.join(self.scripts, install.BRIDGE)))
            self.assertFalse(os.path.exists(os.path.join(self.scripts, install.STOP)))
            self.assertTrue(os.path.isdir(self.state))
            install.uninstall(self.scripts, self.state, purge=True)
            self.assertFalse(os.path.exists(self.state))

    def test_check_reports(self):
        with mock.patch("shutil.which", return_value=None):
            rep = install.check(ROOT, self.scripts, self.state)
            self.assertFalse(rep["bridge_installed"])
            self.assertFalse(rep["stamp_matches"])
            install.install(ROOT, self.scripts, self.state)
            rep = install.check(ROOT, self.scripts, self.state)
        self.assertTrue(rep["bridge_installed"])
        self.assertTrue(rep["stamp_matches"])
        self.assertTrue(rep["stop_script_present"])
        self.assertEqual(rep["state_dir_mode"], "0o700")
        self.assertIsNone(rep["claude_registered"])

    def test_register_without_claude(self):
        with mock.patch("shutil.which", return_value=None):
            ok, msg = install.register_claude_code(ROOT)
        self.assertFalse(ok)
        self.assertIn("claude mcp add --scope user davinci -- /usr/bin/python3 "
                      + os.path.join(ROOT, "server.py"), msg)


if __name__ == "__main__":
    unittest.main()
