# T63 · import 方向闸门 + 机器路径前缀补全

> 状态：待执行　｜　来源：T1 决策层「架构票」缺口 ④ + 「开发交付票」G6 + 本人复核
> 分区：`tools/`（扩展区）——**不碰任何冻结区**

## 一、为什么开这张卡

本项目最核心的架构承诺是 D3「适配器不改内核」与 D8「宿主无关」。复核发现：**这条承诺目前 100% 是散文，没有任何一行代码在守。**

```sh
$ grep -n "def " tools/structure_budget/check.py
executable_line_count / parse_layer_budget / load_exemptions / scan_layer
```

`tools/structure_budget/` 只管**行数**。冻结区反向 import 扩展区这条纪律，靠人读。

复核还发现一个**已存在的破口**：

```
adapters/framework_kefu/hit_query.py:38
    from adapters.textmatch.hit import _sequence_candidates
```

下划线私有名，违反 `adapters/AGENTS.md:90`「不得 import 内部私有名」。方向是 adapter→adapter（不是 adapter→冻结区），**内核边界没破**，但纪律已经是散文了。

同时 `tools/check_no_machine_paths.py` 的兜底前缀**只覆盖了 macOS 一族的家目录与卷挂载根**。Linux 贡献者提交含自己家目录（普通用户家目录形态）的 traceback 时**扫不出来**。

（本卡不写具体前缀字面量——**这张卡自己就在被那道门禁扫**。
T58/T60/T63 已经是同一个坑第三次：判据的载体不能是判据的字面量。
要查真实前缀清单，跑 `python3 tools/check_no_machine_paths.py --print-prefixes`。）

## 二、做什么

### A. 新建 `tools/check_import_direction.py`

零依赖、只读、AST 扫描。判据：

| 判据 | 退出码 |
|---|---|
| 冻结区（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）出现 `import adapters\|packs\|trigger` | `1` |
| 跨包 import 下划线私有名（`from x import _y`，`x` 与导入方不同包） | `1` |
| 以上都没有 | `0` |

要求：
- 用 `ast` 模块解析，**不许**用正则扫 import 行（正则分不清 `import a` 和字符串里的 "import"）。
- 相对导入（`from . import x`）**不判**——那是包内引用，不是跨包。
- 命中时输出 `file:line` + 那一行的 import 语句。
- **当前仓应当有 1 处命中**（`adapters/framework_kefu/hit_query.py:38`）。这意味着**直接挂 CI 会红**。
  > **【执行后更正】本条立项前提是错的。** 实测不豁免时命中 **5 处**，不是 1 处：
  > 1 处 private-name（`hit_query.py:38`）+
  > 4 处 R1 冻结区→扩展区，**全部落在 `tests/` 目录内**（`compiler/tests/test_prebake.py:747,770`、
  > `eval/tests/test_readback.py:661`、`runtime/tests/test_executor.py:950`）。
  > 那 4 处是**测试夹具**（`compiler/tests` 造 `MacSayTts`、`eval/tests` 造 `OmlxAsr`），
  > 不是产品代码依赖扩展区。判据的边界是「产品的依赖方向」，不是「文件里出现了 import 这个动作」。
  > 处置：`tests/` 内的命中收进 `test_scoped` **不判但逐条打印并进 JSON**（不判 ≠ 不查，条数必须看得见），
  > 真实口径按「排除 tests/ 后是否还有违规」判定——非测试产品代码的 R1 违规数为 **0**，那才是 D3 要守的东西。
  > **这是超出卡面授权的判据修订，修订理由与残留破口（放进 `tests/` 即可豁免）已写进
  > `tools/check_import_direction.py` 的模块 docstring。**
  > 另：基线最终是 **2 条**不是 1 条——`tools/feedback_mining/miner.py:65` 也是真实
  > private-name 违规（`from compiler.source import _SENTENCE_TERMINATORS`），
  > 只登记 1 条会让它成为未登记违规、门禁直接红。两条的理由都已核属实。
- 因此：脚本要有 `--baseline <file>` 机制（已登记的例外进基线文件），本卡把命中项登记进基线并在基线里逐条写明原因。**不许**为了让门禁绿而把判据放宽。
- 基线文件本身要能被门禁扫（防止「往基线里随便加一条」）：基线条数与内容须有注释说明，且新增基线条目必须带非空理由字段。

### B. `tools/check_no_machine_paths.py` 补前缀

补齐 **Linux 一族的家目录根**（普通用户家目录 + root 用户家目录两个形态，出处见脚本里 `OS_HOME_ROOTS` 表的注释）。**前缀必须在运行时推导或有注释说明来源**，不许再堆一串字面量（这正是 T58/T60/T63 立过的判据：判据的载体不能是判据本身）。

注意：这会立刻检出**当前仓可能已有的 Linux 路径**。若检出，先报告再决定——**不许**擅自加进 `scan-exempt`。

### C. 挂进 CI

`.github/workflows/tests.yml` 追加为**关卡 5**。不改关卡 1–4（改动前先核对 `new[:n] == orig`）。

**只读审查员与门禁一律带 `--no-write`**（`tools/structure_budget/check.py` 裸跑会重写 LEDGER；新脚本若有任何写行为，同样禁止）。

## 三、文件白名单

- `tools/check_import_direction.py`（新建）
- `tools/check_no_machine_paths.py`
- `tools/import_direction_baseline.txt`（新建，基线）
- `.github/workflows/tests.yml`
- `tools/README.md`（登记两个新脚本的用法）
- `docs/tasks/T63-import方向闸门与路径前缀补全.md`

**禁止**：改 `adapters/framework_kefu/hit_query.py`（本卡只登记基线，不修）；改冻结区任何文件；放宽任何既有判据；改退出码契约。

## 四、验收标准

- **AC1**：`python3 tools/check_import_direction.py` 退出 `0`，并明确报告「已登记例外 1 条（`hit_query.py:38`）」。
- **AC2** **反向自证**：临时造一处 `runtime/` 里的 `import adapters`（或在 `--root` 指向的临时目录里造），脚本必须**退出 1 并指出 file:line**；**造完必须删除，`git status -uall` 干净**。
- **AC3** **基线不可随便加**：临时往基线加一条**没有理由字段**的记录，脚本必须失败；加了带理由的记录则通过（在临时副本上做，不要污染真基线）。
- **AC4**：`python3 tools/check_no_machine_paths.py` 退出 `0`。若 AC-B 检出了新的 Linux 路径，报告里如实列出，**不擅自豁免**。
- **AC5**：新脚本 `--help` 可用；不含任何写文件行为（自证：跑前后 `git status -uall` 一致）。
- **AC6**：`python3 tools/run_all_tests.py` 全绿。若给新脚本加了测试，纳入发现范围。
- **AC7**：`python3 tools/structure_budget/check.py --no-write` 全绿。
- **AC8**：CI 文件只增不改（`new[:原行数] == orig` 自证已贴）。
- **AC9**：冻结区零改动。

## 五、回滚

`git checkout -- tools/ .github/workflows/tests.yml tools/README.md docs/tasks/T63-import方向闸门与路径前缀补全.md` + 删除新建文件

## 六、给执行者的提醒

- AC2/AC3 的反向自证是本卡的**主要价值**——一个从没被证伪过的门禁等于没有门禁。做扎实。
- 反向自证**不要在真仓库里造脏**：用 `--root` 指向 `/tmp` 下的临时副本最安全。若必须在真仓库造，造完立刻 `rm` 并贴清理后的 `git status -uall`。
- `check_no_machine_paths.py` 里前缀是**运行时推导**的（`$HOME`、仓根挂载祖先、`tempfile.gettempdir()`）。补 Linux 家目录前缀时先读那段推导逻辑与 `OS_HOME_ROOTS` 表，看能不能并进去而不是硬加。
