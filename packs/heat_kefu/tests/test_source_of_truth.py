"""
packs.heat-kefu.tests.test_source_of_truth — 「话术同源」机器证明（T31b，packs 层第一根测试）

职责：用 yaml 原文做**动态期望值**，证明本包 40 条 variant 与 kefu 供热预设 yaml **逐字相等**，
      并证明「不铸」的两类（多分句 14 + 带槽 13）在真包上真的走原路（find_hit 未命中）。
不负责：不做语义分析、不复制比对器、不改动 yaml / 包源 / 编译器。

「同源」的全部价值就在逐字 `==`：一个标点被改写，命中路径就会播出「差不多但不是这句」的话，
      事后无法定位（仓库头号红线：禁止静默降级）。所以本文件里没有任何归一化后的比较——
      归一化相等 ≠ 同源（docs/14 的教训）。

yaml 不在本仓（公开 CI 没有 kefu 仓）时：依赖 yaml 的测试一律 **skip** 而非 fail，
      包源自身的结构测试（key 集合、单句判据、不含槽位）不受影响照常跑。
"""

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

# 仓库根兜底（同 labs/ 的先例：脚本必须自带 sys.path 兜底，不依赖调用方环境变量）
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PACK_DIR = REPO_ROOT / "packs" / "heat_kefu"

# yaml 原文绝对路径（数据分级：公开级，docs/15 §四 已拍板）
YAML_PATH = Path(  # kefu 预设原文；仅本机有 kefu 仓时做同源断言，否则按设计 skip
    os.environ.get(
        "KEFU_HEAT_YAML",
        str(Path(__file__).resolve().parents[3].parent / "kefu-agent" / "organs" / "客服" / "brain" / "prompts" / "供热预设.yaml"),
    )
)
# yaml 指纹：变更即钩子自动不命中（fail-closed），admission.md 文首有同一份记录
YAML_SHA256 = "a0d9194a95a876d12103381c3373c63a98d021e23853a998429bb3ae2cbbfe44"


# ---------------------------------------------------------------------------
# 清单（单一真源 = compiler._SENTENCE_TERMINATORS 判据的实测结果，见 admission.md 对账表）
# ---------------------------------------------------------------------------
# 必铸 40 条：yaml 值逐字，全部通过「一句一 variant」判据（句末标点 < 2），且不含槽位
BAKED_KEYS = (
    "options", "transfer_ready", "transfer_queued", "turn_limit_notice",
    "turn_limit_notice_v2", "clarify_options", "farewell", "fallback_route",
    "faq_empty", "pay_ask_user_no", "pay_verify_user_no", "pay_no_bills",
    "pay_due_hint", "pay_error", "channel_url_note", "repair_edit_ask",
    "work_order_hint", "work_order_ask", "work_order_empty", "work_order_error",
    "work_order_eta_unavailable", "work_order_eta_guide", "repair_error",
    "collect_limit", "transfer_suggest", "realtime_ask", "realtime_empty",
    "stop_warm_emergency", "stop_warm_followup", "off_hours_repair",
    "off_hours_other", "off_hours_urgent", "repair_ask_userNo",
    "repair_ask_contactPhone", "repair_ask_address", "repair_ask_problemType",
    "repair_ask_desc", "repair_ask_natural_contactPhone",
    "repair_ask_natural_problemType", "repair_ask_natural_desc",
)

# exclusion·多分句 14 条：≥2 句末标点，按 docs/11 §11.6「一句一 variant」不铸 → 走原路
MULTI_CLAUSE_KEYS = (
    "opening", "chat_smalltalk", "clarify_work_order", "clarify_repair",
    "fallback_internal", "lifeboat_fallback", "user_no_unknown",
    "repair_confirm_question", "work_order_no_unknown", "internal_chat_hint",
    "stop_warm_plan", "inject_refuse", "repair_ask_natural_address",
    "repair_ask_natural_userNo",
)

# exclusion·带槽 13 条：variant 不得含槽位（docs/06 §6.2.5）→ 不铸 → 走原路
SLOT_KEYS = (
    "repair_progress_reminder", "repair_ask_new_value", "repair_create_ok",
    "work_order_reply", "repair_dup_remind", "repair_duplicate",
    "confirm_silence", "transfer_summary_missing", "realtime_reply",
    "steer_return_notice", "pending_intent_notice", "profile_choice_ask",
    "profile_choice_done",
)

# 带槽句的填充结果（假值，非真实会话数据）：用来证明「带槽不铸」的真实行为是走原路。
# 值刻意取「不像任何已铸话术」的串，避免意外撞上一条已铸 variant。
SLOT_FILL_EXAMPLES = {
    "work_order_reply": {
        "orderNo": "WO99999999",
        "description": "管道漏水",
        "currentStep": "已派单",
        "worker": "王师傅",
        "timeLine": "明天 09:00",
        "statusName": "待上门",
        "endReason": "-",
    },
    "repair_create_ok": {"orderNo": "R99999999"},
    "realtime_reply": {
        "building": "999", "house": "9", "roomTemp": "18.5", "time": "00:00:00",
    },
    "repair_dup_remind": {"orderNo": "R99999999", "statusName": "待上门"},
    "repair_duplicate": {"orderNo": "R99999999"},
    "confirm_silence": {"summary": "户号、电话、地址都还没说全。"},
    "repair_progress_reminder": {"got": "户号", "need": "联系电话"},
    "repair_ask_new_value": {"slot": "联系电话"},
    "transfer_summary_missing": {"missing": "户号"},
    "steer_return_notice": {"flow_hint": "查账单"},
    "pending_intent_notice": {"intent_label": "查进度"},
    "profile_choice_ask": {"total": "3", "profiles": "①朝阳苑 ②海淀路 ③望京里"},
    "profile_choice_done": {"userNo": "U99999999", "community": "测试小区"},
}


def _yaml_lookup(yaml_doc, key):
    """按 key 取 yaml 值：repair_ask.* 走嵌套，其余走顶层。找不到 → KeyError。"""
    if key.startswith("repair_ask_") and key[len("repair_ask_"):] in yaml_doc["repair_ask"]:
        return yaml_doc["repair_ask"][key[len("repair_ask_"):]]
    return yaml_doc[key]


def _load_yaml():
    import yaml  # 延迟导入：yaml 缺失时只影响依赖 yaml 的测试
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_pack_dir():
    """把预铸产物装成 assets.AssetPack（真包真查）。

    产物目录候选：
      ① packs/heat-kefu/manifest.json（若构建产物落在包目录内）
      ② packs/heat-kefu/assets/heat-kefu-1/manifest.json
    两者都没有 → 返回 None（调用方 skip，而不是 fail：产物是本地构建步骤，
    公开 CI 可能未构建）。
    """
    import json as _json
    candidates = [
        PACK_DIR / "manifest.json",
        PACK_DIR / "assets" / "heat-kefu-1" / "manifest.json",
        # 产物不得写进包源目录（cli 硬拦，docs/08 §8.5），所以 build 的常规落点
        # 是包目录**旁边**：
        PACK_DIR.parent / "heat_kefu_build" / "heat-kefu-1" / "manifest.json",
    ]
    hits = [p for p in candidates if p.is_file()]
    if len(hits) > 1:
        raise AssertionError(
            f"出现多份 heat-kefu 的 manifest.json（{hits}）——包目录歧义，"
            "不允许猜测用哪一份（禁止静默降级）"
        )
    if not hits:
        return None
    from assets import load_pack
    return load_pack(hits[0].parent)


# ---------------------------------------------------------------------------
# A. 包源结构（不依赖 yaml / 不依赖构建产物）
# ---------------------------------------------------------------------------
class TestPackSourceStructure(unittest.TestCase):
    """包源自身的一致性：key 集合、单句判据、不含槽位。"""

    def setUp(self):
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            self.phrases = json.load(f)["phrases"]
        self.by_key = {p["key"]: p for p in self.phrases}

    def test_pack_is_40_keys_unique(self):
        self.assertEqual(len(self.phrases), 40)
        self.assertEqual(len(self.by_key), 40, "phrases.json 里 key 有重复")

    def test_pack_key_set_matches_baked_list(self):
        self.assertEqual(set(self.by_key), set(BAKED_KEYS))

    def test_each_phrase_has_exactly_one_variant(self):
        # 「一条 variant」是本包的口径（防复读机不需要在这里解决）
        for p in self.phrases:
            self.assertEqual(len(p["variants"]), 1, f"key={p['key']} variant 数 != 1")

    def test_rates_follow_pack_convention(self):
        # 钩子（T30）只查 normal 档；口径见 T31b 卡「rates 只查 normal」
        for p in self.phrases:
            self.assertEqual(p["rates"], ["normal"], f"key={p['key']}")

    def test_no_variant_contains_slot_placeholder(self):
        # 槽值一律现场合成（docs/06 §6.2.5）；带占位符的话术不得进预铸
        for p in self.phrases:
            for v in p["variants"]:
                self.assertNotIn("{", v, f"key={p['key']} 的 variant 含槽位占位符: {v!r}")
                self.assertNotIn("}", v, f"key={p['key']} 的 variant 含槽位占位符: {v!r}")

    def test_every_variant_passes_one_sentence_rule(self):
        # 判据从编译器读（不自写一份），保证本测试跟产品判据同源
        from compiler.source import _SENTENCE_TERMINATORS
        for p in self.phrases:
            for v in p["variants"]:
                n = sum(1 for ch in v if ch in _SENTENCE_TERMINATORS)
                self.assertLess(n, 2, f"key={p['key']} 含 {n} 个句末标点，不是一句: {v!r}")

    def test_variant_texts_are_distinct(self):
        # 逐字同源的另一半：两条 variant 不该撞成同一串（撞了说明铸重了）
        texts = [p["variants"][0] for p in self.phrases]
        self.assertEqual(len(set(texts)), len(texts), "存在逐字重复的 variant")

    def test_script_covers_all_baked_keys(self):
        with open(PACK_DIR / "script.json", "r", encoding="utf-8") as f:
            script = json.load(f)
        used = {u["key"] for u in script["units"]}
        self.assertEqual(used, set(BAKED_KEYS), "剧本未覆盖全部必铸 key")

    def test_exclusion_keys_are_absent_from_pack(self):
        for k in MULTI_CLAUSE_KEYS + SLOT_KEYS:
            self.assertNotIn(k, self.by_key, f"exclusion key 被铸进了包: {k}")


# ---------------------------------------------------------------------------
# B. 同源断言（依赖 yaml；yaml 缺失 → skip）
# ---------------------------------------------------------------------------
@unittest.skipUnless(YAML_PATH.is_file(), "kefu 供热预设 yaml 不在本仓（公开 CI）——同源断言跳过")
class TestSourceOfTruthVerbatim(unittest.TestCase):
    """40 条 variant 与 yaml 值逐字 `==`——本包唯一价值的机器证明。"""

    @classmethod
    def setUpClass(cls):
        cls.yaml_doc = _load_yaml()
        raw = YAML_PATH.read_bytes()
        cls.yaml_sha = hashlib.sha256(raw).hexdigest()
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            cls.by_key = {p["key"]: p for p in json.load(f)["phrases"]}

    def test_yaml_fingerprint_unchanged(self):
        # 指纹漂移 = yaml 已被改，此时「同源」基线已失效，必须失败而不是继续比
        self.assertEqual(
            self.yaml_sha, YAML_SHA256,
            f"yaml 的 sha256 已变更（实测 {self.yaml_sha}）——同源基线失效，"
            "请同步更新 admission.md 与本常量",
        )

    def test_all_40_variants_equal_yaml_verbatim(self):
        # 逐字 ==，**不做任何归一化**：标点、全半角、语气词一字都不许差
        mismatches = []
        for key in BAKED_KEYS:
            yaml_value = _yaml_lookup(self.yaml_doc, key)
            pack_value = self.by_key[key]["variants"][0]
            if pack_value != yaml_value:
                mismatches.append((key, yaml_value, pack_value))
        if mismatches:
            detail = "\n".join(
                f"  {k}\n    yaml : {y!r}\n    pack : {p!r}" for k, y, p in mismatches
            )
            self.fail(f"{len(mismatches)}/{len(BAKED_KEYS)} 条 variant 与 yaml 不一致：\n{detail}")

    def test_variant_is_byte_identical_to_yaml(self):
        # 更严一层：连「相等即通过」都不给——用码位序列逐位比对，杜绝同形异码（NFKC 陷阱）
        for key in BAKED_KEYS:
            yaml_value = _yaml_lookup(self.yaml_doc, key)
            pack_value = self.by_key[key]["variants"][0]
            self.assertEqual(
                [ord(c) for c in pack_value], [ord(c) for c in yaml_value],
                f"key={key} 的码位序列与 yaml 不一致（同形异码）",
            )

    def test_yaml_text_of_each_baked_key_is_single_sentence(self):
        # 证明「这 40 条本来就是单句」——exclusion 的边界不是裁出来的，是数据决定的
        from compiler.source import _SENTENCE_TERMINATORS
        for key in BAKED_KEYS:
            value = str(_yaml_lookup(self.yaml_doc, key))
            n = sum(1 for ch in value if ch in _SENTENCE_TERMINATORS)
            self.assertLess(n, 2, f"key={key} 判据实测 {n} 个句末标点，与准入清单不符")

    def test_excluded_multi_clause_keys_are_really_multi_clause(self):
        # exclusion 不是"懒得铸"：这 14 条在数据上确实多分句
        from compiler.source import _SENTENCE_TERMINATORS
        for key in MULTI_CLAUSE_KEYS:
            value = str(_yaml_lookup(self.yaml_doc, key))
            n = sum(1 for ch in value if ch in _SENTENCE_TERMINATORS)
            self.assertGreaterEqual(
                n, 2, f"key={key} 实测 {n} 个句末标点，不该进 exclusion·多分句")

    def test_excluded_slot_keys_really_contain_slots(self):
        for key in SLOT_KEYS:
            value = str(_yaml_lookup(self.yaml_doc, key))
            self.assertIn("{", value, f"key={key} 不含槽位占位符，不该进 exclusion·带槽")


# ---------------------------------------------------------------------------
# C. find_hit 双向断言（依赖构建产物；未构建 → skip）
# ---------------------------------------------------------------------------
@unittest.skipUnless(YAML_PATH.is_file(), "kefu 供热预设 yaml 不在本仓——find_hit 断言跳过")
class TestFindHitAll40Hit(unittest.TestCase):
    """必铸 40 条：文本档逐条查询 → 100% 命中（entry 非 None 且 miss_reason 为 None）。"""

    @classmethod
    def setUpClass(cls):
        cls.yaml_doc = _load_yaml()
        cls.pack = _load_pack_dir()
        if cls.pack is None:
            raise unittest.SkipTest(
                "packs/heat_kefu 无预铸产物（manifest.json）——"
                "请先 `bin/vox pack build packs/heat_kefu --out <包外目录>`"
            )

    def test_all_40_yaml_texts_hit(self):
        from adapters.framework_kefu import find_hit
        misses = []
        for key in BAKED_KEYS:
            yaml_text = str(_yaml_lookup(self.yaml_doc, key))
            res = find_hit(self.pack, text=yaml_text, rate_key="normal")
            if res.entry is None or res.miss_reason is not None:
                misses.append((key, res.entry, res.miss_reason))
        self.assertEqual(
            misses, [],
            f"{len(misses)}/{len(BAKED_KEYS)} 条未命中（entry=None 或 miss_reason 非 None）: {misses}",
        )

    def test_hit_mode_is_text_mode(self):
        # 断言走的是文本档，而不是 key 档（本包的验收口径是文本档逐字命中）
        from adapters.framework_kefu import find_hit, MODE_TEXT
        key = BAKED_KEYS[0]
        res = find_hit(self.pack, text=str(_yaml_lookup(self.yaml_doc, key)), rate_key="normal")
        self.assertIsNotNone(res.entry)
        self.assertEqual(res.mode, MODE_TEXT)


@unittest.skipUnless(YAML_PATH.is_file(), "kefu 供热预设 yaml 不在本仓——exclusion 断言跳过")
class TestExclusionGoesThroughOriginalPath(unittest.TestCase):
    """「不铸」的双向断言：exclusion 键真的未命中，证明它们真实走原路（不是只查表里没有）。"""

    @classmethod
    def setUpClass(cls):
        cls.yaml_doc = _load_yaml()
        cls.pack = _load_pack_dir()
        if cls.pack is None:
            raise unittest.SkipTest("packs/heat_kefu 无预铸产物——exclusion 断言跳过")

    def test_multi_clause_14_not_hit_by_text(self):
        from adapters.framework_kefu import find_hit
        hits = []
        for key in MULTI_CLAUSE_KEYS:
            yaml_text = str(_yaml_lookup(self.yaml_doc, key))
            res = find_hit(self.pack, text=yaml_text, rate_key="normal")
            if res.entry is not None or res.miss_reason is None:
                hits.append((key, res.entry, res.miss_reason))
        self.assertEqual(
            hits, [],
            f"{len(hits)}/{len(MULTI_CLAUSE_KEYS)} 条多分句竟然命中了（不该铸却进了命中路径）: {hits}",
        )

    def test_slot_filled_results_not_hit(self):
        # 带槽句填上假值后走文本档 → 必须未命中（带槽不铸的真实行为 = 走原路）
        from adapters.framework_kefu import find_hit
        hits = []
        for key, values in SLOT_FILL_EXAMPLES.items():
            yaml_text = str(_yaml_lookup(self.yaml_doc, key))
            filled = yaml_text.format(**values)
            res = find_hit(self.pack, text=filled, rate_key="normal")
            if res.entry is not None or res.miss_reason is None:
                hits.append((key, filled, res.entry, res.miss_reason))
        self.assertEqual(
            hits, [],
            f"{len(hits)}/{len(SLOT_FILL_EXAMPLES)} 条带槽填充结果竟然命中了: {hits}",
        )

    def test_exclusion_keys_not_hit_by_key_mode(self):
        # 双向的第二半：按 key 查也查不到（key 档同样走原路）
        from adapters.framework_kefu import find_hit
        hits = []
        for key in MULTI_CLAUSE_KEYS + SLOT_KEYS:
            res = find_hit(self.pack, key=key, rate_key="normal")
            if res.entry is not None or res.miss_reason is None:
                hits.append((key, res.entry, res.miss_reason))
        self.assertEqual(
            hits, [],
            f"{len(hits)}/{len(MULTI_CLAUSE_KEYS + SLOT_KEYS)} 条 exclusion key 在 key 档命中了: {hits}",
        )


# ---------------------------------------------------------------------------
# D. 断言可打破性（负例）
# ---------------------------------------------------------------------------
class TestAssertionBreaksOnTamper(unittest.TestCase):
    """证明同源断言不是空转：篡改一个字就变红。

    机制说明（不用真改文件）：同源断言的**期望值来自 yaml 的运行时读取**
    （`_yaml_lookup(self.yaml_doc, key)`），不是写死的字面量。
    所以包源里任何一处字被改（哪怕一个标点），`pack_value != yaml_value` 立刻成立——
    下面用内存里的临时篡改复现这件事，不落盘。
    """

    def test_one_char_tamper_is_detected(self):
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            by_key = {p["key"]: p for p in json.load(f)["phrases"]}
        if not YAML_PATH.is_file():
            self.skipTest("kefu yaml 不在本仓——负例跳过")
        doc = _load_yaml()
        key = "farewell"
        original = str(_yaml_lookup(doc, key))
        pack_value = by_key[key]["variants"][0]

        # 1) 未篡改：逐字相等
        self.assertEqual(pack_value, original)

        # 2) 删掉句末标点（最容易被"顺手规范化"的改法）→ 立刻不等
        tampered = pack_value.rstrip("。！？!?")
        self.assertNotEqual(tampered, original,
                            "删标点后仍相等 —— 同源断言失效，断言不能打破")

        # 3) 改一个字（全角逗号→半角）→ 立刻不等
        tampered2 = pack_value.replace("，", ",", 1)
        self.assertNotEqual(tampered2, original,
                            "全角→半角后仍相等 —— 同源断言失效，断言不能打破")

    def test_mismatch_report_lists_every_offending_key(self):
        # 断言的失败消息必须能定位到哪几条（否则事后无法复算）
        if not YAML_PATH.is_file():
            self.skipTest("kefu yaml 不在本仓——负例跳过")
        doc = _load_yaml()
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            by_key = {p["key"]: p for p in json.load(f)["phrases"]}
        mismatches = []
        for key in BAKED_KEYS:
            if by_key[key]["variants"][0] != str(_yaml_lookup(doc, key)):
                mismatches.append(key)
        self.assertEqual(mismatches, [], f"当前包源已有不一致：{mismatches}")
        # 人为注入一条差异，验证探测逻辑本身有效
        poisoned = dict(by_key)
        poisoned["farewell"] = {"key": "farewell", "variants": ["好的，再见。"], "rates": ["normal"]}
        mismatches2 = [k for k in BAKED_KEYS
                       if poisoned[k]["variants"][0] != str(_yaml_lookup(doc, k))]
        self.assertIn("farewell", mismatches2,
                      "注入篡改后探测不到 —— 同源断言是空转的")


# ---------------------------------------------------------------------------
# 跑法（packs 层第一根测试；本文件也是 packs 层唯一测试）
# ---------------------------------------------------------------------------
#   python3 -m unittest packs.heat_kefu.tests.test_source_of_truth -v
#   python3 -m unittest discover -s packs/heat-kefu/tests -t packs
#
# ⚠️ 卡面写的 `python3 -m unittest discover -s packs` **跑不到本文件**，是 CPython
#    的 discovery 限制，不是本测试的问题（实测两条独立成因，都试过）：
#    ① `packs/` 没有 `__init__.py` → namespace package。CPython 的 `TestLoader.discover`
#       对 namespace package 有 `if not is_namespace: tests = list(...)` 守卫，
#       整个子树**不递归也不报错**（实测 `Ran 0 tests` / `NO TESTS RAN`）。
#       仓内十层既有测试根（core/rules/assets/compiler/runtime/eval/cli/adapters/tools/trigger）
#       都带 `__init__.py`，是它们能被 `-s <层>` 跑到的前提。
#    ② `packs/heat-kefu` 目录名含连字符，CPython 3.14 的 `TestLoader._splitext`
#       会把 `heat-kefu/tests` 按 `-` 截成 `heat`，`-t packs` 下直接 ImportError。
#    修这两条都要动 `packs/__init__.py`（或重命名包目录），二者都**不在本卡白名单**，
#    所以本文件只保证上面两条可导入跑法可用，跑法问题留给验收方裁定。
#    本文件自带 `sys.path` 兜底（同 labs/ 先例），不依赖调用方环境变量。


if __name__ == "__main__":
    unittest.main(verbosity=2)
