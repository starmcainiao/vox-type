#!/usr/bin/env python3
"""
semantic_probe.py — 语义档测量（docs/11 §11.5 的「实验②」，T15 的核心数字）

职责：量「用户的自由说法 → 语义检索到正确的 key」的 top-1 准确率与 top-k 覆盖率，
      产出原始 JSONL 证据。**只提案，不出声**——不接 runtime、不改内核（docs/11 裁定 1）。
不负责：不判定安全阈值（那是 T13 准入判据的一部分，需业务决策）、不改产品代码。

**为什么要测两条路线**（这是本脚本的设计要点）：
用户说的话讨论的是**内容**（"工资延迟了周转不开"），而系统该说的那句话承载的是**功能**
（"共情安抚"）。两者是间接关系，所以：

  路线 A：用户话术 → 最近的**预铸 variant 原文** → 该 variant 的 key
          · 优点：匹配对象就是系统真能播的东西
          · 疑点：内容词与功能词的语义距离可能很远

  路线 B：用户话术 → 最近的**key 功能描述** → 该 key
          · 优点：功能描述与用户处境语义更近
          · 疑点：功能描述是我们写的，不在链路上（属于"我们替上游想"，不是系统能自己验的）

两条都报，让数据说话。

嵌入模型：oMLX /v1/embeddings 的 bge-m3-mlx-fp16（1024 维，本机已常驻，零下载）。

用法：
  python3 semantic_probe.py --golden <jsonl> --pack <packs/fin-cs> --out-jsonl raw/semantic_probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OMLX = "http://127.0.0.1:10099"
EMBED_MODEL = "bge-m3-mlx-fp16"
BATCH = 32
TOP_K = 5

# key → 功能描述（业务判断，不是语料自带）。路线 B 的匹配对象。
KEY_INTENT: Dict[str, str] = {
    "greeting_inbound": "开场问候，欢迎客户来电，询问需要什么帮助",
    "verify_identity": "核验客户身份，说明为了账户安全需要核对信息",
    "restate_issue": "重述或转述客户刚说的问题，确认自己理解得对不对",
    "probe_detail": "追问细节，请客户补充事情发生的时间或次数等具体情况",
    "empathy_hardship": "共情安抚，为客户遇到困难表示抱歉和理解",
    "inform_fact": "说明查到的账户事实，比如金额、天数、处理进度",
    "offer_options": "提供建议，给出几个可选的解决方案让客户挑",
    "execute_solution": "实施解决，告知已经提交处理或正在按方案操作",
    "ask_feedback": "请求反馈，请客户对本次服务做出评价",
    "relationship_extend": "关系延续，告知后续有问题可以随时再联系",
    "closing_thanks": "感谢与告别，感谢客户来电并祝生活愉快",
    "off_script_handoff": "转交专人，告知这个情况需要专人跟进处理",
    "handoff_human": "转接人工客服",
    "error_retry": "没听清客户的话，请客户再说一次",
    "asr_clarify": "信号不好听不清，请客户重说一遍",
    "hold_notice": "请客户稍等，正在为客户查询核实",
    "compliance_notice": "合规提示，告知本次通话可能会被录音",
}


def embed(texts: List[str], *, base: str = OMLX, model: str = EMBED_MODEL) -> List[List[float]]:
    """调 oMLX 的 OpenAI 兼容嵌入接口。分批、有限重试、失败抛错（不静默降级为空向量）。"""
    out: List[List[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i : i + BATCH]
        body = json.dumps({"model": model, "input": chunk}).encode("utf-8")
        last: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(
                    f"{base}/v1/embeddings", data=body, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    doc = json.loads(r.read().decode("utf-8"))
                vecs = [d["embedding"] for d in doc["data"]]
                if len(vecs) != len(chunk):
                    raise ValueError(f"返回条数不符：要 {len(chunk)} 得 {len(vecs)}")
                out.extend(vecs)
                break
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as e:
                last = e
                if attempt < 3:
                    time.sleep(2 * attempt)
        else:
            raise RuntimeError(f"嵌入失败（已重试 3 次）: {last!r}")
    return out


def cos(a: List[float], b: List[float]) -> float:
    """余弦相似度。bge-m3 输出已归一化时点积即余弦，但仍显式归一以不依赖该假设。"""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def main() -> int:
    ap = argparse.ArgumentParser(description="语义档测量（只提案不出声）")
    ap.add_argument("--golden", required=True, help="golden JSONL")
    ap.add_argument("--pack", required=True, help="业务包源目录")
    ap.add_argument("--out-jsonl", required=True, help="逐条提案原始数据输出")
    args = ap.parse_args()

    pack_dir = Path(args.pack)
    phrases = json.loads((pack_dir / "phrases.json").read_text(encoding="utf-8"))["phrases"]
    cases = [json.loads(l) for l in Path(args.golden).read_text(encoding="utf-8").splitlines() if l.strip()]

    # ---- 路线 A 的索引：预铸 variant 原文 → key ----
    proto_texts: List[str] = []
    proto_keys: List[str] = []
    for p in phrases:
        for v in p["variants"]:
            proto_texts.append(v)
            proto_keys.append(p["key"])

    # ---- 路线 B 的索引：key 功能描述 → key ----
    intent_keys = [k for k in KEY_INTENT if any(p["key"] == k for p in phrases)]
    intent_texts = [KEY_INTENT[k] for k in intent_keys]

    print(f"golden {len(cases)} 条；路线A 原型 {len(proto_texts)} 条；路线B 原型 {len(intent_keys)} 条")
    t0 = time.perf_counter()
    vec_proto = embed(proto_texts)
    vec_intent = embed(intent_texts)
    print(f"原型嵌入完成（{time.perf_counter() - t0:.1f}s），开始嵌入 {len(cases)} 条用户话术…")
    t1 = time.perf_counter()
    vec_cases = embed([c["user_utterance"] for c in cases])
    print(f"用例嵌入完成（{time.perf_counter() - t1:.1f}s）")

    out_path = Path(args.out_jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 只对有 expect_key 的用例算准确率；unspecified 单独统计（它们是「不该命中任何 key」的那档）
    evaluated = [i for i, c in enumerate(cases) if c["expect_key"]]
    acc = {"A_top1": 0, "A_top3": 0, "B_top1": 0, "B_top3": 0}
    rows: List[Dict[str, Any]] = []

    with out_path.open("w", encoding="utf-8") as f:
        for i, c in enumerate(cases):
            v = vec_cases[i]
            ra = sorted(
                ((cos(v, vec_proto[j]), proto_keys[j], proto_texts[j]) for j in range(len(proto_texts))),
                key=lambda x: -x[0],
            )[:TOP_K]
            # 路线 A 的 top-k 里按 key 去重（同一 key 的多个 variant 不应重复占位）
            ra_dedup: List[Tuple[float, str]] = []
            for s, k, _ in ra:
                if all(k != kk for _, kk in ra_dedup):
                    ra_dedup.append((s, k))
            rb = sorted(
                ((cos(v, vec_intent[j]), intent_keys[j]) for j in range(len(intent_keys))),
                key=lambda x: -x[0],
            )[:TOP_K]

            exp = c["expect_key"]
            rec = {
                "case_id": c["case_id"],
                "user_utterance": c["user_utterance"],
                "expect_key": exp,
                "expect_kind": c["expect_kind"],
                "arm_A_top1": [{"key": ra_dedup[0][1], "score": round(ra_dedup[0][0], 4)}] if ra_dedup else [],
                "arm_A_topk": [{"key": k, "score": round(s, 4)} for s, k in ra_dedup],
                "arm_B_top1": {"key": rb[0][1], "score": round(rb[0][0], 4)},
                "arm_B_topk": [{"key": k, "score": round(s, 4)} for s, k in rb],
                "hit_A_top1": bool(ra_dedup) and ra_dedup[0][1] == exp,
                "hit_A_top3": any(k == exp for _, k in ra_dedup[:3]),
                "hit_B_top1": rb[0][1] == exp,
                "hit_B_top3": any(k == exp for _, k in rb[:3]),
            }
            if exp:
                acc["A_top1"] += rec["hit_A_top1"]
                acc["A_top3"] += rec["hit_A_top3"]
                acc["B_top1"] += rec["hit_B_top1"]
                acc["B_top3"] += rec["hit_B_top3"]
            rows.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n = len(evaluated)
    summary = {
        "_schema": "vox-semantic-probe/1",
        "golden": args.golden,
        "pack": args.pack,
        "embed_model": EMBED_MODEL,
        "n_cases": len(cases),
        "n_evaluated": n,
        "n_unspecified": len(cases) - n,
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "arms": {
            "A_prototype_prebaked_variant": "匹配对象 = 预铸 variant 原文",
            "B_prototype_key_intent": "匹配对象 = key 功能描述（我们写的，不在链路上）",
        },
        "accuracy": {k: {"hits": v, "rate": v / n} for k, v in acc.items()},
    }
    (out_path.parent / "semantic_probe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\n评估 {n} 条（另有 {len(cases) - n} 条 unspecified 不计入）")
    print(f"  路线A（匹配预铸 variant 原文）: top1 {acc['A_top1']/n*100:5.1f}%  top3 {acc['A_top3']/n*100:5.1f}%")
    print(f"  路线B（匹配 key 功能描述）    : top1 {acc['B_top1']/n*100:5.1f}%  top3 {acc['B_top3']/n*100:5.1f}%")
    print(f"逐条原始数据 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
