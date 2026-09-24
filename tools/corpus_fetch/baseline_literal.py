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

**由 labs/corpus-harvest/baseline_literal.py 提升（T12）时去掉的硬编码**：
  `REPO` → `--repo-root` 参数，缺省从 `--pack` 向上推仓库根（packs/<业务>/ → 仓库根）。
  仓库根唯一用途是把 `adapters.*` 加入 `sys.path`（复用产品归一化），不参与任何判定。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

NORMALIZE_MODULE = "adapters.framework_kefu.normalize"
NORMALIZE_FUNC = "normalize_text"


class BaselineError(Exception):
    """基线测量的致命错误——一律向上抛，不吞、不降级。"""


def repo_root_from_pack(pack_dir: Path) -> Path:
    """从 `--pack` 路径向上推仓库根：`<repo>/packs/<业务>/` → `<repo>`。

    推不出（找不到祖先目录里的 `packs/`）就报错——猜一个仓库根可能让 `importlib`
    从别处拉起一套产品代码，测出来的数字无法复核。
    """
    pack = Path(pack_dir).resolve()
    for parent in [pack] + list(pack.parents):
        if parent.name == "packs" and (parent / pack.name).exists():
            return parent.parent
    raise BaselineError(
        f"无法从 --pack 推出仓库根: {pack}（期望形如 <repo>/packs/<业务>/；"
        f"请在该目录的祖先里找到 packs/，或用 --repo-root 显式指定）"
    )


def load_normalize_text(repo_root: Path):
    """加载产品自己的归一化函数（不重写）。"""
    root = Path(repo_root).resolve()
    if not root.exists():
        raise BaselineError(f"仓库根不存在: {root}")
    sys.path.insert(0, str(root))
    try:
        mod = importlib.import_module(NORMALIZE_MODULE)
    except ModuleNotFoundError as e:
        raise BaselineError(
            f"无法 import 产品归一化模块 {NORMALIZE_MODULE}（repo_root={root}）:: {e}"
        ) from e
    fn = getattr(mod, NORMALIZE_FUNC, None)
    if not callable(fn):
        raise BaselineError(f"{NORMALIZE_MODULE} 里没有可调用的 {NORMALIZE_FUNC}")
    return fn


def measure(pack_dir: Path, golden_path: Path, repo_root: Path) -> Dict[str, Any]:
    """跑一遍逐字档测量，返回原始证据（未写盘）。"""
    normalize_text = load_normalize_text(repo_root)

    pack_dir = Path(pack_dir)
    golden_path = Path(golden_path)
    if not pack_dir.exists():
        raise BaselineError(f"业务包目录不存在: {pack_dir}")
    if not golden_path.exists():
        raise BaselineError(f"golden 不存在: {golden_path}")

    phrases_path = pack_dir / "phrases.json"
    if not phrases_path.exists():
        raise BaselineError(f"业务包缺少 phrases.json: {phrases_path}")
    phrases = json.loads(phrases_path.read_text(encoding="utf-8"))["phrases"]
    index: Dict[str, Tuple[str, str]] = {}   # 归一化 key → (所属 key, 归一化前的原文)
    for p in phrases:
        for v in p["variants"]:
            norm = normalize_text(v)
            if norm in index:
                # 碰撞即抛错（T12c 修 2）：归一化后相同的两条 variant 落到同一个 index key 上，
                # 旧实现用 `setdefault` 让后写的那条**静默丢失**、命中归到先写 variant 的 key。
                # 那等于把「两条不同的预铸话术」当成一条来算命中归属，基线数字口径失真，
                # 而且事后无法定位是哪两条撞的。宁可硬失败让人去改 phrases.json。
                prev_key, prev_raw = index[norm]
                raise BaselineError(
                    f"variant 归一化后碰撞，无法确定命中归属: {norm!r}"
                    f"（先出现 {prev_key!r} → {prev_raw!r}；本次 {p['key']!r} → {v!r}）"
                    f"——归一化是唯一的松弛步骤，两条不同 variant 归一后相同说明包内有重复话术，"
                    f"请去重后重跑（不允许静默取其一）"
                )
            index[norm] = (p["key"], v)

    cases: List[Dict[str, Any]] = [
        json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not cases:
        raise BaselineError(f"golden 为空: {golden_path}")
    for i, c in enumerate(cases):
        for field in ("case_id", "assistant_reply_free", "user_utterance", "expect_key"):
            if field not in c:
                raise BaselineError(f"golden 第 {i + 1} 行缺少字段 {field}: {c.get('case_id')!r}")

    rows: List[Dict[str, Any]] = []
    hit_assistant = 0
    hit_user = 0
    for c in cases:
        a = normalize_text(c["assistant_reply_free"])
        u = normalize_text(c["user_utterance"])
        ha = index.get(a, (None, None))[0]
        hu = index.get(u, (None, None))[0]
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
    return {
        "_schema": "vox-literal-baseline/1",
        "pack": str(pack_dir),
        "golden": str(golden_path),
        "n_cases": n,
        "n_prebaked_variants": len(index),
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="逐字档基线测量")
    ap.add_argument("--pack", required=True, help="业务包源目录（packs/<业务>/）")
    ap.add_argument("--golden", default=None, help="golden JSONL（缺省用包内 golden/corpus_derived.jsonl）")
    ap.add_argument("--out", required=True, help="原始证据输出 JSON")
    ap.add_argument(
        "--repo-root",
        default=None,
        help="仓库根，用于 import 产品归一化（缺省从 --pack 向上推）",
    )
    args = ap.parse_args(argv)

    pack_dir = Path(args.pack)
    golden_path = Path(args.golden) if args.golden else pack_dir / "golden" / "corpus_derived.jsonl"
    repo_root = Path(args.repo_root) if args.repo_root else repo_root_from_pack(pack_dir)

    try:
        result = measure(pack_dir, golden_path, repo_root)
    except BaselineError as e:
        print(str(e), file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    n = result["n_cases"]
    print(f"n={n}  预铸 variant 归一化后 {result['n_prebaked_variants']} 条")
    print(f"实验① 逐字档（输入=助手自由文本）: {result['hits_assistant_free_text']}/{n} = {result['hit_rate_assistant_free_text'] * 100:.2f}%")
    print(f"  对照（输入=用户话术）          : {result['hits_user_utterance']}/{n} = {result['hit_rate_user_utterance'] * 100:.2f}%")
    print(f"原始数据 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
