"""
adapters.tests.test_crossvolume — 跨卷落盘回归测试

覆盖 T03b 修订卡要求：
  - 跨设备落盘成功（shutil.move 自动退化为复制+删除）
  - WAV 参数断言（channels/sampwidth/framerate/帧数）
  - 负例：目标父路径存在同名普通文件 → TtsError（非裸 OSError）
"""

import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from adapters.tts_macsay.adapter import MacSayTts, TtsError


# 仓库所在卷用于跨设备测试；若与系统 tmpdir 同设备则 skip
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_DEVICE = os.stat(str(_REPO_ROOT)).st_dev
_TMPDIR_DEVICE = os.stat(tempfile.gettempdir()).st_dev
_ON_DIFFERENT_DEVICE = _REPO_DEVICE != _TMPDIR_DEVICE


def _make_cross_device_tmpdir():
    """在仓库卷上创建临时目录（与系统 tmpdir 一定异设备）。"""
    d = tempfile.mkdtemp(dir=str(_REPO_ROOT))
    return d


@unittest.skipUnless(
    shutil.which("say") and _ON_DIFFERENT_DEVICE,
    "需要 macOS say 且 tmpdir 与仓库在不同设备",
)
class TestCrossVolumeSynthesize(unittest.TestCase):
    """跨设备落盘：synthesize 必须成功且产物为合法 WAV。"""

    def setUp(self):
        self.tts = MacSayTts()
        self.work_dir = _make_cross_device_tmpdir()
        self.addCleanup(shutil.rmtree, self.work_dir)

    def test_cross_volume_wav_params(self):
        """跨设备落盘后 WAV 必须满足 16kHz/单声道/16-bit/帧数>0。"""
        out = Path(self.work_dir) / "cross.wav"
        self.tts.synthesize("跨卷落盘测试", str(out))
        self.assertTrue(out.exists(), "跨设备落盘后文件不存在")
        with wave.open(str(out), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1, "声道数应为 1（单声道）")
            self.assertEqual(wf.getsampwidth(), 2, "采样位宽应为 2 字节（16-bit）")
            self.assertEqual(wf.getframerate(), 16000, "采样率应为 16000 Hz")
            self.assertGreater(wf.getnframes(), 0, "帧数应 > 0")


@unittest.skipUnless(
    shutil.which("say") and _ON_DIFFERENT_DEVICE,
    "需要 macOS say 且 tmpdir 与仓库在不同设备",
)
class TestCrossVolumeMkdirConflict(unittest.TestCase):
    """负例：目标父路径上存在同名普通文件，mkdir 失败 → 抛 TtsError。"""

    def setUp(self):
        self.tts = MacSayTts()
        self.work_dir = _make_cross_device_tmpdir()
        # 在临时目录下放一个同名普通文件，阻止 mkdir
        self.blocker_file = Path(self.work_dir) / "blocker"
        self.blocker_file.write_text("block")
        self.addCleanup(shutil.rmtree, self.work_dir)

    def test_mkdir_conflict_raises_tts_error(self):
        """同名普通文件阻挡 mkdir → TtsError 而非 OSError。"""
        target = self.blocker_file / "out.wav"
        with self.assertRaises(TtsError) as ctx:
            self.tts.synthesize("mkdir冲突测试", str(target))
        msg = str(ctx.exception)
        self.assertIn("创建输出目录失败", msg, "错误消息应提及目录创建失败")


if __name__ == "__main__":
    unittest.main()
