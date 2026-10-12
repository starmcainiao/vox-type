# T89 · 核实：`--allow-fallback` 时 `tts_calls=1` 而 `fallback_count=0` 的口径（**只读，不改代码**）

> **本卡是只读调查卡**：只取证、只写报告，**不得修改任何产品代码**。
> 判定（是不是 bug、该改哪边）归验收方，不归执行方。

## 背景（只写必需）

实测（2026-10-12，见 `T84`）：对一条**未知 key** 的 plan 加 `--allow-fallback` 后：

```
$ sh bin/vox run /tmp/plan_bad.json --pack examples/prebuilt-pack \
      --adapter adapters.tts_omlx:OmlxTts --allow-fallback --out /tmp/bad_fb.wav
run: 通过（hit=0 miss=1 fallback=0 tts_calls=1 first_audio_ms=702.0 out=/tmp/bad_fb.wav）
{ … "hit_count": 0, "miss_count": 1, "fallback_count": 0, "tts_calls": 1, … }
events: [{ "key":"this_key_does_not_exist_zzz", "reason":"key_not_prebaked", "miss": true, … }]
```

**疑点**：产物 2408 ms 的真实音频、`tts_calls=1`（确实发生了一次合成），
但 `fallback_count` 报 **0**，且事件里只有 `miss: true`、没有 fallback 标记。

本仓纪律 4（`AGENTS.md §二.4`）：**禁止静默降级**——「任何"本该命中却换了路径"的情况必须报错**或留痕计数**」。
所以这里要么是"计数口径不同但合规"，要么是"计数漏了"。**这两种结论指向完全不同的修法，必须先查清。**

## 目标（可验收的产物）

- 产物 1：`labs/fallback-count-probe/README.md` —— 结论 + 复现命令 + 原始证据
- 产物 2：`labs/fallback-count-probe/raw/*.json` —— 每次实验的原始输出（原样落盘，不得手工编辑）

## 允许修改的文件（白名单）

```
允许新增：labs/fallback-count-probe/**（README.md、raw/ 下的原始输出、可选的一个复现脚本）
允许修改：无
禁止触碰：其他一切文件（尤其不得改 runtime/、eval/、cli/、core/、docs/、README.md）
```

## 要查清的四个问题（逐条给原始证据）

1. **口径定义**：读 `docs/08-CLI口径.md`、`core/metrics_spec.py`、`eval/` 里对
   `fallback_count` 与 `tts_calls` 的定义原文，**逐字摘录**（含文件路径与行号）。
   `fallback_count` 到底在数什么？`miss` 与 `fallback` 是互斥的两个状态，还是可以同时出现？
2. **代码路径**：`runtime/executor.py` 里 `fallback_count` 与 `tts_calls` 分别在哪个分支被 `+=1`？
   摘录那几行（含行号）。**未知 key + allow_fallback=True** 走的是哪一条分支？
3. **对照实验**（每条都要落原始 JSON）：
   - a) 未知 key + `--allow-fallback` → 记录 `hit/miss/fallback/tts_calls` 与完整 `events`；
   - b) 未知 key + **不带** `--allow-fallback` → 期望 rc=5，记录 `aborted` 原文；
   - c) **引擎不一致**（如对 `examples/prebuilt-pack` 省略 `--adapter`）+ `--allow-fallback`
     → 记录 `fallback_count` 是否非 0（这条用来区分「按 key 未命中」与「按引擎降级」两种语义）；
   - d) 再跑一次 a)，确认结果稳定（不是偶发）。
4. **定性（只给事实，不给"应该改哪"）**：
   - `fallback_count=0` 是**符合既有口径**，还是**与 `docs/08`/`metrics_spec` 的定义不符**？
   - 若是前者，写明是哪个口径条款；若是后者，写明**哪一条定义被违反**、以及
     「事件流里是否仍有足够留痕（能让下游还原降级）」这一问的答案。

## 禁止事项

- **不得修改任何产品代码**（本卡只读；改了 = 整卡退回）
- 不得新增第三方依赖
- 不得触碰本卡白名单以外的任何文件
- **不得下"这是 bug"的结论后顺手去改** —— 只写事实与判据，判定归验收方
- 原始输出**不得手工修饰或摘要替代**：`raw/` 下必须是命令的原样 stdout/stderr

## 验收标准（逐条可判定，验收方逐条核对）

1. `git status --porcelain` **只出现** `labs/fallback-count-probe/` 下的新增文件（无任何产品代码改动）；
2. README 里**四个问题逐条有答案**，且每条答案都带**文件路径 + 行号**或**原始输出文件名**；
3. `labs/fallback-count-probe/raw/` 下至少 **4 个**原始 JSON（对应问题 3 的 a/b/c/d）；
4. **可复现**：README 里的复现命令**逐条可跑**，验收方会**亲跑 a) 与 c)** 并比对数字；
   —— 若复现结果与 README 不一致，**整卡退回**；
5. 结论部分**不得出现**"应该改成……"这类修法建议（那不是本卡范围）。

## 反空转条款（每张卡必带，T01 教训）

- 不得用"我读了代码觉得是这样"代替实跑——**问题 3 的四条实验必须真跑并落原始输出**；
- 不得只跑一条实验就外推到全部（a 与 c 的语义不同，必须分别有数据）；
- 引用定义时必须**逐字摘录 + 给行号**，不得转述（转述会让下一个人无法复核）。

## 回滚方式

删除 `labs/fallback-count-probe/` 整个目录（本卡不触碰任何既有文件）。

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`。
**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T89-*.md)" --dir （仓库根）`。
本卡是**只读 + 摘录 + 实跑**，判据机械，**适合免费档**；走免费档须在报告里点名。

> 跑实验 c) 需要本机 TTS 端点在线（缺省 `http://127.0.0.1:10099`）；端点不可用时**如实在 README 写明**并
> 只交 a/b/d 三条，不得编造 c) 的结果。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
