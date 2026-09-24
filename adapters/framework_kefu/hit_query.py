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
    序列档（T33）：normalize_text → 按包内 variant 归一化文本逐段**逐字覆盖**
             （最长优先、任一段不中即整体未命中）→ 每段仍过 confirm 复核
             —— docs/10 §10.7 的算法判据，冻结，不得另立规则
    禁止语义/模糊/embedding/编辑距离/去语气词/去标点匹配（docs/10 §10.2 裁定 1）。

未命中返回不抛：fail-closed 的抛错留在 run_turn 层——本 API 的语义是"报结论"
      （带 miss_reason 的 HitResult / SequenceHitResult），由调用方决定要不要中止。
"""

from dataclasses import dataclass
from typing import Optional, Tuple

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


# T27 消费侧接线说明（语义零变化）：
#
#   `lookup_key` 原先是「线性遍历 pack.assets 找候选 → 逐候选 pack.lookup」，
#   而 pack.lookup 内部又是一次 O(n) 线性遍历 → 消费侧最坏 O(n²)。
#   T27 把 assets 层的候选定位换成惰性身份索引（O(1) 查表），于是
#   消费侧最坏情形从 O(n²) 降到 O(n·k)（k = 候选数）。
#
#   关键约束（本卡的硬判据）：**候选的筛选条件与顺序一律不动**——
#   仍按 pack.assets 的包内顺序逐条判定 `key == key`、`part_index == 0`、
#   `rate_key == rate_key`，命中第一条复核通过的即返回。
#   顺序变了会让「先出现的 variant 优先」这条既有语义被悄悄改掉
#   （assets 允许同 key 多 variant，顺序是唯一稳定的取法）。
#   因此这里不再抽候选函数：直接沿用原循环体，只把 pack.lookup 的内部
#   定位交给 assets 层的索引。


def lookup_key(pack, key: str, rate_key: str = "normal") -> Optional[AssetEntry]:
    """key 档命中判定：走 pack.lookup（它自带指纹与音频存在性校验）。

    WHY 不自己遍历就返回候选：assets.lookup 一律返回 None 不告诉你原因，
        但它把指纹不符 / 文件不存在都算作"查不到"——这正是预铸资产层
        的保险丝。这里只负责喂对 expected_text（候选条目自己的文本）。

    T27：候选仍按包内顺序逐条取，但每次 `pack.lookup` 的内部定位走 assets 层
        身份索引（O(1)），消费侧最坏情形由 O(n²) 降到 O(n·k)。候选顺序、
        复核次数与返回结果与原实现完全一致。
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


# ---------------------------------------------------------------------------
# 序列命中（T33，docs/10 §10.7）
#
# 为什么有第二个入口：docs/06 §6.1.4 的准入判据是「一句一 variant」（字模最小
#   粒度 = 一句），所以多分句整段不在包内；上游说整段时单段逐字查不到 → 走原路
#   （秒级合成）。§10.7 的裁定 4 允许把整段按包内 variant 的归一化文本逐段覆盖，
#   每段仍是归一化后逐字相等——这不是近似匹配，只是把 (b) 推广到 k 段拼接。
#
# 本入口**不改 find_hit**（T29 公开 API，语义冻结）；k=1 时两者结论一致。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceHitResult:
    """一次序列命中查询的结论（不抛错，命中与否都靠字段读）。

    属性：
        entries:      命中序列（按消费顺序，即输入里的字面顺序）；未命中为空元组
        mode:         恒为 MODE_TEXT——序列只服务文本档，没有 key 档输入
        text:         输入原文（**未归一化**，与 HitResult.text 同口径）
        miss_reason:  命中恒 None；未命中为 REASON_TEXT_NOT_PREBAKED
        uncovered:    未覆盖的剩余——未命中时是**归一化空间**的剩余文本
                      （从第一个覆盖不上的位置起），命中为空串
        blocked_by:   诊断留痕：文本能匹配、但被 rate_key / part_index 口径剔除的候选
                      标识，格式 `"<key>@<rate_key>#<part_index>"`（例 `"greet@slow#0"`）。
                      **命中恒为空元组**；**只进诊断、永不进命中**——对应 docs/10 §10.7
                      与 docs/17 §三 的「near-miss 只进诊断、永不进命中」红线，
                      不得影响 entries / miss_reason 的判定
    """

    entries: Tuple[AssetEntry, ...]
    mode: str
    text: str
    miss_reason: Optional[str]
    uncovered: str
    blocked_by: Tuple[str, ...] = ()


def _sequence_candidates(pack, rate_key: str) -> Tuple[Tuple[str, AssetEntry], ...]:
    """建可用候选索引：(归一化文本, 条目)，按 pack.assets 顺序。

    只收 part_index == 0 且 rate_key 匹配的条目，且必须过 confirm(pack, cand) 的
    指纹/文件复核——复核不过的**不进**候选（指纹漂移/文件缺失在这里就排除，
    对应 §10.4 的 fingerprint_mismatch / audio_file_missing 语义）。

    同一条归一化文本在包内可能有多条资产（例：`点下方按钮或直接说明即可。` 同属
    clarify_work_order__2 与 clarify_repair__3）；本函数**不跨条目去重**，保留全部
    可用条目，让"同长度多候选取首条"在**调用点**判定（见 find_hit_sequence 的
    严格大于替换）——同长度多候选取索引内首条是 §10.7 的判据，不是本函数的私事。
    """
    cands = []
    for cand in pack.assets:
        if cand.part_index != 0 or cand.rate_key != rate_key:
            continue
        entry = confirm(pack, cand)
        if entry is None:
            continue
        text_norm = normalize_text(entry.text)
        if not text_norm:
            # 归一化后为空的条目（" " / "　" / "\t"）不是"可消费的一段"：
            # startswith("") 恒真、消费长度 0 → 覆盖循环会空转（pos 不前进、
            # entries 无上限增长）。这类条目本该在预铸质检被拦（帧数 / 峰值 dBFS），
            # 但 load_pack 层放行任何 manifest，所以这里显式排除——宁可少一个候选，
            # 不可空转（T33 静默失败审计 P1：实测 3 秒内 entries 涨到 3100 万条）。
            continue
        cands.append((text_norm, entry))
    return tuple(cands)


def _blocked_identifiers(pack, norm: str, rate_key: str, pos: int) -> Tuple[str, ...]:
    """诊断留痕：收集「文本能匹配、但被口径剔除」的候选标识（docs/10 §10.7 末节）。

    元素格式（冻结）：`"<key>@<rate_key>#<part_index>"`，例 `"greet@slow#0"`。

    筛选条件（遍历 pack.assets，按包内顺序、同一标识只记一次）：该条目
    `normalize_text(text)` 非空、且是 `norm[pos:]` 的**前缀**（消费长度 > 0），
    但被 `part_index != 0` 或 `rate_key != 传入的 rate_key` 之一排除。

    语义边界：这是**诊断留痕**，对应 §10.7 与 docs/17 §三 的「near-miss 只进诊断、
    永不进命中」红线——它**不得**影响 entries / miss_reason 的判定。**不**收录
    「因 `confirm` 指纹复核不过被剔除」的候选：那是指纹/文件问题，语义不同，
    混进来会让「本该命中却换了路径」的诊断分不清是口径收窄还是资产损坏。

    只在**未命中路径**被调用；命中路径恒返回 ()（由 find_hit_sequence 保证）。
    """
    rest = norm[pos:]
    out: list = []
    seen: set = set()
    for cand in pack.assets:
        if cand.part_index == 0 and cand.rate_key == rate_key:
            continue          # 口径内的条目是候选，不算 blocked
        ident = f"{cand.key}@{cand.rate_key}#{cand.part_index}"
        if ident in seen:
            continue
        text_norm = normalize_text(cand.text)
        if text_norm and rest.startswith(text_norm):
            out.append(ident)
            seen.add(ident)
    return tuple(out)


def find_hit_sequence(pack, text: str, rate_key: str = "normal") -> SequenceHitResult:
    """序列命中查询：整段文本能否被包内 variant 的归一化文本**逐字覆盖**（docs/10 §10.7）。

    `blocked_by`（诊断留痕，§10.7 末节）：未命中时记录「文本能匹配、但因
    rate_key / part_index 口径被剔除」的候选标识（`"<key>@<rate_key>#<part_index>"`）；
    命中恒为空元组。它是**只进诊断、永不进命中**的近失留痕（docs/17 §三），
    不得影响 entries / miss_reason 的判定。

    算法（§10.7 冻结判据，逐条对应，不得另立）：
        1. norm = normalize_text(text)；空串 → 未命中（uncovered=""，不抛错）；
        2. 建可用候选索引（_sequence_candidates：part_index=0 + rate_key 匹配
           + confirm 复核通过）；
        3. 从 pos=0 起，取**能匹配 norm[pos:] 的最长**归一化文本（str.startswith，
           最长优先——"更长的完整句"优先于它的前缀句）；同长度多候选取索引内首条
           （顺序稳定，与 pick_by_text 同源）；找不到 → 未命中，
           uncovered = norm[pos:]（第一个覆盖不上的剩余）；
        4. 消费该段（pos += len(匹配文本)）并记录条目，重复 3 直到覆盖完毕；
        5. 命中：entries 按消费顺序、uncovered=""、miss_reason=None。

    红线（§10.7「不做什么」）：不做部分播放——任一段覆盖不上即**整体未命中**，
        entries 恒为空元组，绝不返回"命中了前几段"。不做近似匹配（无编辑距离/语义/
        去标点），不做段序重排/去重，不允许跨段吞并（每段必须是某条 variant 的
        **完整**归一化文本）。

    参数：
        pack:       只读资产包（assets.AssetPack）
        text:       上游整段文本（内部走 normalize_text 归一化）
        rate_key:   语速档（默认 normal）

    返回：
        SequenceHitResult（命中时 entries 非空、miss_reason 为 None、uncovered 为空串；
        未命中时 entries 为空元组、miss_reason 为 REASON_TEXT_NOT_PREBAKED、
        uncovered 指向断点起——**永不抛错、永不返回裸 None**，与 find_hit 同族）

    异常：
        TypeError: text 不是 str（与 normalize_text 同口径：不把 None 静默当空串）
    """
    norm = normalize_text(text)      # 类型护栏在这里：非 str → TypeError
    if not norm:
        # 空回复没有可覆盖的内容，按未命中处理（不猜、不凑；uncovered 与
        # blocked_by 都保持空——没有断点就无从起算）
        return SequenceHitResult(
            entries=(), mode=MODE_TEXT, text=text,
            miss_reason=REASON_TEXT_NOT_PREBAKED, uncovered="",
            blocked_by=(),
        )

    cands = _sequence_candidates(pack, rate_key)
    entries = []
    pos = 0
    while pos < len(norm):
        rest = norm[pos:]
        best = None                  # (匹配文本, 条目)
        for text_norm, entry in cands:
            if not rest.startswith(text_norm):
                continue
            # 严格大于才替换 → 同长度时保留**索引内首条**（顺序稳定，与
            # pick_by_text 的"退到索引里第一条"同源）；扫描继续到末尾，
            # 保证"更长的完整句"不会被更早出现的较短候选挡住
            if best is None or len(text_norm) > len(best[0]):
                best = (text_norm, entry)
        if best is None or not best[0]:
            # 覆盖不上（或候选退化为空段——_sequence_candidates 已排除，这里是
            # 第二道护栏：消费长度为 0 的匹配必须当场判未命中，不许空转）。
            # 整体未命中，带断点起的剩余（归一化空间）；blocked_by 记录「文本能
            # 匹配、但被 rate/part 口径剔除」的候选——只进诊断、永不进命中
            # （§10.7 末节），且不参与上面的 best 选择，不改判定。
            return SequenceHitResult(
                entries=(), mode=MODE_TEXT, text=text,
                miss_reason=REASON_TEXT_NOT_PREBAKED, uncovered=rest,
                blocked_by=_blocked_identifiers(pack, norm, rate_key, pos),
            )
        entries.append(best[1])
        pos += len(best[0])

    # 命中路径：blocked_by 恒为空元组（诊断留痕只在未命中时产生）
    return SequenceHitResult(
        entries=tuple(entries), mode=MODE_TEXT, text=text,
        miss_reason=None, uncovered="",
        blocked_by=(),
    )
