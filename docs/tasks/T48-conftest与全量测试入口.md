# T48 · 仓根：pytest 根目录可收集 + 一条命令跑全量

> 批次：第三十三批 · 拍板人：主会话（2026-09-28 T1 决策层开发交付票 + 独立验收）
> 性质：仓根测试基建，**零开批**（不碰任何冻结区代码）

---

## 一、为什么开这张卡（事实）

现状三个可复现的事实：

1. **标准 pytest 入口是坏的**
```sh
cd <本仓根>
python3 -m pytest --collect-only -q 2>&1 | tail -3
```
现状输出：`7 errors during collection !  1390 tests collected`，
错误形如 `ModuleNotFoundError: No module named 'tests.test_structure_budget'`。
根因：十一个测试根的 `tests/` **都带 `__init__.py`** 且包名都叫 `tests` → 互相撞名；
仓内**没有** `conftest.py` / `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini`（已 `ls` 确认）。

2. **CI 与 CONTRIBUTING 各抄一份 for 循环**（`.github/workflows/tests.yml:24-29`、
`CONTRIBUTING.md`），没有单一真源。

3. **对外宣称的测试数没有一条命令能产出**：README / `docs/13` / `docs/18` 都写 1,547；
按 CI 的 runner（`unittest discover`）逐根实跑（已核）：

| 根 | ran | skip |
|---|---:|---:|
| core | 62 | |
| rules | 21 | |
| assets | 114 | |
| adapters | 316 | |
| compiler | 185 | |
| runtime | 191 | |
| eval | 239 | |
| cli | 89 | |
| tools | 159 | |
| trigger | 159 | |
| packs | 35 | 24 |
| **合计** | **1,570** | 24 |

（1,570 − 24 skip = 1,546 实际执行。三者都真实，**1,547 谁也产不出**。）

---

## 二、目标

1. `python3 -m pytest` 在仓根**可完整收集、零 error**；
2. 提供**单一真源**的「跑全量」入口（CI 与 CONTRIBUTING 都改成调它）；
3. 提供一条命令产出**对外可引用的测试数**，让 README 的数字从此有机器出处。

**明确的非目标**：
- ❌ 不改任何测试文件的断言逻辑（**只允许改 import 行与新增文件**）
- ❌ 不把 CI 从 `unittest` 换成 `pytest`（CI 换 runner 风险高、且本卡只解决「入口可用」）
- ❌ 不动 `.github/workflows/`（那是 T49 的活，本卡**只提供脚本**）
- ❌ 不把 `labs/` 纳入全量（`labs/` 是不进契约的实验区）
- ❌ 不引入第三方依赖（**包括 pytest 本身**：本仓零第三方依赖是 README 徽章级纪律；
  pytest 没装时本卡脚本必须**清晰报错并回落 unittest**，不能让「跑测试」从此依赖 pip）

---

## 三、落点

| 文件 | 职责 |
|---|---|
| `conftest.py`（仓根，新建） | 设 `rootdir` 友好化：把撞名的 `tests.*` 包用 `importmode=importlib` 隔离；必要时注入 sys.path |
| `tools/run_all_tests.py`（新建） | **单一真源**：逐根跑 `unittest discover`、累计 ran / skipped / failed、打印**可复制的汇总行**、**非零退出码** |
| `tools/count_tests.sh`（新建）或并入上者 | 输出一行对外数字（`ran=… skipped=… failed=…`） |

**关键设计约束（写卡时就钉死，执行者不得自行发挥）**：

- `tools/run_all_tests.py` 必须**断言成功数**：跑完若任一根 `failed > 0` 或有根 ERROR，
  **退出码非零**并把失败根逐个列出来。（`AGENTS.md`「跑批铁律」：失败会被产物数量掩盖。）
- 数字**必须标注 skip**：`packs` 根常态 24 条 skip 是因为需要本机 kefu 仓的 yaml；
  汇总行要写成 `ran=1570 skipped=24 executed=1546 failed=0` 这样的**分项**，
  **不要**只给一个总数——那正是 1,547 那个悬案的成因。
- **零依赖回落**：`python3 -m pytest` 不可用时，`run_all_tests.py` 仍能跑（它走 unittest）。

---

## 四、文件白名单

**可改/新建**：
```
conftest.py                        （新建）
tools/run_all_tests.py             （新建）
tools/count_tests.sh               （新建，可选）
tools/README.md                    （改：把新脚本登记进文件表）
tools/tests/                       （可为新脚本补测试）
CONTRIBUTING.md                    （改：把 for 循环换成调单一真源）
docs/tasks/T48-*.md                （本卡验收记录）
```

**禁改**：
```
core/ rules/ assets/ compiler/ runtime/ eval/ cli/ trigger/ packs/   ← 冻结区与既有包，一律不动
adapters/                                                     ← T47 的活
docs/13 docs/18 docs/20 docs/22 README.md CHANGELOG.md .github/  ← 别的卡的活
任何 tests/ 目录内的**测试文件本体**（只允许改 import 行；断言逻辑一行不动）
```

---

## 五、验收标准（逐条可复跑）

> **路径占位约定（T56 占位化，2026-09-28）**：本节命令一律用占位符书写，**不含本机绝对路径**（口径见 `docs/18 §一`「绝对路径一律 0」）。
> - `<本仓根>` = vox-type 仓根目录。**可执行的等价做法**：先 `cd` 到本仓根再执行本节命令；
>   命令里的 `<本仓根>/xxx` 写成相对路径 `xxx` 即可（`<本仓根>` 本身不是可执行路径，别直接复制）。

1. **pytest 根目录零 error**
```sh
cd <本仓根>
python3 -m pytest --collect-only -q 2>&1 | tail -2
```
期望：末行形如 `<N> tests collected`，**无 `errors during collection`**，
且 N ≥ 1390（基线 1390 collected + 7 个因撞名未收的根，收齐后应更多）。
**若 pytest 本机不可用**：如实记录「pytest 未安装」并只跑第 2 条，**不许假装通过**。

2. **单一真源可跑且计数分项**
```sh
cd <本仓根>
python3 tools/run_all_tests.py; echo "exit=$?"
```
期望：打印每根一行 `== <root> == Ran N tests ...`，末行汇总
`ran=<总数> skipped=<总数> executed=<总数> failed=0`，且 **`exit=0`**。
（基线：ran=1570 / skipped=24 / executed=1546 / failed=0。若因 T47 并行改动使 adapters
条数变化，按实跑数写，**并在汇总里保持分项、不要写死**。）

3. **失败会被拦下（注入验证，必做）**
临时把某个测试根改成必红（例如在 `core/tests/` 下一个文件里加 `self.assertTrue(False)`），
跑 `python3 tools/run_all_tests.py; echo "exit=$?"`，
期望：**`exit` 非 0** 且输出点名 `core` 根失败；改回后 `exit=0`。
**这一步不做 = 本卡未验收**（`AGENTS.md` 跑批铁律）。

4. **CONTRIBUTING 单一真源**
```sh
grep -n "run_all_tests\|unittest discover" <本仓根>/CONTRIBUTING.md
```
期望：出现 `tools/run_all_tests.py`；**不再有**手抄的 `for d in core rules assets ...` 循环
（CI 里的那份由 T49 处理，本卡不许改 CI）。

5. **零依赖纪律未破**
```sh
grep -rn "import pytest\|pip install" <本仓根>/tools/run_all_tests.py <本仓根>/conftest.py
```
期望：两个文件都**不 import pytest**（conftest 只被 pytest 读，自身不 import 它）；
`run_all_tests.py` 零第三方 import。

6. **结构预算与既有测试零回归**
```sh
cd <本仓根>
python3 tools/structure_budget/check.py --no-write | tail -1
python3 -m unittest discover -s tools 2>&1 | tail -1
```
期望：`结构预算：全部合规`；`OK`（基线 tools 根 159 条，若为新脚本补了测试则相应增加）。

---

## 六、回滚

```sh
cd <本仓根>
rm -f conftest.py tools/run_all_tests.py tools/count_tests.sh
git checkout HEAD -- CONTRIBUTING.md tools/README.md
```
无迁移、无产物 → 零残留。

---

## 七、执行者纪律

- 完成后在**本文件末尾**追加「## 验收记录」，逐条贴实际输出
- **注入验证（第 3 条）必须真做并贴出判红时的输出**
- 不提交；不做达标判定

---

## 验收记录（执行者：vox-card-executor · 2026-09-28）

> **路径占位约定（T56 占位化，2026-09-28）**：本节实跑记录里的命令同样用占位符书写，**不含本机绝对路径**。`<本仓根>` / `<kefu-agent 仓根>` **不是可执行路径，别直接复制**——**可执行的等价做法**是：先 `cd` 进对应仓根，再把 `<仓根>/xxx` 写成相对路径 `xxx` 执行。本卡 §五 验收节同款说明为准。

### 落点与白名单核对

| 文件 | 动作 | 说明 |
|---|---|---|
| `conftest.py` | 新建 | 仓根 pytest 引导：`importlib` 模式隔离十一个同名 `tests` 包 + 仓根入 `sys.path` |
| `tools/run_all_tests.py` | 新建 | 单一真源：逐根子进程 `unittest discover`、分项计数、非零退出并点名失败根 |
| `tools/tests/test_run_all_tests.py` | 新建 | 12 条新测试（tools 根 159 → 171） |
| `tools/README.md` | 改 | 文件表登记 `run_all_tests.py` + 新增「run_all_tests（T48）」小节（口径/退出码/三条硬约束） |
| `CONTRIBUTING.md` | 改 | 手抄 `for d in …` 循环换成 `python3 tools/run_all_tests.py` |

**零冻结区改动**：`git status` 里本卡的改动只有上表 5 个文件（`adapters/` 下的
`hit_query.py` / `normalize.py` / `mcp_vox/tools.py` / `adapters/textmatch/` 是 **T47 并行在改**，
不是本卡产物）。`core/ rules/ assets/ compiler/ runtime/ eval/ cli/ trigger/ packs/ .github/
docs/13 docs/18 docs/20 docs/22 README.md CHANGELOG.md` 全部未动。
**测试文件本体一行未改**（含 import 行）——修法走仓根 `conftest.py`，不是改测试。

### 逐条验收

#### 1. pytest 根目录零 error ✅

```console
$ cd <本仓根>
$ python3 -m pytest --collect-only -q 2>&1 | tail -2
1601 tests collected in 1.48s
```

- 修改前基线：`1390 tests collected, 7 errors`（`ModuleNotFoundError: No module named 'tests.test_structure_budget'`）；
- 修改后：**零 error**，`1601 ≥ 1390` ✅（含卡里说的「收齐后应更多」）。
- 本机 pytest 可用（`pytest 9.1.1` / Python 3.14.6），不存在「未安装」的情况。
- **额外抽验**（importlib 模式不只可收集，还要能真跑）：
  ```console
  $ python3 -m pytest rules core tools/tests/test_run_all_tests.py -q
  95 passed, 5 subtests passed in 0.16s
  ```

#### 2. 单一真源可跑且计数分项 ✅

```console
$ python3 tools/run_all_tests.py; echo "exit=$?"
…
== 汇总 ==
ran=1601 skipped=24 executed=1577 failed=0 failures=0 errors=0 roots=11
FAILED roots: (none)
exit=0
```

逐根 `Ran N tests`（节选）：

```
== core ==      Ran 62 tests    == assets ==    Ran 114 tests
== rules ==     Ran 21 tests    == adapters ==  Ran 335 tests
== compiler ==  Ran 185 tests   == runtime ==   Ran 191 tests
== eval ==      Ran 239 tests   == cli ==       Ran 89 tests
== tools ==     Ran 171 tests   == trigger ==   Ran 159 tests
== packs ==     Ran 35 tests / OK (skipped=24)
```

**关于基线 1570 / 24 / 1546 与实测 1601 / 24 / 1577 的差**（动态统计，未写死）：
卡里基线 1570 是**未含 T47** 的数。收尾时的增量有两个来源，均已用 `--json` 逐根核对：

| 根 | 卡里基线 | 收尾实测 | Δ | 来源 |
|---|---:|---:|---:|---|
| tools | 159 | 171 | +12 | **本卡**新增 `test_run_all_tests.py` 12 条 |
| adapters | 316 | 335 | +19 | **T47 并行**（`adapters/textmatch/` 新增） |
| 其余九根 | 1095 | 1095 | ±0 | 无变化 |
| 合计 | 1570 | 1601 | +31 | 12（本卡）+ 19（T47） |

`skipped=24` 恒定（`packs` 根缺本机 kefu 仓 yaml 的常态）。`executed = ran - skipped = 1577`。

#### 3. 失败会被拦下（注入验证）✅ 真做了

注入方式：新建临时文件 `core/tests/test_zz_t48_injected_red.py`（含 `self.assertTrue(False)`），
跑完立刻删除——**没有改动任何既有测试文件**。

**（a）全量 11 根 + core 注入必红**（10 绿 1 红，最强形态：证明失败不被其他根的绿掩盖）：

```console
$ python3 tools/run_all_tests.py > /tmp/vox_inj_full.log 2>&1; echo "exit=$?"
exit=1
$ grep -E "^== |^ran=|^FAILED roots" /tmp/vox_inj_full.log
== core ==
== rules ==
== assets ==
== adapters ==
== compiler ==
== runtime ==
== eval ==
== cli ==
== tools ==
== trigger ==
== packs ==
== 汇总 ==
ran=1602 skipped=24 executed=1578 failed=1 failures=1 errors=0 roots=11
FAILED roots: core
```

**（b）只跑 core 根（卡里指定的形态）**：

```console
$ python3 tools/run_all_tests.py --root core 2>&1 | grep -E "^== |^FAILED|^ran=|FAIL: "
== core ==
FAIL: test_must_fail (tests.test_zz_t48_injected_red.TestInjectedRed.test_must_fail)
FAILED (failures=1)
== 汇总 ==
ran=63 skipped=0 executed=63 failed=1 failures=1 errors=0 roots=1
FAILED roots: core
$ python3 tools/run_all_tests.py --root core > /tmp/vox_inj.log 2>&1; echo "exit=$?"
exit=1
```

**（c）改回后**：

```console
$ test -e core/tests/test_zz_t48_injected_red.py && echo STILL || echo 已删除
已删除
$ git status --porcelain core/          # 无输出 = 冻结区零残留
$ python3 tools/run_all_tests.py --root core > /tmp/vox_clean.log 2>&1; echo "exit=$?"
exit=0
$ tail -3 /tmp/vox_clean.log
ran=62 skipped=0 executed=62 failed=0 failures=0 errors=0 roots=1
FAILED roots: (none)
```

判红 → `exit=1` + 点名 `core`；改回 → `exit=0` 且 `ran` 回到 62。**注入临时文件已删除，
`core/` 无任何残留**（`git status --porcelain core/` 空输出）。

#### 4. CONTRIBUTING 单一真源 ✅

```console
$ grep -n "run_all_tests\|unittest discover" CONTRIBUTING.md
10:1. **先跑全量测试**：`python3 tools/run_all_tests.py`——全绿（`exit=0`）是底线。
14:   **不要再手抄逐根 `unittest discover` 的 shell 循环**：那份循环的唯一副本在
15:   `tools/run_all_tests.py`，CI 与本文档都调它（T48）；数字口径见 `tools/README.md`。

$ grep -nE "for d in|for \w+ in (core|rules)" CONTRIBUTING.md
(无)
```

手抄的 `for d in core rules assets adapters …` 循环**已不存在**（第 14–15 行是
「不要手抄」这句提醒本身，不是循环；且刻意写成 `unittest discover` 而非 `for d in …`，
免得机械 grep 判不出「这是散文还是循环」）。`.github/workflows/tests.yml` 按卡要求**未动**（T49 的活）。

#### 5. 零依赖纪律未破 ✅

```console
$ grep -rn "import pytest\|pip install" tools/run_all_tests.py conftest.py
grep exit=1        # 零命中
```

`run_all_tests.py` 的全部 import（纯标准库）：

```console
$ grep -nE "^(import|from) " tools/run_all_tests.py
35:from __future__ import annotations
37:import argparse
38:import json
39:import subprocess
40:import sys
41:import unittest
42:from pathlib import Path
```

**回落实测**（造一个会抛 ImportError 的假 `pytest` 塞进 `PYTHONPATH`，模拟「本机没装 pytest」）：

```console
$ python3 -c "…把 /tmp/vox_no_pytest/pytest.py 写成 raise ImportError(…)…"
屏蔽生效: ImportError: 模拟未安装 pytest
$ python3 tools/run_all_tests.py --root core --root rules     # 同一环境
无 pytest 下 exit = 0
['ran=83 skipped=0 executed=83 failed=0 failures=0 errors=0 roots=2']
```

**「跑测试」不依赖 pip** 成立。

#### 6. 结构预算与既有测试零回归 ✅

```console
$ python3 tools/structure_budget/check.py --no-write | tail -1
结构预算：全部合规
$ python3 -m unittest discover -s tools 2>&1 | tail -1
OK
$ python3 -m unittest discover -s tools 2>&1 | tail -3
Ran 171 tests in 1.549s
OK
```

`tools` 根 159 → **171**（本卡新增 12 条，验收标准第 6 条已预告「若为新脚本补了测试则相应增加」）。
结构预算全程带 `--no-write`，未改 `snapshot.json` / `LEDGER.md`；
预算表 `.py` 文件数 57 → 60 全部来自 T47 的 `adapters/textmatch/`，本卡新增的三个文件
（仓根 `conftest.py`、`tools/run_all_tests.py`、`tools/tests/test_run_all_tests.py`）**不计入预算**
（预算只扫 core/rules/compiler/assets/runtime/adapters/trigger/eval/cli 九个层目录）。

### 执行中发现并修掉的一个静默降级洞（值得记一笔）

补测试时真踩出来的：`TestLoader.discover(start_dir=<不存在的目录或一个文件>)`
**不抛异常**，而是返回空 suite → `ran=0 / failed=0 / exit=0`。
也就是「CI 里某个根名打错一个字母 → 这一根静默按 0 条全绿」，正是 `AGENTS.md`
禁止的静默降级，且在跑批场景下**失败会被 0 条掩盖**（本卡第三设计约束要防的正是这个）。

修法：新增 `_resolve_root_dir()` 显式校验根存在且是目录，不通过就记 `errors=1` 并点名。
两条回归用例钉住（`test_missing_root_is_counted_as_error_not_silently_zero`、
`test_file_instead_of_dir_is_counted_as_error`）。
另加一道交叉校验：子进程**退出码**与回报**计数**任一为红都算红，哨兵行缺失（被信号杀死 /
解释器起不来）记 error——不许「没回报就当过了」。

### 交给验收方的三个说明（不做达标判定，只交事实）

1. **数字是动态的**：卡里基线 1570 未含 T47。本卡不改任何写死数字，汇总一律当场统计；
   `tools/README.md` 里的示例行明确标了「样例而非契约」。
2. **`.github/workflows/tests.yml` 仍抄着旧循环**（卡里明写「本卡不许改 CI」）。
   `run_all_tests.py` 的 `TEST_ROOTS` 顺序与它一致，**T49 改 CI 时直接删循环调本脚本即可**。
3. **`tools/count_tests.sh` 未新建**（卡里列为「或并入上者 · 可选」）：
   对外一行数字已由 `python3 tools/run_all_tests.py` 的末行分项汇总 +
   `--json` 覆盖，再加一个 shell 包装只会多一份要同步的副本。

