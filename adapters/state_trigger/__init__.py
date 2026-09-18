# adapters/state_trigger/__init__.py
# 前置包触发配置（state 快照格式 + trigger.json 映射格式）的公开 API 导出。
# 落点说明：本包在 adapters/（扩展区）。state → plan 是「决定说什么」，属接缝之上，
# 因此不进 runtime/（冻结区）——见 docs/12 的「分层纪律」与 docs/10 §10.1。

from adapters.state_trigger.format import (
    DEFAULT_BUDGET_CHARS,
    LAYER_SENSITIVE,
    LAYER_STRUCTURED,
    PlanUnit,
    StateError,
    StateField,
    Trigger,
    TriggerError,
    TriggerRule,
    BudgetError,
    check_budget,
    count_plan_chars,
    load_trigger,
    validate_state,
)

__all__ = [
    # 公开 API（T16 卡要求的三个）
    "load_trigger",
    "validate_state",
    "check_budget",
    # 预算口径辅助
    "count_plan_chars",
    "DEFAULT_BUDGET_CHARS",
    "LAYER_STRUCTURED",
    "LAYER_SENSITIVE",
    # 数据结构（不可变）
    "Trigger",
    "TriggerRule",
    "PlanUnit",
    "StateField",
    # 异常
    "TriggerError",
    "StateError",
    "BudgetError",
]
