# tools/structure_budget — 结构预算（R09 适应度函数）

把「各层可执行行数 ≤ 阈值」**从人工对账变成一条命令**。

背景：`docs/13 §五 #16`（R09）指出各层「可执行行 ≤150」长期靠人工数行数，
没有适应度函数、没有豁免台账。本目录补的就是这两样。

## 用法

```sh
# 人读输出：各层阈值 + 文件数 + 可执行行 + 超限数
python3 tools/structure_budget/check.py

# 机器可读（CI 用）
python3 tools/structure_budget/check.py --json

# 只判定不落盘（台账与快照保持原样）
python3 tools/structure_budget/check.py --no-write
```

退出码：`0` 全部合规 / `1` 有违规（stderr 逐条列出：文件、实际行数、阈值）
/ `2` 用法或仓库根解析失败。

## 四条口径（脚本存在的意义）

1. **行数 = 可执行行**。`tokenize` 后 type 为 `NEWLINE` 的 token 计数（一条逻辑语句
   结束时的换行，每个代码行恰好一个）。**不要用 `NL`**：它只在「视觉空白行」
   （空行、纯注释行、docstring 的物理行）才发，实测 `a = 1\nb = 2\n` 的 NL 计数为 0，
   误用会把口径反转为「数空行数」。缩进层（`INDENT`/`DEDENT`）不是物理行，天然不计。
   该口径与 `adapters/AGENTS.md §⑨` 的 145–150 带同算法
   （实测 `adapters/asr_omlx/adapter.py` = 106，落在带内）。
   `tests/` 与 `__pycache__` 不计（量化标准明写「不含注释与测试」）。
   已知取舍：括号内或 `\` 续行的多行语句只算 1 行（与 §⑨ 记的 231/236/176 同算法）。
2. **阈值从各层 AGENTS.md 读取**，脚本**不持有**任何层的阈值字面量。某层没登记
   量化标准 → 该层跳过并打印「未登记」，脚本不替它猜（猜出来的数字没有出处）。
   登记形式是 AGENTS.md 里的一行**机器可读标记**：
   ```
   结构预算：可执行行数阈值 <= 150
   ```
   当前已登记：`core` / `adapters` / `eval`。
3. **豁免在台账里显式登记**（`LEDGER.md` 的 `<!-- BEGIN exemptions -->` 块）。
   超限但未登记 → 违规、非 0 退出。脚本**不会**默默放宽阈值。
4. **tokenize 失败必须抛错，不得返回 0**。返回 0 只能来自「根本没跑完」
   （读到截断或旧内容），把一个几百行的文件记成 0 行等于判成合规——那是静默降级。

## 产物

| 文件 | 说明 |
| --- | --- |
| `check.py` | 检查脚本（可执行；也可 `from tools.structure_budget.check import ...`） |
| `LEDGER.md` | 台账：阈值来源表 + 当前各层行数快照 + 豁免登记块（脚本生成） |
| `snapshot.json` | 机器可读快照（可 diff，供 CI 比对） |

`LEDGER.md` 与 `snapshot.json` **由脚本生成，不要手改数字**——手改会让台账与
磁盘上的实际行数脱节（静默降级）。改豁免：删掉 `check.py` 里对应的判定条件不行，
要在台账的豁免块里加一行（理由同时写进该层 AGENTS.md）。

## 覆盖范围

九个 Python 层：`core` / `rules` / `compiler` / `assets` / `runtime` / `adapters`
/ `trigger` / `eval` / `cli`。`rules/` 无 `.py`，登记但不参与判定。

## 已知边界

- `cli/` 的阈值由本目录新建时一并写入 `cli/AGENTS.md §⑧`（此前该文件不存在，
  为避免脚本自造一个没有出处的数字，等层文件落地时才登记）。
- 快照里的行数会随代码改动变化，这是预期——它不是冻结基线，是**当前实况**。
  要钉基线请用 `snapshot.json` 做 diff。
