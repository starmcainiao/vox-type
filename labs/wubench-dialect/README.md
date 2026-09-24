# T15 · Wu-Bench 吴语方言域 ASR → CER 实测（2026-09-19）

> 本目录属 `labs/` 实验层：**不进任何层的契约**。产物自带口径与原始数据。
> 任务卡：`docs/tasks/T15-labs-方言链路实测-WuBench吴语ASR-CER.md`

## 一、本目录做了什么

测一件事：**方言用户进链路，ASR 这一环损失多少**。

链路严格止于 CER，不含任何下游判定：

```
Wu-Bench asr.parquet（吴语 RIFF/WAV 字节 + label 转写）
  → 固定 seed 抽 100 条（4851 行中）
  → 音频写系统临时目录 → adapters/asr_omlx OmlxAsr.transcribe → 用完即删
  → eval/cer.py normalize_for_cer + cer
  → eval/stats.py percentile + bootstrap_ci
  → raw/samples.jsonl（逐条明细） + report.json（汇总）
```

复用的产品 API（不复制逻辑）：`adapters.asr_omlx.adapter.OmlxAsr`、
`eval.cer.normalize_for_cer` / `eval.cer.cer`、`eval.stats.bootstrap_ci` / `eval.stats.percentile`。

## 二、范围裁定（执行方不得扩大）

**「提案 → 命中」不在本卡范围。** 依据（缺一不可，随卡登记 `docs/13 §七#1` 与 `00-索引`）：

1. 语义提案已实测否掉（`docs/13 §八#1`：top-1 11.8% vs 门槛 98.7%，差 8 倍）；
2. 方言集没有「该命中哪个 key」的标注，第八批前置裁定 3 明令**不得自造命中**（`00-索引` 第八批）；
3. 本卡产出的是**方言域 ASR 能力边界数字**，为「预铸在方言场景的可行性」提供依据。

同样不做：语义归一 / 同义替换 / 拼音近似（CER 口径一律走 `eval/cer.py`，改口径 = 破坏性变更）。

## 三、口径

| 项 | 口径 |
| --- | --- |
| CER | `cer(normalize_for_cer(label), normalize_for_cer(transcript))`——去标点与空白后逐字编辑距离 / 归一后 reference 字符数；不夹取上界（转写多说的字也计入） |
| CER 分母 | 仅 `status=="ok"` 的条数；跳过与失败**不进**分母，但都进 report 计数 |
| 抽样 | `random.Random(20260919).sample(range(4851), 100)`——固定 seed，抽到什么跑什么，不筛选 |
| 时长 | 标准库 `struct` 解析 RIFF/WAVE 头（只读元数据，不解析 PCM）；> 60 s 跳过留痕 |
| CI | `bootstrap_ci(cer_values, q=0.5, seed=20260919, resamples=2000, level=0.95)`，百分位法 |
| 分位数 | `eval.stats.percentile`，线性插值（位置 = (n-1)·q） |
| 并发 | **顺序执行**，不并发（oMLX 是本地常驻服务，禁止压测）；不起停任何服务 |

样本量说明：n=100 时 P95/P99 的估计仍有相当噪声，
**读趋势看 CI，不要拿 P95 的点估计当承诺值**（`eval/AGENTS.md §①`）。

## 四、复现

```sh
cd （仓库根）

# 1) 正式 100 条（约 1 分钟，受 oMLX 负载影响）
PYTHONPATH=. ~/miniforge3/bin/python labs/wubench-dialect/run_dialect_probe.py

# 2) 快验（只跑前 20 条；不影响抽样顺序与 seed）
PYTHONPATH=. ~/miniforge3/bin/python labs/wubench-dialect/run_dialect_probe.py --limit 20

```

快验落盘到 `report.limit20.json` / `raw/samples.limit20.jsonl`（`env.limit=20`、`env.output_is_partial=true`），
**不触碰正式产物** `report.json` / `raw/samples.jsonl`——部分跑 rc 0 不等于完整结果，随时可重跑，不入 git。


前置：
- 解释器必须是 `~/miniforge3/bin/python`（3.12.11 + pyarrow 25.0.1；主解释器 3.14 无 pyarrow wheel）。
  脚本开头有解释器守卫，版本不符直接 rc 2。
- `PYTHONPATH=仓库根`，产品代码经 import 复用，不复制。
- oMLX 本机常驻服务 `http://127.0.0.1:10099`，模型 `Qwen3-ASR-0.6B-8bit`（脚本只用不启停）。
- 语料在**仓外**：`~/corpus/ASLP-lab__WenetSpeech-Wu-Bench/understanding/asr.parquet`
  （apache-2.0，sha256 `d053f15173548df0517200f7af94153ab4d0c53e18b484a9974510c7c0c4e041`）。
  换语料请设 `WUBENCH_PARQUET` 环境变量。

`import pyarrow` **只出现在本 labs 目录内**；产品层（core / rules / compiler / assets /
runtime / eval / cli / adapters / packs / tools）零 pyarrow 依赖。

## 五、产物

| 文件 | 内容 | 分发 |
| --- | --- | --- |
| `run_dialect_probe.py` | 主脚本 | 可分发 |
| `raw/samples.jsonl` | 逐条明细：`utt_id`、`task`、时长、`label` 原文、ASR 转写原文、逐条 CER、`status`、失败/跳过原因 | **含第三方语料逐字原文**，按 `docs/11 §11.7` 属 `labs/**/raw/**`，**不进公开分发** |
| `report.json` | 汇总：计数四元组、status 分桶、CER 块、时长分布、ASR 延迟、task 分桶、抽样 `utt_id` 清单、`env`、`scope`、`caliber`、`sanity` | 不含任何语料逐字原文，可分发 |

## 六、实测结论（2026-09-19，n=100 全数 `status=="ok"`）

- `n_sampled=100` / `n_valid=100` / `n_skipped=0` / `n_asr_fail=0`
  （status 分桶 `{ok: 100}`；无一条超过 60 s，`max=16.62 s`）
- CER：**mean 0.268631 / P50 0.25463 / P95 0.572269 / P99 0.689143**
  / bootstrap 95% CI（对 P50 分位）`[0.214286, 0.3]`（seed 20260919，2000 次重采样）
- 逐条 CER 落在 `[0.0, 0.714286]`，分布无长尾截断（未夹取上界）
- ASR 延迟与总耗时每次运行毫秒级波动，以落盘 `report.json` 的 `asr_latency_seconds` /
  `env.elapsed_seconds` 为准；CER 数字是确定性的（固定 seed 抽样 + 确定性转写），可逐位复现
- `task` 只有 1 类（`asr`），task 分桶退化为全集（仍照报，不隐藏口径）

**读法**：方言域 CER ≈ 27%、半数样本 ≈ 25%。也就是说模型基本"猜中一句"的水平并不成立——
这不是产品可用性问题（产品链路本来就不指望 ASR 在方言上命中固定话术 key），
而是**给"预铸在方言场景的可行性"划的边界**：ASR 这一环的字符错误率本身就是 ~25–27%，
任何靠 ASR 文本做判定/检索的下游都要按这个基底来估。

sanity 自断言（证明 CER 管道真在工作，非恒真）：
`cer(normalize_for_cer("你好世界"), normalize_for_cer("你好时界")) == 0.25`（实测 0.25），
同串对照 `== 0.0`（实测 0.0）。两组任一不符即 rc 4 退出。

脚本结尾自断言 `n_valid + n_skipped + n_asr_fail == n_sampled`，不成立即 rc 4，不产出半个报告。

## 七、回滚

```sh
git clean -fd labs/wubench-dialect
```

本卡不触碰任何既有文件，回滚无残留。
