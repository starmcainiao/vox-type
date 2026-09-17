"""
assets.pack — 包结构定义 + 装载 + 校验

职责：定义 AssetPack / AssetEntry 数据结构，提供 load_pack / lookup / validate_pack API。
      资产包是只读数据层——runtime/ 只读它，永不写它。
不负责：不做音频合成（compiler/）、不做播放（runtime/）、不做命中判定。

关键语义：
  - load_pack：任一条不合格 → 抛 AssetPackError（不得跳过坏条目继续）
  - lookup：指纹不匹配 / 文件不存在 → 返回 None（由调用方决定降级）
  - validate_pack：返回问题列表（空列表=通过），用于 CLI vox verify
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from assets.fingerprint import fingerprint


# ---------------------------------------------------------------------------
# 1. 异常定义
# ---------------------------------------------------------------------------
class AssetPackError(Exception):
    """资产包校验/装载异常——所有校验失败统一抛出此异常。

    消息必须包含导致失败的具体值，以便上层定位问题。
    """


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
    """
    key: str
    part_index: int
    rate_key: str
    variant: int
    text: str
    fingerprint: str
    path: str
    duration_ms: int


@dataclass
class AssetPack:
    """完整资产包：manifest 元数据 + 资产条目列表。

    属性：
        pack_id:          包唯一标识
        pack_version:     包版本号
        protocol_version: 协议版本号
        ruleset_version:  规则集版本号
        voice:            默认音色
        model_version:    模型版本号
        created_at:       创建时间（ISO 8601）
        assets:           资产条目列表
        root:             包根目录路径（用于 lookup 时校验文件存在性）
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

    def lookup(
        self,
        key: str,
        part_index: int,
        rate_key: str,
        variant: int,
        expected_text: str,
        engine_meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[AssetEntry]:
        """在包中查找匹配的资产条目。

        匹配条件：key + part_index + rate_key + variant + 指纹校验。
        指纹不匹配 / 文件不存在 → 返回 None（由调用方决定降级；这里只做判定，不做降级）。

        参数：
            key:           话术 key
            part_index:    分片序号
            rate_key:      语速档位（slow/normal/fast）
            variant:       变体编号
            expected_text: 期望的话术文本（用于指纹校验）
            engine_meta:   引擎元数据（保留字段，暂不使用）

        返回：
            匹配的 AssetEntry 或 None（未命中/指纹不匹配/文件不存在）
        """
        # 1. 遍历条目找到 key + part_index + rate_key + variant 匹配的条目
        candidate = None
        for entry in self.assets:
            if (
                entry.key == key
                and entry.part_index == part_index
                and entry.rate_key == rate_key
                and entry.variant == variant
            ):
                candidate = entry
                break

        if candidate is None:
            return None

        # 2. 指纹校验——用 expected_text 重算指纹并与条目指纹比对
        # WHY：即使 key/part/rate/variant 全部匹配，文本内容变了也必须视为未命中，
        #       否则会念错版本，这是静默降级的根源。
        expected_fp = fingerprint(
            text=expected_text,
            voice=self.voice,
            rate_value=rate_key,
            model_version=self.model_version,
        )
        if candidate.fingerprint != expected_fp:
            return None

        # 3. 音频文件存在性校验
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

    # 6. 音频文件存在性检查
    # WHY：运行时 lookup 返回了路径但文件不存在 = 必然失败，装载时应尽早发现。
    audio_path = root / raw["path"]
    if not audio_path.exists():
        raise AssetPackError(
            f"资产条目 #{index} (key={raw['key']!r}) 的音频文件不存在: {audio_path}"
        )

    return AssetEntry(
        key=raw["key"],
        part_index=part_index,
        rate_key=rate_key,
        variant=variant,
        text=raw["text"],
        fingerprint=actual_fp,
        path=raw["path"],
        duration_ms=duration_ms,
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

    # 5. 构造 AssetPack 实例
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

    返回：
        匹配的 AssetEntry 或 None（未命中/指纹不匹配/文件不存在）
    """
    return pack.lookup(
        key=key,
        part_index=part_index,
        rate_key=rate_key,
        variant=variant,
        expected_text=expected_text,
        engine_meta=engine_meta,
    )


# ---------------------------------------------------------------------------
# 7. validate API（CLI vox verify 用）
# ---------------------------------------------------------------------------
def validate_pack(root: Path) -> List[str]:
    """校验资产包，返回问题列表（空列表=通过）。

    用于 CLI vox verify 命令——不抛异常，返回可读的问题列表。
    校验内容：manifest 完整性 + 资产条目合法性 + 指纹一致性 + 文件存在性
    + 用户数据黑名单。

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

    # 6. 逐条校验资产条目
    for i, raw_entry in enumerate(raw_assets):
        if not isinstance(raw_entry, dict):
            issues.append(f"资产条目 #{i} 必须是字典，实际类型为 {type(raw_entry).__name__}")
            continue

        # 6a. 必填字段
        missing_asset = ASSET_REQUIRED_FIELDS - raw_entry.keys()
        if missing_asset:
            issues.append(f"资产条目 #{i} 缺少必填字段: {sorted(missing_asset)}")
            continue  # 字段不全，后续校验无意义

        # 6b. 用户数据黑名单
        found_user_entry = USER_DATA_BLACKLIST & raw_entry.keys()
        if found_user_entry:
            issues.append(
                f"资产条目 #{i} 中发现用户数据字段: {sorted(found_user_entry)}"
            )

        # 6c. rate_key 合法性
        if raw_entry["rate_key"] not in VALID_RATE_KEYS:
            issues.append(
                f"资产条目 #{i} 的 rate_key 非法: '{raw_entry['rate_key']}'"
            )

        # 6d. 类型检查
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

        # 6e. 指纹校验
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

        # 6f. 音频文件存在性
        audio_path = root / raw_entry["path"]
        if not audio_path.exists():
            issues.append(
                f"资产条目 #{i} (key={raw_entry['key']!r}) 的音频文件不存在: {audio_path}"
            )

    return issues
