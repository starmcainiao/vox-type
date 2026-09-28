# trigger — 前置包触发器 + 轮级留痕

**落点**：`trigger/`（独立扩展区，自带七项 AGENTS.md）。`state → plan` 是「决定说什么」，属接缝之上，
因此不进 `runtime/`（冻结区）——见 `docs/12` 的「分层纪律」与 `docs/10 §10.1`。
本层的登记与边界见 `trigger/AGENTS.md`（边界条款 §⑤）；迁出 `adapters/` 的记录见 `adapters/AGENTS.md §⑧`。

## 它是什么

| 部件 | 文件 | 干什么 |
|---|---|---|
| 格式闸门（T16） | `format.py` | `state` 快照格式 + `trigger.json` 映射格式的定义与校验 |
| 行为侧（T17） | `plan.py` | `build_plan(trigger, state) → plan`：纯函数、确定性 |
| 轮级留痕（T17） | `ledger.py` | `record_turn(...)`：JSONL 追加写，留「该说什么」的正样本 |

**只提案不出声（边界声明）**：本模块产出的是**建议 plan**，交给 `runtime/` 执行。
它不写音频、不调 TTS、不决定打断与耐心窗（`patience_ms` / `barge_in` /
`backchannel` / `rate_band` 四参数的消费方不在这里）。

## 与 T16 格式的关系

`format.py` 定**长什么样算合法**，`plan.py` 定**合法之后选哪条规则**。
本模块**复用** T16 的三个库 API，不复制校验逻辑：

| T16 的 API | 本模块怎么用 |
|---|---|
| `validate_state(state, trigger)` | `build_plan` 的第 0 步；错配声明在这里被拦下 |
| `check_budget(plan, budget_chars)` | `build_plan` 的第 5 步；超支抛 `BudgetError`，不产出 plan |
| `count_plan_chars(plan)` | 统计该轮字数（去标点口径，`docs/12 §12.8`），写进留痕 |
| `load_trigger(pack_dir)` | 装载 `trigger.json` + `phrases.json`（`pack.json`/`phrases.json` 的校验全归 `compiler` 公开面） |

字段名的单一来源：留痕里的 `turn_id` / `plan_id` / `ts` / `key`
**一律引 `core.metrics_spec` 的常量**，不在本模块自造字面量
（事件契约在冻结区，为了塞训练字段而改契约的成本远高于收益——`docs/12 §12.10`）。

## 口径

**匹配**：按 `trigger.json` 的 `rules` 顺序取**第一条**命中的规则（不排序，保确定性）。
`when` 是 `{字段名: 标量}`（单值 `==`）或 `{字段名: {"gt"/"gte"/"lt"/"lte": 整数}}`。
字段缺值 → 不命中。

**兜底规则（`when = null`）是命中，不是豁免**。它由作者在 `trigger.json` 里**显式声明**
（T16 的 `format.py` 已定「`None`/缺省 = 无条件兜底规则，**至多一条**」），
可 Review、可审批——它是本仓「禁止静默降级」想要的**可审核的正面例子**。
顺序按 `rules` 声明走，所以条件规则天然优先、兜底只能最后生效；
若作者把兜底写在前面，它自己就会先命中——那是作者声明的顺序（T16 只约束
「至多一条兜底」，不重排顺序）。

**fail-closed 的准确口径**：**当且仅当不存在兜底规则、且没有任何规则匹配时**，
`build_plan` 抛 `TriggerError`（消息含 `trigger_id`），不返回空 plan、不回落任何默认话术。
这条红线的真实含义是**不得由实现自行挑选兜底**——没有作者声明的兜底规则时，
本模块不替它找一条、不补一个默认话术。「静默降级」指的是这种看不见的**实现侧回落**
（表现为「突然换了个声音」，事后无法定位），**不是** `trigger.json` 里可见的兜底声明。

**变体**：`trigger.json` 里 `variant` 必须是整数（T16 的既有纪律，
变体选择权必须显式）。`build_plan` 对编程式传入的 `variant: "auto"` 走
sha256(`turn_id`\0`part`) 稳定散列（与 `runtime/executor.py` 的 `_resolve_variant`
同一口径、同一分隔符）——同一 `turn_id` 重跑得到同一变体（可重放），
跨轮次会变化（防复读机）。

**预算**：`build_plan` 内部强制调 T16 的 `check_budget`，
超支抛 `BudgetError`（消息含实际字数与预算值），**不产出 plan**。

**plan 单元**：只带 `key` + `rate` + `variant` + `slots`，`text` 恒为 `None`
（自由文本 = `SAY_LIVE`，前置包不碰；对齐 C4a `unreviewed_text`）。
槽值来自 `state` 的已声明字段，缺槽值一律抛错——不做静默替换成空串
（空串会让预算计数失真，就是静默降级）。

## 留痕（正样本通道）

`docs/12 §12.5` 定了两条数据通道；本模块只管正样本「该说什么」：

| 通道 | 来源 | 本模块 |
|---|---|---|
| 正样本「该说什么」 | **轮级留痕**（state 结构化层 + plan） | ✅ `ledger.record_turn` |
| 负样本「哪里没覆盖」 | 事件流的 `miss` + `reason` | ❌ 不碰（事件契约在冻结区） |

**一条记录长这样**（一行一条 JSONL）：

```json
{"ledger_version": 1, "turn_id": "turn-1", "plan_id": "demo-brief.ticket-reminder:turn-1",
 "ts": "2026-09-18T00:00:00Z", "trigger_id": "demo-brief.ticket-reminder",
 "rule_id": "greeting",
 "state": {"assignee_confirmed": true, "days_left": 3, "is_overdue": false,
           "overdue_days": 0, "ticket_status": "处理中"},
 "plan": [{"key": "greeting_ticket", "rate": "normal", "variant": 0, "slots": {}}],
 "keys": ["greeting_ticket"]}
```

**红线：敏感层绝不落盘**（`docs/12 §12.10` 末）。留痕的 `state` 块**只取声明为
`layer: "structured"` 的字段**——按 `trigger.json` 的声明取，不靠字段名嗅探、
不靠调用方自觉；敏感层的字段名与其值都不出现在文件里。
配对的意义是 `(state 结构化层, plan key 列表)`——这就是它作为监督微调数据的
全部价值（`supervision_pairs()` 直接返回这对）。

**落盘位置**：由 `path` 参数指定，**缺省落在仓外**
（`$VOX_LEDGER_DIR/turns.jsonl` → `~/.vox-ledger/turns.jsonl`）。
仓库目录本身不进默认可写路径——留痕含用户的状态，默认落仓内 = 私人数据入仓。
相对路径按**当前工作目录**解析，不锚定到仓库根。

**写入失败**：抛 `LedgerError`，消息**含目标路径与失败原因**。
缺父目录**不自动创建**（自动建目录会让「路径写错」变成静默落盘，
正是仓库红线禁止的静默降级）。
「留痕失败要不要阻断播报」是**调用方的决定**——本模块只负责把失败**响亮地**交出去。

## 复现命令

```sh
cd （仓库根）

python3 -m unittest discover -s trigger

# 只跑某几个测试文件
python3 -m unittest trigger.tests.test_format \
                   trigger.tests.test_trigger \
                   trigger.tests.test_ledger \
                   trigger.tests.test_reachability

# demo 包校验（应 rc 0）
./bin/vox pack check packs/demo-brief
```

## 依赖

- 标准库：`json` / `hashlib` / `os` / `pathlib`
- 仓库自身：`core.protocol`（`PlanUnit`）、`core.metrics_spec`（字段名常量）、
  `compiler` 公开面（`load_source` / `SourceError`，话术表解析）、本包 `format.py`
- **不依赖** `runtime/`、`assets/`、`eval/`、`cli/`；内核也不得反向 import 本模块
  （`trigger/AGENTS.md §⑤` 第 4 条）。
