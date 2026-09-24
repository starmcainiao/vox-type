"""
compiler.source — 源格式装载与校验

职责：读取 packs/<业务>/ 下的 pack.json + phrases.json，校验后产出不可变的 PackSource。
      源格式是「话术」的唯一入口——本模块是编译期的第一道闸门，任何不合规的源
      都在进入合成之前被拦下，避免把成本花在注定被质检/资产层拒绝的内容上。
不负责：不做音频合成（prebake.py）、不做质检（quality.py）、不读剧本（T06 职责）。

设计红线（docs/06 §6.1.2）：字模最小粒度 = 一句。每个 variant 必须是「一句完整话术」，
      文本中出现两个及以上句末标点 → 说明把长流程塞进了一个 variant，必须拆 key。
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core.protocol import VALID_RATES


# ---------------------------------------------------------------------------
# 1. 常量与异常
# ---------------------------------------------------------------------------
# pack.json 必填字段（缺任一 → SourceError，消息含字段名）
PACK_REQUIRED_FIELDS: Tuple[str, ...] = (
    "pack_id",
    "pack_version",
    "protocol_version",
    "ruleset_version",
    "voice",
    "model_version",
    "rates",
)

# pack.json 允许的字段全集（必填 + 业务侧可选）；出现别的 → 未知字段报错
# WHY（docs/08 §8.5 欠账 1）：locale 与 duplex 是业务侧字段（双工参数默认值），
#      这里只校验「存在性允许」，其内部形状由 runtime.DuplexParams 负责——
#      校验强度只增不减，不允许各层各写一份白名单。
PACK_ALLOWED_FIELDS: Tuple[str, ...] = PACK_REQUIRED_FIELDS + (
    "locale",
    "duplex",
)

# phrases.json 每项允许的字段全集；出现别的 → 未知字段报错
# WHY 白名单只增不减：`ttl` / `invalid_at` 是业务侧的失效声明（T18），
#      与 `duplex` 同类——写错一个字母必须响（未知字段报错），不许静默丢弃。
PHRASE_ALLOWED_FIELDS: Tuple[str, ...] = (
    "key",
    "variants",
    "rates",
    "ttl",
    "invalid_at",
)

# 句末标点集合（中英双语）——用于「一句」判据
_SENTENCE_TERMINATORS: str = "。！？!?"


class SourceError(Exception):
    """源格式校验异常——所有装载/校验失败统一抛出此异常。

    消息必须包含导致失败的具体值（字段名 / key 名 / 非法档位），
    以便业务侧定位是哪一条话术写错了，不允许吞掉错误上下文。
    """


def parse_iso8601(value: Any) -> datetime:
    """解析并校验 ISO 8601 时间戳（**公开**），失败抛 SourceError 并带实际值。

    `Z` 结尾归一成 `+00:00`（标准库 `fromisoformat` 不认 `Z`）；naive 值按 UTC
    解释——与 assets 层 `parse_invalid_at` 的口径完全一致，保证两侧解析同形。

    参数：
        value: 原始字段值（通常为字符串）

    返回：
        带时区的 datetime
    """
    if not isinstance(value, str) or not value.strip():
        raise SourceError(
            f"invalid_at 必须是非空 ISO 8601 字符串，实际值为 {value!r}"
            f"（类型 {type(value).__name__}）"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise SourceError(
            f"invalid_at 不是合法的 ISO 8601 时间戳: {value!r}（{e}）"
        ) from e
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.timezone.utc)


def _validate_ttl(ttl: Any, key: str, index: int) -> int:
    """校验单条话术的 `ttl`：正整数秒（T18）。

    `bool` 是 `int` 的子类，必须显式排除——`ttl=true` 会被静默当成 1 秒。
    字符串（如 `"3600"`）同样拒绝：那是一次类型笔误，放行会让话术提前失效。

    参数：
        ttl:   ttl 字段值
        key:   话术 key（错误信息定位）
        index: phrases 下标（错误信息定位）

    返回：
        校验通过的秒数

    异常：
        SourceError: 非整数 / 布尔 / 非正（消息含 key 与实际值）
    """
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise SourceError(
            f"key='{key}' 的 ttl 必须是正整数秒，实际值为 {ttl!r}"
            f"（类型 {type(ttl).__name__}，位置 phrases[{index}]）"
        )
    if ttl <= 0:
        raise SourceError(
            f"key='{key}' 的 ttl 必须是正整数秒，实际值为 {ttl!r}"
            f"（位置 phrases[{index}]）"
        )
    return ttl


# ---------------------------------------------------------------------------
# 2. 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PhraseSource:
    """单条话术：key + 变体列表 + 语速档列表。

    属性：
        key:        话术唯一标识（同一源内唯一）
        variants:   变体文本（元组，防复读机；每个元素必须是一句）
        rates:      该 key 要预铸的语速档（缺省取 pack.json 的 rates）
        ttl:        相对失效时长（秒，T18）；预铸时折算成 invalid_at 落进 manifest
        invalid_at: 绝对失效时刻（T18）；与 ttl 在源里**互斥**
    """
    key: str
    variants: Tuple[str, ...]
    rates: Tuple[str, ...]
    ttl: Optional[int] = None
    invalid_at: Optional[datetime] = None


@dataclass(frozen=True)
class PackSource:
    """一个业务包的源：包元数据 + 话术表。

    属性：
        pack_id / pack_version / protocol_version / ruleset_version: 包身份与版本
        voice:              默认音色（参与资产指纹）
        model_version:      模型版本（参与资产指纹；换版本 = 旧资产失效）
        rates:              包级默认语速档
        phrases:            话术表（元组，每项含 key/variants/rates）
    """
    pack_id: str
    pack_version: str
    protocol_version: str
    ruleset_version: str
    voice: str
    model_version: str
    rates: Tuple[str, ...]
    phrases: Tuple[PhraseSource, ...]


# ---------------------------------------------------------------------------
# 3. 内部校验辅助
# ---------------------------------------------------------------------------
def _read_json(path: Path, label: str) -> Dict[str, Any]:
    """读取并解析一个 JSON 文件，返回字典。

    参数：
        path:  文件路径
        label: 文件角色描述（用于错误信息定位）

    异常：
        SourceError: 文件不存在 / 非 UTF-8 / 非合法 JSON / 顶层不是对象
    """
    if not path.exists():
        raise SourceError(f"缺少 {label}: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except UnicodeDecodeError as e:
        raise SourceError(f"{label} 编码错误（非 UTF-8）: {path} ({e})") from e
    except json.JSONDecodeError as e:
        raise SourceError(f"{label} 不是合法 JSON: {path} ({e})") from e

    if not isinstance(data, dict):
        raise SourceError(
            f"{label} 顶层必须是 JSON 对象，实际类型为 {type(data).__name__}: {path}"
        )
    return data


def _validate_rates(rates: Any, context: str) -> Tuple[str, ...]:
    """校验语速档列表：必须是字符串列表且全部在 VALID_RATES 内。

    未知档位一律报错，**不得回落**——回落会让「本该是 slow 的话术被念成 normal」，
    正是仓库红线里「静默降级」的原型。

    参数：
        rates:   原始语速档列表
        context: 来源描述（用于错误信息定位）

    返回：
        校验通过的语速档元组（保持原顺序）
    """
    if not isinstance(rates, list):
        raise SourceError(
            f"{context} 的 rates 必须是列表，实际类型为 {type(rates).__name__}"
        )
    if len(rates) == 0:
        raise SourceError(f"{context} 的 rates 不能为空列表")

    result: List[str] = []
    for i, r in enumerate(rates):
        if not isinstance(r, str) or not r:
            raise SourceError(
                f"{context} 的 rates[{i}] 必须是非空字符串，实际值为 {r!r}"
            )
        if r not in VALID_RATES:
            raise SourceError(
                f"{context} 的 rates[{i}] 含非法档位 '{r}'"
                f"（仅支持 {sorted(VALID_RATES)}，不允许静默回落）"
            )
        result.append(r)
    return tuple(result)


def _validate_variant(text: Any, key: str, index: int) -> None:
    """校验单个 variant 文本：非空字符串 + 必须是一句。

    「一句」判据：文本中不得出现两个及以上句末标点（。！？!?）。
    出现两个 → 说明把长流程塞进了一个 variant，必须拆成多个 key。

    参数：
        text:  原始 variant 文本
        key:   所属话术 key（用于错误信息定位）
        index: variant 序号（用于错误信息定位）

    异常：
        SourceError: 空值 / 非字符串 / 含两个及以上句末标点
    """
    if not isinstance(text, str) or not text:
        raise SourceError(
            f"key='{key}' 的 variants[{index}] 必须是非空字符串，实际值为 {text!r}"
        )

    # 统计句末标点个数（连续出现如 "！？" 也算两个，均视为跨句）
    terminator_count = sum(1 for ch in text if ch in _SENTENCE_TERMINATORS)
    if terminator_count >= 2:
        raise SourceError(
            f"key='{key}' 的 variants[{index}] 含 {terminator_count} 个句末标点，"
            f"不是「一句」：长流程请拆成多个 key（字模最小粒度 = 一句）"
        )


# ---------------------------------------------------------------------------
# 4. 装载 API
# ---------------------------------------------------------------------------
def load_source(root: Union[str, Path]) -> PackSource:
    """装载并校验一个业务包源，返回不可变的 PackSource。

    参数：
        root: 业务包源目录（packs/<业务>/）

    返回：
        校验通过的 PackSource 实例

    异常：
        SourceError: pack.json/phrases.json 缺失、字段不全、未知字段（typo 必须响）、
                     key 重复、rates 非法档位、variant 非「一句」
    """
    root = Path(root)

    # 1. 装载 pack.json 并校验必填字段
    pack = _read_json(root / "pack.json", "pack.json")
    missing = [f for f in PACK_REQUIRED_FIELDS if f not in pack]
    if missing:
        raise SourceError(f"pack.json 缺少必填字段: {missing}")

    # 1b. 未知字段：typo 必须响，不得静默丢弃
    # WHY（docs/08 §8.5 欠账 1）：`duplex` 写错一个字母（如 `duplx`）被无声忽略
    #      = 业务参数静默不生效，顶在「禁止静默降级」红线上。白名单只增不减——
    #      packs/repair/pack.json 正好用了 locale + duplex，是本改动的活体回归用例。
    unknown_pack = sorted(set(pack.keys()) - set(PACK_ALLOWED_FIELDS))
    if unknown_pack:
        raise SourceError(
            f"pack.json 未知字段: {unknown_pack}（仅允许 {list(PACK_ALLOWED_FIELDS)}）"
        )

    # 2. 校验 pack.json.rates（包级默认语速档）
    default_rates = _validate_rates(pack["rates"], "pack.json")

    # 3. 装载 phrases.json
    phrases_doc = _read_json(root / "phrases.json", "phrases.json")
    if "phrases" not in phrases_doc:
        raise SourceError("phrases.json 缺少必填字段: ['phrases']")

    raw_phrases = phrases_doc["phrases"]
    if not isinstance(raw_phrases, list):
        raise SourceError(
            f"phrases.json 的 phrases 必须是列表，"
            f"实际类型为 {type(raw_phrases).__name__}"
        )
    if len(raw_phrases) == 0:
        raise SourceError("phrases.json 的 phrases 不能为空列表")

    # 4. 逐条校验话术；重复 key 必须报错（不许静默后者覆盖前者）
    # WHY：JSON 里重复 key 或列表里重复 key 若被后者覆盖，会丢掉一条预铸内容
    #      且零留痕——下游运行时查不到该条只会当「未命中」，正是红线禁止的静默降级。
    seen_keys: Dict[str, int] = {}
    phrases: List[PhraseSource] = []
    for i, raw in enumerate(raw_phrases):
        if not isinstance(raw, dict):
            raise SourceError(
                f"phrases[{i}] 必须是字典，实际类型为 {type(raw).__name__}"
            )

        # 未知字段：源格式是「给人和模型看的可 Review 文本」，typo 必须响
        # WHY：`varient` 静默丢弃 = 作者以为写了变体开关，实际没生效，
        #      且零留痕——与 pack.json 的 duplex typo 是同一类静默降级。
        unknown_phrase = sorted(set(raw.keys()) - set(PHRASE_ALLOWED_FIELDS))
        if unknown_phrase:
            raise SourceError(
                f"phrases[{i}] 未知字段: {unknown_phrase}"
                f"（仅允许 {list(PHRASE_ALLOWED_FIELDS)}）"
            )

        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise SourceError(
                f"phrases[{i}] 的 key 必须是非空字符串，实际值为 {key!r}"
            )
        if key in seen_keys:
            raise SourceError(
                f"phrases[{i}] 的 key='{key}' 与 phrases[{seen_keys[key]}] 重复"
                f"（同一源内 key 必须唯一，不允许后者覆盖前者）"
            )
        seen_keys[key] = i

        # variants：非空字符串列表，元素非空且必须是一句
        raw_variants = raw.get("variants")
        if not isinstance(raw_variants, list):
            raise SourceError(
                f"key='{key}' 的 variants 必须是列表，"
                f"实际类型为 {type(raw_variants).__name__}"
            )
        if len(raw_variants) == 0:
            raise SourceError(f"key='{key}' 的 variants 不能为空列表")

        variants: List[str] = []
        for vi, text in enumerate(raw_variants):
            _validate_variant(text, key, vi)
            variants.append(text)

        # rates：缺省取 pack.json.rates（包级默认）
        phrase_rates = (
            tuple(_validate_rates(raw["rates"], f"key='{key}'"))
            if "rates" in raw
            else default_rates
        )

        # ttl / invalid_at（T18）：两者在源里**互斥**——同时给一律报错，不猜哪个优先。
        # WHY 不猜：两个口径（相对 vs 绝对）同时存在时谁赢都是一种静默选择，
        #      会让作者以为写了一个、实际生效另一个，正是"本该命中却换了路径"的同族问题。
        if "ttl" in raw and "invalid_at" in raw:
            raise SourceError(
                f"key='{key}' 的 'ttl' 与 'invalid_at' 互斥，不能同时出现"
                f"（ttl={raw['ttl']!r}, invalid_at={raw['invalid_at']!r}，位置 phrases[{i}]）"
            )

        ttl: Optional[int] = None
        invalid_at: Optional[datetime] = None
        if "ttl" in raw:
            ttl = _validate_ttl(raw["ttl"], key, i)
        if "invalid_at" in raw:
            invalid_at = parse_iso8601(raw["invalid_at"])

        phrases.append(PhraseSource(
            key=key, variants=tuple(variants), rates=phrase_rates,
            ttl=ttl, invalid_at=invalid_at,
        ))

    return PackSource(
        pack_id=pack["pack_id"],
        pack_version=pack["pack_version"],
        protocol_version=pack["protocol_version"],
        ruleset_version=pack["ruleset_version"],
        voice=pack["voice"],
        model_version=pack["model_version"],
        rates=default_rates,
        phrases=tuple(phrases),
    )
