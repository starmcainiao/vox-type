"""
adapters.framework_kefu.hit_query — 命中查询公开 API（T29：为内嵌旁路钩子供数）

职责：把 bridge 里那套命中判定提成模块级公开函数，并组合出单一查询入口 find_hit。
      kefu 侧的钩子（LocalCascadeAdapter.synthesize 前的预铸包查询）只能依赖
      **公开**面——依赖他人私有方法（_lookup_key / _pick_by_text …）就是偷看，违规。

不负责：不驱动播放、不产生事件、不做 ASR/brain、不做引擎一致性检查
      （那是 bridge↔live_tts 的关系，钩子侧自校验）、未命中不抛错。

语义权威（本文件是 bridge 私有方法的**逐语句搬迁**，不得加松）：
    key 档：lookup_key —— part_index=0 + rate 档优先 + pack.lookup 指纹复核
    text 档：normalize_text（docs/10 §10.3 四步）→ pick_by_text（rate 档优先
             → 索引首条，顺序稳定）→ confirm
    禁止语义/模糊/embedding/编辑距离/去语气词/去标点匹配（docs/10 §10.2 裁定 1）。

未命中返回不抛：fail-closed 的抛错留在 run_turn 层——本 API 的语义是"报结论"
      （带 miss_reason 的 HitResult），由调用方决定要不要中止。
"""

from dataclasses import dataclass
from typing import Optional

from runtime import REASON_KEY_NOT_PREBAKED
from assets import AssetEntry

from .normalize import normalize_text


# ---------------------------------------------------------------------------
# 常量归属（T29）：本模块是被 bridge 与 __init__ 共同导入的最底层判定模块。
#   bridge.py 需要这些常量（判定顺序与事件字段），hit_query 也需要同名口径，
#   所以真源放在这里、由 bridge 反向导入——否则 bridge→hit_query→bridge 成环。
# 取值与语义与 T29 之前完全一致，只是换了归属模块。
# ---------------------------------------------------------------------------

# 两种"上游表示形态"（T10.6 报命中率必须带这一列，否则数字会误导）
MODE_KEY = "key"
MODE_TEXT = "text"

# 文本档未命中的原因码：docs/10 §10.4 的合法取值之一。
# WHY 定义在 adapters 层：runtime 冻结区里只有 key_not_prebaked / say_live_text / …，
#      没有 text_not_prebaked；本层不改建内核，只按 docs/10 的表补齐取值。
REASON_TEXT_NOT_PREBAKED = "text_not_prebaked"


@dataclass(frozen=True)
class HitResult:
    """一次命中查询的结论（不抛错，命中与否都靠字段读）。

    属性：
        entry:        命中的资产条目；未命中恒为 None
        mode:         本轮上游表示形态 MODE_KEY | MODE_TEXT
        key:          key 档的输入 key；文本档恒为 None
        text:         命中条目原文，或未命中时的输入文本
        miss_reason:  未命中原因码（REASON_KEY_NOT_PREBAKED / REASON_TEXT_NOT_PREBAKED）；
                      命中恒为 None
    """

    entry: Optional[AssetEntry]
    mode: str
    key: Optional[str]
    text: str
    miss_reason: Optional[str]


def build_text_index(pack) -> dict:
    """建"归一化文本 → 条目列表"索引。

    注意：这里**不缓存**——包是只读，缓存安全，但缓存归属留在 KefuBridge
    （self._text_index_cache 的惰性判断与字段不迁移）。

    返回列表保持 pack.assets 的顺序，pick_by_text 才可能稳定地"退到索引里第一条"。
    """
    idx = {}
    for entry in pack.assets:
        idx.setdefault(normalize_text(entry.text), []).append(entry)
    return idx


def lookup_key(pack, key: str, rate_key: str = "normal") -> Optional[AssetEntry]:
    """key 档命中判定：走 pack.lookup（它自带指纹与音频存在性校验）。

    WHY 不自己遍历就返回候选：assets.lookup 一律返回 None 不告诉你原因，
        但它把指纹不符 / 文件不存在都算作"查不到"——这正是预铸资产层
        的保险丝。这里只负责喂对 expected_text（候选条目自己的文本）。
    """
    for cand in pack.assets:
        if cand.key != key or cand.part_index != 0 or cand.rate_key != rate_key:
            continue
        entry = pack.lookup(
            key=key, part_index=0, rate_key=rate_key,
            variant=cand.variant, expected_text=cand.text,
        )
        if entry is not None:
            return entry
    return None


def pick_by_text(pack, norm: str, rate_key: str = "normal") -> Optional[AssetEntry]:
    """文本档命中判定：归一化后逐字相等 → 取该 key 的某个 variant。

    WHY 逐字相等（str ==）：docs/10 §10.2 裁定 1。任何"差一点也算"的比较
        都会把模型当成命中判据，不可复核，而且会把"接近但不是这句"的音频播出去。
    WHY 多 variant 时优先取当前语速档：同一句在包内可能按 slow/normal/fast 各铸一份，
        取当前会话语速档那份最接近上游预期；都没有时才退到索引里第一条（顺序稳定）。
    """
    cands = build_text_index(pack).get(norm)
    if not cands:
        return None
    for cand in cands:
        if cand.rate_key == rate_key:
            return confirm(pack, cand)
    return confirm(pack, cands[0])


def confirm(pack, cand: AssetEntry) -> Optional[AssetEntry]:
    """用 pack.lookup 复核一次（索引只按文本建，指纹/文件校验在这里补上）。"""
    return pack.lookup(
        key=cand.key, part_index=cand.part_index, rate_key=cand.rate_key,
        variant=cand.variant, expected_text=cand.text,
    )


def text_for_key(pack, key: str, rate_key: str = "normal") -> str:
    """key 档的 live_text：包内该 key 的原文；包里没有该 key 时回退成 key 本身。

    WHY 回退成 key 而不是空串：降级路径要拿它去合成，空串无法合成；
        同时事件的 reason 已标明 key_not_prebaked，不会误认为"命中了这句话"。
    """
    fallback = None
    for cand in pack.assets:
        if cand.key == key:
            if cand.part_index == 0 and cand.rate_key == rate_key:
                return cand.text
            if fallback is None:
                fallback = cand.text
    return fallback if fallback is not None else key


def find_hit(pack, *, key: Optional[str] = None,
             text: Optional[str] = None,
             rate_key: str = "normal") -> HitResult:
    """命中查询单一入口：给定一种"这一轮要说什么"的表示，返回结论不抛错。

    参数：
        pack:       只读资产包（assets.AssetPack）
        key:        key 档：上游直接给的话术 key
        text:       文本档：上游自由文本（内部走 normalize_text 归一化）
        rate_key:   语速档（默认 normal；rate 档优先策略用它）

    返回：
        HitResult（命中时 entry 非 None、miss_reason 为 None；未命中时 entry 为
        None、miss_reason 是具体原因码——**永不抛错、永不返回裸 None**）

    异常：
        ValueError: key / text 不是恰好一种（两种都给或都没给）
    """
    given = sum(1 for v in (key, text) if v is not None)
    if given == 0:
        raise ValueError(
            "find_hit 需要 key / text 之一，实际给了 0 种（key=None, text=None）"
            "——禁止猜调用方想要哪一档"
        )
    if given > 1:
        raise ValueError(
            f"find_hit 只能给一种输入表示（key / text），实际给了 {given} 种"
            f"（key={key!r}, text={text!r}）——禁止猜调用方想要哪一档"
        )

    if key is not None:
        # key 档：直查命中 + 补包内原文（未命中时 key 本身就是给钩子的可用线索）
        entry = lookup_key(pack, key, rate_key)
        return HitResult(
            entry=entry,
            mode=MODE_KEY,
            key=key,
            text=text_for_key(pack, key, rate_key),
            miss_reason=None if entry is not None else REASON_KEY_NOT_PREBAKED,
        )

    norm = normalize_text(text)
    if not norm:
        # 空回复没有可播内容，按未命中处理（不猜、不凑）
        return HitResult(
            entry=None, mode=MODE_TEXT, key=None, text=text,
            miss_reason=REASON_TEXT_NOT_PREBAKED,
        )
    entry = pick_by_text(pack, norm, rate_key)
    return HitResult(
        entry=entry,
        mode=MODE_TEXT,
        key=None,
        text=text,
        miss_reason=None if entry is not None else REASON_TEXT_NOT_PREBAKED,
    )
