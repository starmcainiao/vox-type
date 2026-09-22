#!/usr/bin/env python3
"""
mine_recompute.py — 修正版「从 CrossWOZ 会话记录挖候选话术」

存在理由：mine_candidates.py 的 state_signatures() 假设 dialog_act 是 5 元组
（注释写「第 3 位是槽名，第 4 位是值」），但实测 CrossWOZ 的 dialog_act 是 **4 元组**
`[类别, 域, 槽名, 槽值]`。原脚本按 `da[0]` 当 intent、`da[2]` 当域、`da[3]` 当值
与真实结构错位 —— 结果是 0 个状态签名、0 条候选，而 determinism_check 仍显示 True
（空列表也逐字一致）。这是一次**静默零结果失败**：不报错、退出码 0、自检通过，
只是什么都没挖到。

本文件按实测结构重算，判据与 mine_candidates.py 完全一致：
  · skeleton = 与 labs/corpus-harvest/cross_check_template_rate.py 同源的三条替换；
  · 候选 = 同一状态签名下同一骨架出现 ≥ MIN_GROUP_HITS 次；
  · 显著性下限 = 状态签名自身出现 ≥ MIN_SIGN_STATE_TURNS 轮；
  · 固定种子 SEED。

用法：python3 mine_recompute.py --out out/mine_recomputed.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

SEED = 20260922
MIN_GROUP_HITS = 3        # 同一状态签名下骨架出现 ≥3 次才算候选
MIN_SIGN_STATE_TURNS = 50  # 状态签名自身至少出现过 50 轮
MAX_EXAMPLES = 5


def skeleton(s: str) -> str:
    """去实体值，只留句式骨架。与 cross_check_template_rate.py 逐字一致。"""
    s = re.sub(r"\[[^\]]+\]", "□", s)
    s = re.sub(r"\d+(\.\d+)?", "#", s)
    s = re.sub(r"[A-Za-z]+", "@", s)
    return s.strip()


def state_signatures(msg: Dict[str, Any]) -> List[str]:
    """把一个系统话轮的 dialog_act 拆成状态签名列表。

    CrossWOZ 的 dialog_act 条目是 **4 元组** `['Inform', 域, 槽名, 值]`：
      · 第 0 位 字面量类别（"Inform" / "General" / "Request"）
      · 第 1 位 域（景点 / 餐馆 / 酒店 / 地铁 / 出租 …）
      · 第 2 位 槽名（名称 / 地址 / 评分 / 周边景点 …）
      · 第 3 位 槽值（"无" 或空串 = 系统没在说这个槽，不构成可复现状态）
    签名格式 `槽名/域=值`。
    """
    out: List[str] = []
    for da in msg.get("dialog_act") or []:
        if len(da) != 4:
            continue
        kind, domain, slot, value = da
        if kind != "Inform":
            continue
        v = (value or "").strip()
        if not v or v.lower() in ("none", "null", "-"):
            continue
        out.append(f"{slot}/{domain}={v}")
    return out


def load_turns() -> tuple[List[Dict[str, Any]], int]:
    turns: List[Dict[str, Any]] = []
    n_dialogues = 0
    root = Path(os.environ.get("CORPUS_ROOT", "~/corpus")).expanduser()
    for split in ("train", "val", "test"):
        p = root / "thu-coai__CrossWOZ/data/crosswoz" / f"{split}.json.zip"
        if not p.exists():
            raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
        with zipfile.ZipFile(p) as z:
            name = next(n for n in z.namelist() if n.endswith(".json"))
            doc = json.loads(z.read(name).decode("utf-8"))
        n_dialogues += len(doc)
        for cid, conv in doc.items():
            for i, msg in enumerate(conv["messages"]):
                if msg.get("role") != "sys":
                    continue
                text = (msg.get("content") or "").strip()
                if not text:
                    continue
                turns.append({
                    "cid": str(cid), "i": i, "text": text,
                    "skeleton": skeleton(text), "signatures": state_signatures(msg),
                })
    return turns, n_dialogues


def mine(turns: List[Dict[str, Any]], seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    sig_turns: Counter = Counter()
    sig_skel: Dict[str, Counter] = defaultdict(Counter)
    sig_texts: Dict[Any, List[str]] = defaultdict(list)
    for t in turns:
        for s in t["signatures"]:
            sig_turns[s] += 1
            sig_skel[s][t["skeleton"]] += 1
            key = (s, t["skeleton"])
            if len(sig_texts[key]) < MAX_EXAMPLES:
                sig_texts[key].append(t["text"])

    cands: List[Dict[str, Any]] = []
    for s, cnt in sig_skel.items():
        if sig_turns[s] < MIN_SIGN_STATE_TURNS:
            continue
        for sk, c in cnt.items():
            if c < MIN_GROUP_HITS:
                continue
            ex = sig_texts[(s, sk)]
            examples = (list(rng.sample(ex * MAX_EXAMPLES,
                                        k=min(MAX_EXAMPLES, len(set(ex)))))
                        if ex else [])
            cands.append({
                "state_signature": s, "skeleton": sk, "group_hits": c,
                "sign_total_turns": sig_turns[s], "examples": examples,
            })
    cands.sort(key=lambda x: (-x["group_hits"], -x["sign_total_turns"], x["state_signature"]))
    for i, c in enumerate(cands):
        c["id"] = f"MC-{i:05d}"
    return cands


def main() -> int:
    ap = argparse.ArgumentParser(description="CrossWOZ 系统侧候选话术挖掘（修正版）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    turns, n_dialogues = load_turns()
    c1 = mine(turns, args.seed)
    c2 = mine(turns, args.seed)

    n_sig = sum(1 for t in turns if t["signatures"])
    n_all_sigs = len({s for t in turns for s in t["signatures"]})
    covered = set()
    for c in c1:
        for t in turns:
            if c["state_signature"] in t["signatures"] and t["skeleton"] == c["skeleton"]:
                covered.add(f'{t["cid"]}:{t["i"]}')

    res = {
        "seed": args.seed,
        "n_dialogues": n_dialogues,
        "n_sys_turns": len(turns),
        "n_sys_turns_with_state_signature": n_sig,
        "n_sys_turns_without_state_signature": len(turns) - n_sig,
        "min_sign_state_turns": MIN_SIGN_STATE_TURNS,
        "min_group_hits": MIN_GROUP_HITS,
        "n_state_signatures": n_all_sigs,
        "n_state_signatures_with_candidate": len({c["state_signature"] for c in c1}),
        "n_candidates": len(c1),
        "n_sys_turns_covered_by_a_candidate": len(covered),
        "coverage_of_all_sys_turns": round(len(covered) / len(turns), 4),
        "coverage_of_stateful_sys_turns": round(len(covered) / n_sig, 4),
        "candidates_by_group_hits": {
            ">=50": sum(1 for c in c1 if c["group_hits"] >= 50),
            ">=20": sum(1 for c in c1 if c["group_hits"] >= 20),
            ">=10": sum(1 for c in c1 if c["group_hits"] >= 10),
            f">={MIN_GROUP_HITS}": len(c1),
        },
        "top_states_by_candidate_count": dict(
            Counter(c["state_signature"] for c in c1).most_common(10)),
        "determinism_same_seed_twice_identical_examples":
            [c["examples"] for c in c1[:8]] == [c["examples"] for c in c2[:8]],
        "sample_candidates": c1[:8],
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    for k, v in res.items():
        if k != "sample_candidates":
            print(f"{k}: {v}")
    print("\n样例（前 8 条）：")
    for c in c1[:8]:
        print(f"  {c['id']}  状态={c['state_signature']}  组内 {c['group_hits']} 次")
        print(f"      骨架: {c['skeleton']}")
        for e in c["examples"][:2]:
            print(f"        - {e[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
