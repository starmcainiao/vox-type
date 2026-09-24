"""
rules.tests.test_ruleset — 五类规则集的结构测试

覆盖范围：
  - 规则集结构（5 条规则，id 集合正确）
  - 正例可被 core.protocol.parse_plan 解析
  - 五个反例均可被 parse_plan 解析（格式合法但违反规则）
  - 反例到规则的映射表非空、一一对应
  - skill.md 包含五条规则 id 和禁止关键词
  - keylist.json 包含 ≥6 个 key
"""

import json
import os
import unittest

from core.protocol import parse_plan

# 规则集与示例的基础路径
_RULES_DIR = os.path.join(os.path.dirname(__file__), "..")
_RULESET_PATH = os.path.join(_RULES_DIR, "ruleset.v1.json")
_SKILL_PATH = os.path.join(_RULES_DIR, "skill.md")
_KEYLIST_PATH = os.path.join(_RULES_DIR, "examples", "keylist.json")
_OK_INTAKE_PATH = os.path.join(_RULES_DIR, "examples", "ok_intake.json")

# 反例文件 → 规则 id 的期望映射
_VIOLATION_MAP = {
    "bad_r1_unknown_key.json": "R-1",
    "bad_r2_free_text_slot.json": "R-2",
    "bad_r3_deadlock.json": "R-3",
    "bad_r4_wrong_rate.json": "R-4",
    "bad_r5_silent_fallback.json": "R-5",
}


def _load_json(path):
    """加载并返回 JSON 文件内容。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_text(path):
    """加载并返回文本文件内容。"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================
# 1. 规则集结构测试
# ============================================================
class TestRulesetStructure(unittest.TestCase):
    """ruleset.v1.json 必须包含恰好五条规则，id 集合正确。"""

    def test_ruleset_loadable(self):
        """规则集文件可解析为 JSON。"""
        data = _load_json(_RULESET_PATH)
        self.assertIsInstance(data, dict)

    def test_ruleset_version(self):
        """规则集版本号为 v1。"""
        data = _load_json(_RULESET_PATH)
        self.assertEqual(data.get("ruleset_version"), "v1")

    def test_rules_count(self):
        """恰好包含五条规则。"""
        data = _load_json(_RULESET_PATH)
        rules = data.get("rules", [])
        self.assertEqual(len(rules), 5)

    def test_rule_ids(self):
        """五条规则的 id 集合恰好是 {R-1, R-2, R-3, R-4, R-5}。"""
        data = _load_json(_RULESET_PATH)
        ids = {r["id"] for r in data["rules"]}
        self.assertEqual(ids, {"R-1", "R-2", "R-3", "R-4", "R-5"})

    def test_rule_ids_order(self):
        """五条规则按顺序出现（R-1 在 R-2 之前，以此类推）。"""
        data = _load_json(_RULESET_PATH)
        ids = [r["id"] for r in data["rules"]]
        expected_order = ["R-1", "R-2", "R-3", "R-4", "R-5"]
        for a, b in zip(expected_order, ids):
            self.assertEqual(a, b)

    def test_each_rule_has_required_fields(self):
        """每条规则必须包含 id, name, statement, judge, examples。"""
        data = _load_json(_RULESET_PATH)
        for rule in data["rules"]:
            self.assertIn("id", rule, f"规则 {rule.get('id', '?')} 缺少 id")
            self.assertIn("name", rule, f"规则 {rule.get('id', '?')} 缺少 name")
            self.assertIn("statement", rule, f"规则 {rule.get('id', '?')} 缺少 statement")
            self.assertIn("judge", rule, f"规则 {rule.get('id', '?')} 缺少 judge")
            self.assertIn("examples", rule, f"规则 {rule.get('id', '?')} 缺少 examples")

    def test_each_rule_has_ok_example(self):
        """每条规则的 examples 中必须包含 ok 路径。"""
        data = _load_json(_RULESET_PATH)
        for rule in data["rules"]:
            examples = rule.get("examples", {})
            self.assertIn("ok", examples, f"规则 {rule['id']} 缺少 ok 示例")


# ============================================================
# 2. 正例可解析测试
# ============================================================
class TestOkIntake(unittest.TestCase):
    """ok_intake.json 必须能被 core.protocol.parse_plan 解析。"""

    def test_ok_intake_parses(self):
        """正例 plan 可被 parse_plan 解析。"""
        raw = _load_json(_OK_INTAKE_PATH)
        plan = parse_plan(raw)
        self.assertIsInstance(plan, list)

    def test_ok_intake_has_enough_units(self):
        """正例 plan 至少包含 4 个单元。"""
        raw = _load_json(_OK_INTAKE_PATH)
        plan = parse_plan(raw)
        self.assertGreaterEqual(len(plan), 4)

    def test_ok_intake_has_slot_unit(self):
        """正例 plan 包含至少一个带 slots 的单元。"""
        raw = _load_json(_OK_INTAKE_PATH)
        plan = parse_plan(raw)
        has_slots = any(len(u.slots) > 0 for u in plan)
        self.assertTrue(has_slots, "正例 plan 中未找到带 slots 的单元")

    def test_ok_intake_keys_exist_in_keylist(self):
        """正例 plan 中所有 key 都在 keylist 中。"""
        raw = _load_json(_OK_INTAKE_PATH)
        keylist = _load_json(_KEYLIST_PATH)
        allowed_keys = set(keylist.get("keys", []))
        plan = parse_plan(raw)
        for unit in plan:
            if unit.key is not None:
                self.assertIn(
                    unit.key,
                    allowed_keys,
                    f"正例 key '{unit.key}' 不在 keylist 中",
                )


# ============================================================
# 3. 反例可解析测试
# ============================================================
class TestViolationExamples(unittest.TestCase):
    """五个反例文件必须都能被 core.protocol.parse_plan 解析。

    核心定义：反例 = 格式合法但违反规则。若解析失败则反例无意义。
    """

    def test_all_violation_files_parse(self):
        """所有反例文件都能被 parse_plan 解析。"""
        for filename in _VIOLATION_MAP:
            path = os.path.join(_RULES_DIR, "examples", filename)
            with self.subTest(filename=filename):
                raw = _load_json(path)
                plan = parse_plan(raw)
                self.assertIsInstance(plan, list)
                self.assertGreater(len(plan), 0, f"{filename} 解析后为空")


# ============================================================
# 4. 反例到规则的映射测试
# ============================================================
class TestViolationMapping(unittest.TestCase):
    """反例文件到规则 id 的映射表非空、一一对应。"""

    def test_violation_map_keys_match_files(self):
        """映射表中的文件名都在 examples/ 目录中存在。"""
        for filename in _VIOLATION_MAP:
            path = os.path.join(_RULES_DIR, "examples", filename)
            self.assertTrue(
                os.path.exists(path),
                f"反例文件不存在: {filename}",
            )

    def test_violation_map_rules_match_ruleset(self):
        """映射表中的规则 id 都在规则集中存在。"""
        data = _load_json(_RULESET_PATH)
        ruleset_ids = {r["id"] for r in data["rules"]}
        for filename, rule_id in _VIOLATION_MAP.items():
            self.assertIn(
                rule_id,
                ruleset_ids,
                f"{filename} 映射的规则 {rule_id} 不在规则集中",
            )

    def test_violation_map_one_to_one(self):
        """映射表是双射：5 个文件映射到 5 个不同的规则 id。"""
        rule_ids = list(_VIOLATION_MAP.values())
        self.assertEqual(len(rule_ids), 5, "映射表不是 5 条")
        self.assertEqual(len(set(rule_ids)), 5, "映射表不是一一对应（有重复 rule_id）")
        file_count = len(_VIOLATION_MAP)
        self.assertEqual(file_count, 5, "映射表不是 5 个文件")


# ============================================================
# 5. skill.md 内容测试
# ============================================================
class TestSkillContent(unittest.TestCase):
    """skill.md 必须包含五条规则 id 和禁止关键词。"""

    def setUp(self):
        self.content = _load_text(_SKILL_PATH)

    def test_contains_all_rule_ids(self):
        """包含五条规则的 id。"""
        for rule_id in ["R-1", "R-2", "R-3", "R-4", "R-5"]:
            self.assertIn(
                rule_id,
                self.content,
                f"skill.md 中未找到规则 id {rule_id}",
            )

    def test_contains_prohibit_key_fabrication(self):
        """包含禁止编造 key 的表述。"""
        # 检查中英文多种表述
        keywords = ["禁止编造", "禁止自造", "禁止伪造", "不得编造", "不得自造"]
        found = any(kw in self.content for kw in keywords)
        self.assertTrue(found, "skill.md 中未找到禁止编造 key 的表述")

    def test_contains_prohibit_silent_fallback(self):
        """包含禁止静默降级的表述。"""
        keywords = ["禁止静默降级", "禁止降级", "不得静默降级", "不得降级"]
        found = any(kw in self.content for kw in keywords)
        self.assertTrue(found, "skill.md 中未找到禁止静默降级的表述")


# ============================================================
# 6. keylist.json 测试
# ============================================================
class TestKeylist(unittest.TestCase):
    """keylist.json 必须包含至少 6 个 key。"""

    def test_keylist_loadable(self):
        """keylist 文件可解析为 JSON。"""
        data = _load_json(_KEYLIST_PATH)
        self.assertIsInstance(data, dict)

    def test_keylist_has_enough_keys(self):
        """keylist 包含至少 6 个 key。"""
        data = _load_json(_KEYLIST_PATH)
        keys = data.get("keys", [])
        self.assertGreaterEqual(len(keys), 6, f"keylist 只有 {len(keys)} 个 key，需要 ≥6")

    def test_keylist_keys_are_strings(self):
        """所有 key 都是字符串。"""
        data = _load_json(_KEYLIST_PATH)
        for key in data.get("keys", []):
            self.assertIsInstance(key, str, f"key '{key}' 不是字符串")


if __name__ == "__main__":
    unittest.main()
