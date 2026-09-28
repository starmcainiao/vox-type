# T15 · labs：方言链路实测（Wu-Bench 吴语 → oMLX ASR → CER 报告）

## 背景（只写必需）

T15 原被「音频源 + 解释器」双重阻塞，第九批已全部解除：
- 数据：`WenetSpeech-Wu-Bench` 的 `asr.parquet`（apache-2.0，sha256 已校验，落**仓外** `~/corpus/ASLP-lab__WenetSpeech-Wu-Bench/understanding/asr.parquet`）；4851 行，列 `task / utt_id / audio / label`，`audio` 直接是 RIFF/WAV 字节，`label` 是吴语转写；
- 解释器：`~/miniforge3/bin/python`（3.12.11 + pyarrow 25.0.1）实测可读（主解释器 3.14 无 pyarrow wheel）；
- ASR：复用 T14 交付的 `adapters/asr_omlx`（oMLX `:10099`，`Qwen3-ASR-0.6B-8bit`，本机常驻服务，实测单条 0.7–1.3 秒）。

**范围裁定（策划落卡，执行方不得扩大）**：旧清单里 T15 链路写作「音频→ASR→归一化→提案→命中」，
其中「**提案→命中**」**不在本卡范围**，依据（缺一不可）：
1. 语义提案已实测否掉（`docs/13 §八#1`：top-1 11.8% vs 门槛 98.7%，差 8 倍）；
2. 方言集没有「该命中哪个 key」的标注，第八批前置裁定 3 明令**不得自造命中**（`00-索引` 第八批）；
3. 本卡产出的是**方言域 ASR 能力边界数字**（方言用户进链路，ASR 这一环损失多少），
   为「预铸在方言场景的可行性」提供依据。此裁定随本卡登记 `docs/13 §七#1` 与 `00-索引`。

## 目标（可验收的产物）

全部落在**新目录** `labs/wubench-dialect/`：

- 产物 1：`run_dialect_probe.py` —— 主脚本（详见硬要求）；
- 产物 2：`raw/samples.jsonl` —— 逐条明细：`utt_id`、`task`、音频时长、`label` 原文、ASR 转写原文、逐条 CER、
  （跳过/失败行写原因字段）。**注意：本产物含第三方语料逐字原文，按 `docs/11 §11.7` 属 `labs/**/raw/**`，不进公开分发**；
- 产物 3：`report.json` —— 汇总（**不含任何语料逐字原文**，可进公开分发）：
  `n_sampled / n_valid / n_skipped`（带原因分桶计数）`/ n_asr_fail`、CER 的 `mean / P50 / P95 / bootstrap 95% CI`
  （复用 `eval/stats.py` 的 `bootstrap_ci` 与 `percentile`）、音频时长分布（P50/P95/max）、
  按 `task` 分桶的汇总（若 task 多于 1 类）、抽样 `utt_id` 清单（按序）、`env` 块
  （parquet sha256、seed、模型名、endpoint、python 版本、时间戳）、`scope` 字段（写明上述范围裁定）；
- 产物 4：`README.md` —— 口径 + 复现命令 + 实测结论 + 范围裁定记录（引用本卡依据）。

## 硬要求（逐条都要有可观测的验证）

1. **解释器**：主脚本必须用 `~/miniforge3/bin/python` 跑（pyarrow 只有它有）。
   `import pyarrow` **只允许出现在本 labs 目录内**——任何产品层（core/rules/compiler/assets/runtime/eval/cli/adapters/packs/tools）不得新增 pyarrow 依赖；
2. **必须复用产品 API，不得复制逻辑**（PYTHONPATH=仓库根后 import）：
   - `adapters.asr_omlx.adapter.OmlxAsr`（音频转写；构造参数用默认值即可）；
   - `eval.cer.normalize_for_cer` + `eval.cer.cer`（归一化与 CER，不做语义归一）；
   - `eval.stats.bootstrap_ci` + `eval.stats.percentile`（CI 与分位数）；
3. **抽样可复现**：固定 seed——`random.Random(20260919)`，从 4851 行里抽 **100** 行（`sample(range(n_rows), 100)`），
   抽样结果按抽中顺序写进 report 的 `utt_id` 清单；
4. **音频临时文件不落仓**：`audio` 字节写到系统临时目录（`tempfile`）再喂 `OmlxAsr.transcribe`，用完即删；
   **不得把任何音频字节写进仓库**；
5. **跳过与失败必须留痕计数，不得静默**：
   - 音频时长 > 60 秒 → 跳过，`raw/samples.jsonl` 里写 `status="skipped_too_long"` + 时长；
   - `OmlxAsr` 抛 `AsrError` → 计 `n_asr_fail`，raw 行写 `status="asr_error"` + 异常消息摘要；
   - 跳过/失败行**都不进 CER 统计分母**，但都进 report 的计数；
6. **fail-closed**：parquet 缺任一必需列（`utt_id/audio/label`）/ 行数为 0 → 抛错退出（非零 rc），不产出半个报告；
   脚本结尾自断言：`n_valid + n_skipped + n_asr_fail == n_sampled`，断言失败即非零退出；
7. **内置 sanity 自断言**（调产品 API，不是复制逻辑）：用已知对照断言 CER 管道——
   `cer(normalize_for_cer("你好世界"), normalize_for_cer("你好时界")) == 0.25`，不等即非零退出；
8. **顺序执行，不并发**（本地服务，禁止压测）；不得起停任何服务；不得联网（除 oMLX 本机端点）；
9. 落盘只在 `labs/wubench-dialect/` 内；**不得 `git add` / `git commit`**（提交由主会话做）。

## 允许修改的文件（白名单）

```
允许新增：labs/wubench-dialect/**（run_dialect_probe.py、raw/、report.json、README.md）
允许修改：无
禁止触碰：其他一切文件——尤其不得改 core/ rules/ compiler/ assets/ runtime/ eval/ cli/ adapters/ packs/ tools/ docs/
```

## 禁止事项

- 不得改任何产品代码（上述各层全部**只读**）；不得新增第三方依赖进产品层；
- 不得在脚本里做语义归一、不得自算一份 CER 绕开 `eval/cer.py`；
- 不得做「提案→命中」实验（范围裁定见背景，违反即整卡退回）；
- 不得把音频字节写进仓库；不得把 `label`/转写原文写进 `report.json` 或 `README.md`（raw/ 除外）；
- 不得顺手优化、顺手重构；不得为了让数字好看而筛选样本（seed 定死，抽到什么跑什么）。

## 验收标准（逐条可判定，验收方会逐条核对）

1. `cd （仓库根） && ~/miniforge3/bin/python labs/wubench-dialect/run_dialect_probe.py`
   → **rc 0**，产出 `raw/samples.jsonl` 与 `report.json`；
2. `raw/samples.jsonl` 行数 == 100；每行必有 `utt_id` 与 `status` 三态之一（`ok` / `skipped_too_long` / `asr_error`）；
   `status=="ok"` 的行必有非空转写与 CER 数值；
3. `report.json` 必含：计数四元组（`n_sampled==100` / `n_valid` / `n_skipped` / `n_asr_fail`，且和为 100）、
   CER 块（`mean`、`P50`、`P95`、`ci95_low`、`ci95_high`，基于 `eval/stats.py`）、时长分布、
   按 `task` 分桶（若多类）、抽样 `utt_id` 清单（100 个，按抽中顺序）、`env` 块、`scope` 范围裁定字段；
4. **抽样可复现**：`~/miniforge3/bin/python labs/wubench-dialect/run_dialect_probe.py --limit 20`
   → rc 0，且前 20 个 `utt_id` 与正式 100 条运行的清单**逐位一致**（`--limit` 只影响跑多少条，不影响抽样顺序）；
5. **CER 独立复核**：验收方从 `raw/samples.jsonl` 随机抽 5 条 `ok` 行，用 `eval/cer.py` 独立复算 CER → 逐条一致；
6. `git status --porcelain -uall` 的新增文件**全部**在 `labs/wubench-dialect/` 内；
   `git diff --stat -- core rules compiler assets runtime eval cli adapters packs tools docs` 为空；
7. 防污染回归：`python3 -m unittest discover -s adapters` 与 `-s eval` 全绿（adapters 301 / eval 192）。

## 反空转条款（每张卡必带，T01 教训）

- **必须调用产品 API**：转写走 `OmlxAsr.transcribe`、CER 走 `eval.cer.cer`、CI 走 `eval.stats.bootstrap_ci`，
  不得在 labs 里复制任何一份等价逻辑（违者整卡退回）；
- **断言必须能被打破**：第 7 条 sanity 断言若把对照改成 `cer(normalize_for_cer("你好世界"), normalize_for_cer("你好世界"))`
  必须为 0——执行方在报告里贴出这组对照的实际输出，证明 CER 管道真的在工作；
- 跳过/失败必须留痕（第 5 条）；**不得为了让 CER 变好看而剔除任何 `ok` 行**；
- 不得为通过验收放宽任何自断言。

## 回滚方式

新增目录 → `git clean -fd labs/wubench-dialect`。不触碰既有文件，回滚无残留。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`）。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T15-labs-方言链路实测-WuBench吴语ASR-CER.md)" --dir （仓库根）
```

**数据分级：公开级**——Wu-Bench 是 apache-2.0 公开语料；卡内只有路径与口径，不含语料原文与音频；
运行时音频与转写都在本机，不随派发外发。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19，第十批；经一轮单点退回）**

### 验收记录（验收方独立复核，不采信执行方自述）

- 验收 2–5 + 泄漏检查全过：raw 100 行全 `ok` 无缺字段；report 字段齐备、计数恒等成立；
  抽样独立复算（`random.Random(20260919).sample(range(4851),100)`）与 report/raw 行序逐位一致；
  CER 抽 5 条独立复算逐条一致；report/README 对 100 条 label 原文探针零命中。
- **验收 1 首轮不达标，单点退回**：卡面字面命令（无 PYTHONPATH）rc 1 `ModuleNotFoundError`——
  脚本算了 `REPO_ROOT` 却没塞 `sys.path`，与 T21 先例（`run_e2e.py:246` 自带兜底）不一致。
  修复后裸跑 rc 0，复验落盘与首轮正式报告**实质字段全等**（仅 `asr_latency_seconds` 三项毫秒级
  计时抖动——转写确定性成立，延迟本就该波动）。
- 防污染回归（验收方本轮亲跑）：adapters **311** / eval **208** 全绿。
- **卡错更正注明（纪律 12：卡错实现对）**：验收 7 卡内写的基线「adapters 301 / eval 192」是策划
  抄了接手指南的过时拆分（1,063 拆分早于 T14c 的 +26 条测试）；当前真值 **311 / 208**，
  全层九根合计 **1,089** 全绿（验收方 2026-09-19 逐根实测）。判据按「全绿」执行，数量以真值为准。
- 执行方 3 项扩展的裁定（均**接受**）：① 额外 3 类 `skipped_*` status（`skipped_bad_audio` /
  `skipped_too_large` / `skipped_empty_reference`）——比卡面三态更 fail-closed，符合「跳过必须留痕」
  意图，本次 100 条零触发；② `task` 单类也照报分桶（不隐藏口径）；③ 时长解析自带 RIFF 头解析
  （元数据读取，不属被禁复制的 ASR/CER/CI 逻辑）。
- 静默失败审计（`blackiron-silent-failure-hunter`，固定动作）：**1 P1 + 3 P2 + 2 建议，已全修并逐项复验**；
  审计同时独立确认核心数字（100 条逐条重算 CER 零不符、CI 按声明口径重算逐位一致、抽样可复现）。
  - **P1**：`--limit` 快验破坏性覆盖正式产物且 rc 0 → 修后 `output_paths(limit)` 分流（limit 跑只写
    `*.limit{N}.*` + `env.output_is_partial` + `[partial]` 行）；复验：limit 跑后正式两文件 sha256 逐字不变。
  - **P2a**：非 FatalError 逃出 main → rc 1 → 修后统一收口 rc 3；复验 `--limit 19`（触发 MIN_REPEATS=20 下限）实测 rc 3。
  - **P2b**：README 延迟数字与落盘 report 漂移 → 改「以落盘为准」活引用。
  - **P2c+建议**：cer 块加 `ci_estimand: "P50"`、`resamples=2000` 显式传参、延迟只统计 ok 行。
  - **对外引用口径**：CER 的 CI 必须带「**对 P50**」限定（本仓文档已同步）。
  - 裁定 2 条执行方取舍：`n_valid<20` 统计不可计算归 rc 3（运行期失败，非自断言失败）；正式产物 `env.limit=None`
    而 partial 键留白（避免无意义布尔）——均接受。
