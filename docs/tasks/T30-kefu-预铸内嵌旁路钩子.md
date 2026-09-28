# T30 · kefu：预铸内嵌旁路钩子（synthesize 接缝，默认关，最小侵入）

## 背景（只写必需）

用户 2026-09-19 拍板（vox-type `docs/13 §八#14`）：**允许对 kefu-agent 最小侵入**——可选预铸旁路钩子、
默认关闭、可随时摘除，不改它既有行为。依据 docs/10 裁定 2「命中率上限由上游表示形态决定」：
不改上游，预铸永远停在 key 档演示；本钩子就是上游侧的接缝。

**接缝（策划已勘察定死）**：kefu `<kefu-agent 仓根>/organs/客服/channel-voice/blackiron_kefu_voice/cascade_local.py`
的 `LocalCascadeAdapter.synthesize(text)` 是 TTS 唯一入口（级联链路 ASR→brain→**TTS** 的最后一环）。
vox-type 侧的查询 API 已就绪（T29 交付，基线 `29e5582`）：`adapters.framework_kefu.find_hit(pack, key=…|text=…, rate_key=…)`
→ `HitResult(entry, mode, key, text, miss_reason)`。

**钩子语义（一句话）**：synthesize 开头先查预铸包——命中且合规 → 直接返回包内 wav 字节（跳过 Breeze TTS）；
否则 → 走原路（worker tts），行为与 HEAD 逐字节一致。**预铸的未命中原路是设计内行为，不属静默降级；
但一切计数必须留痕（见硬要求 5）。**

## 目标（可验收的产物，全部在 kefu 仓）

1. 新文件 `organs/客服/channel-voice/blackiron_kefu_voice/precast_hook.py`：
   - `PrecastHook` 类：构造参数 `repo_root`（vox-type 仓根）与 `pack_path`（已铸包目录）；
     **惰性激活**——`sys.path` 注入 repo_root 后 import `adapters.framework_kefu.find_hit` 与
     `assets.load_pack`（纯标准库跨仓 import，不装包，不违反壳层零第三方依赖纪律），`load_pack` 只装载一次并缓存；
   - `try_precast(text) -> Optional[tuple[bytes, str]]`：返回 `(wav 字节, "wav")` 或 None（走原路）：
     - `text` 以 `"key:"` 前缀 → key 档 `find_hit(pack, key=text[4:], rate_key="normal")`；
       否则 text 档 `find_hit(pack, text=text, rate_key="normal")`；
     - **引擎一致性自校验**（docs/10 §10.4 红线）：`pack.voice != live_voice` 或
       `pack.model_version != live_model_version` → 本进程**永久禁用**钩子（stderr 打一行含两边取值）
       并计 `engine_mismatch`，之后全走原路——live 侧取值由构造参数注入；
     - **单 part 收窄**：命中的 key 在 `pack.assets` 中存在 `part_index != 0` 的条目 → 本条不走钩子
       （多 part 拼接是 runtime.Executor 的活，本卡不做）并计 `skipped_multi_part`；
     - 命中 → 读 `entry` 的音频文件字节返回；未命中 → None + 计 `miss`（含 `miss_reason`）；
   - `stats() -> dict`：`{query, hit, miss, engine_mismatch, skipped_multi_part}`，另有
     `disabled`（未配置/未激活时的常驻 False/True 标记）；**禁用态 stats() 也必须可调**（全零 + disabled=True）；
2. `cascade_local.py` 接线（最小 diff）：
   - `__init__`：读 `KEFU_PRECAST_REPO` 与 `KEFU_PRECAST_PACK` 两个环境变量——**两者都非空才构造钩子**；
     任一缺失 → `self._precast = None`，且**只打一次** stderr 提示（含两个变量名与各自当前值，便于排查）；
   - `synthesize` 开头：`if self._precast is not None: r = self._precast.try_precast(text);  if r is not None: return r`
     ——随后原逻辑一行不动；
   - 进程退出钩子（`stop` 或 atexit，选既有生命周期里更自然的一个）打一行 stats 汇总（stderr）；
3. 新测试 `organs/客服/channel-voice/tests/test_precast_hook.py`（kefu 仓既有测试风格；
   fixture 用法见硬要求 6）。

## 允许修改的文件（白名单，kefu 仓 <kefu-agent 仓根>）

```
允许新增：organs/客服/channel-voice/blackiron_kefu_voice/precast_hook.py
允许新增：organs/客服/channel-voice/tests/test_precast_hook.py
允许修改：organs/客服/channel-voice/blackiron_kefu_voice/cascade_local.py
          （仅 __init__ 钩子构造 + synthesize 开头两行 + 退出打点；其余零改动）
禁止触碰：brain/、voice_worker.py、contract.py、channel-web/、channel-internal/、db/、docs/、
          kefu 仓根其他文件；vox-type 仓的全部文件（本卡只读它）
```

## 禁止事项

- **默认关**：不设环境变量时，kefu 既有测试必须原样全绿、synthesize 路径与 HEAD 行为一致；
- 不得改 brain、ASR、事件流、voice_worker 协议；不得引入第三方依赖（vox-type 是纯标准库，允许 import）；
- 不得在钩子失败时吞异常炸主链路：钩子内部任何异常（import 失败/包损坏/文件缺失）→ stderr 留痕 +
  本进程禁用钩子 + 走原路（**fail-open 到原路是本钩子的设计语义，但每次降级必须留痕计数 `hook_error`**）；
- 不得 `git add` / `git commit`（两仓都不做，提交归主会话）。

## 硬要求（逐条都要有可观测的验证）

1. **默认关零行为差**：不设环境变量跑 kefu 既有全部语音通道测试 → 原样全绿；
2. **激活命中**：fixture 包 + 注入假 live（voice/model_version 与包一致）→ `try_precast` 返回
   `(bytes, "wav")` 且字节 == 包内该条目音频文件内容；`stats()` 的 hit==1；
3. **激活未命中**：随意文本 → None；miss==1 且 miss_reason 为 `text_not_prebaked`；
   `key:不存在` → None 且 `key_not_prebaked`；
4. **引擎不一致**：live 注入不同 voice → 返回 None、`engine_mismatch==1`、第二次调用直接 None（已永久禁用，query 不再增长）；
5. **多 part 收窄**：构造含 `part_index=1` 条目的包 → 命中 key 但返回 None、`skipped_multi_part==1`；
6. **fixture 构造**：测试内 `sys.path` 注入 vox-type 仓根（`（仓库根）`），
   **复用 vox-type `adapters/framework_kefu/tests/` 既有包工厂铸包**到 TemporaryDirectory（禁止手搓第二套
   包构造逻辑）；vox-type 仓只读；
7. **key: 直通**：`"key:<包内key>"` 形态命中 key 档（mode 断言用 find_hit 的 HitResult.mode，钩子测 stats 即可）。

## 验收标准（逐条可判定）

1. kefu 仓既有测试原样全绿（执行方找出 kefu 语音通道的测试跑法——参照 `tests/test_cascade_local.py`
   的运行方式——并报告命令与计数）；
2. 新测试全绿，覆盖硬要求 2–7 全部六条；
3. `git -C <kefu-agent 仓根> status --porcelain -uall` 改动全部在白名单内；
   `git -C <kefu-agent 仓根> diff -- organs/客服/channel-voice/blackiron_kefu_voice/cascade_local.py`
   的 diff 行数 ≤ 30 行（最小侵入的量化）；
4. **默认关实证**：`env -u KEFU_PRECAST_REPO -u KEFU_PRECAST_PACK` 下跑既有 cascade 测试 → 全绿；
5. 报告贴：两张仓的 git status 终态、测试命令与计数、cascade_local.py 的完整 diff。

## 反空转条款

- 测试必须真实构造 vox-type 包（硬要求 6），不得用假对象冒充 find_hit 的返回（违者整卡退回）；
- 「引擎不一致永久禁用」必须测第二次调用（证明禁用是常驻的，不是每轮重判）；
- 断言必须能被打破：hit 断言须依赖字节相等（不是"非空"），multi_part 断言须依赖计数器值。

## 回滚方式

kefu 仓：`git -C <kefu-agent 仓根> checkout -- organs/客服/channel-voice/` +
`git -C <kefu-agent 仓根> clean -fd organs/客服/channel-voice/`（动手前记录 HEAD 与 status）。
vox-type 仓零改动，无回滚面。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**数据分级：公开级**——fixture 自造、无真实会话数据；kefu 仓为本地私有仓，不外发。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过（2026-09-19，第十四批）**

### 验收记录（验收方独立复核，测试全数亲跑）

- kefu 既有 **28 条全绿**（含 `env -u` 双变量默认关实证 8 条）；新测试 **12 条全绿**（六场景覆盖；合计 40 条——2026-09-21 复核修正原「既有 40 条」口径）；
  vox-type 仓零改动；kefu 改动面恰 3 文件（白名单内）；
- **裁定 4 条执行方取舍（均接受）**：
  ① diff 36 行 vs 卡面 ≤30——超出 6 行全是 docstring/注释（可执行逻辑 21 行），判据按**可执行行**口径收窄
  （卡数错实现对，接手指南纪律 12 第三次应验）；删除诚实说明来凑数字是形式主义，不要求；
  ② hook_error 进程级永久禁用（最保守侧：包不完整=状态未知=全走原路最诚实，重启进程即恢复）；
  ③ live 侧取值补 `KEFU_PRECAST_LIVE_VOICE` / `KEFU_PRECAST_LIVE_MODEL_VERSION` 两个 env var——
  **缺省空串必引擎不一致 → fail-closed**：只设两个开关变量钩子不会生效，实际联调须设 4 个
  （真实取值 = Breeze 音色名与模型版本，届时从 kefu 配置定）；
  ④ 多 part 收窄按卡文原句最严口径；
- 执行方报告 kefu 仓基线本就不干净（3 M + 49 未跟踪，非本卡改动面）——**确认**：本卡只动 channel-voice/，
  提交时只入库 3 个白名单文件。
