#!/usr/bin/env python3
"""
cross_check_template_rate.py — 跨语料交叉验证「句式可复用率」

职责：在**多份不同来源的语料**上量同一件事——客服应答的句式可复用率，
      用来判断「预铸覆盖率低」是某个语料的属性，还是这个业务的属性。
不负责：不做预铸判定、不改产品代码。

口径（与 labs/README §六一致）：
  - 骨架 = 去实体值（方括号占位符、数字、英文）后的文本；
  - 可复用 = 骨架在**同一份语料内**出现 ≥5 次；
  - 骨架压缩比 = 话轮数 / 骨架种类数（=1.00 表示零模板化）。

用法：
  python3 cross_check_template_rate.py --out raw/cross_check.json
"""

from __future__ import annotations

import argparse
import glob
import os
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", "~/corpus")).expanduser()
REPEAT_MIN = 5


def skeleton(s: str) -> str:
    """去实体值，只留句式骨架。用于区分「措辞不同」与「只是值不同」。"""
    s = re.sub(r"\[[^\]]+\]", "□", s)
    s = re.sub(r"\d+(\.\d+)?", "#", s)
    s = re.sub(r"[A-Za-z]+", "@", s)
    return s.strip()


def stats(texts: List[str]) -> Dict[str, Any]:
    """对一组文本算骨架复用统计。"""
    sk = Counter(skeleton(t) for t in texts)
    n = len(texts)
    reusable_turns = sum(v for v in sk.values() if v >= REPEAT_MIN)
    return {
        "n_turns": n,
        "n_skeletons": len(sk),
        "compress_ratio": round(n / len(sk), 3) if sk else None,
        "reusable_turns": reusable_turns,
        "reusable_rate": reusable_turns / n if n else None,
        "top_skeletons": [{"count": c, "skeleton": s} for s, c in sk.most_common(8)],
    }


def load_dianjin() -> List[str]:
    """DianJin-CSC 真实客服话轮。"""
    p = CORPUS_ROOT / "tongyi_dianjin__DianJin-CSC-Data" / "data" / "CSConv.json"
    if not p.exists():
        raise FileNotFoundError(f"语料缺失（先跑 fetch.py）: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    return [t["text"] for x in doc for t in x["dialogue"] if t.get("speaker") == "客服"]


def load_ecom() -> List[str]:
    """QingshanAI 电商合成语料的客服应答。"""
    out: List[str] = []
    for p in sorted(glob.glob(str(CORPUS_ROOT / "QingshanAI__*" / "data.jsonl"))):
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("bot"):
                    out.append(r["bot"])
    if not out:
        raise FileNotFoundError("电商语料为空（先跑 fetch.py）")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="跨语料交叉验证句式可复用率")
    ap.add_argument("--out", required=True, help="原始证据输出 JSON")
    args = ap.parse_args()

    corpora: List[Tuple[str, str, List[str]]] = [
        (
            "tongyi_dianjin/DianJin-CSC-Data",
            "真实（1855 通催收/账户服务电话）",
            load_dianjin(),
        ),
        (
            "QingshanAI/ecom-*-synthetic（3 份合并）",
            "合成（LLM 生成的电商客服，2976 对 user/bot）",
            load_ecom(),
        ),
    ]

    result: Dict[str, Any] = {
        "_schema": "vox-template-rate-crosscheck/1",
        "repeat_min": REPEAT_MIN,
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "caliber": "骨架 = 去方括号占位符/数字/英文后的文本；可复用 = 同语料内骨架出现≥5次",
        "corpora": {},
    }

    print(f"{'语料':46s} {'话轮':>7s} {'骨架':>7s} {'压缩比':>7s} {'可复用率':>9s}")
    for cid, desc, texts in corpora:
        s = stats(texts)
        result["corpora"][cid] = {"desc": desc, **s}
        print(
            f"{cid:46s} {s['n_turns']:>7d} {s['n_skeletons']:>7d} "
            f"{s['compress_ratio']:>7.2f} {s['reusable_rate'] * 100:>8.1f}%"
        )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n原始数据 → {args.out}")
    print(
        "\n注意：合成语料由 LLM 生成，其措辞多样性是「造」出来的，因此它**不能**用来证明\n"
        "      「真实业务里可预铸率更低」——它只说明这份数据不构成反例的反面。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
