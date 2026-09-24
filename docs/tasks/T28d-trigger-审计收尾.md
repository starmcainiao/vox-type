# T28d · trigger：迁移审计收尾（1 P1 + 4 P2，共 ~10 行）

## 背景（只写必需）

T28/T28b/T28c 验收通过后，`blackiron-silent-failure-hunter` 对迁移做完整性审计，查出 1 P1 + 4 P2。
共同根因：**「零变化迁移」的允许差异只覆盖了 docstring 首行与 import 行，注释与文档字样成了静默漏网**
——它们不影响任何测试，错了也不响。全部 8 行级修改，位置由审计给到 file:line。

## 允许修改的文件（白名单）

```
允许修改：trigger/ledger.py（仅 :175-176 docstring 与 _repo_root 的 .parent 数；_inside_repo 函数体不动）
允许修改：trigger/__init__.py（仅 :3 落点说明一行）
允许修改：trigger/tests/test_format.py :32、test_ledger.py :38、test_reachability.py :38、test_trigger.py :46
          （仅 _REPO_ROOT 行上方的路径链注释，一行注释各文件）
允许修改：labs/ticket-source/README.md（仅 :9 的 trigger.py → plan.py 字样）
允许修改：labs/ticket-source/run_e2e.py（仅 :1592 note 字样 trigger.py → plan.py）
允许重跑：python3 labs/ticket-source/run_e2e.py（刷新 summary.json，:103 字样随之更新；
          本次 summary.json 的 20 行内 diff 属授权产物刷新，不是越界）
禁止触碰：其他一切文件
```

## 逐条修法（审计已给 file:line，不许扩大）

1. **P1 · `trigger/ledger.py`**：`_repo_root()` 的 `.parent` 由 3 个减为 **2 个**（旧位置 adapters/state_trigger/ 需 3 层，
   新位置 trigger/ 只需 2 层；现实测返回仓库上一层目录）；其上方 docstring 的路径链
   「本文件 → state_trigger → adapters → 仓库根」改为「本文件 → trigger → 仓库根」。
   `_inside_repo()` **保留不删**（它是留痕红线的守卫口子，暂无调用方是有意留白），仅随 `_repo_root` 自动修正；
2. **P2 · `trigger/__init__.py:3`**：`落点说明：本包在 adapters/（扩展区）` → `落点说明：本包是顶层扩展区 trigger/（2026-09-19 自 adapters/state_trigger/ 迁入）`；
3. **P2 · 4 个测试文件的路径链注释**：旧链「tests/ → state_trigger/ → adapters/ → 仓库根」改为「tests/ → trigger/ → 仓库根」（3 层）；
4. **P2 · `labs/README.md:9`**：`` `trigger.py` `` → `` `plan.py` ``（:8 的 format.py 已改对，别动）；
5. **P2 · `labs/run_e2e.py:1592`**：`trigger.py 的既有口径` → `plan.py 的既有口径`；随后重跑 run_e2e 刷新 `summary.json`。

## 禁止事项

- 不得改任何断言/逻辑/函数签名；`_inside_repo` 不得删除、不得新增调用方；
- `trigger/` 内 `state_trigger` 字样只允许存在于「迁入记载」句式（`trigger/AGENTS.md:3,35` 保留）；
- 不得 `git add` / `git commit`。

## 验收标准（逐条可判定）

1. **P1 修正实测**：`python3 -c "import sys; sys.path.insert(0,'.'); from trigger.ledger import _repo_root, _inside_repo; r=_repo_root(); print(r.name, _inside_repo(r/'packs'), _inside_repo(r.parent/'other.txt'))"`
   → 输出 **`vox-type True False`**（修前第三个值实测为 True——守卫错位）；
2. `python3 -m unittest discover -s trigger` → **159 条全绿**；`discover -s adapters` → 152；十根 1,089；
3. `python3 labs/ticket-source/run_e2e.py` → rc 0；`grep -c "plan.py 的既有口径" labs/ticket-source/summary.json` → ≥1；
4. **残留清零**：`grep -rn "state_trigger" trigger/ --include="*.py"` → 仅 `trigger/AGENTS.md` 不在内，
   `.py` 文件中 0 命中（`__init__.py` 与 4 个测试注释修后不应再有）；`grep -n "trigger.py" labs/ticket-source/README.md` → 0 命中；
5. `git status --porcelain -uall` 终态核对（改动全部在白名单内；`summary.json` 为授权刷新）。

## 反空转条款

- 验收 1 的三值探针必须原样贴输出——`vox-type True False` 三个值缺一不可；
- 每处修改前后行贴 diff，多改一行即退回。

## 回滚方式

labs 侧 `git checkout -- labs/ticket-source/{README.md,run_e2e.py}`；trigger 侧按 T28c 终态快照恢复。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19；迁移四部曲收口）**

### 验收记录（验收方独立复验，全部亲跑）

- P1 三值探针：**`vox-type True False`**（修前实测 `newProject True False`——守卫把仓库上一层误判为仓内，已纠正）；
- trigger 159 全绿；`summary.json` 新字样 1 命中、旧字样 0 命中；labs rc 0；
- **裁定 2 条**：① 验收 4 与修法 2 的措辞自相矛盾系**卡面瑕疵**（卡文要求 `.py` 内 0 命中，但修法 2 指定的新落点说明
  本身含 `adapters/state_trigger/`）——执行方按逐字修法执行正确，判定按「迁入记载句式可保留」收窄；
  ② `summary.json` 22 行 diff（char 偏移连带重算）属授权刷新连带结果，接受。
- 审计侧确认干净面：159/152 双套全绿、e2e 产物与仓内逐字节一致、全仓无旧路径活引用、25 个导出符号全可解析。
