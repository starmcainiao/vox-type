"""
trigger.tests.test_reachability — T16d 可达性检查（报告类 API）测试

覆盖 T16d 卡的验收标准：
  2  活体基准（demo 包）：check_reachability(load_trigger('packs/demo-brief')) →
     unreferenced_keys == ('offer_help', 'ticket_status') 且
     unreferenced_state_fields == ('ticket_id',)——与卡内实测基准逐个相等
  3  正例（全引用）：合成 Trigger，每个 key 被某单元引用、每个字段被 when 或
     slots 引用 → 两个元组全空
  4  只被 slots 引用算被引用（overdue_days 的实际形态，必须立用例）：字段只出现
     在某单元 slots、不在任何 when → 不出现；同理只出现在 when 的字段也不算未引用
  5  报告不抛错：含未引用 key / 未引用字段的 Trigger → 返回报告而不抛异常
  6  确定性：同一 Trigger 调两次 → 两个报告相等（frozen dataclass 可比较）
  7  包级导出可导入（另由验收标准 7 的命令行单独核对）

反空转约束：本文件**只调用产品 API** `check_reachability`，不在测试里自造可达性
  判定逻辑再拿它当期望值。合成 Trigger 的**期望清单是手写的数据**（测试数据，
  不是被验逻辑的复制）；期望值用字面量元组直书，便于「断言必须能失败」。
  合成 Trigger 直接构造公开 dataclass（Trigger/TriggerRule/PlanUnit/StateField），
  不读盘、不走装载器——本模块的输入本就是已装载的 Trigger。
"""

import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from trigger import (
    PlanUnit,
    ReachabilityReport,
    StateField,
    Trigger,
    TriggerRule,
    check_reachability,
    load_trigger,
)


# 仓库根（tests/ → trigger/ → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEMO_PACK = _REPO_ROOT / "packs" / "demo-brief"


# ---------------------------------------------------------------------------
# 夹具：合成 Trigger（数据，不是校验逻辑）
# ---------------------------------------------------------------------------
def _full_trigger() -> Trigger:
    """一份「全引用」合成 Trigger。

    形态：3 个 key 各被一个单元的 key 引用；4 个字段中
        status    → 出现在 when
        level     → 只出现在 slots（当量：demo 包的 overdue_days 形态）
        done      → 只出现在 when
        assignee  → when 与 slots 都出现
    期望：两个未引用清单都为空。
    """
    return Trigger(
        trigger_id="synthetic.full",
        trigger_version=1,
        budget_chars=60,
        state_fields=(
            ("status", StateField(layer="structured", type="str")),
            ("level", StateField(layer="structured", type="int")),
            ("done", StateField(layer="structured", type="bool")),
            ("assignee", StateField(layer="sensitive", type="str")),
        ),
        phrase_keys=("greeting", "overdue_notice", "closing"),
        rules=(
            TriggerRule(
                rule_id="greeting",
                when={"status": "处理中", "done": False},
                units=(
                    PlanUnit(key="greeting"),
                    PlanUnit(key="overdue_notice", slots=("level", "assignee")),
                ),
            ),
            TriggerRule(
                rule_id="closing",
                when=None,
                units=(PlanUnit(key="closing"),),
            ),
        ),
        pack_dir=str(_REPO_ROOT),
    )


def _dead_weight_trigger() -> Trigger:
    """一份含两类死重量的合成 Trigger（双形态包形态的当量）。

    形态：phrase_keys 里 3 个 key 只被 rules 引用了 1 个（另外 2 个假定只被
    同包 script.json 用到，合法形态）；4 个已声明 state 字段里 3 个无任何
    when / slots 引用（ticket_status 是同名话术 key，但这里没被任何规则判定
    或拼接——key 引用与字段引用是两条独立的线，不互相抵扣）。
    期望：
        unreferenced_keys          == ('offer_help', 'ticket_status')
        unreferenced_state_fields  == ('assignee_confirmed', 'ticket_id', 'ticket_status')
    """
    return Trigger(
        trigger_id="synthetic.dead-weight",
        trigger_version=1,
        budget_chars=60,
        state_fields=(
            ("ticket_id", StateField(layer="sensitive", type="str")),
            ("ticket_status", StateField(layer="structured", type="str")),
            ("assignee_confirmed", StateField(layer="structured", type="bool")),
            ("days_left", StateField(layer="structured", type="int")),
        ),
        phrase_keys=("offer_help", "ticket_status", "closing"),
        rules=(
            TriggerRule(
                rule_id="due_days",
                when={"days_left": 3},
                units=(
                    PlanUnit(key="closing", slots=("days_left",)),
                ),
            ),
        ),
        pack_dir=str(_REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# 验收标准 2：活体基准（demo 包）
# ---------------------------------------------------------------------------
class DemoPackBaselineTest(unittest.TestCase):
    """demo 包实测基准：与卡内 2026-09-19 程序化核对的数逐个相等。"""

    def test_demo_pack_unreferenced_keys_match_card_baseline(self):
        report = check_reachability(load_trigger(str(_DEMO_PACK)))
        self.assertEqual(
            report.unreferenced_keys, ("offer_help", "ticket_status")
        )

    def test_demo_pack_unreferenced_state_fields_match_card_baseline(self):
        report = check_reachability(load_trigger(str(_DEMO_PACK)))
        self.assertEqual(report.unreferenced_state_fields, ("ticket_id",))

    def test_demo_pack_overdue_days_is_referenced_via_slots_only(self):
        """overdue_days 无任何 when 依赖，但被 overdue 规则的 slots 引用 → 算被引用。

        卡内点名必须立的用例：只出现在 slots 的字段不得进未引用清单。
        """
        report = check_reachability(load_trigger(str(_DEMO_PACK)))
        self.assertNotIn("overdue_days", report.unreferenced_state_fields)


# ---------------------------------------------------------------------------
# 验收标准 3：正例（全引用）
# ---------------------------------------------------------------------------
class FullyReferencedTest(unittest.TestCase):
    """每个 key 被某单元引用、每个字段被 when 或 slots 引用 → 两个元组全空。"""

    def test_no_unreferenced_keys(self):
        report = check_reachability(_full_trigger())
        self.assertEqual(report.unreferenced_keys, ())

    def test_no_unreferenced_state_fields(self):
        report = check_reachability(_full_trigger())
        self.assertEqual(report.unreferenced_state_fields, ())


# ---------------------------------------------------------------------------
# 验收标准 4：引用口径的两种形态（slots-only / when-only）
# ---------------------------------------------------------------------------
class ReferenceSitesTest(unittest.TestCase):
    """只出现在 slots 的字段、只出现在 when 的字段都算被引用。"""

    def test_field_referenced_only_via_slots_is_not_unreferenced(self):
        report = check_reachability(_full_trigger())
        # level 不在任何 when 里，只出现在 overdue_notice 单元的 slots
        self.assertNotIn("level", report.unreferenced_state_fields)

    def test_field_referenced_only_via_when_is_not_unreferenced(self):
        report = check_reachability(_full_trigger())
        # done 只在 greeting 规则的 when 里，不在任何 slots
        self.assertNotIn("done", report.unreferenced_state_fields)

    def test_fields_referenced_in_both_sites_are_not_unreferenced(self):
        report = check_reachability(_full_trigger())
        # status / assignee 在 when 与 slots 都出现
        self.assertNotIn("status", report.unreferenced_state_fields)
        self.assertNotIn("assignee", report.unreferenced_state_fields)


# ---------------------------------------------------------------------------
# 验收标准 5：报告不抛错（报告语义的负向断言）
# ---------------------------------------------------------------------------
class ReportDoesNotRaiseTest(unittest.TestCase):
    """含未引用 key / 未引用字段的 Trigger → 返回报告，不抛异常。

    语义裁定：双形态包里「key 只被 script.json 用」是合法形态，本检查只报告
    不报错。若这里抛异常，等于把盘点报告私自升级成校验闸门（越权收紧）。
    """

    def test_dead_weight_returns_report_without_raising(self):
        report = check_reachability(_dead_weight_trigger())  # 不得抛异常
        self.assertIsInstance(report, ReachabilityReport)

    def test_dead_weight_lists_both_categories(self):
        report = check_reachability(_dead_weight_trigger())
        self.assertEqual(
            report.unreferenced_keys, ("offer_help", "ticket_status")
        )
        self.assertEqual(
            report.unreferenced_state_fields,
            ("assignee_confirmed", "ticket_id", "ticket_status"),
        )

    def test_empty_rule_set_reports_every_key_as_unreferenced(self):
        """边界：一条规则都没有（合法的「空规则」装载结果）→ 全部 key 未引用，
        字段亦全部未引用，仍然只报告不报错。"""
        empty = Trigger(
            trigger_id="synthetic.empty-rules",
            trigger_version=1,
            budget_chars=60,
            state_fields=(("only_field", StateField(layer="structured", type="int")),),
            phrase_keys=("a_key", "b_key"),
            rules=(),
            pack_dir=str(_REPO_ROOT),
        )
        report = check_reachability(empty)
        self.assertEqual(report.unreferenced_keys, ("a_key", "b_key"))
        self.assertEqual(report.unreferenced_state_fields, ("only_field",))


# ---------------------------------------------------------------------------
# 验收标准 6：确定性（frozen dataclass 可比较）
# ---------------------------------------------------------------------------
class DeterminismTest(unittest.TestCase):
    """同一 Trigger 调两次 → 两个报告相等；输出排序稳定（与输入顺序无关）。"""

    def test_same_trigger_two_calls_produce_equal_reports(self):
        trigger = load_trigger(str(_DEMO_PACK))
        self.assertEqual(check_reachability(trigger), check_reachability(trigger))

    def test_synthetic_trigger_two_calls_produce_equal_reports(self):
        trigger = _dead_weight_trigger()
        self.assertEqual(check_reachability(trigger), check_reachability(trigger))

    def test_report_is_frozen(self):
        """报告不可变：frozen dataclass 改字段必须抛 FrozenInstanceError。"""
        report = check_reachability(load_trigger(str(_DEMO_PACK)))
        with self.assertRaises(FrozenInstanceError):
            report.unreferenced_keys = ("someone_else",)

    def test_synthetic_results_are_sorted_regardless_of_input_order(self):
        """字段与 key 的声明顺序打乱 → 未引用清单仍为排序后的同一元组。"""
        trigger = _dead_weight_trigger()
        scrambled = Trigger(
            trigger_id=trigger.trigger_id,
            trigger_version=trigger.trigger_version,
            budget_chars=trigger.budget_chars,
            # 声明顺序反转
            state_fields=tuple(reversed(trigger.state_fields)),
            # phrase_keys 顺序反转
            phrase_keys=tuple(reversed(trigger.phrase_keys)),
            rules=trigger.rules,
            pack_dir=trigger.pack_dir,
        )
        self.assertEqual(
            check_reachability(scrambled).unreferenced_keys,
            ("offer_help", "ticket_status"),
        )
        self.assertEqual(
            check_reachability(scrambled).unreferenced_state_fields,
            ("assignee_confirmed", "ticket_id", "ticket_status"),
        )


if __name__ == "__main__":
    unittest.main()
