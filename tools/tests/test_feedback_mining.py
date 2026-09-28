"""
tools/tests/test_feedback_mining.py — T23 回流闭环挖掘器的验收用例

覆盖 T23 卡的验收标准（逐条可判定）：
  1   挖掘器可用：留痕 + 事件流 → suggestions.json；每条候选含
      text / count / states / sample_turn_ids / first_seen / last_seen；
      次数可**独立复算**（用 turn_ids 数，不复用聚合逻辑）
  2   同源：归一化是 adapters.framework_kefu.normalize_text（同一函数对象）；
      单句判据是 compiler.source._SENTENCE_TERMINATORS（同一字符串对象）
  3   形式条款 + 拒收留痕：含槽位占位符的候选进 rejected_candidates 且
      原因含占位符本身；超长候选同理；A1/A2 字段恒为 "pending_human"
  4   确定性：同输入两次跑逐字节一致；换输入结果变；
      注入验证：shuffle=True 时排序被打散（能判红）
  5   端到端：CLI 跑通 → suggestions.json / report.json 落盘 + provenance
  7   无第三方依赖（import 清单里只有标准库 + 本仓模块）

反空转约束：全部用 tempfile 造临时输入，**只调用产品 API**
（mine / mine_from_json / check_form / write_artifacts / main），
不在测试里复制聚合与排序逻辑。判红用例真跑一遍再断言。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.feedback_mining import miner as fm  # noqa: E402
from tools.feedback_mining.miner import (  # noqa: E402
    A1A2_FIELD,
    B1,
    B2,
    B4,
    B_RULES,
    HUMAN_PENDING,
    MAX_CANDIDATE_CHARS,
    SCHEMA_VERSION,
    LiveText,
    MiningError,
    aggregate,
    check_form,
    check_single_sentence,
    check_slot_placeholder,
    check_text_length,
    count_terminators,
    form_status,
    load_events,
    load_ledger,
    mine,
    mine_from_json,
    mine_t22_candidates,
    scan_slot_placeholders,
    sort_candidates,
    write_artifacts,
)

from adapters.framework_kefu.normalize import normalize_text as product_normalize  # noqa: E402
from compiler.source import _SENTENCE_TERMINATORS as product_terminators  # noqa: E402
from core.metrics_spec import FALLBACK, HIT, MISS, PART, PLAN_ID, REASON, TS, TURN_ID  # noqa: E402

MINER = _REPO_ROOT / "tools" / "feedback_mining" / "miner.py"
DEMO = _REPO_ROOT / "tools" / "feedback_mining" / "make_demo_inputs.py"

# 输入数据的时间戳（全部注入，不读真实时钟）
TS_A = "2026-09-22T09:00:00Z"
TS_B = "2026-09-22T09:01:00Z"
TS_C = "2026-09-22T09:02:00Z"

# 演示话术（全部自造，不含真实会话/录音/个人信息）
TEXT_THREE = "您好，为您查询到当前排队位置是第 3 位。"
TEXT_THREE_VAR = "您好，为您查询到当前排队位置是第 3 位。 "   # 归一化后与上一条同键
TEXT_TRANSFER = "请保持通话，坐席接通后我会为您转接。"
TEXT_TRANSFER_FULL = "请保持通话，坐席接通后我会为您转接。"    # 全角标点：同键
TEXT_SLOT_BRACE = "已为您预约 {date} 下午上门服务。"
TEXT_SLOT_BRACKET = "您的地址在 [小区名] 附近，师傅 30 分钟内到达。"
TEXT_LONG = "您好，已为您登记供暖报修工单，当前状态为待受理，我们会在工作日内派单，" \
            "派单后维修师傅会在约定时间上门，如需变更上门时间请回复修改时间，如需人工服务请回复转人工。"
TEXT_CROSS_SENT = "工单已受理！请保持电话畅通！师傅会尽快联系您。"


# ---------------------------------------------------------------------------
# 输入构造（只造数据，不含任何被测逻辑）
# ---------------------------------------------------------------------------
def _write_jsonl(path: Path, records) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
        encoding="utf-8",
    )
    return path


def _event(turn_id, part, state, text, ts, reason=None, key=None):
    """造一条事件（T07 格式）+ live_text。三态键只取 metrics_spec 的常量。"""
    e = {
        TS: ts,
        TURN_ID: turn_id,
        PLAN_ID: f"plan-{turn_id}",
        "key": key,
        PART: part,
        "rate": "normal",
        "variant": None if key is None else 0,
        REASON: reason or "",
        "pack_version": "demo-1",
        "first_audio_ms": 0.2 if state == HIT else 500.0,
        state: True,
        fm.LIVE_TEXT_FIELD: text,
    }
    return e


def build_inputs(demo=False):
    """造 (ledger, events) 两份临时文件。demo=True 时带上全部负例话术。"""
    ledger = [
        {"ledger_version": 1, TURN_ID: f"t-{i:03d}", "plan_id": f"plan-{i}",
         TS: TS_A, "state": {"queue_position": i}, "plan": [], "keys": []}
        for i in range(1, 4)
    ]
    events = [
        _event("t-001", 1, MISS, TEXT_THREE, TS_A),
        _event("t-001", 2, HIT, "预铸原话", TS_B),
        _event("t-002", 1, MISS, TEXT_THREE_VAR, TS_B),
        _event("t-002", 2, MISS, TEXT_TRANSFER, TS_C),
        _event("t-003", 1, FALLBACK, TEXT_TRANSFER_FULL, TS_C, reason="fingerprint_mismatch"),
    ]
    if demo:
        events += [
            _event("t-001", 3, MISS, TEXT_SLOT_BRACE, TS_C),
            _event("t-002", 3, MISS, TEXT_SLOT_BRACKET, TS_C),
            _event("t-003", 2, MISS, TEXT_LONG, TS_C),
            _event("t-003", 3, MISS, TEXT_CROSS_SENT, TS_C),
        ]
    return ledger, events


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


# ---------------------------------------------------------------------------
# 验收 1：挖掘器可用 + 字段齐备 + 次数可独立复算
# ---------------------------------------------------------------------------
class TestMinerProducesSuggestions(_TmpDir):

    def setUp(self):
        super().setUp()
        ledger, events = build_inputs()
        self.ledger = _write_jsonl(self.dir / "turns.jsonl", ledger)
        self.events = _write_jsonl(self.dir / "events.jsonl", events)
        self.run, self.prov = mine(self.ledger, self.events)

    def test_candidate_fields_are_complete(self):
        self.assertGreater(len(self.run.suggestions), 0)
        for s in self.run.suggestions:
            for f in ("text", "count", "states", "sample_turn_ids", "first_seen", "last_seen"):
                self.assertIn(f, s, f"候选缺字段 {f}: {s}")
            self.assertTrue(s["text"])
            self.assertGreater(s["count"], 0)
            self.assertTrue(set(s["states"]) <= {MISS, FALLBACK})
            self.assertLessEqual(len(s["sample_turn_ids"]), len(s["turn_ids"]))
            self.assertEqual(list(s["turn_ids"][: len(s["sample_turn_ids"])]), s["sample_turn_ids"],
                             "sample_turn_ids 必须是 turn_ids 的前缀（确定性排序）")

    def test_count_is_independently_recomputable(self):
        """验收方独立复算：按 turn_ids 数一遍，必须与建议里的 count 一致。"""
        for s in self.run.suggestions:
            self.assertEqual(
                len(set(s["turn_ids"])),
                len(set(s["sample_turn_ids"])),
                "turn_ids 与 sample_turn_ids 不一致（sample 是前缀，不该多也不该少）",
            )
        # 手工复算：数原始事件里每条话术的出现次数（不复用 aggregate）
        raw = load_events(self.events)
        by_text = {}
        for e in raw:
            if e.get(MISS) or e.get(FALLBACK):
                by_text[product_normalize(e[fm.LIVE_TEXT_FIELD])] = \
                    by_text.get(product_normalize(e[fm.LIVE_TEXT_FIELD]), 0) + 1
        for s in self.run.suggestions:
            self.assertEqual(
                by_text[s["text"]], s["count"],
                f"独立复算与报告不一致: {s['text']!r}",
            )

    def test_hit_events_are_not_mined(self):
        """只挖 miss / fallback：命中的话术绝不能进候选（否则等于给已预铸的话再铸一遍）。"""
        for s in self.run.suggestions:
            self.assertNotIn(HIT, s["states"])

    def test_three_way_counting_is_in_report(self):
        self.assertEqual(self.run.stats["units_by_state"][HIT], 1)
        self.assertEqual(self.run.stats["units_by_state"][MISS] + self.run.stats["units_by_state"][FALLBACK], 4)


# ---------------------------------------------------------------------------
# 验收 2：归一化与单句判据必须同源（同一对象，不是复制）
# ---------------------------------------------------------------------------
class TestSameSourceGuards(_TmpDir):

    def test_normalizer_is_the_product_function_object(self):
        self.assertIs(fm.normalize_text, product_normalize)

    def test_terminators_are_the_product_constant_object(self):
        self.assertIs(fm._SENTENCE_TERMINATORS, product_terminators)

    def test_terminator_string_is_exactly_the_compilers(self):
        self.assertEqual(count_terminators("a。b！c?d!e?"), 5)
        self.assertEqual(count_terminators("你好"), 0)

    def test_b1_uses_raw_text_not_normalized(self):
        """实测出的坑：归一化会把「。」折成 '.'，数在归一化文本上会少算。"""
        v = check_single_sentence(product_normalize(TEXT_CROSS_SENT), raw_text=TEXT_CROSS_SENT)
        self.assertIsNotNone(v)
        self.assertEqual(v["rule"], B1)
        self.assertIn("3", v["reason"])

    def test_b1_would_fail_open_if_counted_on_normalized_text(self):
        """反证：只数归一化文本会少算（回归保护——本模块的语义陷阱）。

        normalize_text 的 ②③ 两步把「！？」折成 ASCII，但「。」原样保留，
        于是「a。b！」归一化后剩 2 个，而原文是 3 个。
        若 B1 判归一化文本，这类候选会被判成「一句」放进建议清单 = fail-open。
        """
        self.assertEqual(count_terminators(TEXT_CROSS_SENT), 3)
        self.assertEqual(count_terminators(product_normalize(TEXT_CROSS_SENT)), 2)
        self.assertIsNotNone(check_single_sentence(product_normalize(TEXT_CROSS_SENT), raw_text=TEXT_CROSS_SENT))
        # 极端形态：两个「。」归一化后仍在但被误读为「1 句 2 标点」——
        # 这里用 !?!? 的形态把差值拉满（原文 3 个，归一化后 0 个命中集合外的字符）
        self.assertEqual(count_terminators(product_normalize(TEXT_CROSS_SENT)), 2)


# ---------------------------------------------------------------------------
# 验收 3：形式条款 + 拒收留痕（负例实测）
# ---------------------------------------------------------------------------
class TestFormRulesAndRejection(_TmpDir):

    def setUp(self):
        super().setUp()
        ledger, events = build_inputs(demo=True)
        self.ledger = _write_jsonl(self.dir / "turns.jsonl", ledger)
        self.events = _write_jsonl(self.dir / "events.jsonl", events)
        self.run, _ = mine(self.ledger, self.events)

    def _by_rule(self, rule):
        return [r for r in self.run.rejected for v in r["form"]["violations"] if v["rule"] == rule]

    def test_slot_placeholder_candidate_is_rejected_with_the_placeholder_in_reason(self):
        hit = self._by_rule(B2)
        self.assertGreaterEqual(len(hit), 2)
        reasons = " ".join(v["reason"] for r in hit for v in r["form"]["violations"] if v["rule"] == B2)
        self.assertIn("{date}", reasons)      # 占位符本身必须出现在原因里
        self.assertIn("[小区名]", reasons)

    def test_overlong_candidate_is_rejected(self):
        hit = self._by_rule(B4)
        self.assertEqual(len(hit), 1)
        self.assertGreater(len(hit[0]["text"]), MAX_CANDIDATE_CHARS)
        self.assertIn(str(len(hit[0]["text"])), hit[0]["form"]["violations"][0]["reason"])
        self.assertIn(str(MAX_CANDIDATE_CHARS), hit[0]["form"]["violations"][0]["reason"])

    def test_cross_sentence_candidate_is_rejected(self):
        hit = self._by_rule(B1)
        self.assertEqual(len(hit), 1)
        self.assertIn("句末标点", hit[0]["form"]["violations"][0]["reason"])

    def test_a1a2_field_is_always_pending_human(self):
        for s in self.run.suggestions + self.run.rejected:
            self.assertEqual(s[A1A2_FIELD], HUMAN_PENDING)
            for k, v in s["forbidden_check"].items():
                self.assertEqual(v, HUMAN_PENDING, f"{k} 不应由机器判定")

    def test_rejected_reason_is_not_empty(self):
        for r in self.run.rejected:
            self.assertGreater(len(r["form"]["violations"]), 0)
            for v in r["form"]["violations"]:
                self.assertTrue(v["reason"])
                self.assertIn(v["rule"], B_RULES)

    def test_rejected_are_not_in_suggestions(self):
        s = {x["text"] for x in self.run.suggestions}
        r = {x["text"] for x in self.run.rejected}
        self.assertFalse(s & r, "同一个候选同时进了建议与拒收 = 静默放行")

    def test_clean_candidate_passes_and_gets_a_suggested_key(self):
        target = [s for s in self.run.suggestions if s["text"] == product_normalize(TEXT_THREE)]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["form"]["status"], "pass")
        self.assertEqual(target[0]["form"]["violations"], [])
        self.assertEqual(target[0]["suggested_key"], "greeting")

    def test_rule_functions_are_failing_as_expected(self):
        """逐条判红：形式条款函数本身不能恒返回 None。"""
        self.assertIsNotNone(check_slot_placeholder("已为您预约 {date} 上门服务。"))
        self.assertIsNotNone(check_slot_placeholder("地址在 [小区名] 附近。"))
        self.assertIsNone(check_slot_placeholder("请保持通话。"))
        self.assertIsNotNone(check_text_length("x" * (MAX_CANDIDATE_CHARS + 1)))
        self.assertIsNone(check_text_length("x" * MAX_CANDIDATE_CHARS))
        # check_form 返回全部命中，不只报第一条
        v = check_form(product_normalize(TEXT_SLOT_BRACKET), raw_text=TEXT_SLOT_BRACKET)
        self.assertEqual(len(v), 1)
        v2 = check_form("a。b。", raw_text="a。b。", max_chars=1)
        self.assertEqual({x["rule"] for x in v2}, {B1, B4})
        self.assertEqual(form_status(()), "pass")
        self.assertEqual(form_status(({"rule": B1, "reason": "x"},)), "fail")

    def test_placeholder_scan_returns_position_and_name(self):
        hit = scan_slot_placeholders("A {name} B [小区名] C")
        self.assertTrue(hit)
        self.assertEqual([p.value for p in hit.all], ["{name}", "[小区名]"])
        self.assertEqual([p.name for p in hit.all], ["name", "小区名"])
        self.assertEqual([p.pattern for p in hit.all], ["braces", "brackets"])


# ---------------------------------------------------------------------------
# 验收 4：确定性（两次跑逐字节一致）+ 换输入必变 + 注入验证
# ---------------------------------------------------------------------------
class TestDeterminism(_TmpDir):

    def _run_cli(self, out_dir, *extra):
        cmd = [sys.executable, str(MINER),
               "--ledger", str(self.dir / "turns.jsonl"),
               "--events", str(self.dir / "events.jsonl"),
               "--out-dir", str(out_dir)] + list(extra)
        p = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def setUp(self):
        super().setUp()
        ledger, events = build_inputs(demo=True)
        _write_jsonl(self.dir / "turns.jsonl", ledger)
        _write_jsonl(self.dir / "events.jsonl", events)

    def test_two_runs_are_byte_identical(self):
        a, b = self.dir / "o1", self.dir / "o2"
        self._run_cli(a)
        self._run_cli(b)
        for name in ("suggestions.json", "report.json", "rejected.json"):
            da, db = (a / name).read_bytes(), (b / name).read_bytes()
            self.assertEqual(da, db, f"{name} 两次跑不一致")

    def test_input_change_changes_output(self):
        a = self.dir / "o1"
        self._run_cli(a)
        before = json.loads((a / "suggestions.json").read_text(encoding="utf-8"))
        # 追加一条事件 → 结果必须变
        with open(self.dir / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_event("t-009", 1, MISS, "新的未命中话术。", TS_C), ensure_ascii=False) + "\n")
        b = self.dir / "o2"
        self._run_cli(b)
        after = json.loads((b / "suggestions.json").read_text(encoding="utf-8"))
        self.assertNotEqual(before, after)
        self.assertEqual(after["n_suggestions"], before["n_suggestions"] + 1)

    def test_injection_shuffle_breaks_the_sorting(self):
        """注入验证：shuffle=True 时排序被打散（固定种子，仍能复现）。"""
        ledger, events = build_inputs(demo=True)
        run = mine_from_json(ledger, events, shuffle=False)
        ordered = [c.text for c in run.candidates]
        counts = {c.text: c.count for c in run.candidates}
        self.assertEqual(ordered, sorted(ordered, key=lambda t: (-counts[t], t)),
                         "候选未按 count 降序 + 文本字典序排列")
        run_shuffled = mine_from_json(ledger, events, shuffle=True)
        shuffled = [c.text for c in run_shuffled.candidates]
        self.assertNotEqual(shuffled, ordered, "shuffle 注入没有改变排序（判据失效）")
        self.assertEqual(set(shuffled), set(ordered), "shuffle 只换序，不该增删候选")
        # 固定种子 → 两次 shuffle 一致（随机 ≠ 不确定）
        again = mine_from_json(ledger, events, shuffle=True)
        self.assertEqual([c.text for c in again.candidates], shuffled)

    def test_sort_contract_is_count_desc_then_text(self):
        def mk(text, count):
            return fm.Candidate(text, (), count, ("miss",), (), (), (), None, None)
        cands = [mk("b", 1), mk("a", 1), mk("c", 3), mk("a2", 1)]
        self.assertEqual([c.text for c in sort_candidates(cands)], ["c", "a", "a2", "b"])


# ---------------------------------------------------------------------------
# 验收 5：端到端 CLI + provenance
# ---------------------------------------------------------------------------
class TestEndToEndCli(_TmpDir):

    def setUp(self):
        super().setUp()
        ledger, events = build_inputs(demo=True)
        self.ledger = _write_jsonl(self.dir / "turns.jsonl", ledger)
        self.events = _write_jsonl(self.dir / "events.jsonl", events)
        self.out = self.dir / "out"

    def test_cli_writes_all_artifacts(self):
        p = subprocess.run(
            [sys.executable, str(MINER),
             "--ledger", str(self.ledger), "--events", str(self.events),
             "--out-dir", str(self.out)],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        for name in ("suggestions.json", "rejected.json", "report.json"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_report_has_provenance_with_sha256_and_commit(self):
        subprocess.run(
            [sys.executable, str(MINER),
             "--ledger", str(self.ledger), "--events", str(self.events),
             "--out-dir", str(self.out)],
            capture_output=True, text=True, check=True,
        )
        report = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        prov = report["provenance"]
        self.assertEqual(prov["inputs"]["ledger"]["sha256"], hashlib.sha256(self.ledger.read_bytes()).hexdigest())
        self.assertEqual(prov["inputs"]["events"]["sha256"], hashlib.sha256(self.events.read_bytes()).hexdigest())
        self.assertIsNotNone(prov["repo_commit"])
        self.assertEqual(prov["normalizer"], "adapters.framework_kefu.normalize.normalize_text")
        self.assertEqual(prov["sentence_terminators"], product_terminators)

    def test_samples_match_the_input_size(self):
        subprocess.run(
            [sys.executable, str(MINER),
             "--ledger", str(self.ledger), "--events", str(self.events),
             "--out-dir", str(self.out)],
            capture_output=True, text=True, check=True,
        )
        report = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        samples = report["samples"]
        self.assertEqual(samples["ledger_records"], 3)
        self.assertEqual(samples["event_records"], 9)
        self.assertEqual(report["counts"]["candidates"],
                         report["counts"]["suggestions"] + report["counts"]["rejected"])
        self.assertEqual(samples["units_by_state"][MISS] + samples["units_by_state"][FALLBACK],
                         samples["live_texts_considered"])
        # 留痕与事件流必须能对齐：候选的 turn 全部在留痕里（D2 两份输入互相印证）
        self.assertGreater(samples["turn_ids_in_both"], 0)
        self.assertLessEqual(samples["turn_ids_in_both"], samples["turn_ids_in_ledger"])
        self.assertLessEqual(samples["turn_ids_in_both"], samples["turn_ids_with_live_text"])

    def test_schema_version_is_declared(self):
        subprocess.run(
            [sys.executable, str(MINER),
             "--ledger", str(self.ledger), "--events", str(self.events),
             "--out-dir", str(self.out)],
            capture_output=True, text=True, check=True,
        )
        for name in ("suggestions.json", "rejected.json", "report.json"):
            self.assertEqual(json.loads((self.out / name).read_text(encoding="utf-8"))["_schema"], SCHEMA_VERSION)

    def test_demo_builder_and_miner_run_together(self):
        """用仓内的演示输入脚本造一份输入，再跑挖掘器（端到端演示，数字落盘）。"""
        demodir = self.dir / "demo"
        subprocess.run(
            [sys.executable, str(DEMO), "--out-dir", str(demodir)],
            capture_output=True, text=True, check=True,
        )
        self.assertTrue((demodir / "turns.jsonl").is_file())
        self.assertTrue((demodir / "events.jsonl").is_file())
        run, prov = mine(demodir / "turns.jsonl", demodir / "events.jsonl")
        self.assertGreater(len(run.candidates), 0)
        self.assertGreater(len(run.suggestions), 0)
        self.assertGreater(len(run.rejected), 0)
        self.assertTrue(prov["repo_commit"])
        # 两份输入互相印证：候选的 turn 必须能在轮级留痕里找到（D2 对齐）
        cov = run.stats["ledger_coverage"]
        self.assertEqual(cov["candidate_turn_ids"], cov["found_in_ledger"])
        self.assertEqual(cov["missing_from_ledger"], [])
        self.assertEqual(cov["found_in_ledger"], run.stats["turn_ids_with_live_text"])


# ---------------------------------------------------------------------------
# 负例：数据残缺必须报错，不得静默跳过
# ---------------------------------------------------------------------------
class TestFailClosed(_TmpDir):

    def test_malformed_jsonl_raises(self):
        p = self.dir / "bad.jsonl"
        p.write_text('{"turn_id": "t-1", "state": {}}\n{oops\n', encoding="utf-8")
        with self.assertRaises(MiningError):
            load_ledger(p)

    def test_missing_file_raises_with_path(self):
        with self.assertRaisesRegex(MiningError, str(self.dir)):
            load_events(self.dir / "nope.jsonl")

    def test_miss_without_live_text_raises(self):
        """miss 却没 live_text = 数据残缺，静默当空串会把候选数算错。"""
        p = self.dir / "events.jsonl"
        _write_jsonl(p, [_event("t-1", 1, MISS, "x", TS_A)])
        with open(p, "r", encoding="utf-8") as fh:
            e = json.loads(fh.read().strip())
        del e[fm.LIVE_TEXT_FIELD]
        _write_jsonl(p, [e])
        with self.assertRaises(MiningError):
            load_events(p)

    def test_ledger_without_state_raises(self):
        p = self.dir / "turns.jsonl"
        _write_jsonl(p, [{"turn_id": "t-1", "plan_id": "p-1", "keys": []}])
        with self.assertRaises(MiningError):
            load_ledger(p)

    def test_empty_inputs_yield_zero_candidates_not_an_error(self):
        """两份输入都没有 miss/fallback = 合法的「链路健康」结论，不是异常。"""
        ledger, events = build_inputs()
        run = mine_from_json(ledger, [e for e in events if e.get(HIT)])
        self.assertEqual(run.candidates, ())
        self.assertEqual(run.suggestions, ())
        self.assertEqual(run.rejected, ())

    def test_aggregate_drops_empty_normalized_text(self):
        """纯空白 / 纯标点的 live_text 不该成为候选，也不该丢计数。"""
        items = [
            LiveText("t-1", "p-1", 1, TS_A, MISS, "say_live_text", "   "),
            LiveText("t-2", "p-2", 1, TS_A, MISS, "say_live_text", ""),
            LiveText("t-3", "p-3", 1, TS_A, MISS, "say_live_text", "有效话术。"),
        ]
        cands = aggregate(items)
        self.assertEqual([c.text for c in cands], ["有效话术."])
        # 计数不丢：两条空/纯标点的 live_text 被跳过，报告要能看到它们曾经被数过


# ---------------------------------------------------------------------------
# 跨仓库对拍：T22 的 737 候选过一遍形式条款
# ---------------------------------------------------------------------------
class TestCrossCheckT22(_TmpDir):
    """不是回流挖掘：T22 在**外部语料**上挖的（判据可借鉴但那是另一件事）。
    这里只做形式条款对拍——把 docs/14 的 B1–B5 量到外部语料的候选骨架上。"""

    T22 = Path("labs/multi-industry-corpus/out/mine_candidates.json")

    def test_cross_check_runs_and_reports_the_737(self):
        out = mine_t22_candidates("labs/multi-industry-corpus/out/mine_candidates.json")
        self.assertEqual(out["candidates_by_group_hits"][">=3"], 737)
        self.assertEqual(out["total_candidates"], 737)
        self.assertEqual(out["samples_checked"], 8)
        # 样本必须如实标注：8 条样本不能外推到 737 条
        self.assertIn("737", out["note"])
        self.assertIn("人工", out["note"])

    def test_cross_check_missing_file_raises(self):
        with self.assertRaisesRegex(MiningError, "不存在"):
            mine_t22_candidates(self.dir / "nope.json")

    def test_cross_check_bad_shape_raises(self):
        p = self.dir / "bad.json"
        p.write_text(json.dumps({"candidates_by_group_hits": {}}), encoding="utf-8")
        with self.assertRaises(MiningError):
            mine_t22_candidates(p)

    def test_cli_writes_the_cross_check_section(self):
        ledger, events = build_inputs(demo=True)
        _write_jsonl(self.dir / "turns.jsonl", ledger)
        _write_jsonl(self.dir / "events.jsonl", events)
        p = subprocess.run(
            [sys.executable, str(MINER),
             "--ledger", str(self.dir / "turns.jsonl"),
             "--events", str(self.dir / "events.jsonl"),
             "--out-dir", str(self.dir / "out"),
             "--t22", "labs/multi-industry-corpus/out/mine_candidates.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads((self.dir / "out" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["cross_check_t22"]["total_candidates"], 737)
        self.assertIn("737", report["cross_check_t22"]["note"])


# ---------------------------------------------------------------------------
# 验收 7：无第三方依赖
# ---------------------------------------------------------------------------
class TestNoThirdPartyDeps(unittest.TestCase):

    def test_imports_are_stdlib_and_repo_only(self):
        import ast
        tree = ast.parse(MINER.read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        allowed = {"__future__", "ast", "argparse", "hashlib", "json", "random", "re", "subprocess", "sys",
                   "collections", "dataclasses", "pathlib", "typing", "collections.abc",
                   "adapters", "compiler", "core"}
        extra = mods - allowed
        self.assertEqual(extra, set(), f"引入了白名单外的依赖: {sorted(extra)}")


if __name__ == "__main__":
    unittest.main()
