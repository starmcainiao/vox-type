"""
core.tests.test_protocol — protocol 模块的正例 + 负例测试

覆盖范围：
  - 原语白名单校验（负例：未知原语 → ProtocolError 且消息含原语名）
  - plan 解析（正例：合法 plan；负例：key/text 二选一、rate 非法）
  - 参数覆盖优先级（三档用例）
"""

import unittest
from core.protocol import (
    PRIMITIVES,
    VALID_RATES,
    ProtocolError,
    PlanUnit,
    parse_plan,
    resolve_params,
    validate_primitive,
)


# ============================================================
# 1. 原语白名单
# ============================================================
class TestPrimitiveWhitelist(unittest.TestCase):
    """原语集合必须是封闭白名单，未知原语必须被拒绝。"""

    def test_known_primitives_present(self):
        """已知七个原语全部在白名单中。"""
        expected = {"SAY", "SLOT", "SAY_LIVE", "PAD", "LISTEN", "PRELOAD", "END"}
        self.assertEqual(PRIMITIVES, expected)

    def test_unknown_primitive_raises_error(self):
        """负例：未知原语（如 SING）→ ProtocolError 且消息含 SING。"""
        # 通过 parse_plan 触发原语校验
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan([{"key": "greet", "action": "SING"}])
        self.assertIn("SING", str(ctx.exception))

    def test_validate_primitive_direct(self):
        """直接调用 validate_primitive 校验未知原语。"""
        with self.assertRaises(ProtocolError) as ctx:
            validate_primitive("SING")
        self.assertIn("SING", str(ctx.exception))

    def test_validate_primitive_known(self):
        """已知原语通过校验并返回原名。"""
        result = validate_primitive("SAY")
        self.assertEqual(result, "SAY")

    def test_action_inference_key_to_say(self):
        """action 缺省时，有 key 推断为 SAY。"""
        raw = [{"key": "greet"}]
        plan = parse_plan(raw)
        self.assertEqual(plan[0].action, "SAY")

    def test_action_inference_text_to_say_live(self):
        """action 缺省时，有 text 推断为 SAY_LIVE。"""
        raw = [{"text": "hello"}]
        plan = parse_plan(raw)
        self.assertEqual(plan[0].action, "SAY_LIVE")


# ============================================================
# 2. plan 解析 — 正例
# ============================================================
class TestParsePlanPositive(unittest.TestCase):
    """合法 plan 必须解析成功，字段可访问。"""

    def test_key_based_unit(self):
        """key 模式的合法单元。"""
        raw = [{"key": "greeting", "rate": "slow", "variant": 1, "slots": {"name": "小明"}}]
        plan = parse_plan(raw)
        self.assertEqual(len(plan), 1)
        unit = plan[0]
        self.assertEqual(unit.key, "greeting")
        self.assertIsNone(unit.text)
        self.assertEqual(unit.rate, "slow")
        self.assertEqual(unit.variant, 1)
        self.assertEqual(unit.slots, {"name": "小明"})

    def test_text_based_unit(self):
        """text 模式的合法单元。"""
        raw = [{"text": "自定义文本", "rate": "fast"}]
        plan = parse_plan(raw)
        self.assertEqual(len(plan), 1)
        unit = plan[0]
        self.assertIsNone(unit.key)
        self.assertEqual(unit.text, "自定义文本")
        self.assertEqual(unit.rate, "fast")
        self.assertEqual(unit.variant, "auto")
        self.assertEqual(unit.slots, {})

    def test_multi_unit_plan(self):
        """多单元 plan 解析。"""
        raw = [
            {"key": "step1", "rate": "normal"},
            {"text": "过渡文本", "rate": "slow", "variant": 3},
            {"key": "step2", "rate": "fast", "variant": "auto"},
        ]
        plan = parse_plan(raw)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0].key, "step1")
        self.assertEqual(plan[1].text, "过渡文本")
        self.assertEqual(plan[2].key, "step2")

    def test_default_rate_is_normal(self):
        """缺省 rate 默认为 normal。"""
        raw = [{"key": "default_rate"}]
        plan = parse_plan(raw)
        self.assertEqual(plan[0].rate, "normal")


# ============================================================
# 3. plan 解析 — 负例
# ============================================================
class TestParsePlanNegative(unittest.TestCase):
    """非法 plan 必须被拒绝，报错信息准确。"""

    def test_unknown_primitive_in_unit(self):
        """负例 1：未知原语（如 SING）→ ProtocolError 且消息含 SING。"""
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan([{"key": "greet", "action": "SING"}])
        self.assertIn("SING", str(ctx.exception))

    def test_invalid_rate_raises_error(self):
        """负例 2：rate="very_slow" → 报错（不回落到 normal）。"""
        raw = [{"key": "test", "rate": "very_slow"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        # 错误消息必须包含非法值 "very_slow"
        self.assertIn("very_slow", str(ctx.exception))
        # 抛出异常本身就证明没有静默回落——正常流程不应到达此处

    def test_missing_key_and_text(self):
        """负例 3a：单元同时缺 key 和 text → 报错。"""
        raw = [{"rate": "normal"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        self.assertIn("key", str(ctx.exception))
        self.assertIn("text", str(ctx.exception))

    def test_both_key_and_text(self):
        """负例 3b：单元同时给 key 和 text → 报错。"""
        raw = [{"key": "a", "text": "b", "rate": "normal"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        self.assertIn("key", str(ctx.exception))
        self.assertIn("text", str(ctx.exception))

    def test_empty_plan_raises_error(self):
        """空 plan 列表 → 报错。"""
        with self.assertRaises(ProtocolError):
            parse_plan([])

    def test_non_list_plan_raises_error(self):
        """非列表 plan → 报错。"""
        with self.assertRaises(ProtocolError):
            parse_plan("not a list")

    def test_non_dict_unit_raises_error(self):
        """单元不是字典 → 报错。"""
        with self.assertRaises(ProtocolError):
            parse_plan(["not a dict"])

    def test_invalid_variant_type(self):
        """variant 非整数且非 "auto" → 报错。"""
        raw = [{"key": "test", "variant": "unknown"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        self.assertIn("variant", str(ctx.exception))

    def test_invalid_slots_type(self):
        """slots 非字典 → 报错。"""
        raw = [{"key": "test", "slots": "not a dict"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        self.assertIn("slots", str(ctx.exception))


# ============================================================
# 4. 参数覆盖优先级
# ============================================================
class TestResolveParams(unittest.TestCase):
    """三档参数合并：业务默认 < 会话覆盖 < 剧本显式。"""

    def test_session_overrides_business(self):
        """会话覆盖覆盖业务默认。"""
        result = resolve_params(
            business_default={"rate": "slow", "variant": 1},
            session_override={"rate": "fast"},
            utterance_explicit={},
        )
        self.assertEqual(result["rate"], "fast")
        self.assertEqual(result["variant"], 1)

    def test_utterance_overrides_session(self):
        """剧本显式覆盖会话覆盖。"""
        result = resolve_params(
            business_default={"rate": "slow"},
            session_override={"rate": "normal"},
            utterance_explicit={"rate": "fast"},
        )
        self.assertEqual(result["rate"], "fast")

    def test_utterance_overrides_all(self):
        """剧本显式同时覆盖业务默认和会话覆盖。"""
        result = resolve_params(
            business_default={"rate": "slow", "variant": 1, "extra": "a"},
            session_override={"rate": "normal", "variant": 2},
            utterance_explicit={"rate": "fast", "variant": 3},
        )
        self.assertEqual(result["rate"], "fast")
        self.assertEqual(result["variant"], 3)
        self.assertEqual(result["extra"], "a")

    def test_no_overrides_returns_defaults(self):
        """无覆盖时返回业务默认。"""
        result = resolve_params(
            business_default={"rate": "slow"},
            session_override={},
            utterance_explicit={},
        )
        self.assertEqual(result, {"rate": "slow"})

    def test_partial_override_merges(self):
        """部分覆盖保留未覆盖字段。"""
        result = resolve_params(
            business_default={"a": 1, "b": 2},
            session_override={"b": 20},
            utterance_explicit={"c": 300},
        )
        self.assertEqual(result, {"a": 1, "b": 20, "c": 300})


# ============================================================
# 5. ProtocolError 消息准确性
# ============================================================
class TestProtocolErrorMessages(unittest.TestCase):
    """ProtocolError 消息必须包含导致失败的具体值。"""

    def test_unknown_primitive_message_contains_name(self):
        """错误消息包含未知原语名。"""
        with self.assertRaises(ProtocolError) as ctx:
            validate_primitive("SING")
        self.assertIn("SING", str(ctx.exception))

    def test_invalid_rate_message_contains_value(self):
        """错误消息包含非法 rate 值。"""
        raw = [{"key": "test", "rate": "turbo"}]
        with self.assertRaises(ProtocolError) as ctx:
            parse_plan(raw)
        self.assertIn("turbo", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
