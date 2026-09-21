<div align="center">

# vox-type

**Pre-cast voice asset layer + duplex behaviour controller for voice agents**
**语音链路的预铸话术资产层 + 双工行为控制器**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org)
[![tests](https://github.com/starmcainiao/vox-type/actions/workflows/tests.yml/badge.svg)](https://github.com/starmcainiao/vox-type/actions/workflows/tests.yml)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-standard%20library-orange.svg)](#quick-start)

</div>

> 让语音链路里**流程决定的那部分话术**零延迟、零成本、可审核；不确定的部分才动用模型。
> 定位：**可短路语音栈的覆盖层**——级联链路（ASR→LLM→TTS）里坐在 LLM 与 TTS 之间，
> 端到端 S2S 模型里做整轮旁路。

**English one-liner**: vox-type pre-bakes the *process-determined* portion of a voice agent's
dialogue into audited, versioned audio assets, and plays them in ~0.2 ms when the upstream model
decides to speak them — falling back to your existing TTS path on every miss (fail-closed, fully
traced). Zero third-party dependencies.

---

## Why

1. **流程话术在替慢链路陪跑。** 一通「查一下账单」的电话，用户要等 ASR + LLM 首 token + TTS
   才能听到「请提供您的户号」——这句话由业务流程决定，却陪整条链路等了几秒，还烧一次推理。
   我们实测：预铸命中读包首响 **P50 0.232 ms**，链路级实时合成 **586.7 ms**（≈2530×，n 见 `docs/09`）。
2. **生成式话术不可审计。** 客服话术（催收/政务/通知）有合规要求，LLM 每次生成都在赌。
   预铸资产 = **字节级一致、可审核、可版本化**；源话术一改，指纹门禁自动让缓存失效（fail-closed）。
   行业背景：AI 骚扰电话投诉年增 230%（财联社 2025-04），厂商靠「500 套合规话术模板」应对——
   模板没有治理，我们有准入判据（[`docs/14`](docs/14-预铸准入判据.md)）。
3. **缓存手法行业皆知，资产层没有开源件。** 「缓存预合成音频」是 Vapi 官方文档里的优化技巧；
   把它做成**有准入、有质检、有指纹、能差量重铸的层**（含双工参数治理）没有现成开源实现
   （同类项目检索见 [`docs/04-prior-art.md`](docs/04-prior-art.md)、市场验证见 [`docs/16`](docs/16-行业痛点与切入点分析.md)）。

**诚实边界**：适用范围 = 流程决定的话轮（真实客服语料实测占 23%，催收/账户服务场景）；
自由对话部分永远走原路——本项目不试图、也不应该覆盖它。

## Architecture

```
 compiler（编译期管线）        trigger（决策表）           runtime（线性执行器）
 phrases.json ──预铸──▶  assets 包 ◀──选 key──  state → plan ──▶ 播包/槽位合成/事件流
   │  五类规则校验         │ key→音频+指纹+TTL     │ 规则按序匹配       │
   │  四属性校验          │ +变体池+语速档         │ 显式兜底           │ 未命中 → fail-closed
   ▼                     ▼                      ▼                   ▼ 走宿主原 TTS（留痕）
 packs/<业务>/（数据）─── bin/vox（check/build/bench/verify/run）─── adapters/<宿主>（钩子）
```

三件核心抽象（协议草案见 [`docs/02-protocols-draft.md`](docs/02-protocols-draft.md)）：

| 件 | 职责 | 形态 |
|---|---|---|
| **plan** | 控制面：模型/状态机输出 `{key, slots, variant, rate}` 序列，而非自由文本 | JSON 协议 |
| **asset registry** | 数据面：`key → 音频 + 版本 + TTL + 变体池 + 语速档`，文本指纹防「文音不符」 | 端上 SQLite+文件 / 服务端 KV |
| **duplex controller** | 行为面：耐心窗、语速档收敛、留白/背景回应、打断策略 | 配置 + 策略代码 |

## Status（全部实测，口径见 `docs/09` / `docs/13`）

- **十一个测试根 1,429 条测试全绿**（CI：Python 3.12/3.13/3.14 三档矩阵，跑前先构建 heat-kefu 包让真包断言生效）；`bin/vox` 五命令可用；业务包 3 个（新增业务 = 新增目录，零内核改动）；
- 离线对拍：预铸命中首响 P50 **0.232 ms** vs 同引擎实时合成 586.7 ms；慢路 515.3 ms；
- **真链路命中**：通过适配器接入开源客服框架（不改对方仓库），实弹 4 轮——
  命中 2 轮返回音频与包内资产 **sha256 字节级相同**，未命中 2 轮诚实走原路；
- **多分句整段也能走快路**（T33：拆句铸入 + 逐字覆盖序列命中，`docs/10 §10.7`）：
  14 条多分句预设话术 **14/14** 序列命中、命中臂拼接 **P50 ≈ 5 ms**（冷缓存首跑 ~22 ms）vs 慢路合成
  **≈ 575 ms**、零 TTS 调用（数字以 `labs/heat-kefu-seq/report.json` 为准）；
- 回读 CER 基准可复现（本地 ASR 回读，真机闭环 CER 0.0）；方言域 ASR 边界已实测
  （Wu-Bench 吴语 100 条，CER mean 0.269，见 `labs/wubench-dialect/`）；
- **双工与失效已落地**：四参数各有可观测行为（T19）+ 双工质量三指标可复现（T20，`labs/duplex-quality/`）
  + 资产 TTL 过期拒播（T18）；预铸查找热路径索引化（T27，10k 条下 0.5–0.8 µs，旧 61–2115 µs）。

## Quick Start（纯标准库，Python ≥ 3.12；零第三方依赖）

```sh
git clone https://github.com/starmcainiao/vox-type && cd vox-type

# 1) 全量测试（十一个测试根）
for d in core rules assets adapters compiler runtime eval cli tools trigger packs; do
  python3 -m unittest discover -s $d 2>&1 | tail -1
done

# 2) 铸一个业务包（预铸成功率必须 100%，带病不上线）
bin/vox pack build packs/heat_kefu --out ~/vox-build/heat-kefu --json

# 3) 查包 / 对拍 / 质检
bin/vox pack check packs/heat_kefu --json
bin/vox bench ~/vox-build/heat-kefu --help   # 离线对拍（口径见 docs/08）
bin/vox verify ~/vox-build/heat-kefu         # 对任意产物跑质检
```

可选评测组件（不装也能跑全量测试）：本机模型服务 oMLX（回读 CER 与 ASR 实测用，
`eval/readback.py --asr adapters.asr_omlx:OmlxAsr`）；kefu-agent 集成见
`adapters/framework_kefu/`（环境变量 `KEFU_AGENT_ROOT` 指向其仓根）。

## Documentation

| 主题 | 文档 |
|---|---|
| 问题界定 / 协议草案 / 设计结论 / 同类检索 / 证据计划 | [docs/01](docs/01-problem-and-scope.md) · [02](docs/02-protocols-draft.md) · [03](docs/03-design-notes.md) · [04](docs/04-prior-art.md) · [05](docs/05-evidence-plan.md) |
| 预铸与执行口径 / 剧本四属性 / CLI 行为口径 | `docs/06` ~ `docs/08` |
| 真链路对拍 / 命中口径 / 语料自给实测（23% 边界） | `docs/09` ~ `docs/11` |
| **预铸准入判据**（哪些话允许"固定"；新增 key 前必读） | [`docs/14`](docs/14-预铸准入判据.md) |
| 市场验证 / 业界坐标与改造预测 / 开源就绪评估 | [16](docs/16-行业痛点与切入点分析.md) · [17](docs/17-业界坐标与改造预测.md) · [18](docs/18-开源就绪评估.md) |
| 前置包定位 / 未完成清单（单一事实源） | [docs/12](docs/12-前置包-定位与任务清单.md) · [`docs/13`](docs/13-未完成清单.md) |
| 全部任务卡与逐卡验收记录 / 实验区 | `docs/tasks/` · `labs/` |

## Roadmap

- [x] M1 · 八层机制闭环（compiler → assets → runtime → eval，离线对拍出数）
- [x] M2 · 真链路集成（kefu 钩子 21 行接入 + 实弹命中，字节级验证）
- [x] M2.5 · 话术同源包（`packs/heat_kefu`，yaml 指纹门禁）+ 方言域 ASR 边界实测
- [x] **整段 → plan 拼接匹配**：多分句话术的序列命中（T33：`packs/heat_kefu` 拆句铸入 40→69 key +
  `find_hit_sequence` 逐字覆盖；口径 [`docs/10 §10.7`](docs/10-接入取景与命中口径.md)）
- [x] **双工行为控制器**：四参数（耐心窗/背景回应/打断/语速档）从校验走向消费（T19：`policy_stream`
  事件流带等待窗口/背景回应/可打断标记/关键信息强制 slow；T20：双工质量三指标 harness——打断准确率/
  假阳性/轮转延迟，固定种子可复现）
- [x] **资产 TTL / 失效**（T18：`invalid_at` 绝对失效 + `ttl` 预铸折算 + assets 层保险丝，
  过期拒播且 `allow_fallback` 也不放行——「播报过期事实比不播更糟」）
- [ ] **key 约束解码**：用 Trie/FSM 把 LLM 输出空间物理约束到合法 key 集合——
  **已勘察（`labs/guided-decoding/`）：当前运行时不可行**（`json_schema` 400 拒绝、`guided_json` 被静默忽略），
  需换 vLLM + XGrammar / SGLang / Outlines 类运行时
- [ ] 回流闭环：日志挖掘 → 话术建议（M3）

## Third-party data & attribution

- `packs/fin-cs/` 话术为**改写**（`docs/11 §11.7` 禁止逐字复制语料原文），源语料 MIT / Apache-2.0，
  来源与 sha256 台账：`labs/corpus-harvest/corpus.lock.json`；
- `labs/corpus-harvest/raw/` 含第三方语料**逐字转写**（均为可再分发许可，来源台账即 attribution）；
- 本仓库不含任何用户录音、会话数据或私有部署信息；真人标注（若未来出现）按 `docs/11 §11.7`
  排除出公开分发；
- 环境变量：`CORPUS_ROOT`（语料根，缺省 `~/corpus`）、`KEFU_AGENT_ROOT`（kefu 仓根）、
  `KEFU_PRECAST_*`（预铸钩子开关）。

## Contributing

设计讨论期：欢迎 issue 质疑协议与边界。提 PR 前请读
[CONTRIBUTING.md](CONTRIBUTING.md)（测试跑法 / 纪律要点：fail-closed、数字必须有出处、
包是数据不是代码）。安全漏洞走 [SECURITY.md](SECURITY.md)，勿用公开 issue。

## License

[Apache-2.0](LICENSE)。与仓内 MIT / Apache-2.0 派生语料兼容；
`packs/*/golden/` 的真人标注资产（若未来出现）不进公开分发。
