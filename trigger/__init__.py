# trigger/__init__.py
# 前置包触发配置（state 快照格式 + trigger.json 映射格式）的公开 API 导出。
# 落点说明：本包是顶层扩展区 trigger/（2026-09-19 自 adapters/state_trigger/ 迁入）。state → plan 是「决定说什么」，属接缝之上，
# 因此不进 runtime/（冻结区）——见 docs/12 的「分层纪律」与 docs/10 §10.1。

from trigger.format import (
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
# T17 追加：触发器行为侧（state → plan）与轮级留痕
from trigger.plan import (
    TriggerPlan,
    build_plan,
    rule_matches,
)
from trigger.ledger import (
    LedgerError,
    default_ledger_path,
    read_turns,
    record_turn,
    structured_record,
    supervision_pairs,
)
# T16d 追加：前置包 key / state 字段可达性盘点（报告类 API，只报告不报错）
from trigger.reachability import (
    ReachabilityReport,
    check_reachability,
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
    # T17 追加：触发器行为侧
    "build_plan",
    "TriggerPlan",
    # T17c 追加：规则匹配公开 API（规则匹配的唯一实现，覆盖表等工具依赖它）
    "rule_matches",
    # T17 追加：轮级留痕
    "record_turn",
    "structured_record",
    "read_turns",
    "supervision_pairs",
    "default_ledger_path",
    "LedgerError",
    # T16d 追加：可达性盘点（报告不是报错）
    "check_reachability",
    "ReachabilityReport",
]
