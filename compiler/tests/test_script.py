"""
compiler.tests.test_script — 剧本源格式装载器的正例 + 负例测试

覆盖范围：
  - load_script 正例：合法剧本能装载，Script 字段与结构正确（≥4 单元、含 1 个槽位单元、
    含 1 个终态；max_retry 缺省 3；live_whitelist 允许空列表）
  - reason 保留：load_script 必须留住源格式专有的 reason（对照 core.parse_plan 会静默丢弃）
  - 负例（均断言 ScriptError + 消息含文件名/字段名/单元序号）：
      缺文件 / 非合法 JSON / 顶层非对象 / 缺必填字段 / 顶层未知字段 /
      script_version 非 1 或非整数 / units 为空或非列表 / terminal_keys 为空或非列表 /
      live_whitelist 非列表或元素非字符串 / max_retry 非整数 /
      单元非字典 / key 与 text 同现或缺失 / 单元未知字段 / key 单元带 reason /
      reason 非空字符串 / action 非法 / rate 非法 / variant 非法 / slots 非字典
"""

import json
import tempfile
import unittest
from pathlib import Path

from compiler.script import Script, ScriptError, load_script


# ---------------------------------------------------------------------------
# 辅助：在 tempfile 里自造剧本（不留样例数据在仓库中）
# ---------------------------------------------------------------------------
def _write_json(path: Path, data) -> None:
    """写 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _default_script(**overrides) -> dict:
    """构造一份合法的 script.json 字典。

    正例形状（反空转条款要求）：5 个单元、含 1 个槽位单元、含 1 个终态单元。
    """
    data = {
        "script_version": 1,
        "terminal_keys": ["closing_thank_you"],
        "live_whitelist": ["asr_low_confidence", "user_off_script"],
        "units": [
            {"key": "greeting_welcome", "rate": "normal", "variant": 1},
            {"key": "step_verify_identity", "rate": "slow", "variant": 1},
            {"key": "step_confirm_amount", "rate": "slow", "slots": {"amount": "¥500"}},
            {"text": "听不清，您能再说一遍吗", "action": "SAY_LIVE", "reason": "asr_low_confidence"},
            {"key": "closing_thank_you", "rate": "normal", "variant": 1},
        ],
    }
    data.update(overrides)
    return data


class _TmpDirTestBase(unittest.TestCase):
    """提供 tempfile 目录、剧本写入与清理。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="compiler_script_")
        self.tmp_dir = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_script(self, doc) -> None:
        """把剧本字典写成 <tmp>/script.json。"""
        _write_json(self.tmp_dir / "script.json", doc)

    def _load(self, doc) -> Script:
        """写盘并装载，返回 Script。"""
        self._write_script(doc)
        return load_script(self.tmp_dir)


# ============================================================
# 1. 正例
# ============================================================
class TestLoadScriptHappyPath(_TmpDirTestBase):
    """合法剧本必须能装载，且结构正确。"""

    def test_returns_script_with_declared_fields(self):
        """Script 必须按声明值装载，terminal_keys/live_whitelist 为元组，units 保序。"""
        script = self._load(_default_script())

        self.assertIsInstance(script, Script)
        self.assertEqual(script.script_version, 1)
        self.assertEqual(script.terminal_keys, ("closing_thank_you",))
        self.assertEqual(script.live_whitelist, ("asr_low_confidence", "user_off_script"))
        self.assertEqual(script.max_retry, 3)
        self.assertEqual(len(script.units), 5)
        self.assertEqual(script.units[0]["key"], "greeting_welcome")
        self.assertEqual(script.units[-1]["key"], "closing_thank_you")

    def test_max_retry_is_optional_and_defaults_to_three(self):
        """缺省 max_retry 时必须取 3（docs/07 §7.2）。"""
        doc = _default_script()
        doc.pop("max_retry", None)  # 显式确认默认字典不含 max_retry
        self.assertNotIn("max_retry", doc)
        self.assertEqual(self._load(doc).max_retry, 3)

    def test_explicit_max_retry_is_preserved(self):
        """显式给出 max_retry 时按声明值装载（>3 由 checks.py 的 C2b 拦，不在装载期拦）。"""
        script = self._load(_default_script(max_retry=2))
        self.assertEqual(script.max_retry, 2)

    def test_empty_live_whitelist_is_allowed(self):
        """live_whitelist 允许空列表 = 本业务禁止任何 SAY_LIVE。"""
        script = self._load(_default_script(live_whitelist=[]))
        self.assertEqual(script.live_whitelist, ())

    def test_reason_is_preserved_in_units(self):
        """units 必须保留原始字典，reason 原样留住（不转 PlanUnit）。"""
        script = self._load(_default_script())
        self.assertEqual(script.units[3]["reason"], "asr_low_confidence")
        self.assertEqual(script.units[3]["action"], "SAY_LIVE")
        self.assertEqual(script.units[3]["text"], "听不清，您能再说一遍吗")

    def test_load_script_keeps_reason_where_parse_plan_drops_it(self):
        """WHY 不用 core.parse_plan 装载剧本：它会静默丢弃源格式专有的 reason。

        reason 是 C5（降级留痕）的判据输入；丢掉 = 降级留痕被抹掉且零留痕。
        本用例把这条「跨批待办 ①」的裁定钉成回归锚点。
        """
        units = [{"text": "听不清", "action": "SAY_LIVE", "reason": "asr_low_confidence"}]
        script = self._load(_default_script(units=units))

        # 本模块必须留住 reason
        self.assertEqual(script.units[0]["reason"], "asr_low_confidence")

        # 对照：core.parse_plan 会把 reason 丢掉（PlanUnit 没有这个字段）
        from core.protocol import parse_plan
        plan = parse_plan([dict(units[0])])
        self.assertFalse(hasattr(plan[0], "reason"))

    def test_slot_unit_is_preserved(self):
        """槽位单元必须原样保留 slots。"""
        script = self._load(_default_script())
        self.assertEqual(script.units[2]["slots"], {"amount": "¥500"})

    def test_script_is_frozen(self):
        """Script 必须不可变（frozen dataclass）。"""
        script = self._load(_default_script())
        with self.assertRaises(Exception):
            script.terminal_keys = ("other",)

    def test_text_unit_without_action_loads(self):
        """text 单元不给 action 是合法的（由结构推断为 SAY_LIVE）。"""
        units = [{"text": "听不清，您再说一遍", "reason": "asr_low_confidence"}]
        script = self._load(_default_script(units=units))
        self.assertNotIn("action", script.units[0])

    def test_say_with_text_loads(self):
        """action='SAY' 且带 text **不在装载期拦**——那是 checks.py 的 C4a 判据，
        必须让检查器看到它才能报出 unreviewed_text。"""
        units = [{"action": "SAY", "text": "临时拼凑的话术", "rate": "normal"}]
        script = self._load(_default_script(units=units))
        self.assertEqual(script.units[0]["action"], "SAY")

    def test_end_unit_loads(self):
        """END 是合法的显式出口写法（仍需提供 key 或 text）。"""
        units = [{"action": "END", "key": "closing_thank_you"}]
        script = self._load(_default_script(units=units))
        self.assertEqual(script.units[0]["action"], "END")


# ============================================================
# 2. 负例：文件级
# ============================================================
class TestFileLevelErrors(_TmpDirTestBase):
    """文件缺失/损坏必须抛 ScriptError，消息含文件名。"""

    def test_missing_script_file(self):
        """缺 script.json → ScriptError，消息含 'script.json'。"""
        with self.assertRaises(ScriptError) as ctx:
            load_script(self.tmp_dir)
        self.assertIn("script.json", str(ctx.exception))

    def test_invalid_json(self):
        """script.json 不是合法 JSON → ScriptError，消息含 'script.json'。"""
        (self.tmp_dir / "script.json").write_text("{这不是JSON", encoding="utf-8")
        with self.assertRaises(ScriptError) as ctx:
            load_script(self.tmp_dir)
        msg = str(ctx.exception)
        self.assertIn("script.json", msg)
        self.assertIn("JSON", msg)

    def test_top_level_not_object(self):
        """顶层不是 JSON 对象 → ScriptError，消息含 'script.json'。"""
        self._write_script(["a", "b"])
        with self.assertRaises(ScriptError) as ctx:
            load_script(self.tmp_dir)
        self.assertIn("script.json", str(ctx.exception))


# ============================================================
# 3. 负例：顶层字段
# ============================================================
class TestTopLevelRequiredFields(_TmpDirTestBase):
    """缺必填字段必须抛 ScriptError，消息含字段名。"""

    def _expect_missing(self, field: str) -> None:
        doc = _default_script()
        doc.pop(field)
        with self.assertRaises(ScriptError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn(field, msg)
        self.assertIn("script.json", msg)

    def test_missing_terminal_keys(self):
        self._expect_missing("terminal_keys")

    def test_missing_units(self):
        self._expect_missing("units")

    def test_missing_script_version(self):
        self._expect_missing("script_version")

    def test_missing_live_whitelist(self):
        self._expect_missing("live_whitelist")


class TestTopLevelTypes(_TmpDirTestBase):
    """类型不对或取值越界必须抛 ScriptError，消息含字段名与实际值。"""

    def test_script_version_two_rejected(self):
        """script_version: 2 → ScriptError，消息含 'script_version'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(script_version=2))
        msg = str(ctx.exception)
        self.assertIn("script_version", msg)
        self.assertIn("2", msg)

    def test_script_version_string_rejected(self):
        """script_version: "1"（字符串）→ ScriptError，不算整数。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(script_version="1"))
        self.assertIn("script_version", str(ctx.exception))

    def test_script_version_bool_rejected(self):
        """script_version: true（bool）→ ScriptError，bool 不是本格式的合法整数。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(script_version=True))
        self.assertIn("script_version", str(ctx.exception))

    def test_units_empty_list_rejected(self):
        """units 为空列表 → ScriptError，消息含 'units'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[]))
        self.assertIn("units", str(ctx.exception))

    def test_units_not_a_list_rejected(self):
        """units 非列表 → ScriptError，消息含 'units'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units="not-a-list"))
        self.assertIn("units", str(ctx.exception))

    def test_terminal_keys_empty_rejected(self):
        """terminal_keys 为空列表 → ScriptError（剧本必须声明出口）。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(terminal_keys=[]))
        self.assertIn("terminal_keys", str(ctx.exception))

    def test_terminal_keys_not_a_list_rejected(self):
        """terminal_keys 非列表 → ScriptError，消息含 'terminal_keys'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(terminal_keys="closing_thank_you"))
        self.assertIn("terminal_keys", str(ctx.exception))

    def test_terminal_keys_empty_string_element_rejected(self):
        """terminal_keys 含空字符串 → ScriptError，消息含 'terminal_keys'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(terminal_keys=["closing_thank_you", ""]))
        self.assertIn("terminal_keys", str(ctx.exception))

    def test_live_whitelist_not_a_list_rejected(self):
        """live_whitelist 非列表 → ScriptError（空列表合法，非列表不合法）。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(live_whitelist="asr_low_confidence"))
        self.assertIn("live_whitelist", str(ctx.exception))

    def test_live_whitelist_non_string_element_rejected(self):
        """live_whitelist 含非字符串元素 → ScriptError，消息含 'live_whitelist'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(live_whitelist=[123]))
        self.assertIn("live_whitelist", str(ctx.exception))

    def test_max_retry_string_rejected(self):
        """max_retry: "3"（字符串）→ ScriptError，消息含 'max_retry'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(max_retry="3"))
        self.assertIn("max_retry", str(ctx.exception))

    def test_max_retry_bool_rejected(self):
        """max_retry: true（bool）→ ScriptError，bool 不是合法整数。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(max_retry=True))
        self.assertIn("max_retry", str(ctx.exception))

    def test_unknown_top_level_field_rejected(self):
        """顶层未知字段（typo: termial_keys）→ ScriptError，消息含该字段名。"""
        doc = _default_script()
        doc["termial_keys"] = ["closing_thank_you"]
        with self.assertRaises(ScriptError) as ctx:
            self._load(doc)
        msg = str(ctx.exception)
        self.assertIn("termial_keys", msg)
        self.assertIn("未知字段", msg)


# ============================================================
# 4. 负例：单元级
# ============================================================
class TestUnitRules(_TmpDirTestBase):
    """单元校验必须抛 ScriptError，消息含单元序号与字段名。"""

    def test_unit_not_a_dict(self):
        """单元不是字典 → ScriptError，消息含 'units[1]'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=["not-a-dict"]))
        msg = str(ctx.exception)
        self.assertIn("units[1]", msg)

    def test_unit_missing_both_key_and_text(self):
        """单元既无 key 又无 text → ScriptError，消息含序号与两个字段名。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[{"rate": "normal"}]))
        msg = str(ctx.exception)
        self.assertIn("units[1]", msg)
        self.assertIn("key", msg)
        self.assertIn("text", msg)

    def test_unit_with_both_key_and_text(self):
        """单元同时带 key 与 text → ScriptError（字段冲突）。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(
                _default_script(units=[{"key": "greeting_welcome", "text": "您好"}])
            )
        msg = str(ctx.exception)
        self.assertIn("units[1]", msg)
        self.assertIn("key", msg)
        self.assertIn("text", msg)

    def test_unit_unknown_field_rejected(self):
        """单元未知字段（typo: reson）→ ScriptError，消息含字段名与序号。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(
                _default_script(
                    units=[{"text": "听不清", "action": "SAY_LIVE", "reson": "asr_low_confidence"}]
                )
            )
        msg = str(ctx.exception)
        self.assertIn("reson", msg)
        self.assertIn("units[1]", msg)

    def test_reason_on_keyed_unit_rejected(self):
        """key 单元带 reason → ScriptError（key 单元不应有降级理由）。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(
                _default_script(
                    units=[{"key": "greeting_welcome", "reason": "asr_low_confidence"}]
                )
            )
        msg = str(ctx.exception)
        self.assertIn("reason", msg)
        self.assertIn("units[1]", msg)

    def test_reason_on_say_text_unit_rejected(self):
        """action='SAY' 的 text 单元带 reason → ScriptError，消息含 action 名。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(
                _default_script(
                    units=[{"action": "SAY", "text": "听不清", "reason": "asr_low_confidence"}]
                )
            )
        msg = str(ctx.exception)
        self.assertIn("reason", msg)
        self.assertIn("SAY", msg)

    def test_reason_on_third_unit_reports_third_index(self):
        """第 3 个单元出错 → 消息必须含 'units[3]'（序号是 1-based）。"""
        units = [
            {"key": "greeting_welcome", "rate": "normal"},
            {"key": "step_verify_identity", "rate": "slow"},
            {"key": "step_confirm_amount", "rate": "slow", "reason": "asr_low_confidence"},
        ]
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=units))
        self.assertIn("units[3]", str(ctx.exception))

    def test_reason_empty_string_rejected(self):
        """reason 为空字符串 → ScriptError，消息含 'reason'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(
                _default_script(units=[{"text": "听不清", "action": "SAY_LIVE", "reason": ""}])
            )
        self.assertIn("reason", str(ctx.exception))

    def test_reason_non_string_rejected(self):
        """reason 非字符串 → ScriptError，消息含 'reason'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(
                _default_script(units=[{"text": "听不清", "action": "SAY_LIVE", "reason": 123}])
            )
        self.assertIn("reason", str(ctx.exception))

    def test_action_unknown_primitive_rejected(self):
        """action 不在 core 的原语白名单里 → ScriptError，消息含该原语名。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[{"key": "greeting_welcome", "action": "PLAY"}]))
        msg = str(ctx.exception)
        self.assertIn("PLAY", msg)
        self.assertIn("action", msg)

    def test_rate_unknown_rejected(self):
        """rate 含未知档位 → ScriptError，消息含该档位值（不回落）。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[{"key": "greeting_welcome", "rate": "turbo"}]))
        msg = str(ctx.exception)
        self.assertIn("turbo", msg)
        self.assertIn("rate", msg)

    def test_variant_invalid_rejected(self):
        """variant 既不是整数也不是 'auto' → ScriptError，消息含 'variant'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[{"key": "greeting_welcome", "variant": "first"}]))
        self.assertIn("variant", str(ctx.exception))

    def test_variant_float_rejected(self):
        """variant 为浮点 → ScriptError，消息含 'variant'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[{"key": "greeting_welcome", "variant": 1.5}]))
        self.assertIn("variant", str(ctx.exception))

    def test_slots_not_a_dict_rejected(self):
        """slots 非字典 → ScriptError，消息含 'slots'。"""
        with self.assertRaises(ScriptError) as ctx:
            self._load(_default_script(units=[{"key": "step_confirm_amount", "slots": "¥500"}]))
        self.assertIn("slots", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
