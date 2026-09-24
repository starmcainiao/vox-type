"""
adapters.tests.test_conformance — TTS 适配器接口一致性测试（跨适配器复用）

覆盖范围（同一套断言跑所有 tts-* 适配器）：
  - 接口属性完整性：name / model_version / requires_core / voice / rate_map
  - rate_map 包含 slow/normal/fast 三档
  - rate_value 已知档位返回整数，未知档位抛 TtsError
  - synthesize 空文本抛 TtsError
  - 有 say 时真实合成 WAV 格式校验

扩展方式：在 ADAPTER_CLASSES 列表中追加新适配器类即可，无需重写断言。
"""

import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from adapters.tts_macsay.adapter import MacSayTts, TtsError


# ============================================================
# 待测适配器类注册表（新增 tts-* 时只需在此追加）
# ============================================================
ADAPTER_CLASSES = [
    MacSayTts,
]


# ============================================================
# 1. 接口属性完整性（所有适配器共用）
# ============================================================
class TestAttributeCompleteness(unittest.TestCase):
    """每个 TTS 适配器必须声明完整的接口属性。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]

    def test_name_nonempty(self):
        """name 必须非空。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(tts.name, f"{tts.__class__.__name__}.name 不得为空")

    def test_model_version_nonempty(self):
        """model_version 必须非空。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(
                    tts.model_version,
                    f"{tts.__class__.__name__}.model_version 不得为空",
                )

    def test_requires_core_nonempty(self):
        """requires_core 必须非空。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertTrue(
                    tts.requires_core,
                    f"{tts.__class__.__name__}.requires_core 不得为空",
                )

    def test_rate_map_has_slow(self):
        """rate_map 必须包含 slow 档。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertIn("slow", tts.rate_map, f"{tts.name}.rate_map 缺少 slow")

    def test_rate_map_has_normal(self):
        """rate_map 必须包含 normal 档。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertIn("normal", tts.rate_map, f"{tts.name}.rate_map 缺少 normal")

    def test_rate_map_has_fast(self):
        """rate_map 必须包含 fast 档。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                self.assertIn("fast", tts.rate_map, f"{tts.name}.rate_map 缺少 fast")


# ============================================================
# 2. rate_value 行为（所有适配器共用）
# ============================================================
class TestRateValueBehavior(unittest.TestCase):
    """rate_value 已知档位返回整数，未知档位抛 TtsError。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]

    def test_known_rates_return_int(self):
        """已知三档必须返回整数值。"""
        for tts in self.adapters:
            for key in ("slow", "normal", "fast"):
                with self.subTest(adapter=tts.name, rate=key):
                    val = tts.rate_value(key)
                    self.assertIsInstance(val, int, f"{tts.name}.rate_value({key!r}) 应返回 int")

    def test_unknown_rate_raises_tts_error(self):
        """未知档位必须抛 TtsError。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                with self.assertRaises(TtsError):
                    tts.rate_value("nonexistent_rate_xyz")

    def test_unknown_rate_message_contains_key(self):
        """未知档位的错误消息必须包含该 key 名。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                with self.assertRaises(TtsError) as ctx:
                    tts.rate_value("nonexistent_rate_xyz")
                self.assertIn("nonexistent_rate_xyz", str(ctx.exception))


# ============================================================
# 3. synthesize 空文本行为（所有适配器共用，不依赖外部命令）
# ============================================================
class TestSynthesizeEmptyText(unittest.TestCase):
    """空文本必须抛 TtsError（无条件测，不依赖 say）。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]

    def test_empty_string_raises_tts_error(self):
        """空字符串 → TtsError。"""
        for tts in self.adapters:
            with self.subTest(adapter=tts.name):
                with self.assertRaises(TtsError) as ctx:
                    tts.synthesize("", Path("/dev/null"))
                self.assertIn("空", str(ctx.exception), "错误消息应提及空文本")


# ============================================================
# 4. 真实合成 WAV 格式校验（需 say 可用，否则 skip）
# ============================================================
@unittest.skipUnless(shutil.which("say"), "macOS say 命令不可用，跳过真实合成用例")
class TestSynthesizeWavFormat(unittest.TestCase):
    """有 say 时：产出 WAV 必须为 16kHz/单声道/16-bit。"""

    def setUp(self):
        self.adapters = [cls() for cls in ADAPTER_CLASSES]
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil as _shutil
        _shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_wav_is_mono_16bit_16000hz(self):
        """每个适配器合成的 WAV 必须满足 channels=1, sampwidth=2, framerate=16000。"""
        for tts in self.adapters:
            out = Path(self.tmp_dir) / f"{tts.name}_test.wav"
            tts.synthesize("接口一致性测试", out)
            with wave.open(str(out), "rb") as wf:
                self.assertEqual(
                    wf.getnchannels(), 1,
                    f"{tts.name}: 声道数应为 1（单声道）",
                )
                self.assertEqual(
                    wf.getsampwidth(), 2,
                    f"{tts.name}: 采样位宽应为 2 字节（16-bit）",
                )
                self.assertEqual(
                    wf.getframerate(), 16000,
                    f"{tts.name}: 采样率应为 16000 Hz",
                )
                self.assertGreater(
                    wf.getnframes(), 0,
                    f"{tts.name}: 帧数应 > 0",
                )

    def test_synthesize_with_each_rate(self):
        """每个适配器分别以 slow/normal/fast 合成，格式均合法。"""
        for tts in self.adapters:
            for rate in ("slow", "normal", "fast"):
                out = Path(self.tmp_dir) / f"{tts.name}_{rate}.wav"
                tts.synthesize("速率测试", out, rate_key=rate)
                with wave.open(str(out), "rb") as wf:
                    self.assertEqual(wf.getnchannels(), 1)
                    self.assertEqual(wf.getsampwidth(), 2)
                    self.assertEqual(wf.getframerate(), 16000)


if __name__ == "__main__":
    unittest.main()
