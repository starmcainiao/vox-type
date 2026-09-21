# vox-type

> **Pre-cast voice assets + duplex behaviour controller for voice agents.**
> 让语音链路里**流程决定的那部分话术**零延迟、零成本、可审核；不确定的部分才动用模型。
> （公开语料实测：事实密集型客服里流程决定性话轮约占 23%——适用边界见 `docs/11`。）

**Status**：**十一个测试根全绿（1,142 条测试，含 2 条按设计 skip）**，端到端闭环已通——
`compiler` 出包 → `assets` 校验 → `runtime` 播包 → `eval` 对拍；`bin/vox` 五命令可用；
已通过适配器接入一个开源客服框架**真链路实测**（key 驱动档首帧 P50 0.14 ms，n=5，见 T11 验收；
docs/09 另轮 0.232 ms，n=20；
LLM 自由文本档 0% 命中——命中率上限由上游表示形态决定，见 `docs/09` / `docs/10`）。
业务包 3 个（`packs/repair/`、`packs/fin-cs/`、`packs/demo-brief/`），新增业务 = 新增目录、零内核改动。
回读 CER 基准已可复现（本地 ASR 回读，真机闭环 CER 0.0，见 `eval/readback.py` 与 `docs/13`）；
方言域 ASR 边界已实测（Wu-Bench 吴语 100 条，CER mean 0.269，见 `labs/wubench-dialect-2026-09-19/`）。
License: **Apache-2.0**（2026-09-19 拍板；与仓内 MIT/Apache-2.0 派生语料兼容——语料台账见
`labs/corpus-harvest-2026-09-18/`；`packs/*/golden/` 按 `packs/AGENTS.md` 不进公开分发）。

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
- `docs/06`~`docs/08` — 预铸/执行补定口径、链路级对拍、CLI 行为口径
- `docs/09`~`docs/11` — 真链路对拍、命中口径、语料自给与语义提案（含实测边界）
- `docs/12` — 前置包定位与任务清单（个人助手形态）
- [`docs/14-预铸准入判据.md`](docs/14-预铸准入判据.md) — **哪些话允许"固定"**（准三类/禁四类，2026-09-19 拍板成文；新增 key 前必读）
- [`docs/16-行业痛点与切入点分析-2026-09.md`](docs/16-行业痛点与切入点分析-2026-09.md) — 市场验证：合规可审核的预铸话术资产层（含来源）
- [`docs/17-业界坐标与改造预测-2026-09.md`](docs/17-业界坐标与改造预测-2026-09.md) — 前瞻：约束解码（Trie/FSM）是「brain 按 key 说话」的下一跳；缓存分层全景（含来源）
- [`docs/13-未完成清单.md`](docs/13-未完成清单.md) — **「还没做完什么」的唯一权威来源**（待拍板 / 冻结区批次 / 已关闭裁定）
- `docs/tasks/` — 全部任务卡与逐卡验收记录（`00-索引.md` / `01-接手指南-新会话入口.md`）
- `labs/` — 一次性实验与外部对拍（不进任何层契约，产物自带口径与原始数据）


## Quick Start（纯标准库，Python ≥ 3.12；零第三方依赖）

```sh
git clone <本仓库> vox-type && cd vox-type

# 1) 全量测试（十一个测试根）
for d in core rules assets adapters compiler runtime eval cli tools trigger packs; do
  python3 -m unittest discover -s $d 2>&1 | tail -1
done

# 2) 铸一个业务包（预铸成功率必须 100%）
bin/vox pack build packs/heat_kefu --out ~/vox-build/heat-kefu --json

# 3) 查包/对拍/质检
bin/vox pack check packs/heat_kefu --json
bin/vox bench ~/vox-build/heat-kefu --help   # 离线对拍（详见 docs/08）
bin/vox verify ~/vox-build/heat-kefu         # 对任意产物跑质检
```

可选评测组件（不装也能跑全量测试）：本机模型服务 oMLX（回读 CER 与 ASR 实测用，
`eval/readback.py --asr adapters.asr_omlx:OmlxAsr`）；kefu-agent 集成见
`adapters/framework_kefu/`（环境变量 `KEFU_AGENT_ROOT` 指向其仓根）。

## 第三方语料与归属

- `packs/fin-cs/` 话术为**改写**（`docs/11 §11.7` 禁止逐字复制语料原文），源语料 MIT /
  Apache-2.0，来源与 sha256 台账：`labs/corpus-harvest-2026-09-18/corpus.lock.json`；
- `labs/corpus-harvest-2026-09-18/raw/`、`labs/corpus-harvest-2026-09-18/corpus.lock.json`
  含第三方语料**逐字转写**（均为可再分发许可，本仓保留来源台账即 attribution）；
- `packs/fin-cs/golden/`、`packs/heat_kefu/admission.md` 等验收资产随仓（无真人隐私；
  真人标注（若未来出现）将按 `docs/11 §11.7` 排除出公开分发）；
- 环境变量：`CORPUS_ROOT`（语料根，缺省 `~/corpus`）、`KEFU_AGENT_ROOT`（kefu 仓根）、
  `KEFU_PRECAST_*`（预铸钩子开关，见 `adapters/framework_kefu/`）。

## 贡献与边界

- 设计讨论期，欢迎提 issue 质疑协议；代码开工后按里程碑推进。
- **本仓库不含任何用户录音、会话数据或私有部署信息**——资产库内容只使用公开 demo 话术。
