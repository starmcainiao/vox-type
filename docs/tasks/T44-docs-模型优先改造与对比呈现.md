# T44 · 文档模型优先改造 + 对比呈现落地（去 mac 基调，文档层）

> 层：交付面（`README.md` / `docs/`）
> 由来：2026-09-24 会话拍板（`docs/24 §四.4`）——**去 mac 基调三件拆分**的**第①件：文档/示例现在改**；
> 第②件（测试与 CI 对 `say` 的耦合）登记 `docs/13 §五#27`；第③件（`cli/` 缺省适配器）随接入形态定案、需开批。
> 依赖：T43（对比数字与图）、T45（自证包与脚本路径）、T46（mcp_vox 路径）——**本卡最后执行，只引用它们的真实产物**。

## 一、目标

让读者（人 / agent）第一眼看到的路径是**模型 TTS 优先**（任意 OpenAI speech 端点、跨平台），
`say` 只作为"macOS 上没端点时的应急路径"；并把 T43 的对比以「一眼明白」的方式落进 README。

## 二、产物与白名单

| 文件 | 改什么 |
|---|---|
| `README.md` | ① **Why 节新增「一眼对比」块**：headline 数（两臂首音 + 倍数，**数字与口径逐字对齐 `labs/frontpack-value/report.json`**）+ 引 `docs/media/value-compare.svg`；② Quick Start 与全文改**模型优先**（示例命令给 `--adapter adapters.tts_omlx:OmlxTts` 版本；`say` 收进"macOS 应急"小节）；③ Documentation 表补 `docs/24` 与 `examples/selftest.sh`；④ 相对链接全部有效（含 `examples/prebuilt-pack/`、`adapters/mcp_vox/` 的引用） |
| `docs/22-五分钟跑起来.md` | §1 主线改模型优先（`say` 降为脚注/应急）；§5 表相应行同步；补「一命令自证」小节（引 `examples/selftest.sh` 与 T45 的真实输出口径） |
| `docs/20-合规自审记录.md` | 数据许可表补自证包一行：音频 = oMLX 小模型输出（自造话术，无录音/无克隆）；引擎输出条款说明一句 |
| `docs/13-未完成清单.md` | §五新增 **#27**（中）：**测试与 CI 对 macOS `say` 的耦合**（夹具/CI pre-step 走缺省 say 铸包；去 mac 基调的解耦设计待定，需与缺省适配器一起定案）——格式照既有行；统计数 16/26 → 16/27（**不改已关数**） |

## 三、禁止事项

1. **不删** README/docs 里已实测的数字（口径诚实是资产）；只改**呈现**与**路径优先级**；
2. 文档里的对比数字**只许**来自 `labs/frontpack-value/report.json`——不许复述倍数为裸数字（保留区间与口径脚注）；
3. 不碰冻结区；不改 `examples/`（T45 的产物目录，只引用）；不改 `adapters/`（T46 的产物，只引用）；
4. 不出现无法当场核验的"已兑现"类说法（`docs/20` 教训）。

## 四、验收标准

1. **首屏**：沿用 T42 检查器——前 80 行无任务号/批号、`docs/22` 在前 20 行、**相对链接全部存在**（含 T43/T45/T46 的三个新路径）；
2. **模型优先可核**：README 与 docs/22 的 Quick Start 主命令含 `--adapter adapters.tts_omlx:OmlxTts`；全文 `say` 只出现在"应急/兼容/历史"语境（验收员读判）；
3. **数字一致**：README 对比块的每个数字与 `report.json` 逐值一致（验收员对照）；
4. **docs/13 #27** 新增且统计行同步（16/27）；格式与既有行一致；
5. `check.py --no-write` rc=0；冻结区零改动。

## 五、回滚方式

`git checkout -- README.md docs/`。

## 六、验收记录

**✅ 验收通过（主会话，2026-09-24）**

- **§四.1 首屏**：README 检查器 PASS（前 80 行无任务号/批号；`docs/22` 在 **18** 行；相对链接全部存在——含 `examples/prebuilt-pack/`、`adapters/mcp_vox/`、`docs/media/value-compare.svg`）；
- **§四.2 模型优先**：Quick Start 主命令带 `--adapter adapters.tts_omlx:OmlxTts`；`say` 只在应急小节；**收尾修正**——`docs/22 §6` 贴墙速查的 run 命令原文缺 `--adapter`（照抄会 `engine_mismatch` rc=5），已补；
- **§四.3 数字一致**：对比块逐值对齐 `labs/frontpack-value/report.json`（0.715 / 2336.0 / …），倍数写 **P50/P50 ≈ 3,267×** 并附保守区间 252×–9,026× 与脚注；**收尾修正**——补「快路 `first_audio_ms` 取自 CLI JSON、不含 Python 进程启动」一句（防把 0.7 ms 读成"从零拉起进程到首响"）；
- **§四.4 其余文档**：`docs/13 §五#27` 新增（测试与 CI 对 `say` 的耦合）、统计行 27 项 / 16 已关 / 余 11；`docs/20` 补自证包数据许可行与引擎输出说明；`docs/22` 补「一命令自证」小节；
- 冻结区零改动；结构预算 `--no-write` rc=0。
