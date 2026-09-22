# core/ · 内核（冻结区）

## ① 职责 / 不负责什么

**职责**：定义整个系统的**协议与语义**——唯一被外部依赖的面。
- `plan` 协议（播报计划的 schema：原语序列 + 槽位占位 + 参数）
- **原语集合**（封闭白名单）：`SAY` / `SLOT` / `SAY_LIVE` / `PAD` / `LISTEN` / `PRELOAD` / `END`
- 执行语义（每个原语干什么、参数取值范围与默认值、参数覆盖优先级）
- 资产包格式的**元 schema**（清单/索引/哈希/版本字段）
- 质检规则引擎（规则的表达形式 + 判定结果结构）
- 指标口径（字段名与定义：命中率、预铸时长占比、首音频延迟、降级计数）

**不负责**：不含业务话术（`packs/`）、不含校验实现（`compiler/checks/`）、不含 TTS 实现（`adapters/`）、不做播放（`runtime/`）。

## ② 输入 / 输出契约

- 输入：无（最底层）。
- 输出：可被其他所有层 import 的**公共接口 + schema + 常量**。
- **反向禁止**：core 不得 import 任何其他层（包括 `rules/`）。

## ③ 验收条件（可判定 / 可测）

1. **原语封闭**：出现未定义原语时，解析必须**报错并给出原语名**（不得静默忽略）——负例测试必须存在；
2. schema 可校验：给一份非法 `plan`（缺参数/未知原语/越界取值）必须被拒；
3. 参数覆盖优先级可测：业务默认 < 会话覆盖 < 剧本显式（三档用例各一）；
4. 指标字段定义有单测钉住（字段名改了必须测试失败）。

## ④ 本层数据收集

无（core 只定义字段，不产生数据）。指标字段定义须与 `eval/` 的报告字段一致。

## ⑤ 依赖边界

- 允许依赖：Python 标准库；schema 校验库（如 jsonschema）可选。
- 禁止：import 其他层；禁止在 core 里写业务默认值（默认值属于包级配置）。

## ⑥ 变更纪律

改原语集合/schema = **破坏性变更** → 必须：升级大版本、在 `docs/` 记录迁移说明、同步改 `rules/`（规则文档）与 `compiler/checks/`（校验器）。

## ⑧ 变更记录与迁移说明（T19，2026-09-22）

**本次改动（只增字段）**

1. `metrics_spec` 新增 8 个双工策略字段常量：`BARGE_IN` / `REQUIRES_CONFIRM` /
   `BACKCHANNEL_OK` / `PATIENCE_MS` / `SPOKEN_MS` / `REQUESTED_RATE` /
   `RATE_FALLBACK` / `LISTEN_MS`。
2. `PlanUnit` 新增可选字段 `critical: bool = False`（关键信息标记）；`parse_plan`
   对 `critical` 做类型校验（非 bool → `ProtocolError`，消息含单元序号与实际类型）。
3. 新增常量 `CRITICAL_RATE = "slow"`（关键信息档位的唯一出处）。

**为什么不升 `protocol_version`**

`core` 既有口径没有针对"只增可选字段"的升版条款；本次只增、不改、不删任何既有
字段与语义（旧 plan 无 `critical` → 默认 `False`；`METRIC_FIELDS` 仍是 15 个，
既有断言逐字节不变）。故 `protocol_version` 保持不动，按"只增字段、向后兼容"
处理，迁移说明写在此处。

**迁移说明（旧 plan / 旧事件如何被消费）**

- **旧 plan（无 `critical`）**：解析通过，`critical` 取 `False`，runtime 侧
  档位解析与既往完全一致（不强制 slow、不回落、不留痕）。
- **新 plan（带 `critical: true`）**：runtime 强制 `CRITICAL_RATE`（slow）档；
  包内缺 slow 档时按 `rate_band` 判回落（带内 → 用最近可用档 + 事件留痕
  `rate_fallback` + `requested_rate`；带外 → `miss` 原因 `critical_rate_out_of_band`
  并 fail-closed 中止，**即使 `allow_fallback=True` 也不得用别的档合出音频**）。
- **新事件字段只出现在策略流**：`runtime.Executor(policy_stream=True)` 才会在单元
  事件上写双工策略字段、并在末尾追加等待窗口事件（`LISTEN_MS`）。默认
  `policy_stream=False` 时事件流形状与既往逐字节一致（一个单元恰好一条事件、
  字段集合不变）——这是"不静默降级"的落点：既有下游（`eval/bench.py` 的
  `len(events) == units` 完整性闸门、`cli` 的逐事件打印、`adapters` 的
  `events[0]` 读取）不会因为新事件形态被静默改路径。
- **字段集合分家**：`METRIC_FIELDS`（15 个，eval 报告口径）保持冻结不变；
  双工策略字段单独收集在 `DUPLEX_FIELDS`，二者并集为 `ALL_EVENT_FIELDS`。
  新增常量**不得**并入 `METRIC_FIELDS`（`core/tests/test_metrics_spec.py`
  逐字节钉死 15 个）。

## ⑦ 冻结状态

**[冻] 冻结区**。插件适配器与本内核的版本对应关系：适配器声明 `requires_core: ^x.y`，内核只允许**加能力不改语义**。

## ⑨ 结构预算（T21 登记）

结构预算：可执行行数阈值 <= 150

（机器可读标记行。脚本 `tools/structure_budget/check.py` 只认这行；
**不要在这行加任何修饰符**——加粗星号会让正则匹配失败，等于阈值消失。）

口径：`tokenize` 后 `NEWLINE` token 计数（一条逻辑语句结束时的换行），
排除注释与 docstring；不含 `tests/` 与 `__pycache__`。
与 `adapters/AGENTS.md §⑨` 的 145–150 带同算法。

阈值出处与 `adapters/AGENTS.md §②`「单个适配器 ≤ 150 行」同源（本仓 R09 的
统一体量预算）。core 层明写于此是为了让 `tools/structure_budget/check.py` 有
**机器可读**的阈值来源——脚本不另立一套阈值。超限且未登记理由的文件 →
检查非 0 退出。

当前各文件行数快照见 `tools/structure_budget/LEDGER.md`（脚本生成，不要手改数字）。

### T21 #18 公开面变更

`METRIC_FIELDS`（eval 报告口径的冻结字段名集合，恰好 15 个）自 T21 起进入
`core/__init__.py` 的 `__all__`。此前它只在 `core.metrics_spec` 模块内可见，
跨层引用要么走模块私有路径、要么各自复述集合——「字段名集合」这一公开语义
没有对外的合法入口。字段内容**一字未改**（`core/tests/test_metrics_spec.py`
仍逐字节钉死 15 个）。
