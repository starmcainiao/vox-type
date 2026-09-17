"""
assets.tests.test_fingerprint — 指纹算法的正例 + 负例测试

覆盖范围：
  - 指纹稳定性（相同输入 → 相同输出）
  - 指纹正交性（4 条负例：改 text / 改 voice / 改 rate_value / 改 model_version → 指纹均变化）
  - 指纹格式（16 位 hex 字符串）
"""

import hashlib
import unittest

from assets.fingerprint import fingerprint


class TestFingerprintStability(unittest.TestCase):
    """指纹必须是确定性的：相同输入 → 相同输出。"""

    def test_same_input_same_output(self):
        """相同四元组输入必须产生相同指纹。"""
        a = fingerprint("你好", "zh-CN-Xiaoxiao", "normal", "v1.0")
        b = fingerprint("你好", "zh-CN-Xiaoxiao", "normal", "v1.0")
        self.assertEqual(a, b)

    def test_deterministic_across_calls(self):
        """多次调用结果一致（无随机性）。"""
        results = [
            fingerprint("测试", "voice1", "slow", "v2")
            for _ in range(100)
        ]
        self.assertEqual(len(set(results)), 1)


class TestFingerprintFormat(unittest.TestCase):
    """指纹必须是 16 位 hex 字符串。"""

    def test_hex_format(self):
        """指纹格式：恰好 16 位，字符全部在 [0-9a-f] 范围内。"""
        fp = fingerprint("text", "voice", "normal", "v1")
        self.assertEqual(len(fp), 16)
        # 验证每一位都是合法 hex 字符
        int(fp, 16)  # 如果不是合法 hex 会抛 ValueError

    def test_known_hash_matches(self):
        """指纹值与手动计算的 SHA-256 截断一致。"""
        text, voice, rate, version = "你好", "zh", "fast", "v3"
        raw = f"{text}\x00{voice}\x00{rate}\x00{version}"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        actual = fingerprint(text, voice, rate, version)
        self.assertEqual(actual, expected)


class TestFingerprintOrthogonality(unittest.TestCase):
    """指纹正交性：四个输入参数任一变化 → 指纹必变。

    这四条负例是整套方案的保险丝——缺任何一条都不算合格。
    """

    BASE_TEXT = "欢迎使用语音系统"
    BASE_VOICE = "zh-CN-Xiaoxiao"
    BASE_RATE = "normal"
    BASE_VERSION = "v1.0"
    BASE_FP = fingerprint(BASE_TEXT, BASE_VOICE, BASE_RATE, BASE_VERSION)

    def test_change_text_changes_fingerprint(self):
        """负例 1：改 text → 指纹必变。"""
        changed = fingerprint(
            "改过的文本",  # text 变了
            self.BASE_VOICE,
            self.BASE_RATE,
            self.BASE_VERSION,
        )
        self.assertNotEqual(
            self.BASE_FP,
            changed,
            "改 text 后指纹不变——这意味着话术被改但资产没重铸时无法检测",
        )

    def test_change_voice_changes_fingerprint(self):
        """负例 2：改 voice → 指纹必变。"""
        changed = fingerprint(
            self.BASE_TEXT,
            "zh-CN-Yunxi",  # voice 变了
            self.BASE_RATE,
            self.BASE_VERSION,
        )
        self.assertNotEqual(
            self.BASE_FP,
            changed,
            "改 voice 后指纹不变——这意味着音色切换时资产无法区分",
        )

    def test_change_rate_changes_fingerprint(self):
        """负例 3：改 rate_value → 指纹必变。"""
        changed = fingerprint(
            self.BASE_TEXT,
            self.BASE_VOICE,
            "fast",  # rate 变了
            self.BASE_VERSION,
        )
        self.assertNotEqual(
            self.BASE_FP,
            changed,
            "改 rate 后指纹不变——这意味着语速调整时资产无法区分",
        )

    def test_change_model_version_changes_fingerprint(self):
        """负例 4：改 model_version → 指纹必变。"""
        changed = fingerprint(
            self.BASE_TEXT,
            self.BASE_VOICE,
            self.BASE_RATE,
            "v2.0",  # model_version 变了
        )
        self.assertNotEqual(
            self.BASE_FP,
            changed,
            "改 model_version 后指纹不变——这意味着模型升级时资产无法区分",
        )


if __name__ == "__main__":
    unittest.main()
