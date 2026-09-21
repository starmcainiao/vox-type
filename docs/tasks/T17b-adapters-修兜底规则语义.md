# T17b · adapters：修兜底规则语义（`when: null` 必须命中）

## 背景（只写必需）

T17 交付后我独立复核，发现**一处 T16 与 T17 之间的语义矛盾**（实测确认）：

- **T16**（`format.py:370-371`）明写：`when` 为 `None`（或缺省）**= 无条件兜底规则**，并校验"至多一条"（说明设计意图就是让它生效）；
- **T17**（`trigger.py::_rule_matches`）却让它**永不命中**（`if when is None: return False`）。

**实测证据**：`packs/demo-brief/trigger.json` 的第 6 条规则 `closing` 声明了 `when: null`；
我构造一份合法、且任何条件规则都不匹配的 state 后调 `build_plan` → **抛 `TriggerError`**，`closing` 未生效。

**为什么这是缺陷（而不是"更严格所以更好"）**：它让包作者声明的配置**看起来有兜底、实际没有**——
比"明确没有兜底"更糟，因为人会因此不再检查覆盖漏洞。而且它违背了本项目的取向：
**"禁止静默降级"针对的是实现自行挑选的、看不见的回落**；而 `when: null` 是作者写在 `trigger.json` 里、
可 Review、可审批的**显式声明**——它恰恰是"可审核"的正面例子。

**正确的口径（本卡据此修）**：
- `when: null` = **显式兜底，命中**（至多一条，T16 已校验）；
- 原红线"无条件命中 → 抛错"收窄为：**当且仅当不存在兜底规则、且没有任何规则匹配时**才抛错；
- **不得由实现自行挑选兜底**（这才是"不得回落默认话术"的真实含义）。

## 目标（可验收的产物）

- 产物 1：`adapters/state_trigger/trigger.py` —— 修 `_rule_matches` 与模块红线注释
- 产物 2：`adapters/state_trigger/tests/test_trigger.py` —— 改/补测试（见验收）
- 产物 3：`adapters/state_trigger/README.md` —— 写明兜底语义（显式兜底 vs 无兜底时 fail-closed）

## 允许修改的文件（白名单）

```
允许修改：adapters/state_trigger/trigger.py
允许修改：adapters/state_trigger/tests/test_trigger.py
允许修改：adapters/state_trigger/README.md
禁止触碰：其他一切文件——尤其不得改 core/ rules/ compiler/ assets/ runtime/ eval/ cli/（冻结区）、
          不得改 docs/、不得改 packs/、不得改 format.py / ledger.py / __init__.py / tests/test_ledger.py
```

## 禁止事项

- **不得改 `format.py`**（T16 的校验器是既有产物，它的 `when: null` 定义是对的，不需要改）
- **不得改 `packs/demo-brief/`**（demo 包的 `when: null` 写法在新语义下就正确了）
- **不得放宽其他校验**：无兜底 + 无匹配仍必须抛 `TriggerError`；未审核 key、超预算、槽位缺值仍必须抛错
- 不得新增第三方依赖；**不得在测试里联网**；测试不得依赖真实时钟
- 不得"顺手优化"、"顺手重构"

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿（**`-s adapters`**）；基线 **235 条**，本卡之后 **≥235**；
2. **正例（显式兜底生效）**：用 `packs/demo-brief/` 造一份合法、条件规则全不匹配的 state
   → `build_plan` **返回**（不抛错），且 `rule_id == "closing"`（断言到具体 rule_id，不许只断"没抛错"）；
3. **正例（条件规则优先）**：造一份**同时**匹配某条件规则与兜底规则的 state
   → 命中**条件规则**（兜底只能最后生效）；
4. **负例（无兜底仍 fail-closed）**：另用一份**不含 `when: null`** 的 trigger
   （编程式构造或测试内自建 fixture，**不得改 `packs/demo-brief/`**）
   → 无匹配 state 必须抛 `TriggerError`，**消息含该 trigger_id**；且断言**未产出 plan、未落留痕**；
5. **不变式**：`format.py` / `ledger.py` / `__init__.py` / `tests/test_ledger.py` **零改动**
   （我会跑 `git diff --stat` 核对，只应看到白名单那 3 个文件）；
6. `./bin/vox pack check packs/demo-brief` → 仍 **rc 0**；
7. 冻结区零 diff；内核不得反向依赖（`grep -rn 'state_trigger' runtime/ assets/ eval/ cli/ compiler/ core/` → 0 命中）。

## 反空转条款

- **测试必须调用产品 API**（`build_plan`），不得在测试里复制匹配逻辑；
- 第 2 条必须**断言 `rule_id`**，不能只断言"没抛异常"——否则测不出兜底是否真的生效；
- 正例与负例都要有，负例断言消息含具体非法值；
- 不得为通过测试而放宽校验。

## 回滚方式

`git checkout -- adapters/state_trigger/trigger.py adapters/state_trigger/tests/test_trigger.py adapters/state_trigger/README.md`
（三份文件在 T17 提交里已入库，可精确回滚）。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T17b-adapters-修兜底规则语义.md)" --dir （仓库根）
```

**数据分级：公开级**——只用 `packs/demo-brief/` 的公开 demo 数据。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
