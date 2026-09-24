"""adapters.mcp_vox.tests.test_server — 真子进程 stdio JSON-RPC 握手测试（T46 §四.2）。

起 `python3 -m adapters.mcp_vox.server` 子进程，逐行喂 JSON-RPC 消息、逐行读响应：
initialize 三字段 / tools/list 三工具与 inputSchema / tools/call 命中·未命中·坏包路径 /
未知方法 -32601 / 一行一条消息（多行 JSON 不当两条消息）。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_DIR = str(REPO_ROOT / "examples" / "prebuilt-pack")
HIT_KEY = "transfer_ready"
HIT_TEXT = "已为您转接人工坐席，请稍候。"


class StdioSession:
    """一次真实 stdio 会话：起子进程、逐行收发。"""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c",
             "from adapters.mcp_vox.server import main; raise SystemExit(main())"],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._next_id = 0

    def send_raw(self, text):
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def read_line(self):
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("服务端未返回响应行（进程可能已退出）")
        return json.loads(line)

    def request(self, method, params=None):
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send_raw(json.dumps(message, ensure_ascii=False))
        return self.read_line()

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    @staticmethod
    def payload(response):
        return json.loads(response["result"]["content"][0]["text"])

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.session = StdioSession()
        self.addCleanup(self.session.close)


class TestInitialize(ServerTestCase):
    def test_initialize_three_fields(self):
        result = self.session.request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {},
             "clientInfo": {"name": "test", "version": "0"}},
        )["result"]
        self.assertIn("protocolVersion", result)
        self.assertIn("tools", result["capabilities"])
        self.assertIn("name", result["serverInfo"])


class TestToolsList(ServerTestCase):
    def test_three_tools_with_input_schema(self):
        tools = self.session.request("tools/list")["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        self.assertEqual(
            set(by_name), {"vox_lookup", "vox_plan", "vox_pack_check"}
        )
        for name, tool in by_name.items():
            self.assertIn("description", tool)
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object", name)
            self.assertIn("properties", schema)
            self.assertIn("pack_dir", schema["properties"], name)
        self.assertIn("keys", by_name["vox_plan"]["inputSchema"]["properties"])


class TestVoxLookup(ServerTestCase):
    def test_hit_has_fields_and_no_audio(self):
        response = self.session.call(
            "vox_lookup", {"pack_dir": PACK_DIR, "key": HIT_KEY}
        )
        self.assertNotEqual(response["result"].get("isError"), True)
        payload = self.session.payload(response)
        self.assertEqual(
            set(payload),
            {"hit", "key", "rate_key", "variant", "duration_ms", "model_version"},
        )
        self.assertIs(payload["hit"], True)
        self.assertEqual(payload["key"], HIT_KEY)
        self.assertNotIn("path", payload)
        self.assertNotIn("audio", payload)
        self.assertNotIn("data", payload)
        text = response["result"]["content"][0]["text"]
        self.assertNotIn("RIFF", text)

    def test_hit_by_text(self):
        response = self.session.call(
            "vox_lookup", {"pack_dir": PACK_DIR, "text": HIT_TEXT}
        )
        self.assertIs(self.session.payload(response)["hit"], True)

    def test_miss_is_not_error(self):
        response = self.session.call(
            "vox_lookup", {"pack_dir": PACK_DIR, "key": "no_such_key_xxx"}
        )
        self.assertNotEqual(response["result"].get("isError"), True)
        payload = self.session.payload(response)
        self.assertIs(payload["hit"], False)
        self.assertIn("reason", payload)

    def test_bad_pack_path_is_error(self):
        response = self.session.call(
            "vox_lookup", {"pack_dir": str(REPO_ROOT / "no_such_pack_xxx")}
        )
        self.assertIs(response["result"]["isError"], True)
        self.assertIn("error", self.session.payload(response))


class TestVoxPlan(ServerTestCase):
    def test_all_hit(self):
        response = self.session.call(
            "vox_plan", {"pack_dir": PACK_DIR, "keys": [HIT_KEY, "farewell"]}
        )
        self.assertNotEqual(response["result"].get("isError"), True)
        payload = self.session.payload(response)
        self.assertIs(payload["all_hit"], True)
        self.assertEqual(payload["misses"], [])
        self.assertEqual([item["key"] for item in payload["plan"]],
                         [HIT_KEY, "farewell"])

    def test_miss_listed(self):
        response = self.session.call(
            "vox_plan", {"pack_dir": PACK_DIR, "keys": [HIT_KEY, "nope_xxx"]}
        )
        payload = self.session.payload(response)
        self.assertIs(payload["all_hit"], False)
        self.assertEqual(payload["misses"], ["nope_xxx"])
        self.assertEqual(len(payload["plan"]), 1)


class TestVoxPackCheck(ServerTestCase):
    def test_good_pack_passes(self):
        response = self.session.call("vox_pack_check", {"pack_dir": PACK_DIR})
        self.assertNotEqual(response["result"].get("isError"), True)
        payload = self.session.payload(response)
        self.assertIs(payload["passed"], True)
        self.assertIn(HIT_KEY, payload["keys"])
        self.assertEqual(payload["violations"], [])

    def test_bad_pack_path_is_error_with_violations(self):
        response = self.session.call(
            "vox_pack_check", {"pack_dir": str(REPO_ROOT / "no_such_pack_xxx")}
        )
        self.assertIs(response["result"]["isError"], True)
        payload = self.session.payload(response)
        self.assertIs(payload["passed"], False)
        self.assertTrue(payload["violations"])


class TestProtocolEdges(ServerTestCase):
    def test_unknown_method_returns_32601(self):
        response = self.session.request("resources/list")
        self.assertEqual(response["error"]["code"], -32601)

    def test_unknown_notification_is_silent(self):
        self.session.send_raw(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ))
        self.session.send_raw(json.dumps(
            {"jsonrpc": "2.0", "method": "no/such/method"}
        ))
        response = self.session.request("tools/list")
        self.assertIn("result", response)

    def test_one_message_per_line_multiline_json_not_two_messages(self):
        # 一个跨 4 行的 JSON 文本：**不是**一条消息，而是 4 行各自不成消息 →
        # 4 条 -32700；随后正常请求仍能对上号（证明没有被拼成/串读）。
        self.session.proc.stdin.write(
            '{\n "jsonrpc": "2.0",\n "id": 901,\n "method": "tools/list"\n}\n'
        )
        self.session.proc.stdin.flush()
        for _ in range(5):
            first = self.session.read_line()
            self.assertEqual(first["error"]["code"], -32700)
            self.assertIsNone(first["id"])
        response = self.session.request("initialize")
        self.assertEqual(response["id"], 1)

    def test_two_messages_on_two_lines(self):
        self.session.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/list"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 12, "method": "tools/list"})
            + "\n"
        )
        self.session.proc.stdin.flush()
        self.assertEqual(self.session.read_line()["id"], 11)
        self.assertEqual(self.session.read_line()["id"], 12)


if __name__ == "__main__":
    unittest.main()
