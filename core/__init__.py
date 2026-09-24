# core/__init__.py
# 内核公共接口导出

from .protocol import (
    PRIMITIVES,
    VALID_RATES,
    ProtocolError,
    PlanUnit,
    Plan,
    parse_plan,
    resolve_params,
    validate_primitive,
)
from .metrics_spec import (
    HIT,
    MISS,
    FALLBACK,
    REASON,
    KEY,
    PART,
    RATE,
    VARIANT,
    FIRST_AUDIO_MS,
    PACK_VERSION,
    TS,
    TURN_ID,
    PLAN_ID,
    HIT_RATE,
    PRECAST_RATIO,
    METRIC_FIELDS,
)

__all__ = [
    "PRIMITIVES",
    "VALID_RATES",
    "ProtocolError",
    "PlanUnit",
    "Plan",
    "parse_plan",
    "resolve_params",
    "validate_primitive",
    "HIT",
    "MISS",
    "FALLBACK",
    "REASON",
    "KEY",
    "PART",
    "RATE",
    "VARIANT",
    "FIRST_AUDIO_MS",
    "PACK_VERSION",
    "TS",
    "TURN_ID",
    "PLAN_ID",
    "HIT_RATE",
    "PRECAST_RATIO",
    # METRIC_FIELDS：eval 报告口径的冻结字段名集合（恰好 15 个，core/tests/test_metrics_spec.py
    # 逐字节钉死）。T21 起进 __all__——此前它只在 core.metrics_spec 模块内可见，跨层引用要么
    # 走私有路径要么各自复述集合，「字段名集合」这一公开语义没有对外的合法入口。
    "METRIC_FIELDS",
]
