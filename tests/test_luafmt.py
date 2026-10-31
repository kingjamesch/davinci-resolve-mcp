import os
import subprocess
import tempfile
import unittest

from davinci_mcp import luafmt

FUSCRIPT = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"
HAVE_FUSCRIPT = os.path.exists(FUSCRIPT)


class LuaStringTests(unittest.TestCase):
    def test_escapes(self):
        self.assertEqual(luafmt.lua_string("a\\b"), '"a\\\\b"')
        self.assertEqual(luafmt.lua_string('a"b'), '"a\\"b"')
        self.assertEqual(luafmt.lua_string("a\nb"), '"a\\nb"')
        self.assertEqual(luafmt.lua_string("a\rb"), '"a\\rb"')
        self.assertEqual(luafmt.lua_string("a\tb"), '"a\\tb"')
        self.assertEqual(luafmt.lua_string("a\x00" "1"), '"a\\0001"')
        self.assertEqual(luafmt.lua_string("\x7f"), '"\\127"')

    def test_non_ascii_passthrough(self):
        self.assertEqual(luafmt.lua_string("héllo ☃"), '"héllo ☃"')


class LuaKeyTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(luafmt.lua_key("name"), "name")
        self.assertEqual(luafmt.lua_key("_x1"), "_x1")
        self.assertEqual(luafmt.lua_key("end"), '["end"]')
        self.assertEqual(luafmt.lua_key("a-b"), '["a-b"]')
        self.assertEqual(luafmt.lua_key("1a"), '["1a"]')
        self.assertEqual(luafmt.lua_key(5), "[5]")

    def test_bad_keys(self):
        with self.assertRaises(TypeError):
            luafmt.lua_key(True)
        with self.assertRaises(TypeError):
            luafmt.lua_key(1.5)


class LuaValueTests(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(luafmt.lua_value(None), "nil")
        self.assertEqual(luafmt.lua_value(True), "true")
        self.assertEqual(luafmt.lua_value(False), "false")
        self.assertEqual(luafmt.lua_value(7), "7")
        self.assertEqual(luafmt.lua_value(1.5), "1.5")
        self.assertEqual(luafmt.lua_value("x"), '"x"')

    def test_containers(self):
        self.assertEqual(luafmt.lua_value([1, "a", None]), '{1, "a", nil}')
        self.assertEqual(luafmt.lua_value({"a": 1, "b": None}), "{a = 1}")
        self.assertEqual(luafmt.lua_value({"end": [True]}), '{["end"] = {true}}')

    def test_unsupported(self):
        with self.assertRaises(TypeError):
            luafmt.lua_value(object())
        with self.assertRaises(TypeError):
            luafmt.lua_value({1, 2})
        with self.assertRaises(ValueError):
            luafmt.lua_value(float("nan"))


class BuildRequestTests(unittest.TestCase):
    def test_bad_id(self):
        for bad in ("short", "UPPERCASE1", "has-dash1", "a" * 33):
            with self.assertRaises(ValueError):
                luafmt.build_request(bad, "ping", 1)

    def test_bad_op(self):
        with self.assertRaises(ValueError):
            luafmt.build_request("abcdef1", "nope", 1)

    def test_missing_code(self):
        for code in (None, ""):
            with self.assertRaises(ValueError):
                luafmt.build_request("abcdef1", "run", 1, code=code)

    def test_args_not_dict(self):
        with self.assertRaises(ValueError):
            luafmt.build_request("abcdef1", "run", 1, code="return 1", args=[1])

    def test_payload_cap(self):
        with self.assertRaises(ValueError):
            luafmt.build_request("abcdef1", "run", 1, code="x" * (luafmt.MAX_PAYLOAD + 1))

    def test_ping_has_no_code(self):
        payload = luafmt.build_request("abcdef1", "ping", 5).decode("utf-8")
        self.assertIn('op = "ping"', payload)
        self.assertNotIn("code", payload)

    @unittest.skipUnless(HAVE_FUSCRIPT, "fuscript not installed")
    def test_parses_as_lua_table(self):
        code = 'line1\nq"uote\\ \x00 é end'
        payload = luafmt.build_request("abcdef1", "run", 99, code=code, args={"end": [1, 2], "n": 1.5})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "req.lua")
            with open(path, "wb") as fh:
                fh.write(b"return " + payload)
            script = ("local t=dofile('%s') print(t.id) print(t.op) print(t.ts) "
                      "print(t.code == %s) print(t.args['end'][2])"
                      % (path, luafmt.lua_string(code)))
            res = subprocess.run([FUSCRIPT, "-l", "lua", "-x", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        self.assertEqual(lines[-5:], ["abcdef1", "run", "99", "true", "2"], res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
