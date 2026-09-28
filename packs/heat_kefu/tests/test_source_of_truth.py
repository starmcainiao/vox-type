"""
packs.heat-kefu.tests.test_source_of_truth — 「话术同源」机器证明（T31b，packs 层第一根测试）

职责：用 yaml 原文做**动态期望值**，证明本包 40 条 variant 与 kefu 供热预设 yaml **逐字相等**，
      并证明「不铸」的两类（多分句 14 + 带槽 13）在真包上真的走原路（find_hit 未命中）。
不负责：不做语义分析、不复制比对器、不改动 yaml / 包源 / 编译器。

「同源」的全部价值就在逐字 `==`：一个标点被改写，命中路径就会播出「差不多但不是这句」的话，
      事后无法定位（仓库头号红线：禁止静默降级）。所以本文件里没有任何归一化后的比较——
      归一化相等 ≠ 同源（docs/14 的教训）。

「依赖 yaml 的测试」要真跑需要**两个**前置条件，本文件对它们各有一个判据，
      合取成立才跑（判据集中在一处：`_yaml_guard()`，见下）：
        ① yaml **文件**可达 —— `YAML_PATH.is_file()`（只探测存在性，不读内容）；
        ② yaml **解析器**可导入 —— `_yaml_module()`（`try import yaml`，缺库返回 `None`）。
      缺**任一** → 该组断言诚实 **skip**（不是 fail，也不换路径、不降级）；
      skip 消息指名**当前缺失的那一个**，所以「文件不在」与「没装库」分得开。
      包源自身的结构测试（key 集合、单句判据、不含槽位）不依赖上面任何一个，照常跑。

      PyYAML 是**测试期可选依赖，不是产品依赖**：本仓 `import yaml` 只出现在本文件
      与 `labs/` 的一次性实验里，产品代码（`core/ rules/ compiler/ assets/ runtime/
      cli/ adapters/ trigger/ packs/` 的非测试代码）不 import yaml。所以 `README.md`
      的「零第三方依赖」说的是**产品**，与装不装 PyYAML 无关、互不冲突；没装 PyYAML
      的机器上 B/C/D/E 段是 skip 而不是红，装上（`pip install pyyaml`，装到隔离目录
      再用 `PYTHONPATH` 指过来即可，不必装进解释器）就真跑。
"""

import hashlib
import json
import os
import re
import sys
import unittest
from pathlib import Path

# 仓库根兜底（同 labs/ 的先例：脚本必须自带 sys.path 兜底，不依赖调用方环境变量）
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PACK_DIR = REPO_ROOT / "packs" / "heat_kefu"

# ---------------------------------------------------------------------------
# yaml 原文路径：多候选探测（数据分级：公开级，docs/15 §四 已拍板）
# ---------------------------------------------------------------------------
# 为什么不写死一条路径：kefu 仓是**仓外**私有仓，位置随开发机而异。历史上这里只拼了
#   `<repo>/../kefu-agent/...` 一条，本机真实仓在别处 → 24 条同源断言**恒 skip**，
#   即项目唯一的生产性证据（真链路 sha256 字节级命中）在默认环境下从不执行
#   （docs/13 §五#20 登记为低 severity，实际比登记的更硬）。
#
# 约束（两条红线）：
#   ① **不许写死机器绝对路径**（docs/13 §八#20 的发布树红线）——所有候选都是
#      相对本仓推导或走环境变量。
#   ② **不许用假路径骗过 skip**——探测只影响 YAML_PATH 取哪一条，不影响「判据不通过就
#      skip」这个行为。skip 的判据是**两个前置条件的合取**（见 `_yaml_guard()`）：
#      ① 文件可达（`YAML_PATH.is_file()`）② 解析器可导入（`_yaml_module()`），
#      缺任一即 skip——写假路径、装个假库都换不来真跑。
#
# 候选（按序，取第一个真实存在的）：
#   ① $KEFU_HEAT_YAML             —— 显式指定，优先级最高
#   ② <repo>/../kefu-agent/...    —— 历史缺省，保留兼容
#   ③ <repo>/../../kefu-agent/... —— docs/13 §五#20 的第二候选（kefu 仓与本仓同级）
#   ④ $HOME/kefu-agent/...        —— 常见位置
#
# **显式指定即权威（关键语义）**：设了 $KEFU_HEAT_YAML 就只认它，**不回退**去试 ②③④。
#   回退会把「用户指定的文件不存在」悄悄换成「另一个文件」——那是静默降级
#   （项目纪律 4），比不跑更坏。所以显式指定失效时走 **skip**（诚实 skip）：
#   是修环境变量还是撤掉它，由人决定，不由代码猜。
# 全部候选都不存在 → 同样诚实 skip（公开 CI 没有 kefu 仓，skip 是正确行为，不是失败）。
_YAML_REL_PARTS = ("organs", "客服", "brain", "prompts", "供热预设.yaml")


def _yaml_candidates(env=None):
    """按优先级列出 yaml 候选路径。只拼路径、不读内容（数据分级：仅做存在性探测）。

    env 缺省取 os.environ（便于把「设了/没设显式指定」两种情形都走同一份逻辑）。
    """
    env = os.environ if env is None else env
    cands = []
    explicit = env.get("KEFU_HEAT_YAML")
    if explicit:
        cands.append(Path(explicit))  # ① 显式指定：唯一被采信的那条（见上方「显式指定即权威」）
    rel = Path(*_YAML_REL_PARTS)
    cands.append(REPO_ROOT.parent / "kefu-agent" / rel)  # ②
    cands.append(REPO_ROOT.parent.parent / "kefu-agent" / rel)  # ③
    home = env.get("HOME")
    if home:
        cands.append(Path(home) / "kefu-agent" / rel)  # ④
    return cands


def _resolve_yaml_path(env=None):
    """取生效的 yaml 路径。

    **显式指定即权威**：设了 $KEFU_HEAT_YAML 就只返回它，绝不因它不存在而回退去试
    ②③④——那会让「用户指定的文件失效」被悄悄换成「另一个文件」（静默降级，纪律 4）。
    未设时才按候选顺序取第一个真实存在的；都不存在则返回最高优先级那条。

    返回的那条若不存在，`YAML_PATH.is_file()` 为假 → 前置条件 ① 不成立 →
    依赖 yaml 的测试按设计 skip（判据在 `_yaml_guard()`，不 fail、不换文件、不降级）。
    """
    cands = _yaml_candidates(env)
    if (os.environ if env is None else env).get("KEFU_HEAT_YAML"):
        return cands[0]  # 显式指定：唯一被采信的那条，存在与否都如实反映
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


YAML_PATH = _resolve_yaml_path()  # kefu 预设原文；仅探测到时才做同源断言，否则按设计 skip


def _yaml_module():
    """探测 yaml 解析器是否可导入：可导入返回模块对象，缺库返回 `None`。

    与 `_resolve_yaml_path` 对称：一个只判「文件在不在」，一个只判「库装没装」，
    两者都只做探测、不读任何内容（数据分级：存在性 / 可导入性探测）。
    """
    try:
        import yaml
    except ModuleNotFoundError:
        return None
    return yaml


# yaml 解析器：缺库时是 None（只影响依赖 yaml 的测试，A 段包源结构测试照常跑）
YAML_MODULE = _yaml_module()


def _yaml_guard():
    """「依赖 yaml 的测试」的 skip 判据：两个前置条件的**合取**，缺任一即 skip。

        ① `YAML_PATH.is_file()`  —— yaml 文件可达（公开 CI 没有 kefu 仓）；
        ② `YAML_MODULE is not None` —— yaml 解析器可导入（PyYAML，测试期可选依赖）。

    返回 `None` 表示两个前置都满足（不 skip）；否则返回**当前缺失的那一个**的说明，
    直接当 skip 消息用——「文件不在」与「没装库」在这条路径上分得开。

    刻意**不**在 `_load_yaml()` 里 catch `ModuleNotFoundError` 再 `raise unittest.SkipTest`：
    那样两种情形都会堆在 `setUpClass` 里变成 skip，事后看不出缺的是哪一个（docs/13 里
    「假绿比红更坏」的另一面——看不出来比红更糟）。判据集中在这一处，`skipUnless` 与
    `skipTest` 都走它，不留第二份判据。
    """
    if not YAML_PATH.is_file():
        return "kefu 供热预设 yaml 文件不在候选路径（公开 CI 没有 kefu 仓）"
    if YAML_MODULE is None:
        return "未安装 PyYAML（测试期可选依赖，非产品依赖）"
    return None


def _yaml_skip_msg(what):
    """skip 消息：「哪个前置条件缺失」+ 调用方给的断言组名 `what`（可空串）。

    **恒返回 `str`**——两个前置条件都满足时返回空串，让 `skipUnless` 的条件为真、
    消息字段始终是合法的 `str`。
    刻意**不做条件字符串拼接**：把 `_yaml_guard()` 的返回值（可能为 `None`）直接当
    `+` 的操作数，会在两个前置条件都在时于**类定义期**抛 `TypeError`，整个模块
    import 失败，本文件一条断言都跑不了——门禁不是「跳过」而是「不起作用」。
    """
    reason = _yaml_guard()
    if not reason:
        return ""
    return reason if not what else f"{reason}——{what}"


YAML_RUNNABLE = _yaml_guard() is None  # 类级 skipUnless 的条件（恒为 bool）

# yaml 指纹：变更即钩子自动不命中（fail-closed），admission.md 文首有同一份记录
# 2026-09-28 T53 重对账：T31 基线（kefu 侧 commit 3b296a2）之后上游连改 4 次
#   （d59255a/9fc9cd4 9-23 报修话术落题面锚点 · bd0ae3d 9-24 模糊澄清守卫+转人工三拆表
#     + 拒答去内部字样 · e16a777 9-24 拒答泄露内部实现修复），
#   本指纹在**三张清单逐条对账 + phrases.json 拆句边界同步**完成之后才更新（T53 卡 §三.3 顺序要求）。
#   旧基线指纹值**不在本文件保留**（T53 AC2 要求零命中，避免两份"当前有效"指纹并存）。
#   本包拆句清单目前是**人工产物**（见 packs/AGENTS.md「拆句清单的来源与漂移处理」），
#   下次上游再改仍需按 T53 的同一顺序重做；方案 A（拆句从 yaml 现算）未做。
YAML_SHA256 = "d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652"


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

# T33：14 条多分句整段按 `__1..__N` 拆句铸入（29 条 key）——
#   整段本身仍不在包内（`find_hit` 对整段不回归未命中），
#   整段由 `find_hit_sequence` 逐段覆盖命中（docs/10 §10.7）。
#
# ⚠️ **本清单是人工产物，不是机器产物**（T53 裁定，见 packs/AGENTS.md）：
#   `__N` 的个数与顺序是 T33 执行时按 yaml 手推的，与 yaml 的失配**不会**由本清单自查发现，
#   只能靠「拼回原文 == yaml 原文」和「每段是 yaml 连续子串」两条断言 + 指纹门禁报红。
#   2026-09-28 T53 重对账实测：14 个源 key 的段数与 key 集合**均未变**（仍是 29 条），
#   漂移的只是 3 段**文本**（`repair_confirm_question__1/__2`、`inject_refuse__1`），
#   已按当前 yaml 现算同步进 phrases.json。方案 A（自动拆句）**未做**。
SEQ_KEYS = (
    "opening__1", "opening__2",
    "chat_smalltalk__1", "chat_smalltalk__2",
    "clarify_work_order__1", "clarify_work_order__2",
    "clarify_repair__1", "clarify_repair__2", "clarify_repair__3",
    "fallback_internal__1", "fallback_internal__2",
    "lifeboat_fallback__1", "lifeboat_fallback__2",
    "user_no_unknown__1", "user_no_unknown__2",
    "repair_confirm_question__1", "repair_confirm_question__2",
    "work_order_no_unknown__1", "work_order_no_unknown__2",
    "internal_chat_hint__1", "internal_chat_hint__2",
    "stop_warm_plan__1", "stop_warm_plan__2",
    "inject_refuse__1", "inject_refuse__2",
    "repair_ask_natural_address__1", "repair_ask_natural_address__2",
    "repair_ask_natural_userNo__1", "repair_ask_natural_userNo__2",
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
    # T53 重对账：`repair_create_ok` 上游新增槽位 `{rawType}`（bd0ae3d），假值表补齐。
    # 这是**补齐夹具**不是迁就数据——断言本身没动：填完的文本仍必须查不到包。
    # 假值沿用同表的"明显不是真实会话"口径。
    "repair_create_ok": {"orderNo": "R99999999", "rawType": "示例类型"},
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
    """读 kefu 供热预设 yaml 原文（动态期望值的唯一来源）。

    本函数**不做** skip 判断——前置条件由 `_yaml_guard()` 在调用方判（文件可达 +
    解析器可导入）。正常路径下走到这里时两个前置都已成立。
    """
    if YAML_MODULE is None:
        # 判据与实现分离后的兜底：某个调用方漏了 `_yaml_guard()` 的 skip 判据，
        # 就让它在这里**指名报错**，而不是抛一个和「文件不在」长得一样的错。
        raise AssertionError(
            "YAML_MODULE 为 None（未安装 PyYAML）却调用了 _load_yaml()——"
            "调用方漏了 `_yaml_guard()` 的 skip 判据（fail-closed：不猜、不降级）"
        )
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        return YAML_MODULE.safe_load(f)


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

    def test_pack_is_69_keys_unique(self):
        # 40 单句（T31b）+ 29 拆句（T33）= 69，无重复
        self.assertEqual(len(self.phrases), 69)
        self.assertEqual(len(self.by_key), 69, "phrases.json 里 key 有重复")

    def test_pack_key_set_matches_baked_and_sequence_lists(self):
        # 既不能多也不能少：40 单句 + 29 拆句
        self.assertEqual(set(self.by_key), set(BAKED_KEYS) | set(SEQ_KEYS))
        self.assertEqual(len(SEQ_KEYS), 29, "拆句清单必须是 29 条")

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

    def test_variant_texts_are_distinct_except_the_known_duplicate(self):
        # 逐字同源的另一半：两条 variant 不该撞成同一串（撞了说明铸重了）。
        # 唯一豁免（T33 卡内点名的对账事实）：`点下方按钮或直接说明即可。` 同属
        # clarify_work_order__2 与 clarify_repair__3 —— 两条归属都明确，包内允许同文本
        # 两条资产（文本档索引取首条是既有行为）。所以断言的是"除这对之外无重复"，
        # 而不是无脑放宽成"允许重复"。
        texts = [p["variants"][0] for p in self.phrases]
        dup_keys = []
        seen = {}
        for p in self.phrases:
            t = p["variants"][0]
            if t in seen:
                dup_keys.append((seen[t], p["key"]))
            else:
                seen[t] = p["key"]
        self.assertEqual(
            dup_keys, [("clarify_work_order__2", "clarify_repair__3")],
            f"除已知豁免对之外的重复 variant: {dup_keys}",
        )
        self.assertEqual(len(texts), 69)
        self.assertEqual(len(set(texts)), 68)

    def test_script_covers_all_baked_keys(self):
        with open(PACK_DIR / "script.json", "r", encoding="utf-8") as f:
            script = json.load(f)
        used = {u["key"] for u in script["units"]}
        self.assertEqual(used, set(BAKED_KEYS) | set(SEQ_KEYS),
                       "剧本未覆盖全部包内 key（40 单句 + 29 拆句）")

    def test_exclusion_keys_are_absent_from_pack(self):
        # 整段源 key 仍不得进包（拆句以 `<源 key>__<句序>` 独立铸入）
        for k in MULTI_CLAUSE_KEYS + SLOT_KEYS:
            self.assertNotIn(k, self.by_key, f"exclusion key 被铸进了包: {k}")

    def test_sequence_keys_are_exactly_the_14_sources(self):
        """拆句 key 命名 = `<源 key>__<句序>`，句序连续从 1 起、覆盖 14 个源 key。"""
        from compiler.source import _SENTENCE_TERMINATORS
        for k in SEQ_KEYS:
            base, _, n = k.rpartition("__")
            self.assertIn(base, MULTI_CLAUSE_KEYS, f"{k} 的源 key 不在多分句清单里")
            self.assertTrue(n.isdigit() and int(n) >= 1, f"{k} 句序非法")
        by_source = {}
        for k in SEQ_KEYS:
            base, _, n = k.rpartition("__")
            by_source.setdefault(base, []).append(int(n))
        self.assertEqual(len(by_source), 14, "必须正好覆盖 14 个源 key")
        for base, ns in by_source.items():
            self.assertEqual(sorted(ns), list(range(1, len(ns) + 1)),
                             f"{base} 的句序不连续: {sorted(ns)}")

    def test_sequence_variants_cover_the_source_verbatim(self):
        """拆句同源（不依赖 yaml 的半套）：`''.join(拆句 variant)` 必须等于
        按 `_SENTENCE_TERMINATORS` 切出来的同一批段 —— 即 join 恒等于输入。

        完整的「== yaml 原文」断言见 TestSourceOfTruthVerbatim（需要 yaml）。
        这里验的是拆句的**可逆性**：切完再拼回去一字不差。
        """
        from compiler.source import _SENTENCE_TERMINATORS

        def split_sentences(text):
            out, cur = [], ""
            for ch in text:
                cur += ch
                if ch in _SENTENCE_TERMINATORS:
                    out.append(cur)
                    cur = ""
            if cur:
                out.append(cur)
            return out

        by_key = self.by_key
        for k in SEQ_KEYS:
            base, _, n = k.rpartition("__")
            parts = [by_key[f"{base}__{i}"]["variants"][0] for i in
                     range(1, sum(1 for kk in SEQ_KEYS if kk.rpartition("__")[0] == base) + 1)]
            # 每段自己都是一句（编译器同一判据）
            for part in parts:
                self.assertLess(sum(1 for ch in part if ch in _SENTENCE_TERMINATORS), 2,
                                f"{base} 的拆句不是一句: {part!r}")
            # 可逆性：拼回去还是原来那串
            joined = "".join(parts)
            self.assertEqual(split_sentences(joined), parts,
                             f"{base} 拆句不可逆（拼回后重新切分不一致）")


# ---------------------------------------------------------------------------
# B. 同源断言（依赖 yaml；`_yaml_guard()` 两个前置条件缺任一 → skip）
# ---------------------------------------------------------------------------
@unittest.skipUnless(YAML_RUNNABLE, _yaml_skip_msg("同源断言跳过"))
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

    def test_sequence_split_concatenates_back_to_yaml(self):
        """拆句同源（验收 2）：包内 `__1..__N` 的 variant 依次拼接 == yaml 原文，
        逐码位比对（`[ord(c) for c in …]`）——一个标点、一个全角码位都不能差。"""
        from compiler.source import _SENTENCE_TERMINATORS
        for base in MULTI_CLAUSE_KEYS:
            yaml_value = str(_yaml_lookup(self.yaml_doc, base))
            expected_n = sum(1 for ch in yaml_value if ch in _SENTENCE_TERMINATORS)
            pack_parts = [self.by_key[f"{base}__{i}"]["variants"][0]
                          for i in range(1, expected_n + 1)]
            joined = "".join(pack_parts)
            self.assertEqual(
                [ord(c) for c in joined], [ord(c) for c in yaml_value],
                f"key={base} 拆句拼回与 yaml 原文不一致（逐码位）",
            )

    def test_sequence_split_count_matches_yaml_terminator_count(self):
        """N == 该 yaml 原文按 `_SENTENCE_TERMINATORS` 的实际句数（不多不少）。"""
        from compiler.source import _SENTENCE_TERMINATORS
        for base in MULTI_CLAUSE_KEYS:
            yaml_value = str(_yaml_lookup(self.yaml_doc, base))
            expected_n = sum(1 for ch in yaml_value if ch in _SENTENCE_TERMINATORS)
            actual = [int(k.rpartition("__")[2]) for k in SEQ_KEYS
                      if k.rpartition("__")[0] == base]
            self.assertEqual(sorted(actual), list(range(1, expected_n + 1)),
                             f"key={base} 拆句数 {sorted(actual)} != yaml 句数 {expected_n}")

    def test_sequence_segments_are_verbatim_substrings_of_yaml(self):
        """每一段都必须逐字出现在 yaml 原文里（含标点与引号），且是连续子串。"""
        for base in MULTI_CLAUSE_KEYS:
            yaml_value = str(_yaml_lookup(self.yaml_doc, base))
            for k in SEQ_KEYS:
                if k.rpartition("__")[0] != base:
                    continue
                segment = self.by_key[k]["variants"][0]
                self.assertIn(segment, yaml_value, f"{k} 不是 yaml 原文的连续子串")

    def test_excluded_slot_keys_really_contain_slots(self):
        for key in SLOT_KEYS:
            value = str(_yaml_lookup(self.yaml_doc, key))
            self.assertIn("{", value, f"key={key} 不含槽位占位符，不该进 exclusion·带槽")


# ---------------------------------------------------------------------------
# C. find_hit 双向断言（依赖 yaml + 构建产物；`_yaml_guard()` 缺任一 → skip，未构建 → skip）
# ---------------------------------------------------------------------------
@unittest.skipUnless(YAML_RUNNABLE, _yaml_skip_msg("find_hit 断言跳过"))
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


@unittest.skipUnless(YAML_RUNNABLE, _yaml_skip_msg("exclusion 断言跳过"))
class TestExclusionGoesThroughOriginalPath(unittest.TestCase):
    """「不铸」的双向断言：exclusion 键真的未命中，证明它们真实走原路（不是只查表里没有）。"""

    @classmethod
    def setUpClass(cls):
        cls.yaml_doc = _load_yaml()
        cls.pack = _load_pack_dir()
        if cls.pack is None:
            raise unittest.SkipTest("packs/heat_kefu 无预铸产物——exclusion 断言跳过")

    def _expected_segment_keys(self, base):
        """期望的段 key 序列：按 §10.7 的冻结判据从**包内索引顺序**推导。

        「同长度多候选取索引内首条」意味着某一段若与另一 key 的 variant 归一化后
        同文本，会取**先在 pack.assets 里出现**的那个 key。所以期望值不是
        写死的 `__1..__N`，而是「逐段切出来的文本 + 索引首条」推出来的：
        断言验的是「段数正确 + 每段文本正确 + 段序正确」，
        同文本取首条带来的 key 差异单独断言（见 test_duplicate_segment_text_takes_first_key）。
        """
        import json as _json
        from adapters.framework_kefu import normalize_text
        from compiler.source import _SENTENCE_TERMINATORS
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            phrases = json.load(f)["phrases"]
        # 索引顺序 = phrases.json 顺序 = pack.assets 顺序
        first_key_by_norm = {}
        for ph in phrases:
            n = normalize_text(ph["variants"][0])
            first_key_by_norm.setdefault(n, ph["key"])
        doc = self.yaml_doc
        parts = []
        cur = ""
        for ch in str(_yaml_lookup(doc, base)):
            cur += ch
            if ch in _SENTENCE_TERMINATORS:
                parts.append(cur)
                cur = ""
        if cur:
            parts.append(cur)
        return [first_key_by_norm[normalize_text(part)] for part in parts]

    def test_duplicate_segment_text_takes_first_key(self):
        """同文本两 key 的已知行为：`点下方按钮或直接说明即可。` 同时是
        clarify_work_order__2 与 clarify_repair__3，序列命中取**索引首条**。

        断言的是「取首条 + 段数不变」，不是「必须是 clarify_repair__3」——
        前者是 docs/10 §10.7 的冻结判据，后者取决于包内排布（实现细节）。
        """
        from adapters.framework_kefu import find_hit_sequence, normalize_text
        text = "点下方按钮或直接说明即可。"
        res = find_hit_sequence(self.pack, text)
        self.assertIsNone(res.miss_reason)
        self.assertEqual(len(res.entries), 1)
        self.assertEqual(res.entries[0].text, text)
        self.assertIn(res.entries[0].key, {"clarify_work_order__2", "clarify_repair__3"},
                      "同文本候选只能是这两条之一")
        # 索引首条稳定性：连跑 10 次取同一条
        seen = {r.entries[0].key for r in
                (find_hit_sequence(self.pack, text) for _ in range(10))}
        self.assertEqual(len(seen), 1, f"同长度多候选取首条不稳定: {seen}")
        self.assertEqual(
            normalize_text(res.entries[0].text), normalize_text(text))

    def test_multi_clause_14_hit_by_sequence_with_expected_key_order(self):
        """T33 的正例：14 条整段经 find_hit_sequence 命中，段数 == 句数，
        段 key 顺序 == `__1..__N`。"""
        from adapters.framework_kefu import find_hit_sequence
        bad = []
        for base in MULTI_CLAUSE_KEYS:
            yaml_text = str(_yaml_lookup(self.yaml_doc, base))
            expected_n = len([k for k in SEQ_KEYS if k.rpartition("__")[0] == base])
            res = find_hit_sequence(self.pack, yaml_text, rate_key="normal")
            if res.miss_reason is not None or res.uncovered != "":
                bad.append((base, "miss", res.miss_reason, res.uncovered))
                continue
            got = [e.key for e in res.entries]
            want = self._expected_segment_keys(base)
            if got != want or len(got) != expected_n:
                bad.append((base, f"段序不符 want={want} got={got}"))
        self.assertEqual(bad, [], f"{len(bad)}/{len(MULTI_CLAUSE_KEYS)} 条整段未按预期序列命中: {bad}")

    def test_sequence_hit_entries_all_have_rate_normal(self):
        """命中序列里的每条 entry 都必须是 normal 档（本包只有 normal 档）。

        空转修正（T33b P2-b）：原写法是 `if res.miss_reason is None:` 包裹断言——
        序列档一旦整体 miss，断言体被整段跳过、测试仍绿。现在对每条输入**无条件**
        断言期望的 hit/miss：命中则断言 miss_reason is None + 段数 + 每条 normal 档。
        """
        from adapters.framework_kefu import find_hit_sequence
        for base in MULTI_CLAUSE_KEYS:
            res = find_hit_sequence(self.pack, str(_yaml_lookup(self.yaml_doc, base)))
            self.assertIsNone(
                res.miss_reason,
                f"{base} 必须整体命中（实际 miss_reason={res.miss_reason!r}, "
                f"uncovered={res.uncovered!r}）",
            )
            self.assertGreater(len(res.entries), 0, f"{base} 命中但 entries 为空元组")
            self.assertEqual(
                [e.rate_key for e in res.entries], ["normal"] * len(res.entries),
                f"{base} 命中了非 normal 档条目",
            )

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

    @classmethod
    def _by_key(cls):
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            return {p["key"]: p for p in json.load(f)["phrases"]}

    def test_multi_clause_negative_cases_are_full_miss_not_partial(self):
        """T33 的负例（fail-closed）：整段删标点 / 中间插包外文本 / 未预铸的整段
        → entries 必须是**空元组**（不得部分命中），miss_reason 具体。

        两条负例都构造在**整段**级别：因为 T33 把每段单独铸入了，
        拿拆出来的段去拼再颠倒仍能逐字覆盖（段序自由是设计事实，见
        test_swap_two_segments_is_not_partial_hit），那测的不是 fail-closed。
        """
        from adapters.framework_kefu import find_hit_sequence, REASON_TEXT_NOT_PREBAKED
        from compiler.source import _SENTENCE_TERMINATORS

        def split_sentences(text):
            out, cur = [], ""
            for ch in text:
                cur += ch
                if ch in _SENTENCE_TERMINATORS:
                    out.append(cur)
                    cur = ""
            if cur:
                out.append(cur)
            return out

        bad = []
        for base in MULTI_CLAUSE_KEYS:
            yaml_text = str(_yaml_lookup(self.yaml_doc, base))
            parts = split_sentences(yaml_text)
            # ① 删第一个句末标点（整段第一个断点）
            first = next(ch for ch in yaml_text if ch in _SENTENCE_TERMINATORS)
            drop = yaml_text.replace(first, "", 1)
            # ③ 中间插一句包外文本（包里没有「这是一句包外的话。」）
            insert = parts[0] + "这是一句包外的话。" + "".join(parts[1:]) if parts else yaml_text

            for label, text in (("删句末标点", drop), ("中间插包外文本", insert)):
                res = find_hit_sequence(self.pack, text, rate_key="normal")
                if res.entries != () or res.miss_reason != REASON_TEXT_NOT_PREBAKED:
                    bad.append((base, label,
                                [e.key for e in res.entries],
                                res.miss_reason, res.uncovered[:60]))
        self.assertEqual(
            bad, [],
            f"{len(bad)} 条负例出现部分命中或原因码错误: {bad}",
        )

    def test_swap_two_segments_is_not_partial_hit(self):
        """段级重排的对账：`__2 + __1` 仍能被逐字覆盖（不允许"只播后半段"）。

        口径（docs/10 §10.7）：判据只有"逐字覆盖"一种，不做段序约束；
        而 T33 把每段单独铸入了，所以段的顺序交换后仍能逐段覆盖——
        这是**设计事实**，不是 fail-closed 失效。
        断言锁定三件事：整体命中（不是部分命中）、段数不缩水、
        段 key 恰好是被颠倒的那两条。
        """
        from adapters.framework_kefu import find_hit_sequence
        from compiler.source import _SENTENCE_TERMINATORS

        def split_sentences(text):
            out, cur = [], ""
            for ch in text:
                cur += ch
                if ch in _SENTENCE_TERMINATORS:
                    out.append(cur)
                    cur = ""
            if cur:
                out.append(cur)
            return out

        for base in MULTI_CLAUSE_KEYS:
            with self.subTest(key=base):
                by = self._by_key()
                yaml_text = str(_yaml_lookup(self.yaml_doc, base))
                n = len(split_sentences(yaml_text))
                k1 = f"{base}__1"
                k2 = f"{base}__2"
                tail = "".join(by[f"{base}__{i}"]["variants"][0] for i in range(3, n + 1))
                res = find_hit_sequence(self.pack, by[k2]["variants"][0]
                                        + by[k1]["variants"][0] + tail,
                                        rate_key="normal")
                self.assertIsNone(res.miss_reason, f"{base} 段级重排后应仍命中")
                self.assertEqual(len(res.entries), n,
                                 f"{base} 段级重排后段数应不缩水（期望 {n}，实际 {len(res.entries)}）")
                got = [e.key for e in res.entries]
                # 前两段必须按颠倒后的顺序出现（同文本取索引首条时 key 可能是别名，
                # 所以比的是「段文本序列」而不是「key 序列」；alias 情况另有专测）
                self.assertEqual(
                    [e.text for e in res.entries[:2]],
                    [by[k2]["variants"][0], by[k1]["variants"][0]],
                    f"{base} 段级重排后前两段文本必须是颠倒后的顺序（实际 {got}）",
                )

    def test_swap_whole_text_is_full_miss(self):
        """整段级重排（在包外）→ 整体未命中。

        与上一条对照：整段本身不在包内，位置 0 就覆盖不上，所以是 miss；
        而段的任意重排仍命中——两条合起来锁住了"逐字覆盖"这个判据的边界。
        """
        from adapters.framework_kefu import find_hit_sequence, REASON_TEXT_NOT_PREBAKED
        from compiler.source import _SENTENCE_TERMINATORS

        def split_sentences(text):
            out, cur = [], ""
            for ch in text:
                cur += ch
                if ch in _SENTENCE_TERMINATORS:
                    out.append(cur)
                    cur = ""
            if cur:
                out.append(cur)
            return out

        # 把整段当**未预铸**的长句：它的任一前缀都不等于包内任何 variant 的
        # 归一化文本（整段不在包内），所以位置 0 就断。
        for base in MULTI_CLAUSE_KEYS:
            with self.subTest(key=base):
                yaml_text = str(_yaml_lookup(self.yaml_doc, base))
                # 前缀截断：截到第一个句末标点前，剩下的串既不是任何 variant，
                # 也不能被任何 variant 前缀覆盖
                first = next(ch for ch in yaml_text if ch in _SENTENCE_TERMINATORS)
                cut = yaml_text[:yaml_text.index(first)] + yaml_text[yaml_text.index(first) + 1:]
                res = find_hit_sequence(self.pack, cut, rate_key="normal")
                self.assertEqual(res.entries, (),
                                 f"{base} 删标点后的整段必须整体未命中")
                self.assertEqual(res.miss_reason, REASON_TEXT_NOT_PREBAKED)

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
# D. 断言可打破性（负例；三条都依赖 yaml，按 `_yaml_guard()` 判定 skip）
# ---------------------------------------------------------------------------
@unittest.skipUnless(YAML_RUNNABLE, _yaml_skip_msg("序列命中确定性跳过"))
class TestSequenceHitDeterministic(unittest.TestCase):
    """同一整段连跑 10 次：段 key 序列与长度完全一致（同长度多候选取索引首条）。"""

    @classmethod
    def setUpClass(cls):
        cls.yaml_doc = _load_yaml()
        cls.pack = _load_pack_dir()
        if cls.pack is None:
            raise unittest.SkipTest("packs/heat_kefu 无预铸产物——序列命中断言跳过")

    def test_ten_runs_are_identical(self):
        from adapters.framework_kefu import find_hit_sequence
        for base in MULTI_CLAUSE_KEYS:
            yaml_text = str(_yaml_lookup(self.yaml_doc, base))
            seen = set()
            for _ in range(10):
                res = find_hit_sequence(self.pack, yaml_text, rate_key="normal")
                seen.add((len(res.entries), tuple(e.key for e in res.entries),
                          res.uncovered, res.miss_reason))
            self.assertEqual(len(seen), 1, f"{base} 连跑 10 次结果不一致: {seen}")


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
        if not YAML_RUNNABLE:
            self.skipTest(_yaml_skip_msg("负例跳过"))
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

    def test_sequence_one_char_tamper_is_detected(self):
        """拆句断言同样不空转：把某段改一个字，拼回就不等于 yaml 原文。"""
        if not YAML_RUNNABLE:
            self.skipTest(_yaml_skip_msg("拆句负例跳过"))
        doc = _load_yaml()
        with open(PACK_DIR / "phrases.json", "r", encoding="utf-8") as f:
            by_key = {p["key"]: p for p in json.load(f)["phrases"]}
        base = "clarify_repair"
        keys = [f"{base}__{i}" for i in range(1, 4)]
        parts = [by_key[k]["variants"][0] for k in keys]
        yaml_value = str(_yaml_lookup(doc, base))
        self.assertEqual("".join(parts), yaml_value, "未篡改时必须逐字相等")

        tampered = [parts[0], parts[1].rstrip("。！？!?"), parts[2]]
        self.assertNotEqual("".join(tampered), yaml_value,
                            "删句末标点后仍相等 —— 拆句断言失效，断言不能打破")
        # 注意：`.replace("。", "！")` 验不到断言灵敏度——normalize_text 把两者都归一成
        # `!`，归一化空间相等。所以这条改用**删字**（真正的字节差异）。
        tampered2 = [parts[0][:-1], parts[1], parts[2]]
        self.assertNotEqual("".join(tampered2), yaml_value,
                            "删一个字后仍相等 —— 拆句断言失效，断言不能打破")
        # 再补一条：把引号删掉（yaml 里的中文引号是逐字比对对象）
        with_quote = None
        for seg in parts:
            if "“" in seg or "」" in seg or "「" in seg:
                with_quote = seg
                break
        if with_quote is not None:
            stripped = with_quote.replace("“", "").replace("”", "").replace(
                "「", "").replace("」", "")
            self.assertNotEqual(stripped, with_quote, "删引号后应不等")

    def test_mismatch_report_lists_every_offending_key(self):
        # 断言的失败消息必须能定位到哪几条（否则事后无法复算）
        if not YAML_RUNNABLE:
            self.skipTest(_yaml_skip_msg("负例跳过"))
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
# E. 夹具完整性守卫（T55）
# ---------------------------------------------------------------------------
# 为什么需要这条：2026-09-28 T53 记录的 ④ 号漂移形态是**夹具自己崩掉**——
# 上游给 `repair_create_ok` 加了新槽位，而 `SLOT_FILL_EXAMPLES` 的假值表没跟上，
# 于是 `test_slot_filled_results_not_hit` 里的 `yaml_text.format(**values)` 直接抛
# `KeyError`：**断言体根本没执行**，门禁不是「说不合格」而是「自己 error」。
# 那比 mismatch 更差——mismatch 会指名哪几条，error 只说「炸了」。
#
# 本守卫把这一形态提前到**断言层**：夹具的槽位集合与上游文本的占位符集合必须相等，
# 双向指名——多了指名「夹具多出的占位符」，少了指名「上游有而夹具没有的占位符」
# （后者正是 `.format()` 会抛 KeyError 的那一个）。
#
# 纪律：本守卫**只读** yaml、**不写**任何文件；`_yaml_guard()` 两个前置条件缺任一 → skip，
# 与本文件其余各段走同一判据（诚实 skip，不是失败）。
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


class TestFixtureSlotCompleteness(unittest.TestCase):
    """夹具槽位完整性：`SLOT_FILL_EXAMPLES` 的键集合必须等于上游对应 key 文本的占位符集合。"""

    def test_fixture_slots_match_yaml_placeholders(self):
        if not YAML_RUNNABLE:
            self.skipTest(_yaml_skip_msg("夹具槽位完整性无法对账（诚实 skip）"))
        doc = _load_yaml()
        fixture_extra = []    # 夹具里多出的占位符（上游没有）
        fixture_missing = []  # 上游有、夹具没有的占位符（.format() 会抛 KeyError）
        format_errors = []    # 真的 format 一下，把 KeyError 转成指名失败
        for key, values in SLOT_FILL_EXAMPLES.items():
            yaml_text = str(_yaml_lookup(doc, key))
            yaml_slots = set(_PLACEHOLDER_RE.findall(yaml_text))
            fixture_slots = set(values)
            for name in sorted(fixture_slots - yaml_slots):
                fixture_extra.append(f"{key}: 夹具含占位符 {{{name}}}，但上游该 key 文本里没有")
            for name in sorted(yaml_slots - fixture_slots):
                fixture_missing.append(
                    f"{key}: 上游该 key 文本含占位符 {{{name}}}，但夹具没有"
                    "（.format() 会抛 KeyError——这就是本守卫要拦的形态）"
                )
            try:
                yaml_text.format(**values)
            except KeyError as exc:
                format_errors.append(f"{key}: .format() 抛 KeyError({exc})")
        # 三个方向分别断言：任一非空即红，且红的原因指名到具体占位符
        self.assertEqual(
            fixture_extra, [],
            f"{len(fixture_extra)} 处夹具占位符在上游无对应（夹具可能已过期）: {fixture_extra}",
        )
        self.assertEqual(
            fixture_missing, [],
            f"{len(fixture_missing)} 处上游占位符在夹具里缺失"
            f"（.format() 会 KeyError）: {fixture_missing}",
        )
        self.assertEqual(
            format_errors, [],
            f"{len(format_errors)} 处填充仍然抛 KeyError（守卫的兜底）: {format_errors}",
        )


# ---------------------------------------------------------------------------
# 跑法（packs 层第一根测试；本文件也是 packs 层唯一测试）
# ---------------------------------------------------------------------------
#   python3 -m unittest discover -s packs
#   python3 -m unittest discover -s packs/heat_kefu/tests -t packs
#   python3 -m unittest packs.heat_kefu.tests.test_source_of_truth -v
#
# 实测（2026-09-21，T33b）：`python3 -m unittest discover -s packs` **能跑到本文件**
#   （packs/__init__.py 已入库，CPython 的 namespace package 守卫不再拦；包目录名是
#   `heat_kefu` 下划线形式，`-t packs` 下的 `_splitext` 截断问题不存在）。
#   但真断言（B/C/D/E 段依赖 yaml，C 段还依赖构建产物）需要三个前置，缺任一**诚实 skip**：
#       ① 构建产物 —— `sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1`
#       ② kefu 供热预设 yaml **文件**可达 —— **默认多候选自动探测**（见本文件顶部
#          `_yaml_candidates`，与本仓同级 `<repo>/../../kefu-agent/...` 即可命中），
#          不必设环境变量；只有当你的 kefu 仓在以上候选之外时才需要
#          `KEFU_HEAT_YAML=<绝对路径>`。该变量一旦设置即**唯一权威**：指向不存在的
#          文件不会回退去试别的候选，而是照常 skip（见顶部「显式指定即权威」）。
#       ③ yaml **解析器**可导入 —— `pip install pyyaml`（**测试期可选依赖，不是产品依赖**；
#          装到 /tmp 等隔离目录再用 `PYTHONPATH` 指过来即可，不必装进解释器）。
#          ② 成立而 ③ 不成立时是 **skip 而不是 error**——T70 修的正是这个缝：判据原先
#          只覆盖 ②，「文件在、库不在」的机器上这 25 条（本文件依赖 yaml 的全部测试）
#          会炸成 8 个 error **条目**——error 按 `setUpClass` 归组，所以是 8 不是 25。
#   三个都在时 packs 段才是完整真包断言（否则 A 段包源结构测试照常跑）。
#   本文件自带 `sys.path` 兜底（同 labs/ 先例），不依赖调用方环境变量。


if __name__ == "__main__":
    unittest.main(verbosity=2)
