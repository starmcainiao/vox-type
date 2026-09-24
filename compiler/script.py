"""
compiler.script — 剧本源格式（packs/<业务>/script.json）装载与校验

职责：把「剧本源」（docs/07 §7.2）读成不可变的 Script，并在装载期拦下一切格式错误。
      Script.units 保存**原始字典**——不是 PlanUnit——因为源格式专有的 `reason`
      字段必须原样留住，供 checks.py 的 C5（降级留痕）判定。
不负责：不做五类判据判定（checks.py）、不读资产包、不做运行时命中判定（runtime/）。

设计红线（docs/07 §7.2 / §7.5，照抄不自创）：
  1. **不得**用 core.parse_plan 装载剧本。WHY：core._validate_unit 只认
     action/key/text/rate/variant/slots，遇到源格式专有的 `reason` 会**静默丢弃**；
     而 reason 正是 R-5「SAY_LIVE 必须带 reason」的机器判据输入。丢弃 = 降级留痕
     被抹掉且零留痕，是仓库红线里「静默降级」的原型。所以单元级校验在这里自己写。
  2. 原语与 rate 的合法性**必须**引 core：action 调 core.validate_primitive，
     rate 复用 core.protocol.VALID_RATES——不复制一份白名单（白名单分叉后两边
     各认一套，就是静默降级的温床）。
  3. **未知字段一律报错**。源格式是「给人和模型看的可 Review 文本」，typo 必须响
     （`termial_keys` / `reson` 静默丢弃 = 剧本作者以为写了对，实际没生效）。
     这条是源格式规矩，不涉及 core.protocol 的既有行为。
  4. 所有报错消息必须含**文件名 / 字段名 / 单元序号**，让作者能直接定位到那一行。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple, Union

from core.protocol import VALID_RATES, validate_primitive


# ---------------------------------------------------------------------------
# 1. 常量与异常
# ---------------------------------------------------------------------------
# 剧本文件在业务包目录下的固定文件名
SCRIPT_FILE_NAME: str = "script.json"

# 支持的剧本格式版本（本版只接受 1，写别的 → 报错，不做静默兼容）
SUPPORTED_SCRIPT_VERSION: int = 1

# 追问上限的硬规则值（R-3「最多 3 次」）——max_retry 声明值超过它由 checks.py 的 C2b 拦
MAX_RETRY_RULE: int = 3

# max_retry 缺省值（docs/07 §7.2：可选字段，缺省 3）
DEFAULT_MAX_RETRY: int = 3

# 剧本顶层必填字段（缺任一 → ScriptError，消息含字段名）
SCRIPT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "script_version",
    "terminal_keys",
    "live_whitelist",
    "units",
)

# 剧本顶层可选字段
SCRIPT_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "max_retry",
)

# 剧本顶层允许的字段全集 = 必填 + 可选；出现别的 → 未知字段报错
SCRIPT_ALLOWED_FIELDS: Tuple[str, ...] = SCRIPT_REQUIRED_FIELDS + SCRIPT_OPTIONAL_FIELDS

# 播报单元允许的字段：沿用 core.protocol 的 plan 单元字段，外加源格式专有字段 reason
UNIT_ALLOWED_FIELDS: Tuple[str, ...] = (
    "action",
    "key",
    "text",
    "rate",
    "variant",
    "slots",
    "reason",
)


class ScriptError(Exception):
    """剧本源格式校验异常——所有装载/校验失败统一抛出此异常。

    消息必须包含导致失败的文件名 / 字段名 / 单元序号，不允许吞掉错误上下文。
    """


# ---------------------------------------------------------------------------
# 2. 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Script:
    """一份剧本源：出口声明 + 降级白名单 + 追问上限 + 单元序列。

    属性：
        script_version:  剧本格式版本（本版只接受 1）
        terminal_keys:   终态话术 key（收尾/转人工/结束）——线性 plan 无 END 单元时
                         用它表达「出口」
        live_whitelist:  允许使用 SAY_LIVE 的场景白名单（**可为空** = 本业务禁止降级）
        max_retry:       追问上限（缺省 3；> 3 由 checks.py 的 C2b 拦）
        units:           播报单元原始字典序列（不转 PlanUnit，reason 要留着）
    """

    script_version: int
    terminal_keys: tuple[str, ...]
    live_whitelist: tuple[str, ...]
    max_retry: int
    units: tuple[dict, ...]


# ---------------------------------------------------------------------------
# 3. 单元级读取辅助（原语合法性一律引 core）
# ---------------------------------------------------------------------------
def resolve_action(unit: Dict[str, Any]) -> str:
    """解析单元的 action：显式给出则用显式值，缺省按结构推断。

    推断规则与 core._validate_unit 一致（key → SAY，text → SAY_LIVE），但 core
    没有暴露这个函数，而 checks.py 的 C1a/C4a/C5a 都要判「这个单元是不是 SAY_LIVE」，
    所以在这里落地一份——推断规则只此一处，避免各判据各猜一遍。

    参数：
        unit: 单元的原始字典

    返回：
        解析后的 action 字符串
    """
    action = unit.get("action")
    if action is not None:
        return action
    return "SAY" if unit.get("key") is not None else "SAY_LIVE"


def _has_key(unit: Dict[str, Any]) -> bool:
    """单元是否带了非 None 的 key（与 core._validate_unit 的口径一致）。"""
    return "key" in unit and unit["key"] is not None


def _has_text(unit: Dict[str, Any]) -> bool:
    """单元是否带了非 None 的 text。"""
    return "text" in unit and unit["text"] is not None


def _is_int(value: Any) -> bool:
    """严格整数判定：bool 是 int 的子类，但 true/false 不是本格式的合法整数。"""
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# 4. 内部校验辅助
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> Dict[str, Any]:
    """读取并解析剧本文件，返回字典。

    异常：
        ScriptError: 文件不存在 / 非 UTF-8 / 非合法 JSON / 顶层不是对象
    """
    if not path.exists():
        raise ScriptError(f"缺少剧本文件 {SCRIPT_FILE_NAME}: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except UnicodeDecodeError as e:
        raise ScriptError(f"{SCRIPT_FILE_NAME} 编码错误（非 UTF-8）: {path} ({e})") from e
    except json.JSONDecodeError as e:
        raise ScriptError(f"{SCRIPT_FILE_NAME} 不是合法 JSON: {path} ({e})") from e

    if not isinstance(data, dict):
        raise ScriptError(
            f"{SCRIPT_FILE_NAME} 顶层必须是 JSON 对象，实际类型为 {type(data).__name__}"
        )
    return data


def _validate_string_tuple(value: Any, field: str, context: str) -> Tuple[str, ...]:
    """校验字符串列表字段：必须是列表且元素全部是非空字符串。

    参数：
        value:   原始值
        field:   字段名（用于错误信息）
        context: 来源描述（用于错误信息）

    返回：
        校验通过的字符串元组（保持原顺序）
    """
    if not isinstance(value, list):
        raise ScriptError(
            f"{context} 的 {field} 必须是列表，实际类型为 {type(value).__name__}"
        )
    result: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ScriptError(
                f"{context} 的 {field}[{i}] 必须是非空字符串，实际值为 {item!r}"
            )
        result.append(item)
    return tuple(result)


def _validate_unit(raw: Any, index: int) -> None:
    """校验单个播报单元（1-based 序号进错误信息）。

    校验项（docs/07 §7.2 单元级附加规则 + core 的 plan 单元字段口径）：
      1. 必须是字典
      2. 必须**恰好**是 key 或 text 之一（都给 / 都缺 → 报错）
      3. 未知字段 → 报错（源格式 typo 必须响）
      4. action 显式给出时必须在 core 的原语白名单里
      5. rate 必须在 core 的 VALID_RATES 里（不回落）
      6. variant 必须是整数或 "auto"
      7. slots 必须是字典
      8. reason 只允许出现在 SAY_LIVE 单元上；出现时必须是非空字符串

    注意：`action="SAY"` 且带 text **不在**这里拦——那是 checks.py 的 C4a
    判据（unreviewed_text），必须让检查器看到它才能报出来。
    """
    where = f"{SCRIPT_FILE_NAME} 的 units[{index}]"

    if not isinstance(raw, dict):
        raise ScriptError(f"{where} 必须是字典，实际类型为 {type(raw).__name__}")

    # 3. 未知字段：typo 必须响，不得静默丢弃
    unknown = sorted(set(raw.keys()) - set(UNIT_ALLOWED_FIELDS))
    if unknown:
        raise ScriptError(f"{where} 含未知字段: {unknown}（仅允许 {list(UNIT_ALLOWED_FIELDS)}）")

    has_key = _has_key(raw)
    has_text = _has_text(raw)

    # 2. key / text 恰好一个
    if has_key and has_text:
        raise ScriptError(
            f"{where} 不能同时包含 'key' 和 'text'（必须恰好给一个）"
        )
    if not has_key and not has_text:
        raise ScriptError(
            f"{where} 必须包含 'key' 或 'text' 之一，但两者均缺失"
        )

    # 4. action：显式给出时必须在 core 的原语白名单里（复用 core，不自造）
    action = raw.get("action")
    if action is not None:
        if not isinstance(action, str) or not action:
            raise ScriptError(
                f"{where} 的 action 必须是非空字符串，实际值为 {action!r}"
            )
        try:
            validate_primitive(action)
        except Exception as e:
            # WHY：core 抛的是 ProtocolError，这里转成 ScriptError 让剧本作者的
            #      报错口径统一（loader 层只认 ScriptError）。
            raise ScriptError(f"{where} 的 action 非法: {e}") from e

    # 5. rate：必须在 core 的 VALID_RATES 里，未知档位一律报错，不回落
    if "rate" in raw and raw["rate"] is not None:
        rate = raw["rate"]
        if not isinstance(rate, str) or rate not in VALID_RATES:
            raise ScriptError(
                f"{where} 的 rate 必须是 {sorted(VALID_RATES)} 之一，"
                f"实际值为 {rate!r}（不允许静默回落到 normal）"
            )

    # 6. variant：整数或 "auto"
    if "variant" in raw and raw["variant"] is not None:
        variant = raw["variant"]
        if variant != "auto" and not _is_int(variant):
            raise ScriptError(
                f"{where} 的 variant 必须是整数或 'auto'，实际值为 {variant!r}"
            )

    # 7. slots：必须是字典
    if "slots" in raw and raw["slots"] is not None:
        slots = raw["slots"]
        if not isinstance(slots, dict):
            raise ScriptError(
                f"{where} 的 slots 必须是字典，实际类型为 {type(slots).__name__}"
            )

    # 8. reason：源格式专有字段，只允许出现在 SAY_LIVE 单元上
    if "reason" in raw:
        reason = raw["reason"]
        if not isinstance(reason, str) or not reason:
            raise ScriptError(
                f"{where} 的 reason 必须是非空字符串，实际值为 {reason!r}"
            )
        # WHY：key 单元带 reason 是矛盾信号——key 单元是「资产命中」，
        #      降级理由只可能属于 SAY_LIVE（自由文本）。允许它通过会让剧本作者
        #      误以为这一句走的是降级路径，审计时反而更糟。
        if resolve_action(raw) != "SAY_LIVE":
            raise ScriptError(
                f"{where} 是 {resolve_action(raw)} 单元，不应带 reason"
                f"（reason 只允许出现在 SAY_LIVE 单元上，实际值为 {reason!r}）"
            )


# ---------------------------------------------------------------------------
# 5. 装载 API
# ---------------------------------------------------------------------------
def load_script(root: Union[str, Path]) -> Script:
    """装载并校验一份剧本源，返回不可变的 Script。

    参数：
        root: 业务包目录（packs/<业务>/），从中读 <root>/script.json

    返回：
        校验通过的 Script 实例（units 为原始字典元组，reason 原样保留）

    异常：
        ScriptError: 文件缺失 / 非合法 JSON / 缺必填字段 / 未知字段 /
                     script_version != 1 / units 为空 / 单元字段冲突 /
                     key 单元带 reason / 非法 action 或 rate
    """
    root = Path(root)
    doc = _read_json(root / SCRIPT_FILE_NAME)

    # 1. 顶层缺必填字段
    missing = [f for f in SCRIPT_REQUIRED_FIELDS if f not in doc]
    if missing:
        raise ScriptError(f"{SCRIPT_FILE_NAME} 缺少必填字段: {missing}")

    # 2. 顶层未知字段
    unknown = sorted(set(doc.keys()) - set(SCRIPT_ALLOWED_FIELDS))
    if unknown:
        raise ScriptError(f"{SCRIPT_FILE_NAME} 含未知字段: {unknown}")

    # 3. script_version：必须严格等于 1
    version = doc["script_version"]
    if not _is_int(version):
        raise ScriptError(
            f"{SCRIPT_FILE_NAME} 的 script_version 必须是整数，实际值为 {version!r}"
        )
    if version != SUPPORTED_SCRIPT_VERSION:
        raise ScriptError(
            f"{SCRIPT_FILE_NAME} 的 script_version 只支持 {SUPPORTED_SCRIPT_VERSION}，"
            f"实际值为 {version}"
        )

    # 4. terminal_keys：非空字符串列表（空列表 = 剧本没声明出口，不允许）
    if not isinstance(doc["terminal_keys"], list):
        raise ScriptError(
            f"{SCRIPT_FILE_NAME} 的 terminal_keys 必须是列表，"
            f"实际类型为 {type(doc['terminal_keys']).__name__}"
        )
    if len(doc["terminal_keys"]) == 0:
        raise ScriptError(
            f"{SCRIPT_FILE_NAME} 的 terminal_keys 不能为空列表（剧本必须声明出口）"
        )
    terminal_keys = _validate_string_tuple(doc["terminal_keys"], "terminal_keys", SCRIPT_FILE_NAME)

    # 5. live_whitelist：字符串列表，**允许空列表**（空 = 本业务禁止任何 SAY_LIVE）
    live_whitelist = _validate_string_tuple(doc["live_whitelist"], "live_whitelist", SCRIPT_FILE_NAME)

    # 6. max_retry：可选，缺省 3；给出时必须是整数（上限由 checks.py 的 C2b 判）
    max_retry = DEFAULT_MAX_RETRY
    if "max_retry" in doc:
        max_retry = doc["max_retry"]
        if not _is_int(max_retry):
            raise ScriptError(
                f"{SCRIPT_FILE_NAME} 的 max_retry 必须是整数，实际值为 {max_retry!r}"
            )

    # 7. units：非空列表
    units_doc = doc["units"]
    if not isinstance(units_doc, list):
        raise ScriptError(
            f"{SCRIPT_FILE_NAME} 的 units 必须是列表，实际类型为 {type(units_doc).__name__}"
        )
    if len(units_doc) == 0:
        raise ScriptError(f"{SCRIPT_FILE_NAME} 的 units 不能为空列表")

    # 8. 逐条校验单元（序号 1-based，与运行时事件的 part 对齐）
    for i, unit in enumerate(units_doc, start=1):
        _validate_unit(unit, i)

    return Script(
        script_version=version,
        terminal_keys=terminal_keys,
        live_whitelist=live_whitelist,
        max_retry=max_retry,
        units=tuple(units_doc),
    )
