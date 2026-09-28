# T33 · packs + adapters + labs：多分句整段的序列命中与 plan 拼接

## 卡号 / 标题
`T33 · packs(+adapters+labs)：多分句整段拆句铸入 + 查询面序列命中 + 端到端 plan 拼接实测`

## 背景（只写必需）

- **规格依据（先读，不得改）**：`docs/10-接入取景与命中口径.md §10.7`（序列命中口径：逐字覆盖、
  最长优先、任一段不中即整体未命中）与 `§10.2 裁定 4`。**本卡的实现判据全部以 §10.7 为准**。
- **问题**：`packs/heat_kefu/` 里 14 条预设话术是多分句整段（≥2 个句末标点），被
  `compiler/source.py::_validate_variant` 的「一句一 variant」判据（`docs/06 §6.1.4`，字模最小粒度 = 一句）
  拦在包外 → brain 说整段时文本档查不到 → 走原路（秒级 TTS）。这 14 条含 `opening`（每通必说）、
  `fallback_internal`（系统降级）、`repair_confirm_question`（确认）等高频句。
- **依赖的既有产物**（只读、不得修改）：
  - `packs/heat_kefu/`（T31b：40 单句铸入 + admission.md + 22 条测试）；
  - `adapters/framework_kefu/hit_query.py`（T29 公开 API：`find_hit` / `pick_by_text` / `confirm` /
    `build_text_index`，**语义冻结**）；
  - `runtime/executor.py`（T07：`Executor(pack, tts).execute(plan, plan_id=…, turn_id=…, out_path=…)`，
    plan = `[{"key": …, "rate": "normal"}, …]`，多 unit 依次拼接）；
  - `compiler/source.py` 的判据常量（测试引用它，不得复制一份）。
- **本卡不动 kefu 仓**：钩子（T30）当前只接单段档；把序列档接进钩子是**下一张跨仓卡**的事。

## 目标（可验收的产物）

- 产物 1：`packs/heat_kefu/phrases.json`（新增 29 条拆句 key，清单见下「实现规格 A」）
- 产物 2：`packs/heat_kefu/script.json`（新增 29 个 unit，使剧本覆盖全部包内 key）
- 产物 3：`packs/heat_kefu/admission.md`（申报表扩展：拆句申报 + A1/A2 归类 + 对账表刷新）
- 产物 4：`packs/heat_kefu/tests/test_source_of_truth.py`（断言扩展，见验收标准）
- 产物 5：`adapters/framework_kefu/hit_query.py`（新增 `find_hit_sequence` 与 `SequenceHitResult`）
- 产物 6：`adapters/framework_kefu/__init__.py`（导出新 API）
- 产物 7：`adapters/framework_kefu/tests/test_hit_query.py`（新增序列命中测试）
- 产物 8：`labs/heat-kefu-seq/`（新目录：`run_seq_bench.py` + `README.md` + `report.json`）

## 允许修改的文件（白名单）

```
允许修改：packs/heat_kefu/phrases.json
         packs/heat_kefu/script.json
         packs/heat_kefu/admission.md
         packs/heat_kefu/tests/test_source_of_truth.py
         adapters/framework_kefu/hit_query.py
         adapters/framework_kefu/__init__.py
         adapters/framework_kefu/tests/test_hit_query.py
允许新增：labs/heat-kefu-seq/**（新目录，含 run_seq_bench.py / README.md / report.json）
禁止触碰：其他一切文件。特别是：docs/**（规格由策划写）、compiler/**、runtime/**、core/**、
         assets/**、eval/**、cli/**、packs/heat_kefu/pack.json 以外的其他包、
         <kefu 仓根>/**（另一仓，本卡不许动）
```

## 实现规格（照做；判据冲突时以 `docs/10 §10.7` 为准）

### A. 包侧：14 条整段拆句铸入（29 条 key）

**命名规则（冻结）**：拆句 key 名 = `<源 key>__<句序>`，句序从 1 开始（3 句则为 `__1`/`__2`/`__3`）。
例：`opening__1`、`opening__2`、`clarify_repair__3`。
每条：1 个 variant（单句，与 yaml 子串**逐字一致**，含标点与引号）、rates `["normal"]`。

**29 句清单（逐字，照抄；引号原样保留）**：

```
opening__1 = 您好，我是供热智能客服小暖。
opening__2 = 报修、查账单缴费、供暖政策咨询，都可以直接跟我说，比如“我要报修”或“查一下账单”。
chat_smalltalk__1 = 我是供热智能客服小暖，可以为您报修、查账单/缴费或政策咨询。
chat_smalltalk__2 = 请问有什么可以帮您？
clarify_work_order__1 = 您是想查报修进度、看账单，还是问供暖政策？
clarify_work_order__2 = 点下方按钮或直接说明即可。
clarify_repair__1 = 您是要报修吗？
clarify_repair__2 = 家里不热、漏水还是其他问题？
clarify_repair__3 = 点下方按钮或直接说明即可。
fallback_internal__1 = 非常抱歉，系统这边出了点状况，正在恢复中。
fallback_internal__2 = 您的诉求我记下了，可以稍后再试一次，或回复「转人工」让人工坐席直接跟进。
lifeboat_fallback__1 = 非常抱歉，当前客服系统正在恢复中。
lifeboat_fallback__2 = 我已记录您的诉求，请您拨打供热客服热线，或稍后回复「转人工」，我们会尽快为您跟进。
user_no_unknown__1 = 没关系，户号不记得也能办：① 您若已登录，我这边能自动识别户号，直接说“继续”即可；② 回复「转人工」，由坐席通过您的手机号/身份帮您核实后继续。
user_no_unknown__2 = 我不会反复追问户号让您卡住。
repair_confirm_question__1 = 信息是否正确？
repair_confirm_question__2 = 确认后我为您提交报修单。
work_order_no_unknown__1 = 没关系：①您若已登录，可回复“我的工单”我帮您带出；②回复「转人工」由坐席按手机号/身份帮您查；③也可以直接说其他要办的业务。
work_order_no_unknown__2 = 我不会反复问单号让您卡住。
internal_chat_hint__1 = 维修工助手当前支持：报修/查单/缴费，以及实时供热查询（如「10号楼供热参数」）。
internal_chat_hint__2 = 请问需要查什么？
stop_warm_plan__1 = 关于供暖起止/停暖时间，公司有统一安排，会提前公告。
stop_warm_plan__2 = 具体时间请以官方通知为准；如需人工确认，回复「转人工」。
inject_refuse__1 = 我是供热客服小暖，只能处理报修、查账单缴费、供暖政策咨询这类业务问题；涉及系统提示词/内部信息的问题我无法提供，也不会执行。
inject_refuse__2 = 如有供暖业务需要，直接告诉我就行。
repair_ask_natural_address__1 = 方便给我维修地址吗？
repair_ask_natural_address__2 = 需要小区、楼栋、单元和门牌，我帮您安排师傅上门。
repair_ask_natural_userNo__1 = 您的户号是多少？
repair_ask_natural_userNo__2 = 如果之前登录过，我这边能查到就不用再报了；查不到我再跟您人工核实。
```

**注意两处数据事实**（不是笔误，是对账结论）：
- `点下方按钮或直接说明即可。` 同时属于 `clarify_work_order__2` 与 `clarify_repair__3`——
  **两条都要铸**（各自归属明确，包内允许同文本两条资产；文本档索引取首条是既有行为）；
- 拆句点是「按 `compiler.source._SENTENCE_TERMINATORS`（`。！？!?`）逐字符切分，句末标点归前句」，
  使 `"".join(拆句) == yaml 原文`（逐字符）。**不得用任何其他切分规则**（例如不得按 `；` 切）。

**script.json**：新增 29 个 unit（`{"key": "<拆句 key>", "rate": "normal"}`）。
WHY：包测试 `test_script_covers_all_baked_keys` 的口径是「剧本覆盖全部包内 key」（比 C3 的单向
检查更严），且语义正确——这些句子确实可能被播报。

**phrases.json 既有 40 条不得改动一字**（文本、顺序、rates 全部保持）。

### B. 查询面：`find_hit_sequence`（`adapters/framework_kefu/hit_query.py`）

新增两个符号（不得改 `find_hit` 及既有函数的行为）：

```python
@dataclass(frozen=True)
class SequenceHitResult:
    entries: Tuple[AssetEntry, ...]   # 命中序列（按消费顺序）；未命中为空元组
    mode: str                          # 恒为 MODE_TEXT（序列只服务文本档）
    text: str                          # 输入原文（未归一化）
    miss_reason: Optional[str]         # 命中恒 None；未命中为 REASON_TEXT_NOT_PREBAKED
    uncovered: str                     # 未覆盖的剩余（未命中时是归一化空间的剩余文本；命中为空串）

def find_hit_sequence(pack, text: str, rate_key: str = "normal") -> SequenceHitResult:
    ...
```

**算法（照 `docs/10 §10.7`，冻结）**：
1. `norm = normalize_text(text)`；空串 → 未命中（`uncovered=""`，不抛错）；
2. 建**可用候选索引**：包内每条资产（`part_index == 0`、`rate_key` 匹配）经 `confirm(pack, cand)` 复核
   通过的，其归一化文本进索引（复核不过的**不进**——指纹/文件问题在这里就排除）；
3. 从 `pos = 0` 起：在索引中找**能匹配 `norm[pos:]` 的最长**归一化文本（`str.startswith`）；
   同长度多候选取索引内**首条**（顺序稳定，与 `pick_by_text` 同源）；找不到 → 未命中
   （`uncovered = norm[pos:]`）；
4. 消费该段（`pos += len(匹配文本)`），记录其 entry；重复 3 直到 `pos == len(norm)`；
5. 命中：`entries` 按消费顺序、`uncovered=""`、`miss_reason=None`。

**边界**：`text` 非 str → `TypeError`（与 `normalize_text` 同口径）；未命中**不抛错**（与 `find_hit` 同族）。

### C. labs 实测（`labs/heat-kefu-seq/`）

`run_seq_bench.py`（Python 标准库 + 本仓 API；脚本自带 `sys.path.insert(0, REPO_ROOT)` 兜底，
与 `labs/wubench-dialect/` 同款纪律），对 14 条整段逐条测：

1. `find_hit(text=整段)` → 预期 **miss**（现状档，作为对照臂）；
2. `find_hit_sequence(text=整段)` → 预期 **命中**（记录段数、段 key 序列）；
3. 端到端：把命中序列拼成 plan → `runtime.Executor` 播 → 记录 `tts_calls`（预期 0）、
   `first_audio_ms`、`total_duration_ms`（产物写临时目录，不入仓）；
4. 慢路对照：同一整段用 `say`（`adapters/tts_macsay`）合成一遍，记录耗时（这是"走原路"的成本）。

产物：`report.json`（逐条明细 + P50 + 汇总；**零 yaml 原文以外的内容**——本包数据分级公开，可入仓）
与 `README.md`（口径、复现命令、边界）。

## 禁止事项

- 不得改 `docs/**`（规格与验收记录是策划的活）、不得改 `find_hit` 与既有函数行为；
- 不得新增第三方依赖（labs 只用标准库 + 本仓 API）；
- 不得"顺手优化/重构"白名单外的任何文件；不得改 `packs/heat_kefu/pack.json`；
- 不得把 yaml 全文、kefu 内网地址、token 写进任何产物（kefu 仓路径不写入代码，测试沿用
  `KEFU_HEAT_YAML` 环境变量门控的既有写法）；
- 不得自造命中判据：**只有"归一化后逐字"与"逐字覆盖"两种**，禁编辑距离/语义/去标点。

## 验收标准（逐条可判定，验收方会逐条独立复跑）

1. **包内守恒**：`packs/heat_kefu/phrases.json` 的 key 集合 = 40 既有 + 29 拆句 = **69 个，无重复**；
   每条 1 个 variant；既有 40 条文本逐字未变（验收方与 `git show HEAD:` 逐条比对）。
2. **拆句同源（需 yaml）**：设 `KEFU_HEAT_YAML` 后，对 14 条源 key 的每一条：
   「包内 `__1..__N` 的 variant 依次拼接」== yaml 原文（**逐字符**，`[ord(c) for c in …]` 级比对）；
   且 `N` == 该 yaml 原文按 `_SENTENCE_TERMINATORS` 的实际句数。**不设环境变量时诚实 skip**
   （沿用既有 skip 口径），skip 时不得报"通过"。
3. **剧本覆盖**：`script.json` 的 unit key 集合 == 包内 69 key 集合（`used == set(包内 key)`）；
   `bin/vox pack check packs/heat_kefu` **rc 0**、四属性 0 违规；`bin/vox pack build` **rc 0**、
   预铸成功率 100%（69/69）。
4. **序列命中矩阵**（验收方亲跑，用真包 + 真 API）：
   | 输入 | `find_hit`（既有） | `find_hit_sequence`（新） |
   |---|---|---|
   | 40 条单句原文 | 命中（不回归） | 命中，`len(entries)==1` |
   | 14 条整段 yaml 原文 | **miss**（不回归） | **命中**，`len(entries)==句数`，段**文本**顺序 == `__1..__N` 的文本序列 |
   | 13 条带槽（假值填充） | miss | **miss**，`miss_reason==REASON_TEXT_NOT_PREBAKED` |
   | 空串 | miss | miss（`entries==()`，不抛错） |

   > **判据更正（2026-09-21 验收方，策划原稿有误）**：原写「段 key 顺序 == `__1..__N`」。
   > 实测数据下 `clarify_repair` 第 3 段（`点下方按钮或直接说明即可。`）与
   > `clarify_work_order__2` 归一化同文本，按 `docs/10 §10.7`「同长度多候选取索引内首条」
   > 命中后者——**这是先于本卡的冻结规则**，且验收方实测两条资产音频 **sha256 相同、时长相同**
   > （同一文本同一引擎 → 逐字节等价），播放行为无差异。故判据收窄为**段文本**层面严格成立，
   > 段 key 允许命中同名重复文本的别名。见卡尾验收记录。
5. **负例（fail-closed）**：
   - 整段**删一个句末标点** → `find_hit_sequence` miss，`uncovered` 指向断点起；
   - 整段**中间插一句包外文本** / **包外前缀** → miss（不得跳过、不得只播前半）；
   - 混入带槽整段（`.format()` 填充结果）→ miss；
   - 上面各条都要断言"**不得部分命中**"（`entries` 必须为空元组，不是"命中了前一段"）。

   > **判据更正（2026-09-21 验收方，策划原稿有误）**：原写「整段两句颠倒 → miss」。
   > `docs/10 §10.7` 只约束**逐字覆盖**、不约束段序，故重排输入仍可覆盖命中，且
   > **播出顺序 == 输入顺序**（逐字对应，没有播任何输入里没有的话）——这是设计事实而非缺陷；
   > 加段序约束反而会让「确认句 + 提示句」这类真实混搭输出无法命中。判据更正为：
   > 段级重排 → **仍命中**且段序 == 输入顺序（执行方的
   > `test_swap_two_segments_is_not_partial_hit` 已锁定该语义）。
6. **确定性**：同一输入连跑 10 次，`entries` 的 key 序列与长度完全一致（同长度多候选取首条）。
7. **端到端（labs）**：`labs/heat-kefu-seq/run_seq_bench.py` 在**显式给数据源**时裸跑
   （`env -u PYTHONPATH KEFU_HEAT_YAML=<yaml> python3 …`）**rc 0**，14/14 整段序列命中、
   命中臂 `tts_calls == 0`、慢路对照与命中臂的耗时差有实测数字；
   `report.json` 落盘且与 README 引用一致（README 写「以落盘 report 为准」的活引用）。

   > **判据补充（2026-09-21 验收方）**：原写「裸跑（`env -u PYTHONPATH`）rc 0」——省略了数据源。
   > 实测不设 `KEFU_HEAT_YAML` 时脚本 **rc 2** 并打印「yaml 未设置——整段原文只能从 yaml 读，不写死」：
   > 这是 **fail-closed 的正确行为**（kefu 仓路径不得写死进代码），按"显式给数据源"口径收窄验收。
8. **测试全绿**（下列命令的**退出码必须单独确认**，不得接管道读 `$?`）：
   ```
   python3 -m unittest discover -s packs            # 含 heat_kefu 22 条（+新增）
   python3 -m unittest discover -s adapters         # 188 条（+新增）
   python3 -m unittest discover -s trigger          # 159 条（必须原样通过）
   ```
   并在报告里给出三个根的**实际条数**（验收方会对照：`packs` 与 `adapters` 只增不减、
   `trigger` 应与你动工前一致）。
9. **admission.md**：新增「拆句铸入申报」小节——29 条 key 的 A1/A2 归类（见下表，照填）、
   与 yaml 的对应关系、重复句说明；对账表刷新为：必铸单句 40 + 拆句铸入 29 + 不铸带槽 13 +
   非话术 13 = 76（与 yaml top-level key 数守恒）；文首「40 key / 40 条资产」改为
   「69 key / 69 条资产」并注明本卡来源。

**A1/A2 归类表（照填 admission.md；源 key 类别，拆句继承）**：
A1 = `opening` / `chat_smalltalk` / `clarify_work_order` / `clarify_repair` / `repair_confirm_question` /
`inject_refuse` / `repair_ask_natural_address` / `repair_ask_natural_userNo`；
A2 = `fallback_internal` / `lifeboat_fallback` / `user_no_unknown` / `work_order_no_unknown` /
`internal_chat_hint` / `stop_warm_plan`。

## 反空转条款（每张卡必带，T01 教训）

- **测试必须调用产品 API**：包测试走 `assets.load_pack` + `find_hit` / `find_hit_sequence` 真查，
  不得在测试文件内复制切句/匹配逻辑；期望值来自 yaml 运行时读取（`KEFU_HEAT_YAML`），不得写死。
- 本卡的封样测试：`find_hit_sequence` 的测试必须能**失败**——用「改一字」负例证明断言有灵敏度
  （沿用 `test_one_char_tamper_is_detected` 的做法，内存篡改、不落盘）。
- 正例与负例都要有；负例断言**错误/未命中消息包含具体值**（如 `uncovered` 内容、断点位置）。
- 不得为通过测试放宽校验；既有校验强度只增不减。

## 回滚方式

- 包侧四文件：`git checkout -- packs/heat_kefu/{phrases.json,script.json,admission.md,tests/test_source_of_truth.py}`
- 查询面两文件：`git checkout -- adapters/framework_kefu/{hit_query.py,__init__.py}` + 测试文件同法
- labs 新目录：`git clean -fd labs/heat-kefu-seq/`（未跟踪时 `git checkout` 无效）

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`（商汤 provider，`sensenova-6.8-flash-lite`，自带 Write/Edit）。
**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T33-*.md)" --dir （仓库根）`。
本卡数据分级 = **公开**（kefu 预设是自造 demo 文案；卡内无录音/真实会话/内网地址/token），两条路均可派。

**执行方纪律**：非交互环境，直接落地，不要先写计划再等确认；只回事实与自测输出，不做达标判定；
不提交 git；遇到与卡冲突的事实（如某句与 yaml 不符）**停下上报**，不得自行改口径。

## 验收记录（2026-09-21，验收方逐条独立复核）

**结论：验收通过**（两条策划判据经实测收窄，见下）。证据明细在 `00-索引.md` 第十九批节。

- 越界核对：`git status --porcelain -uall` 仅白名单 7 文件 + `labs/heat-kefu-seq/` 3 文件；
  既有 40 条 phrases 零改动（`git show HEAD:` 逐条比对）。
- 包内守恒：69 key / 69 资产 / 剧本 69 unit；`pack check` rc 0（0 违规）、`pack build` rc 0（69/69 synthesized）。
- **拆句同源用验收方自写脚本独立复算**：29 句与 yaml 逐字符一致、`join == 原文`、无越界 key。
- 序列命中矩阵（自写探针）：40 单句 40/40 双档；14 整段 `find_hit` 0/14 + 序列 14/14；带槽 13 全 miss；空串 miss。
- 负例：删标点 / 包外前缀 / 中间插入 → `entries==()` + `uncovered` 精确指向断点。
- 确定性 10 次一致；重复句两条资产音频 sha256 相同（别名播放等价）。
- labs：rc 0；`seq_hit 14/14` / `old_arm_hit 0` / `tts_calls_sum 0`；首音频 P50 0.1585 ms vs 慢路 568.685 ms。
- 三根测试亲跑：packs 35（yaml 真断言模式 0 skip）/ adapters 211 / trigger 159，rc 全 0；
  十一根合计 **1,192** 全绿。
- **两条判据收窄**（策划原稿有误，卡内已更正注明）：① 段 key 顺序 → 段**文本**顺序；
  ② 「重排 → miss」→ **重排仍命中**（段序 == 输入顺序）。两条均由 §10.7 的冻结口径推出，非实现问题。
- 一处产物数字更正：`admission.md §1.5` 的 A1/A2 分项「16/13」→ **17/12**（总数 29 不变），验收方直接在包内更正并注明。
- 执行方 6 条不确定项：4 条接受（具名豁免断言是**收紧**、`farewell` 末位 C1b 约束、`--skip-say` 留痕、
  段级重排测试锁定）；2 条按上条收窄。
- **静默失败审计**（`blackiron-silent-failure-hunter`）回收：**2 P1 + 3 P2 + 3 建议**。其中
  **P1-1「空归一化条目 → 覆盖循环空转（零留痕挂死）」由验收方在本批直接修复**——两道护栏
  （候选过滤 + 消费长度检查）+ 3 条 SIGALRM 兜底回归测试（修复前会挂死 → **能判红**；灵敏度三段
  验证：仅第一道不挂死 / 仅第二道不挂死 / 两道都还原 → HANG）。其余（P1-2 口径不一致 + P2 三条
  + 建议三条）转 **T33b 修订卡**。审计同时独立复核通过的项见 `00-索引` 第十九批节。

## 卡状态
- [x] 已派发 → [x] 已回收 → [x] **验收通过**（证据见上；两条判据收窄已注明）
