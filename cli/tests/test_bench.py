"""
cli/tests/test_bench.py — `vox bench` 的验收用例（docs/08 §8.6）

覆盖：
  1. 成功路径 rc 0 + `--json` 是纯 JSON + report.json 真写出 + incomplete is False
  2. `arms` 逐字段透传（CLI 不重算任何指标；JSON 顶层字段集照 §8.6 冻结）
  3. --repeats 低于 MIN_REPEATS → rc 2 且不产出任何文件
  4. 包缺 key → incomplete is True → rc 4 且**报告照样写出**
  5. --out 落在 --pack 内 → rc 2 且包目录未被写入（不得静默写进冻结区）
  6. --adapter 解析失败 → rc 2 且不回落
  7. 不带 --json 时 stdout 是人读摘要（不是 JSON）
  8. 语料文件不存在 / 非 JSON → rc 2

反空转：全部经 run_cli 调 cli.main.main；`--json` 断言真跑 json.loads；
      不在测试里复制统计或判定逻辑——对拍全部走 eval.run_bench。
"""

import json
import shutil
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


# 语料 key 必须与 TINY_PACK_JSON 的 phrases 对齐（fast 臂才可能命中）
CORPUS_UNITS = [
    {"key": "greeting", "text": "您好，请问需要什么帮助。", "rate": "normal", "variant": 0},
    {"key": "closing", "text": "感谢您的来电。", "rate": "normal", "variant": 0},
]

# §8.6 冻结的 --json 顶层字段集：多一个就是 CLI 自己算了指标
BENCH_JSON_KEYS = {
    "command", "report_path", "incomplete", "repeats", "seed", "arms",
    "timing_metrics_meaningful",
}


def write_corpus(path: Path, units=None) -> Path:
    write_json(path, {"corpus_id": "cli-bench-corpus", "version": 1,
                      "units": units if units is not None else CORPUS_UNITS})
    return path


class BenchCliBase(CliTestBase):
    """共享夹具：极小包预铸产物 + 一份对齐的语料。

    包与语料按类建一次（每条用例都会预铸一次真机 `say`，逐条重建太慢）；
    用例不得修改 pack_dir（所有写操作都走各自的 --out）。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._shared = Path(tempfile.mkdtemp(prefix="cli_bench_shared_"))
        src = make_tiny_pack(cls._shared / "src")
        cls.pack_dir = cls._shared / "built"
        rc, _, err = build_tiny_pack(src, cls.pack_dir)
        assert rc == 0, f"预铸夹具失败: {err}"
        cls.corpus = write_corpus(cls._shared / "corpus.json")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared, ignore_errors=True)
        super().tearDownClass()

    def bench_argv(self, *, repeats=20, out=None, corpus=None, extra=(),
                   json_flag=True) -> list:
        argv = [
            "bench", str(self.pack_dir),
            "--corpus", str(corpus if corpus is not None else self.corpus),
            "--out", str(out if out is not None else self.tmp / "bench-out"),
            "--repeats", str(repeats),
        ]
        argv += list(extra)
        if json_flag:
            argv.append("--json")
        return argv


# ============================================================
# 1~2. 成功路径与透传
# ============================================================
class TestBenchSuccess(BenchCliBase):
    """rc 0 + 报告真写出 + `arms` 直接透传（不得重算）。"""

    def test_success_exit_0_and_json_contract(self):
        rc, stdout, err = run_cli(self.bench_argv())

        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertEqual(payload["command"], "bench")
        self.assertIs(payload["incomplete"], False)
        self.assertEqual(payload["repeats"], 20)
        self.assertIsInstance(payload["seed"], int)
        self.assertTrue((Path(payload["report_path"])).is_file(),
                        "report_path 指到的 report.json 必须真的存在")
        self.assertIn("bench: 通过", err)

    def test_json_top_level_keys_frozen(self):
        """顶层字段集照 §8.6 冻结——多出来一个字段就说明 CLI 自己算了指标。"""
        rc, stdout, _ = run_cli(self.bench_argv())
        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertEqual(set(payload), BENCH_JSON_KEYS)

    def test_arms_are_verbatim_passthrough(self):
        """`arms` 必须与 report.json 里的同名块逐字段相等（证明是透传不是重算）。"""
        rc, stdout, _ = run_cli(self.bench_argv())
        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        report = json.loads(Path(payload["report_path"]).read_text(encoding="utf-8"))

        self.assertFalse(report["incomplete"])
        self.assertEqual(payload["arms"], report["arms"], "arms 不是报告里的同名块")
        self.assertEqual(payload["repeats"], report["repeats"])
        self.assertEqual(payload["seed"], report["seed"])
        self.assertEqual(
            payload["timing_metrics_meaningful"], report["timing_metrics_meaningful"]
        )
        # 两臂必须在场（对拍的本质就是两臂差值）
        self.assertEqual(set(payload["arms"]), {"fast", "slow"})

    def test_offline_standin_marked_not_meaningful(self):
        """缺省适配器是离线替身 → 时序数字不得被报告背书为真机数据。"""
        rc, stdout, _ = run_cli(self.bench_argv())
        self.assertEqual(rc, 0)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["timing_metrics_meaningful"], False)

    def test_without_json_flag_stdout_is_summary(self):
        """不带 --json 时 stdout 是人读摘要，JSON 走 stderr（stdout/stderr 分工）。"""
        rc, stdout, err = run_cli(self.bench_argv(json_flag=False))
        self.assertEqual(rc, 0)
        assert_not_json(stdout, self)
        self.assertIn("bench: 通过", stdout)
        self.assertEqual(json.loads(err)["command"], "bench")


# ============================================================
# 3. 参数类失败 → rc 2
# ============================================================
class TestBenchUsageErrors(BenchCliBase):
    """配置期失败一律 rc 2，且不得产出任何文件。"""

    def test_repeats_below_min_returns_2(self):
        out = self.tmp / "out-low"
        rc, _, err = run_cli(self.bench_argv(repeats=5, out=out))
        self.assertEqual(rc, 2)
        self.assertIn("MIN_REPEATS", err)
        self.assertIn("5", err)
        self.assertFalse(out.exists(), "参数错不得建输出目录")

    def test_out_inside_pack_returns_2_and_writes_nothing(self):
        """--out 落在 --pack 内 → rc 2，且包目录（冻结区）未被写入。"""
        inside = self.pack_dir / "sub"
        rc, _, err = run_cli(self.bench_argv(out=inside))
        self.assertEqual(rc, 2)
        self.assertIn("--out", err)
        self.assertFalse(inside.exists(), "抛错前不得建目录")
        self.assertFalse((self.pack_dir / "report.json").exists())
        self.assertFalse((self.pack_dir / "raw").exists())

    def test_missing_corpus_returns_2(self):
        rc, _, err = run_cli(self.bench_argv(corpus=self.tmp / "no_such_corpus.json"))
        self.assertEqual(rc, 2)
        self.assertIn("no_such_corpus.json", err)

    def test_invalid_corpus_json_returns_2(self):
        bad = self.tmp / "broken_corpus.json"
        bad.write_text("{不是JSON", encoding="utf-8")
        rc, _, err = run_cli(self.bench_argv(corpus=bad))
        self.assertEqual(rc, 2)
        self.assertIn("broken_corpus.json", err)

    def test_unknown_adapter_returns_2_no_fallback(self):
        """显式给的 --adapter 解析失败 → rc 2，绝不回落成默认引擎。"""
        rc, _, err = run_cli(self.bench_argv(extra=["--adapter", "nope:NopeClass"]))
        self.assertEqual(rc, 2)
        self.assertIn("nope", err)

    def test_missing_pack_returns_2(self):
        argv = self.bench_argv()
        argv[1] = str(self.tmp / "no_such_pack")
        rc, _, err = run_cli(argv)
        self.assertEqual(rc, 2)
        self.assertIn("no_such_pack", err)


# ============================================================
# 4. 业务结论 → rc 4（报告照样写出）
# ============================================================
class TestBenchIncomplete(BenchCliBase):
    """incomplete 是「数据不完整」的结论，不是崩溃：rc 4 + 报告必须写出。"""

    def test_pack_missing_key_returns_4_and_writes_report(self):
        missing_corpus = write_corpus(
            self.tmp / "missing_corpus.json",
            units=[
                CORPUS_UNITS[0],
                {"key": "ghost_key", "text": "这句不在话术库里。",
                 "rate": "normal", "variant": 0},
            ],
        )
        out = self.tmp / "out-incomplete"
        rc, stdout, err = run_cli(self.bench_argv(corpus=missing_corpus, out=out))

        self.assertEqual(rc, 4)
        payload = assert_json_stdout(stdout, self)
        self.assertIs(payload["incomplete"], True)
        report = json.loads(Path(payload["report_path"]).read_text(encoding="utf-8"))
        self.assertTrue(report["incomplete"], "报告必须如实标注 incomplete")
        self.assertTrue(report["incomplete_reasons"], "incomplete 必须给原因（不得美化）")
        blob = " | ".join(report["incomplete_reasons"])
        self.assertIn("ghost_key", blob, "原因必须点出未命中的 key")
        self.assertIn("fail-closed", err, "4 的措辞要中性且说明 fail-closed")
        # 原始数据照样落盘（否则事后无法定位是哪次、什么错）
        self.assertTrue((out / "raw").is_dir())
        self.assertTrue((out / "report.json").is_file())
