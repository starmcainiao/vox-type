# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号语义化（SemVer）。

> 版本号只涨不重铸：`[0.1.0]` 是 2026-09-19 首发快照，此后是**完整累积历史**，不改历史条目。
> 当前版本 **0.2.0**（2026-09-28）。

## [0.2.0] - 2026-09-28

本版本是 `[0.1.0]` 首发之后的全部累积（`git log --oneline | wc -l` = **180** 个提交，首个提交
`0eefd1c docs: 立项前设计文档`，无历史重写）。本节按 **Added / Changed / Fixed** 三段写给外部读者，
末尾的「内部批次」小节保留 T 卡号索引（给维护者与验收方检索，外部用户可跳过）。
数字一律附出处；**口径不同的数字不并列、不相减**。

### Added

- **前置包价值对比（可复跑）**：`labs/frontpack-value/` 一份 harness 同时量两臂，唯一真源
  `report.json`——快路 `vox run` 命中即播 **P50 0.715 ms**（区间 0.475–4.176 ms，N=50，每句重复 5 次
  取中位数，`tts_calls_total=0`）vs oMLX `Qwen3-TTS-12Hz-0.6B-Base-bf16` 全链路
  **P50 2336.0 ms**（区间 1052.3–6453.6 ms，N=10，逐句 1 次）。两臂同机、同句、同 16 kHz 契约；
  `first_audio_ms` 取 `vox run` 的 CLI JSON，**不含 Python 进程启动**。
  倍数只作附注：**P50/P50 ≈ 3,267×（附注，非测量值）**，保守区间约 **252×–9,026×**；
  `report.json` 里**不存任何「×N」字段**，倍数只在 `notes` 与 `README.md` 作附注。
- **零引擎自证包**：`examples/prebuilt-pack/`（10 句 heat_kefu 子集，16 kHz/单声道/16-bit，
  10 个 wav 共 784,028 字节，整目录 799,100 字节）——clone 完**不需要任何 TTS 服务、不联网**就能
  听见「命中即播」（`hit=4 miss=0 tts_calls=0`）。来源与重铸口径见同目录 `PROVENANCE.md`。
- **一命令自证**：`examples/selftest.sh`（POSIX sh，零第三方依赖）——环境自检 → 串真命令 →
  逐条 ✓/✗ → 末行 `SELFTEST: PASS|PARTIAL|FAIL`，退出码 `0 / 3 / 4`。
- **MCP 接入适配器**：`adapters/mcp_vox/`，手写 stdio JSON-RPC，零第三方依赖，落扩展区。
- **「跑全量测试」单一真源**：`conftest.py` + `tools/run_all_tests.py`——十一个测试根的 `tests`
  包名全撞，必须逐根隔离子进程跑；脚本打印分项汇总（`ran / skipped / executed / failed`）
  并逐个点名失败根，非零退出。README / CI / 本指南都调它，不再各抄一份 `for` 循环。
- **CI 非测试关卡**：CI 改调上面的脚本，并接入两道此前只在本机靠人记的门禁——
  **结构预算**（`tools/structure_budget/`）与 **`bin/vox` 五命令冒烟**
  （`docs/08` 冻结的退出码 `0/2/3/4/5` 此前在 CI 里一条都没被验过）。
- **绝对路径门禁落成可跑脚本**：`tools/check_no_machine_paths.py`（只读，输出机器可读 JSON），
  接进 CI 关卡 4；判据与前缀只存在于脚本里，文档侧一律改写为调用命令。
- **import 方向闸门**：`tools/check_import_direction.py` + 基线台账
  `tools/import_direction_baseline.txt`，机器强制「层不跨看」，接进 CI。

### Changed

- **文档模型优先**：README 首屏改为「定位 → 你什么时候需要它 → 架构图 → 演示音频 → 最短上手」，
  口径警告块下移不删；所有对比结论一律指回 `labs/frontpack-value/report.json`，不在正文另抄一套数。
  README 英文行的 `~0.2 ms` 与同屏表格口径矛盾，改为与表格同源的
  `0.475–4.176 ms (P50 0.715 ms, N=50)` 并**加交叉说明**。
- **`first_audio_ms` 口径补全**：README 明写它**不含 Python 进程启动**，并给出墙钟量级对照——
  「引擎就绪后首音」与「从零敲一条命令到首响」是两个口径，后者含进程启动与 CLI 解析，
  是**数十毫秒量级**，差约两个数量级。墙钟**没有落盘报告**，所以 README **不写死任何抄件数字**，
  改为给可复现命令让读者自己量。
- **测试数对外表述收口**：README / `docs/13` / `docs/18` 的测试数统一改为**带命令出处的分项口径**
  （作废「1,547」——仓内无任何命令能产出该数）；`docs/22` 的「18 篇 docs 一篇教程都没有」
  改为如实写现状。
- **`packs` yaml 路径多候选探测**：`test_source_of_truth.py` 的 yaml 路径由「写死一条」改为四级候选
  （`$KEFU_HEAT_YAML` → 两条仓相对路径 → `$HOME`），且**显式指定即权威**——指定了却不回退去试别的
  候选（那是静默降级，比不跑更坏）。后果：**本项目唯一的生产性证据（真链路 sha256 字节级命中）
  自此在默认环境下真跑**，此前恒 24 条 skip。
- **`packs/heat_kefu` 话术同源重对账**：上游在 T31 基线后连改 4 次 yaml，指纹门禁自上述修好路径
  探测后**开火 6 条红**（正确的红）。对账结论：**清单的段数 / key 集合 / key 顺序全部未漂移**，
  漂移只在「3 段文本」与「1 条带槽句的槽位集合」。
- **adapters 命中口径归一**：`adapters/textmatch/` 把 MCP 命中口径与规范实现对齐，
  消除「同一句话两个入口判得不一样」。**扩展区改动，冻结区未动。**
- **conformance 套件自动发现**：`adapters/tests/test_conformance.py` 从手工白名单改为自动发现，
  新增 `adapters/tests/test_conformance_discovery.py`——此前白名单已腐坏，「新增 tts-* 只需追加」
  这句写在文件头的话其实不成立。
- **M2.5 之后的口径修订**：`docs/24 §五` 的 3 条待定 + 新提 3 条，共 **6 条定案**写成文档
  （`trigger/` 产品出口 / 形态 5 是否必动冻结区 / 对外主指标 / Skill 宿主格式 / MCP 传输 /
  `cli/` 缺省适配器），**零代码改动**，汇总见 `docs/26-第三十三批定案.md`。

### Fixed

- **可用性探针只探了一半前置条件，漏掉 ffmpeg**（T78 · 推送前 CI 代理试跑撞出，
  同一轮的第三盏红灯，也是 T77 那个缺陷的下一层）：T77 加的
  `OmlxTts.probe_availability()` 只回答「远端服务在不在跑」，
  但 `synthesize` 在**发任何请求之前**就要求 `ffmpeg` 存在
  （服务出 24 kHz，缺 ffmpeg 无法降到契约 16 kHz）。
  于是「端点可达但缺 ffmpeg」这一档探针报「可用」、套件照跑，然后炸在 ffmpeg 上：
  在 PATH 无 ffmpeg 的机器上（**正是 CI runner 的常态**——workflow 没有任何安装步骤）
  `adapters` 根报 **`errors=2`**。**只覆盖一半前置条件的探针比没有探针更坏**。
  现按 `synthesize` 的检查顺序先查 ffmpeg、再查端点，两条都不满足时**两个原因都报**
  （只报第一个会让人补完 ffmpeg 再跑一遍才发现端点也不通）；
  「ffmpeg 不可用」那句文案抽成模块级常量供两处共用，**不写第二份字符串**
  （两处各写一遍、改一处忘一处就又是一次「本机绿≠CI 绿」）。
  负控实测：同一棵树、同一条件下 `FAILED (failures=3, errors=3, skipped=1)` → `OK (skipped=3)`。
  skip 消息**点名适配器与缺失的前置条件**（`omlx-tts: ffmpeg 不可用: 'ffmpeg'…`），
  缺什么、怎么补都写在里面——skip 不是静默降级。`synthesize` 的失败语义一字未改。
  新增 4 条用例，且它们**能在本机有 ffmpeg 的情况下造出「无 ffmpeg」**
  （用 `VOX_FFMPEG` 指向解析不到的名字），不依赖宿主机状态。
- **一致性套件把「类整体跳过」错当成「逐适配器跳过」**（T77 · 推送前 CI 代理试跑撞出，
  同一轮里的第二盏红灯）：`TestSynthesizeWavFormat` 带着类级
  `@unittest.skipUnless(shutil.which("say"), …)`，里面却是 `for tts in self.adapters:`
  **逐适配器**循环——「本机有 `say`」并不等于「每个适配器现在都能真合成」。
  `tts_omlx` 的可用性取决于远端服务在不在跑，与 `say` 无关：
  在**有 `say`、无 oMLX** 的机器上（即 CI runner 的常态）报 **`errors=2`**。
  现改为：适配器可选实现类方法 `probe_availability() -> (bool, str)`，
  自报不可用则该适配器 skip，**skip 消息同时点名适配器与原因**；不实现 = 永远可用，照常真跑。
  判定只许发生在 `synthesize` 调用**之前**——`synthesize` 周围没有任何 `try/except`，
  真实合成失败照常上抛。负控实测：把 `macsay` 换成产 8 kHz 坏 WAV 后
  `failures=2 errors=0 skipped=0`（`AssertionError: 8000 != 16000 : macsay: 采样率应为 16000 Hz`），
  **真失败没有被 skip 吃掉**。新增 8 条用例，且它们 import 的是套件里那处真实判定。
  同批修掉 `docs/23`（新增 TTS 引擎指南）两处已失效的指引：T62 起注册表是自动发现的，
  原文仍教人改一个**已不存在的字面量**；断言表 8 行行号与两处实测条数（12/302 → 16/369）均已重标。
- **绝对路径门禁在「根卷 checkout」上退化成裸 `/`**（T76 · 推送前 CI 代理试跑撞出）：
  `build_prefixes` 里 `home` 与 `tmp` **都有** `!= "/"` 守卫，唯独 `mount` 三样没有；
  而 `_mount_ancestor` 在根卷 checkout 上返回的正是 `Path("/")`，
  `str("/").rstrip("/") + "/"` 恒等于 `"/"` —— `"/"` 作前缀会命中每一行里的每一个路径分隔符。
  实测在根卷条件下扫描本仓：**`violation_count=12838`、422 个文件全红**，
  而修复后同条件 `violation_count=0`。
  **本机绿是侥幸**（本仓恰在外接卷上，退化不出来）；GitHub runner 的 checkout 在根卷上。
  已公开快照的 workflow 尚未包含关卡 4a，**这次推送是它的第一次执行**——
  不修就会把 CI 直接顶红。现补 `mount` 与 `home` 两处守卫 + 一道兜底
  （`"/"` 键不得进入 `prefixes`），并新增 9 条回归用例
  （用 mock 把三条退化路径各自造出来，**修复前必红、修复后必绿**）。
- **死端点被当成瞬时故障重试**：端点连不上时适配器把「无法连接」判为可重试
  （`MAX_RETRIES=5` + `RETRY_DELAYS=(2,5,10,30)` = 单条 67 秒静默等待），
  `packs/demo-brief`（48 单元）实测 ≈ **54 分钟零输出**，终端像死机。
  现改为**确定性故障首抛不重试**，并在预铸过程输出进度。
- **`packs` 由 6 红修回全绿**，并补**夹具槽位完整性守卫**——此前上游一加槽位，夹具 `.format()`
  抛 `KeyError`，**断言根本没执行**（门禁不是「说不合格」而是「自己崩了」），现改为双向指名失败。
- **绝对路径判据全仓归零**（CI 关卡 4 交付即为绿）：此前判据自指（正则写在被扫文档里，恒非零，
  判据无法自动化），落地脚本后报 21 条违规，本版本全清到 0。
- **门禁被一个 NUL 字节静默绕过**：`check_no_machine_paths.py` 的 `scan_file` 遇 `\x00` 整文件跳过
  且**不影响 `passed` / 退出码**——造 `_INJECT_bin.txt` 首字节 `\x00` 即可让注入路径漏检
  （`passed=True violation_count=0`）。现改为响亮报错。
- **对外状态陈述纠误**：①「干净单提交快照」改为「首发 squash 快照、此后完整累积提交、无历史重写」；
  ②「在途节」曾把三张早已交付验收的卡长期标为「执行中」，与事实不符（T55 更正）；
  ③ `docs/20` 三个包「未补录」更正为已补录并附核验命令与实跑输出
  （**只更正「文件存在」这一事实，不对申报内容质量下结论**），「机器化 checker 未落」这条真缺口保留；
  ④ 本仓已于 2026-09-24 推送到一个**公开** GitHub 仓、且内部仓话术原文在其公开树中这一事实
  改按「已公开」重判并三处登记（`docs/13` / `docs/18` / `docs/20`）；`docs/18` 的「敏感数据清理 ✅」
  降级为「⚠️ 不通过」。**三条互斥处置路径全部标「未决」，等拍板人定，本仓不代选。**
- **CHANGELOG 在途节清理**：把早已交付的卡从「执行中」移入「已交付」，删掉不实陈述。

### 内部批次（T 卡号索引，外部用户可跳过）

> 下面每条是维护者内部的派发与验收记录，**数字与结论以上方三段为准**；这里只保留索引以便追溯。
> 完整卡面见 [`docs/tasks/`](docs/tasks/)。

- **T43** · `labs/frontpack-value/` 前置包价值对比（可复跑 harness + 对比图）
- **T44** · 文档模型优先改造 + 对比呈现（README 首屏、去「mac 基调」）
- **T45** · 自证材料（`examples/prebuilt-pack/` + `examples/selftest.sh`）
- **T46** · MCP 最小实现（`adapters/mcp_vox/`）
- **T47** · adapters 命中口径归一（`adapters/textmatch/`）
- **T48** · 仓根测试基建（`conftest.py` + `tools/run_all_tests.py`）
- **T49** · CI 非测试关卡 + 退出单一真源（`runs-on: macos-latest` 有意不动）
- **T50** · 对外表述收口（纯文档，测试数统一为带命令出处的分项口径）
- **T51** · `packs` yaml 多候选路径探测 + 热路径缓存
- **T52** · 拍板事项落档（`docs/26`，6 条定案，零代码）
- **T53** · `packs/heat_kefu` 话术同源重对账
- **T54** · 测试分项口径随 T51/T53 刷新（数字层与解释层同时改）
- **T55** · T53 余留三事 + CHANGELOG 在途节清理
- **T56** · 清本批引入的机器绝对路径 + 回滚被推翻的已关台账
- **T57** · 记录「已公开」事实 + 改写对外结论
- **T58** · 三条判据落成可跑脚本 + CI 关卡 4
- **T59** · 绝对路径判据全仓归零
- **T60** · 门禁被 NUL 字节静默绕过修复 + 速览表数字打架
- **T61** · 死端点首抛不重试 + 预铸进度可见（扩展区）
- **T62** · conformance 白名单改自动发现（扩展区）
- **T63** · import 方向闸门 + 机器路径前缀补全（新增只读工具 + CI）
- **T64** · 开源运营件补齐（PR 模板 / issue 模板配置 / README 首屏 / 本 CHANGELOG /
  `LICENSE` 权利人 / `CITATION.cff`——**零代码、零冻结区**）
- **T66–T68** · 三张**待开批**卡（引擎身份取自实际适配器 / 适配器契约提成可 import 的声明 /
  Linux·Windows 贡献者可验证）——均属冻结区，**尚未执行**
- **T69** · oMLX 端点可靠性基线（先取证，不先修）
- **T70** · yaml 缺失时的 skip 契约没覆盖第二个前置条件
- **T71** · MCP 启动器 + 三形态速查（扩展区）
- **T72** · 架构图追平 + 三处口径修正（非冻结区）
- **T73** · 修订卡：清掉 T71/T72 独立验收指出的 8 项必改
- **T74** · 可读性整改：让新人 30 秒看懂、5 分钟跑通
- **T75** · 通用 Agent 技能包 `SKILL.md`（国产宿主优先，不造私有格式）
- **T76** · 绝对路径门禁在「根卷仓」上的前缀退化（`volume-mount` 派生出裸 `/`）
- **T77** · 一致性套件按「适配器自身可用性」跳过，而非按类整体跳过
- **T78** · 可用性探针补齐 ffmpeg 前置条件（T77 那个缺陷的下一层）

### 口径提醒（对外引用前必读）

- 本仓有**两组首音数字**，口径不同：**不可混读、不可相减**。
  ① `labs/frontpack-value/report.json`：同机同句同 16 kHz 契约，快路 N=50 / 慢路 N=10，
     `first_audio_ms` 取自 `vox run` 的 CLI JSON，**不含 Python 进程启动**（报告原文：
     「不含 Python 进程启动那几十毫秒」）。
  ② `docs/09` 链路级对拍：预铸命中 P50 0.232 ms（n=20）vs 链路实时合成 586.7 ms（`say` 口径，n=36）。
  **零墙钟量级落盘报告**：`labs/frontpack-value/` 只量引擎就绪后的首音，「从零敲命令到首响」
  （数十毫秒量级）没有 harness、没有 `report.json`——想引用就得自己量并标机器与命令。
- **测试数没有永久值**：一切以 `python3 tools/run_all_tests.py` 的末行实跑输出为准；
  引用文档里的抄件等于引用一个会过期的数字。**2026-09-28 本机实跑：
  `ran=1649 skipped=0 executed=1649 failed=1 failures=1 errors=0 roots=11`**（那 1 条是 `packs` 同源指纹断言发现仓外 kefu yaml 变更、门禁按设计报警）——
  T50 定的 26 条 skip 基线已被 T51/T53 作废，`ran=1601`（T55 新增夹具槽位完整性守卫**之前**）
  与 `ran=1602`（T61–T63 之前）同样已作废。**`skipped=0` 是本机状态、不是契约**。
- **`cli` 根是平台相关的**：它走 macOS 自带的 `say`（缺省适配器 `adapters.tts_macsay:MacSayTts`）。
  缺 `say` 的环境（典型如 Linux CI）下 `cli` 根会红——T64 卡实测模拟无 `say` 环境时
  `cli` 根 `ran 89→26、failed 22`，其余十个根不受影响。**这是已知平台限制，不是回归。**
- **T51/T53 之后，剩下的 skip 条件全在 `adapters`**（`@unittest.skipUnless` 门 + 运行时探测）：
  需 `ffmpeg`、需 macOS `say` 且 tmpdir 与仓库跨设备、需本机 oMLX `:10099` 在跑、
  需仓内已有 heat-kefu 预铸产物。**`packs` 那 24 条 yaml 同源断言此前因仓外 kefu 仓 yaml 路径写死
  而按设计 skip，T51 改为多候选路径探测后本机命中、现在真跑**（T53 把 `packs` 由 6 红修回全绿）。
  **换台机器（典型如 Linux CI）这些依赖不在，`skipped` 会重新大于 0、`executed` 随之变小**；
  skip 不是失败，也不是「没跑」。

## [0.1.0] - 2026-09-19 · 首次开源

### 机制（十一个测试根 1,147 条全绿）
- core：plan 协议、封闭原语集、参数三档合并
- rules：五类话术规则（R-1…R-5）与技能指令层
- compiler：预铸流水线、四属性校验器（无死锁/全 key 可达/无未审核文本/有界）、差量重铸
- assets：包格式（key→音频+文本指纹+变体池+语速档+TTL 位）
- runtime：线性执行器（短路判定/槽位现场合成/事件流三态白名单）
- adapters：TTS（macos say）/ ASR（oMLX）/ 宿主桥（kefu 预铸旁路钩子，默认关）
- trigger：前置包 `state → plan` 决策表 + 轮级留痕（JSONL，敏感分层）
- eval：离线对拍（reference gate）、回读 CER harness（不美化三防线）
- cli：`vox pack check|build` / `run` / `bench` / `verify`（退出码冻结 0/2/3/4/5）
- tools：语料自给管道（fetch / derive / baseline，许可 fail-closed）

### 实测
- 真链路集成（kefu）：钩子命中返回音频与包内资产 sha256 字节级一致；未命中走原路留痕
- 离线对拍：预铸命中首响 P50 0.232 ms vs 实时合成 586.7 ms（≈2530×）
- 回读 CER 真机闭环 0.0；方言域 ASR 边界（Wu-Bench 吴语 n=100，CER mean 0.269）
- 准入边界实测：真实客服语料流程决定性话轮 23%；语义匹配路线判否（top-1 11.8% vs 门槛 98.7%）

### 设计裁定
- key 只能由业务流程产出（准入判据 `docs/14`：A1 流程决定性话轮 / A2 业务可枚举播报）
- 逐字相等是唯一命中判定（归一化四步冻结）；未命中 fail-closed 走原路留痕
