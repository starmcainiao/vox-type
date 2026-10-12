"""adapters.mcp_vox.tests.test_pack_check_hint — vox_pack_check 指向包源目录时的指路提示（T88）。

正例：`packs/heat_kefu`（有 phrases.json、无 manifest.json）→ 返回文本同时含
    `pack build` 与 `manifest.json`，且 `isError` 仍为 `true`。
负例（不得误伤）：`examples/prebuilt-pack`（正常资产包）→ passed:true / violations:[] / 无提示。
负例（不得误报）：既无 manifest.json 也无 phrases.json 的目录 → 只说「缺少 manifest.json」、无提示。

判定逻辑全在产品代码（`adapters.mcp_vox.tools._pack_check`）里，本文件只调
`tools.call_tool` 并断言返回，不复制任何判定。
"""

import json
import tempfile
import unittest
from pathlib import Path

from adapters.mcp_vox import tools

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_SOURCE_DIR = str(REPO_ROOT / "packs" / "heat_kefu")
PREBUILT_PACK_DIR = str(REPO_ROOT / "examples" / "prebuilt-pack")

HINT_MARKERS = ("pack build", "manifest.json")


def _call_pack_check(pack_dir):
    """走产品 API 调一次工具，返回 (原始 text, 解析后的 payload, isError)。"""
    result = tools.call_tool("vox_pack_check", {"pack_dir": pack_dir})
    text = result["content"][0]["text"]
    return text, json.loads(text), result["isError"]


class TestPackSourceDirHint(unittest.TestCase):
    """正例：指向包源目录要指路。"""

    def test_pack_source_dir_hint_lists_build_command(self):
        text, payload, is_error = _call_pack_check(PACK_SOURCE_DIR)
        self.assertIn("pack build", text)
        self.assertIn("manifest.json", text)
        self.assertIn(tools.PACK_SOURCE_HINT, payload["violations"])

    def test_pack_source_dir_stays_error_and_unpassed(self):
        _, payload, is_error = _call_pack_check(PACK_SOURCE_DIR)
        # 提示是「追加」：不改原有违规项，也不改 passed / isError 语义
        self.assertIs(payload["passed"], False)
        self.assertIs(is_error, True)
        self.assertIn("缺少 manifest.json", payload["violations"])
        self.assertEqual(payload["violations"][-1], tools.PACK_SOURCE_HINT)


class TestNormalPackUnaffected(unittest.TestCase):
    """负例（不得误伤）：正常资产包不得出现包源提示。"""

    def test_prebuilt_pack_passes_without_hint(self):
        text, payload, is_error = _call_pack_check(PREBUILT_PACK_DIR)
        self.assertIs(payload["passed"], True)
        self.assertEqual(payload["violations"], [])
        self.assertIs(is_error, False)
        self.assertNotIn(tools.PACK_SOURCE_HINT, text)
        self.assertNotIn("pack build", text)

    def test_nonexistent_dir_gets_no_hint(self):
        """路径不存在 → 两项皆 False，不该被误判成包源目录。"""
        text, _, _ = _call_pack_check(str(REPO_ROOT / "no_such_pack_xxx"))
        self.assertNotIn(tools.PACK_SOURCE_HINT, text)
        self.assertNotIn("pack build", text)


class TestBareDirNoFalsePositive(unittest.TestCase):
    """负例（不得误报）：既没 manifest.json 也没 phrases.json 的目录，只说缺 manifest.json。"""

    def test_bare_dir_only_reports_missing_manifest(self):
        with tempfile.TemporaryDirectory() as bare:
            text, payload, is_error = _call_pack_check(bare)
        self.assertNotIn(tools.PACK_SOURCE_HINT, text)
        self.assertNotIn("pack build", text)
        self.assertIn("缺少 manifest.json", text)
        self.assertIs(payload["passed"], False)
        self.assertIs(is_error, True)


if __name__ == "__main__":
    unittest.main()