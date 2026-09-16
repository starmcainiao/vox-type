# 05 · 数据集与验证方案

> 原则：**开源前先有数据**。每条指标必须可复现（脚本入库 + 公开语料 + 固定话术集）。
> 通道说明：下列数据集均经 ModelScope 可达性核实（本机网络下 huggingface.co 不可达，
> HF 侧链接留待补验）。

## 5.1 五条指标

| 指标 | 方法 | 对标 |
|---|---|---|
| 首响延迟 P50/P99 | harness 采集（快路 vs 慢路对照） | 业界 sub-400ms 目标 |
| 预铸命中率 / 预铸时长占比 | 公开 demo 话术集（表单流程，中英双语）+ 命中日志 | 本项目特有口径 |
| 单位会话成本 | token/算力计量（快路应趋近 0） | 慢路 token 节省量 |
| 拼接自然度 | 回读 CER（Whisper）+ 盲测 A/B（三产线） | Seed-TTS-eval 口径 + MOS |
| 双工质量 | 打断准确率/假阳性/轮转延迟 | Full-Duplex Bench / eot-bench / TurnBench |

## 5.2 数据集清单（用途 → 数据集）

| 用途 | 数据集 | 规模 | 许可 | 通道 |
|---|---|---|---|---|
| ASR 鲁棒（噪声/多场景） | WenetSpeech | 10000h+ | Apache-2.0 | ModelScope: `wenet/WenetSpeech` |
| ASR 标准基线 | AISHELL-1 | 178h | Apache-2.0 | ModelScope: `speech_asr/speech_asr_aishell1_*` |
| **方言** | KeSpeech | 多方言（8 区） | Apache-2.0 | ModelScope: `pengzhendong/KeSpeech` |
| 方言补充 | 河南方言数据集 | 500h | Apache-2.0 | ModelScope: `lukeewin01/HeNan-Dialect-Datasets` |
| 口音多样性 | Common Voice（zh/en） | 众包 | CC0（原始） | ModelScope 镜像 |
| 多语种 | FLEURS | 102 语种 | CC-BY-4.0 | ModelScope: `google/fleurs` |
| 英文基线 | GigaSpeech2 | 万小时级 | Apache-2.0 | ModelScope: `AI-ModelScope/gigaspeech2` |
| 多说话人 TTS | AISHELL-3 | — | Apache-2.0 | ModelScope: `OmniData/AISHELL-3` |
| TTS 评测（WER+相似度） | Seed-TTS-eval（zh/en/hard） | — | Apache-2.0 | ModelScope: `CowboyZ/seed-tts-eval` |
| MOS 语料 | VoiceMOS 2022 / BVCC | — | Apache-2.0 | ModelScope: `sarulab-speech/bvcc-voicemos2022` |
| MOS 预测器（自动打分） | UTMOS | — | 见项目 | GitHub `sarulab-speech/UTMOS22`（HF 侧未核） |
| 多语 TTS 语料（远期） | Emilia / Emilia-Large | 大规模 | Apache-2.0 | ModelScope: `modelscope/Emilia-Dataset` |
| **对话式语音**（双工采样） | MagicData-RAMC | 180h | **CC-BY-NC-ND（非商用）** | ModelScope: `MagicData/A_CHINESE_CONVERSATIONAL_SPEECH_CORPUS` |

## 5.3 缺口即贡献：拼接自然度评测协议

ASR 有 WER、TTS 有 MOS/相似度、双工有轮转延迟——**但「预铸拼接音频的自然度」没有标准基准**。

拟贡献的首个公开资产：

1. **三产线对照**：① 全预铸拼接 ② 逐句实时合成 ③ 端到端模型输出；
2. **评估维度**：接缝可察觉性（人耳 A/B/X）、整段自然度 MOS、内容可懂度（回读 CER）、
   跨片段音色一致性（说话人相似度）；
3. **可复现**：公开话术集（表单流程 demo，中英双语，无私人数据）+ 生成脚本 + 盲测工具；
4. **不绑模型**：任何 TTS/框架都可接入评测。

## 5.4 待定

- 演示话术集的具体内容（建议：报修登记 / 预约回访两条表单流程，各 30–50 状态）
- 盲测工具形态（本地小工具 or 静态网页）
- 对拍基线是否包含本机可跑的端到端小模型（如 MiniCPM-o 单工档）
