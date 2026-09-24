# T27 · assets(+adapters)：查找热路径索引化（**冻结区开批第一张**，语义不变）

## 卡号 / 标题
`T27 · assets[冻] + adapters[扩]：pack.lookup 热路径索引化（线性遍历 → 索引；指纹重算 → 预计算缓存）`

## 背景（只写必需）

- `docs/13 §三` 冻结区批次第一张（建议顺序 **T27 → T19 → T18 → T20**）；用户 **2026-09-21 拍板开批**
  （「可以执行 / 逐步开发一步步验证」）。这是四张里**风险最低**的一张：纯实现优化、**语义不变**。
- 现状（两处叠加的成本）：
  - `assets/pack.py::AssetPack.lookup`（约 `:110-143`）每次调用三步：**线性遍历** O(n) 找候选 →
    **重算 sha256 指纹** → **stat 文件存在性**；
  - `adapters/framework_kefu/hit_query.py::lookup_key`（约 `:88-97`）在调 `lookup` 之前**又**遍历一次
    O(n) 找候选 → 消费侧最坏 O(n²)。
- **D9 冻结区纪律**：本次变更**语义不变**（包格式、API 契约、失败条件、返回值一律不动），
  须在 `assets/AGENTS.md` 记「实现变更记录 + 迁移说明（无产物迁移）」。
- 依赖：`assets/pack.py`、`adapters/framework_kefu/hit_query.py`（T29/T33 已交付的公开面，**不得改其语义**）。

## 目标（可验收的产物）

- 产物 1：`assets/pack.py`——AssetPack 增**惰性索引**与**指纹预计算缓存**（见规格）
- 产物 2：`adapters/framework_kefu/hit_query.py`——消费侧消掉线性遍历（接线用产物 1 的索引）
- 产物 3：`assets/AGENTS.md`——「实现变更记录（T27）」+ 迁移说明
- 产物 4：`assets/tests/`、`adapters/framework_kefu/tests/` 的测试（新增；既有断言一律不改）
- 产物 5：`labs/pack-index-bench/`（性能实测脚本 + README + report.json）

## 允许修改的文件（白名单）

```
允许修改：assets/pack.py
         assets/AGENTS.md
         adapters/framework_kefu/hit_query.py
         assets/tests/**、adapters/framework_kefu/tests/**（新增测试；既有断言不得改）
允许新增：labs/pack-index-bench/**（脚本 + README.md + report.json）
禁止触碰：其他一切。特别是 core/** rules/** compiler/** runtime/** eval/** cli/** packs/**
         docs/**（评审/验收记录是策划的活）、<kefu 仓根>/**
```

## 实现规格（语义零变化是硬判据）

### 1. 索引（`assets/pack.py`）

- AssetPack 增一个**惰性构建**的索引：`(key, part_index, rate_key, variant) → AssetEntry`；
  首次需要时构建一次（`O(n)` 一次），之后 `lookup` 用 O(1) 定位候选。
- 保持**确定性**：同一身份多条条目（理论上不该有；`assets` 已有重复身份校验）时，索引构建要
  与"线性遍历取首条"行为一致（先出现的优先）。
- 索引是**不可变派生数据**（包本身只读）：公开属性或方法自定，但**不得**改变 `AssetPack` 的
  既有公开字段与构造签名（`load_pack` 与既有测试依赖它们）。

### 2. 指纹预计算缓存（`assets/pack.py`）

- 现状：每次 lookup 都用 `expected_text` 重算 sha256 再与 `candidate.fingerprint` 比对。
- 允许的优化（语义等价）：**预计算每条的 `fingerprint(entry.text)`**（惰性、一次 O(n)），
  lookup 时若 `expected_text == entry.text` 则直接与该预计算值比对（省掉每次 sha256）；
  `expected_text != entry.text` 时**仍走原路径重算**（语义最严格，不做任何推测）。
- **不得**因为"load_pack 已经校验过指纹"就直接返回候选——那会把校验变成对装载期的隐式依赖，
  改变 lookup 的失败条件。

### 3. 文件存在性校验（**保持原样，不缓存**）

- `stat` **不缓存**：文件存在性是 assets 层的关键保险丝（T04 的语义），缓存会让"运行期文件被删"
  从"未命中"变成"命中"——**语义就变了**，本卡不许可。
- 索引化后每次 lookup 的 stat 次数从 O(n) 降到 O(1)，收益已足够；实测数字里要单列 stat 成本。

### 4. 消费侧接线（`adapters/framework_kefu/hit_query.py`）

- `lookup_key`：用索引按 `key` 取候选（`part_index == 0`、`rate_key` 匹配者，按包内顺序），
  再逐候选 `confirm`/`lookup`——**结果与顺序必须与原线性遍历完全一致**。
- `build_text_index`、`_sequence_candidates` 保持既有行为（结果集与顺序不变；可用索引加速，
  但若改动风险大于收益，可只做 `lookup_key` 一处）。
- `find_hit` / `find_hit_sequence` 的**对外行为一字不变**（含 `miss_reason`、`uncovered`、`blocked_by`）。

### 5. `assets/AGENTS.md` 实现变更记录

新增一节，内容至少含：变更内容（索引 + 指纹预计算）、**语义不变声明**（包格式 / API 契约 /
失败条件 / 返回值）、迁移说明（**既有产物无需迁移**——manifest 与 audio 格式未动）、
性能实测数字（指向 `labs/pack-index-bench/report.json`）。

## 禁止事项

- 不得改 `assets/pack.py` 的公开字段/方法签名与 `load_pack` 的校验强度（只增不减）；
- 不得改 `docs/**`、不得改 `core/**`（协议定义在 core，本卡不碰）；
- 不得把 stat 结果、音频内容缓存进产物；不得引入第三方依赖；
- 不得"顺手优化"白名单外的文件；不得为通过性能目标而弱化任何校验。

## 验收标准（逐条可判定，验收方独立复跑）

1. **语义零变化（机器证明）**：
   - `assets`（59 条）、`adapters`（226 条）、`runtime`（118 条）三根 rc 0，**既有断言一行未改**
     （验收方用 `git diff` 核对测试文件：只允许"新增"不允许"修改既有断言"）；
   - 新增"索引 vs 线性遍历等价"测试：对真包（`packs/heat_kefu` 构建产物，69 条）与随机组合
     （含不存在/part≠0/rate 不匹配/文本不符的负例）逐一比对旧实现与新实现的返回值（同一条目或同为 None）。
2. **指纹快路径不弱化**：`expected_text != entry.text` → `lookup` 必须返回 None（显式负例，
   消息含 key 与两个文本的首段）；`expected_text == entry.text` → 返回条目。
3. **stat 不缓存**：构造一个包 → 删掉某条的音频文件 → 同进程再 `lookup` 该条 → **必须 None**
   （证明运行期文件删除仍被检出）。
4. **性能实测（产物 5）**：10k 条资产量级下给出对照数字——`lookup` 单次调用与
   `find_hit(text=…)`（文本档全链路）的 P50/P99：**改前 vs 改后**（改前用 `git stash` 或
   `/tmp` 里的旧实现副本实测，口径写进 README：机器、Python 版本、n、重复次数）；
   `report.json` 落盘且 README 用「以落盘 report 为准」的活引用。**不得**用绝对路径（相对仓库根）。
5. **消费侧不回归**：`find_hit`（key 档 / 文本档）、`find_hit_sequence`（14 条真包整段、含
   `blocked_by`）行为与 T33b 基线逐项一致（验收方亲跑对照）。
6. **冻结区记录**：`assets/AGENTS.md` 有实现变更记录节（含"无产物迁移"的明确表述）。
7. 十一根全量：跑 `core rules assets adapters compiler runtime eval cli tools trigger packs`
   （packs 需先 build + 设 `KEFU_HEAT_YAML`，见 T33b 的 CI 步），报每根条数与合计；
   **退出码单独确认**（`cmd >out 2>&1; echo $?`，不得接管道读 `$?`）。

## 反空转条款

- 第 1 条的"等价性测试"必须能判红：把索引实现改成"随机取一条"或在其中注入一个错误映射，
  对应用例必须失败（把注入验证写进报告）；
- 第 4 条的性能脚本要能在"改前"代码上跑出**明显更差**的数字（否则说明测量方法无灵敏度，
  必须在报告里点明并换方法）；
- 测试不得复制产品实现（索引构建/指纹计算一律走产品 API）。

## 回滚方式

`git checkout -- assets/pack.py assets/AGENTS.md adapters/framework_kefu/hit_query.py
assets/tests adapters/framework_kefu/tests` + `git clean -fd labs/pack-index-bench/`。

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`；**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite`。
数据分级 = **公开**，两条路均可派。
非交互环境直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突的事实停下上报。

## 卡状态
- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
