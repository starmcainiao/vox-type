"""
cli/tests/test_run.py — `vox run` 的验收用例

覆盖范围：
  - 纯命中 plan：rc 0 + hit_count/miss_count/tts_calls 字段 + out_path 真实存在
  - 未命中且未开降级：rc 5（fail-closed），stderr 含 key 与 reason，**不写输出文件**
  - 未命中且开 --allow-fallback：rc 0 且 miss_count=1、tts_calls=1
  - --out 落在 --pack 包目录内 → rc 2（含 `..` 逃逸）
  - --duplex 非法（非 JSON / 非对象 / 取值越界）→ rc 2；合法取值 → rc 0
  - plan 协议非法 / 文件非法 JSON / 包目录不存在 → rc 2
  - --json 的 stdout/stderr 分工

夹具：一个极小包在 setUpClass 预铸一次，所有用例共享（run 只读包，不修改它）。
反空转：全部经 `run_cli` 调 `cli.main.main`；`--json` 断言真跑 json.loads。
"""

import json
import tempfile
import unittest
from pathlib import Path

from cli.tests import (
    CliTestBase,
    assert_json_stdout,
    assert_not_json,
    build_tiny_pack,
    make_tiny_pack,
    run_cli,
    write_json,
)


# 卡里给的两份 plan（key 取自极小包夹具，语义与验收口径一致）
PLAN_HIT = [
    {"key": "greeting", "rate": "normal", "variant": "auto"},
    {"key": "closing", "rate": "normal"},
]
PLAN_LIVE = [
    {"key": "greeting", "rate": "normal", "variant": "auto"},
    {
        "action": "SAY_LIVE",
        "text": "不好意思，刚才没听清，请您再说一次要报修的设备。",
        "rate": "normal",
    },
]
PLAN_GHOST_KEY = [{"key": "ghost_key", "rate": "normal"}]


class RunTestBase(CliTestBase):
    """共享一个预铸好的极小包（只读）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._shared = tempfile.mkdtemp(prefix="cli_run_pack_")
        cls.src = Path(cls._shared) / "src"
        make_tiny_pack(cls.src)
        cls.pack_dir = Path(cls._shared) / "built"
        rc, _, err = build_tiny_pack(cls.src, cls.pack_dir, as_json=False)
        if rc != 0:
            raise RuntimeError(f"夹具预铸失败: rc={rc} stderr={err}")

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls._shared, ignore_errors=True)
        super().tearDownClass()

    def write_plan(self, data, name="plan.json"):
        """把 plan 写到临时目录并返回路径（测试不进仓、不写 packs/**）。"""
        path = self.tmp / name
        write_json(path, data)
        return path


class TestRunHitPath(RunTestBase):
    """命中路径：零 TTS 调用，直接播包内音频。"""

    def test_run_hit_plan_returns_0(self):
        """rc 0，hit_count=2 / miss_count=0 / tts_calls=0，out_path 真实存在。"""
        plan = self.write_plan(PLAN_HIT)
        out = self.tmp / "out" / "run.wav"

        rc, stdout, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--out", str(out), "--json"])

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertEqual(payload["command"], "run")
        self.assertEqual(payload["hit_count"], 2)
        self.assertEqual(payload["miss_count"], 0)
        self.assertEqual(payload["fallback_count"], 0)
        self.assertEqual(payload["tts_calls"], 0)
        self.assertEqual(payload["out_path"], str(out))
        self.assertEqual(len(payload["events"]), 2)
        self.assertTrue(Path(payload["out_path"]).is_file(), "输出 WAV 应当真实存在")
        self.assertGreater(payload["total_duration_ms"], 0)
        self.assertIn("run: 通过", err)

    def test_run_default_out_without_flag(self):
        """不带 --out 时落临时目录，产物仍存在。"""
        plan = self.write_plan(PLAN_HIT)
        rc, stdout, _ = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertTrue(Path(payload["out_path"]).is_file())

    def test_run_hit_plan_without_json_flag(self):
        """不带 --json：stdout 是人读摘要（非 JSON），JSON 走 stderr。"""
        plan = self.write_plan(PLAN_HIT)
        rc, stdout, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir)])

        self.assertEqual(rc, 0)
        self.assertIn("run: 通过", stdout)
        assert_not_json(stdout, self)
        self.assertEqual(json.loads(err)["hit_count"], 2)


class TestRunFailClosed(RunTestBase):
    """未命中且未开降级 → 退出码 5，且绝不写输出文件。"""

    def test_run_missing_key_returns_5(self):
        """引用包内不存在的 key → rc 5，stderr 含 key 与 reason。"""
        plan = self.write_plan(PLAN_GHOST_KEY)
        out = self.tmp / "out" / "run.wav"

        rc, stdout, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--out", str(out), "--json"])

        self.assertEqual(rc, 5)
        self.assertIn("ghost_key", err)
        self.assertIn("key_not_prebaked", err)
        self.assertFalse(Path(out).exists(), "fail-closed 不得写输出文件")
        payload = assert_json_stdout(stdout, self)
        self.assertIn("ghost_key", payload["aborted"])

    def test_run_say_live_without_fallback_returns_5(self):
        """SAY_LIVE 自由文本没有资产 → 设计上未命中 → rc 5。"""
        plan = self.write_plan(PLAN_LIVE)

        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 5)
        self.assertIn("say_live_text", err)
        self.assertIn("fail-closed", err)

    def test_run_say_live_with_fallback_returns_0(self):
        """开 --allow-fallback：rc 0，miss_count=1，现场合成 1 次。"""
        plan = self.write_plan(PLAN_LIVE)
        out = self.tmp / "out" / "fallback.wav"

        rc, stdout, _ = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir), "--out", str(out),
             "--allow-fallback", "--json"]
        )

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertEqual(payload["hit_count"], 1)
        self.assertEqual(payload["miss_count"], 1)
        self.assertEqual(payload["fallback_count"], 0)
        self.assertEqual(payload["tts_calls"], 1)
        self.assertTrue(Path(payload["out_path"]).is_file())
        # 事件流里必须留痕（含 reason）
        live_event = [e for e in payload["events"] if e.get("miss")]
        self.assertEqual(len(live_event), 1)
        self.assertEqual(live_event[0]["reason"], "say_live_text")


class TestRunOutputEscaping(RunTestBase):
    """--out 不许写进 --pack 包目录（冻结区）。"""

    def test_run_out_inside_pack_dir_returns_2(self):
        """--out 直接落在包目录内 → rc 2。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir), "--out", str(self.pack_dir / "x.wav"), "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("--out", err)
        self.assertIn(str(self.pack_dir), err)

    def test_run_out_with_dotdot_returns_2(self):
        """--out 用 `..` 折回包目录内 → 解析后仍是子路径 → rc 2。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--out", str(self.pack_dir / "audio" / ".." / "x.wav"), "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("--out", err)


class TestRunDuplex(RunTestBase):
    """--duplex：取值校验在 runtime.DuplexParams，CLI 只负责翻译错误。"""

    def test_run_valid_duplex_ok(self):
        """合法取值（1800 是耐心窗三档之一）→ rc 0。"""
        plan = self.write_plan(PLAN_HIT)
        rc, stdout, _ = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--duplex", '{"patience_ms": 1800}', "--json"]
        )

        self.assertEqual(rc, 0)
        self.assertEqual(assert_json_stdout(stdout, self)["hit_count"], 2)

    def test_run_invalid_duplex_value_returns_2(self):
        """patience_ms=500 不在三档白名单 → DuplexError → rc 2，消息含字段名。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--duplex", '{"patience_ms":500}', "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("patience_ms", err)
        self.assertIn("500", err)

    def test_run_invalid_duplex_json_returns_2(self):
        """--duplex 不是合法 JSON → rc 2。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir), "--duplex", "not-json", "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("--duplex", err)

    def test_run_duplex_not_object_returns_2(self):
        """--duplex 是数组而非对象 → rc 2（不静默当成空对象）。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir), "--duplex", "[1,2]", "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("JSON 对象", err)


class TestRunUsageErrors(RunTestBase):
    """配置期错误一律 rc 2。"""

    def test_run_missing_pack_returns_2(self):
        """--pack 目录不存在 → rc 2，stderr 含该路径。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.tmp / "nope"), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("nope", err)

    def test_run_source_dir_as_pack_returns_2(self):
        """把源目录当包用（没有 manifest.json）→ AssetPackError → rc 2。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.src), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("manifest.json", err)

    def test_run_bad_plan_rate_returns_2(self):
        """plan 单元 rate 非法 → ProtocolError → rc 2，消息含该值（不回落 normal）。"""
        plan = self.write_plan([{"key": "greeting", "rate": "turbo"}])
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("turbo", err)

    def test_run_malformed_plan_json_returns_2(self):
        """plan 文件不是合法 JSON → rc 2。"""
        plan = self.tmp / "broken.json"
        plan.write_text("{这不是JSON", encoding="utf-8")
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("不是合法 JSON", err)

    def test_run_empty_plan_returns_2(self):
        """空 plan → ProtocolError → rc 2（不静默当成空播报）。"""
        plan = self.write_plan([])
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("plan", err)


if __name__ == "__main__":
    unittest.main()
