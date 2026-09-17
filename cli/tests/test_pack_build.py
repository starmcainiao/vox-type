"""
cli/tests/test_pack_build.py — `vox pack build` 的验收用例

覆盖范围：
  - 成功预铸：rc 0 + total/synthesized/clean/validate_pack 字段齐全 + 产物真实落盘
  - --out 越界（docs/08 §8.5 欠账 2）：落在源目录内 → rc 2，含 `..` 逃逸
  - 先检后铸：剧本违规 → rc 4 且**不产出任何资产**
  - 适配器动态解析失败（未知模块 / 未知类 / 格式非法）→ rc 2，不回落
  - 会抛错的适配器 → rc 4 + clean=False + failed 明细可取

反空转：全部经 `run_cli` 调 `cli.main.main`；`--json` 断言真跑 json.loads。
"""

import json
import unittest
from pathlib import Path

from cli.tests import (
    CliTestBase,
    TINY_SCRIPT_BAD_EXIT_JSON,
    assert_json_stdout,
    build_tiny_pack,
    make_tiny_pack,
    run_cli,
)


# ============================================================
# 1. 成功路径
# ============================================================
class TestPackBuildSuccess(CliTestBase):
    """极小包（2 phrase × 1 variant × 1 rate = 2 条资产）预铸必须全绿。"""

    def test_build_tiny_pack_success(self):
        """rc 0，字段名照 docs/08 §8.3，validate_pack 为空，产物真实落盘。"""
        src = make_tiny_pack(self.tmp / "src")
        out = self.tmp / "built"

        rc, stdout, err = build_tiny_pack(src, out)

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertEqual(payload["command"], "pack.build")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["synthesized"], 2)
        self.assertEqual(payload["reused"], 0)
        self.assertEqual(payload["tts_calls"], 2)
        self.assertIs(payload["clean"], True)
        self.assertEqual(payload["failed"], [])
        self.assertEqual(payload["quality_issues"], [])
        self.assertEqual(payload["validate_pack"], [])
        self.assertEqual(payload["pack_dir"], str(out))
        self.assertIn("pack build: 通过", err)

        # 产物必须真的存在（不是只在 JSON 里写了个路径）
        self.assertTrue((out / "manifest.json").is_file(), "manifest.json 应当已写出")
        self.assertEqual(len(list((out / "audio").glob("*.wav"))), 2)

    def test_build_second_run_reuses_all(self):
        """差量复用：同输入二次预铸应全部 reused、零 TTS 调用（幂等）。"""
        src = make_tiny_pack(self.tmp / "src")
        out = self.tmp / "built"
        rc, _, _ = build_tiny_pack(src, out)
        self.assertEqual(rc, 0)

        rc, stdout, _ = build_tiny_pack(src, out)

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertEqual(payload["synthesized"], 0)
        self.assertEqual(payload["reused"], 2)
        self.assertEqual(payload["tts_calls"], 0)
        self.assertIs(payload["clean"], True)


# ============================================================
# 2. --out 越界拦截（docs/08 §8.5 欠账 2）
# ============================================================
class TestPackBuildOutEscaping(CliTestBase):
    """产物不许写进源目录：判据是解析后的绝对路径是否为子路径。"""

    def _src(self) -> Path:
        return make_tiny_pack(self.tmp / "src")

    def test_build_out_inside_source_dir_returns_2(self):
        """--out 直接落在源目录内 → rc 2。"""
        src = self._src()
        rc, _, err = build_tiny_pack(src, src / "sub")

        self.assertEqual(rc, 2)
        self.assertIn("--out", err)
        self.assertIn(str(src), err)

    def test_build_out_escaping_backslash_dotdot_returns_2(self):
        """--out 用 `..` 折回源目录内 → 解析后仍是子路径 → rc 2。"""
        src = self._src()
        rc, _, err = build_tiny_pack(src, src / "audio" / ".." / "sub")

        self.assertEqual(rc, 2)
        self.assertIn("--out", err)

    def test_build_out_equal_to_source_dir_returns_2(self):
        """--out 恰好等于源目录本身 → rc 2（不得原地覆盖源）。"""
        src = self._src()
        rc, _, err = build_tiny_pack(src, src)

        self.assertEqual(rc, 2)
        self.assertIn("--out", err)

    def test_build_out_outside_source_dir_ok(self):
        """--out 在源目录之外是合法的（对照正例，证明不是误杀）。"""
        src = self._src()
        rc, _, _ = build_tiny_pack(src, self.tmp / "elsewhere")

        self.assertEqual(rc, 0)


# ============================================================
# 3. 先检后铸：剧本违规 → 4 且不产出
# ============================================================
class TestPackBuildPrecheck(CliTestBase):
    """源不合规直接 rc 4——不得烧一次 TTS、不得铸出半成品。"""

    def test_build_violating_script_returns_4_without_prebaking(self):
        """末单元非终态 → rc 4，manifest 不写出，total 为 None（根本没跑）。"""
        src = make_tiny_pack(self.tmp / "src", script=TINY_SCRIPT_BAD_EXIT_JSON)
        out = self.tmp / "built"

        rc, stdout, err = build_tiny_pack(src, out)

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["passed"], False)
        self.assertIs(payload["clean"], False)
        self.assertIsNone(payload["total"])
        self.assertIsNone(payload["synthesized"])
        self.assertGreaterEqual(len(payload["violations"]), 1)
        self.assertIn("no_exit", {v["code"] for v in payload["violations"]})
        self.assertFalse((out / "manifest.json").exists(), "先检后铸：不得写出 manifest")
        self.assertIn("pack build: 不通过", err)


# ============================================================
# 4. 适配器动态解析失败 → 2（不回落）
# ============================================================
class TestPackBuildAdapterResolution(CliTestBase):
    """未知适配器一律 rc 2，且**不得回落到默认适配器**（静默降级红线）。"""

    def _src(self) -> Path:
        return make_tiny_pack(self.tmp / "src")

    def test_build_unknown_module_returns_2(self):
        """模块不存在 → rc 2，消息含模块名。"""
        rc, _, err = build_tiny_pack(
            self._src(), self.tmp / "built", adapter="no_such_module:Nope"
        )

        self.assertEqual(rc, 2)
        self.assertIn("no_such_module", err)

    def test_build_unknown_class_returns_2(self):
        """模块存在但类不存在 → rc 2，消息含类名。"""
        rc, _, err = build_tiny_pack(
            self._src(), self.tmp / "built", adapter="core:NoSuchClass"
        )

        self.assertEqual(rc, 2)
        self.assertIn("NoSuchClass", err)

    def test_build_malformed_adapter_spec_returns_2(self):
        """格式不是 <模块>:<类名> → rc 2。"""
        rc, _, err = build_tiny_pack(self._src(), self.tmp / "built", adapter="core_protocol")

        self.assertEqual(rc, 2)
        self.assertIn("adapters.tts_macsay:MacSayTts", err)

    def test_build_does_not_fall_back_on_unknown_adapter(self):
        """解析失败时不得用默认适配器偷偷跑完（否则就是静默降级）。"""
        rc, _, _ = build_tiny_pack(
            self._src(), self.tmp / "built", adapter="no_such_module:Nope"
        )

        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / "built" / "manifest.json").exists())


# ============================================================
# 5. 会抛错的适配器 → 4 + failed 明细
# ============================================================
class TestPackBuildFailingAdapter(CliTestBase):
    """合成失败必须出现在 failed[] 并带原因——不得静默跳过。

    用 `core:ProtocolError` 当适配器：它能被动态解析并实例化，但没有 synthesize，
    所以 prebake 的合成步骤抛 AttributeError 并被记进 failed——
    这条路径同时验证「CLI 不重写质检判定」（判定仍在 compiler.prebake 里）。
    """

    BAD_ADAPTER = "core:ProtocolError"

    def test_build_failing_adapter_returns_4_with_failed_detail(self):
        """rc 4、clean=False、failed 非空且 reason 含异常类型名。"""
        rc, stdout, err = build_tiny_pack(
            make_tiny_pack(self.tmp / "src"),
            self.tmp / "built",
            adapter=self.BAD_ADAPTER,
        )

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["clean"], False)
        self.assertIs(payload["passed"], False)
        self.assertEqual(payload["total"], 2)
        self.assertGreaterEqual(len(payload["failed"]), 1)
        self.assertIn("AttributeError", payload["failed"][0]["reason"])
        self.assertEqual(payload["failed"][0]["key"], "greeting")
        self.assertIn("pack build: 不通过", err)

    def test_build_allow_partial_still_reports_not_clean(self):
        """--allow-partial 只影响 prebake 是否抛错，不改变 clean=False → rc 4 的结论。"""
        rc, stdout, _ = build_tiny_pack(
            make_tiny_pack(self.tmp / "src"),
            self.tmp / "built",
            adapter=self.BAD_ADAPTER,
            allow_partial=True,
        )

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["clean"], False)
        self.assertGreaterEqual(len(payload["failed"]), 1)
