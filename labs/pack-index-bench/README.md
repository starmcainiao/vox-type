# labs/pack-index-bench — T27d 查找热路径索引化的对照实测

对应任务卡：`docs/tasks/T27d-assets-只读契约收口.md`
（历史卡：`docs/tasks/T27-assets-热路径索引化.md` —— 三轮试错的起点）

**数字以本目录落盘的 `report.json` 为准**（活引用：每次重跑会覆盖它，本 README
只固定口径与结论方向，不复制具体数值）。

## 结论（先说，不粉饰）

热路径是 **O(1)**，`problems == []`。

守卫是 `(id(assets), len(assets))` 的 **O(1) 签名**（不是 T27 第一轮的 O(n) 内容签名），
查表 O(1)，命中时再加一次 O(1) 的 `is` 校验（`_entry_still_at`）。成本拆分见
`report.json` 每个量级的 `cost_breakdown_no_stat`：`guard_alone` 与 `index_only_no_guard`
都是 0.125 µs 量级，两者相加仍比线性遍历低 2–4 个数量级。

**代价必须一起看，两处都不藏**：

1. **冷启动**（`cold_first_lookup_us`，单列不摊进热路径 P50）：首次 `lookup` 要付
   一次 O(n) 的建索引 + 全表预计算指纹；旧实现在此几乎为零。这是**摊销**口径。
2. **只读契约**：包是只读产物，全仓产品代码里运行期改 `pack.assets` 的地方是 0 处。
   两道防线是**容忍度**，不是"运行期可改 assets"的授权——**身份键迁移**
   （`assets[i]` 换成不同 `key`/`part_index`/`rate_key` 的条目）与**字段级原地修改**
   （`entry.text = ...`）两道防线都看不见；那种情形下必须显式调
   `invalidate_lookup_caches()`，不调则缓存可能陈旧（契约外行为，不保证）。
   详见 `assets/AGENTS.md` ⑧。

## 跑法

```sh
# 全部性能测量（默认 n=10000 与 200000 两个量级，各 3 轮）
python3 labs/pack-index-bench/run_bench.py

# 指定量级与轮数
python3 labs/pack-index-bench/run_bench.py --n 10000 --n2 0 --repeat 5

# 反空转自检（第二道防线必要性）：摘掉 is 校验 → lookup 返回不在 assets 里的旧条目
python3 labs/pack-index-bench/run_bench.py --selfcheck

# 反空转自检（测量方法灵敏度）：与实现无关，验证测量函数本身分得开
python3 labs/pack-index-bench/run_bench.py --legacy-selfcheck
```

退出码：`0` 成功（`report.json` 已落盘）/ `3` 运行期失败或自检反例不成立。

## 口径

| 项 | 值 |
|---|---|
| 机器 | `report.json` → `env.machine`（本机 `platform.platform()`，未硬编码） |
| CPU 架构 | `env.machine_model` |
| Python | `env.python` / `env.py_impl` |
| 资产条数 | `--n`（默认 10000，卡要求的 10k 量级）、`--n2`（默认 200000） |
| 每档重复轮数 | `--repeat`（默认 3）；每轮 `min(n, 1000)` 次采样 |
| 统计量 | 每组采样的 P50 / P99 / min / max / mean（微秒，线性插值百分位） |

### 为什么这么测

1. **stat 成本单列，不混进主对比。** `Path.exists()` 在本机磁盘单次约数百微秒，
   会淹没定位成本。主对比把 `root` 换成内存对象（两条实现拿到**同一个** stub），
   只测「定位 + 指纹」；末尾单列 `hit_arm_with_real_stat` 记真实 stat 的实测数字。
   注意 stat 不缓存是本卡的硬约束（保险丝），它留在实现里，只是不该混进对比。
2. **主对比测两个不做 stat 的臂**：`miss_key_arm_no_stat`（key 不存在）与
   `fingerprint_mismatch_arm_no_stat`（身份命中但文本不符）。
3. **命中臂按位置分档**（`hit_first/mid/last_no_stat`）：线性遍历的成本强依赖命中
   位置，只测首条会把旧实现"看起来很快"，是误导性测量。
4. **指纹不匹配臂的探针取包中段**（`report.json` → `measurement_caveats.probe_position`）：
   旧实现在**命中首条**时成本最低（本就不付 O(n)），拿首条当基准会制造一个
   ≈0.21 µs 的**伪回归**——那差值是 1–2 个时钟步长级的位置偏差，不是新实现的开销。
   实测证据：同一探针换成 `mid` / `last` 后，旧实现 P50 从 0.583 µs 涨到 32–2557 µs，
   而新实现恒为 0.75–0.79 µs。命中臂保留了"旧实现随位置变慢"的完整对照。
5. **成本拆分**（`cost_breakdown_no_stat`）：用 `--selfcheck` 同款手法临时关掉守卫，
   把「索引查表」和「失效守卫」各自的成本分开记。T27d 下两者都是 O(1)，因此该拆分
   的作用是证明"守卫不再是瓶颈"（T27 第一轮时守卫单独 233.8 µs > 线性遍历 60.7 µs）。
6. **冷启动摊销**（`cold_first_lookup_us`）：首次 `lookup` 要付一次 O(n) 建索引 +
   全表预计算指纹，单列记录，不摊进热路径数字。
7. **基准臂是产品之外的一份逐语句副本**：`legacy_lookup` 是 T27 之前
   `AssetPack.lookup` 的原文照抄（对照臂，不是产品重实现）；指纹算法一律调
   `assets.fingerprint.fingerprint`，不复制哈希实现。
8. **所有路径相对仓库根**，report.json 内不写绝对路径。

### `ratio_old_over_new_p50` 怎么读

`ratio = 旧 P50 / 新 P50`。`>1` 表示旧实现更慢（索引化有收益）；`<1` 表示旧实现
反而更快。判定线见 `measurement_caveats.floor_line_us`（0.2 µs ≈ 5 × 41.67 ns 时钟步长）：

- 反向幅度 **≤ 0.2 µs** → 记进 `measurement_floor_reversals`（**机械分辨率**，单列，
  不算回归）；
- 反向幅度 **> 0.2 µs** → 记进 `problems`（真实回归）。

`problems == []` ⇔ 热路径无真实回归。本次实测 `problems == []`、
`measurement_floor_reversals == []`。

## 反空转条款

- `--selfcheck`：**第二道防线必要性**。摘掉 `_entry_still_at` 的 `is` 校验（恒真）
  后同下标换对象，`lookup` 会返回一条**不在 `assets` 里**的旧条目（静默降级）；
  防线在位时同一操作返回**新**条目。证明正确性依赖这道防线，不是自然成立的。
  注：该自检用的是**契约外**的原地改动，只用于证明防线有效——产品代码不改 `assets`。
- `--legacy-selfcheck`：**测量方法灵敏度**。在同一测量函数下，把基准臂换成
  「线性遍历」与「线性遍历 + 每条候选重算 3 次 sha256」两个明显不同的实现，
  后者 P50 必须显著大于前者。这个比较与实现完全无关，因此能独立证明
  测量方法本身是分得开的。

两项自检都返回 `0` 才算通过。

## 历史留证（不是当前实现）

`report.json` 的 `history_first_round` 字段与 T27 第一轮的报告数字属**历史留证**：
T27 第一轮用逐条目 `id()` 的**O(n) 内容签名**守卫，那一步把索引的节省抵消掉了
（n=10k 时守卫单独 233.791 µs vs 线性遍历 60.708 µs）。T27d 换成 O(1) 签名后
守卫不再是瓶颈。那些数字**不是** T27d 的实测值，不要混用。

三轮试错的时间线见 `assets/AGENTS.md` ⑧「三轮试错的历史」。
