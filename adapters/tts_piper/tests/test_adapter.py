"""
adapters.tts_piper.tests.test_adapter — Piper 本地 TTS 适配器测试

覆盖：
  1. 接口属性：name / model_version（随模型文件变化）/ voice / requires_core
  2. rate_value：三档单调（slow < normal < fast）、normal 为基准、
     未知档抛 TtsError 且消息含档名
  3. fail-closed：空文本 / 未配置模型 / 模型文件不存在 / piper 命令不存在 → TtsError
  4. 产物归一（离线，纯函数）：首尾静音裁剪、峰值归一进带、全静音拒收
  5. 真实合成（skip 条件：piper 可执行 + 模型 + ffmpeg 三件齐）：
     产物必须 16kHz/单声道/16-bit，且过 compiler.quality 同口径自检

纪律：除真实合成外全部离线——用环境变量造缺件，不联网、不起服务。
真实合成在 macOS 开发机缺 piper 时显式 skip；在装有 piper 的 Linux 容器里真跑。
"""

import array
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from adapters.tts_piper import PiperTts, TtsError
from adapters.tts_piper import normalize as norm


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="piper_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _write_wav(path: Path, samples: array, rate: int = 22050) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(samples.tobytes())

    @staticmethod
    def _tone(n: int, amp: int = 12000) -> array:
        # 方波替身：只关心「非静音 / 峰值」两个量，不需要真实音频
        return array.array("h", [amp if i % 2 == 0 else -amp for i in range(n)])


# ============================================================
# 1. 接口属性
# ============================================================
class TestAttributes(unittest.TestCase):
    def test_name_and_requires_core(self):
        self.assertEqual(PiperTts.name, "piper")
        self.assertEqual(PiperTts().requires_core, "^0.1")

    def test_model_version_and_voice_follow_model_file(self):
        """model_version / voice 从模型文件名推导——换模型 = 换指纹。"""
        with mock.patch.dict(os.environ, {"VOX_PIPER_MODEL": "/x/zh_CN-huayan-medium.onnx"}):
            tts = PiperTts()
            self.assertEqual(tts.voice, "zh_CN-huayan-medium")
            self.assertEqual(tts.model_version, "piper-zh_CN-huayan-medium")

    def test_defaults_without_env_are_nonempty(self):
        """未配置时属性仍非空（一致性套件的无条件断言依赖这一点）。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            tts = PiperTts()
            self.assertTrue(tts.voice)
            self.assertTrue(tts.model_version)


# ============================================================
# 2. rate_value
# ============================================================
class TestRateValue(unittest.TestCase):
    def test_monotonic_and_normal_middle(self):
        tts = PiperTts()
        slow, normal, fast = (tts.rate_value(k) for k in ("slow", "normal", "fast"))
        self.assertLess(slow, normal)
        self.assertLess(normal, fast)

    def test_unknown_rate_raises_with_key(self):
        with self.assertRaises(TtsError) as ctx:
            PiperTts().rate_value("nonexistent_rate_xyz")
        self.assertIn("nonexistent_rate_xyz", str(ctx.exception))


# ============================================================
# 3. fail-closed
# ============================================================
class TestSynthesizeFailClosed(TmpDir):
    def test_empty_text_raises(self):
        with self.assertRaises(TtsError) as ctx:
            PiperTts().synthesize("", self.tmp / "o.wav")
        self.assertIn("空", str(ctx.exception))

    def test_missing_model_env_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TtsError) as ctx:
                PiperTts().synthesize("您好", self.tmp / "o.wav")
        self.assertIn("VOX_PIPER_MODEL", str(ctx.exception))

    def test_missing_model_file_raises(self):
        with mock.patch.dict(os.environ, {"VOX_PIPER_MODEL": str(self.tmp / "nope.onnx")}):
            with self.assertRaises(TtsError) as ctx:
                PiperTts().synthesize("您好", self.tmp / "o.wav")
        self.assertIn("不存在", str(ctx.exception))

    def test_missing_piper_binary_raises(self):
        model = self.tmp / "m.onnx"
        model.write_bytes(b"x")
        with mock.patch.dict(os.environ, {
            "VOX_PIPER_MODEL": str(model),
            "VOX_PIPER_BIN": "vox-no-such-piper-binary",
        }):
            with self.assertRaises(TtsError) as ctx:
                PiperTts().synthesize("您好", self.tmp / "o.wav")
        self.assertIn("piper 命令不可用", str(ctx.exception))

    def test_probe_reports_all_missing_reasons(self):
        """探针按 synthesize 的检查顺序报**全部**缺失原因（T78 纪律）。"""
        with mock.patch.dict(os.environ, {
            "VOX_PIPER_MODEL": "",
            "VOX_PIPER_BIN": "vox-no-such-piper-binary",
            "VOX_FFMPEG": "vox-no-such-ffmpeg",
        }, clear=True):
            ok, reason = PiperTts.probe_availability()
        self.assertFalse(ok)
        self.assertIn("VOX_PIPER_MODEL", reason)
        self.assertIn("vox-no-such-piper-binary", reason)
        self.assertIn("vox-no-such-ffmpeg", reason)


# ============================================================
# 4. 产物归一（离线纯函数）
# ============================================================
class TestNormalizeOffline(unittest.TestCase):
    @staticmethod
    def _tone(n: int, amp: int = 12000) -> array:
        # 方波替身：只关心「非静音 / 峰值」两个量，不需要真实音频
        return array.array("h", [amp if i % 2 == 0 else -amp for i in range(n)])

    def test_trim_reduces_head_and_tail(self):
        head, body, tail = 900, 800, 900
        samples = (
            array.array("h", [0] * head)
            + self._tone(body)
            + array.array("h", [0] * tail)
        )
        out = norm._trim(samples)
        first = next(i for i, s in enumerate(out) if abs(s) > norm.SILENCE_THRESHOLD)
        last = next(i for i in range(len(out) - 1, -1, -1) if abs(out[i]) > norm.SILENCE_THRESHOLD)
        head_keep = int(norm.HEAD_PAD_MS / 1000.0 * norm.TARGET_RATE)
        tail_keep = int(norm.TAIL_PAD_MS / 1000.0 * norm.TARGET_RATE)
        self.assertEqual(first, head_keep)
        self.assertEqual(len(out) - 1 - last, tail_keep)

    def test_trim_all_silence_raises(self):
        with self.assertRaises(norm.NormalizeError):
            norm._trim(array.array("h", [0] * 1000))

    def test_peak_normalize_lands_in_band(self):
        import math
        samples = self._tone(1000, amp=32000)
        out = norm._peak_normalize(samples)
        peak = max(abs(s) for s in out)
        peak_dbfs = 20.0 * math.log10(peak / norm.FULL_SCALE)
        self.assertGreaterEqual(peak_dbfs, norm.PEAK_MIN_DBFS)
        self.assertLessEqual(peak_dbfs, norm.PEAK_MAX_DBFS)

    def test_peak_normalize_silence_raises(self):
        with self.assertRaises(norm.NormalizeError):
            norm._peak_normalize(array.array("h", [0] * 100))

    def test_normalize_from_rejects_missing_input(self):
        with self.assertRaises(norm.NormalizeError):
            norm.normalize_from(Path("/no/such/file.wav"), ffmpeg="ffmpeg")


# ============================================================
# 5. 真实合成（piper + 模型 + ffmpeg 三件齐才跑）
# ============================================================
class TestRealSynthesisSkipAware(TmpDir):
    """依赖真 piper 的用例——缺件时 skip（消息点名原因），有件时真跑。"""

    def setUp(self):
        super().setUp()
        ok, reason = PiperTts.probe_availability()
        if not ok:
            self.skipTest(f"piper: {reason}")

    def test_synthesized_wav_passes_contract_and_self_check(self):
        out = self.tmp / "speech.wav"
        PiperTts().synthesize("您好，欢迎致电客户服务中心。", out)

        with wave.open(str(out), "rb") as wf:
            self.assertEqual(wf.getframerate(), norm.TARGET_RATE)
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            raw = wf.readframes(wf.getnframes())
        samples = array.array("h")
        samples.frombytes(raw)

        # 按 compiler.quality 同口径复算（质检门在容器里另有真跑）
        n = len(samples)
        first = next(i for i in range(n) if abs(samples[i]) > norm.SILENCE_THRESHOLD)
        last = next(i for i in range(n - 1, -1, -1) if abs(samples[i]) > norm.SILENCE_THRESHOLD)
        self.assertLessEqual(first / norm.TARGET_RATE * 1000, norm.HEAD_LIMIT_MS)
        self.assertLessEqual((n - 1 - last) / norm.TARGET_RATE * 1000, norm.TAIL_LIMIT_MS)
        self.assertLess(max(abs(s) for s in samples), norm.CLIP_THRESHOLD)

    def test_each_rate_key_runs(self):
        """三档语速都能真合成（只在探针可用时跑，耗时约数秒）。"""
        for key in ("slow", "normal", "fast"):
            with self.subTest(rate=key):
                out = self.tmp / f"rate_{key}.wav"
                PiperTts().synthesize("您好。", out, rate_key=key)
                self.assertTrue(out.is_file() and out.stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
