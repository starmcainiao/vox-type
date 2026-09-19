# trigger/ · 前置包触发器与轮级留痕（扩展区）

**由 `adapters/state_trigger/` 整体迁入（2026-09-19，T28）**：格式定义与校验、`state → plan`
的行为侧、轮级留痕、可达性盘点。迁出记录见 `adapters/AGENTS.md §⑧`。

## ① 职责 / 不负责什么

**职责**：前置包的**运行期输入协议与其校验器**，外加按规则决定「这一轮打算说什么」：
- `format.py`（T16）：`state` 快照格式 + `trigger.json` 映射格式的定义与校验
  （`load_trigger` / `validate_state` / `check_budget` / `count_plan_chars`）
- `plan.py`（T17）：`build_plan(trigger, state) → plan` 与 `rule_matches`——纯函数、确定性
- `ledger.py`（T17）：`record_turn(...)` JSONL 追加写，留「该说什么」的正样本
- `reachability.py`（T16d）：`check_reachability` 前置包 key / state 字段可达性盘点（报告类，只报告不报错）

**不负责**：不做音频、不写音频、不调 TTS（`runtime/`）、不做拼接与命中判定（`runtime/`）、
不做预铸调度（`compiler/`）、不做事件流（事件契约在冻结区）、不含业务逻辑（业务判断属于
`packs/` 的数据与 `trigger.json` 的规则）。
**只提案不出声**：本层产出的是**建议 plan**，交给 `runtime/` 执行；双工四参数
（`patience_ms` / `barge_in` / `backchannel` / `rate_band`）的消费方不在这里。

## ② 输入 / 输出契约

- 输入：`packs/<前置包>/trigger.json` + `phrases.json`（`pack.json` / `phrases.json` 的校验归 `compiler` 公开面）、
  一份 `state` 快照；输出：`core.protocol.Plan`（`build_plan`）、留痕 JSONL（`record_turn`）、
  `ReachabilityReport`（`check_reachability`，**不抛业务异常**）。
- 口径冻结：按 `trigger.json` 的 `rules` 顺序取**第一条**命中（不排序，保确定性）；
  `when = null` 是**显式兜底且算命中**（作者声明的至多一条），**不存在兜底且无匹配**才抛 `TriggerError`——
  不得由实现自行挑选兜底；缺槽值一律抛错，不静默替换空串。
- **行数口径：不适用「≤150 行」量化标准**——本层的同类是 `compiler/source.py` 312 行、
  `compiler/script.py` 373 行（`format.py` 945 行、去注释口径 663 行）。登记于此是为了不出现
  「静默越界」，不是为了开后门；拆分不在本层纪律内，留待另卡。

## ③ 验收条件

1. **159 条测试全绿**：`python3 -m unittest discover -s trigger`（原样自 `adapters/state_trigger/tests`
   迁入，一条不删、断言不改）；`adapters` 侧降为 152 条，十根总数不变；
2. **未知字段一律报错**：`trigger.json` 的 typo 不许静默丢弃（写错一个字母 = 该规则永不触发，
   而零留痕，正是「静默降级」原型）；错误消息含具体非法值；
3. **fail-closed**：超预算抛 `BudgetError`（含实际字数与预算值）、无兜底且无匹配抛 `TriggerError`
   （含 `trigger_id`）——不产出 plan、不回落任何默认话术、不返回空 plan；
4. **确定性**：同一 `state` + 同一 `Trigger` → plan 逐字段相同；`variant` 稳定散列（sha256）在同一
   `turn_id` 下可重放；
5. **敏感层绝不落盘**：留痕的 `state` 块只取 `layer: "structured"` 的字段（按 `trigger.json` 声明取，
   不靠字段名嗅探）；写入失败抛 `LedgerError`（消息含目标路径与失败原因），缺父目录不自动创建。

## ④ 本层数据收集

正样本通道由本层的 `ledger.record_turn` 承担（state 结构化层 + plan 的配对，`supervision_pairs`
直接返回这对）。负样本通道（事件流的 `miss` + `reason`）**不碰**——事件契约在冻结区。
字段名一律引 `core.metrics_spec` 的常量（`PLAN_ID` / `TURN_ID` / `TS` / `KEY`），不自造字面量。

## ⑤ 依赖边界

本层是「**决定说什么**」的部件，属**接缝之上**（`docs/10 §10.1`）。它必须守住的边界：

1. **只许依赖 `core/` 与 `compiler/` 的公开 API**（`compiler/__init__.py` 的 `__all__`），
   **不得走内部模块路径**（`from compiler.source import …` 违反该层的 §⑤；`from compiler import …` 可以）；
2. **不含业务逻辑**（业务判断属于 `packs/` 的数据与 `trigger.json` 的规则，不属于本层）；
3. **不做 I/O 之外的副作用**：只读它声明的那几个包源文件；不写盘、不发网络请求、不起进程
   （`record_turn` 的写盘是它唯一声明的写，且默认落在仓外）；
4. **不得被 `runtime/` / `assets/` / `eval/` / `cli/` import**——本层是"接缝之上"的部件
   （`docs/10 §10.1`：该说不该说由本层定，但执行归 `runtime/`），内核不得反向依赖它。

## ⑥ 变更纪律

本层是 `state → plan` 的**契约面**：改格式字段、改比较符口径、改兜底语义、改留痕行结构，
都是**有版本的契约变更**——必须同步更新 `packs/` 的前置包与 `labs/` 的实验，不得悄悄改语义。
加能力不改语义（新增可选字段、新增报告类 API）允许按 `core/AGENTS.md §⑦` 的口径追加。
本层**禁止静默降级**：任何「本该命中却换了路径」必须报错或留痕计数。

## ⑦ 冻结状态

**[扩] 扩展区**。可以无限新增；新增不需要改内核，只需通过本层测试与公开 API 一致性。
`trigger/` 是 2026-09-19 拍板的独立扩展区（`docs/13 §八#15`），迁出后 `adapters/AGENTS.md §⑧`
关于「不得往 `adapters/` 加第二个同类模块」的禁令随之解除。
