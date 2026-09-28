"""
eval.cer — 回读 CER 指标（Character Error Rate，纯标准库）

职责：实现回读 CER 的两个原子函数——文本归一（normalize_for_cer）与字符级
      错误率（cer），以及本卡新增的指标字段名常量。
不负责：不做 ASR 转写（adapters/asr_omlx）、不做采样与报告组装（readback/）、
      不定义分位数（stats.percentile 是唯一分位实现）。

归一口径（Seed-TTS-eval 式，docs/05 §5.3「内容可懂度」）：
  「去标点与空白后逐字比对」——把文本压成纯字符序列，再做字符级编辑距离。
  标点属于**韵律标记**而非内容：同一段话带不带句号是同一句话，
  因此 cer("今天下午三点提醒你开会", "今天下午三点提醒你开会。") == 0.0。

本卡**不做**的事（docs/10 裁定 1 明确禁止）：
  - 不做同义替换（"你好" ≠ "您好"）；
  - 不做模糊匹配 / 拼音近似 / 语义归一；
  - 不做跨语种转写映射。
CER 就是逐字编辑距离，改口径 = 破坏性变更。

字段名纪律：CER_* 常量定义在**本模块**，不进 core 层的指标规格模块（冻结区，
  本卡不碰 core）；将来指标转正再走大版本迁移。
"""

from typing import Any, Set

# ---------------------------------------------------------------------------
# 归一常量
# ---------------------------------------------------------------------------

# 要剥掉的标点集（中英常规标点 + 全角/半角括号引号 + 空白与不可见字符）。
# WHY 自带常量而不复用别的层：eval/cer.py 不依赖 rules/ 与编译器，
# 且标点集属评测口径的一部分——写在本模块里才能与「指标名常量」同处一地审核。
_PUNCT_CHARS: str = (
    # 中文标点（全角）
    "\uFF0C\u3002\uFF01\uFF1F\u3001\uFF1B\uFF1A\uFF08\uFF09"   # ，。！？、；：（）
    "\u3010\u3011\u300A\u300B\u300C\u300D\u300E\u300F"          # 【】《》「」『』
    "\u201C\u201D\u2018\u2019\u2026\u2014\uFF5E\u00B7"          # “”‘’…—～·
    # 英文与通用标点（半角）
    ".,!?;:'\"()[]{}<>-_~`@#$%^&*+=/\\|"
    # 连接线变体与不可见字符（含全角空格、不换行空格、零宽空格）
    "\u2013\u00A0\u200B\u3000"
    # 空白
    " \t\n\r\f\v"
)

# 集合形式（字符判定的实际使用面；常量保留字符串以便逐码位审阅口径）
_PUNCT_SET: Set[str] = set(_PUNCT_CHARS)

# ---------------------------------------------------------------------------
# 指标字段名常量（本卡新增；冻结在 eval/cer.py，不迁 core）
# ---------------------------------------------------------------------------

# 单条样本的字符错误率（取值 0.0~1.0；分母 = 归一后 reference 的字符数）
CER: str = "cer"

# 汇总分位口径：与 eval/stats.py 的线性插值分位数配合，禁止用均值外推
CER_P50: str = "cer_p50"
CER_P99: str = "cer_p99"
CER_MEAN: str = "cer_mean"

# 有效样本数（分母：只计 transcribe 成功且 reference 非空的条数）
CER_N: str = "cer_n"

# 原始数据指针名（raw/readback_samples.jsonl）
RAW_SAMPLES: str = "readback_samples"


def normalize_for_cer(text: Any) -> str:
    """归一文本为「纯字符序列」：去全部标点与空白。

    参数：
        text: 待归一文本（str）

    返回：
        归一后的字符串（可能为空——调用方需自行判定空的情况）

    异常：
        ValueError: text 不是 str（消息含实际类型）
    """
    if not isinstance(text, str):
        raise ValueError(
            f"normalize_for_cer: text 必须是字符串，实际为 {type(text).__name__}"
            f"（值为 {text!r}）"
        )
    return "".join(ch for ch in text if ch not in _PUNCT_SET)


def _edit_distance(ref: str, hyp: str) -> int:
    """字符级编辑距离（插入 / 删除 / 替换，代价各 1）。

    实现：滚动数组 DP，空间 O(len(ref))。
    WHY 不用库（如 rapidfuzz / jellyfish）：本仓零第三方依赖，
    且编辑距离是几十行标准实现——引库只为了让一个指标依赖外部版本。
    """
    # 空串情形：一个空一个不空 → 距离 = 另一个的长度
    if not ref:
        return len(hyp)
    if not hyp:
        return len(ref)

    # prev[i] = ref[:i] 对齐到 hyp[:j-1] 的距离（上一行）
    prev = list(range(len(ref) + 1))
    for j in range(1, len(hyp) + 1):
        cur = [j]  # cur[0] = ref[:0] 对齐 hyp[:j] 的距离 = j
        ref_ch = None  # 占位，循环内赋值
        for i in range(1, len(ref) + 1):
            ref_ch = ref[i - 1]
            cost = 0 if ref_ch == hyp[j - 1] else 1
            cur.append(
                min(prev[i] + 1,      # 删除 ref[i-1]
                    cur[i - 1] + 1,   # 插入 hyp[j-1]
                    prev[i - 1] + cost)  # 匹配 / 替换
            )
        prev = cur
    return prev[len(ref)]


def cer(reference: Any, hypothesis: Any) -> float:
    """字符错误率 = 归一后编辑距离 / 归一后 reference 长度。

    参数：
        reference:  源文本（合成/拼接前的真值文本）
        hypothesis: 回读转写文本（ASR 输出）

    返回：
        浮点 CER，取值下界 0.0。
        注意：**上界可 > 1.0**——转写比源文本多出的字符全部计入编辑距离
        （多说的字也是错误），这是 Seed-TTS-eval 口径的常规结果，不夹取。

    边界（fail-closed）：
        - 归一后 reference 为空（纯标点/纯空白输入）→ 抛 ValueError（消息含原文本）。
          这是 0/0，返回 0.0 会谎报"完全可懂"，返回 1.0 会谎报"完全不可懂"。
        - 入参不是 str → ValueError（消息含实际类型与值）。
    """
    if not isinstance(reference, str):
        raise ValueError(
            f"cer.reference: 必须是字符串，实际为 {type(reference).__name__}"
            f"（值为 {reference!r}）"
        )
    if not isinstance(hypothesis, str):
        raise ValueError(
            f"cer.hypothesis: 必须是字符串，实际为 {type(hypothesis).__name__}"
            f"（值为 {hypothesis!r}）"
        )

    ref_norm = normalize_for_cer(reference)
    hyp_norm = normalize_for_cer(hypothesis)

    if not ref_norm:
        raise ValueError(
            f"cer.reference: 归一后为空，无法计算 CER（0/0 不做假成功）；"
            f"原文本 {reference!r} 只含标点或空白"
        )

    return _edit_distance(ref_norm, hyp_norm) / len(ref_norm)
