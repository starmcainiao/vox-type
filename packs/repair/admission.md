# repair · 话术准入申报表（报修登记）

> 判据依据：`docs/14-预铸准入判据.md` §二（准入判据与禁入项）、§三（操作口径）、§五（回溯适用）。
> 本表为申报制材料（`docs/14 §三`）：每 key 一行 = 类别 + 流程步骤或枚举来源；验收方抽查，机器化检查留口子未落地（`docs/14 §四`）。

## ① 抬头

- **包名**：`packs/repair`（报修登记）｜**key 数：16**（`phrases.json` 实点 16 个 key，20 个 variant）。
- **数据分级**：`pack.json` 无数据分级字段，**未声明**；依据不足，不自定。
- **来源**：自造 demo 流程——`docs/11 §一`「`packs/repair/` 是 16 key 的虚构报修场景」；`docs/20 §1`「`repair`/`demo-brief` 为自造 demo 流程」；`labs/corpus-harvest/README.md §六`「`packs/repair` 就是自建流程这个形态」。无同源指纹可绑（`pack.json` 仅 `pack_id/pack_version/protocol_version/ruleset_version/voice/model_version/rates/locale/duplex`，无源文件路径、无 sha）。
- **回溯判定**：`docs/14 §五` 已登记「全部属 A1/A2（登记流程与状态播报）；补 `admission.md` 即合规」——本表为该判定的逐 key 落纸。

## ② 逐 key 申报表（16 个 key，一个不少）

| # | key | A1/A2 | 流程步骤（A1）/ 枚举来源（A2） |
|---|---|---|---|
| 1 | `greeting_welcome` | A1 | 步骤 1 接通必说（`script.json` units[0]） |
| 2 | `ask_fault_type` | A1 | 步骤 2 采集槽·设备类型（units[1]） |
| 3 | `ask_fault_detail` | A1 | 步骤 7 采集槽·故障现象（units[6]） |
| 4 | `ask_fault_urgency` | A1 | 步骤 8 采集槽·紧急程度（units[7]） |
| 5 | `ask_address` | A1 | 步骤 9 采集槽·地址（units[8]） |
| 6 | `ask_contact` | A1 | 步骤 10 采集槽·联系电话（units[9]） |
| 7 | `ask_time_window` | A1 | 步骤 11 采集槽·上门时段（units[10]） |
| 8 | `confirm_summary` | A1 | 步骤 12 回读确认（units[11]，带 `address`/`contact` 槽，槽值现场合成、不入 variant） |
| 9 | `confirm_again` | A1 | 步骤 13 二次确认（units[12]） |
| 10 | `ticket_created` | **待人工复核** | `docs/14 §五` 判为「状态播报」，但仓内无真实报修系统状态枚举表可供列举 → 写不出完整枚举表，按 A2 条款不成立、暂归 A1「工单创建成功后必说」（units[13]）。同族缺 `assigned/done` 等其余状态 |
| 11 | `promise_visit` | A1 | 步骤 15 工单落库后必说（units[14]） |
| 12 | `error_retry` | A1 | 异常回路：ASR 未听懂，重试步（units[2]/[3]/[4]，`max_retry` 3） |
| 13 | `asr_clarify` | A1 | 异常回路：ASR 低置信步（units[1]） |
| 14 | `off_script_reply` | A1 | 异常回路：用户偏离流程步（不在 script units；`script.json` 的 `user_off_script` 走 live 白名单） |
| 15 | `handoff_human` | A1 | 异常回路：转人工步，终态（`terminal_keys[1]`） |
| 16 | `closing_thank_you` | A1 | 步骤 16 收尾必说，终态（`terminal_keys[0]`，units[15]） |

**计**：A1 = 15，A2 = 0，待人工复核 = 1（`ticket_created`）；合计 16 行 = 16 key。

## ③ 禁入项 ⅰ–ⅳ 零命中

- **ⅰ 从用户话术反推**：不命中——key 全部映射到登记流程步骤或异常回路（本表 §②），不来自用户说法统计；`docs/20 §1` 同判「`repair`…自造 demo 流程」。
- **ⅱ 语义模糊兜底**：不命中——无「用户说啥都能往这引」的话术；两个异常出口 `off_script_reply` / `handoff_human` 是流程状态触发的显式转接，且 `docs/14 §四` 明示显式兜底须作者声明 `when: null`，本包 `pack.json` 未声明 `admission` 字段，未越界充作语义兜底。
- **ⅲ 纯话术模板复用**：不命中——16 条 variant 为自造 demo 文案，非从语料整句搬入（`docs/11 §一`「虚构报修场景」；语料检索 `labs/multi-industry-corpus/out/` 未命中报修话术原文）。
- **ⅳ 判据外「高频」主张**：不命中——无任何 key 以出现频率为入包理由，本表 §② 每条给的是流程步骤号或枚举缺口说明。

## ④ 形式条款 B1–B4 与 B5 抽查

- **B1–B4 一句话**：由 `sh bin/vox pack check packs/repair` 的输出统一证明（本表未自行执行，脚本稍后统一跑）——B1 一句一 variant / B2 variant 无占位符 / B3 包内无逻辑 / B4 全部文本已审核。
- **B5 抽查 3 条 variant**（判读：句子主语是业务/系统，不是对用户措辞的猜测）：
  1. `greeting_welcome`「您好，这里是报修服务热线。」——主语是**服务线（业务实体）**在自报身份，由接通状态触发，不猜用户想听什么；且非虚构人设（`docs/20 §2` 第 3 项同判）。
  2. `ticket_created`「您的报修单已经创建，稍后会有师傅与您联系。」——主语是**工单状态**（已创建）在播报，触发条件是系统落库完成这一业务事件。
  3. `closing_thank_you`「感谢您的来电，祝您生活愉快。」——主语是**本次通话（业务事件）已结束**触发的收尾话轮，`terminal_keys` 之一；不含任何对用户意图的推断。

> B5 说明：`confirm_again`「麻烦您再确认一下」一类句子的主语落在用户侧，本轮未列入抽查；如实登记，留验收方复核。

## ⑤ 复现命令

```
sh bin/vox pack check packs/repair
```
