# T75 · 通用 Agent 技能包：SKILL.md（国产宿主优先，不造私有格式）

> 卡状态：已派发 → 已回收 → 验收
> 依据：用户 2026-09-28 拍板「那就通用吧……先以国产的为主，再以大厂的 Cloud Codex 这些……我想压注国产的」；
> T1 决策层·业务产品票同日只读检索（38 家宿主名单 + 规范核实 + 形态判断 + 三档取舍）。
> **本卡只新增扩展区文件，零改动既有文件、零改动冻结区、零第三方依赖。**

## 背景与已定结论（不必重新论证，照做）

**「通用 Skill 格式」在 2026-09 已收敛 = Agent Skills 规范**：一个目录 + 一个 `SKILL.md`
（YAML frontmatter + Markdown 正文）+ 可选 `scripts/ references/ assets/`。
frontmatter 必填只有 `name`（1–64 字符、须与父目录同名）与 `description`（1–1024 字符）。
规范站 `https://agentskills.io/specification`（维护者已实测 HTTP 200）。

**国产宿主已采纳同一套 `SKILL.md`**（业务产品票逐个核实，URL 见下方 M3 表）——
所以「通用」与「押注国产」不冲突：**国产用的就是这一套，不是各家私有格式**。押国产 = 优先验证这几个，不是造新格式。

**形态定性（`docs/24` §三已拍板，本卡遵守）**：Skill 是**说明书 + 分发单元**，**不是运行时、不是动作集合**。
动作集合是 MCP 的活（已交 `vox_lookup` / `vox_plan` / `vox_pack_check` 三个工具）。
**所以 Skill 正文只写决策口径，不重写 MCP schema、不复制 `docs/25` 全文**——那会变成第二份真相源。

## 目标（可验收的产物）

1. `adapters/skills/vox-type/SKILL.md` —— 一个符合 Agent Skills 规范的技能包
2. `adapters/skills/vox-type/README.md` —— 装到哪（国产宿主优先的对照表）+ 它是什么/不是什么

## 允许修改的文件（白名单）

```
允许新增：adapters/skills/vox-type/SKILL.md
          adapters/skills/vox-type/README.md
允许修改：无
禁止触碰：其他一切文件（尤其 README.md、docs/24、docs/25、adapters/mcp_vox/**、任何冻结区、任何既有 .py）
```

## M1 · `SKILL.md` 硬性格式要求

- frontmatter **必须**含 `name: vox-type`（**与父目录名 `vox-type` 逐字相同**）与 `description`；
- `description` **≤1024 字符**，且**必须把触发场景写进去**（宿主 agent 靠它决定要不要加载）——
  建议覆盖这些触发词：语音 Agent、语音链路、客服、IVR、催收、话术、预铸音频、TTS 首响慢、毫秒首音、接进语音项目；
- 正文 **<5k token**（规范建议值），**中文为主**；
- **正文只写这五件事**，每件都要能从仓内既有文件核到出处：
  1. **什么时候该用 / 不该用**（出自 `README.md` 的「你什么时候需要它」）；
  2. **三条接入路径怎么选**：CLI / MCP / 进程内嵌入（出自 `docs/25`）；
  3. **命中与未命中的语义**：命中=磁盘读零 TTS 调用；未命中=**默认 fail-closed 中止（rc=5）**，
     降级到宿主 TTS 必须显式 `--allow-fallback` 且留痕（出自 `docs/08`）；
  4. **铁律一条：MCP 与 CLI 是控制面，不出音。** 工具刻意不返回 `wav_path` 与音频字节
     （出自 `adapters/mcp_vox/README.md`）。要出音走进程内 adapter。
  5. **上手三步**：clone → `sh examples/selftest.sh` → 读 `docs/25`。
- **正例与负例都要有**：至少一条「该用它」的例子与一条「不该用它（全是自由对话）」的例子。

## M2 · `README.md`（技能包自己的说明）内容要求

- **装到哪**——国产宿主优先的对照表，逐个写清路径：

  | 宿主 | 落点 | 来源 |
  |---|---|---|
  | TRAE（字节） | 项目级 `.trae/skills/`、全局 `~/.trae-cn/skills/` | https://docs.trae.cn/ide_skills |
  | 扣子 Coze（字节） | 技能商店 / 桌面端扫本地技能目录 | https://docs.coze.cn/guides_skill_overview |
  | Dify | 平台 Skills 库 / `difyctl skills install` | https://docs.dify.ai/en/self-host/use-dify/build/skills |
  | Cherry Studio | 在线注册表 / GitHub 链接 / 本地 ZIP / 本地文件夹 | https://docs.cherryai.com.cn/advanced-basic/extensions/skills |
  | OpenClaw | `<workspace>/.agents/skills`（其优先级表排第 2） | https://docs.openclaw.ai/tools/skills |
  | DeepCode | `./.agents/skills/`（明确注释「shared with other agent clients」） | https://deepcode.vegamo.cn/en/docs/configuration/agent-skills |
  | 大厂参考 | Claude Code / OpenCode / Cline / Roo Code 同格式 | https://opencode.ai/docs/skills/ |

  **必须写明 `.agents/skills/` 是唯一被多家同时扫描的跨宿主落点**——优先写它。
- **每条 URL 必须是你实跑过 200 的**（维护者已实测上表全部 200；执行方**自己再跑一遍**并贴输出）。
  **跑不通或 403 的，在表里标「本次未核实」**，不许假装读过。
- **它是什么 / 不是什么**：是说明书与分发单元；**不是**运行时、**不携带音频资产**、**不替代 MCP**。
- **明确不做**（写进「边界」一节，防止下一个人又来提）：
  - 不自造 vox 私有 Skill 格式或私有 frontmatter 扩展（违反本仓纪律 8 宿主无关）；
  - 不为 Trae/扣子/Dify 各写一份定制副本（三份副本 = 三处要同步的真相源）；
  - 不把 `packs/` 预铸资产包塞进技能分发（资产包有引擎一致性闸，误导「装了就能播」）；
  - 不适配 FastGPT 式沙箱技能（跑在它自己的 Sealos/OpenSandbox VM 里，不是通用 Agent Skills）；
  - 不给 n8n 做 Skill 适配——**已核实 n8n 无 Skill 概念**（`curl -sL https://docs.n8n.io/llms.txt | grep -ci skill` → **0**，只有 MCP 节点），n8n 走 MCP 薄壳即可。

## 禁止事项

- 不许改任何既有文件（**本卡是纯新增**）；
- 不许新增第三方依赖、不许引入校验脚本依赖（官方 `skills-ref` 校验器自述「demonstration purposes only」，**不许依赖它**）；
- 不许在 `SKILL.md` 里复制 MCP 三个工具的 schema 或 `docs/25` 全文（第二份真相源）；
- **不许在 `SKILL.md` 里写任何本机绝对路径**（`tools/check_no_machine_paths.py` 会拦）；
- 不许把「它不增加任何新能力」这件事藏起来——**反面意见要如实写进 README 的「它解决什么 / 它不解决什么」**：
  它只解决**发现性**（宿主 agent 看得见它）与**口径随身携带**，不解决「先要有用户装 MCP」的先有鸡先有蛋问题。

## 验收标准（逐条可判定）

1. **格式合规**：`name` 与父目录名逐字相同：
   ```sh
   ls adapters/skills/
   grep -n '^name:' adapters/skills/vox-type/SKILL.md   # 期望 name: vox-type
   ```
2. **`description` 长度 ≤1024**：
   ```sh
   python3 -c "
   import re,io
   s=io.open('adapters/skills/vox-type/SKILL.md',encoding='utf-8').read()
   m=re.search(r'^description:\s*(.+)$',s,re.M)
   print('description 字符数 =',len(m.group(1).strip()))"
   ```
   **期望 ≤1024**。
3. **正文规模**：`wc -c adapters/skills/vox-type/SKILL.md` 报告字节数；**不得把 `docs/25` 整篇复制进来**
   （判据：`grep -c 'first_audio_ms' SKILL.md` ≤ 3，且正文不含 `docs/25` 的章节标题串）。
4. **铁律在位**：`grep -c '不出音\|不出音频\|wav_path' adapters/skills/vox-type/SKILL.md` **≥1**。
5. **fail-closed 口径正确**：`grep -n 'rc=5\|fail-closed' adapters/skills/vox-type/SKILL.md` 命中处**必须**同时出现「默认中止/中止」语义，
   **不许**出现「未命中回走宿主 TTS」这种反义表述（这一条是 T72/T73 刚清掉的错，不许复辟）。
6. **URL 逐条实跑**：
   ```sh
   for u in https://agentskills.io/specification https://docs.trae.cn/ide_skills \
     https://docs.coze.cn/guides_skill_overview \
     https://docs.dify.ai/en/self-host/use-dify/build/skills \
     https://docs.cherryai.com.cn/advanced-basic/extensions/skills \
     https://docs.openclaw.ai/tools/skills \
     https://deepcode.vegamo.cn/en/docs/configuration/agent-skills \
     https://opencode.ai/docs/skills/ ; do
     printf '%-70s ' "$u"; curl -sL -m 20 -o /dev/null -w '%{http_code}\n' -A 'Mozilla/5.0' "$u"; done
   ```
   **贴真实输出**；非 200 的必须在 README 表里标「本次未核实」。
7. **n8n 断言复跑**：`curl -sL -m 25 https://docs.n8n.io/llms.txt | grep -ci skill` **= 0**。
8. **门禁四道全绿**（`structure_budget` **必须带 `--no-write`**）：
   ```sh
   python3 tools/check_no_machine_paths.py
   python3 tools/structure_budget/check.py --no-write
   python3 tools/check_import_direction.py
   python3 tools/run_all_tests.py
   ```
9. **白名单核查**：`git status -uall --short` 的**新增**只允许是
   `adapters/skills/vox-type/SKILL.md` 与 `adapters/skills/vox-type/README.md` 两个文件；
   **既有文件的 M（modified）状态一条都不许出现**（`docs/tasks/` 下的卡面由维护者提交，不算越界）。

## 回滚方式

`rm -rf adapters/skills/`（本卡纯新增，不触碰任何既有文件 → 回滚即删除目录）。

## 执行方式

交 ZCode 子智能体 **`vox-card-executor`**。**只回事实，不做达标判定。**
