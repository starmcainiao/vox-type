# T31b · packs：`heat-kefu` 包（修订：40 单句准入 + 双 exclusion 表；T31 停手裁定落地）

## 背景（只写必需）

T31 停手正确——卡面自相矛盾是**策划失误**：`docs/11 §11.6`「一句一 variant（≥2 句末标点即拒）」是
**有依据的正确设计**（预铸资产按句粒度管理），我在 T31 里写「多分句整句铸入」与它直接冲突。
**验收方裁定（2026-09-19）**：走收窄方案——
- **拆句不采用**：brain 说出的是整段文本，包存单句则文本档对不上（破同源，本卡唯一价值归零）；
  「整段→多 key 拼接匹配」是 runtime.Executor 级能力，留口子不在本批；
- **放宽内核不采用**：冻结区本批不开（用户拍板 `docs/13 §八#16`）；
- **采用：准入收窄为 40 条单句 key**（全部通过 `_validate_variant`），14 条多分句进 exclusion 表
  （不铸 → 走原路），未来若做「整段→plan 拼接匹配」再收。
- 另一处修正：T31 未归类的 3 个 yaml 标量（`pending_intent_notice` / `profile_choice_ask` /
  `profile_choice_done`）**全部带槽**（`{intent_label}` / `{total}{profiles}` / `{userNo}{community}`——
  验收方已亲验），归「带槽不铸」。执行方 T31 报告里「前两者不含槽位」有误，以本卡为准。

**新口径：必铸 40；不铸 = 带槽 13（原 10 + 新 3）+ 多分句 14 + 非话术结构键；yaml 76 top-level key
全覆盖对账（40+13+14+9 结构 = 76）。**

## 必铸 40 单句清单（yaml 值逐字，全部已过 `_validate_variant` 判据）

`options` `transfer_ready` `transfer_queued` `turn_limit_notice` `turn_limit_notice_v2` `clarify_options`
`farewell` `fallback_route` `pay_ask_user_no` `pay_verify_user_no` `pay_no_bills` `pay_due_hint`
`pay_error` `repair_confirm_question` `work_order_hint` `work_order_ask` `work_order_empty`
`work_order_error` `work_order_no_unknown` `work_order_eta_unavailable` `work_order_eta_guide`
`repair_error` `collect_limit` `transfer_suggest` `internal_chat_hint` `realtime_ask` `realtime_empty`
`stop_warm_emergency` `stop_warm_followup` `off_hours_repair` `off_hours_other` `off_hours_urgent`
`repair_ask_userNo` `repair_ask_contactPhone` `repair_ask_address` `repair_ask_problemType`
`repair_ask_desc` `repair_ask_natural_address` `repair_ask_natural_contactPhone`
`repair_ask_natural_userNo`

（注：`repair_ask_natural_problemType` / `repair_ask_natural_desc` 亦为单句——若对账发现它们过判据，
**一并铸入**，总数 40–42 以实测为准，报告里列明最终清单与逐条判据结果。）

**exclusion·多分句 14**（≥2 句末标点）：`opening` `chat_smalltalk` `clarify_work_order` `clarify_repair`
`fallback_internal` `lifeboat_fallback` `user_no_unknown` `repair_confirm_question`——等等，
`repair_confirm_question` 在上面 40 清单里是错的（它 2 个句末标点），**以执行方实测判据为准**：
执行方 T31 已实测 40/14 的精确划分，**40 清单 = T31 报告的 PASS 集 + 本卡上述 40 条求交集后以实测为准，
报告里必须附「最终必铸清单 + 每条判据 PASS 证据 + exclusion 14 条 + 带槽 13 条 + 结构键 9 个 = 76 对账表」**。
（`stop_warm_plan` / `inject_refuse` / `internal_chat_hint` / `work_order_no_unknown` /
`repair_ask_natural_address` / `repair_ask_natural_userNo` / `clarify_options` 若实测 FAIL 则落 exclusion，
不得为凑数放行。）

**exclusion·带槽 13**：`repair_progress_reminder` `repair_ask_new_value` `repair_create_ok`
`work_order_reply` `repair_dup_remind` `repair_duplicate` `confirm_silence` `transfer_summary_missing`
`realtime_reply` `steer_return_notice` `pending_intent_notice` `profile_choice_ask` `profile_choice_done`

**非话术**：`identity` `tone` `prefetch_ready_suffix`（空串）`slot_labels` `prompts.*` `model_params`
`faq_overrides` `compact_summary_prompt` + 结构 dict/int（`next_prefetch` `pay_cache_ttl_seconds`
`routing` `trace` `transfer_route` `repair_ask`（dict 本身））。

## 目标（可验收的产物，全部在 vox-type 仓）

1. `packs/heat-kefu/phrases.json`：必铸清单（40±2，以实测判据为准）逐字铸入；
   `rates` 字段结构**照抄 `packs/demo-brief/phrases.json` 的形态**（先读它再写）；
2. `packs/heat-kefu/pack.json`：`pack_id="heat-kefu"`、`pack_version="1"`、7 必填齐、
   `rates=["normal"]`、`voice="Tingting"`、`model_version="macos-say"`；
3. `packs/heat-kefu/script.json`：线性 plan 覆盖全部必铸 key（四属性「全 key 可达」）；
4. `packs/heat-kefu/admission.md`：①必铸每行 `key | A1/A2 | yaml 路径 | 备注`（`stop_warm_emergency`
   的「XX区域」字面注明）；②exclusion·多分句 14 行（原因=「一句一 variant」设计，`docs/11 §11.6`）；
   ③exclusion·带槽 13 行（原因=B2）；④yaml sha256（`a0d9194a…bfe44`，变更即钩子自动不命中）；
5. `vox pack check` rc 0 → `vox pack build` rc 0（预铸成功率 100%，say 真合成，超时给足）；
6. `packs/heat-kefu/tests/test_source_of_truth.py`（**packs 层第一根测试**）：
   - 读 yaml + `phrases.json`：必铸每条 variant 与 yaml 值**逐字 `==`**（yaml 不在本仓时 skip）；
   - `find_hit` 对每条 yaml 原文 → `entry is not None and miss_reason is None`，**100% 命中**；
   - exclusion 双向断言：多分句 14 条 + 带槽填充结果（如 `work_order_reply` 填假单号）→ `entry is None`
     （证明它们真实走原路）；
   - 负例：篡改 phrases.json 某条一个字 → 同源断言变红（测试注释说明，不需要真改文件——
     用「断言的期望值来自 yaml 动态读取」的结构性保证替代，报告里说明机制）。

## 允许修改的文件（白名单）

```
允许新增：packs/heat-kefu/**（含 build 产物）
允许修改：无
禁止触碰：其他一切文件
```

## 禁止事项

- variant 一个字不改写；exclusion 键一个都不许铸；必铸键一个不许漏；
- 不得改 `docs/11 §11.6`、`docs/14`、compiler、钩子；不得起 kefu 服务；不得 git add/commit。

## 验收标准（逐条可判定）

1. packs 层测试：`python3 -m unittest discover -s packs` → 全绿（本层第一根）；
   其余十根计数不降（adapters 188 / trigger 159 / eval 208 / …）；
2. `bin/vox pack check packs/heat-kefu` rc 0；`bin/vox pack build packs/heat-kefu` rc 0 且成功率 100%；
3. 同源断言输出（逐字 `==`，N 条全过）+ find_hit N/N 全命中输出（执行方贴，验收方抽 3 条亲跑）；
4. exclusion 双向断言输出（多分句与带槽填充结果均未命中）；
5. 对账表（必铸 N + 多分句 14 + 带槽 13 + 结构 9 = 76）写进 admission.md 或报告；
6. `git status --porcelain -uall` 新增全部在 `packs/heat-kefu/` 内。

## 反空转条款

- 同源断言逐字 `==`，不得归一化后比；find_hit 断言必须含 `miss_reason is None`；
- exclusion 断言必须真的调 `find_hit`（不是只查 phrases.json 没有该 key）；
- fixture：直接用 `assets.load_pack` 装载 build 产物（真包真查）。

## 回滚方式

`git clean -fd packs/heat-kefu`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**（用户已拍板，`docs/15 §四`）。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19，第十五批；T31 停手 → 本卡收口）**

### 验收记录（验收方独立复核）

- **对账 = 76 全覆盖**（必铸 40 + 多分句 14 + 带槽 13 + 非话术 13）；执行方按卡授权以实测判据
  **纠正卡面 7 处归类错误**（我预写清单与 exclusion 两列自相矛盾处，以实测为准）——正确的执行方式；
- `vox pack check` / `build` 均 rc 0，**预铸成功率 40/40 = 100%**；四属性 0 违规（剧本按业务流程重排后
  farewell 收尾，40 key 全可达）；
- **同源亲抽（验收方）**：8 条覆盖三种键形态（顶层 / `repair_ask.*` / `repair_ask_natural_*`）
  全部与 yaml 值逐字一致；码位级断言（防同形异码）由测试承担；
- **exclusion 双向断言**：多分句 14 + 带槽 13（`.format` 填真形假值）文本档与 key 档共 54 次查询全未命中；
- **十一测试根全绿**：core 44 / rules 21 / assets 59 / adapters 188 / compiler 168 / runtime 118 /
  eval 208 / cli 85 / tools 75 / trigger 159 / **packs 17（+2 产物不在场诚实 skip）**；
- **验收方基建伴随（白名单外，提交时做）**：`packs/__init__.py` + `packs/heat_kefu/__init__.py` +
  目录重命名 `heat-kefu`→`heat_kefu`（CPython 3.14 discover 对 namespace package 整树跳过、对连字符
  目录 `_splitext` 截断——两处都修才能让 `-s packs` 打通；pack_id 保持 `heat-kefu` 连字符不变）+
  测试/admission 内 8 处目录路径引用修正；
- **裁定 2 条**：① build 产物不入仓（与三包惯例一致、可再生、`.gitignore` 设计意图；真链路实测时现场 build）；
  ② A1/A2 归类由执行方按 docs/14 判据自判（A2 25 / A1 15，admission.md 逐行可复核）。
