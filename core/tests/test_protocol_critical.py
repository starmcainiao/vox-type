"""
core.tests.test_protocol_critical — PlanUnit.critical 字段（T19 只增）

覆盖范围：
  - critical 缺省为 False（旧 plan 无该字段 → 默认 False，向后兼容）
  - critical=true 解析成功且可访问
  - 负例：critical 非 bool → ProtocolError，消息含单元序号与实际类型
  - 既有字段（action/key/text/rate/variant/slots）语义零变化
  - CRITICAL_RATE 常量钉住

纪律：既有测试文件一行未改（新增文件承载新断言）。
"""

import unittest

from core.protocol import CRITICAL_RATE, PlanUnit, ProtocolError, parse_plan


# ============================================================
# 1. 只增字段：缺省值与向后兼容
# ============================================================
class TestCriticalDefault(unittest.TestCase):
    """旧 plan 不带 critical → 默认 False，且既有字段语义不变。"""

    def test_default_is_false(self):
        """显式构造 PlanUnit 不传 critical → False。"""
        unit = PlanUnit(key="greet")
        self.assertFalse(unit.critical)
        self.assertIsInstance(unit.critical, bool)

    def test_missing_in_raw_plan_defaults_false(self):
        """旧 plan（无 critical 键）解析成功，critical 为 False。"""
        raw = [{"key": "greet", "rate": "normal", "variant": 0,
                "slots": {"name": "小明"}}]
        plan = parse_plan(raw)
        self.assertFalse(plan[0].critical)
        # 既有字段逐字节保持
        self.assertEqual(plan[0].key, "greet")
        self.assertEqual(plan[0].rate, "normal")
        self.assertEqual(plan[0].variant, 0)
        self.assertEqual(plan[0].slots, {"name": "小明"})

    def test_dataclass_default_is_bool(self):
        """字段默认值必须是真正的 bool（不是 0 或 None）。"""
        self.assertIs(PlanUnit(key="x").critical, False)


# ============================================================
# 2. 正例：critical=true
# ============================================================
class TestCriticalPositive(unittest.TestCase):
    """critical=true 必须解析成功并原样落到 PlanUnit。"""

    def test_true_is_accepted(self):
        """显式 critical=true 解析成功。"""
        plan = parse_plan([{"key": "amount", "rate": "normal", "critical": True}])
        self.assertIs(plan[0].critical, True)

    def test_false_is_accepted(self):
        """显式 critical=false 同样合法（允许策划显式否认）。"""
        plan = parse_plan([{"key": "greet", "critical": False}])
        self.assertIs(plan[0].critical, False)

    def test_true_coexists_with_other_fields(self):
        """critical 与其他字段共存，互不干扰。"""
        plan = parse_plan([
            {"key": "amount", "rate": "normal", "variant": 2,
             "critical": True, "slots": {"value": "30 元"}},
        ])
        unit = plan[0]
        self.assertIs(unit.critical, True)
        self.assertEqual(unit.key, "amount")
        self.assertEqual(unit.variant, 2)
        self.assertEqual(unit.slots, {"value": "30 元"})
        self.assertEqual(unit.action, "SAY")

    def test_critical_on_text_unit(self):
        """text 单元也可标 critical（自由文本单元在 runtime 侧按现状走 miss）。"""
        plan = parse_plan([{"text": "现场确认金额", "critical": True}])
        self.assertIs(plan[0].critical, True)
        self.assertEqual(plan[0].action, "SAY_LIVE")

    def test_mixed_units(self):
        """同一 plan 内 critical 与非 critical 单元并存，各自独立。"""
        plan = parse_plan([
            {"key": "greet"},
            {"key": "amount", "critical": True},
            {"key": "bye", "critical": False},
        ])
        self.assertEqual([u.critical for u in plan], [False, True, False])


# ============================================================
# 3. 负例：critical 非 bool → ProtocolError
# ============================================================
class TestCriticalNegative(unittest.TestCase):
    """critical 必须是真正的 bool——字符串/整数/None 一律拒绝。

    WHY 严格：关键信息标记一旦放宽，runtime 就分不清"策划漏标"和"脚本写错"，
    而漏标会让关键信息按 normal 档播出去（静默降级）。
    """

    def _assert_rejected(self, raw_value, label):
        """喂一个非法 critical 值，断言异常类型 + 消息含序号与实际类型。

        序号口径：parse_plan 用 enumerate 从 0 起编号（既有负例测试亦如此），
        因此索引以 "#<index>" 形式出现在消息里。
        """
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan([{"key": "amount", "critical": raw_value}])
        message = str(ctx.exception)
        self.assertIn("#0", message,
                      f"消息必须含单元序号（{label}），实际: {message!r}")
        self.assertIn(type(raw_value).__name__, message,
                      f"消息必须含实际类型 {type(raw_value).__name__}（{label}），"
                      f"实际: {message!r}")
        self.assertIn("critical", message,
                      f"消息必须含字段名（{label}），实际: {message!r}")
        return message

    def test_string_true_rejected(self):
        """critical="true" → 拒绝（消息含实际类型 str 与实际值）。"""
        message = self._assert_rejected("true", "字符串 'true'")
        self.assertIn("str", message)
        self.assertIn("'true'", message)

    def test_string_yes_rejected(self):
        """critical="yes" → 拒绝。"""
        self._assert_rejected("yes", "字符串 'yes'")

    def test_int_one_rejected(self):
        """critical=1 → 拒绝（1 不是 bool；int 是父类不算数）。"""
        message = self._assert_rejected(1, "整数 1")
        self.assertIn("int", message)
        self.assertIn("1", message)

    def test_float_rejected(self):
        """critical=1.0 → 拒绝。"""
        message = self._assert_rejected(1.0, "浮点 1.0")
        self.assertIn("float", message)

    def test_none_rejected(self):
        """critical=None → 拒绝（None 不等于"未给出"，写 key 就是要校验）。"""
        message = self._assert_rejected(None, "None")
        self.assertIn("NoneType", message)

    def test_list_rejected(self):
        """critical=[] → 拒绝。"""
        message = self._assert_rejected([], "空列表")
        self.assertIn("list", message)

    def test_error_points_at_correct_unit_index(self):
        """第二个单元出错时，消息必须指向 #1（0 起编号）而不是 #0。"""
        raw = [{"key": "greet"}, {"key": "amount", "critical": "true"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        message = str(ctx.exception)
        self.assertIn("#1", message, f"必须定位到第二个单元，实际: {message!r}")
        self.assertNotIn("#0", message, f"不得误指向第一个单元，实际: {message!r}")

    def test_valid_plan_still_passes_after_checks(self):
        """加了校验之后，全 bool 的 plan 仍全部通过（不放松、不误伤）。"""
        plan = parse_plan([
            {"key": "a", "critical": True},
            {"key": "b", "critical": False},
            {"key": "c"},
        ])
        self.assertEqual(len(plan), 3)
        self.assertEqual([u.critical for u in plan], [True, False, False])


# ============================================================
# 4. 常量钉住
# ============================================================
class TestCriticalRateConstant(unittest.TestCase):
    """关键信息档必须钉在 slow——改档等于改语义，必须测试失败。"""

    def test_critical_rate_is_slow(self):
        """CRITICAL_RATE == 'slow'。"""
        self.assertEqual(CRITICAL_RATE, "slow")

    def test_critical_rate_is_valid_rate(self):
        """CRITICAL_RATE 必须本身是合法档位（否则 runtime 会拼出查不到的档）。"""
        from core.protocol import VALID_RATES
        self.assertIn(CRITICAL_RATE, VALID_RATES)


if __name__ == "__main__":
    unittest.main()
