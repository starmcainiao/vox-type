# T16b · adapters：兜底规则必须是最后一条（装载期校验补齐）

## 背景（只写必需）

T16 交付的 `adapters/state_trigger/format.py::load_trigger` 已校验「**至多一条**兜底规则」（`when: null`），
但没有校验它的**位置**。而 `trigger.py::build_plan` 的匹配口径是「**按声明顺序取第一条命中**」——
于是存在一个镜像缺陷：作者把 `when: null` 写在**第一条**，它会对**每一个 state 都命中**，
后面的条件规则**一条都不会再被看到**：配置看起来有条件分支、实际全被兜底吃掉，零报错零留痕。

这与 T17b 修掉的那个缺陷（`when: null` 被实现写成"永不命中"）是同一类——
「**配置看起来起作用、实际不起作用**」，正是 `format.py` 模块注释红线 1 点名的
「写错一个字母 = 该规则永不触发，而零留痕」的静默降级原型。

**本卡口径已定**（`docs/13-未完成清单.md` §四 第 2 条 + T17b 验收记录）：
兜底规则（`when: null`）**必须声明在 rules 的最后一条**，否则装载期报错。
这不是运行时行为变更（匹配口径仍是"按声明顺序取第一条命中"，一行不改），
是把「demo 包恰好排对了」的约定变成「装载器强制」的契约。

规格依据：
- `docs/13-未完成清单.md` §四 第 2 条（修法与落点）
- `docs/tasks/00-索引.md` T17b 验收记录（「兜底优先级按声明顺序 → 接受」的裁定）
- `adapters/state_trigger/format.py` 模块注释红线 1（typo 必响的态度）

## 目标（可验收的产物）

给 `load_trigger` 增加一条位置校验：**`when: null` 的规则若不是 rules 的最后一条 → `TriggerError`**。

- 改动点：`adapters/state_trigger/format.py` 的 `load_trigger` 规则循环（现有
  `unconditional_count` 检查就在那里，位置检查加在同一处，不要另起炉灶）
- 错误消息必须包含：**兜底规则的 rule_id、它的序号（rules[i]）、它后面还有几条规则被遮蔽**
  （让作者一眼看到该把哪条挪到哪里；消息风格与既有 TriggerError 一致，含具体值）

## 允许修改的文件（白名单）

```
允许修改：adapters/state_trigger/format.py
允许修改：adapters/state_trigger/tests/test_format.py
禁止触碰：其他一切文件——尤其 trigger.py（运行时匹配口径一行不改）、
          packs/（demo 包的兜底已在最后，活体回归不许动它来凑验收）、
          docs/、core/、compiler/ 等
```

## 禁止事项

- **不得改 `trigger.py`**——运行时匹配口径（按声明顺序取第一条命中）不变
- 不得改 `packs/demo-brief/` 下任何文件（它是活体回归样本，兜底 `closing` 已在最后一条，应当原样通过）
- 不得改既有校验的强度与消息（「至多一条兜底」的检查保留原样，位置检查是**新增**一条，不是替换）
- 不得新增第三方依赖；不得"顺手优化"与本卡无关的代码
- 不得在测试里复制判定逻辑（反空转，见下）

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿，测试数 **只增不减**（基线 241 条）；
2. **负例（位置非法）**：构造 trigger.json——规则 1 是 `when: null`、后面还有 ≥1 条条件规则 →
   `load_trigger` 必须抛 `TriggerError`，且消息**同时含**该兜底规则的 `rule_id`、其序号、被遮蔽的规则条数；
3. **边界正例（合法位置）**：① 兜底是最后一条（前面有条件规则）→ 装载成功；
   ② 全部规则里只有一条且它是兜底 → 装载成功（它既是第一条也是最后一条，合法）；
4. **活体回归**：`load_trigger('packs/demo-brief')` 原样成功（它的 `closing` 兜底在最后）；
   全层测试跑完 adapters 里既有用例无一被改写期望（只新增用例）；
5. **负例（既有校验不退化）**：两条 `when: null`（一前一后）仍被「至多一条」检查拦下
   （消息与改动前同口径）——证明位置检查是**加**上去的，没有替换或弱化旧检查；
6. 测试必须调用产品 API `load_trigger`（从 `adapters.state_trigger` 或模块路径导入），
   不得在测试文件内自造「兜底位置判定」函数（反空转）；
7. `git status --porcelain -uall` 恰为白名单 2 个文件的改动，无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须调用产品 API；测试名与实际调用路径一致；
- 正例与负例都要有，负例断言**错误消息包含具体非法值**（rule_id / 序号 / 遮蔽条数）；
- 不得为通过测试而放宽任何既有校验；异常抛出点只增不减（验收方会比对 `TriggerError` 抛出点数量）。

## 回滚方式

两个白名单文件都是已跟踪文件：`git checkout -- adapters/state_trigger/format.py adapters/state_trigger/tests/test_format.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T16b-adapters-兜底必须最后一条.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**——只涉及合成 fixture 与 demo 包，不含用户录音、真实会话、内网地址、token。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T16b 节）
  - 验收方独立复跑：adapters 249 全绿（241→+8）；自写四组探针全过（兜底在第一条→报错含 rule_id/rules[1]/遮蔽 2 条及被遮蔽 ids；兜底在最后→装载成功；单条兜底→装载成功；两条兜底 [2],[3]→旧「至多一条」口径消息仍在）；demo 包活体 rc OK；`raise TriggerError` 抛出点 55→56 只增。
  - **落点裁定（执行方申报的偏离，接受）**：位置检查放在规则循环**之后**（同函数内）而非循环内——循环内会让位置检查抢先于「至多一条」触发，使旧口径消息在任何双兜底排布下不可达（执行方实测 4 种排布证明）。卡文「加在同一处」按本意（同函数、不另起机制）解释，非字面循环内。
