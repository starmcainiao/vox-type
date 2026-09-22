# T22 · 多行业公开语料收集与「预铸适用边界」跨行业验证

> 任务卡：`docs/tasks/T22-多行业语料收集与适用性验证.md`
> 本目录是 **labs**（一次性实验），产物不进任何层的契约。

## 一句话结论

预铸的可行空间（「系统侧话轮里由流程决定说什么话的比例」）**跨语料成立但跨行业不可平移**。
在**公开数据能触达**的范围内，任务对话域的该占比落在客服基线的同一量级或之上；
而导航播报 / 机场车站广播 / 工业设备 / 政务热线 / 快递通知这五类播报场景**没有公开语料可验证**，
不能用近似场景冒充。全部数字见文末 §六。

---

## 一、口径：主指标 / 辅指标，以及「用户侧 vs 系统侧」

### 1.1 主指标 —— 系统/助手侧「流程决定话轮」占比

一份语料里，**系统（助手）说话**的话轮中，有多少是「由流程而非当场内容决定说什么」的。
这直接回答：预铸能覆盖多少。

### 1.2 辅指标 —— 「指令模板化率」（句式可复用率）

同一份语料内，去实体值后的**句式骨架**出现 ≥ 5 次的话轮占比（`common.py:27-50`，
与 `labs/corpus-harvest/cross_check_template_rate.py:31-39` 逐字同源）。
它回答：句式能不能被预铸成有限模板。

### 1.3 「用户侧」vs「系统侧」——两者测的不是同一件事（混用会让整卡退回）

| | 系统侧（主指标） | 用户侧（辅指标） |
|---|---|---|
| 读的是谁的话 | 助手 / agent / SYSTEM / ASSISTANT | 用户的指令 |
| 回答的问题 | **系统说什么由流程决定** → 预铸能覆盖多少 | **用户怎么说** → 指令能不能被模板化 |
| 对预铸的意义 | 直接：预铸的是系统话术 | 间接：只说明用户输入端的离散度 |
| 本批数据集 | CrossWOZ / ABCD / MultiWOZ 2.1 / MultiWOZ 2.2 / Taskmaster TM-1 | MASSIVE zh-CN / HWU64 |

**不得用用户侧指令集冒充主指标。** 落地约束（可审）：
`analyze_flow_occupancy.py:499-515` 把 HWU64 放在独立顶层键
`user_side_instruction_sets`，**不在** `corpora` 里，并自带警示 note；
而门禁 `common.verify_occupancy`（`common.py:121-153`）只遍历 `corpora`，
所以用户侧集**不可能**被当成系统侧占比参与判定。
`report.json` 的 rows 里唯一用户侧条目（MASSIVE zh-CN）只报 `instruction_template_rate`，
**没有** `flow_decided_rate` 一行。

### 1.4 口径强弱是排序第一位的字段

同样一份数据、两种口径，占比可以差到**近 50 个百分点**
（`report.json` rows 里 MultiWOZ 2.1 与 2.2 那一组——同一批话轮，标注口径 vs 纯文本启发式；
具体数值以 `report.json` 为准）。
**任何跨集横比，先看 `method_strength` 再比数值。** 分档（活引用 `report.json.provenance.method_strength`）：

| 档 | 数据集 | 判据来源 |
|---|---|---|
| 强 | CrossWOZ | 系统侧对话行为标注（intent） |
| 中 | ABCD | 业务动作埋点（`action` 角色）直接计 + agent 文本启发式 |
| 中 | MultiWOZ 2.1 | 系统行为 act 标签，语义粒度粗 |
| 弱 | MultiWOZ 2.2 / Taskmaster TM-1 | 无标注，纯文本形态启发式 → **只能当下界** |

英文文本启发式用的 `EN_FLOW_PATTERNS`（`common.py:66-107`）是中文客服词的**英译近似**，
不是同源标注——这一档数值最弱，如实标注。

---

## 二、复现命令

### 2.1 前置：语料（落仓外，不入库）

```bash
# 收集（许可审计 fail-closed，台账写仓外）
python3 tools/corpus_fetch/fetch.py \
  --sources tools/corpus_fetch/sources.json \
  --lock ~/corpus/corpus.lock.json

# 巡检：sha256 回校
python3 tools/corpus_fetch/fetch.py --recheck --lock ~/corpus/corpus.lock.json
```

脚本读语料的位置由 `CORPUS_ROOT` 决定，默认 `~/corpus`
（`analyze_flow_occupancy.py:37`、`mine_recompute.py:74`）。改位置：`CORPUS_ROOT=/path/to/corpus python3 ...`。
语料本体**一律不入库**；仓内只有台账 `corpus_ledger.json`（路径一律相对 `~/corpus/`，无绝对路径）。

### 2.2 分析（三脚本，全部固定种子 / 无随机）

```bash
cd labs/multi-industry-corpus

# 主指标 + 辅指标（四份对话集，各一份流程决定占比，与客服 23.0% 对照）
python3 analyze_flow_occupancy.py --out out/occ_a.json
python3 analyze_flow_occupancy.py --out out/occ_b.json      # 确定性核对：去 run_at 后应逐字一致
diff <(python3 -c "import json;print(json.dumps(json.load(open('out/occ_a.json')),ensure_ascii=False,indent=2,sort_keys=True,default=str))" | grep -v '"run_at"') \
    <(python3 -c "import json;print(json.dumps(json.load(open('out/occ_b.json')),ensure_ascii=False,indent=2,sort_keys=True,default=str))" | grep -v '"run_at"')   # 无输出 = 一致

# 回流实验（从会话记录挖「某状态下系统固定说什么」）——用修正版
python3 mine_recompute.py --out out/mine_a.json
python3 mine_recompute.py --out out/mine_b.json
diff -q out/mine_a.json out/mine_b.json                     # 无输出 = 一致
```

已核对：`occ_a`/`occ_b` 去 `run_at` 后逐字一致，`mine_a`/`mine_b` 逐字节一致
（`report.json.provenance.determinism = true`）。

### 2.3 反空转：注入验证（判据可判红）

```bash
python3 analyze_flow_occupancy.py --out out/report_inject.json --inject-all-flow
# 期望：全部集占比 = 100%，用例必须失败（退出码 1）
```

已核对：`out/report_inject.json` 里全部集占比为 100%。
门禁逻辑在 `common.verify_occupancy`（`common.py:139-153`）：
`expect_not_all=True` 时任何集占比 ≥ 0.999 直接判失败；
注入跑法把该开关置 False（`analyze_flow_occupancy.py:570`），所以注入产物本身 gate 记 pass、
**是作为证据留存的**，不是通过的产物。正常产物 `out/occ_a.json` 的 gate 为 `pass`、`problems=[]`。

### 2.4 零回归

```bash
python3 -m unittest discover -s tools/tests -t .        # 119 tests
python3 -m tools.structure_budget.check                  # 结构预算
```

条数活引用 `report.json.provenance.zero_regression`（`tools/tests` 119 全绿 + 结构预算全部合规；
本轮已复跑核对，见 `verify.json.checked[0-1]`）。

---

## 三、跨行业对照结论

对照表、样本量、每集口径与差值，全部活引用 `report.json`（`rows` + `comparison_note` +
`provenance.method_strength`）；本 README 不复述数字。

1. **唯一一对口径可比的对**：客服基线（中文、标注驱动）与 CrossWOZ（中文、标注驱动）。
   CrossWOZ **低于**客服基线（`report.json.comparison_note` 给出 pp 差值与原因：
   CrossWOZ 系统侧只有 5 类仪式 intent，而客服基线含合规提示 / 转人工等客服专属流程轮）。
   → 结论是**保守的**：换到另一个中文任务对话域，预铸可行空间只缩不涨。
2. **偏高不能反推客服偏低**：ABCD 与 MultiWOZ 2.1 高于客服基线，但这两族把「业务动作埋点」
   和「系统反问/提议确认」也算作流程决定，这两族在客服基线的七类词表里没有对应项，
   **属于口径更宽**（`report.json.comparison_note`）。
3. **无标注集只能当下界**：MultiWOZ 2.2 与 Taskmaster TM-1 无行为标注，
   纯文本启发式 → 数值是**下界**，不能作为「该域占比低」的证据。
4. **辅指标侧的唯一有效对照**：MASSIVE zh-CN（原始转写）与客服基线同档可比，
   两者**同量级、没有翻转**（`report.json.comparison_note`）→ 支持
   `labs/corpus-harvest/README.md:144` 的结论「句式可复用率很低是跨语料成立的」。
5. **预归一化会伪造高值**：HWU64 取的是数据集自带的**已归一化**规范写法，
   其辅指标值远高于 MASSIVE / 客服基线是**口径造成的**（预归一化 vs 原始转写），
   不是「用户话术更模板」——与客服 / MASSIVE 不可直接比（`report.json.comparison_note`）。
6. **同一批话轮、两种口径 → 占比差近 50pp**（MultiWOZ 2.1 vs 2.2）。
   这是本表里最能说明「占比数字完全由口径决定」的证据。

---

## 四、回流实验（M3「日志挖掘 → 话术建议」在公开数据上能走多远）

用 CrossWOZ 的**系统侧对话行为标注**做状态分组，组内按骨架取高频模板，
候选规则与显著性下限活引用 `report.json.provenance.mining_method`
（固定种子；「同一状态签名下同一骨架出现 ≥ 阈值」；「状态签名自身出现 ≥ 下限」）。

- 挖出的候选条数、覆盖到的状态与话轮占比 → 活引用 `report.json.replay_candidates` 与
  `out/mine_recomputed.json`（`n_state_signatures` / `coverage_of_all_sys_turns` /
  `coverage_of_stateful_sys_turns` / `candidates_by_group_hits`）。
- **能走多远的答案**：能挖出候选，但覆盖的是**「系统确认某个槽的值」这一类窄场景**
  （数据库查询回显），**不是通用话术生成**。
  即：M3 的最保守路径在公开数据上走得通，但产出面是窄的。

---

## 五、诚实边界：公开数据真空

以下五类场景**没有公开语料可验证**（T22 侦察已确认），**不得用近似场景冒充**：

| 场景 | 状态 |
|---|---|
| 导航播报 | 公开数据真空（`Angeriod/in_car_commands` 为空集，已入 `rejected_sources`） |
| 机场车站广播 | 公开数据真空（未见任何可取广播语音/转写数据集） |
| 工业设备 | 公开数据真空（无机器人/PLC/设备语音播报的公开标注语料） |
| 政务热线 | 公开数据真空（政务对话非公开，或无许可干净来源） |
| 快递通知 | 公开数据真空（无快递/物流语音通知公开语料） |

本批入选的六份集覆盖的是**「任务对话」「电商客服」「用户侧指令」**——
**不是**上面这五类播报场景。逐条陈述见 `report.json.boundaries`。

---

## 六、数字出处（一律活引用，本 README 不复述数值）

| 要什么 | 去哪 |
|---|---|
| 全部占比 / 样本量 / 口径说明 | `report.json` → `rows` |
| 对照与口径差异论证 | `report.json` → `comparison_note` |
| 口径强弱分档 | `report.json` → `provenance.method_strength` |
| 客服基线 | `report.json` → `provenance.kefu_baseline`（原始出处 `labs/corpus-harvest/README.md:155` / `:144`） |
| 判据同源出处 | `report.json` → `provenance.criteria_source` |
| 回流实验方法与产出 | `report.json` → `provenance.mining_method`、`replay_candidates`；原始产物 `out/mine_recomputed.json` |
| 对照表含每集方法说明 | `out/occ_a.json` → `comparison_table`（含 `method_strength` / `kefu_delta_pp`） |
| 本轮逐项核对结果 | `verify.json` |
| 拒收集与理由 | `corpus_ledger.json` → `rejected_sources`；选源与许可证据 `sources.json` |

判据出处（**与既有脚本同源，未自造**）：
`labs/corpus-harvest/cross_check_template_rate.py:31-39`（`skeleton()` 三条替换 + `REPEAT_MIN = 5`）→
移植到 `labs/multi-industry-corpus/common.py:25-50`；
流程决定词表对齐 `labs/corpus-harvest/README.md:155` 的七类；
英文近似词表 `common.py:66-107`（弱口径，如实标注）；
客服基线常量 `analyze_flow_occupancy.py:40-46`。

---

## 七、已知局限

按严重度排序，全部是已定位的事实，不是推测：

1. **`mine_candidates.py` 存在静默零结果失败。** 它的 `state_signatures()`（`mine_candidates.py:47-65`）
   按 **4 元组**解 CrossWOZ 的 `dialog_act`，但注释自述「第 3 位是槽名，第 4 位是值」
   与实测结构 `[类别, 域, 槽名, 值]` 错位（取 `da[0]` 当 intent、`da[2]` 当域、`da[3]` 当值）
   → 结果是 0 个状态签名、0 条候选，而 `determinism_check` 仍返回 `True`
   （空列表也逐字一致）、退出码 0。
   **本报告的所有数字来自 `mine_recompute.py`**（同判据、按实测结构），
   同目录并存的 `out/mine_candidates.json`（`n_candidates=0`）是**误导性产物**，请勿引用。
   → 这条正是本项目「禁止静默降级」纪律要拦的那一类事故：不报错、自检过、退出码 0、什么都没挖到。
2. **台账 `sha256` 是 manifest 指纹，不是 zip 哈希。**
   `corpus_ledger.json:3` 与 `tools/corpus_fetch/fetch.py:902-911` 的 `manifest_digest()`
   定义为「对 (path, size, sha256) 排序后求 sha256」。
   所以直接 `shasum -a 256 <zip>` 去对台账值**必然不符**——按 manifest 公式手算才逐字一致
   （已抽 CrossWOZ / ABCD 两份核对，字节数也与台账一致，见 `verify.json.checked[0-1]`）。
   建议后续给台账 `_note` 补一条复算命令。
3. **两份台账条目已过期未同步**（`report.json.boundaries[6-7]`）：
   ① `AmazonScience/massive` 的本体 tar.gz 实测**可下载**（S3 直链），
      而 `corpus_ledger.json:218-222` 仍记「本体取不到」；
   ② MASSIVE zh-CN 实测条数与 `corpus_ledger.json:97` 记的条数不符
      （后者疑似只计 train 分区）。两项目前 ledger 未同步更新。
4. **`report.json` 的 `side` 字段比机器产物更粗**（4 处，不影响数值）：
   ABCD 的 `flow_decided_rate` 分母含「业务动作埋点」话轮，
   在 `out/occ_a.json` 里 `side='system(agent) + action'`，`report.json` 统一写成 `'system'`。
   只看 `report.json` 会看不到这个分母构成——读占比时请回 `out/occ_a.json` 看每集 `method` 字段。
5. **github-archive 来源无平台侧哈希可交叉核对**
   （`verified_against_platform=false`，如实记录于 `corpus_ledger.json` → `honest_boundaries`）。
6. **`primock57` 音频不可得**：`audio/` 是 Git LFS 指针，codeload 整包 zip 不含 LFS 对象，
   只有文本转录与临床记录可用（`corpus_ledger.json:129` 与 `rejected_sources` 同名条目）。
   医疗行业因此**只有文本口径，无音频口径**。
7. **`MASSIVE` 本批次只落了许可证据与元数据**（README.md，`corpus_ledger.json:86-98`），
   52 语种本体在 Amazon S3，本管道无 S3 适配器。
   辅指标里 MASSIVE zh-CN 那一行是补跑取的，不来自管道落盘。
8. **英文文本启发式是最弱一档口径**：`EN_FLOW_PATTERNS` 是中文客服词的英译近似，
   不是同源标注 → MultiWOZ 2.2 / Taskmaster TM-1 的占比只能读作**下界**。
