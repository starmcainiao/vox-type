# cli/ · 薄壳入口（冻结区）

## ① 职责 / 不负责什么

**职责**：解析参数 → 调各层**公开** API → 打印 → 定退出码（`docs/08 §8.1`）。
五个命令：`pack check` / `pack build` / `run` / `bench` / `verify`。

**不负责**：不含任何判定逻辑。命中判定在 `runtime/`，四属性与质检阈值在 `compiler/`，
统计口径在 `eval/`，指标字段名在 `core/`。CLI 一行统计代码都不写——
重算一次就等于第二份口径，两边必然漂移。

## ② 输入 / 输出契约

- 命令名冻结（`AGENTS.md §四`）；`--json` 时 stdout 只出纯 JSON，人读摘要走 stderr。
- `cli/commands/*.py` 每个文件只做一件事：`add_arguments`（参数）+ `run`（主体）。
  判定一律经各层公开面取，不在本层落地。

## ③ 验收条件（可判定 / 可测）

1. 退出码语义冻结：`0` / `2` / `3` / `4` / `5`（`docs/08 §8.2`），一字不能改；
2. `2` 与 `3` 必须分得开：配置期 → 2，执行期 → 3 且 stderr 必带 `type(exc).__name__`；
3. `--json` 时 `json.loads(stdout)` 必须成功（stdout 不得混入人读文本）；
4. `--out` 越界拦截：产物不许写进源/包目录（含 `..` 逃逸）→ 2；
5. 失败一律非零退出，任何非零退出都要有 stderr 说明（不得静默）。

## ④ 本层数据收集

无。运行期指标由 `runtime/` 记录，对拍指标由 `eval/` 统计。

## ⑤ 依赖边界

- 允许依赖：`core/` / `assets/` / `compiler/` / `runtime/` / `eval/` 的**公开 API**；
  适配器**只动态解析**（`importlib`），本包内不得静态 `import adapters`。
- 禁止：调用任何层的下划线私名；在 CLI 里复制各层的判定规则。

## ⑥ 变更纪律

命令名与 `--json` 顶层字段集冻结；新增命令或字段必须同时更新
`docs/08-CLI口径.md` 与本文件的 §②。**公开面变更必须留痕**
（例如 T21 #18 把 `load_corpus` 与 `core.METRIC_FIELDS` 补进公开导出）。

## ⑦ 冻结状态

**[冻] 冻结区**。命令名与退出码一字不改；内部实现可换，
但「解析 → 调用 → 打印 → 退出码」的形状不能变。

## ⑧ 结构预算（T21 登记）

结构预算：可执行行数阈值 <= 150

（机器可读标记行；**不要在这行加任何修饰符**——加粗星号或把 `<=` 写进字符类
都会让正则匹配失败，等于阈值消失。脚本 `tools/structure_budget/check.py` 只认这行。）

口径：`tokenize` 后 `NEWLINE` token 计数（排除注释与 docstring；
不含 `tests/` 与 `__pycache__`），与 `adapters/AGENTS.md §⑨` 的 145–150 带同算法。
当前行数快照见 `tools/structure_budget/LEDGER.md`（脚本生成，不要手改数字）。

## ⑨ T21 实现变更（必须留痕）

### ⑨.1 `vox bench` 的 reference_gate 开始决定退出码（#15「门禁装牙齿」）

此前 `eval.bench` 算出 `reference_gate.passed` 并写进报告，但 CLI 在 gate 不过时
**仍 exit 0**——门禁存在但返回值不影响结果。T21 起：

- `incomplete is False` **且** `reference_gate.passed is False` → **退出码 4**
  （`EXIT_QUALITY`，与「数据不完整」同一性质的业务结论，不是错误）；
- gate 通过 → 退出码 0；
- **报告数字与格式不变**：只**加**了 `reference_gate` 结论块
  （`required_hit_rate` / `observed_hit_rate` / `passed`，直接透传报告同名块，
  不重算），既有七字段原样在场。stderr 多一行说明「为什么是 4」，不静默。

### ⑨.2 公开面（#18）

- `from cli.commands.bench import load_corpus` 可导入；`cli/commands/__init__.py`
  的 `__all__` 点名它，并**用模块级 `__getattr__` 惰性转发**
  （`from cli.commands import load_corpus` 也成立），两处指向同一个 `eval.bench`
  实现，CLI 零本地副本。
- `from cli import load_corpus` 同样成立（`cli/__init__.py` 转发）。

  **为什么不能直接赋值转发**：命令子模块在**本包装载期间**就被 `cli.main` 导入，
  它们反过来从 `cli.commands` 取共享底座。若用属性赋值转发，会形成
  `cli.main ↔ cli.commands.bench` 的循环（实测 `ImportError: cannot import name
  'load_corpus'`）。`__getattr__` 只在名字被真正取用时触发，此时 `cli.main` 早已装完。
- **为什么 `DEFAULT_ADAPTER_SPEC` 必须定义在文件开头**：命令子模块的导入早于
  `cli/commands/__init__.py` 的 §1 执行，它们在 `add_arguments` 里读
  `commands.DEFAULT_ADAPTER_SPEC`。常量若仍在其原位置，子模块导入时取不到就会落到
  `__getattr__`（该钩子只对 `load_corpus` 转发，其余一律 `AttributeError`），
  于是全线命令失败。常量在文件开头绑定，语义与值一字未改。
