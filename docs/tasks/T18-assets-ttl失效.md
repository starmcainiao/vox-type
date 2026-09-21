# T18 · assets + compiler + runtime：TTL / 失效（冻结区第三张）

## 卡号 / 标题
`T18 · assets[冻] + compiler[冻] + runtime[冻]：资产 TTL / invalid_at——过期资产不得被播出`

## 背景（只写必需）

- `docs/12` 第二批第二张（**前置包场景是刚需**——「播报过期事实比不播更糟」）；`docs/13 §三` 冻结区批次第三张。
- 立项案 §4.2：资产字段含 `ttl` / `invalid_at`（对齐 Vapi 踩坑教训：陈旧失效）。
- 现状：**零实现**——manifest 无这两个字段、`lookup` 不看过期、`Executor` 不看过期。
- **本卡的核心红线**：过期资产被引用 → **fail-closed 拒播**（不是"警告后照播"）。

## 目标

| 层 | 改动 |
|---|---|
| `compiler`（源格式） | `phrases.json` 的每条可选 `ttl`（秒，正整数）与/或 `invalid_at`（ISO 8601 时间戳）；校验：类型/取值非法 → `SourceError`（含 key 与实际值）；两者同时给 → 报错（不猜哪个优先） |
| `assets`（manifest） | 资产条目可选 `ttl` / `invalid_at`；`load_pack` 校验类型（非法 → `AssetPackError`）；**旧包无这两个字段 = 永不过期**（向后兼容） |
| `assets`（判定保险丝） | `AssetPack.lookup(..., now=None)` 增**可选**参数：过期条目返回 `None`（与既有失败同形——**保险丝在资产层，任何消费方（含 kefu 钩子）都自动受保护**）；`now=None` 时取当前时间 |
| `assets`（公开辅助） | 新增公开函数 `is_expired(entry, now)`（纯函数，可单测）——`lookup` 与 `runtime` 共用同一判据（**不得两处各写一份**） |
| `runtime` | `Executor` 判定时对过期条目给**明确 reason**（新常量 `REASON_ASSET_EXPIRED`，进 `core/metrics_spec`）且 **fail-closed 拒播**（不写输出文件、不播其他话术——与既有 abort 语义一致） |

## 允许修改的文件（白名单）

```
允许修改：core/metrics_spec.py（只增 REASON_ASSET_EXPIRED）
         compiler/source.py（源格式校验 + PackSource 透传）
         compiler/prebake.py（把 ttl/invalid_at 写进 manifest）
         assets/pack.py（AssetEntry 字段 + load_pack 校验 + lookup(now=) + is_expired 公开函数）
         runtime/executor.py（过期判定 + 明确 reason + fail-closed）
         compiler/tests/**、assets/tests/**、runtime/tests/**（新增测试；**既有断言一行不得改**）
         compiler/AGENTS.md、assets/AGENTS.md、runtime/AGENTS.md（实现变更记录 + 迁移说明）
禁止触碰：其他一切（docs/**、adapters/**、packs/**、eval/**、cli/**、trigger/**、kefu 仓（另一仓，本卡不动））
```

## 规格要点

1. **语义（冻结）**：
   - `invalid_at`：**绝对失效时刻**（ISO 8601）。`now >= invalid_at` → 过期。
   - `ttl`：**相对时长（秒）**，从**预铸时刻**起算 → 预铸时把它折算成 `invalid_at` 落进 manifest
     （manifest 只存 `invalid_at` 与原始 `ttl` 两者之一？——**裁定：manifest 存 `invalid_at`（绝对）+ 可选保留 `ttl` 原值供审计**；
     `lookup` 只按 `invalid_at` 判）。
   - 两者在源里**互斥**（同时给 → `SourceError`）。
2. **`now` 的注入**：`lookup(now=...)` / `is_expired(entry, now=...)` 必须支持**显式传入时间**（评测与测试要能重放，
   不得只依赖 `datetime.now()`）——默认值才是当前时间。
3. **fail-closed 不放松**：过期 → 拒播；`allow_fallback=True` **也不放行**（与 T19 的 critical 超带同款理由：
   播过期事实比不播更糟）。
4. **向后兼容**：无 `ttl`/`invalid_at` 的旧包与旧源**行为完全不变**（既有测试是判据）。

## 验收标准（逐条可判定，验收方独立复跑）

1. **正例**：源带 `invalid_at`（未来时间）→ 铸包成功、manifest 落该字段、`lookup(now=<失效前>)` 命中、
   `Executor` 正常播。
2. **负例（红线）**：`invalid_at` 早于当前 → ① `lookup` 返回 `None`；② `Executor` 拒播且事件/异常带
   **明确 reason = `asset_expired`**；③ **不写输出文件**；④ `allow_fallback=True` 也拒播。
3. **`ttl` 折算**：源带 `ttl=3600` → manifest 的 `invalid_at` == 预铸时刻 + 3600s（**容差 ≤ 2 秒**，
   验收方独立复算）；`lookup(now=invalid_at 之前/之后)` 行为相反。
4. **互斥与非法值**：`ttl` 与 `invalid_at` 同时给 → `SourceError`（消息含 key）；`ttl=-1` / `ttl="3600"` /
   `invalid_at="not-a-time"` → 各自报错且消息含实际值。
5. **保险丝在资产层**：直接调 `pack.lookup(now=<过期后>)` 返回 `None`（不经 runtime）——证明消费方
   （含 kefu 钩子）自动受保护。
6. **既有断言零改动**：`compiler/tests` + `assets/tests` + `runtime/tests` 的既有断言行逐字节不变（`diff` 核对）。
7. **十一根全绿**：报每根 `Ran N` + `OK/FAILED` + 退出码（**以行为准**）；packs 需先 build + 设 `KEFU_HEAT_YAML`。
8. **迁移说明**：三份 `AGENTS.md` 各记「字段只增 + 旧包/旧源行为不变」；`assets/AGENTS.md` 说明
   `lookup(now=)` 的默认语义与评测重放要求。

## 反空转条款

- 过期判定的测试必须能判红：把 `is_expired` 改成恒 False → 负例全红（注入验证写进报告）；
- 时间相关断言**一律注入 `now`**（不得用 `sleep` 等真实时间流逝——评测要可重放）；
- 正例与负例都要有；负例断言消息含具体值（key、时间戳）。

## 回滚方式

`git checkout -- core/metrics_spec.py compiler/source.py compiler/prebake.py assets/pack.py
runtime/executor.py compiler/AGENTS.md assets/AGENTS.md runtime/AGENTS.md compiler/tests assets/tests runtime/tests`

## 执行方式

**首选**：`vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = 公开。非交互直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突停下上报。

## 验收记录（2026-09-21，验收方独立复核）

**结论：验收通过**。

- 白名单：7 个既有文件 + 3 个新测试文件；**既有测试文件零改动**（`git diff --name-only -- compiler/tests assets/tests runtime/tests core/tests` 空）。
- 十一根 **1,398** 全绿（assets 90→**114**、compiler 168→**185**、runtime 170→**191**）。
- **验收方抽验（自写探针，不经执行方的测试）**：`invalid_at=2020-01-01` 的包 →
  `is_expired(now=2026)` = **True**；`pack.lookup(now=2026)` = **None**（**保险丝在资产层** ✓，
  即 kefu 钩子等消费方自动受保护）；`Executor` 对 `allow_fallback=False` **与 `True`** 均抛
  `RuntimeMissError`（消息含 `key='g1'` 与 `reason=asset_expired`）且**不写输出文件** ✓。
- 执行方实测：`ttl=3600` 折算 `invalid_at`（容差 2s，独立复算）；互斥与非法值各报错含实际值；
  注入验证（`is_expired` 改恒 False）→ **22 条判红**；新增测试零 `sleep`（全部显式注入 `now`）。
- 五处执行方不确定项裁定**全部接受**：① `REASON_ASSET_EXPIRED` 真源在 `core/metrics_spec`（卡面如此；
  T19 的 reason 留 runtime 是因它更局部，不构成不一致）；② 过期单元状态取 `MISS`（事实一致）；
  ③ `invalid_at` 规范化 `Z`→`+00:00`（语义等价）；④ `Executor(now=)` 实例级注入（与 `duplex` 风格一致）；
  ⑤ `validate_pack` 追加问题不抛错（沿用既有契约）。

## 卡状态
- [x] 已派发 → [x] 已回收 → [x] **验收通过**（证据见上）
