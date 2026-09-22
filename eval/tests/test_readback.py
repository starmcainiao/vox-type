"""
eval.tests.test_readback — 回读 CER harness（全离线）

覆盖（对应 T14 验收 5 与反空转条款）：
  1. 正常路径：3 条 manifest → 报告 n/mean/p50/p99 与手算一致，raw 带指纹；
  2. **不得美化 ①**：中途一条 transcribe 抛错 → 报告照样写出、incomplete_reasons
     含该 wav 路径与异常原文、成功条数不缩水（分母只计成功条）、摘要首行 [INCOMPLETE]；
  3. **不得美化 ②**：ASR 全挂 → n=0 且 mean/p50/p99 为 **null**（不是 0.0）；
  4. **不得美化 ③**：删 raw 样本一行 / 改一个数值 → 指纹与行数核对报红；
  5. 替身纪律：替身 ASR 产出 → env.asr.synthetic=true + 「不得对外引用」警告；
  6. 层边界：resolve_asr 用 dotted path 动态解析（不静态 import adapters）；
  7. manifest 坏行 → ReadbackError 且逐行报出（不静默跳过 = 不凭空缩分母）。

纪律（反空转）：全部调用产品 API（run_readback / run_readback_cli / resolve_asr /
  load_manifest / write_readback / check_raw_on_disk），不在测试里重实现 CER 或分位数；
  替身 ASR 打了 synthetic 标记，且负例断言错误消息含具体值。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eval import readback as rb
from eval import _io
from eval.cer import CER, CER_MEAN, CER_N, CER_P50, CER_P99
from eval.report import check_raw_on_disk

REF1 = "今天下午三点提醒你开会"
REF2 = "您好这里是报修服务热线"
REF3 = "我们会在工作日内安排师傅上门处理"  # 17 字


# ---------------------------------------------------------------------------
# 替身 ASR（测试专用，非产品件；必须打 synthetic 标记）
# ---------------------------------------------------------------------------
class NoTranscribe:
    """故意缺 transcribe 的适配器：用于测 resolve_asr 的接口形状校验。"""

    name = "bad-asr"
    synthetic = False


class StubAsr:
    """离线替身 ASR：按 wav 名映射到预设转写，或按预设索引抛异常。

    synthetic = True 显式标记（对齐 OfflineTts 的 T08b 替身纪律）。
    """

    name = "stub-asr"
    model_version = "stub/1.0"
    synthetic = True
    voice = None

    def __init__(self, responses=None, fail_at=None, fail_msg="boom"):
        self.responses = dict(responses or {})
        self.fail_at = set(fail_at or [])
        self.fail_msg = fail_msg
        self.calls = []

    def transcribe(self, wav_path):
        """按 wav 名返回预设转写；无预设时回落到去掉标点的 reference 文本。

        回落让 CLI 注入（无预设响应的 StubAsr）也能跑完整轮——
        此时每条 CER = 0.0，报告带 synthetic 警告（替身数字不得对外引用）。
        """
        key = Path(wav_path).name
        self.calls.append(key)
        if len(self.calls) - 1 in self.fail_at:
            raise RuntimeError(f"stub failure at {key}: {self.fail_msg}")
        if key in self.responses:
            return self.responses[key]
        from eval.cer import normalize_for_cer

        stem = Path(key).stem
        self.responses.setdefault(key, normalize_for_cer(stem))
        return self.responses[key]


def make_wavs(dir_path: Path) -> dict:
    """落 3 个 wav 占位文件，返回 {reference: 路径}。"""
    wav_dir = dir_path / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for i, ref in enumerate((REF1, REF2, REF3), start=1):
        p = wav_dir / f"s{i}.wav"
        p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + bytes(range(i)))
        files[ref] = p
    return files


def write_manifest(dir_path: Path, entries, name="manifest.jsonl") -> Path:
    """写 manifest JSONL（每行 wav + reference）。"""
    path = dir_path / name
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def entries_for(wavs: dict, refs=None):
    return [{"wav": str(wavs[r]), "reference": r} for r in (refs or (REF1, REF2, REF3))]


# ---------------------------------------------------------------------------
# 1. 正常路径
# ---------------------------------------------------------------------------
class HappyPathTest(unittest.TestCase):
    """全成功：统计块与手算一致，raw 带指纹，报告可写盘并核对通过。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.wavs = make_wavs(tmp)
        cls.manifest = write_manifest(tmp, entries_for(cls.wavs))
        # s1 完全匹配（0.0）；s2 只多一个句号（归一后 0.0）；s3 漏一个字
        cls.asr = StubAsr({
            "s1.wav": REF1 + "。",
            "s2.wav": REF2,
            "s3.wav": REF3[:7] + REF3[8:],
        })
        cls.report = rb.run_readback(
            cls.manifest, cls.asr, command="python3 -m eval.readback ..."
        )
        cls.tmp = tmp

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_stats_match_hand_computation(self):
        report = self.report
        values = sorted([0.0, 0.0, 1.0 / 16])
        block = report[CER]
        self.assertEqual(block[CER_N], 3)
        self.assertAlmostEqual(block[CER_MEAN], sum(values) / 3, places=12)
        # p50 = 线性插值，n=3 时位置 (n-1)*0.5 = 1.0 → ordered[1]
        self.assertAlmostEqual(block[CER_P50], 0.0, places=12)
        # p99 = 线性插值，位置 (n-1)*0.99 = 1.98 → 在 ordered[1] 与 ordered[2] 之间
        self.assertAlmostEqual(block[CER_P99], 1.0 / 16 * 0.98, places=12)
        self.assertAlmostEqual(block["max"], 1.0 / 16, places=12)
        self.assertEqual(report["incomplete"], False)
        self.assertEqual(report["incomplete_reasons"], [])

    def test_report_writes_and_raw_fingerprints_are_consistent(self):
        report = dict(self.report)
        tmp = self.tmp
        samples = rb._report_samples(report)
        raw_dir = tmp / "raw"
        raw_dir.mkdir(exist_ok=True)
        raw_path = tmp / rb.RAW_SAMPLES_REL
        _io.write_jsonl(raw_path, samples)
        report["raw"] = rb._raw_pointers(raw_path, tmp, samples)

        written = rb.write_readback(report, tmp)
        self.assertTrue(written.is_file())
        self.assertEqual(
            rb.check_raw_on_disk(json.loads(written.read_text(encoding="utf-8")), tmp),
            [],
            "落盘后指纹与行数核对必须无问题",
        )
        # 指纹确实等于文件字节 sha256（不是随便填的），行数也一致
        self.assertEqual(
            report["raw"]["readback_samples_sha256"],
            _io.sha256_of_file(raw_path),
            "报告里的指纹必须等于 raw 文件的实际字节 sha256",
        )
        self.assertEqual(report["raw"]["readback_samples_lines"], len(samples))
        self.assertEqual(report["raw"]["readback_samples"], "raw/readback_samples.jsonl")

    def test_manifest_recorded_with_sha256(self):
        report = dict(self.report)
        tmp = self.tmp
        self.assertEqual(report["manifest"]["entries"], 3)
        self.assertEqual(len(report["manifest"]["sha256"]), 16)

    def test_caliber_mentions_design_basis(self):
        report = self.report
        caliber = report["caliber"]
        self.assertIn(CER, caliber)
        self.assertIn("docs/05", caliber[CER], "口径必须标注设计依据出处")
        self.assertIn("Seed-TTS", caliber[CER])


# ---------------------------------------------------------------------------
# 2. 不得美化 ① —— 中途一条失败
# ---------------------------------------------------------------------------
class PartialFailureTest(unittest.TestCase):
    """① 中途一条 transcribe 抛错：报告照样写出、成功条数不缩水。"""

    def test_partial_failure_keeps_report_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            asr = StubAsr(
                {"s1.wav": REF1, "s2.wav": REF2, "s3.wav": REF3},
                fail_at={1}, fail_msg="connection refused by engine",
            )
            report = rb.run_readback(manifest, asr)

            # 报告写出（run_readback 不抛错）
            self.assertTrue(report["incomplete"])
            failed = [r for r in report["incomplete_reasons"] if "transcribe 失败" in r]
            self.assertEqual(len(failed), 1, "只应有一条 transcribe 失败记录")
            reason = failed[0]
            # 含该 wav 路径 + 异常原文
            self.assertIn("s2.wav", reason, "原因必须点名失败的 wav 文件")
            self.assertIn("connection refused by engine", reason, "原因必须含异常原文")
            self.assertIn("RuntimeError", reason, "原因必须含异常类型名")

            # 成功条数不缩水：另外 2 条照常算 CER
            block = report[CER]
            self.assertEqual(block[CER_N], 2)
            self.assertEqual(len(report["samples"]), 3, "全部 3 条都要留在样本明细里")
            self.assertEqual(report["samples"][1]["status"], "transcribe_failed")
            self.assertIsNone(report["samples"][1][CER])

            # 摘要首行必须是 [INCOMPLETE]
            summary = rb._render_readback_summary(report)
            self.assertTrue(summary.startswith("[INCOMPLETE]"), summary.splitlines()[0])

    def test_partial_failure_still_writes_disk(self):
        """中途失败也不阻止落盘（incomplete 不阻止出报告）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            asr = StubAsr({"s1.wav": REF1, "s2.wav": REF2, "s3.wav": REF3}, fail_at={0})
            report = rb.run_readback(manifest, asr)
            samples = rb._report_samples(report)
            raw_dir = tmp / "raw"
            raw_dir.mkdir(exist_ok=True)
            raw_path = tmp / rb.RAW_SAMPLES_REL
            _io.write_jsonl(raw_path, samples)
            report["raw"] = rb._raw_pointers(raw_path, tmp, samples)

            written = rb.write_readback(report, tmp)
            self.assertTrue(written.is_file(), "中途失败时报告仍必须写出")
            on_disk = json.loads(written.read_text(encoding="utf-8"))
            self.assertTrue(on_disk["incomplete"])
            self.assertEqual(
                len([r for r in on_disk["incomplete_reasons"] if "transcribe 失败" in r]),
                1,
            )


# ---------------------------------------------------------------------------
# 3. 不得美化 ② —— ASR 全挂
# ---------------------------------------------------------------------------
class TotalFailureTest(unittest.TestCase):
    """② ASR 全挂 → n=0 且 mean/p50/p99 = null（不是 0.0）。"""

    def test_all_failed_yields_null_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            asr = StubAsr({}, fail_at={0, 1, 2}, fail_msg="omlx unreachable")
            report = rb.run_readback(manifest, asr)

            block = report[CER]
            self.assertEqual(block[CER_N], 0)
            self.assertIsNone(block[CER_MEAN], "n=0 时 mean 必须是 null，不是 0.0")
            self.assertIsNone(block[CER_P50], "n=0 时 p50 必须是 null，不是 0.0")
            self.assertIsNone(block[CER_P99], "n=0 时 p99 必须是 null，不是 0.0")
            self.assertIsNone(block["max"])
            failed = [r for r in report["incomplete_reasons"] if "transcribe 失败" in r]
            self.assertEqual(len(failed), 3, "三条失败原文必须都在（不缩视图）")
            for key, msg in zip(("s1.wav", "s2.wav", "s3.wav"), failed):
                self.assertIn(key, msg)
                self.assertIn("omlx unreachable", msg)

    def test_all_failed_report_is_serializable(self):
        """null 必须能真落盘成 JSON null（而不是 0.0）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            asr = StubAsr({}, fail_at={0, 1, 2})
            report = rb.run_readback(manifest, asr)
            samples = rb._report_samples(report)
            raw_dir = tmp / "raw"
            raw_dir.mkdir(exist_ok=True)
            raw_path = tmp / rb.RAW_SAMPLES_REL
            _io.write_jsonl(raw_path, samples)
            report["raw"] = rb._raw_pointers(raw_path, tmp, samples)
            written = rb.write_readback(report, tmp)

            on_disk = json.loads(written.read_text(encoding="utf-8"))
            self.assertIsNone(on_disk[CER][CER_MEAN])
            self.assertIsNone(on_disk[CER][CER_P50])
            self.assertIsNone(on_disk[CER][CER_P99])
            self.assertEqual(json.dumps(on_disk[CER][CER_P99]), "null")


# ---------------------------------------------------------------------------
# 4. 不得美化 ③ —— raw 被改
# ---------------------------------------------------------------------------
class RawIntegrityTest(unittest.TestCase):
    """③ raw 样本被抽改 → 指纹/行数核对报红。"""

    @staticmethod
    def _clean(case_dir: Path) -> None:
        """清理用例级临时目录。"""
        import shutil

        shutil.rmtree(case_dir, ignore_errors=True)

    @classmethod
    def setUpClass(cls):
        """准备一份 raw 已落盘且指纹一致的报告（篡改测试需要报告与文件都在磁盘上）。

        故意让 s1 的转写带一个句末句号：归一后 CER=0.0，留出一条「原值已是 0.0」的
        记录——值级篡改测试挑的是另两条非 0 的，改 0.0 才真的改变了字节。
        """
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        wavs = make_wavs(cls.tmp)
        manifest = write_manifest(cls.tmp, entries_for(wavs))
        cls.asr = StubAsr({"s1.wav": REF1 + "。", "s2.wav": REF2, "s3.wav": REF3[:7] + REF3[8:]})
        cls.report = rb.run_readback(manifest, cls.asr)
        cls.samples = rb._report_samples(cls.report)
        cls.raw_path = cls.tmp / rb.RAW_SAMPLES_REL
        cls.raw_path.parent.mkdir(parents=True, exist_ok=True)
        _io.write_jsonl(cls.raw_path, cls.samples)
        cls.report["raw"] = rb._raw_pointers(cls.raw_path, cls.tmp, cls.samples)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _case(self):
        """每个用例拿一份独立的磁盘副本（篡改互不污染）。"""
        import shutil

        case_dir = Path(tempfile.mkdtemp(prefix="vox-readback-case-"))
        for item in self.tmp.iterdir():
            dest = case_dir / item.name
            shutil.copytree(item, dest) if item.is_dir() else shutil.copy2(item, dest)
        return dict(self.report), case_dir, case_dir / rb.RAW_SAMPLES_REL

    def test_deleting_one_raw_line_turns_red(self):
        report, tmp, raw_path = self._case()
        lines = raw_path.read_text(encoding="utf-8").splitlines(True)
        self.assertEqual(len(lines), 3)
        raw_path.write_text("".join(lines[2:]), encoding="utf-8")  # 删掉第 2 行

        issues = check_raw_on_disk(report, tmp)
        self.assertTrue(issues, "删掉一行 raw 必须被指纹/行数核对抓到")
        joined = " | ".join(issues)
        self.assertIn("readback_samples.jsonl", joined, "报错必须点名具体文件")
        self.assertIn("行数", joined)
        self._clean(tmp)

    def test_value_tampering_is_caught_by_sha256(self):
        """值级篡改（把某条 cer 改成 0.0，行数与 index 都不变）→ sha256 报红。

        这是 T08b 审计 #5 留给 T10b 的破口：只有逐字节摘要能抓它。
        前提：被篡改的条目标值必须真的变（原值已是 0.0 就改不动，sha256 也不该报红）。
        """
        report, tmp, raw_path = self._case()
        lines = raw_path.read_text(encoding="utf-8").splitlines(True)
        records = [json.loads(line) for line in lines]

        target = next(i for i, r in enumerate(records) if r[CER] != 0.0)
        before = records[target][CER]
        records[target][CER] = 0.0
        raw_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        after = json.loads(raw_path.read_text(encoding="utf-8").splitlines(True)[target])

        issues = check_raw_on_disk(report, tmp)
        self.assertTrue(issues, "值级篡改必须被 sha256 指纹抓到")
        self.assertIn("sha256", " | ".join(issues))
        # 篡改断言必须能失败：确认写回去的字节确实不同（否则上面通过就是假通过）
        self.assertNotEqual(after[CER], before)
        self._clean(tmp)

    def test_deleting_raw_file_turns_red(self):
        report, tmp, raw_path = self._case()
        raw_path.unlink()
        issues = check_raw_on_disk(report, tmp)
        self.assertTrue(issues)
        self.assertIn("readback_samples.jsonl", " | ".join(issues))
        self._clean(tmp)

    def test_write_readback_marks_incomplete_on_disk_issues(self):
        """落盘前复核发现问题 → 报告被原地标成 incomplete（不美化）。"""
        report, tmp, raw_path = self._case()
        lines = raw_path.read_text(encoding="utf-8").splitlines(True)
        raw_path.write_text("".join(lines[1:]), encoding="utf-8")

        written = rb.write_readback(report, tmp)
        on_disk = json.loads(written.read_text(encoding="utf-8"))
        self.assertTrue(on_disk["incomplete"])
        self.assertTrue(any("readback_samples.jsonl" in r for r in on_disk["incomplete_reasons"]))
        self._clean(tmp)


# ---------------------------------------------------------------------------
# 5. 替身纪律
# ---------------------------------------------------------------------------
class SyntheticMarkerTest(unittest.TestCase):
    """替身 ASR 产出 → synthetic 标记 + 「不得对外引用」警告。"""

    def test_stub_asr_is_marked_synthetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            report = rb.run_readback(manifest, StubAsr({
                "s1.wav": REF1, "s2.wav": REF2, "s3.wav": REF3,
            }))
            self.assertTrue(report["env"]["asr"]["synthetic"])
            self.assertTrue(report["synthetic"], "报告必须带顶层 synthetic 标记")
            # 全成功的替身跑：数据完整（incomplete=False），但不得对外引用——
            # 两件事分开记，避免把「数据不是真机的」误读成「数据有缺陷」
            self.assertFalse(report["incomplete"])
            self.assertEqual(report["incomplete_reasons"], [])
            self.assertEqual(report[CER][CER_N], 3)

    def test_summary_has_synthetic_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            report = rb.run_readback(manifest, StubAsr({
                "s1.wav": REF1, "s2.wav": REF2, "s3.wav": REF3,
            }))
            summary = rb._render_readback_summary(report)
            self.assertIn(rb.SYNTHETIC_WARNING, summary)
            self.assertIn("[警告]", summary)

    def test_real_adapter_is_not_synthetic(self):
        """adapters/ 下的真机适配器声明 synthetic=False → 不被判为替身。"""
        class RealAsr:
            name = "omlx-asr"
            model_version = "Qwen3-ASR-0.6B-8bit"
            synthetic = False
            voice = None

        # 模块归属必须在 adapters. 下，才能通过「非 eval 包 + 显式声明」判定
        real = RealAsr()
        old_module = type(real).__module__
        try:
            sys.modules["adapters.asr_omlx"].__dict__  # 保证已导入
            type(real).__module__ = "adapters.asr_omlx"
            self.assertFalse(rb._is_synthetic(real))
        finally:
            type(real).__module__ = old_module


# ---------------------------------------------------------------------------
# 6. 层边界与 dotted path 注入
# ---------------------------------------------------------------------------
class InjectionTest(unittest.TestCase):
    """ASR 一律经 dotted path 注入；eval 不静态 import adapters。"""

    def test_resolve_asr_loads_by_dotted_path(self):
        asr = rb.resolve_asr("adapters.asr_omlx:OmlxAsr", base_url="http://127.0.0.1:10099")
        self.assertEqual(asr.model, "Qwen3-ASR-0.6B-8bit")
        self.assertEqual(asr.base_url, "http://127.0.0.1:10099")
        self.assertTrue(callable(asr.transcribe))

    def test_resolve_asr_rejects_bad_spec(self):
        with self.assertRaises(rb.ReadbackError) as cm:
            rb.resolve_asr("no-colon-here")
        self.assertIn("模块路径:类名", str(cm.exception))
        self.assertIn("no-colon-here", str(cm.exception))

    def test_resolve_asr_rejects_missing_class(self):
        with self.assertRaises(rb.ReadbackError) as cm:
            rb.resolve_asr("adapters.asr_omlx:NoSuchClass")
        self.assertIn("NoSuchClass", str(cm.exception))

    def test_resolve_asr_rejects_missing_transcribe(self):
        """接口形状校验：缺 transcribe 的适配器必须被拦下。"""
        class NoTranscribe:
            name = "bad"
            synthetic = False

        with self.assertRaises(rb.ReadbackError) as cm:
            rb.resolve_asr("eval.tests.test_readback:NoTranscribe")
        self.assertIn("transcribe", str(cm.exception))

    def test_readback_module_has_no_static_adapter_import(self):
        """硬边界：eval/readback.py 的 import 区不得出现 adapters/compiler/rules。"""
        import re

        source = Path(rb.__file__).read_text(encoding="utf-8")
        hits = re.findall(r"^(from|import)\s+(adapters|compiler|rules)\b", source, re.M)
        self.assertEqual(hits, [], f"发现静态跨层 import: {hits}")

    def test_cli_end_to_end_writes_report(self):
        """CLI 全链路：manifest + 注入替身 ASR → 写出 report.json 与 raw。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            out_dir = tmp / "out"

            code = rb.run_readback_cli([
                "--manifest", str(manifest),
                "--asr", "eval.tests.test_readback:StubAsr",
                "--out", str(out_dir),
            ])
            # 替身 ASR → incomplete（警告行）→ 退出码 5（fail-closed 中止）
            self.assertEqual(code, 5, "替身产出必须 fail-closed 退出（报告已写出但不得引用）")
            self.assertTrue((out_dir / "report.json").is_file())
            self.assertTrue((out_dir / "raw" / "readback_samples.jsonl").is_file())
            on_disk = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            # 替身标记走独立字段：全成功的替身跑数据是完整的（incomplete=False），
            # 但产出不是真机做的（synthetic=True）→ 退出码仍为 5。
            self.assertFalse(on_disk["incomplete"])
            self.assertTrue(on_disk["synthetic"])


# ---------------------------------------------------------------------------
# 7. manifest 校验
# ---------------------------------------------------------------------------
class ManifestTest(unittest.TestCase):
    """manifest 坏行必须逐行报出（不静默跳过 = 不凭空缩分母）。"""

    def test_missing_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            path = write_manifest(tmp, [{"wav": str(wavs[REF1]), "reference": "   "}], "bad.jsonl")
            with self.assertRaises(rb.ReadbackError) as cm:
                rb.load_manifest(path)
            self.assertIn("第 1 行", str(cm.exception))
            self.assertIn("reference", str(cm.exception))

    def test_bad_json_line_is_reported_with_lineno(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            path = tmp / "bad.jsonl"
            path.write_text("{not json}\n", encoding="utf-8")
            with self.assertRaises(rb.ReadbackError) as cm:
                rb.load_manifest(path)
            self.assertIn("第 1 行", str(cm.exception))
            self.assertIn("不是合法 JSON", str(cm.exception))

    def test_empty_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            path = tmp / "empty.jsonl"
            path.write_text("\n", encoding="utf-8")
            with self.assertRaises(rb.ReadbackError) as cm:
                rb.load_manifest(path)
            self.assertIn("无有效条目", str(cm.exception))

    def test_missing_file_is_rejected(self):
        with self.assertRaises(rb.ReadbackError) as cm:
            rb.load_manifest("/definitely/not/here.jsonl")
        self.assertIn("/definitely/not/here.jsonl", str(cm.exception))

    def test_punctuation_only_reference_enters_reasons(self):
        """reference 纯标点 → CER 无定义 → 进 incomplete_reasons，不算作 0.0。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wav_dir = tmp / "wav"
            wav_dir.mkdir()
            p = wav_dir / "p.wav"
            p.write_bytes(b"RIFFxxxxxxxxWAVE")
            manifest = write_manifest(tmp, [{"wav": str(p), "reference": "。。！"}])
            report = rb.run_readback(manifest, StubAsr({"p.wav": "随便"}))
            self.assertEqual(report[CER][CER_N], 0)
            self.assertIsNone(report[CER][CER_P50])
            self.assertIn("CER 无定义", report["incomplete_reasons"][0])
            self.assertIn("。。！", report["incomplete_reasons"][0])


# ---------------------------------------------------------------------------
# 8. T14c 审计修复：替身判定 fail-safe（修 1）
# ---------------------------------------------------------------------------
class SyntheticFailsafeTest(unittest.TestCase):
    """T14c 修 1：synthetic 属性存在即按 bool() 归真，属性缺失按替身。

    修前用 `is True` 比较——`synthetic = 1` 这类类型笔误会穿透成
    `synthetic: false` + `[COMPLETE]` + rc 0，替身数据被当真机背书。
    """

    def _stub(self, synthetic_value, name="stub-typo-asr"):
        """造一个 transcribe 可用的替身类；synthetic_value 为 _NO_ATTR 时不声明该属性。"""
        attrs = {
            "name": name,
            "model_version": "typo/1.0",
            "voice": None,
        }
        if synthetic_value is not _NO_ATTR:
            attrs["synthetic"] = synthetic_value
        cls = type(name, (), attrs)
        cls.transcribe = lambda self, wav_path: Path(wav_path).name
        return cls()

    def _stub_in_adapters(self, synthetic_value):
        """模块归属改到 adapters. 下（模拟真机适配器目录），返回 (实例, 原模块名)。"""
        asr = self._stub(synthetic_value, name="adapters-stub-asr")
        old = type(asr).__module__
        sys.modules["adapters.asr_omlx"].__dict__  # 确保已导入
        type(asr).__module__ = "adapters.asr_omlx"
        return asr, old

    def test_synthetic_int_one_is_synthetic(self):
        """`synthetic = 1`（类型笔误）必须按替身处理。"""
        self.assertTrue(rb._is_synthetic(self._stub(1)), "synthetic=1 必须判为替身")

    def test_synthetic_truthy_string_is_synthetic(self):
        """`synthetic = "yes"` 同罪：任何 truthy 值都按替身。"""
        self.assertTrue(rb._is_synthetic(self._stub("yes")))
        self.assertTrue(rb._is_synthetic(self._stub("False")))

    def test_synthetic_attr_absent_is_synthetic(self):
        """未声明 synthetic 的适配器必须按替身（保守判定）。"""
        self.assertTrue(rb._is_synthetic(self._stub(_NO_ATTR)))

    def test_explicit_false_outside_adapters_is_real(self):
        """显式 `synthetic = False` 且不在 adapters/ 下、也不在 eval 包内 → 按真机。

        注：测试模块自身在 eval 包目录下，所以「eval 内 + 显式 False」仍按替身
        （保守判定）——这个用例把模块归属挪到中性位置，只留「显式 False + 非
        adapters/」这一个变量，对应验收 2 的同款场景。
        """
        asr = self._stub(False, name="neutral-real-asr")
        old = type(asr).__module__
        try:
            type(asr).__module__ = "neutral_real_asr"
            self.assertFalse(rb._is_synthetic(asr))
        finally:
            type(asr).__module__ = old

    def test_explicit_false_inside_eval_pkg_still_synthetic(self):
        """eval 包内的替身即使显式 False 也仍按替身（保守判定不放松）。"""
        asr = self._stub(False, name="eval-inner-asr")
        old = type(asr).__module__
        try:
            type(asr).__module__ = "eval.readback"
            self.assertTrue(rb._is_synthetic(asr))
        finally:
            type(asr).__module__ = old

    def test_explicit_false_in_adapters_is_real(self):
        """adapters/ 下的显式 False 适配器按真机（OmlxAsr 的既有判定不变）。"""
        asr, old = self._stub_in_adapters(False)
        try:
            self.assertFalse(rb._is_synthetic(asr))
        finally:
            type(asr).__module__ = old

    def test_omlx_asr_still_not_synthetic(self):
        """OmlxAsr 真机适配器行为不变。"""
        from adapters.asr_omlx import OmlxAsr

        self.assertIs(OmlxAsr().synthetic, False)
        self.assertFalse(rb._is_synthetic(OmlxAsr()))

    def test_cli_synthetic_one_fails_closed_with_rc_5(self):
        """端到端：`synthetic = 1` 的注入适配器 → synthetic:true + [警告] + rc 5。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            out_dir = tmp / "out"

            code = rb.run_readback_cli([
                "--manifest", str(manifest),
                "--asr", "eval.tests.test_readback:SyntheticOneAsr",
                "--out", str(out_dir),
            ])
            on_disk = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            # 期望值来自卡的验收判据（fail-closed），不从被测逻辑取
            self.assertEqual(code, 5)
            self.assertTrue(on_disk["synthetic"])
            self.assertEqual(on_disk["env"]["asr"]["synthetic"], True)
            self.assertFalse(on_disk["incomplete"])
            summary = rb._render_readback_summary(on_disk)
            self.assertIn("[警告]", summary)
            self.assertIn(rb.SYNTHETIC_WARNING, summary)
            self.assertNotIn("[INCOMPLETE]", summary)

    def test_cli_synthetic_truthy_string_fails_closed(self):
        """`synthetic = "yes"` 同罪：rc 5。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            out_dir = tmp / "out"

            code = rb.run_readback_cli([
                "--manifest", str(manifest),
                "--asr", "eval.tests.test_readback:SyntheticYesAsr",
                "--out", str(out_dir),
            ])
            self.assertEqual(code, 5)
            on_disk = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(on_disk["synthetic"])


_NO_ATTR = object()


class SyntheticOneAsr:
    """CLI 注入用替身：synthetic 写成整数 1（类型笔误）。"""

    name = "stub-one-asr"
    model_version = "stub/1.0"
    synthetic = 1
    voice = None

    def transcribe(self, wav_path):
        from eval.cer import normalize_for_cer

        return normalize_for_cer(Path(wav_path).stem)


class SyntheticYesAsr:
    """CLI 注入用替身：synthetic 写成 truthy 字符串 "yes"。"""

    name = "stub-yes-asr"
    model_version = "stub/1.0"
    synthetic = "yes"
    voice = None

    def transcribe(self, wav_path):
        from eval.cer import normalize_for_cer

        return normalize_for_cer(Path(wav_path).stem)


# ---------------------------------------------------------------------------
# 9. T14c 审计修复：退出码归位（修 2）
# ---------------------------------------------------------------------------
class ExitCodeTest(unittest.TestCase):
    """T14c 修 2：可归类的用法/输入错误 → rc 2；冻结集合 {0,2,3,4,5} 之外无出口。"""

    def _cli(self, argv):
        import io
        import contextlib

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = rb.run_readback_cli(argv)
        return code, err.getvalue()

    def test_non_utf8_manifest_returns_2(self):
        """manifest 非 UTF-8 → rc 2，stderr 含异常类型名，非裸 traceback。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            path = tmp / "bad.jsonl"
            path.write_bytes(b"\xff\xfe\xfa\x00{\"wav\": \"x\", \"reference\": \"y\"}")
            code, err = self._cli([
                "--manifest", str(path),
                "--asr", "eval.tests.test_readback:StubAsr",
                "--out", str(tmp / "out"),
            ])
            self.assertEqual(code, 2, f"非 UTF-8 manifest 应归 rc 2，实际 {code}\n{err}")
            self.assertIn("UnicodeDecodeError", err, "stderr 必须含异常类型名")
            self.assertNotIn("Traceback (most recent call last)", err)

    def test_asr_import_error_returns_2(self):
        """--asr 指向导入即抛错的模块 → rc 2。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            code, err = self._cli([
                "--manifest", str(manifest),
                "--asr", "eval.tests.test_readback:NonexistentAsr",
                "--out", str(tmp / "out"),
            ])
            self.assertEqual(code, 2, f"--asr 类不存在应归 rc 2，实际 {code}\n{err}")
            self.assertIn("ReadbackError", err)
            self.assertNotIn("Traceback (most recent call last)", err)

    def test_missing_manifest_returns_2(self):
        with self.assertRaises(rb.ReadbackError):
            rb.load_manifest("/definitely/not/here.jsonl")
        code, err = self._cli([
            "--manifest", "/definitely/not/here.jsonl",
            "--asr", "eval.tests.test_readback:StubAsr",
        ])
        self.assertEqual(code, 2)
        self.assertIn("ReadbackError", err)

    def test_cli_errors_tuple_is_consumed(self):
        """_CLI_ERRORS 必须被顶层真正引用（修前「定义未用」）。"""
        import re

        source = Path(rb.__file__).read_text(encoding="utf-8")
        # 定义行之外必须还有至少一处 except 消费
        refs = [
            line for line in source.splitlines()
            if "_CLI_ERRORS" in line and not line.strip().startswith("_CLI_ERRORS =")
        ]
        self.assertTrue(
            any("except _CLI_ERRORS" in line for line in refs),
            f"_CLI_ERRORS 未被任何 except 消费：{refs}",
        )

    def test_only_frozen_exit_codes_appear(self):
        """源码里除冻结集合 {0,2,3,4,5} 之外没有别的 return 码（无 rc 1 出口）。"""
        import re

        source = Path(rb.__file__).read_text(encoding="utf-8")
        cli_body = source[source.index("def run_readback_cli"):]
        cli_body = cli_body[: cli_body.index("\ndef _report_samples")]
        codes = {int(m) for m in re.findall(r"^\s*return (\d+)$", cli_body, re.M)}
        self.assertTrue(codes <= {0, 2, 3, 4, 5}, f"冻结集合之外的退出码: {sorted(codes)}")
        self.assertNotIn(1, codes, "不得存在 rc 1 出口")


# ---------------------------------------------------------------------------
# 10. T14c 审计修复：指纹问题不重复追加（修 3）
# ---------------------------------------------------------------------------
class FingerprintDedupTest(unittest.TestCase):
    """T14c 修 3：同一条指纹问题在 report.json 里只出现一次。

    制造窗口的方式：先在 CLI 内部建指针（第一次核对），随后改 raw 文件，
    再调 write_readback（第二次核对）。两次核对得到同一条问题——
    修前会被追加两次（报告 2 条、摘要「2 项原因」）。
    """

    def _base_report(self, tmp: Path):
        wavs = make_wavs(tmp)
        manifest = write_manifest(tmp, entries_for(wavs))
        report = rb.run_readback(manifest, StubAsr({
            "s1.wav": REF1 + "。", "s2.wav": REF2, "s3.wav": REF3[:7] + REF3[8:],
        }))
        samples = rb._report_samples(report)
        raw_path = tmp / rb.RAW_SAMPLES_REL
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        _io.write_jsonl(raw_path, samples)
        report["raw"] = rb._raw_pointers(raw_path, tmp, samples)
        return report, raw_path

    def _tamper(self, raw_path: Path) -> None:
        """删掉第 2 行：既破坏行数也破坏指纹，核对给出同一条问题原文。"""
        lines = raw_path.read_text(encoding="utf-8").splitlines(True)
        self.assertEqual(len(lines), 3)
        raw_path.write_text("".join(lines[2:]), encoding="utf-8")

    def test_write_readback_does_not_duplicate_existing_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report, raw_path = self._base_report(tmp)
            self._tamper(raw_path)
            # 第一次核对（模拟 CLI 建指针后的核对），并把结果先记进报告
            first = check_raw_on_disk(report, tmp)
            self.assertTrue(first, "篡改后必须至少有一条指纹问题")
            report["incomplete"] = True
            report["incomplete_reasons"] = list(report["incomplete_reasons"]) + first

            written = rb.write_readback(report, tmp)

            on_disk = json.loads(written.read_text(encoding="utf-8"))
            counts = [on_disk["incomplete_reasons"].count(r) for r in first]
            self.assertEqual(
                counts, [1] * len(first),
                f"每条指纹问题在报告里应只出现一次，实际 {counts} 次：{first}",
            )
            self.assertTrue(on_disk["incomplete"])
            self.assertEqual(len(on_disk["incomplete_reasons"]), len(first))

    def test_cli_fingerprint_issues_are_not_duplicated(self):
        """端到端：建指针后、写盘前改 raw → 指纹问题原文不重复出现。

        注入方式：把模块级的 write_readback 换成一个「先篡改磁盘 raw、再走原实现」
        的包装——原实现内部会再核一次磁盘，两次核对必须产出同一条问题原文。
        注：CLI 退出码此处断言不重复计数，不断言 rc 4——注入的 StubAsr 是替身，
        fail-closed 会把 rc 抬到 5（rc 4 是质检路径的码，二者互不冲突）。
        """
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wavs = make_wavs(tmp)
            manifest = write_manifest(tmp, entries_for(wavs))
            out_dir = tmp / "out"
            real_write = rb.write_readback
            state = {"tampered": False}

            def patched_write(report, out_dir):
                raw_path = Path(out_dir) / rb.RAW_SAMPLES_REL
                if not state["tampered"]:
                    state["tampered"] = True
                    lines = raw_path.read_text(encoding="utf-8").splitlines(True)
                    raw_path.write_text("".join(lines[2:]), encoding="utf-8")
                return real_write(report, out_dir)

            rb.write_readback = patched_write
            try:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = rb.run_readback_cli([
                        "--manifest", str(manifest),
                        "--asr", "eval.tests.test_readback:StubAsr",
                        "--out", str(out_dir),
                    ])
            finally:
                rb.write_readback = real_write

            on_disk = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            issues = on_disk["incomplete_reasons"]
            fp_issues = [r for r in issues if "readback_samples.jsonl" in r]
            self.assertTrue(fp_issues, "篡改后必须至少有一条指纹问题")
            # 核对会分别报「缺 index / sha256 不符 / 行数不符」三条**不同**问题——
            # 合法的多条；本卡的缺陷是同一条问题原文被追加两次。
            self.assertEqual(
                len(fp_issues), len(set(fp_issues)),
                f"同一问题原文不得重复出现，实际 {len(fp_issues)} 条 / "
                f"{len(set(fp_issues))} 条不同：{fp_issues}",
            )
            self.assertEqual(len(issues), len(set(issues)),
                             f"报告中出现重复的问题原文：{issues}")
            self.assertIn(code, (4, 5), f"指纹报红应归 rc 4 或被替身抬到 rc 5，实际 {code}")
            summary = rb._render_readback_summary(on_disk)
            self.assertIn(f"{len(issues)} 项原因", summary)
            self.assertNotIn(f"{len(issues) * 2} 项原因", summary,
                             "修前重复追加会让摘要原因计数翻倍")


class CliEntryPointTest(unittest.TestCase):
    """回归锚点：`python3 -m eval.readback` 必须有真入口（T14b）。

    修前该模块没有 `if __name__ == "__main__":` 块——`python3 -m` 只导入模块就
    静默退出 rc 0，一行不执行、一个文件不写。本用例在子进程里跑一条**必然失败**
    的命令（manifest 不存在），断言退出码非 0 且 stderr 非空：
    入口丢失时进程会 rc 0 空输出，两个断言都会红。
    """

    def test_module_entrypoint_runs_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "out"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "eval.readback",
                    "--manifest",
                    "/tmp/不存在.jsonl",
                    "--asr",
                    "x:y",
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                timeout=60,
            )
        self.assertNotEqual(
            result.returncode,
            0,
            f"python3 -m eval.readback 静默 rc 0（入口丢失，本应失败报错）：\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertTrue(
            result.stderr.strip(),
            f"python3 -m eval.readback 失败但 stderr 为空：rc={result.returncode}\n"
            f"stdout={result.stdout!r}",
        )


if __name__ == "__main__":
    unittest.main()
