# heat-kefu-seq · 多分句整段的序列命中与 plan 拼接实测（T33）

一次性的离线对拍，**不进任何层的契约**。回答一个问题：

> 40 单句铸入之后，`packs/heat_kefu` 里 14 条多分句整段（`opening`、
> `fallback_internal`、`repair_confirm_question` 等）能不能走快路？
> 走不了的部分，成本是多少？

## 口径（照 `docs/10 §10.7`，不自造判据）

| 项 | 口径 |
|---|---|
| 比较空间 | **归一化空间**（§10.3 四步：去空白 → 全角/半角 → NFKC → 小写），两侧同口径 |
| 判据 | 只有一种：**逐字覆盖**——`normalize(T) == v₁ + v₂ + … + vₖ`，每段仍是「归一化后逐字相等」 |
| 选择规则 | 最长优先；同长度多候选取文本索引内**首条**（与 `pick_by_text` 同源，顺序稳定） |
| 候选过滤 | `part_index == 0` + `rate_key` 匹配 + `pack.lookup` 指纹/文件复核（复核不过 = 该段不可用） |
| 失败语义 | 任一段覆盖不上 → **整体未命中**，`entries == ()`（不播半句），带 `uncovered` 断点 |
| 禁止 | 编辑距离 / 语义 / 去标点 / 部分播放 / 段序重排去重 / 跨段吞并 |
| 与 `find_hit` 的关系 | `find_hit` 是单段逐字（T29 公开 API，语义不变）；`find_hit_sequence` 是新入口，k=1 时退化一致 |

命中 = 用包内音频拼接播放，**零 TTS 调用**（`docs/10 §10.4`）。本脚本把这一点当硬断言：
`tts_calls != 0` 会进 `report.json.problems` 并以 rc 3 退出。

## 复现

前置：需要 kefu 供热预设 yaml（只读，经环境变量门控，路径不写进代码），以及已构建的预铸包。

```sh
# 1) 预铸（--out 必须落在包目录外，cli.assert_not_within 硬拦，docs/08 §8.5）
bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1

# 2) 实测（裸跑，脚本自带 sys.path 兜底，不依赖 PYTHONPATH）
KEFU_HEAT_YAML=/path/to/供热预设.yaml \
  python3 labs/heat-kefu-seq/run_seq_bench.py

# 可选
  python3 labs/heat-kefu-seq/run_seq_bench.py --repeat 5      # P50 采样轮数
  python3 labs/heat-kefu-seq/run_seq_bench.py --skip-say      # 跳过慢路 say 对照
```

退出码：`0` 成功（14/14 序列命中 + 现状档 0 命中 + `tts_calls` 合计 0）/
`2` 前置缺失（yaml 未配置或未构建）/ `3` 运行期失败或口径被打破。

## 四臂对照

每条整段跑四件事，写进 `report.json.details`：

1. **现状档（对照臂）** `find_hit(text=整段)` → 预期 **miss**；
2. **序列档** `find_hit_sequence(text=整段)` → 预期**命中**，记录段数与段 key 序列；
3. **端到端** 命中序列拼成 plan → `runtime.Executor` 播 → `tts_calls`（预期 0）、
   `first_audio_ms`、`total_duration_ms`；音频只落系统临时目录，**不入仓**；
4. **慢路对照** 同一整段用 `adapters.tts_macsay`（macOS `say`）合成一遍 → 这是「走原路」的成本。

## 结果（**以落盘的 `report.json` 为准**）

`report.json.summary` 是唯一的数字来源——本 README 不复述具体数值，
避免报告被重跑后这里漏更新。字段：

- `seq_hit` / `seq_miss` / `n_multi_clause`：序列档命中数 / 未命中数 / 整段条数；
- `old_arm_hit`：现状档命中数（预期 0，证明「整段本来查不到」这个前提没被改掉）；
- `segment_count_p50` / `_min` / `_max`：命中段数分布（= 各整段的句数）；
- `executor_wall_ms_p50` / `executor_first_audio_ms_p50` / `executor_total_duration_ms_p50`：命中臂耗时；
- `tts_calls_sum`：命中臂 TTS 调用合计（预期 0）；
- `slow_arm_say_ms_p50`：慢路合成耗时；`hit_vs_slow_delta_ms_p50`：命中臂减慢路的差值（负数 = 命中臂更快）；
- `problems`：任何打破口径的条目（为空才算 `passed: true`）。

报告自带 `provenance`，可自证版本：`yaml_sha256`（语料 yaml 的 sha256，只记摘要不记
路径与全文）、`manifest_sha256`（构建产物 manifest.json 的 sha256）、`git_commit`
（`git rev-parse HEAD`，git 不可用时为 null 并在 `env.git_error` 留痕）。

## 边界（不做什么）

- 不联网、不起服务、不改任何层的产品代码；
- 音频产物只落系统临时目录，跑完即删，不入仓；
- 只写 yaml 里的预设话术文本（本包数据分级 = 公开），不写 yaml 路径 / kefu 内网地址 / token；
- 不复制任何命中判定或切句逻辑——全部走 `adapters.framework_kefu` 与 `compiler.source` 的公开 API；
- 只做序列命中的实测与对拍；把序列档接进 kefu 钩子是下一张跨仓卡（本仓不动 kefu）。
