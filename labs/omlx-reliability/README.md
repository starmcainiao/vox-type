# labs/omlx-reliability · oMLX 端点可靠性基线（T69）

> 本目录**只取证，不修产品代码**。`compiler/` `runtime/` `core/` `adapters/` 一行不改。

## 一、为什么有这个探针

Quick Start 实跑 4 次里 3 次失败，失败形态是某句音频**约 327 秒**，被 `compiler/quality.py`
的 `MAX_DURATION_MS = 30000` 判 rc=4。写卡前人工直接探过端点 6/6 次都正常（音频 1.4–8.0 s），
复现不出来；也排除了两条路径（`compiler/prebake.py` 串行无并发放大；请求体不带 `ref_audio`，
不是参考条件化慢路）。

**现有证据不足以指认根因**，所以本探针只负责把「猜」变成「不猜」：
固定语料 + 固定轮次，把尾部（max / P99）摆出来。

## 二、怎么跑

```bash
~/miniforge3/bin/python3 labs/omlx-reliability/probe.py            # 默认 3 轮 × 40 句
~/miniforge3/bin/python3 labs/omlx-reliability/probe.py --rounds 1 --sentences 5
```

零第三方依赖（只用 python 标准库）。**不许 pip install**。
产物：本目录 `report.json`（含每条请求的明细 `results[]`）。

可调参数：`--rounds` `--sentences` `--retries`（默认 0）`--corpus` `--url`
`--model` `--timeout`（单请求硬上限，默认 120 s）`--slow-mark`（尾部观测带，默认 20 s）
`--out` `--keep-tmp`。

## 三、口径

| 项 | 值 / 来源 |
|---|---|
| 端点 | `http://127.0.0.1:10099/v1/audio/speech`（`adapters/tts_omlx/adapter.py` 的 `BASE_URL` + `transport.SPEECH_ENDPOINT`，**不是猜的**） |
| model | `Qwen3-TTS-12Hz-0.6B-Base-bf16`（`adapter.DEFAULT_MODEL`） |
| voice / speed | `default` / `1.0`（`rate_map["normal"]`），与 `postprocess.build_payload` 默认音色分支逐字段一致 |
| **请求是否带 `ref_audio`** | **不带**。`request_carries_ref_audio: false`，请求体只有 `model/input/response_format/speed/voice` |
| 语料来源 | `packs/heat_kefu/phrases.json`（仓内已有话术，未引入新语料）；原顺序取前 40 句、每句 `variants[0]` |
| 样本量 | 3 轮 × 40 句 = **120 次请求**（计划） |
| 轮次结构 | 串行（`concurrency: 1`），轮与轮之间**不 sleep**，与 `compiler/prebake.py` 的串行预铸一致 |
| 固定种子 | `fixed-corpus-no-randomness`——本探针不掷骰子，可复现靠的是语料与轮次固定 |
| 并发/排队控制 | 无；探针测的是「连续单请求、无客户端排队」的端点行为 |
| 重试 | 默认 0 次（端点可靠性测试要看真实失败率）。`--retries N` 才按 2^k 退避补试，每次尝试单独计时，`error` 字段带全部尝试的完整文本 |
| 统计口径 | 近秩百分位 `p(N,q)=s[ceil(q/100·N)-1]`；每个指标都给 `count/min/p50/p99/max/mean` |
| 尾部观测带 | `slow_mark_s = 20`：墙钟超过它就在 `slow_outliers[]` 单列（**不是**拒绝条件） |
| quality 判据 | 抄 `compiler/quality.py`：`MAX_DURATION_MS=30000`、`MAX_HEAD_SILENCE_MS=100.0`、`MAX_TAIL_SILENCE_MS=150.0`；静音判据 `|s| ≤ 328` |
| 音频时长来源 | 直接从响应 WAV 头读 `nframes/rate`（24 kHz / 单声道 / 16-bit），**不走 ffmpeg**，也不经过 `postprocess.normalize_from` 的静音裁切 |
| 硬超时 | 单请求 120 s 上限，超时打 `timeout: true` 并继续（AC2）。注：`transport` 的默认 `timeout=600`，探针**显式下调**——用 600 等于没有上限，会把整轮挂死 |
| 临时文件 | `tempfile.mkdtemp(prefix="vox-t69-")`（落在**系统临时目录**；macOS 上是 per-user 目录而非字面 `tmp` 根目录，Linux 上又不一样——**别把路径写死进产物**）。逐轮清空，结束整目录 `rmtree`。`--keep-tmp` 可保留。逐条结果里只存**文件名**（`tmp_wav`），不存路径 |

## 四、AC1：为什么必须看 max 和 P99

`report.json` 的 `wall_clock_s` 与 `audio_duration_ms` 两个块都带 `max` 与 `p99`。
327 秒这种尾部事件在均值里是看不见的——120 条里 1 条 327 s，均值只抬高 2.7 s
（实测 `mean=5.70` vs `max=327.0`，同批数据）。**这正是本卡存在的理由**。

三条腿，缺一不可：

1. `max` —— 单条最坏情况，尾部事件唯一躲不掉的地方。
2. `p99` —— **注意其样本量限制**：近秩百分位 `p(N,99)` 在 N=120 时只取第 2 大
   （下标 `ceil(1.188)-1 = 1`），所以**一条**离群值不会推高 P99，它会留在 `max`
   而 P99 保持平稳。P99 的作用是看「连续 99% 的请求」的水平，不是抓单条离群。
3. `counts.audio_over_30s` 计数 + `quality_rejections[]` 明细 —— 单条离群也能被数出来。

门槛判定与 `compiler/quality.py` 逐条对齐且为**严格大于**：
`duration=30000` 通过、`30001` 拒绝；`head_silence=100` 通过、`101` 拒绝；
`tail_silence=150` 通过、`151` 拒绝（已实测四个边界点）。

## 五、AC2 / AC3：不许挂死、不许假绿

- **AC2 超时自杀**：`urlopen(..., timeout=120)`，超时抛 `socket.timeout` → 记 `timeout: true`、
  `status: "failed"` 并**继续下一条**，整轮不会挂死。
- **AC3 断言 + 非零退出码**：跑批结束断言
  `executed == planned` 且 `失败数 == 0` 且 `成功数 > 0`；
  任何一条不满足 → stderr 打 `[probe] ASSERT FAILED: ...` 并 `exit 3`。
  断言失败**之前**先把报告落盘，不留空报告。

退出码（**labs 自持约定，与 `bin/vox` CLI 的冻结退出码无关**）：

| 码 | 含义 |
|---|---|
| 0 | 全部请求成功且 quality 零拒绝 |
| 2 | 至少一条 quality 拒绝（`duration_over_30s` / `head_silence_over` / `tail_silence_over`）——这是本卡的**目标信号** |
| 3 | 跑批断言失败（有失败请求、或成功数为 0、或没跑满计划数） |

## 六、AC4 / AC5：两种结果怎么写

- **跑出了** `> 30 s` 样本（`audio_over_30s > 0`）：该条的触发句、完整请求体、响应头、
  音频字节会原样落 `captured/r<轮>-<序号>-<key>.{wav,json}` 并进 `report.json` 的
  `quality_rejections[]` 与 `captured[]`。用 `--keep-tmp` 跑批才能留下盘文件；
  否则报告里的 `captured[]` 列表仍带完整元数据与字节数（`wav` 字段为 `null`，如实标注）。
- **跑不出**：`report.json` 里 `audio_over_30s: 0`、`failure_rate: 0/<executed>`。
  报告**不写**「已修复」，也**不写**「无法复现即不存在」——那正是卡 §四 AC5 明令禁止的。
  结论口径由维护者按卡 §五的判据表下（本探针不给判定）。

## 七、我做的取舍（卡里没写，实现时必须选，逐条标明）

1. **不 import 任何产品模块。** 卡要求请求走 oMLX 的 `/v1/audio/speech`，但
   `adapters/tts_omlx/transport.py` 自带 5 次重试 + 600 s 超时 + ffmpeg 归一化，
   会把尾部**掩盖**而不是暴露。所以探针自己用标准库 POST，把常量
   （`BASE_URL` / `DEFAULT_MODEL` / `rate_map` / quality 阈值）从产品代码里抄过来并在注释里标了行号出处。
   代价：探针与产品代码的请求体一致性靠人工对齐，不靠 import 保证。
2. **quality 门只判「时长 + 首尾静音」，不判「全静音」单独条目。**
   全静音文件的 `head_silence_ms` 必然等于整条时长，会被 `head_silence_over` 天然拦住；
   `audio_all_silent` 另列为计数，方便区分「完全无声」和「有声但开头拖长」。
   「完全无声」同样是可取证信号——它意味着端点吐了一条不含语音的音频，
   和 327 秒一样属于 AC4 该留证的形态，所以它被留盘进 `captured/`。
3. **静音判据按 quality.py 的 `SILENCE_THRESHOLD=328`，只在单声道 16-bit 下成立。**
   实测响应就是 24 kHz/单声道/16-bit，`audio_stats` 遇到多声道/其它位深会返回
   `ok: false` 并在 `decode_error` 里写明原因，**不猜**。
4. **`truncated_analysis` 是「没分析完」，不是「后面没声」。** PCM 分析只读前 16 MB
   （24 kHz/16-bit ≈ 337 s，够覆盖 327 s 的异常样本）；超限时置 `truncated: true`，
   时长本身仍从 WAV 头完整读出。
5. **`head_silence_ms` 的语义边界**：全部为静音时它等于整条时长（例如实测一条
   3440 ms 完全无声的响应，`head_silence_ms = 3440`）。这是有意为之——
   让「全静音」在数据里显形，而不是被当成 0。
6. **失败不重试是默认值。** 端点可靠性测试要的是真实失败率；`--retries` 留给
   想对齐生产路径（`transport` 的有界重试）时的复现对照。
7. **退出码 2 代表「出现了 quality 拒绝」而非「跑挂了」。** 0 只留给「全绿」。
   这样 CI 里看到非零码就知道有可取证信号，不会当成「无输出」。

## 八、跑出来的事（第一次基线，2026-09-28）

命令：`probe.py --rounds 3 --sentences 40 --out labs/omlx-reliability/report.json`，
总墙钟 605.35 s（约 10 分钟）。

| 指标 | min | P50 | P99 | **max** | mean |
|---|---|---|---|---|---|
| 请求墙钟 s | 1.1127 | 4.5503 | 12.8934 | **13.9941** | 5.0424 |
| 返回音频 ms | 1280 | 5200 | 13680 | **14480** | 6011.3 |

- 执行 **120/120**（计划 120），失败 0，超时 0，`audio_over_30s = 0`，
  `quality_rejected = 0`，`audio_all_silent = 0`，`slow_outliers = []`。
- 即：**120 次未复现 327 秒级样本，失败率 0/120**。报告按 AC5 如实写这个数，
  **不写**「已修复」，也**不写**「无法复现即不存在」——判据归维护者按卡 §五的表下。
- 墙钟 max 13.99 s 离 30 s 阈值有 2 倍余量；音频 max 14.48 s，离 30 s 有近 2 倍余量。
- 附带观测（卡里没要求，如实记录）：端点对该句集的响应**没有** `Content-Type`
  与 `Content-Length` 头（`results[].content_type` / `content_length` 全为 `null`），
  所以音频时长只能从 WAV 头读，不能从响应头核对。

## 九、不做什么

- 不改 `compiler/quality.py`、`adapters/tts_omlx/transport.py`（卡 §五：AC5 出来之前不要动时长逻辑）。
- 不引入新语料、不写任何仓外/公司数据。
- 不做达标判定：本目录只出数据，结论归验收方。

