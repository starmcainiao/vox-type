"""
cli/tests/test_exit_codes.py — 退出码矩阵（docs/08 §8.2）

卡的要求：2 / 3 / 4 / 5 各至少一条，且**必须断言具体码值**（`assertEqual(rc, 2)`），
不得只断言「非零」。本文件把五个码各钉成独立类，便于单独跑（如只跑 3 的路径）。

单独运行示例：
    python3 -m unittest cli.tests.test_exit_codes.TestExitCode3 -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from cli.errors import (
    EXIT_FAIL_CLOSED,
    EXIT_OK,
    EXIT_QUALITY,
    EXIT_RUNTIME,
    EXIT_USAGE,
    describe,
)
from cli.tests import (
    CliTestBase,
    TINY_SCRIPT_BAD_EXIT_JSON,
    assert_json_stdout,
    build_tiny_pack,
    make_tiny_pack,
    run_cli,
    write_json,
)


FROZEN_EXIT_CODES = {EXIT_OK, EXIT_USAGE, EXIT_RUNTIME, EXIT_QUALITY, EXIT_FAIL_CLOSED}

PLAN_HIT = [
    {"key": "greeting", "rate": "normal", "variant": "auto"},
    {"key": "closing", "rate": "normal"},
]


class _BuiltPackBase(CliTestBase):
    """共享一个预铸好的极小包 + 一份合法 plan（只读）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._shared = tempfile.mkdtemp(prefix="cli_exit_pack_")
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
        path = self.tmp / name
        write_json(path, data)
        return path


# ============================================================
# 0：成功
# ============================================================
class TestExitCode0(_BuiltPackBase):
    """命令完成且结论为「通过 / 已完成」→ 0。"""

    def test_run_ok_returns_0(self):
        """纯命中 plan → rc 0。"""
        plan = self.write_plan(PLAN_HIT)
        rc, stdout, _ = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 0)
        self.assertEqual(assert_json_stdout(stdout, self)["hit_count"], 2)


# ============================================================
# 2：用法或参数错误（配置期）
# ============================================================
class TestExitCode2(_BuiltPackBase):
    """缺子命令 / 缺必填参数 / 路径不存在 / JSON 解析失败 / 未知适配器 / 未知取值 → 2。"""

    def test_missing_subcommand_returns_2(self):
        """不给出命令 → rc 2，stderr 有说明。"""
        rc, _, err = run_cli([])

        self.assertEqual(rc, 2)
        self.assertNotEqual(err.strip(), "")

    def test_pack_without_subcommand_returns_2(self):
        """`vox pack` 不给子命令 → rc 2。"""
        rc, _, err = run_cli(["pack"])

        self.assertEqual(rc, 2)
        self.assertNotEqual(err.strip(), "")

    def test_missing_required_argument_returns_2(self):
        """`vox pack check` 缺 pack_dir → rc 2。"""
        rc, _, err = run_cli(["pack", "check"])

        self.assertEqual(rc, 2)
        self.assertIn("pack_dir", err)

    def test_missing_build_out_returns_2(self):
        """`vox pack build <src>` 缺 --out → rc 2。"""
        rc, _, err = run_cli(["pack", "build", str(self.src)])

        self.assertEqual(rc, 2)
        self.assertIn("--out", err)

    def test_path_not_exists_returns_2(self):
        """路径不存在 → rc 2，stderr 含该路径（可定位）。"""
        rc, _, err = run_cli(["pack", "check", str(self.tmp / "no_such_dir"), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("no_such_dir", err)

    def test_unknown_adapter_returns_2(self):
        """未知适配器 → rc 2，且不得回落到默认适配器。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--adapter", "no_such_module:NopeClass", "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("no_such_module", err)

    def test_invalid_duplex_value_returns_2(self):
        """--duplex '{"patience_ms":500}' → 取值不在白名单 → DuplexError → rc 2。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--duplex", '{"patience_ms":500}', "--json"]
        )

        self.assertEqual(rc, 2)
        self.assertIn("patience_ms", err)

    def test_plan_json_parse_failure_returns_2(self):
        """plan 文件不是合法 JSON → rc 2。"""
        plan = self.tmp / "broken.json"
        plan.write_text("{不是JSON", encoding="utf-8")
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("不是合法 JSON", err)

    def test_source_error_returns_2(self):
        """源格式非法（pack.json 未知字段）→ SourceError → rc 2。"""
        src = self.tmp / "bad_src"
        bad_pack = dict({"pack_id": "x", "pack_version": "1", "protocol_version": "0.1",
                         "ruleset_version": "v1", "voice": "Tingting",
                         "model_version": "macos-say", "rates": ["normal"], "duplx": {}})
        make_tiny_pack(src, pack=bad_pack)
        rc, _, err = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 2)
        self.assertIn("duplx", err)

    def test_bench_missing_required_args_returns_2(self):
        """vox bench 缺 --corpus / --out（T10 的「未实现」占位已由 T10b 替换）→ rc 2。"""
        rc, _, err = run_cli(["bench", str(self.pack_dir)])

        self.assertEqual(rc, 2)
        self.assertIn("--corpus", err)
        self.assertIn("--out", err)
        self.assertIn("bench", err)

    def test_verify_unknown_artifact_returns_2(self):
        """vox verify 遇到不认识的产物 → rc 2，stderr 说明支持的三类形态。"""
        txt = self.tmp / "notes.txt"
        txt.write_text("这不是产物", encoding="utf-8")
        rc, _, err = run_cli(["verify", str(txt)])

        self.assertEqual(rc, 2)
        self.assertIn("verify", err)
        for kind in ("asset_pack", "pack_source", "eval_report"):
            self.assertIn(kind, err, f"stderr 必须列出支持的形态 {kind}")


# ============================================================
# 3：运行期失败（执行期）—— stderr 必带异常类型名
# ============================================================
class TestExitCode3(_BuiltPackBase):
    """执行期抛出的未分类异常 → rc 3，stderr 必带 `type(exc).__name__`。

    复现方式：适配器能被动态解析并实例化，但不满足 Executor 的适配器契约
    （缺 synthesize / voice / model_version）→ Executor.__init__ 抛 TypeError。
    这是「解析成功、执行失败」的路径，与 rc 2 的「解析失败」严格分开。
    """

    def test_runtime_failure_returns_3(self):
        """执行期 TypeError → rc 3。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, _ = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--adapter", "core:ProtocolError", "--json"]
        )

        self.assertEqual(rc, 3)

    def test_runtime_failure_prints_exception_type_name(self):
        """rc 3 时 stderr 必带异常类型名（docs/08 §8.2 的硬要求）。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--adapter", "core:ProtocolError", "--json"]
        )

        self.assertEqual(rc, 3)
        self.assertIn("TypeError", err)
        # 类型名必须在最前面，便于脚本按 `: ` 切分
        self.assertTrue(err.strip().startswith("运行期失败: TypeError"))

    def test_runtime_failure_is_not_collapsed_into_2(self):
        """内部缺陷不得折叠成 2（否则调用者会去查命令行参数）。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, _ = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--adapter", "core:ProtocolError", "--json"]
        )

        self.assertNotEqual(rc, EXIT_USAGE)
        self.assertEqual(rc, EXIT_RUNTIME)

    def test_verbose_prints_traceback(self):
        """--verbose 时打完整 traceback（docs/08 §8.2）。"""
        plan = self.write_plan(PLAN_HIT)
        rc, _, err = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir),
             "--adapter", "core:ProtocolError", "--json", "--verbose"]
        )

        self.assertEqual(rc, 3)
        self.assertIn("Traceback (most recent call last)", err)


# ============================================================
# 4：质检不通过
# ============================================================
class TestExitCode4(_BuiltPackBase):
    """四属性有违规 / prebake 报 clean=False → rc 4（措辞中性，不是「错误」）。"""

    def test_pack_check_violation_returns_4(self):
        """剧本违规 → rc 4，passed=False 且 violations 非空。"""
        src = make_tiny_pack(self.tmp / "bad_src", script=TINY_SCRIPT_BAD_EXIT_JSON)
        rc, stdout, err = run_cli(["pack", "check", str(src), "--json"])

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["passed"], False)
        self.assertGreaterEqual(len(payload["violations"]), 1)
        self.assertIn("不通过", err)
        # 措辞中性：结论行是「不通过」而不是「错误」
        self.assertIn(describe(EXIT_QUALITY), err)
        self.assertNotIn("错误", err.splitlines()[-1])

    def test_pack_build_not_clean_returns_4(self):
        """会抛错的适配器 → clean=False → rc 4 且 failed 明细可取。"""
        src = make_tiny_pack(self.tmp / "bad_src")
        rc, stdout, err = build_tiny_pack(
            src, self.tmp / "built", adapter="core:ProtocolError"
        )

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["clean"], False)
        self.assertGreaterEqual(len(payload["failed"]), 1)
        self.assertIn("AttributeError", payload["failed"][0]["reason"])
        self.assertIn("不通过", err)


# ============================================================
# 5：fail-closed 中止
# ============================================================
class TestExitCode5(_BuiltPackBase):
    """未命中且未开降级 → rc 5（业务语义，不是程序崩溃）。"""

    def test_fail_closed_returns_5(self):
        """引用包内不存在的 key → rc 5，stderr 含 key 与 reason。"""
        plan = self.write_plan([{"key": "ghost_key", "rate": "normal"}])
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, 5)
        self.assertIn("ghost_key", err)
        self.assertIn("key_not_prebaked", err)

    def test_fail_closed_is_not_collapsed_into_3(self):
        """fail-closed 是业务结论（5），不是运行期崩溃（3）。"""
        plan = self.write_plan([{"key": "ghost_key", "rate": "normal"}])
        rc, _, err = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--json"])

        self.assertEqual(rc, EXIT_FAIL_CLOSED)
        self.assertNotEqual(rc, EXIT_RUNTIME)
        # 措辞中性：「已按 fail-closed 中止」，不写成「错误」
        self.assertIn("fail-closed", err)


# ============================================================
# 全局纪律
# ============================================================
class TestExitCodeDiscipline(_BuiltPackBase):
    """跨命令的退出码纪律。"""

    CASES = [
        ("成功-run", ["run", "PLAN", "--pack", "PACK", "--json"], EXIT_OK),
        ("缺子命令", [], EXIT_USAGE),
        ("缺必填参数", ["pack", "check"], EXIT_USAGE),
        ("路径不存在", ["pack", "check", "NOPE"], EXIT_USAGE),
        ("未知适配器", ["run", "PLAN", "--pack", "PACK", "--adapter", "nope:Nope", "--json"], EXIT_USAGE),
        ("bench 缺必填参数", ["bench", "PACK"], EXIT_USAGE),
        ("verify 缺必填参数", ["verify"], EXIT_USAGE),
    ]

    def _argv(self, raw: list, plan: Path) -> list:
        """把占位符 PLAN / PACK 替换成真实路径。"""
        return [
            str(plan) if a == "PLAN" else str(self.pack_dir) if a == "PACK" else a
            for a in raw
        ]

    def test_all_exits_are_inside_frozen_set(self):
        """任何路径的退出码都必须在 {0,2,3,4,5} 内（脚本化调用依赖它）。"""
        plan = self.write_plan(PLAN_HIT)
        for label, argv, expected in self.CASES:
            rc, _, _ = run_cli(self._argv(argv, plan))
            self.assertIn(rc, FROZEN_EXIT_CODES, f"{label} 的退出码 {rc} 不在冻结集合内")
            self.assertEqual(rc, expected, f"{label} 的退出码应为 {expected}，实际 {rc}")

    def test_every_nonzero_exit_has_stderr(self):
        """不静默：任何非零退出都必须有 stderr 一行说明（docs/08 §8.2）。"""
        ghost = self.write_plan([{"key": "ghost_key", "rate": "normal"}], "ghost.json")
        bad_src = make_tiny_pack(self.tmp / "bad_src", script=TINY_SCRIPT_BAD_EXIT_JSON)

        cases = [
            ("路径不存在", ["pack", "check", str(self.tmp / "nope")], EXIT_USAGE),
            ("bench 缺必填参数", ["bench", str(self.pack_dir)], EXIT_USAGE),
            ("fail-closed", ["run", str(ghost), "--pack", str(self.pack_dir), "--json"],
             EXIT_FAIL_CLOSED),
            ("质检不通过", ["pack", "check", str(bad_src), "--json"], EXIT_QUALITY),
            ("运行期失败", ["run", str(self.write_plan(PLAN_HIT, "hit.json")),
                       "--pack", str(self.pack_dir),
                       "--adapter", "core:ProtocolError", "--json"], EXIT_RUNTIME),
        ]

        for label, argv, expected in cases:
            rc, _, err = run_cli(argv)
            self.assertEqual(rc, expected, label)
            self.assertNotEqual(
                err.strip(), "", f"{label} 退出码 {rc} 但 stderr 为空（违反「不静默」）"
            )

    def test_json_stdout_is_always_pure_json_on_success(self):
        """--json 的成功路径 stdout 可被 json.loads 直接解析（脚本化契约）。"""
        plan = self.write_plan(PLAN_HIT)
        rc, stdout, _ = run_cli(["run", str(plan), "--pack", str(self.pack_dir), "--out",
                                 str(self.tmp / "o.wav"), "--json"])
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["command"], "run")
