# T28 · trigger：`state_trigger` 升独立扩展区 `trigger/`（契约迁移，语义零变化）

## 背景（只写必需）

2026-09-19 用户拍板（`docs/13 §八#15`）：`adapters/state_trigger/` 升为**独立扩展区 `trigger/`**（自带七项 AGENTS.md）。
依据：`state → plan` 是「决定说什么」，属**接缝之上**（`docs/10 §10.1`），不属 adapters 的引擎形态；
`adapters/AGENTS.md §⑧` 的暂住条款明言这是权宜，且禁了第二个同类模块——迁移后解禁。

**本卡是纯迁移：文件内容逐字不动（除 import 路径与两处授权改写），行为零变化。**
不拆 `format.py`（945 行/去注释 663 行）——新层 AGENTS.md 按现状登记行数口径（与 `compiler/source.py` 312、
`compiler/script.py` 373 同类），拆分留待另卡（留口子优先于大重构）。

迁移基线事实（验收判据的数字依据）：
- `adapters/state_trigger/`：`format.py` 945 行、`trigger.py` 363、`ledger.py` 337、`reachability.py` 132、
  `__init__.py` 76、`README.md`、`tests/`（4 个测试文件，**159 条**测试全绿）；
- 代码引用方（`*.py`）只有 3 处：`labs/ticket-source/run_e2e.py`、`to_state.py`、以及包自身 `__init__.py`；
  产品层（core/runtime/assets/eval/cli/compiler/rules/packs/tools）**零引用**；
- 迁移后预期：`trigger` 新测试根 159 条全绿；`adapters` 降为 **152** 条全绿；十根总数 **1,089 不变**。

## 目标（可验收的产物）

1. 新顶层扩展区 `trigger/`：`__init__.py`、`format.py`、`trigger.py`、`ledger.py`、`reachability.py`、
   `README.md`、`tests/`（4 文件）——自 `adapters/state_trigger/` 迁入，**内容逐字一致**（见验收 1 的例外）；
2. 新 `trigger/AGENTS.md`（七项；§③ 边界条款**原文转移** `adapters/AGENTS.md §⑧` 的四条暂住纪律：
   只依赖 `core/` 与 `compiler/` 的公开 API、不含业务逻辑、无副作用、内核不得反向依赖；
   行数口径按「不适用 ≤150 行量化标准，同类为 compiler 层大文件」登记）；
3. `adapters/state_trigger/` **整体删除**；
4. `adapters/AGENTS.md §⑧` 改写为「已迁出（2026-09-19）→ `trigger/`」的历史记录（保留原条款要点与迁出原因，
   不再具约束力）；§② 不动；
5. labs 三处 import 更新：`labs/ticket-source/{run_e2e,to_state}.py` 与其 `README.md` 中的
   `adapters.state_trigger` → `trigger`（`__init__.py` 内部 import 同步改为 `from trigger.format import …` 等）。

## 允许修改的文件（白名单）

```
允许新增：trigger/**（含 AGENTS.md、README.md、5 个 .py、tests/4 文件）
允许新增：无其他
允许修改：adapters/AGENTS.md（仅 §⑧ 一节改写为迁出记录）
允许修改：labs/ticket-source/run_e2e.py、to_state.py、README.md（仅 import 路径与对应说明文字）
允许删除：adapters/state_trigger/**（整体）
禁止触碰：core/ rules/ compiler/ assets/ runtime/ eval/ cli/ packs/ tools/ docs/（docs 由验收方迁移后统一刷新）
```

## 禁止事项

- **不得改任何被迁移文件的行为逻辑**：除 import 路径、模块 docstring 首行的落点说明、`__init__.py` 头部注释外，
  与原文件逐字一致（验收方会用 git 历史逐文件比对）；
- 不得「顺手」拆分 `format.py`、不得重命名任何公开 API（`load_trigger` / `validate_state` / `check_budget` /
  `build_plan` / `rule_matches` / `record_turn` / `check_reachability` 等全部原名保留）；
- 不得改 docs/ 下任何文件；不得改历史任务卡（T16/T17/T21 系列卡中的 `state_trigger` 字样是历史记录，不动）；
- 不得 `git add` / `git commit`；不得改根 `AGENTS.md` 与 `README.md`。

## 验收标准（逐条可判定，验收方会逐条核对）

1. **内容零变化**：`trigger/` 下 5 个 `.py` 与 `tests/` 4 个测试文件，与迁移前 `adapters/state_trigger/` 的
   git 历史版本逐字一致——**仅允许的差异**：`__init__.py` 的 `from adapters.state_trigger.X import` →
   `from trigger.X import` 及头部注释落点说明；各文件模块 docstring 首行路径说明。验收方按此口径 diff；
2. **旧路径死透（负例）**：`python3 -c "import adapters.state_trigger"` 必须**非零退出**（ModuleNotFoundError）；
   `git ls-files adapters/state_trigger` 为空（整体删除）；
3. **全仓代码引用零残留**：`grep -rn "adapters\.state_trigger\|adapters/state_trigger" --include="*.py" .`
   （排除 `__pycache__`）→ **0 命中**；`--include="*.md"` 的命中只允许出现在 `adapters/AGENTS.md §⑧` 迁出记录、
   `labs/ticket-source/README.md` 的更新后说明、`docs/` 历史文档与 `docs/tasks/` 历史卡；
4. **测试迁移正确**：`python3 -m unittest discover -s trigger` → **159 条全绿**；
   `discover -s adapters` → **152 条全绿**；`discover -s core -s rules …` 十根合计 **1,089** 不变（验收方复核）；
5. **labs 回归**：`python3 labs/ticket-source/run_e2e.py` → rc 0（它已 import `trigger.*`；
   注意按 T21 卡它自带 sys.path 兜底，直接跑即可）；
6. **新层纪律齐备**：`trigger/AGENTS.md` 七项存在；四条边界条款与 `adapters/AGENTS.md §⑧` 原文要点一一对应
   （验收方逐条比对）；行数口径条款按背景所写登记；
7. `git status --porcelain -uall` 的改动**全部**在白名单内（新增 trigger/**、删除 adapters/state_trigger/**、
   修改 adapters/AGENTS.md 与 labs 三文件，无其他）。

## 反空转条款（每张卡必带，T01 教训）

- 测试迁移必须**原样搬**：不得删测试、不得改断言、不得改名测试方法（159 条 = 原数，少一条即退回）；
- 验收 2 的负例必须真的跑过并贴输出；
- 不得为了让 grep 判据好看而改历史文档（docs/ 历史记录里的旧路径字样**留着不许动**）。

## 回滚方式

`git checkout -- adapters/AGENTS.md labs/ticket-source/`（还原修改）+
`git clean -fd trigger`（删新层）+ `git checkout -- adapters/state_trigger`（还原被删目录；若已 git rm 需先 reset）。
执行方在动手前记录 `git stash list` 与 HEAD，报告里写明回滚命令的实际可用性。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`）。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T28-trigger-扩展区迁移.md)" --dir （仓库根）
```

**数据分级：公开级**——纯代码迁移，无任何数据。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19，第十一批；迁移本体合格，两处收尾转 T28b/T28c）**

### 验收记录（验收方独立复核）

- **内容零变化**：`git show HEAD:<old> | diff` 逐文件核对，差异仅在允许范围（`__init__.py` import 行、
  各文件 docstring 首行落点、注释内模块名提法）；`README.md` 逐字节零差异。验收方抽查 `format.py`：恰 2 行差异（允许项）。
- **数字守恒**：adapters 311→**152**、trigger 新根 **159**、十根合计 **1,089** 不变（验收方逐根亲跑全绿）。
- **负例**：`import adapters.state_trigger` 与 `import trigger.trigger` 均 ModuleNotFoundError；`len(trigger.__all__)==25`。
- **§⑧ 改写**：历史要点保留 + 迁出原因 + 「第二个同类模块」禁令解除声明——压缩表述**裁定接受**（要点齐全）。
- **两处收尾转修订卡**：① 包/模块同名致 discovery 冲突（卡面判据缺口，执行方在干净 HEAD 复现证明非手损，
  停在卡约束内上报处置正确）→ T28b 改名 `plan.py`；② `_REPO_ROOT` 四层上溯 + labs `trigger.trigger` 残留 → T28c。
- **教训（记入 00-索引）**：写「零变化迁移」卡时必须预判**隐含破坏点**——目录深度硬编码（`_REPO_ROOT` 上溯层数）、
  与包同名的模块文件（Python discovery 冲突）、全域字符串替换的双义命中（`trigger.trigger` 既是模块路径也是变量名前缀）。
  连续两张修订卡都出在策划未预判处，非执行方手损。
