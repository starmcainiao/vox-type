"""
labs/ticket-source-2026-09-19/run_e2e.py — 源 → state → plan → 留痕（端到端 + 自断言）

跑一遍整条线，产出可复现的原始证据：
    raw/turns.jsonl   每轮一条留痕（state 结构化层 + plan，由 record_turn 写）
    summary.json      口径、命中分布、敏感值复核结果、可复现性自检结果

用法：
    cd /Volumes/macos/newProject/vox-type
    python3 labs/ticket-source-2026-09-19/run_e2e.py

退出码：0 = 全部断言通过；3 = 任一断言失败（含负例未如预期抛错）。

复用的产品 API（T21 反空转条款：不得在 labs 里复制 state/trigger 校验逻辑）：
    adapters.state_trigger.load_trigger    读 packs/demo-brief（含 phrases.json 校验）
    adapters.state_trigger.validate_state  state 快照的格式闸门（to_state.py 里调用）
    adapters.state_trigger.build_plan      state → plan
    adapters.state_trigger.record_turn     轮级留痕（敏感层绝不落盘）
    adapters.state_trigger.read_turns      读回留痕做复核

本文件不联网、不启停服务、不 import 系统时钟；不写 labs/ticket-source-2026-09-19/ 之外的任何路径。
"""

from __future__ import annotations

import copy
import json
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
def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(HERE))

    from adapters.state_trigger import (
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
    from adapters.state_trigger.trigger import _rule_matches
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
        matching = {rule.rule_id: _rule_matches(rule, state) for rule in trigger.rules}

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
    import re
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

    # ---- 8. 可复现性自检（同一份源 + 同一注入日期，跑两遍 state 逐字段相同）
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

    # ---- 9. summary.json（时间戳类字段单列，其余必须逐字段相同）
    summary = {
        "lab": "ticket-source-2026-09-19",
        "card": "docs/tasks/T21-labs-状态来源与端到端演示.md",
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
            "note": "build_plan 按 trigger.json 的 rules 顺序取第一条命中（trigger.py 的既有口径，本实验不改）。"
                    "T21 的 fixture 只覆盖 2 条规则，根因是行覆盖不足而非包缺陷：T21b 只加 fixture 行就把 6 条全部驱动起来，"
                    "packs/demo-brief/ 一行未改。",
        },
        "rule_matching_matrix": {
            "generated_from": "adapters.state_trigger.trigger._rule_matches 对每条源行 state 的真实判定结果（非手写）",
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
