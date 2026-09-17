"""
compiler.tests.test_checks — 五项机器判据（C1…C5）的正例 + 负例测试

覆盖范围（每条判据都有能失败的负例，且断言 code 与消息里的关键值）：
  - C1a no_exit / C1b+C3a orphan_branch
  - C2a retry_unbounded / C2b max_retry_exceeds_rule
  - C3a orphan_branch / C3b key_not_in_library / C3c key_not_prebaked
  - C4a unreviewed_text / C4b key_text_conflict / C4c reason_on_keyed_unit
  - C5a live_without_reason / C5b reason_not_whitelisted
  - skipped 纪律（pack=None → C3c 跳过且可见，不得默认判过）
  - 判定顺序 C1 → C2 → C3 → C4 → C5
  - docs/07 §7.5 的三条回归检验（rules/examples/ 现成单元按 §7.2 包成源格式）

测试纪律：全部通过产品 API（load_source / load_script / check_properties）走通，
在测试内**不**复制判据逻辑、不自造 Violation 判定；源与包都在 tempfile 里自造。
"""

import json
import tempfile
import unittest
from pathlib import Path

from assets.pack import AssetEntry, AssetPack
from compiler.checks import CheckResult, Violation, check_properties
from compiler.script import Script, load_script
from compiler.source import PackSource, load_source


# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "rules" / "examples"
KEYLIST_PATH = EXAMPLES_DIR / "keylist.json"

# keylist.json 的 7 个 key（验收 2：话术库取这 7 个）
DEFAULT_KEYS = [
    "greeting_welcome",
    "step_verify_identity",
    "step_confirm_amount",
    "step_read_address",
    "step_final_summary",
    "closing_thank_you",
    "error_retry",
]

LIVE_REASON = "asr_low_confidence"


# ---------------------------------------------------------------------------
# 辅助：自造源、包与剧本（全部走产品 API）
# ---------------------------------------------------------------------------
def _write_json(path: Path, data) -> None:
    """写 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _default_pack_json() -> dict:
    """一份合法的 pack.json。"""
    return {
        "pack_id": "repair",
        "pack_version": "1",
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": "Tingting",
        "model_version": "macos-say",
        "rates": ["normal", "slow"],
    }


def _make_source_dir(root: Path, keys) -> None:
    """在 root 下写出 pack.json + phrases.json（每个 key 一条「一句」话术）。"""
    _write_json(root / "pack.json", _default_pack_json())
    _write_json(
        root / "phrases.json",
        {"phrases": [{"key": k, "variants": [f"演示话术{k}"]} for k in keys]},
    )


def _make_pack(keys) -> AssetPack:
    """造一份已预铸资产包：每个 key 一条资产（check_properties 只读 assets[].key）。"""
    assets = [
        AssetEntry(
            key=k,
            part_index=0,
            rate_key="normal",
            variant=1,
            text=f"演示话术{k}",
            fingerprint="0" * 16,
            path=f"audio/{k}.wav",
            duration_ms=1000,
        )
        for k in keys
    ]
    return AssetPack(
        pack_id="repair",
        pack_version="1",
        protocol_version="0.1",
        ruleset_version="v1",
        voice="Tingting",
        model_version="macos-say",
        created_at="2026-09-17T00:00:00+08:00",
        assets=assets,
    )


def _example_units(name: str):
    """读 rules/examples/ 下的现成单元（只读，不改仓库）。"""
    with open(EXAMPLES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _keylist():
    """读 keylist.json 的 7 个 key。"""
    with open(KEYLIST_PATH, encoding="utf-8") as f:
        return json.load(f)["keys"]


def _k(key, **extra) -> dict:
    """构造一个 key 单元。"""
    unit = {"key": key, "rate": "normal"}
    unit.update(extra)
    return unit


def _t(text, **extra) -> dict:
    """构造一个 SAY_LIVE 自由文本单元。"""
    unit = {"text": text, "action": "SAY_LIVE"}
    unit.update(extra)
    return unit


def _codes(result: CheckResult):
    """取出违规的判据码序列（保序，用于断言判定顺序）。"""
    return [v.code for v in result.violations]


def _violations(result: CheckResult, code: str):
    """取出某判据码的全部违规。"""
    return [v for v in result.violations if v.code == code]


class _TmpCheckBase(unittest.TestCase):
    """提供 tempfile 目录 + 话术库 + 资产包 + 剧本装载（全部走产品 API）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="compiler_checks_")
        self.tmp_dir = Path(self._tmp)
        self._src_n = 0

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _source(self, keys=None) -> PackSource:
        """用 load_source 造一份已审核话术库（每次调用独立子目录，可多份）。"""
        ks = list(DEFAULT_KEYS) if keys is None else list(keys)
        d = self.tmp_dir / f"src_{self._src_n}"
        self._src_n += 1
        d.mkdir()
        _make_source_dir(d, ks)
        return load_source(d)

    def _pack(self, keys=None) -> AssetPack:
        """造一份已预铸资产包（缺省覆盖话术库同样的 key 集合，避免误报）。"""
        ks = list(DEFAULT_KEYS) if keys is None else list(keys)
        return _make_pack(ks)

    def _script(
        self,
        units,
        terminal_keys=("closing_thank_you",),
        live_whitelist=(LIVE_REASON,),
        max_retry=3,
        script_version=1,
    ) -> Script:
        """写盘并走 load_script 装载（不得在测试里自造 Script 绕过装载校验）。"""
        doc = {
            "script_version": script_version,
            "terminal_keys": list(terminal_keys),
            "live_whitelist": list(live_whitelist),
            "max_retry": max_retry,
            "units": units,
        }
        _write_json(self.tmp_dir / "script.json", doc)
        return load_script(self.tmp_dir)

    def _run(self, script, pack=None, source=None) -> CheckResult:
        """跑一遍 check_properties（source 缺省取 DEFAULT_KEYS 话术库）。"""
        return check_properties(script, self._source() if source is None else source, pack)


# ============================================================
# 1. docs/07 §7.5 的三条回归检验（本卡最硬的一条）
# ============================================================
class TestRegressionExamples(_TmpCheckBase):
    """把 rules/examples/ 的现成单元按 §7.2 包成源格式喂进检查器。

    话术库取 keylist.json 的 7 个 key、临时包覆盖同样 7 个 key，
    避免 key_not_prebaked 误报。
    """

    def test_ok_intake_zero_violations(self):
        """ok_intake.json（T02 已验收的合规正例，不含 END、不含 SAY_LIVE）→ 零违规。"""
        keys = _keylist()
        units = _example_units("ok_intake.json")
        script = self._script(units, terminal_keys=("closing_thank_you",), live_whitelist=())

        result = check_properties(script, self._source(keys), self._pack(keys))

        self.assertEqual(result.violations, ())
        self.assertEqual(result.skipped, ())

    def test_bad_r3_deadlock_reports_no_exit_and_three_live_without_reason(self):
        """bad_r3_deadlock.json → no_exit + 3 条 live_without_reason，不得有 retry_unbounded。"""
        keys = _keylist()
        units = _example_units("bad_r3_deadlock.json")
        script = self._script(units, terminal_keys=("closing_thank_you",), live_whitelist=())

        result = check_properties(script, self._source(keys), self._pack(keys))

        codes = _codes(result)
        self.assertIn("no_exit", codes)

        without_reason = _violations(result, "live_without_reason")
        self.assertEqual(len(without_reason), 3)
        # 序号 2/3/4 是自由文本
        self.assertEqual({v.unit_index for v in without_reason}, {2, 3, 4})

        # 三句文本各不相同，不构成「同一个 key 连续跑」
        self.assertNotIn("retry_unbounded", codes)

    def test_bad_r5_silent_fallback_reports_unreviewed_text_and_no_exit(self):
        """bad_r5_silent_fallback.json → unreviewed_text + no_exit，不得有 live_without_reason。"""
        keys = _keylist()
        units = _example_units("bad_r5_silent_fallback.json")
        script = self._script(units, terminal_keys=("closing_thank_you",), live_whitelist=())

        result = check_properties(script, self._source(keys), self._pack(keys))

        codes = _codes(result)
        self.assertIn("unreviewed_text", codes)
        self.assertIn("no_exit", codes)

        # 该单元动作是 SAY 不是 SAY_LIVE，走 C4a 而不是 C5a
        self.assertNotIn("live_without_reason", codes)


# ============================================================
# 2. C1 有出口
# ============================================================
class TestC1Exit(_TmpCheckBase):
    """C1a no_exit / C1b orphan_branch。"""

    def test_c1a_last_unit_not_terminal_reports_no_exit(self):
        """末单元既不是 END 也不在 terminal_keys → no_exit，消息含末单元序号与 key。"""
        units = [
            _k("greeting_welcome"),
            _k("step_verify_identity"),
            _k("step_confirm_amount"),
            _k("step_final_summary"),
            _k("error_retry"),
        ]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "no_exit")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 5)
        self.assertEqual(v[0].key, "error_retry")
        msg = v[0].message
        self.assertIn("no_exit", msg)
        self.assertIn("error_retry", msg)
        self.assertIn("5", msg)
        self.assertIn("terminal_keys", msg)

    def test_c1a_last_unit_in_terminal_keys_passes(self):
        """末单元的 key ∈ terminal_keys → 不报 no_exit（用收尾话术表达出口）。"""
        units = [_k("greeting_welcome"), _k("closing_thank_you")]
        result = self._run(self._script(units), self._pack())
        self.assertNotIn("no_exit", _codes(result))

    def test_c1a_end_action_is_terminal(self):
        """action == 'END' 是合法的显式出口写法 → 不报 no_exit。"""
        units = [_k("greeting_welcome"), {"action": "END", "key": "closing_thank_you"}]
        result = self._run(self._script(units), self._pack())
        self.assertNotIn("no_exit", _codes(result))

    def test_c1b_unit_after_terminal_reports_orphan_branch(self):
        """终态之后的单元 → orphan_branch，消息含序号；C3 不重复报。"""
        units = [_k("greeting_welcome"), _k("closing_thank_you"), _k("step_final_summary")]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "orphan_branch")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 3)
        self.assertEqual(v[0].key, "step_final_summary")
        msg = v[0].message
        self.assertIn("orphan_branch", msg)
        self.assertIn("3", msg)
        self.assertIn("step_final_summary", msg)

        # 归 C1 报一次，C3 不重复报
        self.assertEqual(_codes(result).count("orphan_branch"), 1)
        # 末单元不是终态 → C1a 与 C1b 各报各的（两个不同问题，不互相挤掉）
        self.assertIn("no_exit", _codes(result))

    def test_c1b_two_units_after_terminal_report_both(self):
        """终态之后有两个单元 → 各报一条 orphan_branch（各自含自己的序号）。"""
        units = [_k("greeting_welcome"), _k("closing_thank_you"), _k("step_final_summary"), _k("error_retry")]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "orphan_branch")
        self.assertEqual({x.unit_index for x in v}, {3, 4})
        self.assertIn("3", v[0].message)
        self.assertIn("4", v[1].message)


# ============================================================
# 3. C2 追问有上限
# ============================================================
class TestC2Retry(_TmpCheckBase):
    """C2a retry_unbounded / C2b max_retry_exceeds_rule。"""

    def test_c2a_four_repeats_report_retry_unbounded(self):
        """同一 key 连续 4 次 → retry_unbounded，消息含 key、4 与上限 3。"""
        units = [_k("greeting_welcome")] + [_k("error_retry")] * 4
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "retry_unbounded")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, "error_retry")
        self.assertEqual(v[0].unit_index, 5)
        msg = v[0].message
        self.assertIn("retry_unbounded", msg)
        self.assertIn("error_retry", msg)
        self.assertIn("4", msg)
        self.assertIn("3", msg)

    def test_c2a_three_repeats_is_the_allowed_boundary(self):
        """同一 key 连续 3 次 → 不报（3 是允许的，这是边界）。"""
        units = [_k("greeting_welcome")] + [_k("error_retry")] * 3
        result = self._run(self._script(units), self._pack())
        self.assertNotIn("retry_unbounded", _codes(result))

    def test_c2a_uses_declared_max_retry_as_limit(self):
        """声明 max_retry=2 时，连续 3 次也要报，消息含实际值 3 与上限 2。"""
        units = [_k("greeting_welcome")] + [_k("error_retry")] * 3
        result = self._run(self._script(units, max_retry=2), self._pack())

        v = _violations(result, "retry_unbounded")
        self.assertEqual(len(v), 1)
        msg = v[0].message
        self.assertIn("error_retry", msg)
        self.assertIn("3", msg)
        self.assertIn("2", msg)

    def test_c2a_non_key_unit_breaks_the_run(self):
        """text 单元打断连续同 key 的跑：2 + 3 两段都不超上限 → 不报。"""
        units = [
            _k("error_retry"),
            _k("error_retry"),
            _t("听不清，您再说一遍", reason=LIVE_REASON),
            _k("error_retry"),
            _k("error_retry"),
            _k("error_retry"),
        ]
        result = self._run(self._script(units), self._pack())
        self.assertNotIn("retry_unbounded", _codes(result))

    def test_c2a_second_run_reported_separately(self):
        """第二段跑也超长 → 单独报一条，序号落在第二段末尾。"""
        units = [
            _k("error_retry"), _k("error_retry"),
            _t("听不清，您再说一遍", reason=LIVE_REASON),
            _k("step_read_address"), _k("step_read_address"), _k("step_read_address"), _k("step_read_address"),
        ]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "retry_unbounded")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, "step_read_address")
        self.assertEqual(v[0].unit_index, 7)
        self.assertIn("4", v[0].message)

    def test_c2b_declared_max_retry_above_rule(self):
        """max_retry: 5 → max_retry_exceeds_rule，消息含声明值 5 与规则上限 3。"""
        result = self._run(self._script([_k("closing_thank_you")], max_retry=5), self._pack())

        v = _violations(result, "max_retry_exceeds_rule")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 0)
        msg = v[0].message
        self.assertIn("max_retry_exceeds_rule", msg)
        self.assertIn("5", msg)
        self.assertIn("3", msg)

    def test_c2b_three_is_allowed(self):
        """max_retry: 3 → 不报（3 是规则允许的上限）。"""
        result = self._run(self._script([_k("closing_thank_you")], max_retry=3), self._pack())
        self.assertNotIn("max_retry_exceeds_rule", _codes(result))

    def test_c2a_and_c2b_can_both_fire(self):
        """max_retry: 5 且连续 6 次 → C2a 与 C2b 同时报（按 C2a → C2b 的顺序）。"""
        units = [_k("error_retry")] * 6
        result = self._run(self._script(units, max_retry=5), self._pack())
        codes = _codes(result)
        self.assertIn("retry_unbounded", codes)
        self.assertIn("max_retry_exceeds_rule", codes)


# ============================================================
# 4. C3 全 key 可达且都被预铸
# ============================================================
class TestC3Reachability(_TmpCheckBase):
    """C3a orphan_branch / C3b key_not_in_library / C3c key_not_prebaked。"""

    def test_c3a_key_after_terminal_reports_orphan_branch(self):
        """终态之后的 key → orphan_branch，消息含序号与 key。"""
        units = [_k("greeting_welcome"), _k("closing_thank_you"), _k("step_read_address")]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "orphan_branch")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 3)
        self.assertEqual(v[0].key, "step_read_address")
        msg = v[0].message
        self.assertIn("step_read_address", msg)
        self.assertIn("3", msg)

    def test_c3b_key_not_in_library(self):
        """引用话术库里没有的 key → key_not_in_library，消息含该 key。"""
        units = [_k("nonexistent_key"), _k("closing_thank_you")]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "key_not_in_library")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, "nonexistent_key")
        self.assertEqual(v[0].unit_index, 1)
        msg = v[0].message
        self.assertIn("key_not_in_library", msg)
        self.assertIn("nonexistent_key", msg)
        self.assertIn("1", msg)

    def test_c3b_reports_every_missing_key(self):
        """两个未知 key → 各报一条，序号与 key 各自对齐。"""
        units = [_k("unknown_a"), _k("unknown_b"), _k("closing_thank_you")]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "key_not_in_library")
        self.assertEqual({x.key for x in v}, {"unknown_a", "unknown_b"})
        self.assertEqual({x.unit_index for x in v}, {1, 2})

    def test_c3c_key_not_prebaked(self):
        """key 在话术库与包的交集外（库里在、包里不在）→ key_not_prebaked。"""
        keys = [k for k in DEFAULT_KEYS if k != "step_read_address"]
        units = [_k("greeting_welcome"), _k("step_read_address"), _k("closing_thank_you")]

        result = check_properties(
            self._script(units), self._source(DEFAULT_KEYS), self._pack(keys)
        )

        v = _violations(result, "key_not_prebaked")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, "step_read_address")
        self.assertEqual(v[0].unit_index, 2)
        msg = v[0].message
        self.assertIn("key_not_prebaked", msg)
        self.assertIn("step_read_address", msg)
        # 该 key 在话术库里，所以不该有 key_not_in_library
        self.assertNotIn("key_not_in_library", _codes(result))

    def test_c3c_skipped_when_pack_is_none(self):
        """pack=None → C3c 跳过：skipped 含 key_not_prebaked，violations 里没有它。"""
        units = [_k("greeting_welcome"), _k("closing_thank_you")]
        result = check_properties(self._script(units), self._source(), None)

        self.assertEqual(result.skipped, ("key_not_prebaked",))
        self.assertNotIn("key_not_prebaked", _codes(result))
        # skipped 非空这件事在返回值里可见
        self.assertTrue(result.skipped)

    def test_c3c_skipped_does_not_hide_other_violations(self):
        """pack=None 时 C3b 仍然要报（跳过的只是 C3c，不是整条 C3）。"""
        units = [_k("nonexistent_key"), _k("closing_thank_you")]
        result = check_properties(self._script(units), self._source(), None)

        self.assertIn("key_not_in_library", _codes(result))
        self.assertNotIn("key_not_prebaked", _codes(result))
        self.assertEqual(result.skipped, ("key_not_prebaked",))

    def test_skipped_empty_when_pack_given(self):
        """给了资产包 → skipped 为空。"""
        result = self._run(self._script([_k("closing_thank_you")]), self._pack())
        self.assertEqual(result.skipped, ())


# ============================================================
# 5. C4 不夹带未审核文本
# ============================================================
class TestC4Unreviewed(_TmpCheckBase):
    """C4a unreviewed_text / C4b key_text_conflict / C4c reason_on_keyed_unit。"""

    def test_c4a_say_with_text_reports_unreviewed_text(self):
        """action='SAY' 且带 text → unreviewed_text，消息含序号与 text。"""
        units = [_k("greeting_welcome"), {"action": "SAY", "text": "临时拼凑的话术", "rate": "normal"}]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "unreviewed_text")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 2)
        msg = v[0].message
        self.assertIn("unreviewed_text", msg)
        self.assertIn("临时拼凑的话术", msg)
        self.assertIn("SAY", msg)
        # 伪装命中走 C4a，不走 C5a
        self.assertNotIn("live_without_reason", _codes(result))

    def test_c4a_say_live_with_text_passes_c4a(self):
        """action='SAY_LIVE' 且带 text + 合法 reason → C4a 不报。"""
        units = [_k("greeting_welcome"), _t("听不清，您再说一遍", reason=LIVE_REASON)]
        result = self._run(self._script(units), self._pack())
        self.assertNotIn("unreviewed_text", _codes(result))

    def test_c4a_inferred_say_live_goes_to_c5_instead(self):
        """text 单元不给 action → 推断为 SAY_LIVE → 走 C5 不走 C4a。"""
        units = [_k("greeting_welcome"), {"text": "听不清，您再说一遍"}]
        result = self._run(self._script(units), self._pack())

        codes = _codes(result)
        self.assertNotIn("unreviewed_text", codes)
        self.assertIn("live_without_reason", codes)

    def test_c4b_key_and_text_together_reports_key_text_conflict(self):
        """C4b 供不经 load_script 直接喂字典的调用方（load_script 已在装载期拦）。"""
        script = Script(
            1, ("closing_thank_you",), (LIVE_REASON,), 3,
            ({"key": "greeting_welcome", "text": "您好"},),
        )
        result = check_properties(script, self._source(), self._pack())

        v = _violations(result, "key_text_conflict")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, "greeting_welcome")
        self.assertEqual(v[0].unit_index, 1)
        msg = v[0].message
        self.assertIn("key_text_conflict", msg)
        self.assertIn("greeting_welcome", msg)
        self.assertIn("text", msg)

    def test_c4c_reason_on_keyed_unit(self):
        """key 单元带 reason → reason_on_keyed_unit，消息含序号与 reason。"""
        script = Script(
            1, ("closing_thank_you",), (LIVE_REASON,), 3,
            ({"key": "greeting_welcome", "reason": LIVE_REASON},),
        )
        result = check_properties(script, self._source(), self._pack())

        v = _violations(result, "reason_on_keyed_unit")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 1)
        msg = v[0].message
        self.assertIn("reason_on_keyed_unit", msg)
        self.assertIn(LIVE_REASON, msg)
        self.assertIn("1", msg)

    def test_c4c_reason_on_say_text_unit_reports_both_c4a_and_c4c(self):
        """SAY+text 且带 reason → C4a 与 C4c 各报一条。"""
        script = Script(
            1, ("closing_thank_you",), (LIVE_REASON,), 3,
            ({"action": "SAY", "text": "临时话术", "reason": LIVE_REASON},),
        )
        result = check_properties(script, self._source(), self._pack())

        codes = _codes(result)
        self.assertIn("unreviewed_text", codes)
        self.assertIn("reason_on_keyed_unit", codes)

    def test_c4c_reason_on_say_live_unit_is_fine(self):
        """reason 出现在 SAY_LIVE 单元上是合法的 → 不报 C4c。"""
        units = [_k("greeting_welcome"), _t("听不清", reason=LIVE_REASON)]
        result = self._run(self._script(units), self._pack())
        self.assertNotIn("reason_on_keyed_unit", _codes(result))


# ============================================================
# 6. C5 降级留痕
# ============================================================
class TestC5Reason(_TmpCheckBase):
    """C5a live_without_reason / C5b reason_not_whitelisted。"""

    def test_c5a_say_live_without_reason(self):
        """SAY_LIVE 无 reason → live_without_reason，消息含序号与 text 摘要。"""
        units = [_k("greeting_welcome"), {"text": "听不清，您再说一遍", "action": "SAY_LIVE"}]
        result = self._run(self._script(units), self._pack())

        v = _violations(result, "live_without_reason")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 2)
        msg = v[0].message
        self.assertIn("live_without_reason", msg)
        self.assertIn("听不清，您再说一遍", msg)
        self.assertIn("2", msg)

    def test_c5a_empty_reason_via_direct_script(self):
        """load_script 已在装载期拦空 reason；直接喂字典时 C5a 必须接住。"""
        script = Script(
            1, ("closing_thank_you",), (LIVE_REASON,), 3,
            ({"text": "听不清", "action": "SAY_LIVE", "reason": ""},),
        )
        result = check_properties(script, self._source(), self._pack())

        codes = _codes(result)
        self.assertIn("live_without_reason", codes)
        # 没有 reason 只报 C5a，不重复报 C5b
        self.assertNotIn("reason_not_whitelisted", codes)

    def test_c5a_reason_in_whitelist_passes_with_question_mark_text(self):
        """不做语义启发式：带问号的自由文本只要 reason 合法就不报 C5 违规。"""
        units = [_k("greeting_welcome"), _t("您能再说一遍吗？", reason=LIVE_REASON)]
        result = self._run(self._script(units), self._pack())

        codes = _codes(result)
        self.assertNotIn("live_without_reason", codes)
        self.assertNotIn("reason_not_whitelisted", codes)

    def test_c5b_reason_not_whitelisted(self):
        """reason 不在 live_whitelist → reason_not_whitelisted，消息含实际 reason。"""
        units = [_k("greeting_welcome"), _t("听不清", reason=LIVE_REASON)]
        script = self._script(units, live_whitelist=("user_off_script",))
        result = self._run(script, self._pack())

        v = _violations(result, "reason_not_whitelisted")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].unit_index, 2)
        msg = v[0].message
        self.assertIn("reason_not_whitelisted", msg)
        self.assertIn(LIVE_REASON, msg)
        self.assertIn("user_off_script", msg)
        # reason 有值 → 不重复报 C5a
        self.assertNotIn("live_without_reason", _codes(result))

    def test_c5b_empty_whitelist_blocks_every_say_live(self):
        """live_whitelist: [] 时任何 SAY_LIVE 都被拦（白名单必须显式声明）。"""
        units = [_k("greeting_welcome"), _t("听不清", reason=LIVE_REASON)]
        script = self._script(units, live_whitelist=())
        result = self._run(script, self._pack())

        v = _violations(result, "reason_not_whitelisted")
        self.assertEqual(len(v), 1)
        msg = v[0].message
        self.assertIn("[]", msg)
        self.assertIn(LIVE_REASON, msg)

    def test_c5a_reports_instead_of_c5b_when_reason_missing(self):
        """没有 reason → 只报 C5a，不重复报 C5b。"""
        units = [_k("greeting_welcome"), _t("听不清")]
        result = self._run(self._script(units), self._pack())

        codes = _codes(result)
        self.assertIn("live_without_reason", codes)
        self.assertNotIn("reason_not_whitelisted", codes)

    def test_c5_skips_non_say_live_units(self):
        """key 单元不是 SAY_LIVE → 不进 C5（即使没有 reason 也不报）。"""
        units = [_k("greeting_welcome"), _k("closing_thank_you")]
        result = self._run(self._script(units), self._pack())
        self.assertEqual(result.violations, ())


# ============================================================
# 7. 判定顺序与返回契约
# ============================================================
class TestOrderingAndContract(_TmpCheckBase):
    """判定顺序固定 C1 → C2 → C3 → C4 → C5；返回结构不可变。"""

    def test_violations_come_out_in_c1_to_c5_order(self):
        """一份同时踩五条判据的剧本 → 违规按 C1 → C2 → C3 → C4 → C5 排序。

        注：为了让 C3 只落在 C3b（已审核）这一条上，资产包额外放入 missing_key
        ——否则同一个 key 会同时触发 C3b 与 C3c（现实中库外 key 必然也没预铸）。
        """
        units = [
            _k("missing_key"),                        # C3b key_not_in_library
            _k("error_retry"), _k("error_retry"), _k("error_retry"), _k("error_retry"),  # C2a
            {"action": "SAY", "text": "临时话术"},        # C4a unreviewed_text
            {"text": "听不清"},                          # C5a live_without_reason + C1a no_exit
        ]
        result = check_properties(
            self._script(units),
            self._source(DEFAULT_KEYS),
            self._pack(list(DEFAULT_KEYS) + ["missing_key"]),
        )

        self.assertEqual(
            _codes(result),
            ["no_exit", "retry_unbounded", "key_not_in_library", "unreviewed_text", "live_without_reason"],
        )

    def test_result_and_violation_are_frozen(self):
        """CheckResult 与 Violation 必须不可变（frozen dataclass）。"""
        result = self._run(self._script([_k("greeting_welcome")]), self._pack())
        self.assertTrue(result.violations)

        v = result.violations[0]
        with self.assertRaises(Exception):
            result.violations = ()
        with self.assertRaises(Exception):
            result.skipped = ()
        with self.assertRaises(Exception):
            v.code = "changed"

    def test_source_none_raises_type_error(self):
        """source=None 必须报错（C3b 的判据输入缺失，不许装作检查过）。"""
        script = self._script([_k("closing_thank_you")])
        with self.assertRaises(TypeError) as ctx:
            check_properties(script, None)
        self.assertIn("source", str(ctx.exception))

    def test_pack_none_keeps_every_other_criterion_active(self):
        """pack=None 只跳过 C3c，其余判据照常报（跳过不等于放行）。"""
        units = [
            _k("missing_key"),                        # C3b 仍然要报
            _k("error_retry"), _k("error_retry"), _k("error_retry"), _k("error_retry"),  # C2a
        ]
        result = check_properties(self._script(units), self._source(), None)

        codes = _codes(result)
        self.assertIn("retry_unbounded", codes)
        self.assertIn("key_not_in_library", codes)
        self.assertIn("no_exit", codes)
        self.assertNotIn("key_not_prebaked", codes)
        self.assertEqual(result.skipped, ("key_not_prebaked",))


if __name__ == "__main__":
    unittest.main()
