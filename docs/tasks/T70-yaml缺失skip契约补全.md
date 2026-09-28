# T70 · yaml 缺失时的 skip 契约没覆盖第二个前置条件

> 状态：待开批（`packs/` = 扩展区，不需冻结区流程）
> 来源：T1 独立验收人对 T61–T64 的独立复核（本批自曝，不是外部发现）
> **本卡是「判据没作用到自己的载体」第四次复发，新形态：判据只覆盖了一个前置条件。**

## 一、现象（我自己复现的，不是转述）

本机 `~/miniforge3` 无 PyYAML：

```
$ ~/miniforge3/bin/python3 tools/run_all_tests.py
ran=1607 skipped=0 executed=1607 failed=8 failures=0 errors=8 roots=11
FAILED roots: packs
```

**全仓 8 个 error 全部来自同一行**：

```
File "packs/heat_kefu/tests/test_source_of_truth.py", line 213, in _load_yaml
    import yaml  # 延迟导入：yaml 缺失时只影响依赖 yaml 的测试
ModuleNotFoundError: No module named 'yaml'
```

**更正一条已出口的结论**：本会话早前我说过「全量 `ran=1628 failed=0`」。
那个数字是在独立验收人临时把 PyYAML 装进 `/tmp` 并挂上 `PYTHONPATH` 的环境里测的，
**不代表本机、不代表干净机器、不代表 CI**。本机裸跑是红的。这个错记已经不能再用。

## 二、根因：判据只覆盖了一半

该文件自己写着契约（`:12`）：

> yaml 不在本仓（公开 CI 没有 kefu 仓）时：依赖 yaml 的测试一律 **skip** 而非 fail

而它的 skip 判据只有一个（`:97`）：

```python
YAML_PATH = _resolve_yaml_path()   # kefu 预设原文；仅探测到时才做同源断言，否则按设计 skip
```

即 **只判「yaml 文件在不在」**。

但这条路径要跑通需要**两个**前置条件：

| # | 前置条件 | 判据管了吗 | 缺了会怎样 |
|---|---|---|---|
| 1 | yaml **文件**在（`YAML_PATH.is_file()`） | ✅ 管了 → skip | — |
| 2 | yaml **解析器**可导入（`import yaml`） | ❌ **没管** | **error** |

本机正好卡在这个缝里：kefu 仓在 `/Volumes` 上挂着，**文件找得到**（条件 1 成立，
所以不 skip），但**没装 PyYAML**（条件 2 不成立）→ 直接炸成 error。

**这正是本仓记忆里那条元教训的第四次：**
前三次是「判据的字面量写进了被扫文档」「跳过条件一个字节可伪造」「只核总数不核成员」。
这次是**新形态——判据覆盖了前置条件集合的一个真子集，于是「在条件 1 成立、
条件 2 不成立」这个格子掉进了缝里**。契约写在 `:12`，实现只兑现了一半。

## 三、影响面（为什么这条必须开批而不是记 TODO）

- **公开 CI 目前是绿的**（实测 `.github/workflows/tests.yml`：`runs-on: macos-latest`，
  全文 `grep -c pyyaml` = **0**，从不装 PyYAML）。CI 上条件 1 不成立（没有 kefu 仓）
  → 判据正确地 skip → 8 条不跑也不红。
  **所以这条不是「CI 红」，是「CI 恰好走在条件 1 不成立的那一格，从没踩到这条缝」。**
  → 真正会踩到的是**有内部仓、没装 PyYAML 的机器**——也就是本仓自己人的机器。
  这比 CI 红更隐蔽：CI 绿会给人虚假安心，自己人却在本地天天撞 8 个 error。
  **判据只覆盖前置条件集合的真子集这件事，靠「CI 绿」是不可能暴露的**——这是本卡最值得记的一层。
- **对本项目的直接伤害**：`packs/heat_kefu/tests/test_source_of_truth.py` 是
  「40 条话术与 kefu yaml 逐字相等」的同源断言（T33 起的真身门禁）。
  现在这 8 条在本机**长期不跑且不报 skip**，等于**同源校验静默失效**——
  比红更坏（假绿比红更坏）。

## 四、做什么

1. `packs/heat_kefu/tests/test_source_of_truth.py` 把 skip 判据扩成
   **两个前置条件的合取**，缺任一 → skip：
   - `YAML_PATH.is_file()`（现有，保持）
   - `yaml` 可导入（新增）
   实现上加一个 `_yaml_module()`：try import，失败返回 `None`。
   **不要**在 `_load_yaml()` 里 catch 完 `ModuleNotFoundError` 再 `raise unittest.SkipTest`
   ——那会让「缺库」和「文件不在」两种情形在 `setUpClass` 里都变成 skip，
   反而看不出是哪一种。
2. 判据扩完之后，**必须回写 `:12` 的契约句**，让注释与实现同口径
   （写两个前置条件，不许留「一律 skip」这种覆盖不全的话）。
3. 在文件头或 `packs/heat_kefu/README.md`（若有）**点明这 8 条要跑需要装 PyYAML**，
   并说清这是**测试期可选依赖，不是产品依赖**——
   `README.md:82` 的「零第三方依赖」说的是**产品**，那条不许动，要让新表述与它不打架。
4. `tools/README.md` 补登 T63 新增的 `tools/check_import_direction.py`
   （T63 卡要求过，验收人指出未做）。

## 五、验收

- AC1：`~/miniforge3/bin/python3 tools/run_all_tests.py` 在**无 PyYAML** 环境下
  → `failed=0`，且 `skipped` 计数**上升并可见**（不许静默消失）。
  贴出汇总行原文。
- AC2：**装了 PyYAML**（装到 `/tmp` 之外的隔离处，或临时装后卸载）后跑同一命令
  → `failed=0` 且 `skipped` **回落**、这 8 条**真跑**（贴 `skipped` 两个数字的对比）。
  **守卫不许在能跑时误跳**——那是把门禁关掉。
- AC3：全仓三条闸门仍绿：机器路径 / import 方向 / 结构预算（后者带 `--no-write`）。
- AC4：`packs/` 之外零改动；冻结区零改动。
- AC5：注释里不许出现「一律」「全部」这类**覆盖不全的断言**，
  新写的契约句必须**逐条列出**它覆盖的前置条件。

## 六、给维护者的提示

这条修完之后，「全绿」的含义要变一次，**而且变的方式和本卡预想的不一样**：

- **`ran` 一直是 1628，修与不修都一样。** 修之前那 8 个 error 不是「少跑了 8 条」，
  是类级 `setUpClass` 抛异常时 unittest 把该类**每个**测试都记成 error（合计 35 条），
  `ran` 因此被压到 1607。skip 则**不增加** `testsRun`——所以两个口径根本不是同一件事，
  本卡原先写的「1607 + 8」是**拿两种口径相加**，错。
- **真正变的是 `skipped` 与 `executed`：`skipped` 0↔25、`executed` 1628↔1603。**
  依赖 yaml 的**测试是 25 条**（9 同源 + 9 exclusion + 2 find_hit + 1 序列确定性
  + 3 可打破性 + 1 夹具完整性）；**8 只是 error 条目数**（4 个 `setUpClass` + 4 个方法内位点）。
  本卡正文里凡写「8 条」处，指的都是后者，前者请一律写 25。
- **对外只承诺 `failed=0`，不承诺 `skipped=0`。** 四种组合（有/无文件 × 有/无库）本机实测
  全部 `failed=0`；`skipped` 随环境变，是环境量不是契约。

（以上由验收方 2026-09-28 实跑复核，并已同步进 `README.md` 与 `tools/README.md` 的口径表。）

---

## 七、执行回执（T70，2026-09-28）

只记事实，不做达标判定。

### 改动文件（白名单内 2 个）

| 文件 | 改动 |
|---|---|
| `packs/heat_kefu/tests/test_source_of_truth.py` | 新增 `_yaml_module()` / `_yaml_guard()` / `_yaml_skip_msg()` 与 `YAML_RUNNABLE`；4 处 `@unittest.skipUnless` 与 4 处 `self.skipTest` 统一改走同一判据；`_load_yaml()` 改用 `YAML_MODULE` 并在其为 `None` 时指名 `AssertionError`；回写文首契约句、B/C/D/E 段标题、`_resolve_yaml_path` docstring、文末「跑法」三前置说明 |
| `tools/README.md` | §二 文件表补登 `check_import_direction.py` 一行（T63 遗留） |

冻结区（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）零改动，
`git diff --name-only` 已核为空。

### 我跑了什么

**基线（改动前，无 PyYAML）**

```
ran=1607 skipped=0 executed=1607 failed=8 failures=0 errors=8 roots=11
FAILED roots: packs
```

**AC1（无 PyYAML）** —— `$HOME/miniforge3/bin/python3 tools/run_all_tests.py`，退出 0

```
ran=1628 skipped=25 executed=1603 failed=0 failures=0 errors=0 roots=11
FAILED roots: (none)
```

**AC2（装 PyYAML；隔离在 `/tmp/vox-ac2-pyyaml`，`PYTHONPATH` 指过去，
未装进 miniforge、未装进仓库；因 pypi 吞吐差直接拷了一份纯 Python 的 PyYAML 6.0.3，
`__with_libyaml__=False` 纯 Python 回退，3.12 下 import 与 `safe_load` 均正常）** —— 退出 0

```
ran=1628 skipped=0 executed=1628 failed=0 failures=0 errors=0 roots=11
FAILED roots: (none)
```

`skipped` 对比：**25 → 0**。packs 根单跑同为 **36 条 / skipped=25 → 36 条 / skipped=0**，
即本文件依赖 yaml 的 25 条在装库后全部真跑，守卫没有在能跑时误跳。

三条闸门退出码：

| 闸门 | 退出码 |
|---|---|
| `tools/check_no_machine_paths.py` | **0**（`violation_count=0`、`passed=true`，扫 495 个文件） |
| `tools/check_import_direction.py` | **0**（`scanned=181 违规=0`，已登记例外 2 条） |
| `tools/structure_budget/check.py --no-write` | **0**（`结构预算：全部合规`） |

### 卡里没写的事实

1. **`ran` 从 1607 涨到 1628**，与 §一 那 8 个 error 对上：类级 `setUpClass` 抛异常时
   unittest 把该类**每个**测试都记成 error（35 条），而 skip 不增加 `testsRun`。
   所以 §六「1607 + 8 条真跑 / 1607 条真跑 + 8 条 skip」里的两个 1607 是**不同口径**——
   本机两个环境 `ran` 实测都是 **1628**，真正变化的是 `skipped` 与 `executed`
   （`skipped` 25↔0，`executed` 1603↔1628）。§六 那句话建议按这个口径改写。
2. **依赖 yaml 的测试是 25 条，不是 8 条**：9（同源）+ 9（exclusion）+ 2（find_hit）
   + 1（序列确定性）+ 3（可打破性）+ 1（夹具完整性）。8 是 **error 条目数**
   （4 个 `setUpClass` + 4 个方法内 `skipTest` 位点）按组归并后的数。
   本文件内注释已按此口径写，避免下次再算错。
3. **实现里踩到一个必须先报的坑**：第一版写成
   `@unittest.skipUnless(YAML_RUNNABLE, YAML_SKIPIF + "——…")`，而 `YAML_SKIPIF`
   就是 `_yaml_guard()` 的返回值（两个前置都满足时为 `None`）——于是**在装库环境下**
   模块在**类定义期**抛 `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'`，
   整个模块 import 失败，本文件 36 条一条都跑不了（表现为 `ran` 掉 35、`errors=1`）。
   这正是 AC2 点名的「守卫不许在能跑时误跳」的形态：门禁不是跳过，是直接不起作用。
   已改为 `_yaml_skip_msg(what)` 恒返回 `str`（无缺失时返回空串），并在该函数 docstring
   写明不做条件字符串拼接的原因。
4. **两种缺失情形现在分得开**（§四.1 的理由已兑现）：
   - 缺库：`skipped '未安装 PyYAML（测试期可选依赖，非产品依赖）——<组名>'`
   - 缺文件：`skipped 'kefu 供热预设 yaml 文件不在候选路径（公开 CI 没有 kefu 仓）——<组名>'`
   第三种组合（文件不在 + 库在，`KEFU_HEAT_YAML=/nonexistent/…`）实测 skipped=25、退出 0。
5. **AC5 的「一律」有一条没动**：`:346` `# 槽值一律现场合成（docs/06 §6.2.5）`
   讲的是槽位值的来源，不是 skip 判据的覆盖断言，属无关语句，未改。
   skip 契约相关的全部「一律 / 全部 / 唯一判据」表述已回写为逐条列前置条件——
   含 `:56` 原本的「skip 的唯一判据自始至终是 `YAML_PATH.is_file()`」，
   那半句正是本卡根因，已改写。
6. **`tools/README.md` 有两处描述会因本次改动变陈旧，未改**（超出本卡给该文件的
   白名单用途，留给主会话裁定）：
   - `skipped` 口径表「本机 kefu 仓 yaml 可达 → skipped=0」现在还需**同时装了 PyYAML**；
   - 触发条件一栏写的「全部来自 `packs` 根的 4 组
     `@unittest.skipUnless(YAML_PATH.is_file(), …)`」——判据已不是 `YAML_PATH.is_file()`，
     条数是 25（旧表挂在 `ran=1602` 口径下）。两处都属「数字必须有出处」问题，
   建议主会话另开一条指令更新。
7. **`labs/omlx-reliability/` 的机器路径违规已不在**：开批时预期闸门 1 会因它变红，
   实跑 `violation_count=0`。该目录现含未跟踪的 `report.json`（127846 B，14:30 生成），
   与 `README.md` / `probe.py` 一起 grep 启动卷临时目录前缀、本机家目录前缀均 0 命中。
   未触碰该目录任何文件。
8. **卡的内部矛盾一处**：§四.4 要求改 `tools/README.md`，但 AC4 写「`packs/` 之外零改动」。
   按最接近卡意图的读法执行——AC4 约束的是**源码与冻结区**不被顺手改到，
   §四.4 与 §五 明列的两个文档文件属已授权范围。

