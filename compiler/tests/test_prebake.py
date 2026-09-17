"""
compiler.tests.test_prebake — 预铸流水线的集成测试

覆盖范围：
  - 枚举正确性（key × variant × rate）与 tts_calls == synthesized
  - 产物过资产层校验（load_pack / validate_pack / path 内容寻址格式）
  - 幂等（第二次 synthesized=0 / reused=total / 指纹集合相同）
  - 差量重铸（改一个 variant → synthesized=1 / reused=total-1 / 只有一条指纹变化）
  - 质检门真会拦（500ms 头静音 → clean=False → fail-closed → manifest 不存在 + PrebakeError）
  - 失败不静默（synthesize 抛错 → failed 明细；allow_partial=True 写包但不含该条）
  - 预铸账 loudness_normalized 恒为 false
  - 旧包损坏 → 全量重铸且留痕；引擎音色/模型版本变化 → 不复用
  - 真实 MacSayTts 端到端（无 say 时 skip）
"""

import array
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from assets import load_pack, validate_pack
from compiler.prebake import PrebakeError, PrebakeReport, prebake
from compiler.source import load_source


# ---------------------------------------------------------------------------
# 辅助：自造 WAV（wave + array，可精确控制头静音）
# ---------------------------------------------------------------------------
SR = 16000
AMP = 15000            # 约 -6.8 dBFS，落在 [-20, -1] 峰值带内
TONE_SAMPLES = 8001    # 0.5 s（奇数：440Hz 首尾样本均非零，避免被误判为静音）
HEAD_ZERO = 80         # 5 ms
TAIL_ZERO = 320        # 20 ms


def _tone(amp: int, n: int, freq: int = 440) -> array.array:
    """生成 n 个样本的正弦波（相位偏移 1 个采样点，避免首样本为 0 被误判静音）。"""
    return array.array(
        "h", [int(amp * math.sin(2 * math.pi * freq * (i + 1) / SR)) for i in range(n)]
    )


def _clean_wav(path: Path, head_zero: int = HEAD_ZERO) -> None:
    """写一段可通过质检的 WAV；head_zero 控制头静音（样本数）。"""
    samples = (
        array.array("h", b"\x00\x00" * head_zero)
        + _tone(AMP, TONE_SAMPLES)
        + array.array("h", b"\x00\x00" * TAIL_ZERO)
    )
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(samples.tobytes())


# ---------------------------------------------------------------------------
# 辅助：TTS 替身（不依赖外部命令，可精确注入失败/坏音频）
# ---------------------------------------------------------------------------
class FakeTts:
    """FakeTts：产出可通过质检的干净 WAV 的 TTS 替身。

    fail_texts: 这些文本让 synthesize 抛错（模拟引擎失败）
    bad_texts:  这些文本产出 500 ms 头静音（模拟质检不过）
    """

    name = "fake"
    model_version = "macos-say"
    voice = "Tingting"
    rate_map = {"slow": 150, "normal": 200, "fast": 300}
    requires_core = "^0.1"

    def __init__(self, fail_texts=(), bad_texts=(), voice=None,
                 model_version=None, force_bad=False):
        self.fail_texts = set(fail_texts)
        self.bad_texts = set(bad_texts)
        self.force_bad = force_bad  # True 时全部文本都产出坏音频
        if voice is not None:
            self.voice = voice
        if model_version is not None:
            self.model_version = model_version
        self.calls = []

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        self.calls.append((text, rate_key))
        if text in self.fail_texts:
            raise RuntimeError(f"引擎模拟故障: {text}")
        # 8000 / 16000 = 500 ms 头静音，远超 100 ms 阈值
        head = 8000 if (text in self.bad_texts or self.force_bad) else HEAD_ZERO
        _clean_wav(Path(out_path), head_zero=head)


# ---------------------------------------------------------------------------
# 辅助：自造业务包源
# ---------------------------------------------------------------------------
def _write_source(root: Path, phrases, voice="Tingting",
                  model_version="macos-say", rates=("normal", "slow")) -> None:
    """在 root 下写出 pack.json + phrases.json。"""
    pack = {
        "pack_id": "repair",
        "pack_version": "1",
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": voice,
        "model_version": model_version,
        "rates": list(rates),
    }
    with open(root / "pack.json", "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
    with open(root / "phrases.json", "w", encoding="utf-8") as f:
        json.dump({"phrases": phrases}, f, ensure_ascii=False, indent=2)


def _manifest_fingerprints(out: Path) -> set:
    """读取 manifest.json 的指纹集合。"""
    with open(out / "manifest.json", "r", encoding="utf-8") as f:
        man = json.load(f)
    return {a["fingerprint"] for a in man["assets"]}


class _Base(unittest.TestCase):
    """提供 tempfile 目录与清理。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="compiler_prebake_")
        self.tmp_dir = Path(self._tmp)
        self.source_dir = self.tmp_dir / "src"
        self.source_dir.mkdir()
        self.out_dir = self.tmp_dir / "out"
        self.out_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


# ============================================================
# 1. 枚举与基本成功路径
# ============================================================
class TestPrebakeEnumeration(_Base):
    """条目枚举 = key × variant × rate，tts_calls 与 synthesized 同值。"""

    def test_enumerates_key_variant_rate(self):
        """1 key × 2 variant × 2 rate = 4 条。"""
        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好", "你好"], "rates": ["slow", "normal"]}],
            rates=["slow", "normal"],
        )
        source = load_source(self.source_dir)
        tts = FakeTts()
        report = prebake(source, tts, self.out_dir)

        self.assertIsInstance(report, PrebakeReport)
        self.assertEqual(report.total, 4)
        self.assertEqual(report.synthesized, 4)
        self.assertEqual(report.reused, 0)
        self.assertEqual(report.tts_calls, 4)
        self.assertTrue(report.clean)
        self.assertEqual(len(tts.calls), 4)
        # tts_calls 必须与 synthesized 同值（可断言用）
        self.assertEqual(report.tts_calls, report.synthesized)

    def test_phases_report_fields_complete(self):
        """报告字段全量齐备，且与源元数据一致。"""
        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好"], "rates": ["normal"]}],
        )
        source = load_source(self.source_dir)
        report = prebake(source, FakeTts(), self.out_dir)

        self.assertEqual(report.pack_id, "repair")
        self.assertEqual(report.pack_version, "1")
        self.assertEqual(report.protocol_version, "0.1")
        self.assertEqual(report.ruleset_version, "v1")
        self.assertEqual(report.voice, "Tingting")
        self.assertEqual(report.model_version, "macos-say")
        self.assertTrue(report.created_at)
        self.assertEqual(report.failed, [])
        self.assertEqual(report.quality_issues, [])
        self.assertFalse(report.loudness_normalized)

    def test_content_addressing_dedups_identical_audio(self):
        """同文本+同语速+同音色 → 同指纹 → 磁盘上只落一份音频。"""
        phrases = [
            {"key": "a", "variants": ["完全相同的文本"], "rates": ["normal"]},
            {"key": "b", "variants": ["完全相同的文本"], "rates": ["normal"]},
        ]
        _write_source(self.source_dir, phrases, rates=["normal"])
        source = load_source(self.source_dir)
        report = prebake(source, FakeTts(), self.out_dir)

        self.assertEqual(report.total, 2)
        wav_files = list((self.out_dir / "audio").glob("*.wav"))
        self.assertEqual(len(wav_files), 1, "同指纹必须内容寻址去重")
        pack = load_pack(self.out_dir)
        paths = {e.path for e in pack.assets}
        self.assertEqual(len(paths), 1, "两条 manifest 条目应指向同一音频文件")


# ============================================================
# 2. 产物过资产层校验
# ============================================================
class TestOutputPackValid(_Base):
    """预铸产物必须能过资产层校验，且 path 为内容寻址格式。"""

    def setUp(self):
        super().setUp()
        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好，请问需要什么帮助"], "rates": ["normal"]}],
            rates=["normal"],
        )
        self.source = load_source(self.source_dir)
        self.report = prebake(self.source, FakeTts(), self.out_dir)

    def test_load_pack_succeeds(self):
        """assets.load_pack 必须成功装载预铸产物。"""
        pack = load_pack(self.out_dir)
        self.assertEqual(pack.pack_id, "repair")
        self.assertGreater(len(pack.assets), 0)

    def test_validate_pack_no_issues(self):
        """assets.validate_pack 必须返回空列表。"""
        self.assertEqual(validate_pack(self.out_dir), [])

    def test_manifest_assets_nonempty(self):
        """manifest.json 的 assets 必须非空。"""
        with open(self.out_dir / "manifest.json", "r", encoding="utf-8") as f:
            man = json.load(f)
        self.assertIsInstance(man["assets"], list)
        self.assertGreater(len(man["assets"]), 0)

    def test_manifest_paths_are_content_addressed(self):
        """每条 path 必须形如 audio/<16位hex>.wav。"""
        with open(self.out_dir / "manifest.json", "r", encoding="utf-8") as f:
            man = json.load(f)
        for entry in man["assets"]:
            self.assertRegex(
                entry["path"], r"^audio/[0-9a-f]{16}\.wav$",
                f"path 不是内容寻址格式: {entry['path']!r}",
            )
            self.assertTrue((self.out_dir / entry["path"]).exists())

    def test_manifest_entry_fields_complete(self):
        """manifest 条目字段齐全，duration_ms 为正整数。"""
        with open(self.out_dir / "manifest.json", "r", encoding="utf-8") as f:
            man = json.load(f)
        for entry in man["assets"]:
            for field in ("key", "part_index", "rate_key", "variant", "text",
                          "fingerprint", "path", "duration_ms"):
                self.assertIn(field, entry)
            self.assertEqual(entry["part_index"], 0)
            self.assertIsInstance(entry["duration_ms"], int)
            self.assertGreater(entry["duration_ms"], 0)
            self.assertEqual(len(entry["fingerprint"]), 16)

    def test_staging_cleaned_after_success(self):
        """成功后暂存目录必须清理。"""
        self.assertFalse(
            (self.out_dir / ".staging").exists(),
            "成功后 .staging 应被清理（避免残留半成品被下次装载误用）",
        )


# ============================================================
# 3. 幂等
# ============================================================
class TestIdempotent(_Base):
    """同一源连续 prebake 两次 → 第二次全复用、零 TTS 调用。"""

    def setUp(self):
        super().setUp()
        _write_source(
            self.source_dir,
            [
                {
                    "key": "greeting",
                    "variants": ["您好，请问需要什么帮助", "请提供您的手机号码"],
                    "rates": ["slow", "normal"],
                }
            ],
            rates=["slow", "normal"],
        )
        self.source = load_source(self.source_dir)

    def test_second_run_reuses_all(self):
        """第二次 prebake：synthesized=0 / reused=total / clean=True。"""
        r1 = prebake(self.source, FakeTts(), self.out_dir)
        r2 = prebake(self.source, FakeTts(), self.out_dir)

        self.assertEqual(r1.synthesized, 4)
        self.assertEqual(r2.synthesized, 0)
        self.assertEqual(r2.reused, r1.total)
        self.assertEqual(r2.tts_calls, 0)
        self.assertTrue(r2.clean)

    def test_fingerprint_sets_identical(self):
        """两次 manifest 的指纹集合必须相同（可复现）。"""
        prebake(self.source, FakeTts(), self.out_dir)
        fps1 = _manifest_fingerprints(self.out_dir)
        prebake(self.source, FakeTts(), self.out_dir)
        fps2 = _manifest_fingerprints(self.out_dir)

        self.assertGreater(len(fps1), 0)
        self.assertEqual(fps1, fps2)

    def test_second_run_zero_tts_calls(self):
        """第二次预铸不得调用 TTS（差量复用的核心可测判据）。"""
        prebake(self.source, FakeTts(), self.out_dir)
        tts2 = FakeTts()
        prebake(self.source, tts2, self.out_dir)
        self.assertEqual(tts2.calls, [])


# ============================================================
# 4. 差量重铸
# ============================================================
class TestDifferentialRebake(_Base):
    """改一个 variant 文本 → 只重铸该条，其余复用。"""

    def setUp(self):
        super().setUp()
        # 3 variant × 1 rate = 3 条；只改第一个 variant 的文本
        self.phrases_v1 = [
            {
                "key": "greeting",
                "variants": ["您好", "请提供您的手机号码", "是的"],
                "rates": ["normal"],
            }
        ]
        self.phrases_v2 = [
            {
                "key": "greeting",
                "variants": ["您好呀", "请提供您的手机号码", "是的"],
                "rates": ["normal"],
            }
        ]

    def test_only_changed_item_resynthesized(self):
        """改一条文本后：synthesized=1 / reused=total-1。"""
        d1 = self.tmp_dir / "src1"
        d1.mkdir()
        _write_source(d1, self.phrases_v1, rates=["normal"])
        d2 = self.tmp_dir / "src2"
        d2.mkdir()
        _write_source(d2, self.phrases_v2, rates=["normal"])

        r1 = prebake(load_source(d1), FakeTts(), self.out_dir)
        r2 = prebake(load_source(d2), FakeTts(), self.out_dir)
        self.assertEqual(r1.total, 3)
        self.assertEqual(r2.synthesized, 1)
        self.assertEqual(r2.reused, 2)
        self.assertTrue(r2.clean)

    def test_only_changed_fingerprint_differs(self):
        """两次 manifest 恰好只有一条指纹变化。"""
        d1 = self.tmp_dir / "src1"
        d1.mkdir()
        _write_source(d1, self.phrases_v1, rates=["normal"])
        d2 = self.tmp_dir / "src2"
        d2.mkdir()
        _write_source(d2, self.phrases_v2, rates=["normal"])

        prebake(load_source(d1), FakeTts(), self.out_dir)
        fps1 = _manifest_fingerprints(self.out_dir)
        prebake(load_source(d2), FakeTts(), self.out_dir)
        fps2 = _manifest_fingerprints(self.out_dir)

        self.assertEqual(len(fps1 - fps2), 1, "应恰好有一条旧指纹失效")
        self.assertEqual(len(fps2 - fps1), 1, "应恰好有一条新指纹产生")
        self.assertEqual(len(fps1 & fps2), 2, "其余两条指纹必须不变")

    def test_voice_change_invalidates_all(self):
        """音色变化（引擎侧）→ 全部不复用。"""
        d1 = self.tmp_dir / "src1"
        d1.mkdir()
        _write_source(d1, self.phrases_v1, rates=["normal"])
        source = load_source(d1)

        prebake(source, FakeTts(voice="Tingting"), self.out_dir)
        r2 = prebake(source, FakeTts(voice="Yunxi"), self.out_dir)

        self.assertEqual(r2.reused, 0)
        self.assertEqual(r2.synthesized, r2.total)

    def test_model_version_change_invalidates_all(self):
        """模型版本变化 → 全部不复用。"""
        d1 = self.tmp_dir / "src1"
        d1.mkdir()
        _write_source(d1, self.phrases_v1, rates=["normal"])
        source = load_source(d1)

        prebake(source, FakeTts(model_version="macos-say"), self.out_dir)
        r2 = prebake(source, FakeTts(model_version="macos-say-v2"), self.out_dir)

        self.assertEqual(r2.reused, 0)
        self.assertEqual(r2.synthesized, r2.total)


# ============================================================
# 5. 旧包损坏 / 装载失败 → 全量重铸且留痕
# ============================================================
class TestOldPackReload(_Base):
    """旧包装载失败必须全量重铸并在报告里留痕（不得静默）。"""

    def setUp(self):
        super().setUp()
        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好"], "rates": ["normal"]}],
            rates=["normal"],
        )
        self.source = load_source(self.source_dir)

    def test_corrupt_manifest_full_rebake(self):
        """manifest.json 损坏 → 不复用、全量重铸，状态留痕。"""
        (self.out_dir / "manifest.json").write_text("{损坏的JSON", encoding="utf-8")
        report = prebake(self.source, FakeTts(), self.out_dir)

        self.assertEqual(report.reused, 0)
        self.assertEqual(report.synthesized, 1)
        self.assertTrue(
            report.pack_reload_status.startswith("load_failed"),
            f"旧包装载失败必须留痕: {report.pack_reload_status!r}",
        )

    def test_no_manifest_status(self):
        """无旧包时状态为 no_manifest。"""
        report = prebake(self.source, FakeTts(), self.out_dir)
        self.assertEqual(report.pack_reload_status, "no_manifest")

    def test_good_old_pack_status(self):
        """旧包可装载时状态为 loaded。"""
        prebake(self.source, FakeTts(), self.out_dir)
        report = prebake(self.source, FakeTts(), self.out_dir)
        self.assertEqual(report.pack_reload_status, "loaded")

    def test_missing_audio_file_not_reused(self):
        """旧包条目在、指纹一致，但音频文件被删 → 不复用（须重铸）。

        注意：音频缺失会让资产层 load_pack 直接抛错（冻结契约），
        因此这里是「装载失败 → 全量重铸 + 留痕」，而不是静默跳过一条。
        """
        prebake(self.source, FakeTts(), self.out_dir)
        # 删掉音频文件，模拟「包损坏 / 同步不全」
        for wav in (self.out_dir / "audio").glob("*.wav"):
            wav.unlink()

        tts = FakeTts()
        report = prebake(self.source, tts, self.out_dir)

        self.assertEqual(report.reused, 0)
        self.assertEqual(report.synthesized, 1)
        self.assertTrue(
            report.pack_reload_status.startswith("load_failed"),
            f"音频缺失导致旧包不可装载，必须留痕: {report.pack_reload_status!r}",
        )


# ============================================================
# 6. 质检门 fail-closed
# ============================================================
class TestQualityGateFailClosed(_Base):
    """质检不过 → clean=False → 默认不写包 + 抛 PrebakeError。"""

    BAD_TEXT = "这条音频头静音太长"

    def _source(self):
        _write_source(
            self.source_dir,
            [
                {
                    "key": "greeting",
                    "variants": [self.BAD_TEXT, "正常的一条话术"],
                    "rates": ["normal"],
                }
            ],
            rates=["normal"],
        )
        return load_source(self.source_dir)

    def test_fail_closed_raises_and_no_manifest(self):
        """fail-closed 下抛 PrebakeError 且 manifest.json 不存在。"""
        with self.assertRaises(PrebakeError) as ctx:
            prebake(self._source(), FakeTts(bad_texts={self.BAD_TEXT}), self.out_dir)

        self.assertFalse((self.out_dir / "manifest.json").exists())
        report = ctx.exception.report
        self.assertFalse(report.clean)
        self.assertEqual(len(report.quality_issues), 1)
        qi = report.quality_issues[0]
        self.assertEqual(qi["key"], "greeting")
        self.assertIsInstance(qi["issues"], list)
        self.assertTrue(qi["issues"])
        self.assertTrue(any("头静音" in i for i in qi["issues"]), qi["issues"])
        # 失败条不入包；通过的条目也不留下（fail-closed = 什么都不写）
        self.assertEqual(len(report.failed), 0)

    def test_report_attachable_on_exception(self):
        """异常对象必须带 report 属性，能直接取到失败明细。"""
        with self.assertRaises(PrebakeError) as ctx:
            prebake(self._source(), FakeTts(bad_texts={self.BAD_TEXT}), self.out_dir)
        self.assertIsInstance(ctx.exception.report, PrebakeReport)
        self.assertEqual(ctx.exception.report.total, 2)

    def test_staging_cleaned_on_fail_closed(self):
        """fail-closed 后暂存目录必须清理（不得留下半成品音频）。"""
        with self.assertRaises(PrebakeError):
            prebake(self._source(), FakeTts(bad_texts={self.BAD_TEXT}), self.out_dir)
        self.assertFalse(
            (self.out_dir / ".staging").exists(),
            "fail-closed 后 .staging 应被清理，避免半成品被下次装载误用",
        )
        self.assertFalse((self.out_dir / "audio").exists())

    def test_bad_item_not_silently_dropped(self):
        """质检不过的条目必须出现在 quality_issues 里（禁止静默跳过）。"""
        with self.assertRaises(PrebakeError) as ctx:
            prebake(self._source(), FakeTts(bad_texts={self.BAD_TEXT}), self.out_dir)
        report = ctx.exception.report
        # 2 条里 1 条质检不过，但 TTS 仍然被调用了 2 次（合成成功、质检拒收）
        self.assertEqual(report.synthesized, 2)
        self.assertEqual(report.tts_calls, 2)

    def test_allow_partial_writes_partial_pack(self):
        """allow_partial=True → 写包但不含坏条目，且 clean=False。"""
        report = prebake(
            self._source(), FakeTts(bad_texts={self.BAD_TEXT}),
            self.out_dir, allow_partial=True,
        )

        self.assertFalse(report.clean)
        self.assertTrue((self.out_dir / "manifest.json").exists())
        self.assertEqual(len(report.quality_issues), 1)

        with open(self.out_dir / "manifest.json", "r", encoding="utf-8") as f:
            man = json.load(f)
        variants = {(a["key"], a["variant"], a["rate_key"]) for a in man["assets"]}
        # 坏条目（variant=0）不得出现在 manifest 里
        self.assertNotIn(("greeting", 0, "normal"), variants)
        self.assertIn(("greeting", 1, "normal"), variants)
        # 部分包必须仍能过资产层校验
        self.assertEqual(validate_pack(self.out_dir), [])
        load_pack(self.out_dir)

    def test_all_fail_allow_partial_skips_manifest(self):
        """全部条目都质检不过 + allow_partial=True → 不产空包，只留预铸账。

        空 assets 过不了资产层校验（必须非空），所以是「不写 manifest」而不是
        「写一个空包」——否则下游 load_pack 必然失败。
        """
        report = prebake(
            self._source(), FakeTts(force_bad=True),
            self.out_dir, allow_partial=True,
        )

        self.assertFalse(report.clean)
        self.assertEqual(len(report.quality_issues), 2)
        self.assertFalse(
            (self.out_dir / "manifest.json").exists(),
            "全部条目都失败时不得写出空 assets 的 manifest",
        )
        self.assertTrue((self.out_dir / "prebake_ledger.json").exists())

    def test_allow_partial_ledger_records_issues(self):
        """allow_partial 的预铸账必须记录 clean=False 与质检明细。"""
        prebake(
            self._source(), FakeTts(bad_texts={self.BAD_TEXT}),
            self.out_dir, allow_partial=True,
        )
        with open(self.out_dir / "prebake_ledger.json", "r", encoding="utf-8") as f:
            ledger = json.load(f)
        self.assertFalse(ledger["clean"])
        self.assertEqual(len(ledger["quality_issues"]), 1)


# ============================================================
# 7. 合成失败不静默
# ============================================================
class TestSynthesizeFailure(_Base):
    """synthesize 抛错 → 进 failed（reason 非空），其余条目继续处理。"""

    FAIL_TEXT = "这条合成会失败"

    def _source(self):
        _write_source(
            self.source_dir,
            [
                {"key": "broken", "variants": [self.FAIL_TEXT], "rates": ["normal"]},
                {"key": "fine", "variants": ["正常的一条话术"], "rates": ["normal"]},
            ],
            rates=["normal"],
        )
        return load_source(self.source_dir)

    def test_failed_entry_recorded_with_reason(self):
        """失败条目必须进 failed，reason 非空且含异常消息。"""
        with self.assertRaises(PrebakeError) as ctx:
            prebake(self._source(), FakeTts(fail_texts={self.FAIL_TEXT}), self.out_dir)
        report = ctx.exception.report

        self.assertEqual(len(report.failed), 1)
        f = report.failed[0]
        self.assertEqual(f["key"], "broken")
        self.assertEqual(f["part_index"], 0)
        self.assertEqual(f["rate_key"], "normal")
        self.assertEqual(f["variant"], 0)
        self.assertTrue(f["reason"], "reason 不得为空")
        self.assertIn("引擎模拟故障", f["reason"])

    def test_other_items_still_processed(self):
        """一条失败不得中断整批——其余条目必须继续合成。"""
        tts = FakeTts(fail_texts={self.FAIL_TEXT})
        with self.assertRaises(PrebakeError) as ctx:
            prebake(self._source(), tts, self.out_dir)
        report = ctx.exception.report

        self.assertEqual(report.total, 2)
        self.assertEqual(report.synthesized, 1)
        self.assertEqual(report.tts_calls, 1)
        self.assertNotIn("fine", [f["key"] for f in report.failed])

    def test_allow_partial_omits_failed_entry(self):
        """allow_partial=True → 写包但 manifest 里没有失败那条。"""
        report = prebake(
            self._source(), FakeTts(fail_texts={self.FAIL_TEXT}),
            self.out_dir, allow_partial=True,
        )

        self.assertFalse(report.clean)
        self.assertTrue((self.out_dir / "manifest.json").exists())
        with open(self.out_dir / "manifest.json", "r", encoding="utf-8") as f:
            man = json.load(f)
        keys = {a["key"] for a in man["assets"]}
        self.assertNotIn("broken", keys, "失败条目不得进包")
        self.assertEqual(keys, {"fine"})
        self.assertEqual(validate_pack(self.out_dir), [])

    def test_failed_count_message(self):
        """fail-closed 异常消息应含失败条数，便于定位。"""
        with self.assertRaises(PrebakeError) as ctx:
            prebake(self._source(), FakeTts(fail_texts={self.FAIL_TEXT}), self.out_dir)
        self.assertIn("1", str(ctx.exception))


# ============================================================
# 8. 预铸账
# ============================================================
class TestPrebakeLedger(_Base):
    """预铸账 = report 全量落盘 + loudness_normalized 显式 false。"""

    def setUp(self):
        super().setUp()
        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好", "你好"], "rates": ["normal"]}],
            rates=["normal"],
        )
        self.report = prebake(load_source(self.source_dir), FakeTts(), self.out_dir)

    def test_ledger_exists(self):
        """预铸账必须落盘。"""
        self.assertTrue((self.out_dir / "prebake_ledger.json").exists())

    def test_loudness_normalized_is_false(self):
        """loudness_normalized 必须为 False（LUFS 归一明确推迟）。"""
        with open(self.out_dir / "prebake_ledger.json", "r", encoding="utf-8") as f:
            ledger = json.load(f)
        self.assertIs(ledger["loudness_normalized"], False)

    def test_ledger_contains_full_report(self):
        """预铸账必须含 report 的全部字段（含 failed / quality_issues）。"""
        with open(self.out_dir / "prebake_ledger.json", "r", encoding="utf-8") as f:
            ledger = json.load(f)
        for field in ("pack_id", "pack_version", "protocol_version", "ruleset_version",
                      "voice", "model_version", "created_at", "total", "synthesized",
                      "reused", "failed", "quality_issues", "clean", "tts_calls"):
            self.assertIn(field, ledger, f"预铸账缺字段: {field}")
        self.assertEqual(ledger["total"], 2)
        self.assertEqual(ledger["synthesized"], 2)
        self.assertEqual(ledger["reused"], 0)
        self.assertEqual(ledger["failed"], [])
        self.assertEqual(ledger["quality_issues"], [])
        self.assertTrue(ledger["clean"])

    def test_ledger_written_on_fail_closed(self):
        """fail-closed 时预铸账也要写（失败不静默），且逐条记录质检明细。"""
        out2 = self.tmp_dir / "out2"
        out2.mkdir()
        # force_bad=True：全部文本都产出坏音频，2 条全部质检不过
        with self.assertRaises(PrebakeError):
            prebake(
                load_source(self.source_dir),
                FakeTts(force_bad=True),
                out2,
            )
        self.assertTrue((out2 / "prebake_ledger.json").exists())
        with open(out2 / "prebake_ledger.json", "r", encoding="utf-8") as f:
            ledger = json.load(f)
        self.assertFalse(ledger["clean"])
        self.assertEqual(len(ledger["quality_issues"]), 2)
        self.assertEqual(ledger["synthesized"], 2)
        self.assertEqual(ledger["tts_calls"], 2)
        self.assertEqual(ledger["loudness_normalized"], False)


# ============================================================
# 9. 真实 MacSayTts 端到端（无 say / 音色不可用时 skip）
# ============================================================
def _say_usable() -> bool:
    """判断 macOS say 与默认音色是否可用（决定真实合成用例 skip 与否）。"""
    if not shutil.which("say"):
        return False
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            p = tmp.name
        try:
            r = subprocess.run(
                ["say", "-v", "Tingting", "-o", p,
                 "--file-format=WAVE", "--data-format=LEI16@16000", "测试"],
                capture_output=True, timeout=60,
            )
            return r.returncode == 0
        finally:
            if os.path.exists(p):
                os.unlink(p)
    except Exception:
        return False


_HAS_SAY = _say_usable()


@unittest.skipUnless(_HAS_SAY, "macOS say 命令或 Tingting 音色不可用，跳过真实合成用例")
class TestRealMacSayEndToEnd(_Base):
    """真实 MacSayTts 跑通全链路：合成 → 质检 → 写包 → 资产层校验。"""

    def test_real_synthesis_produces_valid_pack(self):
        """真实合成产物必须 clean 且过资产层校验。"""
        from adapters.tts_macsay import MacSayTts

        tts = MacSayTts()
        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好，请问需要什么帮助"], "rates": ["normal"]}],
            voice=tts.voice,
            model_version=tts.model_version,
            rates=["normal"],
        )
        source = load_source(self.source_dir)

        report = prebake(source, tts, self.out_dir)

        self.assertTrue(report.clean, f"真实合成产物应通过质检: {report.quality_issues}")
        self.assertEqual(report.synthesized, report.total)
        self.assertEqual(report.failed, [])
        self.assertEqual(validate_pack(self.out_dir), [])
        pack = load_pack(self.out_dir)
        self.assertGreater(len(pack.assets), 0)

    def test_real_synthesis_idempotent(self):
        """真实合成连续两次 → 第二次零 TTS 调用。"""
        from adapters.tts_macsay import MacSayTts

        _write_source(
            self.source_dir,
            [{"key": "greeting", "variants": ["您好", "好的"], "rates": ["normal"]}],
            voice=MacSayTts().voice,
            model_version=MacSayTts().model_version,
            rates=["normal"],
        )
        source = load_source(self.source_dir)

        r1 = prebake(source, MacSayTts(), self.out_dir)
        r2 = prebake(source, MacSayTts(), self.out_dir)

        self.assertEqual(r1.synthesized, 2)
        self.assertEqual(r2.synthesized, 0)
        self.assertEqual(r2.reused, 2)
        self.assertTrue(r2.clean)


if __name__ == "__main__":
    unittest.main()
