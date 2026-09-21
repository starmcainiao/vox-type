# T08 · eval：离线对拍 harness + 可复现报告（快路 vs 慢路）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡只有口径、公式与字段名，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。卡内要求的固定语料是**自造公开话术**（表单流程 demo），不得引入任何真实通话内容。可派发。

## 背景

六层已通（core / rules / assets / adapters / compiler / runtime，313 条测试；「compiler 出包 → runtime 播包」闭环已由验收方手跑通过）。但 **`eval/` 至今没有任何数字** —— 项目对外声明的"零延迟、零成本"目前只有机制证据，**没有数据**。

本卡做 `eval/` 的第一件事：**离线对拍 harness**——同一份话术语料，跑两条产线并出可复现报告：

- **快路**：`plan` 命中预铸资产（`runtime.Executor`，`allow_fallback=False`）；
- **慢路**：同样的话术**全程现场合成**（同一 `Executor`，单位以 `SAY_LIVE` 自由文本表达 → `miss / say_live_text` → 逐句调 TTS）。

产出：首响延迟 P50/P99（含置信区间与样本量）、命中率、预铸时长占比、TTS 调用数与合成字符数。

**必读**（不得修改）：`AGENTS.md`、`eval/AGENTS.md`（本层的职责/契约/验收条件，尤其 §③ 五条）、`docs/05-evidence-plan.md` §5.1（五条指标口径）、`runtime/AGENTS.md` §②④（事件字段）、`docs/06-预铸与执行-补定口径.md` §6.2（执行语义，尤其 §6.2.2 三种未命中原因）。

依赖（只读其公开 API，不得修改）：`core/metrics_spec.py`（**字段名一律引这里的常量，不得字面量复制**）、`assets/`（`load_pack` 读包）、`runtime/`（**只从 `runtime/__init__.py` 导出的公共名字**用 `Executor` / `RuntimeMissError`）。

## 目标（产物）

```
eval/__init__.py                    导出 run_bench / BenchConfig / BenchReportError /
                                    percentile / bootstrap_ci / build_report / write_report /
                                    OfflineTts / MIN_REPEATS / DEFAULT_REPEATS
eval/stats.py                       分位数 + 置信区间 + 样本量校验（纯函数，零依赖）
eval/bench.py                       harness：两臂执行 + 重复采样 + 原始数据落盘 + CLI
eval/report.py                      报告组装（JSON + 人读摘要）
eval/offline_tts.py                 离线替身适配器（**非产品件**：只为无网无 `say` 时也能出报告）
eval/corpus/demo_broadcast.json     固定语料（公开 demo 话术，12 个单位）
eval/tests/__init__.py
eval/tests/test_stats.py
eval/tests/test_bench.py
eval/tests/test_report.py
```

### 1. `eval/stats.py`（纯函数，标准库）

```python
def percentile(samples: Sequence[float], q: float) -> float
    # q ∈ (0,1)；线性插值法（位置 = (n-1)*q）；空样本 → ValueError（消息含实际样本量 0）
def bootstrap_ci(samples: Sequence[float], q: float, *, seed: int,
                 resamples: int = BOOTSTRAP_RESAMPLES, level: float = 0.95) -> tuple[float, float]
    # 百分位 bootstrap：random.Random(seed) 有放回重采样，取重采样分布的 2.5% / 97.5% 分位
    # （同一线性插值法）。固定 seed → 同一输入必得同一区间（可复现）。
def require_min_samples(samples, *, name: str) -> None
    # len(samples) < MIN_REPEATS → ValueError，消息含 name 与实际样本量
```

常量（**冻结**，报告里要写出来）：`MIN_REPEATS = 20`、`DEFAULT_REPEATS = 30`、`BOOTSTRAP_RESAMPLES = 2000`、`DEFAULT_SEED = 20260917`、`QUANTILE_METHOD = "linear"`。

> 口径理由（写进报告）：厚尾分布禁均值外推（`eval/AGENTS.md` §①），所以一律报 P50/P99 + 置信区间 + 样本量；`n < 100` 时 P99 区间必然很宽，报告必须如实呈现，**不得只看点估计**。

### 2. `eval/offline_tts.py`（离线替身适配器）

接口与 `adapters.tts_macsay.MacSayTts` **完全一致**（`synthesize(text, out_path, rate_key)` / `voice` / `model_version`；参数名与顺序照 `adapters/tts_macsay/adapter.py`，不得自创签名）：

```python
class OfflineTts:
    name = "offline-synthetic"          # 报告 env.adapter.name 会显示它
    synthetic = True                    # 关键标记：见第 4 节"不得美化"
    def __init__(self, *, voice: str, model_version: str,
                 ms_per_char: float = 70.0, rate_factor: dict | None = None): ...
    def synthesize(self, text: str, out_path, rate_key: str) -> Path
```

- 产出合法 WAV：16000 Hz / 单声道 / 16 bit（与 `runtime.audio.read_wav` 和包内资产同格式），否则运行时读不回来；
- 时长 = `max(1, len(text)) * ms_per_char * rate_factor[rate_key]`，`rate_factor` 缺省 `{"slow": 1.3, "normal": 1.0, "fast": 0.75}`；
- **必须确定性**：同样输入 → 逐字节相同的 WAV（禁随机数、禁时间戳进音频）；
- 空文本 → 抛 `ValueError`（消息含"空"），不得合成静音（与适配器层同规矩）。

### 3. `eval/bench.py`（harness）

```python
@dataclass(frozen=True)
class BenchConfig:
    pack: Any                  # AssetPack（assets.load_pack 装载的只读包）
    adapter: Any               # 注入的 TTS 适配器（真机 或 OfflineTts）
    corpus: tuple[dict, ...]   # 语料单位：{"key","text","rate","variant"}
    plan_id: str = "bench"
    repeats: int = DEFAULT_REPEATS
    warmup_runs: int = 1
    seed: int = DEFAULT_SEED
    required_hit_rate: float = 0.987
    out_dir: Path | None = None

def run_bench(config: BenchConfig) -> dict          # 返回报告 dict（不写盘）
def run_bench_cli(argv=None) -> int                 # CLI：写盘 + 打印摘要，返回退出码
```

**两臂构造（同一语料，必须严格对拍）：**

| 臂 | plan 单位 | Executor 参数 | 期望事件 |
|---|---|---|---|
| `fast` | `{"key": <key>, "rate": <rate>, "variant": <variant>}` | `allow_fallback=False`（fail-closed） | 每单位 `hit` |
| `slow` | `{"text": <text>, "rate": <rate>}`（即 `SAY_LIVE`） | `allow_fallback=True` | 每单位 `miss`，`reason == "say_live_text"` |

- 两臂用**同一个 pack 对象**、**同一个 adapter**（否则不可比）；`out_path` 一律写到 `out_dir/audio/` 下（缺省用 `tempfile`），**不得写进包目录**；
- `turn_id` 逐次变化（`f"{plan_id}-{arm}-{i:04d}"`），`plan_id` 带臂名，便于事件溯源；
- **预热**：每臂先跑 `warmup_runs` 次**不计入样本**（记进报告的 `warmup_runs`），避免首跑冷读盘污染 P99；
- 每次 `execute()` 采一条样本：`first_audio_ms`（取 `ExecutionResult`）、`tts_calls`、`synthesized_chars`（推导值，见口径）、`total_duration_ms`、`state_counts`、`hit_rate`、`precast_ratio`；
- **原始样本逐条落盘**（JSONL，一次一行）：`out_dir/raw/fast_samples.jsonl`、`out_dir/raw/slow_samples.jsonl`；每臂的事件流落 `out_dir/raw/fast_events.jsonl` / `slow_events.jsonl`（每条事件带臂名与序号）。报告里给出这些**指针**（相对路径）；
- 快路 fail-closed 抛 `RuntimeMissError` → **必须捕获并记入 `incomplete_reasons`**（原因含 key），不得静默跳过、不得改用 `allow_fallback=True` 掩盖；
- 任何一次执行抛异常 → 该次不计入样本，异常消息进 `incomplete_reasons`。

**指标口径（逐条写进报告 `caliber`，不得自创）：**

| 指标 | 公式 | 设计依据（写进报告） |
|---|---|---|
| `hit` / `miss` / `fallback` | 逐单元事件计数（字段名取 `core.metrics_spec` 的 `HIT`/`MISS`/`FALLBACK`） | `runtime/AGENTS.md` §④ |
| `hit_rate` | 命中单元数 / 总单元数 | `core.metrics_spec.HIT_RATE`；`docs/01` 判据 `ε ≤ 1 - p^(1/n)` |
| `precast_ratio` | Σ(命中单元对应包内音频帧数) ÷ 输出音频总帧数（含句间静音垫，`total_duration_ms` 换算） | `core.metrics_spec.PRECAST_RATIO` |
| `tts_calls` | `ExecutionResult.tts_calls`（运行时**实测**） | `runtime/AGENTS.md` §③.1 |
| `synthesized_chars` | Σ 非命中单元的文本长度 + Σ 槽值长度（**推导值，非 TTS 实测**，报告须标注） | 成本代理 |
| `first_audio_ms` | `ExecutionResult.first_audio_ms`（运行时**实测**） | `docs/05` §5.1（对标 sub-400ms） |

**确定性 vs 时序（本卡的关键切分，照 `eval/AGENTS.md` §③.1）：**

- `deterministic_metrics`：不含挂钟时间的量（`state_counts` / `hit_rate` / `precast_ratio` / `tts_calls` / `synthesized_chars`）。**同输入 + 同种子跑两次必须完全相等**（测试断言）；
- `timing_metrics`：`first_audio_ms` 的 `{n, p50, p99, max, ci95_p50, ci95_p99, unit:"ms", machine_dependent: true}`。**不同机器/负载下会变**，因此报告口径写明：可复现性只保证"分布口径与样本量、CI 方法一致"，不保证数值相等；
- 报告必须有 `timing_metrics_meaningful`：当 `adapter.synthetic is True`（离线替身）时 **必须为 `false`**，并在人读摘要里打一行显式警告——替身不产生真实合成耗时，**时序数字此时不得对外引用**（这是"不得美化"的硬约束）。

### 4. `eval/report.py`（报告组装，字段名冻结）

`build_report(...) -> dict` + `write_report(report, out_dir) -> Path` + 人读摘要 `render_summary(report) -> str`。

JSON 顶层字段（**可追加，不得改名或缺项**）：

```json
{
  "schema_version": "vox-eval-report/1",
  "generated_at": "<ISO 8601>",
  "command": "<可原样复现的完整命令行>",
  "seed": 20260917, "repeats": 30, "warmup_runs": 1,
  "incomplete": false, "incomplete_reasons": [],
  "env": {"platform": "...", "python": "...", "cpu_count": 8,
          "adapter": {"name": "...", "voice": "...", "model_version": "...", "synthetic": false},
          "pack": {"pack_version": "...", "voice": "...", "model_version": "...",
                   "asset_count": 6, "path": "..."}},
  "corpus": {"path": "...", "corpus_id": "demo-broadcast-v1", "units": 12, "sha256": "<前16位>"},
  "caliber": {"hit_rate": "<公式 + 设计依据>", "precast_ratio": "...", "tts_calls": "...",
              "synthesized_chars": "...", "first_audio_ms": "...",
              "quantile_method": "linear", "bootstrap_resamples": 2000,
              "reproducibility": "deterministic_metrics 逐值相等；timing_metrics 仅分布口径可复现"},
  "arms": {"fast": {"state_counts": {"hit": 12, "miss": 0, "fallback": 0},
                    "hit_rate": 1.0, "precast_ratio": 1.0,
                    "tts_calls": {"n": 30, "p50": 0.0, "max": 0.0},
                    "synthesized_chars": {"n": 30, "p50": 0.0, "max": 0.0}},
           "slow": {"...": "同结构"}},
  "delta": {"first_audio_ms_p50": 0.0, "first_audio_ms_p99": 0.0,
            "tts_calls_p50": 12.0, "synthesized_chars_p50": 0.0},
  "deterministic_metrics": {"fast": {"...": "..."}, "slow": {"...": "..."}},
  "timing_metrics": {"fast": {"n": 30, "p50": 1.2, "p99": 3.4, "max": 5.6,
                              "ci95_p50": [1.1, 1.4], "ci95_p99": [2.9, 4.1],
                              "unit": "ms", "machine_dependent": true},
                     "slow": {"...": "同结构"}},
  "timing_metrics_meaningful": true,
  "raw": {"fast_samples": "raw/fast_samples.jsonl", "slow_samples": "raw/slow_samples.jsonl",
          "fast_events": "raw/fast_events.jsonl", "slow_events": "raw/slow_events.jsonl"},
  "reference_gate": {"required_hit_rate": 0.987,
                     "source": "docs/01 判据 ε ≤ 1 - p^(1/n)（p=0.9, n=8）",
                     "observed_hit_rate": 1.0, "passed": true}
}
```

**不得美化（`eval/AGENTS.md` §③.3）——`incomplete` 判定规则（逐条实现）：**

只要出现下列任一情况 → `incomplete = true`，且 `incomplete_reasons` 每条写明**缺什么 + 哪个臂 + 哪个单位/样本**：

1. 任一臂的有效样本数 `< repeats`；
2. 任一臂出现 `RuntimeMissError`（快路 fail-closed 被触发）或有执行抛异常；
3. 事件流里有单位缺事件，或事件数 ≠ 语料单位数；
4. `raw/*.jsonl` 任一文件缺失或行数与样本数不符。

`incomplete = true` **不阻止**出报告（原始数据仍要落盘），但人读摘要第一行必须是 `[INCOMPLETE]` 前缀 + 原因。

### 5. 固定语料 `eval/corpus/demo_broadcast.json`

```json
{"corpus_id": "demo-broadcast-v1", "version": 1,
 "notes": "公开 demo 话术（报修登记表单流程），自造文本，无个人数据",
 "units": [{"key": "greeting_welcome", "text": "您好，这里是报修服务热线。",
            "rate": "normal", "variant": 0}]}
```

- **12 个单位**，覆盖 `slow`/`normal`/`fast` 三档至少各 2 条；
- 每条 `text` 是**一句**（不得出现两个及以上句末标点 `。！？!?`）——与 `compiler` 的源格式规矩一致；
- `key` 唯一、非空；**不得**出现任何真实人名、电话、地址、公司名（全部自造）。

### 6. CLI（T10 的 `vox bench` 会接这里）

```
python3 -m eval.bench --pack <包目录> --corpus eval/corpus/demo_broadcast.json \
    --out <输出目录> [--adapter <模块>:<类名>] [--repeats N] [--seed N] \
    [--required-hit-rate F] [--warmup N]
```

- `--adapter` 缺省用 `OfflineTts`（离线出报告用，`voice`/`model_version` 取包里的值；此时 `timing_metrics_meaningful=false`）；给 `--adapter adapters.tts_macsay:MacSayTts` 时用真机；
- 适配器**动态解析**（`importlib`），`eval/` 的静态 import 里**不得**出现 `adapters` / `compiler` / `rules`；
- `--repeats < MIN_REPEATS` → 立刻报错退出（非零退出码，**不写报告**），消息含实际值与 `MIN_REPEATS`；
- 参数错误（包不存在 / 语料缺失 / 单位为 0）→ 报错含路径或数值，非零退出码。

## 允许修改的文件（白名单）

```
允许新增：eval/__init__.py, eval/stats.py, eval/bench.py, eval/report.py, eval/offline_tts.py,
          eval/corpus/demo_broadcast.json,
          eval/tests/__init__.py, eval/tests/test_stats.py, eval/tests/test_bench.py,
          eval/tests/test_report.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、rules/**、assets/**、adapters/**、compiler/**、runtime/**、
          eval/AGENTS.md、docs/**、AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库；**禁止 numpy/pandas/scipy**——分位数与 bootstrap 自己写）；
- **不得改产品代码**（`eval/AGENTS.md` §⑤）：不得为了让数字好看而改口径、不得改 `core/metrics_spec.py`；
- 不得在 `eval/` 里静态 import `compiler/` `rules/` `adapters/`（层边界；适配器一律注入）；
- 不得把报告或音频写进仓库（`--out` 指定目录；测试一律 `tempfile`）；不得往包目录里写任何东西（只读）；
- 不得把挂钟时间当作"可复现指标"来断言，也不得对 `timing_metrics` 做"必须相等"的断言；
- 不得用替身适配器的时序数字冒充真机数据（`timing_metrics_meaningful` 必须如实为 `false`）；
- 不得"顺手优化"、不得改与本卡无关的格式；不得实现 `vox` CLI（那是 T10）。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s eval -v` **全绿**（我会贴实际计数）；
2. **离线**：`env PATH= /opt/homebrew/bin/python3 -m unittest discover -s eval` **仍然 OK**（测试全程不调真 TTS、不联网；不得靠 `skip` 掩盖）；
3. **真机对拍我手跑**（真 `say` 引擎 + 真预铸包）：`python3 -m eval.bench --pack <包> --corpus eval/corpus/demo_broadcast.json --adapter adapters.tts_macsay:MacSayTts --repeats 20 --out <临时目录>` → 报告满足：
   - `incomplete is False`；`arms.fast.state_counts.hit == 12`、`arms.fast.hit_rate == 1.0`、**每次样本 `tts_calls` 全为 0**；`precast_ratio` 落在 `(0, 1)` 内且**等于独立复算值**（见下）；
     > 口径更正（2026-09-17 验收时）：本卡初版此处写 `precast_ratio == 1.0`，**写错了**——冻结定义（`core.metrics_spec.PRECAST_RATIO` = 预铸时长 ÷ **全部**音频时长）的分母含句间静音垫，12 个单位之间有 11 个 200 ms 垫，全命中时也必然 < 1。实测 **0.941121**（命中资产 562632 帧 ÷ 输出 597832 帧），与实现逐位一致。**是卡数错，实现对**；验收改为"独立复算相等"。
   - `arms.slow.state_counts.miss == 12` 且每条事件 `reason == "say_live_text"`、`tts_calls == 12`（无槽位时）；
   - 两臂 `timing_metrics` 均有 `n == 20`、`p50`、`p99`、`ci95_p50`、`ci95_p99`，且 `delta.first_audio_ms_p50 > 0`（快路更快）；
4. **可复现**（我手跑）：同参数跑两次 → 两份报告的 `deterministic_metrics` **深度相等**（我会用脚本比 JSON）；`timing_metrics` 允许不同但必须都存在且带 CI；再用 `OfflineTts` 连跑两次 → `deterministic_metrics` 相等；
   > 口径裁定（2026-09-17 验收时）：执行方曾就 `percentile` 的索引基准提出异议（它改的是测试期望，不是实现）。裁定：**实现对**——`pos=(n-1)*q` + 0-based 索引 = 标准 R-7 线性插值（`[10,20,30,40,50]` q=0.3 → 20+0.2×10 = **22.0**），我用 numpy 2.4.4 逐例对照一致；早先那条期望 12.0 的测试是自己把位置当成了 1-based。卡文「位置 = (n-1)*q」本就不含歧义，故不改口径、不改实现。
5. **不得美化**（我手跑三例）：
   - `--repeats 5` → 非零退出、消息含 `5` 与 `20`、**不产生报告文件**；
   - 故意抽掉 `raw/fast_samples.jsonl` 的一行后重建报告 → `incomplete is True` 且 `incomplete_reasons` 含该文件名/样本编号；
   - 用只覆盖一部分 key 的包跑快路（fail-closed）→ `incomplete is True`、原因含缺失的 key；
6. **替身标记**：`OfflineTts` 跑出来的报告 `timing_metrics_meaningful is False`，且人读摘要含显式警告行；
7. **口径与设计依据**：报告 `caliber` 每条含公式 + 设计依据引用；`hit_rate`/`precast_ratio` 的字段名来自 `core.metrics_spec` 常量（我会 grep 核对：`eval/` 里不得出现 `"hit_rate"`/`"precast_ratio"`/`"first_audio_ms"` 这类**字面量**作为字典键——必须引用常量）；
8. **层边界**（我会 grep）：`grep -nE "^(from|import) +(compiler|rules|adapters)" eval/*.py` 为空；`eval/` 只 import `core` / `assets` / `runtime` 公共名 + 标准库；
9. 含方法级中文注释与关键步骤 WHY 注释（尤其：为何分两臂、为何切分确定性与时序、为何禁均值外推、为何固定 seed）；
10. 白名单之外无改动（我用 `git status --porcelain -uall` 核对）；`eval/AGENTS.md` 零 diff。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`run_bench` / `percentile` / `bootstrap_ci` / `build_report` / `write_report` / `OfflineTts.synthesize`），不得在测试文件内复制这些实现；
- **不得在 `eval/tests/` 里手搓 manifest 之外的捷径**：快路用例必须真的经 `runtime.Executor` 执行（我会在测试里核对事件确实来自运行时——事件字段齐备、`part` 从 1 起、`pack_version` 与包一致）；
- 断言必须**能失败**：至少覆盖「样本数不足」「样本缺失 → incomplete」「快路 fail-closed → incomplete」「替身 → timing_metrics_meaningful=false」「同 seed 两次 bootstrap 区间相同」「不同 seed 区间允许不同」六类；
- 负例必须断言**异常类型 + 消息含关键值**（数值、路径或 key 名），不得只写 `assertRaises(Exception)`；
- 不得 `except: pass`、不得 `assertTrue(True)`、不得用 `skipTest` 绕过任何本卡验收项。

## 本卡不做（留给后续卡）

真人盲测 / 拼接自然度（CER、MOS、接缝可检测性，见 `docs/05` §5.3）、双工质量（打断准确率/轮转延迟）、单位会话成本（token 计量）、`vox` CLI 命令本体（T10）、示例业务包（T09）。

## 回滚方式

```
cd （仓库根）
git clean -fd eval && git checkout -- eval/AGENTS.md
```

（本卡只新增 `eval/` 下的文件；`eval/AGENTS.md` 是已跟踪文件，若被误改用 `git checkout` 还原。）

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`，自带 Write/Edit 直接落地）。

**回落**（`vox-card-executor` 不可用时）：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T08-eval-离线对拍harness.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要往仓库里写计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，回落路径 `opencode run` / `sense-nova/sensenova-6.8-flash-lite`，第一轮被人工中断后 `-s` 续会话收尾，续跑 2727s）→ [x] 已回收 → [x] **验收通过**（10/10；两处口径经我独立复算裁定，见验收标准 3、4 的更正与裁定块）
