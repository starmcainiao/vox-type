"""
eval.tests.test_cer — 回读 CER 指标（纯函数，全离线）

覆盖（对应 T14 验收 4 与反空转条款）：
  1. 归一吃掉标点：cer("今天下午三点提醒你开会", "今天下午三点提醒你开会。") == 0.0；
  2. 插错一个字 → 1/n；漏一个字 → 1/n；调序两字 → 编辑距离 2（不是 0）；
  3. 归一后 reference 为空（纯标点输入）→ ValueError（消息含原文本），不做 0/0 假成功；
  4. 不做同义/模糊/语义归一（"你好" ≠ "您好" → CER > 0）；
  5. 指标名常量定义在本模块且不来自 core.metrics_spec；
  6. 上界可 > 1.0（转写多说一个字不算"完全正确"），不夹取。

纪律（反空转）：全部调用产品 API（cer / normalize_for_cer），
  不在测试里重实现编辑距离或分位数当期望值——期望值一律手算并写在断言旁。
"""

import unittest

from eval import cer as cer_mod
from eval.cer import CER, CER_MEAN, CER_N, CER_P50, CER_P99, cer, normalize_for_cer

# 卡里的基准句；N 由 len 派生——写死字符数是最容易漂的期望值
REF = "今天下午三点提醒你开会"
N = len(REF)


class NormalizeTest(unittest.TestCase):
    """归一 = 去全部标点与空白，不动汉字/字母/数字。"""

    def test_strips_chinese_punctuation(self):
        self.assertEqual(normalize_for_cer(REF + "。"), REF)

    def test_strips_whitespace_and_mixed_punct(self):
        self.assertEqual(
            normalize_for_cer(" 今天 下午 ， 三点 提醒你 开会 ！"), REF
        )

    def test_keeps_letters_and_digits(self):
        """拉丁字符与数字不是标点，不得被吃掉（否则数字识别错误会被归一掉）。"""
        self.assertEqual(normalize_for_cer("12:30 会"), "1230会")
        self.assertEqual(normalize_for_cer("abcXYZ123"), "abcXYZ123")

    def test_fullwidth_space_is_stripped(self):
        self.assertEqual(normalize_for_cer("今天 下午"), "今天下午")

    def test_rejects_non_string(self):
        """入参不是字符串 → ValueError（消息含实际类型），不做隐式转换。"""
        with self.assertRaises(ValueError) as cm:
            normalize_for_cer(123)
        self.assertIn("int", str(cm.exception))

    def test_empty_string_yields_empty(self):
        """空串归一后为空串（是否报错由 cer() 决定，归一函数本身不做口径判定）。"""
        self.assertEqual(normalize_for_cer(""), "")


class CerPositiveTest(unittest.TestCase):
    """验收 4 的手算对照：正例。"""

    def test_identical_is_zero(self):
        self.assertEqual(cer(REF, REF), 0.0)

    def test_reference_punct_ignored(self):
        """句号在归一里被吃掉 → 完全相同 = 0.0（卡的原文判据）。"""
        self.assertEqual(cer(REF, REF + "。"), 0.0)

    def test_extra_punct_on_both_sides_is_zero(self):
        self.assertEqual(cer("您好，这里是报修服务热线。", "您好这里是报修服务热线"), 0.0)

    def test_single_insertion_is_1_over_n(self):
        """插错一个字（替换）→ 编辑距离 1 → CER = 1/n。"""
        value = cer(REF, REF[:4] + "错" + REF[4:])
        self.assertAlmostEqual(value, 1.0 / N, places=9)

    def test_single_deletion_is_1_over_n(self):
        """漏一个字 → 编辑距离 1 → CER = 1/n。"""
        value = cer(REF, REF[:5] + REF[6:])
        self.assertAlmostEqual(value, 1.0 / N, places=9)

    def test_single_substitution_is_1_over_n(self):
        value = cer(REF, REF[:5] + "错" + REF[6:])
        self.assertAlmostEqual(value, 1.0 / N, places=9)

    def test_transposed_two_chars_is_2_over_n(self):
        """调序两字 → 字符级编辑距离是 2（逐字比对，不做词级/顺序容忍）。

        手算：「提→醒」→「提醒」需要 2 次替换（第 1 字 提→醒，第 2 字 醒→提），
        不是 0——CER 明确不做模糊/顺序归一。
        """
        hyp = REF.replace("提醒", "醒提")
        value = cer(REF, hyp)
        self.assertAlmostEqual(value, 2.0 / N, places=9)
        self.assertGreater(value, 0.0, "调序两字不得被当成完全正确")

    def test_empty_hypothesis_is_one_over_n(self):
        """转写完全为空（识别不到）→ 删除全部字符 = n → CER = 1.0。"""
        self.assertAlmostEqual(cer(REF, ""), 1.0, places=9)

    def test_hypothesis_longer_can_exceed_one(self):
        """转写比源文本多出的字全部计入 → CER 可 > 1.0，不夹取到 1.0。"""
        value = cer("提醒", "提醒" + "啊" * 5)
        self.assertAlmostEqual(value, 5.0 / 2, places=9)
        self.assertGreater(value, 1.0)


class CerUndefinedTest(unittest.TestCase):
    """归一后 reference 为空 → ValueError（0/0 不做假成功）。"""

    def test_punctuation_only_reference_raises(self):
        bad_ref = "。！？，、"
        with self.assertRaises(ValueError) as cm:
            cer(bad_ref, "随便什么")
        msg = str(cm.exception)
        self.assertIn(bad_ref, msg, "消息必须带出原始 reference 文本，便于定位是哪条语料")
        self.assertIn("0/0", msg, "消息必须说明是 0/0 无定义，而不是静默给 0.0 或 1.0")

    def test_whitespace_only_reference_raises(self):
        with self.assertRaises(ValueError) as cm:
            cer("   \t \n", "x")
        self.assertIn("cer.reference", str(cm.exception))

    def test_empty_reference_raises(self):
        with self.assertRaises(ValueError) as cm:
            cer("", "x")
        self.assertIn("cer.reference", str(cm.exception))

    def test_non_string_reference_raises(self):
        """类型错误必须带出实际类型（不吞上下文）。"""
        with self.assertRaises(ValueError) as cm:
            cer(None, "x")
        self.assertIn("NoneType", str(cm.exception))

    def test_non_string_hypothesis_raises(self):
        with self.assertRaises(ValueError) as cm:
            cer(REF, None)
        self.assertIn("cer.hypothesis", str(cm.exception))


class NoFuzzyMatchingTest(unittest.TestCase):
    """卡明令：不做同义/模糊/语义比对。"""

    def test_synonyms_are_errors(self):
        """「你好」与「您好」是同义不同字 → 必须被算成错误，不得归一。"""
        value = cer("你好", "您好")
        self.assertAlmostEqual(value, 1.0 / 2, places=9)
        self.assertGreater(value, 0.0, "同义替换不得被归一为正确")

    def test_extra_fillers_are_errors(self):
        """转写多出语气词（「嗯」「那个」）是内容差异，必须计入。"""
        self.assertGreater(cer("开会", "嗯 开会"), 0.0)

    def test_halfwidth_comma_is_punct(self):
        """标点集含半角逗号（Seed-TTS-eval 去全部标点）。"""
        self.assertEqual(cer("A,B", "AB"), 0.0)


class MetricNameConstantsTest(unittest.TestCase):
    """指标名常量：定义在本模块，不来自 core.metrics_spec（本卡不碰 core）。"""

    def test_constants_are_string_names(self):
        self.assertEqual(CER, "cer")
        self.assertEqual(CER_P50, "cer_p50")
        self.assertEqual(CER_P99, "cer_p99")
        self.assertEqual(CER_MEAN, "cer_mean")
        self.assertEqual(CER_N, "cer_n")

    def test_module_owns_the_names(self):
        """常量必须定义在 eval.cer 里，而不是从 core 转发过来。"""
        for name, expected in (("CER", "cer"), ("CER_P50", "cer_p50"),
                               ("CER_P99", "cer_p99"), ("CER_MEAN", "cer_mean"),
                               ("CER_N", "cer_n")):
            self.assertEqual(getattr(cer_mod, name), expected)
        self.assertTrue(cer_mod.__file__.endswith("eval" + __import__("os").sep + "cer.py"))


if __name__ == "__main__":
    unittest.main()
