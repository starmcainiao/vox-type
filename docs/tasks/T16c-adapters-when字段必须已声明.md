# T16c · adapters：`when` 条件的字段名必须已声明（装载期校验补齐）

## 背景（只写必需）

`format.py::load_trigger` 已校验：plan 单元的 `slots` 里出现的槽位名必须是 `state_fields` 已声明字段
（未声明 → 报错）。**但 `when` 条件里的字段名没有对称的校验**——实测：在 trigger.json 里写
`"when": {"typo_filed": "x"}`（字段拼错）能通过装载；运行时 `trigger.py::_rule_matches` 看到
state 里没有这个字段就返回「不命中」，于是**这条规则永远静默地不触发**，零报错零留痕。

这正是 `format.py` 模块注释红线 1 的原文场景：「写错一个字母 = 该规则永不触发，而零留痕，
正是仓库红线禁止的『静默降级』原型」。`state` 侧已有两道闸（`validate_state` 拒绝未声明字段；
slots 已做装载期校验），`when` 是三处里唯一漏掉的那处。

**本卡范围只有字段名声明校验**。类型相容性（如对 `bool` 字段写 `{"gt": 5}`）**明确不做**——
那是另一个议题（涉及 `_rule_matches` 的 TypeError→不命中语义），另行登记，不许顺手做。

规格依据：
- `adapters/state_trigger/format.py` 模块注释红线 1、红线 2（state 字段必须预先声明）
- `docs/13-未完成清单.md` §四 第 2 条同族缺陷（T17b / T16b 同类：「配置看起来起作用、实际不起作用」）

## 目标（可验收的产物）

给 `load_trigger` 的规则循环增加一条校验：**`when` 里的每个字段名必须 ∈ `state_fields` 已声明的字段名集合**，
否则抛 `TriggerError`。

- 改动点：`adapters/state_trigger/format.py` 的 `load_trigger` 规则循环（`_validate_when` 返回后、
  该循环里 `declared_names` 已就绪，加在那里；不要改 `_validate_when` 的签名与既有校验）
- 错误消息必须包含：**非法字段名本身、所在规则的 rule_id**，并提示「该字段未在 state_fields 里声明」
  （消息风格与既有 TriggerError 一致，含具体值）
- 注意：`when: null`（兜底）没有字段名，不涉及本检查，原样放行

## 允许修改的文件（白名单）

```
允许修改：adapters/state_trigger/format.py
允许修改：adapters/state_trigger/tests/test_format.py
禁止触碰：其他一切文件——尤其 trigger.py（_rule_matches 的「字段缺值 → 不命中」运行时语义不变）、
          packs/、docs/、core/、compiler/ 等
```

## 禁止事项

- **不做类型相容性校验**（when 期望值 vs 声明 type/enum 的匹配检查不在本卡范围，明确不做）
- 不得改 `_validate_when` 既有校验（未知比较符 / 期望值形状等保持原样，本卡是**新增**一条检查）
- 不得改 `packs/` 任何文件（demo 包 `when` 字段全部已声明，应原样通过——活体回归样本）
- 不得新增第三方依赖；不得"顺手优化"

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿，测试数 **只增不减**（基线以派发时实测为准）；
2. **负例（typo 字段）**：trigger.json 里某条件规则 `"when": {"typo_filed": "x"}`（该字段未声明）→
   `load_trigger` 必须抛 `TriggerError`，且消息**同时含** `typo_filed` 与该规则的 `rule_id`；
3. **负例（区间条件同样被拦）**：`"when": {"another_typo": {"gt": 0}}` → 同样抛 `TriggerError` 且消息含字段名；
4. **正例（合法引用）**：`when` 引用已声明字段（等值与区间两种写法）→ 装载成功；
5. **活体回归**：`load_trigger('packs/demo-brief')` 原样成功（它的 `when` 只引用 `ticket_status` /
   `is_overdue` / `days_left` / `assignee_confirmed`，全部已声明）；
6. **兜底不受影响**：`when: null` 的规则（含位于最后的合法兜底）装载成功——证明本检查没有误伤兜底语义；
7. 测试必须调用产品 API `load_trigger`，不得在测试文件内自造「when 字段是否声明」的判定函数（反空转）；
8. `git status --porcelain -uall` 恰为白名单 2 个文件的改动，无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须调用产品 API；测试名与实际调用路径一致；
- 正例与负例都要有，负例断言**错误消息包含具体非法字段名与 rule_id**；
- 不得为通过测试而放宽任何既有校验；`TriggerError` 抛出点只增不减。

## 回滚方式

两个白名单文件都是已跟踪文件：`git checkout -- adapters/state_trigger/format.py adapters/state_trigger/tests/test_format.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T16c-adapters-when字段必须已声明.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**——只涉及合成 fixture 与 demo 包。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T16c 节）
  - 验收方独立复跑：adapters 262 全绿（249→+13）；自写探针——when typo 等值/区间两种写法均报错且消息含字段名与 rule_id、已声明字段等值/区间装载成功、demo 包活体 rc OK 且兜底末条不受影响；adapters 改动恰为白名单 2 文件。
  - **登记（本卡明确不做的）**：when 期望值与声明 type 的**类型相容性**校验（如对 bool 字段写 `{"gt": 5}`）仍缺——执行方用 `test_when_type_mismatch_not_checked` 锁定了当前状态；改语义时须同步改该测试。已登记进 `docs/13`。
