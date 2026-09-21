# T08b · eval：修审计查出的「不得美化」破口与部分失败失明（4 项）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡只有判据、复现命令与字段名，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发。

## 背景（卡的来源：源码级审计，不是策划拍脑袋）

T08 已验收（提交 `ce5d2f1`，122 条测试全绿）。按项目纪律「源码级审计是固定动作」，验收后调只读审查员 `blackiron-silent-failure-hunter` 扫了 `eval/`，报 **2 条 P1 + 5 条 P2**，每条都带**实测复现脚本**（在 `/tmp/vox-audit/`）。本卡修其中 **4 项**（2 条 P1 + 2 条 P2 里最咬人的两条）；其余三条（审计 #2/#3 之外的 #5/#6/#7 见文末「明确不修」）。

**共同点**：这几条都在 `eval/AGENTS.md §③.3「不得美化」` 与 T08 卡「任何一次执行抛异常 → 异常消息进 `incomplete_reasons`；`incomplete=true` 不阻止出报告」这两条契约上咬了洞。

**必读**（不得修改）：`eval/AGENTS.md`（§③.3 不得美化、§⑤ 依赖边界）、`docs/tasks/T08-eval-离线对拍harness.md`（原卡的验收标准与字段冻结表）、`core/metrics_spec.py`。

## 目标（四项修复，逐项给复现 → 期望）

### 修复 1（P1）· 部分失败不得让整轮对拍中止

**现状**：`eval/bench.py` 的 `_timing_block`（约 558–585 行）直接调 `stats.bootstrap_ci`，而 `bootstrap_ci` 在 `n < MIN_REPEATS` 时抛 `ValueError`；`_timing_block` 又跑在完整性判定**之前** → 只要任一臂有效样本落在 **1..19**（用 `--repeats 20` 时**单次**执行失败就够），`run_bench` 整体抛出 → **不写 `report.json`**，且 per-run 失败原文只存在内存里（`raw/` 里只有成功样本）→ 事后无法定位是哪次、什么错。CLI 退出码 2、stderr 只有一句「有效样本量 19 小于 MIN_REPEATS=20」，读起来像 `--repeats` 传错。

**复现**（审计脚本 `python3 -B /tmp/vox-audit/repro_partial_failure.py`，我验收时会重跑同类构造）：让某一臂的一次 `execute()` 抛错 → 现在抛 `ValueError: bootstrap_ci.samples: 有效样本量 19 小于 MIN_REPEATS=20`，`out/` 下**无 `report.json`**。

**期望（改后）**：
- `n < MIN_REPEATS` 时 `_timing_block` **不抛错**：返回 `{"n": n, "p50": None, "p99": None, "max": None, "ci95_p50": None, "ci95_p99": None, "unit": "ms", "machine_dependent": True}`；
- **照样写报告**：`incomplete=true`，`incomplete_reasons` 里既有「有效样本数 N < repeats」也有**每一条 per-run 失败原文**（含 `turn_id` 与异常类型+消息）；
- CLI：`stderr` 打印每条失败原文；退出码**非 0 但区分语义**：样本不完整 → 退出码 `2`（与"参数错误"合流可接受，但消息必须说明是"执行期失败导致样本不足"而不是"--repeats 传错"）。
- **人类摘要**首行仍是 `[INCOMPLETE]` + 原因（沿用既有规则）。

### 修复 2（P1）· 替身适配器的标记必须在报告层交叉校验

**现状**：`bench.py` 用 `getattr(adapter, "synthetic", False)` 取标记（约 675–676 行），而 `report.py` 的 `build_report` 只校验类型（约 228–256 行），**不比对** `env.adapter.synthetic` 与 `timing_metrics_meaningful`。审计实测：一个没打 `synthetic` 属性的替身适配器跑出来 `env.adapter.synthetic=false`、`timing_metrics_meaningful=true`、摘要 `[COMPLETE]` **无警告行** → **替身时序数字被报告背书为真机数据**（T08 卡 §224 明令禁止）。

**复现**：`python3 -B /tmp/vox-audit/repro_synthetic_marker.py`。

**期望（改后）**：
- `build_report` 增加**硬校验**：`env.adapter.synthetic is True` 时 `timing_metrics_meaningful` **必须**为 `False`，否则抛 `BenchReportError`（消息含两个字段名与实际值）——校验必须落在**函数体内**（不得只在测试里断言）；
- `bench.py` 判定「是否替身」改为**不依赖单一属性**：`adapter.synthetic is True` **或** `type(adapter).__module__.startswith("eval.")` 都算替身 → `meaningful=False`。这样"忘了打标记"的替身也拦得住；
- 加一条测试：构造一个**没有 `synthetic` 属性**的假适配器（放 `eval/tests/` 内，不改产品码）→ 报告 `timing_metrics_meaningful is False` 且摘要含 `[警告]`。

### 修复 3（P1，同属红线）· `precast_ratio` 不得夹取

**现状**：`eval/bench.py` 约 320–324 行用 `round(min(1.0, precast/total), 6)`，分母为 0 时写 `0.0`。审计实测：篡改 manifest 的 `duration_ms`（使分子 > 分母）后，**未夹取比值 1.396**，报告给出 `precast_ratio=1.0`、`incomplete=false`、无任何原因 —— 异常数据被静默改写成"满分"。

**复现**：`python3 -B /tmp/vox-audit/repro_clamp_value.py`。

**期望（改后）**：
- **去掉 `min(1.0, …)`**：比值 > 1 时**如实写出**（例如 `1.396`），并在 `incomplete_reasons` 追加一条（含实际比值、分子、分母）；
- 分母（输出总帧数）为 0 → `precast_ratio=None` + `incomplete_reasons` 追加一条（**不得写 0.0**，0.0 会被读成"没有任何预铸音频"）；
- 加两条测试：包元数据不一致 → 比值 > 1 且 `incomplete=true`；总帧数为 0 → `None` 且 `incomplete=true`。

### 修复 4（P2）· 补三类缺失的测试锚点（防回归）

**现状**：审计 #6 指出三处「承诺无测试锚定」：
1. **1..19 有效样本的部分失败路径**（正是修复 1 的触发域）无用例；
2. `OfflineTts` 承诺「同输入 → 逐字节相同 WAV」无用例（`bench` 的确定性子集不含时长，替身一旦引入随机/时间戳仍会全绿）；
3. `test_stats.py` 有 3 处、`test_report.py` 有 3 处负例只写 `with self.assertRaises(Type):` 且**块内无断言**。

**期望（改后）**：
- 新增用例：某一臂一次 `execute()` 抛错 → **仍产出报告**、`incomplete is True`、原因含该次 `turn_id` 与异常消息；
- 新增用例：`OfflineTts.synthesize()` 对同一 (text, rate_key) 调两次 → 两次产物 `read_bytes()` **相等**；
- 给那 6 处负例补**消息断言**（`assertIn("<关键字>", str(ctx.exception))`），关键字取该异常消息里真实存在的词（key 名/字段名/数值）。

## 允许修改的文件（白名单）

```
允许修改：eval/bench.py, eval/report.py,
          eval/tests/test_bench.py, eval/tests/test_report.py, eval/tests/test_stats.py
允许新增：eval/tests/test_offline_tts.py（若要单独立文件）
禁止触碰：其余一切文件（含 eval/stats.py、eval/offline_tts.py、eval/__init__.py、eval/corpus/**、
          eval/AGENTS.md、core/**、runtime/**、assets/**、compiler/**、docs/**、AGENTS.md）
```

> 注意：`eval/stats.py` **本卡不许改**（`bootstrap_ci` 在样本不足时报错是**正确**行为，要改的是调用方 `_timing_block` 的处置方式）。

## 禁止事项

- 不得新增第三方依赖（只用标准库）；不得引 numpy/pandas/scipy。
- **不得放宽既有校验强度**：`stats.bootstrap_ci`/`require_min_samples` 的拦截必须保留；`check_raw_on_disk` 的既有核对不得削弱。
- 不得用「把 `MIN_REPEATS` 调小」或「样本不足时静默返回 0.0」的方式让测试变绿（那是把红线挪开，不是修）。
- 不得改报告字段名（`schema_version` 与冻结字段表照 T08 卡）；本次只**追加**字段（如需，如修复 3 的原因文案）。
- 不得在报告层「顺手」加与审计无关的新指标。
- 不得改 `eval/AGENTS.md`（层契约），改它得走大版本 + 重跑历史基线。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s eval -v` **全绿**（T08 的 122 条只增不减；我会贴计数）；
2. `env PATH= /opt/homebrew/bin/python3 -m unittest discover -s eval` **仍 OK**（离线可跑不破）；
3. **修复 1 我手跑**：用 mock 让 slow 臂第 1 次 `execute()` 抛 `RuntimeError("注入的失败")` → 命令**正常退出并写出 `report.json`**；`incomplete is True`；`incomplete_reasons` 里能 grep 到 `注入的失败` 与那次 `turn_id`；`timing_metrics.slow.n == 19` 且 `p50 is None`（不是 0.0）；摘要首行 `[INCOMPLETE]`；
4. **修复 2 我手跑**：构造一个无 `synthetic` 属性的假适配器 → `timing_metrics_meaningful is False`、摘要含 `[警告]`；再直接调 `build_report(..., env.adapter.synthetic=True, timing_metrics_meaningful=True)` → **抛 `BenchReportError`**（消息含两个字段名）；
5. **修复 3 我手跑**：篡改包 manifest 的时长使分子 > 分母 → 报告 `precast_ratio > 1.0`（**不是 1.0**）且 `incomplete is True`、原因含分子/分母实际值；输出总帧数为 0 → `precast_ratio is None` 且 `incomplete is True`；
6. **修复 4 我手跑**：三处新增用例存在且**能失败**——我把修复 1/2/3 的改动分别还原一处（或注入同型故障）后，对应用例必须变红（我会至少验一处，做法与 T03d 相同：备份 → 注入 → 跑 → 还原 → `md5` 比对）；
7. **不得美化回归**：真机对拍（真 `say`）在**正常**路径下仍 `incomplete is False`、`precast_ratio` 与 T08 验收值一致（0.9411，±0.002），且 `timing_metrics_meaningful is True`；
8. 白名单之外无改动（`git status --porcelain -uall` 核对）；`eval/stats.py`、`eval/offline_tts.py` 零 diff；
9. 含方法级中文注释与 WHY 注释（改动的每一处都要说明「为什么这样改」，尤其是 `n < MIN_REPEATS` 时的降级路径）；
10. 卡内所有复现脚本在改后**不再复现**（我手跑审计给的三个脚本路径若还在，或等价构造）。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`run_bench` / `build_report` / `check_raw_on_disk` / `OfflineTts.synthesize`），不得在测试里复制判据逻辑；
- 新增的每个负例必须断言**异常类型 + 消息含关键值**（数值/key 名/字段名），不得只写类型；
- 断言必须**能失败**：三处新增用例都要给出"注入故障后变红"的实证（我会独立重做一遍）；
- 不得 `assertTrue(True)`、不得 `except: pass`、不得用 `skipTest` 绕过本卡任何验收项。

## 本卡明确不修（审计提了，留待后续卡，理由在此）

- 审计 #2（**CLI 错误分类**：内部缺陷被折叠成 exit 2，无类型名无 traceback）→ 与 T10（`vox` CLI 骨架）一起做，那时才有统一的错误码约定；
- 审计 #5（**raw 的值级篡改不被发现**：行数/index 都在，只把数值改了）→ 审计员自评"最可能误报"的一条，且 `raw` 的指纹入报告属报告 schema 扩展；留到 T10 `vox verify`（"对任意产物跑质检"）一并做；
- 审计 #7（`--out` 指到包目录无拦截）→ 属操作者误用防护，与 T10 的 CLI 参数校验一起做。

## 回滚方式

```
cd （仓库根）
git checkout -- eval/bench.py eval/report.py eval/tests/test_bench.py eval/tests/test_report.py eval/tests/test_stats.py
rm -f eval/tests/test_offline_tts.py
```

（本卡只改已入库的 `eval/` 文件 + 至多新增一个测试文件 → `git checkout` 即可回到 T08 验收态 `ce5d2f1`。）

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`；`thoughtLevel` 必须是 `enabled`，改过 agent 定义要新开会话）。

**回落**：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T08b-eval-审计修复.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要往仓库里写计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，回落路径 `opencode run`；**首轮 9 分钟零改动退出**，续跑约 22 分钟完成）→ [x] 已回收 → [x] **验收通过**（10/10；`eval` 122 → **129 条测试全绿**；两条「注入故障 → 新用例变红」我已独立重做）
