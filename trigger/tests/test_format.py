"""
trigger.tests.test_format — T16 格式校验器测试

覆盖 T16 卡的验收标准 1–10：
  正例：load_trigger('packs/demo-brief') / validate_state 合法快照 / check_budget 未超预算
  负例：未声明字段 / 敏感值在结构化层 / trigger.json 未知字段 / 引用未审核 key /
        自由文本（SAY_LIVE 与 text）/ 超预算（消息含实际字数与预算值）
  确定性：同一 state + 同一 trigger.json → 逐字段相同的 plan 序列

反空转约束：本文件**只调用产品 API**（trigger 的公开函数），
不在测试里复制/重写被验逻辑。为了构造负例，这里只有一份 trigger.json 的
**原始夹具**（JSON 形状的数据，不是校验实现），负例 = 改夹具 → 调产品 API → 断言异常。
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from trigger import (
    BudgetError,
    StateError,
    TriggerError,
    check_budget,
    count_plan_chars,
    load_trigger,
    validate_state,
)


# 仓库根（tests/ → trigger/ → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEMO_PACK = _REPO_ROOT / "packs" / "demo-brief"


def _make_trigger_fixture() -> dict:
    """构造一份合法 trigger.json 夹具（6 个 key，全部来自 packs/demo-brief/phrases.json）。"""
    return {
        "trigger_version": 1,
        "trigger_id": "test-pack.brief",
        "budget_chars": 60,
        "state_fields": {
            "ticket_id": {"layer": "sensitive", "type": "str"},
            "ticket_status": {
                "layer": "structured",
                "type": "str",
                "enum": ["待受理", "处理中", "待验收", "已关闭"],
            },
            "days_left": {"layer": "structured", "type": "int", "min": 0, "max": 365},
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
                "when": {"is_overdue": False},
                "units": [
                    {"key": "due_days_left", "rate": "normal", "variant": 0, "slots": ["days_left"]}
                ],
            },
            {
                "rule_id": "ask_assignee",
                "when": {"assignee_confirmed": False},
                "units": [{"key": "ask_assignee", "rate": "normal", "variant": 0}],
            },
            {
                "rule_id": "closing",
                "when": None,
                "units": [{"key": "closing_brief", "rate": "normal", "variant": 0}],
            },
        ],
    }



def _write_pack(tmp_dir: Path, trigger_doc: dict) -> Path:
    """把夹具写成一份完整业务包（pack.json + phrases.json + trigger.json）。"""
    pack_dir = tmp_dir / "t-pack"
    pack_dir.mkdir(parents=True, exist_ok=True)

    (pack_dir / "pack.json").write_text(
        json.dumps(
            {
                "pack_id": "t-pack",
                "pack_version": "1",
                "protocol_version": "0.1",
                "ruleset_version": "v1",
                "voice": "Tingting",
                "model_version": "macos-say",
                "rates": ["normal", "slow", "fast"],
                "locale": "zh-CN",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    keys = [
        "greeting_ticket",
        "ticket_status",
        "due_tonight",
        "due_days_left",
        "already_overdue",
        "ask_assignee",
        "offer_help",
        "closing_brief",
    ]
    phrases = {
        "phrases": [
            {
                "key": k,
                "variants": [f"测试话术 {k}，请听。"],
                "rates": ["normal", "slow", "fast"],
            }
            for k in keys
        ]
    }
    (pack_dir / "phrases.json").write_text(
        json.dumps(phrases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pack_dir / "trigger.json").write_text(
        json.dumps(trigger_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return pack_dir


# 合法的 state 快照（工单到期提醒场景，公开 demo 数据）
_VALID_STATE = {
    "ticket_id": "WB20260918001",
    "ticket_status": "处理中",
    "days_left": 3,
    "is_overdue": False,
    "assignee_confirmed": False,
}


# 未超预算的 plan（已展开为文本，总字数远小于 60）
_VALID_PLAN = [{"text": "您好，这里是工单提醒服务。"}]


# ============================================================
# 1. 正例：load_trigger / validate_state / check_budget
# ============================================================
class TestLoadTriggerPositive(unittest.TestCase):
    """正例：load_trigger 对 packs/demo-brief 成功，产出不可变对象。"""

    def test_load_trigger_demo_brief(self):
        """T16 验收 3：load_trigger('packs/demo-brief') 成功。"""
        trigger = load_trigger(_DEMO_PACK)
        self.assertEqual(trigger.trigger_id, "demo-brief.ticket-reminder")
        self.assertEqual(trigger.trigger_version, 1)
        self.assertEqual(trigger.budget_chars, 60)
        self.assertGreater(len(trigger.rules), 0)
        self.assertIn("ticket_status", dict(trigger.state_fields))

    def test_load_trigger_returns_immutable(self):
        """load_trigger 返回 frozen dataclass：改动必须抛 FrozenInstanceError。"""
        trigger = load_trigger(_DEMO_PACK)
        with self.assertRaises(AttributeError):
            trigger.budget_chars = 999

    def test_load_trigger_budget_default_is_60(self):
        """budget_chars 缺省时取 60 字（docs/12 §12.8）。"""
        doc = _make_trigger_fixture()
        del doc["budget_chars"]
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            trigger = load_trigger(pack_dir)
            self.assertEqual(trigger.budget_chars, 60)


class TestValidateStatePositive(unittest.TestCase):
    """正例：validate_state 对合法快照不抛错。"""

    def test_validate_state_demo_brief(self):
        """T16 验收 3：validate_state 对一份合法快照通过。"""
        trigger = load_trigger(_DEMO_PACK)
        # 不应抛错
        validate_state(_VALID_STATE, trigger)

    def test_validate_state_accepts_dict_declaration(self):
        """validate_state 也接受 trigger.json 形状的普通字典声明表。"""
        doc = _make_trigger_fixture()
        validate_state(_VALID_STATE, doc["state_fields"])

    def test_validate_state_empty_state_ok(self):
        """空 state 快照合法（没有任何必填字段——字段由规则条件自己判定）。"""
        trigger = load_trigger(_DEMO_PACK)
        validate_state({}, trigger)


class TestCheckBudgetPositive(unittest.TestCase):
    """正例：check_budget 对未超预算的 plan 不抛错。"""

    def test_check_budget_within_limit(self):
        """T16 验收 3：check_budget 对未超预算的 plan 通过。"""
        check_budget(_VALID_PLAN, 60)

    def test_check_budget_accepts_pack_path(self):
        """check_budget 接受包目录路径作为预算来源（读 trigger.json 的 budget_chars）。"""
        plan = [{"text": "提醒完毕，祝您工作顺利。"}]
        # 不应抛错：4+6=10 字 < 60 字
        check_budget(plan, _DEMO_PACK)

    def test_count_plan_chars_counts_meaningful_chars(self):
        """count_plan_chars 去标点计字符（docs/12 §12.8 的实测口径）。"""
        # 「您好，这里是工单提醒服务。」= 11 个汉字 + 2 个标点，计数应为 11
        self.assertEqual(count_plan_chars([{"text": "您好，这里是工单提醒服务。"}]), 11)


# ============================================================
# 2. 负例：state 未声明字段
# ============================================================
class TestStateUndeclaredField(unittest.TestCase):
    """T16 验收 4：state 里多一个未声明字段 → StateError，消息含字段名。"""

    def test_state_undeclared_field_raises(self):
        trigger = load_trigger(_DEMO_PACK)
        state = dict(_VALID_STATE)
        state["undeclared_field"] = "x"
        with self.assertRaises(StateError) as ctx:
            validate_state(state, trigger)
        self.assertIn("undeclared_field", str(ctx.exception))

    def test_state_undeclared_field_listed(self):
        """多个未声明字段都要报出来（不只报第一个）。"""
        doc = _make_trigger_fixture()
        state = {"a_field": 1, "b_field": 2}
        with self.assertRaises(StateError) as ctx:
            validate_state(state, doc["state_fields"])
        msg = str(ctx.exception)
        self.assertIn("a_field", msg)
        self.assertIn("b_field", msg)

    def test_state_typo_field_raises(self):
        """typo 形式的未声明字段同样报错（对齐源格式对 typo 的态度）。"""
        doc = _make_trigger_fixture()
        with self.assertRaises(StateError) as ctx:
            validate_state({"days_lft": 3}, doc["state_fields"])
        self.assertIn("days_lft", str(ctx.exception))


# ============================================================
# 3. 负例：敏感值出现在结构化层
# ============================================================
class TestSensitiveInStructuredLayer(unittest.TestCase):
    """T16 验收 5：把账号/金额类值放进结构化层 → 报错，消息含字段名。"""

    def _decl(self, overrides=None):
        doc = _make_trigger_fixture()
        doc["state_fields"]["overdue_days"] = {"layer": "structured", "type": "int", "min": 0}
        if overrides:
            doc["state_fields"].update(overrides)
        return doc["state_fields"]

    def test_structured_decl_with_sensitive_marker_raises(self):
        """结构化层里声明账号字段 → 报错（docs/12 §12.10 的分层红线）。"""
        decl = self._decl({"user_account": {"layer": "structured", "type": "str"}})
        with self.assertRaises(StateError) as ctx:
            validate_state({"user_account": "13800000000"}, decl)
        self.assertIn("user_account", str(ctx.exception))

    def test_structured_decl_with_amount_marker_raises(self):
        """结构化层里声明金额字段 → 报错。"""
        decl = self._decl({"refund_amount": {"layer": "structured", "type": "str"}})
        with self.assertRaises(StateError) as ctx:
            validate_state({"refund_amount": "977.41 元"}, decl)
        self.assertIn("refund_amount", str(ctx.exception))

    def test_sensitive_value_type_mismatch_raises(self):
        """结构化层声明 type='int' 却给出金额串 → 报错（类型越界同样拦下）。"""
        decl = self._decl()
        with self.assertRaises(StateError) as ctx:
            validate_state({"overdue_days": "6 天"}, decl)
        self.assertIn("overdue_days", str(ctx.exception))

    def test_sensitive_layer_accepts_amount(self):
        """对照正例：敏感层声明 str/number/text 都接受（分层不是全禁，是分开存放）。"""
        decl = {
            "ticket_amount": {"layer": "sensitive", "type": "number"},
            "ticket_id": {"layer": "sensitive", "type": "str"},
            "raw_text": {"layer": "sensitive", "type": "text"},
        }
        validate_state(
            {"ticket_amount": 977.41, "ticket_id": "WB20260918001", "raw_text": "原始文本"},
            decl,
        )

    def test_sensitive_layer_type_rejected_in_structured(self):
        """敏感层类型（number/text）被声明成结构化层 → 报错。"""
        decl = {"balance": {"layer": "structured", "type": "number"}}
        with self.assertRaises(StateError) as ctx:
            validate_state({"balance": 977.41}, decl)
        self.assertIn("balance", str(ctx.exception))

    def test_structured_layer_rejects_nested_value(self):
        """state 必须扁平：嵌套字典 → 报错（消息含字段名）。"""
        decl = self._decl()
        with self.assertRaises(StateError) as ctx:
            validate_state({"overdue_days": {"v": 6}}, decl)
        self.assertIn("overdue_days", str(ctx.exception))

    def test_bool_is_not_int(self):
        """bool 不是合法整数（结构化层 int 字段的既有纪律）。"""
        decl = {"overdue_days": {"layer": "structured", "type": "int", "min": 0}}
        with self.assertRaises(StateError) as ctx:
            validate_state({"overdue_days": True}, decl)
        self.assertIn("overdue_days", str(ctx.exception))

    def test_enum_violation_raises(self):
        """枚举越界 → 报错，消息含字段名与实际值。"""
        decl = {
            "ticket_status": {
                "layer": "structured",
                "type": "str",
                "enum": ["待受理", "处理中"],
            }
        }
        with self.assertRaises(StateError) as ctx:
            validate_state({"ticket_status": "已作废"}, decl)
        self.assertIn("ticket_status", str(ctx.exception))
        self.assertIn("已作废", str(ctx.exception))


# ============================================================
# 4. 负例：trigger.json 未知字段
# ============================================================
class TestTriggerUnknownField(unittest.TestCase):
    """T16 验收 6：trigger.json 里塞 typo_field → 抛错，消息含 typo_field。"""

    def test_top_level_unknown_field_raises(self):
        doc = _make_trigger_fixture()
        doc["typo_field"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("typo_field", str(ctx.exception))

    def test_rule_unknown_field_raises(self):
        """规则级未知字段同样报错（不许静默丢弃）。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["whne"] = {"ticket_status": "处理中"}
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("whne", str(ctx.exception))

    def test_field_decl_unknown_field_raises(self):
        """state_fields 的字段声明里出现未知字段 → 报错。"""
        doc = _make_trigger_fixture()
        doc["state_fields"]["ticket_status"]["enums"] = ["处理中"]
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("enums", str(ctx.exception))

    def test_unit_unknown_field_raises(self):
        """plan 单元里的未知字段 → 报错。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["rat"] = "normal"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("rat", str(ctx.exception))

    def test_missing_required_field_raises(self):
        """顶层缺必填字段 → 报错。"""
        doc = _make_trigger_fixture()
        del doc["rules"]
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("rules", str(ctx.exception))

    def test_unsupported_version_raises(self):
        """trigger_version 不是 1 → 报错（不做静默兼容）。"""
        doc = _make_trigger_fixture()
        doc["trigger_version"] = 2
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("trigger_version", str(ctx.exception))

    def test_duplicate_rule_id_raises(self):
        """重复 rule_id → 报错（不许后者覆盖前者）。"""
        doc = _make_trigger_fixture()
        doc["rules"][1]["rule_id"] = doc["rules"][0]["rule_id"]
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("重复", str(ctx.exception))

    def test_two_unconditional_rules_raise(self):
        """兜底规则超过一条 → 报错（否则触发顺序不确定）。"""
        doc = _make_trigger_fixture()
        doc["rules"][1]["when"] = None
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("兜底", str(ctx.exception))

    def test_slot_referring_undeclared_field_raises(self):
        """槽位引用未声明的 state 字段 → 报错（不许静默丢弃）。"""
        doc = _make_trigger_fixture()
        doc["rules"][1]["units"][0]["slots"] = ["no_such_field"]
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("no_such_field", str(ctx.exception))

    def test_invalid_rate_raises(self):
        """非法语速档 → 报错（不回落，仓库红线）。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["rate"] = "turbo"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("turbo", str(ctx.exception))

    def test_auto_variant_raises(self):
        """variant='auto' 不允许（前置包要求确定性）。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["variant"] = "auto"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("auto", str(ctx.exception))


# ============================================================
# 4b. 负例：兜底规则不是最后一条（T16b）
# ============================================================
class TestFallbackNotLast(unittest.TestCase):
    """T16b 验收 2/3/5：when: null 的规则若不是 rules 的最后一条 → TriggerError。

    trigger.build_plan 的匹配口径是「按声明顺序取第一条命中」，兜底规则对**每一个**
    state 都命中，所以它放在前面会把后面的条件规则全部遮蔽——配置看起来有条件分支、
    实际全被兜底吃掉，零留痕（静默降级）。本类只在装载期把它拦下。
    """

    def _load(self, doc):
        """把 doc 写成合成包并走产品 API load_trigger（不在测试里自造位置判定）。"""
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            return load_trigger(pack_dir)

    def test_fallback_first_shadows_three_rules_raises(self):
        """T16b 验收 2：兜底在 rules[1]、后面还有 3 条条件规则 → 报错。

        消息必须同时含兜底的 rule_id、它的序号、被遮蔽的规则条数。
        """
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][3], doc["rules"][0], doc["rules"][1], doc["rules"][2]]
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("closing", msg)       # 兜底规则的 rule_id
        self.assertIn("rules[1]", msg)      # 兜底规则自己的序号
        self.assertIn("rules[2]", msg)      # 被遮蔽区间的起点
        self.assertIn("rules[4]", msg)      # 被遮蔽区间的终点
        self.assertIn("greeting", msg)      # 被遮蔽规则的 rule_id
        self.assertIn("ask_assignee", msg)
        self.assertIn("3", msg)             # 被遮蔽条数

    def test_fallback_in_middle_shadows_two_raises(self):
        """兜底在 rules[2]、后面还有 2 条条件规则 → 报错，被遮蔽条数为 2。"""
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][0], doc["rules"][3], doc["rules"][1], doc["rules"][2]]
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("closing", msg)
        self.assertIn("rules[2]", msg)
        self.assertIn("rules[3]", msg)
        self.assertIn("rules[4]", msg)
        self.assertIn("ask_assignee", msg)
        self.assertIn("2", msg)

    def test_fallback_at_end_of_three_raises(self):
        """兜底在 rules[3]、后面还有 1 条条件规则 → 报错，被遮蔽条数为 1。"""
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][1], doc["rules"][2], doc["rules"][3], doc["rules"][0]]
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("closing", msg)
        self.assertIn("rules[3]", msg)
        self.assertIn("rules[4]", msg)
        self.assertIn("greeting", msg)
        self.assertIn("1", msg)

    def test_fallback_last_is_valid(self):
        """T16b 验收 3-①：兜底是最后一条（前面有条件规则）→ 装载成功。

        fixture 默认顺序就是 closing 在最后，即真实包（demo-brief）的形状。
        """
        doc = _make_trigger_fixture()
        trigger = self._load(doc)
        self.assertEqual(len(trigger.rules), 4)
        self.assertIsNone(trigger.rules[-1].when, "兜底规则应落在 rules 的最后一条")
        self.assertIsNotNone(trigger.rules[-2].when)
        self.assertEqual(trigger.rules[-1].rule_id, "closing")

    def test_only_rule_is_fallback_valid(self):
        """T16b 验收 3-②：全部规则只有一条且它是兜底 → 装载成功（它既是首条也是末条）。"""
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][3]]
        trigger = self._load(doc)
        self.assertEqual(len(trigger.rules), 1)
        self.assertIsNone(trigger.rules[0].when)
        self.assertEqual(trigger.rules[0].rule_id, "closing")

    def test_fallback_not_last_but_no_fallback_valid(self):
        """无兜底规则的包不受位置检查影响 → 装载成功（位置检查只针对 when: null）。"""
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][1], doc["rules"][0], doc["rules"][2]]
        trigger = self._load(doc)
        self.assertEqual(len(trigger.rules), 3)
        for rule in trigger.rules:
            self.assertIsNotNone(rule.when)

    def test_two_unconditional_middle_last_still_caught_by_uniqueness(self):
        """T16b 验收 5：两条 when: null（一前一后）仍由「至多一条」检查定口径。

        兜底在 rules[2]、第二条 when: null 在 rules[3]：解析到第二条时才第一次发现
        重复，旧检查先按原口径抛错（消息与改动前同口径）——位置检查没替换它。
        """
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][0], doc["rules"][3], doc["rules"][1]]
        doc["rules"][2]["when"] = None
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("兜底规则最多一条", msg)
        self.assertIn("触发顺序不确定", msg)
        self.assertIn("due_days_left", msg)   # 第一条兜底（fixture[1]）
        self.assertIn("closing", msg)         # 第二条兜底（fixture[3]）
        self.assertNotIn("被遮蔽", msg, "该负例应由旧检查命中，不得被位置检查抢先替代")

    def test_two_unconditional_first_middle_still_caught_by_uniqueness(self):
        """T16b 验收 5（另一处）：两条 when: null 在 rules[1] 与 rules[3]。

        第二条落在 rules[3]（末条）——它本身位置合法，仍必须被「至多一条」拦下，
        不能因为第二条排在最后就让两条兜底同时通过。
        """
        doc = _make_trigger_fixture()
        doc["rules"] = [doc["rules"][1], doc["rules"][0], doc["rules"][2], doc["rules"][3]]
        doc["rules"][2]["when"] = None
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("兜底规则最多一条", msg)
        self.assertIn("closing", msg)
        self.assertIn("ask_assignee", msg)
        self.assertNotIn("被遮蔽", msg, "该负例应由旧检查命中，不得被位置检查抢先替代")


# ============================================================
# 4c. 负例：when 含未声明字段（T16c）
# ============================================================
class TestWhenUndeclaredField(unittest.TestCase):
    """T16c 验收 2/3：when 里引用未声明的 state 字段 → TriggerError。

    trigger.rule_matches 对 state 里缺值的字段返回「不命中」，所以一条引用未声明字段
    （typo）的规则会**永远静默地不触发**——零报错零留痕，正是本仓红线禁止的静默降级原型。
    与 slots 的既有装载期校验（test_slot_referring_undeclared_field_raises）对称补齐。
    """

    def _load(self, doc):
        """把 doc 写成合成包并走产品 API load_trigger（不在测试里自造判定）。"""
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            return load_trigger(pack_dir)

    def test_when_typo_field_raises_with_rule_id(self):
        """T16c 验收 2：when = {"typo_filed": "x"} → 报错，消息含字段名与该规则 rule_id。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["when"] = {"typo_filed": "x"}
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("typo_filed", msg)
        self.assertIn("greeting", msg)        # 所在规则的 rule_id
        self.assertIn("state_fields", msg)    # 点明该字段未在 state_fields 里声明

    def test_when_range_condition_typo_also_raises(self):
        """T16c 验收 3：区间写法 {"another_typo": {"gt": 0}} 同样被拦，消息含字段名。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["when"] = {"another_typo": {"gt": 0}}
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("another_typo", msg)
        self.assertIn("greeting", msg)

    def test_when_typo_in_later_rule_reports_that_rule(self):
        """typo 在后面的条件规则里 → 报错点明**该**规则的 rule_id，不误报前面的规则。"""
        doc = _make_trigger_fixture()
        doc["rules"][2]["when"] = {"assignee_confrimed": False}
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("assignee_confrimed", msg)
        self.assertIn("ask_assignee", msg)
        self.assertNotIn("greeting", msg)

    def test_when_typo_does_not_shadow_older_checks(self):
        """同一规则既有 typo 字段名、又有一个合法字段 → 先命中字段名检查（新增检查不替换旧检查）。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["when"] = {"typo_filed": "x", "ticket_status": "处理中"}
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("typo_filed", msg)
        self.assertNotIn("未知比较符", msg, "字段名检查不得改走 _validate_when 的旧口径")

    def test_when_typo_does_not_shadow_slot_check(self):
        """既有 slots 引用未声明字段的旧检查保持原口径（本卡只增不改）。"""
        doc = _make_trigger_fixture()
        doc["rules"][1]["units"][0]["slots"] = ["no_such_field"]
        with self.assertRaises(TriggerError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("no_such_field", msg)
        self.assertIn("槽位", msg)
        self.assertNotIn("含未声明字段", msg)


class TestWhenDeclaredField(unittest.TestCase):
    """T16c 验收 4/5/6：when 引用已声明字段（含区间写法）与 when: null 兜底 → 装载成功。"""

    def _load(self, doc):
        """把 doc 写成合成包并走产品 API load_trigger。"""
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            return load_trigger(pack_dir)

    def test_when_equal_reference_is_valid(self):
        """等值写法引用已声明字段 → 装载成功（fixture 默认形状）。"""
        trigger = self._load(_make_trigger_fixture())
        self.assertEqual(len(trigger.rules), 4)
        self.assertEqual(trigger.rules[0].when, {"ticket_status": "处理中"})

    def test_when_range_reference_is_valid(self):
        """区间写法（gt/lte）引用已声明字段 → 装载成功。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["when"] = {"days_left": {"gt": 0, "lte": 14}}
        trigger = self._load(doc)
        self.assertEqual(trigger.rules[0].when, {"days_left": {"gt": 0, "lte": 14}})

    def test_when_all_declared_fields_referenceable(self):
        """fixture 声明的每个字段都可被 when 引用（本检查不误伤任何已声明字段）。"""
        doc = _make_trigger_fixture()
        for field_name in doc["state_fields"]:
            doc = copy.deepcopy(_make_trigger_fixture())
            doc["rules"][0]["when"] = {field_name: 1}
            with self.subTest(field=field_name):
                trigger = self._load(doc)
                self.assertEqual(trigger.rules[0].when, {field_name: 1})

    def test_demo_brief_loads_unchanged(self):
        """T16c 验收 5（活体回归）：packs/demo-brief 原样装载成功。

        它的 when 只引用 ticket_status / is_overdue / days_left / assignee_confirmed，
        全部已声明；其中 due_tonight / due_days_left 是区间写法。
        """
        trigger = load_trigger(_DEMO_PACK)
        self.assertEqual(len(trigger.rules), 6)
        self.assertEqual(trigger.rules[3].when, {"is_overdue": False, "days_left": {"gt": 0, "lte": 14}})

    def test_when_none_fallback_unchanged(self):
        """T16c 验收 6：when: null 的兜底规则（位于最后）装载成功——本检查不误伤兜底语义。"""
        doc = _make_trigger_fixture()
        trigger = self._load(doc)
        self.assertIsNone(trigger.rules[-1].when)
        self.assertEqual(trigger.rules[-1].rule_id, "closing")
        # 兜底规则在合成包与真实包里都仍按原口径放行
        demo = load_trigger(_DEMO_PACK)
        self.assertIsNone(demo.rules[-1].when)

    def test_when_omitted_is_still_fallback(self):
        """when 字段整体缺省（不是显式 null）同样按兜底放行，不受本检查影响。"""
        doc = _make_trigger_fixture()
        del doc["rules"][3]["when"]
        trigger = self._load(doc)
        self.assertIsNone(trigger.rules[-1].when)

    def test_when_type_mismatch_not_checked(self):
        """T16c 明确不做类型相容性校验：对 bool 字段写 {"gt": 5} 属合法引用，装载成功。

        期望值与声明 type 的匹配是另一个议题（涉及 rule_matches 的语义），不在本卡范围。
        """
        doc = _make_trigger_fixture()
        doc["rules"][0]["when"] = {"is_overdue": {"gt": 5}}
        trigger = self._load(doc)
        self.assertEqual(trigger.rules[0].when, {"is_overdue": {"gt": 5}})

    def test_when_value_not_in_enum_still_loads(self):
        """同理不做枚举相容性校验：enum 字段的 when 期望值越界也照旧装载成功。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["when"] = {"ticket_status": "从未声明过的状态"}
        trigger = self._load(doc)
        self.assertEqual(trigger.rules[0].when, {"ticket_status": "从未声明过的状态"})


# ============================================================
# 5. 负例：引用未审核 key
# ============================================================
class TestUnreviewedKey(unittest.TestCase):
    """T16 验收 7：trigger.json 引用 phrases.json 里没有的 key → 抛错，消息含该 key。"""

    def test_unknown_key_raises(self):
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["key"] = "ghost_key"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("ghost_key", str(ctx.exception))

    def test_key_not_in_library_alignment(self):
        """口径对齐 C3b key_not_in_library：报错消息点明该 key 不在已审核库里。"""
        doc = _make_trigger_fixture()
        doc["rules"][2]["units"][0]["key"] = "not_prebaked_at_all"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("not_prebaked_at_all", str(ctx.exception))
        self.assertIn("phrases.json", str(ctx.exception))


# ============================================================
# 6. 负例：自由文本（SAY_LIVE / text）
# ============================================================
class TestFreeTextForbidden(unittest.TestCase):
    """T16 验收 8：plan 单元带 SAY_LIVE 或 text → 抛错（前置包不产生未审核文本）。"""

    def test_say_live_action_raises(self):
        """action='SAY_LIVE'（自由文本原语）→ 报错，消息含 SAY_LIVE。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["action"] = "SAY_LIVE"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("SAY_LIVE", str(ctx.exception))
        self.assertIn("C4a", str(ctx.exception))

    def test_say_live_with_text_raises(self):
        """完整 SAY_LIVE 单元（action + text）→ 报错。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"].append({"action": "SAY_LIVE", "text": "自由文本"})
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError):
                load_trigger(pack_dir)

    def test_text_field_raises(self):
        """plan 单元带 text 字段 → 报错（字段本身禁止出现，不是取值问题）。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["text"] = "自由文本"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("text", str(ctx.exception))

    def test_unknown_primitive_raises(self):
        """未知原语 → 报错（原语封闭白名单，引 core）。"""
        doc = _make_trigger_fixture()
        doc["rules"][0]["units"][0]["action"] = "SHOUT"
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(TriggerError) as ctx:
                load_trigger(pack_dir)
        self.assertIn("SHOUT", str(ctx.exception))


# ============================================================
# 7. 负例：超预算
# ============================================================
class TestOverBudget(unittest.TestCase):
    """T16 验收 9：总字数 61 字（预算 60）→ BudgetError，消息含 61 与 60。"""

    def test_over_budget_raises_with_both_numbers(self):
        plan = [{"text": "一" * 61}]
        with self.assertRaises(BudgetError) as ctx:
            check_budget(plan, 60)
        msg = str(ctx.exception)
        self.assertIn("61", msg)
        self.assertIn("60", msg)

    def test_at_budget_boundary_passes(self):
        """正好 60 字 = 未超预算（上限是 ≤，不是 <）。"""
        plan = [{"text": "一" * 60}]
        check_budget(plan, 60)

    def test_multi_unit_over_budget(self):
        """总字数按全 plan 累加（拼接才是超预算的真实来源，docs/12 §12.8）。"""
        plan = [{"text": "一" * 30}, {"text": "一" * 30}]
        self.assertEqual(count_plan_chars(plan), 60, "两个 30 字单元累加应为 60")
        # 边界：正好 60 字未超预算
        check_budget(plan, 60)
        # 拼上第三句 → 超预算
        with self.assertRaises(BudgetError) as ctx:
            check_budget(plan + [{"text": "一"}], 60)
        msg = str(ctx.exception)
        self.assertIn("61", msg)
        self.assertIn("60", msg)

    def test_budget_via_pack_path_uses_pack_value(self):
        """check_budget 以包目录为预算来源时，用的是该包 trigger.json 的 budget_chars。"""
        doc = _make_trigger_fixture()
        doc["budget_chars"] = 10
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            with self.assertRaises(BudgetError) as ctx:
                check_budget([{"text": "一" * 11}], pack_dir)
            self.assertIn("11", str(ctx.exception))
            self.assertIn("10", str(ctx.exception))

    def test_invalid_budget_value_raises(self):
        """budget_chars 不是整数/路径 → 报错（不静默用缺省值）。"""
        with self.assertRaises(BudgetError) as ctx:
            check_budget(_VALID_PLAN, 60.5)
        self.assertIn("60.5", str(ctx.exception))

    def test_nonexistent_pack_path_raises(self):
        """包目录不存在 → TriggerError（消息含该路径），不静默落到缺省预算。"""
        with self.assertRaises(TriggerError) as ctx:
            check_budget(_VALID_PLAN, "not-a-number")
        self.assertIn("not-a-number", str(ctx.exception))


# ============================================================
# 8. 确定性
# ============================================================
class TestDeterminism(unittest.TestCase):
    """T16 验收 10：同一 state + 同一 trigger.json → 逐字段相同的 plan 序列。"""

    def _plan_from_trigger(self, trigger):
        """把 Trigger 里全部规则的 units 按声明顺序展开成 plan 序列（不做任何匹配判定）。

        这里只遍历 Trigger 的不可变数据结构，不涉及匹配逻辑——
        匹配是 T17 的行为侧职责，本卡只验「格式是确定的」。
        """
        plan = []
        for rule in trigger.rules:
            for unit in rule.units:
                plan.append(
                    {
                        "action": unit.action,
                        "key": unit.key,
                        "rate": unit.rate,
                        "variant": unit.variant,
                        "slots": list(unit.slots),
                    }
                )
        return plan

    def test_two_loads_yield_identical_plan(self):
        """同一份 trigger.json 装载两次 → plan 序列逐字段相同。"""
        doc = _make_trigger_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = _write_pack(Path(tmp), doc)
            plan_a = self._plan_from_trigger(load_trigger(pack_dir))
            plan_b = self._plan_from_trigger(load_trigger(pack_dir))
        self.assertGreater(len(plan_a), 0, "plan 序列必须非空（否则确定性无从谈起）")
        self.assertEqual(plan_a, plan_b, "同一 state + 同一 trigger.json 必须产出逐字段相同的 plan")

    def test_plan_order_and_fields_stable(self):
        """plan 的字段集合、顺序、变体、语速档全部由 trigger.json 给出。"""
        trigger = load_trigger(_DEMO_PACK)
        plan = self._plan_from_trigger(trigger)
        for unit in plan:
            self.assertEqual(sorted(unit.keys()), ["action", "key", "rate", "slots", "variant"])
            self.assertIsInstance(unit["variant"], int)
            self.assertNotIn("text", unit)

    def test_state_validation_does_not_mutate_state(self):
        """validate_state 是纯校验：不得改动传入的 state 快照。"""
        trigger = load_trigger(_DEMO_PACK)
        snapshot = copy.deepcopy(_VALID_STATE)
        validate_state(snapshot, trigger)
        self.assertEqual(_VALID_STATE, snapshot)

    def test_demo_brief_trigger_is_deterministic(self):
        """packs/demo-brief 真实夹具也满足确定性。"""
        plan_a = self._plan_from_trigger(load_trigger(_DEMO_PACK))
        plan_b = self._plan_from_trigger(load_trigger(_DEMO_PACK))
        self.assertEqual(plan_a, plan_b)


# ============================================================
# 9. demo 包端到端：pack check 通过 + 格式校验通过
# ============================================================
class TestDemoBriefPack(unittest.TestCase):
    """T16 验收 1/2/3 的交叉点：packs/demo-brief 同时过 voxa pack check 与格式校验。"""

    def test_demo_brief_trigger_loads_and_state_validates(self):
        trigger = load_trigger(_DEMO_PACK)
        validate_state(_VALID_STATE, trigger)
        self.assertEqual(trigger.budget_chars, 60)

    def test_demo_brief_phrase_keys_all_present(self):
        """trigger 引用的每个 key 都在 phrases.json 里（C3b 口径的活体回归）。"""
        trigger = load_trigger(_DEMO_PACK)
        keys = set(trigger.phrase_keys)
        self.assertGreaterEqual(len(keys), 6, "demo 包至少 6 个 key（卡片要求 6–8 个）")
        for rule in trigger.rules:
            for unit in rule.units:
                self.assertIn(unit.key, keys, f"unit key '{unit.key}' 未出现在 phrases.json")

    def test_demo_brief_plan_within_budget(self):
        """demo 包单条规则产出的 plan 不超过其声明的预算（槽位按 5 字上限估）。"""

        trigger = load_trigger(_DEMO_PACK)
        phrases_doc = json.loads((_DEMO_PACK / "phrases.json").read_text(encoding="utf-8"))
        variant_map = {p["key"]: p["variants"][0] for p in phrases_doc["phrases"]}
        for rule in trigger.rules:
            with self.subTest(rule_id=rule.rule_id):
                rule_text = ""
                for unit in rule.units:
                    text = variant_map[unit.key]
                    for slot in unit.slots:
                        text = text.replace(f"{{{slot}}}", "一" * 5)
                    rule_text += text
                total = count_plan_chars([{"text": rule_text}])
                self.assertLessEqual(total, trigger.budget_chars, "单条规则的 plan 应不超预算")

    def test_demo_brief_pack_check_rc_zero(self):
        """T16 验收 2：./bin/vox pack check packs/demo-brief → rc 0 且 passed: true。"""
        import subprocess

        vox = _REPO_ROOT / "bin" / "vox"
        proc = subprocess.run(
            [str(vox), "pack", "check", str(_DEMO_PACK), "--json"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0, f"vox pack check 失败: {proc.stderr}")
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["passed"], f"pack check 未通过: {payload['violations']}")
        self.assertEqual(payload["phrases"], 8, "demo 包应有 8 个 key")


if __name__ == "__main__":
    unittest.main()
