"""
eval.tests.test_report — 报告组装 / 落盘 / 原始数据核对 / 人读摘要

覆盖（对应 T08 验收 4~7 与反空转条款）：
  1. build_report 产出冻结 schema：schema_version + 全部顶层字段齐备
  2. caliber 每条含公式 + 设计依据，且指标键来自 core.metrics_spec 常量
  3. 不得美化：incomplete=True 无原因 → ValueError；incomplete=False 有原因 → ValueError
  4. assess_completeness 五条规则逐条可触发（样本不足 / fail-closed / 执行异常 /
     事件流不完整 / 确定性被破坏 / 原始文件缺失 / 行数不符 / index 不连续）
  5. write_report 落盘前重新核对磁盘 JSONL：抽掉一行 → incomplete=True 且原因含文件名
  6. render_summary：incomplete 时第一行以 [INCOMPLETE] 开头；
     替身适配器时必须有显式警告行；完整时不出现 [INCOMPLETE]

纪律：全部走产品 API（build_report / write_report / render_summary / assess_completeness），
      不在测试里重实现报告结构；负例断言异常类型 + 消息含关键值。
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core.metrics_spec import FALLBACK, FIRST_AUDIO_MS, HIT, HIT_RATE, MISS, PRECAST_RATIO

from eval.report import (
    BenchReportError,
    CALIBER,
    REQUIRED_TOP_LEVEL,
    SCHEMA_VERSION,
    assess_completeness,
    build_report,
    check_raw_on_disk,
    render_summary,
    write_report,
)
from eval.stats import BOOTSTRAP_RESAMPLES, DEFAULT_SEED, MIN_REPEATS, QUANTILE_METHOD

UNITS = 12          # 与固定语料一致
REPEATS = MIN_REPEATS


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------
def make_arms(units=UNITS, repeats=REPEATS):
    """构造两臂的确定性指标块（结构与 bench 产出一致）。"""
    return {
        "fast": {
            "state_counts": {HIT: units, MISS: 0, FALLBACK: 0},
            HIT_RATE: 1.0,
            PRECAST_RATIO: 0.901,
            "tts_calls": {"n": repeats, "p50": 0.0, "max": 0.0},
            "synthesized_chars": {"n": repeats, "p50": 0.0, "max": 0.0},
        },
        "slow": {
            "state_counts": {HIT: 0, MISS: units, FALLBACK: 0},
            HIT_RATE: 0.0,
            PRECAST_RATIO: 0.0,
            "tts_calls": {"n": repeats, "p50": float(units), "max": float(units)},
            "synthesized_chars": {"n": repeats, "p50": 187.0, "max": 187.0},
        },
    }


def make_timing(n=REPEATS):
    return {
        "fast": {
            "n": n, "p50": 1.2, "p99": 3.4, "max": 5.6,
            "ci95_p50": [1.1, 1.4], "ci95_p99": [2.9, 4.1],
            "unit": "ms", "machine_dependent": True,
        },
        "slow": {
            "n": n, "p50": 480.5, "p99": 700.0, "max": 900.0,
            "ci95_p50": [430.0, 540.0], "ci95_p99": [640.0, 760.0],
            "unit": "ms", "machine_dependent": True,
        },
    }


def make_report(*, incomplete=False, reasons=None, meaningful=True, arms=None,
                timing=None, generated_at=None) -> dict:
    """组装一份最小但完整的报告字典。"""
    arms = make_arms() if arms is None else arms
    timing = make_timing() if timing is None else timing
    return build_report(
        command="python3 -m eval.bench --pack /tmp/p --corpus /tmp/c.json --out /tmp/o",
        seed=DEFAULT_SEED,
        repeats=REPEATS,
        warmup_runs=1,
        generated_at=generated_at,
        incomplete=incomplete,
        incomplete_reasons=list(reasons or []),
        env={
            "platform": "darwin", "python": "3.11", "cpu_count": 8,
            "adapter": {"name": "offline-synthetic", "voice": "Tingting",
                        "model_version": "macos-say", "synthetic": not meaningful},
            "pack": {"pack_version": "1.0.0", "voice": "Tingting",
                     "model_version": "macos-say", "asset_count": UNITS,
                     "path": "/tmp/p"},
        },
        corpus={"path": "/tmp/c.json", "corpus_id": "demo-broadcast-v1",
                "units": UNITS, "sha256": "0123456789abcdef"},
        arms=arms,
        delta={f"{FIRST_AUDIO_MS}_p50": 479.3, f"{FIRST_AUDIO_MS}_p99": 696.6,
               "tts_calls_p50": float(UNITS), "synthesized_chars_p50": 187.0},
        deterministic_metrics=arms,
        timing_metrics=timing,
        timing_metrics_meaningful=meaningful,
        raw={"fast_samples": "raw/fast_samples.jsonl",
             "slow_samples": "raw/slow_samples.jsonl",
             "fast_events": "raw/fast_events.jsonl",
             "slow_events": "raw/slow_events.jsonl"},
        reference_gate={"required_hit_rate": 0.987,
                        "source": "docs/01 判据 ε ≤ 1 - p^(1/n)（p=0.9, n=8）",
                        "observed_hit_rate": 1.0, "passed": True},
    )


def write_raw(out_dir: Path, arms=None, units=UNITS, repeats=REPEATS) -> None:
    """按报告 raw 指针写出 4 份原始 JSONL（行数与 arms 一致）。"""
    arms = make_arms() if arms is None else arms
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for arm in ("fast", "slow"):
        n = arms[arm]["tts_calls"]["n"]
        with (raw / f"{arm}_samples.jsonl").open("w", encoding="utf-8") as f:
            for i in range(n):
                f.write(json.dumps({"index": i, "arm": arm}) + "\n")
        total = sum(arms[arm]["state_counts"].values()) * n
        with (raw / f"{arm}_events.jsonl").open("w", encoding="utf-8") as f:
            for i in range(total):
                f.write(json.dumps({"index": i, "arm": arm}) + "\n")


# ---------------------------------------------------------------------------
# 1. schema
# ---------------------------------------------------------------------------
class BuildReportTest(unittest.TestCase):
    """build_report：字段名冻结，可追加不得改名或缺项。"""

    def test_schema_version(self):
        self.assertEqual(SCHEMA_VERSION, "vox-eval-report/1")
        self.assertEqual(make_report()["schema_version"], SCHEMA_VERSION)

    def test_all_top_level_fields_present(self):
        report = make_report()
        for key in REQUIRED_TOP_LEVEL:
            self.assertIn(key, report, f"报告缺顶层字段 {key!r}")

    def test_generated_at_is_iso8601(self):
        report = make_report(generated_at="2026-09-17T00:00:00Z")
        self.assertEqual(report["generated_at"], "2026-09-17T00:00:00Z")
        self.assertIn("T", make_report()["generated_at"])

    def test_caliber_uses_metric_constants(self):
        """口径说明的指标键必须来自 core.metrics_spec 常量。"""
        for const in (HIT_RATE, PRECAST_RATIO, FIRST_AUDIO_MS):
            self.assertIn(const, CALIBER, f"caliber 缺 {const!r} 口径")

    def test_caliber_entries_carry_formula_and_basis(self):
        """每条口径都要同时给公式与设计依据（验收标准 7）。"""
        for const in (HIT_RATE, PRECAST_RATIO, "tts_calls", "synthesized_chars",
                      FIRST_AUDIO_MS):
            text = CALIBER[const]
            self.assertIn("设计依据", text, f"{const!r} 缺设计依据引用")
            self.assertIn("=", text, f"{const!r} 缺公式")

    def test_caliber_statistics_frozen(self):
        self.assertEqual(CALIBER["quantile_method"], QUANTILE_METHOD)
        self.assertEqual(CALIBER["bootstrap_resamples"], BOOTSTRAP_RESAMPLES)
        self.assertEqual(CALIBER["min_repeats"], MIN_REPEATS)
        self.assertIn("可复现", CALIBER["reproducibility"])

    def test_synthesized_chars_marked_as_derived(self):
        """推导值必须在口径里标注，不能冒充 TTS 实测。"""
        self.assertIn("推导值", CALIBER["synthesized_chars"])
        self.assertIn("不是 TTS 实测", CALIBER["synthesized_chars"])

    def test_incomplete_true_without_reasons_rejected(self):
        """不得美化：不完整就必须写原因。"""
        with self.assertRaises(ValueError) as ctx:
            make_report(incomplete=True, reasons=[])
        self.assertIn("incomplete_reasons", str(ctx.exception))

    def test_incomplete_false_with_reasons_rejected(self):
        """不得美化：有原因却声称完整。"""
        with self.assertRaises(ValueError) as ctx:
            make_report(incomplete=False, reasons=["[fast] 有效样本数 5 < repeats 20"])
        self.assertIn("美化", str(ctx.exception))

    def test_non_bool_incomplete_rejected(self):
        with self.assertRaises(TypeError) as ctx:
            make_report(incomplete="yes")
        self.assertIn("bool", str(ctx.exception))

    def test_non_bool_meaningful_rejected(self):
        with self.assertRaises(TypeError) as ctx:
            make_report(meaningful="false")
        self.assertIn("bool", str(ctx.exception))

    def test_empty_arms_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            make_report(arms={})
        self.assertIn("arms", str(ctx.exception))

    def test_empty_timing_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            make_report(timing={})
        self.assertIn("timing_metrics", str(ctx.exception))

    def test_synthetic_marker_cross_validation(self):
        """build_report 硬校验：env.adapter.synthetic=True 时 timing_metrics_meaningful 必须为 False。

        WHY: 替身适配器的时序数字不得被报告背书为真机数据（T08 卡 §224 明令禁止）。
        """
        env = {
            "platform": "darwin", "python": "3.11", "cpu_count": 8,
            "adapter": {"name": "test", "voice": "Tingting",
                        "model_version": "macos-say", "synthetic": True},
            "pack": {"pack_version": "1.0.0", "voice": "Tingting",
                     "model_version": "macos-say", "asset_count": 12,
                     "path": "/tmp/p"},
        }
        with self.assertRaises(BenchReportError) as ctx:
            build_report(
                command="",
                seed=DEFAULT_SEED,
                repeats=REPEATS,
                warmup_runs=0,
                incomplete=False,
                incomplete_reasons=[],
                env=env,
                corpus={"path": "/tmp/c.json", "corpus_id": "demo-broadcast-v1",
                        "units": 12, "sha256": "0123456789abcdef"},
                arms=make_arms(),
                delta={},
                deterministic_metrics=make_arms(),
                timing_metrics=make_timing(),
                timing_metrics_meaningful=True,
                raw={},
                reference_gate={},
            )
        msg = str(ctx.exception)
        self.assertIn("env.adapter.synthetic", msg)
        self.assertIn("timing_metrics_meaningful", msg)

    def test_reasons_preserved_in_order(self):
        reasons = ["r1", "r2", "r3"]
        report = make_report(incomplete=True, reasons=reasons)
        self.assertEqual(report["incomplete_reasons"], reasons)
        self.assertTrue(report["incomplete"])


# ---------------------------------------------------------------------------
# 2. 完整性判定
# ---------------------------------------------------------------------------
def good_stats(arms=("fast", "slow"), repeats=REPEATS):
    return {
        arm: {"samples": repeats, "runtime_miss": [], "errors": [],
              "event_issues": [], "divergent_samples": []}
        for arm in arms
    }


def good_raw():
    return {
        "raw/fast_samples.jsonl": {"exists": True, "lines": REPEATS,
                                    "expected_lines": REPEATS, "missing_indices": []},
        "raw/slow_samples.jsonl": {"exists": True, "lines": REPEATS,
                                    "expected_lines": REPEATS, "missing_indices": []},
        "raw/fast_events.jsonl": {"exists": True, "lines": UNITS * REPEATS,
                                   "expected_lines": UNITS * REPEATS, "missing_indices": []},
        "raw/slow_events.jsonl": {"exists": True, "lines": UNITS * REPEATS,
                                   "expected_lines": UNITS * REPEATS, "missing_indices": []},
    }


class AssessCompletenessTest(unittest.TestCase):
    """assess_completeness 的每条规则单独可触发。"""

    def test_all_good_is_complete(self):
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=good_stats(), raw_checks=good_raw()
        )
        self.assertFalse(incomplete)
        self.assertEqual(reasons, [])

    def test_sample_shortfall(self):
        stats = good_stats()
        stats["fast"]["samples"] = 5
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=stats, raw_checks=good_raw()
        )
        self.assertTrue(incomplete)
        self.assertIn("[fast]", reasons[0])
        self.assertIn("5", reasons[0])
        self.assertIn(str(REPEATS), reasons[0])

    def test_runtime_miss_fail_closed(self):
        stats = good_stats()
        stats["fast"]["runtime_miss"].append(
            "bench-fast-0000: plan 单元 #1 未命中（key='missing_key'，reason=key_not_prebaked）"
        )
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=stats, raw_checks=good_raw()
        )
        self.assertTrue(incomplete)
        self.assertTrue(any("RuntimeMissError" in r for r in reasons))
        self.assertTrue(any("missing_key" in r for r in reasons))

    def test_execution_error(self):
        stats = good_stats()
        stats["slow"]["errors"].append("bench-slow-0003: OSError: 磁盘满")
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=stats, raw_checks=good_raw()
        )
        self.assertTrue(incomplete)
        self.assertTrue(any("执行抛异常" in r for r in reasons))
        self.assertTrue(any("bench-slow-0003" in r for r in reasons))

    def test_event_stream_incomplete(self):
        stats = good_stats()
        stats["fast"]["event_issues"].append("bench-fast-0001: 事件缺 part=[3, 7]")
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=stats, raw_checks=good_raw()
        )
        self.assertTrue(incomplete)
        self.assertTrue(any("事件流不完整" in r and "part=[3, 7]" in r for r in reasons))

    def test_divergent_deterministic_metrics(self):
        stats = good_stats()
        stats["slow"]["divergent_samples"] = [2, 9]
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=stats, raw_checks=good_raw()
        )
        self.assertTrue(incomplete)
        self.assertTrue(any("确定性" in r and "[2, 9]" in r for r in reasons))

    def test_raw_file_missing(self):
        raw = good_raw()
        raw["raw/fast_samples.jsonl"]["exists"] = False
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=good_stats(), raw_checks=raw
        )
        self.assertTrue(incomplete)
        self.assertTrue(any("raw/fast_samples.jsonl" in r and "缺失" in r for r in reasons))

    def test_raw_line_mismatch(self):
        raw = good_raw()
        raw["raw/slow_samples.jsonl"]["lines"] = REPEATS - 3
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=good_stats(), raw_checks=raw
        )
        self.assertTrue(incomplete)
        self.assertTrue(
            any("raw/slow_samples.jsonl" in r and "≠" in r for r in reasons)
        )

    def test_raw_index_gap(self):
        raw = good_raw()
        raw["raw/fast_events.jsonl"]["missing_indices"] = [41]
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=good_stats(), raw_checks=raw
        )
        self.assertTrue(incomplete)
        self.assertTrue(any("index=[41]" in r for r in reasons))

    def test_multiple_issues_all_reported(self):
        stats = good_stats()
        stats["fast"]["samples"] = 7
        stats["slow"]["runtime_miss"].append("bench-slow-0000: key='x'")
        raw = good_raw()
        raw["raw/slow_events.jsonl"]["exists"] = False
        incomplete, reasons = assess_completeness(
            repeats=REPEATS, corpus_units=UNITS, arm_stats=stats, raw_checks=raw
        )
        self.assertTrue(incomplete)
        self.assertGreaterEqual(len(reasons), 3)


# ---------------------------------------------------------------------------
# 3. 落盘与磁盘核对
# ---------------------------------------------------------------------------
class WriteReportTest(unittest.TestCase):
    """write_report：写盘 + 落盘前重新核对磁盘原始数据。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vox-eval-report-")
        self.out = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_report_json_and_returns_path(self):
        write_raw(self.out)
        report = make_report()
        path = write_report(report, self.out)
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "report.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["schema_version"], SCHEMA_VERSION)
        self.assertFalse(loaded["incomplete"])

    def test_json_round_trip(self):
        """报告必须能被原样读回（机器可读 JSON，验收标准要求）。"""
        write_raw(self.out)
        report = make_report(generated_at="2026-09-17T00:00:00Z")
        write_report(report, self.out)
        loaded = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded, report)

    def test_tampered_raw_marks_incomplete(self):
        """抽掉 fast_samples 一行后重建报告 → incomplete=True 且原因含文件名。"""
        write_raw(self.out)
        report = make_report()
        samples = self.out / "raw" / "fast_samples.jsonl"
        lines = samples.read_text(encoding="utf-8").splitlines()
        del lines[5]                      # 抽掉第 6 行（index=5）
        samples.write_text("\n".join(lines) + "\n", encoding="utf-8")

        write_report(report, self.out)
        self.assertTrue(report["incomplete"])
        joined = " | ".join(report["incomplete_reasons"])
        self.assertIn("raw/fast_samples.jsonl", joined)
        self.assertIn("index=[5]", joined)

    def test_missing_raw_file_marks_incomplete(self):
        write_raw(self.out)
        (self.out / "raw" / "slow_events.jsonl").unlink()
        report = make_report()
        write_report(report, self.out)
        self.assertTrue(report["incomplete"])
        self.assertTrue(
            any("raw/slow_events.jsonl" in r and "缺失" in r for r in report["incomplete_reasons"])
        )

    def test_incomplete_does_not_block_writing(self):
        """incomplete=True 也照样出报告（原始数据仍要落盘，报告要如实标注）。"""
        write_raw(self.out)
        report = make_report(incomplete=True, reasons=["[fast] 有效样本数 5 < repeats 20"])
        path = write_report(report, self.out)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(loaded["incomplete"])
        self.assertEqual(loaded["incomplete_reasons"][0], "[fast] 有效样本数 5 < repeats 20")

    def test_check_raw_on_disk_clean_returns_empty(self):
        write_raw(self.out)
        self.assertEqual(check_raw_on_disk(make_report(), self.out), [])

    def test_check_raw_on_disk_detects_unparseable_line(self):
        write_raw(self.out)
        events = self.out / "raw" / "fast_events.jsonl"
        lines = events.read_text(encoding="utf-8").splitlines()
        lines[0] = "{这不是合法 JSON"
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
        reasons = check_raw_on_disk(make_report(), self.out)
        self.assertTrue(any("无法解析" in r for r in reasons))


# ---------------------------------------------------------------------------
# 4. 人读摘要
# ---------------------------------------------------------------------------
class RenderSummaryTest(unittest.TestCase):

    def test_complete_first_line(self):
        summary = render_summary(make_report())
        self.assertTrue(summary.splitlines()[0].startswith("[COMPLETE]"))
        self.assertNotIn("[INCOMPLETE]", summary)

    def test_incomplete_first_line_prefixed(self):
        """人读摘要第一行必须是 [INCOMPLETE] 前缀 + 原因。"""
        report = make_report(incomplete=True, reasons=["[fast] 有效样本数 5 < repeats 20"])
        summary = render_summary(report)
        self.assertTrue(summary.splitlines()[0].startswith("[INCOMPLETE]"))
        self.assertIn("[fast] 有效样本数 5 < repeats 20", summary)

    def test_synthetic_adapter_warning(self):
        """替身适配器必须打显式警告行（不得美化，时序数字不得对外引用）。"""
        summary = render_summary(make_report(meaningful=False))
        self.assertIn("警告", summary)
        self.assertIn("synthetic", summary)
        self.assertIn("不得对外引用", summary)

    def test_no_warning_when_meaningful(self):
        self.assertNotIn("不得对外引用", render_summary(make_report(meaningful=True)))

    def test_summary_carries_sample_size_and_ci(self):
        """摘要必须同时给样本量与置信区间，禁止只给点估计。"""
        summary = render_summary(make_report())
        self.assertIn("n=20", summary)
        self.assertIn("CI95", summary)
        self.assertIn("P50", summary)
        self.assertIn("P99", summary)

    def test_summary_mentions_reference_gate(self):
        summary = render_summary(make_report())
        self.assertIn("0.987", summary)
        self.assertIn("通过", summary)

    def test_summary_empty_arm_timing(self):
        """0 样本的臂也要能渲染（不得因为 None 而抛错）。"""
        timing = make_timing()
        timing["fast"] = {"n": 0, "p50": None, "p99": None, "max": None,
                          "ci95_p50": None, "ci95_p99": None,
                          "unit": "ms", "machine_dependent": True}
        summary = render_summary(make_report(timing=timing))
        self.assertIn("无有效样本", summary)


# ---------------------------------------------------------------------------
# 5. 原始数据指纹核对（docs/08 §8.7 欠账 1）
# ---------------------------------------------------------------------------
def _write_samples_with_values(out_dir: Path) -> None:
    """写一份带数值字段的 raw（slow_samples 里放 first_audio_ms，供值级篡改用例）。"""
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for arm in ("fast", "slow"):
        with (raw / f"{arm}_samples.jsonl").open("w", encoding="utf-8") as f:
            for i in range(REPEATS):
                f.write(json.dumps({"index": i, "arm": arm, FIRST_AUDIO_MS: 1000.0},
                                   ensure_ascii=False) + "\n")
        total = UNITS * REPEATS
        with (raw / f"{arm}_events.jsonl").open("w", encoding="utf-8") as f:
            for i in range(total):
                f.write(json.dumps({"index": i, "arm": arm}, ensure_ascii=False) + "\n")


def _append_fingerprints(report: dict, out_dir: Path) -> dict:
    """把已落盘文件的字节 sha256 与行数追加进报告 raw 块（与 bench 落盘同口径）。

    只追加 `*_sha256` / `*_lines`，既有路径键原样保留。
    """
    raw = dict(report["raw"])
    for name, rel in report["raw"].items():
        path = out_dir / rel
        raw[f"{name}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open("r", encoding="utf-8") as f:
            raw[f"{name}_lines"] = sum(1 for line in f if line.strip())
    stamped = dict(report)
    stamped["raw"] = raw
    return stamped


class RawFingerprintCheckTest(unittest.TestCase):
    """check_raw_on_disk 的指纹核对：值级篡改必须可见；老报告必须向后兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vox-eval-fp-")
        self.out = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_old_report_without_fingerprints_still_passes(self):
        """向后兼容：无指纹的老报告走原行数/index 逻辑，不得因缺指纹而报错。"""
        write_raw(self.out)
        self.assertEqual(check_raw_on_disk(make_report(), self.out), [])

    def test_old_report_value_tamper_is_not_a_false_positive(self):
        """无指纹的老报告改数值 → 不报「指纹不符」（不得美化，也不得误报）。"""
        _write_samples_with_values(self.out)
        target = self.out / "raw" / "slow_samples.jsonl"
        records = []
        with target.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        for record in records:
            record[FIRST_AUDIO_MS] = 1.0
        with target.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.assertEqual(check_raw_on_disk(make_report(), self.out), [])

    def test_value_tampering_detected_when_fingerprint_present(self):
        """核心回归：行数与 index 不动、只改 first_audio_ms → sha256 核对必须拦下。"""
        _write_samples_with_values(self.out)
        report = _append_fingerprints(make_report(), self.out)

        target = self.out / "raw" / "slow_samples.jsonl"
        records = []
        with target.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        for record in records:
            record[FIRST_AUDIO_MS] = 1.0          # 1000.0 → 1.0（值级篡改）
        with target.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        reasons = check_raw_on_disk(report, self.out)
        hits = [r for r in reasons if "slow_samples.jsonl" in r]
        self.assertTrue(hits, f"值级篡改未被拦下，reasons={reasons}")
        self.assertTrue(any("sha256" in r for r in hits), "消息必须点出是 sha256 不符")
        self.assertTrue(any("期望" in r and "实际" in r for r in hits),
                        "消息必须含期望值与实际值")
        self.assertFalse(any("行数" in r for r in hits),
                         "行数没变，不该报行数不符（证明不是靠行数抓到的）")

    def test_line_count_change_detected_via_fingerprint(self):
        """抽掉一行后指纹核对给出「文件名 + 期望/实际」的行数差异。"""
        _write_samples_with_values(self.out)
        report = _append_fingerprints(make_report(), self.out)
        target = self.out / "raw" / "fast_samples.jsonl"
        lines = target.read_text(encoding="utf-8").splitlines()
        target.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
        reasons = check_raw_on_disk(report, self.out)
        self.assertTrue(
            any("fast_samples.jsonl" in r and "行数" in r and "20" in r for r in reasons),
            f"行数差异未被点名，reasons={reasons}",
        )

    def test_missing_file_with_fingerprint_reports_missing_only(self):
        """文件被删（而非被改）→ 报缺失，指纹核对不得抛异常。"""
        _write_samples_with_values(self.out)
        report = _append_fingerprints(make_report(), self.out)
        (self.out / "raw" / "slow_events.jsonl").unlink()
        reasons = check_raw_on_disk(report, self.out)
        self.assertEqual(len(reasons), 1)
        self.assertIn("slow_events.jsonl", reasons[0])
        self.assertIn("缺失", reasons[0])

    def test_fingerprint_keys_are_additive(self):
        """追加指纹键不得改既有路径键（报告 schema 只追加、不改名不缺项）。"""
        _write_samples_with_values(self.out)
        before = make_report()["raw"]
        after = _append_fingerprints(make_report(), self.out)["raw"]
        for key, value in before.items():
            self.assertEqual(after[key], value, f"既有键 {key} 被改动")
        self.assertEqual(len(after), len(before) * 3, "应恰好追加 sha256 与 lines 各一份")


if __name__ == "__main__":
    unittest.main()
