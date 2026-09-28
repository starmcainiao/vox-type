# T09 · packs：示例业务包（报修登记 —— 话术 + 剧本 + 业务参数）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡是**自造的公开 demo 话术**，不含任何真实通话、用户录音、个人身份信息、内网地址或 token。**卡里已经把每一条话术的文本写死，不得自行替换成"更真实"的内容**（真实感越好越可能夹带真人信息）。可派发。

## 背景

七层已全部验收（`core / rules / assets / adapters / compiler / runtime / eval`，572 条测试），但**`packs/` 至今是空的**——`packs/AGENTS.md` 立着，"一业务一目录"的落点还没被任何真实产物走过一遍。

本卡交付**第一个示例业务包**（报修登记表单流程），它的作用是当"活文档"：证明从**包源 → 四属性校验 → 真机预铸 → 执行器播放**整条链在一个真实业务形状上跑得通，并作为 T10（`vox pack check/build/bench`）的现成输入。

**包的形态是数据，不是代码**（`packs/AGENTS.md §②`：包不得 import 任何 Python 实现代码）。所以本卡**只产出 JSON**，验证由我用现成的层 API 手跑（见验收标准）。

**必读**（不得修改）：
- `packs/AGENTS.md`（层契约；刚做过命名对账，注意 `phrases.json` 不叫 `talk.json`）
- `docs/06-预铸与执行-补定口径.md` §6.1.2（**话术源格式权威**：`pack.json` 必填字段、`phrases.json` 形状、"每个 variant 必须是一句"）
- `docs/07-剧本源格式与四属性校验.md` §7.2/§7.3（**剧本源格式与判据权威**）
- `rules/ruleset.v1.json` 与 `rules/skill.md`（R-1…R-5：禁止自造文案 / 槽位边界 / 流程边界 / 节奏边界 / 降级留痕）
- `runtime/duplex.py` 的 `DuplexParams`（`duplex` 的键名与取值范围，**照抄，不得自创**）

## 目标（产物）

```
packs/repair/pack.json        包元数据 + 业务默认参数（duplex / locale）
packs/repair/phrases.json     话术表（16 个 key，见下）
packs/repair/script.json      剧本（线性 plan，含终态声明与直播白名单）
```

**不得**产出：任何 `.py`、任何音频（`.wav`）、任何预铸产物、`golden/` 内容（真人标注不进公开分发）。

### 1. `pack.json`

```json
{"pack_id": "repair", "pack_version": "1", "protocol_version": "0.1",
 "ruleset_version": "v1", "voice": "Tingting", "model_version": "macos-say",
 "rates": ["normal", "slow", "fast"], "locale": "zh-CN",
 "duplex": {"patience_ms": 900, "rate_band": 0.15, "backchannel": "on",
            "barge_in": "allow", "silence_pad_ms": 200, "slot_pad_ms": 80, "fade_ms": 5}}
```

（`duplex` 用的是 `DuplexParams` 的字段名与合法值；`rates` 三档全给，让执行器能按档取资产。）

### 2. `phrases.json`（16 个 key，**文本照抄下表，不得改写**）

规则：每个 variant **必须是一句**（不得出现两个及以上句末标点 `。！？!?`）；`error_retry` 与 `greeting_welcome` 各给 2 个 variant（演示多变体 + `variant="auto"`）；其余 1 个。

| # | key | variants | rates |
|---|---|---|---|
| 1 | `greeting_welcome` | "您好，这里是报修服务热线。" ／ "您好，报修服务为您服务。" | 三档 |
| 2 | `ask_fault_type` | "请问您要报修的是什么设备？" | 三档 |
| 3 | `ask_fault_detail` | "请简单描述一下故障现象。" | 三档 |
| 4 | `ask_fault_urgency` | "请问这个问题影响正常使用吗？" | 三档 |
| 5 | `ask_address` | "请问设备所在的地址是哪里？" | 三档 |
| 6 | `ask_contact` | "请留下您的联系电话。" | 三档 |
| 7 | `ask_time_window` | "请问师傅什么时间上门比较方便？" | 三档 |
| 8 | `confirm_summary` | "我再和您确认一下，地址和联系方式都已经记下了。" | 三档 |
| 9 | `confirm_again` | "麻烦您再确认一下，这样填写可以吗？" | 三档 |
| 10 | `ticket_created` | "您的报修单已经创建，稍后会有师傅与您联系。" | 三档 |
| 11 | `promise_visit` | "师傅会在约定时间前与您电话联系。" | 三档 |
| 12 | `error_retry` | "抱歉，我没听清，请您再说一次。" ／ "不好意思，麻烦您再说一遍。" | 三档 |
| 13 | `asr_clarify` | "这边信号不太好，您能再说一遍吗？" | 三档 |
| 14 | `off_script_reply` | "这个问题我需要转给人工同事帮您处理。" | 三档 |
| 15 | `handoff_human` | "好的，我为您转接人工客服，请稍等。" | 三档 |
| 16 | `closing_thank_you` | "感谢您的来电，祝您生活愉快。" | 三档 |

- `rates` 缺省可用 `pack.json.rates`（三档），但**必须显式**写出来（别依赖缺省，减少歧义）；
- 共 **16 个 key × 18 个 variant**（14 个单 variant + 2 个双 variant）× 3 档 = **54 条资产**（不是 48，别算错）。

### 3. `script.json`（照 `docs/07 §7.2`；单元顺序照抄）

```json
{"script_version": 1,
 "terminal_keys": ["closing_thank_you", "handoff_human"],
 "live_whitelist": ["asr_low_confidence", "user_off_script"],
 "max_retry": 3,
 "units": [
   {"key": "greeting_welcome", "rate": "normal", "variant": "auto"},
   {"key": "ask_fault_type", "rate": "normal"},
   {"key": "asr_clarify", "rate": "normal"},
   {"action": "SAY_LIVE", "text": "不好意思，刚才没听清，请您再说一次要报修的设备。", "reason": "asr_low_confidence", "rate": "normal"},
   {"key": "error_retry", "rate": "normal"},
   {"key": "error_retry", "rate": "normal"},
   {"key": "error_retry", "rate": "normal"},
   {"key": "ask_fault_detail", "rate": "normal"},
   {"key": "ask_fault_urgency", "rate": "normal"},
   {"key": "ask_address", "rate": "normal"},
   {"key": "ask_contact", "rate": "normal"},
   {"key": "ask_time_window", "rate": "normal"},
   {"key": "confirm_summary", "rate": "normal", "slots": {"address": "北京市朝阳区示范路 1 号", "contact": "13800000000"}},
   {"key": "confirm_again", "rate": "normal"},
   {"key": "ticket_created", "rate": "normal", "slots": {"ticket_id": "R20260917001"}},
   {"key": "promise_visit", "rate": "normal"},
   {"key": "closing_thank_you", "rate": "normal"}
 ]}
```

> **更正（2026-09-17，第一轮执行后被 C1b 拦下，是卡的错）**：初版把 `handoff_human`（∈ `terminal_keys`）排在第 15 个单元、后面还接 3 个单元 → 四属性校验器报 3 条 `orphan_branch`（终态之后永远执行不到），人读也不通（刚说"转人工"又继续走流程）。根因是**一条线性 plan 只能走一条路径**（这条约束已补进 `docs/07 §7.3` 的 C1b 注）：追问耗尽转人工与继续成功路径**不能共存于同一条 plan**。更正后的排法 = 只保留**成功路径**，三个 `error_retry` 紧跟在第一个提问之后（**连着 3 次 = R-3 上限边界，校验器必须放行**）；`handoff_human` **不再出现在 plan 里**，但**仍留在 `terminal_keys` 与话术库中**（它是另一条路径的合法出口，也是"库比剧本大"的第二个示例）。

**为什么这样排**（这几处是刻意的，别"优化"掉）：
- **三个 `error_retry` 连着**：正好 3 次 = R-3 的追问上限**边界**，四属性校验器必须**放行**（第 4 次才会拦）；它们之后接的是继续追问流程（而不是"转人工"），因为一条 plan 只能走一条路径；
- **一条 `SAY_LIVE` 带白名单内的 reason**：演示 R-5 的降级留痕（机器可判）；
- **两个带槽位的单元**：槽值是示例占位（`13800000000` 这种明显是占位号，**不得**换成看起来真实的号码），演示"槽值现场合成、不入包"；
- **末单元是 `closing_thank_you`**（∈ `terminal_keys`）：演示"有出口"；plan 里**只有这一个终态**，且它在末尾 —— 这是 C1b 的要求（首个终态之后不得再有单元）；
- **`off_script_reply` 与 `handoff_human` 故意只出现在话术表、不进剧本**：演示"库比剧本大"是合法的（C3 只要求剧本引用的 key 都在库里与包里，不要求反向）。**不要**为了"都用上"把它们塞进剧本 —— 塞 `handoff_human` 会直接触发 `orphan_branch`。

## 允许修改的文件（白名单）

```
允许新增：packs/repair/pack.json, packs/repair/phrases.json, packs/repair/script.json
允许修改：无（packs/AGENTS.md 已由策划对齐，本卡不得再改）
禁止触碰：其余一切文件（含 packs/AGENTS.md、core/**、rules/**、assets/**、adapters/**、
          compiler/**、runtime/**、eval/**、docs/**、AGENTS.md、README.md）
```

## 禁止事项

- **不得写任何 Python / 任何脚本进仓**（包是数据；自测脚本写到 `/tmp`，用完即弃）。
- **不得改动卡里给定的话术文本**（逐字照抄）；不得新增/删除 key；不得改 `script.json` 的单元顺序或字段。
- 不得把 `reason` 写在非 `SAY_LIVE` 单元上；不得让任何 variant 含两个及以上句末标点。
- 不得在包里出现真实姓名、真实手机号、真实地址、公司名（只能用卡里给的 `13800000000` / "北京市朝阳区示范路 1 号" 这类明显占位）。
- 不得产出预铸产物或音频进仓；不得建 `packs/repair/golden/`。
- 不得"顺手"改任何已有文件。

## 验收标准（我会逐条核对）

1. `packs/repair/` 下**恰好 3 个 JSON**、无其他文件；`python3 -c "import json…"` 三个文件均可解析；
2. **话术源格式过 `compiler`**：`compiler.load_source("packs/repair")` 成功；`len(source.phrases) == 16`；`greeting_welcome` 与 `error_retry` 各 2 个 variant、其余各 1；每个 key 的 `rates` 均为三档；
3. **每个 variant 都是一句**（我逐条数句末标点，>1 即不达标）；
4. **剧本过 `compiler` 与四属性校验**：`load_script("packs/repair")` 成功；`check_properties(script, source, pack=None)` → `violations == ()` 且 `skipped == ("key_not_prebaked",)`（`pack=None` 时 C3c 必然跳过，这是**预期**，不是缺陷）；
5. **`duplex` 参数合法且真被校验**：`runtime.DuplexParams(**pack["duplex"])` 构造成功；负例：把 `patience_ms` 改成 `500` 后构造**必须抛 `DuplexError`**（证明这层校验真在跑，不是我念了口径）；
6. **真机预铸（我手跑）**：`compiler.prebake(source, MacSayTts(), <临时目录>)` → `clean is True`、`total == 54`、`synthesized == 54`；产物 `assets.load_pack` 成功且 `assets.validate_pack == []`；
7. **四属性校验带上真包**：`check_properties(script, source, pack)` → `violations == ()` 且 `skipped == ()`（C3c 真跑过：剧本引用的每个 key 都在包里）；
8. **端到端播两条 plan（我手跑）**：
   - **纯命中臂**：取剧本里**全是 key** 的 5 个单元（`greeting_welcome`(variant=auto) / `ask_fault_type` / `ask_fault_detail` / `ask_fault_urgency` / `closing_thank_you`）→ `Executor(pack, MacSayTts())`（默认 `allow_fallback=False`）→ `hit_count == 5`、`miss_count == 0`、**`tts_calls == 0`**（命中零调用）、输出 WAV 存在且时长 > 0；
   - **含直播单元臂**：取剧本前 6 个单元（第 4 个是那条 `SAY_LIVE`；剧本新排法下前 6 个单元 = greeting_welcome / ask_fault_type / asr_clarify / SAY_LIVE / error_retry / error_retry）→ `Executor(..., allow_fallback=True)` → `hit_count == 5`、`miss_count == 1` 且该事件 `reason == "say_live_text"`、`tts_calls == 1`（只来自那条 SAY_LIVE）；
   - 注：`confirm_summary` 带 2 个槽位，若把含槽位的单元放进 plan，则 `tts_calls` 应等于**槽位数**（槽值现场合成，不算破"命中零调用"）；
9. **无个人数据**：我人工读一遍全部话术 + grep 手机号/身份证/邮箱模式，只允许出现卡里给定的占位值；
10. `git status --porcelain -uall` 仅 3 个新文件（+ 本卡）；仓内无 `.py`/`.wav` 混入 `packs/`。

## 反空转条款（必带）

- 本卡的"测试"是**用现成层 API 跑真链路**，不是写测试文件；执行方的自测脚本必须写在 `/tmp`、**不得进仓**；
- 自测必须**真调产品 API**（`compiler.load_source` / `compiler.load_script` / `compiler.check_properties` / `compiler.prebake` / `assets.load_pack` / `runtime.DuplexParams` / `runtime.Executor`），不得在自测脚本里复制这些判据（例如自己写一遍"有没有出口"的判定）；
- 自测输出要**原样贴回**：`load_source` 的 phrases 数、`check_properties` 的 `violations`/`skipped`、`prebake` 的 `total/synthesized/clean` 三行；
- 报告里**必须点名**你把预铸产物写到了哪个 `/tmp` 路径（我复现时用它对照），以及是否在仓内留下任何非白名单产物。

## 回滚方式

```
cd （仓库根）
git rm -r --cached packs/repair && rm -rf packs/repair          # 若要整体删除
# 或只回滚剧本到某次提交：
git checkout <commit> -- packs/repair/script.json
```

（**事实更正（2026-09-17）**：三个 JSON 已随 `8a7bcd4` 入库（`git add -A` 扫入），因此 `git clean` 不再适用；回滚用 `git checkout -- <path>` 或 `git rm`。）

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`；`thoughtLevel` 必须是 `enabled`；**改过 agent 定义后需新开会话**）。

**回落**：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T09-packs-示例业务包报修登记.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地文件，不要先写计划再等确认，不要往仓库里写计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，回落路径 `opencode run`；**第一轮 12–14 分钟被 C1b 拦下——是卡的剧本排法写错**，执行方按"逐字照抄"未擅改、把冲突顶回；更正卡后**续会话 4 分 56 秒**完成）→ [x] 已回收 → [x] **验收通过**（10/10；54 条资产真机预铸 clean，端到端两臂 plan 均符合预期）
- **事实更正**：三个 JSON 已随 `8a7bcd4` 入库（`git add -A` 扫入），回滚用 `git checkout -- <path>` 而非 `git clean`。
