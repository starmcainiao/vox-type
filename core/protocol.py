"""
core.protocol — 协议与语义：原语集合、plan 数据结构、解析校验、参数覆盖优先级

职责：定义整个系统唯一被外部依赖的面——plan 协议（播报计划 schema）。
不负责：不含业务话术（packs/）、不含校验实现（compiler/checks/）、不含 TTS（adapters/）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union


# ---------------------------------------------------------------------------
# 1. 原语集合（封闭白名单）
# ---------------------------------------------------------------------------
# WHY：语音链路的固定部分必须是封闭集合，未知原语 = 拒绝，禁止静默降级。
PRIMITIVES: frozenset = frozenset({
    "SAY",
    "SLOT",
    "SAY_LIVE",
    "PAD",
    "LISTEN",
    "PRELOAD",
    "END",
})

# rate 取值范围（枚举）
VALID_RATES: frozenset = frozenset({"slow", "normal", "fast"})

# 关键信息档位——标为 critical 的 plan 单元必须用该档位播报（T19 只增）
# WHY 单列常量而不是复用字符串：critical 的档位选择是 runtime 的判定输入，
#     写在这里保证 core 与 runtime 对"关键信息=slow"只有一个出处。
CRITICAL_RATE: str = "slow"


# ---------------------------------------------------------------------------
# 2. 异常定义
# ---------------------------------------------------------------------------
class ProtocolError(Exception):
    """协议校验异常——所有解析/校验失败统一抛出此异常。

    消息必须包含导致失败的具体值（如未知原语名、非法 rate 值），
    以便上层定位问题，不允许吞掉错误上下文。
    """


# ---------------------------------------------------------------------------
# 2.1 原语校验公开函数
# ---------------------------------------------------------------------------
def validate_primitive(name: str) -> str:
    """校验原语名是否在封闭白名单中。

    参数：
        name: 待校验的原语名

    返回：
        通过校验的原语名（与输入相同）

    异常：
        ProtocolError: 原语名不在 PRIMITIVES 白名单中
    """
    if name not in PRIMITIVES:
        raise ProtocolError(f"未知原语: {name}")
    return name


# ---------------------------------------------------------------------------
# 3. 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanUnit:
    """播报单元：一份 plan 中的一个原子执行项。

    属性：
        action:  原语名（可选，缺省时按结构推断：key→SAY, text→SAY_LIVE）
        key:     话术 key（与 text 二选一）
        text:    自由文本（与 key 二选一）
        rate:    语速，仅允许 slow / normal / fast
        variant: 变体编号（整数）或 "auto"（自动选择）
        slots:   槽位键值对（仅 key 模式下有效）
        critical: 关键信息标记（T19 只增，默认 False）——为 True 时该单元
                  必须按 CRITICAL_RATE（slow）档播报，且缺档时按语速收敛带判回落
    """
    action: str = "SAY"
    key: Optional[str] = None
    text: Optional[str] = None
    rate: str = "normal"
    variant: Union[int, str] = "auto"
    slots: Dict[str, str] = field(default_factory=dict)
    critical: bool = False


Plan = List[PlanUnit]


# ---------------------------------------------------------------------------
# 4. 解析与校验
# ---------------------------------------------------------------------------
def _validate_unit(raw: Any, index: int) -> PlanUnit:
    """校验并转换单个原始单元字典为 PlanUnit。

    参数：
        raw:   原始字典（来自 JSON）
        index: 单元序号（用于错误信息定位）

    返回：
        合法的 PlanUnit 实例

    异常：
        ProtocolError: key/text 二选一校验失败、rate 非法、variant 非法
    """
    if not isinstance(raw, dict):
        raise ProtocolError(
            f"plan 单元 #{index} 必须是字典，实际类型为 {type(raw).__name__}"
        )

    has_key = "key" in raw and raw["key"] is not None
    has_text = "text" in raw and raw["text"] is not None

    # key 与 text 互斥校验：都缺或都给 → 报错
    if not has_key and not has_text:
        raise ProtocolError(
            f"plan 单元 #{index} 必须包含 'key' 或 'text' 之一，但两者均缺失"
        )
    if has_key and has_text:
        raise ProtocolError(
            f"plan 单元 #{index} 不能同时包含 'key' 和 'text'"
        )

    # rate 校验：只允许 slow / normal / fast，不得静默回落
    rate = raw.get("rate", "normal")
    if rate not in VALID_RATES:
        raise ProtocolError(
            f"plan 单元 #{index} 的 rate 必须是 'slow'/'normal'/'fast' 之一，"
            f"实际值为 '{rate}'（不允许静默回落到 normal）"
        )

    # variant 校验：允许整数或 "auto"
    variant = raw.get("variant", "auto")
    if variant != "auto" and not isinstance(variant, int):
        raise ProtocolError(
            f"plan 单元 #{index} 的 variant 必须是整数或 'auto'，"
            f"实际值为 {variant!r}"
        )

    # slots 校验：仅 key 模式下使用
    slots = raw.get("slots", {})
    if not isinstance(slots, dict):
        raise ProtocolError(
            f"plan 单元 #{index} 的 slots 必须是字典，"
            f"实际类型为 {type(slots).__name__}"
        )

    # action 校验：显式给出时必须在 PRIMITIVES 白名单中，缺省时按结构推断
    if "action" in raw and raw["action"] is not None:
        validate_primitive(raw["action"])
        action = raw["action"]
    else:
        # 按结构推断：有 key → SAY，有 text → SAY_LIVE
        action = "SAY" if has_key else "SAY_LIVE"

    # critical 校验（T19 只增）：必须是真正的 bool。
    # WHY 显式排除 bool 之外的"看起来像真值"的写法（"true" / 1 / "yes"）：
    #     关键信息标记一旦放宽，runtime 就无法区分"策划漏标"与"脚本写错"——
    #     而漏标会让关键信息按 normal 档播出去，属于静默降级。
    critical = raw.get("critical", False)
    if not isinstance(critical, bool):
        raise ProtocolError(
            f"plan 单元 #{index} 的 critical 必须是 bool，"
            f"实际类型为 {type(critical).__name__}（值 {critical!r}）"
        )

    return PlanUnit(
        action=action,
        key=raw.get("key"),
        text=raw.get("text"),
        rate=rate,
        variant=variant,
        slots=slots,
        critical=critical,
    )


def parse_plan(raw_plan: List[Any]) -> Plan:
    """解析原始 JSON plan 为经过校验的 PlanUnit 序列。

    参数：
        raw_plan: 原始 plan 列表（来自 JSON 解析后的 list）

    返回：
        校验通过的 PlanUnit 列表

    异常：
        ProtocolError: plan 非列表、单元缺失、原语/字段校验失败
    """
    if not isinstance(raw_plan, list):
        raise ProtocolError(
            f"plan 必须是列表，实际类型为 {type(raw_plan).__name__}"
        )
    if len(raw_plan) == 0:
        raise ProtocolError("plan 不能为空列表，必须包含至少一个播报单元")

    return [_validate_unit(unit, i) for i, unit in enumerate(raw_plan)]


# ---------------------------------------------------------------------------
# 5. 参数覆盖优先级
# ---------------------------------------------------------------------------
def resolve_params(
    business_default: Dict[str, Any],
    session_override: Dict[str, Any],
    utterance_explicit: Dict[str, Any],
) -> Dict[str, Any]:
    """三档参数合并：业务默认 < 会话覆盖 < 剧本显式。

    合并策略：
        1. 以 business_default 为基底
        2. session_override 中的 key 覆盖基底同名 key
        3. utterance_explicit 中的 key 覆盖前两层的同名 key

    参数：
        business_default: 业务层默认参数
        session_override: 会话级覆盖参数
        utterance_explicit: 剧本显式指定的参数（最高优先级）

    返回：
        合并后的参数字典（三档完整保留所有 key）
    """
    if not isinstance(business_default, dict):
        raise TypeError(
            f"business_default 必须是字典，实际类型为 {type(business_default).__name__}"
        )
    if not isinstance(session_override, dict):
        raise TypeError(
            f"session_override 必须是字典，实际类型为 {type(session_override).__name__}"
        )
    if not isinstance(utterance_explicit, dict):
        raise TypeError(
            f"utterance_explicit 必须是字典，实际类型为 {type(utterance_explicit).__name__}"
        )

    # 合并顺序：基底 → 会话覆盖 → 剧本显式
    merged: Dict[str, Any] = {}
    merged.update(business_default)
    merged.update(session_override)
    merged.update(utterance_explicit)
    return merged
