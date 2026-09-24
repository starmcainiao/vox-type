# labs/ticket-source — 状态来源 + 前置包端到端演示（T21 / T21b / T21c）

**这是 `labs/` 实验区的产物：不进任何层的契约。**

## 它补上了什么

前置包这条线之前只有两半：
- **格式**（T16）：`trigger/format.py` —— `state` 快照格式 + `trigger.json` 映射格式 + 校验器
- **行为**（T17/T17b）：`plan.py`（`state → plan`，含显式兜底）+ `ledger.py`（轮级留痕）

缺的那一件是**状态的来源**：`state` 只能手工构造，所以整条线从没被真正跑过一遍。
本实验补上第一个状态来源（工单系统导出的 demo 形态），并把
**源 → state → plan → 留痕** 端到端跑通，产出可复现的原始证据。

T21c 又补上了**事实陈述的自我校验**：`source_map.json#rule_reachability` 这段手写陈述
必须与实测覆盖逐条一致，不一致即 rc 3（见「T21c：把过期陈述改成自我验证」）。

**为什么落 `labs/` 而不是 `adapters/`**：① 形态未稳定，照 `labs/kefu-bridge/` 的前身做法；
② 真实业务系统（工单/CRM/账务）不在公开仓里，正式适配器现在做不了；
③ `adapters/AGENTS.md §⑧` 已明文禁止再往 `adapters/` 加第二个非引擎同类模块。

## 数据分级：公开级

`tickets.source.json` 是**自造**的工单数据：工单号、客户标识、负责人姓名、手机号全为虚构字符串，
不含任何用户录音、真实会话、内网地址、token。

## 文件

| 文件 | 是什么 |
|---|---|
| `tickets.source.json` | 产物 1：工单系统导出的公开 demo 形态（T21b 后 **12 条**自造工单，见下「规则覆盖」表） |
| `source_map.json` | 产物 2：**显式映射表** —— 源字段 → state 字段、派生字段的计算规则、敏感字段声明。**可 Review、可 diff。T21b 未改一行；T21c 只改了 `rule_reachability` 一段事实陈述（`state_fields` / `sensitive_fields` 未动）** |
| `to_state.py` | 产物 3：读源 + 套映射 → state 快照；**调用 T16 的 `validate_state`** |
| `run_e2e.py` | 产物 4：端到端 + 自断言（正例 + 覆盖表 + 兜底/优先级语义 + 陈述自校验 + 负例 7 类 + 可复现性）；失败即非零退出 |
| `raw/turns.jsonl` | 产物 5a：留痕，一行一条（`record_turn` 写的） |
| `summary.json` | 产物 5b：口径、命中分布、敏感值复核、可复现性结果 |
| `README.md` | 产物 5c：本文件 |

## 复现命令

```sh
cd （仓库根）

# 端到端（rc 0 = 全部断言通过；3 = 有断言失败）
python3 labs/ticket-source/run_e2e.py

# 既有测试必须仍全绿（证明本实验没有反向污染既有层）
python3 -m unittest discover -s adapters        # 241 条

# 冻结区与 adapters/ 零 diff（应为空）
git diff --stat -- core rules compiler assets runtime eval cli adapters packs
```

留痕文件是 append 写，`run_e2e.py` 每次运行前会先清空 `raw/turns.jsonl` 以保证幂等
（清空动作只发生在 `labs/ticket-source/` 内）。

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

## 这条线现在走到哪

**已经通了**：源数据 → state（过 T16 校验）→ plan（过 key/预算复核）→ 留痕（只落结构化层）。
断言 **109 条全过**（T21b 是 84 条，T21c 增 25 条，只增不减），`summary.json` 两次运行逐字段相同。

### 规则覆盖：6 条全驱动（T21b）

T21 当时只把两条条件规则驱动起来，执行方已如实报告根因是**行覆盖不足**。T21b 只加 fixture 行，
把 6 条全部驱动起来 —— **`packs/demo-brief/` 一行未改，`source_map.json` 一行未改**。

| 规则（包内顺序） | `when` | 驱动它的行 | 匹配 | 命中 |
|---|---|---|---|---|
| 1 `greeting` | `{ticket_status: "处理中"}` | 0007/0008/0009/0011/0012 | 5 | 5 |
| 2 `overdue` | `{is_overdue: true}` | **0015**（status=待验收）+ 0009/0012 | 3 | 1 |
| 3 `due_tonight` | `{is_overdue: false, days_left: {lte: 0}}` | **0016**（status=待验收，days_left=0）+ 0008 | 2 | 1 |
| 4 `due_days_left` | `{is_overdue: false, days_left: {gt: 0, lte: 14}}` | 0007/0010/0013/0014 | 4 | 3 |
| 5 `ask_assignee` | `{assignee_confirmed: false}` | **0017**（status=待受理，days_left=26）+ 0011 | 2 | 1 |
| 6 `closing` | `null`（兜底） | **0018**（status=已关闭，days_left=43）+ 其余全部 | 12 | 1 |

**匹配 ≠ 命中**。`closing` 是 `when: null`，**每一行都匹配**（12 行）；但按 `rules` 顺序取第一条，
只有前面的条件规则全不匹配时它才会被命中（仅 0018）。这正是 T17b 修的「兜底只是最后手段」语义，
本实验在端到端链路上证明它生效：

- **兜底语义**：0018 是**唯一**只有 `closing` 匹配的行（前 5 条条件规则全不匹配）→ 命中 `closing`。
- **优先级**：其余 11 行**同时**匹配条件规则与兜底 `closing`，断言命中的是**包内顺序最靠前的条件规则**。
  其中 0009（`处理中` + `is_overdue=true`，同时匹配 `greeting` 与 `overdue`）命中 `greeting` ——
  这就是 T21 报告里「`overdue` 被 `greeting` 吃掉」的那条；T21b 的做法不是改包，而是另加一行
  `ticket_status="待验收"` 的 0015 绕开 `greeting`，让 `overdue` 真正被驱动。

四条条件算子都被真实驱动：`==`（`ticket_status`）、`bool` 判定（`is_overdue` / `assignee_confirmed`）、
`lte`（`due_tonight`）、`gt` + `lte` 合取（`due_days_left`）。

**覆盖表是跑出来的，不是写出来的**：脚本用引擎的 `rule_matches` 对每条源行的 state 逐条判定，
生成 `summary.json#rule_matching_matrix`（含逐行匹配布尔矩阵）与 `#fallback_semantics`。
断言里有一条方向性检查：`命中结果 == 该行匹配集合里包内顺序最靠前的那条`。

**`days_left` 的 `min: 0` 没有被绕过**。`overdue` 靠 `is_overdue: true` 驱动，不靠把 `days_left` 塞负值。
脚本有一条负例断言证明负值被 T16 的 `validate_state` 拒掉（越界 `min=0`，这是正确行为），
并额外断言所有源行 `days_left >= 0`、命中 `overdue` 的行 `is_overdue` 都是 `true`。

**反空转（实测变红，rc=3）**：

| 改动 | 结果 |
|---|---|
| 改坏一条映射（`source_map.json` 把 `ticket_status` 指到不存在的源列） | `SourceMapError`，rc 3 |
| 把某条规则的条件改到不可能满足（全部 `is_overdue=false`） | 3 条断言失败，rc 3 |
| 改坏一行源字段（0015 的 `is_overdue` 翻成 `false`） | 2 条断言失败，rc 3 |
| 全部 `assignee_confirmed=true` | rc 3，且 `unreachable_from_source` 给出带具体取值集合的理由 |
| 陈述与实测不符：`rule_reachability.reachable` 里删掉 `overdue` | 3 条断言失败，rc 3，消息点名 `实际命中了但声明里没列: ['overdue']` |
| 陈述与实测不符：`unreachable_from_source` 写 `["ask_assignee"]` | 2 条断言失败，rc 3，消息点名 `声明里有但实际可达: ['ask_assignee']` |

### T21c：把过期的事实陈述改成自我验证

T21 在 `source_map.json#rule_reachability` 里写了「只有两条条件规则被驱动、其余条件规则未被驱动」，
并给了一个错误因果：把 `days_left` 的 `min=0` 当成 is_overdue 的取值约束。
**这两条都与事实不符**（T21b 实测、且可独立复核）：

1. T21 实测驱动的是 `greeting` + `due_days_left`，不是 `greeting` + `closing`；
2. T21b 实测 **6 条规则全部可达**，只需加 fixture 行、不动 `packs/`；
3. `overdue` 的 `when` 是 `{is_overdue: true}`，**不涉及 `days_left`**；
   is_overdue 是从源**透传**的布尔列，本 fixture 里有 3 行取 true
   （WB-DEMO-0009 `days_left=5`、WB-DEMO-0012 `days_left=0`、WB-DEMO-0015 `days_left=0`）
   ——`days_left` 的 `min=0` 与 is_overdue 互不约束。

T21c 做了两件事：

- **改对**：`rule_reachability` 改成与实测一致的**可机检枚举**
  （`reachable` / `unreachable_from_source` / `hit_counts` / `covered_by_ticket_id` / `per_rule[*].when`），
  理由文字与 `trigger.json` 的 `when` 声明一致。`state_fields` / `sensitive_fields` 未动。
- **加自校验**：`run_e2e.py` 用实测的 `rule_hit_count` / `rows_covering` / `trigger.rules`
  逐条断言该段陈述，不一致即 rc 3 且失败消息点名具体规则。照 `eval/` 报告
  「数字带原始数据指针、可复核」的做法——陈述不再靠人记得同步。

自校验的口径：

| 声明字段 | 与实测的对应 |
|---|---|
| `reachable` | 实际被命中过的规则（`rule_hit_count` 非零者，按包内顺序） |
| `unreachable_from_source` | 实际未被命中的规则（当前 fixture 必须为 `[]`） |
| `hit_counts[rid]` | `rule_hit_count[rid]` |
| `covered_by_ticket_id[rid]` | `rows_covering[rid]`（`rule_matches` 判定匹配的行，兜底规则 12 行全中） |
| `per_rule[rid].when` | `trigger.json` 里该规则的 `when` 原文 |

另有一条防回潮断言：该段不得再出现 T21 那句错误因果的原话（含缩写变体），
也不得写成「大部分规则可达」这类无法机检的模糊陈述。

### 本目录的文字陈述由什么守着（T21d 扩面，T21e 做实）

T21c 只给 `source_map.json#rule_reachability` 一段加了自校验，扫描范围是单段；
同一份文件里另一处（`_why_is_as_of_still_needed`）当时仍写着与实测不符的因果。
T21d 把扫描范围从「一段」扩到**整个目录**，T21e 把这个机制本身做实
（行内注释此前漏扫、标签与实际范围不一致、以及一处为躲数字字符的过度工程），
并在 `run_e2e.py` 里落了几件事：

- **禁语表**（`run_e2e.py#BANNED_TEXTUAL_CLAIMS`）：登记**已被实测推翻的因果陈述**。
  表项是「短语 + 为什么禁、正确的说法是什么」；只登记与实测矛盾的陈述——
  「overdue_days 确实 0 条 when 引用」是事实，所以这句话不在禁语表里。
  表项分成两组：事实性短语进自扫（`_FACTUAL_BANNED`）；反向的模糊化规避类短语
  只由负例注入测试覆盖，不写进任何文件正文。
- **扫描目标**：4 个，扫**文件全文**而不是某一段，每次运行打印清单
  （扫了几个目标、各多少字符、合计多少字符）。
  | 目标 | 口径 |
  |---|---|
  | `source_map.json` | 全文 |
  | `README.md` | 全文 |
  | `tickets.source.json` | 只扫 `_fixture_meta` 注解文字，fixture 数据行不参与 |
  | `run_e2e.py` | **全文 − 禁语表自身的字面量区间**（不剥注释、不剥文档串；唯一的跳过区间按定义就是被扫描的对象本身） |
  T21d 在这里漏了行内 `#` 注释：`_strip_docstrings` 只跳过了三个引号的定界符，
  注释与文档串内容其实一直在被扫，但标签写的是「已剥除文档串」——
  一个专抓假陈述的机制，自己的自我描述是假的。T21e 起标签与实际范围由断言绑定
  （改一处不改另一处会红），`_strip_docstrings` 已删掉。
- **字段可达性是现算的**（`summary.json#field_reachability`）：`rules[].when` 的引用关系、
  `rules[].units[].slots` 的引用关系、`phrases.json` 模板占位符的引用关系，
  全部由 `run_e2e.py` 从 `packs/demo-brief/trigger.json` / `phrases.json` 现算，脚本不复制任何判定逻辑。
  实测：`days_left` 被 `due_tonight` / `due_days_left` 的 when 引用；
  `is_overdue` 被 `overdue` / `due_tonight` / `due_days_left` 的 when 引用；
  `overdue_days` **0 条** when 引用。
- **`overdue_days` 这件事如实记录**：它是 `overdue` 规则 `units[].slots` 的值来源，
  不是 when 的判定输入——这与「完全不参与触发」是两回事。
  本目录**不改 `packs/`**（改包属 `packs/` 的范围，不在本卡白名单内）；
  它归到下面第 4 条（README 的「下一步缺的」第 4 条 = `docs/tasks/00-索引.md`
  「前置包的 key 可达性检查」跨批待办 3）。
- **逐条事实陈述清单**（`summary.json#textual_claims_audit`）：每条陈述带
  「出处（文件）+ 判定方式 + 实测值 + 结论」；凡举不出判定方式的陈述不在此列
  （要么删掉，要么改成可判定的）。
- **扫描器自身要能被打破**（反空转）：脚本里有正例（四个目标在真实文件里全部零命中）
  与负例（向 `README.md` 注入一句被禁的旧因果后必须能检出，且失败消息同时含
  **具体文件名与被禁短语**），还有一条覆盖面自检（每个目标注入禁语后命中数都会增加，
  防止某个目标被静默跳过）。

`overdue_days` 的 0 条 when 引用写在 `source_map.json#state_fields.overdue_days.note` 与
`summary.json#field_reachability`，两处都是可机检的具体陈述（含现算的引用条数、
引用它的规则、以及它属于 slots 值来源而非 when 输入）。

**下一步缺的（都不在本卡白名单内）**：

1. **正式的工单适配器** —— 真实业务系统不在公开仓；等形态稳定后再进 `adapters/`，
   并先解决 `adapters/AGENTS.md §⑧` 关于「非引擎同类模块」的约束。
2. **状态层 → 资产层的规范**（T22）—— 「什么状态该说什么话」目前靠 `trigger.json` 手工写。
3. **`greeting` 的优先级值得业务侧复核** —— 它排在所有日期/负责人规则之前，一条已逾期且仍标
   「处理中」的工单只会听到问候语。这是 `packs/` 的话术设计问题（属 `packs/`），本实验只如实记录。
4. **前置包的 key/字段可达性检查**（= `docs/tasks/00-索引.md`「前置包的 key 可达性检查」跨批待办 3）—— `overdue_days` 被声明为 `state_field`
   但 0 条 `when` 引用它（它是 `overdue` 规则 `units[].slots` 的值来源）。这类「声明了但触发层
   没人引用」的字段值得在 `packs/` 侧建一道静态检查；**本目录没有为了"全部字段都被引用"去改包**。
   同类已登记项：`phrases.json` 里有 key 只被 `script.json` 引用。
