# eval/ · 对拍与盲测（冻结区·口径冻结）

## ① 职责 / 不负责什么

**职责**：产**可复现的数字**，作为一切对外声明的唯一来源。
- **离线对拍 harness**：同一 plan，快路（命中资产）vs 慢路（全程现场合成）——延迟、TTS 调用数与字符数、命中率、预铸时长占比
- **指标口径**：字段名与算法（命中率 = 命中 literal 段/全部 literal 段；预铸时长占比；首音频延迟 P50/P99）
- **统计纪律**：结论必须带样本量与置信区间（厚尾分布 → 报 P99/P999，禁止用均值外推）
- **golden set 与回归门槛**：真人标注用例的存放格式与回归判定
- **拼接自然度基准**（拟对外贡献）：三产线对照（全预铸拼接 / 逐句实时合成 / 端到端输出）× 四维度（接缝可察觉性、整段 MOS、回读 CER、跨段音色一致性）

**不负责**：不改产品代码（评测只读）、不做优化、不做命中判定。

## ② 输入 / 输出契约

- 输入：资产包 + 固定语料（公开 demo 话术）+ 固定种子的用例集 + `runtime/` 事件流。
- 输出：**报告（JSON + 人类可读摘要）**，含：口径说明、样本量、原始数据指针、复现命令。
- 报告格式冻结：字段名与 `runtime/` 事件字段一致。

## ③ 验收条件

1. **可复现**：同输入 + 同种子 → 指标一致（跑两次断言）；
2. **口径与设计基础一致**：报告里每个数字都标注它对应 §2 的哪条硬产出（命中率门槛/可靠性红线/统计口径…）；
3. **不得美化**：原始数据缺失时报告必须标 `incomplete`，不得用估算值填补；
4. **离线可跑**：不依赖真实语音链路（无麦无网也能出报告）——这是"快速验证"的技术前提；
5. 盲测协议的样本量与随机化方式写明（否则不算基准）。

## ④ 本层数据收集

评测报告 + 原始指标序列（供复查）；**真人标注单独存放**，只入本地仓库，不进公开分发。

## ⑤ 依赖边界

- 允许依赖：`core/`（指标字段定义）、`assets/`（读包）、`runtime/`（只读其事件流，不 import 内部）。
- 禁止：不得修改产品代码；不得为了让数字好看而改口径（改口径 = 改 `core/` 定义 = 破坏性变更）。

## ⑥ 变更纪律

改指标口径 = 契约变更 → 大版本 + 重跑历史基线（否则新旧数字不可比）。

## ⑦ 冻结状态

**[冻] 冻结区**。指标口径不能随便改；新增指标只能追加，不能覆盖既有定义。

---

## ⑧ 双工质量指标（T20 追加；只增不覆盖）

**实现**：`eval/duplex_quality.py`（harness + CLI）+ `eval/tests/test_duplex_quality.py`。
产物：`labs/duplex-quality/`（跑批脚本 + `report.json` + README）。

### 边界声明（docs/12 原话落点）

**只测「系统主动开口」这一侧**——发起方是我们、场景可自造。客服场景的双工是「用户打断我」
（别人的行为，本仓测不了）；前置包场景是「我什么时候该开口」（我们自己的行为，可自造可测）。
因此：

- 用户行为脚本**全部由固定种子生成**（`generate_scenario`），**不读任何真实用户数据、录音、会话**；
- 三指标**全部从 `runtime.Executor(policy_stream=True)` 的事件流算**——本仓 runtime 是
  离线产物生成器（plan → WAV + 事件流），不需要真实音频、不依赖实时播放、不碰麦克风；
- 场景时长轴取**包内 manifest 的 `duration_ms`**（不吃挂钟时间），这是可复现的前提。

### 三指标口径（冻结；改口径 = 大版本）

| 指标 | 定义 |
|---|---|
| **打断准确率** `barge_in_accuracy` | 对脚本里每个 `barge_in` 动作，判定来自事件流：该单元 `requires_confirm=True` → `blocked`（应拦下等确认）；`False` → `allowed`（应允许打断）。脚本期望（`want_blocked`）由**生成规则**在生成时刻记录（终态单元 = 本条 plan 最后一个单元 → 期望拦；否则期望允许），与事件流判定独立比对。准确率 = 一致数 / 打断动作总数。 |
| **假阳性** `false_positive_rate` | 脚本里 `speak` / `silence`（**非打断**）落在 `requires_confirm=True` 单元上的数量 / 非打断动作总数（0 为理想）。只有 `barge_in=confirm` 且命中终态（`terminal_keys` 或最后一个单元）时该值才可能 > 0；`allow` 预设下恒 0，报告如实写出、不隐藏。 |
| **轮转延迟** `turnover_latency_ms` | = `listen_ms`（等待窗口事件，最后一个单元播完 → 窗口结束，语义 = `patience_ms`）+ `silence_pad_ms` × (单元数-1)（单元间切换耗时）。单位 ms，报 **P50 / P99**——分位数**直接调用 `eval.stats.percentile`**（linear，位置=(n-1)q，与 bench 同口径）；本模块不自造分位数算法，否则两处口径会悄悄漂移。空样本由 stats 层报错后转成 `DuplexQualityError`，不写 0.0 冒充零延迟。 |

**与 bench 时序指标的区别**：`turnover_latency_ms` 是**设计量**（只由参数与包内预铸时长推得），
所以同输入跑两次必逐值相等；bench 的 `first_audio_ms` 是真机实测值，只承诺分布口径与 CI 方法一致，
不承诺数值相等。报告里如实标注，不得混称。

**`barge_in` 模式与判据的关系（口径裁定）**：卡面把「`barge_in=allow` → 应允许」与
「`requires_confirm=true` → 应拦」并列为两条判据；在本仓 runtime 实现里两者**等价**
（`barge_in=allow` 时 `_requires_confirm` 恒 `False`），所以一律以事件流布尔值
`requires_confirm` 为准，`barge_in` 模式值不进判定——事件流是唯一事实来源，
同一事实不被算两遍，且指标可被验收方从事件流独立复算。

### 复现命令

```sh
# 前置：先 build 包（与 eval 其它 harness 同一前置）
sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1

# 跑批（默认三预设：baseline-allow / confirm-patience400 / backchannel-off）
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out labs/duplex-quality --seed 20260920

# 可复现校验：同一 (参数集, 种子, 包) 跑两次，除 generated_at 外逐字节一致
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out /tmp/dq-a --seed 20260920
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out /tmp/dq-b --seed 20260920
diff <(grep -v '"generated_at"' /tmp/dq-a/report.json) <(grep -v '"generated_at"' /tmp/dq-b/report.json)   # 无输出 = 一致
# 换种子必须变
python3 -m eval.duplex_quality --pack packs/heat_kefu_build/heat-kefu-1 --out /tmp/dq-c --seed 111
diff <(grep -v '"generated_at"' /tmp/dq-a/report.json) <(grep -v '"generated_at"' /tmp/dq-c/report.json)   # 有差异 = 种子起作用
```

`provenance` 必带：包 manifest sha256 + 参数集 + 种子 + 本仓 commit。

### 负例纪律（不产半份报告）

空脚本 / 非法 duplex 参数 / 包不存在 → 抛错（消息含字段名与实际值，非法参数引 `DuplexError` 语义）
且**退出码非 0、不建目录、不落 `report.json`**。执行期失败（`RuntimeMissError` 等）不同：
报告如实标 `incomplete` 并写出，退出码仍非 0（可复查，不得美化）。

### 反空转验证（已内置于 `eval/tests/test_duplex_quality.py`）

- `TestInjectionCatchesConstantMetric`：把指标算成常数 → 断言必须判红；
- `TestInjectionCatchesConstantSeed`：把种子改成固定常数（忽略入参）→ 「换种子必变」断言必须判红；
- 全部走产品 API（`run_duplex_quality` / `run_duplex_quality_cli` / `Executor(policy_stream=True)`），
  测试内不自造事件流、不重实现指标算法。

**注入实测记录（2026-09-22，基线 cac6b57，改产品代码 → 跑全量 → 还原）**：

| 注入点 | 注入内容 | 判红用例 |
|---|---|---|
| `compute_metrics` | `barge_in_accuracy` 写死 `1.0` | `test_accuracy_assertion_fails_on_constant_metric`、`test_accuracy_recomputes_from_event_stream` |
| `_quantile` | 恒返回 `4242.0` | `test_p50_p99_differs_when_varied`、`test_turnover_assertion_fails_on_constant_metric`、`test_turnover_latency_recomputes_from_params` |
| `generate_scenario` | `Random(seed)` → `Random(20260920)` | `test_seed_change_assertion_fails_on_constant_seed`、`test_different_seed_changes_report`、`test_scenario_generator_deterministic_and_seed_sensitive` |

还原后 31 用例全绿。种子注入用例**真跑产品 API 两次**（不同 seed）再把「换种子必变」抽成独立
断言函数执行——不靠测试内自造的替身函数空转，判红能力是实测出来的。

## ⑩ 结构预算（T21 登记）

结构预算：可执行行数阈值 <= 150

（机器可读标记行；**不要在这行加任何修饰符**——加粗星号会让正则匹配失败，等于阈值消失。）

口径：`tokenize` 后 `NEWLINE` token 计数（一条逻辑语句结束时的换行），
排除注释与 docstring；不含 `tests/` 与 `__pycache__`。
与 `adapters/AGENTS.md §⑨` 的 145–150 带同算法。

阈值出处与 `adapters/AGENTS.md §②`「单个适配器 ≤ 150 行」同源（本仓 R09 的统一体量预算）。
eval 层明写于此是为了让 `tools/structure_budget/check.py` 有机器可读的阈值来源——
**脚本不另立一套阈值**。

本层是 harness 层，体量天然偏大：`bench.py` / `duplex_quality.py` / `readback.py` /
`report.py` 均超阈值，已在 `tools/structure_budget/LEDGER.md` 逐条登记豁免（含理由）。
豁免不是永久豁票：新增功能前先评估能否拆（如把「采样-聚合」「落盘-指纹」分开）。

## ⑪ helper 去重（T21 #17）

`bench.py` / `readback.py` / `report.py` 曾各持一份逐字相同的
`_write_jsonl` / `_read_jsonl` / `_sha256_of_file`（另有 `duplex_quality.py` 一份
`_sha256_of_file`）。T21 收敛为 `eval/_io.py` 的**唯一**实现：

- `eval/_io.py` **只 import 标准库**（`hashlib` / `json` / `pathlib` / `typing`）。
  收敛点必须落在这里而不是 `bench.py`：`bench` 为跑 harness 静态 import 了
  `assets/` 与 `runtime/`，helper 住在那里会把两条不该有的跨层依赖带给
  只需要标准库的 `readback` / `report`。
- 行为契约逐字不变：`ensure_ascii=False`、`default=str`、一行一条；
  坏行 `{"__bad_line__": 行号}`、非 dict `{"__not_dict__": 行号}`；
  sha256 分块 65536。**指纹值不变**。
- **指纹咬合**：`bench` 落 raw 时写 `{name}_sha256`（写侧），
  `report.check_raw_on_disk` 重算比对（验侧），两侧共用同一个 `_io.sha256_of_file`。
  写侧与验侧各持一份时，分块策略或编码假设差一个字就会出现
  「自己写的指纹自己验不过」或「该抓的值级篡改抓不到」（docs/08 §8.7 欠账 1）。
