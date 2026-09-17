"""
adapters.tts_macsay.tests.test_adapter — MacSayTts 适配器正例 + 负例测试

覆盖范围：
  - rate_value：正例（三档映射）+ 负例（未知档位抛错且消息含 key）
  - synthesize：正例（有 say 时产出合法 WAV）+ 负例（空文本抛错）
  - WAV 格式校验：channels/sampwidth/framerate
"""

import shutil
import tempfile
import unittest
import unittest.mock
import wave
from pathlib import Path

from adapters.tts_macsay.adapter import MacSayTts, TtsError


# ============================================================
# 1. rate_value — 正例
# ============================================================
class TestRateValuePositive(unittest.TestCase):
    """rate_value 必须正确映射语义档位到物理参数。"""

    def setUp(self):
        self.tts = MacSayTts()

    def test_slow_mapping(self):
        """slow → 150 wpm。"""
        self.assertEqual(self.tts.rate_value("slow"), 150)

    def test_normal_mapping(self):
        """normal → 200 wpm。"""
        self.assertEqual(self.tts.rate_value("normal"), 200)

    def test_fast_mapping(self):
        """fast → 300 wpm。"""
        self.assertEqual(self.tts.rate_value("fast"), 300)


# ============================================================
# 2. rate_value — 负例（无条件测，不依赖 say）
# ============================================================
class TestRateValueNegative(unittest.TestCase):
    """未知档位必须抛 TtsError，消息含该 key 名。"""

    def setUp(self):
        self.tts = MacSayTts()

    def test_unknown_rate_raises_tts_error(self):
        """未知档位 very_slow → TtsError 且消息含 very_slow。"""
        with self.assertRaises(TtsError) as ctx:
            self.tts.rate_value("very_slow")
        self.assertIn("very_slow", str(ctx.exception))

    def test_unknown_rate_message_lists_valid_keys(self):
        """错误消息中包含已配置的有效 key。"""
        with self.assertRaises(TtsError) as ctx:
            self.tts.rate_value("turbo")
        msg = str(ctx.exception)
        self.assertIn("slow", msg)
        self.assertIn("normal", msg)
        self.assertIn("fast", msg)


# ============================================================
# 3. synthesize — 负例（无条件测，不依赖 say）
# ============================================================
class TestSynthesizeNegative(unittest.TestCase):
    """空文本必须抛 TtsError（禁止合成静音）。"""

    def setUp(self):
        self.tts = MacSayTts()

    def test_empty_text_raises_tts_error(self):
        """空字符串 → TtsError。"""
        with self.assertRaises(TtsError) as ctx:
            self.tts.synthesize("", Path("/tmp/test_empty.wav"))
        self.assertIn("空文本", str(ctx.exception))

    def test_whitespace_only_text_is_allowed(self):
        """纯空格文本不算空（say 可能拒绝但 adapter 层不算空）。"""
        tmp = Path(tempfile.mktemp(suffix=".wav"))
        try:
            # 空格文本不是空字符串，adapter 层不拦截
            # 实际 say 可能成功也可能失败，这里只验证不触发空文本检查
            self.tts.synthesize("  ", tmp)
        except TtsError as e:
            # say 可能失败（合理行为），但错误不应是"空文本"
            self.assertNotIn("空文本", str(e), "空白文本不应触发空文本检查")
        finally:
            tmp.unlink(missing_ok=True)


# ============================================================
# 4. synthesize — 正例（需 say 可用）
# ============================================================
@unittest.skipUnless(shutil.which("say"), "macOS say 命令不可用，跳过真实合成用例")
class TestSynthesizePositive(unittest.TestCase):
    """有 say 时：产出合法 WAV，格式为 16kHz/单声道/16-bit。"""

    def setUp(self):
        self.tts = MacSayTts()
        self.tmp_dir = tempfile.mkdtemp()
        self.out_path = Path(self.tmp_dir) / "test_output.wav"

    def tearDown(self):
        import shutil as _shutil
        _shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_synthesize_produces_wav(self):
        """合成一段短文本，产出的文件存在且大小 > 0。"""
        self.tts.synthesize("你好", self.out_path)
        self.assertTrue(self.out_path.exists(), "输出文件应存在")
        self.assertGreater(self.out_path.stat().st_size, 0, "输出文件应非空")

    def test_wav_channels_is_mono(self):
        """WAV 声道数必须为 1（单声道）。"""
        self.tts.synthesize("测试", self.out_path)
        with wave.open(str(self.out_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1, "声道数应为 1（单声道）")

    def test_wav_sampwidth_is_16bit(self):
        """WAV 采样位宽必须为 2 字节（16-bit）。"""
        self.tts.synthesize("测试", self.out_path)
        with wave.open(str(self.out_path), "rb") as wf:
            self.assertEqual(wf.getsampwidth(), 2, "采样位宽应为 2 字节（16-bit）")

    def test_wav_framerate_is_16000(self):
        """WAV 采样率必须为 16000 Hz。"""
        self.tts.synthesize("测试", self.out_path)
        with wave.open(str(self.out_path), "rb") as wf:
            self.assertEqual(wf.getframerate(), 16000, "采样率应为 16000 Hz")

    def test_wav_frame_count_positive(self):
        """WAV 帧数必须 > 0。"""
        self.tts.synthesize("测试", self.out_path)
        with wave.open(str(self.out_path), "rb") as wf:
            self.assertGreater(wf.getnframes(), 0, "帧数应 > 0")

    def test_synthesize_with_rate_slow(self):
        """以 slow 速率合成，格式仍合法。"""
        self.tts.synthesize("慢速测试", self.out_path, rate_key="slow")
        with wave.open(str(self.out_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)

    def test_synthesize_with_rate_fast(self):
        """以 fast 速率合成，格式仍合法。"""
        self.tts.synthesize("快速测试", self.out_path, rate_key="fast")
        with wave.open(str(self.out_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)

    def test_synthesize_creates_parent_dir(self):
        """输出目录不存在时自动创建。"""
        nested = Path(self.tmp_dir) / "sub" / "dir" / "out.wav"
        self.tts.synthesize("嵌套目录", nested)
        self.assertTrue(nested.exists(), "嵌套目录下的文件应存在")

    def test_synthesize_uses_tempfile(self):
        """合成过程不留下临时文件（tempfile 已清理）。"""
        tmp_dir = Path(tempfile.gettempdir())
        before = len([f for f in tmp_dir.iterdir() if f.name.startswith("tmp") and f.suffix == ".wav"])
        self.tts.synthesize("临时文件", self.out_path)
        after = len([f for f in tmp_dir.iterdir() if f.name.startswith("tmp") and f.suffix == ".wav"])
        self.assertEqual(after, before, "合成后不应多出临时文件")


# ============================================================
# 4b. 失败路径临时文件泄漏回归（不依赖 say，纯 mock）
# ============================================================
class TestFailurePathTempfileLeak(unittest.TestCase):
    """失败路径（say 非零退出）也不得留下临时文件——finally 清理必须生效。"""

    def setUp(self):
        self.tts = MacSayTts()

    def _count_tmp_wav(self) -> int:
        """统计系统临时目录内 tmp*.wav 数量。"""
        tmp_dir = Path(tempfile.gettempdir())
        return len([f for f in tmp_dir.iterdir() if f.name.startswith("tmp") and f.suffix == ".wav"])

    @unittest.mock.patch("adapters.tts_macsay.adapter.subprocess.run")
    def test_failure_path_leaves_no_tempfile(self, mock_run):
        """失败路径（say 非零退出）也不得留下临时文件。"""
        mock_run.return_value = unittest.mock.Mock(returncode=1, stderr="mocked error")

        before = self._count_tmp_wav()
        out_path = Path(tempfile.mktemp(suffix=".wav"))
        try:
            with self.assertRaises(TtsError):
                self.tts.synthesize("测试失败路径", out_path)
        finally:
            out_path.unlink(missing_ok=True)

        after = self._count_tmp_wav()
        self.assertEqual(after, before, "失败路径下临时文件应被清理，不应多出 tmp*.wav")


# ============================================================
# 5. 属性声明校验
# ============================================================
class TestAdapterAttributes(unittest.TestCase):
    """适配器必须声明必要的接口属性。"""

    def setUp(self):
        self.tts = MacSayTts()

    def test_name_is_macsay(self):
        """name 属性必须为 'macsay'。"""
        self.assertEqual(self.tts.name, "macsay")

    def test_model_version_nonempty(self):
        """model_version 必须非空。"""
        self.assertTrue(self.tts.model_version, "model_version 不得为空")

    def test_requires_core_nonempty(self):
        """requires_core 必须非空。"""
        self.assertTrue(self.tts.requires_core, "requires_core 不得为空")

    def test_voice_nonempty(self):
        """voice 必须非空。"""
        self.assertTrue(self.tts.voice, "voice 不得为空")

    def test_rate_map_has_three_keys(self):
        """rate_map 必须包含 slow/normal/fast 三档。"""
        for key in ("slow", "normal", "fast"):
            self.assertIn(key, self.tts.rate_map, f"rate_map 缺少 {key}")


if __name__ == "__main__":
    unittest.main()
