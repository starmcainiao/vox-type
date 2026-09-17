# rules/ · 五类规则与技能

## 概述

`rules/` 层定义了「剧本该怎么写」的规则本体——同时给模型（当提示词）、给业务（当文档）、给校验器（当检查清单）。

## 五类规则

### R-1 可用词表

**陈述**：每个 literal 必须引用话术库中**已存在**的 key，禁止自造文案。

**正例**：`{"key": "greeting_welcome"}` — key 存在于预铸清单中。

**反例**：`{"key": "nonexistent_key"}` — 引用了不存在的 key。

**判法**：机器（校验器比对 keylist）

---

### R-2 槽位边界

**陈述**：槽值只能来自声明字段；槽值不得进入资产库；不得整段自由文本入槽。

**正例**：`{"slots": {"amount": "¥500"}}` — 槽值是具体字段值。

**反例**：`{"slots": {"amount": "用户刚才说他想转500块钱给他的朋友"}}` — 整段用户原话入槽。

**判法**：机器（校验器检查槽值结构）

---

### R-3 流程边界

**陈述**：剧本必须有出口（无死锁）；追问有上限；无孤立枝。

**正例**：plan 以 END 或确定的结束状态收尾。

**反例**：plan 中出现无出口的 retry 循环。

**判法**：机器 + 人（死锁检测需要图遍历，边界情况需人工判断）

---

### R-4 节奏边界

**陈述**：语速档只能取已预铸的档位（slow/normal/fast）；关键信息（数字/金额/地址）强制 slow。

**正例**：`{"key": "step_confirm_amount", "rate": "slow"}` — 金额信息用 slow。

**反例**：`{"key": "step_confirm_amount", "rate": "fast"}` — 金额信息用了 fast。

**判法**：机器 + 人（语速档合法性可机器判断，关键信息识别需语义理解）

---

### R-5 降级规则

**陈述**：`SAY_LIVE` 只能在白名单场景使用，必须带 `reason`；禁止静默降级。

**正例**：`{"action": "SAY_LIVE", "text": "...", "reason": "资产缺失"}` — 显式声明降级原因。

**反例**：`{"action": "SAY", "text": "临时拼凑的话术"}` — 伪装成资产命中，无 reason。

**判法**：机器 + 人（action 与 text 的语义一致性需人工判断）

## 文件结构

```
rules/
├── AGENTS.md                    # 本层约束
├── README.md                    # 本文件（人读规则文档）
├── ruleset.v1.json              # 机器可读规则集
├── skill.md                     # 给模型的提示词
├── examples/
│   ├── keylist.json             # 预铸 key 清单
│   ├── ok_intake.json           # 合规示例 plan
│   ├── bad_r1_unknown_key.json  # R-1 违反
│   ├── bad_r2_free_text_slot.json  # R-2 违反
│   ├── bad_r3_deadlock.json     # R-3 违反
│   ├── bad_r4_wrong_rate.json   # R-4 违反
│   └── bad_r5_silent_fallback.json  # R-5 违反
└── tests/
    └── test_ruleset.py          # 结构测试
```

## 版本

当前规则集版本：`v1`（`rules/ruleset.v1.json`）

## 依赖

- 允许依赖：`core/`（协议定义）
- 禁止：不得包含任何具体业务的话术（那是 `packs/`）；不得实现校验逻辑（那是 `compiler/checks/`）
