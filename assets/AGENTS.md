# assets/ · 资产包与索引（冻结区·格式冻结）

## ① 职责 / 不负责什么

**职责**：定义并维护**资产包的格式与索引**（数据层）。
- 包结构：`manifest`（协议版本/规则集版本/音色/模型版本/包版本）+ `index`（key → 资产引用）+ `audio/`（音频文件）
- **内容寻址**：每个资产的稳定标识 = 文本+音色+语速+模型版本的哈希（指纹）
- 分片与增量：包可分片（按业务/场景），支持增量同步（只传变化分片）
- 版本与回滚：包版本号可回滚（旧包保留，切换瞬时）

**不负责**：不合成音频（`compiler/`）、不做播放（`runtime/`）、不做命中判定。

## ② 输入 / 输出契约

- 输入：`compiler/` 产出的资产与报告。
- 输出：**只读**的资产包（`runtime/` 只读它，永不写它）。
- 包的消费者只有两个：`runtime/`（运行期读）与 `eval/`（评测读）。

## ③ 验收条件

1. **格式校验器**：缺字段/哈希不符/索引指向不存在文件 → 必拒；
2. **哈希可验证**：任取一条资产，能用其文本+音色+语速+模型版本**重算指纹**并与包内一致；
3. **分片独立**：任一分片可单独校验与装载（不依赖其他分片元数据）；
4. **版本可回滚**：装载旧版本包不报错（向后兼容声明）；
5. 包内**不得含用户数据**（只有话术文本与音频；槽值永不入包）——检查项必须自动化。

## ④ 本层数据收集

包级统计：资产条数 / 总时长 / 总字节 / 分片数 / 版本号 / 生成时间。

## ⑤ 依赖边界

- 允许依赖：`core/`（元 schema 与指纹算法定义）。
- 禁止：不得依赖 `compiler/` 的实现细节（只认它产出的格式）；不得被任何层写入（只读契约）。

## ⑥ 变更纪律

**格式变更 = 破坏性** → 大版本 + 迁移脚本；旧版本包必须继续可读（声明支持窗口）。

## ⑦ 冻结状态

**[冻] 冻结区（格式冻结，内容随业务）**。业务新增话术只改内容（`compiler/` 重新出包），不触格式。

---

## ⑧ 实现变更记录（T27 · T27d · 2026-09-21）：查找热路径索引化 + 只读契约收口

> 开批第一张卡（`docs/tasks/T27-assets-热路径索引化.md`），三轮试错后由
> `docs/tasks/T27d-assets-只读契约收口.md` 收口。
> **语义零变化**是硬判据：包格式、API 契约、失败条件、返回值一律不动。

### 只读契约（本节的总前提）

**本包是只读产物。** `pack.assets` 只在装载期（`load_pack`）被赋值一次，之后**只读**。
全仓产品代码里运行期改 `pack.assets` 的地方是 **0 处**；历史上只有测试与 bench 脚本
改过它，那属于**契约外行为**。

因此下面两道防线是**容忍度**（让测试/工具的意外改动不会立刻变成静默错误），
**不是"运行期可改 assets"的授权**：

| 运行期改了 `assets` | 两道防线能发现吗 | 正确姿势 |
|---|---|---|
| 整体换列表（`pack.assets = [...]`） | 能（自动重建） | 推荐改为**新建 `AssetPack` 实例** |
| 增删条目（`append` / `pop` / `del`） | 能（自动重建） | 同上 |
| 同下标换对象（`assets[i] = x`，身份键不变） | 能（自动重建） | 同上 |
| **身份键迁移**（`assets[i] = x`，`key`/`part_index`/`rate_key` 变了） | **不能** | 改后调 `invalidate_lookup_caches()` |
| **原地改字段值**（`entry.text = ...`） | **不能** | 改后调 `invalidate_lookup_caches()` |

- **`invalidate_lookup_caches()` 是公开的显式失效入口**，语义未变：把两个派生缓存清空、
  令牌归零，下一次 `lookup` 按**当前** `assets` 重建。
- **契约外不保证**：不调它就直接改 `assets`（且改动落在上表最后两类），缓存**可能陈旧**
  ——调用方会看到"未命中"而不是正确结果。这是**如实的**后果，不粉饰也不掩盖。
- 生产路径不该改 `pack.assets`。要"换一个包"，新建实例（`dataclasses.replace(pack, assets=…)`
  或重新 `load_pack`）；`replace` 会重跑 `__post_init__`，缓存自然重置。

### 变更内容

`assets/pack.py::AssetPack.lookup` 的两处成本被消除：

1. **候选定位：线性遍历 → 惰性身份索引**
   - 新增私有派生属性 `_identity_index`：`(key, part_index, rate_key, variant) → (index, AssetEntry)`。
     值带**构建时的下标**——这是第二道防线（见下）的依据。
   - 首次 lookup 时按包内顺序构建一次（O(n)），之后是 **O(1)** 查表 + **O(1)** 守卫。
   - 确定性：**首条优先**（`setdefault`），与"线性遍历取首条"完全一致；
     索引**不过滤**任何条目（指纹不符 / 文件已删的条目照样在索引里，
     由后续步骤按原判据返回 None）——索引只做寻址，不做判定。
   - **两道防线（热路径都是 O(1)）**：
     1. 一致性守卫（`_ensure_lookup_caches` + `_assets_signature`）：
        签名是 `(id(assets), len(assets))`——**O(1)**，拦"整体换列表 / 增删条目"。
     2. 索引身份校验（`_entry_still_at`）：命中后校验
        `0 <= index < len(assets) and assets[index] is entry`——**O(1)**，
        拦"同下标换对象"（第 1 道签名看不见的类）。
        失配 → `invalidate_lookup_caches()` + 重建 + **只重试一次**；
        仍失配 → 返回 `None`（fail-closed，不猜、不无限重试）。
   - **明确不做**"索引 miss 时兜底重建"：那会把未命中路径变成 O(n)——正是 T27 第一
     轮的教训（见下）。契约外行为不该由产品付这个代价。
   - 已知盲区（诚实记录）：**身份键迁移**与**字段级原地修改**两道防线都看不见。
     前者会让 `lookup` 拿到 `candidate is None` 直接返回（校验无项可校）；后者
     不改变 `id()`、`len()`、对象同一性。都需要显式调 `invalidate_lookup_caches()`。
2. **指纹重算 → 预计算缓存**
   - 新增私有派生属性 `_text_fingerprints`：按身份键预计算 `fingerprint(entry.text, …)`，
     一次 O(n)。
   - **快路径只在 `expected_text == entry.text` 时启用**；`expected_text != entry.text`
     时**仍走原路径重算**（语义最严格，不做任何推测）。
   - 快路径只省去这次 sha256：`fingerprint` 是纯函数，预计算值与重算值逐字同参，
     比对对象与判据都没变。
   - 该缓存与身份索引**同批**重建，因此不会因陈旧指纹导致误判。

### 语义不变声明

| 项 | 状态 |
|---|---|
| 包格式（manifest / audio / 指纹算法） | **未动**——一个字没改 |
| API 契约（`load_pack` / `lookup` / `validate_pack` / `AssetPack` 构造签名与字段清单 / `AssetEntry`） | **未动**——公开字段仍是 `pack_id … root` 九个；私有派生属性用 `InitVar` 接入 `__post_init__`，**不进** dataclass 字段清单、不进 `__init__` 参数、不进 `repr` 与比较 |
| `load_pack` 的校验强度 | **只增不减**——所有既有校验原样保留 |
| lookup 的失败条件 | **未动**——身份不匹配 / 指纹不匹配 / 文件不存在三种情形仍各自返回 None；**没有**因为"load_pack 已校验过指纹"就直接返回候选（那会把校验变成对装载期的隐式依赖） |
| 返回值 | **未动**——仍返回包内那条 `AssetEntry` 对象本身（不是副本、不是新造对象） |

### 明确不做的优化（保险丝）

- **`stat` 不缓存**：文件存在性是 assets 层的关键保险丝，缓存会让"运行期文件被删"
  从"未命中"变成"命中"——语义就变了，本卡不许可。索引化后每次 lookup 的 stat 次数
  已从 O(n) 降到 O(1)，收益已足够。
- **不把音频内容 / stat 结果缓存进产物**。
- **不做"索引 miss 兜底重建"**：见上「两道防线」。

### 迁移说明

**既有产物无需迁移。** manifest 与 audio 的格式一个字都没改，`load_pack` 的校验
强度只增不减；旧包、新包对同一份代码表现完全一致。没有版本闸门、没有双读期、
没有弃用窗口——这是一次纯实现优化。

### 消费侧（`adapters/framework_kefu/hit_query.py`）

`lookup_key` 原先「线性遍历找候选 → 逐候选 `pack.lookup`」，而 `pack.lookup` 内部
又是一次 O(n) 线性遍历 → 消费侧最坏 **O(n²)**。T27 保留候选筛选的线性遍历（顺序
语义必须跟着包内顺序走），把 `pack.lookup` 的内部定位换成索引后，消费侧最坏情形降到
**O(n·k)**（k = 候选数；每次 `pack.lookup` 内部是 1 次 O(1) 守卫 + 1 次 O(1) 查表
+ 1 次 O(1) 身份校验，**稳态全部 O(1)**）。
**候选的筛选条件与顺序一律不动**（仍按 `pack.assets` 包内顺序逐条判定
`key` / `part_index == 0` / `rate_key`），`find_hit` / `find_hit_sequence`
（含 `miss_reason` / `uncovered` / `blocked_by`）对外行为一字未变。

### 性能实测

数字一律以落盘报告为准：`labs/pack-index-bench/report.json`（口径与复现步骤见
`labs/pack-index-bench/README.md`）。

**结论（T27d，不粉饰）**：热路径 **O(1)**——守卫是 `(id, len)` 签名、查表 O(1)、
命中时再加一次 O(1) 的 `is` 校验。`problems == []`，逐量级倍数见 report.json
（n=10k 时 1.14×–294×，n=200k 时 1.14×–10¹⁴×）。

**代价（必须一起看）**：
1. **冷启动**：首次 `lookup` 付一次 O(n) 的建索引 + 全表预计算指纹（n=10k ≈ 7ms、
   n=200k ≈ 187ms）。旧实现在此几乎为零。这是**摊销**口径，不是热路径口径。
2. **契约**：只读产物。运行期改 `assets` 落在「身份键迁移 / 字段级原地修改」这两类时，
   必须显式调 `invalidate_lookup_caches()`；不调则缓存可能陈旧。

### 三轮试错的历史（留证，不是当前实现）

| 轮次 | 守卫形态 | 结论 |
|---|---|---|
| T27 第一轮 | 逐条目 `id()` 的**内容签名**（O(n)） | 语义正确但**性能净失败**：n=10k 时守卫单独 233.8µs > 线性遍历 60.7µs，把索引的节省抵消掉了 |
| T27b | O(1) 签名、**放弃原地检测** | 既有测试红（`test_part_index_nonzero_and_wrong_rate_are_excluded` 0 != 3）——已有实现被回滚 |
| T27c | O(1) 签名 + 索引带 index + `is` 校验，但把"运行期改 assets"当**能力** | 仍然红：该既有测试是"三段轮换"，①`part_index` 0→1、②`rate` normal→slow、③恢复——①②把**身份键本身换掉**，旧索引里已无正确键，`is` 校验**无项可校** |
| **T27d（当前）** | 同 T27c 的实现 + **把"运行期改 assets"判为契约外**，测试/bench 改契约内写法 | 十一根全绿，`problems == []` |

关键认识：T27c 的失败**不是实现 bug**——它证明了"O(1) 且完全覆盖三类改动"做不到
（身份键迁移一旦让 `lookup` 拿不到候选，任何判断都无从触发）。所以正确的收口不是
"再找一种守卫"，而是**承认包是只读的**：产品不为契约外行为付 O(n)。

### 判定与验收

- 语义等价性、指纹快路径不弱化、stat 不缓存、索引确定性、公开契约未动、
  「等价性断言不是空转」的注入验证、以及**只读契约的三类语义**（换列表/长度变化
  自动重建、同下标换对象自愈、身份键迁移如实陈旧）：全部在
  `assets/tests/test_lookup_index.py`。
- 消费侧等价性与顺序灵敏度：`adapters/framework_kefu/tests/test_hit_query_index.py`。
- 反空转（摘掉 `is` 校验 → 必须判红）：`python3 labs/pack-index-bench/run_bench.py --selfcheck`。

**历史引用标注**：本节及 report.json 中所有"T27 第一轮 / T27b / T27c"的数字与
结论均为**历史留证**，不代表当前实现。当前实现的判据以「语义不变声明」+「只读契约」
+ report.json 的 `problems` 字段为准。

---

## ⑨ 实现变更记录（T18 · 2026-09-22）：TTL / invalid_at 失效判定

**核心红线**：过期资产不得被播出。播报过期事实比不播更糟。

### 字段只增，旧包行为完全不变

| 改动 | 形态 | 旧包（无这两个字段） |
|---|---|---|
| `AssetEntry.invalid_at` | 新增，`Optional[datetime]`，默认 `None` | `None` = **永不过期**，`is_expired` 恒 `False` |
| `AssetEntry.ttl` | 新增，`Optional[int]`，默认 `None` | `None`；**仅审计留痕**，判定不看它 |
| manifest 资产条目 | 新增可选键 `invalid_at` / `ttl` | 不出现这两个键 → manifest 与既有产物**逐字节一致** |

`load_pack` 对这两个字段做类型校验（非法 → `AssetPackError`，消息含实际值）：
`invalid_at` 必须是可解析的 ISO 8601 字符串；`ttl` 必须是正整数（`0` / 负数 /
字符串 `"3600"` / 布尔 `true` 全部拒绝——`bool` 是 `int` 子类，必须显式排除）。

### 保险丝在资产层（本卡的关键决定）

`AssetPack.lookup(..., now=None)` 对过期条目返回 `None`，与既有未命中**同形**
（指纹不匹配 / 文件不存在）。**任何**消费方——`runtime` 的 `Executor` 与
`adapters/framework_kefu` 的钩子都直接调 `pack.lookup`，不经 runtime——因此
过期资产对**所有**调用方都不可能再被取出，调用方零改动即受保护。

### 判据只有一份

公开纯函数 `is_expired(entry, now)` 是**唯一**判据，`lookup` 与 `runtime` 共用，
不得两处各写一份：

- 语义（冻结）：`now >= invalid_at` → 过期（**恰好等于失效时刻也算过期**）；
- 只看 `invalid_at`，不看 `ttl`（折算已在 compiler 预铸时刻完成）；
- 无 `invalid_at` 恒 `False`（旧包唯一锚点）。

### `lookup(now=)` 的默认语义与评测重放要求

`now` 接受 `datetime` 或 ISO 8601 字符串（`Z` 结尾归一成 `+00:00`；naive 值按
UTC 解释，与 manifest 的 `created_at` 同口径）。**`now=None` 才取当前 UTC 时间**。

> **评测与测试必须显式注入 `now`**（`lookup(..., now=...)` /
> `is_expired(entry, now=...)`），不得依赖 `sleep` 等真实时间流逝——否则评测不可
> 重放。本层的失效相关测试（`assets/tests/test_ttl.py`）全部注入固定时刻，零 sleep。

### 迁移说明

- **旧包 / 旧源：零改动即可继续用**。无 `ttl` / `invalid_at` 的包永不过期，
  `lookup()` 默认参数与显式 `now=` 都照常命中（见
  `test_lookup_legacy_pack_lookup_unchanged_with_and_without_now`）。
- **新包要加失效声明**：改 `phrases.json` 的单条话术，给 `ttl`（秒，正整数，相对
  预铸时刻）或 `invalid_at`（ISO 8601 绝对时刻），两者**互斥**——同时给由 compiler
  报 `SourceError`，不猜哪个优先。重新 `vox pack build` 即可，无需迁移脚本。
- **消费方**：无需改动。过期条目返回 `None` 与未命中同形；需要区分原因时调
  `is_expired(entry, now)`。

### 判定与验收

- `assets/tests/test_ttl.py`（24 条）：`is_expired` 纯函数（含边界 `==`）、
  `load_pack` / `validate_pack` 的坏值拒绝（消息含实际值）、`lookup(now=)` 的
  正反翻转、模块级与实例方法同形、**直接调 `pack.lookup` 不经 runtime 的保险丝**。
- 反空转（把 `is_expired` 改成恒 `False` → 负例必须全红）：实测 assets 9 条判红、
  runtime 13 条判红（共 22 条），见 `runtime/tests/test_executor_expiry.py`。
