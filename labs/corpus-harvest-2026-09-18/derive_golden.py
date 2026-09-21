#!/usr/bin/env python3
"""
derive_golden.py — 从公开语料派生 golden set 对照集（docs/11 §11.5 的「集合 A」）

职责：把 DianJin-CSC 的真实对话切成用例，产出 packs/fin-cs/golden/corpus_derived.jsonl。
不负责：不做标注判定（那是人的事）、不跑评测（eval 层的活）、不改产品代码。

**为什么一条用例要同时记两个字段（这是本脚本最容易搞错的地方）**：

`docs/10 §10.2` 说的「逐字（归一化后）比对」，比的是**助手打算说的那句话**与包内 variant，
不是用户说的话。而用户话术与客服话术是两拨不同的句子——拿用户话术去逐字比客服 variant
必然 0%，那是范畴错误，不是「命中率低」。

因此两个实验的输入本来就不同，必须分开记：

  实验①（逐字档，现状基线）：输入 = 助手自由文本 `assistant_reply_free`
      → 问「LLM 生成的那句话，能不能逐字命中预铸 variant？」
      → T11 实测答案：0/2（措辞每次不同）

  实验②（语义档，T15）：输入 = 用户话术 `user_utterance`
      → 问「用户的自由说法，能不能被语义检索映射到正确的 key？」
      → 这才是「让上游按 key 说话」那条路的前置能力

  `expect_key` 回答的是「**这一轮系统该说哪个 key**」——由该轮客服话轮的功能策略映射而来。

用法：
  python3 derive_golden.py --corpus ~/corpus/tongyi_dianjin__DianJin-CSC-Data/data/CSConv.json \
                           --out <仓库根>/packs/fin-cs/golden/corpus_derived.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 策略 → 本包 key 的映射。**这是业务判断，不是语料自带的事实**——语料给的是 12 类「话轮功能」，
# 本包按这些功能立 key（见 packs/fin-cs/phrases.json）。
STRATEGY_TO_KEY: Dict[str, Optional[str]] = {
    "策略1 礼貌问候": "greeting_inbound",
    "策略2 确认身份": "verify_identity",
    "策略3 重述或转述": "restate_issue",
    "策略4 细化问题": "probe_detail",
    "策略5 情感管理": "empathy_hardship",
    "策略6 提供建议": "offer_options",
    "策略7 信息传达": "inform_fact",
    "策略8 解决实施": "execute_solution",
    "策略9 反馈请求": "ask_feedback",
    "策略10 关系延续": "relationship_extend",
    "策略11 感谢与告别": "closing_thanks",
    # 策略12「其它」= 装不进任何固定功能的轮次 → 明确标 unspecified，
    # 即「这一轮**不该**命中任何预铸 key」。docs/11 §11.5 要求这一档必须存在，
    # 否则命中率是虚高的（把所有轮次都算成「应该有 key」）。
    "策略12 其它": None,
}

# 用户话术的可用性过滤：太短没信息、太长像拼接、含占位符说明已被脱敏替换
MIN_UTTER_LEN = 4
MAX_UTTER_LEN = 40


def is_usable_utterance(text: str) -> bool:
    """判断一条用户话术能不能进 golden set。

    排除：占位符（脱敏替换过，不是真实说法）、过长（多为拼接）、过短（无信息）。
    """
    if not (MIN_UTTER_LEN <= len(text) <= MAX_UTTER_LEN):
        return False
    if re.search(r"\[[^\]]+\]", text):
        return False
    if re.search(r"\*{2,}", text):
        return False
    return True


def derive(corpus_path: Path, cap_per_strategy: int) -> List[Dict[str, Any]]:
    """把对话语料切成用例。返回用例列表（未写盘）。"""
    dialogues = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not isinstance(dialogues, list):
        raise ValueError(f"语料顶层应为列表，实际 {type(dialogues).__name__}: {corpus_path}")

    cases: List[Dict[str, Any]] = []
    per_strategy: Dict[str, int] = {}
    seq = 0

    for di, item in enumerate(dialogues):
        turns = item.get("dialogue") or []
        for ti, turn in enumerate(turns):
            if turn.get("speaker") != "客户":
                continue
            # 找这条用户话术之后的第一条客服话轮——它才是「系统该说什么」
            nxt = next((t for t in turns[ti + 1 :] if t.get("speaker") == "客服"), None)
            if nxt is None:
                continue
            strategy = nxt.get("strategy")
            if strategy not in STRATEGY_TO_KEY:
                # 未知策略标签一律跳过并留痕，不猜
                continue
            utter = (turn.get("text") or "").strip()
            if not is_usable_utterance(utter):
                continue

            key = STRATEGY_TO_KEY[strategy]
            if key is not None:
                if per_strategy.get(strategy, 0) >= cap_per_strategy:
                    continue
                per_strategy[strategy] = per_strategy.get(strategy, 0) + 1

            seq += 1
            cases.append(
                {
                    "case_id": f"fin-cs-{seq:05d}",
                    "user_utterance": utter,
                    # 助手这轮实际说的话——实验①（逐字档）的输入
                    "assistant_reply_free": (nxt.get("text") or "").strip(),
                    "expect_key": key,
                    "expect_kind": "key" if key else "unspecified",
                    "source_strategy": strategy,
                    "source_id": "tongyi_dianjin/DianJin-CSC-Data",
                    "turn_ref": f"d{di:04d}/u{ti}",
                    # 半自动标注：key 由语料自带的策略标签映射而来，不是真人逐条判的
                    "labeled_by": "corpus-strategy-label",
                    "labeled_at": datetime.now(timezone.utc).astimezone().date().isoformat(),
                }
            )
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="从公开语料派生 golden set 对照集")
    ap.add_argument("--corpus", required=True, help="CSConv.json 路径")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument("--cap-per-strategy", type=int, default=200, help="每个策略最多取多少条（默认 200）")
    args = ap.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"语料不存在: {corpus_path}", file=sys.stderr)
        return 2

    cases = derive(corpus_path, args.cap_per_strategy)
    if not cases:
        # 空产物必须是失败，不能写一个空文件让人以为跑成功了
        print("派生结果为空——语料格式与预期不符，未写盘", file=sys.stderr)
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 分布摘要（人读）
    from collections import Counter

    print(f"写出 {len(cases)} 条 → {out}")
    print("\n按 expect_key 分布：")
    for k, n in Counter(c["expect_key"] or "(unspecified)" for c in cases).most_common():
        print(f"  {n:5d}  {k}")
    n_unspec = sum(1 for c in cases if c["expect_kind"] == "unspecified")
    print(f"\n其中 unspecified（不该命中任何 key）: {n_unspec} ({n_unspec / len(cases) * 100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
