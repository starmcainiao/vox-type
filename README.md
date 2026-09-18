# vox-type

> **Pre-cast voice assets + duplex behaviour controller for voice agents.**
> 给语音 Agent 的固定部分：零延迟、零成本、可审核；不确定的部分才动用模型。

**Status: **八层可跑（落盘基线见 `git log`）**：协议与资产包格式已冻结，`compiler`/`runtime`/`eval` 打通端到端，`bin/vox` 五个命令可用；`packs/repair/` 是示例业务包；首组真实数字见 `docs/09`。尚未接入任何生产链路（接入规格见 `docs/10`）。** 本仓库目前只包含设计文档与调研，
代码待协议定稿后开工。License: **TBD**（候选 Apache-2.0）。

---

## 一句话

大厂做端到端语音模型（语音进、语音出）；我们做**模型外面的那一层**——
把「哪些话该预铸成音频、怎么按 key 取、命中怎么播、未命中怎么兜底、双工节奏怎么配」
做成开放协议 + 参考实现。

## 为什么是这一层

1. **端到端模型把语义行为压进权重里**：语速档、耐心窗、回读密度这些业务参数，
   在 E2E 模型里改不了、审不了、版本化不了——每个业务都得重训。
   资产层天生是**显式、可编辑、可审核、可版本化**的。
2. **延迟的账**：级联链路里 LLM 首 token（300–1000ms）才是大头，TTS 首包只占 75–200ms。
   所以预铸必须「文本 + 音频成对」——命中时**连 LLM 一起跳过**，才有 2.5s → 0.5s 量级的收益
   （Vapi 公开案例口径）。
3. **业界已验证但没开源**：预录音（IVR 传统）、音频缓存（Vapi 2025）、
   部分语音预测整句并预取（Amazon, Interspeech 2023）、推测式语音处理（NVIDIA）——
   都是各家私有实现。**「plan 键值寻址的预铸资产层 + 双工参数控制器」这个组合没有现成开源件**
   （检索记录见 [docs/04-prior-art.md](docs/04-prior-art.md)）。

## 三件东西（协议草案）

| 件 | 职责 | 形态 |
|---|---|---|
| **plan** | 控制面：模型/状态机输出 `{key, slots, variant, rate}` 序列，而非自由文本 | JSON 协议 |
| **asset registry** | 数据面：`key → 音频 + 版本 + TTL + 变体池 + 语速档`，含文本指纹防「文音不符」 | 端上 SQLite+文件 / 服务端 KV |
| **duplex controller** | 行为面：耐心窗（自适应端点）、语速档收敛、留白/背景回应、打断策略 | 配置 + 策略代码 |

细节见 [docs/02-protocols-draft.md](docs/02-protocols-draft.md)。

## 设计要点（讨论沉淀）

- 字模最小粒度 = **整句**（含完整语调轮廓），拼接只发生在句边界 + 自然停顿；
  词级拼接仅在**封闭词表**（数字/金额/日期/是-否）合法。
- 难点不在预铸，在**一致性**：同源 TTS、同版本、统一响度、变体池防复读机、
  预铸流水线自动质检。
- 预存的是**话术**（静态资产），**用户数据不预存**——槽值现场本地合成、不进资产库。

见 [docs/03-design-notes.md](docs/03-design-notes.md)。

## 证据计划（开源前先有数据）

五条指标：首响延迟 P50/P99 · 预铸命中率/时长占比 · 单位会话成本 ·
拼接自然度（回读 CER + 盲测 A/B）· 双工质量（打断准确率/轮转延迟）。
数据集与评测协议见 [docs/05-evidence-plan.md](docs/05-evidence-plan.md)。
其中「**拼接自然度评测协议**」目前业界没有标准基准，是本仓库打算贡献的首个公开资产。

## 文档索引

- [docs/01-problem-and-scope.md](docs/01-problem-and-scope.md) — 问题与被砍掉的范围
- [docs/02-protocols-draft.md](docs/02-protocols-draft.md) — 三契约草案
- [docs/03-design-notes.md](docs/03-design-notes.md) — 设计结论（为什么这么设计）
- [docs/04-prior-art.md](docs/04-prior-art.md) — 同类项目检索与缺口论证
- [docs/05-evidence-plan.md](docs/05-evidence-plan.md) — 数据集与验证方案

## 贡献与边界

- 设计讨论期，欢迎提 issue 质疑协议；代码开工后按里程碑推进。
- **本仓库不含任何用户录音、会话数据或私有部署信息**——资产库内容只使用公开 demo 话术。
