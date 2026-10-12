# T88 · adapters：MCP `vox_pack_check` 被指向包源目录时要指路

## 背景（只写必需）

实测（2026-10-12，见 `T84`）：MCP 工具 `vox_pack_check` 只认**铸出来的资产包**（要有 `manifest.json`），
而用户手里最自然的路径是指向**包源目录** `packs/<业务>/`（有 `phrases.json`、没有 `manifest.json`）。
实测把它指向包源：

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call",
 "params":{"name":"vox_pack_check","arguments":{"pack_dir":"packs/heat_kefu"}}}
→ {"content":[{"type":"text","text":"{\"passed\": false, \"keys\": [], \"violations\": [\"缺少 manifest.json\"]}"}],"isError":true}
```

报错**准确但不指路**：没告诉用户"这是包源目录，先 `pack build`"。
而 CLI 的 `vox pack check packs/heat_kefu` 是**能**吃包源目录的（rc=0）——两条入口语义不同，用户必然踩。

**为什么不让 MCP 也支持包源**：`adapters/AGENTS.md §⑤/§⑨` 禁止 `adapters` import `compiler`
（源格式校验在 `compiler`），所以本卡**只加提示，不扩语义**。

## 目标（可验收的产物）

- 产物 1：`adapters/mcp_vox/tools.py`（`_pack_check` 增加"包源目录"识别与提示）
- 产物 2：`adapters/mcp_vox/tests/` 下新增/修改的测试（正例 + 负例）
- 产物 3：`adapters/mcp_vox/README.md`（补一行说明三条工具的 `pack_dir` 只认资产包）

## 允许修改的文件（白名单）

```
允许新增：adapters/mcp_vox/tests/test_pack_check_hint.py（若 tests 目录已有其它测试文件，也可改它）
允许修改：adapters/mcp_vox/tools.py, adapters/mcp_vox/README.md
禁止触碰：其他一切文件（尤其不得改 assets/、compiler/、cli/、runtime/、AGENTS.md）
```

## 具体改法（**按此实现，不要自由发挥**）

在 `_pack_check()` 里，当**校验不通过**、且 `pack_dir` 下**存在 `phrases.json` 但不存在 `manifest.json`** 时，
在 `violations` 列表**末尾追加一条**提示（不替换原有违规项、不改 `passed` 语义）：

```
这是包源目录（有 phrases.json、没有 manifest.json）；vox_pack_check 只认铸出来的资产包。
请先跑 `sh bin/vox pack build <包源目录> --out <输出目录>`，再把 <输出目录> 传进来。
```

约束：
- `violations` 可能是 list 或 tuple —— 追加时**先转成 list**，不要就地 `append` 到 tuple；
- 提示文案必须**逐字包含** `pack build` 与 `manifest.json` 两个字符串（验收按此判定）；
- **不改** `isError` 语义（原来为真仍为真），**不改**其他两个工具（`vox_lookup` / `vox_plan`）；
- 不得 import `compiler` 或 `assets` 的内部私有名（只走公开 `__all__`）。

## 禁止事项

- 不得新增第三方依赖
- 不得改动本卡白名单以外的任何文件
- 不得把 MCP 工具改成"也能吃包源目录"（越界：那会要求 import `compiler`，违反 `adapters/AGENTS.md §⑤`）
- 不得改动 `mcp_vox/server.py` 的协议实现

## 验收标准（逐条可判定，验收方逐条核对）

1. `python3 -m unittest discover -s adapters` → `OK`，且条数**只增不减**；
2. **正例（本卡要做的）**：`tools/call vox_pack_check {"pack_dir":"packs/heat_kefu"}` 的返回文本里
   **同时包含** `pack build` 与 `manifest.json`，且 `isError` 仍为 `true`；
3. **负例（不得误伤）**：对一个**正常资产包**（如 `examples/prebuilt-pack`）调用同一工具，
   返回 `passed: true`、`violations: []`，**不得出现**那句提示；`isError` 为 `false`；
4. **负例（不得误报）**：对一个**既没有 `manifest.json` 也没有 `phrases.json`** 的目录，
   返回里**不得出现**那句"包源目录"提示（它只说 `缺少 manifest.json`）；
5. 协议不破：`printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 -m adapters.mcp_vox`
   → rc=0、stderr 为空、stdout 含 `protocolVersion`。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须**调用产品 API**（`adapters.mcp_vox.tools.call_tool`），不得在测试文件内复制判定逻辑；
- 正例与负例都要有；负例断言必须**指出具体情形**（正常包 / 空目录）；
- 不得为通过测试而放宽校验；既有校验强度只增不减（验收方会比对 `_pack_check` 的返回字段数）。

## 回滚方式

删除本卡新增的测试文件 + `git checkout -- adapters/mcp_vox/tools.py adapters/mcp_vox/README.md`。

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`。
**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T88-*.md)" --dir （仓库根）`。
本卡改动点单一、判据是字符串存在性，**适合免费档**；走免费档须在报告里点名。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
