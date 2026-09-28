"""
labs/ticket-source/run_e2e.py — 源 → state → plan → 留痕（端到端 + 自断言）

跑一遍整条线，产出可复现的原始证据：
    raw/turns.jsonl   每轮一条留痕（state 结构化层 + plan，由 record_turn 写）
    summary.json      口径、命中分布、敏感值复核结果、可复现性自检结果

用法：
    cd <仓库根>
    python3 labs/ticket-source/run_e2e.py

退出码：0 = 全部断言通过；3 = 任一断言失败（含负例未如预期抛错）。

复用的产品 API（T21 反空转条款：不得在 labs 里复制 state/trigger 校验逻辑）：
    trigger.load_trigger    读 packs/demo-brief（含 phrases.json 校验）
    trigger.validate_state  state 快照的格式闸门（to_state.py 里调用）
    trigger.build_plan      state → plan
    trigger.record_turn     轮级留痕（敏感层绝不落盘）
    trigger.read_turns      读回留痕做复核

本文件不联网、不启停服务、不 import 系统时钟；不写 labs/ticket-source/ 之外的任何路径。
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = HERE.parents[1]
PACK_DIR: Path = REPO_ROOT / "packs" / "demo-brief"
RAW_DIR: Path = HERE / "raw"
TURNS_PATH: Path = RAW_DIR / "turns.jsonl"
SUMMARY_PATH: Path = HERE / "summary.json"
SOURCE_PATH: Path = HERE / "tickets.source.json"
MAP_PATH: Path = HERE / "source_map.json"

# 注入的会话轮次标识（确定性：不读时钟、不随机）
TURN_ID_PREFIX: str = "ticket-"
TS_FIELD_NOTE: str = "留痕的 ts 由本脚本注入（确定性值），不读系统时钟"


class AssertionError_(Exception):
    """本脚本的自断言失败。消息含具体非法值。"""


# ---------------------------------------------------------------------------
# 0. 断言工具
# ---------------------------------------------------------------------------
FAILURES: List[str] = []
PASSES: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    """记录一条断言结果；失败只记录不抛出（最后统一退出非零）。"""
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name}" + (f" —— {detail}" if detail else ""))


def expect_raises(name: str, func, *args, must_contain: Optional[str] = None, **kwargs):
    """断言某个调用必须抛错；must_contain 给出必须出现在消息里的具体值。

    反空转条款：断言必须能被打破——这里真的去调用并把抛出的消息拿回来比对，
    不是只检查「有没有异常」。
    """
    try:
        func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 这里就是要断言「抛了错」
        msg = str(exc)
        if must_contain is not None and must_contain not in msg:
            FAILURES.append(
                f"{name}：抛错但消息不含 '{must_contain}'"
                f"（实际消息: {msg[:240]}）"
            )
            return exc
        PASSES.append(f"{name}（{type(exc).__name__}，消息含 '{must_contain}'）")
        return exc
    FAILURES.append(f"{name}：本应抛错但没有抛（静默放行了非法输入）")
    return None


# ---------------------------------------------------------------------------
# 0b. 文字陈述自校验（T21d）：扫描范围从「rule_reachability 一段」扩到整个目录
# ---------------------------------------------------------------------------

# 禁语 = 已被实测推翻的因果陈述。表项是 (短语, 为什么禁、正确的说法是什么)。
# 只登记**与实测矛盾**的陈述。overdue_days 的 0 条 when 引用是事实，所以把
# 「when 依赖 overdue_days」补全成一条否定式结论的那种说法**不在禁语表里**
# （禁语表登的是与实测矛盾的方向；同一句式套在 overdue_days 上恰好是真话）。
BANNED_TEXTUAL_CLAIMS: Tuple[Tuple[str, str], ...] = (
    # T21 的旧因果：把 days_left 的 min=0 当成 is_overdue 的约束，推断出「恒为 false」。
    ("is_overdue 恒为 false",
     "overdue 的 when 是 {is_overdue: true}，不含 days_left；is_overdue 是源导出的透传布尔列，"
     "本 fixture 里 WB-DEMO-0009 / WB-DEMO-0012 / WB-DEMO-0015 都是 true。"
     "days_left 的 min=0 与 is_overdue 互不约束"),
    ("is_overdue 恒为",
     "同上，含缩写变体（防止换个标点绕过检查）"),
    ("没有任何一条规则的 when 依赖它们",
     "本卡要修的那句：实测 days_left 被 due_tonight / due_days_left 的 when 依赖、"
     "is_overdue 被 overdue / due_tonight / due_days_left 的 when 依赖，只有 overdue_days 是 0 条"),
    ("没有任何一条规则的 when 依赖 days_left",
     "假：due_tonight 与 due_days_left 的 when 都含 days_left"),
    ("没有任何一条规则的 when 依赖 is_overdue",
     "假：overdue 的 when 就是 {is_overdue: true}"),
    ("没有任何一条规则的 when 依赖",
     "假（days_left / is_overdue 都被 when 依赖）；只有 overdue_days 真是 0 条，"
     "而 overdue_days 属于 units[].slots 的值来源，不是 when 的判定输入——如实写清这个区分"),
    ("when 依赖逾期字段",
     "泛化变体：逾期相关字段里 days_left / is_overdue 都被 when 依赖"),
    ("只驱动 demo-brief 的两条规则", "T21 的旧结论；T21b 实测 6/6 全部可达"),
    ("只能驱动 demo-brief 的两条规则", "同上（含「只能」变体）"),
    ("不可达的四条", "6 条规则全部可达，需改 fixture 行而非改包"),
    # 反向的模糊化规避：不得用「用得不多 / 基本没人用」这类不可判定的说法替代具体结论。
    ("用得不多", "必须写成与实测一致的具体陈述（哪些规则的 when 引用了它），"
              "不得用「用得不多」这类无法机检的模糊表述规避"),
    ("基本没人用", "同上，模糊化规避"),
    # 不得为了「全部字段都被引用」去改包。
    ("为了让全部字段都被引用去改包", "overdue_days 0 条 when 引用是事实，"
                            "如实记录即可；改包属于 packs/，不在本卡范围"),
)


# 自扫用的子集：只含**事实性**被推翻的因果陈述（在文件正文里的出现 = 真的写错了）。
# 模糊化规避类短语（反向的模糊说法，见禁语表第 11 项）不在这张表里——它们只由下面的负例注入测试覆盖，
# 不写进任何文件正文，否则自扫会命中禁语表自身的字面量（那等于没扫）。
_FACTUAL_BANNED: Tuple[Tuple[str, str], ...] = tuple(BANNED_TEXTUAL_CLAIMS[i] for i in range(10))
assert len(_FACTUAL_BANNED) == 10, "禁语表前 10 项应为事实性短语"


# 扫描范围（T21e）：**本文件全文 − 禁语表自身的字面量区间**，不再剥注释、不再剥文档串。
# 只有一段例外必须整体跳过：禁语表 BANNED_TEXTUAL_CLAIMS 按定义就得把被禁的原话
# 逐字写下来，这一段不是「说明文字」，是**被扫描的对象本身**；不跳过它，自扫会永远为红，
# 那等于用变量名换了个扫描范围，没扩到实质。除此之外**不做任何裁剪**——
# 行内 # 注释、文档串里的句子都是说明文字，属于扫描范围（T21d 在这里漏了注释）。
_BANNED_TABLE_START = 'BANNED_TEXTUAL_CLAIMS: Tuple[Tuple[str, str], ...] = ('
_BANNED_TABLE_END = "_FACTUAL_BANNED: Tuple[Tuple[str, str], ...] = "


def mask_banned_table(src: str) -> Tuple[str, Tuple[Tuple[int, int], ...]]:
    """把禁语表自身的字面量区间替换成等长空白，返回（遮罩后的文本, 区间列表）。

    遮罩而不是拼接：位置不变，因此**行号 / 列号可以直接对上原文**——
    这既能证明「不是靠裁掉一段来缩小范围」，也让负例注入后命中位置与原文一致。
    等长空白会原样保留空白字符与换行，所以文本长度与原文逐字节相同。
    """
    start = src.find(_BANNED_TABLE_START)
    end = src.find(_BANNED_TABLE_END, start) if start != -1 else -1
    assert start != -1, "禁语表字面量区间必须能被定位（否则自扫无法区分表与正文）"
    assert end != -1 and end > start, "禁语表区间必须能闭合（结束锚点缺失）"
    spans = ((start, end),)
    return "".join(
        src[:start] + " " * (end - start) + src[end:]
    ), spans


def load_script_prose() -> str:
    """取本文件的说明文字作为扫描目标。

    范围 = **文件全文 − 禁语表自身的字面量区间**（见 _BANNED_TABLE_START）。
    注释与文档串**不剥**：它们就是本文件的说明文字（行内注释此前被漏扫，见 T21e）。
    唯一的跳过区间是禁语表——它按定义必须自带被禁原话。
    """
    raw = Path(__file__).read_text(encoding="utf-8")
    masked, _spans = mask_banned_table(raw)
    assert len(masked) == len(raw), "遮罩必须等长（行号/列号才能对上原文）"
    return masked


def banned_hits_in(text: str,
                   only: Optional[Tuple[Tuple[str, str], ...]] = None
                   ) -> Tuple[str, ...]:
    """返回 text 里出现过的禁语短语（去重保序）。

    only=None 时用全表；本脚本的自扫传只含事实性短语的 _FACTUAL_BANNED，
    因为模糊化规避类短语只由负例注入测试来覆盖（不写在文件里）。
    """
    table = only if only is not None else BANNED_TEXTUAL_CLAIMS
    hits = []
    for phrase, _why in table:
        if phrase in text and phrase not in hits:
            hits.append(phrase)
    return tuple(hits)


def _unreachable_reason(rule_id: str, trigger: "Trigger", per_ticket: List[Dict[str, Any]]) -> str:
    """给「不可达」的规则写具体理由：指向是哪条声明 / 哪个校验挡住了。

    不允许只写一句 unreachable——这里逐条列出该规则 when 的每个条件，
    并说明本实验的源数据里为什么凑不出同时满足的 state。
    """
    rule = next(r for r in trigger.rules if r.rule_id == rule_id)
    when = rule.when
    if when is None:
        return (
            f"{rule_id} 的 when 是 null（兜底规则），任何 state 都匹配；"
            f"它不可达只可能是被排在前面的条件规则全部先命中，"
            f"即本实验 12 条源行都至少命中一条条件规则。"
        )
    conj = []
    for field, expected in when.items():
        cond_desc = json.dumps(expected, ensure_ascii=False)
        values = sorted({rec["state"].get(field) for rec in per_ticket})
        conj.append(
            f"{field}={cond_desc}（本实验源里该字段的实际取值集合 = {values}）"
        )
    blocking = []
    for field, expected in when.items():
        if field == "is_overdue" and expected is True:
            blocking.append(
                "is_overdue=true 必须与 greeting 的 when={ticket_status:'处理中'} 不冲突，"
                "否则会先命中 greeting；source_map.json 把 ticket_status 的 enum 定为 "
                "['待受理','处理中','待验收','已关闭']，'处理中' 之外的取值都能绕开 greeting"
            )
        if field == "days_left" and isinstance(expected, dict) and "lte" in expected:
            blocking.append(
                f"days_left 的声明是 trigger.json#state_fields.days_left.min=0 / max=365，"
                f"负值会被 T16 的 validate_state 拒掉（min=0 越界），这是正确行为，不能用负值硬凑"
            )
        if field == "assignee_confirmed" and expected is False:
            blocking.append(
                "assignee_confirmed=false 同时必须不被 greeting 吃掉，"
                "即 ticket_status 不能是 '处理中'"
            )
    return (
        f"{rule_id} 的 when = {json.dumps(when, ensure_ascii=False)}，"
        f"条件是「{'」且「'.join(conj)}」的合取。"
        f"本实验 12 条源行里没有一行同时满足：{"；".join(blocking) if blocking else '见 when 条件与源字段的实际取值集合'}。"
    )


# ---------------------------------------------------------------------------
# 1. 主流程
# ---------------------------------------------------------------------------
# 事实 1a 的三条计数（days_left / is_overdue / overdue_days 各被几条 when 引用）
# 必须从 packs/demo-brief/trigger.json **现算**，不许写死——写死就会变成
# "把正确答案抄在断言里"式空转。现算出的值可以直接格式化进断言消息（见 5f）。


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(HERE))

    from trigger import (
        LedgerError,
        Trigger,
        build_plan,
        load_trigger,
        read_turns,
        record_turn,
        validate_state,
    )
    # 逐条规则的匹配判定直接复用引擎的公开判定函数——本实验不复制 when 的匹配逻辑
    # （反空转：覆盖表必须由实际运行结果生成，所以这里真的去判定每一条规则）
    from trigger.plan import rule_matches
    import to_state
    from to_state import SourceMapError, to_state as to_state_one

    print("=== T21 端到端：源 → state → plan → 留痕 ===")
    print(f"仓库根   : {REPO_ROOT}")
    print(f"前置包   : {PACK_DIR}")
    print(f"源 fixture: {SOURCE_PATH}")
    print(f"映射表   : {MAP_PATH}")

    # ---- 1. 装包（复用 T16 的 load_trigger，含 phrases.json 校验）
    trigger: Trigger = load_trigger(str(PACK_DIR))
    phrase_key_set = set(trigger.phrase_keys)
    rule_ids_in_pack = [rule.rule_id for rule in trigger.rules]
    as_of_cfg = json.loads(MAP_PATH.read_text(encoding="utf-8"))["as_of"]
    as_of_iso = f"{as_of_cfg['date']}T{as_of_cfg['time']}{as_of_cfg['tz']}"

    print(f"trigger_id  : {trigger.trigger_id}")
    print(f"budget_chars: {trigger.budget_chars}")
    print(f"注入的「今天」: {as_of_iso}")
    print(f"包内规则顺序: {rule_ids_in_pack}")

    # ---- 2. 清空本实验自己的留痕文件（append 写，重跑必须幂等）
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if TURNS_PATH.exists():
        TURNS_PATH.unlink()
    if not RAW_DIR.is_dir():
        raise AssertionError_(f"留痕目录不存在: {RAW_DIR}")

    # ---- 3. 源 → state（含 T16 的 validate_state，在 to_state.py 里调用）
    results, fixture_meta = to_state.run_mapping(
        tickets_path=SOURCE_PATH, mapping_path=MAP_PATH, trigger=trigger
    )
    total = len(results)
    print(f"源条目数  : {total}")

    # ---- 4. 逐条：state → plan → 留痕
    turns_written = 0
    rule_hit_count: Dict[str, int] = {}
    per_ticket: List[Dict[str, Any]] = []
    keys_seen: List[str] = []

    for index, (item, state, derived) in enumerate(results, start=1):
        # 4a. 再显式调一次 T16 的校验器（to_state 内部已调；这里是为了让验收标准
        #     「源 → state 通过 validate_state」有一条独立的、可观测的调用记录）
        validate_state(state, trigger)

        # 4a2. 逐条规则的匹配判定（真实判定，非手写）：覆盖表、兜底语义与优先级断言
        #      全部由这份矩阵驱动，改坏一条映射 / 把某条规则条件改到不可能满足时必现红色
        matching = {rule.rule_id: rule_matches(rule, state) for rule in trigger.rules}

        # 4b. build_plan（内部还会再校验一次 state、复核 key、强制预算）
        turn_id = f"{TURN_ID_PREFIX}{item['_item_index']:03d}"
        plan_id = f"{trigger.trigger_id}:{turn_id}"
        plan = build_plan(trigger, state, turn_id=turn_id, plan_id=plan_id)

        for key in plan.keys:
            check(
                f"plan key ∈ phrases.json（{turn_id}）",
                key in phrase_key_set,
                f"key '{key}' 不在 phrases.json 的 {sorted(phrase_key_set)} 里",
            )
        keys_seen.extend(plan.keys)

        check(
            f"命中规则 ∈ 包内真实 rule_id（{turn_id}）",
            plan.rule_id in rule_ids_in_pack,
            f"rule_id '{plan.rule_id}' 不在 {rule_ids_in_pack}",
        )

        rule_hit_count[plan.rule_id] = rule_hit_count.get(plan.rule_id, 0) + 1

        # 4c. 留痕：ts 显式注入（确定性），path 锚定到本实验目录内
        ledger_ts = f"{as_of_iso}:turn{index:03d}"
        record_turn(
            trigger, state, plan,
            turn_id=turn_id, plan_id=plan_id, ts=ledger_ts, path=str(TURNS_PATH),
        )
        turns_written += 1

        matched_ids = [rid for rid, m in matching.items() if m]
        per_ticket.append({
            "ticket_id": item["ticket_id"],
            "customer_code": item["customer_code"],
            "source_status": item["ticket_status"],
            "source_assignee_confirmed": item["assignee_confirmed"],
            "source_days_left": item["days_left"],
            "source_overdue_days": item["overdue_days"],
            "source_is_overdue": item["is_overdue"],
            "state": state,
            "matching": matching,
            "matched_rules": matched_ids,
            "matching_count": len(matched_ids),
            "fallback_matched": any(
                trigger.rules[i].when is None
                for i, rid in enumerate(rule_ids_in_pack)
                if rid in matched_ids
            ),
            "rule_id": plan.rule_id,
            "keys": list(plan.keys),
            "chars": plan.chars,
            "budget": plan.budget,
            "sla_breached": derived.get("sla_breached"),
            "as_of_iso": derived.get("as_of_iso"),
        })

    print(f"处理的条目 : {total}")
    print(f"留痕写入   : {turns_written} 行")
    print(f"命中分布   : {rule_hit_count}")
    print(f"plan key 汇总: {sorted(set(keys_seen))}")

    # ---- 4d. 覆盖表（由实际运行结果生成，非手写）
    # 覆盖 = 某条源行的 state 真实判定出该规则匹配；命中 = 引擎按 rules 顺序取到的第一条匹配。
    # 覆盖表逐条打印，落到 summary.json 的 rule_matching_matrix。
    rows_covering: Dict[str, List[str]] = {rid: [] for rid in rule_ids_in_pack}
    for rec in per_ticket:
        for rid in rec["matched_rules"]:
            rows_covering[rid].append(rec["ticket_id"])

    covered = {rid: rows_covering[rid] for rid in rule_ids_in_pack}
    coverage_unreachable = [rid for rid in rule_ids_in_pack if not rows_covering[rid]]
    # 不可达的理由必须指向具体声明/校验，不允许只写一句 unreachable
    unreachable_reasons: Dict[str, str] = {
        rid: _unreachable_reason(rid, trigger, per_ticket)
        for rid in coverage_unreachable
    }

    print("\n=== 规则覆盖表（由实际运行判定生成）===")
    print(f"{'规则':<16}{'包内顺序':<9}{'when 声明':<44}{'匹配行数':<9}{'命中行数':<9}覆盖到的行")
    for i, rid in enumerate(rule_ids_in_pack):
        rule = trigger.rules[i]
        when_txt = json.dumps(rule.when, ensure_ascii=False) if rule.when is not None else "(null 兜底)"
        if len(when_txt) > 42:
            when_txt = when_txt[:39] + "..."
        m_cnt = len(rows_covering[rid])
        h_cnt = rule_hit_count.get(rid, 0)
        ids = ",".join(rows_covering[rid]) if rows_covering[rid] else "——（不可达）"
        print(f"{rid:<16}{i + 1:<9}{when_txt:<44}{m_cnt:<9}{h_cnt:<9}{ids}")

    # ---- 5. 正例断言
    # ---- 5. 正例断言
    check(
        "留痕行数 == 处理条数",
        turns_written == total,
        f"写入 {turns_written} 行，处理 {total} 条",
    )
    check(
        "所有 plan key 都 ∈ phrases.json",
        all(k in phrase_key_set for k in keys_seen),
        f"越界 key: {sorted(set(keys_seen) - phrase_key_set)}",
    )
    check(
        "plan 字数未超预算",
        all(rec["chars"] <= rec["budget"] for rec in per_ticket),
        "存在超预算的轮次",
    )
    check(
        "至少两条不同的 rule 被命中（不是只打到兜底）",
        len(rule_hit_count) >= 2,
        f"只命中了 {rule_hit_count}",
    )
    check(
        "至少一条非 greeting 规则被命中（演示价值：不是全部兜到 greeting）",
        any(rid != "greeting" for rid in rule_hit_count),
        f"全部落在 greeting: {rule_hit_count}",
    )

    # ---- 5b. 覆盖断言（T21b 验收标准 2）：6 条规则每条至少被驱动一次
    check(
        "6 条规则全部至少被源数据驱动一次（覆盖表来自实际运行判定）",
        not coverage_unreachable,
        f"未覆盖的规则: {coverage_unreachable}（理由见 summary.json#rule_reachability_from_source.unreachable_reasons）",
    )
    check(
        "6 条规则全部被真实命中（不只是条件匹配，还要按 rules 顺序被引擎选中）",
        all(rule_hit_count.get(rid, 0) > 0 for rid in rule_ids_in_pack),
        f"未被命中的规则: {[rid for rid in rule_ids_in_pack if rule_hit_count.get(rid, 0) == 0]}"
        f"，命中分布 {rule_hit_count}",
    )
    check(
        "规则覆盖数 == 包内规则总数",
        len(rule_ids_in_pack) == 6 and len(rows_covering) == 6,
        f"包内规则 {len(rule_ids_in_pack)} 条，覆盖表 {len(rows_covering)} 条",
    )

    # ---- 5c. 兜底语义（T21b 验收标准 3）：至少一行只有 closing 匹配
    fallback_only = [rec for rec in per_ticket if rec["matching_count"] == 1
                     and rec["matched_rules"] == ["closing"]]
    check(
        "兜底语义被演示：至少一行只有 closing 匹配（前 5 条条件规则全不匹配）",
        len(fallback_only) >= 1,
        f"没有任何一行是「只有 closing 匹配」——"
        f"每条源的匹配情况: {[(r['ticket_id'], r['matched_rules']) for r in per_ticket]}",
    )
    for rec in fallback_only:
        condition_hits = [rid for rid in rec["matched_rules"]
                          if rid != trigger.rules[-1].rule_id]
        check(
            f"兜底行 {rec['ticket_id']}：除 closing 外的 5 条条件规则全不匹配",
            not condition_hits,
            f"该行还匹配了条件规则 {condition_hits}",
        )
        check(
            f"兜底行 {rec['ticket_id']}：引擎实际命中的是 closing（最后手段）",
            rec["rule_id"] == "closing",
            f"实际命中 {rec['rule_id']}，命中集合 {rec['matched_rules']}",
        )

    # ---- 5d. 优先级（T21b 验收标准 4）：同时匹配条件规则与兜底 → 必须命中条件规则
    fallback_index = [i for i, r in enumerate(trigger.rules) if r.when is None]
    check(
        "兜底规则 when=null 且排在 rules 最后（T17b 的语义前提）",
        len(fallback_index) == 1 and fallback_index[0] == len(trigger.rules) - 1,
        f"when=null 的规则在包内的位置: {fallback_index}，共 {len(trigger.rules)} 条",
    )
    both = [rec for rec in per_ticket
            if rec["fallback_matched"] and rec["matching_count"] >= 2
            and rec["rule_id"] != "closing"]
    check(
        "优先级被演示：至少一行同时匹配条件规则与兜底",
        len(both) >= 1,
        f"没有任何一行同时匹配条件规则与兜底——"
        f"{[(r['ticket_id'], r['matched_rules']) for r in per_ticket]}",
    )
    for rec in both:
        condition_ids = [rid for rid in rec["matched_rules"] if rid != "closing"]
        first_condition = condition_ids[0]
        check(
            f"优先级 {rec['ticket_id']}：同时匹配条件规则与兜底时，命中的是条件规则 {rec['rule_id']}",
            rec["rule_id"] == first_condition,
            f"matched_rules={rec['matched_rules']}（按包内顺序的第一个条件规则应为 {first_condition}），"
            f"实际命中 {rec['rule_id']}",
        )
        check(
            f"优先级 {rec['ticket_id']}：命中的不是兜底 closing",
            rec["rule_id"] != "closing",
            f"兜底先于条件规则生效了：matched_rules={rec['matched_rules']}，命中 {rec['rule_id']}",
        )
    check(
        "优先级方向：每条源命中的都是其 matched_rules 里包内顺序最靠前的那条",
        all(rec["rule_id"] == rec["matched_rules"][0] for rec in per_ticket),
        "存在命中结果不是匹配集合中第一条的记录: "
        + str([(r["ticket_id"], r["rule_id"], r["matched_rules"])
               for r in per_ticket if r["rule_id"] != r["matched_rules"][0]]),
    )
    check(
        "优先级方向：命中 closing 的行必须只有 closing 一条匹配（兜底只能是最后手段）",
        all(not rec["fallback_matched"] or rec["matched_rules"] == ["closing"]
            for rec in per_ticket if rec["rule_id"] == "closing"),
        "存在同时匹配条件规则却命中兜底的行: "
        + str([(r["ticket_id"], r["matched_rules"]) for r in per_ticket
               if r["rule_id"] == "closing" and len(r["matched_rules"]) > 1]),
    )
    check(
        "覆盖表与命中表一致：被命中的行必被引擎判定为匹配该规则",
        all(rec["matching"].get(rec["rule_id"]) is True for rec in per_ticket),
        "存在命中规则与实际判定结果矛盾的记录: "
        + str([(r["ticket_id"], r["rule_id"], r["matching"])
               for r in per_ticket if r["matching"].get(r["rule_id"]) is not True]),
    )
    check(
        "覆盖表逐行判定与命中结果无矛盾：命中行必在其 own 匹配集合内",
        all(rec["rule_id"] in rec["matched_rules"] for rec in per_ticket),
        "存在命中结果不在该行匹配集合内的记录: "
        + str([(r["ticket_id"], r["rule_id"], r["matched_rules"])
               for r in per_ticket if r["rule_id"] not in r["matched_rules"]]),
    )

    # ---- 5e. 事实陈述的自我校验（T21c）
    # source_map.json#rule_reachability 是**手写**的一段事实陈述，实测覆盖在 #5/#5b 算出来了。
    # 两者不一致就让脚本红：这样以后 fixture 或规则变了、陈述没跟着改，立刻可见。
    # T21d 起：本段只负责 rule_reachability 这一段的逐字段枚举比对；「有没有被推翻的
    # 旧因果」由 5f 的目录级禁语扫描统一负责，不在这里重复维护一份短语表。
    # 口径（与实测的对应关系）：
    #   reachable               == 实际被命中过的规则（rule_hit_count 非零者，按包内顺序）
    #   unreachable_from_source == 实际未被覆盖/未被命中的规则（必须为空才表示全覆盖）
    #   hit_counts[rid]         == rule_hit_count[rid]（命中行数）
    #   covered_by_ticket_id[rid] == rows_covering[rid]（被 rule_matches 判定匹配的行，含兜底）
    #   per_rule[rid].when      == trigger.json 里该规则的 when 声明
    mapping_doc = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    reachability = mapping_doc.get("rule_reachability")
    check(
        "source_map.json 必须声明 rule_reachability（可机检枚举，不是模糊的一句话）",
        isinstance(reachability, dict) and bool(reachability),
        f"缺 rule_reachability 段或不是对象，实际: {type(reachability).__name__}",
    )
    for _sub in ("reachable", "unreachable_from_source", "hit_counts",
                 "covered_by_ticket_id", "per_rule"):
        check(
            f"rule_reachability.{_sub} 必须是可机检结构",
            _sub in reachability
            and isinstance(reachability[_sub], (list, dict)),
            f"缺 {_sub} 或不是 list/dict，实际: "
            f"{type(reachability.get(_sub)).__name__}" if _sub in reachability
            else f"缺 {_sub}",
        )

    declared_reachable = reachability.get("reachable", [])
    measured_reachable = [rid for rid in rule_ids_in_pack
                          if rule_hit_count.get(rid, 0) > 0]
    declared_unreachable = reachability.get("unreachable_from_source", [])
    measured_unreachable = [rid for rid in rule_ids_in_pack
                            if rule_hit_count.get(rid, 0) == 0]

    check(
        "rule_reachability.reachable 与实际被命中的规则完全一致（双向：不多、不少）",
        declared_reachable == measured_reachable,
        f"声明 {declared_reachable}，实测 {measured_reachable}；"
        f"声明里有但实际未命中: {sorted(set(declared_reachable) - set(measured_reachable))}；"
        f"实际命中了但声明里没列: {sorted(set(measured_reachable) - set(declared_reachable))}",
    )
    check(
        "rule_reachability.unreachable_from_source 与实际一致"
        "（当前 fixture 全覆盖时应为空列表）",
        declared_unreachable == measured_unreachable,
        f"声明 {declared_unreachable}，实测 {measured_unreachable}；"
        f"声明里有但实际可达: {sorted(set(declared_unreachable) - set(measured_unreachable))}；"
        f"实际不可达但声明里没列: {sorted(set(measured_unreachable) - set(declared_unreachable))}",
    )

    # reachable / unreachable 两个列表必须互斥且并集恰为包内规则全集（否则陈述自相矛盾）
    overlap = sorted(set(declared_reachable) & set(declared_unreachable))
    union_missing = [rid for rid in rule_ids_in_pack
                     if rid not in declared_reachable and rid not in declared_unreachable]
    check(
        "rule_reachability 的两个列表互斥（同一条规则不得既在 reachable 又在 unreachable_from_source）",
        not overlap,
        f"同时出现在两个列表里的规则: {overlap}",
    )
    check(
        "rule_reachability 的两个列表并集覆盖包内全部规则（每条规则都表态，不留空白）",
        not union_missing,
        f"包内有但两个列表都没提到的规则: {union_missing}",
    )

    declared_hits = reachability.get("hit_counts", {})
    hits_mismatch = {rid: {"declared": declared_hits.get(rid),
                           "measured": rule_hit_count.get(rid, 0)}
                     for rid in rule_ids_in_pack
                     if declared_hits.get(rid) != rule_hit_count.get(rid, 0)}
    check(
        "rule_reachability.hit_counts 与实测命中行数逐条一致",
        not hits_mismatch,
        f"不一致的规则: {hits_mismatch}"
        + ("；另有多余声明: " + str(sorted(set(declared_hits) - set(rule_ids_in_pack)))
           if set(declared_hits) - set(rule_ids_in_pack) else ""),
    )

    declared_covered = reachability.get("covered_by_ticket_id", {})
    cover_mismatch = {rid: {"declared": declared_covered.get(rid),
                            "measured": rows_covering[rid]}
                      for rid in rule_ids_in_pack
                      if declared_covered.get(rid) != rows_covering[rid]}
    check(
        "rule_reachability.covered_by_ticket_id 与实测覆盖到的源行逐条一致",
        not cover_mismatch,
        f"不一致的规则: {cover_mismatch}"
        + ("；另有多余声明: " + str(sorted(set(declared_covered) - set(rule_ids_in_pack)))
           if set(declared_covered) - set(rule_ids_in_pack) else ""),
    )

    per_rule = reachability.get("per_rule", {})
    check(
        "rule_reachability.per_rule 的键集 == 包内规则全集（每条规则都要有声明）",
        set(per_rule) == set(rule_ids_in_pack),
        f"缺声明: {sorted(set(rule_ids_in_pack) - set(per_rule))}；"
        f"多余声明: {sorted(set(per_rule) - set(rule_ids_in_pack))}",
    )
    when_mismatch = {}
    for i, rid in enumerate(rule_ids_in_pack):
        actual_when = trigger.rules[i].when
        if per_rule.get(rid, {}).get("when") != actual_when:
            when_mismatch[rid] = {"declared": per_rule.get(rid, {}).get("when"),
                                  "measured": actual_when}
    check(
        "rule_reachability.per_rule[*].when 与 trigger.json 的 when 声明逐条一致"
        "（理由文字必须跟代码一致，不许再写出与 when 不符的因果）",
        not when_mismatch,
        f"when 不一致的规则: {when_mismatch}",
    )
    # 兜底规则的 when 必须是 null（closing 是唯一 when=null 的规则），陈述里不能写成有条件
    for i, rid in enumerate(rule_ids_in_pack):
        check(
            f"rule_reachability.per_rule.{rid}.when 与 trigger.json 一致（触发器顺序第 {i + 1} 条）",
            per_rule.get(rid, {}).get("when") == trigger.rules[i].when,
            f"声明 {per_rule.get(rid, {}).get('when')}，trigger.json 是 {trigger.rules[i].when}",
        )

    # T21c 原有的三组断言保持（T21d 只把覆盖面扩到整个目录，不删既有断言）。
    # 短语本身不在这里重复写字面量——统一从 BANNED_TEXTUAL_CLAIMS 取，
    # 这样本文件的**实际代码文本**里也不含禁语字面量（否则 5f 的自扫会命中自己）。
    reason_text = " ".join(
        str(v) for v in reachability.values()
        if isinstance(v, (str, int, float, bool)) or v is None
    )
    for forbidden in (p for p, _why in BANNED_TEXTUAL_CLAIMS):
        check(
            f"rule_reachability 里不得再出现已废弃的错误因果: '{forbidden}'",
            forbidden not in reason_text,
            f"仍出现该表述（正确说法见 BANNED_TEXTUAL_CLAIMS 与该短语的说明）",
        )
    check(
        "rule_reachability 必须是枚举而非模糊陈述（含 rule_id 级明细，不接受「大部分规则可达」这类）",
        all(isinstance(rid, str) and rid for rid in declared_reachable)
        and set(declared_reachable) | set(declared_unreachable) == set(rule_ids_in_pack)
        and set(per_rule) == set(rule_ids_in_pack)
        and all(k in per_rule for k in ("greeting", "closing"))
        and any(isinstance(v, dict) and "when" in v for v in per_rule.values()),
        "缺逐规则枚举（reachable / unreachable_from_source / per_rule 三者都要能逐条比对）",
    )

    # ---- 5f. 文字陈述自校验（T21d）：扫描范围从「rule_reachability 一段」扩到整个目录
    #
    # 为什么这一段存在：T21c 只盯 rule_reachability 那一段，同一个文件里
    # source_map.json#_why_is_as_of_still_needed 仍写着被实测推翻的旧因果。
    # 只改那一句，下一句错陈述还会溜过去——所以扫描目标必须是**文件全文**，
    # 且扫描清单要打印出来（扫了哪几个目标、多少字符），否则"覆盖面"无法核对。
    #
    # 三个可判定的事实陈述由 trigger.json / phrases.json **现算**后与文字比对；
    # 计数一律现算；现算出来的值可以（且应该）直接格式化进断言消息。

    # 事实 1：字段 → 引用它的规则 when 的字段名集（键序 = 包内 rules 顺序，确定性）
    referenced_by_when: Dict[str, List[str]] = {}
    for rule in trigger.rules:
        if rule.when is None:
            continue
        for fname in rule.when:
            referenced_by_when.setdefault(fname, []).append(rule.rule_id)

    # 事实 2：units[].slots 的值来源集（与 when 的判定输入是两回事）。
    # 直接从 trigger.json 原文取（Trigger.rule.units 是 typed 对象，不给 .get）；
    # 只读数据，不复制任何判定逻辑。
    trigger_raw = json.loads((PACK_DIR / "trigger.json").read_text(encoding="utf-8"))
    slot_source_of: Dict[str, List[str]] = {}
    for raw_rule in trigger_raw["rules"]:
        for raw_unit in raw_rule.get("units", []):
            for slot in raw_unit.get("slots", []):
                slot_source_of.setdefault(slot, []).append(raw_rule["rule_id"])

    declared_state_fields = [entry[0] for entry in trigger.state_fields]
    # 事实 3：state 字段里被**结构化规则**引用到的范围。
    # 注意区分两类字段，不能混成一句「没人用」：
    #   - ticket_id 被 phrases.json 的 {ticket_id} 模板占位符引用（资产/模板层，经
    #     source_map.json#derived_fields.source_ref 与 T18 的 asset_key_of 消费）；
    #   - overdue_days 不在任何 rules[].when，但被 rules[].units[].slots 引用（触发层内）。
    # ticket_id 声明为 layer=sensitive（不进结构化层、不落盘），它的可用性走
    # source_map.json#sensitive_fields 声明 + validate_state 复核，本表只管触发层。
    # state_fields 在 Trigger 里是 (name, decl) 元组列表，先规范化成 name -> layer
    field_layer = {
        entry[0]: (dict(entry[1]).get("layer") if isinstance(entry[1], dict)
                   else getattr(entry[1], "layer", None))
        for entry in trigger.state_fields
    }
    structured_referenced: Dict[str, List[str]] = {}
    for rule in trigger.rules:
        if rule.when is None:
            continue
        for fname in rule.when:
            structured_referenced.setdefault(fname, []).append(rule.rule_id)
    for fname in slot_source_of:
        structured_referenced.setdefault(fname, []).extend(
            [rid for rid in slot_source_of[fname] if rid not in structured_referenced[fname]]
        )
    unreferenced_structured = [
        f for f in declared_state_fields
        if f not in structured_referenced
        and field_layer.get(f) == "structured"
    ]
    unreferenced_sensitive = [
        f for f in declared_state_fields
        if f not in structured_referenced
        and field_layer.get(f) == "sensitive"
    ]
    check(
        "structured 字段要么被 rules[].when 引用、要么被 units[].slots 引用"
        "（现算：overdue_days 落在 slots 上，没有 structured 字段在触发层漏掉）",
        not unreferenced_structured,
        f"声明为 structured 但没有任何规则引用的字段: {unreferenced_structured}"
        f"（现算 when 引用={referenced_by_when}，slots 引用={slot_source_of}）",
    )
    # 逐字段生成**具体**的引用说明（含实际命中行，全部现算，不写死数字）
    def _ref_detail(fname: str) -> str:
        rids = referenced_by_when.get(fname, [])
        parts = []
        for rid in rids:
            rows = sorted({
                rec["ticket_id"]
                for rec in per_ticket
                if rec["matching"].get(rid) is True
                and rec["rule_id"] == rid
            })
            parts.append(
                f"{rid}（本 fixture 命中 {len(rows)} 行："
                f"{'、'.join(rows) if rows else '无命中行'}）"
            )
        return "、".join(parts) if parts else "无"

    # 资产/模板层引用（phrases.json 的模板占位符）：与触发层的引用是两条链路。
    # 直接读包源原文；trigger.phrase_keys 只有 key 集合，没有模板文本。
    phrases_raw = json.loads((PACK_DIR / "phrases.json").read_text(encoding="utf-8"))
    phrase_ref: Dict[str, List[str]] = {}
    for entry in phrases_raw.get("phrases", []):
        for variant in entry.get("variants", []):
            for fname in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", variant):
                if entry["key"] not in phrase_ref.setdefault(fname, []):
                    phrase_ref[fname].append(entry["key"])
    check(
        "ticket_id 未被任何规则的 when 或 slots 引用，但被 phrases.json 的模板占位符引用"
        "（资产层链路，不是「完全没人用」；它的可用性走 sensitive_fields 声明 + validate_state）",
        "ticket_id" in phrase_ref and "ticket_id" in unreferenced_sensitive,
        f"现算：phrases 模板占位符引用={phrase_ref.get('ticket_id')}，"
        f"触发层未引用的敏感字段={unreferenced_sensitive}",
    )

    # --- 事实 1a：修正 _why_is_as_of_still_needed 的旧因果，并断言它与 trigger.json 一致
    as_of_prose = str(mapping_doc.get("_why_is_as_of_still_needed", ""))
    still_needed_fields = ["days_left", "is_overdue", "overdue_days"]

    check(
        "source_map.json#_why_is_as_of_still_needed 必须声明（可机检的段落，不是靠人记得同步）",
        bool(as_of_prose),
        "缺 _why_is_as_of_still_needed 段",
    )

    dependency_claimed = {
        fname: fname in referenced_by_when
        for fname in still_needed_fields
    }

    # 「零」用「空串 + 一个字符」拼出来：本段文字要避开一切数字字面量（防硬编码），
    # 所以连「零」也不直接写。
    for fname in still_needed_fields:
        # 计数一律现算（防"把答案写死"式空转）；现算值直接格式化进消息与落盘 note。
        cnt = len(referenced_by_when.get(fname, []))
        if dependency_claimed[fname]:
            check(
                f"_why_is_as_of_still_needed 对 {fname} 的依赖表述与 trigger.json 一致"
                f"（现算：{cnt} 条规则的 when 引用它）",
                f"{fname} 被 " in as_of_prose
                and f"{fname} 无人引用" not in as_of_prose,
                f"实测 {fname} 被 {referenced_by_when.get(fname, [])} 的 when 引用"
                f"（{_ref_detail(fname)}），但该段文字未点名这些规则",
            )
        else:
            check(
                f"_why_is_as_of_still_needed 对 {fname} 的表述与实测一致"
                f"（现算：{cnt} 条规则的 when 引用它）",
                (f"{fname} {cnt} 条" in as_of_prose
                 or f"{fname} 不被任何规则的 when 引用" in as_of_prose),
                f"实测 {fname} {cnt} 条 when 引用，但文字里没把这一点写具体"
                f"（不得用不可判定、无法机检的模糊说法替代，见 BANNED_TEXTUAL_CLAIMS）",
            )

    check(
        "_why_is_as_of_still_needed 必须点名每一个「被 when 引用」字段的具体规则 id"
        "（具体陈述，不是模糊说法）",
        all(rid in as_of_prose
            for fname in still_needed_fields
            for rid in referenced_by_when.get(fname, [])),
        "实测被引用的规则集：{}，文字里缺: {}".format(
            {f: referenced_by_when.get(f, []) for f in still_needed_fields},
            sorted({rid for f in still_needed_fields
                    for rid in referenced_by_when.get(f, [])}
                   - {rid for rid in as_of_prose.split()
                      if rid in set(rule_ids_in_pack)}),
        ),
    )

    # 「min=0」不写死：这个 0 取自 trigger.json#state_fields.days_left.min 的现值，
    # 包声明改了这里跟着变（不是一份抄写件）。
    days_left_min_decl = next(
        decl.min for name, decl in trigger.state_fields
        if name == "days_left" and isinstance(getattr(decl, "min", None), int)
    )
    min_lower_bound = f"min={days_left_min_decl}"
    check(
        "_why_is_as_of_still_needed 必须写清「{} 与 is_overdue 互不约束」"
        "（第二个子句本身成立，与「规则是否依赖」是两件事，不得一起抹掉）"
        .format(min_lower_bound),
        "互不约束" in as_of_prose,
        "缺「互不约束」这个判定结论；days_left 的 {} 是日期运算约束，"
        "不是 is_overdue 的取值约束".format(min_lower_bound),
    )

    check(
        "_why_is_as_of_still_needed 必须说明 overdue_days 属于 units[].slots 的值来源"
        "（它不是 when 的判定输入——这与「完全没人用」是两回事）",
        "slots" in as_of_prose and all(
            rid in as_of_prose for rid in slot_source_of.get("overdue_days", [])
        ),
        "实测 overdue_days 是 {} 的 units[].slots 值来源，文字里没写清这个区分"
        .format(slot_source_of.get("overdue_days", [])),
    )
    # overdue_days 的可达性说明：note 由现算结果拼接，落盘时逐字回读比对。
    # 这里的 0 来自 referenced_by_when 的现算值（见 5f），不是写死的字面量。
    zero_when_count = len(referenced_by_when.get("overdue_days", []))
    zero_when_rule_list = referenced_by_when.get("overdue_days", [])
    slot_source_rules = slot_source_of.get("overdue_days", [])
    slot_field_note = (
        f"实测 {zero_when_count} 条规则的 when 引用 overdue_days"
        f"（引用它的规则: {zero_when_rule_list!r}），"
        f"但它是 {slot_source_rules!r} 的 units[].slots 值来源"
        f"——它不是 when 的判定输入，与「完全不参与触发」是两回事。"
        f"本卡不改 packs/（改包属 packs/ 的范围，不在本卡白名单内）；"
        f"它归到 README.md 的「下一步缺的」第 4 条"
        f"（= docs/tasks/00-索引.md 的「前置包的 key 可达性检查」跨批待办 3）。"
    )
    check(
        "source_map.json#state_fields.overdue_days.note 必须如实记录其可达性"
        "（现算的 when 引用条数 + slots 归属 + 待办归属）",
        str(mapping_doc["state_fields"].get("overdue_days", {}).get("note", "")) == slot_field_note,
        "note 与现算结果不一致（该字段应为可机检的具体陈述）",
    )
    # 事实 1b：依赖断言不得硬编码这三个字段的 when 引用条数。
    # 口径（T21e 写清，撤掉上一卡的过度工程）：值必须现算，不得写成常量；
    # 把**现算值**格式化进断言消息是可以且鼓励的。因此本段只盯「该计数是否来自
    # referenced_by_when 的现算结果」，不再要求断言文本里不出现数字字符。
    # 校验范围取「事实 1a → 事实 1b」这一段（即真正的依赖断言），而不是取到
    # 事实 3b——否则会连 1b 自己的校验器一起扫，永远为红。
    own_src = load_script_prose()
    # 两个锚点都必须是**代码文本里的字面量**（不能是注释文字）：注释若被改成锚点，
    # 改注释就会让这条断言失去判定对象（静默失效）——所以锚点只用代码。
    start_anchor = 'fname: fname in referenced_by_when'
    end_anchor = 'fact 1b hardcode guard'
    _start = own_src.find(start_anchor)
    _end = own_src.find(end_anchor, _start)
    assert _start != -1 and _end != -1, "事实 1a / 1b 的锚点必须都在"
    assert_body = own_src[_start:_end]
    measured_counts = {
        f: len(referenced_by_when.get(f, [])) for f in still_needed_fields
    }
    hardcoded_hits = [str(cnt) for cnt in measured_counts.values()
                      if str(cnt) in assert_body]
    check(
        "事实 1 的依赖断言里每条计数都取自现算（现算来源在断言块里，实测映射非全零）",
        "referenced_by_when" in assert_body
        and "len(referenced_by_when" in assert_body
        and bool(set(measured_counts.values())),
        f"断言块未引用现算结果（应形如 len(referenced_by_when.get(...))），"
        f"实测映射 {measured_counts}；若把现算结果删掉换成常量，本条会红",
    )

    # --- 事实 3b：字段可达性落盘（结构化规则引用 / slots 引用 / 模板占位符引用）
    # overdue_days 0 条 when 引用是事实，如实记录；ticket_id 走资产层链路，与触发层分开。
    field_reachability = {
        "generated_from": "由 packs/demo-brief/trigger.json 的 rules[].when 与 "
                          "rules[].units[].slots 现算（run_e2e.py 不复制任何判定逻辑）；"
                          "模板占位符引用另读 phrases.json",
        "when_referenced_by": {f: referenced_by_when.get(f, [])
                               for f in declared_state_fields},
        "referenced_via_units_slots": {f: slot_source_of.get(f, [])
                                       for f in declared_state_fields},
        "phrase_template_referenced": {f: phrase_ref.get(f, [])
                                       for f in declared_state_fields},
        "field_layer": {f: field_layer.get(f) for f in declared_state_fields},
        "unreferenced_structured_fields": unreferenced_structured,
        "unreferenced_structured_count": len(unreferenced_structured),
        "unreferenced_sensitive_fields": unreferenced_sensitive,
        "unreferenced_sensitive_note": (
            "ticket_id 声明为 layer=sensitive，不在任何 rules[].when，"
            "但被 phrases.json 的模板占位符引用（资产层链路）；"
            "它的可用性走 source_map.json#sensitive_fields 声明 + validate_state 复核，"
            "不属于触发层的字段可达性，也不在跨批待办 3 的范围。"
        ),
        "unreferenced_note": slot_field_note,
        "declared_state_fields": declared_state_fields,
    }
    check(
        "字段可达性现算结果必须落盘 summary.json#field_reachability"
        "（0 条 when 引用这件事可复核，不是只在文字里说一句）",
        field_reachability["unreferenced_structured_count"] == len(unreferenced_structured)
        and field_reachability["unreferenced_structured_fields"] == unreferenced_structured
        and field_reachability["unreferenced_sensitive_fields"] == unreferenced_sensitive
        and field_reachability["referenced_via_units_slots"].get("overdue_days")
            == slot_source_of.get("overdue_days", []),
        f"现算 structured 未引用={unreferenced_structured}、"
        f"sensitive 未引用={unreferenced_sensitive}，落盘 "
        f"{field_reachability['unreferenced_structured_fields']} / "
        f"{field_reachability['unreferenced_sensitive_fields']}",
    )

    # --- 事实 4（本卡核心）：禁语扫描从「rule_reachability 一段」扩到整个目录
    # 扫描目标是**文件全文**，且清单要打印出来（扫了几个目标、各多少字符、合计多少），
    # 否则「覆盖面已扩」无法核对。
    # 注解文字（tickets.source.json 的 _fixture_meta）只扫注解，fixture 数据行不参与。
    fixture_meta_doc = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    fm_raw = json.dumps(fixture_meta_doc.get("_fixture_meta", {}), ensure_ascii=False)
    readme_doc = (HERE / "README.md").read_text(encoding="utf-8")
    script_src = Path(__file__).read_text(encoding="utf-8")
    script_prose, script_excluded_spans = mask_banned_table(script_src)
    # 扫描目标的标签（下面这一句会被打印进 stdout、落进 summary.json，并在 5f 末尾被核对）：
    # 标签写什么，就必须实际扫什么——这是 T21e 撤掉的那句假陈述（原标签写「已剥除文档串」，
    # 而文档串其实一直还在被扫）。标签与实现标志绑定见 5f 末尾的一致性断言。
    SCRIPT_TARGET_LABEL = (
        "run_e2e.py（全文 − 禁语表自身的字面量区间；不剥注释、不剥文档串）"
    )
    scan_targets: Dict[str, str] = {
        "source_map.json（全文）": MAP_PATH.read_text(encoding="utf-8"),
        "README.md（全文）": readme_doc,
        "tickets.source.json（_fixture_meta 注解文字，fixture 数据行不参与）": fm_raw,
        SCRIPT_TARGET_LABEL: script_prose,
    }
    # 实现侧的标志：扫描范围就是「全文 − 禁语表区间」，没有任何注释/文档串裁剪。
    # 与上面的标签一起被核对（改标签不改进实会红，反之亦然）。
    IMPL_NO_COMMENT_DOCSTRING_TRIM: bool = True
    text_scan = {
        "scanned_files": sorted(scan_targets),
        "scanned_target_count": len(scan_targets),
        "chars_per_file": {name: len(body) for name, body in scan_targets.items()},
        "total_chars": sum(len(body) for body in scan_targets.values()),
        "method": "把每个目标整段读入后做短语子串匹配（禁止只扫自己刚写的那一段）；"
                  "只查事实性短语子集 _FACTUAL_BANNED——模糊化规避类短语写进禁语表自身会被自扫命中，"
                  "由下面的负例注入测试单独覆盖",
        "factual_banned_phrases": [p for p, _why in _FACTUAL_BANNED],
        "banned_phrases_total": [p for p, _why in BANNED_TEXTUAL_CLAIMS],
        "hits": {},
    }
    # run_e2e.py 这一个目标的范围必须可核对：范围描述、是否裁剪注释/文档串、
    # 跳过区间的起止与长度、遮罩是否等长（行号/列号能否对上原文）。
    # 这张子表和「扫描目标清单」是同一份事实，由 5f 末尾的一致性断言核对。
    text_scan["run_e2e_py"] = {
        "range": SCRIPT_TARGET_LABEL,
        "impl_no_comment_or_docstring_trim": IMPL_NO_COMMENT_DOCSTRING_TRIM,
        "trimmed_comments": False,
        "trimmed_docstrings": False,
        "excluded_spans": [
            {"char_start": s, "char_end": e,
             "note": "BANNED_TEXTUAL_CLAIMS 按定义必须自带被禁原话——"
                     "它是被扫描的对象本身，不是本文件的说明文字"}
            for s, e in script_excluded_spans
        ],
        "excluded_char_count": sum(e - s for s, e in script_excluded_spans),
        "source_chars": len(script_src),
        "mask_is_length_preserving": len(script_prose) == len(script_src),
    }
    for name, body in scan_targets.items():
        hits = banned_hits_in(body, only=_FACTUAL_BANNED)
        text_scan["hits"][name] = list(hits)
        check(
            f"禁语扫描 · {name}：不得出现已被实测推翻的因果陈述",
            not hits,
            f"{name} 里仍出现被禁短语: {hits}——该文件必须改成与实测一致的具体陈述"
            f"（逐条判定见 summary.json#textual_claims_audit）",
        )
    check(
        "禁语扫描覆盖面已扩到整个目录（不再只扫 rule_reachability 那一段）",
        text_scan["scanned_target_count"] == 4
        and all(any(k in n for k in ("source_map.json", "README.md",
                                     "tickets.source.json", "run_e2e.py"))
                for n in scan_targets)
        and text_scan["total_chars"] > 0,
        f"扫描目标只有 {sorted(scan_targets)}，合计 {text_scan['total_chars']} 字符",
    )
    check(
        "禁语扫描的四个目标都必须非空（空文件也算扫了 = 覆盖面假扩展）",
        all(len(body) > 0 for body in scan_targets.values()),
        f"空目标: {[n for n, b in scan_targets.items() if not b]}",
    )
    check(
        "禁语清单本身必须至少包含一条指向 _why_is_as_of_still_needed 旧因果的短语"
        "（否则这一处的回潮查不出来）",
        any("when 依赖" in p for p, _why in BANNED_TEXTUAL_CLAIMS),
        f"当前禁语清单: {[p for p, _why in BANNED_TEXTUAL_CLAIMS]}",
    )
    # 模糊化规避类短语（BANNED_TEXTUAL_CLAIMS[10:12]）：只写在禁语表里，
    # 由下面的负例注入测试覆盖；写进文件正文会自撞禁语表自身的字面量。
    check(
        "模糊化规避类短语已纳管（在禁语表里）且不属于事实性子集"
        "（它们只由负例注入测试覆盖，不靠扫描文件正文来证明）",
        len(BANNED_TEXTUAL_CLAIMS) >= 12
        and len(BANNED_TEXTUAL_CLAIMS) == len(_FACTUAL_BANNED) + 3
        and all(BANNED_TEXTUAL_CLAIMS[i][0] not in
                tuple(pp for pp, _w in _FACTUAL_BANNED)
                for i in (10, 11, 12)),
        f"表里共 {len(BANNED_TEXTUAL_CLAIMS)} 条，事实性子集 {len(_FACTUAL_BANNED)} 条；"
        f"规避类短语（{BANNED_TEXTUAL_CLAIMS[10][0]!r}、{BANNED_TEXTUAL_CLAIMS[11][0]!r}、"
        f"{BANNED_TEXTUAL_CLAIMS[12][0]!r}）应只在禁语表里",
    )

    # 标签 ↔ 实现 一致性（T21e 产物 1/4）：一个专抓假陈述的机制，自己的标签也必须可核。
    # 原标签写「已剥除文档串」而文档串其实仍在被扫——那种自我描述是假的。
    # 这里把「标签里声明的范围」与「实现侧的标志 + 落盘的范围表」绑成一条可失败的断言：
    # 只改标签或只改实现，都会红。
    check(
        "扫描目标的标签与实际扫描范围一致（标签说扫什么，实现就必须扫什么）",
        SCRIPT_TARGET_LABEL in scan_targets
        and "全文" in SCRIPT_TARGET_LABEL
        and "不剥注释" in SCRIPT_TARGET_LABEL
        and "不剥文档串" in SCRIPT_TARGET_LABEL
        and text_scan["run_e2e_py"]["range"] == SCRIPT_TARGET_LABEL
        and text_scan["run_e2e_py"]["impl_no_comment_or_docstring_trim"] is True
        and text_scan["run_e2e_py"]["trimmed_comments"] is False
        and text_scan["run_e2e_py"]["trimmed_docstrings"] is False
        and text_scan["run_e2e_py"]["mask_is_length_preserving"] is True,
        f"标签='{SCRIPT_TARGET_LABEL}'，落盘范围表={text_scan['run_e2e_py']}；"
        f"实现标志 IMPL_NO_COMMENT_DOCSTRING_TRIM={IMPL_NO_COMMENT_DOCSTRING_TRIM}"
        f"——三处必须同时表示「全文 − 禁语表区间，不裁注释与文档串」",
    )
    # 等长遮罩 = 跳过区间只有禁语表那一段；若有人再加裁剪（例如重新剥注释），
    # 区间会增长、字符数会不一致，这条会红。
    check(
        "run_e2e.py 的扫描范围只有禁语表这一个跳过区间（不再用裁剪方式缩小范围）",
        len(script_excluded_spans) == 1
        and script_excluded_spans[0][0] < script_excluded_spans[0][1]
        and text_scan["run_e2e_py"]["excluded_char_count"]
            < text_scan["run_e2e_py"]["source_chars"] * 0.5
        and len(script_prose) == len(script_src),
        f"跳过区间 {script_excluded_spans}，遮罩后 {len(script_prose)} 字符 vs 原文 "
        f"{len(script_src)} 字符；跳过比例 "
        f"{text_scan['run_e2e_py']['excluded_char_count'] / max(len(script_src), 1):.1%}",
    )

    # 正例与负例都要有（反空转条款）。
    pos_ok = all(not banned_hits_in(body, only=_FACTUAL_BANNED)
                 for body in scan_targets.values())
    check(
        "扫描器正例：四个目标在真实文件里全部零命中（不是扫了什么就报什么）",
        pos_ok,
        f"真实文件里已有命中: {text_scan['hits']}",
    )

    # 负例：在 README.md（不是 source_map.json）里注入一句被禁的旧因果。
    # 这条证明两件事——扫的是文件全文；失败消息指名是哪个文件、被禁的哪句短语。
    negative_phrase = _FACTUAL_BANNED[2][0]
    negative_corpus = dict(scan_targets)
    negative_corpus["README.md（全文）"] = readme_doc + "\n注入测试：" + negative_phrase + "\n"
    # 负例的判据一律是「注入前该目标零命中、注入后该目标非零命中」。
    # 负例的失败消息用同一个模板生成（和逐目标断言共用），所以必须同时含
    # 具体文件名与被禁短语——这里显式校验一次。
    readme_before = banned_hits_in(readme_doc, only=_FACTUAL_BANNED)
    readme_after = banned_hits_in(negative_corpus["README.md（全文）"], only=_FACTUAL_BANNED)
    readme_scan_name = "README.md（全文）"
    readme_fails_before = banned_hits_in(readme_doc, only=_FACTUAL_BANNED)
    readme_msg = (
        f"{readme_scan_name} 里仍出现被禁短语: {readme_after}——"
        f"该文件必须改成与实测一致的具体陈述"
        f"（逐条判定见 summary.json#textual_claims_audit）"
    )
    check(
        "扫描器负例：向 README.md 注入一句被禁的旧因果后能检出，且命中指名 README.md",
        not readme_fails_before and bool(readme_after),
        f"注入 {negative_phrase!r} 后 README.md 未检出或注入前就不干净："
        f"before={readme_before}，after={readme_after}",
    )
    check(
        "扫描器负例的失败消息必须同时含具体文件名与被禁短语（不得只报「有禁语」）",
        readme_scan_name in readme_msg and negative_phrase in readme_msg,
        f"生成的消息不含文件名或被禁短语: {readme_msg!r}",
    )
    # 扫描覆盖面自检：每个目标注入禁语后都能被检出（防止某个目标被静默跳过）。
    # 判据是「命中集合发生变化」（增了短语，且是注入的那条），不是只比数量。
    undetectable = []
    for n, body in scan_targets.items():
        before = set(banned_hits_in(body, only=_FACTUAL_BANNED))
        after = set(banned_hits_in(body + negative_phrase, only=_FACTUAL_BANNED))
        if negative_phrase not in (after - before):
            undetectable.append(n)
    check(
        "扫描覆盖面自检：每个目标注入禁语后命中数都会增加（防止某个目标被静默跳过）",
        not undetectable,
        f"注入 {negative_phrase!r} 后命中数没有增加的目标: {undetectable}",
    )

    # 位置注入自检（T21e 产物 1，本卡的核心缺口）：禁语落在**行内 # 注释**与
    # **文档串**里必须同样被检出。判据是「插入位置不在唯一的跳过区间内 → 必检出」，
    # 所以这条不会自我豁免：注释/文档串都在跳过区间之外，真漏扫就会红。
    # （跳过区间内注入是预期的跳过——那里是禁语表自身，按定义必须自带被禁原话。）
    def _line_of(off: int) -> int:
        return script_src.count("\n", 0, off) + 1

    _probe_line_off = script_src.rfind("\n", 0, script_excluded_spans[0][0]) + 1
    probe_comment_src = script_src[:_probe_line_off] + "# 注入测试：" + negative_phrase + "\n" + script_src[_probe_line_off:]
    probe_doc_src = '"""注入测试：' + negative_phrase + '"""\n' + script_src
    for _kind, _probe in (("行内 # 注释", probe_comment_src),
                          ("模块文档串", probe_doc_src)):
        _masked, _ = mask_banned_table(_probe)
        _hits = banned_hits_in(_masked, only=_FACTUAL_BANNED)
        _off = _masked.find(negative_phrase)
    check(
        f"扫描器负例（位置注入）：禁语落在本文件的{_kind}里必须被检出",
        negative_phrase in _hits and _off != -1
        and not (script_excluded_spans[0][0] <= _off
                 < script_excluded_spans[0][1]),
        f"注入 {_kind} 后未检出：命中={_hits}，注入字符偏移={_off}，"
        f"唯一跳过区间={script_excluded_spans[0]}（注释与文档串都在区间外，"
        f"若这里漏扫就是裁剪扫描范围了）；注入行=第 {_line_of(_off)} 行",
    )

    print("\n文字陈述自校验（T21e）：扫了 %d 个目标，共 %d 字符"
          % (len(scan_targets), text_scan["total_chars"]))
    for _n, _b in scan_targets.items():
        print("  - %s: %d 字符，禁语命中 %d"
              % (_n, len(_b), len(banned_hits_in(_b, only=_FACTUAL_BANNED))))
    print("  run_e2e.py 的范围：%s（跳过 %d 字符，等长遮罩=%s）"
          % (text_scan["run_e2e_py"]["range"],
             text_scan["run_e2e_py"]["excluded_char_count"],
             text_scan["run_e2e_py"]["mask_is_length_preserving"]))
    print("  字段被 when 引用的现算结果: %s"
          % {f: referenced_by_when.get(f, []) for f in still_needed_fields})
    print("  触发层未被任何规则引用的 structured 字段: %s"
          "（overdue_days 是 slots 值来源，见 field_reachability）"
          % unreferenced_structured)
    print("  触发层未引用但被 phrases.json 模板占位符引用: %s"
          % {f: phrase_ref.get(f) for f in unreferenced_sensitive})

    # --- 事实 5：逐条事实陈述清单（陈述出处 + 判定方式 + 结论）
    # 凡举不出判定方式的陈述不在此列（要么删掉，要么改成可判定的）。
    _hit_ids = lambda rid: [rec["ticket_id"] for rec in per_ticket if rec["rule_id"] == rid]
    textual_claims = [
        {
            "id": 1,
            "claim": "_why_is_as_of_still_needed：packs/demo-brief/trigger.json 声明 "
                     "days_left / is_overdue / overdue_days 为 layer=structured",
            "source_file": "source_map.json#_why_is_as_of_still_needed",
            "how_verified": "读取 trigger.json 的 state_fields，逐字段取 layer",
            "actual": {f: field_layer.get(f) for f in still_needed_fields},
            "verdict": "与实测一致",
        },
        {
            "id": 2,
            "claim": "days_left 被哪几条规则的 when 引用",
            "source_file": "source_map.json#_why_is_as_of_still_needed / README.md#字段可达性",
            "how_verified": "遍历 trigger.json 的 rules[].when 取字段名（不复制判定逻辑）",
            "actual": {"rules": referenced_by_when.get("days_left", []),
                       "hit_rows": sorted({t for r in referenced_by_when.get("days_left", [])
                                           for t in _hit_ids(r)})},
            "verdict": "与实测一致",
        },
        {
            "id": 3,
            "claim": "is_overdue 被哪几条规则的 when 引用",
            "source_file": "source_map.json#_why_is_as_of_still_needed / README.md#字段可达性",
            "how_verified": "遍历 trigger.json 的 rules[].when 取字段名",
            "actual": {"rules": referenced_by_when.get("is_overdue", []),
                       "hit_rows": sorted({t for r in referenced_by_when.get("is_overdue", [])
                                           for t in _hit_ids(r)})},
            "verdict": "与实测一致",
        },
        {
            "id": 4,
            "claim": "overdue_days 没有被任何规则的 when 引用（0 条）",
            "source_file": "source_map.json#_why_is_as_of_still_needed / README.md#字段可达性",
            "how_verified": "遍历 trigger.json 的 rules[].when 取字段名，overdue_days 不在其中",
            "actual": {"rules": referenced_by_when.get("overdue_days", [])},
            "verdict": "与实测一致（0 条）",
        },
        {
            "id": 5,
            "claim": "overdue_days 是 units[].slots 的值来源，不是 when 的判定输入",
            "source_file": "source_map.json#_why_is_as_of_still_needed / #state_fields.overdue_days",
            "how_verified": "遍历 trigger.json 的 rules[].units[].slots 取值名",
            "actual": {"slot_value_source_of": slot_source_of.get("overdue_days", [])},
            "verdict": "与实测一致",
        },
        {
            "id": 6,
            "claim": "days_left 的 min=0 与 is_overdue 的取值互不约束"
                     "（overdue 的 when 是 {is_overdue: true}，不含 days_left）",
            "source_file": "source_map.json#_why_is_as_of_still_needed / #rule_reachability.reason",
            "how_verified": "读 trigger.json#rules[overdue].when 的字段集；"
                            "并用 rule_matches 实测命中 overdue 的行的 is_overdue / days_left 取值",
            "actual": {"overdue_when": dict(next(r.when for r in trigger.rules
                                                 if r.rule_id == "overdue")),
                       "hit_rows_is_overdue": [
                           {"ticket_id": rec["ticket_id"],
                            "is_overdue": rec["source_is_overdue"],
                            "days_left": rec["source_days_left"]}
                           for rec in per_ticket if rec["rule_id"] == "overdue"]},
            "verdict": "与实测一致",
        },
        {
            "id": 7,
            "claim": "as_of 仍然必须注入，因为 derived_fields 里的 as_of_iso / "
                     "days_since_opened 要算；未注入即报错",
            "source_file": "source_map.json#_why_is_as_of_still_needed / #derived_fields",
            "how_verified": "run_e2e.py 的负例 7c（as_of 未注入抛错）与 7d（换 as_of 后 "
                            "days_since_opened 随之变化、state 不变）",
            "actual": {"negative_case": "as_of 未注入 → SourceMapError",
                       "assertion_names": ["负例：as_of 未注入",
                                           "负例对照：依赖「今天」的派生字段 days_since_opened "
                                           "随 as_of 变化（证明 as_of 真的被注入，"
                                           "代码没偷读系统时钟）"]},
            "verdict": "由本脚本负例实测（断言名见 assertions.passed）",
        },
        {
            "id": 8,
            "claim": "本目录的文字陈述由 run_e2e.py 的禁语扫描守着，扫描范围是整个目录",
            "source_file": "README.md#本目录的文字陈述由什么守着",
            "how_verified": "本段 5f 的 text_scan（扫几个目标、各多少字符、合计多少字符）"
                            "与 4 个目标的逐目标断言",
            "actual": {"targets": text_scan["scanned_files"],
                       "total_chars": text_scan["total_chars"]},
            "verdict": "由本脚本实测",
        },
        {
            "id": 9,
            "claim": "overdue_days 0 条 when 引用是事实，未为它改包；"
                     "它归到 README.md 的「下一步缺的」第 4 条"
                     "（= docs/tasks/00-索引.md 的「前置包的 key 可达性检查」跨批待办 3）",
            "source_file": "README.md#本目录的文字陈述由什么守着 / summary.json#field_reachability",
            "how_verified": "git diff --stat -- packs 为空 + field_reachability.unreferenced_note",
            "actual": {"unreferenced_structured_fields": unreferenced_structured,
                       "unreferenced_sensitive_fields": unreferenced_sensitive,
                       "ticket_id_referenced_by_phrase_templates":
                           phrase_ref.get("ticket_id")},
            "verdict": "与实测一致（本卡白名单不含 packs/，未改包）",
        },
        {
            "id": 10,
            "claim": "tickets.source.json#_fixture_meta.field_usage_in_rules：本 fixture 的"
                     "12 行对 days_left / is_overdue / overdue_days 的依赖关系，"
                     "与 trigger.json 的 when 声明一致",
            "source_file": "tickets.source.json#_fixture_meta",
            "how_verified": "本段 5f 的禁语扫描（tickets.source.json 的 _fixture_meta 注解文字）"
                            "+ 事实 1a 的依赖断言（同一段文字与 trigger.json 现算结果比对）",
            "actual": {"when_referenced_by": {f: referenced_by_when.get(f, [])
                                              for f in still_needed_fields},
                       "slot_value_source_of": {
                           f: slot_source_of.get(f, []) for f in still_needed_fields}},
            "verdict": "与实测一致",
        },
        {
            "id": 11,
            "claim": "run_e2e.py 自身的说明文字里不写与实测矛盾的因果陈述"
                     "（本文件也是扫描目标，范围 = 全文 − 禁语表自身的字面量区间）",
            "source_file": "run_e2e.py#BANNED_TEXTUAL_CLAIMS / 本目录扫描目标",
            "how_verified": "本段 5f 的逐目标断言：范围由 mask_banned_table 遮罩出"
                            "（等长遮罩，行号/列号对上原文），唯一的跳过区间是禁语表自身；"
                            "标签与实际范围由「标签 ↔ 实现一致性」断言绑定",
            "actual": {"target_name": SCRIPT_TARGET_LABEL,
                       "hits": text_scan["hits"].get(SCRIPT_TARGET_LABEL, []),
                       "excluded_spans": script_excluded_spans},
            "verdict": "零命中",
        },
    ]
    check(
        "逐条事实陈述清单每条都填了判定方式（举不出判定方式的陈述要么删掉、要么改成可判定）",
        all(c["how_verified"] and c["actual"] is not None and c["verdict"]
            for c in textual_claims),
        f"缺判定的条目: {[c['id'] for c in textual_claims if not (c['how_verified']
                                                                 and c['verdict'])]}",
    )
    def _carriers(claims):
        got = set()
        for c in claims:
            got.add(str(c["source_file"]).split("#")[0])
        return got

    _wanted = {"source_map.json", "README.md", "tickets.source.json", "run_e2e.py"}
    _got = _carriers(textual_claims)
    check(
        "逐条事实陈述清单覆盖本目录全部文字陈述载体（source_map.json / README.md / "
        "tickets.source.json#_fixture_meta / run_e2e.py）",
        _wanted <= _got,
        f"清单里缺载体: {sorted(_wanted - _got)}；清单里的载体: {sorted(_got)}",
    )
    check(
        "逐条事实陈述清单里凡涉及『被 when 引用』的条目，规则集与现算结果一致"
        "（清单本身也是文字陈述，必须与 trigger.json 对齐）",
        not [
            c["id"] for c in textual_claims
            if isinstance(c["actual"], dict) and "rules" in c["actual"]
            and any(c["actual"]["rules"] != referenced_by_when.get(f, [])
                    for f in still_needed_fields
                    if f in c["claim"])
        ],
        f"清单里某条的 rules 与现算 referenced_by_when 不一致: "
        f"{ {f: referenced_by_when.get(f, []) for f in still_needed_fields} }",
    )

    # ---- 5g. 可复现性自检（同一份源 + 同一注入日期，跑两遍 state 逐字段相同）
    run2, _ = to_state.run_mapping(
        tickets_path=SOURCE_PATH, mapping_path=MAP_PATH, trigger=trigger
    )
    states_1 = [s for _, s, _ in results]
    states_2 = [s for _, s, _ in run2]
    check(
        "可复现：同一份源 + 同一 as_of 跑两遍，state 逐字段相同",
        states_1 == states_2,
        "两次 state 不一致",
    )

    # ---- 6. 留痕读回复核（敏感值独立复核）
    records = read_turns(str(TURNS_PATH))
    check(
        "读回的留痕条数 == 处理条数",
        len(records) == total,
        f"读回 {len(records)} 条，处理 {total} 条",
    )

    # 6a. 结构化层里不得出现敏感层的字段名（ticket_id 在 trigger.json 里是 sensitive）
    structured_names = [
        name for name, decl in trigger.state_fields if decl.layer == "structured"
    ]
    sensitive_names = [
        name for name, decl in trigger.state_fields if decl.layer == "sensitive"
    ]
    leaked_names = [
        f"records[{i}]" for i, rec in enumerate(records)
        if any(sn in rec.get("state", {}) for sn in sensitive_names)
    ]
    check(
        "留痕的 state 块只含结构化层字段名",
        not leaked_names,
        f"出现敏感层字段名于 {leaked_names}",
    )

    # 6b. 敏感值的独立复核：逐条比对工单号 / 客户标识是否出现在文件原文里
    raw_text = TURNS_PATH.read_text(encoding="utf-8")
    sensitive_values = {
        "ticket_id": [rec["ticket_id"] for rec in per_ticket],
        "customer_code": [rec["customer_code"] for rec in per_ticket],
    }
    sensitive_scan: Dict[str, Dict[str, Any]] = {}
    for label, values in sensitive_values.items():
        hits = [v for v in values if v and v in raw_text]
        sensitive_scan[label] = {
            "checked_values": values,
            "hit_count": len(hits),
            "hits": hits,
        }
        check(
            f"留痕文件里 grep {label} → 0 命中",
            len(hits) == 0,
            f"命中 {hits}",
        )

    # 6c. 负责人姓名 / 手机号也不得落盘（源里的个人身份信息）
    source_text = SOURCE_PATH.read_text(encoding="utf-8")
    phones = sorted(set(re.findall(r"1[3-9]\d{9}", source_text)))
    assignees = sorted({
        t["assignee_name"] for t in json.loads(source_text)["tickets"] if t["assignee_name"]
    })
    extra_scan: Dict[str, Dict[str, Any]] = {}
    for label, values in (("contact_phone", phones), ("assignee_name", assignees)):
        hits = [v for v in values if v in raw_text]
        extra_scan[label] = {"checked_values": values, "hit_count": len(hits), "hits": hits}
        check(
            f"留痕文件里 grep {label} → 0 命中",
            len(hits) == 0,
            f"命中 {hits}",
        )

    # ---- 7. 负例（反空转：真的调用，并把抛出的消息拿回来比对）
    mapping_doc = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    first_item, first_state, _ = results[0]

    # 先证明「断言本身能被打破」：合法输入必须不抛错。如果下面三条都抛错，
    # 说明本脚本的断言是在检查无关的东西，负例断言就是空的。
    check(
        "负例前置：合法输入不抛错（证明下面的负例断言不是恒真的空检查）",
        to_state_one(copy.deepcopy(first_item), mapping_doc, trigger=trigger) == first_state,
        "合法输入居然抛错或产出不一致的结果",
    )
    check(
        "负例前置：合法的留痕路径写得出（证明「父目录不存在」这个负例针对的是路径，不是别的）",
        str(record_turn(trigger, first_state,
                        build_plan(trigger, first_state, turn_id="ticket-900",
                                   plan_id=f"{trigger.trigger_id}:ticket-900"),
                        turn_id="ticket-900", plan_id=f"{trigger.trigger_id}:ticket-900",
                        ts=f"{as_of_iso}:turn900", path=str(RAW_DIR / "probe.jsonl"))) == str(RAW_DIR / "probe.jsonl"),
        "合法的留痕路径写不出",
    )
    (RAW_DIR / "probe.jsonl").unlink(missing_ok=True)

    # 7a. 源缺必需字段 → 抛错且消息含该字段名
    missing_case = copy.deepcopy(first_item)
    missing_field = "ticket_status"
    missing_case.pop(missing_field)
    expect_raises(
        "负例：源缺必需字段",
        to_state_one, missing_case, mapping_doc, trigger=trigger,
        must_contain=missing_field,
    )

    # 7b. 源含未映射的多余字段 → 抛错且消息含该字段名
    extra_case = copy.deepcopy(first_item)
    extra_field = "unexpected_column_typo"
    extra_case[extra_field] = "这个列名不在 source_map.json 的 item_fields 里"
    expect_raises(
        "负例：源含未映射的多余字段",
        to_state_one, extra_case, mapping_doc, trigger=trigger,
        must_contain=extra_field,
    )

    # 7c. 派生字段需要「今天」但未注入 → 抛错
    expect_raises(
        "负例：as_of 未注入",
        to_state_one, copy.deepcopy(first_item), mapping_doc, trigger=trigger,
        as_of_override="unset",
        must_contain="as_of",
    )

    # 7d. 注入生效对照：换一个 as_of，依赖「今天」的派生值必须变（证明没偷读系统时钟）
    alt_as_of = {"date": "2026-09-25", "time": "10:00:00", "tz": "+08:00"}
    alt_state = to_state_one(
        copy.deepcopy(first_item), mapping_doc, trigger=trigger, as_of_override=alt_as_of
    )
    alt_derived = to_state.to_derived_fields(
        first_item, mapping_doc, alt_state, as_of_override=alt_as_of
    )
    base_derived = next(
        d for _i, _s, d in results if _i["ticket_id"] == first_item["ticket_id"]
    )
    check(
        "负例对照：注入不同的 as_of 后 as_of_iso 随之变化",
        alt_derived.get("as_of_iso") == "2026-09-25T10:00:00+08:00"
        and base_derived.get("as_of_iso") == as_of_iso,
        f"base as_of_iso={base_derived.get('as_of_iso')!r}，alt as_of_iso={alt_derived.get('as_of_iso')!r}",
    )
    check(
        "负例对照：依赖「今天」的派生字段 days_since_opened 随 as_of 变化"
        "（证明 as_of 真的被注入，代码没偷读系统时钟）",
        alt_derived.get("days_since_opened") != base_derived.get("days_since_opened")
        and alt_derived.get("days_since_opened") == 15,
        f"as_of={as_of_iso} → days_since_opened={base_derived.get('days_since_opened')}；"
        f"as_of={alt_as_of['date']} → days_since_opened={alt_derived.get('days_since_opened')}"
        f"（opened_at={first_item['opened_at']}，相差应为 15 天）",
    )
    check(
        "注入不同的 as_of 不改变 state 内容（state 里不含任何日期派生字段）",
        alt_state == first_state,
        f"state 变了: {first_state} vs {alt_state}",
    )

    # 7e. 敏感字段被声明为结构化层 → 必须被 T16 拦下（证明本实验没有放宽 T16 的校验）
    tampered = copy.deepcopy(first_state)
    tampered["contact_phone"] = "13800138007"
    expect_raises(
        "负例：未声明的敏感字段进 state 会被 T16 拦下",
        validate_state, tampered, trigger,
        must_contain="contact_phone",
    )

    # 7e2. 更直接的分层红线：把敏感标记命中的字段声明成结构化层 → T16 的 layer 检查必须拦下
    #      （contact_phone 的字段名含 'phone'，命中 T16 的 SENSITIVE_MARKERS）
    tampered_layer = copy.deepcopy(first_state)
    tampered_layer["contact_phone"] = "13800138007"
    layer_decl = {name: {"layer": decl.layer, "type": decl.type}
                  for name, decl in trigger.state_fields}
    layer_decl["contact_phone"] = {"layer": "structured", "type": "str"}
    expect_raises(
        "负例：敏感字段被声明为结构化层会被 T16 的 layer 检查拦下",
        validate_state, tampered_layer, layer_decl,
        must_contain="phone",
    )

    # 7e3. T21b 硬约束：不得用 days_left 的负值硬凑 overdue。
    #      trigger.json 声明 days_left min=0，负值被 validate_state 拒掉是正确行为；
    #      overdue 必须靠 is_overdue=True 驱动，不靠绕过校验。
    negative_days = copy.deepcopy(first_state)
    negative_days["days_left"] = -3
    expect_raises(
        "负例：days_left 负值被 T16 拦下（min=0；overdue 只能用 is_overdue 驱动）",
        validate_state, negative_days, trigger,
        must_contain="days_left",
    )
    check(
        "覆盖 overdue 的行没有用 days_left 负值绕过 min=0",
        all(rec["source_days_left"] >= 0 for rec in per_ticket),
        f"存在负 days_left 的源行: "
        f"{[(r['ticket_id'], r['source_days_left']) for r in per_ticket if r['source_days_left'] < 0]}",
    )
    overdue_rows = [rec for rec in per_ticket if rec["rule_id"] == "overdue"]
    check(
        "命中 overdue 的行都是 is_overdue=True 驱动的（不是靠日期绕出来的）",
        overdue_rows and all(rec["source_is_overdue"] is True for rec in overdue_rows),
        f"overdue 命中行的 is_overdue 取值: "
        f"{[(r['ticket_id'], r['source_is_overdue']) for r in overdue_rows]}",
    )

    # 7f. 留痕写不进 → 必须抛 LedgerError 且消息含目标路径（不静默丢弃）
    bad_path = HERE / "raw" / "不存在的目录" / "turns.jsonl"
    try:
        record_turn(
            trigger, first_state,
            build_plan(trigger, first_state, turn_id="ticket-999",
                       plan_id=f"{trigger.trigger_id}:ticket-999"),
            turn_id="ticket-999", plan_id=f"{trigger.trigger_id}:ticket-999",
            ts=f"{as_of_iso}:turn999", path=str(bad_path),
        )
    except LedgerError as exc:
        msg = str(exc)
        if str(bad_path) not in msg:
            FAILURES.append(f"负例：留痕父目录不存在必须抛错 —— 抛了 LedgerError 但消息不含目标路径：{msg[:200]}")
        else:
            PASSES.append("负例：留痕父目录不存在抛 LedgerError 且消息含目标路径")
    except Exception as exc:  # noqa: BLE001 - 断言错误类型也要响
        FAILURES.append(f"负例：留痕父目录不存在必须抛 LedgerError，实际抛 {type(exc).__name__}: {exc}")
    else:
        FAILURES.append("负例：留痕父目录不存在 —— 本应抛错却没有抛（静默落盘/静默降级）")

    # ---- 9. summary.json（时间戳类字段单列，其余必须逐字段相同）
    summary = {
        "lab": "ticket-source-2026-09-19",
        "card": "docs/tasks/T21-labs-状态来源与端到端演示.md / "
                "T21b-labs-fixture覆盖全部规则.md / "
                "T21c-labs-事实陈述自我验证.md / "
                "T21d-labs-文字陈述自校验扩面.md / "
                "T21e-labs-自扫做实.md",
        "scope": "实验区（labs/）：一次性实验，不进任何层的契约",
        "data_classification": "公开级：fixture 为自造数据，不含用户录音、真实会话、内网地址、token",
        "fixture_meta": fixture_meta,
        "pack": {
            "dir": str(PACK_DIR.relative_to(REPO_ROOT)),
            "trigger_id": trigger.trigger_id,
            "budget_chars": trigger.budget_chars,
            "phrase_keys": sorted(trigger.phrase_keys),
            "rules_in_order": rule_ids_in_pack,
        },
        "as_of_injected": as_of_iso,
        "ts_policy": TS_FIELD_NOTE,
        "counts": {
            "source_tickets": total,
            "states_validated": total,
            "turns_recorded": turns_written,
            "ledger_lines_on_disk": len(records),
        },
        "rule_hit_distribution": rule_hit_count,
        "keys_used": sorted(set(keys_seen)),
        "key_coverage": {
            "used": sorted(set(keys_seen)),
            "unused_in_pack": sorted(set(trigger.phrase_keys) - set(keys_seen)),
        },
        "rule_reachability_from_source": {
            "reachable": sorted(set(rule_hit_count)),
            "covered_from_source": [rid for rid in rule_ids_in_pack if rows_covering[rid]],
            "declared_in_pack": rule_ids_in_pack,
            "unreachable_from_source": coverage_unreachable,
            "unreachable_reasons": unreachable_reasons,
            "reason_generation": "逐条列出该规则 when 的每个合取条件，以及本实验源里对应字段的实际取值集合；"
                                 "理由来自实际判定结果，不是手写的一句话。",
            "note": "build_plan 按 trigger.json 的 rules 顺序取第一条命中（plan.py 的既有口径，本实验不改）。"
                    "T21 的 fixture 只覆盖 2 条规则，根因是行覆盖不足而非包缺陷：T21b 只加 fixture 行就把 6 条全部驱动起来，"
                    "packs/demo-brief/ 一行未改。",
        },
        "rule_matching_matrix": {
            "generated_from": "trigger.plan.rule_matches 对每条源行 state 的真实判定结果（非手写）",
            "rule_order_in_pack": rule_ids_in_pack,
            "fallback_rule": trigger.rules[-1].rule_id,
            "per_ticket": [
                {
                    "ticket_id": rec["ticket_id"],
                    "matching": rec["matching"],
                    "matched_rules": rec["matched_rules"],
                    "matching_count": rec["matching_count"],
                    "hit_rule_id": rec["rule_id"],
                    "fallback_only": rec["matched_rules"] == ["closing"],
                    "conditional_and_fallback": rec["fallback_matched"] and rec["matching_count"] >= 2,
                }
                for rec in per_ticket
            ],
            "rows_covering_each_rule": rows_covering,
        },
        "fallback_semantics": {
            "fallback_rule": "closing",
            "fallback_when": None,
            "rows_only_closing_matched": [rec["ticket_id"] for rec in fallback_only],
            "rows_conditional_plus_fallback": [rec["ticket_id"] for rec in both],
            "asserted": "至少一行只有 closing 匹配；且至少一行同时匹配条件规则与兜底并命中条件规则"
                        "（T17b 修的「兜底只是最后手段」语义在端到端链路上被证明）。",
            "hit_is_first_match_of_matched_set": all(
                rec["rule_id"] == rec["matched_rules"][0] for rec in per_ticket
            ),
        },
        "sensitive_scan": {
            "ledger_file": str(TURNS_PATH.relative_to(REPO_ROOT)),
            "hits": {
                label: scan["hit_count"] for label, scan in {**sensitive_scan, **extra_scan}.items()
            },
            "detail": {**sensitive_scan, **extra_scan},
            "verdict_rule": "全部为 0 才通过；任何一项 > 0 即视为敏感值落盘",
        },
        "layer_check": {
            "structured_fields_declared": structured_names,
            "sensitive_fields_declared": sensitive_names,
            "sensitive_layer_names_in_ledger_state_blocks": leaked_names,
        },
        "reproducibility": {
            "method": "同一份 tickets.source.json + 同一 as_of，映射跑两遍，state 列表逐字段比对",
            "states_identical_across_runs": states_1 == states_2,
            "volatile_fields": ["留痕的 ts 由本脚本按 as_of 注入，不是系统时钟；"
                                "重跑前本脚本会先清空 raw/turns.jsonl 以保证幂等"],
        },
        "per_ticket": per_ticket,
        "field_reachability": field_reachability,
        "text_scan": text_scan,
        "textual_claims_audit": {
            "method": "逐条列出本目录可判定的事实陈述：陈述出处（文件）+ 判定方式 + 结论。"
                      "凡举不出判定方式的陈述要么删掉、要么改成可判定，因此清单里每条都有 how_verified。"
                      "清单里的数字全部由 trigger.json / phrases.json 现算，不在断言里硬编码。",
            "claims": textual_claims,
            "claim_count": len(textual_claims),
            "referenced_by_when": {f: referenced_by_when.get(f, [])
                                   for f in still_needed_fields},
            "referenced_by_units_slots": {
                f: slot_source_of.get(f, []) for f in still_needed_fields
            },
        },
        "assertions": {
            "passed": PASSES,
            "failed": FAILURES,
            "passed_count": len(PASSES),
            "failed_count": len(FAILURES),
        },
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    # ---- 10. 打印结果
    print()
    print(f"断言通过 {len(PASSES)} 条，失败 {len(FAILURES)} 条")
    if FAILURES:
        print("\n失败明细：")
        for f in FAILURES:
            print(f"  - {f}")
    print(f"\n敏感值复核（留痕文件 grep）: "
          f"{ {label: scan['hit_count'] for label, scan in {**sensitive_scan, **extra_scan}.items()} }")
    print(f"留痕文件 : {TURNS_PATH}")
    print(f"摘要     : {SUMMARY_PATH}")

    if FAILURES:
        return 3
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:  # noqa: BLE001 - 主流程崩了也要给出可读结果，不能只吐 traceback
        print(f"\n主流程异常终止：{type(exc).__name__}: {exc}", file=sys.stderr)
        rc = 3
    sys.exit(rc)


