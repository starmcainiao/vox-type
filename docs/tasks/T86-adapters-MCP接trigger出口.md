# T86 · adapters：给 `trigger/` 一条产品出口（MCP `vox_plan_from_state`）

> ⚠️ **本卡不建议派给免费档 / 弱模型**（见文末「执行方式」）。它涉及**跨层依赖授权**与公开 API 语义，
> 属"需要判断"的卡；改动本身不大，但边界错了会破坏 D2/D9 纪律。

## 背景（只写必需）

实测（2026-10-12，见 `T84`）：`trigger/` 这条线**能力完整、跑得通**——
`python3 labs/ticket-source/run_e2e.py` → rc=0、**148 条断言全过**、12 轮 `源 → state → plan → 留痕`、
敏感值扫描 4 项全 0。

**但它没有任何产品出口**：`docs/24 §五#5` 记的复跑判据
`grep -rn --include='*.py' "^from trigger" . | grep -v "^./trigger"` 只命中 `labs/ticket-source/`。
`docs/26` 候选队列第 1 条就是"给 trigger 一条出口"。

**本卡走 MCP 路线**（`adapters/` 是扩展区，零开批）；`cli run --state` 那条线要动冻结区 `cli/`，不在本卡。

## 目标（可验收的产物）

- 产物 1：`adapters/mcp_vox/tools.py` —— 新增第 4 个工具 `vox_plan_from_state`
- 产物 2：`adapters/mcp_vox/tests/` 下对应测试（正例 + 两个负例）
- 产物 3：`adapters/AGENTS.md` —— §⑨ 新增一条依赖授权登记（**文本见下，逐字照抄**）
- 产物 4：`adapters/mcp_vox/README.md` —— 工具表补第 4 行

## 工具语义（钉死，不得自由发挥）

```
vox_plan_from_state(pack_dir: str, state: object) -> object
```

- 入参：`pack_dir`（**资产包或包源目录都要能取到 `trigger.json`**——实现按 `trigger/` 的公开 API 约定取；
  若 `trigger.json` 取不到 → `isError: true`，消息点名该路径）；
  `state`：一份状态快照对象（与 `trigger.validate_state` 的入参同形）。
- 出参（`content[0].text` 里的 JSON 字符串）：
  `{"plan": [...], "trigger_id": "...", "budget_chars": ..., "fallback_used": bool}`
  —— `plan` 元素形状与既有 `vox_plan` 保持一致（**但不含音频字节、不含 path**）。
- **不出音频字节、不写盘、不调 TTS**（与既有三个工具同口径）。
- 边界：只走 `trigger` 的**公开 `__all__`**（`load_trigger` / `validate_state` / `build_plan` /
  `TriggerError` / `StateError` / `BudgetError`），**不得 import 下划线私有名**，
  **不得 import `runtime`/`assets` 的私有名**，**不得 import `compiler`**。

## 需要你照抄进 `adapters/AGENTS.md §⑨` 的登记条目（逐字，加在第 3 条之后）

```markdown
5. **依赖授权：`mcp_vox/` 对 `trigger/` 的公开面（T86，2026-10-12 登记）**——第 4 个工具
   `vox_plan_from_state` 要把「状态 → plan」这条能力接给外部 agent，因此允许 `mcp_vox` 依赖
   `trigger/` 的**公开 `__all__`**（`load_trigger` / `validate_state` / `build_plan` / 三个异常类）。
   **边界仍硬**：只走公开名，不得 import 下划线私有名；不得由此把 `trigger` 拉进 `runtime/`；
   本工具**只出控制面**（plan 与元数据），**不出音频字节**——出音仍归进程内 adapter。
   理由：`trigger/` 是"接缝之上"的扩展区部件（`docs/10 §10.1`），MCP 是控制面出口，
   两者同属扩展区，**不构成"内核反向依赖 trigger"**（后者才是禁止的，见 `trigger/AGENTS.md §⑤.4`）。
```

## 允许修改的文件（白名单）

```
允许新增：adapters/mcp_vox/tests/test_plan_from_state.py（tests 目录已有文件则改它）
允许修改：adapters/mcp_vox/tools.py, adapters/mcp_vox/README.md, adapters/AGENTS.md
禁止触碰：其他一切文件（尤其不得改 trigger/、runtime/、assets/、cli/、core/、packs/）
```

## 禁止事项

- 不得新增第三方依赖
- 不得改动本卡白名单以外的任何文件
- **不得修改 `trigger/` 的任何文件**（本卡只消费它的公开 API；发现了 `trigger` 的缺陷 → 写进报告，不许就地改）
- 不得改动既有三个工具的行为与出参
- 不得把本工具做成"带音频"或"带 path"
- 不得让 `runtime/` / `assets/` / `cli/` 反向 import `trigger`（那是纪律问题，本卡不碰）

## 验收标准（逐条可判定，验收方逐条核对）

1. `python3 -m unittest discover -s adapters` → `OK`，条数只增不减；
2. `tools/list` 返回**四个**工具，且第 4 个的 `inputSchema.required` 同时含 `pack_dir` 与 `state`；
3. **正例**：用 `packs/demo-brief` 与一条**手工构造的合法 state**（字段按该包 `trigger.json` 的
   `state_fields` 声明）调用 → `isError` 为 `false`，返回里含 `plan`、`trigger_id`、`budget_chars`；
   且 `plan` 里的 key **都在** 该包 `phrases.json` 里（测试需断言这一条）；
4. **负例 1（未知字段必须报错）**：state 里多一个未声明字段 → `isError: true`，
   且**错误消息包含该字段名**；
5. **负例 2（无兜底且无匹配）**：构造一个所有规则都不匹配的 state → `isError: true`，
   消息含 `trigger_id`；**不得返回空 plan、不得自行挑一条兜底**；
6. **反空转（必查）**：测试必须经 `adapters.mcp_vox.tools.call_tool` 调用**产品 API**，
   不得在测试文件内复制任何规则匹配/预算逻辑（验收方会打开测试逐行看）；
7. 协议不破：`initialize` rc=0、stderr 为空。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须调用产品 API；正例与负例都要有；负例断言必须**指出具体非法值/字段名**；
- 不得为通过测试而放宽校验（尤其不得把 `TriggerError` 吞成空 plan）；
- **模型自评不得作为验收证据**（T01 教训：曾自述"验收 1–8 全 ✅"而实际未实现）。

## 回滚方式

删除新增测试文件 + `git checkout -- adapters/mcp_vox/tools.py adapters/mcp_vox/README.md adapters/AGENTS.md`。

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`（商汤 provider）。
**不建议免费档**：本卡要新建一条**跨层依赖授权**（`adapters → trigger`），并要正确区分
"扩展区之间的授权"与"内核反向依赖"——这是判断题，不是抄写题。
若主模型不可用而必须回落免费档：**必须在报告里点名**，且验收方（我）会额外核对
`adapters/AGENTS.md §⑨` 的登记文本与 `grep -rn "^from trigger"` 的结果。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
