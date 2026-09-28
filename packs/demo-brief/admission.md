# demo-brief · 话术准入申报表（A1/A2 申报，docs/14 §三）

## ① 抬头

- 包：`packs/demo-brief`，`pack_id=demo-brief`、`pack_version=1`、`protocol_version=0.1`、`ruleset_version=v1`（`pack.json:2-5`）。
- key 数：**8**（`phrases.json:3-66` 逐条数出，与 `docs/20 §一` 台账的 8 一致）；每 key 恰 2 个 variant（`phrases.json:5-64`），`rates=["normal","slow","fast"]` 全 8 条一致（`pack.json:8`）。
- 数据分级与来源：**公开级 / 自造演示流程**——依据 `docs/tasks/T16-packs-前置包形态定稿.md:121`「公开级，不含任何用户录音、真实会话、内网地址、token」，`docs/20 §一` 记本包为「自造 demo 流程」；8 条 variant 全文自审亦无录音/真实会话/内网地址/token。
- ⚠️ **一处未声明**：`pack.json` 共 18 行（`:1-19`），**无任何数据分级字段**，仓内亦无包内声明机制——`packs/AGENTS.md` 仅规定 `golden/`「不进公开分发」，而本包**不含 `golden/`**（目录实为 `pack.json`/`phrases.json`/`script.json`/`trigger.json` 四件，`git ls-files packs/demo-brief/` 实测）。故分级系**任务卡与自审推导**，不是包内声明，依据不足处如实标注。

## ② 逐 key 申报表（8 行，逐 key 一个不省）

| # | key | A1/A2 | 流程步骤（A1）/ 枚举来源（A2） |
|---|---|---|---|
| 1 | `greeting_ticket` | A1 | 流程走到第 1 步（开场问候）必说。`script.json:7` 首 unit；`trigger.json:15-19` `rule_id=greeting` `when={ticket_status:"处理中"}` |
| 2 | `ticket_status` | A2 | 工单状态播报；枚举来源 = `trigger.json:7` `state_fields.ticket_status.enum = ["待受理","处理中","待验收","已关闭"]` |
| 3 | `due_tonight` | A2 | 到期日结论播报；枚举来源 = `trigger.json:30` `when={is_overdue:false, days_left lte:0}` |
| 4 | `due_days_left` | A2 | 距到期天数播报；枚举来源 = `trigger.json:37` `when={is_overdue:false, days_left gt:0, lte:14}` |
| 5 | `already_overdue` | A2 | 逾期结论播报；枚举来源 = `trigger.json:23` `when={is_overdue:true}`（`state_fields.is_overdue` 为 bool，`trigger.json:10`） |
| 6 | `ask_assignee` | A1 | 流程走到「在岗确认」步必问。`script.json:10`；`trigger.json:44` `when={assignee_confirmed:false}` |
| 7 | `offer_help` | **待人工复核** | `script.json:11` 有 unit，但 `trigger.json` 六条规则（`:14-56`）**无一条引用** → 触发条件查不到；`docs/13 §四#6` 实测「未引用 key 2 个」，本 key 属其一。无流程步骤/枚举表可写，不敢自行补判 |
| 8 | `closing_brief` | A1 | 流程走到终态必说（收尾）。`script.json:3` `terminal_keys=["closing_brief"]`；`trigger.json:51` `when=null` = 作者显式声明兜底（T17b 裁定，`docs/14 §五` 判合规） |

A1 = 3（`greeting_ticket`/`ask_assignee`/`closing_brief`）；A2 = 4（`ticket_status`/`due_tonight`/`due_days_left`/`already_overdue`）；**待人工复核 = 1**（`offer_help`）。3+4+1 = 8 = 包内 key 数，无遗漏。
说明：A2 四项虽带槽（`{ticket_id}`/`{ticket_status}`/`{days_left}`/`{overdue_days}`），槽值按 `docs/06 §6.2.5` 运行期现场合成、不进资产，不改变 A1/A2 判定；A2 判据 = 该结论可由业务系统可枚举状态推出，故判 A2 不判 A1。

## ③ 禁入项 ⅰ–ⅳ 零命中（依据 docs/14 §二 禁入表）

- **ⅰ（从用户话术反推 key）不命中**：8 个 key 全部由 `script.json` 剧本单元或 `trigger.json` 状态规则声明，无一条来自用户说法统计；`offer_help` 的缺口是**触发条件缺失**，不是反向从用户话术反推而来。
- **ⅱ（语义模糊类 key）不命中**：无「用户说啥都能往这引」的话术；全仓唯一 `when:null` 兜底即本包 `trigger.json:51`（`closing_brief`），属**作者显式声明**的 B 类显式兜底，`docs/14 §五` 已判合规。
- **ⅲ（纯话术模板复用）不命中**：8 条 variant 均自造（`docs/20 §一` 记本包为「自造 demo 流程」），非从语料库整句搬入。
- **ⅳ（判据外「高频」主张）不命中**：本表未引用任何频率/命中率/成本数字作准入依据；8 条全部由流程或可枚举状态推出。

## ④ 形式条款 B1–B5

- **B1–B4**（单句 / 无占位符 / 包内无逻辑 / 已审核）：由 `sh bin/vox pack check packs/demo-brief` 的既有输出证明（源校验 + 五类规则 R-1…R-5 + 四属性 `compiler.check_properties`）。**本表未自行运行该命令**——按派发口径由脚本统一执行；`docs/20 §一` 记载该包 `pack check` rc 0（2026-09-22 实跑），此处仅作旁证引用，不作为本表的检查结论。
- **B5 抽查 3 条 variant**（主语须是业务/系统，不是对用户措辞的猜测）：
  - `greeting_ticket`#1「您好，这里是**工单提醒服务**。」——主语为「工单提醒服务」（系统），是系统自我声明，不是猜用户想听什么。（`phrases.json:6`）
  - `ticket_status`#1「**工单编号 {ticket_id}** 当前状态为 {ticket_status}。」——主语为工单实体及其状态字段（业务数据），陈述业务系统事实，非措辞猜测。（`phrases.json:14`）
  - `already_overdue`#1「**这条工单**已经逾期 {overdue_days} 天，请立即处理。」——主语为工单实体（业务事实），逾期天数由业务系统给出；受众是工单负责人（`docs/20 §二` 已作此认定）。**边界判定：本句是业务事实播报而非猜测用户情绪，但语气词「请立即」偏催办口吻，建议验收方复核是否越界为用户措辞的预设。**（`phrases.json:38`）

## ⑤ 复现

```sh
sh bin/vox pack check packs/demo-brief
```
