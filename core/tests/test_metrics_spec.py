"""
core.tests.test_metrics_spec — metrics_spec 模块的字段名钉住测试

职责：确保指标字段名常量值不变，改名必须导致测试失败。
不负责：不测试字段的业务语义（那是 eval/ 的活）。
"""

import unittest
from core import metrics_spec


# ============================================================
# 1. 字段名钉住测试
# ============================================================
class TestMetricFieldNamePinning(unittest.TestCase):
    """钉住每个指标字段的字符串值——改名 = 测试失败。"""

    def test_hit(self):
        self.assertEqual(metrics_spec.HIT, "hit")

    def test_miss(self):
        self.assertEqual(metrics_spec.MISS, "miss")

    def test_fallback(self):
        self.assertEqual(metrics_spec.FALLBACK, "fallback")

    def test_reason(self):
        self.assertEqual(metrics_spec.REASON, "reason")

    def test_key(self):
        self.assertEqual(metrics_spec.KEY, "key")

    def test_part(self):
        self.assertEqual(metrics_spec.PART, "part")

    def test_rate(self):
        self.assertEqual(metrics_spec.RATE, "rate")

    def test_variant(self):
        self.assertEqual(metrics_spec.VARIANT, "variant")

    def test_first_audio_ms(self):
        self.assertEqual(metrics_spec.FIRST_AUDIO_MS, "first_audio_ms")

    def test_pack_version(self):
        self.assertEqual(metrics_spec.PACK_VERSION, "pack_version")

    def test_ts(self):
        self.assertEqual(metrics_spec.TS, "ts")

    def test_turn_id(self):
        self.assertEqual(metrics_spec.TURN_ID, "turn_id")

    def test_plan_id(self):
        self.assertEqual(metrics_spec.PLAN_ID, "plan_id")

    def test_hit_rate(self):
        self.assertEqual(metrics_spec.HIT_RATE, "hit_rate")

    def test_precast_ratio(self):
        self.assertEqual(metrics_spec.PRECAST_RATIO, "precast_ratio")


# ============================================================
# 2. METRIC_FIELDS 完整性测试
# ============================================================
class TestMetricFieldsCompleteness(unittest.TestCase):
    """METRIC_FIELDS 必须包含全部 15 个字段常量。"""

    def test_all_fields_in_frozenset(self):
        """所有常量必须在 METRIC_FIELDS 中。"""
        expected = {
            "hit",
            "miss",
            "fallback",
            "reason",
            "key",
            "part",
            "rate",
            "variant",
            "first_audio_ms",
            "pack_version",
            "ts",
            "turn_id",
            "plan_id",
            "hit_rate",
            "precast_ratio",
        }
        self.assertEqual(metrics_spec.METRIC_FIELDS, expected)

    def test_field_count(self):
        """METRIC_FIELDS 必须包含恰好 15 个字段。"""
        self.assertEqual(len(metrics_spec.METRIC_FIELDS), 15)


# ============================================================
# 3. 导出一致性测试
# ============================================================
class TestExportsConsistency(unittest.TestCase):
    """导出的常量值必须与 METRIC_FIELDS 中的值一致。"""

    def test_all_constants_are_strings(self):
        """所有字段常量必须是字符串类型。"""
        constants = [
            metrics_spec.HIT,
            metrics_spec.MISS,
            metrics_spec.FALLBACK,
            metrics_spec.REASON,
            metrics_spec.KEY,
            metrics_spec.PART,
            metrics_spec.RATE,
            metrics_spec.VARIANT,
            metrics_spec.FIRST_AUDIO_MS,
            metrics_spec.PACK_VERSION,
            metrics_spec.TS,
            metrics_spec.TURN_ID,
            metrics_spec.PLAN_ID,
            metrics_spec.HIT_RATE,
            metrics_spec.PRECAST_RATIO,
        ]
        for const in constants:
            self.assertIsInstance(const, str, f"字段 {const!r} 必须是字符串")


if __name__ == "__main__":
    unittest.main()
