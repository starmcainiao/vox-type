# T31 · packs：kefu 话术同源包 `heat-kefu`（方案 A′，docs/15 三点已拍板）

## 背景（只写必需）

2026-09-19 用户三点拍板（`docs/15 §四`）：①kefu 供热预设话术铸进 vox-type 包，落 `packs/`（公开分发范围）；
②同意真链路实测（起 kefu 服务，用后即关）；③验收口径 = 离线「无槽句 100% 铸入且 find_hit 全命中」必测 +
真链路实数。**真链路段由验收方执行，执行方不碰任何服务。**

勘察结论（`docs/15 §一`）：kefu brain 流程轮**逐字**取 `供热预设.yaml` 固定句——把无槽固定句按原文铸包
（key 对齐 yaml 键），T30 钩子的文本档命中率在流程轮上应接近 100%。**variant = yaml 值逐字原样**（含标点），
这是「话术同源」的全部价值；差一个字符就白铸。

## 准入清单（策划已判定，执行方对账不得自行取舍）

**必铸 54 key**（variant = yaml 值逐字；`repair_ask` 与 `repair_ask_natural_*` 嵌套按此扁平化）：

- 顶标量 44：`opening` `chat_smalltalk` `options` `transfer_ready` `transfer_queued` `turn_limit_notice`
  `turn_limit_notice_v2` `clarify_work_order` `clarify_repair` `clarify_options` `farewell` `fallback_route`
  `fallback_internal` `lifeboat_fallback` `faq_empty` `pay_ask_user_no` `pay_verify_user_no` `pay_no_bills`
  `pay_due_hint` `pay_error` `user_no_unknown` `channel_url_note` `repair_confirm_question` `repair_edit_ask`
  `work_order_hint` `work_order_ask` `work_order_empty` `work_order_error` `work_order_no_unknown`
  `work_order_eta_unavailable` `work_order_eta_guide` `repair_error` `collect_limit` `transfer_suggest`
  `internal_chat_hint` `realtime_ask` `realtime_empty` `stop_warm_emergency` `stop_warm_plan`
  `stop_warm_followup` `off_hours_repair` `off_hours_other` `off_hours_urgent` `inject_refuse`
- `repair_ask.*` 扁平 5：`repair_ask_userNo` `repair_ask_contactPhone` `repair_ask_address`
  `repair_ask_problemType` `repair_ask_desc`
- `repair_ask_natural_*` 5：`repair_ask_natural_address` `repair_ask_natural_contactPhone`
  `repair_ask_natural_userNo` `repair_ask_natural_problemType` `repair_ask_natural_desc`

**不铸**（对账表里列出+原因）：带槽句 10（`repair_progress_reminder` `repair_ask_new_value`
`repair_create_ok` `work_order_reply` `repair_dup_remind` `repair_duplicate` `confirm_silence`
`transfer_summary_missing` `realtime_reply` `steer_return_notice`——`docs/14` B2）；非话术键
（`identity` `tone` `prefetch_ready_suffix`（空串）`slot_labels` `prompts.*` `faq_overrides` `model_params`
`compact_summary_prompt`——提示词/结构数据）。
注：`stop_warm_emergency` 的「XX区域」是 yaml 字面文本（demo 局限），**照铸**并在 admission.md 注明。

## 目标（可验收的产物，全部在 vox-type 仓）

1. `packs/heat-kefu/phrases.json`：54 key，每 key 一条 variant（yaml 值逐字），
   `rates` 结构照既有包惯例；无槽字段、无多句拼接（yaml 句本身含多个分句的——如 `opening`——
   **整句铸入**：`compiler` 的「一句一 variant」校验按 `docs/11 §11.6` 口径执行，若它对多分句报错，
   **停下来在报告里说明并停手**，不得自行拆句或放宽校验——由验收方裁定）；
2. `packs/heat-kefu/pack.json`：`pack_id="heat-kefu"`、`pack_version="1"`、7 必填齐、
   `rates=["normal"]`（钩子只查 normal 档）、`voice="Tingting"`、`model_version="macos-say"`（与 demo-brief 同）；
3. `packs/heat-kefu/script.json`：线性 plan 依准入清单顺序覆盖**全部 54 key**（四属性「全 key 可达」）；
4. `packs/heat-kefu/admission.md`：54 行申报表——`key | A1/A2 | yaml 路径 | 备注`；
   文首记录 yaml 的 sha256（指纹：yaml 变更 → 钩子自动不命中，fail-closed）；
   **不进公开分发的声明不需要**（本包无真人标注）；
5. **已铸产物**：`vox pack check` rc 0 → `vox pack build` rc 0（预铸成功率 100%，say 真合成）；
   产物随包入仓（assets 是包的组成部分）；
6. 离线全命中测试 `packs/heat-kefu/tests/test_source_of_truth.py`（packs 层测试根）：
   - 读 yaml 原文（绝对路径 `<kefu-agent 仓根>/organs/客服/brain/prompts/供热预设.yaml`）+
     `phrases.json`，断言 54 条 variant 与 yaml 值**逐字相等**（这是「同源」的机器证明；
     yaml 不在本仓时 skip 而非 fail——公开 CI 无 kefu 仓）；
   - `find_hit`（T29 API）对 54 条 yaml 原文逐条查询 → 全部 `entry is not None`（100% 命中）；
   - 负例：带槽句的 `.format()` 填充结果（如 work_order_reply 填上假单号）→ 未命中
     （证明「带槽不铸」的真实行为是走原路）；篡改 phrases.json 某条一个字 → 同源断言变红（断言可打破）。

## 允许修改的文件（白名单）

```
允许新增：packs/heat-kefu/**（phrases.json / pack.json / script.json / admission.md /
          tests/test_source_of_truth.py / 编译产物 assets 与索引文件——build 产什么收什么）
允许修改：无
禁止触碰：其他一切文件（尤其 kefu 仓、其他 packs/、docs/、各冻结区）
```

## 禁止事项

- **variant 不得有一个字的改写**（标点、全半角、语气词都算改写）——同源是本卡唯一价值；
- 不得铸清单外的键、不得漏铸清单内的键；对账发现出入 → 停手报告，不自行取舍；
- 不得改 `docs/14` 准入判据、不得改钩子（T30）、不得 `git add` / `git commit`；
- 不得起 kefu 服务（真链路段归验收方）。

## 验收标准（逐条可判定）

1. `python3 -m unittest discover -s packs`（或 packs 层既有测试跑法，执行方查明并报告）→ 全绿含新测试；
   既有九/十根不受影响（adapters 188 / trigger 159 / eval 208 / … 全层总数只增不减，执行方报告计数）；
2. `bin/vox pack check packs/heat-kefu` → rc 0；`bin/vox pack build packs/heat-kefu` → rc 0 且预铸成功率 100%；
3. 同源断言：54 条 variant == yaml 值逐字（测试内证明，执行方贴输出；验收方抽 5 条独立 diff）；
4. find_hit 54/54 全命中（执行方贴输出；验收方抽 3 条亲跑）；
5. `admission.md`：54 行齐、sha256 与 yaml 实测一致、每 key 有 A1/A2 归类；
6. `git status --porcelain -uall` 新增全部在 `packs/heat-kefu/` 内。

## 反空转条款

- 同源断言必须逐字 `==`（不得归一化后再比——归一化相等 ≠ 同源，`docs/14` 的教训在本卡的镜像）；
- find_hit 命中必须断言 `entry is not None` 且 `miss_reason is None`，不得只断言"不抛错"；
- 测试必须调产品 API（`find_hit` / `load_pack` / `compiler` 装载），不得自写比对器。

## 回滚方式

`git clean -fd packs/heat-kefu`（全新目录，无既有文件触碰）。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**数据分级：公开级**——kefu 供热预设是自造业务 demo 文案（yaml 全文已勘察：无录音/真实会话/内网地址/token；
FAQ 条目标注「演示样例」）；用户拍板放行进 packs/（`docs/15 §四`）。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
