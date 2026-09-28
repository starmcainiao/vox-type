"""
assets.pack — 包结构定义 + 装载 + 校验

职责：定义 AssetPack / AssetEntry 数据结构，提供 load_pack / lookup / validate_pack API。
      资产包是只读数据层——runtime/ 只读它，永不写它。
不负责：不做音频合成（compiler/）、不做播放（runtime/）、不做命中判定。

关键语义：
  - load_pack：任一条不合格 → 抛 AssetPackError（不得跳过坏条目继续）
  - lookup：指纹不匹配 / 文件不存在 → 返回 None（由调用方决定降级）
  - validate_pack：返回问题列表（空列表=通过），用于 CLI vox verify

T27（查找热路径索引化，**语义零变化**）+ T27d（O(1) 索引 + 只读契约收口）：
  - lookup 的线性遍历改为**惰性身份索引**定位候选（首次 O(n) 一次），热路径 O(1)；
  - 指纹从"每次 lookup 重算"改为**预计算缓存**（惰性、一次 O(n)），且仅在
    `expected_text == entry.text` 时使用——`!=` 时仍走原路径重算（最严格，不推测）；
  - 文件存在性校验**保持每次实查、绝不缓存**：缓存会让"运行期文件被删"从未命中变命中，
    语义就变了。索引化后每次 lookup 的 stat 次数已从 O(n) 降到 O(1)。
  - 失效判断是**两道防线**（热路径都是 O(1)）：
      ① 签名 `(id(assets), len(assets))` → 拦"整体换列表 / 增删条目"，立即重建；
      ② 索引值 `(index, entry)` + 命中时 `assets[index] is entry` → 拦"同下标换对象"
        （`assets[i] = x`，身份键不变），命中时重建 + **只重试一次**，仍失配返回 None
        （fail-closed）。
    **不做**"索引 miss 时兜底重建"——那会把未命中路径变成 O(n)，回到 T27 第一轮。
  - **本包是只读产物**。上面两道防线是**容忍度**，不是"运行期可改 assets"的授权：
    它们**挡不住身份键迁移**（`assets[i]` 换成不同 key / part_index / rate_key 的条目，
    旧索引里已无正确键，`is` 校验无项可校），也**挡不住字段级原地修改**
    （`entry.text = ...`）。改 `assets` 的正确姿势是改后调
    `invalidate_lookup_caches()` 或新建 `AssetPack` 实例；不调则缓存可能陈旧，
    属**契约外行为，不保证**。历史：T27 第一轮用 O(n) 内容签名守卫（性能净失败）、
    T27b 放弃原地检测（既有测试红）、T27c 同实现但把契约外行为当能力（既有测试仍红），
    本实现取代三者。
  包格式 / API 契约 / 失败条件 / 返回值一律未动（见 AGENTS.md「实现变更记录（T27）」
  与「T27d」小节）。

T18（TTL / 失效，字段只增）：
  - `AssetEntry` 增两个**可选**字段 `ttl` / `invalid_at`（默认 None）；**旧包无这两个
    字段 = 永不过期**，既有行为零变化。
  - 判定保险丝放在**本层**：`lookup(..., now=None)` 对过期条目返回 `None`（与既有
    未命中同形）。这样**任何**消费方（含 adapters/framework_kefu 的钩子）不经 runtime
    就直接调 `pack.lookup` 时也自动受保护——过期资产不会经任何路径被播出。
  - 判据只有**一份**：公开纯函数 `is_expired(entry, now)`，`lookup` 与 runtime 共用。
  - `now` 一律**注入**（`datetime` 或 ISO 8601 字符串）；`now=None` 才取当前 UTC。
    评测与测试必须显式传 `now` 才能重放，不得依赖 sleep 等真实时间流逝。
"""

import json
import os
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from assets.fingerprint import fingerprint


# ---------------------------------------------------------------------------
# 1. 异常定义
# ---------------------------------------------------------------------------
class AssetPackError(Exception):
    """资产包校验/装载异常——所有校验失败统一抛出此异常。

    消息必须包含导致失败的具体值，以便上层定位问题。
    """


# ---------------------------------------------------------------------------
# 1b. 失效判定（T18：唯一判据，lookup 与 runtime 共用）
# ---------------------------------------------------------------------------
def _coerce_to_dt(value: Union[datetime, str]) -> datetime:
    """把时间值归一成 **UTC** `datetime`（内部辅助，不参与任何判定语义）。

    接受两种形态：`datetime` 实例（naive 一律视为 UTC）与 ISO 8601 字符串。
    字符串一律走 `datetime.fromisoformat`（零第三方依赖；`Z` 结尾由本函数归一成
    `+00:00`，因为标准库在该写法下不认 `Z`）。

    为什么必须归一到 UTC：manifest 里 `created_at` 用 `+00:00`、测试与业务侧常见
        `Z` 结尾，两者语义相同。不归一就会拿两个 aware datetime 相减时 TypeError，
        或把 naive 与 aware 混着比——那是「本该过期却判成没过期」的静默降级。

    参数：
        value: datetime 或 ISO 8601 字符串

    返回：
        UTC 时区的 datetime
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # naive 字符串（如 "2026-09-22T00:00:00"）：与 manifest 的时间戳同口径按 UTC 处理。
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_invalid_at(value: str) -> datetime:
    """解析并校验 `invalid_at`（**公开**）：必须是合法 ISO 8601 时间戳。

    与 `_coerce_to_dt` 的区别：这里是**装载期校验**——失败必须抛 `AssetPackError`
    并带实际值（编译期就把坏包拦下），而 `_coerce_to_dt` 是运行期的归一化。

    参数：
        value: invalid_at 字段值

    返回：
        UTC 时区的 datetime

    异常：
        AssetPackError: 非字符串 / 无法解析 / 缺时区且无法按 UTC 解释
    """
    if not isinstance(value, str) or not value.strip():
        raise AssetPackError(
            f"invalid_at 必须是非空 ISO 8601 字符串，实际值为 {value!r}"
        )
    try:
        return _coerce_to_dt(value)
    except ValueError as e:
        raise AssetPackError(
            f"invalid_at 不是合法的 ISO 8601 时间戳: {value!r}（{e}）"
        ) from e


def _ttl_seconds(ttl: Any) -> int:
    """校验 `ttl` 的取值域（**公开**）：正整数秒，否则抛 AssetPackError。

    注意 `bool` 是 `int` 的子类，必须显式排除——`ttl=true` 会被静默当成 1 秒，
    那就是「本该永不过期却 1 秒后失效」的静默降级。

    参数：
        ttl: ttl 字段值

    返回：
        校验通过的秒数

    异常：
        AssetPackError: 非整数 / 布尔 / 非正
    """
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise AssetPackError(
            f"ttl 必须是正整数秒，实际值为 {ttl!r}（类型 {type(ttl).__name__}）"
        )
    if ttl <= 0:
        raise AssetPackError(
            f"ttl 必须是正整数秒，实际值为 {ttl!r}"
        )
    return ttl


def is_expired(entry: "AssetEntry", now: Union[datetime, str, None] = None) -> bool:
    # WHY 字符串注解：AssetEntry 定义在本函数之后；Python < 3.14 会在模块导入时**立即求值**注解
    #     （3.14 起 PEP 649 延迟求值，故本机 3.14 掩盖了这个 NameError，CI 的 3.12/3.13 直接炸）。
    """判定一个资产条目在 `now` 时刻是否已过期（**公开纯函数，T18 的唯一判据**）。

    语义（冻结）：`now >= invalid_at` → 过期（**恰好等于失效时刻也算过期**）。

    口径要点：
      - **无 `invalid_at` 恒返回 False**（旧包 / 旧源永不过期）——向后兼容的唯一锚点。
      - 判定**只看 `invalid_at`**，不看 `ttl`。`ttl` 是相对时长，已由 compiler 在预铸
        时刻折算成 `invalid_at` 落进 manifest；这里不做二次折算，避免两处各算一份。
      - `now` 支持**显式注入**（`datetime` 或 ISO 8601 字符串）；`now=None` 才取当前
        UTC 时间。评测与测试必须注入 `now` 才能重放。
      - 纯函数：不读时钟以外的任何状态、不改 `entry`、不查文件，可直接单测。

    参数：
        entry: 资产条目（AssetEntry）
        now:   判定时刻，None = 当前 UTC 时间

    返回：
        True = 已过期（消费方必须拒播）
    """
    invalid_at = getattr(entry, "invalid_at", None)
    if not isinstance(invalid_at, datetime):
        return False

    if now is None:
        now_dt = datetime.now(timezone.utc)
    else:
        now_dt = _coerce_to_dt(now)
    return now_dt >= invalid_at


# ---------------------------------------------------------------------------
# 2. 数据结构（包格式冻结——对应 AGENTS.md 中的 JSON schema）
# ---------------------------------------------------------------------------
@dataclass
class AssetEntry:
    """单条预铸资产：key + 分片索引 + 语速 + 变体 → 文本 + 指纹 + 音频路径 + 时长。

    属性：
        key:           话术唯一标识
        part_index:    分片序号（从 0 开始）
        rate_key:      语速档位（slow / normal / fast）
        variant:       变体编号（整数）
        text:          话术文本内容
        fingerprint:   16 位 hex 指纹（预铸时计算）
        path:          相对于包根目录的音频文件路径
        duration_ms:   音频时长（毫秒）
        ttl:           原始相对失效时长（秒，int），**仅审计留痕**——
                       预铸时已由 compiler 折算成 invalid_at；判定只看 invalid_at
        invalid_at:    绝对失效时刻（UTC datetime）；None = 永不过期
    """
    key: str
    part_index: int
    rate_key: str
    variant: int
    text: str
    fingerprint: str
    path: str
    duration_ms: int
    ttl: Optional[int] = None
    invalid_at: Optional[datetime] = None


@dataclass
class AssetPack:
    """完整资产包：manifest 元数据 + 资产条目列表。

    属性（公开契约，冻结）：
        pack_id:          包唯一标识
        pack_version:     包版本号
        protocol_version: 协议版本号
        ruleset_version:  规则集版本号
        voice:            默认音色
        model_version:    模型版本号
        created_at:       创建时间（ISO 8601）
        assets:           资产条目列表
        root:             包根目录路径（用于 lookup 时校验文件存在性）

    T27 追加的私有属性（`_identity_index` / `_text_fingerprints` /
    `_fingerprint_cache_built` / `_assets_token`）是 lookup 热路径的惰性缓存，
    **非公开契约**——调用方不得读取或写入。它们用 `InitVar` 接入 `__post_init__`，
    因此**不出现在 dataclass 字段清单与构造签名里**：`AssetPack(...)` 的参数清单、
    顺序、默认值与 `load_pack` 的校验强度一字未动。
    """

    pack_id: str
    pack_version: str
    protocol_version: str
    ruleset_version: str
    voice: str
    model_version: str
    created_at: str
    assets: List[AssetEntry] = field(default_factory=list)
    root: Optional[Path] = None

    def __post_init__(
        self,
        _identity_index: InitVar[
            Optional[Dict[tuple, Tuple[Optional[int], Optional[AssetEntry]]]]
        ] = None,
        _text_fingerprints: InitVar[Optional[Dict[tuple, str]]] = None,
        _fingerprint_cache_built: InitVar[bool] = False,
        _assets_token: InitVar[Optional[List[tuple]]] = None,
    ) -> None:
        # WHY 用 InitVar 而不是普通字段：缓存必须**不进公开契约**——
        # InitVar 既不出现在 dataclass 字段清单（`dataclasses.fields`）里，
        # 也不进构造签名、不进 repr、不参与相等性比较。
        # 四个 InitVar 全部有默认值，`AssetPack(八个公开字段)` 的位置/关键字
        # 传参方式与「改前」完全一致。传非 None 只作一次初始化，之后由
        # lookup 按需重建，不构成可写契约。
        self._identity_index = _identity_index if _identity_index is not None else {}
        self._text_fingerprints = (
            _text_fingerprints if _text_fingerprints is not None else {}
        )
        self._fingerprint_cache_built = bool(_fingerprint_cache_built)
        # 令牌为 None = "尚未构建缓存"；构建缓存时钉成当前 assets 的 **O(1) 签名**
        # （`(id(assets), len(assets))`，见 `_assets_signature`）。
        # 注意：这个令牌**只**拦"换列表 / 长度变化"两类；同下标换对象由索引值的
        # `(index, entry)` 身份校验兜住（见 `_entry_still_at`）。
        self._assets_token = _assets_token


    def invalidate_lookup_caches(self) -> None:
        """使两个派生缓存失效，下一次 lookup 时按**当前** assets 重建。

        语义边界：本包是**只读**数据层（契约规定永不被写入），正常运行期不会有人
        改 assets。生产代码若确需改动 assets，请显式调用本方法。

        不需要主动清缓存也能自动跟上的是**两类**改动（两道防线，全部自动）：
          ① **整体换列表 / 增删条目**（`pack.assets = [...]` / `append` / `pop` /
             `del`）——改变 O(1) 签名（`_assets_signature`），下一次 lookup 立即重建；
          ② **同下标换对象**（`assets[i] = x`，身份键不变）——签名不变，但索引值是
             `(index, entry)`，命中时 `pack.assets[index] is entry` 对不上 → 重建
             + 只重试一次（见 `lookup` 与 `_entry_still_at`）。
        本方法覆盖的**不可替代**情形：
          - **身份键迁移**（`assets[i] = x`，x 的 key / part_index / rate_key 变了）
            ——旧索引里已无正确键，`is` 校验无项可校，**两道防线都看不见**；
          - **原地改字段值**（`entry.text = ...` / `entry.fingerprint = ...`）——
            不换对象、不换长度，两道防线都看不见。
        上面两类属于**契约外行为**（本包是只读产物）；需要它们成立时必须显式调本方法，
        推荐改为新建 `AssetPack` 实例（`dataclasses.replace(pack, assets=...)`）。

        不缓存的东西：文件存在性（stat）与音频内容——不缓存是保险丝（见 lookup）。

        返回：None
        """
        # WHY 就地 clear 而不是赋新对象：_identity_lookup_index() 按**引用**返回
        # 缓存本体，调用方手里那份旧引用同样需要失效；赋新对象会让旧引用继续装着
        # 已不在 assets 里的条目。
        self._identity_index.clear()
        self._text_fingerprints.clear()
        self._fingerprint_cache_built = False
        self._assets_token = None   # 显式标记"未构建"，下一次 lookup 必然重建

    @staticmethod
    def _assets_signature(assets: List[AssetEntry]) -> Tuple[int, int]:
        """`assets` 的 **O(1)** 签名：`(id(assets), len(assets))`。

        为什么是 O(1) 而不是逐条目取 `id()`：逐条目的内容签名是 O(n)，
        在大包上比"改前"的线性遍历本身还贵（`labs/pack-index-bench/report.json`
        T27 第一轮实测：n=10k 时守卫 233.8 µs vs 线性遍历 60.7 µs）。本签名把每次
        `lookup` 的失效判定压回常数开销。

        它**只**拦住两类改动：
          ① 整体换列表（`pack.assets = [...]`）—— `id` 变了；
          ② 增删条目（`append` / `pop` / `del` / `assets[3:5] = ...`）—— `len` 变了。
        **抓不住**同下标换对象（`assets[i] = x`）——那既不换列表也不改长度。
        这正是索引值必须自带 `index` 的原因：`lookup` 命中后用
        `pack.assets[index] is entry`（O(1)）自己核一次身份，对不上就重建
        （见 `_entry_still_at`）。**也抓不住**身份键迁移与字段级原地修改——
        那两类属契约外行为，需显式调 `invalidate_lookup_caches()`。

        不比较任何字段值：`AssetEntry` 是可变 dataclass，字段级比较既贵又会对
        调用方改字段值产生假阴性。
        """
        return (id(assets), len(assets))

    def _ensure_lookup_caches(self) -> None:
        """按需重建两个派生缓存（第一道防线：O(1) 签名）。

        触发条件（都走一次 O(n) 构建）：
          ① `_assets_token is None` —— 尚未构建，或刚被 `invalidate_lookup_caches`
             清掉（必须重建，不能因为"令牌恰好等于当前签名"就跳过）；
          ② 令牌不等于当前 O(1) 签名 —— assets 被整体换列表，或增删了条目。

        **本方法故意不检测**同下标换对象（`assets[i] = x`）：那是 O(1) 签名的
        已知盲区，由第二道防线兜住——`lookup` 命中后用 `pack.assets[index] is
        entry`（见 `_entry_still_at`）核身份，对不上就在这里重建 + 只重试一次。

        本包是**只读产物**：这两道防线是容忍度，不是"运行期可改 assets"的授权。
        身份键迁移与字段级原地修改两道防线都看不见，需要显式调
        `invalidate_lookup_caches()` 或新建 `AssetPack` 实例——不调则缓存可能
        陈旧，属契约外行为，不保证。

        最坏代价：索引重建的 O(n) **只在检测到变化时**发生；稳态热路径是
        一次 O(1) 签名比对 + 一次 O(1) 查表。
        """
        signature = self._assets_signature(self.assets)
        if self._assets_token is not None and self._assets_token == signature:
            return
        self._assets_token = signature
        self._identity_index.clear()
        self._rebuild_identity_index()
        self._rebuild_text_fingerprints()

    def _identity_lookup_index(self) -> Dict[tuple, Tuple[int, AssetEntry]]:
        """惰性身份索引：`(key, part_index, rate_key, variant) → (index, entry)`。

        确定性：**首条优先**（`setdefault`）——与"线性遍历取首条"完全一致。
        重复身份在 load_pack 层已被拒绝，这里只为手工构造的包兜住顺序语义。

        值为什么是 `(index, entry)` 而不是裸 `entry`：索引是只增缓存，而 O(1)
        签名（`_assets_signature`）抓不住**同下标换对象**（`assets[i] = x`）——
        那种改动下列表对象没换、长度没变，签名完全一样，索引会继续指着被替换
        掉的旧对象。带上构建时的下标，`lookup` 就能用 `pack.assets[index] is
        entry` 花 O(1) 核出"这条还在原位吗"，对不上就重建（第二道防线）。

        语义：**不过滤任何条目**。指纹不符、文件已删的条目照常在索引里，由后续
        步骤按原判据各自返回 None。索引只做寻址，不做判定（否则会把校验变成
        对装载期的隐式依赖，改变 lookup 的失败条件）。

        成本：首次 O(n)；之后每次调用只付一次 O(1) 签名比对 + O(1) 查表。
        注意这**不是**文件存在性的缓存 —— stat 仍在 lookup 里每次实查。

        返回：身份 → (index, 条目) 的字典（内部可变缓存；不要外传）
        """
        self._ensure_lookup_caches()
        return self._identity_index

    def _rebuild_identity_index(self) -> None:
        """按包内顺序重建身份索引（首条优先），值带构建时的下标。

        抽出成独立方法：lookup 的指纹分支也需要在缓存失效时重建索引，
        两处共用同一份构建逻辑，避免两个副本各自演化。
        """
        for idx, entry in enumerate(self.assets):
            identity = (
                entry.key, entry.part_index, entry.rate_key, entry.variant
            )
            self._identity_index.setdefault(identity, (idx, entry))

    def _rebuild_text_fingerprints(self) -> None:
        """按当前 `assets` 全表预计算指纹（身份 → 指纹）。

        必须与身份索引**同时**重建：两个缓存都从 `assets` 派生，只重建一个
        会让它们指向不同的版本，lookup 的指纹比对就会失真。

        口径与 lookup 的重算路径逐字一致；值等于条目自己的 `fingerprint`
        （load_pack 已保证一致），但这里**不读** `entry.fingerprint` 而是重算
        ——这样即使有手工构造的包把 `fingerprint` 字段写错，快路径也不会把
        错值当成判据（与 lookup 第 2 步的判据保持同一来源）。

        与身份索引**同批**重建保证「只有 `expected_text == entry.text` 才走
        快路径」的语义不变：指纹键是身份四元组，而 `lookup` 在取用前已用
        `(index, entry)` 核过候选身份，两条缓存指向同一个列表版本——
        陈旧指纹不会导致误判。
        """
        cache = self._text_fingerprints
        cache.clear()
        self._fingerprint_cache_built = False
        for item in self.assets:
            identity = (
                item.key, item.part_index, item.rate_key, item.variant
            )
            cache[identity] = fingerprint(
                text=item.text,
                voice=self.voice,
                rate_value=item.rate_key,
                model_version=self.model_version,
            )
        self._fingerprint_cache_built = True

    def _entry_still_at(self, idx: Optional[int], entry: Optional[AssetEntry]) -> bool:
        """O(1) 身份校验：`assets[idx]` 还是不是当年索引里那条 `entry`。

        两道条件必须**同时**满足才算"还在原位"：
          ① `0 <= idx < len(self.assets)` —— 下标越界一律视为失配（列表被裁短、
             或被换成一个更短的列表时，旧下标不再指向同一条目）；
          ② `self.assets[idx] is entry` —— **对象同一性**，不是相等性。`AssetEntry`
             是可变 dataclass，两条字段全同的条目用 `==` 会判等，但它们是
             **不同的对象**：把 `assets[i]` 原地换成语义相同的新对象时，"索引里
             那条"已经不是列表里那条了，必须算失配（否则就是静默降级）。

        这是第二道防线：它补上 O(1) 签名抓不住的"同下标换对象"。
        **已知盲区（契约外行为，不保证）**：它只校验**身份键已存在**的项——若原地
        改动把某条目的身份键本身换掉（`key` / `part_index` / `rate_key` 改了），
        旧索引里已无正确键，`lookup` 拿到的是 `candidate is None`，本校验
        **不会被调用**，缓存会保持陈旧。要那种保证请显式调
        `invalidate_lookup_caches()` 或新建 `AssetPack` 实例。
        """
        if idx is None or entry is None:
            return False
        if not 0 <= idx < len(self.assets):
            return False
        return self.assets[idx] is entry

    def _text_fingerprint(self, identity: tuple, entry: AssetEntry) -> str:
        """身份对应的**预计算**指纹（惰性，全表一次 O(n)，命中后 O(1)）。

        口径与 lookup 的重算路径逐字一致：`text=entry.text`、`voice=self.voice`、
        `rate_value=entry.rate_key`、`model_version=self.model_version`
        （指纹四要素的 voice / model_version 取包级，格式冻结里没有条目级字段）。

        为什么按 identity 而不是按条目对象做键：`AssetEntry` 是可变 dataclass，
        不可哈希，做 dict 键会直接 TypeError。按四元组键还有一个额外好处——
        重复身份时 `setdefault` 天然落到先出现的那条，与身份索引的取首条一致。
        预计算的是"条目自己文本的指纹"，不是某个 expected_text 的指纹——
        只有 `expected_text == entry.text` 时才允许用它。

        身份不在缓存里时**不重建整表**，直接按同口径重算该条目的指纹：条目不在
        缓存里说明它不是本次缓存构建时的包内条目，走重算与「改前」行为一致，
        且**不写回**缓存（避免一条外部条目把后续真条目的指纹顶掉）。
        """
        self._ensure_lookup_caches()
        cache = self._text_fingerprints
        cached = cache.get(identity)
        if cached is not None:
            return cached
        return fingerprint(
            text=entry.text,
            voice=self.voice,
            rate_value=entry.rate_key,
            model_version=self.model_version,
        )


    def lookup(
        self,
        key: str,
        part_index: int,
        rate_key: str,
        variant: int,
        expected_text: str,
        engine_meta: Optional[Dict[str, Any]] = None,
        now: Union[datetime, str, None] = None,
    ) -> Optional[AssetEntry]:
        """在包中查找匹配的资产条目。

        匹配条件：key + part_index + rate_key + variant + 指纹校验 + **未过期**。
        指纹不匹配 / 文件不存在 / 已过期 → 返回 None（由调用方决定降级；这里只做判定，不做降级）。

        参数：
            key:           话术 key
            part_index:    分片序号
            rate_key:      语速档位（slow/normal/fast）
            variant:       变体编号
            expected_text: 期望的话术文本（用于指纹校验）
            engine_meta:   引擎元数据（保留字段，暂不使用）
            now:           失效判定时刻（T18）。None = 当前 UTC 时间；
                           显式传入（datetime 或 ISO 8601 字符串）用于评测/测试重放

        返回：
            匹配的 AssetEntry 或 None（未命中/指纹不匹配/文件不存在/已过期）
        """
        # 1. 定位候选：身份索引（T27 惰性派生，值带构建时下标）。索引按包内顺序
        #    "首条优先"构建，与原线性遍历取首条完全一致。注意索引只**寻址**，不做
        #    任何判定——指纹不符、文件已删的条目依然在索引里，由第 2 / 3 步按原
        #    判据返回 None。
        #    两道防线（热路径都是 O(1)）：
        #      ① 取索引前核对 O(1) 签名（`_ensure_lookup_caches`）——拦"换列表 /
        #         长度变化"；
        #      ② 命中后核身份（`_entry_still_at`）——拦"同下标换对象"。
        #    **不做**"索引 miss 时兜底重建"：那会把未命中路径变成 O(n)（T27 第一
        #    轮的教训），而契约外行为不该由产品付代价。
        identity = (key, part_index, rate_key, variant)
        idx, candidate = self._identity_lookup_index().get(
            identity, (None, None))

        if candidate is None:
            return None

        # 1b. 索引身份自校验（第二道防线）+ **只重试一次**。
        # WHY 必须校验：O(1) 签名抓不住 `assets[i] = x`（同下标换对象），索引里那条
        #       可能已被替换，直接返回它会是一条"不在 assets 里"的条目——静默降级的
        #       破口。
        # WHY 只重试一次：重建索引本身就可能再次与列表失配（病态的持续原地改动）。
        #       此时**不猜**——返回 None（fail-closed），由调用方按原判据降级；
        #       无限重试既会死循环，也会把"对不上"这个事实吞掉。
        if not self._entry_still_at(idx, candidate):
            self.invalidate_lookup_caches()
            self._ensure_lookup_caches()          # 按**当前** assets 重建
            idx, candidate = self._identity_index.get(identity, (None, None))
            if (
                candidate is None
                or not self._entry_still_at(idx, candidate)
            ):
                return None                       # 重建后仍对不上 → fail-closed

        # 1c. 失效判定（T18，保险丝放在本层）。
        # WHY 在资产层而不是 runtime：本层是所有消费方的唯一入口——runtime 的
        #       Executor 与 adapters/framework_kefu 的钩子都直接调 `pack.lookup`，
        #       判定放在这里，过期条目对**任何**调用方都不可能再被取出。
        #       返回值与既有未命中**同形**（None），所以调用方零改动即受保护。
        # 判据只有 `is_expired` 一份（公开纯函数），runtime 复用同一个，
        #       避免两处各写一份导致口径漂移。
        # 放在指纹/文件校验之前：过期是"不该播"，优先级高于"包是否完好"。
        if is_expired(candidate, now):
            return None

        # 2. 指纹校验——用 expected_text 重算指纹并与条目指纹比对。
        # WHY：即使 key/part/rate/variant 全部匹配，文本内容变了也必须视为未命中，
        #       否则会念错版本，这是静默降级的根源。
        if expected_text == candidate.text:
            # 指纹快路径（T27）：text 相等 → 与**预计算**的指纹比对，省掉这次 sha256。
            # 语义等价：fingerprint 是纯函数，fingerprint(candidate.text) 与下面重算的
            # expected_fp 逐字同参，比对对象与判据都没变。
            expected_fp = self._text_fingerprint(identity, candidate)
        else:
            # expected_text != entry.text → **仍走原路径重算**（不做任何推测）。
            # 在这里改判成"直接 None"或"直接放行"都会改变 lookup 的失败条件，不许可。
            expected_fp = fingerprint(
                text=expected_text,
                voice=self.voice,
                rate_value=rate_key,
                model_version=self.model_version,
            )
        if candidate.fingerprint != expected_fp:
            return None

        # 3. 音频文件存在性校验——**每次实查，绝不缓存**（T04 的保险丝）。
        # WHY 不缓存：缓存会让"运行期文件被删"从"未命中"变成"命中"，语义就变了。
        # 索引化后每次 lookup 的 stat 次数已从 O(n) 降到 O(1)，收益已足够。
        if self.root is not None:
            audio_path = self.root / candidate.path
            if not audio_path.exists():
                return None

        return candidate


# ---------------------------------------------------------------------------
# 3. manifest 必填字段清单
# ---------------------------------------------------------------------------
# WHY：格式冻结——这些字段必须存在且非空，缺任何一个 = 包损坏。
MANIFEST_REQUIRED_FIELDS: frozenset = frozenset({
    "pack_id",
    "pack_version",
    "protocol_version",
    "ruleset_version",
    "voice",
    "model_version",
    "created_at",
})

# 单条资产必填字段
ASSET_REQUIRED_FIELDS: frozenset = frozenset({
    "key",
    "part_index",
    "rate_key",
    "variant",
    "text",
    "fingerprint",
    "path",
    "duration_ms",
})

# 用户数据字段黑名单——包内不得含用户隐私数据
# WHY：本层只存话术模板，槽值/用户录音/电话号码等属于运行时数据，入包 = 违规。
USER_DATA_BLACKLIST: frozenset = frozenset({
    "slots",
    "phone",
    "user_utterance",
})

# 合法的 rate_key 取值
VALID_RATE_KEYS: frozenset = frozenset({"slow", "normal", "fast"})


# ---------------------------------------------------------------------------
# 4. 内部校验辅助函数
# ---------------------------------------------------------------------------
def _check_user_data_fields(data: Dict[str, Any], context: str) -> None:
    """检查字典中是否含有用户数据黑名单字段。

    参数：
        data:    待检查的字典
        context: 上下文描述（用于错误信息定位）

    异常：
        AssetPackError: 发现用户数据字段
    """
    found = USER_DATA_BLACKLIST & data.keys()
    if found:
        raise AssetPackError(
            f"{context} 中发现用户数据字段: {sorted(found)}——"
            f"包内不得含用户隐私数据（slots/phone/user_utterance）"
        )


def _check_duplicate_identities(raw_assets: List[Dict[str, Any]]) -> None:
    """检查资产条目列表中是否存在重复的 (key, part_index, rate_key, variant) 身份。

    寻址身份是 (key, part_index, rate_key, variant)。当同一身份出现两条时，
    lookup 只会命中先出现的那条，新条目永远查不到——正是「本该命中却换了路径」
    且零留痕的原型，必须拦截。

    参数：
        raw_assets: 原始资产条目列表（来自 JSON）

    异常：
        AssetPackError: 存在重复身份（消息含冲突 key 与坐标）
    """
    seen: Dict[tuple, int] = {}
    for i, raw in enumerate(raw_assets):
        if not isinstance(raw, dict):
            continue
        identity = (
            raw.get("key"),
            raw.get("part_index"),
            raw.get("rate_key"),
            raw.get("variant"),
        )
        if identity in seen:
            raise AssetPackError(
                f"资产条目 #{i} 与 #{seen[identity]} 身份重复: "
                f"(key={identity[0]!r}, part_index={identity[1]!r}, "
                f"rate_key={identity[2]!r}, variant={identity[3]!r})"
            )
        seen[identity] = i


def _validate_asset_path_str(path_str: Any, index: int, root: Path) -> None:
    """校验资产条目的 path 字段：必须是字符串、相对路径、不逃逸出包根。

    参数：
        path_str: path 字段值
        index:    条目序号（用于错误信息）
        root:     包根目录路径

    异常：
        AssetPackError: path 不合法
    """
    if not isinstance(path_str, str):
        raise AssetPackError(
            f"资产条目 #{index} 的 path 必须是字符串，"
            f"实际类型为 {type(path_str).__name__}"
        )
    p = Path(path_str)
    if p.is_absolute():
        raise AssetPackError(
            f"资产条目 #{index} 的 path 必须是相对路径，"
            f"实际值为 {path_str!r}（绝对路径不被允许）"
        )
    normalized = (root / p).resolve()
    root_resolved = root.resolve()
    if not str(normalized).startswith(str(root_resolved) + os.sep) and normalized != root_resolved:
        raise AssetPackError(
            f"资产条目 #{index} 的 path 逃逸出包根: {path_str!r}"
        )


def _validate_asset_entry(
    raw: Dict[str, Any],
    index: int,
    root: Path,
    manifest_voice: str = "",
    manifest_model_version: str = "",
) -> AssetEntry:
    """校验单条资产条目并转为 AssetEntry。

    校验项：
      1. 必填字段完整性
      2. rate_key 合法性（slow/normal/fast）
      3. part_index / variant / duration_ms 类型正确
      4. 指纹与重算指纹一致（防篡改）
      5. 音频文件存在性（path 相对于包根目录）

    参数：
        raw:   原始字典（来自 JSON）
        index: 条目序号（用于错误信息定位）
        root:  包根目录路径

    返回：
        校验通过的 AssetEntry 实例

    异常：
        AssetPackError: 任何校验失败
    """
    # 1. 必填字段检查
    missing = ASSET_REQUIRED_FIELDS - raw.keys()
    if missing:
        raise AssetPackError(
            f"资产条目 #{index} 缺少必填字段: {sorted(missing)}"
        )

    # 2. 用户数据黑名单检查
    _check_user_data_fields(raw, f"资产条目 #{index}")

    # 3. rate_key 合法性
    rate_key = raw["rate_key"]
    if rate_key not in VALID_RATE_KEYS:
        raise AssetPackError(
            f"资产条目 #{index} 的 rate_key 必须是 'slow'/'normal'/'fast' 之一，"
            f"实际值为 '{rate_key}'"
        )

    # 4. 类型检查
    part_index = raw["part_index"]
    if not isinstance(part_index, int):
        raise AssetPackError(
            f"资产条目 #{index} 的 part_index 必须是整数，"
            f"实际类型为 {type(part_index).__name__}"
        )

    variant = raw["variant"]
    if not isinstance(variant, int):
        raise AssetPackError(
            f"资产条目 #{index} 的 variant 必须是整数，"
            f"实际类型为 {type(variant).__name__}"
        )

    duration_ms = raw["duration_ms"]
    if not isinstance(duration_ms, int) or duration_ms < 0:
        raise AssetPackError(
            f"资产条目 #{index} 的 duration_ms 必须是非负整数，"
            f"实际值为 {duration_ms!r}"
        )

    # 5. 指纹校验——重算指纹并与包内值比对
    # WHY：指纹不一致 = 话术被改但资产没重铸，这是静默降级的根源，必须拦截。
    # 指纹四要素中的 voice / model_version 一律取 manifest 级——格式冻结里没有条目级字段。
    expected_fp = fingerprint(
        text=raw["text"],
        voice=manifest_voice,
        rate_value=rate_key,
        model_version=manifest_model_version,
    )
    actual_fp = raw["fingerprint"]
    if actual_fp != expected_fp:
        raise AssetPackError(
            f"资产条目 #{index} (key={raw['key']!r}) 指纹不匹配: "
            f"包内={actual_fp!r}, 重算={expected_fp!r}"
        )

    # 6. path 安全性检查——必须是相对路径且不逃逸出包根
    # WHY：绝对路径或 .. 逃逸会导致资产指向包外文件，迁移/换包后静默指向错误文件。
    _validate_asset_path_str(raw["path"], index, root)

    # 7. 音频文件存在性检查
    # WHY：运行时 lookup 返回了路径但文件不存在 = 必然失败，装载时应尽早发现。
    audio_path = root / raw["path"]
    if not audio_path.exists():
        raise AssetPackError(
            f"资产条目 #{index} (key={raw['key']!r}) 的音频文件不存在: {audio_path}"
        )

    # 8. 失效字段（T18，可选——缺省 = 永不过期，旧包行为零变化）
    # WHY 在此校验而不是等到 lookup：装载期放行坏值，运行期就会在热路径上抛
    #     TypeError（字符串与 datetime 不可比），那是「本该报错却换了失败形态」。
    invalid_at = None
    if "invalid_at" in raw:
        invalid_at = parse_invalid_at(raw["invalid_at"])
    ttl = None
    if "ttl" in raw:
        ttl = _ttl_seconds(raw["ttl"])

    return AssetEntry(
        key=raw["key"],
        part_index=part_index,
        rate_key=rate_key,
        variant=variant,
        text=raw["text"],
        fingerprint=actual_fp,
        path=raw["path"],
        duration_ms=duration_ms,
        ttl=ttl,
        invalid_at=invalid_at,
    )


# ---------------------------------------------------------------------------
# 5. 装载 API
# ---------------------------------------------------------------------------
def load_pack(root: Path) -> AssetPack:
    """装载资产包：读 manifest.json → 逐条校验资产 → 返回 AssetPack。

    校验失败（缺 manifest / 缺必填字段 / 指纹不匹配 / 文件不存在）
    → 抛 AssetPackError，不得跳过坏条目继续。

    参数：
        root: 资产包根目录路径

    返回：
        校验通过的 AssetPack 实例

    异常：
        AssetPackError: manifest 校验失败或任一资产条目校验失败
    """
    root = Path(root)

    # 1. 读取 manifest.json
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise AssetPackError(f"资产包根目录 {root} 中缺少 manifest.json")

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except UnicodeDecodeError as e:
        raise AssetPackError(f"manifest.json 编码错误（非 UTF-8）: {e}")
    except json.JSONDecodeError as e:
        raise AssetPackError(f"manifest.json 不是合法 JSON: {e}")

    if not isinstance(manifest, dict):
        raise AssetPackError(
            f"manifest.json 必须是 JSON 对象，实际类型为 {type(manifest).__name__}"
        )

    # 2. manifest 必填字段检查
    missing = MANIFEST_REQUIRED_FIELDS - manifest.keys()
    if missing:
        raise AssetPackError(
            f"manifest.json 缺少必填字段: {sorted(missing)}"
        )

    # 3. manifest 用户数据黑名单检查
    _check_user_data_fields(manifest, "manifest.json")

    # 4. 逐条校验资产条目
    raw_assets = manifest.get("assets", [])
    if not isinstance(raw_assets, list) or len(raw_assets) == 0:
        raise AssetPackError("manifest.json 中 assets 必须是非空列表")

    # 5. 重复身份检查
    # WHY：同一 (key, part_index, rate_key, variant) 出现两次时 lookup 只命中先出现的，
    #       新条目永远查不到——是「本该命中却换了路径」且零留痕的原型。
    _check_duplicate_identities(raw_assets)

    entries: List[AssetEntry] = []
    for i, raw_entry in enumerate(raw_assets):
        if not isinstance(raw_entry, dict):
            raise AssetPackError(
                f"资产条目 #{i} 必须是字典，实际类型为 {type(raw_entry).__name__}"
            )
        entry = _validate_asset_entry(
            raw_entry, i, root,
            manifest_voice=manifest.get("voice", ""),
            manifest_model_version=manifest.get("model_version", ""),
        )
        entries.append(entry)

    # 6. 构造 AssetPack 实例
    return AssetPack(
        pack_id=manifest["pack_id"],
        pack_version=manifest["pack_version"],
        protocol_version=manifest["protocol_version"],
        ruleset_version=manifest["ruleset_version"],
        voice=manifest["voice"],
        model_version=manifest["model_version"],
        created_at=manifest["created_at"],
        assets=entries,
        root=root,
    )


# ---------------------------------------------------------------------------
# 6. lookup API（薄委托——保留模块级接口以兼容现有调用）
# ---------------------------------------------------------------------------
def lookup(
    pack: AssetPack,
    key: str,
    part_index: int,
    rate_key: str,
    variant: int,
    expected_text: str,
    engine_meta: Optional[Dict[str, Any]] = None,
    now: Union[datetime, str, None] = None,
) -> Optional[AssetEntry]:
    """在包中查找匹配的资产条目（委托 AssetPack.lookup 方法）。

    参数：
        pack:          已装载的 AssetPack 实例
        key:           话术 key
        part_index:    分片序号
        rate_key:      语速档位（slow/normal/fast）
        variant:       变体编号
        expected_text: 期望的话术文本（用于指纹校验）
        engine_meta:   引擎元数据（保留字段，暂不使用）
        now:           失效判定时刻（None = 当前 UTC；显式传入用于评测/测试重放）

    返回：
        匹配的 AssetEntry 或 None（未命中/指纹不匹配/文件不存在/已过期）
    """
    return pack.lookup(
        key=key,
        part_index=part_index,
        rate_key=rate_key,
        variant=variant,
        expected_text=expected_text,
        engine_meta=engine_meta,
        now=now,
    )


# ---------------------------------------------------------------------------
# 7. validate API（CLI vox verify 用）
# ---------------------------------------------------------------------------
def validate_pack(root: Path) -> List[str]:
    """校验资产包，返回问题列表（空列表=通过）。

    用于 CLI vox verify 命令——不抛异常，返回可读的问题列表。
    校验内容：manifest 完整性 + 资产条目合法性 + 指纹一致性 + 文件存在性
    + 用户数据黑名单 + 重复身份 + path 安全性。

    参数：
        root: 资产包根目录路径

    返回：
        问题列表（空列表表示校验通过）
    """
    root = Path(root)
    issues: List[str] = []

    # 1. manifest.json 存在性
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        issues.append("缺少 manifest.json")
        return issues

    # 2. manifest.json 合法性
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except UnicodeDecodeError as e:
        issues.append(f"manifest.json 编码错误（非 UTF-8）: {e}")
        return issues
    except json.JSONDecodeError as e:
        issues.append(f"manifest.json 不是合法 JSON: {e}")
        return issues

    if not isinstance(manifest, dict):
        issues.append(
            f"manifest.json 必须是 JSON 对象，实际类型为 {type(manifest).__name__}"
        )
        return issues

    # 3. manifest 必填字段
    missing = MANIFEST_REQUIRED_FIELDS - manifest.keys()
    if missing:
        issues.append(f"manifest.json 缺少必填字段: {sorted(missing)}")

    # 4. manifest 用户数据黑名单
    found_user = USER_DATA_BLACKLIST & manifest.keys()
    if found_user:
        issues.append(
            f"manifest.json 中发现用户数据字段: {sorted(found_user)}"
        )

    # 5. 资产条目列表
    raw_assets = manifest.get("assets", [])
    if not isinstance(raw_assets, list):
        issues.append(
            f"manifest.json 中 assets 必须是列表，实际类型为 {type(raw_assets).__name__}"
        )
        return issues

    if len(raw_assets) == 0:
        issues.append("manifest.json 中 assets 为空列表")
        return issues

    # 6. 重复身份检查
    try:
        _check_duplicate_identities(raw_assets)
    except AssetPackError as e:
        issues.append(str(e))

    # 7. 逐条校验资产条目
    for i, raw_entry in enumerate(raw_assets):
        if not isinstance(raw_entry, dict):
            issues.append(f"资产条目 #{i} 必须是字典，实际类型为 {type(raw_entry).__name__}")
            continue

        # 7a. 必填字段
        missing_asset = ASSET_REQUIRED_FIELDS - raw_entry.keys()
        if missing_asset:
            issues.append(f"资产条目 #{i} 缺少必填字段: {sorted(missing_asset)}")
            continue  # 字段不全，后续校验无意义

        # 7b. 用户数据黑名单
        found_user_entry = USER_DATA_BLACKLIST & raw_entry.keys()
        if found_user_entry:
            issues.append(
                f"资产条目 #{i} 中发现用户数据字段: {sorted(found_user_entry)}"
            )

        # 7c. rate_key 合法性
        if raw_entry["rate_key"] not in VALID_RATE_KEYS:
            issues.append(
                f"资产条目 #{i} 的 rate_key 非法: '{raw_entry['rate_key']}'"
            )

        # 7d. 类型检查
        if not isinstance(raw_entry["part_index"], int):
            issues.append(
                f"资产条目 #{i} 的 part_index 必须是整数，"
                f"实际类型为 {type(raw_entry['part_index']).__name__}"
            )
        if not isinstance(raw_entry["variant"], int):
            issues.append(
                f"资产条目 #{i} 的 variant 必须是整数，"
                f"实际类型为 {type(raw_entry['variant']).__name__}"
            )
        if not isinstance(raw_entry["duration_ms"], int) or raw_entry["duration_ms"] < 0:
            issues.append(
                f"资产条目 #{i} 的 duration_ms 必须是非负整数，"
                f"实际值为 {raw_entry['duration_ms']!r}"
            )

        # 7e. 指纹校验
        expected_fp = fingerprint(
            text=raw_entry["text"],
            voice=manifest.get("voice", ""),
            rate_value=raw_entry["rate_key"],
            model_version=manifest.get("model_version", ""),
        )
        if raw_entry["fingerprint"] != expected_fp:
            issues.append(
                f"资产条目 #{i} (key={raw_entry['key']!r}) 指纹不匹配: "
                f"包内={raw_entry['fingerprint']!r}, 重算={expected_fp!r}"
            )

        # 7f. path 安全性检查
        try:
            _validate_asset_path_str(raw_entry["path"], i, root)
        except AssetPackError as e:
            issues.append(str(e))

        # 7g. 音频文件存在性
        if isinstance(raw_entry["path"], str):
            audio_path = root / raw_entry["path"]
            if not audio_path.exists():
                issues.append(
                    f"资产条目 #{i} (key={raw_entry['key']!r}) 的音频文件不存在: {audio_path}"
                )

        # 7h. 失效字段（T18，可选）——口径与 load_pack 完全一致（共用公开校验函数）
        if "invalid_at" in raw_entry:
            try:
                parse_invalid_at(raw_entry["invalid_at"])
            except AssetPackError as e:
                issues.append(f"资产条目 #{i}: {e}")
        if "ttl" in raw_entry:
            try:
                _ttl_seconds(raw_entry["ttl"])
            except AssetPackError as e:
                issues.append(f"资产条目 #{i}: {e}")

    return issues
