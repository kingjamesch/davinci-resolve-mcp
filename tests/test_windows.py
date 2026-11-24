"""Windows support: path helpers, chunk sizing under MAX_PATH, process liveness, Lua path checks."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import install  # noqa: E402
from davinci_mcp import paths, transport  # noqa: E402
from davinci_mcp.tools._lua import PRELUDE  # noqa: E402


class MediaStorageTests(unittest.TestCase):
    def test_parses_roots_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.dat")
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write("Site.Count = 1\n"
                         "Site.1.FS.Count = 2\n"
                         "Site.1.FS.1.Root = C:\\Users\\me\\Videos\n"
                         "Site.1.FS.1.MappedRoot = \n"
                         "Site.1.FS.2.Root = ResolveVirtual\n")
            self.assertEqual(paths.media_storage_roots(cfg), ["C:\\Users\\me\\Videos", "ResolveVirtual"])
        self.assertIsNone(paths.media_storage_roots(os.path.join(ROOT, "no-such-config.dat")))

    def test_inside_media_storage(self):
        root = os.path.join(tempfile.gettempdir(), "MediaRoot")
        self.assertTrue(paths.is_inside_media_storage(root, [root]))
        self.assertTrue(paths.is_inside_media_storage(os.path.join(root, "ClaudeMCP"), [root + os.sep]))
        self.assertFalse(paths.is_inside_media_storage(root + "Sibling", [root]))
        self.assertFalse(paths.is_inside_media_storage(root, ["ResolveVirtual", ""]))
        self.assertFalse(paths.is_inside_media_storage(root, None))

    def test_check_reports_media_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "ClaudeMCP")
            cfg = os.path.join(tmp, "config.dat")
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write("Site.1.FS.1.Root = %s\n" % state)
            rep = install.check(ROOT, os.path.join(tmp, "Scripts"), state, resolve_config=cfg)
            self.assertTrue(rep["media_storage_ok"])

    def test_strip_sep_keeps_roots(self):
        self.assertEqual(install._strip_sep("/a/b/"), "/a/b")
        self.assertEqual(install._strip_sep("/"), "/")
        self.assertEqual(install._strip_sep("C:\\x\\"), "C:\\x")
        self.assertEqual(install._strip_sep("C:\\"), "C:" + os.sep)


class ChunkSizeTests(unittest.TestCase):
    def test_posix_keeps_default(self):
        self.assertEqual(transport.chunk_bytes_for("/tmp/x", windows=False), transport.CHUNK_BYTES)

    def test_windows_fits_max_path(self):
        parent = "C:\\Users\\someone\\AppData\\Local\\ClaudeMCP\\inbox\\abcdefg123456.tmp"
        size = transport.chunk_bytes_for(parent, windows=True)
        longest = os.path.join(os.path.abspath(parent), "%05d-%s" % (99999, "ab" * size))
        self.assertLessEqual(len(longest), transport.WINDOWS_MAX_PATH)
        self.assertGreaterEqual(size, transport.MIN_CHUNK_BYTES)

    def test_windows_rejects_absurd_parent(self):
        with self.assertRaises(transport.BridgeError):
            transport.chunk_bytes_for("C:\\" + "x" * 240, windows=True)


@unittest.skipUnless(paths.IS_WINDOWS, "Windows only")
class PidAliveWindowsTests(unittest.TestCase):
    def test_own_process_is_alive(self):
        self.assertTrue(transport._pid_alive(os.getpid()))

    def test_exited_process_is_not_alive_and_is_not_killed(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(transport._pid_alive(proc.pid))
            self.assertIsNone(proc.poll(), "_pid_alive must not terminate the process")
        finally:
            proc.kill()
            proc.wait()
        self.assertFalse(transport._pid_alive(proc.pid))


@unittest.skipUnless(os.path.exists(paths.FUSCRIPT), "fuscript not installed")
class LuaAbsPathTests(unittest.TestCase):
    def test_is_abs_path(self):
        cases = {"/Users/me/a.mov": True, "C:\\clips\\a.mov": True, "c:/clips/a.mov": True,
                 "\\\\server\\share\\a.mov": True, "clips\\a.mov": False, "a.mov": False, "C:a.mov": False}
        checks = " ".join("print(is_abs_path(%s) == %s)" % (_lua_str(p), "true" if v else "false")
                          for p, v in cases.items())
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False, encoding="utf-8") as fh:
            fh.write(PRELUDE + "\n" + checks + "\n")
            path = fh.name
        try:
            res = subprocess.run([paths.FUSCRIPT, "-l", "lua", path], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        finally:
            os.unlink(path)
        results = [ln.strip() for ln in res.stdout.splitlines() if ln.strip() in ("true", "false")]
        self.assertEqual(results, ["true"] * len(cases), res.stdout + res.stderr)


def _lua_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


if __name__ == "__main__":
    unittest.main()
