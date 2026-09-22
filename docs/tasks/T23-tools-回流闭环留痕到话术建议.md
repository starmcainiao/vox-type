# T23 · tools：回流闭环——留痕 → 话术建议（立项 M3 核心项）

## 卡号 / 标题
`T23 · tools[扩]：回流闭环——从轮级留痕与事件流挖「本该预铸却未预铸」的话术候选 → 建议清单（对接 docs/14 准入申报）`

## 背景

- **立项 M3 项**「回流闭环（日志挖掘 → 字模建议）」——`docs/13 §六#3` 当前标 **❌ 零实现**；`docs/19 §二` 把它列为 M3 待做第一项。
- **数据源已具备**（这是本卡能落地的原因）：
  - **轮级留痕**（T17）：`trigger/ledger.py` 每轮一条 `(state 快照, plan, turn_id)`，可拼出 `(状态, 话术)` 监督配对；
  - **事件流**（T07）：每单元带三态（`hit` / `miss` / `fallback`）+ `live_text`（未命中时实际合成播出的话术）；
  - **参考实现**（T22）：`labs/multi-industry-corpus/mine_recompute.py` 在 CrossWOZ 上挖出 737 候选——**判据可借鉴，但那是"离线语料挖掘"，本卡是"从本仓自己的留痕挖"**，两者不要混。
- **准入判据**：`docs/14-预铸准入判据.md`（A1/A2 + 禁四类 ⅰ–ⅱ；`packs/AGENTS.md §③` 第 6 条要求内容准入过它）。
  **关键边界：A1/A2 是语义判断，机器判不了** → 本卡只做**形式条款机器化**，A1/A2 标注"待人工判定"（申报制，不越权）。

## 目标（三件）

### 1. 挖掘器（`tools/feedback_mining/`）

```
输入：① 轮级留痕（JSONL，T17 格式）② 事件流（JSONL，T07 格式）
过程：取三态为 miss / fallback 的单元 → 用其 live_text（实际播出的话术）→ 归一化后聚合
      （归一化函数复用 adapters.framework_kefu.normalize_text，不得自造）
      → 统计：出现次数、涉及状态集合、样本 turn_id 列表、首次/末次出现
输出：suggestions.json（候选按出现次数降序；同次数按文本字典序——**确定性排序**）
```

### 2. 形式条款机器化检查（对接 `docs/14`）

- 对每个候选跑**形式条款**（B1–B5：无槽位占位符 / 单句（复用 `compiler.source._SENTENCE_TERMINATORS`）/ 长度上限 / 非空 / 无敏感字段）→ 标注 `pass` / `fail` + **具体原因**；
- **A1/A2 归类字段写 `"pending_human"`**（机器不判语义）——这是诚实边界，不得让脚本猜；
- **不符合形式条款的候选不得静默丢弃**：进 `rejected_candidates` 并写明原因（台账的另一半，同 `corpus-harvest` 的 rejected_sources 纪律）。

### 3. 报告与对接

- `report.json`（含口径、样本量、`provenance`：输入文件 sha256 + 本仓 commit）+ `README.md`（口径 / 复现命令 / 边界）；
- README 写明**下一步人工动作**：候选 → `admission.md` 申报（`docs/14`）→ `vox pack check` → 铸包；
- **演示数据**：用本仓可造的场景（T17 留痕 demo 或 `packs/heat_kefu` 跑一轮 miss 产生的事件流）+ T22 的 737 候选做一次端到端演示（数字落盘）。

## 允许修改的文件（白名单）

```
允许新增：tools/feedback_mining/**（挖掘器 + README + 可选的示例输入）
         tools/tests/test_feedback_mining.py
允许修改：tools/README.md（登记新工具；只增）
禁止触碰：docs/**（策划写）、core/** runtime/** assets/** compiler/** adapters/** packs/** cli/** eval/**
         trigger/**（只读其 ledger 格式，不得改）、kefu 仓
```

## 验收标准（逐条可判定，验收方独立复跑）

1. **挖掘器可用**：给一份留痕 + 事件流 → 产出 `suggestions.json`；每条候选含
   `text` / `count` / `states` / `sample_turn_ids` / `first_seen` / `last_seen`；
   **验收方用自写脚本独立复算其中一条候选的次数**，与报告一致。
2. **归一化同源**：候选聚合用的归一化与 `adapters.framework_kefu.normalize_text` **同一函数**（import，不得复制）；
   单句判据与 `compiler.source._SENTENCE_TERMINATORS` **同源**（实测断言）。
3. **形式条款 + 拒收留痕**：构造一个含槽位占位符的候选 → 该候选进 `rejected_candidates` 且原因含占位符本身；
   一个超长候选同理；**A1/A2 字段恒为 `"pending_human"`**。
4. **确定性**：同输入两次跑，`suggestions.json` 除时间戳外**逐字节一致**（diff 核对）；换输入 → 结果变。
5. **端到端演示**：用本仓可造的场景跑通一次，`report.json` 落盘且含 `provenance`（输入 sha256 + commit）；
   README 有复现命令与"下一步人工动作"。
6. **零回归**：既有断言一行不改；十一根全绿（报条数）；`tools` 根测试含新文件。
7. **无第三方依赖**（仅标准库）。

## 反空转条款

- 第 1 条的"独立复算"由验收方做，执行方要给出**可复算的中间量**（候选的 turn_id 列表）；
- 第 3 条必须有**负例实测**（含槽/超长候选真的被拒收并写明原因），不得只断言字段存在；
- 第 4 条必须能判红：把排序改成随机 → 两次跑必须不同（注入验证写进报告）；
- 测试必须调用产品 API（挖掘器入口），不得在测试内自造聚合逻辑。

## 回滚方式

`git clean -fd tools/feedback_mining/` + `git checkout -- tools/README.md tools/tests/`

## 执行方式

**首选**：`vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = 公开（**不得**把真实会话/录音写进产物；示例输入用自造话术）。
非交互直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突停下上报。

## 卡状态
- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
