# fin-cs · 话术准入申报表（A1/A2 申报制）

> 判据：`docs/14 §三` 验收操作口径；A1/A2 定义见 `docs/14 §二`。本文件是**申报制**（判据机器化未做，`docs/14 §四`）。
> 申报人 = 内容合规自检（AI）；生效以验收方逐条过 checklist 为准。

## 一、抬头

| 项 | 值 |
|---|---|
| 包 | `packs/fin-cs`（`pack_id=fin-cs`，`pack_version=1`，`protocol_version=0.1`） |
| key 数 | **17**（`phrases.json` 逐个数过，见 §二 表 17 行） |
| 资产量 | 102 条 = 17 key × 2 variant × 3 语速档（`pack.json` `rates=["normal","slow","fast"]`） |
| 数据来源 | 改写自 `tongyi_dianjin/DianJin-CSC-Data`（ModelScope，`license_spdx=mit`，`size_bytes=9037158`，`sha256=248f88b9…`，见 `labs/corpus-harvest/corpus.lock.json`） |
| 数据分级 | 本包**无内部数据**：无用户录音、无真实会话原文、无内部地址/token；逐字原文另存 `golden/`（不进公开分发，`docs/11 §11.7`） |
| 改写声明 | `phrases.json` 为**改写版**、不逐字复制语料原文（`README.md:13`）；改写的逐条对照证据**未在包元数据中保留** → 该条为「依据不足」 |
| golden 状态 | `golden/acceptance.jsonl`（真人验收集 B）**尚未创建**（`golden/README.md:25`）；对照集 A 为半自动标注，非真人逐条判 |

## 二、逐 key 申报表（17 key，A1=流程走到第 k 步必说 / A2=业务系统可枚举状态）

| key | A1/A2 | 流程步骤（A1）或枚举来源（A2） |
|---|---|---|
| `greeting_inbound` | A1 | k=1 来电接入，首句必说（`script.json:7`） |
| `compliance_notice` | A1 | k=2 合规录音告知，开场强制（`script.json:8`；`docs/14 §一` 列明） |
| `verify_identity` | A1 | k=3 身份核对，账户操作前置门槛（`script.json:9`） |
| `asr_clarify` | A1 | 转写层信号质量不足 → 系统发起重述（`script.json:11`；非用户措辞触发） |
| `error_retry` | A1 | 理解失败 → 重述，最多 `max_retry=3`（`script.json:12-14`） |
| `restate_issue` | A1 | k=5 复述确认，核对前必说（`script.json:15`） |
| `probe_detail` | A1 | k=6 缺口问询：仅当流程缺槽（时间/次数）才发 |
| `empathy_hardship` | A1 | k=7 情感管理环节（`script.json:17`） |
| `hold_notice` | A1 | k=8 进入查询等待的过渡（`script.json:18`） |
| `inform_fact` | A2 | 系统结论播报：枚举源 = 账户状态表；槽值由 `slots={amount,overdue_days}` 现场合成（`script.json:19`）——**枚举表本体未声明（见下）** |
| `offer_options` | A2 | 方案清单播报：枚举源 = 业务方案表（`script.json:21`）——**枚举表本体未声明** |
| `execute_solution` | A1 | k=9 执行动作前的确认/告知（`script.json:22`） |
| `ask_feedback` | A1 | k=10 服务评价请求（`script.json:23`） |
| `relationship_extend` | A1 | k=11 关系延续（`script.json:24`） |
| `off_script_handoff` | A1 | k=12 越权兜底，`terminal_keys` 之一（`script.json:3`） |
| `handoff_human` | A1 | 转接动作播报，`terminal_keys` 之一（`script.json:3`） |
| `closing_thanks` | A1 | 正常挂机告别，`terminal_keys` 之一（`script.json:3,25`） |

**计数自校**：上表数据行 = **17**（`wc -l` 不可信——markdown 表格续行会虚增）。与 `phrases.json` 的 17 个 key 一一对应，无遗漏、无越界。

**待人工复核（3 key）**——均不是 A1/A2 归类摇摆，而是 **A2 要求的枚举表本体缺证据**：
`inform_fact`、`offer_options` 按条款需「业务方确认的完整枚举表」，包内只有 `key` 与槽值形态，**枚举表未在仓内声明** → 现按 A2 申报，属**证据不足**；
`probe_detail` 的槽位清单（时间/次数）无业务侧出处 → 流程步骤为申报者据 variant 推断，请复核。

## 三、禁入项 ⅰ–ⅳ：零命中

- **ⅰ 从用户话术反推 key —— 不命中**：17 个 key 全部由业务流程步骤或系统状态产出，`script.json` 的 `units` 序列即其证据；`vox bench` 的 `hit_rate=1.0` 是 key 驱动档的**回归闸门、不是真实覆盖率**（`README.md:24-25`）。
- **ⅱ 语义模糊兜底 key —— 不命中**：无「怎么引都能挂」的兜底话术；两个转人工/挂机出口（`off_script_handoff`、`handoff_human`）是**作者显式声明的**流程出口（`terminal_keys`），不是语义匹配兜底。
- **ⅲ 纯话术模板复用 —— 不命中**：`phrases.json` 为改写版，未整句搬用语料话轮（`README.md:13`、`docs/11 §11.7`）；逐字原文留在 `golden/` 不进分发。**保留项**：改写的逐条对照证据未随包保留，无法逐句证明「非逐字」，只能靠改写声明。
- **ⅳ 判据外的高频主张 —— 不命中**：本包不以出现频率为入包理由；`docs/11 §11.9.4` 实测（逐字档 0.00%、语义档 11.8%、句级可复用 1.8%、流程决定性 23.0%）正是用来否掉频率路线的。

## 四、形式条款 B1–B4 与 B5 抽查

**B1–B4 由 `sh bin/vox pack check packs/fin-cs` 的输出证明**（源校验 B1「一句一 variant」= `compiler/source.py:232-257`，`_SENTENCE_TERMINATORS="。！？!?"` ≥2 即拒；B2 无占位符；B3 包内无逻辑 = `packs/AGENTS.md §⑤`；B4 全部已审核 = `compiler.check_properties`）。
**该命令由验收方统一执行，本人未运行**——B1–B4 在此**记为「未运行」**，不以人工数标点代替机器判定。
（仅申报者自查、非判定：17 key × 2 = 34 条 variant，句末标点计数全部为 1，无 `[ ]`/`【】`/`{}` 形态占位符。）

**B5 抽查 3 条 variant（主语句是「业务/系统」，不是对用户措辞的猜测）：**

1. `compliance_notice`：「本次通话可能会被录音，用于服务质量提升。」—— 主语是「本次通话」，陈述合规事实；不含对「用户愿不愿被录」的猜测。
2. `asr_clarify`：「刚才有几句话没听清，麻烦您重说一次好吗？」—— 主语是「我（坐席/系统）」，重述由转写结果触发，非对用户说辞的推断。
3. `off_script_handoff`：「您反馈的情况需要专人跟进，我为您转接人工同事。」—— 主语是「该情况」，转接由流程越权判定触发，不预设用户诉求。

## 五、复现

`sh bin/vox pack check packs/fin-cs`
