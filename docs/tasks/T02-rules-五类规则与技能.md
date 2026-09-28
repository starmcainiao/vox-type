# T02 · rules：五类规则与技能（规则集 / 提示词 / 正反例）

## 背景

`rules/` 层是**规则本体**——一份东西同时给模型（当提示词）、给业务（当文档）、给校验器（当检查清单）。
本卡**只做落地**：下面的规则内容与示例**已由设计方给定**，执行者**不得自行增删规则、不得改写规则语义**，只做"格式化成文件 + 写结构测试"。

依赖：`core/`（T01/T01b 已验收：`core/protocol.py` 的 `parse_plan` / `PlanUnit` / `PRIMITIVES` / `VALID_RATES`）。
必读：`AGENTS.md`、`rules/AGENTS.md`。**不得修改这两个文件。**

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有规则陈述、接口规格与示例话术文本，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 目标（产物）

1. `rules/README.md` — 规则文档（人读）：五类规则逐条陈述 + 正例 + 反例 + 判法（机器/人）。
2. `rules/ruleset.v1.json` — 机器可读规则集，结构：
   ```json
   {"ruleset_version": "v1",
    "rules": [{"id": "R-1", "name": "可用词表", "statement": "...", "judge": "machine",
               "examples": {"ok": "rules/examples/ok_intake.json", "violations": ["rules/examples/bad_r1_unknown_key.json"]}}, ...]}
   ```
   （五条规则 id 固定为 `R-1`…`R-5`，`judge` 取 `machine` 或 `machine+human`）
3. `rules/skill.md` — 给模型的提示词：要求模型按五类规则产出剧本（plan），**明确禁止**：编造话术 key、把用户原文入槽、写出无出口的循环、使用未预铸的语速档、静默降级；并说明"不合规会被拦，宁可拒绝也不要编"。
4. `rules/examples/ok_intake.json` — 一个合规示例 plan（≥4 个单元，含 1 个槽位单元，用 `core.protocol` 的 plan 结构）。
5. `rules/examples/bad_r1_unknown_key.json` … `bad_r5_silent_fallback.json` — **五个反例文件，每个违反且只违反一条规则**（见下表）。
6. `rules/tests/test_ruleset.py` — 结构测试（见"验收标准"）。

### 五类规则内容（照抄，不得改写语义）

| id | 名称 | 陈述 | 反例文件必须违反的点 |
|---|---|---|---|
| R-1 | 可用词表 | 每个 literal 必须引用话术库中**已存在**的 key，禁止自造文案 | 引用一个不存在于 `rules/examples/keylist.json` 的 key |
| R-2 | 槽位边界 | 槽值只能来自声明字段；**槽值不得进入资产库**；不得整段自由文本入槽 | `slots` 里放 `{"text": "<整段用户原话>"}` 这类自由文本槽 |
| R-3 | 流程边界 | 剧本必须有出口（无死锁）；追问有上限；无孤立枝 | 出现 `retry_ask` 自循环（无出口） |
| R-4 | 节奏边界 | 语速档只能取**已预铸**的档位（`slow/normal/fast`）；关键信息（数字/金额/地址）强制 `slow` | 关键回读单元用 `rate="fast"` |
| R-5 | 降级规则 | `SAY_LIVE` 只能在白名单场景使用，必须带 `reason`；**禁止静默降级** | 自由文本单元使用 `action="SAY"`（伪装成命中资产，无 reason） |

补充：还需生成 `rules/examples/keylist.json`（该示例业务已预铸的 key 清单，≥6 个 key），供 R-1 反例引用"不存在的 key"。

### 口径澄清（策划 2026-09-17 增补，派发前补钉）

**`rules/examples/*.json` 的文件根一律是 JSON 数组（不是包装对象）。**
理由：验收标准 3 要求反例能被 `core.protocol.parse_plan` **直接**解析，而 `parse_plan` 只接受列表、且拒绝空列表。
所以**不要**写成 `{"plan_id": "...", "utterances": [...]}` 这种包装结构，直接写 `[ {...}, {...} ]`。
`keylist.json` 不受此约束（它不是 plan，结构自定，但必须是合法 JSON）。

## 允许修改的文件（白名单）

```
允许新增：rules/README.md, rules/ruleset.v1.json, rules/skill.md,
          rules/examples/*.json（ok_intake + 5 个反例 + keylist）, rules/tests/__init__.py, rules/tests/test_ruleset.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、AGENTS.md、README.md、docs/**）
```

## 禁止事项

- **不得增删规则、不得改写规则语义**（内容是给定的，只做落地）。
- 不得实现校验算法（校验器属 `compiler/`，T06 才做）。
- 不得新增第三方依赖（只用标准库）。
- 不得改动白名单外文件；不得顺手重构。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s rules/tests -v` 全绿；
2. `rules/ruleset.v1.json` 的 `rules[].id` 恰好是 `["R-1","R-2","R-3","R-4","R-5"]`（顺序不限，集合相等）；
3. **每个反例文件都能被 `core.protocol.parse_plan` 解析**（即"格式合法但违反规则"——这是反例的定义；若解析失败则反例无意义）；
4. 测试必须**调用产品 API**（`core.protocol.parse_plan`）来验证示例文件可解析，且断言五个反例与五条规则的映射表非空、一一对应；
5. `rules/skill.md` 里出现五条规则的 id 与"禁止编造 key / 禁止静默降级"的明确表述（测试断言这几个关键词存在）；
6. 五个反例各自**只**违反对应规则（人工可判：例如 `bad_r1` 不得同时缺出口）。

## 反空转条款（必带）

- 测试必须调用产品 API（`parse_plan`），不得在测试内复制解析逻辑；
- 不得只断言文件存在——必须断言**内容结构**（如 `rules` 长度、id 集合、示例可解析）；
- 不得为了让测试过而放宽任何断言。

## 回滚方式

本卡只新增文件 → `git clean -fd rules/examples rules/tests` + 手工删除 rules/ 下三个新文件（或 `git clean -fd rules` 后恢复 rules/AGENTS.md）。

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T02-rules-五类规则与技能.md)" --dir （仓库根）
```

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 验收通过（提交 38ebad4）
