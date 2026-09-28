# T28b · trigger：`trigger.py` → `plan.py`（修 T28 迁移暴露的包/模块同名冲突）

## 背景（只写必需）

T28 迁移已落地（`adapters/state_trigger/` → `trigger/`，语义零变化），但执行方正确上报了一个**卡面判据缺口**：
顶层包 `trigger/` 与包内模块 `trigger.py` **同名**，Python 3.14 的 unittest discovery 下必然冲突
（`trigger` 测试根只发现 4 条且全 ERROR；在干净 HEAD 上复现过，非迁移手损）。

**验收方裁定（2026-09-19）**：包内模块改名 `trigger.py` → **`plan.py`**。理由：
1. 模块文件名**不是公开 API**——`from trigger import build_plan` 等全部导出符号（`trigger/__init__.py` 的
   `__all__`，25 个）零变化，外部使用方式不变；卡 T28 禁的是「重命名公开 API」，不覆盖此项；
2. `trigger.py` 的内容就是「`state → plan` 行为侧」（T17 卡原文），`plan.py` 名实相副；
3. 与包同名的模块是 Python 反模式（PEP 8 explicit relative import 相关讨论），迁移正好暴露它。

本卡是 T28 的收尾修订，范围**只动 `trigger/` 目录内部**，其余一切不动。

## 目标（可验收的产物）

1. `trigger/trigger.py` → `trigger/plan.py`（文件内容除本卡允许差异外与 T28 后现状逐字一致）；
2. 引用同步（全部在 `trigger/` 内）：
   - `trigger/__init__.py`：`from trigger.trigger import …` → `from trigger.plan import …`；
   - `trigger/tests/test_trigger.py`、`trigger/tests/test_ledger.py` 中对模块 `trigger` 的 import
     （T28 报告所指 `test_trigger:630`、`test_ledger:562` 附近）→ 改为显式 `from trigger import plan` /
     `import trigger.plan` 等等价形式，**断言与测试逻辑零改动**；
   - `trigger/AGENTS.md`、`trigger/README.md` 中提「`trigger.py`」的行 → 改为 `plan.py`；
3. `trigger/README.md` 的落点改写（T28 欠账，本卡授权）：标题行的旧包路径、落点说明段、
   旧测试命令（原 110–116 行一带）改为 `trigger/` 现状——**只改路径与命令字样，不改口径内容**。

## 允许修改的文件（白名单）

```
允许改名：trigger/trigger.py → trigger/plan.py
允许修改：trigger/__init__.py（仅 import 行）
允许修改：trigger/tests/test_trigger.py、trigger/tests/test_ledger.py（仅被改名模块的 import 行，断言零改动）
允许修改：trigger/AGENTS.md、trigger/README.md（仅模块名提法与落点/命令字样）
禁止触碰：其他一切文件（docs/、adapters/、labs/、根 README/AGENTS.md 全部不动）
```

## 禁止事项

- 不得改任何断言、测试方法名、公开 API 导出（`trigger/__init__.py` 的 `__all__` 25 个符号名单不变）；
- 不得动 T28 已完成的迁移结果（`adapters/state_trigger/` 删除状态、`adapters/AGENTS.md §⑧`、labs 三文件）；
- 不得 `git add` / `git commit`；不得顺手优化。

## 验收标准（逐条可判定）

1. **冲突消解（核心）**：`python3 -m unittest discover -s trigger` → **159 条全绿**（T28 现状是 4 条 ERROR）；
2. **数字守恒**：`discover -s adapters` → **152 条全绿**；十根合计 **1,089** 不变；
3. **负例**：`python3 -c "import trigger.trigger"` → 非零退出（ModuleNotFoundError，旧模块名已死）；
   `python3 -c "import trigger; print(len(trigger.__all__))"` → 输出 `25`（导出面无变化）；
4. **labs 回归**：`python3 labs/ticket-source/run_e2e.py` → rc 0（应不受影响，跑一遍确认）；
5. **diff 卫生**：`git status --porcelain -uall` 新增改动全部在白名单内；`plan.py` 与改名前 `trigger.py` 的
   内容差异仅限 import/文档字符串中的模块名提法（验收方逐行 diff）。

## 反空转条款

- 验收 1 的 159 条是本卡存在的意义——跑不到 159 全绿即整卡退回，不许用「4 条也测到了」交差；
- 测试文件的改动**只许**是被改名模块的 import 行，验收方会 diff 核对，多一行改动即退回。

## 回滚方式

`git clean -fd trigger` + `git checkout -- .` 前先记录：本卡只动未跟踪的 `trigger/`（T28 未提交），
回滚 = 恢复 T28 终态（验收方掌握 T28 终态快照）。执行方动手前照常记录 HEAD 与 status。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**——纯代码迁移收尾。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19；改名消解 discovery 冲突；遗留 `_REPO_ROOT` 与 labs 残留转 T28c）**

### 验收记录

- 同名冲突消解：`discover -s trigger` 从「4 条 ERROR」变为「159 条全部发现执行」（余 85 ERROR 系 T28 深度硬编码，T28c 收）；
- 导出面零变化：`len(trigger.__all__)==25`、别名解析正确（`trigger.plan.TURN_ID_FIELD=='turn_id'`）；
- `ledger.py:31` 的 1 行引用同步**裁定接受**（属 T28b「引用同步（全部在 trigger/ 内）」授权范围，卡面列举遗漏）；
- labs `run_e2e.py:260` 的 `trigger.trigger` 残留（T28 全域替换双义命中）超出本卡白名单，执行方停在卡内并上报 → T28c。
