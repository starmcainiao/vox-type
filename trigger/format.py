"""
trigger.format — state 快照格式 + trigger.json 映射格式（定义与校验）

职责：把 packs/<前置包>/trigger.json 读成不可变的 Trigger，并提供三个库 API：
      load_trigger(pack_dir) / validate_state(state, declared) / check_budget(plan, budget_chars)
      本模块是「state → plan 这一侧」的**格式闸门**：格式错误的 state 与 trigger 在这里就被
      拦下，不进入任何判定（docs/12 §12.11 部件①与③的接缝）。
不负责：不做 state → plan 的**行为**（T17 的 trigger.build_plan）、不写留痕（T17 的 ledger）、
      不改内核、不做音频（compiler/ assets/ runtime/）。

设计红线（逐条对齐本仓既有做法）：
  1. **未知字段一律报错**（docs/08 §8.5 欠账 1 在 compiler/source.py 与 compiler/script.py
     的既有态度）：trigger.json 的 typo 不许静默丢弃——写错一个字母 = 该规则永不触发，
     而零留痕，正是仓库红线禁止的「静默降级」原型。
  2. **state 必须扁平、字段必须预先声明**（docs/12 §12.10 末）：未声明的字段报错，
     与源格式对 typo 的态度一致。
  3. **state 必须分层**：结构化层（状态码/枚举/计数/布尔，可入库、可导出）与敏感层
     （账号/金额/原始文本，只落本地）分开存放；**敏感值出现在结构化层 → 报错**。
     本仓没有第二处声明敏感字段的白名单，所以这里必须自建 —— 白名单只此一处。
  4. **前置包不产生未审核文本**：plan 单元只能引用 phrases.json 里存在的 key，
     引用不存在的 key → 报错（对齐 compiler/checks.py 的 C3b `key_not_in_library`）；
     trigger.json 里出现 `SAY_LIVE` / `text` → 报错（对齐 C4a `unreviewed_text` 的口径）。
  5. **原语与 rate 的合法性一律引 core**（core.protocol.PRIMITIVES / VALID_RATES），
     不复制一份白名单 —— 白名单分叉后两边各认一套，就是静默降级的温床。
  6. **确定性**：plan 完全由 trigger.json 给出（顺序、variant、rate 全部显式），
     本模块不做任何随机或时间相关的选择；不写 turn_id 派生变体（那是 T17 的行为侧）。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core import PRIMITIVES
from core import VALID_RATES


# ---------------------------------------------------------------------------
# 1. 常量
# ---------------------------------------------------------------------------
# trigger.json 的固定文件名与支持的格式版本（写别的版本 → 报错，不做静默兼容）
TRIGGER_FILE_NAME: str = "trigger.json"
SUPPORTED_TRIGGER_VERSION: int = 1

# 单轮播报预算缺省值（docs/12 §12.8：60 字 ≈ 13 秒 @normal，实测语速 4.64 字/秒）
DEFAULT_BUDGET_CHARS: int = 60

# state 分层名（docs/12 §12.10 末）：结构化层可入库/可导出，敏感层只落本地
LAYER_STRUCTURED: str = "structured"
LAYER_SENSITIVE: str = "sensitive"
VALID_LAYERS: Tuple[str, ...] = (LAYER_STRUCTURED, LAYER_SENSITIVE)

# 结构化层允许的字段类型（封闭集合：状态码/枚举/计数/布尔，docs/12 §12.10）
STRUCTURED_TYPES: Tuple[str, ...] = ("str", "int", "bool")

# 敏感层允许的字段类型（封闭集合：账号/金额/原始文本，docs/12 §12.10）
SENSITIVE_TYPES: Tuple[str, ...] = ("str", "number", "text")

# 敏感值嗅探标记（docs/12 §12.10：账号、金额、原始文本属敏感层）。
# 用途：结构化层的字段名若含这些标记 → 视同敏感值被放进了结构化层，报错。
# 只按字段名嗅探，不解析值——嗅探不到的一律不拦（不做假阳性），
# 但嗅探到的一律报错（不做静默放行）。
SENSITIVE_MARKERS: Tuple[str, ...] = (
    "account",
    "phone",
    "tel",
    "mobile",
    "email",
    "name",
    "amount",
    "money",
    "price",
    "balance",
    "id_card",
    "secret",
    "token",
    "password",
    "text",
)

# 结构化层字段声明允许的字段全集（出现别的 → 未知字段报错）
FIELD_DECL_ALLOWED_FIELDS: Tuple[str, ...] = (
    "layer",
    "type",
    "enum",
    "min",
    "max",
)

# plan 单元允许的字段全集。
# WHY 比 core.plan 单元少 key 之外的分支：前置包只允许「已审核的话术」，
#      因此 `text`（自由文本）与 `reason`（SAY_LIVE 的降级理由）**根本不允许出现**——
#      不是「禁止填错值」，而是「字段本身禁止出现」。
UNIT_ALLOWED_FIELDS: Tuple[str, ...] = (
    "action",
    "key",
    "rate",
    "variant",
    "slots",
)

# when 条件允许的字段：单值 ==；区间用 {"gt": n} / {"gte": n} / {"lt": n} / {"lte": n}
WHEN_OP_ALLOWED_FIELDS: Tuple[str, ...] = ("gt", "gte", "lt", "lte")

# rules 单元允许的字段全集
RULE_ALLOWED_FIELDS: Tuple[str, ...] = (
    "rule_id",
    "when",
    "units",
)

# 顶层必填字段
TRIGGER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "trigger_version",
    "state_fields",
    "rules",
)

# 顶层可选字段
TRIGGER_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "trigger_id",
    "budget_chars",
)

# 顶层允许字段全集 = 必填 + 可选；出现别的 → 未知字段报错
TRIGGER_ALLOWED_FIELDS: Tuple[str, ...] = TRIGGER_REQUIRED_FIELDS + TRIGGER_OPTIONAL_FIELDS


# ---------------------------------------------------------------------------
# 2. 异常
# ---------------------------------------------------------------------------
class TriggerError(Exception):
    """trigger.json 装载/校验异常——所有格式错误统一抛出此异常。

    消息必须包含导致失败的具体值（字段名 / rule_id / key 名 / 非法档位），
    以便业务侧定位到 trigger.json 的哪一行，不允许吞掉错误上下文。
    """


class StateError(Exception):
    """state 快照校验异常（validate_state 抛出）。

    消息必须包含具体非法字段名或字段名列表。
    """


class BudgetError(Exception):
    """单轮播报预算超支异常（check_budget 抛出）。

    消息必须同时包含实际字数与预算值（docs/12 §12.8：超出是可判据，不是警告）。
    """


# ---------------------------------------------------------------------------
# 3. 数据结构（全部 frozen：Trigger 不可变）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StateField:
    """一个预先声明的 state 字段。

    属性：
        layer:   "structured"（可入库/可导出）或 "sensitive"（只落本地）
        type:    结构化层限 str/int/bool；敏感层限 str/number/text
        enum:    结构化层 str 字段的封闭取值集合（None = 不限）
        min/max: 结构化层 int 字段的取值范围（None = 不限该侧）
    """

    layer: str
    type: str
    enum: Optional[Tuple[str, ...]] = None
    min: Optional[int] = None
    max: Optional[int] = None


@dataclass(frozen=True)
class PlanUnit:
    """trigger.json 里的一个 plan 单元：只引用已审核话术，不产生自由文本。

    属性：
        action:  原语名（缺省 SAY；必须 ∈ core.PRIMITIVES）
        key:     phrases.json 里存在的话术 key
        rate:    语速档（缺省 normal；必须 ∈ core.VALID_RATES）
        variant: 变体序号，**必须是整数**——确定性要求（docs/12 §12.6 / 本卡硬要求 5），
                 不允许 "auto"：变体的选择权在触发器里必须显式，不许交给运行时猜
        slots:   槽位名列表，元素必须是已声明的 state 字段（缺省 = 无槽位）
    """

    action: str = "SAY"
    key: str = ""
    rate: str = "normal"
    variant: int = 0
    slots: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TriggerRule:
    """一条 state → plan 规则。

    属性：
        rule_id: 规则标识（同一 trigger.json 内唯一）
        when:    匹配条件——字段名 → 期望值或 {"gt"/"gte"/"lt"/"lte": 值}；
                 None = 无条件（兜底规则），**至多一条且必须是 rules 的最后一条**
                 （放在前面 = 每一条 state 都命中它，后面的条件规则一条都看不到，零留痕）
        units:   该规则产出的 plan 单元序列
    """

    rule_id: str
    when: Optional[Dict[str, Any]]
    units: Tuple[PlanUnit, ...]


@dataclass(frozen=True)
class Trigger:
    """一份校验通过的前置包触发配置（不可变）。

    属性：
        trigger_id:   触发器标识（用于留痕关联；缺省由包目录名给出）
        trigger_version: 格式版本（本版只接受 1）
        budget_chars: 单轮播报总字数上限（缺省 60，docs/12 §12.8）
        state_fields: 预先声明的 state 字段（字段名 → 声明）
        phrase_keys:  该包已审核的话术 key 集合（来自 phrases.json，C3b 口径）
        rules:       规则序列
        pack_dir:    来源包目录（相对/绝对路径字符串，便于报错定位）
    """

    trigger_id: str
    trigger_version: int
    budget_chars: int
    state_fields: Tuple[Tuple[str, StateField], ...]
    phrase_keys: Tuple[str, ...]
    rules: Tuple[TriggerRule, ...]
    pack_dir: str


# ---------------------------------------------------------------------------
# 4. 内部辅助
# ---------------------------------------------------------------------------
def _is_int(value: Any) -> bool:
    """严格整数判定：bool 是 int 的子类，但 true/false 不是本格式的合法整数。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    """数值判定（str 不算）：用于敏感层 number 字段。"""
    return _is_int(value) or isinstance(value, float)


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    """读取并解析一个 JSON 文件；失败抛 TriggerError（消息含文件名与原因）。"""
    if not path.exists():
        raise TriggerError(f"缺少 {label}: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except UnicodeDecodeError as e:
        raise TriggerError(f"{label} 编码错误（非 UTF-8）: {path} ({e})") from e
    except json.JSONDecodeError as e:
        raise TriggerError(f"{label} 不是合法 JSON: {path} ({e})") from e

    if not isinstance(data, dict):
        raise TriggerError(
            f"{label} 顶层必须是 JSON 对象，实际类型为 {type(data).__name__}: {path}"
        )
    return data


def _read_phrase_keys(root: Path) -> Tuple[str, ...]:
    """读取 phrases.json 的 key 集合（复用 compiler.load_source，不自造话术表解析）。

    话术表的装载与校验归 compiler（白名单分叉 = 静默降级的温床），这里只取 key 集合
    作为 C3b 的「已审核集合」判据输入。
    """
    from compiler import SourceError, load_source

    try:
        source = load_source(root)
    except SourceError as e:
        # WHY：源格式错误必须原样透出（含哪条 key 写错了），
        #      包成 TriggerError 会让报错口径统一，但**不得**吞掉原始消息。
        raise TriggerError(f"包源（pack.json/phrases.json）校验失败: {e}") from e

    return tuple(p.key for p in source.phrases)


def _validate_field_decl(raw: Any, field_name: str, where: str) -> StateField:
    """校验一个 state 字段声明，返回 StateField。"""
    if not isinstance(raw, dict):
        raise TriggerError(
            f"{where} 的 state_fields['{field_name}'] 必须是字典，"
            f"实际类型为 {type(raw).__name__}"
        )

    unknown = sorted(set(raw.keys()) - set(FIELD_DECL_ALLOWED_FIELDS))
    if unknown:
        raise TriggerError(
            f"{where} 的 state_fields['{field_name}'] 未知字段: {unknown}"
            f"（仅允许 {list(FIELD_DECL_ALLOWED_FIELDS)}）"
        )

    for required in ("layer", "type"):
        if required not in raw:
            raise TriggerError(
                f"{where} 的 state_fields['{field_name}'] 缺少必填字段 '{required}'"
            )

    layer = raw["layer"]
    if not isinstance(layer, str) or layer not in VALID_LAYERS:
        raise TriggerError(
            f"{where} 的 state_fields['{field_name}'] 的 layer 必须是 {list(VALID_LAYERS)} "
            f"之一，实际值为 {layer!r}"
        )

    typ = raw["type"]
    if not isinstance(typ, str) or not typ:
        raise TriggerError(
            f"{where} 的 state_fields['{field_name}'] 的 type 必须是非空字符串，"
            f"实际值为 {typ!r}"
        )

    if layer == LAYER_STRUCTURED:
        if typ not in STRUCTURED_TYPES:
            raise TriggerError(
                f"{where} 的 state_fields['{field_name}'] 是结构化层，type 必须是 "
                f"{list(STRUCTURED_TYPES)} 之一，实际值为 {typ!r}"
            )
    else:
        if typ not in SENSITIVE_TYPES:
            raise TriggerError(
                f"{where} 的 state_fields['{field_name}'] 是敏感层，type 必须是 "
                f"{list(SENSITIVE_TYPES)} 之一，实际值为 {typ!r}"
            )

    enum = None
    if "enum" in raw:
        enum_raw = raw["enum"]
        if not isinstance(enum_raw, list) or len(enum_raw) == 0:
            raise TriggerError(
                f"{where} 的 state_fields['{field_name}'] 的 enum 必须是非空字符串列表，"
                f"实际值为 {enum_raw!r}"
            )
        for i, item in enumerate(enum_raw):
            if not isinstance(item, str) or not item:
                raise TriggerError(
                    f"{where} 的 state_fields['{field_name}'] 的 enum[{i}] 必须是非空字符串，"
                    f"实际值为 {item!r}"
                )
        enum = tuple(enum_raw)

    field_min = None
    if "min" in raw:
        if not _is_int(raw["min"]):
            raise TriggerError(
                f"{where} 的 state_fields['{field_name}'] 的 min 必须是整数，"
                f"实际值为 {raw['min']!r}"
            )
        field_min = raw["min"]

    field_max = None
    if "max" in raw:
        if not _is_int(raw["max"]):
            raise TriggerError(
                f"{where} 的 state_fields['{field_name}'] 的 max 必须是整数，"
                f"实际值为 {raw['max']!r}"
            )
        field_max = raw["max"]

    return StateField(layer=layer, type=typ, enum=enum, min=field_min, max=field_max)


def _validate_when(raw: Any, where: str) -> Optional[Dict[str, Any]]:
    """校验 when 条件。None（或缺省）= 无条件兜底规则。"""
    if raw is None:
        return None

    if not isinstance(raw, dict):
        raise TriggerError(
            f"{where} 的 when 必须是字典或 null（无条件），"
            f"实际类型为 {type(raw).__name__}"
        )

    for field_name, expected in raw.items():
        if not isinstance(field_name, str) or not field_name:
            raise TriggerError(f"{where} 的 when 含非字符串字段名: {field_name!r}")
        if not isinstance(expected, (str, int, float, bool)) and not isinstance(expected, dict):
            raise TriggerError(
                f"{where} 的 when['{field_name}'] 的期望值必须是标量或比较条件字典，"
                f"实际类型为 {type(expected).__name__}"
            )
        if isinstance(expected, dict):
            unknown_ops = sorted(set(expected.keys()) - set(WHEN_OP_ALLOWED_FIELDS))
            if unknown_ops:
                raise TriggerError(
                    f"{where} 的 when['{field_name}'] 未知比较符: {unknown_ops}"
                    f"（仅允许 {list(WHEN_OP_ALLOWED_FIELDS)}）"
                )
            for op, op_value in expected.items():
                if not _is_int(op_value):
                    raise TriggerError(
                        f"{where} 的 when['{field_name}'].{op} 必须是整数，"
                        f"实际值为 {op_value!r}"
                    )
    return dict(raw)


def _validate_unit(raw: Any, where: str, phrase_keys: set) -> PlanUnit:
    """校验一个 plan 单元：前置包只允许已审核话术，禁止自由文本。"""
    if not isinstance(raw, dict):
        raise TriggerError(f"{where} 必须是字典，实际类型为 {type(raw).__name__}")

    # 未知字段：typo 必须响（与 compiler/source.py、compiler/script.py 同一态度）
    unknown = sorted(set(raw.keys()) - set(UNIT_ALLOWED_FIELDS))
    if unknown:
        raise TriggerError(f"{where} 含未知字段: {unknown}（仅允许 {list(UNIT_ALLOWED_FIELDS)}）")

    # 前置包不产生未审核文本：action 必须是 SAY；SAY_LIVE / 其它原语一律拒绝
    action = raw.get("action", "SAY")
    if not isinstance(action, str) or not action:
        raise TriggerError(
            f"{where} 的 action 必须是非空字符串，实际值为 {action!r}"
        )
    if action not in PRIMITIVES:
        raise TriggerError(
            f"{where} 的 action 含未知原语 '{action}'"
            f"（仅支持 {sorted(PRIMITIVES)}）"
        )
    if action != "SAY":
        raise TriggerError(
            f"{where} 的 action='{action}' 不允许：前置包只允许已审核话术（action='SAY'），"
            f"SAY_LIVE 等自由文本原语一律拒绝（对齐 C4a unreviewed_text）"
        )

    # key 必填且在 phrases.json 里存在（C3b key_not_in_library 口径）
    if "key" not in raw:
        raise TriggerError(f"{where} 缺少必填字段 'key'")
    key = raw["key"]
    if not isinstance(key, str) or not key:
        raise TriggerError(f"{where} 的 key 必须是非空字符串，实际值为 {key!r}")
    if key not in phrase_keys:
        raise TriggerError(
            f"{where} 引用了未审核的 key '{key}'（phrases.json 里不存在该 key，"
            f"对齐 C3b key_not_in_library）"
        )

    # rate：必须在 core.VALID_RATES 里，未知档位一律报错，不回落
    rate = raw.get("rate", "normal")
    if not isinstance(rate, str) or rate not in VALID_RATES:
        raise TriggerError(
            f"{where} 的 rate 必须是 {sorted(VALID_RATES)} 之一，实际值为 {rate!r}"
            f"（不允许静默回落到 normal）"
        )

    # variant：必须是整数（确定性：变体选择权必须显式，不许 "auto"）
    variant = raw.get("variant", 0)
    if not _is_int(variant):
        raise TriggerError(
            f"{where} 的 variant 必须是整数（前置包要求确定性，不允许 'auto'），"
            f"实际值为 {variant!r}"
        )

    # slots：槽位名列表，元素必须是已声明的 state 字段
    slots: Tuple[str, ...] = ()
    if "slots" in raw and raw["slots"] is not None:
        raw_slots = raw["slots"]
        if not isinstance(raw_slots, list):
            raise TriggerError(
                f"{where} 的 slots 必须是列表，实际类型为 {type(raw_slots).__name__}"
            )
        slots = tuple(raw_slots)

    return PlanUnit(action=action, key=key, rate=rate, variant=variant, slots=slots)


def _validate_budget(raw: Any, where: str) -> int:
    """校验单轮播报预算：必须是正整数（缺省 60 字，docs/12 §12.8）。"""
    if not _is_int(raw):
        raise TriggerError(
            f"{where} 的 budget_chars 必须是整数，实际值为 {raw!r}"
        )
    if raw <= 0:
        raise TriggerError(
            f"{where} 的 budget_chars 必须是正整数，实际值为 {raw}"
        )
    return raw


# ---------------------------------------------------------------------------
# 5. 公开 API
# ---------------------------------------------------------------------------
def load_trigger(pack_dir: Union[str, Path]) -> Trigger:
    """装载并校验一个前置包，返回不可变的 Trigger。

    读取 <pack_dir>/pack.json + phrases.json + trigger.json，全部校验通过后返回 Trigger。
    pack.json / phrases.json 的校验复用 compiler.load_source（不自造话术表解析）。

    参数：
        pack_dir: 业务包目录（packs/<前置包>/）

    返回：
        校验通过的 Trigger 实例（frozen dataclass，不可变）

    异常：
        TriggerError: 文件缺失 / 非合法 JSON / 缺必填字段 / 未知字段（typo 必须响）/
                      非法 layer 或 type / 引用未审核 key / 出现 SAY_LIVE 或 text /
                      变体不是整数 / 兜底规则不止一条 / 兜底规则不是最后一条 / 重复 rule_id /
                      when 含未声明字段
    """
    root = Path(pack_dir)
    where = f"{TRIGGER_FILE_NAME}（{root}）"

    # 先取已审核 key 集合（C3b 判据输入），话术表的校验强度全归 compiler
    phrase_keys = _read_phrase_keys(root)
    phrase_key_set = set(phrase_keys)

    doc = _read_json(root / TRIGGER_FILE_NAME, TRIGGER_FILE_NAME)

    # 1. 顶层必填字段
    missing = [f for f in TRIGGER_REQUIRED_FIELDS if f not in doc]
    if missing:
        raise TriggerError(f"{where} 缺少必填字段: {missing}")

    # 2. 顶层未知字段：typo 必须响，不得静默丢弃
    unknown = sorted(set(doc.keys()) - set(TRIGGER_ALLOWED_FIELDS))
    if unknown:
        raise TriggerError(
            f"{where} 含未知字段: {unknown}（仅允许 {list(TRIGGER_ALLOWED_FIELDS)}）"
        )

    # 3. trigger_version：必须严格等于 1
    version = doc["trigger_version"]
    if not _is_int(version):
        raise TriggerError(
            f"{where} 的 trigger_version 必须是整数，实际值为 {version!r}"
        )
    if version != SUPPORTED_TRIGGER_VERSION:
        raise TriggerError(
            f"{where} 的 trigger_version 只支持 {SUPPORTED_TRIGGER_VERSION}，实际值为 {version}"
        )

    # 4. trigger_id：可选，缺省用包目录名
    trigger_id = root.name
    if "trigger_id" in doc:
        tid = doc["trigger_id"]
        if not isinstance(tid, str) or not tid:
            raise TriggerError(
                f"{where} 的 trigger_id 必须是非空字符串，实际值为 {tid!r}"
            )
        trigger_id = tid

    # 5. budget_chars：可选，缺省 60 字（docs/12 §12.8）
    budget_chars = DEFAULT_BUDGET_CHARS
    if "budget_chars" in doc:
        budget_chars = _validate_budget(doc["budget_chars"], where)

    # 6. state_fields：字段声明表（必须非空——没有声明就等于不校验）
    fields_doc = doc["state_fields"]
    if not isinstance(fields_doc, dict):
        raise TriggerError(
            f"{where} 的 state_fields 必须是字典，实际类型为 {type(fields_doc).__name__}"
        )
    if len(fields_doc) == 0:
        raise TriggerError(f"{where} 的 state_fields 不能为空字典（state 字段必须预先声明）")

    state_fields: List[Tuple[str, StateField]] = []
    declared_names: set = set()
    for field_name, raw_decl in fields_doc.items():
        if not isinstance(field_name, str) or not field_name:
            raise TriggerError(f"{where} 的 state_fields 含非字符串字段名: {field_name!r}")
        if field_name in declared_names:
            raise TriggerError(f"{where} 的 state_fields['{field_name}'] 重复声明")
        declared_names.add(field_name)
        state_fields.append((field_name, _validate_field_decl(raw_decl, field_name, where)))

    # 7. rules：非空列表，逐条校验
    rules_doc = doc["rules"]
    if not isinstance(rules_doc, list):
        raise TriggerError(
            f"{where} 的 rules 必须是列表，实际类型为 {type(rules_doc).__name__}"
        )
    if len(rules_doc) == 0:
        raise TriggerError(f"{where} 的 rules 不能为空列表")

    rules: List[TriggerRule] = []
    seen_rule_ids: Dict[str, int] = {}
    unconditional_count = 0
    first_unconditional_id: Optional[str] = None
    first_unconditional_index: Optional[int] = None
    for i, raw_rule in enumerate(rules_doc, start=1):
        r_where = f"{where} 的 rules[{i}]"

        if not isinstance(raw_rule, dict):
            raise TriggerError(f"{r_where} 必须是字典，实际类型为 {type(raw_rule).__name__}")

        unknown_rule = sorted(set(raw_rule.keys()) - set(RULE_ALLOWED_FIELDS))
        if unknown_rule:
            raise TriggerError(
                f"{r_where} 含未知字段: {unknown_rule}（仅允许 {list(RULE_ALLOWED_FIELDS)}）"
            )

        for required in ("rule_id", "units"):
            if required not in raw_rule:
                raise TriggerError(f"{r_where} 缺少必填字段 '{required}'")

        rule_id = raw_rule["rule_id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise TriggerError(
                f"{r_where} 的 rule_id 必须是非空字符串，实际值为 {rule_id!r}"
            )
        if rule_id in seen_rule_ids:
            raise TriggerError(
                f"{r_where} 的 rule_id='{rule_id}' 与 rules[{seen_rule_ids[rule_id]}] 重复"
            )
        seen_rule_ids[rule_id] = i

        when = _validate_when(raw_rule.get("when"), f"{r_where} 的 rule_id='{rule_id}'")

        # when 的字段名必须是已声明的 state 字段（未声明 → 报错，不静默降级）。
        # WHY：trigger.rule_matches 对 state 里缺值的字段返回「不命中」，一条引用未声明
        #      字段的规则会**永远静默地不触发**，零报错零留痕——正是本模块红线 1 的原文
        #      场景（写错一个字母 = 该规则永不触发）。slots 已有对称的装载期校验，
        #      when 是三处引用里唯一漏掉的那处。这里只校验字段名是否已声明，
        #      **不做类型相容性校验**（when 期望值 vs 声明 type/enum 的匹配不在本卡范围），
        #      也不改 _validate_when 的签名与既有校验。
        # when is None（兜底）没有字段名，不涉及本检查，原样放行。
        for field_name in (when or {}):
            if field_name not in declared_names:
                raise TriggerError(
                    f"{r_where} 的 rule_id='{rule_id}' 的 when 含未声明字段 '{field_name}'"
                    f"（state_fields 里没有该字段，必须预先声明；"
                    f"trigger 运行时对缺值字段返回不命中，该规则会永不触发且零留痕）"
                )

        if when is None:
            if unconditional_count > 0:
                raise TriggerError(
                    f"{r_where} 的 rule_id='{rule_id}' 是无条件兜底规则，"
                    f"但 rules[{seen_rule_ids[first_unconditional_id]}] "
                    f"(rule_id='{first_unconditional_id}') 已经是兜底规则"
                    f"（兜底规则最多一条，否则触发顺序不确定）"
                )
            unconditional_count += 1
            first_unconditional_id = rule_id
            first_unconditional_index = i

        units_doc = raw_rule["units"]
        if not isinstance(units_doc, list):
            raise TriggerError(
                f"{r_where} 的 units 必须是列表，实际类型为 {type(units_doc).__name__}"
            )
        if len(units_doc) == 0:
            raise TriggerError(f"{r_where} 的 units 不能为空列表")

        units: List[PlanUnit] = []
        for ui, raw_unit in enumerate(units_doc, start=1):
            u = _validate_unit(raw_unit, f"{r_where} 的 units[{ui}]", phrase_key_set)

            # 槽位名必须是已声明的 state 字段（未声明 → 报错，不静默丢弃）
            for slot_name in u.slots:
                if not isinstance(slot_name, str) or not slot_name:
                    raise TriggerError(
                        f"{r_where} 的 units[{ui}] 的 slots 含非字符串槽位名: {slot_name!r}"
                    )
                if slot_name not in declared_names:
                    raise TriggerError(
                        f"{r_where} 的 units[{ui}] 引用了未声明的槽位 '{slot_name}'"
                        f"（state_fields 里没有该字段，必须预先声明）"
                    )
            units.append(u)

        rules.append(TriggerRule(rule_id=rule_id, when=when, units=tuple(units)))

    # 7b. 位置校验（T16b）：兜底规则（when: null）必须是 rules 的最后一条。
    # WHY：trigger.build_plan 的匹配口径是「按声明顺序取第一条命中」，兜底规则对
    #      **每一个** state 都命中。它若不是最后一条，后面的条件规则一条都轮不到被看到——
    #      配置看起来有条件分支、实际全被兜底吃掉，零报错零留痕（与 T17b 的
    #      「when: null 写成永不命中」是同一类静默降级）。这里只校验位置，
    #      不改匹配口径（plan.py 一行不动）。
    # 放在整条 rules 解析完成**之后**：循环内若已有第二条 when: null，上面的
    # 「至多一条」检查会先按旧口径抛错（消息与改动前同口径）；能走到这里的配置
    # 恰好只有 0 或 1 条兜底规则，位置检查是**新增**的一条，不替换、不弱化旧检查。
    if first_unconditional_index is not None and first_unconditional_index != len(rules_doc):
        shadowed = len(rules_doc) - first_unconditional_index
        shadowed_ids = [raw.get("rule_id") for raw in rules_doc[first_unconditional_index:]]
        raise TriggerError(
            f"{where} 的 rules[{first_unconditional_index}] "
            f"的 rule_id='{first_unconditional_id}' 是无条件兜底规则，"
            f"它后面还有 {shadowed} 条规则"
            f"（rules[{first_unconditional_index + 1}]..rules[{len(rules_doc)}]，"
            f"rule_id={shadowed_ids}）会被它遮蔽"
            f"（trigger 按声明顺序取第一条命中，兜底对每一个 state 都命中，"
            f"后面的条件规则一条都轮不到）；"
            f"兜底规则必须是 rules 的最后一条（rules[{len(rules_doc)}]）"
            f"（把 rule_id='{first_unconditional_id}' 挪到最后）"
        )

    return Trigger(
        trigger_id=trigger_id,
        trigger_version=version,
        budget_chars=budget_chars,
        state_fields=tuple(state_fields),
        phrase_keys=phrase_keys,
        rules=tuple(rules),
        pack_dir=str(root),
    )


def validate_state(state: Any, declared: Any) -> None:
    """校验一份 state 快照是否符合预先声明的字段表；不合规抛 StateError。

    参数：
        state:    state 快照（**必须扁平**：字段名 → 标量值；不得嵌套字典/列表）
        declared: 字段声明表。接受两种输入，同一套校验逻辑：
                  - `Trigger`（load_trigger 的产物）
                  - `{字段名: {layer, type, enum?, min?, max?}}` 的普通字典
                    （与 trigger.json 的 state_fields 同一形状，便于单元测试与外部工具调用）

    校验项（任一失败 → StateError，消息含具体字段名）：
      1. 未声明字段 → 报错（对齐源格式对 typo 的态度，不静默忽略）
      2. 顶层必须是扁平字典（禁止嵌套 dict/list）
      3. 分层纪律：结构化层只允许 str/int/bool；敏感层只允许 str/number/text
      4. **敏感值出现在结构化层 → 报错**（docs/12 §12.10 末的分层红线）
      5. enum / min / max 越界 → 报错；bool 不是合法整数

    返回 None（校验通过）。
    """
    if not isinstance(state, dict):
        raise StateError(
            f"state 必须是扁平字典，实际类型为 {type(state).__name__}"
        )

    # 解析声明表
    if isinstance(declared, Trigger):
        decl_map = dict(declared.state_fields)
        decls = decl_map
        use_decl = True
    else:
        if not isinstance(declared, dict):
            raise StateError(
                f"declared 必须是 Trigger 或字段声明字典，实际类型为 {type(declared).__name__}"
            )
        decl_map = {}
        for name, raw in declared.items():
            if not isinstance(raw, dict):
                raise StateError(
                    f"declared['{name}'] 必须是字典，实际类型为 {type(raw).__name__}"
                )
            decls = {
                "layer": raw.get("layer"),
                "type": raw.get("type"),
                "enum": raw.get("enum"),
                "min": raw.get("min"),
                "max": raw.get("max"),
            }
            decl_map[name] = decls
        use_decl = False

    # 1. 未声明字段（扁平 + 预先声明；typo 必须响）
    undeclared = sorted(set(state.keys()) - set(decl_map.keys()))
    if undeclared:
        raise StateError(
            f"state 含未声明字段: {undeclared}"
            f"（state 字段必须预先声明，已声明的字段为 {sorted(decl_map.keys())}）"
        )

    for field_name, value in state.items():
        if use_decl:
            decl = decl_map[field_name]
            layer = decl.layer
            typ = decl.type
            enum = decl.enum
            f_min = decl.min
            f_max = decl.max
        else:
            raw = decl_map[field_name]
            layer = raw["layer"]
            typ = raw["type"]
            enum = raw["enum"]
            f_min = raw["min"]
            f_max = raw["max"]

        # 2. 扁平：禁止嵌套容器
        if isinstance(value, (dict, list, tuple)):
            raise StateError(
                f"state['{field_name}'] 必须是标量值，实际类型为 {type(value).__name__}"
                f"（state 必须扁平，不得嵌套）"
            )

        # 4. 分层纪律：敏感值不许出现在结构化层
        #    敏感层允许一切标量；结构化层只允许 str/int/bool。
        #    因此「结构化层里出现 float / 金额串 / 原始文本」天然就是类型不合法。
        if layer == LAYER_STRUCTURED:
            if typ not in STRUCTURED_TYPES:
                raise StateError(
                    f"state['{field_name}'] 的声明 type='{typ}' 不是结构化层允许的 "
                    f"{list(STRUCTURED_TYPES)}（敏感值必须声明为 sensitive 层）"
                )
            # 字段名嗅探：账号/金额/原始文本类字段不许进结构化层
            lowered = field_name.lower()
            for marker in SENSITIVE_MARKERS:
                if marker in lowered:
                    raise StateError(
                        f"state['{field_name}'] 被声明为结构化层，但字段名含敏感标记 "
                        f"'{marker}'（账号/金额/原始文本属敏感层，docs/12 §12.10 的分层红线）"
                    )

        # 3. 类型校验（封闭集合，未知类型一律报错，不降级）
        if typ == "str":
            if not isinstance(value, str):
                raise StateError(
                    f"state['{field_name}'] 声明 type='str'，"
                    f"实际类型为 {type(value).__name__}"
                )
        elif typ == "int":
            if not _is_int(value):
                raise StateError(
                    f"state['{field_name}'] 声明 type='int'，"
                    f"实际值为 {value!r}（bool 不是合法整数）"
                )
        elif typ == "bool":
            if not isinstance(value, bool):
                raise StateError(
                    f"state['{field_name}'] 声明 type='bool'，"
                    f"实际值为 {value!r}（bool 必须是 true/false）"
                )
        elif typ == "number":
            if not _is_number(value):
                raise StateError(
                    f"state['{field_name}'] 声明 type='number'，"
                    f"实际值为 {value!r}"
                )
        elif typ == "text":
            if not isinstance(value, str):
                raise StateError(
                    f"state['{field_name}'] 声明 type='text'，"
                    f"实际类型为 {type(value).__name__}"
                )
        else:
            raise StateError(
                f"state['{field_name}'] 的声明 type='{typ}' 不是已知类型"
                f"（结构化层允许 {list(STRUCTURED_TYPES)}，敏感层允许 {list(SENSITIVE_TYPES)}）"
            )

        # 5. enum / min / max 越界
        if enum is not None:
            if value not in enum:
                raise StateError(
                    f"state['{field_name}'] 的值 {value!r} 不在声明的枚举 {list(enum)} 内"
                )
        if _is_int(value):
            if f_min is not None and value < f_min:
                raise StateError(
                    f"state['{field_name}'] 的值 {value} 小于声明的最小值 {f_min}"
                )
            if f_max is not None and value > f_max:
                raise StateError(
                    f"state['{field_name}'] 的值 {value} 大于声明的最大值 {f_max}"
                )


def count_plan_chars(plan: Any) -> int:
    """统计一份 plan 的总字数（去标点，中文字符逐个计）。

    与 docs/12 §12.8 的口径一致：实测语速是按「去标点后计中文字符」算出来的
    （fin-cs 包 102 条资产），预算判据必须用同一口径，否则两边各算各的就成了静默降级。

    参数：
        plan:  已解析的 plan —— 接受 `List[core.protocol.PlanUnit]`（触发器产出的 plan）
               或原始字典列表（JSON 形状）。自由文本单元（有 text 无 key）按 text 计数。

    返回：
        总字数（非负整数）

    异常：
        BudgetError: plan 不是列表 / 单元不是字典
    """
    if not isinstance(plan, list):
        raise BudgetError(
            f"plan 必须是列表，实际类型为 {type(plan).__name__}"
        )

    total = 0
    for index, unit in enumerate(plan, start=1):
        if isinstance(unit, dict):
            text = unit.get("text")
            if not isinstance(text, str):
                raise BudgetError(
                    f"plan 单元 #{index} 缺少可计数的 text 字段（key 单元须先展开为文本）"
                )
        else:
            # core.protocol.PlanUnit：key 单元必须已展开成 text（slots 已在 T17 侧替换）
            text = getattr(unit, "text", None)
            if not isinstance(text, str):
                raise BudgetError(
                    f"plan 单元 #{index} 缺少可计数的 text 字段（key 单元须先展开为文本）"
                )
        total += _char_count(text)
    return total


def _char_count(text: str) -> int:
    """统计一句话的字数：去标点与空白，按字符计（docs/12 §12.8 口径）。"""
    return sum(1 for ch in text if not ch.isspace() and not _is_punct(ch))


def _is_punct(ch: str) -> bool:
    """判断一个字符是否为标点（中英常见句读与括号）。"""
    return ch in "，。！？、；：、“”‘’（）()《》〈〉【】[]…—-~·,.!?;:\"'`"


def check_budget(plan: Any, budget_chars: Union[int, str] = None) -> None:
    """校验一份 plan 的总字数是否在单轮播报预算内；超支抛 BudgetError。

    参数：
        plan:         已展开为文本的 plan（见 count_plan_chars）
        budget_chars: 单轮字数上限。三种来源，优先级从高到低：
                      1. 显式传入的整数
                      2. 显式传入的包目录路径 —— 读该包的 trigger.json 取 budget_chars
                         （缺省 60 字），让调用方只需给一个包路径
                      3. None → 用 DEFAULT_BUDGET_CHARS（60 字，docs/12 §12.8）

    异常：
        BudgetError: 超支（消息**必须同时含实际字数与预算值**）或 plan 形状非法

    返回 None（校验通过）。
    """
    if budget_chars is None:
        limit = DEFAULT_BUDGET_CHARS
    elif isinstance(budget_chars, int) and not isinstance(budget_chars, bool):
        limit = budget_chars
    elif isinstance(budget_chars, (str, Path)):
        # 字符串/路径 = 包目录：读该包的 trigger.json 取 budget_chars。
        # 包目录不存在或格式非法 → TriggerError（原样透出，消息含路径）；
        # 不把这种输入静默当成缺省预算值。
        limit = load_trigger(Path(budget_chars)).budget_chars
    else:
        raise BudgetError(
            f"budget_chars 必须是整数或包目录路径，实际值为 {budget_chars!r}"
        )

    actual = count_plan_chars(plan)
    if actual > limit:
        raise BudgetError(
            f"单轮播报超预算: 实际 {actual} 字 > 预算 {limit} 字"
            f"（docs/12 §12.8：超出必须拆轮，不是警告）"
        )
