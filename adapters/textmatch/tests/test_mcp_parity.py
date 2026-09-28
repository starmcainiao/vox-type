"""
adapters.textmatch.tests.test_mcp_parity — 两入口同判（走 MCP 工具分派入口）

T47 的核心判据：同一句话在 `framework_kefu` 桥与 `mcp_vox` 控制面必须**判出同一个结果**。
本文件刻意**走 `adapters.mcp_vox.tools.call_tool` 的分派入口**（而不是直接调
`_find_entry`）——卡要求验证的是「MCP 客户端真实收到的那份 JSON」，不是某个内部函数。

原始分歧例：包内 text = `价格①`、查询传入 `价格1`
    旧 MCP：裸 `entry.text == text` → False → 判未命中，且**无留痕**（纪律 4 的静默降级）
    kefu ：normalize_text 两边都 → `价格1` → True → 判命中
"""

import json
import tempfile
import unittest
from pathlib import Path

from adapters.mcp_vox import tools
from adapters.textmatch import find_hit

from adapters.textmatch.tests import build_pack

# 包内用「①」，查询用「1」：这两者只在 docs/10 §10.3 第 ③ 步 NFKC 之后才相等
PACK_TEXT = "价格①"
QUERY_TEXT = "价格1"
KEY = "price"


class McpParityBase(unittest.TestCase):
    """临时造一个含 `价格①` 的包，拆出 pack_dir（给 MCP）与 pack（给 kefu 口径）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="textmatch-mcp-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.pack = build_pack(self.tmp / "pack", [(KEY, PACK_TEXT, 0)])
        self.pack_dir = str(self.tmp / "pack")

    def call(self, name, arguments):
        """走 MCP 工具分派入口，拆出 (payload, isError)。"""
        result = tools.call_tool(name, arguments)
        payload = json.loads(result["content"][0]["text"])
        return payload, result.get("isError", False)

    def lookup(self, **kwargs):
        return self.call("vox_lookup", {"pack_dir": self.pack_dir, **kwargs})


class TestMcpUsesNormalizedHit(McpParityBase):
    """MCP 侧：半角查询命中全角包内文本。"""

    def test_halfwidth_query_hits_fullwidth_pack_entry(self):
        payload, is_error = self.lookup(text=QUERY_TEXT)
        self.assertNotEqual(is_error, True, payload)
        self.assertIs(payload["hit"], True, payload)
        self.assertEqual(payload["key"], KEY)

    def test_identical_text_still_hits(self):
        payload, is_error = self.lookup(text=PACK_TEXT)
        self.assertIs(payload["hit"], True, payload)

    def test_key_path_still_hits(self):
        payload, is_error = self.lookup(key=KEY)
        self.assertNotEqual(is_error, True, payload)
        self.assertIs(payload["hit"], True, payload)

    def test_genuine_miss_is_not_error(self):
        payload, is_error = self.lookup(text="这句话不在包里。")
        self.assertNotEqual(is_error, True, payload)
        self.assertIs(payload["hit"], False, payload)
        self.assertIn("reason", payload)


class TestTwoEntriesAgree(McpParityBase):
    """同一输入，MCP 入口与 textmatch 口径必须给出同一结论。"""

    def test_hit_agrees_with_textmatch(self):
        payload, _ = self.lookup(text=QUERY_TEXT)
        hit = find_hit(self.pack, text=QUERY_TEXT)
        self.assertEqual(payload["hit"], hit.entry is not None)

    def test_miss_agrees_with_textmatch(self):
        payload, _ = self.lookup(text="这句话不在包里。")
        hit = find_hit(self.pack, text="这句话不在包里。")
        self.assertEqual(payload["hit"], hit.entry is not None)
        self.assertIsNone(hit.entry)


class TestNoGuessingWhichChannel(McpParityBase):
    """key / text 同时给 → 报错，不猜（沿用 find_hit 的冻结契约）。"""

    def test_both_key_and_text_is_error_not_silent_pick(self):
        payload, is_error = self.lookup(key=KEY, text=QUERY_TEXT)
        self.assertIs(is_error, True, payload)
        self.assertIn("error", payload)

    def test_neither_key_nor_text_is_error(self):
        payload, is_error = self.lookup()
        self.assertIs(is_error, True, payload)
        self.assertIn("error", payload)


class TestPlanGoesThroughSameJudgment(McpParityBase):
    """vox_plan 走同一条判定，不许另开一份。"""

    def test_plan_hit_and_miss(self):
        payload, is_error = self.call(
            "vox_plan", {"pack_dir": self.pack_dir, "keys": [KEY, "nope_xxx"]}
        )
        self.assertNotEqual(is_error, True, payload)
        self.assertIs(payload["all_hit"], False)
        self.assertEqual(payload["misses"], ["nope_xxx"])
        self.assertEqual([item["key"] for item in payload["plan"]], [KEY])


if __name__ == "__main__":
    unittest.main()
