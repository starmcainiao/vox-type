# 04 · 同类项目检索与缺口论证

> 检索日：2026-09-16 · 通道：GitHub API / gh CLI（repos + code search）/ ModelScope API。
> **未覆盖**：WebSearch（当时不可用）、huggingface.co（网络不可达）、Gitee、arXiv 附带 repo。
> **结论是「未发现」而不是「不存在」**——正式开源前应补一轮检索。

## 4.1 语音 Agent 框架（主流，均无「预铸资产层」）

| 项目 | ★ | 现状 | 与本项目关系 |
|---|---|---|---|
| pipecat-ai/pipecat | 15.6k | 活跃 | 管线框架；有 **evals 用的 synthesized-audio cache**（`src/pipecat/evals/`，CLI 有「user-audio cache」开关）——**服务于评测复现，不是生产预铸资产层** |
| livekit/agents | 14.2k | 活跃 | 管线框架 + 官方 turn-detection 模型；无按 key 取预铸资产的机制 |
| RasaHQ/rasa | 21.3k | 维护中 | **形态学祖先**：forms/槽填充 = 表单流程状态机；但音频每句现合成，无资产层 |
| bolna-ai/bolna | 763 | 活跃 | 有会话/LLM 侧 cache 抽象；无音频预铸 |
| vocodedev/vocode-core | 3.8k | **2024-11 后停滞** | — |
| jambonz（开源 CPaaS） | — | 活跃 | **IVR 传统**：prompt 音频即资产（Asterisk/FreeSWITCH 音频包路线），无模型命中与回流 |

## 4.2 缓存类（文本侧，机制邻居）

- **zilliztech/GPTCache**（8.2k）— LLM 语义缓存：文本应答缓存，无音频/无双工/无预铸流水线。
- **aurelio-labs/semantic-router**（3.9k）— 语义路由：「选 key」半边的现成工具，可作为路由层零件。
- **elizaOS/eliza → plugin-local-inference** — 有 `corpus-generator.ts`「pre-rendered audio supplied through the corpus on disk」，语料级预渲染，用于本地模型语料，非话术资产层。

## 4.3 全双工 / 打断（交互行为层）

- **nabaruns/backchannel**（0★，2026-09 新建）— 全双工框架，一等公民的 barge-in + turn detection（Apple Silicon）→ 与双工控制器重叠，值得跟踪与对拍。
- **moabs-dev/RT-Voice-Agent**（1★）— scripted dialogue engine + STT/LLM/TTS → 与状态机层重叠，但无资产层。
- **NVIDIA voice-agent-examples** — 推测式语音处理工程指南（预取思想同源，非资产层实现）。

## 4.4 学术侧的近亲（思想已验证）

- **Amazon, Interspeech 2023**（arXiv:2305.13794）— 用部分语音预测完整话语 → **预取 LLM 应答并缓存**，说完命中即播。
- **Vapi 官方博客（2025-05）**— Audio Caching for Latency Reduction：短语级音频缓存 + 语义命中；公开案例 2.5s→0.8s / 1.8s→0.5s（厂商口径）；踩坑=缓存了过期内容 → TTL/事件刷新。

## 4.5 缺口陈述

**未发现**任何开源项目实现以下组合：

> plan 键值寻址的预铸音频资产层（key → 音频 + 版本 + TTL + 变体池 + 文本指纹）
> ＋ 双工参数控制器（耐心窗/语速档/留白/打断策略）
> ＋ 命中率回流闭环（日志挖掘 → 预铸建议）

已存在的是各自单边：框架有管线、缓存是文本侧、IVR 有预录音传统但无模型化、
全双工有 barge-in 但无资产层。**这个组合是本项目的定位。**
