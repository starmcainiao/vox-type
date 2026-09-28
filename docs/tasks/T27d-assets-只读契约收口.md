# T27d · assets：O(1) 索引 + 只读契约收口（三轮试错的最终解）

## 卡号 / 标题
`T27d · assets[冻] + 测试契约内改写：O(1) 索引（签名 + is 校验）；「运行期改 assets」判为契约外，测试/bench 改契约内写法`

## 背景（三轮试错 + 一条决定性事实）

| 轮次 | 形态 | 热路径（n=10k） | 语义 | 结论 |
|---|---|---|---|---|
| T27（`6e33b68`，已入库） | `[id(e) for e in assets]` O(n) 内容签名 | hit 470 µs | 原地改动被抓到 ✓ | **性能净失败**（旧 74.8 µs） |
| T27b（未提交，已回滚） | O(1) 签名、放弃原地检测 | 0.5 µs ✓ | 既有测试红（`test_part_index_nonzero_and_wrong_rate_are_excluded` 0 != 3） | 语义回归 |
| T27c（未提交，已回滚） | O(1) 签名 + 索引带 index + `is` 校验 | 0.5 µs ✓ | **仍红**：既有测试是「三段轮换」——①`part_index` 0→1、②`rate` normal→slow、③恢复；①②把**身份键本身换掉**，旧索引里已无正确键，`is` 校验**无项可校**（`lookup` 拿到 candidate=None 直接返回） | 盲区：身份键迁移 |
| **T27d（本卡）** | 同 T27c 的产品实现 + **把「运行期改 assets」判为契约外**，测试/bench 改契约内写法 | 目标 0.5–0.8 µs | 目标：十一根全绿、既有**断言**零改动 | — |

**决定性事实（验收方全仓 grep，2026-09-21）**：
```
产品代码里运行期改 pack.assets 的地方：0 处
改它的只有：adapters/framework_kefu/tests/test_hit_query.py（T33 写的 setup）
           adapters/framework_kefu/tests/test_hit_query_index.py（T27 新增）
           assets/tests/test_lookup_index.py（T27 新增）
           labs/pack-index-bench/run_bench.py:402-403
```
→ 所以「运行期改 assets」**不是产品能力，是测试写法违反了「只读资产包」契约**（`assets/AGENTS.md` 与
`docs/06` 都把包定义为只读产物）。本卡的裁定：**产品按只读契约实现**（不付 O(n) 去容忍违约），
**测试与 bench 改为契约内写法**（**断言一行不动**）。

## 目标（可验收的产物）

1. `assets/pack.py`：O(1) 签名（`(id(assets), len(assets))`）+ 索引值 `(index, entry)` + 命中时
   `pack.assets[index] is entry` 校验（O(1)）；签名变化或 `is` 失配 → 重建（O(n)，**只在检测到变化时**）；
   **不做** miss 兜底重建
2. `assets/pack.py` / `assets/AGENTS.md`：契约文档写清——包只读；运行期改 `assets` 后**必须**显式调
   `invalidate_lookup_caches()`；不调则缓存可能陈旧（**契约外行为，不保证**）
3. **测试/bench 契约内改写**（清单见规格 §3；**断言零改动**）
4. `labs/pack-index-bench/`：重跑刷新（口径同 T27 第一轮）

## 允许修改的文件（白名单）

```
允许修改：assets/pack.py
         assets/AGENTS.md
         assets/tests/test_lookup_index.py
         adapters/framework_kefu/tests/test_hit_query.py      ← 仅改 setup 构造方式（断言零改动）
         adapters/framework_kefu/tests/test_hit_query_index.py ← 同上
         labs/pack-index-bench/run_bench.py、README.md、report.json
禁止触碰：其他一切（**adapters/framework_kefu/hit_query.py 等产品代码不得动**；docs/**；core/** runtime/**）
```

## 实现规格

### 1. 产品（`assets/pack.py`）

- `_assets_signature` → `(id(assets), len(assets))`（O(1)）。
- 索引值 → `(index, entry)`；构建用 `enumerate` + `setdefault`（首条优先语义不变）。
- `lookup` 命中索引后：`0 <= idx < len(self.assets) and self.assets[idx] is entry`（O(1)）；
  不成立 → `invalidate_lookup_caches()` + 重建 + **重查一次**；仍不成立 → `None`（fail-closed）。
- **不做**"索引 miss 时兜底重建"：那会把 miss 路径变成 O(n)（回到 T27 第一轮），而契约外行为不该
  由产品付代价。
- 指纹缓存：仍只在 `expected_text == entry.text` 时走快路径（语义不变）；缓存键若与条目身份相关，
  重建时一并失效（**不得**让陈旧指纹导致误判）。
- 既有公开契约（字段清单 / 构造签名 / `repr` / `load_pack` 校验强度）一律不动。

### 2. 契约文档（`assets/pack.py` docstring + `assets/AGENTS.md` ⑧ 节）

必须写明（不得留假陈述）：
- 包是**只读产物**；索引与指纹缓存是派生数据；
- **运行期改 `assets` 的正确姿势**：改后调 `invalidate_lookup_caches()`（或新建 `AssetPack` 实例，
  推荐后者——完全不触碰既有包）；
- 不调而直接改的后果：`is` 校验能挡住"同下标换对象"与"换列表/长度变化"，但**挡不住身份键迁移**
  （`assets[i]` 换成不同 key/part/rate 的条目）——属**契约外行为，不保证**；
- 最坏代价：索引重建 O(n) 只在检测到变化时发生。

### 3. 测试/bench 契约内改写（**只改 setup，断言一行不动**）

| 文件 | 现状 | 改成 |
|---|---|---|
| `adapters/.../test_hit_query.py::TestSequenceEdgeCases::test_part_index_nonzero_and_wrong_rate_are_excluded` | 三次原地 `pack.assets[i] = …` | 每次用 `dataclasses.replace(pack, assets=[…])` **新建包实例**（`replace` 会重跑 `__post_init__`，缓存自动重置）；断言（`res.entries == ()` / `len(res3.entries) == 3`）**原样保留** |
| 同文件其他同类写法 | 用 `grep -n '\.assets\[' adapters/framework_kefu/tests/*.py` 全量排查 | 同法改为新建实例 |
| `adapters/.../test_hit_query_index.py`（T27 新增的注入测试） | 若靠原地改 assets 构造 | 同法改；**反空转强度不得降低**（注入分支仍要能判红） |
| `assets/tests/test_lookup_index.py`（T27 新增守卫测试） | 断言"原地改动能被自动抓到" | 改为：① 换列表 / 长度变化 → 自动重建（不调 invalidate）；② 原地改 + **显式 `invalidate_lookup_caches()`** → 正确；③ 把"不调 invalidate 且身份键迁移"如实锁定为契约外行为（断言其陈旧，并注明契约） |
| `labs/pack-index-bench/run_bench.py:402-403` | `pack.assets = list(...)` + `assets[1] = ...` | 改为新建实例（保证 bench 数字在契约内测得） |

## 验收标准（逐条可判定，验收方独立复跑）

1. **既有断言零改动（硬判据）**：`git diff` 中 `test_hit_query.py` / `test_hit_query_index.py` 的改动
   **只允许出现在 setup/构造语句**；所有 `assertEqual/assertTrue/assertIsNone/...` 行**逐字节不变**
   （验收方逐条核对 diff）。
2. **十一根全绿**：`adapters`（含 `test_part_index_nonzero_and_wrong_rate_are_excluded`）全过；
   报每根 `Ran N` + `OK/FAILED` + 退出码（**以行为准**：`discover` 在 1 failure 时退出码仍可能为 0）。
   packs 需先 build + 设 `KEFU_HEAT_YAML`。
3. **性能达标**：bench 重跑（同口径 n=10k/200k、repeat=3），**每个臂 新 P50 ≤ 旧 P50**
   （miss 臂现在也是 O(1)：索引 miss 直接返回，不兜底重建）；`problems` 只装真实回归；
   `fp-mismatch` 臂若出现 ≤0.2 µs 的机械分辨率反向 → 单列（`measurement_floor_reversals` + `repeat5` 证据）。
4. **契约语义（新增测试）**：① 换列表 / 长度变化 → 不调 invalidate 也正确；② 同下标换对象 →
   `is` 校验自愈；③ **身份键迁移**（`assets[i]` 换成不同 key 的条目）→ 不调 invalidate 时**如实陈旧**
   （测试锁定该事实并注明"契约外"）、调 `invalidate_lookup_caches()` 后正确。
5. **注入验证**：去掉 `is` 校验 → 第 4② 必须判红（写进报告）。
6. **契约文档**：`assets/AGENTS.md` ⑧ 节含"只读 + 显式失效入口 + 身份键迁移属契约外"的明确表述；
   grep 核对无"能自动检测原地替换"之类过期承诺。
7. **冷启动单列**：首次 lookup（建索引 + 预计算指纹）成本单列，不混进热路径 P50。

## 反空转条款

- 第 1 条的"断言零改动"由验收方逐条核对，**不采信自述**；
- 第 5 条的注入验证是硬要求；
- 性能目标不得靠减少测量次数 / 换机器口径达成；口径与 T27 第一轮一致。

## 回滚方式

`git checkout -- assets/pack.py assets/AGENTS.md assets/tests/test_lookup_index.py
adapters/framework_kefu/tests/test_hit_query.py adapters/framework_kefu/tests/test_hit_query_index.py
labs/pack-index-bench/`

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = 公开。非交互直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突停下上报。

## 卡状态
- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
