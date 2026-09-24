# heat-kefu · 话术准入申报表（T31b）

> 包：`packs/heat_kefu`（69 key / 69 条资产 / 四属性 0 违规 / 预铸成功率 100%）
>   （T31b 铸入 40 单句；**T33 追加 29 条拆句**，见 §1.5「拆句铸入申报」）
> 数据分级：**公开级**（kefu 供热预设是自造业务 demo 文案；无录音/真实会话/内网地址/token，
>   FAQ 条目标注「演示样例」）。用户拍板放行进 `packs/`（`docs/15 §四`）。
> 本包无真人标注，故**不需要**「不进公开分发」声明。

## 0. yaml 指纹（同源基线）

| 项 | 值 |
|---|---|
| 源文件 | `<kefu-agent 仓根>/organs/客服/brain/prompts/供热预设.yaml` |
| sha256 | `a0d9194a95a876d12103381c3373c63a98d021e23853a998429bb3ae2cbbfe44` |
| 大小 | 19808 bytes / 287 行 |
| mtime | 2026-09-10 09:47 |
| top-level key | 76（scalar 67 + dict 9） |

**指纹即门禁**：yaml 一变，`tests/test_source_of_truth.py::test_yaml_fingerprint_unchanged`
立刻变红，且 kefu 钩子按 yaml 原文查文本档也会自动不命中——**fail-closed，不会播出旧版话术**。

## 0.1 对账表（yaml 76 top-level key 全覆盖）

| 归类 | 数量 | 说明 |
|---|---|---|
| 拆句铸入（T33） | **29** | 14 条多分句整段按 `<源 key>__<句序>` 拆句逐段铸入，见 §1.5 |
| 必铸（本包） | **40** | 35 个 top-level key + `repair_ask.*` 嵌套 5 个 + `repair_ask_natural_*` 嵌套 3 个（`repair_ask` dict 本身不计为话术） |
| exclusion·多分句（整段） | **14** | ≥2 句末标点，整段不铸（`docs/11 §11.6`「一句一 variant」）；T33 起**逐段铸入 29 条**（§1.5），整段由 `find_hit_sequence` 逐段覆盖命中 |
| exclusion·带槽 | **13** | variant 不得含槽位（`docs/06 §6.2.5`）→ 走原路 |
| 非话术结构 / 配置 | **13** | 结构 dict/int 6 + 非话术标量 7 |
| **合计** | **76** | = yaml top-level 实际 key 数，无遗漏无越界（40 单句 + 14 整段 + 13 带槽 + 13 非话术；29 拆句是这 14 整段的派生，不占新的 top-level key） |

非话术 13 项：`next_prefetch`(dict) `routing`(dict) `trace`(dict) `transfer_route`(dict) `slot_labels`(dict)
`prompts`(dict) `model_params`(dict) `faq_overrides`(list) `pay_cache_ttl_seconds`(int)
`identity`(str 人设) `tone`(str 语气) `prefetch_ready_suffix`(空串) `compact_summary_prompt`(提示词)。

### 与 T31b 卡面清单的出入（以实测判据为准，卡已授权）

卡的「必铸 40 清单」正文与「exclusion 14」段互相矛盾（`repair_confirm_question` 同列两处；
`internal_chat_hint` / `work_order_no_unknown` / `clarify_options` / `repair_ask_natural_address` /
`repair_ask_natural_userNo` 等 7 个 key 归类写错）。本包**完全按 `compiler._validate_variant`
的实测判据**（`_SENTENCE_TERMINATORS = '。！？!?'`，≥2 即拒）划分：

| key | 卡面位置 | 实测句末标点 | 本包归属 |
|---|---|---|---|
| repair_confirm_question | 40 清单 / exclusion 14 两列 | 2 | **exclusion·多分句** |
| internal_chat_hint | 40 清单 | 2 | **exclusion·多分句** |
| work_order_no_unknown | 40 清单 | 2 | **exclusion·多分句** |
| repair_ask_natural_address | 40 清单 | 2 | **exclusion·多分句** |
| repair_ask_natural_userNo | 40 清单 | 2 | **exclusion·多分句** |
| clarify_options | exclusion 14 附注 | 1 | **必铸** |
| repair_ask_natural_problemType | 卡注「若过判据则并入」 | 1 | **必铸** |
| repair_ask_natural_desc | 卡注「若过判据则并入」 | 1 | **必铸** |
| faq_empty | 未列 | 1 | **必铸** |
| channel_url_note | 未列 | 1 | **必铸** |
| repair_edit_ask | 未列 | 1 | **必铸** |

35 + 5 + 3 = 40；卡授权「40–42 以实测为准」，最终 **40**。

### 3 个 T31 未归类标量的更正

`pending_intent_notice` / `profile_choice_ask` / `profile_choice_done` —— T31 执行报告称「前两者
不含槽位」，**该判断有误**。实测三者都带槽：`{intent_label}` / `{total}`+`{profiles}` /
`{userNo}`+`{community}`（见下表 ②）。按验收方裁定归 **exclusion·带槽 13**。

## 1. 必铸 40（逐字铸入，一个标点不改）

| key | A1/A2 | yaml 路径 | 备注 |
|---|---|---|---|
| `options` | A2 | `options` |  |
| `transfer_ready` | A2 | `transfer_ready` |  |
| `transfer_queued` | A2 | `transfer_queued` |  |
| `turn_limit_notice` | A2 | `turn_limit_notice` |  |
| `turn_limit_notice_v2` | A2 | `turn_limit_notice_v2` |  |
| `clarify_options` | A2 | `clarify_options` |  |
| `farewell` | A1 | `farewell` | 剧本唯一终态（terminal_keys）。 |
| `fallback_route` | A2 | `fallback_route` |  |
| `faq_empty` | A2 | `faq_empty` |  |
| `pay_ask_user_no` | A1 | `pay_ask_user_no` |  |
| `pay_verify_user_no` | A1 | `pay_verify_user_no` |  |
| `pay_no_bills` | A1 | `pay_no_bills` |  |
| `pay_due_hint` | A1 | `pay_due_hint` |  |
| `pay_error` | A1 | `pay_error` |  |
| `channel_url_note` | A2 | `channel_url_note` | 句末标点仅 1 个（结尾 `。`），中间是 `；` 与 `——`，判据不计——实测 PASS。 |
| `repair_edit_ask` | A1 | `repair_edit_ask` |  |
| `work_order_hint` | A2 | `work_order_hint` |  |
| `work_order_ask` | A2 | `work_order_ask` |  |
| `work_order_empty` | A2 | `work_order_empty` |  |
| `work_order_error` | A2 | `work_order_error` |  |
| `work_order_eta_unavailable` | A2 | `work_order_eta_unavailable` |  |
| `work_order_eta_guide` | A2 | `work_order_eta_guide` |  |
| `repair_error` | A2 | `repair_error` |  |
| `collect_limit` | A2 | `collect_limit` |  |
| `transfer_suggest` | A2 | `transfer_suggest` |  |
| `realtime_ask` | A2 | `realtime_ask` |  |
| `realtime_empty` | A2 | `realtime_empty` |  |
| `stop_warm_emergency` | A2 | `stop_warm_emergency` | **「XX区域」是 yaml 字面文本**（kefu demo 局限，非占位符、非槽位），按 yaml 原文照铸；上线前需在 kefu 侧替换为真实区域名，届时 yaml 变更 → 指纹漂移 → 钩子自动不命中（fail-closed）。 |
| `stop_warm_followup` | A2 | `stop_warm_followup` |  |
| `off_hours_repair` | A2 | `off_hours_repair` |  |
| `off_hours_other` | A2 | `off_hours_other` |  |
| `off_hours_urgent` | A2 | `off_hours_urgent` |  |
| `repair_ask_userNo` | A1 | `repair_ask.userNo` |  |
| `repair_ask_contactPhone` | A1 | `repair_ask.contactPhone` |  |
| `repair_ask_address` | A1 | `repair_ask.address` |  |
| `repair_ask_problemType` | A1 | `repair_ask.problemType` |  |
| `repair_ask_desc` | A1 | `repair_ask.desc` |  |
| `repair_ask_natural_contactPhone` | A1 | `repair_ask_natural_contactPhone` | yaml 原文本身即单句，与 `_address`/`_userNo`（多分句）判据不同——exclusion 边界由数据决定，不是裁出来的。 |
| `repair_ask_natural_problemType` | A1 | `repair_ask_natural_problemType` | 同 `_desc`：卡面预写清单未列，实测判据 PASS，按「以实测为准」一并铸入。 |
| `repair_ask_natural_desc` | A1 | `repair_ask_natural_desc` | 实测判据 PASS，卡面预写清单未列，按「以实测为准」一并铸入。 |

A1（流程决定性话轮）= 15 条：问候/意图澄清/转人工/收尾等由流程决定的话轮。
A2（业务可枚举播报）= 25 条：账单/工单/实时参数/停暖/非营业时间等固定结果播报。

## 1.5 拆句铸入申报（T33）

### 为什么要拆

`compiler/source.py::_validate_variant` 的判据是「一句一 variant」（句末标点 < 2，
`docs/06 §6.1.4` 字模最小粒度 = 一句）。所以 14 条多分句整段（§2）整段进不了包 →
brain 说整段时文本档查不到 → 走原路（秒级 TTS）。这批含 `opening`（每通必说）、
`fallback_internal`（系统降级）、`repair_confirm_question`（确认）等高频句。

T33 按 `docs/10 §10.7` 的裁定把它们**逐段**铸入，查询面用新入口
`adapters.framework_kefu.find_hit_sequence` 逐字覆盖命中（每段仍是「归一化后逐字相等」，
最长优先，任一段不中即整体未命中、不播半句）。整段本身仍不在包内
（`find_hit` 对整段不回归未命中）。

### 命名与拆句规则（冻结）

- 拆句 key = `<源 key>__<句序>`，句序从 1 起（3 句 = `__1`/`__2`/`__3`）。
- 拆句点 = 按 `compiler.source._SENTENCE_TERMINATORS`（`。！？!?`）**逐字符**切分，
  句末标点归前句，使 `"".join(拆句) == yaml 原文`（逐字符）。
  **不得用任何其他切分规则**（例如不得按 `；` 切）。
- 每条 1 个 variant、`rates: ["normal"]`、文本与 yaml 子串**逐字一致**（含标点与引号）。
- 同源判据：`tests/test_source_of_truth.py` 的
  `test_sequence_split_concatenates_back_to_yaml`（拼回 == yaml 原文，逐码位）与
  `test_sequence_split_count_matches_yaml_terminator_count`（N == 实测句数）。

### 29 条拆句的 A1/A2 归类（源 key 类别，拆句继承）

| 源 key（类别） | 拆句 key | 句数 |
|---|---|---|
| `opening`（A1） | `opening__1`、`opening__2` | 2 |
| `chat_smalltalk`（A1） | `chat_smalltalk__1`、`chat_smalltalk__2` | 2 |
| `clarify_work_order`（A1） | `clarify_work_order__1`、`clarify_work_order__2` | 2 |
| `clarify_repair`（A1） | `clarify_repair__1`、`clarify_repair__2`、`clarify_repair__3` | 3 |
| `repair_confirm_question`（A1） | `repair_confirm_question__1`、`repair_confirm_question__2` | 2 |
| `inject_refuse`（A1） | `inject_refuse__1`、`inject_refuse__2` | 2 |
| `repair_ask_natural_address`（A1） | `repair_ask_natural_address__1`、`repair_ask_natural_address__2` | 2 |
| `repair_ask_natural_userNo`（A1） | `repair_ask_natural_userNo__1`、`repair_ask_natural_userNo__2` | 2 |
| `fallback_internal`（A2） | `fallback_internal__1`、`fallback_internal__2` | 2 |
| `lifeboat_fallback`（A2） | `lifeboat_fallback__1`、`lifeboat_fallback__2` | 2 |
| `user_no_unknown`（A2） | `user_no_unknown__1`、`user_no_unknown__2` | 2 |
| `work_order_no_unknown`（A2） | `work_order_no_unknown__1`、`work_order_no_unknown__2` | 2 |
| `internal_chat_hint`（A2） | `internal_chat_hint__1`、`internal_chat_hint__2` | 2 |
| `stop_warm_plan`（A2） | `stop_warm_plan__1`、`stop_warm_plan__2` | 2 |

A1（流程决定性话轮）源 8 个 → **拆句 17 条**：`opening` 2 + `chat_smalltalk` 2 +
`clarify_work_order` 2 + `clarify_repair` 3 + `repair_confirm_question` 2 + `inject_refuse` 2 +
`repair_ask_natural_address` 2 + `repair_ask_natural_userNo` 2；
A2（业务可枚举播报）源 6 个 → **拆句 12 条**：`fallback_internal` 2 + `lifeboat_fallback` 2 +
`user_no_unknown` 2 + `work_order_no_unknown` 2 + `internal_chat_hint` 2 + `stop_warm_plan` 2。
（17 + 12 = 29。验收方 2026-09-21 复算更正——原稿写「A1 16 / A2 13」，总数对、分项错。）

### 重复句（卡内点名的对账事实，不是笔误）

`点下方按钮或直接说明即可。` 同时属于 `clarify_work_order__2` 与 `clarify_repair__3`
—— **两条都铸**。各自归属明确，包内允许同文本两条资产；文本档索引取首条是既有行为
（`pick_by_text` 的「顺序稳定」）。`tests/test_variant_texts_are_distinct_except_the_known_duplicate`
把这唯一一对写成**具名豁免**，不放宽成「允许任意重复」。

### script.json

29 条拆句各加一个 unit（`{"key": "<拆句 key>", "rate": "normal"}`）——这些句子确实可能被播报，
且包测试 `test_script_covers_all_baked_keys` 的口径是「剧本覆盖全部包内 key」
（比 C3 的单向检查更严）。

⚠️ **unit 必须插在终态单元之前**：编译器 `compiler/checks.py` 的 C1b（无孤立枝）
判定「首个终态单元之后的单元归 orphan_branch」，而本包 `terminal_keys = ["farewell"]`。
若把 29 条追加在 `farewell` 之后，`bin/vox pack check` 会报 29 条 `orphan_branch`
+ 1 条 `no_exit`（实测 rc 4）。T33 的实际排布是 68 个 SAY unit + 末位 `farewell`，
check rc 0、violations 0。

### 对账（刷新）

| 归类 | 数量 |
|---|---|
| 必铸单句 | 40 |
| 拆句铸入（T33） | 29 |
| 不铸带槽 | 13 |
| 非话术结构 / 配置 | 13 |
| **合计** | **76**（= yaml top-level key 数，守恒） |

禁入项 ⅰ–ⅳ 零命中：40 条全部**无槽位占位符**（测试
`test_no_variant_contains_slot_placeholder` 逐条断言）、全部**单句**
（`test_every_variant_passes_one_sentence_rule` 用编译器同一判据断言）、
无真人标注语料派生（源为业务配置 yaml，非语料库）。

## 2. exclusion·多分句 14（原因：`docs/11 §11.6`「一句一 variant」设计）

字模最小粒度 = 一句（`compiler/source.py`）。整段话术天然多分句，**不拆句**
（brain 说出的是整段文本，包存单句则文本档对不上，破同源）、**不放宽内核**
（`compiler` 是冻结区，本批不开）→ 不铸，运行期走原路。

| key | 实测句末标点 | yaml 文本（节选） |
|---|---|---|
| `opening` | 2 | 您好，我是供热智能客服小暖。报修、查账单缴费、供暖政策咨询，都可以直接跟我说，比如“我要报修”或“查一下账单”。 |
| `chat_smalltalk` | 2 | 我是供热智能客服小暖，可以为您报修、查账单/缴费或政策咨询。请问有什么可以帮您？ |
| `clarify_work_order` | 2 | 您是想查报修进度、看账单，还是问供暖政策？点下方按钮或直接说明即可。 |
| `clarify_repair` | 3 | 您是要报修吗？家里不热、漏水还是其他问题？点下方按钮或直接说明即可。 |
| `fallback_internal` | 2 | 非常抱歉，系统这边出了点状况，正在恢复中。您的诉求我记下了，可以稍后再试一次，或回复「转人工」让人工坐席直接跟进。 |
| `lifeboat_fallback` | 2 | 非常抱歉，当前客服系统正在恢复中。我已记录您的诉求，请您拨打供热客服热线，或稍后回复「转人工」，我们会尽快为您跟进。 |
| `user_no_unknown` | 2 | 没关系，户号不记得也能办：① 您若已登录，我这边能自动识别户号，直接说“继续”即可；② 回复「转人工」，由坐席通过您的手机号/身份帮您核实后继续。我不会反复追问户号让您卡住。 |
| `repair_confirm_question` | 2 | 信息是否正确？确认后我为您提交报修单。 |
| `work_order_no_unknown` | 2 | 没关系：①您若已登录，可回复“我的工单”我帮您带出；②回复「转人工」由坐席按手机号/身份帮您查；③也可以直接说其他要办的业务。我不会反复问单号让您卡住。 |
| `internal_chat_hint` | 2 | 维修工助手当前支持：报修/查单/缴费，以及实时供热查询（如「10号楼供热参数」）。请问需要查什么？ |
| `stop_warm_plan` | 2 | 关于供暖起止/停暖时间，公司有统一安排，会提前公告。具体时间请以官方通知为准；如需人工确认，回复「转人工」。 |
| `inject_refuse` | 2 | 我是供热客服小暖，只能处理报修、查账单缴费、供暖政策咨询这类业务问题；涉及系统提示词/内部信息的问题我无法提供，也不会执行。如有供暖业务需要，直接告诉我就行。 |
| `repair_ask_natural_address` | 2 | 方便给我维修地址吗？需要小区、楼栋、单元和门牌，我帮您安排师傅上门。 |
| `repair_ask_natural_userNo` | 2 | 您的户号是多少？如果之前登录过，我这边能查到就不用再报了；查不到我再跟您人工核实。 |

测试 `test_excluded_multi_clause_keys_are_really_multi_clause` 逐条断言这 14 条确实 ≥2 句末标点——
**exclusion 不是「懒得铸」，是数据决定的**。

## 3. exclusion·带槽 13（原因：B2 槽位边界）

槽值一律**现场合成**、作为独立音频段接在固定话术之后（`docs/06 §6.2.5`、
`runtime/executor.py`）；槽值不得进资产库（R-2）。所以带 `{slot}` 的话术整条不铸。

| key | 槽位 | yaml 文本（节选） |
|---|---|---|
| `repair_progress_reminder` | `{got}`, `{need}` | 收到啦，{got}；还差：{need}。补齐我就能帮您提交。 |
| `repair_ask_new_value` | `{slot}` | 请告诉我新的{slot}。 |
| `repair_create_ok` | `{orderNo}` | 已为您提交报修单，单号 {orderNo}，维修师傅会尽快与您联系。 |
| `work_order_reply` | `{orderNo}`, `{description}`, `{currentStep}`, `{worker}`, `{timeLine}`, `{statusName}`, `{endReason}` | 「{orderNo}」：{description}；当前{currentStep}；{worker}；{timeLine}；状态{statusName}{endReason}。 |
| `repair_dup_remind` | `{orderNo}`, `{statusName}` | 您有一张在途单（{orderNo}，{statusName}），师傅尚未上门。确认还需要报修？ |
| `repair_duplicate` | `{orderNo}` | 您刚提交过同一报修，单号 {orderNo}，维修师傅会尽快联系您，无需重复提交。 |
| `confirm_silence` | `{summary}` | 我需要您确认一下报修信息才能提交。{summary} |
| `transfer_summary_missing` | `{missing}` | {missing}未提供，坐席需向用户核实。 |
| `realtime_reply` | `{building}`, `{house}`, `{roomTemp}`, `{time}` | {building}{house}号：实时室温 {roomTemp}℃，数据时间 {time}。 |
| `steer_return_notice` | `{flow_hint}` | 回到刚才的事——{flow_hint}，不耽误咱们继续办。 |
| `pending_intent_notice` | `{intent_label}` | 对了，您刚才还提到{intent_label}——现在帮您办吗？ |
| `profile_choice_ask` | `{total}`, `{profiles}` | 您名下 {total} 套档案：{profiles}，这次办哪一套？回复序号或点按钮即可。 |
| `profile_choice_done` | `{userNo}`, `{community}` | 好的，本次为您办理 {userNo}（{community}）的业务，请继续。 |

## 4. 双向断言：exclusion 真实走原路

只证明「表里没有该 key」不够——必须证明**查询真的查不到**。
`tests/test_source_of_truth.py` 用 `assets.load_pack` 装载真包、`find_hit` 真查：

| 断言 | 范围 | 期望 | 结果 |
|---|---|---|---|
| 必铸命中 | 40 条 yaml 原文 → 文本档 | `entry is not None` 且 `miss_reason is None` | 40/40 |
| 多分句未命中（文本档） | 14 条 yaml 原文 | `entry is None` | 0/14 命中 |
| 多分句未命中（key 档） | 14 + 13 条 | `entry is None` | 0/27 命中 |
| 带槽填充未命中 | 13 条 `.format(假值)` 结果 → 文本档 | `entry is None` | 0/13 命中 |

带槽填充用的都是假值（`WO99999999` / `R99999999` / `U99999999` / `测试小区` 等），
无真实会话数据。

## 5. 负例与反空转

- **同源断言不空转**：期望值来自 yaml 运行时读取（`_yaml_lookup`），非写死字面量。
  `test_one_char_tamper_is_detected` 用内存篡改（删句末标点、全角→半角）复现「改一个字就变红」，
  不落盘；`test_mismatch_report_lists_every_offending_key` 注入一条差异验证探测逻辑本身有效。
- **断言严格于「相等」**：`test_variant_is_byte_identical_to_yaml` 逐码位比对
  （`[ord(c) for c in ...]`），同形异码（NFKC 陷阱）也会被抓住——**归一化相等 ≠ 同源**。
- **不复制比对器**：判据从 `compiler.source._SENTENCE_TERMINATORS` 读取，测试与产品判据同源。

## 6. 构建与测试命令

```sh
python3 -m unittest discover -s packs/heat_kefu/tests -t packs   # 本包 22 条
bin/vox pack check packs/heat_kefu                               # rc 0
bin/vox pack build packs/heat-kefu --out packs/heat-kefu_build/heat-kefu-1   # rc 0
```

⚠️ `bin/vox pack build` 的 `--out` **禁止落在包源目录内**（`cli.assert_not_within` 硬拦，
`docs/08 §8.5` 欠账 2）；产物落在包目录**旁边**的 `packs/heat-kefu_build/`。
仓内 `.gitignore` 已排除 `*.wav` 与 `assets/audio/`，三包既有惯例也是**只提交源文件**
（`git ls-files packs` 无任何 `.wav` / `manifest.json`），本包与之保持一致。

⚠️ 卡面验收标准 1 写的 `python3 -m unittest discover -s packs` **跑不到本包测试**
（CPython 对 namespace package 有 `if not is_namespace` 守卫，`packs/` 无 `__init__.py`；
且 `heat-kefu` 含连字符，`-t packs` 下 `_splitext` 会截断）。
修法需动 `packs/__init__.py` 或重命名包目录，二者均**不在本卡白名单**——留给验收方裁定。

