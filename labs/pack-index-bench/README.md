# labs/pack-index-bench — T27 查找热路径索引化的对照实测

对应任务卡：`docs/tasks/T27-assets-热路径索引化.md`

**数字以本目录落盘的 `report.json` 为准**（活引用：每次重跑会覆盖它，本 README
只固定口径与结论方向，不复制具体数值）。

## 结论（先说，不粉饰）

在"运行期允许原地改动 `assets`"这个前提下，索引化**并没有让 `lookup` 更快**。

原因可以拆得很清楚（见 `report.json` 每个量级的 `cost_breakdown_no_stat`）：

- 身份索引的**查表本身**比线性遍历便宜一个量级（`index-only` ≈ 0.1 µs）；
- 但失效守卫（`_ensure_lookup_caches` 的内容签名比对）本身是 **O(n)**，
  成本高于「改前」的纯线性遍历；
- 两者相加后，正确实现的总耗时**慢于**「改前」实现，而且随 n 线性增长。

所以 T27 的收益**不是**大包上的 CPU 时间，而是：

1. **语义正确性**：守卫能发现原地改列表（`assets[i] = x` / `append` / `pop`），
   不留下"本该命中却换了路径"的破口。这是本卡的头号红线（fail-closed）。
   若为了性能把守卫换成常数开销的判断，`lookup` 会在原地改动后返回一条已不在
   `assets` 里的条目，`None` 永远不会出现——`--selfcheck` 给出了可复现反例。
2. **stat 次数 O(n) → O(1)**：索引化后每次 `lookup` 最多做一次 `Path.exists()`，
   而「改前」实现每次 `lookup` 也是各做一次（对候选那一条），但候选定位不再
   遍历全表。

已知盲区（诚实记录）：原地改**字段值**（`entry.text = ...`）不改变 `id()`，
守卫检测不到；需要那种粒度时必须显式调 `invalidate_lookup_caches()`。

## 跑法

```sh
# 全部性能测量（默认 n=10000 与 200000 两个量级，各 3 轮）
python3 labs/pack-index-bench/run_bench.py

# 指定量级与轮数
python3 labs/pack-index-bench/run_bench.py --n 10000 --n2 0 --repeat 5

# 反空转自检（守卫必要性）：关掉守卫后原地改 assets → lookup 返回已不存在的条目
python3 labs/pack-index-bench/run_bench.py --selfcheck

# 反空转自检（测量方法灵敏度）：与 T27 实现无关，验证测量函数本身分得开
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
4. **成本拆分**（`cost_breakdown_no_stat`）：用 `--selfcheck` 同款手法临时关掉守卫，
   把「索引查表」和「失效守卫」各自的成本分开记——这是上面结论的关键证据。
5. **冷启动摊销**（`cold_first_lookup_us`）：首次 `lookup` 要付一次 O(n) 建索引 +
   全表预计算指纹，单列记录，不摊进热路径数字。
6. **基准臂是产品之外的一份逐语句副本**：`legacy_lookup` 是 T27 之前
   `AssetPack.lookup` 的原文照抄（对照臂，不是产品重实现）；指纹算法一律调
   `assets.fingerprint.fingerprint`，不复制哈希实现。
7. **所有路径相对仓库根**，report.json 内不写绝对路径。

### `ratio_old_over_new_p50` 怎么读

`ratio = 旧 P50 / 新 P50`。`>1` 表示旧实现更慢（索引化有收益）；`<1` 表示旧实现
反而更快。本次实测所有主对比臂都 `<1`，因此这些臂都进 `problems` 列表——
那里记的是**事实**（"新实现不快于旧实现"），不是失败状态，退出码仍为 0。

## 反空转条款

任务卡第 4 条要求"性能脚本要能在改前代码上跑出明显更差的数字，否则说明测量方法
无灵敏度，必须在报告里点明并换方法"。

本卡的实测结论是"正确实现不快于改前实现"，因此**不能**用"新 vs 旧 P50 比较"
充当灵敏度证据。这里换成两个不依赖该结论的独立自检：

- `--selfcheck`：**守卫必要性**。临时关掉失效守卫、原地改 `assets`，`lookup` 会返回
  一条已不在 `assets` 里的条目；恢复守卫后同一操作返回 `None`。证明索引化后的
  正确性依赖守卫，不是自然成立的。
- `--legacy-selfcheck`：**测量方法灵敏度**。在同一测量函数下，把基准臂换成
  「线性遍历」与「线性遍历 + 每条候选重算 3 次 sha256」两个明显不同的实现，
  后者 P50 必须显著大于前者。这个比较与 T27 的实现完全无关，因此能独立证明
  测量方法本身是分得开的。

两项自检都返回 `0` 才算通过。
