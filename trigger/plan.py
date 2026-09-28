"""
trigger.plan — state 快照 → plan（触发器，行为侧）

职责：给定一份 state 快照 + 一份已校验的 Trigger，按 trigger.json 的规则
      选出命中的那条规则，产出 core 协议的 plan（core.protocol.Plan），
      并顺带算出该轮的播报字数（供预算判据与留痕使用）。
不负责：不校验格式（T16 的 format.validate_state / load_trigger 已负责，本模块复用）、
      不写留痕（本包的 ledger.record_turn）、不写盘、不发网络请求、不改内核、不做音频。

与 T16 的关系：format.py 是**格式闸门**（trigger.json 与 state 长什么样算合法），
      本模块是**行为侧**（合法之后选哪条规则、产出哪份 plan 由本模块定）。
      两者的错误消息口径一致（都含具体非法值），不互相降级。

设计红线（逐条对齐本仓既有做法）：
  1. **无兜底 + 无匹配 → fail-closed**：**当且仅当**该包不含兜底规则、且没有任何
     条件规则匹配该 state → 抛 TriggerError，不返回空 plan、不回落到任何默认话术
     （AGENTS.md 红线 4）。对齐 runtime/executor.py 的 allow_fallback=False：
     未命中就中止，不编造内容。
     `when = null` 的兜底规则**是命中**（T16 的 format.py 已定「None/缺省 =
     无条件兜底规则，至多一条」）——它写在 trigger.json 里、可 Review、可审批，
     是**作者显式声明**的兜底，不是实现自己挑的。顺序按 rules 声明走：条件规则在前、
     兜底通常最后，因此条件规则天然优先；兜底规则若被写在前，它自己就会先命中，
     那是作者显式声明的顺序（T16 只约束「至多一条兜底」，不重排顺序）。
     这条红线的真实含义是：**不得由本模块自行挑选兜底**——没有作者声明的兜底规则时，
     本模块不替它找一条、不回落默认话术（「静默降级」指的是这种看不见的实现侧回落，
     不是 trigger.json 里可见的兜底声明）。
  2. **确定性**：同一 state + 同一 Trigger + 同一 turn_id → plan 逐字段相同
     （含顺序、rate、variant）。不读时钟、不做随机选择；变体若由散列选择，
     输入只能是 turn_id 与单元序号（照 runtime/executor.py 对 variant: auto 的
     sha256 做法，键含 NUL 分隔符——sha256 而非内建 hash()，因为 PYTHONHASHSEED
     随进程变化，留痕要能重放）。
  3. **只产出已审核的话术**：plan 每个单元的 key 必须 ∈ trigger.phrase_keys
     （T16 装载期已校验一次；本模块**再断言一次**——plan 是运行时产物，
     两道闸门不算冗余）。
  4. **预算强制**：build_plan 内部调 T16 的 check_budget，超支抛 BudgetError，
     不产出 plan（docs/12 §12.8：超出必须拆轮，不是警告）。
  5. **不产生未审核文本**：plan 单元只带 key + 槽名/槽值，永不带自由文本 text
     （自由文本 = SAY_LIVE，前置包不碰；对齐 C4a unreviewed_text）。

边界声明：本模块**只提案不出声**——产出的是给 runtime/ 执行的**建议** plan，
  不写音频、不调 TTS、不决定打断与耐心窗。
"""

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import PLAN_ID, TURN_ID
from core import PlanUnit as CorePlanUnit
from trigger.format import (
    BudgetError,
    StateError,
    Trigger,
    TriggerError,
    check_budget,
    count_plan_chars,
    validate_state,
)


# ---------------------------------------------------------------------------
# 1. 常量（字段名一律引 core.metrics_spec，不自造字面量）
# ---------------------------------------------------------------------------
# TURN_ID / PLAN_ID 在 core.metrics_spec 里是冻结常量；本模块在此建别名，
# 让 ledger.py 与 plan.py 共用同一份别名（字段名只在 core 里定义一次）。
TURN_ID_FIELD: str = TURN_ID
PLAN_ID_FIELD: str = PLAN_ID

# plan_id 的派生形状："<trigger_id>:<turn_id>"（确定性，便于人工排查）
PLAN_ID_SEP: str = ":"

# 槽位占位符形状：{槽位名}（与 phrases.json 里的 {ticket_id} 同一写法）
_SLOT_OPEN: str = "{"
_SLOT_CLOSE: str = "}"

# 散列键分隔符：与 runtime/executor.py 的 _resolve_variant 一致（\x00）
_HASH_SEP: str = "\x00"


# ---------------------------------------------------------------------------
# 2. 内部辅助
# ---------------------------------------------------------------------------


def _stable_variant(turn_id: Any, part: int, choices: int) -> int:
    """按 turn_id 稳定散列在 [0, choices) 里选一个变体序号。

    口径：同一 turn_id + 同一 part → 同一变体（留痕与评测要能重放）；
          跨 turn_id 通常会变（防复读机）。choices <= 0 → 返回 0。
    """
    if choices <= 0:
        return 0
    digest = hashlib.sha256(
        f"{turn_id}{_HASH_SEP}{part}".encode("utf-8")
    ).hexdigest()
    return int(digest[:16], 16) % choices


def _resolve_variants(trigger: Any, phrase_variants: Optional[Dict[str, Tuple[str, ...]]],
                      source_pack_dir: Any) -> Dict[str, Tuple[str, ...]]:
    """取 {key: variants 元组}。缺省时从包源读取（复用 compiler 公开面，不自造解析）。"""
    if phrase_variants is not None:
        return phrase_variants

    root = source_pack_dir if source_pack_dir is not None else getattr(trigger, "pack_dir", None)
    if not root:
        raise TriggerError(
            f"无法定位包源目录（trigger.pack_dir 为空且未传 source_pack_dir）："
            f"变体表读不到，不编造话术文本"
        )

    from compiler import SourceError, load_source

    try:
        source = load_source(str(root))
    except SourceError as e:
        raise TriggerError(f"包源（pack.json/phrases.json）校验失败: {e}") from e

    return {phrase.key: tuple(phrase.variants) for phrase in source.phrases}


def _expand_text(unit: Any, variants: Optional[Tuple[str, ...]], variant: Any,
                 state: Dict[str, Any], index: int) -> str:
    """把一个单元展开为可计数文本；失败一律抛 TriggerError（消息含单元序号）。"""
    if variants is None or len(variants) == 0:
        raise TriggerError(
            f"plan 单元 #{index} 的 key '{unit.key}' 查不到话术变体（不编造播报文本）"
        )
    if not isinstance(variant, int) or isinstance(variant, bool) \
            or variant < 0 or variant >= len(variants):
        raise TriggerError(
            f"plan 单元 #{index} 的 variant {variant!r} 越界"
            f"（key '{unit.key}' 只有 {len(variants)} 个变体，合法索引 0..{len(variants) - 1}）"
        )
    text = variants[variant]
    for slot in unit.slots:
        if slot not in state:
            raise TriggerError(
                f"plan 单元 #{index} 的槽位 '{slot}' 在该 state 里不存在"
                f"（state 必须提供槽值，不做静默替换成空串——空串会让预算计数失真）"
            )
        text = text.replace(
            f"{_SLOT_OPEN}{slot}{_SLOT_CLOSE}", str(state[slot])
        )
    return text


# ---------------------------------------------------------------------------
# 3. 公开 API
# ---------------------------------------------------------------------------
def rule_matches(rule: Any, state: Dict[str, Any]) -> bool:
    """判断一条规则是否匹配该 state（规则匹配的**唯一实现**）。

    职责：给定一条规则（TriggerRule）与一份 state 快照，判定该规则的 when 条件
          是否在该 state 上成立。这是全仓唯一的匹配实现——兜底语义、字段缺值
          语义、区间比较都在这里；其他任何层要做「某规则是否匹配某 state」的
          判断，必须调本函数，不得复制这段逻辑（复制即违反 D7「不写他人的活」，
          labs 的覆盖表曾为此踩过一次）。

    参数：
        rule:  一条规则。本函数只消费 rule.when 这一个属性：
                 - dict = 条件规则，形如 {字段名: 期望值} 或
                        {字段名: {"gt"/"gte"/"lt"/"lte": 边界值}}（可组合）
                 - None = 无条件兜底规则
               不消费 rule.rule_id / rule.units，因此可用只提供 .when 的最小
               替身当参数。注意：when 属性必须**存在**（值为 None 或 dict）；
               属性整个不存在会抛 AttributeError——那是调用方传了个不是规则的
               东西，不是本函数的判定输入。
        state: state 快照（扁平字典，键为已声明字段名，值为其声明类型）。
               本函数**不做格式校验**（校验归 T16 的 validate_state），
               只按「字段在不在」判定。

    返回值：
        bool —— True = 该规则的 when 在此 state 上成立；False = 不成立。
        注意 False 有两种语义完全不同的来源，调用方需要靠 state 自行区分：
          - 条件规则真的不匹配（值不符合期望）
          - 条件**无法判定**（字段在 state 里缺值）
        两条路径都不静默当成满足——这就是 fail-closed 在匹配层的落点。

    边界（四条语义，逐条不可改，改动等于改行为）：
        1. when is None → **命中**。兜底是作者在 trigger.json 里显式声明的
           规则，不是实现自己挑的（T16 已校验「至多一条」；优先级靠 rules
           声明顺序，见模块红线 1）。
        2. 字段在 state 里缺值 → 不命中。缺值意味着条件无法判定，
           绝不静默当成满足。
        3. 比较符越界（when 的 dict 里出现 gt/gte/lt/lte 之外的键）→
           该键被忽略，其余比较符照判。越界比较符本身由 T16 的
           validate_state 在装载/校验阶段拦下，本函数不重复校验、也不因此报错。
        4. 类型不可比较（value 与边界值不可比）→ 该字段判为不命中
           （捕 TypeError 后返回 False），不抛异常、不静默当成满足。

    调用方：
        - build_plan（本模块）：按 trigger.rules 顺序逐条判定，取第一条命中。
        - trigger.reachability.check_reachability：
          规则/字段可达性盘点，用它判断某规则能否被某 state 命中。
        - labs/ticket-source/run_e2e.py：端到端实验的规则覆盖表，
          用它对每条源行的 state 逐条判定，生成 summary.json#rule_matching_matrix。
        - trigger.tests（test_trigger.py 的公开 API 直测）。
    """
    when = rule.when
    if when is None:
        return True

    for field_name, expected in when.items():
        if field_name not in state:
            return False

        value = state[field_name]
        if isinstance(expected, dict):
            for op, op_value in expected.items():
                try:
                    if op == "gt" and not (value > op_value):
                        return False
                    if op == "gte" and not (value >= op_value):
                        return False
                    if op == "lt" and not (value < op_value):
                        return False
                    if op == "lte" and not (value <= op_value):
                        return False
                except TypeError:
                    return False
        elif value != expected:
            return False
    return True


def build_plan(trigger: Any, state: Any, *, turn_id: Any = None,
               plan_id: Any = None, source_pack_dir: Any = None,
               phrase_variants: Optional[Dict[str, Tuple[str, ...]]] = None) -> Any:
    """从 state 快照产出一份 plan（core 协议形状）。

    参数：
        trigger:         format.load_trigger 的产物（不可变 Trigger）
        state:           state 快照（扁平字典；必须通过 format.validate_state）
        turn_id:         会话轮次 ID（参与变体的稳定散列；可注入，便于重放与测试）
        plan_id:         播报计划 ID（缺省由 trigger_id + turn_id 确定性派生）
        source_pack_dir: 包源目录（缺省用 trigger.pack_dir）；供读话术变体表与预算

    返回：
        TriggerPlan —— 一份不可变的运行时 plan，含：
            .plan    core.protocol.PlanUnit 列表（可交给 runtime 执行）
            .rule_id 命中的规则 rule_id（留痕用）
            .chars   该轮播报字数（去标点口径，docs/12 §12.8）
            .budget  该包的单轮预算（docs/12 §12.8）
            .keys    plan 引用的话术 key 序列（按出现顺序，留痕用）
            .plan_id 播报计划 ID

    异常：
        StateError:   state 不合法（复用 T16 的 validate_state）
        TriggerError: 没有任何规则命中 / 引用未审核 key / 槽位缺值 / 变体越界
        BudgetError:  单轮超预算（消息含实际字数与预算值，复用 T16 的 check_budget）
    """
    # 0. 复用 T16 的 state 校验器（不自造校验）
    validate_state(state, trigger)

    # 1. 规则匹配：按 trigger.json 的规则顺序取第一条命中的规则（不排序，保确定性）。
    #    兜底规则（when = null）同样参与匹配，顺序由 rules 声明决定（见模块红线 1）。
    matched = None
    has_fallback = False
    for rule in trigger.rules:
        if rule.when is None:
            has_fallback = True
        if rule_matches(rule, state):
            matched = rule
            break

    # 2. 无兜底 + 无匹配 → fail-closed：不返回空 plan、不回落默认话术。
    #    兜底规则存在时它已被上面当成命中处理，走不到这里；只有「既没有作者声明的
    #    兜底、又没有任何条件规则命中」才抛错（不得由本模块自行挑选兜底）。
    if matched is None:
        declared = [name for name, _decl in trigger.state_fields]
        rule_ids = [rule.rule_id for rule in trigger.rules]
        raise TriggerError(
            f"没有任何规则命中该 state（trigger_id={trigger.trigger_id!r}）："
            f"已声明字段 {declared}，state 值 {state!r}，"
            f"规则 {rule_ids} 均未匹配，且该包未声明兜底规则（when = null）"
            f"→ fail-closed（不返回空 plan、不回落默认话术）；"
            f"请在 trigger.json 里显式声明一条兜底规则"
        )

    # 3. 话术变体表（供变体选择与文本展开）
    variants_by_key = _resolve_variants(trigger, phrase_variants, source_pack_dir)

    # 4. 逐单元展开：key 复核 + 变体选择 + 文本展开
    plan: List[CorePlanUnit] = []
    texts: List[str] = []
    keys: List[str] = []
    for index, unit in enumerate(matched.units, start=1):
        # 4a. 已审核话术复核（第二道闸门：plan 是运行时产物）
        if unit.key not in trigger.phrase_keys:
            raise TriggerError(
                f"plan 单元 #{index} 引用了未审核的 key '{unit.key}'"
                f"（不在该包的 phrase_keys 内，对齐 C3b key_not_in_library）"
            )

        # 4b. 变体：整数直接透传；'auto' 由 turn_id 稳定散列派生
        variants = variants_by_key.get(unit.key)
        variant = unit.variant
        if variant == "auto":
            choices = len(variants) if variants else 0
            variant = _stable_variant(turn_id, index, choices)

        # 4c. 文本展开（槽值只来自 state 的已声明字段；缺值/越界一律报错）
        texts.append(_expand_text(unit, variants, variant, state, index))
        keys.append(unit.key)
        slots = {slot: str(state[slot]) for slot in unit.slots}
        plan.append(CorePlanUnit(
            action=unit.action,
            key=unit.key,
            text=None,
            rate=unit.rate,
            variant=variant,
            slots=slots,
        ))

    # 5. 预算强制（复用 T16 的 check_budget，口径单一，不复制计数逻辑）
    check_budget([{"text": text} for text in texts], trigger.budget_chars)

    # 6. plan_id：缺省由 trigger_id 与 turn_id 确定性派生
    resolved_plan_id = plan_id if plan_id is not None else f"{trigger.trigger_id}{PLAN_ID_SEP}{turn_id}"

    return TriggerPlan(
        plan=plan,
        rule_id=matched.rule_id,
        chars=count_plan_chars([{"text": text} for text in texts]),
        budget=trigger.budget_chars,
        keys=tuple(keys),
        plan_id=resolved_plan_id,
    )


class TriggerPlan:
    """一份运行时 plan（不可变视图）。

    只承载「触发器这一侧」的产物：core 协议的 plan 单元序列 + 命中信息 + 字数。
    不含 state（state 的落盘归 ledger，且敏感层绝不落盘）。
    """

    __slots__ = ("plan", "rule_id", "chars", "budget", "keys", "plan_id")

    def __init__(self, plan: Sequence[CorePlanUnit], rule_id: str, chars: int,
                 budget: int, keys: Tuple[str, ...], plan_id: Any) -> None:
        self.plan: Tuple[CorePlanUnit, ...] = tuple(plan)
        self.rule_id: str = rule_id
        self.chars: int = chars
        self.budget: int = budget
        self.keys: Tuple[str, ...] = tuple(keys)
        self.plan_id: Any = plan_id

    def __len__(self) -> int:
        return len(self.plan)

    def __iter__(self):
        return iter(self.plan)

    def __getitem__(self, index):
        return self.plan[index]

    def __repr__(self) -> str:  # pragma: no cover - 仅便于调试
        return (
            f"TriggerPlan(rule_id={self.rule_id!r}, plan_id={self.plan_id!r}, "
            f"units={len(self.plan)}, keys={list(self.keys)}, "
            f"chars={self.chars}, budget={self.budget})"
        )
