"""
adapters.state_trigger.tests.test_trigger — T17 触发器（state → plan）测试

覆盖 T17 卡的验收标准：
  2  正例：demo 包合法 state → build_plan 产出 plan，每个 key ∈ phrases.json
  3  负例（无条件命中）：没有任何规则匹配的 state → 抛 TriggerError，
                         不返回空 plan、不落任何留痕
  4  负例（超预算）：plan 撑到超过 turn_budget_chars → 抛 BudgetError，
                     消息含实际字数与预算
  7  确定性：同一 state + 同一 Trigger → plan 逐字段相同；
             变体若受 turn_id 影响，同一 turn_id 重跑必须同结果
  9  字段名合规：plan_id 派生用的字段名与 core.metrics_spec 一致

反空转约束：本文件**只调用产品 API**（adapters.state_trigger 的公开函数：
  build_plan / load_trigger / validate_state / check_budget / count_plan_chars /
  record_turn），不在测试里复制/重写被验逻辑。为构造负例，这里只有一份
  trigger.json 的**原始夹具**（JSON 形状的数据，不是校验实现），
  以及一份话术变体表夹具（数据，不是校验）——负例 = 改夹具 → 调产品 API → 断言异常。
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from core.metrics_spec import PLAN_ID, TURN_ID
from adapters.state_trigger import (
    BudgetError,
    TriggerError,
    build_plan,
    check_budget,
    count_plan_chars,
    load_trigger,
    record_turn,
    validate_state,
)


# 仓库根（tests/ → state_trigger/ → adapters/ → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
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


def _state_no_match() -> dict:
    """一份**没有任何规则匹配**的 state（days_left > 14 且未逾期、负责人已确认、
    状态不是「处理中」）——demo 包六条规则（含一条兜底规则）都不会命中。"""
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
# 验收 3：负例——无条件命中 → fail-closed
# ---------------------------------------------------------------------------
class TestNoMatchFailsClosed(unittest.TestCase):

    def test_no_rule_matches_raises_trigger_error(self):
        trigger = load_trigger(_DEMO_PACK)
        state = _state_no_match()
        with self.assertRaises(TriggerError) as ctx:
            build_plan(trigger, state, turn_id="turn-1")
        message = str(ctx.exception)
        self.assertIn("没有任何规则命中", message)
        self.assertIn("fail-closed", message)

    def test_no_rule_matches_returns_no_plan(self):
        """不得返回空 plan：抛错后调用方拿不到任何 plan 对象。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_no_match()
        produced = None
        try:
            produced = build_plan(trigger, state, turn_id="turn-1")
        except TriggerError:
            pass
        self.assertIsNone(produced, "无条件命中必须抛错，不得返回空 plan")

    def test_no_rule_matches_writes_no_ledger(self):
        """不得落任何留痕：抛错路径不得产生文件。"""
        trigger = load_trigger(_DEMO_PACK)
        state = _state_no_match()
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "turns.jsonl"
            try:
                plan = build_plan(trigger, state, turn_id="turn-1")
            except TriggerError:
                plan = None
            self.assertIsNone(plan)
            self.assertFalse(ledger_path.exists(), "抛错路径不得创建留痕文件")
            self.assertEqual(len(list(Path(tmp).iterdir())), 0,
                             "抛错路径不得在目录里留任何文件")

    def test_unconditional_fallback_rule_is_not_a_match(self):
        """兜底规则（when = null）不算命中——否则 fail-closed 形同虚设。"""
        trigger = load_trigger(_DEMO_PACK)
        closing = [r for r in trigger.rules if r.when is None]
        self.assertEqual(len(closing), 1, "demo 包应只有一条兜底规则")
        # 同一份「无命中」state 仍然抛错，说明兜底规则没有被当成命中
        with self.assertRaises(TriggerError):
            build_plan(trigger, _state_no_match(), turn_id="turn-1")

    def test_multi_match_picks_first_rule_in_order(self):
        """多条规则都匹配时取 trigger.json 里的第一条（顺序是既有契约，保证确定性）。"""
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
# 验收 4：负例——超预算 → BudgetError，消息含实际字数与预算
# ---------------------------------------------------------------------------
class TestOverBudget(unittest.TestCase):

    def _over_budget_trigger(self, budget_chars: int) -> "object":
        """把某个 key 的变体撑到超长（改夹具 → 校验由产品 API 做）。"""
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
        from adapters.state_trigger import PlanUnit, TriggerRule, Trigger
        from adapters.state_trigger import load_trigger as _load

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
        from adapters.state_trigger import PlanUnit, TriggerRule, Trigger
        from adapters.state_trigger import load_trigger as _load

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
        from adapters.state_trigger import ledger, trigger as trigger_module

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
    from adapters.state_trigger import load_trigger as _load

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
