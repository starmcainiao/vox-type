# T27c · assets：索引自校验——O(1) 热路径 + 原地改动的自愈（T27/T27b 的正确解）

## 卡号 / 标题
`T27c · assets[冻]：索引条目带 index + is 校验（热路径 O(1)，assets 被原地改动时自愈重建）`

## 背景（两轮试错的结论，验收方已实测）

| 轮次 | 守卫形态 | 热路径（n=10k, hit-last） | 语义 | 结论 |
|---|---|---|---|---|
| T27 第一轮（`6e33b68`） | `[id(e) for e in assets]`（**O(n) 内容签名**） | 470 µs | 原地改动能被抓到 ✓ | **性能净失败**（旧 74.8 µs） |
| T27b（未提交，已回滚） | `(id(assets), len(assets))`（**O(1)，但放弃原地检测**） | 0.5 µs ✓ | **破坏既有行为**：`adapters/.../test_hit_query.py::TestSequenceEdgeCases::test_part_index_nonzero_and_wrong_rate_are_excluded` 变红（0 != 3）✗ | 语义回归 |
| **T27c（本卡）** | `(id(assets), len(assets))` **+ 索引条目自带 index、命中时 `is` 校验** | 目标 0.5–0.8 µs | 三类改动**全部自愈**（行为与 O(n) 守卫一致） | **目标：两全** |

- 失败根因（T27b 实测隔离证据）：既有测试在同一列表上原地 `assets[i] = X` 三次再恢复；
  O(1) 签名不重建索引 → `lookup` 用索引里的旧 `entry` 返回 → `find_hit_sequence` 的
  `confirm(pack, stale)` 拿不到条目 → 期望 3 段得 0。
- 验收方对照实验（T27 第一轮产物）：**O(n) 守卫 477.52 µs vs O(1) 守卫 0.50 µs（955×）**——
  守卫是唯一瓶颈，所以正确解不是"回到 O(n)"，而是**让 O(1) 守卫也能容忍原地改动**。

## 目标（可验收的产物）

1. `assets/pack.py`：索引值由 `entry` 改为 **`(index, entry)`**；`lookup` 命中后 O(1) 校验
   `pack.assets[index] is entry`；失配 → **重建索引 + 重试一次**（仍失配 → 返回 `None`，fail-closed）
2. `assets/pack.py` / `assets/AGENTS.md`：文档说明"自愈"机制与最坏代价（列表被频繁改动时退化为
   每次重建 = O(n)，**不会比 T27 第一轮更差**）
3. `assets/tests/test_lookup_index.py`：新增自愈测试（三类改动）+ 重试上限测试
4. `labs/pack-index-bench/`：重跑（口径与 T27 第一轮一致），`report.json` + README 刷新

## 允许修改的文件（白名单）

```
允许修改：assets/pack.py
         assets/AGENTS.md
         assets/tests/test_lookup_index.py
         labs/pack-index-bench/run_bench.py、README.md、report.json
禁止触碰：其他一切（**尤其 adapters/**——既有测试一行不得改；本卡的正解是让产品代码适配既有行为，
         不是改测试；docs/**；core/** runtime/** …）
```

## 实现规格

### 1. 索引自校验（`assets/pack.py`）

```python
# 索引值：(index, entry)
self._identity_index: Dict[tuple, Tuple[int, AssetEntry]] = {}

# 构建时带 index（首条优先语义不变）
for idx, entry in enumerate(self.assets):
    identity = (entry.key, entry.part_index, entry.rate_key, entry.variant)
    self._identity_index.setdefault(identity, (idx, entry))
```

`lookup` 命中索引后：

```python
idx, entry = index[identity]
if not self._entry_still_at(idx, entry):     # 即：self.assets[idx] is entry 且 idx < len
    self.invalidate_lookup_caches()
    self._ensure_lookup_caches()             # 重建
    idx, entry = self._identity_index.get(identity, (None, None))
    if entry is None or not self._entry_still_at(idx, entry):
        return None                          # 重建后仍对不上 → fail-closed（不猜）
```

- **只重试一次**（防病态循环）；`_entry_still_at(idx, entry)` 必须同时判 `0 <= idx < len(self.assets)`
  与 `self.assets[idx] is entry`（O(1)）。
- 指纹预计算缓存 `_text_fingerprints` 同样带身份校验：其键若是 `id(entry)` 或 `(idx, entry)`，
  失配时一并重建（**保持"expected_text == entry.text 才走快路径"的语义不变**）。
- `_ensure_lookup_caches` 的第一道判据（`(id(assets), len(assets))` 签名）**保留**——它拦住
  "换列表 / 长度变化"两类，不需要每次 O(n)。
- `invalidate_lookup_caches()` 保持公开（显式失效入口），语义不变。

### 2. 文档（`assets/pack.py` docstring + `assets/AGENTS.md` ⑧ 节）

必须写清（**不得留假陈述**）：
- 三道防线各拦什么：① O(1) 签名（换列表/长度变化）→ 立即重建；② 索引 `is` 校验（原地替换元素/
  重排）→ 命中时发现并重建；③ 显式 `invalidate_lookup_caches()`（字段级原地修改等极端场景）。
- 最坏代价：列表被持续原地改动时每次 lookup 都重建（= T27 第一轮的成本），**是上界不是常态**。
- 仍是**只读契约**：自愈是容忍度，不是鼓励运行期改动；生产路径不该改 `pack.assets`。

## 验收标准（逐条可判定，验收方独立复跑）

1. **既有行为零回归（本卡头号判据）**：`adapters` 全绿——特别是
   `adapters.framework_kefu.tests.test_hit_query.TestSequenceEdgeCases.test_part_index_nonzero_and_wrong_rate_are_excluded`
   必须通过（T27b 下红：0 != 3）；`adapters/**` **一行不得改**（验收方用 `git diff --name-only` 核对）。
2. **自愈三类（新增测试，都要能判红）**：
   ① 整体换列表 → 不调 invalidate，下一个 lookup 正确；
   ② 增删条目（长度变化）→ 同上；
   ③ **原地替换元素**（`assets[i] = 同身份的新条目`）→ 不调 invalidate，下一个 lookup **返回新条目**
   （与 O(n) 守卫行为一致）；把 `is` 校验去掉后 ③ 必须失败（注入验证写进报告）。
3. **重试上限**：构造"重建后仍对不上"的病态场景（如把 `_entry_still_at` 打进不去死的状态、
   或用测试替身在重建后立刻再改列表）→ 断言 `lookup` 返回 `None` 而**不是**无限重试（可加计数断言）。
4. **语义不变**：T27 第一轮新增的全部测试（等价性 / 指纹快路径不弱化 / stat 不缓存 / 公开契约 /
   索引确定性）保持通过；`assertEqual` 级断言不得为通过而放宽。
5. **性能达标**：`labs/pack-index-bench` 重跑（同口径：n=10k / 200k、repeat=3），**除 `fp-mismatch`
   臂外**每个臂 新 P50 ≤ 旧 P50（预期 hit/miss 臂 10²–10³× 加速）。
   - `fp-mismatch` 臂允许 ≤ 0.2 µs 的反向：它是"旧实现命中包首条即返回、本就不付 O(n)"的机械分辨率
     效应（T27b 实测 0.792 vs 0.625 µs，≈4 个 41.67 ns 时钟步长，两量级逐字相同）——
     **允许但必须单列**（如 `measurement_floor_reversals` 字段 + `repeat5` 证据），不得混进 `problems`；
     `problems` 只能装真实回归，`problems == []` ⇔ 热路径无回归。
   - 若 `fp-mismatch` 反向 > 0.2 µs → 停下上报（说明索引自校验引入了非机械开销）。
6. **冷启动单列**：首次 lookup（建索引 + 预计算指纹）成本仍单列记录，不得混进热路径 P50。
7. **十一根全绿**：报每根条数与合计；packs 需先 build + 设 `KEFU_HEAT_YAML`；
   **退出码单独确认**（`cmd >out 2>&1; echo $?`），并**以 `Ran N` + `OK/FAILED` 行为准**
   （`unittest discover` 在 1 failure 时退出码仍可能为 0——上一轮已踩到）。
8. **无假陈述**：全文（docstring / AGENTS.md / README / report / 卡外文档）grep 核对：
   不得残留"O(n) 守卫""放弃原地检测"之类的过期说法；历史引用必须显式标注为历史。

## 反空转条款

- 第 2 ③ 的注入验证是硬要求（去掉 `is` 校验 → 该测试必须红）；
- 第 5 条的"每臂 新 ≤ 旧"必须有同口径对照（`git stash` 或 `/tmp` 旧实现副本；
  T27 第一轮的 report.json 已在库中，可作旧实现数字来源，但**口径必须一致**）；
- 不得靠减少测量次数 / 换机器口径达成性能目标。

## 回滚方式

`git checkout -- assets/pack.py assets/AGENTS.md assets/tests/test_lookup_index.py
labs/pack-index-bench/run_bench.py labs/pack-index-bench/README.md labs/pack-index-bench/report.json`

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = 公开。非交互直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突停下上报
（**尤其：若发现又有既有测试因本卡改动变红，立刻停下贴证据**）。

## 卡状态
- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
