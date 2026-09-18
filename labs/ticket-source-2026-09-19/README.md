# labs/ticket-source-2026-09-19 — 状态来源 + 前置包端到端演示（T21）

**这是 `labs/` 实验区的产物：不进任何层的契约。**

## 它补上了什么

前置包这条线之前只有两半：
- **格式**（T16）：`adapters/state_trigger/format.py` —— `state` 快照格式 + `trigger.json` 映射格式 + 校验器
- **行为**（T17/T17b）：`trigger.py`（`state → plan`，含显式兜底）+ `ledger.py`（轮级留痕）

缺的那一件是**状态的来源**：`state` 只能手工构造，所以整条线从没被真正跑过一遍。
本实验补上第一个状态来源（工单系统导出的 demo 形态），并把
**源 → state → plan → 留痕** 端到端跑通，产出可复现的原始证据。

**为什么落 `labs/` 而不是 `adapters/`**：① 形态未稳定，照 `labs/kefu-bridge-2026-09-18/` 的前身做法；
② 真实业务系统（工单/CRM/账务）不在公开仓里，正式适配器现在做不了；
③ `adapters/AGENTS.md §⑧` 已明文禁止再往 `adapters/` 加第二个非引擎同类模块。

## 数据分级：公开级

`tickets.source.json` 是**自造**的工单数据：工单号、客户标识、负责人姓名、手机号全为虚构字符串，
不含任何用户录音、真实会话、内网地址、token。

## 文件

| 文件 | 是什么 |
|---|---|
| `tickets.source.json` | 产物 1：工单系统导出的公开 demo 形态（8 条自造工单） |
| `source_map.json` | 产物 2：**显式映射表** —— 源字段 → state 字段、派生字段的计算规则、敏感字段声明。**可 Review、可 diff** |
| `to_state.py` | 产物 3：读源 + 套映射 → state 快照；**调用 T16 的 `validate_state`** |
| `run_e2e.py` | 产物 4：端到端 + 自断言（正例 6 条 + 负例 6 类 + 可复现性）；失败即非零退出 |
| `raw/turns.jsonl` | 产物 5a：留痕，一行一条（`record_turn` 写的） |
| `summary.json` | 产物 5b：口径、命中分布、敏感值复核、可复现性结果 |
| `README.md` | 产物 5c：本文件 |

## 复现命令

```sh
cd /Volumes/macos/newProject/vox-type

# 端到端（rc 0 = 全部断言通过；3 = 有断言失败）
python3 labs/ticket-source-2026-09-19/run_e2e.py

# 既有测试必须仍全绿（证明本实验没有反向污染既有层）
python3 -m unittest discover -s adapters        # 241 条

# 冻结区与 adapters/ 零 diff（应为空）
git diff --stat -- core rules compiler assets runtime eval cli adapters packs
```

留痕文件是 append 写，`run_e2e.py` 每次运行前会先清空 `raw/turns.jsonl` 以保证幂等
（清空动作只发生在 `labs/ticket-source-2026-09-19/` 内）。

## 口径

- **注入的「今天」**：`source_map.json#as_of` = `2026-09-19T10:00:00+08:00`。
  代码不 `import` 系统时钟；未注入即抛 `SourceMapError`。留痕的 `ts` 也由本脚本按 `as_of` 注入。
- **映射是数据**：源字段 → state 字段、派生字段的 op 与参数全在 `source_map.json` 里；
  `to_state.py` 里**不含任何具体字段名字面量**（`grep` 可验），换一份映射表不改一行代码也能跑。
- **校验器是复用**：state 的格式闸门是 T16 的 `validate_state`，plan 的预算与 key 复核是
  `build_plan` 内部的 `check_budget` / `phrase_keys`。本实验没有复制任何校验逻辑。
- **敏感值复核**：脚本把工单号、客户标识、手机号、负责人姓名逐个 grep 留痕文件原文，
  结果写进 `summary.json#sensitive_scan.hits`（四项必须全 0）。
- **预算**：单轮 60 字（`trigger.json#budget_chars`），`build_plan` 内部强制校验。

## 这条线现在走到哪、下一步缺什么

**已经通了**：源数据 → state（过 T16 校验）→ plan（过 key/预算复核）→ 留痕（只落结构化层）。
断言 38 条全过，`summary.json` 两次运行逐字段相同。

**本实验只打到了 6 条规则里的 2 条**（`greeting` 5 次、`due_days_left` 3 次）。这是
`packs/demo-brief/trigger.json` 的规则顺序问题，不是本实验的映射问题，且本实验不改 `packs/`：

1. `greeting` 的 `when` 是 `{ticket_status: "处理中"}`，在 `rules` 里排在**所有**日期/负责人规则之前；
   `build_plan` 按 `rules` 顺序取第一条命中（`trigger.py` 的既有口径），所以任何处理中的工单先命中 `greeting`。
2. `state_fields` 声明 `days_left` 的 `min: 0`，而 `is_overdue` 的语义是「到期日之前」= `days_left < 0`，
   两者在 T16 的校验范围内不可同时满足 → `is_overdue` 恒为 `false` → `overdue` / `due_tonight` 永远打不到。
3. `ask_assignee`（`when: {assignee_confirmed: false}`）被 `greeting` 遮住。

`summary.json#rule_reachability_from_source` 把这份覆盖边界记录在案 —— 本实验记录的是**覆盖边界**，
不是假装六条规则都跑到了。

**下一步缺的（都不在本卡白名单内）**：

1. `packs/demo-brief/trigger.json` 的规则顺序 / `days_left` 的 `min` —— 要让 `overdue`、
   `due_tonight`、`ask_assignee` 可被源数据驱动，得动这里（属 `packs/`）。
2. **正式的工单适配器** —— 真实业务系统不在公开仓；等形态稳定后再进 `adapters/`，
   并先解决 `adapters/AGENTS.md §⑧` 关于「非引擎同类模块」的约束。
3. **状态层 → 资产层的规范**（T22）—— 「什么状态该说什么话」目前靠 `trigger.json` 手工写。
