"""
compiler.tests.test_quality — 质检门的度量 + 判据测试

覆盖范围：
  - measure：正常 WAV 能度量；非 WAV / 缺文件 → QualityError（消息含路径）
  - check_quality 正例：干净音频 → 空列表
  - check_quality 负例（全部用**自造 WAV** 精确构造违规输入，断言判据名 + 实际值）：
      头静音超阈 / 尾静音超阈 / 削波 / 时长超 30s / 采样率错 / 声道错 / 位深错 /
      帧数为 0 / 峰值过低 / 峰值过高
"""

import array
import math
import tempfile
import unittest
import wave
from pathlib import Path

from compiler.quality import AudioStats, QualityError, check_quality, measure


# ---------------------------------------------------------------------------
# 辅助：自造 WAV（用 wave 写，可精确控制头/尾静音、削波、采样率等）
# ---------------------------------------------------------------------------
SR = 16000          # 合法采样率
AMP_OK = 15000      # 合法峰值（约 -6.8 dBFS，落在 [-20, -1] 带内）
HEAD_ZERO = 80      # 5 ms 头静音（<= 100 ms）
TAIL_ZERO = 320     # 20 ms 尾静音（<= 150 ms）
TONE_SAMPLES = 8001  # 0.5 s 音调（奇数：440Hz 首尾样本均非零，避免静音检测 off-by-one）


def _tone(amp: int, n: int, freq: int = 440) -> array.array:
    """生成 n 个样本的正弦波。

    相位从第 1 个采样点起（而非 0）：sin(0) = 0 会让首样本被误判为静音，
    偏移 1 个采样点后静音检测的长度才与构造值精确相等。
    """
    return array.array(
        "h", [int(amp * math.sin(2 * math.pi * freq * (i + 1) / SR)) for i in range(n)]
    )


def _write_wav(path: Path, samples: array.array, framerate: int = SR,
               channels: int = 1, sampwidth: int = 2) -> Path:
    """把样本写成 WAV 文件。"""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(samples.tobytes())
    return path


def _clean_samples() -> array.array:
    """构造一段「应该通过质检」的音频：短头静音 + 正常音调 + 短尾静音。"""
    return array.array("h", b"\x00\x00" * HEAD_ZERO) + _tone(AMP_OK, TONE_SAMPLES) + \
        array.array("h", b"\x00\x00" * TAIL_ZERO)


class _TmpDirTestBase(unittest.TestCase):
    """提供 tempfile 目录与清理。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="compiler_quality_")
        self.tmp_dir = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


# ============================================================
# 1. measure 正例
# ============================================================
class TestMeasure(_TmpDirTestBase):
    """measure 必须正确读出 WAV 属性与静音/峰值/削波统计。"""

    def test_measure_clean_wav(self):
        """干净 WAV 的度量结果各项合理。"""
        p = _write_wav(self.tmp_dir / "clean.wav", _clean_samples())
        stats = measure(p)

        self.assertIsInstance(stats, AudioStats)
        self.assertEqual(stats.framerate, SR)
        self.assertEqual(stats.channels, 1)
        self.assertEqual(stats.sampwidth, 2)
        self.assertEqual(
            stats.frames, HEAD_ZERO + TONE_SAMPLES + TAIL_ZERO
        )
        self.assertAlmostEqual(stats.head_silence_ms, 5.0, places=1)
        self.assertAlmostEqual(stats.tail_silence_ms, 20.0, places=1)
        self.assertAlmostEqual(stats.peak_dbfs, -6.8, delta=0.5)
        self.assertEqual(stats.clipped_samples, 0)
        self.assertEqual(stats.duration_ms, 525)

    def test_measure_accepts_str_path(self):
        """measure 接受字符串路径。"""
        p = _write_wav(self.tmp_dir / "s.wav", _clean_samples())
        stats = measure(str(p))
        self.assertEqual(stats.frames, HEAD_ZERO + TONE_SAMPLES + TAIL_ZERO)


# ============================================================
# 2. measure 负例（非 WAV / 打不开）
# ============================================================
class TestMeasureErrors(_TmpDirTestBase):
    """非 WAV / 缺文件必须抛 QualityError，且消息含路径。"""

    def test_non_wav_raises_quality_error(self):
        """非 WAV 文件 → QualityError，消息含文件路径。"""
        p = self.tmp_dir / "not_a_wav.wav"
        p.write_text("这不是 WAV 文件", encoding="utf-8")
        with self.assertRaises(QualityError) as ctx:
            measure(p)
        self.assertIn("not_a_wav.wav", str(ctx.exception))

    def test_missing_file_raises_quality_error(self):
        """文件不存在 → QualityError，消息含文件路径。"""
        p = self.tmp_dir / "missing.wav"
        with self.assertRaises(QualityError) as ctx:
            measure(p)
        self.assertIn("missing.wav", str(ctx.exception))

    def test_directory_raises_quality_error(self):
        """目录 → QualityError。"""
        d = self.tmp_dir / "a_dir"
        d.mkdir()
        with self.assertRaises(QualityError):
            measure(d)


# ============================================================
# 3. check_quality 正例
# ============================================================
class TestCheckQualityPass(_TmpDirTestBase):
    """干净音频必须通过全部判据（返回空列表）。"""

    def test_clean_audio_returns_empty(self):
        """干净 WAV → check_quality 返回空列表。"""
        p = _write_wav(self.tmp_dir / "clean.wav", _clean_samples())
        issues = check_quality(measure(p))
        self.assertEqual(issues, [])

    def test_boundary_head_silence_100ms_passes(self):
        """头静音恰好 100 ms（= 阈值）→ 通过（判据是 <=）。"""
        head = int(0.100 * SR)  # 1600
        samples = array.array("h", b"\x00\x00" * head) + _tone(AMP_OK, TONE_SAMPLES)
        p = _write_wav(self.tmp_dir / "boundary_head.wav", samples)
        issues = check_quality(measure(p))
        self.assertEqual(issues, [], f"头静音恰好在阈值应通过: {issues}")


# ============================================================
# 4. check_quality 负例（每类违规输入一条）
# ============================================================
class TestHeadSilenceOverThreshold(_TmpDirTestBase):
    """头静音 500 ms > 100 ms 阈值 → 必须拦住。"""

    def test_head_silence_500ms_rejected(self):
        """头静音 500 ms → 返回非空，消息含判据名与实际值。"""
        head = int(0.500 * SR)  # 8000 样本 = 500 ms
        samples = (
            array.array("h", b"\x00\x00" * head)
            + _tone(AMP_OK, TONE_SAMPLES)
            + array.array("h", b"\x00\x00" * TAIL_ZERO)
        )
        p = _write_wav(self.tmp_dir / "long_head.wav", samples)
        issues = check_quality(measure(p))

        self.assertTrue(issues, "500 ms 头静音必须被拦住")
        head_issues = [i for i in issues if "头静音" in i]
        self.assertEqual(len(head_issues), 1, f"应只有头静音一条: {issues}")
        # 消息必须含判据名与实际值
        self.assertIn("100", head_issues[0])
        self.assertIn("500.0", head_issues[0])


class TestTailSilenceOverThreshold(_TmpDirTestBase):
    """尾静音 313 ms > 150 ms 阈值 → 必须拦住。"""

    def test_tail_silence_313ms_rejected(self):
        """尾静音 313 ms → 返回非空，消息含判据名与实际值。"""
        tail = int(0.313 * SR)  # 5008 样本 = 313.0 ms（> 150 ms 阈值）
        samples = (
            array.array("h", b"\x00\x00" * HEAD_ZERO)
            + _tone(AMP_OK, TONE_SAMPLES)
            + array.array("h", b"\x00\x00" * tail)
        )
        p = _write_wav(self.tmp_dir / "long_tail.wav", samples)
        issues = check_quality(measure(p))

        self.assertTrue(issues)
        tail_issues = [i for i in issues if "尾静音" in i]
        self.assertEqual(len(tail_issues), 1, f"应只有尾静音一条: {issues}")
        self.assertIn("150", tail_issues[0])
        self.assertIn("313.0", tail_issues[0])


class TestClipping(_TmpDirTestBase):
    """削波：满幅样本数必须为 0。"""

    def test_clipped_samples_rejected(self):
        """含满幅样本 → 返回非空，消息含判据名与实际值。"""
        samples = _clean_samples()
        # 注入 7 个满幅样本（削波）
        for i in range(HEAD_ZERO, HEAD_ZERO + 7):
            samples[i] = 32767
        p = _write_wav(self.tmp_dir / "clipped.wav", samples)

        stats = measure(p)
        self.assertEqual(stats.clipped_samples, 7)
        issues = check_quality(stats)

        clip_issues = [i for i in issues if "削波" in i]
        self.assertEqual(len(clip_issues), 1, f"应只有削波一条: {issues}")
        self.assertIn("满幅样本数=0", clip_issues[0])
        self.assertIn("实际=7", clip_issues[0])

    def test_negative_clipped_sample_counts(self):
        """负满幅样本（-32768）同样计为削波。"""
        samples = _clean_samples()
        samples[HEAD_ZERO] = -32768
        p = _write_wav(self.tmp_dir / "neg_clip.wav", samples)
        stats = measure(p)
        self.assertEqual(stats.clipped_samples, 1)
        self.assertTrue(check_quality(stats))


class TestDurationOverLimit(_TmpDirTestBase):
    """时长 > 30000 ms → 必须拦住。"""

    def test_duration_31250ms_rejected(self):
        """31.25 s 音频 → 返回非空，消息含判据名与实际值。"""
        n = 500000  # 500000 / 16000 * 1000 = 31250 ms
        samples = _clean_samples()
        # 用静音填充到超长（静音不影响峰值/削波判据，便于隔离时长判据）
        pad = array.array("h", b"\x00\x00" * (n - len(samples)))
        p = _write_wav(self.tmp_dir / "long.wav", samples + pad)

        stats = measure(p)
        self.assertGreater(stats.duration_ms, 30000)
        issues = check_quality(stats)

        dur_issues = [i for i in issues if "时长超限" in i]
        self.assertEqual(len(dur_issues), 1, f"应只有时长一条: {issues}")
        self.assertIn("30000", dur_issues[0])
        self.assertIn(str(stats.duration_ms), dur_issues[0])


class TestFormatCriteria(_TmpDirTestBase):
    """采样率 / 声道 / 位深不符 → 必须拦住。"""

    def test_wrong_sample_rate(self):
        """8000 Hz → 返回非空，消息含判据名与实际值。"""
        p = _write_wav(self.tmp_dir / "sr8k.wav", _clean_samples(), framerate=8000)
        issues = check_quality(measure(p))
        sr_issues = [i for i in issues if "采样率" in i]
        self.assertEqual(len(sr_issues), 1)
        self.assertIn("16000", sr_issues[0])
        self.assertIn("8000", sr_issues[0])

    def test_stereo_rejected(self):
        """立体声（2 声道）→ 返回非空。"""
        samples = _clean_samples() * 2  # 复制成两倍长度模拟立体声数据
        p = _write_wav(self.tmp_dir / "stereo.wav", samples, channels=2)
        issues = check_quality(measure(p))
        ch_issues = [i for i in issues if "声道" in i]
        self.assertEqual(len(ch_issues), 1)
        self.assertIn("实际=2", ch_issues[0])

    def test_wrong_bitdepth(self):
        """8-bit 位深 → 返回非空。"""
        # 8-bit 用无符号字节，构造等价的正弦数据
        n = TONE_SAMPLES
        raw8 = bytearray(int(128 + 127 * math.sin(2 * math.pi * 440 * i / SR))
                         for i in range(n))
        with wave.open(str(self.tmp_dir / "bit8.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(1)
            wf.setframerate(SR)
            wf.writeframes(bytes(raw8))
        issues = check_quality(measure(self.tmp_dir / "bit8.wav"))
        bw_issues = [i for i in issues if "位深" in i]
        self.assertEqual(len(bw_issues), 1)
        self.assertIn("实际=1 字节", bw_issues[0])


class TestZeroFrames(_TmpDirTestBase):
    """帧数为 0 → 帧数判据 + 峰值判据都必须触发。"""

    def test_empty_wav_rejected(self):
        """空 WAV（0 帧）→ 返回非空，含帧数与峰值两条问题。"""
        p = _write_wav(self.tmp_dir / "empty.wav", array.array("h"))
        stats = measure(p)
        self.assertEqual(stats.frames, 0)
        self.assertEqual(stats.peak_dbfs, float("-inf"))

        issues = check_quality(stats)
        self.assertTrue(any("帧数" in i for i in issues), f"缺帧数判据: {issues}")
        self.assertTrue(any("峰值" in i for i in issues), f"缺峰值判据: {issues}")


class TestPeakBand(_TmpDirTestBase):
    """峰值电平必须在 [-20, -1] dBFS 带内。"""

    def test_peak_too_low(self):
        """峰值过低（约 -50 dBFS）→ 返回非空，消息含实际值。"""
        samples = array.array("h", b"\x00\x00" * HEAD_ZERO) + _tone(100, TONE_SAMPLES)
        p = _write_wav(self.tmp_dir / "quiet.wav", samples)
        issues = check_quality(measure(p))
        pk = [i for i in issues if "峰值" in i]
        self.assertEqual(len(pk), 1, f"应只有峰值一条: {issues}")
        self.assertIn("-20", pk[0])
        self.assertIn("-50", pk[0])

    def test_peak_too_high(self):
        """峰值过高（约 -0.76 dBFS，贴顶但未削波）→ 返回非空。"""
        samples = (
            array.array("h", b"\x00\x00" * HEAD_ZERO)
            + _tone(30000, TONE_SAMPLES)
            + array.array("h", b"\x00\x00" * TAIL_ZERO)
        )
        p = _write_wav(self.tmp_dir / "loud.wav", samples)
        stats = measure(p)
        self.assertEqual(stats.clipped_samples, 0, "本用例应只触发峰值判据")
        issues = check_quality(stats)
        pk = [i for i in issues if "峰值" in i]
        self.assertEqual(len(pk), 1, f"应只有峰值一条: {issues}")
        self.assertIn("-1", pk[0])


if __name__ == "__main__":
    unittest.main()
