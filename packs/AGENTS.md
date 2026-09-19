# packs/ · 业务包（扩展区，「万物皆插件」的落点）

## ① 职责 / 不负责什么

**职责**：**一个业务一个目录**，装该业务的全部"该说什么"与"怎么说"。
- `phrases.json`：话术表（key + 文本 + 变体 + 语速档）——**源格式权威 = `docs/06 §6.1.2`**，由 `compiler.load_source` 装载
- `script.json`：剧本（线性 plan + 终态声明 + 直播白名单）——**源格式权威 = `docs/07 §7.2`**，由 `compiler.load_script` 装载、`compiler.check_properties` 判定
- `pack.json`：包元数据与业务默认参数（`pack_id` / `pack_version` / `protocol_version` / `ruleset_version` / `voice` / `model_version` / `rates` 为 **`compiler` 必填**；`locale` 与 `duplex`（双工参数默认值）为业务侧字段）
- `golden/`：该业务的真人标注与验收用例（**不进公开分发**）
- `rules.ext.json`（可选）：业务自定义规则扩展位

> **命名对账（2026-09-17，策划）**：本文件早期草稿写的是 `talk.json` / `version` / `rules_version`，与已实现并验收的 `docs/06 §6.1.2`（`phrases.json` / `pack_version` / `ruleset_version`）不一致。**以 `docs/06` 为准**，本文件已改正——包目录里不存在 `talk.json`。
> **已知口子（交接给 T10）**：`compiler.load_source` 目前只校验**必填字段是否齐**，对**未知字段是静默忽略**的（实测：pack.json 里塞 `typo_field` 不报错）。这意味着 `duplex` 写错一个字母会被无声丢弃 → 业务参数静默不生效（顶在「禁止静默降级」红线上）。落点：`vox pack check`（T10）接通"源格式未知字段拒绝"，不要各层各写一份白名单。

**不负责**：**不改内核、不写代码逻辑**（业务侧的唯一动作 = 写包 + 跑 `vox pack check`）。

## ② 输入 / 输出契约

- 输入：`rules/` 的规则集 + `adapters/` 可用引擎清单。
- 输出：可被 `compiler/` 编译的包源（编译产物进 `assets/`）。
- 包必须声明：`pack_id` / `pack_version` / `protocol_version` / `ruleset_version` / `voice` / `model_version` / `rates`（**这 7 项是 `compiler.load_source` 的必填**），外加业务侧字段 `locale` 与 `duplex`（双工默认参数，键名与取值范围照 `runtime.DuplexParams`：`patience_ms ∈ {400,900,1800}` / `rate_band` / `backchannel ∈ {on,off}` / `barge_in ∈ {allow,confirm}` / `silence_pad_ms ∈ [120,300]` / `slot_pad_ms ∈ [50,150]` / `fade_ms ≥ 0`）。

## ③ 验收条件（业务上线的门槛）

1. `vox pack check` 通过：五类规则（R-1…R-5）全部合规；
2. `vox pack build` 成功且**预铸成功率 100%**（失败即阻断，不允许带病上线）；
3. 剧本四属性通过（无死锁/全 key 可达且已预铸/无未审核文本/有界）；
4. `vox bench` 出报告：**命中率过 §2.1 门槛**（按轮数 n 与目标顺畅率解出）、延迟 P99 与降级率在阈值内；
5. 真人验收：`golden/` 用例经真人对话确认（漏判/误判均可判）；
6. **内容准入过 `docs/14-预铸准入判据.md`**（2026-09-19 拍板：只准流程决定话轮）——每个 key 属 A1（流程决定性话轮）
   或 A2（业务可枚举播报），禁入项 ⅰ–ⅳ 零命中；证明材料随包存档 `admission.md`（key → 类别 + 流程步骤/枚举来源）。
   既有三包回溯适用不阻塞，随下次触碰补录（`docs/14 §五`）。

## ④ 本层数据收集

包内统计（话术条数/剧本节点数/状态数）+ 该业务运行期事件（由 `runtime/` 记录，按 pack_id 归集）。

## ⑤ 依赖边界

- 允许：引用 `rules/` 的规则集版本、`core/` 的原语、`adapters/` 的引擎名。
- 禁止：import 任何 Python 实现代码（包是**数据**，不是代码）。

## ⑥ 变更纪律

包升级走 Git 式流程：话术 diff → check → build → bench → **灰度** → 放量；回滚 = 切回旧包版本。
**灰度判据必须量化**（命中率/降级率/P99），且最小灰度单位可以细到单条话术。

## ⑦ 冻结状态

**[扩] 扩展区**。新增业务 = 新增目录，零内核改动——这是"所有业务融合、基础版本不动"的实际含义。
