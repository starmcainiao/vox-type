#!/usr/bin/env python3
"""
common.py — 多行业语料分析的**共用判据层**。

职责（三件事，全部从既有脚本移植，不新造一套）：
  1. `skeleton()` / `REPEAT_MIN` / `template_stats()` —— 与 `labs/corpus-harvest/
     cross_check_template_rate.py` **同源**（逐字一致的三条替换规则、同一个阈值 5），
     用于「句式可复用率」（辅指标 / 客服 1.8% 那个口径）。
  2. `FLOW_PATTERNS` —— 「流程决定话轮」的判定词表。语义与 `labs/corpus-harvest/README.md §六`
     的客服 23.0% 表同源：仪式性/流程性 = 问候、确认身份、告别、合规提示、请稍等、
     未听清重试、转人工。跨语料时按各语料自带的对话行为标注对齐到这一族（见各 loader）。
  3. `inject_all_flow=True` —— 反空转开关：把所有话轮强判为「流程决定」。
     打开后占比应≈100%，`verify_occupancy` 会响亮失败。

**不得**在本文件里放任何产品代码、任何层契约、任何第三方依赖。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 1) 句式可复用率（与 cross_check_template_rate.py 同源，勿改）
# ---------------------------------------------------------------------------
REPEAT_MIN = 5


def skeleton(s: str) -> str:
    """去实体值，只留句式骨架。与 labs/corpus-harvest/cross_check_template_rate.py 逐字一致。"""
    s = re.sub(r"\[[^\]]+\]", "□", s)
    s = re.sub(r"\d+(\.\d+)?", "#", s)
    s = re.sub(r"[A-Za-z]+", "@", s)
    return s.strip()


def template_stats(texts: List[str]) -> Dict[str, Any]:
    """对一组文本算骨架复用统计。返回结构与 cross_check_template_rate.py 的 stats() 一致。"""
    sk = Counter(skeleton(t) for t in texts)
    n = len(texts)
    reusable_turns = sum(v for v in sk.values() if v >= REPEAT_MIN)
    return {
        "n_turns": n,
        "n_skeletons": len(sk),
        "compress_ratio": round(n / len(sk), 3) if sk else None,
        "reusable_turns": reusable_turns,
        "reusable_rate": round(reusable_turns / n, 4) if n else None,
        "top_skeletons": [{"count": c, "skeleton": s} for s, c in sk.most_common(8)],
    }


# ---------------------------------------------------------------------------
# 2) 流程决定话轮：判定词表
# ---------------------------------------------------------------------------
# 中文（CrossWOZ）：系统侧 dialog_act 的 intent 取值里，仪式性/流程性的那一部分。
# 实测 CrossWOZ 的 sys_da_voc.json 全量 intent 集合（155 个组合，`类别+intent+域+槽` 形态）里
# General 类的 intent 为 {bye, greet, reqmore, thank, welcome}，
# 对应客服表的「问候 / 确认身份(此处无对应) / 告别 / 合规提示(无) / 未听清重试(reqmore) / 转人工(无)」
# inform = 信息传达（内容决定）。
CN_FLOW_INTENTS = {"welcome", "greet", "thank", "bye", "reqmore"}

# 英文：MASSIVE/HWU/Taskmaster/MultiWOZ 都没有「问候/告别」这类对话行为标注，
# 只能用**文本形态**近似。词表按客服表的语义（问候、告别、确认身份、请稍等、未听清重试、转人工）
# 逐条给正则，逐条可删——不要在这里写 catch-all。
EN_FLOW_PATTERNS = [
    # 问候
    (r"\bhi(?!a|er|gh)\b", "greeting"),
    (r"\bhello\b", "greeting"),
    (r"\bhey\b", "greeting"),
    (r"\bgood (morning|afternoon|evening|day)\b", "greeting"),
    # 告别
    (r"\bbye\b", "farewell"),
    (r"\bgood ?bye\b", "farewell"),
    (r"\bsee (you|ya|u)\b", "farewell"),
    (r"\btake (care|it)\b", "farewell"),
    (r"\bhave a (great|nice|good|wonderful) (day|night|time|trip)\b", "farewell"),
    (r"\bthanks?\b", "farewell_thanks"),
    (r"\bthank (you|u)\b", "farewell_thanks"),
    (r"\byou'?re welcome\b", "farewell_thanks"),
    # 确认身份 / 索要身份信息
    (r"your (name|order id|email|phone|username|member level|address)", "verify_identity"),
    (r"may i (have|ask|get) (your|the)", "verify_identity"),
    (r"could i (have|get) (your|the)", "verify_identity"),
    (r"can i (have|get) (your|the)", "verify_identity"),
    (r"what'?s your (name|order)", "verify_identity"),
    (r"\byour username\b", "verify_identity"),
    # 请稍等 / 未听清重试
    (r"\bone moment\b", "hold_notice"),
    (r"\bhold on\b", "hold_notice"),
    (r"\blet me (check|see|look)\b", "hold_notice"),
    (r"\blet me take a look\b", "hold_notice"),
    (r"\bgive me a (second|minute|moment)\b", "hold_notice"),
    (r"\bcould you (repeat|say that again|try again)\b", "repeat_ask"),
    (r"\bi'?m sorry, could you", "repeat_ask"),
    # 转人工 / 升级
    (r"\bmanager\b", "transfer"),
    (r"\bescalate\b", "transfer"),
    (r"\btransfer( you| you to| to someone)?\b", "transfer"),
    # 身份/关系确认（ABCD 这类客服语料里高频的仪式轮：确认对方身份、称呼、致谢收尾）
    (r"\bwhat('?s| is) your (order )?id\b", "verify_identity"),
    (r"may i ask the reason", "clarify"),
    (r"\bok(ay)?, (i'?ve|i have) (let|informed|notified)", "closing"),
    (r"\bi can'?t (accept|process|refund) (the|this|that)", "policy_decline"),
    (r"would there be anything else", "closeout"),
    (r"is there (anything|else) (else )?i can help", "closeout"),
]


def classify_by_flow_patterns(text: str) -> List[str]:
    """按 EN_FLOW_PATTERNS 命中的类别列表返回（空列表 = 未命中 = 内容决定）。"""
    t = text.lower()
    return [kind for pat, kind in EN_FLOW_PATTERNS if re.search(pat, t)]


def occupancy(total: int, flow_turns: int) -> float:
    """流程决定话轮占比（0~1，保留 4 位）。"""
    return round(flow_turns / total, 4) if total else 0.0


def verify_occupancy(
    report: Dict[str, Any],
    *,
    key: str = "flow_decided_rate",
    lower: float = 0.0,
    upper: float = 1.0,
    expect_not_all: bool = True,
    skip: Any = None,
) -> List[str]:
    """反空转校验：占比必须落在 (lower, upper) 内，且不能是全 1。

    这是卡面「把分类判据改成「全判为流程决定」→ 占比应≈100%、用例必须失败」的落地。
    `expect_not_all=True` 时任何一份集占比 >= 0.999 直接判失败。
    `skip` 是集合：里面列出的 corpus 名不参与本校验（用于「口径修正项」这类
    本身就不出流程占比的条目——它们不该被当成空跑）。
    """
    skip = skip or ()
    problems: List[str] = []
    for cid, block in report.get("corpora", {}).items():
        if cid in skip:
            continue
        v = block.get(key)
        n = block.get("n_turns", 0)
        v = block.get(key)
        n = block.get("n_turns", 0)
        if v is None or n == 0:
            problems.append(f"{cid}: 缺 {key} 或 n_turns=0（不得空跑）")
            continue
        if not (lower <= v <= upper):
            problems.append(f"{cid}: {key}={v} 超出 [{lower}, {upper}]")
        if expect_not_all and v >= 0.999:
            problems.append(f"{cid}: {key}={v} ≈ 100% —— 分类判据疑似退化为「全判流程决定」")
    return problems
