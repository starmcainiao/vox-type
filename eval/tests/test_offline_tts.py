"""
eval.tests.test_offline_tts — 离线替身适配器确定性验证

覆盖（对应 T08b 修复 4 审计 #6-2）：
  OfflineTts.synthesize 对同一 (text, rate_key) 调两次 → 两次产物 read_bytes 相等。

WHY: bench 的确定性子集不含时长，替身一旦引入随机/时间戳仍会全绿——
     这条用例锚定"同输入 → 逐字节相同 WAV"的硬承诺。

纪律：全部走产品 API（OfflineTts.synthesize），不在测试里重实现音频生成逻辑。
"""

import tempfile
import unittest
import wave
from pathlib import Path

from eval.offline_tts import OfflineTts

VOICE = "Tingting"
MODEL_VERSION = "macos-say"


class OfflineTtsDeterminismTest(unittest.TestCase):
    """OfflineTts.synthesize 对同一 (text, rate_key) 调两次 → 两次产物逐字节相同。"""

    def test_synthesize_is_byte_identical(self):
        """同输入 → 逐字节相同 WAV（确定性硬承诺，替身一旦引入随机/时间戳必须变红）。"""
        tts = OfflineTts(voice=VOICE, model_version=MODEL_VERSION, ms_per_char=10.0)
        text = "你好，世界。"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out1 = tmp_path / "a.wav"
            out2 = tmp_path / "b.wav"

            tts.synthesize(text, out1, "normal")
            tts.synthesize(text, out2, "normal")

            bytes1 = out1.read_bytes()
            bytes2 = out2.read_bytes()

            self.assertEqual(bytes1, bytes2, "同输入两次合成产物必须逐字节相同")
            self.assertGreater(len(bytes1), 44, "WAV 文件必须有实际音频数据（不止头）")

            with wave.open(str(out1), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 16000)

    def test_different_text_produces_different_bytes(self):
        """不同文本 → 不同字节（证明输入确实参与了样本生成，不是死代码）。"""
        tts = OfflineTts(voice=VOICE, model_version=MODEL_VERSION, ms_per_char=10.0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out1 = tmp_path / "a.wav"
            out2 = tmp_path / "b.wav"

            tts.synthesize("第一句话。", out1, "normal")
            tts.synthesize("第二句话。", out2, "normal")

            bytes1 = out1.read_bytes()
            bytes2 = out2.read_bytes()

            self.assertNotEqual(bytes1, bytes2, "不同文本必须产生不同音频数据")


if __name__ == "__main__":
    unittest.main()
