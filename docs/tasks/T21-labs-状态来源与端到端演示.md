# T21 · labs：状态来源 + 前置包端到端演示

## 背景（只写必需）

前置包这条线现在已经有了两半：
- **格式**：T16 交付 `adapters/state_trigger/format.py`（`state` 快照格式 + `trigger.json` 映射格式 + 校验器）
- **行为**：T17/T17b 交付 `trigger.py`（`state` → plan，含显式兜底）+ `ledger.py`（轮级留痕）

**缺的那一件是「状态的来源」**——目前 `state` 只能手工构造，所以整条线**从没被真正跑过一遍**。
本卡补上第一个状态来源，并让 `源 → state → plan → 留痕` 端到端跑通，产出**可复现的原始证据**。

**为什么落 `labs/` 而不是 `adapters/`**（三条，缺一不可）：
1. 形态未稳定，照 T11 的前身做法先落实验区（`labs/kefu-bridge/run_session.py` 就是这么起步的）；
2. **真实业务系统（工单/CRM/账务）不在公开仓里**，正式适配器现在做不了——本卡用公开 demo 形态代替；
3. `adapters/AGENTS.md §⑧` 已**明文禁止**再往 `adapters/` 加第二个非引擎同类模块。

规格依据（执行前先读）：
- `docs/12 §12.11`（三个部件：状态层 / 资产层 / 触发与开口层）、`§12.10`（留痕与存储、`state` 分层红线）
- `adapters/state_trigger/README.md` 与 `format.py`（**本卡复用它的校验器，不得重写**）
- 根 `AGENTS.md`（`labs/` = 实验区：一次性实验，**不进任何层契约**，产物自带口径与原始数据）

## 目标（可验收的产物）

全部落在**新目录** `labs/ticket-source/`：

- 产物 1：`tickets.source.json` —— 公开 demo 的「工单系统导出」形态（自造数据，**无任何私人信息**）
- 产物 2：`source_map.json` —— **显式映射表**：源字段 → `state` 字段、派生字段的计算规则、敏感字段声明
- 产物 3：`to_state.py` —— 读源 + 套映射 → `state` 快照（**复用 T16 的 `validate_state`**）
- 产物 4：`run_e2e.py` —— 端到端：源 → state → `build_plan` → `record_turn`，含自断言；失败即非零退出
- 产物 5：`raw/turns.jsonl`、`summary.json`、`README.md`（口径 + 复现命令 + 「这条线现在走到哪、下一步缺什么」）

## 硬要求（逐条都要有可观测的验证）

1. **映射是数据不是代码**：源字段 → state 字段的对应、派生字段（`is_overdue` / `days_left` / `overdue_days` 之类）的**计算规则**，
   必须写在 `source_map.json` 里，**可 Review、可 diff**；`to_state.py` 只做通用执行，**不得把某个字段的映射硬编码进代码**；
2. **未映射的源字段 → 报错**（不得静默丢弃——对齐 `compiler/source.py` 对 typo 的态度）；
3. **敏感字段绝不进 `state` 结构化层**：工单号 / 客户标识等只允许进**敏感层**（或由映射表声明为"不入 state"）；
   落进 `state` 的 structured 层即报错；
4. **不依赖真实时钟**：派生字段需要"今天"的地方**必须能注入**（否则结果不确定、也无法测）；未注入即报错；
5. **fail-closed**：源缺字段 / 类型不符 / 无法计算派生字段 → **抛错，不产出半个 state**；
6. **落盘一律在 `labs/ticket-source/` 内**（留痕、原始数据、摘要都在这里）；不得写进 `packs/`、
   不得写仓外、**不得写仓内其他目录**。

## 允许修改的文件（白名单）

```
允许新增：labs/ticket-source/**（含 raw/、README.md、fixture 与脚本）
允许修改：无
禁止触碰：其他一切文件——尤其不得改 core/ rules/ compiler/ assets/ runtime/ eval/ cli/（冻结区）、
          不得改 adapters/（本卡只**读**它的公开 API，不改任何字符）、不得改 packs/、不得改 docs/
```

## 禁止事项

- **不得改 `adapters/state_trigger/` 的任何文件**（只读其公开 API：`load_trigger` / `validate_state` / `check_budget` / `build_plan` / `record_turn`）
- **不得改 `packs/demo-brief/`**（本卡的 demo 前置包就用它）
- 不得新增第三方依赖；**不得在脚本里联网**；不得启停任何服务
- **不得 `git add` / `git commit`**（提交由主会话做）
- 不得"顺手优化"、"顺手重构"；不得为了让脚本跑通而放宽 T16 的校验

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 labs/ticket-source/run_e2e.py` → **rc 0**，产出 `raw/turns.jsonl` 与 `summary.json`；
2. **端到端断言在内**：源 → state 通过 `validate_state`；`build_plan` 产出的 plan 的每个 key 都 ∈ `phrases.json`；
   `record_turn` 写出留痕且 **留痕行数 == 处理条数**；
3. **负例（脚本内自断言，失败即非零退出）**：
   - 源**缺**必需字段 → 抛错且消息含**该字段名**；
   - 源含**未映射**的多余字段 → 抛错且消息含**该字段名**；
   - 派生字段需要"今天"但**未注入** → 抛错；
4. **敏感值独立复核**（脚本里做，把结果打进 `summary.json` 并在报告里贴）：
   留痕文件里 grep 工单号 / 客户标识 → **0 命中**；
5. **可复现**：同一份源 + 同一注入日期，跑两次 → `summary.json` **逐字段相同**（除时间戳类字段外）；
6. `git status --porcelain -uall` 的新增文件**全部**在 `labs/ticket-source/` 内；
   **冻结区与 `adapters/` 零 diff**（我会跑 `git diff --stat -- core rules compiler assets runtime eval cli adapters packs` 应为空）；
7. `python3 -m unittest discover -s adapters` → 仍 **241 条全绿**（证明本卡没有反向污染既有层）。

## 反空转条款（每张卡必带，T01 教训）

- **必须调用产品 API**：`to_state` 与 `run_e2e` 必须 import `adapters.state_trigger` 的公开函数并用 T16 的校验器，
  **不得在 labs 里复制一份 state/trigger 校验逻辑**（违者整卡退回）；
- **断言必须能被打破**：负例断言要先证明"改坏了会红"（例如把映射表改坏、或在断言前临时注入一个未映射字段）；
- 正例与负例都要有；负例断言**消息含具体非法值**；
- 不得为通过而放宽校验。

## 回滚方式

新增目录 → `git clean -fd labs/ticket-source`。不触碰既有文件，回滚无残留。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T21-labs-状态来源与端到端演示.md)" --dir （仓库根）
```

**数据分级：公开级**——fixture 是**自造**的工单数据，不含任何用户录音、真实会话、内网地址、token。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
