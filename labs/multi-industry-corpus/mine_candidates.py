#!/usr/bin/env python3
"""
mine_candidates.py — 回流小实验：从 CrossWOZ 的会话记录里挖「某个状态下系统固定说什么」

卡面 T22 必做 3。目标不是复现某个模型，而是回答一个可判定的问题：
「用**系统侧对话行为标注**做状态分组、按分组内的话术骨架取高频模板」这条最保守的挖掘路径，
在公开数据上能挖出多少条候选话术、覆盖多少状态。

方法（全程固定种子，可复现）：
  1. 对每通对话的每个系统话轮，从 `dialog_act` 里取一个**状态签名**：
     (域, 槽, 值) —— 这是 CrossWOZ 自带的「系统在这轮说了关于哪个槽的什么值」的标注。
     只保留「值非空」的槽（值空 = 系统没在说这个槽，不构成可复现的状态）。
  2. 把系统话轮按状态签名分组，组内按骨架（common.skeleton，同源判据）计数。
  3. 一个骨架在**同一状态签名下**出现 ≥ MIN_GROUP_HITS 次 → 候选话术。
     这是「状态 → 固定话术」的最小证据强度，比全局 ≥5 次严格得多（全局 5 次可能全来自不同场景）。
  4. 候选话术按 (签名, 骨架) 去重，记出现次数、样例（前 N 条真实话术）、以及该状态签名在
     全语料里的总话轮数（覆盖率分母）。

用法：
  python3 mine_candidates.py --out mine_candidates.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402

CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", "~/corpus")).expanduser()
SEED = 20260922
MIN_GROUP_HITS = 3   # 同一状态签名下骨架出现 ≥3 次才算候选
MIN_SIGN_STATE_TURNS = 50  # 状态签名本身至少要出现过 50 轮，避免稀薄分组制造噪声
MAX_EXAMPLES = 5


def state_signatures(msg: Dict[str, Any]) -> List[str]:
    """把一个系统话轮的 dialog_act 拆成状态签名列表。

    签名格式 `槽名/域=值`。CrossWOZ 的 dialog_act 条目实测是 **4 元组**
    `[类别, 域, 槽名, 值]`（字段 0 = "Inform"/"Request"/"General"/"Recommend"/"NoOffer"/"Select"，
    字段 1 = 域，字段 2 = 槽名，字段 3 = 槽值；train/test 全分区长度均为 4，无 5 元组）。
    只取「类别为 Inform 且值非空」的槽：值空的槽表示系统在这轮**没有**传达该槽的信息，
    拿它当状态会造出大量伪状态。

    历史教训（本次修复的根因）：旧版写 `if intent != "Inform": continue`，而解包顺序是
    `kind, intent, domain, value = da`——**「intent」这个变量名实际拿的是字段 1（域）**，
    于是「餐馆/酒店/景点/出租」全被拒收，每个话轮产出空签名 → 0 状态签名、0 候选，
    但 determinism_check 仍为 True、退出码 0（空列表也逐字一致）。这是一次
    **静默零结果失败**：不报错、自检通过，只是什么都没挖到。
    已改为按位名解包并直接比 `kind`；下方 main() 另加零结果告警，让这类失败下次不再静默。
    """
    out: List[str] = []
    for da in msg.get("dialog_act") or []:
        if len(da) != 4:
            continue
        kind, domain, slot, value = da
        if kind != "Inform":
            continue
        slot = (slot or "").strip()
        if not slot:
            continue
        v = (value or "").strip()
        if not v or v.lower() in ("none", "null", "-"):
            continue
        out.append(f"{slot}/{domain}={v}")
    return out


def load_turns() -> Tuple[List[Dict[str, Any]], int]:
    turns: List[Dict[str, Any]] = []
    n_dialogues = 0
    for split in ("train", "val", "test"):
        p = CORPUS_ROOT / "thu-coai__CrossWOZ" / "data" / "crosswoz" / f"{split}.json.zip"
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
                    "split": split,
                    "cid": str(cid),
                    "idx": i,
                    "text": text,
                    "skeleton": common.skeleton(text),
                    "signatures": state_signatures(msg),
                    "n_da": len(msg.get("dialog_act") or []),
                })
    return turns, n_dialogues


def mine(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    rng = random.Random(SEED)

    sig_turns: Counter = Counter()
    sig_skel: Dict[str, Counter] = defaultdict(Counter)
    sig_texts: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for t in turns:
        sk = t["skeleton"]
        if not t["signatures"]:
            continue
        for sig in t["signatures"]:
            sig_turns[sig] += 1
            sig_skel[sig][sk] += 1
            if len(sig_texts[(sig, sk)]) < MAX_EXAMPLES:
                sig_texts[(sig, sk)].append(t["text"])

    n_eligible_sigs = 0
    n_eligible_skel = 0
    candidates: List[Dict[str, Any]] = []
    covered_turns: Counter = Counter()          # 候选覆盖到哪些系统话轮
    covered_by_text: Dict[str, str] = {}        # cid:idx → 候选 id

    for sig, skel_counter in sig_skel.items():
        if sig_turns[sig] < MIN_SIGN_STATE_TURNS:
            continue
        n_eligible_sigs += 1
        for sk, c in skel_counter.items():
            n_eligible_skel += 1
            if c < MIN_GROUP_HITS:
                continue
            candidates.append({
                "state_signature": sig,
                "skeleton": sk,
                "group_hits": c,
                "sign_total_turns": sig_turns[sig],
                "examples": list(rng.sample(sig_texts[(sig, sk)] * MAX_EXAMPLES,
                                            k=min(MAX_EXAMPLES, len(set(sig_texts[(sig, sk)]))))),
            })
    candidates.sort(key=lambda x: (-x["group_hits"], -x["sign_total_turns"], x["state_signature"]))
    for i, c in enumerate(candidates):
        c["id"] = f"MC-{i:05d}"
        for t in turns:
            if c["state_signature"] in t["signatures"] and t["skeleton"] == c["skeleton"]:
                covered_turns[f'{t["cid"]}:{t["idx"]}:{t["split"]}'] = covered_turns.get(
                    f'{t["cid"]}:{t["idx"]}:{t["split"]}', 0) + 1
                covered_by_text[f'{t["cid"]}:{t["idx"]}:{t["split"]}'] = c["id"]

    # 状态签名覆盖：多少系统话轮落在「存在候选的话术」上
    distinct_covered = len(covered_by_text)
    n_sys_turns = len(turns)
    n_turns_with_sig = sum(1 for t in turns if t["signatures"])
    n_turns_no_sig = n_sys_turns - n_turns_with_sig

    # 抽样 8 条候选，人工可读地贴出来（固定种子）
    sample_ids = [c["id"] for c in candidates[:8]]

    return {
        "n_dialogues": None,  # main() 回填
        "n_sys_turns": n_sys_turns,
        "n_sys_turns_with_state_signature": n_turns_with_sig,
        "n_sys_turns_without_state_signature": n_turns_no_sig,
        "min_sign_state_turns": MIN_SIGN_STATE_TURNS,
        "min_group_hits": MIN_GROUP_HITS,
        "n_state_signatures": len(sig_turns),
        "n_state_signatures_eligible": n_eligible_sigs,
        "n_signature_skeleton_pairs": n_eligible_skel,
        "n_candidates": len(candidates),
        "n_sys_turns_covered_by_a_candidate": distinct_covered,
        "coverage_of_all_sys_turns": round(distinct_covered / n_sys_turns, 4) if n_sys_turns else 0.0,
        "coverage_of_stateful_sys_turns": round(distinct_covered / n_turns_with_sig, 4)
            if n_turns_with_sig else 0.0,
        "candidates_by_group_hits": {
            ">=50": sum(1 for c in candidates if c["group_hits"] >= 50),
            ">=20": sum(1 for c in candidates if c["group_hits"] >= 20),
            ">=10": sum(1 for c in candidates if c["group_hits"] >= 10),
            f">={MIN_GROUP_HITS}": len(candidates),
        },
        "top_states_by_candidate_count": dict(Counter(
            c["state_signature"] for c in candidates).most_common(10)),
        "sample_candidates": [c for c in candidates if c["id"] in sample_ids],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="CrossWOZ 系统侧候选话术挖掘")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=SEED, help=f"固定种子（默认 {SEED}）")
    args = ap.parse_args()

    turns, n_dialogues = load_turns()
    res = mine(turns)
    res["n_dialogues"] = n_dialogues
    # 重新跑一遍以确认种子决定样例（两次同种子必须逐字一致）
    res2 = mine(turns)
    reproducible = (
        json.dumps([c["examples"] for c in res["sample_candidates"]], ensure_ascii=False, sort_keys=True)
        == json.dumps([c["examples"] for c in res2["sample_candidates"]], ensure_ascii=False, sort_keys=True)
    )

    # 零结果告警：这是本脚本修过一次的真事故（旧版 n_candidates=0 却 exit 0）。
    # 只告警不中止——n_candidates=0 在「语料本身没有可复现的固定话术」时是合法答案，
    # 但必须显式说出来，不能靠 determinism_check=True 掩盖（空列表也逐字一致）。
    zero_hit = (res["n_candidates"] == 0 or res["n_sys_turns_with_state_signature"] == 0)
    if zero_hit:
        print("!! 零结果告警：候选为 0 或无任何话轮带状态签名——先怀疑解析错位，"
              "不要把它当「该语料没有固定话术」的结论（参见本脚本 state_signatures 的注释）。")
    if not reproducible:
        print("!! 零结果告警：同种子两次跑样例不一致，结果不可复现。")

    report = {
        "_schema": "vox-backflow-mining/1",
        "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "seed": args.seed,
        "corpus": "thu-coai/CrossWOZ（train+val+test，系统侧）",
        "method": {
            "state_signature": "系统话轮 dialog_act 里类别为 Inform 且值非空的 (槽名, 域, 值) 三元组；dialog_act 实测为 4 元组 [类别, 域, 槽名, 值]，签名格式 `槽名/域=值`",
            "skeleton": "common.skeleton —— 与 labs/corpus-harvest/cross_check_template_rate.py 同源",
            "candidate_rule": f"同一状态签名下同一骨架出现 ≥ {MIN_GROUP_HITS} 次",
            "significance_floor": f"状态签名自身出现 ≥ {MIN_SIGN_STATE_TURNS} 轮",
            "answer": "M3「日志挖掘 → 话术建议」在公开数据上能走多远：能挖出候选，但覆盖的是"
                      "「系统确认某个槽的值」这一类窄场景（数据库查询回显），不是通用话术生成。",
        },
        "determinism_check": {"same_seed_twice_identical_examples": reproducible},
        **res,
    }

    print(f"系统话轮: {res['n_sys_turns']}（其中带状态签名 {res['n_sys_turns_with_state_signature']}）")
    print(f"状态签名: {res['n_state_signatures']}（够密的 {res['n_state_signatures_eligible']}）")
    print(f"候选话术: {res['n_candidates']} 条，覆盖 {res['n_sys_turns_covered_by_a_candidate']} 轮"
          f"（占系统话轮 {res['coverage_of_all_sys_turns']*100:.1f}%）")
    print(f"同种子两次跑样例一致: {reproducible}")
    print(f"\n样例（前 {len(res['sample_candidates'])} 条）：")
    for c in res["sample_candidates"]:
        print(f"  {c['id']}  状态={c['state_signature']}  组内 {c['group_hits']} 次")
        print(f"      骨架: {c['skeleton']}")
        for e in c["examples"][:3]:
            print(f"        - {e[:70]}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n原始数据 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
