# T27b · assets：T27 退回修订——守卫降 O(1)，让索引收益落地

## 卡号 / 标题
`T27b · assets[冻]：lookup 缓存守卫从 O(n) 内容签名降为 O(1) 身份签名`

## 背景（退回原因，验收方实测）

T27 第一轮（提交 `6e33b68`）**语义正确**（十一根 1,261 全绿、既有断言零改动、等价性/指纹快路径/
stat 不缓存测试齐备），但**性能目标净失败**：

| 臂（n=10,000，无 stat） | 旧实现 | T27 第一轮 | 结论 |
|---|---|---|---|
| miss(key) | 60.7 µs | **238.5 µs** | 慢 3.9× |
| hit(first) | 0.67 µs | **470.1 µs** | 慢 700× |
| hit(mid/last) | 32.6 / 74.8 µs | **472 µs** | 慢 6–14× |

**根因（验收方对照实验，非推测）**：`_assets_signature` 返回 `[id(entry) for entry in assets]`——
**每次 lookup 都 O(n) 扫一遍**。把该实现换成 O(1) 签名后实测：

```
当前实现（[id(e) for e in assets] 守卫）  P50 = 477.52 µs
O(1) 守卫（id(list)+len）              P50 =    0.50 µs
→ 加速 955×
```

即：**索引本身是 0.125 µs 级的，唯一瓶颈是"确认索引没过期"这一步**。执行方在 `problems`
里如实记录了该结论（诚实，值得保留）；本卡修掉它。

## 目标（可验收的产物）

1. `assets/pack.py`：`_assets_signature` 降为 **O(1)**（`(id(assets), len(assets))`）
2. `assets/pack.py` / `assets/AGENTS.md`：把"原地替换元素（`assets[i] = x`）也能被守卫自动抓到"
   的承诺**删掉**，改为明确契约：**运行期原地修改 `pack.assets` 的元素属契约外行为**；
   需要该粒度时显式调 `invalidate_lookup_caches()`
3. `assets/tests/test_lookup_index.py`：更新/新增守卫语义测试（见验收标准 2）
4. `labs/pack-index-bench/`：重跑，`report.json` 刷新；`problems` 中**不得再有**"旧不比新差"类条目
5. （若 README 里有对旧结论的引用）同步刷新

## 允许修改的文件（白名单）

```
允许修改：assets/pack.py
         assets/AGENTS.md
         assets/tests/test_lookup_index.py
         labs/pack-index-bench/run_bench.py、README.md、report.json
禁止触碰：其他一切（尤其 adapters/**——消费侧接线已定型且正确；docs/**；core/** runtime/** …）
```

## 实现规格

### 1. 守卫（`assets/pack.py`）

```python
def _assets_signature(self, assets):
    """O(1) 失效判据：列表**对象身份** + 长度。

    WHY 不比逐条 id()：那需要 O(n) 扫描，把索引收益全部抵消（T27 实测：477µs vs 0.5µs，955×）。
    `assets/AGENTS.md` 把包定义为**只读产物**——运行期原地替换元素（assets[i] = x）属契约外
    行为；需要那种粒度时由调用方显式调 invalidate_lookup_caches()（本类已提供）。
    本签名仍能抓住两类常见违约：整个列表被换成新对象、长度变化（增删条目）。
    """
    return (id(assets), len(assets))
```

- `_ensure_lookup_caches` 的其余逻辑（令牌比对、清空重建、`_assets_token is None` 必重建）**保持不变**。
- `invalidate_lookup_caches()` 保持现状（显式失效入口）。

### 2. 文档与注释同步

- `assets/pack.py` 内与该守卫相关的 docstring（现写有"逐个条目取 id() 才能发现它"那套论证）
  要**整体改写**为新口径：说明选择 O(1) 的理由、契约外行为的边界、逃生口。
- `assets/AGENTS.md` 的 ⑧ 实现变更记录：把"守卫粒度"一节改为上述口径，并补一句"原地改元素需
  `invalidate_lookup_caches()`"。
- **不得**在文档里留下"能检测原地替换元素"的旧承诺（那会变成假陈述）。

## 验收标准（逐条可判定，验收方独立复跑）

1. **性能达标（本卡成败判据）**：`labs/pack-index-bench` 重跑后，n=10,000 与 n=200,000 的
   **每一个臂**都满足「新 P50 ≤ 旧 P50」（预期 miss/hit 臂为亚微秒级，即 ~10²–10³× 加速）；
   `report.json.problems` 中**不得再有**"旧 P50 不比新 P50 更差"类条目；机械噪声导致的个别
   反向（若出现）必须在报告里单独说明并给出重复测量证据，**不得**把它塞进 problems 就当过。
2. **守卫语义（两条测试，方向要写对）**：
   - **自动检测**：整体换列表 / 增删条目（长度变化）→ 不调 invalidate，下一个 `lookup` 结果正确；
   - **契约边界（诚实锁定）**：原地替换元素（`assets[i] = 另一条同身份的条目`）→ **不调 invalidate 时
     会返回旧条目**（测试要断言这个"失配"事实本身，写明"这是契约外行为"）；调
     `invalidate_lookup_caches()` 后恢复正确。
3. **语义零回归**：T27 第一轮新增的全部测试（等价性 / 指纹快路径 / stat 不缓存 / 公开契约 /
   注入验证）保持通过；既有断言一行不改。
4. **十一根全绿**：报每根条数与合计（packs 需先 build + 设 `KEFU_HEAT_YAML`）；
   **退出码单独确认**（`cmd >out 2>&1; echo $?`）。
5. **冷启动成本记录**：首次 lookup（建索引 + 预计算指纹）的耗时仍如实记录在 report 里
   （n=10k / 200k 各一条）；不得把它混进热路径 P50。
6. **无假陈述**：全文（代码注释 / AGENTS.md / README / report）不得留下"O(n) 守卫""能抓原地替换"
   之类的过期说法（验收方会 grep 核对）。

## 反空转条款

- 第 1 条的达标必须有"改前代码上跑出更差数字"的**同口径对照**（`git stash` 或 `/tmp` 旧实现副本），
  否则测量无灵敏度；
- 第 2 条的"契约边界"测试要能判红（把守卫改回 O(n) 后该测试应失败或行为不符——写进报告）；
- 性能目标不得靠"减少测量次数/换更快的机器口径"达成；口径（n、repeat、机器）与 T27 第一轮一致。

## 回滚方式

`git checkout -- assets/pack.py assets/AGENTS.md assets/tests/test_lookup_index.py
labs/pack-index-bench/run_bench.py labs/pack-index-bench/README.md labs/pack-index-bench/report.json`

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = 公开。非交互直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突停下上报。

## 卡状态
- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
