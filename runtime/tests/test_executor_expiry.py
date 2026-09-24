"""
runtime.tests.test_executor_expiry - 过期资产拒播（T18 红线用例）

覆盖范围（对应 T18 验收标准 1/2/5）：
  1. 正例：invalid_at 在未来 -> lookup(now=失效前) 命中、Executor 正常播、零 TTS
  2. 负例（红线）：invalid_at 早于当前 ->
       (1) lookup 返回 None
       (2) Executor 拒播，事件/异常带明确 reason = asset_expired
       (3) 不写输出文件
       (4) allow_fallback=True **也拒播**（播报过期事实比不播更糟）
  3. 判定唯一来源：runtime 复用 assets.is_expired（同一判据，不各写一份）
  4. 向后兼容：无失效字段的旧包在任意 now= 下行为不变
  5. 时间注入：全部断言注入 now，不 sleep - 评测可重放

纪律：资产包在 tempfile 手搓；不得 import compiler/ 或 rules/。
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core.metrics_spec as spec
from assets.pack import is_expired, load_pack

from runtime.executor import (
    REASON_ASSET_EXPIRED,
    Executor,
    RuntimeMissError,
)
from runtime.tests.test_executor import (
    FakeTts,
    default_entries,
    hit_plan,
    make_pack,
)

# 固定时刻：全部断言围绕这两个值构造，绝不吃墙钟
NOW_BEFORE = "2026-09-22T12:00:00+00:00"    # 失效前
NOW_AFTER = "2026-09-24T00:00:00+00:00"     # 失效后


def make_pack_with_expiry(pack_root, entries):
    """手搓带失效字段的资产包。

    为什么不用 `test_executor.make_pack`：它是**既有测试**的夹具，只写既有字段
    （`invalid_at` 会被丢掉），不能改它的形状。这里复用它的落盘/指纹逻辑，
    然后在已写好的 manifest 上补回失效字段 - 两条路径都不动既有断言。
    """
    stripped = [{k: v for k, v in e.items() if k != "invalid_at"} for e in entries]
    pack = make_pack(pack_root, stripped)
    manifest_path = Path(pack_root) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for asset, src in zip(manifest["assets"], entries):
        if "invalid_at" in src:
            asset["invalid_at"] = src["invalid_at"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load_pack(manifest_path.parent)


class _Tmp(unittest.TestCase):
    """提供临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="runtime_expiry_")
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.out = Path(self.tmp.name) / "out.wav"

    def tearDown(self):
        self.tmp.cleanup()

    def _execute(self, plan, **kwargs):
        return Executor(self.pack, FakeTts(), **kwargs).execute(
            plan, plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    @staticmethod
    def _with_expiry(entries, invalid_at):
        """给 entries 附加失效字段（浅拷贝，不改调用方的字典）。"""
        return [{**e, "invalid_at": invalid_at} for e in entries]

    @staticmethod
    def _unit(key):
        from core.protocol import PlanUnit
        return PlanUnit(key=key)


# ============================================================
# 1. 正例：未来失效 -> 正常播
# ============================================================
class TestFutureExpiryPlays(_Tmp):
    """invalid_at 在未来：行为与旧包完全一致。"""

    def setUp(self):
        super().setUp()
        self.tts = FakeTts()
        # 失效时刻**相对当前墙钟**构造（T18 遗留修复，2026-09-23 / docs/13 §五#22）。
        # 原先写死 "2026-09-23T00:00:00Z"——本类有两条用例用**默认 now（=墙钟）**，
        # 墙钟越过该点后必然转红，测试就成了**日历炸弹**（2026-09-23 CI 三档全红即由此）。
        # 改相对后任何日期都成立；需要精确时刻的用例仍显式注入 now=，可重放性不变。
        self.expires = datetime.now(timezone.utc) + timedelta(hours=1)
        self.pack = make_pack_with_expiry(
            self.pack_root,
            self._with_expiry(default_entries(), self.expires.isoformat()),
        )

    def test_lookup_hits_before_expiry(self):
        before = datetime.now(timezone.utc) - timedelta(minutes=1)
        entry = self.pack.lookup("greeting", 0, "normal", 0,
                                 "您好，请问需要什么帮助", now=before)
        self.assertIsNotNone(entry)
        self.assertEqual(
            entry.invalid_at, self.expires,
            "落进条目里的失效时刻必须与 manifest 写的一致（UTC）",
        )
        self.assertGreater(entry.invalid_at, datetime.now(timezone.utc),
                           "前提：本类是「失效在未来」的正例")

    def test_executor_plays_normally(self):
        """正例：invalid_at 在未来 -> Executor 正常播、零 TTS。"""
        result = self._execute(hit_plan())
        self.assertTrue(self.out.exists())
        self.assertEqual(result.hit_count, 2)
        self.assertEqual(result.miss_count, 0)
        self.assertEqual(result.fallback_count, 0)
        self.assertEqual(result.tts_calls, 0, "命中路径零 TTS")
        for event in result.events:
            self.assertEqual(event[spec.REASON], "")

    def test_executor_plays_with_explicit_now_injection(self):
        """now= 注入失效前时刻 -> 仍然正常播（可重放）。"""
        result = self._execute(hit_plan(), now=NOW_BEFORE)
        self.assertEqual(result.hit_count, 2)
        self.assertTrue(self.out.exists())

    def test_lookup_after_expiry_returns_none(self):
        """同一条目在失效后取不出来（与 Executor 判据同一来源）。"""
        # 同样相对构造：写死的"以后"迟早会变成"以前"（见 setUp 的日历炸弹说明）
        self.assertIsNone(
            self.pack.lookup("greeting", 0, "normal", 0,
                             "您好，请问需要什么帮助",
                             now=self.expires + timedelta(hours=1)))


# ============================================================
# 2. 负例：过期 -> 拒播（红线）
# ============================================================
class TestExpiredRejected(_Tmp):
    """invalid_at 早于当前：拒播、reason 明确、不写文件、不降级。"""

    def setUp(self):
        super().setUp()
        self.tts = FakeTts()
        self.pack = make_pack_with_expiry(
            self.pack_root,
            self._with_expiry(default_entries(), "2026-09-01T00:00:00Z"),
        )

    # ---- (1) lookup 返回 None ----
    def test_lookup_returns_none_for_expired(self):
        self.assertIsNone(
            self.pack.lookup("greeting", 0, "normal", 0,
                             "您好，请问需要什么帮助", now=NOW_BEFORE))

    # ---- (2) 明确 reason ----
    def test_reason_is_asset_expired(self):
        """异常消息带明确 reason = asset_expired（不是 key_not_prebaked 冒充）。"""
        with self.assertRaises(RuntimeMissError) as ctx:
            self._execute(hit_plan())
        message = str(ctx.exception)
        self.assertIn("asset_expired", message)
        self.assertIn(REASON_ASSET_EXPIRED, message)
        self.assertIn("greeting", message)
        self.assertNotIn("key_not_prebaked", message,
                         "不能是'没预铸'语义 - 那条修复动作完全不同")

    # ---- (3) 不写输出文件 ----
    def test_no_output_file_written(self):
        """拒播不得留下半截输出（与既有 abort 语义一致）。"""
        with self.assertRaises(RuntimeMissError):
            self._execute(hit_plan())
        self.assertFalse(self.out.exists(), "过期拒播后不得存在输出文件")

    def test_no_tts_called_on_reject(self):
        """拒播不得调用 TTS 合成别的话术。"""
        with self.assertRaises(RuntimeMissError):
            self._execute(hit_plan())
        self.assertEqual(self.tts.calls, [])

    # ---- (4) allow_fallback=True 也不放行 ----
    def test_allow_fallback_true_still_rejected(self):
        """红线：播过期事实比不播更糟 - 降级也不允许。"""
        with self.assertRaises(RuntimeMissError) as ctx:
            self._execute(hit_plan(), allow_fallback=True)
        message = str(ctx.exception)
        self.assertIn("asset_expired", message)
        self.assertIn("allow_fallback=True", message,
                      "消息必须如实写出 allow_fallback 的实际取值")
        self.assertFalse(self.out.exists())
        self.assertEqual(self.tts.calls, [], "过期内容不得被现场合成播出")

    def test_later_expired_unit_aborts_whole_plan(self):
        """第二个单元过期也要中止整条 plan（不能播掉前面再报错）。"""
        plan = [{"key": "greeting"}, {"key": "goodbye"}]
        with self.assertRaises(RuntimeMissError):
            self._execute(plan)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.tts.calls, [])

    def test_decision_carries_expired_reason(self):
        """判定层直接给出明确 reason 与 miss 状态。"""
        decision = Executor(self.pack, FakeTts())._decide_unit(
            self._unit("greeting"), 1, "turn-1"
        )
        self.assertEqual(decision.reason, REASON_ASSET_EXPIRED)
        self.assertEqual(decision.state, spec.MISS)
        self.assertEqual(REASON_ASSET_EXPIRED, spec.REASON_ASSET_EXPIRED)


# ============================================================
# 3. 混合包：过期单元拒播，正常单元仍能命中
# ============================================================
class TestMixedPack(_Tmp):
    """过期判定按条目生效，不影响同一包里的其他条目。"""

    def setUp(self):
        super().setUp()
        self.pack = make_pack_with_expiry(self.pack_root, [
            {"key": "expired", "text": "即将停暖，请注意安排", "rate_key": "normal",
             "variant": 0, "path": "audio/expired_v0.wav", "level": 20000, "ms": 100,
             "invalid_at": "2026-09-01T00:00:00Z"},
            {"key": "fresh", "text": "您好，请问需要什么帮助", "rate_key": "normal",
             "variant": 0, "path": "audio/fresh_v0.wav", "level": 10000, "ms": 100,
             "invalid_at": "2099-01-01T00:00:00Z"},
        ])

    def test_only_expired_unit_is_miss(self):
        executor = Executor(self.pack, FakeTts())
        expired = executor._decide_unit(self._unit("expired"), 1, "t")
        fresh = executor._decide_unit(self._unit("fresh"), 2, "t")
        self.assertEqual(expired.reason, REASON_ASSET_EXPIRED)
        self.assertEqual(expired.state, spec.MISS)
        self.assertEqual(fresh.reason, "")
        self.assertEqual(fresh.state, spec.HIT)

    def test_fresh_unit_still_plays_alone(self):
        result = self._execute([{"key": "fresh"}])
        self.assertEqual(result.hit_count, 1)
        self.assertTrue(self.out.exists())

    def test_plan_with_expired_unit_rejected_even_if_others_fine(self):
        with self.assertRaises(RuntimeMissError) as ctx:
            self._execute([{"key": "fresh"}, {"key": "expired"}])
        self.assertIn("asset_expired", str(ctx.exception))
        self.assertFalse(self.out.exists())


# ============================================================
# 4. 向后兼容 + 时间注入
# ============================================================
class TestBackwardCompatAndInjection(_Tmp):
    """旧包行为零变化；全部断言注入 now。"""

    def setUp(self):
        super().setUp()
        self.pack = make_pack(self.pack_root, default_entries())

    def test_legacy_pack_unchanged_at_arbitrary_now(self):
        """无失效字段的旧包：任意 now= 下都照常命中。"""
        for now in (NOW_BEFORE, NOW_AFTER, "2999-12-31T23:59:59Z", None):
            with self.subTest(now=now):
                entry = self.pack.lookup("greeting", 0, "normal", 0,
                                         "您好，请问需要什么帮助", now=now)
                self.assertIsNotNone(entry)

    def test_legacy_pack_executes_at_arbitrary_now(self):
        for now in (NOW_BEFORE, NOW_AFTER):
            with self.subTest(now=now):
                result = self._execute(hit_plan(), now=now)
                self.assertEqual(result.hit_count, 2)
                self.assertEqual(result.tts_calls, 0)
                self.assertTrue(self.out.exists())
                self.out.unlink()

    def test_is_expired_shared_between_lookup_and_executor(self):
        """判据只有一份：assets.is_expired 对同一时刻给出一致结论。"""
        pack = make_pack_with_expiry(
            self.pack_root,
            self._with_expiry(default_entries(), "2026-09-01T00:00:00Z"),
        )

        self.assertTrue(is_expired(pack.assets[0], NOW_BEFORE))
        self.assertIsNone(
            pack.lookup("greeting", 0, "normal", 0,
                        "您好，请问需要什么帮助", now=NOW_BEFORE))
        decision = Executor(pack, FakeTts())._decide_unit(
            self._unit("greeting"), 1, "t")
        self.assertEqual(decision.reason, REASON_ASSET_EXPIRED)

    def test_expiry_is_now_dependent_not_wall_clock(self):
        """注入时刻决定结论 - 反空转：不用 sleep 也能翻转结果。"""
        pack = make_pack_with_expiry(
            self.pack_root,
            self._with_expiry(default_entries(), "2026-09-22T13:00:00Z"),
        )

        before = Executor(pack, FakeTts(), now=NOW_BEFORE)._decide_unit(
            self._unit("greeting"), 1, "t")
        after = Executor(pack, FakeTts(), now="2026-09-22T14:00:00+00:00")._decide_unit(
            self._unit("greeting"), 1, "t")
        self.assertEqual(before.reason, "")
        self.assertEqual(before.state, spec.HIT)
        self.assertEqual(after.reason, REASON_ASSET_EXPIRED)
        self.assertEqual(after.state, spec.MISS)

    def test_boundary_exact_expiry_is_expired(self):
        """now == invalid_at -> 已过期（与 assets 层语义一致）。"""
        pack = make_pack_with_expiry(
            self.pack_root,
            self._with_expiry(default_entries(), "2026-09-22T13:00:00Z"),
        )
        exact = "2026-09-22T13:00:00+00:00"
        self.assertTrue(is_expired(pack.assets[0], exact))
        self.assertIsNone(
            pack.lookup("greeting", 0, "normal", 0,
                        "您好，请问需要什么帮助", now=exact))


# ============================================================
# 5. metrics_spec 常量一致性
# ============================================================
class TestReasonConstantPlacement(unittest.TestCase):
    """REASON_ASSET_EXPIRED 的真源在 core.metrics_spec，runtime 只转发。"""

    def test_value_is_asset_expired(self):
        self.assertEqual(spec.REASON_ASSET_EXPIRED, "asset_expired")
        self.assertEqual(REASON_ASSET_EXPIRED, spec.REASON_ASSET_EXPIRED)

    def test_not_added_to_metric_fields(self):
        """reason 的取值不属于 eval 报告字段名集合（该集合被既有测试钉死）。"""
        self.assertNotIn(REASON_ASSET_EXPIRED, spec.METRIC_FIELDS)
        self.assertNotIn(REASON_ASSET_EXPIRED, spec.DUPLEX_FIELDS)
        self.assertEqual(len(spec.METRIC_FIELDS), 15)
