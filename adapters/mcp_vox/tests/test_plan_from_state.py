"""adapters.mcp_vox.tests.test_plan_from_state — MCP 第 4 个工具（T86）。

`vox_plan_from_state(pack_dir, state, turn_id?)` 把 `trigger/` 的「状态 → plan」接成产品出口。

正例：`packs/demo-brief` + 合法 state → isError False，plan 非空，
      且 plan 里的 key 全部 ∈ 该包 phrase_keys（用 load_trigger 作 oracle，不复制判定逻辑）。
负例 1：state 含未声明字段 → isError True，**消息含该字段名**。
负例 2：把兜底规则摘掉后喂一个「谁都不匹配」的 state → isError True，**消息含 trigger_id**，
       且**不得返回空 plan、不得自行挑一条兜底**。
负例 3：把「铸出来的资产包」传进来（无 trigger.json）→ isError True，**消息含该路径**。

判定逻辑全在产品代码（`adapters.mcp_vox.tools._plan_from_state` + `trigger/`）里，
本文件只调 `tools.call_tool` 并断言返回。
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from adapters.mcp_vox import tools
from trigger import load_trigger

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_SOURCE_DIR = REPO_ROOT / "packs" / "demo-brief"
PREBUILT_PACK_DIR = REPO_ROOT / "examples" / "prebuilt-pack"

# 合法 state：命中第一条规则 greeting（ticket_status == "处理中"）
VALID_STATE = {
    "ticket_id": "WB-DEMO-0001",
    "ticket_status": "处理中",
    "days_left": 5,
    "overdue_days": 0,
    "is_overdue": False,
    "assignee_confirmed": True,
}

# 谁也不匹配的 state（各条条件规则的 when 全不满足）
MATCHLESS_STATE = {
    "ticket_id": "WB-DEMO-9999",
    "ticket_status": "已关闭",
    "days_left": 30,
    "overdue_days": 0,
    "is_overdue": False,
    "assignee_confirmed": True,
}


def _call(pack_dir, state, **extra):
    """走产品 API 调一次工具，返回 (原始 text, 解析后的 payload, isError)。"""
    args = {"pack_dir": str(pack_dir), "state": state}
    args.update(extra)
    result = tools.call_tool("vox_plan_from_state", args)
    text = result["content"][0]["text"]
    return text, json.loads(text), result["isError"]


class TestPlanFromStateHappyPath(unittest.TestCase):
    """正例：合法 state 出一条 plan。"""

    def test_returns_plan_and_hits_first_rule(self):
        _, payload, is_error = _call(PACK_SOURCE_DIR, VALID_STATE)
        self.assertIs(is_error, False)
        self.assertEqual(payload["trigger_id"], "demo-brief.ticket-reminder")
        self.assertEqual(payload["rule_id"], "greeting")
        self.assertTrue(payload["plan"], "plan 不得为空")
        self.assertEqual(payload["keys"], ["greeting_ticket"])

    def test_plan_keys_all_declared_in_pack(self):
        """plan 的 key 必须在包内（oracle = trigger.load_trigger 的 phrase_keys，非本文件自算）。"""
        trigger = load_trigger(PACK_SOURCE_DIR)
        _, payload, _ = _call(PACK_SOURCE_DIR, VALID_STATE)
        for key in payload["keys"]:
            self.assertIn(key, trigger.phrase_keys)

    def test_budget_and_shape_reported(self):
        _, payload, _ = _call(PACK_SOURCE_DIR, VALID_STATE)
        trigger = load_trigger(PACK_SOURCE_DIR)
        self.assertEqual(payload["budget_chars"], trigger.budget_chars)
        self.assertIsInstance(payload["chars"], int)
        self.assertLessEqual(payload["chars"], trigger.budget_chars)
        unit = payload["plan"][0]
        for field in ("action", "key", "rate", "variant", "slots"):
            self.assertIn(field, unit)

    def test_deterministic_for_same_state_and_turn(self):
        """同一 state + 同一 turn_id → 逐字段相同（触发器侧红线 2）。"""
        _, first, _ = _call(PACK_SOURCE_DIR, VALID_STATE, turn_id="t-1")
        _, second, _ = _call(PACK_SOURCE_DIR, VALID_STATE, turn_id="t-1")
        self.assertEqual(first, second)


class TestPlanFromStateNegatives(unittest.TestCase):
    """负例：三种非法情形都必须响亮地报错，不得静默。"""

    def test_undeclared_field_is_rejected_and_named(self):
        bad = dict(VALID_STATE)
        bad["unexpected_field"] = "x"
        text, _, is_error = _call(PACK_SOURCE_DIR, bad)
        self.assertIs(is_error, True)
        self.assertIn("unexpected_field", text)

    def test_no_fallback_and_no_match_fails_closed(self):
        """摘掉兜底规则后喂 MATCHLESS_STATE：必须抛错，不得返回空 plan。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "no-fallback"
            shutil.copytree(PACK_SOURCE_DIR, src)
            trigger_path = src / "trigger.json"
            data = json.loads(trigger_path.read_text(encoding="utf-8"))
            data["rules"] = [r for r in data["rules"] if r.get("when") is not None]
            self.assertNotIn("closing", [r["rule_id"] for r in data["rules"]])
            trigger_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            text, payload, is_error = _call(src, MATCHLESS_STATE)
        self.assertIs(is_error, True)
        self.assertIn("demo-brief.ticket-reminder", text)
        # 不得返回空 plan、不得自行挑一条兜底
        self.assertNotIn("plan", payload)

    def test_built_asset_pack_is_rejected_with_path(self):
        """铸出来的资产包没有 trigger.json → 报错并点名路径（不许含糊）。"""
        text, _, is_error = _call(PREBUILT_PACK_DIR, VALID_STATE)
        self.assertIs(is_error, True)
        self.assertIn("prebuilt-pack", text)
        self.assertIn("trigger.json", text)

    def test_state_must_be_object(self):
        text, _, is_error = _call(PACK_SOURCE_DIR, "not-an-object")
        self.assertIs(is_error, True)
        self.assertIn("state", text)


if __name__ == "__main__":
    unittest.main()
