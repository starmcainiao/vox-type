#!/usr/bin/env python3
"""
semantic_probe_control.py — 语义档的对照实验与混淆分析

职责：回答「top-1 只有 11.8% 是任务本身难，还是我自拟的 key 体系把任务变难了？」
不负责：不出结论性判定（判定归策划），只出数字。

两个对照：
  对照 1（去除我的 key 设计）：直接以语料自带的 12 类策略标签为目标空间，
          看 top-1 是多少。若同样低 → 任务本身难；若明显更高 → key 设计有问题。
  对照 2（混淆结构）：从 raw/semantic_probe.jsonl 统计「期望 key → 实际 top1 key」
          的最常见错法，看错误是否集中在语义相邻的功能上（那说明功能之间本就难分）。

用法：
  python3 semantic_probe_control.py --golden <jsonl> --probe raw/semantic_probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from semantic_probe import embed, cos  # 复用同一个嵌入函数，避免两份实现  # noqa: E402

# 12 类策略的功能描述（语料自带的标签体系，用于对照 1）
STRATEGY_INTENT: Dict[str, str] = {
    "策略1 礼貌问候": "开场问候，欢迎客户来电",
    "策略2 确认身份": "核验客户身份，说明为了安全需要核对信息",
    "策略3 重述或转述": "重述或转述客户刚说的问题",
    "策略4 细化问题": "追问细节，请客户补充具体情况",
    "策略5 情感管理": "共情安抚，为客户遇到困难表示抱歉和理解",
    "策略6 提供建议": "给出可选的解决方案让客户挑",
    "策略7 信息传达": "说明查到的账户事实，比如金额、天数、进度",
    "策略8 解决实施": "告知已经提交处理或正在按方案操作",
    "策略9 反馈请求": "请客户对本次服务做出评价",
    "策略10 关系延续": "告知后续有问题可以随时再联系",
    "策略11 感谢与告别": "感谢客户来电并祝生活愉快",
    "策略12 其它": "需要专人跟进，转交或转接",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="语义档对照实验与混淆分析")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--probe", required=True, help="raw/semantic_probe.jsonl")
    args = ap.parse_args()

    cases = [json.loads(l) for l in Path(args.golden).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [json.loads(l) for l in Path(args.probe).read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {r["case_id"]: r for r in rows}

    # ---------- 对照 1：策略级（12 类），去掉我的 key 设计 ----------
    sk = sorted(STRATEGY_INTENT)
    vec_s = embed([STRATEGY_INTENT[k] for k in sk])
    usable = [c for c in cases if c.get("source_strategy") in STRATEGY_INTENT]
    vec_u = embed([c["user_utterance"] for c in usable])
    top1 = top3 = 0
    for c, v in zip(usable, vec_u):
        ranked = sorted(((cos(v, vec_s[j]), sk[j]) for j in range(len(sk))), key=lambda x: -x[0])
        exp = c["source_strategy"]
        top1 += ranked[0][1] == exp
        top3 += any(k == exp for _, k in ranked[:3])
    n = len(usable)
    print(f"=== 对照1：策略级（{len(sk)} 类，语料自带标签，无我的 key 设计介入）===")
    print(f"  n={n}  top1 {top1/n*100:.1f}%  top3 {top3/n*100:.1f}%   随机基线 {1/len(sk)*100:.1f}%")

    # ---------- 对照 2：key 级混淆结构 ----------
    print("\n=== 对照2：key 级混淆（期望 → 实际 top1，出现≥15 次的前 14 个）===")
    conf = Counter()
    for c in cases:
        r = by_id.get(c["case_id"])
        if not r or not c["expect_key"]:
            continue
        pred = r["arm_A_topk"][0]["key"] if r["arm_A_topk"] else None
        if pred and pred != c["expect_key"]:
            conf[(c["expect_key"], pred)] += 1
    for (exp, pred), cnt in conf.most_common(14):
        print(f"  {cnt:5d}  期望 {exp:22s} → 预测 {pred}")

    # ---------- 对照 3：把「语义相邻的功能」算作对，还剩多少 ----------
    # 这些组合在业务上常可互换（同一轮说哪句都成立），单看 top1 会低估可用性
    ADJACENT = [
        {"offer_options", "execute_solution"},
        {"execute_solution", "inform_fact"},
        {"inform_fact", "offer_options"},
        {"empathy_hardship", "restate_issue"},
        {"restate_issue", "probe_detail"},
        {"closing_thanks", "relationship_extend"},
        {"relationship_extend", "ask_feedback"},
        {"hold_notice", "inform_fact"},
        {"off_script_handoff", "handoff_human"},
    ]
    adj_hit = 0
    denom = 0
    for c in cases:
        if not c["expect_key"]:
            continue
        r = by_id.get(c["case_id"])
        if not r or not r["arm_A_topk"]:
            continue
        denom += 1
        exp = c["expect_key"]
        pred = r["arm_A_topk"][0]["key"]
        if pred == exp or any({exp, pred} == a for a in ADJACENT):
            adj_hit += 1
    print(f"\n=== 对照3：把语义相邻功能视为可用 ===")
    print(f"  top1 宽松命中 {adj_hit}/{denom} = {adj_hit/denom*100:.1f}%（严格 top1 为 11.8%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
