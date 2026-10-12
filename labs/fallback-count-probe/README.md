# fallback-count-probe · `--allow-fallback` 时 `tts_calls=1` 而 `fallback_count=0` 的口径取证

> **本目录是 T89 的只读调查产物**：只取证、只摘录、只实跑，**未修改任何产品代码**。
> 判定（是不是 bug、该改哪边）归验收方，本目录只给事实与判据。
>
> **数据改写留痕（验收方，2026-10-13）**：`raw/_rc-and-stderr.txt` 的 4 处 `cmd:` 行里原有
> **本机绝对路径**（命中发布树红线 `tools/check_no_machine_paths.py`），已按 `AGENTS.md` 二.4
> 「不静默改数据」的处置口径**占位化为 `<仓库根>`**（照 T59 对 `labs/frontpack-value/` 的先例）。
> **被改的只有那 4 个路径字面量**：`rc`、`stderr` 正文、四条实验的全部数值**一字未动**，
> §0 那张表的证据价值不受影响。

## 0. 一句话事实

同一条**未知 key**的 plan，在**引擎一致**时得到 `miss=1 / fallback_count=0 / tts_calls=1`，
在**引擎不一致**时得到 `miss=0 / fallback_count=1 / tts_calls=1`。
两者 `tts_calls` 都是 1（都真合成了一次），但 `fallback_count` 相差 1——
该计数器按**单元状态是不是 `FALLBACK`**计数，与「有没有发生现场合成」无关。

| 实验 | 条件 | rc | hit | miss | fallback | tts_calls | 事件里的 state | reason | 原始文件 |
|---|---|---|---|---|---|---|---|---|---|
| a) | 未知 key + `--allow-fallback`（omlx，引擎一致） | 0 | 0 | 1 | **0** | 1 | `"miss": true` | `key_not_prebaked` | `raw/a-unknown-key-allow-fallback.json` |
| b) | 未知 key + **不带** `--allow-fallback` | **5** | 0 | 0 | 0 | 0 | `events` 为空数组 | `key_not_prebaked`（在 `aborted` 字段里） | `raw/b-unknown-key-fail-closed.json` |
| c) | **省略 `--adapter`**（缺省 macsay → 引擎不一致）+ `--allow-fallback` | 0 | 0 | 0 | **1** | 1 | `"fallback": true` | `engine_mismatch` | `raw/c-engine-mismatch-allow-fallback.json` |
| d) | 复跑 a)（确认稳定） | 0 | 0 | 1 | **0** | 1 | `"miss": true` | `key_not_prebaked` | `raw/d-unknown-key-allow-fallback-rerun.json` |

a) 与 d) 的 `hit/miss/fallback/tts_calls/reason` **逐值相同**（稳定，非偶发）；
逐次变化的只有挂钟量（`ts` / `first_audio_ms` / `total_duration_ms` / `out_path`），见 §3.5。

---

## 1. 问题一：口径定义（逐字摘录）

### 1.1 三态的冻结定义 —— `core/metrics_spec.py:15-25`

```python
15: # 快路命中——plan 单元命中预铸资产，直接返回预铸音频
16: HIT: str = "hit"
17:
18: # 快路未命中——plan 单元无对应预铸资产，需降级到慢路
19: MISS: str = "miss"
20:
21: # 降级回退——原应命中但因校验/版本/一致性等原因被迫走慢路
22: FALLBACK: str = "fallback"
23:
24: # 降级原因——自由文本，记录 miss/fallback 的具体原因（如"资产版本不匹配"）
25: REASON: str = "reason"
```

### 1.2 CLI `--json` 输出形状 —— `docs/08-CLI口径.md:70`

> 6. 成功 → `0`，`--json` 输出 `{"command":"run","hit_count":5,"miss_count":1,"fallback_count":0,"tts_calls":1,"first_audio_ms":…,"total_duration_ms":…,"out_path":"…","events":[…]}`（事件字段名照 `core.metrics_spec` 常量，**不得改名**）。

**这条即卡面疑点的直接对照物**：规格自己给的示例就是
`miss_count=1` 与 `fallback_count=0`、`tts_calls=1` **同时出现**。

### 1.3 三态与原因的映射（策划裁定） —— `docs/06-预铸与执行-补定口径.md:191`、`:195-204`

```
191: - 结果三态的定义（照 `metrics_spec` 注释）：`HIT` = 命中预铸资产；`MISS` = **无对应预铸资产**需走慢路；`FALLBACK` = **原应命中**但因校验/版本/一致性原因被迫走慢路。
```

| 情形 | 结果 | `reason` | 行号 |
|---|---|---|---|
| key 单元，资产命中且音色/模型版本一致 | `hit` | —（空） | `docs/06:197` |
| key 单元，包内无该 `(key, part, rate, variant)` 组合 | `miss` | `key_not_prebaked` | `docs/06:198` |
| plan 单元是自由文本（`SAY_LIVE`，设计上就没有资产） | `miss` | `say_live_text` | `docs/06:199` |
| 组合存在但指纹不匹配（话术改了没重铸） | `fallback` | `fingerprint_mismatch` | `docs/06:200` |
| 组合存在、指纹匹配，但音频文件缺失 | `fallback` | `audio_file_missing` | `docs/06:201` |
| 包与引擎的 `voice`/`model_version` 不一致 | `fallback` | `engine_mismatch` | `docs/06:202` |

```
204: **这三个 `fallback` 原因就是跨批待办 ③ 要的"可区分且留痕"**——不许合并成笼统的"未命中"。
```

### 1.4 执行器自述的字段口径 —— `runtime/executor.py:66-73`

```python
66:        events:           逐单元事件列表（每个单元恰好一条，含未命中/降级）
67:        output_path:      输出 WAV 路径
68:        hit_count:        命中单元数
69:        miss_count:       未命中单元数
70:        fallback_count:   降级单元数
71:        tts_calls:        本次执行真正调用 TTS 的次数（纯命中无槽位时为 0）
```

注意 `miss_count`（:69）与 `fallback_count`（:70）是**两个并列的独立字段**，
不是同一个东西的两种叫法。

### 1.5 本层验收条件 —— `runtime/AGENTS.md:22-24`

```
22: 1. **命中即零调用**：命中路径的 TTS 调用次数 = 0（测试断言）；
23: 2. **fail-closed**：未命中且未显式开启降级 → **抛错中止**（不得静默换路、不得播别的话术）；
24: 3. **降级必留痕**：开启降级时，每次降级都产生事件（含 key、原因、时间），且计入指标；
```

第 ① 条把 `tts_calls` 的约束系在**命中路径**上（命中 → 0），
第 ③ 条把「每次降级都产生事件且计入指标」系在**降级**上——
两处都没有把「未命中走慢路」写成必须计入 `fallback_count`。

### 1.6 eval/ 侧的口径原文

`eval/report.py:90-93`（`tts_calls` 口径）：

```python
90:    "tts_calls": (
91:        "tts_calls = ExecutionResult.tts_calls（运行时**实测**计数，只计成功调用；"
92:        "纯命中且无槽位时为 0）。设计依据：runtime/AGENTS.md §③.1 命中即零调用"
93:    ),
```

`eval/report.py:104-108`（`state_counts` 口径）：

```python
104:    "state_counts": (
105:        "state_counts = 单条 plan 内逐单元事件计数，键为 core.metrics_spec 的 "
106:        "hit/miss/fallback 常量。设计依据：runtime/AGENTS.md §④ 事件字段。"
107:        "报告取首样本值；样本间不一致即标 incomplete（确定性被破坏）"
108:    ),
```

`eval/bench.py:310-317`（三个计数器如何累加）：

```python
310:    state_counts: Dict[str, int] = {HIT: 0, MISS: 0, FALLBACK: 0}
311:    for event in result.events:
312:        if event.get(HIT):
313:            state_counts[HIT] += 1
314:        elif event.get(MISS):
315:            state_counts[MISS] += 1
316:        elif event.get(FALLBACK):
317:            state_counts[FALLBACK] += 1
```

`eval/bench.py:337`（命中率只按 hit 数算，miss 与 fallback 同样拉低命中率）：

```python
337:        HIT_RATE: round(state_counts[HIT] / units, 6) if units else 0.0,
```

### 1.7 对问题一的直接回答

**`fallback_count` 在数什么**：数「**本应命中**（包里有这条 key 的组合）但因校验/版本/一致性等原因
被迫走慢路」的单元数，即三态里 `FALLBACK` 那一态的单元数（`runtime/executor.py:365`）。
它**不数**「包内根本没有这条 key」而现场合成的单元——那是 `MISS`（`docs/06:198`）。
它也**与**「有没有发生 TTS 调用」无关——`tts_calls` 是另一条独立的口径
（`eval/report.py:90-93`：数成功调用次数）。

**`miss` 与 `fallback` 是不是互斥？** 分两层：

- **单元级：互斥，且是结构性的**。`_UnitDecision.state` 是单个 `str` 字段
  （`runtime/executor.py:96`），事件构造只写**一个**三态布尔位
  （`runtime/events.py:133-134`）：

  ```python
  133:    # 三态占独立一位：键就是 metrics_spec 的 HIT / MISS / FALLBACK 常量
  134:    event[state] = True
  ```

  `runtime/events.py:115-119` 另有一条硬约束（非 hit 必须带 reason）：

  ```python
  115:    # 留痕红线：miss / fallback 必须带原因，禁止"没命中但不知道为什么"
  116:    if state != HIT and not reason:
  117:        raise EventError(
  118:            f"state={state!r} 必须带非空 reason（禁止留痕缺失），实际为 {reason!r}"
  119:        )
  ```

  实测印证：a) 的事件只有 `"miss": true`、**没有** `fallback` 键；
  c) 的事件只有 `"fallback": true`、**没有** `miss` 键（见 §3.1 / §3.3 原始 JSON）。

- **plan 级：可以同时非零**（不同单元各自一态），而且 `fallback_count=0` 与
  `miss_count≥1` 同时出现是规格示例里的**正常形状**（`docs/08:70`，见 §1.2）。

---

## 2. 问题二：代码路径（哪几行 `+=1`，未知 key 走哪条分支）

### 2.1 两个计数器的产出位置

**`fallback_count`** —— 一次遍历三个计数器并列产出，`runtime/executor.py:363-366`：

```python
363:            hit_count=sum(1 for d in decisions if d.state == HIT),
364:            miss_count=sum(1 for d in decisions if d.state == MISS),
365:            fallback_count=sum(1 for d in decisions if d.state == FALLBACK),
366:            tts_calls=tts_calls,
```

即 `fallback_count` **没有 `+=1` 的分支**，它是「终态 unit 里 `state == FALLBACK` 的条数」。

**`tts_calls`** —— 在 `synth()` 闭包里成功合成后 `+=1`，`runtime/executor.py:249-256`：

```python
249:        def synth(text: str, rate_key: str):
250:            """现场合成一段音频并读回样本；tts_calls 只计成功调用。"""
251:            nonlocal tts_calls, seq
252:            seq += 1
253:            target = tmpdir / f"live_{seq:03d}.wav"
254:            self.adapter.synthesize(text, target, rate_key)
255:            tts_calls += 1
256:            return read_wav(target)[0]
```

### 2.2 谁会调 `synth()` —— 判据是「有没有包内条目」，不看三态

`runtime/executor.py:264-284`：

```python
264:                if decision.entries:
265:                    # 命中：直接读包内音频（part_index 升序），零 TTS 调用
266:                    for entry in decision.entries:
267:                        samples, _ = read_wav(self.pack.root / entry.path)
268:                        if first_audio_at is None:
269:                            first_audio_at = time.perf_counter()
270:                        segments.append(samples)
271:                else:
272:                    # SAY_LIVE / 降级：现场合成
273:                    samples = synth(decision.text, rate_key)
...
281:                for slot_value in decision.unit.slots.values():
282:                    segments.append(silence(self.duplex.slot_pad_ms, SAMPLE_RATE))
283:                    segments.append(synth(slot_value, rate_key))
284:                    segments.append(silence(self.duplex.slot_pad_ms, SAMPLE_RATE))
```

分支条件是 `decision.entries` 空不空。`MISS` 与 `FALLBACK` 两种 decision
都不带 `entries`（`runtime/executor.py:449-464` 与 `:467-474` 只在命中时填 `entries`），
所以**两者都落到 :273 的 `synth()`**，`tts_calls` 都 +1。
（:283 是槽位合成，命中单元也可能因槽位而有 TTS 调用。）

### 2.3 「未知 key + `allow_fallback=True`」走的那一条分支

1. `_decide_unit` 逐单元判定（`runtime/executor.py:381`）→ 引擎一致 → 未过期 → 进包内查表；
2. `_diagnose` 查表无候选 → `runtime/executor.py:506-508`：

   ```python
   506:        candidate = self._find_candidate(key, part_index, rate_key, variant)
   507:        if candidate is None:
   508:            return MISS, REASON_KEY_NOT_PREBAKED, None
   ```

3. 回到 `_decide_unit`，`entry is None` → `runtime/executor.py:457-464`：

   ```python
   457:            if entry is None:
   458:                # 只可能发生在 part_index == 0：包内根本没有这个组合
   459:                return _UnitDecision(
   460:                    part=part, unit=unit, state=MISS,
   461:                    reason=REASON_KEY_NOT_PREBAKED, variant=variant,
   462:                    text=unit.key, requested_rate=requested_rate,
   463:                    rate_fallback=rate_fallback,
   464:                )
   ```

   → **state = `MISS`**（不是 `FALLBACK`），`entries` 为空、`text = unit.key`。
4. `allow_fallback=True` 时 fail-closed 闸**跳过**（`runtime/executor.py:224-228`）：

   ```python
   224:        # fail-closed：判定全部完成、尚未合成/落盘，此时中止才不会留下半截输出
   225:        if not self.allow_fallback:
   226:            for decision in decisions:
   227:                if decision.state != HIT:
   228:                    raise RuntimeMissError(self._abort_message(decision))
   ```

   注意该闸的判据是 `decision.state != HIT`——`MISS` 与 `FALLBACK` **都**被拦。
5. 合成阶段：没有 `entries` → `:273` 调 `synth()` → `tts_calls = 1`（`:255`）。
6. 汇总：`:364` 计入 `miss_count=1`；`:365` 因 `state != FALLBACK` 而 **不**计入。

**一句话**：未知 key 这条路把 `tts_calls` 抬到 1，但 `state` 落在 `MISS` 而非 `FALLBACK`，
所以 `fallback_count` 停在 0。两个计数器读的是**不同的东西**，不矛盾。

### 2.4 对照：`engine_mismatch` 那条分支（实验 c 走的就是它）

`runtime/executor.py:425-434`（在查表**之前**，音色/模型版本不一致即判 `FALLBACK`）：

```python
425:        if (
426:            self.pack.voice != self.adapter.voice
427:            or self.pack.model_version != self.adapter.model_version
428:        ):
429:            return _UnitDecision(
430:                part=part, unit=unit, state=FALLBACK,
431:                reason=REASON_ENGINE_MISMATCH, variant=variant,
432:                text=self._candidate_text(unit.key, 0, rate_key, variant) or unit.key,
433:                requested_rate=requested_rate, rate_fallback=rate_fallback,
434:            )
```

这条路 `state = FALLBACK` → 同样落到 `:273` 的 `synth()`（`tts_calls=1`），
但 `:365` 会把它计入 → `fallback_count=1`。**这正是 a) 与 c) 的唯一差别**：
同一条未知 key、同样合成一次，`fallback_count` 差 1。
（附一条代码路径事实：:432 在候选为 `None` 时回落到 `unit.key`，
即 c) 这次现场合成的文本是 key 字符串本身。事件流里不含 `text` 字段，
这一点只能从代码路径读出，不在 JSON 里。）

---

## 3. 问题三：对照实验（全部实跑，原始输出原样落盘）

### 3.0 复现命令（逐条可跑）

前置：本机 TTS 端点在线（缺省 `http://127.0.0.1:10099`，供 a/b/d 用 omlx 合成），
且 `/usr/bin/say` 可用（供 c 用 macsay 合成）。
一条命令跑全部四条（stdout 原样落 `raw/*.json`，rc + stderr 落 `raw/_rc-and-stderr.txt`）：

```sh
# 仓库根
sh labs/fallback-count-probe/run.sh
```

四条也可各自单跑（注意：`--out` 指向自己的临时文件，别共用同一个输出路径）：

```sh
# a) 未知 key + --allow-fallback
sh bin/vox run labs/fallback-count-probe/plan_unknown_key.json \
  --pack examples/prebuilt-pack --adapter adapters.tts_omlx:OmlxTts \
  --allow-fallback --out /tmp/t89-a.wav --json ; echo "RC=$?"

# b) 未知 key + 不带 --allow-fallback（期望 rc=5）
sh bin/vox run labs/fallback-count-probe/plan_unknown_key.json \
  --pack examples/prebuilt-pack --adapter adapters.tts_omlx:OmlxTts \
  --out /tmp/t89-b.wav --json ; echo "RC=$?"

# c) 引擎不一致（省略 --adapter → 缺省 adapters.tts_macsay:MacSayTts）+ --allow-fallback
sh bin/vox run labs/fallback-count-probe/plan_unknown_key.json \
  --pack examples/prebuilt-pack --allow-fallback \
  --out /tmp/t89-c.wav --json ; echo "RC=$?"

# d) 复跑 a)
sh bin/vox run labs/fallback-count-probe/plan_unknown_key.json \
  --pack examples/prebuilt-pack --adapter adapters.tts_omlx:OmlxTts \
  --allow-fallback --out /tmp/t89-d.wav --json ; echo "RC=$?"
```

plan 文件 `labs/fallback-count-probe/plan_unknown_key.json` 内容（单一未知 key）：

```json
[
  {
    "key": "this_key_does_not_exist_zzz"
  }
]
```

> 实验 c) 依赖 CLI 的 `--adapter` 缺省值，来源 `cli/commands/__init__.py:34`：
> `DEFAULT_ADAPTER_SPEC: str = "adapters.tts_macsay:MacSayTts"`。
> 该 adapter 的 `voice = "Tingting"`（`adapters/tts_macsay/adapter.py:37`）、
> `model_version = "macos-say"`（:36），与 `examples/prebuilt-pack/manifest.json` 的
> `voice="default"` / `model_version="mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit"` 不一致
> → 命中 :425-434 的 `engine_mismatch` 分支。

### 3.1 实验 a —— 未知 key + `--allow-fallback`（rc=0）

原始文件：`raw/a-unknown-key-allow-fallback.json`（stdout 原样）。
计数与事件（逐字）：

```json
  "hit_count": 0,
  "miss_count": 1,
  "fallback_count": 0,
  "tts_calls": 1,
  "total_duration_ms": 2568,
  "events": [
    {
      "key": "this_key_does_not_exist_zzz",
      "part": 1,
      "rate": "normal",
      "variant": null,
      "reason": "key_not_prebaked",
      "pack_version": "1",
      "first_audio_ms": 2689.264709013514,
      "miss": true
    }
  ]
```

stderr（`raw/_rc-and-stderr.txt:6`）：

```
run: 通过（hit=0 miss=1 fallback=0 tts_calls=1 first_audio_ms=2689.3 out=/private/tmp/vox-t89-wav.88v0Cw/a.wav）
```

事件里**只有** `"miss": true`，没有 `fallback` 键；`tts_calls=1` 与产物时长
`total_duration_ms=2568`（真音频，非 0 长）一致。

### 3.2 实验 b —— 未知 key + **不带** `--allow-fallback`（rc=5，符合期望）

原始文件：`raw/b-unknown-key-fail-closed.json`（stdout 原样）。

```json
  "hit_count": 0,
  "miss_count": 0,
  "fallback_count": 0,
  "tts_calls": 0,
  "first_audio_ms": 0,
  "total_duration_ms": 0,
  "out_path": null,
  "events": [],
  "aborted": "plan 单元 #1 未命中（key='this_key_does_not_exist_zzz'，reason=key_not_prebaked）：allow_fallback=False → 中止整条 plan（fail-closed：不写输出文件、不播其他话术）"
```

stderr（`raw/_rc-and-stderr.txt:12-13`）：

```
run: 已按 fail-closed 中止（plan 单元 #1 未命中（key='this_key_does_not_exist_zzz'，reason=key_not_prebaked）：allow_fallback=False → 中止整条 plan（fail-closed：不写输出文件、不播其他话术））
已按 fail-closed 中止（退出码 5）
```

**这里记录一条中止路径的事实**：`events` 是**空数组**，计数器全为 0，
中止信息只落在 `aborted` 一个文本字段里（key + reason + `allow_fallback` 取值齐全）。
零值来自 `cli/commands/run.py:149-163`（中止分支自造 payload，不是执行器产物）。

### 3.3 实验 c —— 引擎不一致 + `--allow-fallback`（rc=0，`fallback_count` 非 0）

原始文件：`raw/c-engine-mismatch-allow-fallback.json`（stdout 原样）。

```json
  "hit_count": 0,
  "miss_count": 0,
  "fallback_count": 1,
  "tts_calls": 1,
  "total_duration_ms": 3229,
  "events": [
    {
      "key": "this_key_does_not_exist_zzz",
      "part": 1,
      "rate": "normal",
      "variant": null,
      "reason": "engine_mismatch",
      "pack_version": "1",
      "first_audio_ms": 742.2671669628471,
      "fallback": true
    }
  ]
```

stderr（`raw/_rc-and-stderr.txt:19`）：

```
run: 通过（hit=0 miss=0 fallback=1 tts_calls=1 first_audio_ms=742.3 out=/private/tmp/vox-t89-wav.88v0Cw/c.wav）
```

**这条是区分两种语义的关键数据**：同一个未知 key，`tts_calls` 与 a) 同为 1，
但因为引擎不一致在查表前就被判成 `FALLBACK`，`fallback_count` 就成了 1、
`miss_count` 成了 0。也就是说 `fallback_count` 记的是「引擎/校验类降级」，
不记「按 key 未命中」。

### 3.4 实验 d —— 复跑 a)（确认稳定）

原始文件：`raw/d-unknown-key-allow-fallback-rerun.json`（stdout 原样）。

```json
  "hit_count": 0,
  "miss_count": 1,
  "fallback_count": 0,
  "tts_calls": 1,
  "total_duration_ms": 2516,
```

事件同样是 `"reason": "key_not_prebaked"` + `"miss": true`，无 `fallback` 键。

与 a) 逐值比对（a 的 `total_duration_ms=2568` / d 的 `2516`）：

| 字段 | a) | d) | 稳定？ |
|---|---|---|---|
| `hit_count` | 0 | 0 | ✅ 稳定 |
| `miss_count` | 1 | 1 | ✅ 稳定 |
| `fallback_count` | 0 | 0 | ✅ 稳定 |
| `tts_calls` | 1 | 1 | ✅ 稳定 |
| `events[0].reason` | `key_not_prebaked` | `key_not_prebaked` | ✅ 稳定 |
| `events[0].miss` | `true` | `true` | ✅ 稳定 |
| `total_duration_ms` | 2568 | 2516 | ⚠️ 每次不同（挂钟） |
| `first_audio_ms` | 2689.26… | 1333.56… | ⚠️ 每次不同（挂钟） |
| `events[0].ts` | `…16:22:10Z` | `…16:22:12Z` | ⚠️ 每次不同（挂钟） |
| `out_path` | 临时目录 | 临时目录 | ⚠️ 每次不同（脚本用 mktemp） |

### 3.5 验收方亲跑时的比对口径（哪些数该一致、哪些本就会变）

- **应当逐值一致**：`hit_count` / `miss_count` / `fallback_count` / `tts_calls` /
  `events[*].key|part|rate|reason|miss|fallback|pack_version`。
- **本就会变**（不是偶发差异）：`first_audio_ms`、`total_duration_ms`、`events[*].ts`、
  `out_path`（`run.sh` 用 `mktemp -d /tmp/vox-t89-wav.XXXXXX`）。
- **rc 应一致**：a) 0 / b) 5 / c) 0 / d) 0。
- **端点依赖**（本卡四条全部实跑成功，2026-10-13 00:22 +0800，端点返回 200）：
  - **a) / d)** 需要 oMLX HTTP 端点（缺省 `http://127.0.0.1:10099`）；
  - **b)** **不需要** TTS：fail-closed 闸在合成阶段之前触发
    （`runtime/executor.py:224-228` 位于 `:243` 「② 合成与拼接阶段」之前），端点不在时仍能出 rc=5；
  - **c)** 走 macsay 的本地 `/usr/bin/say`，不依赖 HTTP 端点，但需要该可执行文件存在。

---

## 4. 问题四：定性（只给事实，不给"应该改哪"）

### 4.1 `fallback_count=0` 是符合既有口径，还是与定义不符？

**与本仓既有口径一致**，判据是下面这几条（逐条给出处）：

| 判据 | 出处（行号） | 原文 |
|---|---|---|
| ① `FALLBACK` 的定义只覆盖「原应命中但因校验/版本/一致性被迫走慢路」 | `core/metrics_spec.py:21-22` | 「降级回退——原应命中但因校验/版本/一致性等原因被迫走慢路」 |
| ② 「包内没有该 key 组合」被明确映射为 `miss`，不是 `fallback` | `docs/06-预铸与执行-补定口径.md:198` | 表格行：key 单元，包内无该 `(key, part, rate, variant)` 组合 → `miss` / `key_not_prebaked` |
| ③ CLI 规格自带的成功输出示例就是 `miss_count=1, fallback_count=0, tts_calls=1` | `docs/08-CLI口径.md:70` | 见 §1.2 逐字摘录 |
| ④ 实现按 `state == FALLBACK` 计，与①②③同形 | `runtime/executor.py:365` | `fallback_count=sum(1 for d in decisions if d.state == FALLBACK),` |
| ⑤ 本层验收条件把「每次降级都产生事件且计入指标」系在**降级**上，未把「未命中走慢路」写入 `fallback_count` | `runtime/AGENTS.md:24` | 「**降级必留痕**：开启降级时，每次降级都产生事件（含 key、原因、时间），且计入指标」 |

也就是说：`tts_calls=1` 记的是「确实合成了一次」（`eval/report.py:90-93` 的口径），
`fallback_count=0` 记的是「没有发生引擎/校验类降级」。两个数字描述**不同的事实**，
不是同一个事实的两次不一致报告。

**未发现**任何条款规定「发生现场合成的单元必须计入 `fallback_count`」；
`docs/08` / `core/metrics_spec` / `runtime/AGENTS.md` / `eval/report.py` 四处口径均无此要求。

### 4.2 「事件流里是否仍有足够留痕（能让下游还原降级）」——分三种情况给事实

| 情况 | 留痕字段（实测自原始 JSON） | 能否还原「这条没走快路」 |
|---|---|---|
| a) 未知 key + `--allow-fallback` | `events[0]` 含 `key`、`part`、`rate`、`variant`、`reason="key_not_prebaked"`、`pack_version`、`ts`、`first_audio_ms`、`miss=true` | 能：`miss=true` + `reason=key_not_prebaked` + `key` 三者齐全，且 `tts_calls=1` 佐证发生过现场合成 |
| b) 未知 key + 不开降级（中止） | `events` 为**空数组**；留痕只在 `aborted` 文本里：`key='this_key_does_not_exist_zzz'`、`reason=key_not_prebaked`、`allow_fallback=False` | 能（但**只在 `aborted` 字段**，事件流为空数组；本条 plan 的逐单元事件流不存在） |
| c) 引擎不一致 + `--allow-fallback` | `events[0]` 含 `key`、`part`、`reason="engine_mismatch"`、`pack_version`、`ts`、`first_audio_ms`、`fallback=true` | 能：`fallback=true` + `reason=engine_mismatch` 齐全，且 `fallback_count=1` 计入指标 |

补充事实（关于下游消费口径，均出自源码，未做改动）：

- 下游若按 `fallback_count` 这一**单个标量**判断「本次执行有没有降级」，
  则 a) 这类「按 key 未命中」的降级**不会**被它计入；能还原该事实的是
  `miss_count`（=1）与 `events[*].reason`（= `key_not_prebaked`）。
- `eval/` 侧不读 `fallback_count` 标量：它按事件流逐条数三态
  （`eval/bench.py:310-317`），且命中率只按 `state_counts[HIT] / units` 算
  （`eval/bench.py:337`）——a) 这类单元照样拉低 `hit_rate`，不会漏出统计。
- 事件流三态位结构上互斥且非 hit 必带 reason（`runtime/events.py:115-119`、`:133-134`），
  「本该命中却换了路径」的两类（`key_not_prebaked` / `engine_mismatch`）都带**互不相同**的
  `reason` 值，可区分（与 `docs/06:204`「不许合并成笼统的未命中」一致）。
- 另有两类状态**取 `MISS` 而非 `FALLBACK`**，代码里写明了理由，属既定口径而非遗漏：
  过期资产（`runtime/AGENTS.md` §⑨「状态取 MISS 而非 FALLBACK：过期内容不允许现场合成播出」）
  与 critical 超带（`runtime/AGENTS.md` §⑧）。

### 4.3 判据清单（供验收方裁定，本卡不提修法）

1. `fallback_count` 的定义条款：`core/metrics_spec.py:21-22` + `docs/06:191` + `docs/06:195-204`；
2. 「未命中走慢路」是否属于该定义：按现有条款，**不在**（`docs/06:198` 映射到 `miss`）；
3. CLI 输出形状：`docs/08:70`（其示例与本次实测形状逐字段同形）；
4. 实现侧计数：`runtime/executor.py:363-366`（`fallback_count` 无 `+=1` 分支，按终态统计）；
5. 留痕充分性：见 §4.2 三行表（a/c 有事件 + reason；b 只有 `aborted`、`events` 为空）。

---

## 5. 产物清单与回滚

```
labs/fallback-count-probe/
├── README.md                                  ← 本文件（结论 + 摘录 + 复现命令）
├── plan_unknown_key.json                      ← 单一未知 key 的 plan（实验共用）
├── run.sh                                     ← 复现脚本（一条命令跑 a/b/c/d）
└── raw/
    ├── a-unknown-key-allow-fallback.json          ← a) stdout 原样
    ├── b-unknown-key-fail-closed.json             ← b) stdout 原样
    ├── c-engine-mismatch-allow-fallback.json      ← c) stdout 原样
    ├── d-unknown-key-allow-fallback-rerun.json    ← d) stdout 原样
    └── _rc-and-stderr.txt                        ← 四条的 rc + stderr 原样
```

- `raw/*.json` 是命令 stdout 的**原样落盘**（`>` 重定向，未经任何加工）；
  `--json` 模式下 stdout 是纯 JSON（人读摘要在 stderr，见 `docs/08-CLI口径.md:39`）。
- 复现脚本把 WAV 写到 `mktemp -d` 的临时目录，**不入仓**。
- **回滚**：`rm -rf labs/fallback-count-probe/`（本卡未触碰任何既有文件）。
- **未修改任何产品代码**；本卡只新增本目录内容。

---

## 6. 工作树基线（供验收方核对「无产品代码改动」）

本卡执行前后 `git status --porcelain` 的差异（HEAD = `4f3aafd`）：

**本卡新增（全部在本卡白名单内）**

```
?? labs/fallback-count-probe/
```

**本卡未触碰、进入本卡时已存在的改动（属其他在飞卡 T85–T88，非本卡产物）**

```
 M README.md
 M adapters/mcp_vox/README.md
 M adapters/mcp_vox/tools.py
 M docs/22-五分钟跑起来.md
 M examples/selftest.sh
?? adapters/mcp_vox/tests/test_pack_check_hint.py
?? docs/27-对外口径与接入三步.md
?? docs/tasks/T84-…  T85-…  T86-…  T87-…  T88-…  T89-…
```

执行本卡期间，该基线里的 `tools/structure_budget/LEDGER.md` 与
`tools/structure_budget/snapshot.json` 不再出现在 `git status` 中（mtime 2026-10-13 00:21:27，
两者现与 HEAD 逐字节一致）。本卡的文件写入**全部发生在
`labs/fallback-count-probe/` 内**（mtime 00:21–00:23），未对这两个文件发起过任何写操作——
变化来源在本卡之外，此处如实记录，不作归因。