# tools/feedback_mining · 回流闭环挖掘器（立项 M3）

> **定位**：`tools/` 扩展区，**不进任何层契约**，不被 core/rules/compiler/assets/runtime/eval/cli 依赖
> （依赖方向只指向它们：import 产品归一化与 compiler 的句末标点集合）。
> 卡号：`docs/tasks/T23-tools-回流闭环留痕到话术建议.md`；判据：`docs/14-预铸准入判据.md`。
>
> **数据分级 = 公开**：所有示例话术均为自造，不含真实会话、用户录音、个人信息或内网地址。

---

## 一、它解决什么

立项 M3 的「回流闭环（日志挖掘 → 字模建议）」此前零实现（`docs/13 §六#3` 标 ❌）。
本工具吃**本仓自己的两份留痕**，把「本该预铸却未预铸」的话术挖出来，
交给 `docs/14` 的准入申报流程。

| 输入 | 格式 | 来源 |
|---|---|---|
| 轮级留痕 JSONL | T17 | `trigger/ledger.py` 的产物（state 结构化层 + plan） |
| 事件流 JSONL | T07 | `runtime/events.py` 的产物（三态）+ 适配层挂的 `live_text` |

**注意与 T22 的分工**：`labs/multi-industry-corpus/mine_recompute.py` 是在
CrossWOZ 这种**外部语料**上挖（737 候选），判据可借鉴但那是「离线语料挖掘」；
本工具只从**本仓自己的留痕**挖，两者不要混。
`miner.py --t22 <out/mine_candidates.json>` 会把 T22 的 737 候选**过一遍本工具的形式条款**
（`cross_check_t22` 段），但那只量出 B1–B5，A1/A2 与禁入项仍需人工判（见下文边界）。

## 二、过程

```
事件流 → 取三态为 miss / fallback 的单元 → 取 live_text（实际播出的话术）
       → normalize_text 归一化 → 按归一化文本聚合
       → 形式条款机器化（docs/14 的 B1–B5 中可机检的部分）
       → 合格 → suggestions.json      不合格 → rejected.json（写明规则与原因）
```

统计口径（落 `report.json` 的 `samples`）：
`ledger_records` / `event_records` / `units_by_state{hit,miss,fallback,other}` /
`live_texts_considered` / `turn_ids_with_live_text` / `turn_ids_in_ledger` /
`turn_ids_in_both` / `distinct_normalized_candidates` /
`suggestions` / `rejected` / `rejected_by_rule` / `states_referenced` /
`ledger_coverage{candidate_turn_ids,found_in_ledger,missing_from_ledger}`。

**分母必须落报告**：只报「候选数」而不报 `units_by_state`，等于把命中率口径藏起来。
**两份输入必须能对齐**：`turn_ids_in_both` 与 `ledger_coverage.found_in_ledger` 是
D2「每层只认上一层产物」的印证点——候选的 turn 如果找不到留痕，报告里就会列出
`missing_from_ledger`，不会静默丢掉。

## 三、三条硬边界（写代码时同样适用）

1. **归一化与单句判据一律同源，不得复制**
   - 聚合归一化 = `adapters.framework_kefu.normalize.normalize_text`（import）
   - 单句判据 = `compiler.source._SENTENCE_TERMINATORS`（import）
   - 测试里 `assertIs` 钉住这两个是**同一个对象**，不是同名函数。
2. **只形式条款机器化**：`docs/14` 的 A1/A2 与禁入项 ⅰ–ⅳ 是语义/来源判断，
   脚本一律写 `"pending_human"`，**不得让脚本猜语义**（申报制，不越权）。
3. **拒收不得静默**：不合格候选进 `rejected.json` 并写明规则编号与原因
   （同 `corpus-harvest` 的 rejected_sources 纪律——丢弃不留痕 = 台账残缺）。

### B1–B5 的机器化边界

| 条款 | 可否机检 | 本工具的做法 |
|---|---|---|
| B1 一句话 | 可 | `count_terminators(原文) >= 2` 即 fail（同源 compiler） |
| B2 无槽位占位符 | 可 | 扫 `{...}` 与 `[中文短语]` 两种写法，原因里写出占位符本身 |
| B3 非空 | 可（一半） | 归一化后为空 → 不进候选；包级「无逻辑」是包断言，不在候选级 |
| B4 长度上限 | 可 | `len > 60` 即 fail（见下） |
| B5 面向流程 | **人工** | 机器只兜「无敏感字段痕迹」（`check_sensitive`），语义侧留给人 |
| B5 面向流程措辞 | **人工** | 未做，如实标注 |

**B4 的上限 60 字是本工具自定的口径**——`docs/14` 没有冻结这个数值。
改它就改 `miner.py` 的 `MAX_CANDIDATE_CHARS`（CLI 也能 `--max-chars` 覆盖）。

### 一个实测出来的语义陷阱（B1 必须判原文）

`normalize_text` 的第 ②③ 步把 `！？` 折成 ASCII，但**「。」原样保留**（NFKC 不动它）。
于是 `「工单已受理！请保持电话畅通！师傅会尽快联系您。」` 归一化后剩 2 个句末标点、
原文有 3 个；只数归一化文本就会**少算**，把跨句候选判成「一句」放进建议清单 = fail-open。
所以：**聚合键用归一化文本，B1 判据用原文**（`check_single_sentence(text, raw_text=...)`）。

## 四、确定性

- **不读真实时钟**：`first_seen` / `last_seen` 取事件的 `ts` 字段；产物的 `run_at`
  缺省取输入里最晚的 `ts`，不是 `datetime.now()`。
- **不随机**：所有输出集合排序（count 降序 + text 字典序）；`states` / `turn_ids` /
  `reasons` 全部 `sorted`。
- 因此**同输入两次跑，`suggestions.json` / `report.json` / `rejected.json` 逐字节一致**。
- `--shuffle` 是**注入开关**（固定种子 0xC0FFEE）：只用来验证「确定性断言真的能判红」，
  生产运行永远不要带。

## 五、复现命令

```bash
cd （仓库根）

# ① 造演示输入（全部自造话术，含占位符 / 超长 / 跨句三类负例）
python3 tools/feedback_mining/make_demo_inputs.py --out-dir /tmp/vox-feedback-demo

# ② 挖掘 → /tmp/vox-feedback-demo 下出 suggestions.json / rejected.json / report.json
python3 tools/feedback_mining/miner.py \
    --ledger /tmp/vox-feedback-demo/turns.jsonl \
    --events /tmp/vox-feedback-demo/events.jsonl \
    --out-dir /tmp/vox-feedback-demo

# ③ 换输入必变（确定性判红）
cp -r /tmp/vox-feedback-demo /tmp/vox-fb-a
python3 tools/feedback_mining/miner.py --ledger /tmp/vox-feedback-demo/turns.jsonl \
    --events /tmp/vox-feedback-demo/events.jsonl --out-dir /tmp/vox-feedback-demo
diff -q /tmp/vox-fb-a/suggestions.json /tmp/vox-feedback-demo/suggestions.json   # 应无输出

# ④ 注入验证（把排序打散，验证确定性断言能判红）
python3 tools/feedback_mining/miner.py --ledger /tmp/vox-feedback-demo/turns.jsonl \
    --events /tmp/vox-feedback-demo/events.jsonl --out-dir /tmp/vox-feedback-shuf --shuffle

# ⑤ 跨仓库对拍：把 T22 的 737 候选过一遍形式条款（写进 report.json 的 cross_check_t22）
python3 tools/feedback_mining/miner.py \
    --ledger /tmp/vox-feedback-demo/turns.jsonl \
    --events /tmp/vox-feedback-demo/events.jsonl \
    --out-dir /tmp/vox-feedback-demo \
    --t22 labs/multi-industry-corpus/out/mine_candidates.json

# ⑥ 测试（38 条，离线可跑，不联网）
python3 -m unittest tools/tests/test_feedback_mining.py -v
```

产物字段（`suggestions.json` 的 `suggestions[]`）：
`text`（归一化文本，聚合键）/ `raw_texts`（各变体原文）/ `count` / `states` /
`reasons` / `turn_ids` / `sample_turn_ids` / `first_seen` / `last_seen` /
`a1a2_class`（恒为 `"pending_human"`）/ `form{status,violations,checked_rules}` /
`forbidden_check{ⅰ–ⅳ}`（恒为 `"pending_human"`）/ `suggested_key`（仅建议，不进契约）。

`rejected.json` 与它同形，但 `form.status = "fail"` 且 `suggested_key = null`。

## 六、下一步人工动作（本工具的边界）

挖掘器只产出**候选**，不产出**准入结论**。每个候选要走完这条路才算入包：

1. **人工判 A1/A2**：这个话轮的「存在与否」由业务流程状态决定（A1，画出流程步骤 → key
   的一一对应），还是业务系统的一个可枚举结论（A2，列出完整枚举表）？
   写不出来就不入包——这是 `docs/14 §一` 的根，频率不是准入依据。
2. **人工判禁入项 ⅰ–ⅳ**：尤其 ⅱ（语义模糊兜底）与 ⅰ（从用户话术反推）——
   本工具挖的是「实际播出过的话术」，天然贴着 ⅰ 的边界，必须人工过一遍。
3. **写申报**：`packs/<包>/admission.md`（`docs/14 §三`：每个 key 一行——类别 +
   流程步骤或枚举来源）。
4. **机器防线**：`bin/vox pack check <包>` → `bin/vox pack build <包> --out <dir>`
   （B1–B4 与四属性由 compiler / assets 兜住；`vox pack check` 的机器化 admission 校验
   是 `docs/14 §四` 登记的待办，本工具不接线——cli/ 是冻结区）。

## 七、口径与已知限制

- `states_referenced` 只会有 `miss` / `fallback`——本工具只挖这两态，命中的话术不挖
  （给已预铸的话再铸一遍是浪费且会污染 `admission.md`）。
- `suggested_key` 是纯字面提示（按 `您好` / `转人工` / `再见` 等词面匹配），
  **不是判定**，人工写 `admission.md` 时才落定。
- `check_sensitive` 是关键词粗筛（账号 / 手机号 / 工号 …），用来兜「留痕没按分层过滤」
  的错配；它是**兜底**，不是替代 `trigger/ledger.py` 的分层过滤。
- 本目录只写 `/tmp` 或调用方指定的目录，不写仓库内任何路径。
