# T49 · CI：加非测试关卡 + 退出单一真源

> 批次：第三十三批 · 拍板人：主会话（2026-09-28 T1 决策层开发交付票 + 独立验收）
> 性质：`.github/` 扩展区配置，**零开批**；依赖 T48 的 `tools/run_all_tests.py` 已就位

---

## 一、为什么开这张卡（事实）

`.github/workflows/tests.yml` 全文只有一个 job、三个 step：

1. `runs-on: macos-latest`（第 12 行，注释自陈「Linux runner 无 say → 夹具失败」）
2. 构建 heat-kefu 包（第 22 行）
3. `for d in core rules assets ...` 循环跑 `unittest discover`（第 24-29 行）

**缺口**（`grep -c "run:" .github/workflows/tests.yml` → 只算 2 个真 run 步）：
- ❌ **不跑结构预算**：`grep -rn "structure_budget" .github/workflows/tests.yml` → 无输出。
  所以「R09 结构预算」这道门禁只在本机靠人记，CI 里等于不存在。
- ❌ **不跑 CLI 冒烟**：`bin/vox` 五个命令（`docs/08-CLI口径.md` 退出码 0/2/3/4/5 冻结）
  没有一条在 CI 里被验过。退出码是**对外契约**，冻在文档里却无机器保护。
- ❌ **测试跑批仍是手抄循环**：与 `CONTRIBUTING.md` 重复（T48 已把 CONTRIBUTING 改成调
  `tools/run_all_tests.py`，CI 还没跟上 → 两处会漂移）。
- ❌ **测试数无机器出处**：README 宣称 1,547，但仓内没有任何命令能产出这个数
  （实测 `unittest` 口径 ran=1601/skipped=24/executed=1577，见 T48 收尾）。

**已知并接受的**：`runs-on: macos-latest` **本卡不动**。原因：`docs/13 §五#27` 已把它登记为
治理项，且解它要动缺省适配器（冻结区）——那是独立决策，不在本卡范围。**但 CI 必须在
`macos-latest` 上把非测试关卡全跑起来**，让「本机绿≠CI 绿」这个已栽过四次的坑有牙齿。

---

## 二、目标

CI 从「只跑测试」升级为「跑测试 + 三道非测试关卡」，且测试跑批改走单一真源。

**明确的非目标**：
- ❌ 不改 `runs-on`（不加 Linux 档、不动 macOS 绑定）
- ❌ 不动任何冻结区代码
- ❌ 不引入第三方 action（零依赖纪律）
- ❌ 不改测试根清单（顺序与 T48 的 `run_all_tests.py` 保持一致）
- ❌ 不做发布/打 tag 动作（`git remote` 为空，打 tag 无意义——见 §六）

---

## 三、落点：`.github/workflows/tests.yml`

保留现有 job 与三档矩阵，在此之上**加关卡**：

| 关卡 | 命令 | 为什么必须有 |
|---|---|---|
| 结构预算 | `python3 tools/structure_budget/check.py --no-write` | 本卡头号缺口。**必须带 `--no-write`**（不带会重写台账，在 CI 里等于制造 diff） |
| CLI 冒烟 + 退出码 | 见 §五第 3 条的命令 | 退出码是对外契约（`docs/08`），冻在文档里却无机器保护 |
| 发布树卫生 | `git archive HEAD \| tar -t` + 断言 | 分发排除靠 `.gitattributes export-ignore` 机器强制（`docs/20`），CI 必须持续验它没被违反 |

**测试跑批改为**：`python3 tools/run_all_tests.py`（T48 单一真源），删掉手抄 for 循环。
保留 heat-kefu 构建步（packs 根的真包断言依赖它）。

**关键约束（写卡时钉死）**：
- 任何关卡失败 → **整个 job 非零退出**，不许 `|| true`、不许 `continue-on-error`。
  这是本项目吃过亏的地方（`docs/13 §八#22`：「负例因 ffmpeg 不存在抛了同一个异常而通过」=
  假绿）。**假绿比红更坏。**
- 关卡失败时的输出必须**点名是哪个关卡**（便于定位，不要只看到 job 红）。
- CI 徽章（`README.md` 顶部）是唯一的对外状态灯，**不许加第二个 workflow**。

---

## 四、文件白名单

**可改**：
```
.github/workflows/tests.yml       （改）
docs/tasks/T49-*.md               （本卡验收记录）
```

**禁改**：
```
conftest.py  tools/  adapters/  core/ rules/ assets/ compiler/ runtime/ eval/ cli/ trigger/ packs/  ← 别的卡/冻结区
README.md  CHANGELOG.md  docs/13 docs/18 docs/20 docs/22  .gitattributes  LICENSE   ← 别的卡的活
```

---

## 五、验收标准（逐条可复跑）

> **路径占位约定（T56 占位化，2026-09-28）**：本节命令一律用占位符书写，**不含本机绝对路径**（口径见 `docs/18 §一`「绝对路径一律 0」）。
> - `<本仓根>` = vox-type 仓根目录。**可执行的等价做法**：先 `cd` 到本仓根再执行本节命令；
>   命令里的 `<本仓根>/xxx` 写成相对路径 `xxx` 即可（`<本仓根>` 本身不是可执行路径，别直接复制）。

1. **workflow 语法有效**
```sh
cd <本仓根>
python3 -c "import sys,yaml" 2>/dev/null && python3 -c "
import yaml;d=yaml.safe_load(open('.github/workflows/tests.yml'))
print('jobs:',list(d['jobs']));print('steps:',[s.get('name','<unnamed>') for s in d['jobs']['unittest']['steps']])
" || echo "无 yaml 模块，改用人工核对：缩进与 key 结构须合法"
```
期望：能解析出 `jobs: ['unittest']` 与 ≥6 个 step（含原三步 + 新增关卡）。
若本机无 pyyaml，**如实说明**并改用「逐行人工核对 + 与 git diff 对照」作为证据，不许跳过。

2. **结构预算关卡带 --no-write**
```sh
grep -n "structure_budget" <本仓根>/.github/workflows/tests.yml
```
期望：有输出，且**同行含 `--no-write`**。

3. **退出码冒烟在 workflow 里**
```sh
grep -n "pack check\|pack build\|vox run\|vox verify\|vox bench" <本仓根>/.github/workflows/tests.yml
```
期望：有输出。**具体断言由本卡的实现段定义**（见 §六 验收细则），且**失败必须非零退出**。

4. **测试跑批单一真源**
```sh
grep -n "run_all_tests\|for d in" <本仓根>/.github/workflows/tests.yml
```
期望：出现 `tools/run_all_tests.py`；**不再有** `for d in` 手抄循环。

5. **发布树排除零违规**（本卡新增的第三道关卡）
```sh
cd <本仓根>
git archive HEAD | tar -t | grep -cE 'packs/.*/golden/|semantic_probe\.jsonl' ; echo "count_rc=$?"
```
期望：`0`。**注意**：`git archive HEAD` 导出的是**已提交**内容，而本卡未提交，
所以这条现在跑的是旧快照——验收方在提交后必须**重跑一次**确认仍为 0。

6. **零第三方 action**
```sh
grep -n "uses:" <本仓根>/.github/workflows/tests.yml
```
期望：只有 `actions/checkout@v4` 与 `actions/setup-python@v5` 两行（与改动前一致），无新增。

7. **YAML 变更不影响本地可复现性**
```sh
cd <本仓根>
python3 tools/run_all_tests.py 2>&1 | tail -2
```
期望：`failed=0` 且 `exit=0`（确认改 workflow 没有顺手改坏别的东西）。

---

## 六、验收细则（CLI 冒烟关卡具体要验什么）

按 `docs/08-CLI口径.md` 的冻结退出码 `0/2/3/4/5`，冒烟关卡**至少**要覆盖：

- `vox pack check <合法包> --json` → **rc=0**
- `vox pack check <非法输入>`（如不存在的包） → **rc≠0**（不得静默 0）
- `vox verify <资产包>` → **rc=0**
- `vox run` 一条已知全命中的 plan（用 `examples/plan.json` + `examples/prebuilt-pack`，
  显式给 `--adapter`，非 mac 或无端点时不依赖 `say`）→ **rc=0**
- 一条**故意错误**的调用 → **rc≠0**（这条最重要：验「该红时真的红」，堵死假绿）

**写实现时的约束**：这些命令里凡是需要真实 TTS 引擎的，**必须在 CI 里显式给 `--adapter` 或
用不依赖引擎的路径**；macOS runner 上 `say` 可用，但**不许依赖它**——依赖它就是 T35/T38
栽过的「环境假设错误」。若某条实在无法在 CI 稳定跑（如需在线端点），**明确不写进 CI**，
在 workflow 里留注释说明「为什么不验」，**不许写一个永远绿的假断言**。

---

## 七、回滚

```sh
cd <本仓根>
git checkout HEAD -- .github/workflows/tests.yml
```
无迁移、无产物 → 零残留。

---

## 八、执行者纪律

- 完成后在**本文件末尾追加「## 验收记录」**，逐条贴命令与实际输出
- **任何关卡不许写成永远绿的假断言**——这是本项目的历史教训，写不出真断言就如实说明「不验」
- 不提交；不做达标判定

---

## 验收记录

> 执行者：`vox-card-executor` · 执行日期：2026-09-28 · 环境：darwin 25.3.0 arm64，
> Python 3.14.6，pyyaml 可用。**本节只贴实际命令与实际输出，不做达标判定。**

### 动手前的工作区基线（回滚参照）

`git status -uall` 动手前已有未提交改动（T47/T48 的活，本卡未触碰）：
`CONTRIBUTING.md` / `adapters/framework_kefu/hit_query.py` /
`adapters/framework_kefu/normalize.py` / `adapters/mcp_vox/tools.py` / `tools/README.md`
（modified），以及 `conftest.py`、`tools/run_all_tests.py`、`adapters/textmatch/**`、
`docs/tasks/T4[789]*.md`、`T5[012]*.md`（untracked）。

### 三项拦截检查

1. **数据分级**：本卡全文无用户录音 / 真实会话 / 内网地址 / token。**通过**。
2. **文件白名单**：本卡只写了 `.github/workflows/tests.yml` 与本卡文件自身。**通过**。
3. **回滚基线**：见上。**通过**。

---

### 标准 1 · workflow 语法有效

```sh
cd <本仓根>
python3 -c "
import yaml;d=yaml.safe_load(open('.github/workflows/tests.yml'))
print('jobs:',list(d['jobs']));print('steps:',[s.get('name','<unnamed>') for s in d['jobs']['unittest']['steps']])
"
```

实际输出：

```
jobs: ['unittest']
steps: ['<unnamed>', '<unnamed>', '关卡1 结构预算（只读，不改台账）', '构建 heat-kefu 包（packs 的真包断言依赖它；不构建则诚实 skip）', '全量测试（单一真源 tools/run_all_tests.py，零第三方依赖）', '关卡2 CLI 冒烟与退出码契约（docs/08 冻结码 0/2/3/4/5）', '关卡3 发布树卫生（git archive 零排除违规）']
```

`jobs: ['unittest']` 保持；step 数 **7**（≥6）。两个 `<unnamed>` 是 `actions/checkout` 与
`actions/setup-python` 两步（改动前同样无 `name:`，非本次引入）。

> **首轮曾解析失败，如实记录**：第一次写入时，关卡2 里内嵌的 `python3 -c '...'` 多行
> Python 源码落在**列 1**，越过了 YAML 块标量的缩进边界 →
> `yaml.scanner.ScannerError: while scanning a simple key ... line 112`。
> 已改为单行 `python3 -c` 断言后解析通过。

### 标准 2 · 结构预算关卡带 --no-write

```sh
grep -n "structure_budget" <本仓根>/.github/workflows/tests.yml
```

实际输出：

```
25:      # --no-write 必须带：不带会重写 tools/structure_budget/{LEDGER.md,snapshot.json}，
32:          python3 tools/structure_budget/check.py --no-write
```

第 32 行同行含 `--no-write`。**带 `--no-write` 实跑不落盘的旁证**：
`git status -uall --short -- tools/structure_budget/` → 空（台账与快照未被改写）。
`check.py:424` 的 `if not args.no_write:` 是唯一写盘分支。

### 标准 3 · 退出码冒烟在 workflow 里

```sh
grep -n "pack check\|pack build\|vox run\|vox verify\|vox bench" .github/workflows/tests.yml
```

实际输出（节选，行号对应最终文件）：

```
39:  run: sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1
82:  expect "pack check 合法包源 packs/heat_kefu" 0 \
85:    sh bin/vox verify packs/heat_kefu_build/heat-kefu-1 --json
87:    sh bin/vox verify examples/prebuilt-pack --json
92:    sh bin/vox run examples/plan.json --pack examples/prebuilt-pack \
97:  expect "pack check 不存在的包 → rc=2（不得静默 0）" 2 \
103:   sh bin/vox run examples/plan.json --pack examples/prebuilt-pack \
109:  sh bin/vox run examples/plan.json --pack examples/prebuilt-pack \
```

**关卡2 本机实跑输出**（从 workflow 里抽出 `run:` 脚本按 GitHub 同等方式执行）：

```
PASS  pack check 合法包源 packs/heat_kefu  rc=0
PASS  verify 本步刚铸出的包  rc=0
PASS  verify 随仓自证包 examples/prebuilt-pack  rc=0
PASS  run 全命中 plan（prebuilt-pack，零引擎调用）  rc=0
PASS  pack check 不存在的包 → rc=2（不得静默 0）  rc=2
PASS  run 引擎不一致 → rc=5 fail-closed（不触引擎）  rc=5
---- 快路零引擎调用断言 ----
hit=4 miss=0 fallback=0 tts_calls=0
PASS  快路纯命中、零引擎调用（tts_calls=0）
PASS 关卡2 CLI 冒烟与退出码契约
关卡2 rc=0
```

`0 / 2 / 5` 三档真实退出码均被比对；任一条不符 → 末尾 `exit 1` → 整 job 非零退出。

**引擎依赖已证伪**（卡 §六「不许依赖 say」）：

```sh
# 把一个假 say 放在 PATH 首位，看引擎是否真被调用
env PATH="/tmp/fakesay:$PATH" sh bin/vox run examples/plan.json \
  --pack examples/prebuilt-pack --adapter adapters.tts_macsay:MacSayTts --json
```
实际输出：`rc=5`，stderr 为 engine_mismatch fail-closed；
`grep -c "FAKE SAY WAS CALLED"` → **0**（`say` 一次都没被调用，引擎检查先于合成）。

> **首轮曾踩中真 bug，如实记录**：关卡2 的 `expect()` 原写成
> `local rc` 与 `rc=$?` **分两行**。`local` 自身返回 0，会把 `$?` 覆盖成 0，
> 于是两条**负例**都被读成 `rc=0` → 报 PASS。实际 CLI 返回的是 2 与 5。
> 这就是 docs/13 §八#22 的假绿形态（且发生在断言器自己身上）。
> 已改为 `local rc="$?"` 同行取值；改前该关卡整体 rc=1（因期望值不匹配），
> 改后逐条读到真实码 2 / 5。
> 另注：正因为负例被误判，**若当初写的是「非零即过」，这两条会永远绿**——
> 现行实现断言的是**具体码值**，比卡 §六最低要求更严。

### 标准 4 · 测试跑批单一真源

```sh
grep -n "run_all_tests\|for d in" .github/workflows/tests.yml
```

实际输出：

```
43:      # run_all_tests.py 失败时非零退出并逐个点名 FAILED 根，无静默通过路径。
44:      - name: 全量测试（单一真源 tools/run_all_tests.py，零第三方依赖）
45:        run: python3 tools/run_all_tests.py
```

`tools/run_all_tests.py` 在位；**`for d in` 零命中**（原手抄循环已删）。
（首轮第 41 行注释里写了 `` `for d in ...` `` 字面量，会让本条验收命令误报，
已改写为「手抄的逐目录 shell 循环」。）

### 标准 5 · 发布树排除零违规

```sh
cd <本仓根>
git archive HEAD | tar -t | grep -cE 'packs/.*/golden/|semantic_probe\.jsonl' ; echo "count_rc=$?"
```

实际输出：

```
0
count_rc=1
```

计数 `0`；`count_rc=1` 是 `grep -c` 无匹配时的正常退出码，非异常。
**关卡3 本机实跑**：`git archive HEAD | tar -t` 得 531 条，判定零违规，`关卡3 rc=0`。

**关卡3 变异测试**（证明不是永远绿的假断言）：往导出清单注入一条
`packs/fin-cs/golden/leak.txt` 后重跑 → 实际输出
`::error::关卡3 发布树卫生失败：导出树里出现了应被 export-ignore 排除的路径` +
该路径，**rc=1**。

**关卡2 变异测试**：把负例期望值从 2 改成 0 后重跑 → 该行
`FAIL … rc=2 期望 rc=0`，**rc=1**。

> 本卡未提交，本条跑的是 `HEAD` 旧快照。按卡 §五#5 的要求，
> **验收方在提交后必须重跑一次**确认仍为 0。

### 标准 6 · 零第三方 action

```sh
grep -n "uses:" .github/workflows/tests.yml
```

实际输出：

```
18:      - uses: actions/checkout@v4
19:      - uses: actions/setup-python@v5
```

仍只有两行，与改动前一致，无新增。

**红线自检**（卡 §三「不许 `|| true`、不许 `continue-on-error`」）：

```sh
grep -n "|| *true\|continue-on-error" .github/workflows/tests.yml
```
实际输出：**零命中**。（首轮关卡3 的注释里写了 `grep -c ... || true` 字面量会被误报，
已改写措辞；关卡3 实现本身用 `awk` 取计数——`grep -c` 无匹配退 1，
加忽略退出码的兜底会把「无匹配」与「管道某段真失败」混成同一种绿。）

**`runs-on` 未动**：`grep -n "runs-on"` → 第 13 行 `runs-on: macos-latest`（仅加了一行说明注释）。

### 标准 7 · YAML 变更不影响本地可复现性

```sh
cd <本仓根>
python3 tools/run_all_tests.py 2>&1 | tail -2
```

实际输出：

```
ran=1601 skipped=0 executed=1601 failed=6 failures=5 errors=1 roots=11
FAILED roots: packs
```

**未达卡中期望的 `failed=0`。如实说明失败原因，与本卡改动无关：**

- 本卡只写了 `.github/workflows/tests.yml`，该文件不被任何测试根 import，
  物理上不可能影响跑批结果。
- 逐根定位：除 `packs` 外的 **10 个根全绿**——
  `python3 tools/run_all_tests.py --root core --root rules --root assets --root adapters
  --root compiler --root runtime --root eval --root cli --root tools --root trigger`
  → `ran=1566 skipped=0 executed=1566 failed=0 failures=0 errors=0 roots=10`。
- 6 条失败全部落在**同一个文件** `packs/heat_kefu/tests/test_source_of_truth.py`：
  ```
  1 heat_kefu.tests.test_source_of_truth.TestExclusionGoesThroughOriginalPath.test_multi_clause_14_hit_by_sequence_with_expected_key_order
  1 heat_kefu.tests.test_source_of_truth.TestExclusionGoesThroughOriginalPath.test_sequence_hit_entries_all_have_rate_normal
  1 heat_kefu.tests.test_source_of_truth.TestExclusionGoesThroughOriginalPath.test_slot_filled_results_not_hit
  1 heat_kefu.tests.test_source_of_truth.TestSourceOfTruthVerbatim.test_sequence_segments_are_verbatim_substrings_of_yaml
  1 heat_kefu.tests.test_source_of_truth.TestSourceOfTruthVerbatim.test_sequence_split_concatenates_back_to_yaml
  1 heat_kefu.tests.test_source_of_truth.TestSourceOfTruthVerbatim.test_yaml_fingerprint_unchanged
  ```
  其中 `test_yaml_fingerprint_unchanged` 是 `YAML_SHA256` 常量与实际 yaml 的 sha256 不符
  （实测 `d893fee8…` vs 常量 `a0d9194a…`）。
- **该文件在本卡动手前不在 `git status` 里，动手后变成 modified，mtime 06:56:51，
  晚于本卡开工、早于本卡的唯一写入（`.github/workflows/tests.yml`，06:57:34）**——
  是**并发会话**在改它，对应本仓并行的 `docs/tasks/T51-packs-yaml证据补齐.md`
  （该卡正是「让恒 skip 的同源断言真跑起来」）。
  `skipped` 由卡 §一记的 24 变成 0、`executed` 由 1577 变成 1601，正是那 24 条
  从 skip 转为实跑的直接证据。改动内容是给 yaml 原文做**多候选路径探测**。
- `packs/` 在本卡**禁改清单**内，且是另一张在跑的卡的活，**故本卡不动、不修**。
  待 T51 收尾（同步 `YAML_SHA256` 与 `admission.md`）后本条应转绿，请验收方重跑确认。

### 三道关卡的本机整跑结果

```sh
for i in 1 2 3; do bash /tmp/g$i.sh; echo "关卡$i rc=$?"; done
```
```
关卡1 rc=0
关卡2 rc=0
关卡3 rc=0
```
（脚本由 workflow 的 `run:` 字段用 pyyaml 抽出，与 GitHub 执行的是同一段文本。）

### 已知遗留（发现但未修，超出本卡范围）

1. **`packs/heat_kefu/tests/test_source_of_truth.py` 当前红**——T51 并发在改，见标准 7。
2. **heat-kefu 构建步仍走 macsay 适配器**（`tests.yml` 第 39 行，缺省
   `adapters.tts_macsay:MacSayTts`）。该步是改动前既有行为，卡 §三明确「保留
   heat-kefu 构建步」，本卡未改。但它意味着**整个 job 仍依赖 `say` 可用**，
   卡 §六「不许依赖 say」目前只对新增的冒烟关卡成立。若要彻底去掉，
   需换 `adapters.tts_omlx:OmlxTts`（会真调端点）或 `--allow-partial` 降级，
   两者都动既有断言，应另开卡。
3. **关卡3 只验「不该有的没有」**，不验「该有的都在」——后者需维护一份必须包含清单，
   属 docs/20 的分发口径。已在 workflow 注释里写明为什么不验，未写假断言。
