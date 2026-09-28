# T16 · packs+adapters：前置包形态定稿（`state` 格式 + `trigger.json` + 校验器）

## 背景（只写必需）

`docs/12-前置包-定位与任务清单.md` 定了前置包的定位与任务清单。本卡是**前置包线的入口卡**：
先把「状态长什么样」和「状态怎么变成 plan」这两件事的形式定死，后面的触发器（T17）、
业务状态适配器（T21）、结论项派生（T22）才有地基。

**为什么落在扩展区而不是内核**：`docs/10 §10.1` 定死本层**不负责"该说什么"**，
而 `state → plan` 恰恰是"决定什么说"——它属于**接缝之上**，不属于本层。
因此本卡**只新增 `adapters/` 与 `packs/` 下的文件，一颗冻结区都不动**（见 `docs/12` 的「分层纪律」）。

规格依据（执行前先读）：
- `docs/12 §12.10`（`state` 必须分层：结构化层 / 敏感层）、`§12.8`（单轮播报预算）
- `docs/11 §11.4`（多文件说明：本卡不需要联网）
- `compiler/source.py`（源格式校验的既有态度：**未知字段必须报错**，typo 不许静默丢弃）
- `packs/AGENTS.md`（包是数据不是代码，包内不得 import Python）
- `compiler/checks.py` 的 C3b（`key_not_in_library`）——本卡的 key 校验要与它同一口径

## 目标（可验收的产物）

- 产物 1：`adapters/state_trigger/__init__.py`（导出公开 API）
- 产物 2：`adapters/state_trigger/format.py` —— `state` 快照与 `trigger.json` 的**格式定义 + 校验器**
- 产物 3：`adapters/state_trigger/tests/__init__.py`、`adapters/state_trigger/tests/test_format.py`
- 产物 4：`packs/demo-brief/` —— 一个**公开 demo 前置包**（工单到期提醒场景），含
  `pack.json` / `phrases.json` / `script.json` / `trigger.json`

### 产物 2 必须提供的公开 API（名字照用，签名可加可选参数）

```
load_trigger(pack_dir) -> Trigger          # 读 pack.json+phrases.json+trigger.json，校验后返回不可变对象
validate_state(state, declared) -> None    # 校验一份 state 快照；不合规抛 StateError
check_budget(plan, budget_chars) -> None   # 校验一份 plan 的总字数；超预算抛 BudgetError
```

### 格式的硬要求（这是本卡的主要产出，逐条都要有测试）

1. **`state` 必须扁平、字段必须预先声明**：`trigger.json` 里声明该前置包接受哪些 state 字段；
   **出现未声明的字段 → 报错**（对齐源格式对 typo 的态度，不许静默忽略）；
2. **`state` 必须分层**：结构化层（状态码/枚举/计数/布尔，**可入库、可导出**）与
   敏感层（账号/金额/原始文本，**只落本地**）分开存放；
   **敏感值出现在结构化层 → 报错**（`docs/12 §12.10` 末的分层纪律）；
3. **`trigger.json` 只允许产出「已审核的话术」**：每个 plan 单元必须引用 `phrases.json` 里
   存在的 key；**引用不存在的 key → 报错**（对齐 C3b `key_not_in_library`）；
   **禁止在 `trigger.json` 里写 `SAY_LIVE` / 自由文本**——前置包不产生未审核文本；
4. **`trigger.json` 未知字段 → 报错**（与 `compiler/source.py` 对 `pack.json`/`phrases.json` 的既有做法一致）；
5. **确定性**：同一份 `state` + 同一份 `trigger.json` → **逐字段相同**的 plan（顺序、变体、语速档全确定）；
6. **单轮播报预算**：`trigger.json` 含单轮总字数上限（缺省 **60 字**，`docs/12 §12.8` 实测语速 4.64 字/秒）；
   `check_budget` 超限必须抛错，**错误消息必须含实际字数与预算值**。

### 产物 4（demo 包）的要求

- 场景自选但必须**公开、无私人数据**（建议：工单到期提醒 / 预约确认，`docs/12 §12.9` 的例子里有）；
- 6–8 个 key，`packs/repair/` 与 `packs/fin-cs/` 是现成样板（照它们的 prose/字段写）；
- **必须通过 `./bin/vox pack check packs/demo-brief`**（这会把既有五类规则 + 四属性也验一遍）；
- 包内**不得出现任何 Python**，不得出现 `slots` 字段以外的运行时数据。

## 允许修改的文件（白名单）

```
允许新增：adapters/state_trigger/**（含 adapters/state_trigger/tests/**）
允许新增：packs/demo-brief/**
允许修改：无
禁止触碰：其他一切文件——尤其不得改 core/ rules/ compiler/ assets/ runtime/ eval/ cli/（这五个是冻结区）、
          不得改 docs/、不得改 README.md / AGENTS.md、不得改 packs/repair/ 与 packs/fin-cs/
```

## 禁止事项

- **不得新增第三方依赖**（只用标准库 + 仓库自身模块）
- **不得改冻结区任何文件**——若你判断必须改才能完成任务，**停下来在报告里说明**，不要自己动
- 不得改动本卡白名单以外的任何文件
- 不得"顺手优化"、"顺手重构"、不得改动与本卡无关的格式
- **不得在测试里联网**（本卡不需要网络）
- **不得为了让测试好写而放宽校验**；未知字段 / 未声明 key / 超预算一律**报错**，不许降级成警告

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿（**注意是 `-s adapters` 不是 `-s adapters/tests`**——
   后者会漏掉嵌套目录）；且基线不破：本卡之前 adapters 是 **125 条**，本卡之后必须 **≥125 且全绿**；
2. `./bin/vox pack check packs/demo-brief` → **rc 0**、`passed: true`；
   `./bin/vox pack check packs/repair` 与 `packs/fin-cs` → **仍 rc 0**（不得因本卡退化）；
3. **正例**：`load_trigger('packs/demo-brief')` 成功；`validate_state` 对一份合法快照通过；
   `check_budget` 对一份未超预算的 plan 通过；
4. **负例（未声明字段）**：state 里多一个 `undeclared_field` → 抛 `StateError`，
   **消息必须含 `undeclared_field`**；
5. **负例（敏感值在结构化层）**：把账号/金额类值放进结构化层 → 抛错，消息含字段名；
6. **负例（未知字段）**：`trigger.json` 里塞 `typo_field` → 抛错，消息含 `typo_field`；
7. **负例（未审核 key）**：`trigger.json` 引用一个 `phrases.json` 里没有的 key → 抛错，
   **消息含该 key 名**；
8. **负例（自由文本）**：`trigger.json` 的某个 plan 单元带 `SAY_LIVE` 或 `text` → 抛错；
9. **负例（超预算）**：构造一份总字数 61 字的 plan（预算 60）→ `check_budget` 抛错，
   **消息含 `61` 与 `60`**；
10. **确定性**：同一 state + 同一 `trigger.json` 跑两次，plan 序列**逐字段相同**（写进测试断言）；
11. `git status --porcelain -uall` 显示的新增文件**全部**落在白名单内，冻结区**零 diff**
    （我会跑 `git diff --stat -- core rules compiler assets runtime eval cli` 应为空）。

## 反空转条款（每张卡必带，T01 教训）

- **测试必须调用产品 API**：测试必须 import `adapters.state_trigger` 的公开函数，
  不得在测试文件里复制/重写被验逻辑（违者整卡退回）；
- 测试名必须与实际调用路径一致（禁止名为 `*_in_unit` 却只测独立辅助函数）；
- **正例与负例都要有**，且负例断言**错误消息包含具体非法值**（见上面第 4/6/7/9 条）；
- 不得为通过测试而放宽校验；既有校验强度只增不减。

## 回滚方式

本卡只新增文件 → 回滚 = `git clean -fd adapters/state_trigger packs/demo-brief`。不触碰既有文件，回滚无残留。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（跑在 ZCode 的「商汤」provider 上，自带 `Write/Edit/Bash` 直接落地）。
定义：`~/.zcode/agents/vox-card-executor.md`。

**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T16-packs-前置包形态定稿.md)" --dir （仓库根）
```

**数据分级：公开级**——demo 包只用公开/自造场景话术，**不含任何用户录音、真实会话、内网地址、token**。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
