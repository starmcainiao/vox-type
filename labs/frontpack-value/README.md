# frontpack-value · 前置包价值对比（快路 vs 慢路，本仓自测可复跑）

任务卡：[`docs/tasks/T43-labs-前置包价值对比与呈现素材.md`](../../docs/tasks/T43-labs-前置包价值对比与呈现素材.md)
依据：[`docs/24-接入形态与评判.md`](../../docs/24-接入形态与评判.md) §一（零第三方依赖、毫秒首音只在链路内兑现）

把「前置包解决了什么问题」从主张变成**同一台机器、同一批话术、可复跑**的对照。
`labs/` 是实验区，不进任何层的契约；本目录自带口径与原始数据。

## 一眼看懂

见 [`docs/media/value-compare.svg`](../../docs/media/value-compare.svg) —— 对数刻度横条：

| 臂 | 路径 | 首音（本次实测 P50） |
|---|---|---|
| **快路** | `vox run` 命中即播（磁盘读，零 TTS 调用） | **0.715 ms**（区间 0.475–4.176 ms，N=50） |
| **慢路** | oMLX Qwen3-TTS-12Hz-0.6B 全链路 | **2336.0 ms**（区间 1052.3–6453.6 ms，N=10） |

> 数字唯一来源是 [`report.json`](report.json)。上面这张表、`docs/media/value-compare.svg`、
> 以及本文件其余处出现的任何毫秒数，都从 `report.json` 抄自同一个文件——**改数字只改 `report.json`**。

## 复跑

```sh
python3 labs/frontpack-value/run_compare.py        # 端点在线时 rc=0
```

前置条件：

1. TTS 端点在线，缺省 `http://127.0.0.1:10099`（`/v1/audio/speech`，OpenAI 兼容协议），
   模型 `Qwen3-TTS-12Hz-0.6B-Base-bf16`。覆盖方式：`VOX_TTS_ENDPOINT` / `--endpoint`。
2. `ffmpeg` 在 PATH 上（适配器用它把 24 kHz 降到契约的 16 kHz）。
3. 语料来自 `packs/heat_kefu/phrases.json`（**程序化读取**，不手抄）。

选项：

```sh
python3 labs/frontpack-value/run_compare.py --no-write     # 结构自检：不连端点、不落盘
python3 labs/frontpack-value/run_compare.py --repeats 10   # 快路每句重复次数（缺省 5）
python3 labs/frontpack-value/run_compare.py --skip-slow    # 端点不在线时只跑快路
```

退出码：`0` 成功 / `2` 语料或参数错 / `3` 运行期失败（**任一句失败即 3**，见下）。

**一次完整跑约 10–14 分钟**：先铸一份 69 句的 heat_kefu 包（~9 分钟，这是大头），
再量快路 50 次子进程（~40 s）与慢路 10 句（~45 s）。

## 两条臂怎么量（口径）

### 快路：`vox run` 子进程

每句造一份单句 plan，跑 `sh bin/vox run <plan> --pack <包> --json`（**子进程**），
取 JSON 输出里的 `first_audio_ms`。

- 这是**产品口径的首音**（`core.metrics_spec` 常量，CLI 不改名不加工）：命中即读磁盘资产 + 拼接。
- **不含 Python 进程启动那几十毫秒**——它测的是「引擎就绪后首音到得多快」，
  不是「从零拉起进程到首音」。这条差别说清很重要：`vox run` 本身是几十 ms 起步的。
- 每句重复 5 次取中位数（子进程调度抖动会进这个数，所以必须重复）。
- 断言 `hit_count=1 / miss_count=0 / tts_calls=0 / rc=0`——任一条不满足就整条 harness 失败。
  `tts_calls_total=0` 是快路的**定义性特征**：一次合成都没发生。
- 另外整跑一次 `examples/plan.json`（4 句）记交叉值，落在 `machine.examples_plan_cross`。

### 慢路：`OmlxTts` 现场合成

`from adapters.tts_omlx import OmlxTts`（default 音色、非克隆，端点/模型见上），
**先 1 次热身不计统计**，再逐句墙钟计时。

- 计时是**全链路**：服务端合成 + ffmpeg 降采样归一到 16k + 落盘 + 契约复验。
  这不是"只有模型推理"的口径，而是真实未命中轮要付的全部代价。
- 逐句 1 次（采样型引擎，逐句重复会让总时长线性膨胀）。
- `rtf_median` = 墙钟秒 / 产物音频秒 的中位数。**≈1 即恰好实时，>1 表示比实时慢**。
  本次 `rtf_median=0.941`；三次独立复跑的 `rtf_median` 分别是 **0.967 / 1.053 / 0.941**——
  也就是说慢路**在实时线上抖动**：本次 10 句里有 4 句 `rtf>1`（`chat_smalltalk__2` 1.0512、
  `pay_no_bills` 1.0532、`work_order_empty` 1.1996、`repair_ask_userNo` 2.573），另一次是 6 句。
  逐句值在 `raw/slow.jsonl`。

### 为什么这是公平对比

两臂同机、同句、同 `16 kHz / 单声道 / 16-bit` 契约，慢路**含完整后处理**，
没有偷偷只量「模型吐完第一个 token」。唯一口径差异已写明：快路不含进程启动，
慢路含网络往返（本机回环）。

## 怎么读这个数字

**倍数只能作附注。** 本次 P50/P50 ≈ **3,267×**，但这是单个中位数对单个中位数。
保守端更稳的说法（也是 SVG 脚注里写的那条）：

- 快路取**上界** 4.176 ms 对慢路取**最小** 1.0523 s → 约 **252×**
- 快路取中位 0.715 ms 对慢路取**上界** 6.4536 s → 约 **9,026×**

结论区间：**约 250× – 9,000×**。
快路的中位分布很窄（十句的 `per_sentence_ms` 全在 0.518–0.784 ms），
但 50 次采样里混进了**单次离群**——`opening__1` 第 3 次重复测到 4.176 ms，
把全臂上界从 0.7 ms 量级抬到了 4 ms 量级（倍差 8.8×）；子进程调度抖动进这个数，
所以**上界只能当噪声看，不能当典型值**。
慢路很宽（1.0523–6.4536 s，倍差 6.1×）——散度来自采样型引擎：本次上界是
`repair_ask_userNo` 单句 6.45 s，而它 `audio_s` 只有 2.51 s、`rtf=2.573`，
**属端点抖动而非句长**；最长的 `opening__2`（42 字）6.13 s、`audio_s=7.07 s` 排第二。
上界由哪一句贡献在复跑间会换人，这本身就是「散度来自采样型引擎」的表现。

倍数会随机器、模型负载、端点并发漂移，**所以 report.json 里不存任何「×N」字段**，
只在 `notes` 与本节作附注，给区间不给单点。

## 局限（如实列，别当结论外推）

1. **本机单账号单机**：一台 arm64 Mac（Mach-O，10 核），一个 oMLX 进程，无并发竞争。
   换机器、换模型规模、加并发，绝对值全变——**倍数区间也会变**，不要跨机器引用。
2. **同句对照，不代表生产分布**：语料是 10 句固定的 heat_kefu 短句（共 147 字，最长 42 字、最短 7 字）。
   慢路耗时随句长近似线性，长句会更慢；这 10 句偏短，等于**高估**了快路优势的上限。
3. **快路是"已命中"的口径**：它量的是命中之后的首音。命中判定本身另计（`find_hit` 微秒级，
   见 `labs/cloud-bench/report.json` 臂 C），未命中的轮走的是慢路。
4. **快路不含进程启动**：`vox run` 每次几十 ms 的 Python 启动不在 `first_audio_ms` 里。
   进程内适配器（`adapters/framework_kefu`）才是真正兑现 0.x ms 的路径——
   CLI 是控制面/演示面，不是数据面（`docs/24` §三已拍板）。
5. **样本量小**：快路 N=50（10 句 × 5 次）、慢路 N=10；慢路逐句 1 次是因为重复会让
   整卡跑不动，不是统计上最优。慢路尾部（本次上界 `repair_ask_userNo` 6.45 s、`rtf=2.573`）
   只观测到 1 次；快路那个 4.176 ms 上界同样只出现 1 次（`opening__1` 第 3 次重复）。
6. **模型加载状态是"已加载"而不是冷启动**：端点常驻，热身 2.13 s（`machine.slow_warmup_s`）
   未计入统计。冷启动口径（0.6B 冷启动可达数十秒）会进一步拉大慢路，本卡**没有**测冷启动。
7. **慢路有采样波动，别拿单次跑当结论**：本卡已实跑三次，快路 P50 稳定在 0.69–0.715 ms
   （最近两次 0.710 / 0.715，快路可以放心引用），慢路 P50 在 2.09 s–2.52 s 之间
   （最近两次 2088.5 / 2336.0 ms）、上界在 6.45 s–8.55 s 之间摆动。
   所以倍数要按区间读，**不要**把某一次的 P50/P50 当硬指标。
8. **不用 `say`、不引云端 API**：`say` 降为 macOS 应急路径，云端 / 端到端数字
   只引用既有落盘值（`machine.background_reference`），**口径不同、不直接相减、不进本图**。

## 产物

| 文件 | 内容 |
|---|---|
| `run_compare.py` | 可复跑 harness，**零第三方依赖**（只用标准库 + 本仓 `adapters.tts_omlx`） |
| `raw/fast.jsonl` | 50 行：每行 key / repeat / first_audio_ms / hit·miss·fallback·tts_calls / rc |
| `raw/slow.jsonl` | 10 行：每行 key / wall_s / audio_s / rtf / bytes / rate_key |
| `raw/plan_run.jsonl` | 1 行：`examples/plan.json` 整跑的交叉值 |
| `report.json` | **数字唯一来源**：两臂 N/P50/min/max、`rtf_median`、语料 keys、机器与模型加载状态、逐句对照 |
| `docs/media/value-compare.svg` | 一眼明白图（本 harness 生成；手写、对数刻度、合法 XML、无 `<image>`、无外链） |

`raw/*.jsonl` 是每句每臂的**原始记录**，不接受手改——重跑覆盖即可。

## 关于"部分包"这个口径决定

harness 铸 69 句整包，用的适配器是 `tts_omlx`。它的归一自检会**随机**拒掉
头/尾静音超标的产物（`[TTS 归一重试] 重试 N/5`），单个整包约 5% 的句过不了质检，
`vox pack build` 因此会以 `clean=False` 退出 4。

这里**没有**把整包重铸到干净为止（69 句 × 数分钟的循环会让本卡不可复跑——
"不可复跑"比"脏数字"更坏），而是用仓库既有的 `--allow-partial` 逃生口，
然后**硬断言语料 10 句每条都铸出来了**：

```
report.machine.pack_build.corpus_all_present == true   # 否则整条 harness 退出 3
```

部分包里缺的只能是语料**之外**的句。断言明细（`total` / `failed` / `quality_issues`）
写进 `report.machine.pack_build`，验收时能看见口径，不必信任 README。

本次（第三次实跑，落盘的这份 `report.json`）**撞上了**质量问题：
`total=69 / synthesized=69 / failed=0 / quality_issues=1`，`vox pack build` 以
`clean=False` 退出 **rc=4**——这正是上面留 `--allow-partial` 口子的原因。
硬断言仍然通过（`corpus_all_present=true`：缺的 1 条在语料之外），
所以本报告的快慢路数字照常有效；断言照跑，因为「这次的 1」不预示「下次也会是 1」，
「第 1 次实跑 `quality_issues=0`」也不预示「下次会是 0」（见 `notes` 与
`machine.pack_build.note`）。

## 失败不留假数字

端点不通、任一句失败、产物不足 N、包缺语料句 → 退出码 3，
`report.json` 写成 `arms.*.n=0` 并把 `machine.failure` 带上异常类型与消息。
**失败报告不会被误读成测量结果**（`--no-write` 看到 `n=0` 会跳过 SVG 渲染并说明）。
