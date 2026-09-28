# T53 · packs/heat_kefu：话术同源重对账（上游漂移修复）

> 批次：第三十四批 · 拍板人：主会话（2026-09-28 T1 决策层）
> 性质：`packs/` **扩展区**（+ 测试常量），**零开批**；不改任何冻结区
> 来源：T51 把 24 条恒 skip 打开后，门禁开火挖出埋了 7 天的上游漂移

---

## 一、为什么开这张卡（**门禁正常工作，不是 bug**）

`packs/heat_kefu/tests/test_source_of_truth.py:98` 的指纹门禁：

```python
YAML_SHA256 = "a0d9194a95a876d12103381c3373c63a98d021e23853a998429bb3ae2cbbfe44"
# 实测 kefu 侧现值：
#   d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652
```

T51 之前，这条门禁**从未在本机执行过**（`YAML_PATH` 缺省拼 `<repo>/../kefu-agent`，本机真实仓在
`<kefu-agent 仓根>`）→ `Ran 35 tests / skipped=24`。**同源基线在 7 天里一直是失效的，
而没有任何东西报红。**

T51 把路径探测修好后，门禁立刻开火，**6 条红**：

| 测试 | 类别 |
|---|---|
| `test_yaml_fingerprint_unchanged` | 指纹门禁本体 |
| `test_sequence_split_concatenates_back_to_yaml` | 拆句拼回 ≠ yaml 原文 |
| `test_sequence_segments_are_verbatim_substrings_of_yaml` | 拆出的段不是 yaml 子串 |
| `test_multi_clause_14_hit_by_sequence_with_expected_key_order` | 拆句命中顺序 |
| `test_sequence_hit_entries_all_have_rate_normal` | 拆句命中档位 |
| `test_slot_filled_results_not_hit` | 带槽走原路（error） |

**5 条聚在同一个 key**：`repair_confirm_question`（T33 的 14 条多分句拆句之一）。

### 根因（我已独立核实，不是执行者转述）

kefu 仓该文件在基线（9-19 T31）之后连续三次改动：

```
9fc9cd4  2026-09-23  fix(报修/评测): 报修话术落题面锚点 + meta 计数与陈旧数字同步   ← 动到本 key
bd0ae3d  2026-09-24  fix(红线面/转人工): 转人工三拆表 + 拒答话术去内部字样
e16a777  2026-09-24  fix(红线面/拒答): P0-5 泄露内部实现修复 + 三条剧本真违规清零
```

包内现值：`repair_confirm_question__1` = `信息是否正确？`（7 字）、
`__2` = `确认后我为您提交报修单。`（12 字）。**上游改了拆句边界**，
T33 当时照着旧 yaml 手推的拆句清单失效 → 拼不回原文、段不是子串。

### ⚠️ 绝对不许做的事（本卡红线）

- ❌ **不许直接改 `YAML_SHA256` 让它变绿**。那是把 fail-closed 钩子拆掉——
  同源包的全部意义就是「话术变了必须响」，抹掉它等于回到 T51 之前那个坏状态。
- ❌ **不许改测试期望去迁就新数据**（把 `__1`/`__2` 硬编码成新文本让断言通过）。
- ❌ **不许动 `runtime/` `compiler/` `core/` 去「让拆句更宽容」**——那是用降级换绿灯。

---

## 二、本卡要回答的真问题

**拆句边界由谁定义？** 现在是「T33 执行时手推的清单」——**这是一个会随上游漂移而失配的
人工产物**。上游改一次话术，它就静默过期，直到有人手滑改掉指纹。

三条路，**必须选一条并写进文档**：

| 方案 | 做法 | 代价 | 评价 |
|---|---|---|---|
| **A. 拆句清单从 yaml 现算** | 加一个脚本，从当前 yaml 按「句末标点」自动切出 `__1..__N`，与包内 key 对账 | 要定标点边界（`compiler._SENTENCE_TERMINATORS` 已有一份，**必须复用不许另造**） | **首选**：把人工产物变机器产物，失配自动可见 |
| **B. 只做一次性重对账** | 手工把新 yaml 的 14 条重新拆一遍，更新清单 + 指纹 + admission.md | 最小 | **保底**：能立刻变绿，但**下次上游一改又红**——治标不治本 |
| **C. 拆句进包改为整段不拆** | 多分句整段不进包，一律走原路 | 包内 key 从 29 降到 15 | 命中能力下降，**不推荐** |

**我的裁定倾向 A**（把失配从「沉默过期」变成「当下就报」），但 **B 是 A 的前置**——
先按当前 yaml 重新对账（让门禁恢复绿），再决定是否在下一批做 A。

**本卡范围 = 恢复门禁为绿 + 把裁定写进文档 + 明确指出 A 未做**。
不许在本卡顺手实现 A（那是另一张卡的规模）。

---

## 三、落点

### 必做

1. **对账**：`packs/heat_kefu/tests/test_source_of_truth.py` 的三张清单
   （`MULTI_CLAUSE_KEYS` 14 / `SEQ_KEYS` 29 / `SLOT_KEYS` 13）与当前 yaml 逐条对账，
   找出**全部**失配项（不只 `repair_confirm_question` 一个——那 6 条红可能掩盖了别的）。
2. **修正**：清单与包内 `phrases.json` 同步到与当前 yaml 一致。
   - 若某条上游已删或改名 → **如实移出清单并记录**，不许留空壳条目。
   - 若某条拆句数变了（2 段变 3 段）→ 按新边界改，**并同步 `phrases.json`**。
3. **指纹**：**在 1、2 全部对完之后**，才更新 `YAML_SHA256` 与 `admission.md` 的同一份记录。
   顺序不能反（先改指纹就等于抹掉门禁）。
4. **裁定落档**：在 `packs/AGENTS.md` 加一节，写明「拆句清单是**人工产物**、会随上游漂移、
   失配由指纹门禁报红」，并指向本卡是方案 B（已做）+ 方案 A（未做，下一批）。

### 明确不做

- ❌ 不实现方案 A（自动拆句脚本）
- ❌ 不动任何冻结区
- ❌ 不改 `phrases.json` 之外的包数据（`admission.md` 除外，那是记录）
- ❌ 不重建 `packs/heat_kefu` 之外的包
- ❌ 不动 kefu 仓任何文件（**只读**）

---

## 四、文件白名单

**可改**：
```
packs/heat_kefu/tests/test_source_of_truth.py   （清单 + 指纹）
packs/heat_kefu/phrases.json                    （拆句边界同步）
packs/heat_kefu/admission.md                    （对账记录 + 指纹）
packs/AGENTS.md                                 （裁定落档）
docs/tasks/T53-*.md                             （本卡验收记录）
```

**禁改**：
```
core/ rules/ assets/ compiler/ runtime/ eval/ cli/  tools/  adapters/  conftest.py  .github/
labs/
<kefu-agent 仓根>/**                    ← 仓外，只读；一个字都不许写
```

---

## 五、验收标准（逐条可复跑）

> **路径占位约定（T56 占位化，2026-09-28）**：本节命令一律用占位符书写，**不含本机绝对路径**（口径见 `docs/18 §一`「绝对路径一律 0」）。
> - `<本仓根>` = vox-type 仓根目录。**可执行的等价做法**：先 `cd` 到本仓根再执行本节命令；
>   命令里的 `<本仓根>/xxx` 写成相对路径 `xxx` 即可（`<本仓根>` 本身不是可执行路径，别直接复制）。
> - `<kefu-agent 仓根>` = 内部 kefu-agent 仓根目录，**仓外、只读**；同样需先 `cd` 进去，
>   其后命令按相对路径写（如 `find . -name … `）。

1. **对账是全量的，不是只修那 6 条红**
```sh
cd <本仓根>
python3 -m unittest discover -s packs 2>&1 | tail -3
```
期望：`Ran 35 tests` / **`OK`（skipped=0）**。
**并在验收记录里贴出**：对账脚本的输出（逐条 key × 现状 × 判定），
证明不是「改到不红为止」。若对账后仍有条目**故意**移出清单，逐条列出并给理由。

2. **指纹与 admission.md 同步，且一致**
```sh
cd <本仓根>
grep -o 'a0d9194a[a-f0-9]*\|d893fee8[a-f0-9]*' packs/heat_kefu/tests/test_source_of_truth.py packs/heat_kefu/admission.md
```
期望：两处**同一个**新 sha；旧 sha `a0d9194a…` **零命中**（不是两个都留着）。

3. **门禁仍然是活的（注入自证）**——**本卡最关键的一条**
```sh
cd <本仓根>
cp packs/heat_kefu/phrases.json /tmp/t53_phrases.bak
python3 - <<'PY'
import json,pathlib
p=pathlib.Path('packs/heat_kefu/phrases.json')
d=json.loads(p.read_text(encoding='utf-8'))
for e in d['phrases']:
    if e['key'].endswith('__1'):
        e['variants'][0]='篡改一个字符'      # 故意破坏同源
p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
PY
python3 -m unittest discover -s packs 2>&1 | tail -3
cp /tmp/t53_phrases.bak packs/heat_kefu/phrases.json
python3 -m unittest discover -s packs 2>&1 | tail -3
```
期望：**注入后 `FAILED`（门禁真的会红）→ 还原后 `OK`**。
⚠️ 若注入后仍然 `OK`，说明门禁被你改废了——**本卡直接判不通过**。

4. **kefu 仓零改动**
```sh
cd <kefu-agent 仓根> && git status --porcelain -uall | head
```
期望：与 T51 执行前一致（T51 报的是基线状态；**若有变化，核对是否本卡造成**）。

5. **包质检与构建仍过**
```sh
cd <本仓根>
./bin/vox pack check packs/heat_kefu --json | python3 -c "import json,sys; d=json.load(sys.stdin); print('passed=',d.get('passed'),'violations=',d.get('violations'))"
./bin/vox pack build packs/heat_kefu --out /tmp/t53_build/heat-kefu-1 2>&1 | tail -2
```
期望：`passed=True` `violations=[]`；构建 `clean=true`。

6. **未越权：冻结区零 diff**
```sh
cd <本仓根>
git diff --stat -- core rules compiler assets runtime eval cli tools adapters conftest.py .github labs
```
期望：**空**。

7. **裁定已落档且 A/B 状态清楚**
```sh
grep -n "拆句" <本仓根>/packs/AGENTS.md | head
```
期望：有输出，且**同时**写明「已做 B（一次性重对账）」与「未做 A（自动拆句），下一批」。

---

## 六、回滚

```sh
cd <本仓根>
git checkout HEAD -- packs/
```
无外部状态变更（kefu 仓只读）→ 零残留。

---

## 七、执行者纪律

- 完成后在**本文件末尾追加「## 验收记录」**，逐条贴命令与实际输出
- **红线**：不许改 `YAML_SHA256` 让红变绿而不先做对账；不许改测试期望迁就数据；
  不许动冻结区；不许写 kefu 仓
- **不许把 kefu 侧 yaml 的内容粘进回报或卡文件**（只贴 sha、key 名、字数/段数等元数据）
- 不提交；不做达标判定

---

## 验收记录（执行者：vox-card-executor，2026-09-28）

> **路径占位约定（T56 占位化，2026-09-28）**：本节实跑记录里的命令同样用占位符书写，**不含本机绝对路径**。`<本仓根>` / `<kefu-agent 仓根>` **不是可执行路径，别直接复制**——**可执行的等价做法**是：先 `cd` 进对应仓根，再把 `<仓根>/xxx` 写成相对路径 `xxx` 执行。本卡 §五 验收节同款说明为准。

> 以下只贴命令与**实跑输出**。yaml 正文、话术原文、内部标识符一律未入库（只出现 sha / key 名 / 字数 / 段数 / 标点计数）。
> **本节不做达标判定**——「本卡通过与否」由验收方裁定。

### 0. 改了什么（文件 + 行数）

| 文件 | 增/删 | 性质 |
|---|---|---|
| `packs/heat_kefu/phrases.json` | **+3 / −3** | 3 段拆句文本按当前 yaml 现算同步（§3.2 ①②③） |
| `packs/heat_kefu/tests/test_source_of_truth.py` | **+28 / −3**（本卡净改动） | 指纹常量更新（+11/−2）、`SEQ_KEYS` 纪律注释（+12）、夹具补 `rawType`（+5/−1） |
| `packs/heat_kefu/admission.md` | +94 / −18 | 指纹、§0.1 守恒表、§1.5 对账表、**新增 §0.2 重对账记录**、3 条失效节选撤下 |
| `packs/AGENTS.md` | +55 / −1 | **新增 §⑥.5「拆句清单的来源与漂移处理」**（裁定落档） |
| `docs/tasks/T53-*.md` | 本节 | 验收记录 |

⚠️ `git diff --numstat` 对 `test_source_of_truth.py` 显示 **+91 / −10**，
差额（+63/−7）是 **T51 开工前既有**的 `YAML_PATH` 多候选探测改动（`_yaml_candidates` 等），
不是本卡产出。`packs/AGENTS.md` 开工前无既有改动。

`phrases.json` 的 diff 经 `difflib` 校验：**恰好 6 行（3 删 3 增）**，无格式化噪声
（先验证 `json.dumps(indent=2)` 与原文件**逐字节相同**，才做程序化改写）。
另重建了 gitignore 的产物 `packs/heat_kefu_build/`（非入库文件），
否则 C/D 段 `find_hit` 断言会拿旧产物跑。

### 1. AC1 全量对账（不是只修那 6 条红）

先跑对账脚本（逐条 key × 现状 × 判定，判据从 `compiler.source._SENTENCE_TERMINATORS` **读**、未另造；
输出只含元数据）。**修正前**：

```
[1] BAKED_KEYS       声明 40  失配 0
[2] MULTI_CLAUSE    声明 14  失配 0   （14 条段数全部未变）
[3] SEQ             声明 29  失配 3   ['repair_confirm_question__1','repair_confirm_question__2','inject_refuse__1']
[3b] join 回原文             失配 2   ['repair_confirm_question','inject_refuse']
[4] SLOT            声明 13  失配 0   （但 repair_create_ok 槽位集合变了，见 ④）
[5] yaml 未归类 key         5      ['compact_summary_prompt','identity','prefetch_ready_suffix','tone','vague_slot_clarify']
    应删/应增（现算 vs 清单）= [] / []   顺序是否一致 = True
    包内多出/缺失 key       = [] / []
```

**修正后**（同脚本重跑）：

```
  1) BAKED          声明 40  失配  0  []
  2) MULTI_CLAUSE   声明 14  失配  0  []
  3) SEQ            声明 29  失配  0  []
  3b) join 回原文            失配  0  []
  4) SLOT           声明 13  失配  0  []
  5) 未归类 yaml key         5  ['compact_summary_prompt','identity','prefetch_ready_suffix','tone','vague_slot_clarify']
  现算 SEQ_KEYS 应为 29 条 / 顺序一致=True / 包内 69 条无多无缺
```

```
$ python3 -m unittest discover -s packs 2>&1 | tail -3
Ran 35 tests in 0.185s

OK
```

`OK` 而非 `OK (skipped=N)` ⇒ **skipped=0**（`-v` 全量输出中 `skipped` 出现 0 次）。

**故意移出清单的条目：0 条**（无删除/改名漂移——yaml 侧零删除）。
**故意未纳入清单的条目：1 条**，逐条理由：

| key | 元数据 | 为什么不纳入 | 现状 |
|---|---|---|---|
| `vague_slot_clarify` | 55 字 / 0 槽位 / 2 句末标点 | **上游 09-24 新增的话术**（基线 `3b296a2` 无此 key）。补铸需连带改 `script.json`（`test_script_covers_all_baked_keys` 断言「剧本覆盖全部包内 key」），而 `script.json` **不在本卡白名单** | **留挂**，已记入 `admission.md §0.2.3` 并报验收方；查不到即如实未命中、走原路（exclusion 的既定行为），非静默降级 |

其余 4 个未归类 key 是**既有**的非话术配置（`identity` 人设 0 标点 / `tone` 语气 0 标点 /
`prefetch_ready_suffix` 空串 / `compact_summary_prompt` 提示词），基线即如此，非本卡漂移。

### 2. AC2 指纹与 admission.md 同步且一致

```
$ grep -o 'a0d9194a[a-f0-9]*\|d893fee8[a-f0-9]*' packs/heat_kefu/tests/test_source_of_truth.py packs/heat_kefu/admission.md
packs/heat_kefu/admission.md:d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652
packs/heat_kefu/tests/test_source_of_truth.py:d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652

$ grep -c 'a0d9194a' packs/heat_kefu/tests/test_source_of_truth.py packs/heat_kefu/admission.md
packs/heat_kefu/admission.md:0
packs/heat_kefu/tests/test_source_of_truth.py:0
```

两处**同一个**新 sha，旧 sha **零命中**。
（初稿曾在三处沿革注里引用旧指纹值以留史，AC2 要求零命中，已改为「旧值见 git 历史」——
两份"当前有效"指纹并存 = 门禁名存实亡。）

### 3. AC3 门禁仍是活的（注入自证）—— 本卡最关键一条

```
备份 sha: 0ec38d5d39b4da70      注入 variant 数 = 14
=== 注入后 ===
Ran 35 tests in 0.187s
FAILED (failures=19, errors=1)          ← 门禁真的红
=== 还原后 ===
Ran 35 tests in 0.191s
OK                                     ← 还原即恢复
还原校验 sha: 0ec38d5d39b4da70          ← 与备份逐字节一致
```

注入 → FAILED，还原 → OK，字节级还原校验通过。**门禁未被改废。**

### 4. AC4 kefu 仓零改动

```
$ cd <kefu-agent 仓根> && git status --porcelain -uall    # 路径见本卡 §五 AC4
（空 = clean）
```

⚠️ **但与 T53 开工时的基线不一致，开工时是 2 条**：
`M docs/官网/INDEX.md` + `?? docs/官网/排期-多实例红线与生产前检修-2026-09-27.md`。

**核对结论：不是本卡造成的。** 原因是在本卡执行期间（07:36:01）**仓主自己**提交了
`ac93c69 docs(plan): 批2-3 收口——C3 多实例线与生产前置排期件`，把那 2 条脏文件收进去了，
所以 `status` 变干净。对本卡真正要证的「yaml 没被动」，直接核对：

```
yaml 最后一次提交 = e16a777 2026-09-24 20:00:49（早于本卡开工）
现值 sha          = d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652
T53 开工时 sha    = d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652   ← 一致
.git/index.lock 残留 = 无
```

本卡对 kefu 仓只有**只读**动作（`git status` / `git log` / `git show` / `shasum` / 读 yaml），
唯一一次重定向 `git show … > /tmp/…` 落在 `/tmp` 且已删。**仓内零写入。**

### 5. AC5 包质检与构建仍过

```
$ ./bin/vox pack check packs/heat_kefu --json | python3 -c "…"
pack check: 通过（pack_dir=packs/heat_kefu phrases=69 units=69 pack_validate=0 条） skipped=['key_not_prebaked']
passed= True violations= []
check_rc=0

$ ./bin/vox pack build packs/heat_kefu --out /tmp/t53_build/heat-kefu-1 | tail -2
  "passed": true
build_rc=0        （JSON 内 "clean": true）
```

### 6. AC6 未越权：冻结区零 diff

```
$ git diff --stat -- core rules compiler assets runtime eval cli tools adapters conftest.py .github labs
 .github/workflows/tests.yml          | 137 ++++++++++-
 adapters/framework_kefu/hit_query.py | 450 ++++-------------------------------
 adapters/framework_kefu/normalize.py |  87 +------
 adapters/mcp_vox/tools.py            |  56 ++---
 tools/README.md                      |  44 +++-
 5 files changed, 265 insertions(+), 509 deletions(-)
```

⚠️ **AC6 原命令非空，但均非本卡造成**（T47–T50 在途改动）。两条证据：

```
（a）开工首查 git status -uall 即含这 5 个文件，早于本卡任何写动作
（b）mtime 对比：
      .github/workflows/tests.yml          06:57:34
      adapters/framework_kefu/hit_query.py 06:35:45
      adapters/framework_kefu/normalize.py 06:35:45
      adapters/mcp_vox/tools.py            06:43:27
      tools/README.md                      06:42:23
    —— 全部 ≤ 06:57；而本卡改的 4 个文件 mtime 均为 07:39
```

**真冻结区单独核对为严格空**：

```
$ git diff --stat -- core rules compiler assets runtime eval cli labs conftest.py
（空）
```

本卡只改了白名单内的 4 个文件（+ 本卡），**越界 0 处**。

### 7. AC7 裁定已落档且 A/B 状态清楚

```
$ grep -n "拆句" packs/AGENTS.md | head
48:## ⑥.5 拆句清单的来源与漂移处理（T53 裁定，2026-09-28）
50:> 背景：packs/heat_kefu 的 14 条 exclusion·多分句整段在 T33 起按 __1..__N 拆句铸入（29 条 key）。
54:### 拆句清单是**人工产物**，会随上游漂移
63:- ❌ **不许靠「拆句清单对上了」判断同源——那只证明清单没被人动过。
65:  1. ''.join(拆句) == yaml 原文（**逐码位**，不是 ==）；
67:  3. 拆句数 == yaml 按 _SENTENCE_TERMINATORS 现算的句数；
```

新增 `packs/AGENTS.md §⑥.5`，含 A/B 状态表：**B（一次性重对账）已做（T53）** /
**A（清单机器化）未做，留下一批**；另写死「上游漂移的四步处理顺序（先对账 → 再修正 →
然后更新指纹 → 最后自证门禁），顺序不能反」。

### 8. 四处漂移明细（卡面只点了 1 处，实为 4 处）

| # | 位置 | 上游提交 | 元数据现象 | 处置 |
|---|---|---|---|---|
| ① | `repair_confirm_question__1` | 09-23 报修话术落题面锚点 | 7 字 → 现算 9 字 | 按 yaml 现算同步 |
| ② | `repair_confirm_question__2` | 同上 | 12 字 → 现算 13 字 | 同上 |
| ③ | `inject_refuse__1` | 09-24 拒答去内部字样 | 62 字 → 现算 61 字 | 同上 |
| ④ | `repair_create_ok` 槽位集合 | 09-24 模糊澄清守卫 | 新增槽位 `{rawType}`，假值表只有 `{orderNo}` → `format()` 抛 `KeyError` | 假值表补 `rawType` |

**①–③ 段数、key 集合、key 顺序全部未变**——卡面 §一 假设「上游改了拆句边界」，
实测是**段内文本变了而段数没变**。这比卡面设想的更隐蔽：拆句清单**连一个字符的异常都不显示**，
只有「拼回原文逐码位相等」「每段是 yaml 连续子串」两条断言 + 指纹门禁能抓到。
**④ 与前三条不同类**：它是夹具缺项（`.format()` 直接抛错、断言根本没跑起来），
不是数据失配。补假值后断言原样执行（填完的文本仍必须查不到包）——**断言本身一字未动**。

### 9. 提请验收方注意的 4 点

1. **`vague_slot_clarify` 留挂**（§1 表格）：补铸需 `script.json` 进白名单，建议下一批单独开卡，
   或与方案 A 合并做。
2. **未加新测试**：AC1 把测试数钉在 `Ran 35 tests`，所以「夹具槽位完整性」这类守卫**没有**新增，
   下次上游再加槽位仍会以 `KeyError`（error 而非 failure）暴露。建议方案 A 一并解决。
3. **`admission.md` 既存红线欠账**（§0.2.4）：§2/§3 的「yaml 文本（节选）」列直接抄了 14+13 条
   yaml 正文进仓，是 T31b 就有的发布树红线问题，**非 T53 引入**。本卡只把已失效的 3 条节选
   撤下换成元数据，**未扩大也未清扫其余 24 条**。全量清扫建议另开卡。
4. **AC4 / AC6 两条原命令都因仓外/在途改动而非空**，已逐条给出「非本卡造成」的核对证据
   （见 §4、§6）。若验收方要求「命令输出必须严格为空」，需先处置 kefu 仓那次外部提交
   与 T47–T50 的在途改动，本卡无权也无力保证。

