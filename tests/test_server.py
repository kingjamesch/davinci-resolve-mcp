"""Unit tests for the MCP JSON-RPC dispatcher plus one process-level stdio test.

The dispatcher is driven directly with a fake client; no bridge, no Resolve.
Python 3.9 compatible, standard library only.
"""
import atexit
import json
import os
import shutil
import subprocess
import tempfile
import unittest

# The process-level test spawns the real server; keep it away from the user's live state dir.
_ISOLATED = tempfile.mkdtemp(prefix="davinci-mcp-test-")
os.environ.setdefault("DAVINCI_MCP_STATE_DIR", os.path.join(_ISOLATED, "state"))
os.environ.setdefault("DAVINCI_MCP_PROFILES_DIR", os.path.join(_ISOLATED, "Profiles"))
atexit.register(shutil.rmtree, _ISOLATED, True)
from unittest import mock

from davinci_mcp import server
from davinci_mcp.server import INSTRUCTIONS, MCPDispatcher, SUPPORTED_VERSIONS
from davinci_mcp.tools import READ_ONLY, TOOLS, WRITE, ToolError, register
from davinci_mcp.transport import BridgeNotRunning, BridgeTimeout, LuaError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ECHO = "zz_test_echo"
RAISER = "zz_test_raise"

ECHO_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "maxLength": 10},
        "n": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "required": ["text"],
    "additionalProperties": False,
}


class FakeClient(object):
    """Stands in for BridgeClient; raise_exc is a callable the raising tool invokes."""

    def __init__(self):
        self.raise_exc = None


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.dispatcher = MCPDispatcher(self.client)
        patcher = mock.patch.object(server, "log")
        self.log = patcher.start()
        self.addCleanup(patcher.stop)
        self._added = []

        # Write tool first so the ordering test proves reads are listed ahead of writes.
        @register(RAISER, "Raises whatever the test injects.", {"type": "object", "properties": {}}, WRITE)
        def _raiser(client, args):
            client.raise_exc()

        @register(ECHO, "Echoes its arguments.", ECHO_SCHEMA, READ_ONLY)
        def _echo(client, args):
            return {"echo": args}

        self._added = [RAISER, ECHO]

    def tearDown(self):
        for name in self._added:
            TOOLS.pop(name, None)

    # -- helpers ------------------------------------------------------------------------------

    def rpc(self, method, params=None, req_id=1):
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        return self.dispatcher.handle(msg)

    def call(self, name, arguments=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments if arguments is not None else {}})

    def assert_tool_error(self, resp):
        self.assertNotIn("error", resp)
        self.assertTrue(resp["result"]["isError"])
        return resp["result"]["content"]

    # -- 1. initialize ------------------------------------------------------------------------

    def test_initialize_echoes_supported_version(self):
        resp = self.rpc("initialize", {"protocolVersion": "2025-03-26"}, req_id=42)
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["result"]["protocolVersion"], "2025-03-26")

    def test_initialize_falls_back_for_unknown_or_absent_version(self):
        self.assertEqual(SUPPORTED_VERSIONS[0], "2025-06-18")
        unknown = self.rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(unknown["result"]["protocolVersion"], "2025-06-18")
        absent = self.rpc("initialize", {})
        self.assertEqual(absent["result"]["protocolVersion"], "2025-06-18")
        no_params = self.rpc("initialize")
        self.assertEqual(no_params["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_result_shape(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertIs(result["capabilities"]["tools"]["listChanged"], False)
        self.assertEqual(result["serverInfo"]["name"], "davinci")
        self.assertEqual(result["instructions"], INSTRUCTIONS)

    # -- 2. notifications ---------------------------------------------------------------------

    def test_notifications_return_none(self):
        for method in ("notifications/initialized", "notifications/cancelled", "anything/else"):
            self.assertIsNone(self.dispatcher.handle({"jsonrpc": "2.0", "method": method}), method)
        self.assertIsNone(self.dispatcher.handle(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}}))

    # -- 3. ping ------------------------------------------------------------------------------

    def test_ping(self):
        resp = self.rpc("ping", req_id="abc")
        self.assertEqual(resp, {"jsonrpc": "2.0", "id": "abc", "result": {}})

    # -- 4. tools/list ------------------------------------------------------------------------

    def test_tools_list_shape_and_order(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertIn(ECHO, names)
        self.assertIn(RAISER, names)
        for t in tools:
            self.assertIsInstance(t["name"], str)
            self.assertTrue(t["description"])
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertIs(type(t["annotations"]["readOnlyHint"]), bool)
            self.assertIs(type(t["annotations"]["destructiveHint"]), bool)
        # The write tool was registered first, yet the read-only tool must be listed before it.
        self.assertLess(names.index(ECHO), names.index(RAISER))

    # -- 5. tools/call happy path -------------------------------------------------------------

    def test_call_happy_path(self):
        resp = self.call(ECHO, {"text": "hi"})
        result = resp["result"]
        self.assertIs(result["isError"], False)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"]), {"echo": {"text": "hi"}})

    def test_call_with_valid_optional_argument(self):
        resp = self.call(ECHO, {"text": "hi", "n": 5})
        self.assertIs(resp["result"]["isError"], False)
        self.assertEqual(json.loads(resp["result"]["content"][0]["text"]), {"echo": {"text": "hi", "n": 5}})

    # -- 6. validation ------------------------------------------------------------------------

    def _validation_text(self, arguments):
        content = self.assert_tool_error(self.call(ECHO, arguments))
        self.assertEqual(len(content), 1)
        return content[0]["text"]

    def test_validation_missing_required(self):
        text = self._validation_text({})
        self.assertTrue(text.startswith("Error: zz_test_echo: missing required argument 'text'."), text)

    def test_validation_wrong_type(self):
        self.assertIn("must be string", self._validation_text({"text": 5}))

    def test_validation_over_cap(self):
        self.assertIn("at most 10 characters", self._validation_text({"text": "x" * 11}))

    def test_validation_unknown_argument(self):
        self.assertIn("unknown argument", self._validation_text({"text": "hi", "bogus": 1}))

    def test_validation_out_of_range(self):
        self.assertIn("must be <= 5", self._validation_text({"text": "hi", "n": 9}))
        self.assertIn("must be >= 1", self._validation_text({"text": "hi", "n": 0}))

    # -- 7. unknown tool ----------------------------------------------------------------------

    def test_unknown_tool(self):
        content = self.assert_tool_error(self.call("nope"))
        self.assertEqual(content[0]["text"], "Error: Unknown tool: nope.")

    # -- 8. handler failures ------------------------------------------------------------------

    def _raise_and_call(self, exc):
        def boom():
            raise exc
        self.client.raise_exc = boom
        return self.call(RAISER)

    def test_tool_error_is_reported_verbatim(self):
        content = self.assert_tool_error(self._raise_and_call(ToolError("Custom sentence.")))
        self.assertEqual(content, [{"type": "text", "text": "Error: Custom sentence."}])

    def test_bridge_not_running(self):
        sentence = "The Resolve bridge is not running. Start it from the Workspace menu."
        content = self.assert_tool_error(self._raise_and_call(BridgeNotRunning(sentence)))
        self.assertEqual(content[0]["text"], "Error: " + sentence)

    def test_bridge_timeout(self):
        content = self.assert_tool_error(self._raise_and_call(BridgeTimeout("The bridge did not answer within 30 s.")))
        self.assertIn("did not answer", content[0]["text"])

    def test_lua_error_with_prints(self):
        content = self.assert_tool_error(
            self._raise_and_call(LuaError("Lua error: claude:1: boom", prints=["a", "b"])))
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]["text"], "Error: Lua error: claude:1: boom")
        self.assertTrue(content[1]["text"].startswith("print output:"))
        self.assertIn("a\nb", content[1]["text"])

    def test_lua_error_without_prints_has_one_block(self):
        content = self.assert_tool_error(self._raise_and_call(LuaError("Lua error: x")))
        self.assertEqual(len(content), 1)

    def test_unexpected_exception_becomes_internal_error(self):
        resp = self._raise_and_call(RuntimeError("secret detail /Users/someone/file.py"))
        self.assertNotIn("result", resp)
        self.assertEqual(resp["error"]["code"], -32603)
        self.assertEqual(resp["error"]["message"], "Internal error")
        self.assertNotIn("Traceback", resp["error"]["message"])
        self.assertNotIn("secret detail", resp["error"]["message"])
        self.assertEqual(resp["id"], 1)

    # -- 9. protocol errors and batches -------------------------------------------------------

    def test_unknown_method(self):
        resp = self.rpc("does/not_exist")
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertIn("does/not_exist", resp["error"]["message"])
        self.assertEqual(resp["id"], 1)

    def test_batch_returns_only_responses(self):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
        out = self.dispatcher.handle(batch)
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 2)
        self.assertEqual([r["id"] for r in out], [1, 2])

    def test_batch_of_only_notifications(self):
        batch = [
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled"},
        ]
        self.assertIsNone(self.dispatcher.handle(batch))

    def test_empty_batch(self):
        resp = self.dispatcher.handle([])
        self.assertEqual(resp["error"]["code"], -32600)

    def test_non_object_message(self):
        for bad in ("hello", 5, None):
            resp = self.dispatcher.handle(bad)
            self.assertEqual(resp["error"]["code"], -32600, repr(bad))
            self.assertIsNone(resp["id"])

    # -- 10. client responses -----------------------------------------------------------------

    def test_client_response_is_ignored(self):
        self.assertIsNone(self.dispatcher.handle({"jsonrpc": "2.0", "id": 7, "result": {}}))
        self.assertIsNone(self.dispatcher.handle(
            {"jsonrpc": "2.0", "id": 8, "error": {"code": -1, "message": "x"}}))


class ProcessTests(unittest.TestCase):
    def test_stdio_roundtrip(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "".join(json.dumps(m) + "\n" for m in lines).encode("utf-8")
        proc = subprocess.run(
            ["/usr/bin/python3", os.path.join(ROOT, "server.py")],
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], 1)
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "davinci")
        self.assertEqual(out[1]["id"], 2)
        names = [t["name"] for t in out[1]["result"]["tools"]]
        self.assertIn("run_lua", names)
        self.assertIn("davinci-mcp", proc.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
