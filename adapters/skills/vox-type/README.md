# vox-type 技能包

一个目录加一个 `SKILL.md`（YAML frontmatter 加 Markdown 正文），符合
[Agent Skills 规范](https://agentskills.io/specification)。
没有 vox 私有格式，没有私有 frontmatter 扩展，不依赖任何校验器。

本仓不写运行时、不写动作集合——动作集合是 MCP 的活（`vox_lookup` / `vox_plan` /
`vox_pack_check` 三个工具已在 `adapters/mcp_vox/` 交付）。这里只有说明书。

---

## 先装这里：`.agents/skills/`

**`.agents/skills/` 是唯一被多家宿主同时扫描的跨宿主落点，优先写它。**
DeepCode 的文档里对这个目录明确注释了「shared with other agent clients」，
OpenClaw 的优先级表把它排在第 2 位。放进这一个目录，同时被 OpenClaw、DeepCode 以及
约定俗成的通用 agent 客户端看见；换宿主不用重打分发件。

## 装到哪（国产宿主优先）

下表路径逐条照抄各宿主文档，「核实」一列是**本次执行方实跑** `curl -sL` 的 HTTP 状态码
（2026-09-28，用户代理 `Mozilla/5.0`）。非 200 的会标「本次未核实」——本次八条全部 200，
没有一条需要降级。

| 宿主 | 落点 | 核实 |
|---|---|---|
| TRAE（字节） | 项目级 `.trae/skills/`、全局 `~/.trae-cn/skills/` | 200 |
| 扣子 Coze（字节） | 技能商店 / 桌面端扫本地技能目录 | 200 |
| Dify | 平台 Skills 库 / `difyctl skills install` | 200 |
| Cherry Studio | 在线注册表 / GitHub 链接 / 本地 ZIP / 本地文件夹 | 200 |
| OpenClaw | `<workspace>/.agents/skills`（其优先级表排第 2） | 200 |
| DeepCode | `./.agents/skills/`（文档明确注释 shared with other agent clients） | 200 |
| 大厂参考 | Claude Code / OpenCode / Cline / Roo Code 同格式 | 200 |
| 规范站 | Agent Skills specification（frontmatter 只必填 `name` 与 `description`） | 200 |

来源地址：

- TRAE — https://docs.trae.cn/ide_skills
- 扣子 Coze — https://docs.coze.cn/guides_skill_overview
- Dify — https://docs.dify.ai/en/self-host/use-dify/build/skills
- Cherry Studio — https://docs.cherryai.com.cn/advanced-basic/extensions/skills
- OpenClaw — https://docs.openclaw.ai/tools/skills
- DeepCode — https://deepcode.vegamo.cn/en/docs/configuration/agent-skills
- OpenCode（大厂参考） — https://opencode.ai/docs/skills/
- Agent Skills 规范 — https://agentskills.io/specification

**装完不需要任何配置。** 宿主按目录扫描发现它，靠 `description` 里写的触发场景决定
要不要在对话里加载。不需要装依赖、不需要 MCP 配置、不需要重启任何服务。

## 它是什么 / 不是什么

**是**：说明书加分发单元。让宿主 agent 看得见 vox-type、看得懂它的边界与口径，
并且可以随仓一起分发。

**不是**：

- **不是运行时。** 它自己不执行任何代码，不提供任何可调用函数。
- **不携带音频资产。** `packs/` 里的预铸资产包不在这个目录里，装技能包不等于装了能播的音频。
- **不替代 MCP。** 三个工具（`vox_lookup` / `vox_plan` / `vox_pack_check`）在 `adapters/mcp_vox/`，
  该装 MCP 的场景这个技能包帮不上忙。

## 它解决什么 / 它不解决什么

**解决**：

1. **发现性**——之前得去读仓的 `README.md` 和 `docs/25` 才知道这东西存在、怎么用；
   现在宿主 agent 在目录扫描里就能看见它，靠 `description` 判断要不要展开。
2. **口径随身携带**——「未命中默认 fail-closed 中止（退出码 5）」「MCP 与 CLI 不出音」
   这两条最容易被人复述错的话，跟着技能包走，不用再去翻文档。

**不解决（如实写，别指望它）**：

- **不增加任何新能力。** 技能包本身一行可执行代码都没有，装完不产生新命令、新工具、新接口。
- **不解决「先有鸡先有蛋」。** 它解决的是发现性与口径随身携带，
  **不解决**「先要有用户去装 MCP」这个先有鸡先有蛋的问题——
  MCP 那三个工具该装还得用户自己配。这个技能包让 agent 知道「查」该问 MCP，
  但不会替用户把 MCP 装上。
- **不解决数据面。** 要听见声音仍然得走进程内 adapter，那条路这个技能包只指个方向。

## 边界（明确不做，防止下一个人又来提）

- **不自造 vox 私有 Skill 格式或私有 frontmatter 扩展。**
  frontmatter 只有规范必填的两个字段 `name` 与 `description`，一个私有字段都没有。
  加私有字段违反本仓纪律 8（宿主无关）——换宿主就该新写适配器，不该让格式长成某一家的形状。
- **不为 Trae / 扣子 / Dify 各写一份定制副本。** 它们用的就是同一套 `SKILL.md`，
  三份副本等于三处要同步的真相源。
- **不把 `packs/` 预铸资产包塞进技能分发。** 资产包有引擎一致性闸（比对清单的
  `voice` / `model_version` 与 adapter），塞进去会误导「装了就能播」。
- **不适配 FastGPT 式沙箱技能。** 那种技能跑在它自己的 Sealos / OpenSandbox VM 里，
  不是通用 Agent Skills，塞进来只会得到一个永远加载不到技能包的宿主。
- **不给 n8n 做 Skill 适配。** 已核实 n8n 没有 Skill 概念：
  `curl -sL https://docs.n8n.io/llms.txt | grep -ci skill` 本次执行实跑得 **0**
  （`grep -c` 计数为 0，退出码 1，无匹配行），它只有 MCP 节点。n8n 走 MCP 薄壳即可，
  不需要也不该有技能包这一层。

## 与仓内其它件的关系

| 件 | 分工 | 位置 |
|---|---|---|
| `SKILL.md`（本目录） | 说明书与分发单元；给宿主 agent 看的发现入口 | `adapters/skills/vox-type/` |
| CLI | 控制面，人人能用；`bin/vox` 自带仓库根解析 | `bin/vox`、`cli/` |
| MCP 薄壳 | 控制面，agent 生态即插即用；三个工具只出元数据 | `adapters/mcp_vox/` |
| 进程内 adapter | 数据面，出音；毫秒级首音只在这里兑现 | `adapters/tts_omlx/`、`adapters/asr_omlx/` |

`docs/25` 是接入形态的完整说明（三种形态各一节，带可复制命令与期望输出），
`docs/24` 是「为什么这么分」的评判依据。**`SKILL.md` 不复制这两份的任何正文**——
那是第二份真相源，会漂移。这里只写决策口径与指向。

## 维护

改 `SKILL.md` 的 `description` 要重新数一遍字符数（上限 1024），并且**必须保留触发场景**——
宿主 agent 靠这段决定要不要加载，删成一句「vox-type 语音工具」等于让技能包隐形。

`name` 必须与父目录名逐字相同（规范硬性要求），本目录叫 `vox-type`，frontmatter 就写 `vox-type`。
