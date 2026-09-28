# T34 · kefu（跨仓）：预铸钩子接序列档——多段拼接与真链路实测

## 卡号 / 标题
`T34 · kefu[另一仓] + vox-type labs：precast_hook 接 find_hit_sequence（多段拼接 + key 档整段展开）`

## 背景（只写必需）

- **用户 2026-09-21 拍板**：「1、可以执行」——批准 **改 kefu 仓**（2026-09-19 已授权最小侵入）+
  **起服务跑真链路实测**（历史上起服务是单独拍板项，本次已授权；仍守「用后即关」纪律）。
- 接缝现状：`kefu-agent` 仓的 `organs/客服/channel-voice/blackiron_kefu_voice/precast_hook.py`（152 行，T30）。
  其模块红线里写着：**「多 part 收窄：命中 key 在包内存在 part_index != 0 的条目 → 本条不走钩子
  （拼接是 runtime.Executor 的活，本钩子不做）」**——本卡就是兑现这个口子。
- vox-type 侧已就绪（T33/T33b，基线 `be02cf6`）：
  - `adapters.framework_kefu.find_hit_sequence`（逐字覆盖序列命中，含 `blocked_by` 诊断留痕，`docs/10 §10.7`）；
  - `runtime.Executor`（多 unit plan → 拼接 + 静音垫 + 淡入 + 事件流，T07）；
  - `packs/heat_kefu` 已拆句铸入（40 → 69 key；`opening__1`/`__2` 等 29 条）。
- 起草本卡时已确认的接口事实（执行方不必再猜）：`Executor(pack, adapter, *, duplex=None, allow_fallback=False)`；
  adapter 契约 = 有 `synthesize` / `voice` / `model_version` 三属性；`DuplexParams.default()` = heat_kefu 的
  包级参数（`silence_pad_ms=200` / `fade_ms=5` / `slot_pad_ms=80`）。

## 目标（可验收的产物）

- 产物 1：**kefu 仓** `precast_hook.py`——`try_precast` 增两档（序列档 + key 档整段展开），
  多段走 `runtime.Executor` 拼接（**禁止两套拼接代码**）
- 产物 2：**kefu 仓** 测试（该包既有测试目录）——序列档单测（拼接正确性 / 未命中 / 留痕 / 可摘除）
- 产物 3：**vox-type 仓** `labs/kefu-seq-e2e/`（新目录）——真链路实测驱动脚本 + README + report.json

## 允许修改的文件（白名单）

```
kefu-agent 仓：
  允许修改：organs/客服/channel-voice/blackiron_kefu_voice/precast_hook.py
  允许新增：该包测试目录下的新测试文件（若既有测试文件在同目录，可追加但不得改既有断言）
  禁止：kefu 仓其他一切——尤其 brain/**、config、.env、worker、其他 channel
vox-type 仓：
  允许修改：adapters/framework_kefu/__init__.py（仅在需要补导出时；当前预计不需要）
  允许新增：labs/kefu-seq-e2e/**
  禁止：vox-type 仓其他一切（含 docs/**）
```

## 实现规格

### 1. 三档查询顺序（`try_precast`，顺序冻结）

```
① key 档（上游按 key 说话）：text 以 "key:" 开头 → 查 key = text[4:]
   1a. 包内存在该 key → 现状行为（单段；多 part 收窄红线不变）→ 返回 (bytes, "wav")
   1b. 包内没有该 key，但存在 **连续完整的** `<key>__1 … <key>__N`（N ≥ 2，全部在包内、
       part_index == 0、rate_key 匹配）→ **整段展开**：按 `__1..__N` 顺序拼 plan → 走 ③ 的拼接路径
   1c. 都不满足 → None（不猜、不凑）
② 文本档单段（现状）：find_hit(text) 命中 → 返回单段 bytes（不变）
③ 文本档序列（新）：find_hit(text) miss → find_hit_sequence(text)
   3a. entries ≥ 2 → 拼 plan 播多 unit → 读回 WAV bytes → 返回
   3b. entries ≤ 1（含 0）→ None（**单段情形已在 ② 覆盖；3a 之外的任何结果都不播**）
```

- **拼接实现（硬约束）**：plan = `[{"key": e.key, "rate": e.rate_key, "variant": e.variant} for e in entries]`
  （variant 显式传入，避免 `auto` 的稳定散列选到别的变体）→
  `runtime.Executor(pack, _NoSynthAdapter(pack), allow_fallback=False).execute(plan, plan_id=…, turn_id=…, out_path=tmp)`
  → 读回 `tmp` 字节 → 清理临时目录。
  - `_NoSynthAdapter`：实现 `synthesize`（**调用即抛 RuntimeError**，消息注明"钩子内不做合成"）
    与 `voice` / `model_version`（取 pack 的值，仅满足契约）；
  - `out_path` 落 `tempfile.mkdtemp()`，读回后删除该目录（异常路径也要清理并留痕）；
  - `allow_fallback=False`：任何 unit 未命中即抛错 → 钩子 fail-open 到原路（现状红线不变）。
- **留痕（stats 新增两个计数）**：`seq_hit`（序列档/整段展开命中）、`seq_miss`；
  文本档序列 miss 时，若 `blocked_by` 非空 → stderr 一行（诊断留痕，接线 `docs/10 §10.7`），
  格式形如 `seq_miss blocked_by=('greet@slow#0',)`（不打印音频内容）。
- **红线一律不变**：默认关（双环境变量）、引擎不一致永久禁用、任何异常 fail-open 到原路 + 留痕、
  可摘除（新增部分仍隔离在本文件 + 测试内）。

### 2. 真链路实测（vox-type 侧 `labs/kefu-seq-e2e/`）

- 起法照 `docs/09 §10.4`（brain 8092 + 语音端 8096，云端档）+ 4 个 `KEFU_PRECAST_*` 环境变量；
  **用后即关**（脚本收尾与 README 里都写明；验收方会核对无残留进程）。
- 驱动方式（两臂，都要跑）：
  - **臂 A（key 档整段展开）**：走 kefu 已有接口让 brain 输出 `key:` 形态或直接驱动钩子——
    若 brain 无法产出 `key:` 前缀，则用 `labs/kefu-bridge/` 的既有直驱方式调钩子（说明清楚是哪种）；
  - **臂 B（文本档整段）**：驱动一轮让 brain 输出**多分句整段预设话术**（候选场景：
    `repair_confirm_question`（报修确认）、`inject_refuse`（prompt 注入话术）、`user_no_unknown`（户号兜底）；
    执行方先读 kefu brain 代码确认触发条件再设计话术）；
  - **对照臂**：一轮 LLM 闲聊（必然 miss）→ 走原路。
- 每轮记录：钩子 `stats()` 计数、返回音频的 sha256 与时长、与"vox-type 侧用同一 plan 播一遍"的音频
  **逐字节比对**（同一 Executor、同一包 → 应完全相同）；未命中轮记录 `miss_reason`。
- 产物：`report.json`（含 `provenance` 风格溯源：kefu 仓 commit、包 manifest sha256、vox-type commit）
  + `README.md`（口径、复现命令、边界）。
- **若臂 B 确实触发不了**（brain 不会说整段）：如实记录"未取到"，改以臂 A + 钩子单测作为本卡证据，
  并在 README 与报告中写明这个边界；**不得**为此改 brain 或自造命中。

## 禁止事项

- 不得改 kefu brain / config / .env / worker 一行；不得为"让命中发生"而改上游行为；
- 不得在钩子内实现第二套音频拼接（必须走 `runtime.Executor`）；
- 不得把本机绝对路径 / 内网地址 / token / 真实会话写进任何产物；
- 不得改 vox-type 的 `docs/**`、`packs/**`、`runtime/**`、`assets/**`；
- 不起服务时不得留残留进程（脚本必须自己收尾）。

## 验收标准（逐条可判定；验收方逐条独立复跑）

1. **kefu 既有测试全绿**：kefu 仓该包测试 rc 0（条数与动工前一致或只增不减），既有断言未改。
2. **序列档单测**（在 kefu 侧或 labs 侧，走真包 + 真 Executor）：
   - 造/用含 ≥2 段的输入 → `try_precast` 返回 `(bytes, "wav")`，**该 WAV 时长 == 各段时长之和 +
     (段数−1) × `silence_pad_ms`**（用 `wave` 读帧数核算，允许 ±1 帧）；
   - 序列 miss（含包外句）→ `None` + `seq_miss` +1 +（若 `blocked_by` 非空）stderr 有该行；
   - `key:opening` → 整段展开命中；`key:opening__1` → 单段命中（1a 优先）；
     `key:` 一个不存在且无 `__N` 兄弟的 key → `None`（1c）。
3. **真链路实测**：臂 A **至少 1 轮**实弹（钩子返回音频与 vox-type 同 plan 产物**逐字节相同**）；
   臂 B 取到就报，取不到如实记录（见规格 §2）；
   对照臂走原路（`ttsEngine` 非 wav 或按 kefu 既有表现）；
   `report.json` 落盘、README 活引用、**无敏感内容**（路径/token 扫描 0 命中）。
4. **用后即关**：`lsof -i :8092 -i :8096` 无残留（验收方亲跑）。
5. **可摘除性**：kefu 仓改动仅限 `precast_hook.py`（+ 测试）；给出 `git diff --stat` 行数；
   删除该文件 + `git checkout` 即完全摘除（README 里写明）。
6. **vox-type 零回归**：十一根 rc 0（packs 先 build + 设 `KEFU_HEAT_YAML`；数字应与基线 `be02cf6` 一致）。
   **退出码单独确认**（`cmd >out 2>&1; echo $?`）。

## 反空转条款

- 测试必须调用产品 API（钩子走 `try_precast`，拼接走 `runtime.Executor`），不得在测试内自己拼 WAV；
- 第 2 条的时长断言必须能判红（把 `silence_pad_ms` 改成 0 或把 plan 少放一个 unit → 对应用例必须失败；
  把这条注入验证写进报告）；
- 真链路实测的"逐字节相同"要贴 sha256 实际值（不是"看起来一样"）。

## 回滚方式

- kefu 仓：`git checkout -- organs/客服/channel-voice/blackiron_kefu_voice/precast_hook.py` + 删新增测试文件；
- vox-type 仓：`git clean -fd labs/kefu-seq-e2e/`。

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`（可同时读写两仓白名单）。
数据分级 = **公开**（kefu 预设是自造 demo 文案；服务起停只用本机回环地址，地址不进产物）。
非交互环境直接落地；只回事实与自测输出，不做达标判定；不提交 git；与卡冲突的事实停下上报。

## 卡状态
- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
