"""
runtime.tests.test_audio — WAV 读取/拼接/静音垫/淡入淡出/click 判据

覆盖范围：
  - read_wav 正例：16kHz/单声道/16-bit 可读
  - read_wav 负例：采样率/声道/位深不符一律抛 AudioError（不得重采样、不得静默接受）
  - silence：样本数精确等于时长换算，全零
  - apply_fade：首尾样本落到 0、长度不变、不改入参、短片段自动收敛、fade_ms=0 恒等
  - concat_wavs：写盘格式正确、总长等于片段之和、静音垫样本数精确保留
  - max_sample_jump：已知向量可算，空/单样本返回 0
  - click 判据：淡入淡出把拼接边界跳变压到阈值以下，关闭则超过阈值
  - 不得 import compiler/（本层只依赖 core/）
"""

import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from runtime.audio import (
    SAMPLE_RATE,
    AudioError,
    apply_fade,
    concat_wavs,
    max_sample_jump,
    read_wav,
    silence,
)


# ---------------------------------------------------------------------------
# 辅助：手搓 WAV（测试用 tempfile，不落仓库）
# ---------------------------------------------------------------------------
def _write_wav(path, samples, *, channels=1, sampwidth=2, framerate=SAMPLE_RATE):
    """按指定格式写一个 WAV（用于构造合法与非法两种样本）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(samples.tobytes())
    return path


def _tone(level, ms=100):
    """固定电平样本：不同电平用于判别"播的是哪条音频"。"""
    return array("h", [level]) * int(round(ms * SAMPLE_RATE / 1000))


def _zero_run(samples, start):
    """从 start 起连续零样本的个数（用于核对静音垫样本数）。"""
    count = 0
    for value in samples[start:]:
        if value != 0:
            break
        count += 1
    return count


# ---------------------------------------------------------------------------
# 1. read_wav 正例
# ---------------------------------------------------------------------------
class TestReadWavPositive(unittest.TestCase):
    """合法 WAV 必须读出样本数组与采样率。"""

    def test_read_valid_wav(self):
        """16kHz/单声道/16-bit 可读，样本数与写入一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _tone(1000, 50)
            path = _write_wav(Path(tmp) / "a.wav", samples)
            read_back, framerate = read_wav(path)
            self.assertEqual(len(read_back), len(samples))
            self.assertEqual(framerate, SAMPLE_RATE)
            self.assertEqual(read_back[0], 1000)


# ---------------------------------------------------------------------------
# 2. read_wav 负例（不得重采样、不得静默接受）
# ---------------------------------------------------------------------------
class TestReadWavRejected(unittest.TestCase):
    """非 16kHz/单声道/16-bit 一律抛 AudioError，且消息含实际值。"""

    def _assert_rejected(self, samples, kwargs, must_contain):
        """统一的负例断言：抛 AudioError 且消息含关键值。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_wav(Path(tmp) / "bad.wav", samples, **kwargs)
            with self.assertRaises(AudioError) as ctx:
                read_wav(path)
            for token in must_contain:
                self.assertIn(token, str(ctx.exception),
                              f"错误消息应含 {token!r}，实际: {str(ctx.exception)!r}")

    def test_rejects_wrong_framerate(self):
        """8000Hz 必须被拒（不得重采样到 16k）。"""
        self._assert_rejected(_tone(100), {"framerate": 8000}, ["8000"])

    def test_rejects_stereo(self):
        """双声道必须被拒。"""
        stereo = array("h", [1, 1]) * 100
        self._assert_rejected(stereo, {"channels": 2}, ["2", "声道"])

    def test_rejects_8bit(self):
        """8-bit 位深必须被拒。"""
        narrow = array("b", [100]) * 100
        self._assert_rejected(narrow, {"sampwidth": 1}, ["8-bit"])

    def test_rejects_missing_file(self):
        """文件不存在必须抛 AudioError，消息含路径。"""
        with self.assertRaises(AudioError) as ctx:
            read_wav("/nonexistent/nope.wav")
        self.assertIn("nope.wav", str(ctx.exception))

    def test_rejects_non_wave_file(self):
        """非 WAV 内容必须抛 AudioError，不得静默当成空音频。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not_wav.wav"
            path.write_bytes(b"this is definitely not a wave file")
            with self.assertRaises(AudioError):
                read_wav(path)

    def test_rejects_zero_sample_wav(self):
        """0 样本的 WAV 必须被拒（空音频无法播放）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_wav(Path(tmp) / "empty.wav", array("h"))
            with self.assertRaises(AudioError) as ctx:
                read_wav(path)
            self.assertIn("0 样本", str(ctx.exception))


# ---------------------------------------------------------------------------
# 3. silence 静音垫
# ---------------------------------------------------------------------------
class TestSilence(unittest.TestCase):
    """静音垫的样本数必须精确等于时长换算。"""

    def test_length_matches_duration(self):
        """200ms @16kHz = 3200 样本，且全零。"""
        pad = silence(200, SAMPLE_RATE)
        self.assertEqual(len(pad), 3200)
        self.assertTrue(all(s == 0 for s in pad))

    def test_zero_ms_is_empty(self):
        """0ms 静音垫为空数组。"""
        self.assertEqual(len(silence(0, SAMPLE_RATE)), 0)

    def test_rejects_wrong_framerate(self):
        """静音垫不接受非 16kHz（禁止按别的采样率算样本数）。"""
        with self.assertRaises(AudioError) as ctx:
            silence(200, 8000)
        self.assertIn("8000", str(ctx.exception))

    def test_rejects_negative_ms(self):
        """负时长非法。"""
        with self.assertRaises(AudioError) as ctx:
            silence(-1, SAMPLE_RATE)
        self.assertIn("-1", str(ctx.exception))


# ---------------------------------------------------------------------------
# 4. apply_fade 淡入淡出
# ---------------------------------------------------------------------------
class TestApplyFade(unittest.TestCase):
    """线性淡入淡出：首尾必须落到 0，长度不变，不改入参。"""

    def test_ends_reach_zero(self):
        """首样本与尾样本必须为 0（否则拼接边界跳变没被消除）。"""
        faded = apply_fade(_tone(1000, 100), fade_ms=25, framerate=SAMPLE_RATE)
        self.assertEqual(faded[0], 0)
        self.assertEqual(faded[-1], 0)

    def test_length_unchanged(self):
        """淡入淡出不改变样本数。"""
        samples = _tone(1000, 100)
        self.assertEqual(len(apply_fade(samples, 25, SAMPLE_RATE)), len(samples))

    def test_inner_samples_untouched(self):
        """淡入淡出区间之外的样本必须原样保留。"""
        samples = _tone(1234, 100)
        faded = apply_fade(samples, fade_ms=25, framerate=SAMPLE_RATE)
        fade_samples = int(round(25 * SAMPLE_RATE / 1000))
        self.assertEqual(faded[fade_samples], 1234)
        self.assertEqual(faded[len(samples) - fade_samples - 1], 1234)

    def test_does_not_mutate_input(self):
        """必须返回新数组，不得改动入参。"""
        samples = _tone(1000, 100)
        apply_fade(samples, 25, SAMPLE_RATE)
        self.assertEqual(samples[0], 1000)

    def test_zero_fade_is_identity(self):
        """fade_ms=0 表示关闭，输出必须与输入逐样本相等。"""
        samples = _tone(1000, 100)
        self.assertEqual(list(apply_fade(samples, 0, SAMPLE_RATE)), list(samples))

    def test_short_segment_clamps_fade(self):
        """片段比淡入淡出短时自动收敛，不得让首尾互相覆盖。"""
        short = array("h", [100, 200, 300, 400])
        faded = apply_fade(short, fade_ms=500, framerate=SAMPLE_RATE)
        self.assertEqual(len(faded), 4)
        self.assertEqual(faded[0], 0)
        self.assertEqual(faded[-1], 0)

    def test_rejects_negative_fade(self):
        """负淡入淡出时长非法。"""
        with self.assertRaises(AudioError) as ctx:
            apply_fade(_tone(100, 10), -5, SAMPLE_RATE)
        self.assertIn("-5", str(ctx.exception))


# ---------------------------------------------------------------------------
# 5. concat_wavs 拼接与写盘
# ---------------------------------------------------------------------------
class TestConcatWavs(unittest.TestCase):
    """按顺序拼接并写盘 16kHz/单声道/16-bit。"""

    def test_writes_valid_mono_16bit_16k(self):
        """输出必须能被 read_wav 读回，格式合规。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            concat_wavs([_tone(100, 50), _tone(200, 50)], out,
                        framerate=SAMPLE_RATE, fade_ms=0)
            samples, framerate = read_wav(out)
            self.assertEqual(framerate, SAMPLE_RATE)
            with wave.open(str(out), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)

    def test_total_length_is_sum_of_segments(self):
        """总样本数必须等于各片段之和（不含任何额外填充）。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            a, b = _tone(100, 50), _tone(200, 50)
            concat_wavs([a, b], out, framerate=SAMPLE_RATE, fade_ms=0)
            samples, _ = read_wav(out)
            self.assertEqual(len(samples), len(a) + len(b))

    def test_silence_pad_kept_exactly(self):
        """静音垫样本数必须精确保留——全零片段不参与淡入淡出。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            seg_a = _tone(100, 50)                      # 800 样本
            pad = silence(200, SAMPLE_RATE)              # 3200 样本
            concat_wavs([seg_a, pad, _tone(200, 50)], out,
                        framerate=SAMPLE_RATE, fade_ms=25)
            samples, _ = read_wav(out)
            self.assertTrue(
                all(s == 0 for s in samples[len(seg_a):len(seg_a) + len(pad)]),
                "静音垫区域必须全零",
            )

    def test_applies_fade_to_audio_segments(self):
        """音频片段的首尾样本必须被淡到 0。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            concat_wavs([_tone(1000, 100)], out,
                        framerate=SAMPLE_RATE, fade_ms=25)
            samples, _ = read_wav(out)
            self.assertEqual(samples[0], 0)
            self.assertEqual(samples[-1], 0)

    def test_rejects_wrong_framerate(self):
        """不得按非 16kHz 写盘（禁止重采样）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AudioError) as ctx:
                concat_wavs([_tone(100, 50)], Path(tmp) / "o.wav",
                            framerate=8000, fade_ms=0)
            self.assertIn("8000", str(ctx.exception))

    def test_rejects_empty_segments(self):
        """空片段列表必须被拒——禁止产出空音频。"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AudioError) as ctx:
                concat_wavs([], Path(tmp) / "o.wav",
                            framerate=SAMPLE_RATE, fade_ms=0)
            self.assertIn("空", str(ctx.exception))

    def test_rejects_bad_segment_type(self):
        """片段必须是 array('h')，其它类型必须被拒。"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AudioError):
                concat_wavs([b"not-an-array"], Path(tmp) / "o.wav",
                            framerate=SAMPLE_RATE, fade_ms=0)


# ---------------------------------------------------------------------------
# 6. max_sample_jump click 判据
# ---------------------------------------------------------------------------
class TestMaxSampleJump(unittest.TestCase):
    """相邻样本最大绝对差——click（爆音）判据。"""

    def test_known_vector(self):
        """[0,100,-100,0] 的跳变为 100/200/100，峰值 200。"""
        self.assertEqual(max_sample_jump(array("h", [0, 100, -100, 0])), 200)

    def test_empty_returns_zero(self):
        """空数组返回 0（无相邻对）。"""
        self.assertEqual(max_sample_jump(array("h")), 0)

    def test_single_sample_returns_zero(self):
        """单样本返回 0。"""
        self.assertEqual(max_sample_jump(array("h", [12345])), 0)

    def test_constant_returns_zero(self):
        """恒定电平无跳变，返回 0。"""
        self.assertEqual(max_sample_jump(_tone(500, 20)), 0)

    def test_fade_brings_jump_under_threshold(self):
        """开淡入淡出：拼接边界跳变被压到阈值以下。"""
        threshold = 100
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "faded.wav"
            concat_wavs([_tone(20000, 100), _tone(10000, 100)], out,
                        framerate=SAMPLE_RATE, fade_ms=200)
            samples, _ = read_wav(out)
            self.assertLessEqual(
                max_sample_jump(samples), threshold,
                "淡入淡出后跳变应低于阈值",
            )

    def test_without_fade_jump_exceeds_threshold(self):
        """关淡入淡出：同样的两段必然超阈——证明上一例的通过是靠淡入淡出。"""
        threshold = 100
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "hard.wav"
            concat_wavs([_tone(20000, 100), _tone(10000, 100)], out,
                        framerate=SAMPLE_RATE, fade_ms=0)
            samples, _ = read_wav(out)
            self.assertGreater(max_sample_jump(samples), threshold)


if __name__ == "__main__":
    unittest.main()
