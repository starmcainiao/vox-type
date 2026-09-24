# T28c · trigger：收 T28 零变化迁移的两处遗留硬编码（4 行 + 2 处）

## 背景（只写必需）

T28b（`trigger.py` → `plan.py` 改名）已完成且有效，但暴露 T28「零变化迁移」的两处遗留——都不是改名引起，
是迁移本身授权伴随的修正（验收方 2026-09-19 已独立验证根因）：

1. **测试的仓库根上溯层数**：`trigger/tests/` 下 4 个测试文件写死
   `_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent`（4 层，原 `adapters/state_trigger/tests/` 位置正确）；
   迁到 `trigger/tests/` 只需 **3 层**——现解析到 ``，导致 `discover -s trigger`
   159 条中 85 条 ERROR（找不到 `packs/demo-brief/pack.json` 等）。执行方已在临时目录实证改 3 层 → 159/OK；
2. **labs 的旧模块路径残留**：`labs/ticket-source/run_e2e.py:260`
   `from trigger.trigger import rule_matches`（T28 全域替换在 adapters 语境下合法、迁到顶层包后失效，
   `import trigger.trigger` → ModuleNotFoundError，labs 回归 rc 3）；`:1597` docstring 里的同名字样顺手改。

## 允许修改的文件（白名单）

```
允许修改：trigger/tests/test_format.py、test_trigger.py、test_reachability.py、test_ledger.py
          （各 1 行：_REPO_ROOT 的 .parent 由 4 个减为 3 个，其余零改动）
允许修改：labs/ticket-source/run_e2e.py（仅 :260 的 import 行与 :1597 的 docstring 字样）
禁止触碰：其他一切文件
```

## 禁止事项

- 不得改任何断言/逻辑/方法名；不得动 `_REPO_ROOT` 行以外的任何行；
- labs 的其余 `trigger.trigger_id` 等命中是**变量属性访问**，不是模块路径，**不许动**（grep 全文只许 260/1597 两行变化）；
- 不得 `git add` / `git commit`。

## 验收标准（逐条可判定）

1. `python3 -m unittest discover -s trigger` → **159 条全绿**；
2. `discover -s adapters` → **152 条全绿**；十根合计 **1,089**；
3. `python3 labs/ticket-source/run_e2e.py` → **rc 0**；
4. `python3 -c "import trigger; print(len(trigger.__all__))"` → `25`；`python3 -c "import trigger.trigger"` → 非零；
5. `git status --porcelain -uall` 终态 + `git diff labs/ticket-source/run_e2e.py` 恰好 2 行变化。

## 回滚方式

改动均在已跟踪文件（tests 4 文件在 trigger/ 未跟踪目录内、labs 文件已跟踪）：
labs 侧 `git checkout -- labs/ticket-source/run_e2e.py`；trigger 侧按 T28/T28b 终态快照恢复。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19；trigger 159 全绿，迁移三部曲收口）**

### 验收记录（验收方独立复核，全部亲跑）

- 十根全绿：core 44 / rules 21 / assets 59 / **adapters 152** / compiler 168 / runtime 118 / eval 208 / cli 85 /
  tools 75 / **trigger 159** = **1,089**（与迁移前守恒）；
- 负例：`import trigger.trigger` 与 `import adapters.state_trigger` 均 ModuleNotFoundError；`__all__==25`；
- labs `run_e2e.py` rc 0（148 断言）；`summary.json` 由验收方重跑刷新——**裁定接受**：它是 labs 再生产物，
  `generated_from` 字样随源码修正而变属正确伴随更新，入库与源码自洽；
- labs diff 恰 2 行（:260 import / :1597 docstring），`trigger.trigger_id` 等变量属性访问未被误改（逐处核对）。
