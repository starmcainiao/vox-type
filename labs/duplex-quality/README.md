# labs/duplex-quality — 双工质量评测产物（T20）

**定位（docs/12 原话）**：只测「系统主动开口」这一侧——发起方是我们、场景可自造。
客服场景的双工是「用户打断我」（别人的行为，本仓测不了）；前置包场景是「我什么时候该开口」
（我们自己的行为，可自造、可测）。

**为什么不需要真实音频**：本仓 runtime 是**离线产物生成器**（plan → WAV + 事件流，
`policy_stream=True` 时每个单元带 `barge_in` / `requires_confirm` / `spoken_ms` / `backchannel_ok`，
末尾带等待窗口事件 `listen_ms`）。三指标**全部从事件流算**，不依赖实时播放、不碰麦克风。

## 产物

| 文件 | 内容 |
|---|---|
| `report.json` | 三指标 + 中间量 + provenance（`bash run.sh` 生成） |
| `run.sh` | 跑批 + 可复现自检（相对路径，无绝对路径） |
| `README.md` | 本文件：口径、边界声明、复现命令 |

## 三指标口径（冻结，详见 `eval/AGENTS.md ⑧`）

| 指标 | 定义 |
|---|---|
| 打断准确率 `barge_in_accuracy` | 判定来自事件流：单元 `requires_confirm=True` → `blocked`，`False` → `allowed`；与生成规则在生成时刻记录的脚本期望（`want_blocked`）独立比对。一致数 / 打断动作总数。 |
| 假阳性 `false_positive_rate` | 非打断动作（`speak` / `silence`）落在 `requires_confirm=True` 单元上的比例（0 为理想）。仅 `barge_in=confirm` 预设下有判别力。 |
| 轮转延迟 `turnover_latency_ms` | `listen_ms`（+ `silence_pad_ms` × (单元数-1)），报 P50 / P99。是**设计量**，不吃挂钟时间。 |

## 边界声明

- 场景脚本**全部由固定种子生成**（`generate_scenario`），**不读任何真实用户数据、录音、会话**；
- 时长轴取包内 manifest 的 `duration_ms`（不吃 wall-clock）；
- `provenance` 必带：包 manifest sha256 + 参数集 + 种子 + 本仓 commit；
- 负例（空脚本 / 非法 duplex 参数 / 包不存在）→ 非 0 退出且**不落 `report.json`**。

## 复现命令

```sh
# 在仓库根目录（前置：包已 build；run.sh 会自动构建）
sh labs/duplex-quality/run.sh

# 单步
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 \
    --out labs/duplex-quality --seed 20260920

# 可复现校验：除 generated_at 外逐字节一致；换种子必变
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out /tmp/dq-a --seed 20260920
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out /tmp/dq-b --seed 20260920
diff <(grep -v '"generated_at"' /tmp/dq-a/report.json) <(grep -v '"generated_at"' /tmp/dq-b/report.json)  # 无输出
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out /tmp/dq-c --seed 111
diff <(grep -v '"generated_at"' /tmp/dq-a/report.json) <(grep -v '"generated_at"' /tmp/dq-c/report.json)  # 有差异
```

## 测试

```sh
python3 -m unittest eval.tests.test_duplex_quality
```

含反空转注入验证：指标算成常数 → 判红；种子改常数 → 换种子用例判红。
