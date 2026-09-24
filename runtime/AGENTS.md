# runtime/ · 执行器与旁路（冻结区）

## ① 职责 / 不负责什么

**职责**：把一条 `plan`（播报计划）变成声音，并**能短路下游**。
- **短路判定**：命中资产 → 直接播（零 TTS、零模型）；未命中 → 按显式降级规则走
- **槽位处理**：槽值**永远现场合成**（用户数据不入包），前后加微停顿
- **拼接**：句间静音垫 + 段级淡入淡出 + 响度统一（沿用包内资产已是归一化的）
- **双工参数执行**：语速档、留白、耐心窗（输入侧）、打断策略
- **事件与留痕**：命中 / 漏判 / 误判 / 降级（带 key 与原因）/ 首音频延迟

**不负责**：不做语义分析（判定只用确定性路由 + 剧本上下文，语义命中至多提供候选）、不做预铸（`compiler/`）、不实现 TTS（`adapters/`）、不管 ASR 与会话管理。

## ② 输入 / 输出契约

- 输入：`plan`（来自上层编排或状态机）+ 只读资产包 + `adapters/` 引擎。
- 输出：音频流 + **事件流**（结构化，字段与 `eval/` 报告一致）。
- 对外只暴露**一个接缝 + 一个旁路开关**（D3）：LLM/状态机输出 → 本层 → 资产或 TTS。

## ③ 验收条件

1. **命中即零调用**：命中路径的 TTS 调用次数 = 0（测试断言）；
2. **fail-closed**：未命中且未显式开启降级 → **抛错中止**（不得静默换路、不得播别的话术）；
3. **降级必留痕**：开启降级时，每次降级都产生事件（含 key、原因、时间），且计入指标；
4. **槽位不入包**：槽值只出现在运行时合成的音频里，包内不得出现（与 `assets/` 的检查项呼应）；
5. **拼接规程**：段间静音垫与淡入淡出参数化且可测（音频边界无 click —— 以峰值跳变检测为判据）；
6. **双工参数生效**：三档耐心窗与语速档的用例各一，行为可断言。

## ④ 本层数据收集

逐轮事件：`{ts, turn_id, plan_id, key, part, hit|miss|fallback, reason, rate, variant, first_audio_ms, pack_version}`。
**字段名第一天冻结**（否则数据不可比）；真人标注单独存放，不进公开资产。

## ⑤ 依赖边界

- 允许依赖：`core/`（协议）、`assets/`（只读）、`adapters/`（经接口）。
- 禁止：不得 import `compiler/`、`rules/`；不得读写包以外的状态（无隐藏缓存）。

## ⑥ 变更纪律

改判定语义 / 事件字段 = 契约变更 → 同步 `docs/`、`eval/`（报告口径）、`core/`（若涉及协议）。

## ⑦ 冻结状态

**[冻] 冻结区**。执行器内部实现可换（同步/异步、不同播放后端），但**判定语义与事件字段不变**。

## ⑧ 实现变更记录（T19，2026-09-22）

四参数从「只校验」到「被消费」，各至少一个可观测行为：

| 参数 | 被消费的行为 | 事件字段 |
|---|---|---|
| `patience_ms` | 事件流末尾追加等待窗口事件（语义 = 播报完成后麦克风打开时长）；**音频不变** | `LISTEN_MS` |
| `backchannel` | `on` 时累计播报时长 ≥ `patience_ms` 的单元带标记；`off` 恒 `False` | `BACKCHANNEL_OK`（+ `SPOKEN_MS` 判据输入） |
| `barge_in` | 每条单元事件带值；`confirm` 时终态单元（`terminal_keys` 命中或 plan 最后一个）额外带「需确认」 | `BARGE_IN` / `REQUIRES_CONFIRM` |
| `rate_band` | `critical` 单元强制 `CRITICAL_RATE`（slow）；包内缺档时带内回落留痕、带外 fail-closed | `RATE_FALLBACK` / `REQUESTED_RATE` |

**音频与事件分离**（判据要显式区分）：`patience_ms` / `backchannel` / `barge_in`
三个参数在任意取值下**不改变输出 WAV 字节**（sha256 相同，见
`runtime/tests/test_duplex_policy.py::TestAudioImmutability`，含 8 种组合对照）；
只有 `rate_band`（档位选择 → 选到不同档的资产）会改变音频。

**策略流是显式 opt-in**：`Executor(..., policy_stream=True)` 才写双工策略字段并追加
等待窗口事件。默认 `False` = 保持"一个单元恰好一条事件"的旧形状（字段集合逐字节
不变，走 `build_event`）。WHY：`eval/bench.py` 用 `len(events) == units` 做完整性
闸门、既有下游按位置读 `events[0]`，默认开启会让它们静默读到等待窗口事件——
那是"本该命中却换了路径"，属于静默降级。

**新增原因常量**：`REASON_CRITICAL_RATE_OUT_OF_BAND = "critical_rate_out_of_band"`
（`critical` 单元缺关键信息档且带外 → `miss` 并 fail-closed；即使
`allow_fallback=True` 也不放行，因为那是"本该关键信息慢说却跑成常速"）。

**档位差口径**：`duplex.rate_distance()` 按相邻档差 0.20 计（跨两档 0.40）。
故默认 `rate_band=0.15` < 0.20 → 关键信息缺 slow 档判"超带"，fail-closed；
业务把 `rate_band` 放宽到 ≥0.20 → 允许回落并留痕。

**新增测试**：`runtime/tests/test_duplex_policy.py`（46 条，每条可观测行为至少一个
可判红用例 + 负例 + 音频 sha256 不变性）、`core/tests/test_protocol_critical.py`
（18 条，`critical` 正例/负例/常量钉住）。**既有测试文件一行未改**。

---

## ⑨ 实现变更记录（T18 · 2026-09-22）：过期资产拒播

**核心红线**：过期资产**拒播**。`allow_fallback=True` **也不放行**——播报过期事实
比不播更糟（与 T19 的 critical 超带同款理由）。

### 新增原因常量

`REASON_ASSET_EXPIRED = "asset_expired"`。**真源在 `core/metrics_spec.py`**
（只增，不进 `METRIC_FIELDS` / `DUPLEX_FIELDS`），本模块的 `REASON_ASSET_EXPIRED`
是从 `core` 引入的同名别名——取值只有一份，避免两层各写一份字面量。

WHY 单独一个原因而不是复用 `key_not_prebaked`：那条语义是"整条 key 都没预铸"，
这里是"预铸了但已失效"——修复动作完全不同（补铸 vs 刷新内容 / 延长有效期），
共用原因码会让定位的人以为是"没铸"从而做错修复。

### 判定位置与优先级

判定插在**引擎一致性检查之后、包内查表之前**（`_decide_unit`）：

- 排在 `engine_mismatch` 之后 —— 保持既有优先级语义不变（"引擎换了"优先）；
- 排在 `key_not_prebaked` 之前 —— 过期不是"没预铸"，不得用那个原因冒充；
- 状态取 `MISS` 而非 `FALLBACK` —— 过期内容**不允许**现场合成播出，若取
  `FALLBACK` 会让事件语义与事实不符。

`allow_fallback=True` 的独立硬门在 `execute()` 里（与 T19 的 critical 超带并列）：
判定阶段先全部算完，`reason == REASON_ASSET_EXPIRED` 时**无条件抛
`RuntimeMissError`**，不写输出文件、不调 TTS、不播其他话术。

### 判据只有一份

本模块**不重写**过期判据：`_expired_at()` 调 `assets.pack.is_expired(entry, now)`，
与 `lookup` 共用同一个函数。runtime 之所以要自己判一次，是因为 `lookup` 只返回
`None`、不告诉你原因，而**原因必须写进事件**（红线：本该命中却换了路径必须留痕）。

### 时钟注入

`Executor(..., now=None)`：`now=None` = 当前 UTC；显式传入（`datetime` 或 ISO 8601
字符串）用于评测 / 测试**重放**。同一轮 `execute()` 内的所有失效判定都用这一个值。

> **时间断言一律注入 `now`，不得用 `sleep`**——评测要能重放。本卡测试全部注入
> 固定时刻，零 sleep。

### 迁移说明（字段只增 + 旧包 / 旧源行为不变）

- **旧包 / 旧源零改动**：无 `invalid_at` 的条目 `is_expired` 恒 `False`，
  `lookup()` 与 `execute()` 行为**完全不变**（既有 170 条测试逐字节未改、全绿即为
  判据）。
- **新包**：`phrases.json` 加 `ttl` 或 `invalid_at`（互斥），重新 `vox pack build`；
  无迁移脚本。
- **事件流形状不变**：过期单元走 `miss` + `reason=asset_expired`，字段集与既有
  miss 事件一致；`policy_stream` 默认仍为 `False`。

### 判定与验收

- `runtime/tests/test_executor_expiry.py`（21 条）：正例（未来 `invalid_at` →
  正常播 / 零 TTS）、负例四件套（① `lookup` 返回 `None` ② reason = `asset_expired`
  ③ 不写输出文件 ④ `allow_fallback=True` 也拒播）、混合包（只过期那条 miss）、
  旧包在任意 `now=` 下不变、边界 `now == invalid_at` 算过期、常量位置与
  `METRIC_FIELDS` 未被污染。
- **反空转**：把 `assets.is_expired` 改成恒 `False` → 本文件 13 条判红 + assets 侧
  9 条判红（共 22 条），证明负例真的在验过期判定而不是空转。
