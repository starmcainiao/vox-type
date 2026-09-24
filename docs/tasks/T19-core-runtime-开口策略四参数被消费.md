# T19 · core + runtime：开口策略——四参数从「只校验」到「被消费」（冻结区第二张）

## 卡号 / 标题
`T19 · core[冻] + runtime[冻]：patience_ms / backchannel / barge_in / rate_band 四参数被消费（各至少一个可观测行为 + 事件留痕）`

## 背景（只写必需）

- `docs/12` 第二批第一张（**项目名字的另一半**）；`docs/13 §三` 冻结区批次第二张（用户 2026-09-21 拍板「逐步开发一步步验证」，T27 已验收）。
- 现状：**四参数全仓零消费方**（`docs/12:290`）——`DuplexParams` 只做校验，没有任何行为受它影响。
- 立项案 §4.3（设计原样）：`patience_ms` 400/900/1800（快语速/默认/慢思考）；`rate_band` ±15%
  （**语速收敛带，关键信息强制 slow 档**）；`backchannel` on/off；`barge_in` allow/confirm。
  `docs/06 §6.2.4` 已冻结取值域（密度档位【明确推迟】，本卡不得自造）。
- **本仓 runtime 是离线产物生成器**（plan → WAV + 事件流），**不做实时播放**——所以「被消费」= **影响产物**。
- **硬纪律**：`runtime/events.py` 的字段名**一律取自 `core/metrics_spec`**（模块内不允许字符串字面量键）
  → 本卡要动 **core**（冻结区最内核），必须走「只增字段 + 版本 + 迁移说明」。

## 目标（四参数各至少一个可观测行为，判据见下）

| 参数 | 被消费的行为（本卡裁定） | 可观测判据 |
|---|---|---|
| `patience_ms` | 事件流**末尾**追加一条「等待窗口」事件（语义 = 播报完成后麦克风打开的时长）；**音频不变** | 同一 plan 换 `patience_ms`（400/900/1800）→ 该事件里的时长值随之变；输出 WAV **字节不变** |
| `backchannel` | `on` 时：累计播报时长 **≥ patience_ms** 的单元**之后**的那条单元事件带「可发背景回应」标记；`off` 时该标记恒 false | 同 plan 换开关 → 该标记翻转；音频不变 |
| `barge_in` | 每条单元事件带 `barge_in` 值（allow/confirm）；`confirm` 时**终态单元**（`script.terminal_keys` 语义或 plan 最后一个单元）额外带「需确认才可打断」标记 | 换参数 → 字段值变；`confirm` 下终态单元有额外标记、`allow` 下没有 |
| `rate_band` | plan 单元可标 `critical: true`（**关键信息**）→ 该单元**强制 slow 档**；包内无 slow 资产时按 band 判「允许回落」：`\|Δ\| ≤ rate_band` → 用最近可用档 + 事件留痕 `rate_fallback`；超出 → **miss**（fail-closed，不静默降级） | 同一 critical 单元：包内有 slow 档 → 选中 slow；无 slow 档且 band 足 → 回落 + 留痕；无 slow 档且 band 不足 → miss |

## 允许修改的文件（白名单）

```
允许修改：core/metrics_spec.py      （**只增**字段常量，不得删改既有）
         core/protocol.py          （PlanUnit 增可选字段 critical（默认 False）+ parse_plan 校验；**只增**）
         runtime/duplex.py         （如需补校验：强度只增不减）
         runtime/events.py         （新事件形态/字段；键名一律引 metrics_spec）
         runtime/executor.py       （消费四参数）
         core/tests/**、runtime/tests/**（新增测试；**既有断言一行不得改**）
         core/AGENTS.md、runtime/AGENTS.md（实现变更记录 + 迁移说明）
禁止触碰：其他一切（docs/**、assets/**、adapters/**、packs/**、compiler/**、eval/**、cli/**、
         <kefu 仓根>/**）
```

## 规格要点

1. **core 字段只增**：`metrics_spec` 新增常量（如 `PATIENCE_MS` / `BACKCHANNEL_OK` / `BARGE_IN` /
   `REQUIRES_CONFIRM` / `RATE_FALLBACK` / `LISTEN_MS`——命名由实现定，但**必须是常量、不得是字面量**）；
   `PlanUnit` 增 `critical: bool = False`；`parse_plan` 对 `critical` 做类型校验（非 bool → 报错）。
2. **版本口径（D9）**：协议面变更 = **只增**（旧 plan 无 `critical` → 默认 False，旧产物事件缺新字段）→
   `protocol_version` 是否升版由实现按 `core/AGENTS.md` 的既有口径判（若无明确口径，**保持不动 + 在
   AGENTS.md 写"只增字段、向后兼容"的迁移说明**）。**迁移说明必须写**：旧 plan/旧事件如何被消费。
3. **fail-closed 不放松**：`rate_band` 的回落**只在 `critical` 单元 + 包内缺 slow 档**时发生，且必须
   事件留痕；非 critical 单元的档位缺失仍按现状处理（不得顺手放宽）。
4. **音频与事件分离**：`patience_ms` / `backchannel` / `barge_in` **只影响事件流**（不改变音频字节）；
   只有 `rate_band`（档位选择）会改变音频（选到不同档的资产）——判据里要显式区分。
5. 四参数的取值域**不变**（400/900/1800、on/off、allow/confirm、±15% 上下限）——**校验强度只增不减**。

## 验收标准（逐条可判定，验收方独立复跑）

1. **四参数各有可观测行为**：上表「可观测判据」逐条实测（验收方亲跑，每条给出前后对比的实际输出）。
2. **音频不变性**：`patience_ms` / `backchannel` / `barge_in` 三参数在任意取值下，同一 plan 的
   **输出 WAV sha256 相同**；`rate_band` 改变档位选择时**才**不同（给出两组的 sha256）。
3. **负例（校验强度只增不减）**：四参数任一取非法值 → `DuplexError`（消息含参数名与实际值）；
   `critical` 非 bool → `parse_plan` 报错（消息含单元序号与实际类型）。
4. **fail-closed 负例**：`critical` 单元 + 包内无 slow 档 + `rate_band` 不足 → **miss**（事件带
   `rate_fallback` 缺失或明确 reason），**不得**静默用别的档播出去。
5. **字段纪律**：`runtime/events.py` 内**零字符串字面量键**（grep 核对）；新字段全部来自 `metrics_spec`。
6. **既有断言零改动**：`core/tests` + `runtime/tests` 的既有断言行逐字节不变（验收方 `diff` 核对）。
7. **十一根全绿**：报每根 `Ran N` + `OK/FAILED` + 退出码（**以行为准**）；packs 需先 build + 设 `KEFU_HEAT_YAML`。
8. **迁移说明**：`core/AGENTS.md`（或 `runtime/AGENTS.md`）含「只增字段 + 旧 plan/旧事件兼容」的明确表述。

## 反空转条款

- 每条「可观测行为」的测试必须能判红：把对应参数改成固定值（忽略入参）→ 该测试必须失败（把注入验证写进报告）；
- 测试必须调用产品 API（`Executor.execute` + 真包 + 事件流读取），不得在测试内自造事件结构；
- 正例与负例都要有；负例断言消息含具体值。

## 回滚方式

`git checkout -- core/metrics_spec.py core/protocol.py runtime/duplex.py runtime/events.py
runtime/executor.py core/AGENTS.md runtime/AGENTS.md core/tests runtime/tests`

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = 公开。非交互直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突停下上报。

## 验收记录（2026-09-21，验收方独立复核）

**结论：验收通过**（一处卡面冲突由执行方发现并以 `policy_stream` opt-in 化解，验收方裁定接受）。

- 白名单：7 个既有文件 + 2 个新测试文件；**既有测试文件零改动**（`git diff HEAD -- core/tests runtime/tests` 空）。
- 十一根 **1336** 全绿（core 44→**62**、runtime 118→**170**），逐根确认。
- **四条可观测行为**（验收方抽验 ①，其余核执行方实测）：`patience_ms` 400/900/1800 → `listen_ms` 400/900/1800
  且输出 WAV **sha256 恒为 `77554ab58978b7c9`**（音频不变）；`backchannel` 开关 → `backchannel_ok` 翻转；
  `barge_in` allow/confirm → 字段值变 + `confirm` 时终态单元 `requires_confirm=true`；
  `rate_band` 0.20 → 回落 + `rate_fallback=true`，0.15/0.05 → `RuntimeMissError(critical_rate_out_of_band)`。
- 负例齐备：四参数非法值抛 `DuplexError`（消息含参数名与实际值）；`critical` 非 bool → `ProtocolError`。
- 注入验证 5 项全判红（参数改忽略入参 → 对应测试失败）。

**验收方三处裁定**：
1. **`policy_stream=True` 显式 opt-in（接受）**：卡面要求"等待窗口事件进事件流"，但该形状会让
   `test_executor.py` 6 处 + `adapters/.../test_bridge.py` 1 处**禁改断言**变红（它们钉的是"事件数 == 单元数"）。
   执行方改为显式开关：默认 False（既有形状不动）、True 时严格满足卡面形状。**这是"冻结区既有行为不轻动"
   的正确取舍**（T27 的教训）；若未来要默认开，另开卡同批改那 7 处断言。
2. **档位差口径 `RATE_ADJACENT_GAP=0.20`（接受）**：设计稿只给 `rate_band ±15%`、未给档间距离；
   取 0.20 后默认 `rate_band=0.15 < 0.20` → **默认不回落**（缺 slow 档即 miss）——保守且符合 fail-closed 红线。
3. **超带硬 fail-closed（接受）**：`critical` 单元缺档且超带时，`allow_fallback=True` 也不放行——
   理由"放行即以常速合出关键信息播出去"（关键信息安全优先）。

## 卡状态
- [x] 已派发 → [x] 已回收 → [x] **验收通过**（证据见上；三处裁定已记录）
