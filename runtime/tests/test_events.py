"""
runtime.tests.test_events — 事件构造（字段名一律取自 core.metrics_spec）

覆盖范围：
  - 键名全部来自 core.metrics_spec.METRIC_FIELDS（实现里不得写字面量字段名）
  - 三态用 HIT/MISS/FALLBACK 常量作为键，且一条事件只出现一个三态
  - PART 从 1 开始（1 起的真整数，0/负数/bool 一律拒绝）
  - VARIANT 不得是 "auto"（必须在 executor 里解析成整数）
  - 留痕红线：miss / fallback 必须带非空 reason
  - 实现源码里没有字符串字面量形式的指标字段名（自检，防回归）
  - 不得 import compiler/（本层只依赖 core/）
"""

import ast
import unittest
from pathlib import Path

import core.metrics_spec as spec
from runtime.events import EventError, build_event

# 事件实现与执行器实现的源码路径（用于"无字面量字段名"自检）
_RUNTIME_DIR = Path(__file__).resolve().parent.parent
_EVENT_SOURCE_FILES = ("events.py", "executor.py")


# ---------------------------------------------------------------------------
# 1. 键名契约
# ---------------------------------------------------------------------------
class TestEventKeys(unittest.TestCase):
    """事件字典的键必须全部来自 core.metrics_spec。"""

    def _event(self, **overrides):
        """构造一条默认 hit 事件，便于按需覆盖字段。"""
        defaults = {
            "turn_id": "turn-1",
            "plan_id": "plan-1",
            "part": 1,
            "state": spec.HIT,
        }
        defaults.update(overrides)
        return build_event(**defaults)

    def test_all_keys_come_from_metric_spec(self):
        """任何键都不允许自造——必须是 METRIC_FIELDS 的成员。"""
        event = self._event()
        unknown = set(event.keys()) - spec.METRIC_FIELDS
        self.assertEqual(
            unknown, set(),
            f"事件出现了 metrics_spec 之外的键: {sorted(unknown)}",
        )

    def test_all_expected_fields_present(self):
        """AGENTS.md §④ 约定的字段一个都不能少。"""
        event = self._event()
        expected = {
            spec.TS, spec.TURN_ID, spec.PLAN_ID, spec.KEY, spec.PART,
            spec.RATE, spec.VARIANT, spec.REASON, spec.PACK_VERSION,
            spec.FIRST_AUDIO_MS, spec.HIT,
        }
        self.assertEqual(set(event.keys()), expected)

    def test_state_key_holds_the_state(self):
        """三态作为键出现（AGENTS.md §④ 中 "hit|miss|fallback" 占独立一位）。"""
        hit = self._event(state=spec.HIT)
        self.assertIs(hit[spec.HIT], True)
        self.assertNotIn(spec.MISS, hit)
        self.assertNotIn(spec.FALLBACK, hit)

    def test_only_one_state_key_per_event(self):
        """一条事件只能出现一个三态——多态 = 事件不可判读。"""
        for state in (spec.HIT, spec.MISS, spec.FALLBACK):
            with self.subTest(state=state):
                event = self._event(
                    state=state, reason="" if state == spec.HIT else "some_reason",
                )
                present = [s for s in (spec.HIT, spec.MISS, spec.FALLBACK) if s in event]
                self.assertEqual(present, [state])


# ---------------------------------------------------------------------------
# 2. PART 序号
# ---------------------------------------------------------------------------
class TestPartNumbering(unittest.TestCase):
    """PART 是同一 turn 内的执行序号，从 1 开始。"""

    def test_part_starts_at_one(self):
        """part=1 合法，且原样写进事件。"""
        event = build_event(
            turn_id="t", plan_id="p", part=1, state=spec.HIT,
        )
        self.assertEqual(event[spec.PART], 1)

    def test_part_zero_rejected(self):
        """part=0 非法（序号从 1 开始），消息含字段名与值。"""
        with self.assertRaises(EventError) as ctx:
            build_event(turn_id="t", plan_id="p", part=0, state=spec.HIT)
        self.assertIn("part", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))

    def test_part_negative_rejected(self):
        """负序号非法。"""
        with self.assertRaises(EventError) as ctx:
            build_event(turn_id="t", plan_id="p", part=-1, state=spec.HIT)
        self.assertIn("part", str(ctx.exception))

    def test_part_bool_rejected(self):
        """bool 是 int 子类，必须显式排除——True 不是合法序号。"""
        with self.assertRaises(EventError):
            build_event(turn_id="t", plan_id="p", part=True, state=spec.HIT)


# ---------------------------------------------------------------------------
# 3. VARIANT 与留痕红线
# ---------------------------------------------------------------------------
class TestVariantAndReason(unittest.TestCase):
    """variant 必须已解析；miss / fallback 必须带原因。"""

    def test_variant_auto_rejected(self):
        """'auto' 必须已在 executor 里解析成整数，不得原样进事件。"""
        with self.assertRaises(EventError) as ctx:
            build_event(
                turn_id="t", plan_id="p", part=1, state=spec.MISS,
                reason="key_not_prebaked", variant="auto",
            )
        self.assertIn("variant", str(ctx.exception))

    def test_variant_non_int_rejected(self):
        """variant 只允许整数或 None。"""
        with self.assertRaises(EventError) as ctx:
            build_event(
                turn_id="t", plan_id="p", part=1, state=spec.HIT, variant=1.5,
            )
        self.assertIn("variant", str(ctx.exception))

    def test_variant_int_and_none_accepted(self):
        """整数（已解析）与 None（无候选可选）都必须合法。"""
        for variant in (0, 1, 7, None):
            with self.subTest(variant=variant):
                event = build_event(
                    turn_id="t", plan_id="p", part=1, state=spec.HIT, variant=variant,
                )
                self.assertEqual(event[spec.VARIANT], variant)

    def test_miss_requires_reason(self):
        """miss 不带原因 = 留痕缺失，必须拒绝（docs/06 §6.2.2）。"""
        with self.assertRaises(EventError) as ctx:
            build_event(turn_id="t", plan_id="p", part=1, state=spec.MISS, reason="")
        self.assertIn("reason", str(ctx.exception))
        with self.assertRaises(EventError):
            build_event(turn_id="t", plan_id="p", part=1, state=spec.MISS)

    def test_fallback_requires_reason(self):
        """fallback 不带原因同样拒绝——三种降级原因必须可区分。"""
        with self.assertRaises(EventError):
            build_event(turn_id="t", plan_id="p", part=1, state=spec.FALLBACK, reason="")

    def test_hit_reason_is_empty_by_convention(self):
        """命中的 reason 为空字符串（docs/06 §6.2.7 ② 的表里命中为"空"）。"""
        event = build_event(turn_id="t", plan_id="p", part=1, state=spec.HIT, reason="")
        self.assertEqual(event[spec.REASON], "")

    def test_invalid_state_rejected(self):
        """三态只认冻结常量，自造结果名必须拒绝。"""
        with self.assertRaises(EventError) as ctx:
            build_event(turn_id="t", plan_id="p", part=1, state="maybe")
        self.assertIn("maybe", str(ctx.exception))


# ---------------------------------------------------------------------------
# 4. 时间戳
# ---------------------------------------------------------------------------
class TestTimestamp(unittest.TestCase):
    """TS 默认取当前 UTC（ISO 8601），也可注入固定值（评测重放用）。"""

    def test_ts_defaults_to_utc_iso(self):
        """缺省 ts 必须是带 Z 后缀的 UTC ISO 时间戳。"""
        event = build_event(turn_id="t", plan_id="p", part=1, state=spec.HIT)
        ts = event[spec.TS]
        self.assertTrue(ts.endswith("Z"), f"TS 应为 UTC（Z 后缀），实际 {ts!r}")
        self.assertIn("T", ts)

    def test_ts_injection_preserved(self):
        """注入的固定 ts 必须原样落进事件。"""
        event = build_event(
            turn_id="t", plan_id="p", part=1, state=spec.HIT,
            ts="2026-01-01T00:00:00Z",
        )
        self.assertEqual(event[spec.TS], "2026-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# 5. 实现自检：不得写字面量字段名
# ---------------------------------------------------------------------------
class TestNoLiteralFieldNames(unittest.TestCase):
    """实现源码里不允许用字符串字面量当字典键（防回归）。"""

    def _literal_string_keys(self, source):
        """用 AST 找出源码里所有"字符串字面量作字典键"的 (行号, 值) 对。

        为什么用 AST 而不是 grep：executor.py 里有 AssetPack 的成员名字面量
        （如 "pack_version"）与关键词参数名，它们不是事件键，grep 会误报。
        """
        tree = ast.parse(source)
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key_node in node.keys:
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    hits.append((node.lineno, key_node.value))
        return hits

    def test_no_literal_metric_field_names_as_dict_keys(self):
        """events.py / executor.py 里不得用字面量指标字段名当字典键。"""
        for filename in _EVENT_SOURCE_FILES:
            with self.subTest(file=filename):
                source = (_RUNTIME_DIR / filename).read_text(encoding="utf-8")
                for lineno, value in self._literal_string_keys(source):
                    self.assertNotIn(
                        value, spec.METRIC_FIELDS,
                        f"{filename}:{lineno} 用字面量 {value!r} 当键"
                        f"——必须改用 core.metrics_spec 常量",
                    )

    def test_events_module_imports_metric_constants(self):
        """events.py 必须从 core.metrics_spec 引入常量，而不是自己定义字符串。"""
        source = (_RUNTIME_DIR / "events.py").read_text(encoding="utf-8")
        self.assertIn("core.metrics_spec", source)


if __name__ == "__main__":
    unittest.main()
