import os
import shutil
import subprocess
import tempfile
import time
import unittest

from davinci_mcp import luafmt, prefs
from davinci_mcp.transport import BridgeClient, LuaError

FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "lua_harness.lua")
BRIDGE = os.path.join(ROOT, "bridge", "Claude_MCP_Bridge.lua")


@unittest.skipUnless(os.path.exists(FUSCRIPT), "fuscript not installed")
class BridgeRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cmcp-")
        cls.state = os.path.join(cls.tmp, "state")
        cls.profiles = os.path.join(cls.tmp, "Profiles")
        os.makedirs(cls.state)
        os.makedirs(os.path.join(cls.profiles, "Default"))
        cls.prefs_file = os.path.join(cls.profiles, "Default", "Fusion.prefs")
        with open(cls.prefs_file, "w"):
            pass
        cls.client = BridgeClient(state_dir=cls.state, profiles_dir=cls.profiles, poll_s=0.01,
                                  log=lambda line: None)
        cls.client.startup_cleanup()
        cls.out = open(os.path.join(cls.tmp, "stdout.txt"), "w+")
        cls.err = open(os.path.join(cls.tmp, "stderr.txt"), "w+")
        cls.proc = subprocess.Popen(
            [FUSCRIPT, "-l", "lua", HARNESS, cls.state, cls.prefs_file, "6000", BRIDGE],
            stdout=cls.out, stderr=cls.err, universal_newlines=True)
        deadline = time.time() + 10
        while time.time() < deadline:
            if cls.client.liveness()["running"]:
                return
            if cls.proc.poll() is not None:
                break
            time.sleep(0.05)
        cls._kill()
        detail = "bridge did not start; stdout=%r stderr=%r" % (cls._read(cls.out), cls._read(cls.err))
        for fh in (getattr(cls, "out", None), getattr(cls, "err", None)):
            try:
                fh.close()
            except Exception:
                pass
        shutil.rmtree(cls.tmp, ignore_errors=True)
        raise RuntimeError(detail)

    @staticmethod
    def _read(fh):
        fh.flush()
        fh.seek(0)
        return fh.read()

    @classmethod
    def _kill(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait()

    @classmethod
    def tearDownClass(cls):
        cls._kill()
        cls.out.close()
        cls.err.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_ping(self):
        res = self.client.ping()
        self.assertEqual(res.get("product"), "DaVinci Resolve")
        self.assertTrue(res.get("pid"))

    def test_02_arithmetic(self):
        self.assertEqual(self.client.run("return 6*7")["result"], 42)

    def test_03_table_and_prints(self):
        env = self.client.run("print('hi'); return {a=1, list={1,2}}")
        self.assertEqual(env["result"], {"a": 1, "list": [1, 2]})
        self.assertEqual(env["prints"], ["hi"])

    def test_04_args(self):
        self.assertEqual(self.client.run("return args.name", args={"name": "x"})["result"], "x")

    def test_05_lua_error(self):
        with self.assertRaises(LuaError) as cm:
            self.client.run("error('boom')")
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Lua error: "), msg)
        self.assertTrue(msg.endswith("boom"), msg)  # Lua prefixes the chunk position: "claude:1: boom"

    def test_06_truncation(self):
        env = self.client.run("return string.rep('x', 200000)", max_kb=16)
        self.assertIs(env.get("truncated"), True)
        self.assertGreater(env.get("result_bytes"), 100000)

    def test_07_fake_resolve(self):
        env = self.client.run("return resolve:GetProjectManager():GetCurrentProject():GetName()")
        self.assertEqual(env["result"], "Fake Project")

    def test_08_malformed_request(self):
        req_id = "badreq01"
        folder = os.path.join(self.state, "inbox", req_id)
        os.makedirs(os.path.join(folder, "0000-zz"))
        with open(os.path.join(self.state, "inbox", "PENDING"), "a"):
            pass
        env = None
        deadline = time.time() + 5
        try:
            while time.time() < deadline and env is None:
                text = prefs.read_text(self.prefs_file) or ""
                rid, body = prefs.split_resp(prefs.extract_keys(text).get("CMCP_Resp", ""))
                if rid == req_id and body:
                    env = prefs.decode_hex_json(body)
                    break
                time.sleep(0.02)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                os.remove(os.path.join(self.state, "inbox", "PENDING"))
            except OSError:
                pass
        self.assertIsNotNone(env, "no response to malformed request within 5 s")
        self.assertIs(env.get("ok"), False)

    def test_99_stop(self):
        self.assertTrue(self.client.stop())
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.fail("harness did not exit within 5 s of stop")
        # the stop op ends the loop with "stopped"; "stop-flag" is the CMCP_Stop (Stop script) path
        self.assertIn("stopped", self._read(self.out), self._read(self.err))


if __name__ == "__main__":
    unittest.main()
