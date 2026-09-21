# T32 · 扩展区卫生：包顶导入统一 + fetch 成功数下限断言

## 背景（只写必需）

三审计员全仓对齐审计（第十六批，`docs/tasks/00-索引.md`）在**扩展区**查出两类卫生问题（冻结区部分另随 §三）：

1. **公开面绕行**（`docs/13 §五#18`）：符号都在下层 `__all__` 里，但走了**内部模块路径**——trigger 自己违反
   自己 `AGENTS.md §⑤-1` 的反例写法（`from core.protocol import …`）；framework_kefu 同理（`§⑨` 已授权
   runtime/assets 公开面，但路径写法应统一为包顶导入）。
2. **fetch 成功数无下限断言**（`docs/13 §五#16`）：`tools/corpus_fetch/fetch.py` 全量跳过场景
   「成功 0 / 拒绝 0」→ exit 0——R09 验收 5（跑批脚本必须断言成功数）不满足。

**本卡只动扩展区**（trigger/ 与 adapters/framework_kefu/ 与 tools/）；cli→eval 的绕行（`cli/commands/bench.py:38`
的 `load_corpus` 未导出）与 eval 导出面补充**不动**（都在冻结区，随 §三）。

## 目标与修法（逐条给定，不许扩大）

1. **trigger 三文件内部路径改包顶导入**（符号均在 `core/__init__.py` 的 `__all__`，**只改 import 行**）：
   - `trigger/format.py:34,35`：`from core.protocol import …` / `from core.metrics_spec import …` → `from core import …`
   - `trigger/plan.py:47,48`：同上
   - `trigger/ledger.py:29`：同上
   - 若 `core/__init__.py` 的 `__all__` 实际缺某符号（对照检查）→ **停手报告**，不自行补导出（core 冻结区）。
2. **framework_kefu 两文件内部路径改包顶导入**（符号均在 runtime/assets `__all__`；`§⑨` 已登记该授权）：
   - `bridge.py:29`（`runtime.events.build_event`）、`:31`（`runtime.executor.Executor`）、`:32`（`assets.pack.AssetEntry`）
     → `from runtime import …` / `from assets import …`；
   - `hit_query.py:25`：`from assets.pack import AssetEntry` → `from assets import AssetEntry`；
   - bridge.py 若因 `build_event` 走包顶导入出现**循环导入**（runtime 反向依赖？先实测）→ 停手报告本行，
     其余照改。
3. **fetch 成功数下限断言**：`tools/corpus_fetch/fetch.py` 在「全部源都被跳过（成功 0）」时：
   - 缺省 → **exit 非 0**（fail-closed），错误消息含「全部跳过——若确为预期请显式 --allow-all-skipped」；
   - 新增 `--allow-all-skipped` 开关：显式允许时才 exit 0（并在 stderr 打 WARNING 留痕）；
   - 新增/扩展 `tools/corpus_fetch/tests/` 测试两条：全跳过无开关 → 非零退出且消息含开关名；带开关 → rc 0 + 警告。
4. **AST 级语义零变化自证**：改完的每个 import 行，改动前后被导入符号集合完全相同（报告里贴对照）。

## 允许修改的文件（白名单）

```
允许修改：trigger/format.py、trigger/plan.py、trigger/ledger.py（仅 import 行）
允许修改：adapters/framework_kefu/bridge.py、hit_query.py（仅 import 行）
允许修改：tools/corpus_fetch/fetch.py（仅成功数断言 + --allow-all-skipped 开关 + 用法串）
允许新增：tools/corpus_fetch/tests/ 下的新测试文件（若该目录已有测试，扩其文件亦可）
禁止触碰：其他一切文件（尤其 core/ eval/ cli/——冻结区）
```

## 禁止事项

- 不得改任何逻辑/断言/方法名；不得动 `core/__init__.py`；不得顺手重构；
- 不得 `git add` / `git commit`。

## 验收标准（逐条可判定）

1. **十一根全绿**（计数与基线一致：core 44 / rules 21 / assets 59 / adapters 188 / compiler 168 /
   runtime 118 / eval 208 / cli 85 / tools 75+新增 / trigger 159 / packs 17）；
2. `grep -rn "from core\.\|from runtime\.\|from assets\." trigger/ adapters/framework_kefu/ --include="*.py"`
   → **0 命中**（测试目录也一并核对；若测试内有故意引用私有名做断言的，**停手报告**不自行改）；
3. fetch 全跳过负例实测：构造全跳过场景（可用 `--only` 指向不存在平台或 mock）→ 无开关非零退出、
   带 `--allow-all-skipped` rc 0 + WARNING（执行方贴实际输出）；
4. tools 测试计数 = 75 + 新增数（报告里贴）且全绿；adapters/trigger 计数不变；
5. `git status --porcelain -uall` 改动全部在白名单内。

## 反空转条款

- import 改动的验收 = 「既有测试全绿且计数不变」的机器证明 + 符号集合对照（不得只 grep 通过就交）；
- fetch 的负例测试必须真实调用 CLI 入口（不 import 内部函数绕开退出码）。

## 回滚方式

`git checkout -- trigger/ adapters/framework_kefu/ tools/corpus_fetch/`（均为已跟踪文件）。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19，第十七批）**

### 验收记录（验收方复核 + 裁定 4 条）

- **生产代码内部路径 0 命中**（验收方亲跑；`bridge.py:30` 系卡面漏列，验收方补改一行——
  符号集合不变）；**十一根全绿**：core44/rules21/assets59/adapters188/compiler168/runtime118/eval208/cli85/tools**84**/trigger159/packs**22**；
- **裁定**：① `bridge.py:30` 属卡漏列，验收方补改（一行机械）；② **测试目录豁免**（测试非生产面；
  `METRIC_FIELDS` 不在 core `__all__`——**core 有语义公开的常量未导出，登记 docs/13 §五**，冻结区问题本卡不动）；
  ③ `exit_code_for`（FetchError→3/ArgumentError→2/其它→5）**接受**——原 rc 1+裸 traceback 破坏 CLI 退出码契约（与 T15 同族）；
  ④ 执行方顺带修的 `DEFAULT_CORPUS_ROOT` 别名确实是**验收方路径中性化引入的回归**（`--help` 直接 NameError），修复接收；
- **fetch 负例实测**：全跳过无开关 → rc 3 且消息含 `--allow-all-skipped`；带开关 → rc 0 + WARNING（均不走 fake，真 CLI 入口）；
- **packs 计数说明**：22 条 / 13 skip（yaml 默认邻居路径不在本机时诚实 skip）——设
  `KEFU_HEAT_YAML=<kefu yaml 路径>` 后仅 2 skip（产物缺席）其余真跑全绿，**同源防线在本机有效**；
  跑本仓全量测试建议带该 env（README/tests 注记项）。
