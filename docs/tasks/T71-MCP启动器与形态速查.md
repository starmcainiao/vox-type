# T71 · adapters/docs：MCP 启动器 + 三形态速查（零开批，全部落在扩展区）

## 背景（只写必需）

决策层四票（2026-09-28）实测发现：`adapters/mcp_vox/` 已有 515 行可用的 stdio JSON-RPC 实现，
但**全仓没有任何一处告诉用户怎么启动它**。实跑证据：

```sh
# 从仓库根跑：通
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox.server
# → {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", ...}}

# 从别的目录跑：ModuleNotFoundError: No module named 'adapters'
cd /tmp && ... python3 -m adapters.mcp_vox.server

# 包方式直接跑：不行
python3 -m adapters.mcp_vox
# → No module named adapters.mcp_vox.__main__; 'adapters.mcp_vox' is a package and cannot be directly executed
```

同时 `python3 -m adapters.mcp_vox.server` 会在 **stderr** 吐一行
`RuntimeWarning: 'adapters.mcp_vox.server' found in sys.modules after import of package 'adapters.mcp_vox'`
（因 `adapters/mcp_vox/__init__.py:7` 先 import 了 `.server`）。stdout 干净、协议不破，
但把 stderr 当错误的宿主会误判。

`bin/vox` 已经是现成的「自解析仓库根、cwd 无关」薄壳范式（`bin/vox:24` 用脚本自身位置推导 `VOX_ROOT`，
`:29` 塞进 `PYTHONPATH`）。MCP 没享受到这个待遇。`ls bin/` 实测**只有 `vox` 一个入口**。

另：MCP 现在是三条路里**唯一一条没有启动器**的形态；Skill 尚未落地（`docs/26` 定案 5，本批不做）；
「嵌进代码当库用」这条形态**零文档**（`grep -rn "Executor(" --include='*.md' . | grep -v docs/tasks`
只有 `docs/08` 的 CLI 规格与 `runtime/AGENTS.md`，没有任何面向外部使用者的 `from runtime import` 示例）。

## 目标（可验收的产物）

- 产物 1：`adapters/mcp_vox/__main__.py`（新增）—— `python3 -m adapters.mcp_vox` 可直接起 stdio 服务，
  消除 stderr 那行 `RuntimeWarning`。
- 产物 2：`adapters/mcp_vox/README.md`（新增）—— MCP 形态的上手说明书，含宿主 `mcpServers` 配置片段、
  cwd 无关的启动方式、三个工具各自出参里**有什么/没有什么**、以及「控制面不出音」的明确说明。
- 产物 3：`docs/25-怎么在你的项目里用vox-type.md`（新增，占空号）—— 三形态各一节：
  (a) 借来执行（CLI）、(b) 接到 MCP 客户端、(c) 嵌进自己代码。**每节必带可直接复制的真实命令 + 期望退出码**。
- 产物 4：`examples/embed_demo/`（新增目录）—— 一个可跑的小 demo，演示「从 vox-type 仓外 import 并执行一轮」。

## 允许修改的文件（白名单）

```
允许新增：
  adapters/mcp_vox/__main__.py
  adapters/mcp_vox/README.md
  docs/25-怎么在你的项目里用vox-type.md
  examples/embed_demo/demo.py
  examples/embed_demo/README.md
  examples/embed_demo/run.sh

允许修改：无

禁止触碰：其他一切文件（尤其不得改 README.md / 根 AGENTS.md / 任何 docs/*.md 既有篇 /
          冻结区 core|rules|compiler|assets|runtime|eval|cli / adapters/mcp_vox/{__init__,server,tools}.py）
```

## 禁止事项

- 不得新增第三方依赖。
- **不得改 `tools.py` / `server.py` / `__init__.py`**（本卡只加 `__main__.py` 与文档）。
  任何需要改这三个文件才能跑通的，说明本卡范围定错了，退回报告，不要自行扩大。
- **绝对路径红线**：新增的文本文件里**不许出现任何机器绝对路径**（判据取
  `tools/check_no_machine_paths.py` 自身的 prefix 标签表：machine-home / user-home-macos /
  user-home-linux / volume-mount / per-user-tmp / boot-tmp 等）。仓内相对路径，
  或用 `<vox-type 仓库路径>` 占位。门禁 `tools/check_no_machine_paths.py` 会扫，
  但**不许靠它兜底**——自己写的时候就要对。
- **不许写死会漂移的抄件数字**（如「本仓共 N 篇文档」「MCP 有 M 行」）。需要数字就给命令不给数字。
- 不得"顺手优化"、不得改动与本卡无关的格式。
- **不许在 demo 里复制产品逻辑**（判定、拼接、播包一律调公开 API）。

## 关键事实（已由维护者实测，执行方直接采信，不必重跑；但**签名必须自己去源码核对**）

以下四条来自 2026-09-28 本机实跑，卡面已复核：

1. 公开 API（**抄之前必须自己去 `assets/__init__.py`、`runtime/__init__.py`、`core/protocol.py` 核对签名**）：
   - `assets.load_pack(root: Path) -> AssetPack`（`assets/__init__.py:11`）
   - `core.protocol.parse_plan(raw_plan: list) -> Plan`（`core/protocol.py:183`）
   - `runtime.Executor.__init__(self, pack, adapter, *, duplex=None, allow_fallback=False,
     policy_stream=False, now=None)`（`runtime/executor.py:122`）
   - `runtime.Executor.execute(self, plan, *, plan_id, turn_id, out_path) -> ExecutionResult`（`runtime/executor.py:197`）
   - 适配器：`adapters.tts_omlx.__all__ == ['OmlxTts', 'TtsError']`
   - `ExecutionResult` 字段：`events / fallback_count / first_audio_ms / hit_count / miss_count /
     output_path / total_duration_ms / tts_calls`
2. **命名坑（必须在文档里写出来）**：结果对象的字段叫 `output_path`（`runtime/executor.py:77`），
   但 CLI 的 JSON 键叫 `out_path`（`docs/08`）。写成 `r.out_path` 会
   `AttributeError: 'ExecutionResult' object has no attribute 'out_path'. Did you mean: 'output_path'?`
3. **`out_path` 是必填关键字，语义是「写盘」**。`docs/24` §五·附 已记：内核里根本没有
   「返回音频」这个概念，只有「写到一个路径」。**文档里不许把 embed 形态说成
   "拿到字节直接播"**——它写盘，宿主再去读。这是既有设计，不是本卡要改的。
4. **无 packaging 文件**（`ls pyproject.toml setup.py setup.cfg requirements.txt` → 四项全 No such file）。
   所以外部项目**不能 `pip install`**，只能靠 `PYTHONPATH`。文档里必须明说这一点，
   并且**必须写上顶层包名撞名风险**：cwd 里若有自己的 `adapters/` `core/` `tools/` `cli/` 目录，
   会静默劫持 vox-type 的同名顶层包（cwd 优先于 PYTHONPATH），失败形态是
   `ModuleNotFoundError: No module named 'adapters.textmatch'` 这种**不指向真因**的错误。
   demo 至少要有一句防呆断言（见验收标准 5）。

## 验收标准（逐条可判定）

1. `python3 -m adapters.mcp_vox` 可起服务且** stderr 无 `RuntimeWarning` **。
   验证命令（在仓库根）：
   ```sh
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
     | python3 -m adapters.mcp_vox 2>/tmp/mcp_stderr.txt
   cat /tmp/mcp_stderr.txt   # 期望：空
   ```
   stdout 须含 `"protocolVersion"`。
2. `python3 -m adapters.mcp_vox.server` **仍然可用**（不得因本卡改动而失效）。
3. `adapters/mcp_vox/README.md` 至少含：一段可复制的 `mcpServers` 配置 JSON、
   一条**不依赖 cwd** 的启动方式说明、三个工具的出参字段清单、
   以及一句明确的「MCP 是控制面，不出音频字节；毫秒首音靠进程内 adapter」口径说明。
4. `docs/25-...md` 三节齐全，每节都有**可复制的真实命令**（不是伪代码），
   命令全部从仓内已有入口抄，不许发明。至少覆盖：
   - 借来执行：`sh bin/vox run examples/plan.json --pack examples/prebuilt-pack
     --adapter adapters.tts_omlx:OmlxTts --out <tmp>/x.wav`（说明 `--adapter` 为何必须显式给）
   - MCP：AC1 的命令 + 宿主配置
   - embed：`PYTHONPATH=<vox-type 仓库路径> python3 examples/embed_demo/demo.py`
5. `examples/embed_demo/demo.py` 满足：
   - 只用 §关键事实 1 列的公开 API，**不定义任何判定/拼接/播包逻辑**；
   - **防撞名断言**：脚本启动时断言 `import adapters` 解析到的路径**不在当前 cwd 下**
     （即自己没在 vox-type 仓根里跑），否则打印清晰提示并退出；
   - 打出 `hit` / `miss` / `tts_calls` / `first_audio_ms` 四个值；
   - 在**本机**实跑通过。
6. **负例必须验**：把 demo 放到一个含 `adapters/__init__.py` 的临时目录里跑，
   demo 应当**明确报错或明确提示**，而不是给出误导性的结果。
   验证：见卡底「自测命令」。
7. 门禁全绿（逐条跑，贴真实输出）：
   ```sh
   python3 tools/run_all_tests.py | tail -2              # failed=0
   python3 tools/structure_budget/check.py --no-write     # 全部合规（**必须带 --no-write**）
   python3 tools/check_no_machine_paths.py               # violation_count=0
   python3 tools/check_import_direction.py               # 违规=0，例外仍为基线 2 条
   ```
8. 冻结区零改动：`git status --porcelain` 的改动路径**全部**落在白名单内。

## 反空转条款

- demo **必须真的调产品 API**，不得在 demo 内复制被验逻辑。
- 文档里的每条命令**必须真的跑过**，把真实输出贴进文档（命令 + 期望值 + 期望退出码），
  格式照抄 `examples/README.md` 既有惯例。
- **不许为让文档好写而放宽任何表述**。例如 MCP 的出参确实没有 path/音频字节，
  文档就写"没有"，不许含糊成"可按需获取"。

## 回滚方式

删除本卡新增的五个文件/目录即可（未触碰任何既有文件 → 回滚 = `git clean -fd` 白名单路径）。

## 执行方式

首选 ZCode 子智能体 `vox-card-executor`；回落 `blackiron-opencode-writer`。
**含用户数据/密钥/内网地址的卡两条路都不许派**——本卡已核对，无此类内容（仅引用 key 名与路径）。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
