"""
adapters.framework_kefu.tests.test_normalize — 归一化四步各有正反例（docs/10 §10.3）

只调产品 API normalize_text，不在测试里重抄归一化实现。

覆盖：
    ① 去空白（含全角空格 U+3000）       ② 全角↔半角（ASCII 可见区 + 8 个常见全角标点）
    ③ NFKC（兼容字符）                  ④ 小写
    同输入同输出（幂等）+ 无副作用
    禁止项：去标点 / 去语气词 / 同义改写 一律不许做（用断言证明"多做一步就会不一样"）
"""

import unittest

from adapters.framework_kefu import normalize_text


class TestStep1Whitespace(unittest.TestCase):
    """① 去除首尾与内部所有空白，含全角空格 U+3000。"""

    def test_leading_trailing_removed(self):
        self.assertEqual(normalize_text("  您好  "), "您好")

    def test_internal_removed(self):
        self.assertEqual(normalize_text("您 好 吗"), "您好吗")

    def test_fullwidth_space_removed(self):
        # U+3000 全角空格：docs/10 §10.3 明确点名
        self.assertEqual(normalize_text("您好\u3000吗"), "您好吗")
        self.assertEqual(normalize_text("\u3000您好\u3000"), "您好")

    def test_tab_newline_and_nbsp_removed(self):
        self.assertEqual(normalize_text("您\t好\n吗\r"), "您好吗")
        self.assertEqual(normalize_text("您\u00a0好"), "您好")

    def test_no_whitespace_is_unchanged_by_this_step(self):
        # 反例：没有空白的文本，这一步不该动它
        self.assertEqual(normalize_text("您好吗"), "您好吗")


class TestStep2FullToHalf(unittest.TestCase):
    """② 全角 ↔ 半角（ASCII 可见区 + ，。？！：；（））。"""

    def test_fullwidth_ascii_letters(self):
        self.assertEqual(normalize_text("ＡＢＣ"), "abc")

    def test_fullwidth_digits(self):
        self.assertEqual(normalize_text("１２ ３"), "123")

    def test_all_eight_puncts(self):
        # 逐个断言，任何一个漏映射都会让这条变红
        self.assertEqual(normalize_text("，。？！：；（）"), ",.?!:;()")
        self.assertEqual(normalize_text("（您好）"), "(您好)")

    def test_dun_hao_needs_explicit_mapping(self):
        # "。" 是 U+3002，不在 FF01–FF5E 区间内：显式表与 NFKC 都能命中，
        # 这里钉住最终结果，任何一步漏掉都会让这条变红
        self.assertEqual(normalize_text("您好。"), "您好.")

    def test_halfwidth_is_not_expanded(self):
        # 归一方向是"全角→半角"，不该反向；半角原文保持半角
        self.assertEqual(normalize_text("abc123.,"), "abc123.,")

    def test_cjk_ideographs_untouched(self):
        # 反例：中文汉字不属 ASCII 可见区，不该被平移
        self.assertEqual(normalize_text("您好"), "您好")


class TestStep3NFKC(unittest.TestCase):
    """③ Unicode NFKC：兼容字符归一。"""

    def test_circled_digit(self):
        self.assertEqual(normalize_text("①"), "1")

    def test_superscript(self):
        self.assertEqual(normalize_text("２²"), "22")

    def test_ligature(self):
        self.assertEqual(normalize_text("ﬁne"), "fine")

    def test_katakana_corp(self):
        self.assertEqual(normalize_text("㈱"), "(株)")

    def test_plain_chinese_survives_nfkc(self):
        # 反例：NFKC 不该改中文
        self.assertEqual(normalize_text("您好"), "您好")


class TestStep4Casefold(unittest.TestCase):
    """④ 大小写统一（小写）。"""

    def test_upper_to_lower(self):
        self.assertEqual(normalize_text("Hello"), "hello")

    def test_mixed(self):
        self.assertEqual(normalize_text("ABC123"), "abc123")

    def test_no_case_chars_untouched(self):
        # 反例：没有大小写的字符保持原样
        self.assertEqual(normalize_text("您１２３"), "您123")


class TestCompositeAndOrder(unittest.TestCase):
    """四步合起来，顺序冻结为 ①→②→③→④。"""

    def test_all_four_steps_at_once(self):
        # 全角空格 + 全角字母数字 + 全角标点 + 大小写
        self.assertEqual(
            normalize_text("  Ａ０１ 您好，这里是报修。 "),
            "a01您好,这里是报修.",
        )

    def test_compatible_char_with_fullwidth(self):
        self.assertEqual(normalize_text("①Ａ"), "1a")

    def test_same_input_same_output(self):
        # 确定性：同输入必得同输出（归一化是查表 + 标准库，不该有随机性）
        src = " Ａ０１ 您好，这里是报修。 "
        results = {normalize_text(src) for _ in range(5)}
        self.assertEqual(len(results), 1, f"同输入出现多个输出: {results}")

    def test_idempotent(self):
        # 再归一次不该变：四步都是单调映射
        src = " Ａ０１ ②您好，好的。 "
        once = normalize_text(src)
        self.assertEqual(normalize_text(once), once)

    def test_no_shared_state_between_calls(self):
        # 无副作用：交替归一不同文本，互不污染
        self.assertEqual(normalize_text("Ａ"), "a")
        self.assertEqual(normalize_text("Ｂ"), "b")
        self.assertEqual(normalize_text("Ａ"), "a")


class TestNoExtraStep(unittest.TestCase):
    """禁止项：多做一步（去标点 / 去语气词 / 同义改写）都会改变结果。

    这一组是"防偷偷放宽"的用例——若有人把"去标点后比较"塞进归一化，
    下面任何一条都会变红。
    """

    def test_punctuation_is_kept(self):
        a = normalize_text("您好。")
        b = normalize_text("您好")
        self.assertEqual(a, "您好.")          # 标点被归一成半角，但保留
        self.assertNotEqual(a, b)             # 有标点 ≠ 无标点

    def test_punctuation_stripping_would_be_a_false_hit(self):
        # 反证：去掉标点后这两句确实"相等"——这正是本卡不许放宽的地方
        a = normalize_text("您好。")
        b = normalize_text("您好")
        self.assertEqual(
            a.strip(",.;:!?()"), b.strip(",.;:!?()"),
            "去掉标点比较会让二者相等，因此归一化绝不允许做去标点"
        )
        self.assertNotEqual(a, b, "归一化后必须仍然不等")

    def test_backchannel_word_is_kept(self):
        # 去语气词属于语义操作：嗯/啊/哦 必须原样保留
        self.assertEqual(normalize_text("嗯，您好"), "嗯,您好")
        self.assertNotEqual(normalize_text("嗯，您好"), normalize_text("您好"))

    def test_synonym_is_kept(self):
        # 同义改写属于语义操作："什么设备" ≠ "什么设备？"，也不等于"哪个设备"
        self.assertEqual(normalize_text("什么设备"), "什么设备")
        self.assertNotEqual(
            normalize_text("请问您要报修的是什么设备？"),
            normalize_text("请问您要报修的是哪个设备？"),
        )

    def test_char_count_only_changes_via_declared_steps(self):
        # 四步都不会增删非空白字符以外的东西：空白数变化 = 去空白的结果
        src = "您好   吗"
        out = normalize_text(src)
        self.assertEqual(out.count("您"), src.count("您"))
        self.assertEqual(out.count("吗"), src.count("吗"))
        self.assertEqual(len(out), 3)


class TestInputGuard(unittest.TestCase):
    """入参不是 str 必须报错，不得静默当空串。"""

    def test_none_raises_type_error(self):
        with self.assertRaises(TypeError) as ctx:
            normalize_text(None)
        self.assertIn("NoneType", str(ctx.exception))

    def test_bytes_raises_type_error(self):
        with self.assertRaises(TypeError):
            normalize_text(b"abc")

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(normalize_text(""), "")


if __name__ == "__main__":
    unittest.main()
