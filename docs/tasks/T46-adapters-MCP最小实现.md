# T46 · MCP 最小实现（手写 stdio JSON-RPC，零依赖，落扩展区）

> 层：`adapters/`（扩展区）
> 由来：2026-09-24 会话拍板（`docs/24 §四.1`）——MCP 是 agent 生态最大覆盖面，但 **官方 SDK 是第三方依赖**，
> 与「零第三方依赖」纪律冲突；故**手写最小子集**：只做控制面，不传音频。
> 依赖：无（不碰冻结区）；执行走 dynamic workflow。

## 一、目标

让任何 MCP 客户端（Claude / ZCode / 自研）能通过 stdio 调用前置包的**控制面**：
查命中、出 plan、验包。**音频与播放路径不进 MCP**（数据面留在进程内 adapter——`docs/24 §三`）。

## 二、产物与白名单

| 文件 | 内容 |
|---|---|
| `adapters/mcp_vox/__init__.py` | 导出面（`__all__`） |
| `adapters/mcp_vox/server.py` | stdio 主循环 + JSON-RPC 2.0 分发（**每行一个 JSON 消息**，MCP stdio 传输）；方法：`initialize` / `tools/list` / `tools/call`（`notifications/*` 与未知通知静默忽略；未知方法返回 `-32601`） |
| `adapters/mcp_vox/tools.py` | 三个工具的实现与 `inputSchema` |
| `adapters/mcp_vox/tests/test_server.py` | 真子进程 stdio 握手测试（见 §四.2） |
| `adapters/AGENTS.md` | §⑨ 授权登记一行：`mcp_vox` 允许依赖 `runtime/` `assets/` **公开 API**（与 `framework_kefu` 同款口径，只走 `__all__`） |

## 三、工具面（钉死，只这三件）

| 工具 | 入参 | 出参（`content[0].text` 为 JSON 字符串） | 失败语义 |
|---|---|---|---|
| `vox_lookup` | `pack_dir`, `text` 或 `key` | 命中：`{hit: true, key, rate_key, variant, duration_ms, model_version}`（**不含音频字节**）；未命中：`{hit: false, reason}` | 未命中是**正常结果**不是 error；包不存在/非法 → `isError: true` |
| `vox_plan` | `pack_dir`, `keys[]` | `{plan: [{key,...}], all_hit: bool, misses: []}` | 有 miss 时 `all_hit=false` 并在 `misses` 列出 |
| `vox_pack_check` | `pack_dir` | `{passed, keys, violations[]}`（走 `assets` 公开 API） | 校验不过 → `isError: true` + 违规明细 |

## 四、硬约束与验收标准

1. **零第三方依赖**（只用 stdlib：`json` `sys` 等）；每个文件 ≤150 可执行行（`adapters/AGENTS.md §②`），
   超限先拆文件、**不许登记豁免**；`python3 tools/structure_budget/check.py --no-write` rc=0；
2. **测试**（`python3 -m unittest discover -s adapters`，并入既有根）：
   - 真起子进程：`initialize` → 断言返回 `protocolVersion`/`capabilities.tools`/`serverInfo`；
   - `tools/list` → 断言三个工具名与 `inputSchema` 齐；
   - `tools/call`：命中（用 `examples/prebuilt-pack`）断言字段齐且**无音频字节**；未命中断言 `hit:false` 且 `isError` 不为 true；错误包路径断言 `isError:true`；
   - 未知方法返回 `-32601`；**一行一条消息**（多行 JSON 不得当两条消息）。
3. **协议子集边界写清**：README 式注释注明"最小子集：stdio / 三方法 / 三工具；HTTP-SSE 传输与资源（resources）与提示（prompts）不在本卡"；
4. **不碰冻结区**；不改 `core/`；不引 MCP SDK；
5. 冻结区零改动（`git diff -- compiler/ core/ rules/ assets/ runtime/ eval/ cli/` 为空）。

## 五、回滚方式

`git clean -fd adapters/mcp_vox/` + `git checkout -- adapters/AGENTS.md`。

## 六、验收记录

**✅ 验收通过（主会话，2026-09-24）**

- **§四.1 零依赖与行数**：只用 stdlib（`json`/`sys` 等）；可执行行 `__init__.py` **4** / `server.py` **56** / `tools.py` **73**（tests 不参与判定）——均 ≤150；`check.py --no-write` rc=0（主会话终态复跑）；**未新增豁免**；
- **§四.2 协议测试**：`tests/test_server.py` 真起子进程做 stdio 握手，覆盖 initialize 三字段 / tools/list 三工具与 inputSchema / 命中（`examples/prebuilt-pack`）字段齐且无音频 / 未命中 `hit:false` 非 error / 坏包 `isError:true` / 未知方法 `-32601` / 一行一条消息；`python3 -m unittest discover -s adapters` rc=0（含 mcp_vox）；
- **§四.3 子集边界**：写在模块注释（stdio + 三方法 + 三工具；HTTP-SSE / resources / prompts / sampling 不在本卡）；
- **§四.4 公开面与登记**：三工具只走 `assets`/`runtime` 公开 `__all__`、只出控制面元数据不出音频字节；`adapters/AGENTS.md §⑨` 授权登记在位；
- **终验**：CI 代理（无 ffmpeg 的 PATH）十一根全绿、范围核查无越界；冻结区零改动。
