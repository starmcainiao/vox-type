# T07 · runtime：执行器最小闭环（命中零调用 / fail-closed / 槽位拼接 / 事件流）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有执行语义、参数与事件字段规格，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发执行模型。

## 背景

`runtime/` 把一条 `plan` 变成声音，并**能短路下游**。本卡做**最小闭环**：命中判定 → 槽位现场合成 → 拼接 → 输出 WAV + **事件流**。
（真机播放/打断/ASR 侧耐心窗不在本卡；本卡的耐心窗只做参数化与可断言。）

**必读**（不得修改）：`AGENTS.md`、`runtime/AGENTS.md`、`docs/06-预铸与执行-补定口径.md`（**§6.2 全部为执行器口径，§6.2.7 是本卡专用补定**）。

依赖（只读其公开 API，**不得修改**）：
- `core/protocol.py`：`parse_plan` / `PlanUnit`（`action` / `key` / `text` / `rate` / `variant` / `slots`）
- `core/metrics_spec.py`：事件字段常量（**必须 import 这些常量，不许写字面量**）
- `assets/`：`load_pack` / `AssetPack.lookup(key, part_index, rate_key, variant, expected_text, engine_meta)` / `validate_pack` / `AssetPackError`
- `adapters/`：`MacSayTts` 的 `synthesize(text, out_path, rate_key)` / `voice` / `model_version`

**层边界（硬约束）**：`runtime/AGENTS.md §⑤` 规定 runtime **不得 import `compiler/`、`rules/`**。测试也不许——需要资产包时，在 `tempfile` 里**手搓**（`wave` 造音频 + `assets.fingerprint` 算指纹 + 按 `assets` 包格式写 `manifest.json`）。

## 目标（产物）

```
runtime/__init__.py        导出 DuplexParams / DuplexError / Executor / ExecutionResult /
                          RuntimeMissError / build_event / concat_wavs / max_sample_jump 等公共 API
runtime/duplex.py          DuplexParams（双工参数，含三档耐心窗）与校验
runtime/events.py          事件构造（字段名一律取自 core.metrics_spec）
runtime/audio.py           WAV 读取/拼接/静音垫/淡入淡出/写盘 + 峰值跳变（click）判据
runtime/executor.py        Executor：判定 → 合成/取资产 → 拼接 → 事件流
runtime/tests/__init__.py
runtime/tests/test_duplex.py, test_events.py, test_audio.py, test_executor.py
```

### 1. 双工参数（`runtime/duplex.py`）

```python
@dataclass(frozen=True)
class DuplexParams:
    patience_ms: int = 900          # 端点判断耐心窗：400 快语速 / 900 默认 / 1800 慢思考
    rate_band: float = 0.15         # 语速收敛带 ±15%
    backchannel: str = "on"         # on / off
    barge_in: str = "allow"         # allow / confirm
    silence_pad_ms: int = 200       # 段间静音垫（120–300）
    slot_pad_ms: int = 80           # 槽值前后微停顿（50–150）
    fade_ms: int = 5                # 段级淡入淡出（docs/06 §6.2.7 ④）
```

- 三个类方法给三档预设：`DuplexParams.fast()`（patience 400）、`DuplexParams.default()`（900）、`DuplexParams.slow_thinking()`（1800），其余字段取默认。
- `validate()`：`patience_ms` 必须是 400/900/1800 之一；`silence_pad_ms` 必须落在 **120–300**；`slot_pad_ms` 必须落在 **50–150**；`fade_ms` 必须 ≥ 0；`backchannel` ∈ {on, off}；`barge_in` ∈ {allow, confirm}。违反 → `DuplexError`（消息含字段名与实际值，**不得回落**）。
- 构造时（`__post_init__`）就校验，非法值不许被静默接受。

### 2. 事件（`runtime/events.py`）

`build_event(...) -> dict`，键名**只能**用 `core.metrics_spec` 的常量（`TS`/`TURN_ID`/`PLAN_ID`/`KEY`/`PART`/`RATE`/`VARIANT`/`FIRST_AUDIO_MS`/`PACK_VERSION`），结果三态用 `HIT`/`MISS`/`FALLBACK` 常量，原因用 `REASON` 常量。

- `PART` = **同一 turn 内 plan 单元的执行序号，从 1 开始**（不是资产的 `part_index`，别混）。
- 每个 plan 单元产**恰好一条**事件（含未命中/降级），`reason` 取值照 `docs/06 §6.2.7 ②` 的表。
- 事件里 `variant` 记**实际选中的值**（`auto` 必须已解析成整数）。

### 3. 音频工具（`runtime/audio.py`）

- `read_wav(path) -> (samples: array('h'), framerate)`；非 16k/单声道/16-bit → 抛 `AudioError`（**不得重采样、不得静默接受**）。
- `silence(ms, framerate) -> array('h')`、`apply_fade(samples, fade_ms, framerate)`（线性淡入淡出）。
- `concat_wavs(segments, path, *, framerate, fade_ms)`：按顺序拼接并写盘（16k/单声道/16-bit）。
- `max_sample_jump(samples) -> int`：相邻样本最大绝对差，**click 判据**（阈值由测试给定）。

### 4. 执行器（`runtime/executor.py`）

```python
@dataclass
class ExecutionResult:
    events: list[dict]
    output_path: Optional[Path]
    hit_count: int; miss_count: int; fallback_count: int
    tts_calls: int              # 本次执行真正调用 TTS 的次数（命中路径应为 0）
    first_audio_ms: float
    total_duration_ms: int

class Executor:
    def __init__(self, pack, adapter, *, duplex=None, allow_fallback=False): ...
    def execute(self, plan, *, plan_id, turn_id, out_path) -> ExecutionResult: ...
```

判定顺序（**逐单元**，`part` 从 1 开始）：

1. **引擎一致性前置检查**：`pack.voice` / `pack.model_version` 与 `adapter.voice` / `adapter.model_version` 不一致 → 该单元按 `fallback`（`engine_mismatch`）处理，**不得播包内音频**（`docs/06 §6.2.3`）。
2. `PlanUnit.text`（`SAY_LIVE`）→ `miss`（`say_live_text`），走现场合成。
3. `PlanUnit.key`：
   - 解析 `variant`（`auto` → 按 `(turn_id, part)` 稳定散列在包内可用 variant 里选，`docs/06 §6.2.7 ③`）；
   - 取 `part_index`：按 0,1,2… 升序探测（本轮包只产 0，但要对未来兼容）；
   - 用 `assets.AssetPack.lookup(key, part_index, rate_key, variant, expected_text=单元文本, ...)` 取资产；`expected_text` 从包内条目文本取（见下"实现提示"）；
   - 命中且校验通过 → `hit`，播包内音频；
   - 未命中要**区分三种原因**（`key_not_prebaked` → `miss`；`fingerprint_mismatch` / `audio_file_missing` → `fallback`），**必须自行判定原因**（`lookup` 只返回 `None`，不告诉你为什么）：包内查 `(key, part, rate, variant)` 是否有候选条目、候选的 `text` 重算指纹是否匹配、音频文件是否存在。
4. **槽位**：`PlanUnit.slots` 非空时，**槽值一律现场合成**（`docs/06 §6.2.5`），前后垫 `slot_pad_ms`，与框架音频拼接；命中路径因此**允许有 TTS 调用**（每槽一次），但**只有槽位**会调——这不算破"命中零调用"（见验收 2 与 3 的区分）。
5. **fail-closed**：出现 `miss`/`fallback` 且 `allow_fallback is False` → **抛 `RuntimeMissError`**（消息含 key 与该单元的原因），**中止整条 plan**、**不写输出文件**、不得播别的话术。
6. `allow_fallback is True` → 走现场合成（`SAY_LIVE` 与降级单元都用 `adapter.synthesize`），事件照常产出 `miss`/`fallback`。
7. **拼接**：所有片段按顺序拼接，片段之间垫 `silence_pad_ms`，每段施 `fade_ms` 淡入淡出，输出到 `out_path`（16k/单声道/16-bit）。
8. `first_audio_ms`：从 `execute()` 进入第一条音频可用的耗时（毫秒，`time.perf_counter` 测）。

**实现提示（避免卡壳）**：`AssetPack.lookup` 需要 `expected_text` 才能校验指纹，而 plan 单元里只有 `key`。做法：**先从包内按 `(key, part, rate, variant)` 找到候选条目，取其 `text` 作为 `expected_text` 再调 `lookup`**——这样"指纹不匹配"就只会在包自身损坏（包内文本与其指纹不符）时出现，正好对应 `fingerprint_mismatch` 语义。

## 允许修改的文件（白名单）

```
允许新增：runtime/__init__.py, runtime/duplex.py, runtime/events.py, runtime/audio.py,
          runtime/executor.py, runtime/tests/__init__.py, runtime/tests/test_duplex.py,
          runtime/tests/test_events.py, runtime/tests/test_audio.py, runtime/tests/test_executor.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、rules/**、assets/**、adapters/**、compiler/**、docs/**、runtime/AGENTS.md、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- **不得 import `compiler/` 或 `rules/`**（层边界硬约束，测试也不许）。
- **不得在实现里写字面量字段名**（事件键必须取自 `core.metrics_spec`）。
- 不得做语义分析 / 意图识别（判定只用确定性路由 + 包内查表）。
- 不得重采样、不得静默接受非法 WAV。
- 不得改动 `assets/`、`adapters/`、`core/` 的任何文件（它们是只读依赖）。
- 不得往仓库里写音频（测试用 `tempfile`）；不得改判据数值（照 `docs/06`）。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s runtime -v` **全绿**；无 `say` 环境下依赖真合成的用例 `skip` 而非失败；
2. **命中即零调用**：全命中且**无槽位**的 plan → `result.tts_calls == 0`，输出 WAV 存在且时长 ≈ 各段之和 + 垫子；
3. **命中 + 槽位**：带 1 个槽位的命中单元 → `tts_calls == 1`（**只有槽值现场合成**），且**包目录内任何文件都不含该槽值字符串**（我 grep 包目录核对）；
4. **fail-closed**：让 plan 引用包内不存在的 key，`allow_fallback=False` → 抛 `RuntimeMissError`（消息含该 key）、**输出文件不存在**；同一 plan 在 `allow_fallback=True` 下正常产出且事件含 `miss`；
5. **三种未命中原因可区分**（我各构造一例，断言事件里的 `reason`）：
   - 包内无该组合 → `miss` + `key_not_prebaked`；
   - 包内条目文本被改过（指纹不符）→ `fallback` + `fingerprint_mismatch`；
   - 包内条目音频文件删掉 → `fallback` + `audio_file_missing`；
6. **音色/模型版本不一致**：包与 adapter 的 `voice` 不同 → `fallback` + `engine_mismatch`，且**不得播放包内音频**（我会核对输出音频不是包内那条）；
7. **事件字段**：每个单元恰好一条事件、`part` 从 1 递增；键名全部来自 `core.metrics_spec`（我 grep 核对无字面量字符串键）；
8. **`variant="auto"`**：同一 `(turn_id, part)` 两次执行选到**同一** variant；用一批不同 `turn_id` 能覆盖到包内两个变体（我实测）；事件里 `variant` 是整数不是 `"auto"`；
9. **拼接与 click**：段间确有 `silence_pad_ms` 静音（我按样本数核对）；`max_sample_jump` 在拼接产物上不超过测试给定阈值；
10. **DuplexParams**：三档预设的 `patience_ms` 分别为 400/900/1800 且用例各一；非法值（`patience_ms=1000`、`silence_pad_ms=50`、`slot_pad_ms=10`、`backchannel="maybe"`、`barge_in="x"`）→ 构造即抛 `DuplexError` 且消息含字段名与实际值；
11. 含方法级中文注释与关键步骤 WHY 注释；`git status --porcelain -uall` 仅白名单文件。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`Executor.execute` / `DuplexParams` / `concat_wavs` / `max_sample_jump`），不得在测试内复制判定或拼接逻辑；
- 测试**不得 import `compiler/`**（要包就手搓：`wave` 造音频 + `assets.fingerprint` 算指纹 + 写 `manifest.json`）；
- 负例必须断言**异常类型 + 消息含关键值**（key 名 / 字段名 / reason），不得只写 `assertRaises(Exception)`；
- 断言必须**能失败**：第 4/5/6/10 条各自的用例喂的是**确实违规**的输入；
- 不得 `except: pass`、不得 `assertTrue(True)`、不得用 `skipTest` 绕过（真合成用例除外）。

## 回滚方式

```
cd （仓库根）
git clean -fd runtime && git checkout -- runtime/AGENTS.md
```

（本卡只新增 `runtime/` 下的文件；`runtime/AGENTS.md` 已跟踪，若被误改用 `git checkout` 还原。）

## 执行方式

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T07-runtime-执行器最小闭环.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，sense-nova/sensenova-6.8-flash-lite，62m，中途限流一次后 -c 续跑）→ [x] 已回收 → [x] 验收通过（提交 f2d23de）
- 边界留档：`fingerprint_mismatch` 在正常路径为防御性分支（`load_pack` 装载期已校验同一对值），真实危险由编译期兜——见 `docs/06 §6.2.2` 的「可达性说明」
