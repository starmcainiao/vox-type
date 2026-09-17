"""
runtime.tests.test_duplex — DuplexParams 三档预设与非法值拒绝（正例 + 负例）

覆盖范围：
  - 三档预设：fast/default/slow_thinking 的 patience_ms 分别为 400/900/1800，其余字段取默认
  - 构造即校验（__post_init__）：非法值当场抛 DuplexError，不得静默接受
  - 错误消息可定位：必须含字段名与实际值
  - 边界值放行：区间端点与 fade_ms=0 合法
  - 冻结：frozen dataclass 不允许改字段
  - 不得 import compiler/（本层只依赖 core/）
"""

import unittest
from dataclasses import FrozenInstanceError

from runtime.duplex import DuplexError, DuplexParams


# ---------------------------------------------------------------------------
# 1. 三档预设
# ---------------------------------------------------------------------------
class TestPresets(unittest.TestCase):
    """三档耐心窗预设必须精确取 400 / 900 / 1800。"""

    def test_fast_patience_is_400(self):
        """fast() 的 patience_ms 必须是 400（快语速）。"""
        self.assertEqual(DuplexParams.fast().patience_ms, 400)

    def test_default_patience_is_900(self):
        """default() 的 patience_ms 必须是 900（默认）。"""
        self.assertEqual(DuplexParams.default().patience_ms, 900)

    def test_slow_thinking_patience_is_1800(self):
        """slow_thinking() 的 patience_ms 必须是 1800（慢思考）。"""
        self.assertEqual(DuplexParams.slow_thinking().patience_ms, 1800)

    def test_presets_keep_other_fields_at_default(self):
        """三档预设只改 patience_ms，其余字段必须取默认值（不得顺手改别的）。"""
        defaults = {
            "rate_band": 0.15,
            "backchannel": "on",
            "barge_in": "allow",
            "silence_pad_ms": 200,
            "slot_pad_ms": 80,
            "fade_ms": 5,
        }
        for preset in (DuplexParams.fast(), DuplexParams.default(),
                       DuplexParams.slow_thinking()):
            for field_name, expected in defaults.items():
                self.assertEqual(
                    getattr(preset, field_name), expected,
                    f"预设 {preset.patience_ms}ms 的 {field_name} 应为默认值 {expected!r}",
                )


# ---------------------------------------------------------------------------
# 2. 非法值拒绝（负例：必须真的违规）
# ---------------------------------------------------------------------------
class TestIllegalValuesRejected(unittest.TestCase):
    """非法取值必须构造即抛 DuplexError，且消息含字段名与实际值。"""

    # (字段名, 实际值, 期望消息中出现的字段名, 期望消息中出现的值)
    CASES = [
        ({"patience_ms": 1000}, "patience_ms", "1000"),
        ({"patience_ms": 500}, "patience_ms", "500"),
        ({"silence_pad_ms": 50}, "silence_pad_ms", "50"),
        ({"silence_pad_ms": 301}, "silence_pad_ms", "301"),
        ({"slot_pad_ms": 10}, "slot_pad_ms", "10"),
        ({"slot_pad_ms": 151}, "slot_pad_ms", "151"),
        ({"backchannel": "maybe"}, "backchannel", "maybe"),
        ({"barge_in": "x"}, "barge_in", "x"),
        ({"fade_ms": -1}, "fade_ms", "-1"),
        ({"rate_band": 0}, "rate_band", "0"),
        ({"rate_band": 1.5}, "rate_band", "1.5"),
    ]

    def test_illegal_value_raises_duplex_error(self):
        """每个非法取值都必须抛 DuplexError（而不是回落成默认值）。"""
        for kwargs, field_name, value_text in self.CASES:
            with self.subTest(field=field_name, value=value_text):
                with self.assertRaises(DuplexError) as ctx:
                    DuplexParams(**kwargs)
                message = str(ctx.exception)
                self.assertIn(
                    field_name, message,
                    f"错误消息必须含字段名 {field_name!r}，实际: {message!r}",
                )
                self.assertIn(
                    value_text, message,
                    f"错误消息必须含实际值 {value_text!r}，实际: {message!r}",
                )

    def test_illegal_value_is_not_silently_accepted(self):
        """非法值不得被悄悄替换成默认值——只允许抛错这一条路。"""
        with self.assertRaises(DuplexError):
            DuplexParams(patience_ms=1000)
        with self.assertRaises(DuplexError):
            DuplexParams(silence_pad_ms=50)


# ---------------------------------------------------------------------------
# 3. 边界值放行（确认区间是闭区间、fade_ms=0 合法）
# ---------------------------------------------------------------------------
class TestBoundaryValuesAccepted(unittest.TestCase):
    """区间端点与 fade_ms=0 必须合法——过严会把合法配置误拦。"""

    def test_silence_pad_boundaries_accepted(self):
        """silence_pad_ms 的 120 与 300 两个端点必须合法。"""
        self.assertEqual(DuplexParams(silence_pad_ms=120).silence_pad_ms, 120)
        self.assertEqual(DuplexParams(silence_pad_ms=300).silence_pad_ms, 300)

    def test_slot_pad_boundaries_accepted(self):
        """slot_pad_ms 的 50 与 150 两个端点必须合法。"""
        self.assertEqual(DuplexParams(slot_pad_ms=50).slot_pad_ms, 50)
        self.assertEqual(DuplexParams(slot_pad_ms=150).slot_pad_ms, 150)

    def test_zero_fade_accepted(self):
        """fade_ms=0 表示关闭淡入淡出，必须合法（只有负数非法）。"""
        self.assertEqual(DuplexParams(fade_ms=0).fade_ms, 0)

    def test_all_three_patience_tiers_accepted(self):
        """400/900/1800 三档必须都合法。"""
        for patience in (400, 900, 1800):
            self.assertEqual(
                DuplexParams(patience_ms=patience).patience_ms, patience,
                f"耐心窗 {patience}ms 应为合法值",
            )


# ---------------------------------------------------------------------------
# 4. 冻结与显式校验
# ---------------------------------------------------------------------------
class TestFrozenAndExplicitValidate(unittest.TestCase):
    """DuplexParams 是冻结对象，且 validate() 可被显式调用。"""

    def test_instance_is_frozen(self):
        """frozen dataclass：改字段必须抛 FrozenInstanceError。"""
        params = DuplexParams.default()
        with self.assertRaises(FrozenInstanceError):
            params.patience_ms = 400

    def test_validate_returns_self(self):
        """validate() 通过校验后返回 self，便于链式使用。"""
        params = DuplexParams.fast()
        self.assertIs(params.validate(), params)

    def test_default_construction_is_valid(self):
        """无参数构造必须直接合法（默认值本身要落在合法区间内）。"""
        params = DuplexParams()
        self.assertEqual(params.validate().patience_ms, 900)
