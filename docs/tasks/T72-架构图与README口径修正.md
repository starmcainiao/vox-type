# T72 · docs/media + README：架构图追平 + 三处口径修正（零开批，全在非冻结区）

## 背景（只写必需）

`docs/media/architecture.svg` 画于 2026-09-24，是 README 首屏的架构图（`README.md:44`）。
之后仓库加了 `adapters/mcp_vox/`、`trigger/` 决策表（T28 起）、`adapters/textmatch/`、
`adapters/framework_kefu/` 真链路、`adapters/asr_omlx/`，以及 CI 五道关卡。**图落后了一代半。**

决策层三票（2026-09-28）独立指出的问题，本卡只修**已实测确认**的那些，不做发挥。

### 问题 1（最严重，传播风险最高）：fail-closed 语义画反了

SVG `:6` 副标题写「未命中 fail-closed 回宿主 TTS」，`:68-69` 方框写「未命中回走宿主 / 原 TTS（秒级）」。
**这读起来是"每次未命中都优雅回退"，与实现不符。** 实测：

```sh
printf '[{"key":"__no_such_key__"}]' > <tmp>/miss_plan.json
sh bin/vox run <tmp>/miss_plan.json --pack examples/prebuilt-pack \
  --adapter adapters.tts_omlx:OmlxTts --out <tmp>/miss.wav; echo "RC=$?"
```
```
run: 已按 fail-closed 中止（plan 单元 #1 未命中（key='__no_such_key__'，reason=key_not_prebaked）:
     allow_fallback=False → 中止整条 plan（fail-closed：不写输出文件、不播其他话术）
  "aborted": "...", "out_path": null, "tts_calls": 0
已按 fail-closed 中止（退出码 5）
RC=5
```
代码：`runtime/executor.py:119` `allow_fallback: bool = False`（默认即中止），
`cli/commands/run.py:58` 是显式 opt-in 开关。**回宿主 TTS 要主动加 `--allow-fallback` 才发生。**

同一处混淆在对外文本里重复了三次：`README.md:77`、`CITATION.cff:12-13`、
以及 GitHub 仓库 description（`0.2ms 播放 / 合规可审核 / 未命中 fail-closed`）。
本卡只改仓内两处（README、CITATION）；**GitHub 仓库 description 不在本卡范围**（无 remote 授权）。

### 问题 2：`adapters/<宿主>` 一个框会让人把真链路读成兜底分支

实有 6 个 adapter 家族：`asr_omlx framework_kefu mcp_vox textmatch tts_macsay tts_omlx`。
图把它们压成一个橙色虚线框画在 `runtime` 右侧（视觉语言就是"回退/可选"）。
但 `adapters/framework_kefu/` 是**已验证的真链路**——`README.md:206-207` 记着
实弹 4 轮、2 轮命中、**sha256 字节级相同**。图上它与兜底分支同形。

### 问题 3：`trigger/` 缺失，且图比它自己的"文字等价物"还旧

SVG 里 `grep -c trigger` = 0。而 `README.md:28` 的 ASCII 版已写
`trigger（决策表）` 独立一栏，`AGENTS.md:39` 也把 `trigger/` 列为独立扩展区。
README 声称 ASCII 是 SVG 的「文字等价物」（`README.md:46-47`），**两者已不等价**。

### 问题 4：图上写死了 `约 0.2 ms`，且无口径

SVG `:56` 写「命中即拼接读出（约 0.2 ms）」——该数出自 `docs/09`（`say` 口径 0.232 ms，n=20）。
README 首屏用另一套：`README.md:76` 写 `P50 0.715 ms, N=50, measured on one machine`
（`labs/frontpack-value/report.json`），且 `README.md:100-105` 专门用 6 行解释
**为什么首屏不写死数字**。图与 README 用两套口径讲同一个数，**图上没有标注出处**——
违反项目第 6 条纪律「数字必须有出处」。

### 问题 5：图缺「接入形态」带

图上只有 `bin/vox` 一个入口。而 README `:251` 表格里已列 MCP 形态，仓内 515 行实现可跑（AC 见 T71）。
这是**唯一一条有实现、零上手文档**的形态（T71 处理文档）；图上补一条入口带是本卡范围。

## 目标（可验收的产物）

- 产物 1：`docs/media/architecture.svg`（修改）—— 修问题 1–5。
- 产物 2：`README.md`（修改）—— 修问题 1 的 README 处 + 运营票点名的三处首屏/口径问题。
- 产物 3：`CITATION.cff`（修改）—— 修问题 1 的 CITATION 处。

## 允许修改的文件（白名单）

```
允许修改：
  docs/media/architecture.svg
  README.md
  CITATION.cff

允许新增：无
禁止触碰：其他一切文件（尤其 docs/*.md 全部、任何冻结区目录、.github/、任何 .py）
```

## 禁止事项

- 不得新增第三方依赖（本卡不改任何 .py）。
- **绝对路径红线**：改后的三个文件里不许出现任何机器绝对路径（判据取
  `tools/check_no_machine_paths.py` 自身的 prefix 标签表：machine-home / user-home-macos /
  user-home-linux / volume-mount / per-user-tmp / boot-tmp 等）。门禁 `tools/check_no_machine_paths.py` 会扫。
- **不许发明新的数字**。图上要写的每一个数字，必须在仓内找到出处（文件:行）并在图上标注口径；
  找不到出处的就删掉，**不许留一个"大概是这个量级"的数**。
- **不许给 SVG 加外链资源**（外部字体/图片）。它现在是纯文本 SVG、无外链，是本仓的既有优点，
  `README.md:46-47` 已在夸它——保持住。
- SVG 必须**保持纯文本可 diff**（不许用图形化工具导出成不可读的一坨）。
- 不得"顺手优化"、不得改动与本卡无关的格式。

## 验收标准（逐条可判定）

1. **fail-closed 语义修正**（三条全改）：
   - SVG 副标题与 `adapters` 框：改成「未命中 → 默认 fail-closed 中止（rc=5）；
     显式 `--allow-fallback` 才回慢路并留痕」。
   - `README.md:77` 英文行的 `falling back to the host's own TTS path on every miss` 措辞，
     与 `README.md:93-105` 的中文口径警告**同一口径**（默认中止，非每次回退）。
   - `CITATION.cff:12-13` 同样措辞。
2. **adapters 拆分**：SVG 里把「宿主接入（`framework_kefu`，已验证真链路）」与
   「慢路引擎（`tts_omlx` / `tts_macsay`）」分开；**只有后者**那条箭头标
   「未命中且显式允许降级时」。`framework_kefu` 旁标注证据出处（指向 `README.md:206-207` 那一段）。
3. **trigger 补上**：SVG 里有 `trigger（决策表）` 独立一框，与 `README.md:28` 的 ASCII 对齐。
4. **数字处理**：`约 0.2 ms` 改成带口径的写法，**或**直接删掉数字改为指向 `README.md` 首屏口径。
   二选一，但**必须留一个可点到的出处**（图内文字指向 README/docs 某节）。
5. **接入形态带**：SVG 底部加一行三格入口带（CLI / Python 库 / MCP stdio），
   指向 T71 产出的三份文档。
6. **README 三处修正**（运营票已定位行号，执行方须自己复核行号，行号可能漂）：
   - 首屏「第一次来」那一行（约 `README.md:19-20`）加上 `sh examples/selftest.sh`
     （现在只在 `README.md:250` 的文档表里提过一次，埋在全文第 250 行）。
   - `README.md:183` 附近的「四种环境」表述：那张表的三行变量全是 yaml 可达性 × PyYAML，
     **四种组合 ≠ 四种平台**，文字却叫「四种环境」，Linux 读者会合理外推成"跨平台没问题"。
     改成不误導的表述，并在该处明说「无 macOS `say` 时 `cli` 根会红 22 条，见 CONTRIBUTING.md」
     （该数字来自 `docs/tasks/T68-Linux贡献者可验证.md` 的实测表，执行方可复跑核对）。
   - `docs/22-五分钟跑起来.md:305` 的「`ls docs/*.md | wc -l` = **24** 篇」——**这一条在白名单外**，
     本卡**不改**，但 README 里若复述了这个数字须一并处理。**执行方注意：不要越界改 docs/22。**
7. **SVG 仍是纯文本可 diff、无外链**：`grep -c "http" docs/media/architecture.svg` 不新增外链资源。
8. 门禁全绿（逐条跑，贴真实输出）：
   ```sh
   python3 tools/check_no_machine_paths.py     # violation_count=0
   python3 tools/structure_budget/check.py --no-write   # 全部合规
   python3 tools/run_all_tests.py | tail -2    # failed=0（本卡不改 .py，应当与基线一致）
   ```
9. 冻结区零改动：`git status --porcelain` 的改动路径**只有**白名单那三个。

## 反空转条款

- SVG 的每一处改动都要能说清"改前读起来是什么、为什么是错的"，**不许为了"看起来更全"而堆元素**。
  图是**形态说明书**，不是功能清单。
- 改完的 SVG 必须自己打开看过（用 `rsvg-convert` 或浏览器渲染一次确认不破版、不重叠、文字不溢出框）。
  **不许只改 XML 就交付**。
- **不许为让表述好写而弱化纪律**。fail-closed 的真实语义是"默认中止 + 显式放行才降级"，
  不许为了行文顺畅写成"可配置降级"。

## 回滚方式

`git checkout -- docs/media/architecture.svg README.md CITATION.cff`（本卡只改这三个既有文件）。

## 执行方式

首选 ZCode 子智能体 `vox-card-executor`；回落 `blackiron-opencode-writer`。
本卡不含用户数据/密钥/内网地址。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
