#!/usr/bin/env python3
"""
baseline_literal.py — 逐字档基线测量（docs/11 §11.5 的「实验①」）

职责：在 golden set 上量「助手自由文本 → 逐字命中预铸 variant」的命中率，产出原始 JSON 证据。
不负责：不做语义检索（那是 T15 的语义档）、不改产品代码。

**复用产品代码的归一化**（`adapters.framework_kefu.normalize.normalize_text`），
不另写一份——否则测的是自己的实现而不是产品（"反空转"纪律，接手指南 §五.2）。

口径：
  - 命中 = `normalize_text(assistant_reply_free)` 归一化后与某个预铸 variant **逐字相等**
    （docs/10 §10.3 的四步归一化，一步不多）
  - 不判定大小写/空白以外的任何松弛；不许语义近似
  - 同时报「若误用用户话术当输入」的对照值——证明那是范畴错误而非命中率问题

用法：
  python3 baseline_literal.py --pack <仓库根>/packs/fin-cs \
                              --out raw/literal_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path("<仓库根>")
sys.path.insert(0, str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser(description="逐字档基线测量")
    ap.add_argument("--pack", required=True, help="业务包源目录（packs/<业务>/）")
    ap.add_argument("--golden", default=None, help="golden JSONL（缺省用包内 golden/corpus_derived.jsonl）")
    ap.add_argument("--out", required=True, help="原始证据输出 JSON")
    args = ap.parse_args()

    # 产品代码，不重写
    from adapters.framework_kefu.normalize import normalize_text

    pack_dir = Path(args.pack)
    golden_path = Path(args.golden) if args.golden else pack_dir / "golden" / "corpus_derived.jsonl"
    if not golden_path.exists():
        print(f"golden 不存在: {golden_path}", file=sys.stderr)
        return 2

    phrases = json.loads((pack_dir / "phrases.json").read_text(encoding="utf-8"))["phrases"]
    index: Dict[str, str] = {}
    for p in phrases:
        for v in p["variants"]:
            index.setdefault(normalize_text(v), p["key"])

    cases: List[Dict[str, Any]] = [
        json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not cases:
        print(f"golden 为空: {golden_path}", file=sys.stderr)
        return 3

    rows: List[Dict[str, Any]] = []
    hit_assistant = 0
    hit_user = 0
    for c in cases:
        a = normalize_text(c["assistant_reply_free"])
        u = normalize_text(c["user_utterance"])
        ha = index.get(a)
        hu = index.get(u)
        hit_assistant += ha is not None
        hit_user += hu is not None
        rows.append(
            {
                "case_id": c["case_id"],
                "expect_key": c["expect_key"],
                "hit_assistant_free": ha is not None,
                "hit_assistant_key": ha,
                "hit_user_utterance": hu is not None,
            }
        )

    n = len(cases)
    result = {
        "_schema": "vox-literal-baseline/1",
        "pack": str(pack_dir),
        "golden": str(golden_path),
        "n_cases": n,
        "n_prebaked_variants": len(index),
        "run_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "caliber": {
            "hit_rule": "normalize_text(x) 与某个预铸 variant 归一化后逐字相等（docs/10 §10.3 四步归一化）",
            "arm_1_assistant_free_text": "输入 = 助手自由文本 assistant_reply_free（真实链路里 LLM 生成的那句）",
            "arm_2_user_utterance": "输入 = 用户话术 user_utterance（对照：证明拿它比客服 variant 是范畴错误）",
        },
        "hit_rate_assistant_free_text": hit_assistant / n,
        "hit_rate_user_utterance": hit_user / n,
        "hits_assistant_free_text": hit_assistant,
        "hits_user_utterance": hit_user,
        "cases": rows,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"n={n}  预铸 variant 归一化后 {len(index)} 条")
    print(f"实验① 逐字档（输入=助手自由文本）: {hit_assistant}/{n} = {hit_assistant / n * 100:.2f}%")
    print(f"  对照（输入=用户话术）          : {hit_user}/{n} = {hit_user / n * 100:.2f}%")
    print(f"原始数据 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
