# T51 · packs：yaml 路径探测多候选 + 热路径 pack 缓存（可选）

> 批次：第三十三批 · 拍板人：主会话（2026-09-28 T1 决策层四票 + 独立验收）
> 性质：`packs/` 扩展区，**零开批**；**不碰冻结区**（`runtime/executor.py` 一行不动）

---

## 一、为什么开这张卡（事实）

### 事实 1：唯一真链路证据默认不执行（**四票一致认定最该先修**）

`packs/heat_kefu/tests/test_source_of_truth.py:31-36`：

```python
YAML_PATH = Path(
    os.environ.get(
        "KEFU_HEAT_YAML",
        str(Path(__file__).resolve().parents[3].parent / "kefu-agent" / "organs" / "客服" / "brain" / "prompts" / "供热预设.yaml"),
    )
)
```

`docs/13 §五#20` 已登记这条「yaml 默认路径漂移」为低severity，但**至今未修**，且独立验收人发现
它比登记的更硬：缺省拼出的路径 `<本仓根>/../kefu-agent` **本机也不存在**
（真实仓在 `<kefu-agent 仓根>`）。

后果：`python3 -m unittest discover -s packs` → `Ran 35 tests ... skipped=24`。
**「真链路 sha256 字节级命中」这条项目唯一的生产性证据，在默认环境下恒不执行。**
新人 clone 后永远看不到它跑起来（`docs/13 §五#20` 原文：「不设 `KEFU_HEAT_YAML` 时本机也走
24 条 skip」）。

### 事实 2：MCP 每次调用重 `load_pack`（架构票提出，本卡**可选**，见 §三）

`adapters/mcp_vox/tools.py` 中 `load_pack(Path(arguments["pack_dir"]))` 出现在
`vox_lookup` 与 `vox_plan` 两个 handler 里，**无缓存**。
`assets` 层已有 O(1) 索引（T27 落地），但**每次进程内重开包 = 重建索引**。
在控制面（CLI/MCP）上，每轮重建一次索引是可优化的。但：**控制面本来就不在毫秒关键路径上**
（`docs/24:35`），所以这是**优化，不是缺口**。

---

## 二、目标

**主目标（必须做）**：让 `packs/heat_kefu` 的同源断言在**候选路径存在时真的跑起来**，
而不是恒 skip。修法是**多候选探测**，不是写死绝对路径。

**次目标（本卡做，但标明可拆）**：`mcp_vox` 加一个**极简** pack 缓存。

**明确的非目标**：
- ❌ 不把绝对路径写进代码或台账（`docs/13 §八#20` 的红线：机器绝对路径泄漏到仓里是发布树违规）
- ❌ 不动 `runtime/executor.py`（冻结区）
- ❌ 不把 `labs/` 的任何脚本升格进产品
- ❌ 不为了让测试变绿而**降低断言强度**（同源断言是 40 条 variant 与 yaml 逐字 `==`，一行不许松）
- ❌ 不删 `KEFU_HEAT_YAML` 环境变量（它是显式指定的口径，必须保留）

---

## 三、落点与实现约束

### 主目标：多候选探测

只改 `packs/heat_kefu/tests/test_source_of_truth.py` 的路径解析逻辑：
- 候选序列（按序尝试，全用**相对推导**，不用机器绝对路径）：
  1. `os.environ["KEFU_HEAT_YAML"]`（显式指定，优先级最高，**保留**）
  2. `<repo>/../kefu-agent/...`（原缺省，保持兼容）
  3. `../../kefu-agent/...`（`docs/13 §五#20` 建议的第二候选）
  4. `os.environ["HOME"]/...` 等常见位置（若要加，须在 docstring 说明）
- 全部候选都不存在 → **仍然 skip**（诚实 skip，不是 fail——公开 CI 确实没有 kefu 仓）。
- ⚠️ **不得**因为「本机能找到」就把某路径写死。本卡的验收要同时证明两件事：
  ①本机能跑起来（不 skip）；②把 `KEFU_HEAT_YAML` 指向不存在的路径时**仍然诚实 skip**
  （堵「靠一个假路径骗过 skip 条件」）。

### 次目标：MCP pack 缓存

- 落点 `adapters/mcp_vox/tools.py`（**注意**：T47 已改过这个文件，你是在 T47 之上叠加，
  **不许回退 T47 的改动**——`git diff adapters/mcp_vox/tools.py` 里必须同时看到 T47 与 T50 之前的改动）。
- 缓存键必须包含 `pack_dir` 的**解析后绝对路径**（不是原始字符串，否则相对路径/大小写会串包）。
- 必须是**进程内、带界**的简单缓存（`functools.lru_cache` 或等价的简单实现），
  **不许**引入第三方依赖、不许实现磁盘缓存、不许做失效协议（MCP server 生命周期内包不会变）。
- ⚠️ **静默失败风险**（项目纪律 4）：缓存如果因为路径别名而返回了**错误的包**，
  那是比慢更坏的后果。缓存键用解析后绝对路径正是为了防这个；**必须在 docstring 写清这个 WHY**。
- 若你判断此项收益不足、风险不明，**可以只做主目标并在验收记录里说明理由**——
  本卡对次目标是「做，但标明可拆」，不是硬指标。

---

## 四、文件白名单

**可改**：
```
packs/heat_kefu/tests/test_source_of_truth.py   （改：路径探测）
packs/AGENTS.md                                 （改：若需登记新口径，先读该文件）
adapters/mcp_vox/tools.py                       （改：仅次目标；须叠在 T47 之上）
docs/tasks/T51-*.md                             （本卡验收记录）
```

**禁改**：
```
core/ rules/ assets/ compiler/ runtime/ eval/ cli/        ← 冻结区
trigger/  tools/  conftest.py  .github/                   ← 别的卡
adapters/framework_kefu/  adapters/textmatch/             ← T47 的产物
README.md  CHANGELOG.md  docs/13 docs/18 docs/20 docs/22  ← T50 的活
packs/heat_kefu/phrases.json  packs/*/admission.md        ← 数据与合规，不许动
任何 *.wav 任何 corpus
```

---

## 五、验收标准（逐条可复跑）

> **路径占位约定（T56 占位化，2026-09-28）**：本节命令一律用占位符书写，**不含本机绝对路径**（口径见 `docs/18 §一`「绝对路径一律 0」）。
> - `<本仓根>` = vox-type 仓根目录。**可执行的等价做法**：先 `cd` 到本仓根再执行本节命令；
>   命令里的 `<本仓根>/xxx` 写成相对路径 `xxx` 即可（`<本仓根>` 本身不是可执行路径，别直接复制）。
> - `<kefu-agent 仓根>` = 内部 kefu-agent 仓根目录，**仓外、只读**；同样需先 `cd` 进去，
>   其后命令按相对路径写（如 `find . -name … `）。

1. **同源断言在本机真的跑起来（不是 skip）**
```sh
cd <本仓根>
python3 -m unittest discover -s packs 2>&1 | tail -2
```
期望：`Ran 35 tests` 且 **`skipped` 从 24 降到显著更小**（理想是 0；
若因本机无 yaml 依赖或 kefu 仓不在候选路径，如实记录实际数与原因，**不许伪造**）。
**注入验证**：把 `KEFU_HEAT_YAML` 指向不存在的路径
```sh
KEFU_HEAT_YAML=/nonexistent/供热预设.yaml python3 -m unittest discover -s packs 2>&1 | tail -2
```
期望：**仍然 skip，且条数回到 24 附近**（证明 skip 条件诚实，不是被绕过）。

2. **无机器绝对路径泄漏进仓**
```sh
cd <本仓根>
python3 tools/check_no_machine_paths.py ; echo "rc=$?"
```
期望：**`"passed": true`**（rc=0）。新增的候选路径必须全是相对推导或 `$HOME`/`$KEFU_HEAT_YAML`。

> **T58 改写说明**：此处原为一段 `grep -nE` 正则。判据的正则会扫到写判据的那一行
> （**判据自指**），于是这条门禁恒非零、事实上无法自动化。正则与前缀已收进
> `tools/check_no_machine_paths.py`，本处只留调用；确需豁免的行在行内标
> `# scan-exempt: <理由>`，不留「悄悄不查」的口子。

3. **断言强度未降**
```sh
cd <本仓根>
git diff packs/heat_kefu/tests/test_source_of_truth.py | grep -E "^-.*(assert|==)" ; echo "rc=$?"
```
期望：**无输出**（rc=1）——不许删改任何断言行。

4. **发布树仍零违规**（本卡改了 tests/，确认没把 yaml 或语料带进可分发范围）
```sh
cd <本仓根>
grep -c "export-ignore" .gitattributes
```
期望：`≥2`（与改动前一致，未被动过）。

5. **全量零回归**（次目标若做了，必须验）
```sh
cd <本仓根>
python3 tools/run_all_tests.py 2>&1 | tail -2 ; echo "exit=$?"
```
期望：`failed=0`、`exit=0`。**注意**：`skipped` 数会因第 1 条而下降，这是**预期改善**，
在回报里写清前后对比。

6. **MCP 缓存（若做了）**
```sh
cd <本仓根>
grep -n "cache\|lru_cache" adapters/mcp_vox/tools.py
```
期望：有输出；且 `python3 -m unittest discover -s adapters 2>&1 | tail -1` → `OK`。
**必须有一条测试覆盖「同一 pack_dir 连续两次调用只 load 一次」**，
以及「不同 pack_dir（含同一目录的不同写法）不串包」。

7. **结构预算**
```sh
cd <本仓根>
python3 tools/structure_budget/check.py --no-write | tail -1
```
期望：`结构预算：全部合规`。

---

## 六、回滚

```sh
cd <本仓根>
git checkout HEAD -- packs/
# 次目标若已做：
git checkout HEAD~1 -- adapters/mcp_vox/tools.py   # 注意：T47 也在此文件，回滚要挑对提交
```
无数据迁移、无包格式变更 → 零残留。

---

## 七、执行者纪律

- 完成后在**本文件末尾追加「## 验收记录」**，逐条贴命令与实际输出
- **不许为了「让测试跑起来」而降低断言强度或伪造路径存在性**（这是本卡的红线）
- 不提交；不做达标判定

---

## 验收记录

> 执行：`vox-card-executor`（只回事实，不做达标判定）· 2026-09-28
> 改动文件：**仅 1 个** —— `packs/heat_kefu/tests/test_source_of_truth.py`（+80 / -7）
> 未动：`adapters/mcp_vox/tools.py`（T47 改动原样保留）、`packs/heat_kefu/phrases.json`、
> 任何 `admission.md`、`runtime/executor.py`、冻结区。

### 落点：多候选探测（主目标，已做）

替换原「单条缺省路径」为 `_yaml_candidates()` + `_resolve_yaml_path()`，候选按序：

| # | 候选 | 推导方式 | 本机 `is_file()` |
|---|---|---|---|
| ① | `$KEFU_HEAT_YAML` | 环境变量（显式指定） | — |
| ② | `<repo>/../kefu-agent/…` | 相对本仓推导（原缺省，保留兼容） | **False** |
| ③ | `<repo>/../../kefu-agent/…` | 相对本仓推导（`docs/13 §五#20` 第二候选） | **True ← 命中** |
| ④ | `$HOME/kefu-agent/…` | 环境变量 | False |

全部候选均为相对推导或环境变量，**无任何机器绝对路径**。

**关键语义（显式指定即权威）**：设了 `$KEFU_HEAT_YAML` 就只认它，**不回退**去试 ②③④。
回退会把「用户指定的文件不存在」悄悄换成「另一个文件」= 静默降级（纪律 4），比不跑更坏。

> ⚠️ 实施中此语义**第一版写错了**：`_resolve_yaml_path` 仍会往下遍历候选，
> 导致注入验证一跑就发现「指向不存在路径时仍然命中了候选③」。
> 正是验收第 1 条的注入验证把它抓出来的，已修正为「显式指定即直接返回、不遍历」。
> 记录在此，因为它是「注入验证确有拦截力」的实证。

### 逐条验收

#### 1. 同源断言在本机真的跑起来 — ⚠️ **skip 达标，但暴露出 6 条真实失败**

改动前：
```
$ python3 -m unittest discover -s packs 2>&1 | tail -2
Ran 35 tests in 0.040s
OK (skipped=24)
```
改动后（默认探测）：
```
$ python3 -m unittest discover -s packs 2>&1 | tail -2
Ran 35 tests in 0.219s
FAILED (failures=5, errors=1)
```
**`skipped` 由 24 → 0**（目标达成：同源断言真的执行了）。**`Ran` 仍为 35**（未增删测试）。

**注入验证**（`KEFU_HEAT_YAML` 指向不存在的路径）：
```
$ KEFU_HEAT_YAML=/nonexistent/供热预设.yaml python3 -m unittest discover -s packs 2>&1 | tail -2
Ran 35 tests
OK (skipped=24)
```
**skip 诚实回到 24，未被绕过** ✔（改动前同一命令同样是 `OK (skipped=24)`，前后一致）。

**但 6 条失败是真的**，不是本卡引入，也不是环境问题：
```
FAIL: test_yaml_fingerprint_unchanged
      'd893fee866…' != 'a0d9194a95a876d12103381c3373c63a98d021e23853a998429bb3ae2cbbfe44'
FAIL: test_sequence_split_concatenates_back_to_yaml
FAIL: test_sequence_segments_are_verbatim_substrings_of_yaml
      key=repair_confirm_question__2 不是 yaml 原文的连续子串
FAIL: test_multi_clause_14_hit_by_sequence_with_expected_key_order
FAIL: test_sequence_hit_entries_all_have_rate_normal
      repair_confirm_question 必须整体命中（miss_reason='text_not_prebaked'）
ERROR: test_slot_filled_results_not_hit   → KeyError: 'rawType'
```
**归因（一条根因）**：kefu 侧 yaml 自 T31b/T33（2026-09-21~23）记录基线后**已发生漂移**，
当前 sha256 与 `YAML_SHA256` 常量不符。漂移集中在两处：
- key `repair_confirm_question` 的拆句结构/文本变了；
- 某带槽 key 的 yaml 值新增了 `{rawType}` 占位符（`SLOT_FILL_EXAMPLES` 未覆盖）。

**已排除「我找错文件」这一可能**：
```
$ find <kefu-agent 仓根> -name "供热预设.yaml" -type f | wc -l
1        # 全仓仅此一份，即文档记载的规范路径，不存在选错副本
```
> **占位说明（T56）**：上面那行是**占位形态、不可直接复制执行**——`<kefu-agent 仓根>` 是内部仓
> 根目录的占位符，不是真实路径（本仓文档一律不写本机绝对路径，见 `docs/18 §一`）。
> **可执行的等价命令**：先进入 kefu-agent 仓根，再在该仓根下执行
> `find . -name "供热预设.yaml" -type f | wc -l`——从仓根做递归查找，与上面那行语义相同。

**为什么不修**（本卡无权修，也不该修）：
- `phrases.json`、`admission.md` 在本卡**禁改清单**里；
- 改 `YAML_SHA256` = 把基线改成现状 = **抹掉 fail-closed 钩子**（卡片明令不许）；
- 给 `SLOT_FILL_EXAMPLES` 补 `rawType` = 「把测试拟合到新数据」，且它属 `admission.md` 对账产物。

→ **这是一张新卡的活**（话术同源重对账 + 合规复核），不是 T51 的活。
T51 的职责（让证据能跑）在**机制层面已完成**——而且正是它把这条被埋了 7 天的漂移挖了出来。

#### 2. 无机器绝对路径泄漏 — ✔
```
$ python3 tools/check_no_machine_paths.py --json ; echo "rc=$?"
"passed": true      # T58 改写：判据收进脚本，原 grep 正则恒命中写它的那一行（判据自指）
```

#### 3. 断言强度未降 — ✔
```
$ git diff packs/heat_kefu/tests/test_source_of_truth.py | grep -E "^-.*(assert|==)" ; echo "rc=$?"
rc=1        # 无输出，一行断言未删未改
```
删除的 7 行全是旧路径解析代码，无任何 `assert`/`==`。40 条逐字 `==`、码位序列比对、
substring 断言全部原样。

#### 4. 发布树仍零违规 — ✔
```
$ grep -c "export-ignore" .gitattributes
6        # ≥2，与改动前一致（本卡未动 .gitattributes）
```

#### 5. 全量零回归 — ✘ **不达标**（`failed=7`）
```
$ python3 tools/run_all_tests.py ; echo "RUNNER_EXIT=$?"
ran=1601 skipped=0 executed=1601 failed=7 failures=6 errors=1 roots=11
FAILED roots: adapters, packs
RUNNER_EXIT=1
```
`failed=7` 拆开看：
- **6 条 = 第 1 条的 packs 漂移**（真实、稳定可复现）；
- **1 条 = 与本卡无关的环境 flake**：本轮是
  `adapters/tts_macsay.tests.test_adapter.TestSynthesizePositive.test_synthesize_uses_tempfile`
  （依赖 macOS `say`）。该非 packs 根**每轮换一个**：
  | 运行 | 非 packs 失败根 |
  |---|---|
  | 注入版（24 skip） | `eval`（`test_fast_arm_not_slower_than_slow`，墙钟性能断言） |
  | 满载并发版 | `tools`（`test_fetch_success_floor`，`/tmp` sources 缺失） |
  | 空闲重跑 | `adapters`（`tts_macsay`） |

**`packs` 之外的根在我改动下是干净的**——注入版（我的代码路径完全失效、行为等同改动前）
`packs` 零失败，而 `eval` 仍然失败；且 `python3 -m unittest discover -s eval` 单独跑
`Ran 239 tests / OK`。→ 这些是 `run_all_tests.py` 既有的负载/顺序 flake，非本卡引入。
但**验收第 5 条的字面要求（`failed=0`、`exit=0`）确实没达到**，如实记录，不做判定。

**skip 前后对比**：`skipped=24 → skipped=0`（`executed` 1577 → 1601）。

#### 6. MCP pack 缓存（次目标）— **未做**，理由如下

- 卡上写明「做，但标明可拆」，可只做主目标并说明理由；
- 验收第 6 条**强制要求**「同一 pack_dir 连续两次只 load 一次」「不同写法不串包」两条测试，
  而 mcp 的测试文件是 `adapters/mcp_vox/tests/test_server.py`，
  **该文件不在本卡文件白名单内**（白名单只给了 `adapters/mcp_vox/tools.py`）。
  要满足第 6 条就必须改白名单外文件 → 越权，故不做；
- 收益本身有限：卡上已认定控制面本就不在毫秒关键路径上（`docs/24:35`），是优化非缺口；
- 附带收益：**完全未触碰 `adapters/mcp_vox/tools.py`**，故不存在回退 T47
  （裸 `entry.text ==` → `adapters.textmatch.find_hit`）的风险。`git diff` 中该文件的
  改动仍全部是 T47 的。

```
$ grep -n "cache\|lru_cache" adapters/mcp_vox/tools.py ; echo "rc=$?"
rc=1        # 无缓存代码（未做，符合预期）
```

#### 7. 结构预算 — ✔
```
$ python3 tools/structure_budget/check.py --no-write | tail -1
结构预算：全部合规
```

### 拦截检查与纪律自证

- **数据分级**：kefu 仓仅做 `is_file()` / `find -name` **存在性探测**，
  未读入任何文件内容；本记录只出现 **key 名**（`repair_confirm_question`、`rawType`）与
  sha256 **指纹**（测试自身打印），**无任何 yaml 正文**。
- **文件白名单**：仅改 `packs/heat_kefu/tests/test_source_of_truth.py`（白名单内）+ 本卡文件。
- **T47 基线**：`adapters/mcp_vox/tools.py` **未被我改动**。

> ⚠️ 需上报的并发观察：会话期间 `git status` 里**新增**了
> `.github/workflows/tests.yml` 的改动（初始基线快照中没有它）。
> 非本卡所为（本卡只 Edit 过 packs 那个测试文件），疑似 T49/CI 卡并发写入。
> **未回滚**（不属本卡、且可能是他人进行中的工作）。提请验收方确认。

### 结论（只陈述事实，不做达标判定）

主目标机制已落地并被验收命令验证：skip 24→0、注入验证诚实回到 24、无绝对路径泄漏、
断言零削弱、结构预算合规。**代价是暴露出 6 条真实的 yaml 漂移失败**，
外加 3 类与本卡无关的既有环境 flake，故第 5 条不达标。
漂移的修复需动 `phrases.json` / `admission.md`（本卡禁改），建议另开卡。
