"""
runtime.events — 事件构造（事件字段名一律取自 core.metrics_spec）

职责：把"一个 plan 单元的一次执行结果"打包成结构化事件字典，供 runtime/ 逐轮留痕、eval/ 汇总。
不负责：不做判定（三态与原因由 executor 决定），不做统计口径计算（eval/ 的事）。

字段口径（docs/06 §6.2.7 ①）：
    - 键名只能是 core.metrics_spec 的常量；本模块内不允许出现字符串字面量键。
    - PART 是"同一 turn 内 plan 单元的执行序号（从 1 开始）"，不是资产的 part_index。
    - 三态用 HIT / MISS / FALLBACK 常量作为键（AGENTS.md §④ 的事件形状里
      "hit|miss|fallback" 占独立一位，metrics_spec 没有单独的 result 字段常量）。
    - VARIANT 记实际选中的值：auto 必须在 executor 里解析成整数后才能进事件。
"""

from datetime import datetime, timezone

from core.metrics_spec import (
    FALLBACK,
    FIRST_AUDIO_MS,
    HIT,
    KEY,
    MISS,
    PACK_VERSION,
    PART,
    PLAN_ID,
    RATE,
    REASON,
    TS,
    TURN_ID,
    VARIANT,
)

# 三态白名单：只能用 metrics_spec 里冻结的常量，不接受自造结果名
_VALID_STATES = frozenset({HIT, MISS, FALLBACK})


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class EventError(Exception):
    """事件构造参数非法。

    消息必须含字段名与实际值。事件是留痕的唯一载体，字段不合法的事件
    比没有事件更糟（会污染 eval 口径），所以在构造处就 fail-closed。
    """


# ---------------------------------------------------------------------------
# 构造 API
# ---------------------------------------------------------------------------
def build_event(
    *,
    turn_id,
    plan_id,
    part,
    state,
    key=None,
    rate=None,
    variant=None,
    reason="",
    pack_version=None,
    first_audio_ms=None,
    ts=None,
) -> dict:
    """构造一条单元事件。

    参数：
        turn_id:        会话轮次 ID
        plan_id:        播报计划 ID
        part:           同一 turn 内的执行序号，从 1 开始
        state:          结果三态之一（HIT / MISS / FALLBACK 常量）
        key:            话术 key（自由文本单元为 None）
        rate:           语速档位
        variant:        实际选中的变体（整数）或 None（自由文本、无候选可选）
        reason:         未命中/降级原因；state=HIT 时必须为空
        pack_version:   资产包版本
        first_audio_ms: 首音频延迟（毫秒，plan 级口径，逐事件冗余一份便于按事件聚合）
        ts:             事件时间戳（默认取当前 UTC，测试可注入固定值）

    返回：
        事件字典；键全部来自 core.metrics_spec，三态作为键出现且值为 True。

    异常：
        EventError: state 非法 / part 不是 >=1 的整数 / variant 仍是 'auto' /
                    miss 或 fallback 缺 reason
    """
    # 三态：只认冻结常量
    if state not in _VALID_STATES:
        raise EventError(
            f"state 必须是 {sorted(_VALID_STATES)} 之一，实际值为 {state!r}"
        )

    # part：1 起的真整数（bool 是 int 子类，必须排除）
    if not isinstance(part, int) or isinstance(part, bool) or part < 1:
        raise EventError(f"part 必须是从 1 开始的整数，实际值为 {part!r}")

    # variant：auto 必须已解析；没有可选变体时允许 None
    if variant == "auto":
        raise EventError(
            "variant 必须已解析为整数，不得把 'auto' 写进事件"
        )
    if variant is not None and (
        not isinstance(variant, int) or isinstance(variant, bool)
    ):
        raise EventError(f"variant 必须是整数或 None，实际值为 {variant!r}")

    # 留痕红线：miss / fallback 必须带原因，禁止"没命中但不知道为什么"
    if state != HIT and not reason:
        raise EventError(
            f"state={state!r} 必须带非空 reason（禁止留痕缺失），实际为 {reason!r}"
        )

    event = {
        TS: ts if ts is not None else _utc_now_iso(),
        TURN_ID: turn_id,
        PLAN_ID: plan_id,
        KEY: key,
        PART: part,
        RATE: rate,
        VARIANT: variant,
        REASON: reason,
        PACK_VERSION: pack_version,
        FIRST_AUDIO_MS: first_audio_ms,
    }
    # 三态占独立一位：键就是 metrics_spec 的 HIT / MISS / FALLBACK 常量
    event[state] = True
    return event


def _utc_now_iso() -> str:
    """取当前 UTC 时间戳（ISO 8601，带 Z 后缀），用于事件 TS 字段默认值。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
