#!/usr/bin/env python3
"""
derive_golden.py — 从公开语料派生 golden set 对照集（docs/11 §11.5 的「集合 A」）

职责：把语料里的真实对话切成用例，产出 packs/<业务>/golden/corpus_derived.jsonl。
不负责：不做标注判定（那是人的事）、不跑评测（eval 层的活）、不改产品代码。

**为什么一条用例要同时记两个字段（这是本脚本最容易搞错的地方，docs/11 §11.9.3）**：

`docs/10 §10.2` 说的「逐字（归一化后）比对」，比的是**助手打算说的那句话**与包内 variant，
不是用户说的话。而用户话术与客服话术是两拨不同的句子——拿用户话术去逐字比客服 variant
必然 0%，那是范畴错误，不是「命中率低」。

因此两个实验的输入本来就不同，必须分开记：

  实验①（逐字档，现状基线）：输入 = 助手自由文本 `assistant_reply_free`
      → 问「LLM 生成的那句话，能不能逐字命中预铸 variant？」

  实验②（语义档，T15）：输入 = 用户话术 `user_utterance`
      → 问「用户的自由说法，能不能被语义检索映射到正确的 key？」

  `expect_key` 回答的是「**这一轮系统该说哪个 key**」——由该轮客服话轮的功能策略映射而来。

**由 labs/corpus-harvest/derive_golden.py 提升（T12）时去掉的硬编码**：
  `STRATEGY_TO_KEY` → 外部映射文件（`--strategy-map <json>`）。**这张表是业务判断，不是语料自带的事实**——
  换业务时它一定不同，所以缺省值内置但必须能换。映射文件形如：

      {"策略1 礼貌问候": "greeting_inbound", "策略12 其它": null}

  值为 `null` = 该策略对应「不该命中任何预铸 key」（`expect_kind="unspecified"`，docs/11 §11.5 要求这一档必须存在，
  否则命中率是虚高的）。缺省映射沿用 DianJin-CSC 的 12 类话轮功能 → `packs/fin-cs` 的 key。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 缺省映射：DianJin-CSC 的 12 类「话轮功能」→ packs/fin-cs 的 key。
# **这是业务判断，不是语料自带的事实**；换业务请用 `--strategy-map` 传入另一张表。
DEFAULT_STRATEGY_TO_KEY: Dict[str, Optional[str]] = {
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
    # 即「这一轮**不该**命中任何预铸 key」。docs/11 §11.5 要求这一档必须存在。
    "策略12 其它": None,
}

# 用户话术的可用性过滤：太短没信息、太长像拼接、含占位符说明已被脱敏替换
MIN_UTTER_LEN = 4
MAX_UTTER_LEN = 40

# 占位符形态：[中括号内容] 或 连续星号脱敏（语料里的脱敏替换痕迹，不是真实说法）
PLACEHOLDER_PATTERNS = (r"\[[^\]]+\]", r"\*{2,}")

DERIVED_BY = "corpus-strategy-label"  # 半自动标注：key 由语料策略标签映射而来，不是真人逐条判


class DeriveError(Exception):
    """派生流程的致命错误——一律向上抛，不吞、不降级。"""


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


def load_strategy_map(path: Optional[Path]) -> Dict[str, Optional[str]]:
    """读外部「策略 → key」映射；缺省用内置的 DianJin-CSC 映射。

    缺 key / 值不是 str 或 null / 顶层不是对象 → 一律报错（fail-closed）：
    一张写错的映射表会让全批用例的 expect_key 悄悄错掉。
    """
    if path is None:
        return dict(DEFAULT_STRATEGY_TO_KEY)
    if not Path(path).exists():
        raise DeriveError(f"策略映射文件不存在: {path}")
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DeriveError(f"策略映射不是合法 JSON: {path} :: {e}") from e
    if not isinstance(doc, dict) or not doc:
        raise DeriveError(f"策略映射顶层应为非空对象（策略名 → key 或 null）: {path}")
    for strat, key in doc.items():
        if key is not None and not isinstance(key, str):
            raise DeriveError(f"策略映射里 {strat!r} 的值应为 key 字符串或 null，实际 {key!r}")
    return doc


def derive(
    corpus_path: Path,
    cap_per_strategy: int,
    strategy_to_key: Optional[Dict[str, Optional[str]]] = None,
    source_id: str = "tongyi_dianjin/DianJin-CSC-Data",
) -> Tuple[List[Dict[str, Any]], Counter]:
    """把对话语料切成用例。

    返回 (用例列表, 未知策略 Counter)——未知策略 = 映射表外的策略标签，被跳过但**必须留痕**：
    静默跳过会让轮次悄悄消失、golden set 残缺、命中率口径失真。
    """
    mapping = dict(strategy_to_key) if strategy_to_key is not None else dict(DEFAULT_STRATEGY_TO_KEY)
    corpus_path = Path(corpus_path)
    if not corpus_path.exists():
        raise DeriveError(f"语料不存在: {corpus_path}")
    try:
        dialogues = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DeriveError(f"语料不是合法 JSON: {corpus_path} :: {e}") from e
    if not isinstance(dialogues, list):
        raise DeriveError(f"语料顶层应为列表，实际 {type(dialogues).__name__}: {corpus_path}")

    cases: List[Dict[str, Any]] = []
    per_strategy: Dict[str, int] = {}
    unknown_strategies = Counter()  # 映射表外的策略标签：跳过并留痕，不猜
    seq = 0
    today = datetime.now(timezone.utc).astimezone().date().isoformat()

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
            if strategy not in mapping:
                # 未知策略标签一律跳过并留痕，不猜（静默降级是头号红线）。
                # 计数在 `unknown_strategies`，结尾由 main 打印并据此非零退出——
                # 轮次静默消失会让 golden set 残缺、命中率口径失真。
                unknown_strategies[strategy] += 1
                continue
            utter = (turn.get("text") or "").strip()
            if not is_usable_utterance(utter):
                continue

            key = mapping[strategy]
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
                    "source_id": source_id,
                    "turn_ref": f"d{di:04d}/u{ti}",
                    # 半自动标注：key 由语料自带的策略标签映射而来，不是真人逐条判的
                    "labeled_by": DERIVED_BY,
                    "labeled_at": today,
                }
            )
    return cases, unknown_strategies


def write_jsonl(cases: List[Dict[str, Any]], out: Path) -> int:
    """把用例逐行写入 JSONL：**临时文件 + rename 原子落盘**，返回写出的条数。

    为什么不能直接 `out.open("w")`（T12c 修 1）：JSONL 是**追加语义**的产物，下游按
    存活行计数。中断 / 盘满 / 进程被杀会在原位置留下**半截 JSONL**——最后一行截断、
    中间某行缺失，而每行本身都长得像合法 JSON，于是下游的 `splitlines` + 逐行解析
    **静默少算几条**，golden set 变小、命中率分母悄悄失真，事后无从定位。
    原子写把失败面收窄成「目标文件要么不存在、要么是完整产物」。
    与 `fetch.write_lock` 同款模式（临时文件 + `rename`）。

    目录缺失在 rename **之前**建好——不能让 mkdir 失败留下一个写了一半的临时文件
    被误认为目标产物。
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        tmp.replace(out)
    except BaseException:
        # 失败必须删掉临时文件：残留的 .tmp 不属任何层契约，留盘会污染目录巡检
        tmp.unlink(missing_ok=True)
        raise
    return len(cases)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="从公开语料派生 golden set 对照集")
    ap.add_argument("--corpus", required=True, help="CSConv.json 路径")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument(
        "--strategy-map",
        default=None,
        help="策略→key 映射 JSON（缺省用内置的 DianJin-CSC 12 类映射；换业务必须传）",
    )
    ap.add_argument("--source-id", default="tongyi_dianjin/DianJin-CSC-Data", help="写入用例的 source_id")
    ap.add_argument("--cap-per-strategy", type=int, default=200, help="每个策略最多取多少条（默认 200）")
    args = ap.parse_args(argv)

    corpus_path = Path(args.corpus)
    strategy_path = Path(args.strategy_map) if args.strategy_map else None
    out = Path(args.out)

    try:
        mapping = load_strategy_map(strategy_path)
        cases, unknown_strategies = derive(
            corpus_path, args.cap_per_strategy, strategy_to_key=mapping, source_id=args.source_id
        )
    except DeriveError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not cases:
        # 空产物必须是失败，不能写一个空文件让人以为跑成功了
        print("派生结果为空——语料格式与预期不符，未写盘", file=sys.stderr)
        return 3

    write_jsonl(cases, out)

    # 分布摘要（人读）
    print(f"写出 {len(cases)} 条 → {out}")
    print("\n按 expect_key 分布：")
    for k, n in Counter(c["expect_key"] or "(unspecified)" for c in cases).most_common():
        print(f"  {n:5d}  {k}")
    n_unspec = sum(1 for c in cases if c["expect_kind"] == "unspecified")
    print(f"\n其中 unspecified（不该命中任何 key）: {n_unspec} ({n_unspec / len(cases) * 100:.1f}%)")

    # 映射表外的策略标签：必须**打印**出来，并据此非零退出。
    # 原来这里是 `continue` + 上一行注释「跳过并留痕」——实际没有任何计数或日志，
    # 轮次静默消失、golden set 静默残缺、命中率口径失真（T12b 修 4）。
    n_skipped = sum(unknown_strategies.values())
    if n_skipped:
        print(f"\n跳过：映射表外的策略标签（未生成用例，golden set 不完整）")
        for strat, n in unknown_strategies.most_common():
            print(f"  {n:5d}  {strat}")
        print(f"  共 {n_skipped} 条被跳过——请补全 --strategy-map 后重跑", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
