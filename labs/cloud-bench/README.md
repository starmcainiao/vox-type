# cloud-bench · 云端语音链路（阿里百炼）延迟分解 vs 预铸命中

> 目的：把 `docs/17` 缓存分层论证中「云端优化档」的数字从**引用业界数据**升级为**本仓自测**。
> 三臂对照（**命中路径与引擎无关**的跨厂商实证：本地 oMLX/say 之外的第二家云厂商）。

## 口径（读数字前必看）

| 臂 | 内容 | 口径与边界 |
|---|---|---|
| A · LLM | `qwen-turbo`（stream，kefu persona）| TTFT = 首个 delta chunk；total = 至流结束。10 轮用户话术（公开 demo 文案） |
| B · TTS | `qwen-tts`（`/api/v1/services/aigc/multimodal-generation/generation`，非流式 REST，voice=Cherry） | total = gen（服务端合成响应）+ download（OSS 音频拉取）。**非流式 = 上界**——生产流式 WS 首字节更早（业界口径 75–300ms），整句完成仍需合成时间。调用间 1.2s 节流，429 退避重试（等待不计入延迟）；B1=生成回复 10 条，B2=heat_kefu 预铸句 38/40 条（2 条 429 耗尽） |
| C · 预铸命中 | `find_hit` 读包（T29 API，本地进程内） | 40 条查询含索引缓存；「与引擎无关」= 命中路径没有 LLM/TTS 调用 |

**未命中轮生产口径 ≈ A.total + B.total**；命中轮 = C。相加为**保守上界**（分布之和的 P50 ≤ 各自 P50 之和）。

## 结果（2026-09-21 实测，原始数据 `raw/*.jsonl`）

| 测量 | P50 | mean | n |
|---|---|---|---|
| **A · LLM TTFT**（qwen-turbo 云端） | **226 ms** | 220 ms | 10 |
| A · LLM 总时长 | **419 ms** | 391 ms | 10 |
| **B1 · TTS 合成生成回复** | **3253 ms** | 3176 ms | 10 |
| **B2 · TTS 合成预铸句** | **2454 ms** | 2759 ms | 38 |
| **C · 预铸命中查询** | **0.1 ms** | 0.1 ms | 40（40/40 命中） |

**对照**：云端未命中链 P50 ≈ **2.87 s** vs 命中 **0.1 ms** ≈ **28700×**。

## 这三组数字各说明了什么

1. **云端 LLM 并不慢**：TTFT P50 226 ms——恰好落在业界「优化栈 ~250ms 首 token」区间（`docs/16` 引用值），**本仓自测证实了行业数据**；
2. **云端 TTS 的秒级延迟是真实的**：即便全云栈、即便 qwen-tts，非流式合成仍是 **2.4–3.2 s**（gen 服务端合成 2.3–3.0s + OSS 下载 ~90ms）——比本地 say（586.7ms）慢，因为含云端合成队列；流式可把**首字节**压到百毫秒级，但整句完成时间仍在——**预铸把这一整段（含合成与下载）归零**；
3. **命中路径与引擎无关得到跨厂商实证**：同一套 `find_hit` + 预铸包，在本地（oMLX/say）与云端（百炼）链路里的命中行为、查询耗时一致（进程内 0.1ms）——这正是「覆盖层」定位的验证。

## 复现

```sh
# 凭据：labs/cloud-bench/.env.local（gitignored）内容一行：
#   DASHSCOPE_API_KEY=sk-...
python3 labs/cloud-bench/run_cloud_bench.py 10     # 参数 = LLM 轮数
# 产物：report.json + raw/armA_llm.jsonl + raw/armB_tts.jsonl + raw/armC_precast.jsonl
```

依赖：Python 标准库（urllib）；heat_kefu 包由脚本现场 `bin/vox pack build` 铸到临时目录。
失败 fail-closed：无凭据 / 端点不通 → 非零退出，不产出半个报告。凭据内容不进任何产物。

## 边界（不吹的部分）

- TTS 为**非流式 REST**上界口径；流式 WS 的首字节数字未测（留口子：需要 WS 实现，收益有限）；
- 单机单账号、含 429 限流节流（等待已从延迟中剔除）；高并发排队未测（本机盲区，`docs/18` 已列）；
- ASR 段未纳入（生产为流式，形态不同）；LLM 为 qwen-turbo 一档。
