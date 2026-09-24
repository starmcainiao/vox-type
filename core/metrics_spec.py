"""
core.metrics_spec — 指标字段名常量（冻结定义）

职责：定义所有指标字段名的字符串常量，供 eval/ 层引用。
      字段名一经冻结，改名 = 破坏性变更，必须同步 eval/ 报告。
不负责：不产生数据、不做计算、不做统计。

约定：每个常量附中文注释说明口径。
"""

# ---------------------------------------------------------------------------
# 结果判定类字段
# ---------------------------------------------------------------------------

# 快路命中——plan 单元命中预铸资产，直接返回预铸音频
HIT: str = "hit"

# 快路未命中——plan 单元无对应预铸资产，需降级到慢路
MISS: str = "miss"

# 降级回退——原应命中但因校验/版本/一致性等原因被迫走慢路
FALLBACK: str = "fallback"

# 降级原因——自由文本，记录 miss/fallback 的具体原因（如"资产版本不匹配"）
REASON: str = "reason"

# ---------------------------------------------------------------------------
# 计划单元标识字段
# ---------------------------------------------------------------------------

# 话术 key——plan 单元中引用的话术唯一标识
KEY: str = "key"

# 播报序号——同一 turn 内多个 plan 单元的执行顺序（从 1 开始）
PART: str = "part"

# 语速参数——plan 单元指定的语速（slow / normal / fast）
RATE: str = "rate"

# 变体编号——plan 单元指定的变体（整数或 "auto"）
VARIANT: str = "variant"

# ---------------------------------------------------------------------------
# 性能与版本字段
# ---------------------------------------------------------------------------

# 首音频延迟（毫秒）——从请求到首帧音频可播放的时间差
FIRST_AUDIO_MS: str = "first_audio_ms"

# 资产包版本——命中的预铸资产包版本号
PACK_VERSION: str = "pack_version"

# 时间戳——事件发生的 UTC 时间戳（ISO 8601 格式）
TS: str = "ts"

# 会话轮次 ID——用户一轮交互的唯一标识
TURN_ID: str = "turn_id"

# 播报计划 ID——一份 plan 的唯一标识（可关联到上游系统）
PLAN_ID: str = "plan_id"

# ---------------------------------------------------------------------------
# 比率统计字段（eval/ 报告专用）
# ---------------------------------------------------------------------------

# 命中率——命中单元数 / 总单元数（取值 0.0~1.0）
HIT_RATE: str = "hit_rate"

# 预铸时长占比——预铸音频总时长 / 全部音频总时长（取值 0.0~1.0）
PRECAST_RATIO: str = "precast_ratio"

# ---------------------------------------------------------------------------
# 全量字段集合（供批量校验用）
# ---------------------------------------------------------------------------

METRIC_FIELDS: frozenset = frozenset({
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
})

# ---------------------------------------------------------------------------
# 双工策略字段（T19 只增，不进入 METRIC_FIELDS）
# ---------------------------------------------------------------------------
# WHY 单列一个集合而不并入 METRIC_FIELDS：
#     METRIC_FIELDS 是"eval 报告口径"的冻结集合，被 core/tests/test_metrics_spec.py
#     逐字节钉死（恰好 15 个）；双工策略字段只出现在 runtime 的逐单元事件流与
#     plan 尾部的等待窗口事件里，不进 eval 汇总口径。并入会破坏既有断言。
#     本模块纪律仍是"运行时不写字面量键"——只是钉住的集合分两个。

# 打断策略取值——单元事件里的 barge_in 值（allow / confirm），docs/06 §6.2.4
BARGE_IN: str = "barge_in"

# 需确认才可打断——barge_in=confirm 时挂在终态单元上的布尔标记
REQUIRES_CONFIRM: str = "requires_confirm"

# 可发背景回应——backchannel=on 且累计播报时长已达耐心窗后，挂在单元事件上的布尔标记
BACKCHANNEL_OK: str = "backchannel_ok"

# 耐心窗时长（毫秒）——单元事件里冗余一份本 plan 生效的 patience_ms，便于按事件聚合
PATIENCE_MS: str = "patience_ms"

# 已播累计时长（毫秒）——该单元播完后的累计时长，backchannel 判据的输入
SPOKEN_MS: str = "spoken_ms"

# 计划中的语速档位——critical 单元本应使用的档位（slow），与实际播放档区分开留痕
REQUESTED_RATE: str = "requested_rate"

# 档位回落留痕——critical 单元缺 slow 档、且回落档在收敛带内时写 True
RATE_FALLBACK: str = "rate_fallback"

# 等待窗口事件类型标记——plan 末尾追加的"播报完成后麦克风打开时长"事件
LISTEN_MS: str = "listen_ms"

# 等待窗口事件自带上述标记键（值为 patience_ms），不引入额外的枚举字段

# ---------------------------------------------------------------------------
# reason 值常量（T18 只增，不进入 METRIC_FIELDS）
# ---------------------------------------------------------------------------
# WHY 收在 core 而不是留在 runtime：这些是"值"，不是字段名；runtime 与 assets 两侧
#     共用同一 reason 码（runtime 的 REASON_* 只是从本处引入的同名别名），避免
#     "asset_expired" 在两层各写一份字面量。METRIC_FIELDS 是 eval 报告的字段名集合
#     （被 core/tests/test_metrics_spec.py 钉死为 15 个），reason 的**取值**不属于它。
# 资产已过期——命中路径上的条目过了 invalid_at：拒播，且不写输出文件、不降级。
REASON_ASSET_EXPIRED: str = "asset_expired"

DUPLEX_FIELDS: frozenset = frozenset({
    BARGE_IN,
    REQUIRES_CONFIRM,
    BACKCHANNEL_OK,
    PATIENCE_MS,
    SPOKEN_MS,
    REQUESTED_RATE,
    RATE_FALLBACK,
    LISTEN_MS,
})

# 事件合法键全集 = 报告口径字段 + 双工策略字段
ALL_EVENT_FIELDS: frozenset = METRIC_FIELDS | DUPLEX_FIELDS
