# T21c · labs：把过期的事实陈述改成自我验证

## 背景（只写必需）

T21 的 `labs/ticket-source/source_map.json` 里有一段 `rule_reachability`，写的是：

- `reachable: ["greeting", "closing"]`
- `unreachable_from_source: ["overdue", "due_tonight", "due_days_left", "ask_assignee"]`
- `reason: "…demo-brief 的 state_fields 声明 days_left min=0 … 因此 is_overdue 恒为 false …"`

**这三条都与事实不符**（T21b 实测、我独立复核确认）：

1. T21 实测驱动的是 `greeting` + `due_days_left`，不是 `greeting` + `closing`；
2. T21b 实测 **6 条规则全部可达**，**只需加 fixture 行、不动 `packs/`**；
3. `overdue` 的 `when` 是 `{is_overdue: true}`，**根本不涉及 `days_left`**；
   `is_overdue` 是从源**透传**的布尔列——**"is_overdue 恒为 false"这个因果推断是错的**。

**为什么这张卡值得做**：这属于本仓最忌讳的一类问题——**仓库里的数字/事实与事实不符**。
它比"缺一个功能"更坏：下一个会话读到它，会以为"这四条规则不可达、必须改包"，从而做出错误的动作
（而 T21b 已经证明不需要改包）。**并且 T21b 的执行方因为白名单约束不能改它，所以这个错陈述现在还躺在仓库里。**

更根本的一步：**这份陈述应该由运行结果自动校验**，而不是靠人记得同步——
仓库里其它地方（`eval/` 的报告）就是"数字带原始数据指针、可复核"的做法，这里照做。

## 目标（可验收的产物）

- 产物 1：`labs/ticket-source/source_map.json` —— **修正 `rule_reachability` 为与实测一致**的陈述
- 产物 2：`labs/ticket-source/run_e2e.py` —— **新增断言**：`source_map.json#rule_reachability`
  必须与实际测得的覆盖一致（不一致 → 非零退出）
- 产物 3：`labs/ticket-source/README.md` —— 若其中也复述了旧结论，一并改正
- 产物 4：重跑产物 `summary.json` / `raw/turns.jsonl`

## 允许修改的文件（白名单）

```
允许修改：labs/ticket-source/source_map.json
允许修改：labs/ticket-source/run_e2e.py
允许修改：labs/ticket-source/README.md
允许修改：labs/ticket-source/raw/turns.jsonl（重跑产物）
允许修改：labs/ticket-source/summary.json（重跑产物）
禁止触碰：其他一切文件——尤其不得改 packs/ adapters/ core/ rules/ compiler/ assets/ runtime/ eval/ cli/ docs/，
          也不得改 tickets.source.json（本卡不涉及 fixture）
```

## 禁止事项

- **不得改 `source_map.json` 里 `state_fields` / `sensitive_fields` 的既有映射语义**——
  本次只改 `rule_reachability` 那一段事实陈述（以及必要的理由文字）
- **不得改 `packs/demo-brief/`**（T21b 已证明不需要改包；若你认为必须改，**停下来在报告里说明**）
- **不得删既有断言**（T21b 后是 84 条，只增不减）
- 不得放宽 T16 任何校验；不得改 `adapters/`；不得联网；不得新增依赖
- **不要 `git add` / `git commit`**；**不要做达标判定**

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 labs/ticket-source/run_e2e.py` → **rc 0**；断言数 **≥84**（只增不减）；
2. **`rule_reachability` 与实测一致**：`reachable` 必须列出**实际被命中过**的规则；
   `unreachable_from_source` 在当前 fixture 下必须为**空**（T21b 实测 6/6）；理由文字必须与代码一致
   （尤其**不得再出现"is_overdue 恒为 false"这类与 `when` 声明不符的因果**）；
3. **新增的自我校验断言必须能被打破**（这条是卡的核心）：把 `rule_reachability.reachable` 里
   删掉任意一条**实际可达**的规则 → 脚本必须 **rc != 0**，消息指出哪条不一致；
   **实测并贴出输出**（改前 rc 0、改后 rc 3、还原后 rc 0）；
4. **可复现**：同一份源 + 同一注入日期，跑两次 `summary.json` 逐字段相同（除时间戳类）；
5. **敏感值复核仍为 0**：脚本内断言保留；我验收时会**再次独立重查**（换方法：从源挖候选值）；
6. `git status --porcelain -uall` 的改动**全部**在白名单那 5 个文件内；
   `git diff --stat -- core rules compiler assets runtime eval cli adapters packs docs` **为空**；
7. `python3 -m unittest discover -s adapters` → 仍 **241 条全绿**。

## 反空转条款

- 断言必须能被打破（见验收第 3 条，必须实测三种状态：改前/改坏/还原）；
- **不得把 `rule_reachability` 写成一句模糊的话**（如"大部分规则可达"）——必须是可机检的枚举，
  否则新增的自校验断言无从落笔；
- 正例与负例都要有；负例断言消息含**具体规则名**。

## 回滚方式

`git checkout -- labs/ticket-source/`（T21b 的产物已入库，可精确回滚）。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T21c-labs-事实陈述自我验证.md)" --dir （仓库根）
```

**数据分级：公开级**。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
