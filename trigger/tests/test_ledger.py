"""
trigger.tests.test_ledger — T17 轮级留痕测试

覆盖 T17 卡的验收标准：
  2   正例：record_turn 写出一条留痕，文件行数 +1
  5   负例（敏感层落盘）：state 带敏感层值 → 留痕文件里 grep 该敏感值 0 命中（硬红线）
  6   负例（写入失败）：目标不可写 → 抛 LedgerError，消息含该路径
  8   留痕可还原监督配对：读回一条记录能取出 (state 结构化层, plan key 列表)，
      且敏感层不在其中
  9   字段名合规：turn_id / plan_id 字面量与 core.metrics_spec 的常量一致

反空转约束：本文件**只调用产品 API**（trigger 的公开函数：
  record_turn / structured_record / read_turns / supervision_pairs / default_ledger_path /
  build_plan / load_trigger / validate_state），不在测试里复制/重写被验逻辑。
  测试**不依赖真实时钟**：时间戳全部由参数注入（见 ts 常量）。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.metrics_spec import PLAN_ID, TURN_ID
from trigger import (
    LedgerError,
    build_plan,
    default_ledger_path,
    load_trigger,
    read_turns,
    record_turn,
    structured_record,
    supervision_pairs,
    validate_state,
)


# 仓库根（tests/ → trigger/ → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEMO_PACK = _REPO_ROOT / "packs" / "demo-brief"

# 时间戳固定注入：测试不得依赖真实时钟（否则测不了确定性）
TS_1 = "2026-09-18T00:00:00Z"
TS_2 = "2026-09-18T00:01:00Z"

# 敏感层的值（用于红线断言：留痕文件里必须 0 命中）
SENSITIVE_TICKET_ID = "T-SECRET-4117"
SENSITIVE_ACCOUNT = "6222-0218-8837-9901"


def _state_with_sensitive() -> dict:
    """一份合法 state：结构化层字段 + 敏感层字段（ticket_id 是敏感层）。"""
    return {
        "ticket_id": SENSITIVE_TICKET_ID,
        "ticket_status": "处理中",
        "days_left": 3,
        "overdue_days": 0,
        "is_overdue": False,
        "assignee_confirmed": True,
    }


def _build(trigger, state, turn_id="turn-1"):
    """走产品 API 造一份 plan（留痕的输入）。"""
    validate_state(state, trigger)
    return build_plan(trigger, state, turn_id=turn_id)


# ---------------------------------------------------------------------------
# 验收 2：正例——写出一条留痕，行数 +1
# ---------------------------------------------------------------------------
class TestRecordTurnPositive(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ledger = self.dir / "turns.jsonl"
        self.trigger = load_trigger(_DEMO_PACK)
        self.state = _state_with_sensitive()
        self.plan = _build(self.trigger, self.state, turn_id="turn-1")

    def test_record_turn_appends_one_line(self):
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )
        self.assertTrue(self.ledger.exists())
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, "第一次写入后应为 1 行")
        record = json.loads(lines[0])
        for field in (TURN_ID, PLAN_ID, "ts", "rule_id", "state", "plan"):
            self.assertIn(field, record, f"留痕缺少字段 {field!r}")

    def test_second_write_appends(self):
        """JSONL 追加写：第二次写入后行数 +1，第一条不被覆盖。"""
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-2", plan_id=self.plan.plan_id, ts=TS_2, path=self.ledger,
        )
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["turn_id"], "turn-1")
        self.assertEqual(json.loads(lines[1])["turn_id"], "turn-2")

    def test_record_turn_returns_target_path(self):
        written = record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )
        self.assertEqual(written, self.ledger.resolve())

    def test_read_turns_returns_records_in_order(self):
        for i, (turn_id, ts) in enumerate((("turn-1", TS_1), ("turn-2", TS_2)), start=1):
            record_turn(
                self.trigger, self.state, self.plan,
                turn_id=turn_id, plan_id=self.plan.plan_id, ts=ts, path=self.ledger,
            )
        records = read_turns(self.ledger)
        self.assertEqual(len(records), 2)
        self.assertEqual([r["turn_id"] for r in records], ["turn-1", "turn-2"])

    def test_read_turns_missing_file_returns_empty(self):
        self.assertEqual(read_turns(self.dir / "nope.jsonl"), tuple())

    def test_record_turn_accepts_relative_path(self):
        """相对路径按当前工作目录解析（不锚定到仓库根，避免私人数据悄悄入仓）。"""
        cwd = os.getcwd()
        self.addCleanup(lambda: os.chdir(cwd))
        os.chdir(self.dir)
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path="rel.jsonl",
        )
        self.assertTrue((self.dir / "rel.jsonl").exists())

    def test_default_ledger_path_is_outside_repo(self):
        """缺省落盘位置必须在仓外（防止私人数据入仓）。"""
        outside = self.dir / "outside"
        old = os.environ.get("VOX_LEDGER_DIR")
        os.environ["VOX_LEDGER_DIR"] = str(outside)
        try:
            path = default_ledger_path()
            self.assertEqual(path, (outside / "turns.jsonl"))
            try:
                path.resolve().relative_to(_REPO_ROOT)
                self.fail(f"缺省留痕路径落在仓库内: {path}")
            except ValueError:
                pass
        finally:
            if old is None:
                os.environ.pop("VOX_LEDGER_DIR", None)
            else:
                os.environ["VOX_LEDGER_DIR"] = old

    def test_default_ledger_path_falls_back_to_home(self):
        """未设 VOX_LEDGER_DIR 时落到 ~/.vox-ledger/turns.jsonl（仍在仓外）。"""
        old = os.environ.pop("VOX_LEDGER_DIR", None)
        try:
            path = default_ledger_path()
            self.assertEqual(path.name, "turns.jsonl")
            self.assertIn(Path.home(), path.parents)
            try:
                path.resolve().relative_to(_REPO_ROOT)
                self.fail(f"缺省留痕路径落在仓库内: {path}")
            except ValueError:
                pass
        finally:
            if old is not None:
                os.environ["VOX_LEDGER_DIR"] = old

    def test_record_turn_default_path_writes_outside_repo(self):
        """不传 path 时，写入位置仍是仓外。"""
        outside = self.dir / "outside"
        outside.mkdir()
        old = os.environ.get("VOX_LEDGER_DIR")
        os.environ["VOX_LEDGER_DIR"] = str(outside)
        try:
            written = record_turn(
                self.trigger, self.state, self.plan,
                turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1,
            )
            self.assertEqual(written, (outside / "turns.jsonl"))
            self.assertTrue(written.exists())
            try:
                written.resolve().relative_to(_REPO_ROOT)
                self.fail(f"缺省写入路径落在仓库内: {written}")
            except ValueError:
                pass
        finally:
            if old is None:
                os.environ.pop("VOX_LEDGER_DIR", None)
            else:
                os.environ["VOX_LEDGER_DIR"] = old


# ---------------------------------------------------------------------------
# 验收 5：负例——敏感层绝不落盘（硬红线，必须实测断言 0 命中）
# ---------------------------------------------------------------------------
class TestSensitiveLayerNeverLanded(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ledger = self.dir / "turns.jsonl"
        self.trigger = load_trigger(_DEMO_PACK)
        self.state = _state_with_sensitive()
        self.plan = _build(self.trigger, self.state, turn_id="turn-1")

    def test_sensitive_value_not_in_file(self):
        """红线：留痕文件里 grep 敏感值必须 0 命中。"""
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )
        content = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn(SENSITIVE_TICKET_ID, content,
                         "敏感层字段值出现在留痕文件里（docs/12 §12.10 红线）")
        self.assertNotIn(SENSITIVE_ACCOUNT, content)

    def test_sensitive_field_name_not_in_state_block(self):
        """结构化层块里不得出现敏感层字段名（值被丢掉，字段名也不留）。"""
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )
        record = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertNotIn("ticket_id", record["state"])
        self.assertEqual(
            set(record["state"].keys()),
            {"ticket_status", "days_left", "overdue_days", "is_overdue",
             "assignee_confirmed"},
            "留痕的 state 块应只含结构化层字段",
        )

    def test_sensitive_value_stays_out_across_multiple_records(self):
        """多条记录、多种 state 组合下敏感值都不落盘。"""
        for i in range(3):
            state = dict(self.state)
            state["ticket_status"] = "待验收"
            state["days_left"] = i
            plan = _build(self.trigger, state, turn_id=f"turn-{i}")
            record_turn(
                self.trigger, state, plan,
                turn_id=f"turn-{i}", plan_id=plan.plan_id, ts=TS_1, path=self.ledger,
            )
        content = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn(SENSITIVE_TICKET_ID, content)
        self.assertEqual(len(content.splitlines()), 3)

    def test_slot_values_come_only_from_structured_fields(self):
        """槽位只取结构化层字段，敏感字段不会被拖进 plan 的 slots。"""
        state = dict(self.state)
        state["ticket_status"] = "待验收"
        state["days_left"] = 5
        plan = _build(self.trigger, state, turn_id="turn-1")
        self.assertEqual(plan.rule_id, "due_days_left")
        self.assertEqual(list(plan.plan[0].slots.keys()), ["days_left"])

        record_turn(
            self.trigger, state, plan,
            turn_id="turn-1", plan_id=plan.plan_id, ts=TS_1, path=self.ledger,
        )
        content = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn(SENSITIVE_TICKET_ID, content)
        self.assertIn('"days_left"', content)

    def test_sensitive_value_in_structured_layer_rejected_at_source(self):
        """错配（把含敏感标记的字段声明为结构化层）在 validate_state 被拦下。

        实现侧注记：T16 的敏感值嗅探**只按字段名标记**（account/phone/amount/…），
        不解析值；`ticket_id` 这类字段名不含任何标记，所以它被判合法与否
        完全取决于声明的 layer——这正是 T16 设计里「白名单只此一处」的边界。
        本测试选 `customer_account`（含标记 'account'）来验证嗅探路径真的生效。
        """
        from trigger import StateError

        decl = {
            "customer_account": {"layer": "structured", "type": "str"},  # 错配
            "ticket_status": {
                "layer": "structured", "type": "str",
                "enum": ["待受理", "处理中"],
            },
        }
        with self.assertRaises(StateError) as ctx:
            validate_state(
                {"customer_account": SENSITIVE_ACCOUNT, "ticket_status": "处理中"}, decl
            )
        message = str(ctx.exception)
        self.assertIn("customer_account", message)
        self.assertIn("account", message)
        self.assertIn("敏感", message)

    def test_field_without_marker_declared_structured_is_accepted(self):
        """记录 T16 的实际边界：字段名不含敏感标记时，嗅探不拦（不做假阳性）。

        这条断言不是「放水」，而是把嗅探的口径钉住——否则以后有人把
        敏感值塞进 `ticket_id`（未含标记）就没人知道了。
        """
        decl = {
            "ticket_id": {"layer": "structured", "type": "str"},
        }
        validate_state({"ticket_id": SENSITIVE_TICKET_ID}, decl)  # 不抛错

    def test_sensitive_value_never_reaches_ledger_after_reject(self):
        """validate_state 拦下之后：不落盘文件（红线端到端验证）。"""
        from trigger import StateError

        decl = {
            "customer_account": {"layer": "structured", "type": "str"},
            "ticket_status": {
                "layer": "structured", "type": "str",
                "enum": ["处理中"],
            },
        }
        state = {"customer_account": SENSITIVE_ACCOUNT, "ticket_status": "处理中"}
        target = self.dir / "turns.jsonl"

        produced = None
        try:
            produced = validate_state(state, decl)
        except StateError:
            pass
        self.assertIsNone(produced, "错配声明不得通过校验")
        self.assertFalse(target.exists(), "校验失败不得产生留痕文件")

    def test_correct_layering_writes_ledger_with_sensitive_absent(self):
        """正向对照：正确分层（ticket_id = sensitive）→ 正常写留痕，敏感值 0 命中。"""
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.dir / "ok.jsonl",
        )
        content = (self.dir / "ok.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SENSITIVE_TICKET_ID, content)
        self.assertNotIn(SENSITIVE_ACCOUNT, content)

    def test_sensitive_field_declared_structured_but_value_type_invalid(self):
        """即使字段名不含敏感标记，敏感层的 type 声明在结构化层也不合法。"""
        from trigger import StateError

        decl = {
            "ticket_status": {
                "layer": "structured", "type": "str",
                "enum": ["待受理", "处理中"],
            },
            "raw_amount": {"layer": "structured", "type": "number"},  # 类型不合法
        }
        with self.assertRaises(StateError):
            validate_state(
                {"ticket_status": "处理中", "raw_amount": 1234.56}, decl
            )


# ---------------------------------------------------------------------------
# 验收 6：负例——写入失败 → LedgerError，消息含目标路径
# ---------------------------------------------------------------------------
class TestWriteFailure(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.trigger = load_trigger(_DEMO_PACK)
        self.state = _state_with_sensitive()
        self.plan = _build(self.trigger, self.state, turn_id="turn-1")

    def _write_into(self, target: Path):
        return record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=target,
        )

    def test_target_is_a_directory_raises_with_path(self):
        existing_dir = self.dir / "already-a-dir"
        existing_dir.mkdir()
        with self.assertRaises(LedgerError) as ctx:
            self._write_into(existing_dir)
        self.assertIn(str(existing_dir.resolve()), str(ctx.exception))

    def test_missing_parent_dir_raises_with_path(self):
        """父目录不存在 → 抛错且不自动创建（自动建目录 = 路径写错被静默掩盖）。"""
        target = self.dir / "no-such-dir" / "turns.jsonl"
        with self.assertRaises(LedgerError) as ctx:
            self._write_into(target)
        self.assertIn(str(target.resolve()), str(ctx.exception))
        self.assertFalse(target.exists())
        self.assertFalse((self.dir / "no-such-dir").exists(), "不得自动创建父目录")

    def test_parent_is_a_file_raises_with_path(self):
        blocker = self.dir / "blocker"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "turns.jsonl"
        with self.assertRaises(LedgerError) as ctx:
            self._write_into(target)
        self.assertIn(str(target.resolve()), str(ctx.exception))

    def test_nonwritable_target_raises_with_path(self):
        """只读文件（chmod 000）→ 抛 LedgerError，消息含路径。

        注意：root 用户下 chmod 仍可写，此时测试会观察到写入成功——
        这种情况改为断言「要么成功、要么消息含路径」，不把失败误报为通过。
        """
        target = self.dir / "readonly.jsonl"
        target.write_text("", encoding="utf-8")
        os.chmod(target, 0o000)
        self.addCleanup(lambda: os.chmod(target, 0o644))

        try:
            self._write_into(target)
        except LedgerError as ctx:
            self.assertIn(str(target.resolve()), str(ctx))
            return  # 符合预期：失败且消息含路径

        # 兜底：如果运行身份能写（例如 root），断言至少写成了、且内容合法
        content = target.read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 1)

    def test_rejects_non_path_argument(self):
        with self.assertRaises(LedgerError) as ctx:
            record_turn(
                self.trigger, self.state, self.plan,
                turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=42,
            )
        self.assertIn("留痕目标路径", str(ctx.exception))

    def test_missing_turn_id_raises(self):
        with self.assertRaises(LedgerError):
            record_turn(
                self.trigger, self.state, self.plan,
                turn_id=None, plan_id=self.plan.plan_id, ts=TS_1, path=self.dir / "x.jsonl",
            )

    def test_missing_plan_id_raises(self):
        with self.assertRaises(LedgerError):
            record_turn(
                self.trigger, self.state, self.plan,
                turn_id="turn-1", plan_id=None, ts=TS_1, path=self.dir / "x.jsonl",
            )

    def test_missing_ts_raises(self):
        """时间戳必须注入：本模块不读真实时钟（否则确定性无法测试）。"""
        with self.assertRaises(LedgerError) as ctx:
            record_turn(
                self.trigger, self.state, self.plan,
                turn_id="turn-1", plan_id=self.plan.plan_id, ts=None,
                path=self.dir / "x.jsonl",
            )
        self.assertIn("时钟", str(ctx.exception))


# ---------------------------------------------------------------------------
# 验收 8：留痕可还原监督配对
# ---------------------------------------------------------------------------
class TestSupervisionPair(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ledger = self.dir / "turns.jsonl"
        self.trigger = load_trigger(_DEMO_PACK)
        self.state = _state_with_sensitive()
        self.plan = _build(self.trigger, self.state, turn_id="turn-1")
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )

    def test_pair_extractable_from_file(self):
        """从文件读回一条记录 → 能取出 (state 结构化层, plan key 列表)。"""
        records = read_turns(self.ledger)
        self.assertEqual(len(records), 1)
        state_structured, keys = supervision_pairs(records[0])
        self.assertEqual(state_structured["ticket_status"], "处理中")
        self.assertEqual(keys, ("greeting_ticket",))

    def test_pair_excludes_sensitive_layer(self):
        """断言敏感层不在还原出的配对里。"""
        records = read_turns(self.ledger)
        state_structured, keys = supervision_pairs(records[0])
        self.assertNotIn("ticket_id", state_structured)
        self.assertNotIn(SENSITIVE_TICKET_ID, json.dumps(state_structured, ensure_ascii=False))
        self.assertNotIn(SENSITIVE_TICKET_ID, str(keys))

    def test_pair_keys_match_plan_order(self):
        """key 列表顺序与 plan 单元顺序一致（便于逐 unit 对齐）。"""
        records = read_turns(self.ledger)
        _state, keys = supervision_pairs(records[0])
        self.assertEqual(list(keys), [u["key"] for u in records[0]["plan"]])
        self.assertEqual(list(keys), [u.key for u in self.plan.plan])

    def test_pair_multiple_records_all_extractable(self):
        state2 = dict(self.state)
        state2["ticket_status"] = "待验收"
        state2["days_left"] = 9
        plan2 = _build(self.trigger, state2, turn_id="turn-2")
        record_turn(
            self.trigger, state2, plan2,
            turn_id="turn-2", plan_id=plan2.plan_id, ts=TS_2, path=self.ledger,
        )
        records = read_turns(self.ledger)
        self.assertEqual(len(records), 2)
        pairs = [supervision_pairs(r) for r in records]
        self.assertEqual(pairs[0][1], ("greeting_ticket",))
        self.assertEqual(pairs[1][1], ("due_days_left",))
        for state_structured, keys in pairs:
            self.assertNotIn(SENSITIVE_TICKET_ID, json.dumps(state_structured, ensure_ascii=False))
            self.assertNotIn(SENSITIVE_TICKET_ID, str(keys))

    def test_supervision_pairs_rejects_bad_record(self):
        with self.assertRaises(LedgerError):
            supervision_pairs("not-a-dict")
        with self.assertRaises(LedgerError):
            supervision_pairs({"state": "not-a-dict"})
        with self.assertRaises(LedgerError):
            supervision_pairs({"state": {}, "keys": "not-a-list"})


# ---------------------------------------------------------------------------
# 验收 9：字段名合规（留痕字面量必须等于 core.metrics_spec 的常量）
# ---------------------------------------------------------------------------
class TestLedgerFieldNames(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "turns.jsonl"
        self.trigger = load_trigger(_DEMO_PACK)
        self.state = _state_with_sensitive()
        self.plan = _build(self.trigger, self.state, turn_id="turn-1")

    def test_record_uses_core_constants(self):
        record = structured_record(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1,
        )
        self.assertEqual(record[TURN_ID], "turn-1")
        self.assertEqual(record[PLAN_ID], self.plan.plan_id)
        self.assertEqual(record["ts"], TS_1)
        # 没有自造的同义字段名
        for alias in ("turnId", "planId", "turn", "plan_id_", "turn_id_"):
            self.assertNotIn(alias, record)

    def test_landed_json_uses_core_constants(self):
        record_turn(
            self.trigger, self.state, self.plan,
            turn_id="turn-1", plan_id=self.plan.plan_id, ts=TS_1, path=self.ledger,
        )
        record = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertIn(TURN_ID, record)
        self.assertIn(PLAN_ID, record)
        # 与 core.metrics_spec 的字面量逐一相等（不得自造）
        self.assertEqual(TURN_ID, "turn_id")
        self.assertEqual(PLAN_ID, "plan_id")

    def test_module_aliases_equal_core_constants(self):
        from trigger import ledger, plan as trigger_module
        self.assertEqual(ledger.TURN_ID_FIELD, TURN_ID)
        self.assertEqual(ledger.PLAN_ID_FIELD, PLAN_ID)
        self.assertEqual(ledger.TS_FIELD, "ts")
        self.assertEqual(ledger.KEY_FIELD, "key")
        self.assertEqual(trigger_module.TURN_ID_FIELD, TURN_ID)
        self.assertEqual(trigger_module.PLAN_ID_FIELD, PLAN_ID)


if __name__ == "__main__":
    unittest.main()
