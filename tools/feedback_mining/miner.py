#!/usr/bin/env python3
"""
tools.feedback_mining.miner — 回流闭环挖掘器：留痕 + 事件流 → 话术建议

存在理由（T23）：立项 M3 的「回流闭环（日志挖掘 → 字模建议）」此前零实现。
本卡不是「离线语料挖掘」——T22 的 `labs/multi-industry-corpus/mine_recompute.py`
是在 CrossWOZ 这种**外部语料**上挖（挖出 737 候选，判据可借鉴但那是另一件事）。
本模块只吃**本仓自己的两份留痕**：

    ① 轮级留痕 JSONL   T17 格式（trigger/ledger.py 的产物）
        {turn_id, plan_id, ts, trigger_id, rule_id, state{结构化层}, plan[], keys[]}
    ② 事件流 JSONL     T07 格式（runtime/events.py 的产物）
        {ts, turn_id, plan_id, key, part, rate, variant, reason,
         pack_version, first_audio_ms, hit|miss|fallback: true, ...,
         live_text}    ← live_text 由 kefu 适配层挂上：未命中时实际合成的话术

过程：取三态为 miss / fallback 的单元 → 用其 live_text → 归一化后聚合
      （出现次数 / 涉及状态集合 / 样本 turn_id / 首次末次出现）
      → 形式条款机器化检查（docs/14 的 B1–B5 中可机检的部分）→ 拆分：
      合格进 suggestions，不合格进 rejected_candidates（写明规则与原因，绝不静默）。

用法：
    python3 tools/feedback_mining/miner.py \
        --ledger /tmp/ds/turns.jsonl --events /tmp/ds/events.jsonl --out-dir /tmp/ds

输出（--out-dir 下）：
    suggestions.json    候选清单（确定性排序：count 降序 + text 字典序）
    report.json         口径 + 样本量 + provenance（输入 sha256 + 仓 commit）

确定性纪律（本模块的命门）：
  · **不读真实时钟**：时间戳全来自输入数据（首次/末次出现取事件的 ts 字段）；
  · **不随机**：所有输出集合排序；`--shuffle` 是注入开关（把排序打散，
    用来验证「确定性断言真的能判红」，正常运行永远不开）。
  · 因此同输入两次跑，产物**逐字节一致**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from collections.abc import Mapping

# ---------------------------------------------------------------------------
# 同源判据（卡内硬约束：import，不得复制）
# ---------------------------------------------------------------------------
# 归一化：adapters.framework_kefu 的「逐字相等」唯一被允许的松弛（docs/10 §10.3）
# 单句判据：compiler 的句末标点集合（B1 的判据来源，不得自造第二份）
import sys as _sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from adapters.framework_kefu.normalize import normalize_text  # noqa: E402
from compiler.source import _SENTENCE_TERMINATORS  # noqa: E402


# ---------------------------------------------------------------------------
# 产物契约常量
# ---------------------------------------------------------------------------

# 产物 schema 版本（追加式契约：字段语义变更必须 +1，不许原地改）
SCHEMA_VERSION: str = "vox-feedback-mining/1"

# docs/14 的 A1/A2 归类字段名与「待人工判定」取值——本模块**永不写别的值**
A1A2_FIELD: str = "a1a2_class"
HUMAN_PENDING: str = "pending_human"

# docs/14 §二B 的形式条款编号（与文档逐一对应，便于人工逐条对账）
B1: str = "B1"   # 一个 variant 是一句话（无多句号拼接）
B2: str = "B2"   # 无槽位占位符（槽值一律运行时现场合成）
B3: str = "B3"   # 非空文本（「非空 / 无占位文案」这条在候选上可机检）
B4: str = "B4"   # 长度上限（长流程必须拆成多个 key）
B5: str = "B5"   # 无敏感字段痕迹（本工具的红线自检：候选来自 live_text）

# 可机检的形式条款集合（docs/14 的 B3「无逻辑」是包级断言、B5「面向流程」是人工判定，
# 都不在本模块的判定范围内——留给人工，脚本不猜）
B_RULES: Tuple[str, ...] = (B1, B2, B3, B4, B5)

# 长度上限：docs/14 未冻结数值，本工具自定并写进 README（口径必须可复算）
MAX_CANDIDATE_CHARS: int = 60

# docs/14 §二的禁入项，全部是**语义/来源**判断，机器判不了——如实标注待人工
FORBIDDEN_CODES: Tuple[str, ...] = ("i", "ii", "iii", "iv")

# 三态白名单：只从 core.metrics_spec 取常量，不接受自造结果名
from core.metrics_spec import FALLBACK, HIT, MISS, PART, PLAN_ID, REASON, TS, TURN_ID  # noqa: E402

_MINING_STATES: Tuple[str, ...] = (MISS, FALLBACK)

# 事件里挂 live_text 的字段名（T07 契约没有这个键——它是 kefu 适配层
# （adapters/framework_kefu/bridge.py）的增项，事件字段名一律引 core.metrics_spec，
# 只有这个字段在适配层，所以这里只能是字面量）
LIVE_TEXT_FIELD: str = "live_text"

# 建议的 key 命名：流程话轮的常见形态（仅建议，人工写 admission.md 时才落定）
_SUGGESTED_KEYS: Tuple[str, ...] = (
    "greeting",
    "farewell",
    "confirm_identity",
    "transfer_notice",
    "please_hold",
    "options",
)
_KEY_HINTS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("您好", "你好", "早上好", "下午好"), "greeting"),
    (("再见", "祝您", "结束本次"), "farewell"),
    (("转人工", "人工坐席", "转接"), "transfer_notice"),
    (("请稍候", "稍等", "等待"), "please_hold"),
    (("确认", "核实", "核对"), "confirm_identity"),
)

# 槽位占位符的两种书写形态：
#   ① {...}        Python/模板风格（plan.slots 的写法，docs/06 §6.2.5）
#   ② [中文短语]    方括号占位（docs/14 B2 的原文例子：`[公司名]`）
# 「不复制 compiler 的判据」这一条针对归一化与单句判据；compiler 的 source 校验本身
# 不查占位符（实测：phrases.json 里写 {name} 不会被 compiler 拦下），
# 因此这里的占位符判据由本模块自持——它比 compiler 更严，不是放宽。
_SLOT_RE: Tuple[re.Pattern, ...] = (
    re.compile(r"\{[^}]*\}"),
    re.compile(r"\[[^\]]{0,12}\]"),
)

# 敏感字段痕迹（B5 的机检部分：候选若带这些字段名/值，说明留痕没按分层过滤）
_SENSITIVE_FIELD_RE = re.compile(
    r"(account|bank_card|id_card|mobile|phone|password|token|session_id|"
    r"ticket_id|工号|账号|身份证|银行卡|手机号|密码)",
    re.IGNORECASE,
)


class MiningError(Exception):
    """挖掘器异常——所有失败统一抛出。

    消息必须含目标路径与失败原因（fail-closed：不静默跳行、不静默降级）。
    注意**空结果**（两份输入都没有 miss/fallback）不是异常：那是合法的
    「链路很健康」结论，报告会如实写 0 候选。
    """


@dataclass(frozen=True)
class SlotPlaceholder:
    """一个被抽出来的槽位占位符（B2 违例的证据，必须写进原因）。"""

    pattern: str        # 命中的写法：braces / brackets
    value: str          # 占位符本身（含定界符），例如 '{name}'
    name: str           # 占位符名，例如 'name'


@dataclass(frozen=True)
class SlotHit:
    """占位符扫描结果（pattern → 命中列表，同 pattern 内按出现位置去重保序）。"""

    braces: Tuple[SlotPlaceholder, ...]
    brackets: Tuple[SlotPlaceholder, ...]

    @property
    def all(self) -> Tuple[SlotPlaceholder, ...]:
        return self.braces + self.brackets

    def __bool__(self) -> bool:
        return bool(self.all)


@dataclass(frozen=True)
class LiveText:
    """一条待聚合的 live_text（事件侧的最小事实单元）。"""

    turn_id: str
    plan_id: str
    part: Optional[int]
    ts: Optional[str]
    state: str
    reason: str
    text: str


@dataclass
class Candidate:
    """一个聚合出来的候选（出现次数 + 涉及状态 + 样本 turn_id + 首末次）。"""

    text: str                      # 归一化后的文本（聚合键，也是候选内容）
    raw_texts: Tuple[str, ...]     # 各变体的原文（确定性去重保序；供人工看原始写法）
    count: int
    states: Tuple[str, ...]
    reasons: Tuple[str, ...]
    turn_ids: Tuple[str, ...]      # 全部出现过的 turn_id（确定性排序）
    sample_turn_ids: Tuple[str, ...]
    first_seen: Optional[str]
    last_seen: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "raw_texts": list(self.raw_texts),
            "count": self.count,
            "states": list(self.states),
            "reasons": list(self.reasons),
            "turn_ids": list(self.turn_ids),
            "sample_turn_ids": list(self.sample_turn_ids),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class MiningRun:
    """一次挖掘的结果（不含任何文件副作用；落盘由 write_artifacts 做）。"""

    candidates: Tuple[Candidate, ...]
    suggestions: Tuple[Dict[str, Any], ...]
    rejected: Tuple[Dict[str, Any], ...]
    stats: Dict[str, Any]


# ---------------------------------------------------------------------------
# 1. 读输入（JSONL；坏行一律抛错，不静默跳行）
# ---------------------------------------------------------------------------
def read_jsonl(path: Any) -> Tuple[Dict[str, Any], ...]:
    """读一个 JSONL 文件的全部记录（顺序保留）。

    解析失败抛 MiningError（消息含路径与行号）——静默跳行会让
    「留痕残缺」伪装成「候选少」，正是本卡要防的静默降级。
    """
    p = Path(path)
    if not p.is_file():
        raise MiningError(f"输入文件不存在: {p}")
    records: List[Dict[str, Any]] = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise MiningError(
                        f"{p} 第 {no} 行不是合法 JSON: {e}"
                    ) from e
                if not isinstance(obj, dict):
                    raise MiningError(
                        f"{p} 第 {no} 行必须是 JSON 对象，实际为 {type(obj).__name__}"
                    )
                records.append(obj)
    except (OSError, IOError, UnicodeError) as e:
        raise MiningError(f"读取失败: {p} —— {type(e).__name__}: {e}") from e
    return tuple(records)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Any) -> str:
    """算一个文件的 sha256（provenance 用：产物必须能反查是哪份输入）。"""
    p = Path(path)
    if not p.is_file():
        raise MiningError(f"无法取 sha256: {p} 不存在")
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_commit(root: Optional[Path] = None) -> Optional[str]:
    """当前 HEAD（失败不抛错：CI 里可能不是 git 工作区，但 provenance 要如实标注）。"""
    base = root or _REPO_ROOT
    try:
        out = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


def load_ledger(path: Any) -> Tuple[Dict[str, Any], ...]:
    """读轮级留痕（T17 格式），返回 (记录, 字段是否齐备)。

    留痕缺 turn_id / state 时**不当空字典静默补**：直接抛错，
    因为聚合要靠 (turn_id, state) 对齐，缺字段会让 states 集合凭空变空。
    """
    records = read_jsonl(path)
    bad = [i for i, r in enumerate(records) if TURN_ID not in r or not isinstance(r.get("state"), dict)]
    if bad:
        raise MiningError(
            f"留痕含非法记录（缺 {TURN_ID} 或 state 非字典），行号(0 起)：{bad[:5]}"
        )
    return records


def load_events(path: Any) -> Tuple[Dict[str, Any], ...]:
    """读事件流（T07 格式）；必须含三态键之一。

    不含三态键的（如 listen_ms 窗口事件）不是"坏行"，只是不产生候选——
    它没有 live_text 可挖。但 miss/fallback 却没 live_text 必须报错：
    「本该有文本却没有」是数据残缺，静默当空串会把候选数算错。
    """
    records = read_jsonl(path)
    for i, e in enumerate(records):
        if not (HIT in e or MISS in e or FALLBACK in e):
            continue   # 窗口事件等形态自描述，跳过
        if e.get(MISS) or e.get(FALLBACK):
            if not isinstance(e.get(LIVE_TEXT_FIELD), str):
                raise MiningError(
                    f"事件流第 {i} 行（{TURN_ID}={e.get(TURN_ID)!r}）三态为 "
                    f"{'miss' if e.get(MISS) else 'fallback'} 却缺 {LIVE_TEXT_FIELD} "
                    f"（str）——未命中单元的实际话术必须留痕，否则候选数会被算错"
                )
    return records


# ---------------------------------------------------------------------------
# 2. 抽取：三态 miss / fallback 的 live_text
# ---------------------------------------------------------------------------
def _state_of(event: Dict[str, Any]) -> Optional[str]:
    """取事件的三态；不含三态键（窗口事件）返回 None。"""
    for state in (HIT, MISS, FALLBACK):
        if event.get(state):
            return state
    return None


def extract_live_texts(
    events: Sequence[Dict[str, Any]],
) -> Tuple[Tuple[LiveText, ...], Dict[str, Any]]:
    """从事件流抽出所有 miss/fallback 单元的 live_text，并给三态计数。

    计数（hit/miss/fallback/other）必须如实落报告——只报「候选数」而不报
    分母，等于把命中率口径藏起来。
    """
    items: List[LiveText] = []
    counts: Dict[str, int] = defaultdict(int)
    for event in events:
        state = _state_of(event)
        if state is None:
            counts["other"] += 1
            continue
        counts[state] += 1
        if state not in _MINING_STATES:
            continue
        items.append(
            LiveText(
                turn_id=str(event.get(TURN_ID) or ""),
                plan_id=str(event.get(PLAN_ID) or ""),
                part=event.get(PART),
                ts=event.get(TS),
                state=state,
                reason=str(event.get(REASON) or ""),
                text=event.get(LIVE_TEXT_FIELD),
            )
        )
    return tuple(items), dict(counts)


def state_of_turns(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """从留痕里取 (turn_id → state 结构化层快照)，供候选的 states 集合用。

    state 快照的值一律字符串化（int/bool 也要能进 JSON 且能排序），
    不丢结构：形如 "ticket_status=处理中"。
    """
    out: Dict[str, Any] = {}
    for r in records:
        turn_id = str(r.get(TURN_ID) or "")
        state = r.get("state") or {}
        parts = [f"{k}={v}" for k, v in sorted(state.items(), key=lambda kv: str(kv[0]))]
        out[turn_id] = ";".join(parts) if parts else "(no structured state)"
    return out


# ---------------------------------------------------------------------------
# 3. 聚合（确定性；归一化同源）
# ---------------------------------------------------------------------------
def _norm_or_none(text: Optional[str]) -> Optional[str]:
    """归一化；非 str 一律返回 None（不静默当空串——见 normalize_text 的 WHY）。"""
    if not isinstance(text, str):
        return None
    return normalize_text(text)


def aggregate(live_texts: Sequence[LiveText]) -> List[Candidate]:
    """按归一化文本聚合 live_text，返回确定性排序的候选列表。

    排序：count 降序 → text 字典序（同次数时逐字比较）。
    这个排序是**契约**（验收 4 判据依赖它），不是实现细节。

    归一化后为空的 live_text（纯空白 / 纯标点）**不进候选**，但也不算错误——
    它们是「这一轮没说出什么可铸的话」。B3 只兜住**能归一化出内容**的候选，
    所以这里不把它们硬塞进候选再靠 B3 拒收（那样会让 rejected 里出现空文本）。
    """
    grouped: Dict[str, Dict[str, Any]] = {}

    for item in live_texts:
        norm = _norm_or_none(item.text)
        if norm is None:
            continue
        if not norm:
            continue
        g = grouped.get(norm)
        if g is None:
            g = grouped[norm] = {
                "text": norm,
                "raw": set(),
                "count": 0,
                "states": set(),
                "reasons": set(),
                "turns": set(),
                "first": None,
                "last": None,
            }
        g["count"] += 1
        if isinstance(item.text, str) and item.text:
            g["raw"].add(item.text)
        g["states"].add(item.state)
        if item.reason:
            g["reasons"].add(item.reason)
        g["turns"].add(item.turn_id)
        if item.ts:
            if g["first"] is None or item.ts < g["first"]:
                g["first"] = item.ts
            if g["last"] is None or item.ts > g["last"]:
                g["last"] = item.ts

    candidates: List[Candidate] = []
    for norm, g in grouped.items():
        turn_ids = sorted(t for t in g["turns"] if t)
        candidates.append(
            Candidate(
                text=norm,
                raw_texts=tuple(sorted(g["raw"], key=lambda s: (len(s), s))),
                count=g["count"],
                states=tuple(sorted(g["states"])),
                reasons=tuple(sorted(r for r in g["reasons"])),
                turn_ids=tuple(turn_ids),
                sample_turn_ids=tuple(turn_ids[:10]),
                first_seen=g["first"],
                last_seen=g["last"],
            )
        )
    return sort_candidates(candidates)


# ---------------------------------------------------------------------------
# 排序（确定性契约）
# ---------------------------------------------------------------------------
def sort_candidates(candidates: Sequence[Candidate], shuffle: bool = False) -> List[Candidate]:
    """确定性排序：count 降序 + text 字典序。

    shuffle=True 是**注入开关**（只供测试验证「确定性断言能判红」用）：
    真实运行绝不该带这个参数。
    """
    ordered = sorted(candidates, key=lambda c: (-c.count, c.text))
    if not shuffle:
        return ordered
    rng = random.Random(0xC0FFEE)   # 固定种子：随机 ≠ 不确定，仍能两次跑一致
    rng.shuffle(ordered)
    return ordered


# ---------------------------------------------------------------------------
# 4. 形式条款机器化检查（docs/14 的 B1–B5 中可机检的部分）
# ---------------------------------------------------------------------------
def count_terminators(text: str) -> int:
    """句末标点个数（同源：compiler.source._SENTENCE_TERMINATORS）。"""
    return sum(1 for ch in text if ch in _SENTENCE_TERMINATORS)


def scan_slot_placeholders(text: str) -> SlotHit:
    """扫槽位占位符（B2）；返回带位置信息的命中，供原因里写出占位符本身。"""
    braces: List[SlotPlaceholder] = []
    brackets: List[SlotPlaceholder] = []
    for m in re.finditer(r"\{([^}]*)\}", text):
        braces.append(SlotPlaceholder("braces", m.group(0), m.group(1)))
    for m in re.finditer(r"\[([^\]]{0,12})\]", text):
        brackets.append(SlotPlaceholder("brackets", m.group(0), m.group(1)))
    return SlotHit(tuple(braces), tuple(brackets))


def check_nonempty(text: str) -> Optional[Dict[str, str]]:
    """B3：文本非空（归一化后为空 = 没有可铸的话术）。"""
    if not text:
        return {"rule": B3, "reason": "文本为空或归一化后为空，无可预铸内容"}
    return None


def check_slot_placeholder(text: str) -> Optional[Dict[str, str]]:
    """B2：无槽位占位符。原因里必须写出占位符本身（验收 3 的负例断言点）。"""
    hit = scan_slot_placeholders(text)
    if not hit:
        return None
    items = "、".join(p.value for p in hit.all)
    return {
        "rule": B2,
        "reason": f"含槽位占位符 {items}：槽值一律运行时现场合成，"
                  f"不得进 variant 原文（docs/14 B2）",
    }


def check_single_sentence(text: str, raw_text: Optional[str] = None) -> Optional[Dict[str, str]]:
    """B1：一句话。判据同源 compiler（_SENTENCE_TERMINATORS 计数 ≥ 2 即跨句）。

    计数在**原文**上做，不做在归一化文本上——这是实测出来的坑：
    `normalize_text` 的第 ②③ 步会把「。」折成 '.'、「？」折成 '?'（NFKC），
    于是「已受理。请保持电话畅通。」归一化后是 'a.b.'，句号数从 2 变成 2
    这个例子恰好没事；但一旦输入里句末标点是半角 '.'（`工单已受理.请保持.。`）
    或混排，归一化后再数就**少算**了。compiler 判「一句」判的是**源文本**，
    本模块必须与它同口径——聚合键用归一化文本，B1 判据用原文。
    raw_text 为 None 时退回数 text（仅测试/调用方直接给原文时用）。
    """
    n = count_terminators(raw_text if raw_text is not None else text)
    if n >= 2:
        return {
            "rule": B1,
            "reason": f"原文含 {n} 个句末标点，不是「一句」："
                      f"长流程必须拆成多个 key（字模最小粒度 = 一句）",
        }
    return None


def check_text_length(text: str, limit: int = MAX_CANDIDATE_CHARS) -> Optional[Dict[str, str]]:
    """B4：长度上限（长文本多为拼接，必须拆）。"""
    if len(text) > limit:
        return {
            "rule": B4,
            "reason": f"归一化后 {len(text)} 字 > 上限 {limit} 字："
                      f"超长话术多为多步流程拼接，必须拆成多个 key",
        }
    return None


def check_sensitive(text: str) -> Optional[Dict[str, str]]:
    """B5：无敏感字段痕迹（本工具的红线自检：候选来自 live_text，不得带用户信息）。"""
    m = _SENSITIVE_FIELD_RE.search(text)
    if m:
        return {
            "rule": B5,
            "reason": f"含疑似敏感字段痕迹 '{m.group(0)}'："
                      f"候选若带用户信息，不得进建议清单（留痕分层红线）",
        }
    return None


def check_form(
    text: str,
    max_chars: int = MAX_CANDIDATE_CHARS,
    raw_text: Optional[str] = None,
) -> Tuple[Optional[Dict[str, str]], ...]:
    """跑全部可机检的形式条款，返回所有命中（一条文本可能同时违 B1 和 B2）。

    text      归一化文本（聚合键；B2/B4/B5 判它——占位符与长度是字符级事实）
    raw_text  原文（B1 判它：归一化会折叠句末标点，数在归一化文本上会少算）
    返回全部而非首个：一个候选可能同时超长又含占位符，只报第一条会漏报另一半。
    """
    checks = (
        check_nonempty(text),
        check_slot_placeholder(text),
        check_single_sentence(text, raw_text=raw_text),
        check_text_length(text, max_chars),
        check_sensitive(text),
    )
    return tuple(c for c in checks if c is not None)


def form_status(violations: Sequence[Dict[str, str]]) -> str:
    """把违规列表折成一个 pass/fail 标记。"""
    return "fail" if violations else "pass"


def suggest_key(text: str) -> Optional[str]:
    """给一个**建议**的 key 名（纯字面提示，不是判定，不进任何契约）。"""
    for hints, key in _KEY_HINTS:
        if any(h in text for h in hints):
            return key
    for k in _SUGGESTED_KEYS:
        if k in text:
            return k
    return None


def split_candidates(
    candidates: Sequence[Candidate],
    max_chars: int = MAX_CANDIDATE_CHARS,
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[Dict[str, Any], ...]]:
    """按形式条款把候选拆成 suggestions 与 rejected_candidates。

    rejected 的条目**必须带原因与规则编号**——丢弃不留痕 = 台账残缺。
    """
    suggestions: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for c in candidates:
        # B1 判原文（见 check_single_sentence 的 WHY），其余判归一化文本
        raw = c.raw_texts[0] if c.raw_texts else c.text
        violations = check_form(c.text, max_chars, raw_text=raw)
        d = c.to_dict()
        d[A1A2_FIELD] = HUMAN_PENDING          # 语义归类一律待人工，脚本不猜
        d["form"] = {
            "status": form_status(violations),
            "violations": [dict(v) for v in violations],
            "checked_rules": list(B_RULES),
        }
        d["forbidden_check"] = {
            "i_from_user_utterance": HUMAN_PENDING,
            "ii_semantic_ambiguous": HUMAN_PENDING,
            "iii_template_reuse": HUMAN_PENDING,
            "iv_frequency_claim": HUMAN_PENDING,
        }
        if violations:
            d["suggested_key"] = None
            rejected.append(d)
        else:
            d["suggested_key"] = suggest_key(c.text)
            suggestions.append(d)
    return tuple(suggestions), tuple(rejected)


# ---------------------------------------------------------------------------
# 5. 组装一次挖掘（纯函数：不碰文件）
# ---------------------------------------------------------------------------
def mine_from_json(
    ledger_records: Sequence[Dict[str, Any]],
    event_records: Sequence[Dict[str, Any]],
    *,
    max_chars: int = MAX_CANDIDATE_CHARS,
    shuffle: bool = False,
) -> MiningRun:
    """从已读入的记录跑一次挖掘（不落盘）。

    返回 MiningRun：候选、建议、拒收、统计。文件与 provenance 由 mine() 补。
    """
    live_texts, state_counts = extract_live_texts(event_records)
    candidates = aggregate(live_texts)
    if shuffle:
        candidates = sort_candidates(candidates, shuffle=True)
    suggestions, rejected = split_candidates(candidates, max_chars)

    # 留痕覆盖率：候选的 turn 有多少能在轮级留痕里找到（D2 对齐：两份输入互相印证）
    ledger_turns = {str(r.get(TURN_ID) or "") for r in ledger_records}
    cand_turns = {t for c in candidates for t in c.turn_ids if t}
    covered = cand_turns & ledger_turns
    stats = {
        "ledger_records": len(ledger_records),
        "event_records": len(event_records),
        "units_by_state": {
            HIT: state_counts.get(HIT, 0),
            MISS: state_counts.get(MISS, 0),
            FALLBACK: state_counts.get(FALLBACK, 0),
            "other": state_counts.get("other", 0),
        },
        "live_texts_considered": len(live_texts),
        "turn_ids_with_live_text": len({lt.turn_id for lt in live_texts}),
        "turn_ids_in_ledger": len(ledger_turns),
        "turn_ids_in_both": len({t.turn_id for t in live_texts} & ledger_turns),
        "distinct_normalized_candidates": len(candidates),
        "suggestions": len(suggestions),
        "rejected": len(rejected),
        "rejected_by_rule": dict(
            sorted(
                {
                    rule: sum(1 for r in rejected for v in r["form"]["violations"] if v["rule"] == rule)
                    for rule in B_RULES
                }.items()
            )
        ),
        "states_referenced": sorted(
            {s for c in candidates for s in c.states}
        ),
        "ledger_coverage": {
            "candidate_turn_ids": len(cand_turns),
            "found_in_ledger": len(covered),
            "missing_from_ledger": sorted(cand_turns - ledger_turns),
        },
    }
    return MiningRun(
        candidates=tuple(candidates),
        suggestions=tuple(suggestions),
        rejected=tuple(rejected),
        stats=stats,
    )


def mine(
    ledger_path: Any,
    events_path: Any,
    *,
    max_chars: int = MAX_CANDIDATE_CHARS,
    shuffle: bool = False,
    repo_root: Optional[Path] = None,
) -> Tuple[MiningRun, Dict[str, Any]]:
    """读文件 + 挖掘，返回 (结果, provenance)。

    provenance 必须能反查输入：两份输入的 sha256 + 仓库 commit。
    """
    ledger_records = load_ledger(ledger_path)
    event_records = load_events(events_path)
    run = mine_from_json(ledger_records, event_records, max_chars=max_chars, shuffle=shuffle)
    provenance = {
        "repo_commit": _repo_commit(repo_root),
        "inputs": {
            "ledger": {"path": str(Path(ledger_path)), "sha256": sha256_file(ledger_path)},
            "events": {"path": str(Path(events_path)), "sha256": sha256_file(events_path)},
        },
        "normalizer": "adapters.framework_kefu.normalize.normalize_text",
        "sentence_terminators": _SENTENCE_TERMINATORS,
        "max_candidate_chars": max_chars,
        "shuffle": bool(shuffle),
    }
    return run, provenance


# ---------------------------------------------------------------------------
# 6. 落盘（suggestions.json + report.json；时间戳可注入，默认不读时钟）
# ---------------------------------------------------------------------------
def mine_t22_candidates(path: Any, *, max_chars: int = MAX_CANDIDATE_CHARS) -> Dict[str, Any]:
    """把 T22 的离线语料挖掘结果过一遍本工具的形式条款（跨仓库对拍，数字落盘）。

    这不是回流挖掘——T22 的 `labs/multi-industry-corpus/` 是在 CrossWOZ 这种
    **外部语料**上挖的（737 候选），判据可借鉴但那是另一件事（见卡背景）。
    这里只做一件事：拿 docs/14 的形式条款去量那 737 条的句式骨架，
    回答「按形式条款，外部语料挖出来的候选有多少能进申报」。

    输入形状（T22 的产物，out/mine_candidates.json）：
        {candidates_by_group_hits: {">=50": 10, ">=20": 58, ">=10": 208, ">=3": 737},
         sample_candidates: [{state_signature, skeleton, group_hits, id}, ...]}
    注意：骨架里 `#` 是槽值占位、`@` 是英文词占位（T22 的 skeleton 口径），
    本工具的 `{...}` / `[...]` 占位符判据不覆盖它们——所以 B2 在外部语料上基本不命中，
    这也正是 T22 那 737 条**不能直接进包**的证据（形式条款只量出一部分）。
    返回 {t22_report_file, n_keys, by_key, samples_checked, samples_rejected_by_rule}。
    """
    p = Path(path)
    if not p.is_file():
        raise MiningError(f"T22 挖掘产物不存在: {p}（先跑 labs/multi-industry-corpus/mine_recompute.py）")
    doc = json.loads(p.read_text(encoding="utf-8"))
    by_key = doc.get("candidates_by_group_hits")
    if not isinstance(by_key, dict) or not by_key:
        raise MiningError(f"{p} 缺 candidates_by_group_hits（T22 产物形状不符）")
    samples = doc.get("sample_candidates") or []
    kept, rejected = 0, 0
    rejected_by_rule: Dict[str, int] = {}
    for sc in samples:
        skel = sc.get("skeleton") or ""
        v = check_form(skel, max_chars=max_chars, raw_text=skel)
        if v:
            rejected += 1
            for x in v:
                rejected_by_rule[x["rule"]] = rejected_by_rule.get(x["rule"], 0) + 1
        else:
            kept += 1
    return {
        "t22_report_file": str(p),
        "method": "labs/multi-industry-corpus/mine_recompute.py（外部语料挖掘，非回流）",
        "candidates_by_group_hits": by_key,
        "total_candidates": doc.get("n_candidates"),
        "samples_checked": len(samples),
        "samples_passing_form_rules": kept,
        "samples_rejected": rejected,
        "samples_rejected_by_rule": dict(sorted(rejected_by_rule.items())),
        "note": (
            "sample_candidates 只有 %d 条（T22 的样本），不能外推到 %d 条候选；"
            "且形式条款全过不等于可入包——A1/A2 与禁入项 ⅰ–ⅳ 必须人工判（本工具的边界）"
        ) % (len(samples), doc.get("n_candidates")),
    }


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_artifacts(
    out_dir: Any,
    run: MiningRun,
    provenance: Dict[str, Any],
    *,
    run_at: Optional[str] = None,
    **extra_sections: Any,
) -> Tuple[Path, Path]:
    """把结果写成 suggestions.json / rejected.json 与 report.json，返回两个路径。

    run_at 缺省时取「输入数据里最晚的 ts」，而不是当前时间——
    同输入两次跑必须逐字节一致（验收 4）。
    extra_sections 是可选的附加报告段（键 → 值），统一落进 report.json 的顶层。
    """
    out = Path(out_dir)
    if not out.exists():
        out.mkdir(parents=True)

    suggestions_doc = {
        "_schema": SCHEMA_VERSION,
        "sort_order": "count desc, then text lexicographic",
        "a1a2_policy": f"{A1A2_FIELD} 恒为 {HUMAN_PENDING!r}（A1/A2 是语义判断，机器不判）",
        "run_at": run_at if run_at is not None else _latest_ts(run),
        "n_suggestions": len(run.suggestions),
        "n_rejected": len(run.rejected),
        "suggestions": list(run.suggestions),
    }
    rejected_doc = {
        "_schema": SCHEMA_VERSION,
        "note": "拒收清单：形式条款未过的候选。留痕不丢弃（docs/14 B1–B5）",
        "rejected_candidates": list(run.rejected),
    }
    report_doc = {
        "_schema": SCHEMA_VERSION,
        "tool": "tools/feedback_mining/miner.py",
        "run_at": suggestions_doc["run_at"],
        "scope": {
            "inputs": ["轮级留痕 JSONL（T17）", "事件流 JSONL（T07，含 live_text）"],
            "mined_states": list(_MINING_STATES),
            "text_field": LIVE_TEXT_FIELD,
            "normalizer": "adapters.framework_kefu.normalize.normalize_text（import，非复制）",
            "single_sentence_rule": "compiler.source._SENTENCE_TERMINATORS（import，非复制）",
            "max_candidate_chars": provenance.get("max_candidate_chars"),
        },
        "samples": run.stats,
        "counts": {
            "candidates": len(run.candidates),
            "suggestions": len(run.suggestions),
            "rejected": len(run.rejected),
            "rejected_by_rule": run.stats["rejected_by_rule"],
        },
        "provenance": provenance,
        "rejected_candidates": list(run.rejected),
    }
    for name, value in extra_sections.items():
        report_doc[name] = value

    sugg_path = out / "suggestions.json"
    rej_path = out / "rejected.json"
    report_path = out / "report.json"
    sugg_path.write_text(_dump(suggestions_doc), encoding="utf-8")
    rej_path.write_text(_dump(rejected_doc), encoding="utf-8")
    report_path.write_text(_dump(report_doc), encoding="utf-8")
    return sugg_path, report_path


def _latest_ts(run: MiningRun) -> Optional[str]:
    """取输入里最晚的 ts（缺省时用，保证同输入两次跑逐字节一致）。"""
    ts = [c.last_seen for c in run.candidates if c.last_seen]
    return max(ts) if ts else None


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="回流闭环挖掘：留痕 + 事件流 → 话术建议")
    ap.add_argument("--ledger", required=True, help="轮级留痕 JSONL（T17 格式）")
    ap.add_argument("--events", required=True, help="事件流 JSONL（T07 格式，含 live_text）")
    ap.add_argument("--out-dir", required=True, help="产物目录（suggestions/rejected/report）")
    ap.add_argument("--max-chars", type=int, default=MAX_CANDIDATE_CHARS, help=f"长度上限（默认 {MAX_CANDIDATE_CHARS}）")
    ap.add_argument(
        "--run-at",
        default=None,
        help="写入产物的 run_at（缺省取输入里最晚的 ts；不读真实时钟，保证确定性）",
    )
    ap.add_argument(
        "--t22",
        default=None,
        help="T22 的挖掘产物（out/mine_candidates.json）：做一次跨仓库对拍，写进 report.json 的 cross_check_t22",
    )
    ap.add_argument(
        "--shuffle",
        action="store_true",
        help="注入开关：打散候选排序（仅用于验证确定性断言能判红，禁止在生产用）",
    )
    args = ap.parse_args(argv)

    try:
        extra = {}
        if args.t22:
            extra["cross_check_t22"] = mine_t22_candidates(
                args.t22, max_chars=args.max_chars
            )
        run, provenance = mine(
            args.ledger,
            args.events,
            max_chars=args.max_chars,
            shuffle=args.shuffle,
        )
        sugg_path, report_path = write_artifacts(
            args.out_dir, run, provenance, run_at=args.run_at, **extra
        )
    except MiningError as e:
        print(f"MiningError: {e}", file=sys.stderr)
        return 3

    print(
        f"候选 {len(run.candidates)} 条 → 建议 {len(run.suggestions)} / 拒收 {len(run.rejected)}"
        f"（ledger {run.stats['ledger_records']} 行，events {run.stats['event_records']} 行）"
    )
    print(f"  {sugg_path}")
    print(f"  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
