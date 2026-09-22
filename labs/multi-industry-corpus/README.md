# T22 · 多行业公开语料收集与「预铸适用边界」跨行业验证

> 任务卡：`docs/tasks/T22-多行业语料收集与适用性验证.md`
> 本目录是 **labs**（一次性实验），产物不进任何层的契约。
>
> **数值一律活引用 `report.json`，本 README 不复述具体数字。**
> 要数字就查 §七 的路由表；本文件只给口径、命令、结论方向与边界。

## 一句话结论

预铸的可行空间（「系统侧话轮里由流程决定说什么话的比例」）**跨语料成立，但跨行业不可平移**。
在**公开数据能触达**的四个对话域里，唯一一对口径真正可比的对照显示：换成另一个中文任务对话域，
预铸可行空间**只缩不涨**——这是本批最保守也最硬的结论。
而导航播报 / 机场车站广播 / 工业设备 / 政务热线 / 快递通知这五类播报场景**没有公开语料可验证**，
不能用近似场景冒充。

---

## 一、口径

### 1.1 主指标 —— 系统/助手侧「流程决定话轮」占比

一份语料里，**系统（助手）说话**的话轮中，有多少是「由流程而非当场内容决定说什么」的。
回答：**预铸能覆盖多少系统话术**。数据源必须是带系统/助手侧话轮的对话集。

### 1.2 辅指标 —— 「指令模板化率」（句式可复用率）

去实体值后的**句式骨架**在同语料内出现 ≥ 5 次的话轮占比。判据与既有脚本**逐字同源**：
`labs/corpus-harvest/cross_check_template_rate.py:31-39` → 移植为 `common.py:25-50`
（`REPEAT_MIN = 5` 两处一致，body 已逐字比对）。回答：**句式能不能被预铸成有限模板**。

### 1.3 「用户侧」vs「系统侧」——两者测的不是同一件事（混用会让整卡退回）

| | 系统侧（主指标） | 用户侧（辅指标） |
|---|---|---|
| 读的是谁的话 | 助手 / agent / SYSTEM / ASSISTANT | 用户的指令 |
| 回答的问题 | **系统说什么由流程决定** → 预铸能覆盖多少 | **用户怎么说** → 指令能不能被模板化 |
| 对预铸的意义 | 直接：预铸的是系统话术 | 间接：只说明用户输入端的离散度 |
| 本批数据集 | CrossWOZ / ABCD / MultiWOZ 2.1 / MultiWOZ 2.2 / Taskmaster TM-1 | MASSIVE zh-CN（utt 与 annot_utt 两列）/ HWU64 |

**不得用用户侧指令集冒充主指标。** 落地约束（可审）：

1. `analyze_flow_occupancy.py:499-515` 把用户侧集放进独立顶层键
   `report["user_side_instruction_sets"]`，**不在** `report["corpora"]` 内，并自带警示 note。
2. 门禁 `common.verify_occupancy`（`common.py:121-153`）**只遍历** `report.get("corpora", {})`
   → 用户侧集在**结构上**不可能进入系统侧占比判定。
3. `report.json.rows` 共 14 行，其中 `side='user'` 的 **3 行**（MASSIVE utt / HWU64 answer /
   MASSIVE annot_utt）metric **全部**是 `instruction_template_rate`，**没有一行** `flow_decided_rate`；
   5 行 `flow_decided_rate` 的 side 均为 `system` / `system(agent) + action` / `system(assistant)`。

> ⚠ 一个反直觉的机制事实：脚本 `analyze_flow_occupancy.py` **只**从 `corpora` 算主指标，
> 用户侧只放 HWU64 一集；`report.json` 里那 3 条 user 侧条目是**报告撰写阶段**
> 另用 `common.template_stats` 补算写进的（MASSIVE 两列 + HWU64）。
> 所以「用户侧不进主指标」是双保险：结构上隔离 + 报告里补的也只是辅指标。

### 1.4 口径强弱是第一排序字段——先读它，再读数值

同样一份数据、两种口径，占比可以差出近 50 个百分点（见 §三 第 6 条；数值以 `report.json` 为准）。
分档活引用 `report.json.provenance.method_strength` 与 `report.json.rows[].method_strength`：

| 档 | 数据集 | 判据来源 |
|---|---|---|
| 强 | CrossWOZ | 系统侧对话行为标注（intent）驱动 |
| 中 | ABCD | `action` 业务动作埋点是强证据；agent 部分仍是文本启发式 |
| 中 | MultiWOZ 2.1 | `SYSTEM-*` act 是行为标签，语义粒度粗于 CrossWOZ intent |
| 弱 | MultiWOZ 2.2 / Taskmaster TM-1 | 无对话行为标注，纯文本形态启发式 → **只能读作下界** |

英文文本启发式用的 `EN_FLOW_PATTERNS`（`common.py:66-107`）是中文客服词的**英译近似**，
不是同源标注——这一档最弱，如实标注，不得当作「该域占比低」的证据。

---

## 二、复现命令

### 2.1 前置：语料（落仓外，不入库）

```bash
# 收集（许可审计 fail-closed；管道台账写仓外）
python3 tools/corpus_fetch/fetch.py \
  --sources tools/corpus_fetch/sources.json \
  --lock ~/corpus/corpus.lock.json

# 巡检：sha256 回校
python3 tools/corpus_fetch/fetch.py --recheck --lock ~/corpus/corpus.lock.json
```

语料根由 `CORPUS_ROOT` 决定，默认 `~/corpus`（`analyze_flow_occupancy.py:37`、`mine_recompute.py:74`）：
`CORPUS_ROOT=/path/to/corpus python3 ...`。
语料本体**一律不入库**；仓内只有台账 `corpus_ledger.json`，路径一律相对 `~/corpus/`，无绝对路径。

### 2.2 分析（三脚本，固定种子 / 无随机）

```bash
cd labs/multi-industry-corpus

# 主指标 + 辅指标
python3 analyze_flow_occupancy.py --out out/occ_a.json
python3 analyze_flow_occupancy.py --out out/occ_b.json

# 确定性核对：剔除 run_at 后应逐字一致
python3 -c "import json;print(json.dumps(json.load(open('out/occ_a.json')),ensure_ascii=False,indent=2,sort_keys=True,default=str))" | grep -v '"run_at"' > /tmp/a.txt
python3 -c "import json;print(json.dumps(json.load(open('out/occ_b.json')),ensure_ascii=False,indent=2,sort_keys=True,default=str))" | grep -v '"run_at"' > /tmp/b.txt
diff /tmp/a.txt /tmp/b.txt                      # 无输出 = 一致

# 回流实验（修订轮已修好 mine_candidates.py，两个脚本同判据、产物一致，任一可跑）
python3 mine_candidates.py  --out out/mine_candidates.json   # 已修复版，n_candidates=737
python3 mine_recompute.py   --out out/mine_a.json
python3 mine_recompute.py   --out out/mine_b.json
diff -q out/mine_a.json out/mine_b.json         # 无输出 = 逐字节一致
```

> 修订轮说明（见 `revision.json` items[4]）：`mine_candidates.py` 此前是一次
> **静默零结果失败**（`n_candidates=0`、`determinism_check=True`、exit 0），
> 根因是 `state_signatures()` 里名为 `intent` 的变量实际拿的是 `dialog_act` 的字段 1（域），
> 于是「餐馆/酒店/景点」全被拒收 → 每个话轮产出空签名。已修好并复跑，产出 737 条候选，
> 与 `mine_recompute.py` 的全部共享数值字段逐字一致。原先「不要用 mine_candidates.py」的
> 禁令已失效，予以更正。

已核对（`report.json.provenance.commands_run`）：`occ_a`/`occ_b` 剔 `run_at` 后逐字一致；
`mine_a`/`mine_b` 与仓内 `out/mine_recomputed.json` 逐字节一致；
且 `out/report_occ.json` 与复跑产物逐字一致——即本次复跑重现了仓内已验收产物
（`report.json.provenance.determinism = true`）。

### 2.3 反空转：注入验证（判据必须能判红）

```bash
python3 analyze_flow_occupancy.py --out out/report_inject.json --inject-all-flow
```

期望：所有可判集占比全为 100%，用例必须失败。
判红在 `common.verify_occupancy`（`common.py:139-153`）：`expect_not_all=True` 时任何集占比 ≥ 0.999 直接判失败。

**注意注入产物的 gate 是 `pass`**——因为注入跑法把 `expect_not_all` 置为 False
（`analyze_flow_occupancy.py:570` 写 `"expect_not_all": not args.inject_all_flow`）。
所以 `out/report_inject.json` 是**留存证据**，不是通过的产物；
它本身记 `expect_not_all: false, status: pass`（已核对落盘文件）。
正常产物 `out/occ_a.json` 记 `expect_not_all: true, status: pass, problems: []`（已核对落盘文件）。

### 2.4 独立性验证（可选，验「占比不是抄来的」）

`verify.json.checked[2]` 记录了验证员写的独立脚本 `/tmp/verify_abc.py`：
不 import 本目录任何模块，判据按 `report.json.provenance.criteria_source` 指向的原始出处手抄，
直读仓外数据。结果是 ABCD 的**辅指标两行完全独立复现**，但**主指标一行对不上**——
见 §七 第 1 条。

### 2.5 零回归门禁（修订轮已亲跑）

任务卡验收标准第 7 条要求零回归全绿。**修订轮（2026-09-22）已亲跑两项**
（处置记录见 `revision.json` items[5]）：

```bash
python3 -m unittest discover -s tools        # → exit 0；Ran 119 tests in 0.282s，OK
python3 -m tools.structure_budget.check      # → exit 0；合计 50 个 .py 文件 / 5070 可执行行，结构预算：全部合规
```

实测输出：`unittest` 119 tests / OK（与上轮验收记录 `docs/tasks/T22-*.md:110` 的 119 passed 一致）；
`structure_budget` 逐层 core 阈值≤150 超限 0、adapters ≤150 超限 2（历史豁免）、
eval ≤150 超限 4（历史豁免）、cli ≤150 超限 0，compiler/assets/runtime/trigger 阈值未登记不参与判定。
`report.json.provenance.zero_regression` 已同步为实测记录。

---

## 三、跨行业对照结论

对照表、样本量、每集口径与 pp 差值，全部活引用 `report.json`
（`rows` + `comparison_table`（在 `out/occ_a.json` 内，含 `kefu_delta_pp` / `method_strength`）
+ `comparison_note` + `provenance.method_strength`）。本 README 只给方向，不复述数字。

1. **唯一一对口径真正可比的对照**：客服基线（中文、标注驱动）× CrossWOZ（中文、标注驱动）。
   CrossWOZ **低于**客服基线。原因在 `report.json.comparison_note` 第（1）点：
   CrossWOZ 系统侧只有 5 类仪式 intent，而客服基线的七类词表还含合规提示 / 转人工等客服专属流程轮。
   → **换到另一个中文任务对话域，预铸可行空间只缩不涨。** 这是本批最保守也最硬的结论。
2. **偏高不能反推客服偏低。** ABCD 与 MultiWOZ 2.1 高于客服基线，但这两族把
   「业务动作埋点」（ABCD 的 `action` 角色，直接计为流程决定）与「系统反问/提议确认」
   （MultiWOZ 2.1 的 `*-request`/`-offer`/`-book`）也算作流程决定——这两族在客服基线七类词表里
   **没有对应项**，属于**口径更宽**。只能说它们那部分里有一部分不是客服意义上的流程轮，
   不能据此说客服偏低。
3. **无标注集只能读作下界。** MultiWOZ 2.2 与 Taskmaster TM-1 无对话行为标注，
   纯 `EN_FLOW_PATTERNS` 启发式 → 数值是**下界**，不能当「该域占比低」的证据。
4. **辅指标侧唯一有效的同档对照**：MASSIVE zh-CN（原始转写 utt 列）× 客服基线，
   同量级、**没有翻转** → 支持 `labs/corpus-harvest/README.md:144` 的结论
   「句式可复用率低是跨语料成立的」：换成 52 语种语料里的中文，结论仍然成立。
5. **预归一化会伪造高值。** HWU64 的 `answer` 列是数据集**自带的已归一化规范写法**，
   其辅指标值远高于 MASSIVE / 客服基线是**列被归一化**造成的，不是「用户话术更模板」。
   同一份 MASSIVE zh-CN 从 `utt` 列换到 `annot_utt` 列，模板化率会被放大约一个数量级
   （`report.json.comparison_note` 第（6）点）。跨集比辅指标**必须先确认读的是原始转写还是归一化文本**，
   否则数字可以差一个数量级。
6. **占比数字完全由口径决定——本表自己就是证据。** MultiWOZ 2.2 与 MultiWOZ 2.1
   是**同一批话轮**的两种口径，差近 49 个百分点（`report.json.comparison_note` 第（3）点，
   精确 pp 差以 `report.json` 为准）。

---

## 四、回流实验（M3「日志挖掘 → 话术建议」在公开数据上能走多远）

用 CrossWOZ 的**系统侧对话行为标注**做状态分组，组内按骨架取高频模板。
候选规则、显著性下限、种子全部活引用 `report.json.provenance.mining_method`
（`SEED=20260922`；「同一状态签名下同一骨架出现 ≥ 阈值」；「状态签名自身出现 ≥ 下限」；
dialog_act 实测为 **4 元组** `[类别, 域, 槽名, 值]`）。

- 挖出的候选条数 → `report.json.replay_candidates`；
  覆盖到的状态、话轮占比、top 状态 → `report.json.provenance.mining_method.outputs`
  与原始产物 `out/mine_recomputed.json`。
- **能走多远的答案**：能挖出候选，但产出面很窄——集中在
  **「系统确认/回显某个槽的值」这一类**（数据库查询回显），**不是通用话术生成**。
  即：M3 的最保守路径在公开数据上走得通，但产出面就是这个窄场景。

---

## 五、诚实边界：公开数据真空

以下五类场景**没有公开语料可验证**（T22 侦察已确认），**不得用近似场景冒充**：

| 场景 | 状态 |
|---|---|
| 导航播报 | 公开数据真空（`Angeriod/in_car_commands` 为空集，已入 `rejected_sources`） |
| 机场车站广播 | 公开数据真空（侦察未见任何可取的广播语音/转写数据集） |
| 工业设备 | 公开数据真空（无机器人/PLC/设备语音播报的公开标注语料） |
| 政务热线 | 公开数据真空（政务对话非公开，或无许可干净来源） |
| 快递通知 | 公开数据真空（无快递/物流语音通知的公开语料） |

本批入选集覆盖的是**「任务对话」「电商客服」「用户侧指令」**三个对话域，
**不是**上面这五类播报场景。
逐条陈述见 `report.json.boundaries`，跨行业结论的适用范围只有「公开数据能触达的四个对话域」，
那五类**没有任何一行数字**能支撑预铸适用性结论。

---

## 六、拒收与许可纪律

拒收集**必须入台账并留理由 + 证据位置**：见 `corpus_ledger.json.rejected_sources`（14 条，
含卡面侦察清单 9 条 + 本次联网取证暴露的 5 条）与 `sources.json.rejected`（10 条）。

许可纪律（复用 `labs/corpus-harvest/README.md §三`，**front-matter 会撒谎，必须读正文**）：

- 仓库有 `LICENSE` 文件即视为正文证据，但台账必须记**证据位置**（文件 + 行号）；
- README 正文与 LICENSE 冲突 → **拒收**（RiSAWOZ / yangzailu 全系即此形态）；
- 禁商用 / 禁衍生 → **硬拒收**（SLURP、FSC、MagicData SLR68）；
- 许可不明 / 证据不足 → 拒收（market.aliyun `license=other`、Primock57 第三方镜像）；
- 商业数据 → 拒收（数据堂全系）；
- 规模不足以构成统计估计 → 拒收（`tor24/smart-home-voice-commands-v1` 仅 10 行）；
- 空集 → 拒收（`Angeriod/in_car_commands`）。
- **MASSIVE 只收源仓库的许可证据与元数据**（README.md 34420 字节）：52 语种本体
  实测可下载（HTTP 200，40251390 bytes），但本管道三个平台适配器不覆盖 S3 直链 → **未取本体入库**。
  辅指标里那两条 user 侧条目是分析层走 S3 直链补跑算的（落仓外 `/tmp`，未写入 `~/corpus`）。
  zh-CN 实测 16521 条 = train 11514 + dev 2033 + test 2974。
- 医疗语料 **Primock57 只收源仓库本体**：音频波形是 Git LFS 指针，不可得，
  只留文本转录与临床记录 → 医疗行业**只有文本口径，无音频口径**。

---

## 七、数字出处路由（一律活引用，本 README 不复述数值）

| 要什么 | 去哪 |
|---|---|
| 全部占比 / 样本量 / 每行口径说明 | `report.json` → `rows` |
| 对照与口径差异论证（六条） | `report.json` → `comparison_note` |
| 逐集口径强弱分档 | `report.json` → `provenance.method_strength` |
| 客服基线 | `report.json` → `provenance.kefu_baseline`（原始出处 `labs/corpus-harvest/README.md:155` / `:144`） |
| 判据同源出处清单 | `report.json` → `provenance.criteria_source` |
| 逐轮命令与输出 | `report.json` → `provenance.commands_run` |
| 回流实验方法与产出 | `report.json` → `provenance.mining_method`、`replay_candidates`；原始产物 `out/mine_recomputed.json` |
| 用户侧辅指标明细（含分区） | `report.json` → `provenance.secondary_indicator_method.user_side_sets` |
| 本轮独立验证逐项结果 | `verify.json`（`checked` 4 项 / `discrepancies` 6 项） |
| 台账（规模 / sha256 / 许可证据） | `corpus_ledger.json` → `entries`、`rejected_sources`、`honest_boundaries` |
| 台账 sha256 口径与**可照抄复算命令** | `corpus_ledger.json` → `sha256_semantics`（`kind="manifest_digest"`） |
| 本轮修订逐项处置（7 项） | `revision.json` → `items[]`（`id` / `status` / `evidence`） |
| 选源与许可证据 | `sources.json` → `sources`、`rejected` |
| 机器侧对照表（含 `kefu_delta_pp`） | `out/occ_a.json` → `comparison_table` |

判据出处（**与既有脚本同源，未自造**）：
`labs/corpus-harvest/cross_check_template_rate.py:31`（`REPEAT_MIN = 5`）与 `:34-39`（`skeleton()` 三条替换）
与 `:42-54`（`stats()`）→ 移植到 `labs/multi-industry-corpus/common.py:25-50`；
流程决定词表对齐 `labs/corpus-harvest/README.md:155` 的七类；
英文近似词表 `common.py:66-107`（弱口径，如实标注）；
客服基线常量 `analyze_flow_occupancy.py:40-46`；
回流判据 `mine_recompute.py:33-36`、`:47-68`。

---

## 八、已知局限

> **修订轮（2026-09-22）已收口 5 项**，处置逐项见 `revision.json`。本节先列已收口项（标「已修」），
> 再列仍然成立的事实性局限——**已收口项不再作为「未修」陈述留在这里**。

### 已收口（修订轮，详见 `revision.json`）

| 项 | 原状态 | 处置 |
|---|---|---|
| 台账 `sha256` 口径不明、无可照抄复算命令 | 照字面 `shasum` 会误判「语料被改动」 | 已修：`corpus_ledger.json` 新增 `sha256_semantics`（`kind="manifest_digest"` + 公式 + 可照抄 5 行复算命令 + 自校）。已亲跑 CrossWOZ 一份复算，`779ca2d5…` 与台账逐字一致（items[2]） |
| MASSIVE zh-CN 条数 11514 只计 train | 台账与实测 16521 不符 | 已修：台账改为 16521 = train 11514 + dev 2033 + test 2974，并加 `n_rows_detail` 三分区明细（items[1]） |
| MASSIVE 本体被记为「取不到」 | 台账过期表述 | 已修：改为「实测可下载（HTTP 200，40251390 bytes），但管道三适配器不覆盖 S3 直链 → 未取本体」——把「取不到」与「管道不覆盖」区分开（items[0]） |
| `mine_candidates.py` 静默零结果失败 | `n_candidates=0`、`determinism=True`、exit 0，产物误导 | 已修：根因是名为 `intent` 的变量取到的是 `dialog_act` 字段 1（域）；修复后复跑 `n_candidates=737`，与 `mine_recompute.py` 共享数值字段逐字一致、前 8 条候选逐字段一致；并加零结果告警防复发（items[4]） |
| 零回归两项未复跑 | `report.json.zero_regression` 与 §2.5 都写「本轮未复跑」 | 已修：修订轮亲跑两项，`unittest` 119 tests / OK、`structure_budget` 50 个 .py 文件 / 5070 可执行行 / 全部合规，均 exit 0（items[5]，§2.5） |
| `report.json` rows 的 `side` 精度 | 表体看不出 ABCD 的 27.7pp 来自 action 埋点 | 已修：ABCD 行 `side` 细化为 `system(agent) + action(36482 turns)` 并加 `side_detail`（分母 131611 = agent 95129 + action 36482、埋点占 27.7%、agent-only ≈0.4240）；MultiWOZ/Taskmaster/CrossWOZ 行补「分母不含 action 埋点轮」。`rate` 与 `n` 一字未动（已用改前快照逐行 diff，14 行 0 差异）（items[3]） |

### 仍然成立（事实性局限，未被修订轮改变）

#### 1. ABCD 主指标的分母构成：独立验证员的差异结论算术上不成立

`verify.json.discrepancies[0]` 报 ABCD 的 `flow_decided_rate` 不可复现、差 27.66pp，
并给出「把 action 埋点轮计了两遍」的结论。**该结论的算术不成立**，本批不采纳
（已写入 `report.json.boundaries` 末条与 `revision.json` items[3]）。

独立脚本自报的命中数是 agent 40423 + action 36482 = 76905，
`76905 / 131611 = 0.5843`——**与报告主值只差 0.0006**，不是 0.2766
（已复算：`(40423+36482)/131611 = 0.5843`，`(40334+36482)/131611 = 0.5837`）。
即它自己的分子算对了，但写出的比值对不上自己的分子。

再看「27.7pp 差额恰等于 action 话轮数一次」：
`action_turns / n_turns = 36482/131611 = 0.2772`，确实 ≈ 0.2766。
但「action 计两遍」要成立，`flow_decided_turns` 得是 76482 而不是 76816，
两者差 334，**不相等**——所以「双计 action」的判据站不住。

结论：`out/occ_a.json` 的 `flow_decided_turns=76816 = agent 40334 + action 36482` 是自洽的，
与 `agent_flow_rate_text_only=0.4240` 也不矛盾；真正成立的描述是**分母构成不同**：
action 埋点 36482 轮占分母 27.7%，agent 侧文本形态命中只有约 0.4240（40334/95129）。
修订轮已按这条把 rows 的 `side` / `side_detail` 细化（items[3]）。
同批 ABCD 的 `n`、`agent_turns`、`action_turns` 与两条辅指标均独立复现无误。

#### 2. 两个回流脚本的状态签名字段不是同一个定义

`mine_candidates.py` 报 `n_state_signatures_eligible=237`，
`mine_recompute.py` 报 `n_state_signatures_with_candidate=212`——**两个不同定义**：
前者 = 出现 ≥50 轮的状态签名数，后者 = 带候选的状态签名数。
字段名不同，不可互相当同一字段读；212 < 237 自洽（够密的签名里有 212 个带候选）。
同理 `mine_candidates.py` 多记 `n_signature_skeleton_pairs=25830` 与
`run_at`/`_schema`/`corpus`/`method` 等报告壳字段。
**所有共同数值字段（737 / 8904 / 40657 / 5173 / 0.1018 / 0.1272 等）两脚本逐字一致。**

#### 3. `MASSIVE` 主指标侧数据源不来自管道落盘

本批次 `MASSIVE` 只落了许可证据与元数据（`corpus_ledger.json` entries 的 MASSIVE 条，
README.md 34420 字节）。52 语种本体（40251390 bytes）**实测可下载**，
但本管道三个平台适配器都不覆盖 S3 直链 → 未取本体入库；
辅指标里那两条 user 侧条目是分析层**走 S3 直链补跑**算的，
落仓外 `/tmp`，不来自 `tools/corpus_fetch` 管道落盘、未写入 `~/corpus`。

#### 4. 英文文本启发式是最弱一档口径

`EN_FLOW_PATTERNS` 是中文客服词的英译近似，不是同源标注
（`common.py:66-107`）→ MultiWOZ 2.2 / Taskmaster TM-1 的占比只能读作**下界**，
不能当「该域占比低」的证据。

#### 5. 医疗行业只有文本口径

`Primock57` 的 `audio/` 是 Git LFS 指针（114 个文件、合计 15160 字节），
codeload zip 不含 LFS 对象 → 只有文本转录与临床记录可用，无音频口径。

#### 6. 回流实验的产出面很窄

737 条候选只覆盖 10.18% 的系统话轮（带状态签名的话轮 12.72%），
且集中在「系统确认/回显某个槽的值」这一类（数据库查询回显），不是通用话术生成。
M3「日志挖掘 → 话术建议」的最保守路径在公开数据上走得通，但产出面就是这个窄场景。

#### 7. 五个播报场景仍是公开数据真空

导航播报 / 机场车站广播 / 工业设备 / 政务热线 / 快递通知——无任何一行数字支撑，
不得用近似场景冒充（见 §五）。

#### 8. 台账产物名与 `commands_run` 曾不一致（已更正，不改数值）

`report.json.provenance.commands_run` 曾写过 `occ_inj.json`，`out/` 实际是 `report_inject.json`
（另有 `occ_a.json` / `occ_b.json` / `report_occ.json`）。三份 occ 产物 ABCD 数值一致（0.5837），
不构成数值差异；名称已更正。

---
