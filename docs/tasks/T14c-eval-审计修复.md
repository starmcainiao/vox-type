# T14c · eval + adapters：T14 审计修复（1 阻塞 + 1 P1 + 2 P2）

## 背景（只写必需）

T14 验收通过后按固定动作调 `blackiron-silent-failure-hunter` 扫回读 CER 三件套，
报 **1 阻塞 + 1 P1 + 2 P2**（全部带 CLI 级复现；阻塞项已由验收方亲手复现）：

1. **〔阻塞〕替身识别被 truthy 非 True 击穿**（`eval/readback.py:252,263`）：
   适配器写 `synthetic = 1`（类型笔误）→ 报告 `"synthetic": false`、摘要 `[COMPLETE]`、**rc 0**
   ——替身数据被当真机背书。**验收方已复现**（stub `synthetic=1` 全链路）。这与 T08b 在 bench 上
   修掉的缺陷同类：判定必须 fail-safe，不能依赖「恰好写了对的 True」。
2. **〔P1〕CLI 存在冻结表之外的 rc=1 出口，`_CLI_ERRORS` 定义了没用**（`eval/readback.py:682`）：
   manifest 非 UTF-8 / `--asr` 模块导入抛错 → 裸 traceback + rc 1。冻结退出码集合是 {0,2,3,4,5}；
   `bench.py:961→1001` 同场景归 rc 2——两个 harness 口径不一致。
3. **〔P2〕raw 指纹报警双计**（`readback.py:540-543` 与 `584-587`）：
   `check_raw_on_disk` 在指针计算与写盘各跑一次，1 条问题 → 报告 2 条、摘要「2 项原因」。
4. **〔P2〕适配器读阶段异常逃出 AsrError**（`adapters/asr_omlx/adapter.py:190,203`）：
   body 传输中断（`IncompleteRead` / `ConnectionResetError` / 裸 `TimeoutError`）与
   `--timeout nan/inf`（校验放过、urlopen 内裸 ValueError）都不在异常分类内，违反模块自订
   「一律抛 AsrError」契约（当前 harness 宽 except 兜住，对未来的直接消费者是分类缺口）。

## 目标（可验收的产物）

- 修 1：替身判定改 fail-safe——`synthetic` 属性存在 → `bool()` 归真；**真引擎必须显式
  `synthetic = False` 才按真机**（`OmlxAsr` 已显式声明，不变）；属性缺失或任何 truthy 值
  → 一律按替身处理（`synthetic=true` + 警告 + rc 5）。对齐 T08b 的「未知适配器偏保守」。
- 修 2：`run_readback_cli` 顶层真正使用 `_CLI_ERRORS`：可归类的用法/输入错误 → **rc 2**、
  运行期错误 → **rc 3**（stderr 带异常类型名，对齐 `AGENTS.md §四` 与 bench 同款）；消除 rc 1 出口。
- 修 3：`write_readback` 不再重复追加指纹问题（接收已算好的 issues，或先核对后变更——二选一，
  保证同一条问题在 report.json 里只出现一次）。
- 修 4：适配器把读阶段异常（`IncompleteRead` / `ConnectionResetError` / `TimeoutError` /
  `http.client.HTTPException` / `OSError`）统一归入 `AsrError`（消息含阶段与原因）；
  `timeout` 校验拒绝 NaN/inf（非有限正数 → `AsrError`/构造期报错）。

## 允许修改的文件（白名单）

```
允许修改：eval/readback.py
允许修改：adapters/asr_omlx/adapter.py
允许修改：eval/tests/test_readback.py（只允许新增用例；既有用例不得删改）
允许修改：adapters/asr_omlx/tests/test_adapter.py（同上）
禁止触碰：其他一切文件——尤其 eval/cer.py、eval/bench.py / report.py / stats.py、adapters/state_trigger/
```

## 禁止事项

- 不得改 CER 数值口径与归一化（`eval/cer.py` 零 diff）
- 不得改 bench/report/stats 的既有行为（对齐只允许 readback 向 bench 看齐，不许反向）
- 不得新增第三方依赖；不得"顺手优化"

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s eval` 与 `discover -s adapters` 全绿，两边**只增不减**（191+1 / 301 基线）；
2. **修 1 注入实测（验收方复现同款）**：stub `synthetic = 1` → 报告 `synthetic: true`、
   摘要含 `[警告]`、**rc 5**；`synthetic = "yes"` 同罪；显式 `synthetic = False` 的非 adapters/
   适配器 → 按真机（`synthetic: false`）；`OmlxAsr` 行为不变；
3. **修 2 两复现归位**：manifest 非 UTF-8 → rc 2（stderr 含异常类型名，非裸 traceback）；
   `--asr` 指向导入即抛错的模块 → rc 2；`_CLI_ERRORS` 从「定义未用」变为真实消费（grep 可见）；
4. **修 3 实测**：制造「指针计算后、写盘前改 raw」的窗口（或等价注入）→ report.json 中
   同一条指纹问题**只出现一次**，rc 仍 4；
5. **修 4 负例**：mock 读阶段抛 `IncompleteRead` / `ConnectionResetError` → 均 `AsrError`；
   `--timeout nan` / `inf` → 构造期或调用期报错，绝不进 urlopen 才炸；
6. **真机无回归（验收方亲跑）**：fin-cs 3 条 manifest → oMLX 真机 → `incomplete=False`、
   `synthetic=false`、CER 逐值与 T14 验收记录一致（0.0）；
7. `git status --porcelain -uall` 恰为白名单 4 文件。

## 反空转条款（每张卡必带）

- 新增测试必须调用产品 API / CLI 入口；注入用例的期望值不许从被测逻辑复制；
- 不得为通过测试而放宽既有防线（修 1 只许更严，不许更松）。

## 回滚方式

`git checkout -- eval/readback.py adapters/asr_omlx/adapter.py eval/tests/test_readback.py adapters/asr_omlx/tests/test_adapter.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19）
  - 验收方独立复跑：eval 208 / adapters 311 全绿（192+16 / 301+10）；**阻塞探针重放**——`synthetic=1` 修前 rc 0/判真机 → 修后 **rc 5、synthetic=true、警告在摘要**；非 UTF-8 manifest → **rc 2、零裸 traceback**；`eval/cer.py` 及 bench/report/stats 零 diff；**真机回归** rc 0、CER 3/3 = 0.0、`synthetic=false` 不变。
  - **裁定（4 条执行方取舍，全部接受）**：① rc 断言在替身注入下为 4/5 并集（fail-closed 优先，语义正确）；② 「非 adapters/ 显式 False=真机」与「eval 包内永远保守判替身」并存——保守端优先，正确；③ 去重落在 `write_readback` 侧（同题只报一次，两处核对保留）；④ manifest 读取移进 try 块（先报 asr 错，无行为回归）。
