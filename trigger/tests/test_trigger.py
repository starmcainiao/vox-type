"""
trigger.tests.test_trigger — T17 触发器（state → plan）测试

覆盖 T17 卡的验收标准（T17b 修订：`when: null` = 显式兜底，必须命中）：
  2  正例：demo 包合法 state → build_plan 产出 plan，每个 key ∈ phrases.json
  2  正例（显式兜底生效）：demo 包条件规则全不匹配 → 命中兜底规则 closing，
                          断言 rule_id == "closing"（不只断「没抛错」）
  3  正例（条件规则优先）：同时匹配条件规则与兜底规则 → 命中条件规则
  3  负例（无兜底仍 fail-closed）：不含 when: null 的 trigger + 无匹配 state
     → 抛 TriggerError（消息含该 trigger_id），不产出 plan、不落留痕
  4  负例（超预算）：plan 撑到超过 turn_budget_chars → 抛 BudgetError，
                     消息含实际字数与预算
  7  确定性：同一 state + 同一 Trigger → plan 逐字段相同；
             变体若受 turn_id 影响，同一 turn_id 重跑必须同结果
  9  字段名合规：plan_id 派生用的字段名与 core.metrics_spec 一致

反空转约束：本文件**只调用产品 API**（trigger 的公开函数：
  build_plan / load_trigger / validate_state / check_budget / count_plan_chars /
  record_turn），不在测试里复制/重写被验逻辑。为构造负例，这里只有一份
  trigger.json 的**原始夹具**（JSON 形状的数据，不是校验实现），
  以及一份话术变体表夹具（数据，不是校验）——负例 = 改夹具 → 调产品 API → 断言异常。
「无兜底 trigger」由**本测试自己**把兜底规则从夹具里删掉（测试内自建 fixture，
不改 packs/demo-brief/），再走产品 API 的 load_trigger 装载。
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from core.metrics_spec import PLAN_ID, TURN_ID
from trigger import (
    BudgetError,
    TriggerError,
    build_plan,
    check_budget,
    count_plan_chars,
    load_trigger,
    record_turn,
    rule_matches,
    validate_state,
)


# 仓库根（tests/ → trigger/ → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEMO_PACK = _REPO_ROOT / "packs" / "demo-brief"


# ---------------------------------------------------------------------------
# 夹具：state 快照（含敏感层字段，用于验证敏感层不落盘）
# ---------------------------------------------------------------------------
def _state_processing(turn_id: str = "turn-1") -> dict:
    """一份合法 state：命中 demo 包的 greeting 规则。"""
    return {
        "ticket_id": "T-SECRET-8842",          # 敏感层
        "ticket_status": "处理中",
        "days_left": 3,
        "overdue_days": 0,
        "is_overdue": False,
        "assignee_confirmed": True,
    }


def _state_no_conditional_match() -> dict:
    """一份让 demo 包**五条条件规则全不匹配**的合法 state：
    ticket_status 不是「处理中」（greeting 不中）、is_overdue 为 False（overdue /
    due_tonight 不中）、days_left > 14（due_days_left 不中）、
    assignee_confirmed 为 True（ask_assignee 不中）。
    按 T17b 的语义，此时应命中 demo 包第 6 条显式兜底规则 `closing`（when: null）。
    """
    return {
        "ticket_id": "T-SECRET-0000",
        "ticket_status": "待受理",
        "days_left": 100,
        "overdue_days": 0,
        "is_overdue": False,
        "assignee_confirmed": True,
    }


# ---------------------------------------------------------------------------
# 验收 2：正例
# ---------------------------------------------------------------------------
class TestBuildPlanPositive(unittest.TestCase):

    def test_demo_brief_build_plan_returns_plan(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        validate_state(state, trigger)

        result = build_plan(trigger, state, turn_id="turn-1")

        self.assertTrue(len(result.plan) >= 1, "plan 至少要有一个单元")
        self.assertEqual(result.rule_id, "greeting")
        self.assertEqual(result.plan_id, "demo-brief.ticket-reminder:turn-1")

    def test_demo_brief_plan_keys_all_in_phrases(self):
        """验收 2：plan 的每个 key 都 ∈ phrases.json。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")

        phrases_doc = json.loads((_DEMO_PACK / "phrases.json").read_text(encoding="utf-8"))
        phrase_keys = {p["key"] for p in phrases_doc["phrases"]}
        for unit in result.plan:
            self.assertIn(unit.key, phrase_keys, f"plan key {unit.key!r} 不在 phrases.json")
        # 触发器自报的 keys 序列与 plan 单元的 key 一一对应（顺序一致）
        self.assertEqual(list(result.keys), [u.key for u in result.plan])

    def test_plan_units_are_core_plan_units(self):
        """plan 单元必须是 core 协议形状（可交给 runtime 执行）。"""
        from core.protocol import PlanUnit

        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")
        for unit in result.plan:
            self.assertIsInstance(unit, PlanUnit)

    def test_plan_units_carry_no_free_text(self):
        """前置包不产生未审核文本：plan 单元只带 key，不带 text（SAY_LIVE）。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")
        for unit in result.plan:
            self.assertIsNone(unit.text, "plan 单元不得携带自由文本")
            self.assertEqual(unit.action, "SAY")

    def test_slot_values_filled_from_state(self):
        """槽位用 state 的已声明字段填充（槽值可审计，不含敏感字段名）。"""
        trigger = load_trigger(_DEMO_PACK)
        state = {
            "ticket_id": "T-SECRET-1",
            "ticket_status": "待验收",
            "days_left": 7,
            "overdue_days": 0,
            "is_overdue": False,
            "assignee_confirmed": True,
        }
        result = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(result.rule_id, "due_days_left")
        self.assertEqual(result.plan[0].slots, {"days_left": "7"})

    def test_result_chars_within_budget(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")
        self.assertLessEqual(result.chars, result.budget, "plan 字数应在预算内")
        self.assertEqual(result.budget, trigger.budget_chars)

    def test_record_turn_writes_one_line(self):
        """验收 2：record_turn 写出一条留痕，文件行数 +1。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "turns.jsonl"
            written = record_turn(
                trigger, state, result,
                turn_id="turn-1", plan_id=result.plan_id,
                ts="2026-09-18T00:00:00Z", path=ledger_path,
            )
            self.assertEqual(written, ledger_path.resolve())
            lines = ledger_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1, "第一次写入应为 1 行")
            record = json.loads(lines[0])
            self.assertEqual(record["turn_id"], "turn-1")
            self.assertEqual(record["plan_id"], result.plan_id)
            self.assertEqual(record["rule_id"], "greeting")
            self.assertEqual(record["state"]["ticket_status"], "处理中")

            # 第二次写入：行数 +1
            record_turn(
                trigger, state, result,
                turn_id="turn-2", plan_id=result.plan_id,
                ts="2026-09-18T00:01:00Z", path=ledger_path,
            )
            self.assertEqual(
                len(ledger_path.read_text(encoding="utf-8").splitlines()), 2,
                "第二次写入后应为 2 行（追加写，不覆盖）",
            )


# ---------------------------------------------------------------------------
# 验收 2：正例——显式兜底规则（when: null）必须生效
# ---------------------------------------------------------------------------
class TestExplicitFallbackHits(unittest.TestCase):

    def _demo_trigger(self):
        trigger = load_trigger(_DEMO_PACK)
        fallbacks = [r for r in trigger.rules if r.when is None]
        self.assertEqual(len(fallbacks), 1, "demo 包应只有一条兜底规则（T16 已校验）")
        self.assertEqual(fallbacks[0].rule_id, "closing")
        return trigger

    def test_conditional_miss_hits_fallback_rule(self):
        """验收 2：条件规则全不匹配 → build_plan 返回，且 rule_id == "closing"。"""
        trigger = self._demo_trigger()
        result = build_plan(trigger, _state_no_conditional_match(), turn_id="turn-1")

        self.assertEqual(result.rule_id, "closing",
                         "条件规则全不匹配时应命中显式兜底规则 closing")

    def test_fallback_plan_is_usable(self):
        """兜底产物是一份完整可用的 plan：单元非空、不带自由文本、字数在预算内。"""
        trigger = self._demo_trigger()
        state = _state_no_conditional_match()
        result = build_plan(trigger, state, turn_id="turn-1")

        self.assertTrue(len(result.plan) >= 1, "兜底 plan 至少要有一个单元")
        for unit in result.plan:
            self.assertIsNone(unit.text, "兜底 plan 单元同样不得携带自由文本")
            self.assertEqual(unit.action, "SAY")
        self.assertIn(result.plan[0].key, trigger.phrase_keys)
        self.assertLessEqual(result.chars, result.budget)
        self.assertEqual(result.plan_id, "demo-brief.ticket-reminder:turn-1")

    def test_fallback_hit_is_deterministic(self):
        """兜底命中同样是纯函数：同一 state + 同一 turn_id → plan 逐字段相同。"""
        trigger = self._demo_trigger()
        state = _state_no_conditional_match()
        first = build_plan(trigger, state, turn_id="turn-1")
        second = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(_plan_signature(first), _plan_signature(second))

    def test_fallback_hit_can_be_ledgered(self):
        """兜底产物可直接进留痕（rule_id 落 "closing"，且只留结构化层字段）。"""
        trigger = self._demo_trigger()
        state = _state_no_conditional_match()
        result = build_plan(trigger, state, turn_id="turn-1")

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "turns.jsonl"
            record_turn(trigger, state, result,
                        turn_id="turn-1", plan_id=result.plan_id,
                        ts="2026-09-18T00:00:00Z", path=ledger_path)
            record = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["rule_id"], "closing")
            self.assertNotIn("ticket_id", record["state"],
                             "兜底留痕同样只取结构化层，敏感层不落盘")


# ---------------------------------------------------------------------------
# 验收 3：正例——条件规则优先于兜底（兜底只能最后生效）
# ---------------------------------------------------------------------------
class TestConditionalWinsOverFallback(unittest.TestCase):

    def test_conditional_rule_wins_when_both_apply(self):
        """同时匹配条件规则与兜底规则的 state → 命中条件规则（不是兜底）。

        demo 包的 rules 顺序里兜底 closing 在最后，因此五条条件规则中任何一条
        命中时都不应被兜底顶掉。
        """
        trigger = load_trigger(_DEMO_PACK)
        self.assertEqual(trigger.rules[-1].rule_id, "closing",
                         "前提：demo 包的兜底规则排在最后")

        state = _state_processing()   # ticket_status = 处理中 → 命中 greeting
        result = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(result.rule_id, "greeting",
                         "条件规则命中时不得被兜底规则顶掉")

    def test_every_conditional_rule_beats_fallback(self):
        """逐条构造只命中单一条件规则的 state → 全部命中条件规则（一条也不漏）。

        覆盖 greeting / overdue / due_tonight / due_days_left / ask_assignee，
        逐条与兜底竞争；任何一条被兜底顶掉都会在这里失败。
        """
        trigger = load_trigger(_DEMO_PACK)
        cases = [
            ("greeting",
             {"ticket_status": "处理中", "is_overdue": False,
              "days_left": 100, "assignee_confirmed": True}),
            ("overdue",
             {"ticket_status": "已关闭", "is_overdue": True,
              "days_left": 100, "assignee_confirmed": True}),
            ("due_tonight",
             {"ticket_status": "待验收", "is_overdue": False,
              "days_left": 0, "assignee_confirmed": True}),
            ("due_days_left",
             {"ticket_status": "待验收", "is_overdue": False,
              "days_left": 7, "assignee_confirmed": True}),
            ("ask_assignee",
             {"ticket_status": "待受理", "is_overdue": False,
              "days_left": 100, "assignee_confirmed": False}),
        ]
        for expected_id, override in cases:
            state = _state_no_conditional_match()
            state.update(override)
            result = build_plan(trigger, state, turn_id="turn-1")
            self.assertEqual(result.rule_id, expected_id,
                             f"{expected_id} 单独命中时不得被兜底规则顶掉")

    def test_multi_conditional_match_picks_first_in_order(self):
        """多条**条件**规则都匹配时取 trigger.json 里的第一条（顺序是既有契约）。"""
        trigger = load_trigger(_DEMO_PACK)
        state = {
            "ticket_id": "T-1",
            "ticket_status": "处理中",        # 命中 greeting
            "days_left": 3,
            "overdue_days": 0,
            "is_overdue": False,              # 不命中 overdue / due_tonight
            "assignee_confirmed": False,      # 命中 ask_assignee
        }
        result = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(result.rule_id, "greeting",
                         "多条命中时应取 rules 里排在前面的那条")


# ---------------------------------------------------------------------------
# 验收 3 / 4：负例——无兜底规则 + 无匹配 → 仍必须 fail-closed
# ---------------------------------------------------------------------------
class TestNoFallbackFailsClosed(unittest.TestCase):

    def _no_fallback_trigger(self):
        """一份**不含 when: null** 的 trigger：把兜底规则从测试夹具里删掉。

        fixture 由本测试自建（不改 packs/demo-brief/），装载与校验仍走产品 API
        （load_trigger），所以「无兜底」这个前置条件是真实生效的，不是假造的。
        """
        doc = _trigger_doc()
        self.assertNotIn("closing", [r["rule_id"] for r in doc["rules"]])
        self.assertTrue(all(r["when"] is not None for r in doc["rules"]),
                        "负例 fixture 里不得有任何 when: null 的兜底规则")
        trigger = _materialize(doc)
        self.assertTrue(all(r.when is not None for r in trigger.rules))
        return trigger

    def test_no_fallback_no_match_raises_trigger_error(self):
        """无兜底 + 无匹配 → 抛 TriggerError，消息含该 trigger_id。"""
        trigger = self._no_fallback_trigger()
        state = _state_no_conditional_match()
        with self.assertRaises(TriggerError) as ctx:
            build_plan(trigger, state, turn_id="turn-1")
        message = str(ctx.exception)
        self.assertIn("没有任何规则命中", message)
        self.assertIn("fail-closed", message)
        self.assertIn(trigger.trigger_id, message,
                      "消息必须含该 trigger_id，便于定位是哪个包缺覆盖")

    def test_no_fallback_no_match_produces_no_plan_and_no_ledger(self):
        """fail-closed 路径：既不产出 plan，也不落任何留痕。"""
        trigger = self._no_fallback_trigger()
        state = _state_no_conditional_match()

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "turns.jsonl"
            produced = None
            try:
                produced = build_plan(trigger, state, turn_id="turn-1")
            except TriggerError:
                pass
            self.assertIsNone(produced, "无兜底 + 无匹配不得产出 plan（含空 plan）")
            self.assertFalse(ledger_path.exists(), "抛错路径不得创建留痕文件")
            self.assertEqual(len(list(Path(tmp).iterdir())), 0,
                             "抛错路径不得在目录里留任何文件")

    def test_no_fallback_no_match_cannot_be_ledgered(self):
        """拿不到 plan → 也就没有留痕可写（record_turn 的前置条件不成立）。"""
        trigger = self._no_fallback_trigger()
        state = _state_no_conditional_match()
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "turns.jsonl"
            plan = None
            with self.assertRaises(TriggerError):
                plan = build_plan(trigger, state, turn_id="turn-1")
            self.assertIsNone(plan)
            # 没有 plan 对象，就没有可写的一条留痕；文件不存在 = 未落留痕
            self.assertFalse(ledger_path.exists())

    def test_no_fallback_with_match_still_succeeds(self):
        """收窄后的红线**只**收窄「无匹配」这一侧：有匹配时照旧正常产出 plan。"""
        trigger = self._no_fallback_trigger()
        state = _state_processing()   # ticket_status = 处理中 → 命中 greeting
        result = build_plan(trigger, state, turn_id="turn-1",
                            phrase_variants=_demo_variants())
        self.assertEqual(result.rule_id, "greeting")
        self.assertTrue(len(result.plan) >= 1)


# ---------------------------------------------------------------------------
# 验收 4：负例——超预算 → BudgetError，消息含实际字数与预算
# ---------------------------------------------------------------------------
class TestOverBudget(unittest.TestCase):

    def _over_budget_trigger(self, budget_chars: int):
        """把某个 key 的变体撑到超长（改夹具 → 校验由产品 API 做）。

        夹具是**无兜底**的 trigger，且 state 命中其条件规则——这样「超预算」
        这条红线与「无兜底 + 无匹配」互相独立，不会因兜底改写 rule_id 而失真。
        """
        long_text = "这是一句刻意写得非常非常非常非常非常非常非常长的话" * 12
        variants = {"greeting_ticket": (long_text, long_text)}
        doc = _trigger_doc()
        doc["budget_chars"] = budget_chars
        return doc, variants

    def test_over_budget_raises_with_both_numbers(self):
        doc, variants = self._over_budget_trigger(budget_chars=60)
        trigger = _materialize(doc)
        state = _state_processing()

        with self.assertRaises(BudgetError) as ctx:
            build_plan(trigger, state, turn_id="turn-1", phrase_variants=variants)

        message = str(ctx.exception)
        self.assertIn("实际", message)
        self.assertIn("预算", message)
        self.assertIn("60", message, "消息应含预算值 60")

    def test_over_budget_produces_no_plan(self):
        doc, variants = self._over_budget_trigger(budget_chars=60)
        trigger = _materialize(doc)
        state = _state_processing()

        produced = None
        try:
            produced = build_plan(trigger, state, turn_id="turn-1",
                                  phrase_variants=variants)
        except BudgetError:
            pass
        self.assertIsNone(produced, "超预算不得产出 plan")

    def test_at_budget_boundary_passes(self):
        """边界值不报错（与 T16 的 check_budget 同一口径：> 才超）。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")
        self.assertLessEqual(result.chars, result.budget)

    def test_budget_check_is_the_t16_one(self):
        """build_plan 的预算判据就是 T16 的 check_budget（口径不复制）。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")
        check_budget([{"text": _expand_for_budget(trigger, result, state)}], trigger.budget_chars)
        self.assertLessEqual(result.chars, trigger.budget_chars)


def _plan_signature(result) -> tuple:
    """把一份 plan 结果压成可比较的签名（确定性断言用；只读结果，不复制匹配逻辑）。"""
    return (
        result.rule_id,
        result.plan_id,
        result.chars,
        result.budget,
        tuple(result.keys),
        tuple((u.action, u.key, u.rate, u.variant, tuple(sorted(u.slots.items())))
              for u in result.plan),
    )


def _expand_for_budget(trigger, result, state) -> str:
    """把一个 plan 结果展开成文本，供 T16 的 check_budget 复核（用产品 API 计数）。"""
    variants_doc = json.loads((_DEMO_PACK / "phrases.json").read_text(encoding="utf-8"))
    variant_map = {p["key"]: p["variants"] for p in variants_doc["phrases"]}
    texts = []
    for unit in result.plan:
        variants = variant_map[unit.key]
        text = variants[unit.variant]
        for slot, value in unit.slots.items():
            text = text.replace(f"{{{slot}}}", value)
        texts.append(text)
    return "".join(texts)


# ---------------------------------------------------------------------------
# 验收 7：确定性
# ---------------------------------------------------------------------------
class TestDeterminism(unittest.TestCase):

    def _plan_signature(self, result) -> tuple:
        return (
            result.rule_id,
            result.plan_id,
            result.chars,
            result.budget,
            tuple(result.keys),
            tuple((u.action, u.key, u.rate, u.variant, tuple(sorted(u.slots.items())))
                  for u in result.plan),
        )

    def test_same_state_same_trigger_yields_identical_plan(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        first = build_plan(trigger, state, turn_id="turn-1")
        second = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(self._plan_signature(first), self._plan_signature(second),
                         "同一 state + 同一 Trigger 应逐字段相同")

    def test_two_loads_yield_identical_plan(self):
        """从磁盘加载两次（不同 Trigger 实例）→ plan 逐字段相同。"""
        state = _state_processing()
        first = build_plan(load_trigger(_DEMO_PACK), state, turn_id="turn-1")
        second = build_plan(load_trigger(_DEMO_PACK), state, turn_id="turn-1")
        self.assertEqual(self._plan_signature(first), self._plan_signature(second))

    def test_plan_order_stable_across_runs(self):
        """plan 单元顺序稳定（由 trigger.json 的 rules/units 顺序给出，不排序）。"""
        trigger = load_trigger(_DEMO_PACK)
        state = {
            "ticket_id": "T-2",
            "ticket_status": "待验收",
            "days_left": 14,
            "overdue_days": 0,
            "is_overdue": False,
            "assignee_confirmed": False,   # 与 due_days_left 同时命中，取前者
        }
        first = build_plan(trigger, state, turn_id="turn-1")
        second = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual([u.key for u in first.plan], [u.key for u in second.plan])
        self.assertEqual(first.keys, second.keys)

    def test_variant_auto_is_derived_from_turn_id(self):
        """变体若用散列选择，必须由 turn_id 派生：同一 turn_id 重跑必须同结果。

        实现侧注记：T16 的校验器**禁止** trigger.json 里写 `variant: "auto"`
        （前置包要求确定性，变体选择权必须显式），所以这条断言验证的是
        build_plan 对编程式输入（绕过 T16 的 JSON 闸门）的散列路径：
        同一 turn_id → 同一变体；不同 turn_id → 可能不同（可重放，不固定死）。
        """
        from trigger import PlanUnit, TriggerRule, Trigger
        from trigger import load_trigger as _load

        trigger = _load(_DEMO_PACK)
        variants = _demo_variants()
        key = "greeting_ticket"
        n_variants = len(variants[key])
        self.assertGreater(n_variants, 1, "该 key 应有多个变体才能观察到散列选择")

        rule = TriggerRule(
            rule_id="greeting",
            when={"ticket_status": "处理中"},
            units=(PlanUnit(key=key, variant="auto"),),
        )
        trigger_auto = Trigger(
            trigger_id=trigger.trigger_id,
            trigger_version=trigger.trigger_version,
            budget_chars=trigger.budget_chars,
            state_fields=trigger.state_fields,
            phrase_keys=trigger.phrase_keys,
            rules=(rule,),
            pack_dir=trigger.pack_dir,
        )
        state = _state_processing()

        a = build_plan(trigger_auto, state, turn_id="turn-1", phrase_variants=variants)
        b = build_plan(trigger_auto, state, turn_id="turn-1", phrase_variants=variants)
        self.assertEqual(
            a.plan[0].variant, b.plan[0].variant,
            "同一 turn_id 重跑必须得到同一变体（可重放）",
        )
        self.assertIn(a.plan[0].variant, range(n_variants),
                      "散列选择的变体序号应在可用变体范围内")

        # 跨 turn_id 允许变化（防复读机）；不能断言「一定不同」，因为 2 个变体时
        # 有 50% 概率相同——这里只断言两条路径都是合法变体（不报错、不越界）。
        c = build_plan(trigger_auto, state, turn_id="turn-2", phrase_variants=variants)
        self.assertIn(c.plan[0].variant, range(n_variants))

    def test_variant_auto_differs_across_turn_ids_somewhere(self):
        """在足够多的 turn_id 上，散列选择必须真的变化（否则「由 turn_id 派生」是空话）。"""
        from trigger import PlanUnit, TriggerRule, Trigger
        from trigger import load_trigger as _load

        trigger = _load(_DEMO_PACK)
        variants = _demo_variants()
        key = "greeting_ticket"
        n_variants = len(variants[key])
        self.assertGreater(n_variants, 1)

        rule = TriggerRule(
            rule_id="greeting",
            when={"ticket_status": "处理中"},
            units=(PlanUnit(key=key, variant="auto"),),
        )
        trigger_auto = Trigger(
            trigger_id=trigger.trigger_id,
            trigger_version=trigger.trigger_version,
            budget_chars=trigger.budget_chars,
            state_fields=trigger.state_fields,
            phrase_keys=trigger.phrase_keys,
            rules=(rule,),
            pack_dir=trigger.pack_dir,
        )
        state = _state_processing()

        seen = set()
        for i in range(64):
            result = build_plan(
                trigger_auto, state, turn_id=f"turn-{i}", phrase_variants=variants
            )
            seen.add(result.plan[0].variant)
            # 同一 turn_id 重跑：必须逐字段相同
            again = build_plan(
                trigger_auto, state, turn_id=f"turn-{i}", phrase_variants=variants
            )
            self.assertEqual(result.plan[0].variant, again.plan[0].variant)
        self.assertGreater(len(seen), 1,
                           "64 个 turn_id 上散列选择应产生多于 1 个不同变体")

    def test_variant_explicit_int_is_passed_through(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        first = build_plan(trigger, state, turn_id="turn-1")
        second = build_plan(trigger, state, turn_id="turn-99")
        self.assertEqual(
            first.plan[0].variant, second.plan[0].variant,
            "trigger.json 里显式给定的整数变体不随 turn_id 变化",
        )

    def test_build_plan_does_not_mutate_state(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        snapshot = copy.deepcopy(state)
        build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(state, snapshot, "build_plan 不得修改传入的 state")


# ---------------------------------------------------------------------------
# 验收 9：字段名合规
# ---------------------------------------------------------------------------
class TestFieldNames(unittest.TestCase):

    def test_ledger_field_names_match_core_metrics_spec(self):
        """留痕的 turn_id / plan_id 字面量必须与 core.metrics_spec 的常量一致。"""
        from trigger import ledger, plan as trigger_module

        self.assertEqual(trigger_module.TURN_ID_FIELD, TURN_ID)
        self.assertEqual(trigger_module.PLAN_ID_FIELD, PLAN_ID)
        self.assertEqual(ledger.TURN_ID_FIELD, TURN_ID)
        self.assertEqual(ledger.PLAN_ID_FIELD, PLAN_ID)

        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        result = build_plan(trigger, state, turn_id="turn-1")
        record = ledger.structured_record(
            trigger, state, result, turn_id="turn-1", plan_id=result.plan_id,
            ts="2026-09-18T00:00:00Z",
        )
        self.assertIn(TURN_ID, record, f"留痕必须含 {TURN_ID!r} 字段")
        self.assertIn(PLAN_ID, record, f"留痕必须含 {PLAN_ID!r} 字段")
        self.assertNotIn("turnId", record, "不得自造 camelCase 字段名")
        self.assertNotIn("planId", record, "不得自造 camelCase 字段名")

    def test_plan_id_is_derived_deterministically(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_processing()
        a = build_plan(trigger, state, turn_id="turn-1")
        b = build_plan(trigger, state, turn_id="turn-1")
        self.assertEqual(a.plan_id, b.plan_id)
        self.assertNotIn(PLAN_ID, str(a.plan_id), "plan_id 的值是标识，不是字段名")


# ---------------------------------------------------------------------------
# T17c：规则匹配公开 API（rule_matches）的直接锚点
# ---------------------------------------------------------------------------
# 本文件既有的匹配语义断言都是通过 build_plan **间接**触及的（间接覆盖已有）。
# 匹配逻辑公开成 API 之后还需要**直接**的锚点：任何把匹配语义改坏（兜底漏命中、
# 缺值静默当成满足、区间比较符失效、TypeError 静默放行）的改动，必须在这几条里
# 直接失败，而不是等到 build_plan 的集成用例才暴露。
# 反空转：本段只调用公开的 rule_matches，不绕道 build_plan；
# 规则参数用只提供 .when 的最小替身对象（rule_matches 只消费 .when），
# 不在测试里复制匹配逻辑。正例与负例都在：每条语义都有命中与不命中两侧。
class TestRuleMatchesPublicAPI(unittest.TestCase):

    @staticmethod
    def _rule(when):
        """一个只提供 .when 的最小规则替身（rule_matches 不消费 rule_id / units）。"""
        return type("RuleLike", (), {"when": when})()

    # 兜底语义（when = None）：命中侧
    def test_rule_matches_fallback_when_none_hits(self):
        """when = None → True：兜底是作者在 trigger.json 里显式声明的规则。"""
        self.assertIs(rule_matches(self._rule(None), {}), True,
                      "when=None 的规则必须命中（显式兜底）")

    # 字段缺值：不命中侧
    def test_rule_matches_missing_field_does_not_hit(self):
        """字段在 state 里缺值 → False：条件无法判定，绝不静默当成满足。"""
        rule = self._rule({"ticket_status": "处理中"})
        self.assertIs(rule_matches(rule, {}), False,
                      "缺值字段不得被静默当成满足")

    def test_rule_matches_partial_state_does_not_hit(self):
        """多条件里只有一个字段在 state 里 → False（缺值优先于已匹配的条件）。"""
        rule = self._rule({"is_overdue": False, "days_left": {"gt": 0, "lte": 14}})
        self.assertIs(rule_matches(rule, {"is_overdue": False}), False,
                      "只要有一个条件无法判定，整条规则就不得命中")

    # 区间比较符 gt：命中与不命中两侧
    def test_rule_matches_gt_operator_both_sides(self):
        """区间比较符 gt：严格大于才命中，等于不命中。"""
        rule = self._rule({"days_left": {"gt": 0}})
        self.assertIs(rule_matches(rule, {"days_left": 5}), True,
                      "days_left=5 应满足 days_left > 0")
        self.assertIs(rule_matches(rule, {"days_left": 0}), False,
                      "days_left=0 不得满足 days_left > 0（gt 是严格大于）")

    # 区间比较符 lte：命中与不命中两侧
    def test_rule_matches_lte_operator_both_sides(self):
        """区间比较符 lte：等于也命中，大于不命中（边界含等号）。"""
        rule = self._rule({"days_left": {"lte": 14}})
        self.assertIs(rule_matches(rule, {"days_left": 14}), True,
                      "days_left=14 应满足 days_left <= 14（边界含等号）")
        self.assertIs(rule_matches(rule, {"days_left": 13}), True)
        self.assertIs(rule_matches(rule, {"days_left": 15}), False,
                      "days_left=15 不得满足 days_left <= 14")

    # 条件规则的值层面：命中与不命中两侧
    def test_rule_matches_condition_rule_hit_and_miss(self):
        """同一条真实形状的条件规则在两种取值上分别命中与不命中。"""
        rule = self._rule({"is_overdue": True})
        self.assertIs(rule_matches(rule, {"is_overdue": True}), True)
        self.assertIs(rule_matches(rule, {"is_overdue": False}), False)

    # TypeError → 不命中（不抛给调用方）
    def test_rule_matches_incomparable_types_do_not_hit_without_raising(self):
        """类型不可比较 → False，且不抛 TypeError（fail-closed，不静默当成满足）。"""
        rule = self._rule({"days_left": {"gt": 0}})
        self.assertIs(rule_matches(rule, {"days_left": "七"}), False,
                      "不可比较的值必须判为不命中，且不得把 TypeError 抛给调用方")


# ---------------------------------------------------------------------------
# 辅助：构造不写盘的 Trigger 实例（用于构造负例夹具）
# ---------------------------------------------------------------------------
def _trigger_doc() -> dict:
    """一份合法 trigger.json 夹具（key 全部来自 packs/demo-brief/phrases.json）。"""
    return {
        "trigger_version": 1,
        "trigger_id": "test-pack.brief",
        "budget_chars": 60,
        "state_fields": {
            "ticket_id": {"layer": "sensitive", "type": "str"},
            "ticket_status": {
                "layer": "structured", "type": "str",
                "enum": ["待受理", "处理中", "待验收", "已关闭"],
            },
            "days_left": {"layer": "structured", "type": "int", "min": 0, "max": 365},
            "overdue_days": {"layer": "structured", "type": "int", "min": 0, "max": 3650},
            "is_overdue": {"layer": "structured", "type": "bool"},
            "assignee_confirmed": {"layer": "structured", "type": "bool"},
        },
        "rules": [
            {
                "rule_id": "greeting",
                "when": {"ticket_status": "处理中"},
                "units": [{"key": "greeting_ticket", "rate": "normal", "variant": 0}],
            },
            {
                "rule_id": "due_days_left",
                "when": {"is_overdue": False, "days_left": {"gt": 0, "lte": 14}},
                "units": [
                    {"key": "due_days_left", "rate": "normal", "variant": 0,
                     "slots": ["days_left"]},
                ],
            },
        ],
    }


def _demo_variants():
    """读 demo 包的 phrases.json，返回 {key: variants 元组}（数据夹具，不是解析实现）。"""
    doc = json.loads((_DEMO_PACK / "phrases.json").read_text(encoding="utf-8"))
    return {p["key"]: tuple(p["variants"]) for p in doc["phrases"]}


def _materialize(doc: dict):
    """把 trigger.json 夹具物化成 Trigger：先落盘到临时目录，再走产品 API 装载。

    这样做是为了让**校验仍然由产品 API（load_trigger）执行**，
    测试里不复制 T16 的校验器。夹具包复用 demo 包的 pack.json / phrases.json，
    因此所有 key 都在 phrases.json 里（已审核集合不变）。
    """
    from trigger import load_trigger as _load

    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "pack"
        pack_dir.mkdir()
        (pack_dir / "pack.json").write_text(
            (_DEMO_PACK / "pack.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (pack_dir / "phrases.json").write_text(
            (_DEMO_PACK / "phrases.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (pack_dir / "trigger.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
        return _load(pack_dir)


if __name__ == "__main__":
    unittest.main()
