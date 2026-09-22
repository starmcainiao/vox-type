# e2e-vs-cascade · 组合版（级联）vs 端到端 S2S 三维对照

> 立项差异化论证的最后一格：`docs/13 §六#5` / `docs/19 §二 M3`（T24）。
> 目的：把「组合版 vs 端到端版」的差异化论证从**主张**升级为**本仓自测数字**——延迟 / 可控性 / 可审核性三条。

## 两臂

| | 臂 A · 组合版（级联） | 臂 B · 端到端 S2S |
|---|---|---|
| 实现 | ASR（本机 oMLX `Qwen3-ASR-0.6B-8bit`）→ 文本 → **预铸命中**（`find_hit`）→ 包内音频；未命中 → 云端 LLM（`qwen-turbo`）+ TTS（`qwen-tts`） | `qwen-omni-turbo`，`stream=true`，SSE 手写解析：**音频进 → 音频出** |
| 复用 | `adapters/asr_omlx`、`adapters/framework_kefu.find_hit`、`assets.load_pack`；LLM/TTS 臂**从 `labs/cloud-bench/run_cloud_bench.py` 以文件路径导入** | 无既有实现（本机 oMLX 无 S2S 模型，条件探测见 T24 卡） |
| 样本数 | 命中支路 N=10、未命中支路 N=10 | N=10 |

## 结果（2026-09-22 实测，原始数据 `raw/*.jsonl`）

### 1. 延迟

| 测量 | P50 | mean | n |
|---|---|---|---|
| **臂 A · 预铸命中首字节音频** | **0.1 ms** | 0.8 ms | 10 |
| 臂 A · ASR 段（独立，不叠加） | 215 ms | 218 ms | 10 |
| 臂 A · 未命中支路 整链（LLM+TTS） | 4256 ms | 3858 ms | 10 |
| ├ LLM TTFT | 276 ms | 274 ms | 10 |
| └ TTS 合成（非流式上界） | 3757 ms | 3414 ms | 10 |
| **臂 B · 首字节音频** | **717 ms** | 721 ms | 10 |
| 臂 B · 首字节文本（附带产出） | 352 ms | 400 ms | 10 |
| 臂 B · 总时长（至 `[DONE]`） | 1681 ms | 1692 ms | 10 |
| 臂 B · 回转写（oMLX，仅测可控性用） | 345 ms | 350 ms | 10 |

口径：两臂均含完整网络往返，本机直连，单账号单进程，含 429 退避（等待不计入延迟）。
**未直接相减对比**——臂 A 命中路径零网络、臂 B 含完整往返，口径不可通约（见 `latency_compare.caveat`）。
交叉对照：`labs/cloud-bench/report.json` 云端未命中链 P50 ≈ 2.87 s、`qwen-turbo` TTFT P50 226 ms。

### 2. 可控性（同一固定话术 N 次 → 逐字一致率）

| | 臂 A 命中支路 | 臂 A 未命中支路 | 臂 B |
|---|---|---|---|
| exact_match_rate | **1.0**（n=10） | **0.0**（n=10） | **1.0**（n=10） |
| 比对对象 | 命中条目原文 | LLM 自由生成文本 | 输出音频经 oMLX 回转写 |

- 逐字比对统一走 `eval.cer.normalize_for_cer`（去全部标点与空白，两臂同源）；
  臂 A 的命中判定本身另走 `adapters/framework_kefu.normalize_text`（产品同源归一化）。
- **臂 A 的可控性是「分支决定」的**：命中 → 逐字 100%（命中即固定条目原文）；未命中走 LLM → 逐字 0%
  （n=10 两两全不同）。0.0 同时证明**该口径确实能测出差异**，不是恒等 1.0 的空转指标。
- **臂 B 报 1.0 是「未复现出差异」，不是「保证逐字」**：N=10 的回转写全等，但回转写误差会把真实差异掩盖掉
  ——该比率是**下界证据**（实测到的差异是真差异，未实测到的不排除存在）。
  客观补充观测：N=10 的输出音频字节数出现 **8 种不同取值**（min 243840 / max 268800 B），
  输出音频长度并不逐次相同；但字节数不等于内容，逐字结论仍以回转写为准（见 `raw_audio_bytes_variability_note`）。

### 3. 可审核性（定性，不做主观评分）

- **臂 A 可审计**：命中判定走 `find_hit`（文本归一化逐字相等，无模型判据）；资产带 16 位指纹 + 音频存在性校验
  （`assets` 层保险丝）；四属性校验与准入判据在包级；事件留痕可复核。同一输入必得同一音频。
- **臂 B 黑盒**：单次调用即返回音频，无准入判据、无逐字保证、无资产指纹、无事件留痕；同一输入的输出不保证逐字相同。

## 臂 B 音频非空校验（反空转条款的硬判据）

响应 200 **不算过**。每次运行逐次校验四项，全部通过才计入 `audio_valid_runs`：

1. 字节数 > 0；2. 字节数 ≥ 16000；3. 偶数字节（可按 int16 成对读样本）；4. 非零样本占比 ≥ 0.001 且峰值 > 0。

本次 10/10 通过（字节数 P50 259200 B）。SSE 音频块在 `delta.audio.data`（base64），文本在 `delta.content`，
两者分属不同 chunk——解析时按 key 分流，未把文本块误当音频。

**音频格式说明**：厂商响应未声明格式（chunk 仅含 `data` / `expires_at` / `id`）。按 24000Hz / 1ch / int16
假设做「字节数 → 时长」换算，**仅为记账**（`audio_format.declared_by_vendor = false`），不对外引用。

## 边界（如实记录的部分）

- **臂 A 未命中是预期结果**：真实用户轮次（"我要查一下账单"等）经 say→oMLX 转写后走 `find_hit` 文本档
  → 10/10 miss（`text_not_prebaked`）。命中上限由上游表示形态决定（`docs/10` 裁定 2），不是本卡的缺陷。
  故命中支路与未命中支路分设两路采样、分开报数。
- **臂 B 未复现出逐字差异**（见上），未用它否定"端到端不保证逐字"——该点由臂 A 未命中支路的 0.0
  与臂 B 字节数变异共同支撑，臂 B 的 1.0 按 `arm_b_caveat` 口径如实标注为下界。
- 单机单账号、含限流节流；无并发排队；单模型单档（`qwen-omni-turbo`）。
- 未测多语种、未测多轮上下文、未测打断（双工行为在 `eval/duplex_quality.py`，与本卡正交）。

## 复现

```sh
# 凭据：labs/cloud-bench/.env.local（gitignored），内容一行 DASHSCOPE_API_KEY=...
#   也可由环境变量 DASHSCOPE_API_KEY 覆盖（优先）
# packs 前置：需要 heat_kefu 同源 yaml（不设则 packs 测试 skip，但本脚本的包构建需该文件存在）
export KEFU_HEAT_YAML=/path/to/供热预设.yaml

python3 labs/e2e-vs-cascade/run_e2e_vs_cascade.py 10   # 参数 = 每臂采样数 N（必须 ≥ 2）
# 产物：report.json + raw/armA_cascade.jsonl + raw/armB_e2e.jsonl + raw/armA_miss_fallback.jsonl
```

依赖：Python 标准库（urllib / wave / struct / json / statistics），**无第三方依赖**，SSE 手写解析。
失败 fail-closed：无凭据 → 退出 2；oMLX/say 前置探针不通 → 退出 3，不产出半个报告。
包现场由脚本 `bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1` 铸造。

**产物卫生**：Key 只从 `.env.local` / 环境变量读，不回显、不入报告（脚本内对凭据做掩码扫描自证）。
仓库内不含 `sk-` 形态密钥、不含绝对路径、不含真实会话录音或转写（话术全部为公开 demo 文案）。
