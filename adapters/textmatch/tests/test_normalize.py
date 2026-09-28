"""
adapters.textmatch.tests.test_normalize — 归一化第 ③ 步（NFKC）是真判据，不是摆设

本文件是 T47 注入验证的靶子：把 `adapters/textmatch/normalize.py` 第 ③ 步
（`unicodedata.normalize("NFKC", s)`）删掉，下面 `TestStepThree` 的用例**必须判红**。

WHY 只测这一小段：四步语义本身由 `adapters/framework_kefu/tests/test_normalize.py`
    （既有测试，仍从 re-export 薄壳导入）逐条覆盖；本文件只钉住 T47 特有的那一条
    ——「兼容字符（①）必须与半角数字判为同一句话」，因为它正是 MCP 与 kefu
    两入口当初判出分歧的那一句。
"""

import unittest

from adapters.textmatch import normalize_text


class TestStepThree(unittest.TestCase):
    """第 ③ 步 NFKC：把"长得一样但码位不同"的兼容字符折成同一串。"""

    def test_circled_digit_equals_ascii_digit(self):
        """本卡的原始分歧例：包内 `价格①` / 传入 `价格1` 必须判为同一句。"""
        self.assertEqual(normalize_text("价格①"), normalize_text("价格1"))

    def test_circled_digit_normalizes_to_plain_digit(self):
        self.assertEqual(normalize_text("价格①"), "价格1")

    def test_halfwidth_and_fullwidth_agree_after_step_three(self):
        """全角数字走第 ② 步、兼容字符走第 ③ 步，两条路径终值必须一致。"""
        self.assertEqual(normalize_text("价格１"), normalize_text("价格1"))

    def test_still_covers_more_compatibility_chars(self):
        """③ 步不只管 ①：² / ﬁ 同属兼容字符族。"""
        self.assertEqual(normalize_text("２²"), "22")
        self.assertEqual(normalize_text("ﬁne"), "fine")


class TestPurity(unittest.TestCase):
    """归一化是纯函数：确定性、幂等、不吃 None。"""

    def test_deterministic_across_calls(self):
        src = "  Ａ０１ 您好，这里是报修。  "
        results = {normalize_text(src) for _ in range(5)}
        self.assertEqual(len(results), 1)

    def test_idempotent(self):
        once = normalize_text("  Ａ０１ 您好，这里是报修。  ")
        self.assertEqual(normalize_text(once), once)

    def test_empty_string_stays_empty(self):
        self.assertEqual(normalize_text(""), "")

    def test_none_raises_typeerror(self):
        with self.assertRaises(TypeError):
            normalize_text(None)


class TestSingleImplementation(unittest.TestCase):
    """唯一实现：re-export 薄壳转发的必须是**同一个函数对象**。"""

    def test_framework_kefu_shell_is_the_same_object(self):
        from adapters.framework_kefu.normalize import (
            normalize_text as via_shell,
        )
        from adapters.framework_kefu import normalize_text as via_pkg_root

        self.assertIs(via_shell, normalize_text)
        self.assertIs(via_pkg_root, normalize_text)

    def test_hit_shell_is_the_same_object(self):
        from adapters.framework_kefu.hit_query import find_hit as via_shell
        from adapters.textmatch import find_hit

        self.assertIs(via_shell, find_hit)


if __name__ == "__main__":
    unittest.main()
