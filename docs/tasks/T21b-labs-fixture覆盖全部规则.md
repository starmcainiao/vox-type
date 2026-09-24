# T21b · labs：把 fixture 扩到覆盖全部 6 条规则

## 背景（只写必需）

T21 把 `源 → state → plan → 留痕` 端到端跑通了（39 条断言、8 条源数据、可复现），但**只驱动到 6 条规则里的 2 条**
（`greeting` 5 次、`due_days_left` 3 次）；`overdue` / `due_tonight` / `ask_assignee` / `closing` 一条都没打到。

**根因不在代码，在 fixture 的行覆盖不足**（T21 的执行方已如实报告并写进 `summary.json#rule_reachability_from_source`）。
四条规则**都能从源数据驱动**：

| 规则 | 需要什么行 | 现有的冲突要绕开 |
|---|---|---|
| `overdue` | `is_overdue=True` **且** `ticket_status != "处理中"` | 否则被排在前面的 `greeting` 吃掉 |
| `due_tonight` | `is_overdue=False` 且 `days_left=0` | `days_left` 声明 `min: 0`，所以只能用 `0` |
| `ask_assignee` | `assignee_confirmed=False` 且前三条都不匹配 | 需 `is_overdue=False`、`days_left>14` |
| `closing` | 上面全不匹配、且不命中兜底之外的条件 | `closing` 是 `when: null` 兜底，**只有它前面全不匹配才生效** |

**为什么值得做**：一份"只驱动 2/6 规则"的演示**证明不了机制**——它看不出版本里最要紧的几件事：
条件规则的优先级、兜底的"最后手段"语义（正是 T17b 修的那条）、以及四条不同条件算子（`==` / `gt` / `lte` / bool 判定）都真的能用。

## 目标（可验收的产物）

- 产物 1：`labs/ticket-source/tickets.source.json` —— **扩充 fixture 行**（只加行，不改已有行的语义）
- 产物 2：`labs/ticket-source/run_e2e.py` —— 增断言：**6 条规则每条至少被驱动一次**
- 产物 3：`labs/ticket-source/summary.json`、`raw/turns.jsonl` —— 重跑后更新
- 产物 4：`labs/ticket-source/README.md` —— 更新覆盖说明（把"只覆盖 2 条"改成实际覆盖情况）

## 允许修改的文件（白名单）

```
允许修改：labs/ticket-source/tickets.source.json
允许修改：labs/ticket-source/run_e2e.py
允许修改：labs/ticket-source/README.md
允许修改：labs/ticket-source/raw/turns.jsonl（重跑产物）
允许修改：labs/ticket-source/summary.json（重跑产物）
禁止触碰：其他一切文件——尤其 **不得改 source_map.json 的既有映射语义**（只允许在"加行了需要新字段"时追加，
          且必须在 README 里说明为什么）、不得改 packs/、不得改 adapters/、不得改核心层
```

## 禁止事项

- **不得改 `packs/demo-brief/`**——本卡只改 fixture 与断言，**不许为了让规则可达去动包**。
  这条是硬约束：如果某条规则**真的无法**从源数据驱动，**如实报告**并把该规则的覆盖标记为 `unreachable_from_source`
  加上理由，**不要改包来迁就**。
- 不得改 `adapters/state_trigger/`（只读其公开 API）
- **不得放宽 T16 的任何校验**（`validate_state` / `check_budget` 的强度只增不减）
- **不得删掉既有的断言**（39 条只增不减）
- 不得新增第三方依赖；不得联网；**不要 `git add` / `git commit`**
- **不得把 `days_left` 的负值塞进 state 来硬凑 `overdue`**——`trigger.json` 声明了 `min: 0`，
  被 `validate_state` 拒绝是**正确行为**；`overdue` 要用 `is_overdue=True` 驱动

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 labs/ticket-source/run_e2e.py` → **rc 0**；断言数 **≥39**（只增不减）；
2. **6 条规则全部至少被驱动一次**——脚本必须输出一张可达性表并在 `summary.json` 里落盘；
   若有规则判为不可达，**必须给出"为什么不可达"的具体理由**（指出是哪条声明/哪个校验挡住了），
   而不是只写一句 `unreachable`；
3. **兜底语义被真正演示**：至少要有一行**只有 `closing` 匹配**（即 `closing` 之外的 5 条全不匹配）——
   这是 T17b 修的那条语义，必须由本实验证明它在端到端链路上生效；
4. **优先级被真正演示**：至少要有一行**同时匹配条件规则与兜底**，且断言命中的是**条件规则**；
5. **增量断言不许互相抵消**：`留痕行数 == 源条目数` 仍成立（加行后要一起更新）；
6. **敏感值复核仍为 0**：脚本内的 grep 断言保留，且我验收时会**再次独立重查**；
7. **可复现**：同一份源 + 同一注入日期，跑两次 `summary.json` 逐字段相同（除时间戳类）；
8. `git status --porcelain -uall` 的改动**全部**在白名单那 5 个文件内；
   `git diff --stat -- core rules compiler assets runtime eval cli adapters packs docs` **为空**；
9. `python3 -m unittest discover -s adapters` → 仍 **241 条全绿**。

## 反空转条款

- 断言必须能被打破：**改坏一条映射、或把某条规则的条件改到不可能满足，脚本必须变红**（`rc != 0`）；
- 覆盖表必须由**实际运行结果**生成（不得手写一张"应该能覆盖"的表）；
- 不得为通过而放宽校验或删断言。

## 回滚方式

`git checkout -- labs/ticket-source/`（T21 的产物已入库，可精确回滚）。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T21b-labs-fixture覆盖全部规则.md)" --dir （仓库根）
```

**数据分级：公开级**——fixture 是自造工单数据。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
