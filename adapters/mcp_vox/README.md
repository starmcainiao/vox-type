# adapters/mcp_vox · MCP 接入（stdio JSON-RPC，零第三方依赖）

vox-type 的 MCP 最小子集：**只出控制面，不出音频字节**。三条形态的整体评判见 [`docs/24`](../../docs/24-接入形态与评判.md)，
「怎么用」的三种入口汇总见 [`docs/25`](../../docs/25-怎么在你的项目里用vox-type.md)。

---

## 一、启动

协议只有两种写法，区别全在那一行 `RuntimeWarning`：

```sh
# 写法 A（推荐）：干净，stderr 为空
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox

# 写法 B（旧写法，仍然可用）：stderr 会多一行 RuntimeWarning
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox.server
```

写法 B 会在 stderr 吐 `RuntimeWarning: 'adapters.mcp_vox.server' found in sys.modules
after import of package 'adapters.mcp_vox'`——原因是本包 `__init__.py` 先 import 了
`.server`，`runpy` 因此认为该模块"已被包提前装进 `sys.modules`"。stdout 与协议都不破，
但**把 stderr 当错误判据的宿主会误判**，所以推荐写法 A。

**不依赖 cwd 的启动**（写法 A 从任意目录都能跑，前提是仓库根在 `PYTHONPATH` 里；
本仓没有 `pyproject.toml` / `setup.py`，不能 `pip install`）：

```sh
export VOX_ROOT="<vox-type 仓库路径>"
export PYTHONPATH="${VOX_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# 现在在任意目录都能起服务
cd /tmp
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox
# → {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05",
#     "capabilities": {"tools": {}}, "serverInfo": {"name": "vox-type-mcp", "version": "1.0.0"}}}
```

同一条命令的**期望值**（从仓库根跑，退出码 0，stderr 为空）：

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox 2>/tmp/mcp_stderr.txt
cat /tmp/mcp_stderr.txt     # 期望：空文件
```

`<vox-type 仓库路径>` 指含 `core/` `assets/` `runtime/` `adapters/` 四个顶层目录的那个目录
（本文件在 `adapters/mcp_vox/` 下，从它数两层就是）。
`bin/vox` 已经自带同样的自解析（`bin/vox` 用脚本自身位置推导仓库根再塞进 `PYTHONPATH`），
MCP 没有薄壳，所以这步得自己做——`PYTHONPATH` 是**唯一**的安装方式。

## 二、宿主配置片段（`mcpServers`）

```json
{
  "mcpServers": {
    "vox-type": {
      "command": "python3",
      "args": ["-m", "adapters.mcp_vox"],
      "env": {
        "PYTHONPATH": "<vox-type 仓库路径>"
      }
    }
  }
}
```

**`env.PYTHONPATH` 必须写**，否则宿主从自己的工作目录起子进程，拿到的是
`ModuleNotFoundError: No module named 'adapters'`——这条错误**不指向真因**
（真因是仓库根不在 `sys.path`，不是包不存在）。换成绝对路径的 `python3` 也可以，
但 `PYTHONPATH` 仍要指到仓库根。

`"args": ["-m", "adapters.mcp_vox"]` 用包名而不是 `adapters.mcp_vox.server`：
后者会在 stderr 多一行 `RuntimeWarning`（见第一节）。

## 三、协议范围（钉死）

- 传输：**stdio，一行一条 JSON-RPC 2.0**（多行 JSON 文本不当两条消息，按解析失败处理，`-32700`）。
- 方法：`initialize` / `tools/list` / `tools/call` 三个。未知方法 → `-32601`。
  没有 `id` 的通知（含未知通知）静默忽略。
- `protocolVersion` 固定 `"2024-11-05"`；`serverInfo` 固定 `vox-type-mcp` / `1.0.0`。
- **明确不做**：HTTP-SSE 传输、resources、prompts、sampling、批处理、进度通知、取消。
  完整理由见 [`docs/24`](../../docs/24-接入形态与评判.md) §四定案 6。
- 工具内部错误 → `tools/call` 的 `isError: true`（不崩、不吐栈）。

## 四、四个工具的出参

`tools/list` 返回工具名与 `inputSchema`；`tools/call` 的结果包在
`result.content[0].text` 里，**那是又一个 JSON 字符串**（宿主要再解析一次）。
四个工具都没有 path 字段、没有音频字节——这是有意设计，见下节。

### `vox_lookup`

入参：`pack_dir`（必填）+ `text` 或 `key` 二选一。命中判据走
`adapters.textmatch.find_hit`（与 `framework_kefu` 同一份归一化与逐字判据，`docs/10` §10.3 / §10.7）。

| 场景 | `result.content[0].text` 的字段 | `isError` |
|---|---|---|
| 命中 | `hit: true`、`key`、`rate_key`、`variant`、`duration_ms`、`model_version` | `false` |
| 未命中 | `hit: false`、`reason`（=`key_not_prebaked`） | `false` |
| `pack_dir` 不存在 / 包非法 | `error`（`AssetPackError: ...`） | `true` |
| `text` 与 `key` 都没给 | `error`（`ValueError: vox_lookup 需要 text 或 key 之一`） | `true` |

**没有** `path` / 音频文件位置 / 音频字节。拿到 `hit: true` 说明这条话术在包里是铸好的，
**不是**你拿到了音频。

实测（仓库根，`examples/prebuilt-pack`）：

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"vox_lookup","arguments":{"pack_dir":"examples/prebuilt-pack","text":"完全没铸过的话术"}}}' \
  | python3 -m adapters.mcp_vox
# → ... "text": "{\"hit\": false, \"reason\": \"key_not_prebaked\"}", "isError": false}
```

### `vox_plan`

入参：`pack_dir`（必填）+ `keys`（字符串数组）。

| 字段 | 含义 |
|---|---|
| `plan` | 命中条目的数组，元素字段与 `vox_lookup` 命中形态完全相同（`key` / `rate_key` / `variant` / `duration_ms` / `model_version`） |
| `all_hit` | 全部命中才为 `true` |
| `misses` | 未命中的 key 数组（**保留原顺序、原值**） |

**没有** `hit` 布尔单条、**没有** `duration_ms` 汇总、**没有**音频。
未命中是正常结果（`isError: false`），不是错误。

实测：

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"vox_plan","arguments":{"pack_dir":"examples/prebuilt-pack","keys":["farewell","no_such_key"]}}}' \
  | python3 -m adapters.mcp_vox
# → ... "text": "{\"plan\": [{\"key\": \"farewell\", \"rate_key\": \"normal\", \"variant\": 0,
#    \"duration_ms\": 2510, \"model_version\": \"Qwen3-TTS-12Hz-0.6B-Base-bf16\"}],
#    \"all_hit\": false, \"misses\": [\"no_such_key\"]}", "isError": false}
```

### `vox_pack_check`

入参：`pack_dir`（必填）。走 `assets.validate_pack` 公开校验。

| 字段 | 含义 |
|---|---|
| `passed` | `validate_pack` 返回空列表才为 `true` |
| `keys` | 从 `manifest.json` 尽力读出的 key 清单（已排序去重；读不到返回空数组，**不抛**） |
| `violations` | 违规明细数组（空 = 通过） |

校验不过 → `isError: true` **且** `passed: false`（两者同时成立）。
`pack_dir` 读不了 → `isError: true`。

实测：

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"vox_pack_check","arguments":{"pack_dir":"examples/prebuilt-pack"}}}' \
  | python3 -m adapters.mcp_vox
# → ... "text": "{\"passed\": true, \"keys\": [\"chat_smalltalk__2\", \"clarify_repair__1\",
#    \"farewell\", \"opening__1\", \"opening__2\", \"repair_ask_desc\",
#    \"repair_ask_natural_userNo__1\", \"repair_confirm_question__1\",
#    \"transfer_ready\", \"work_order_empty\"], \"violations\": []}", "isError": false}
```

## 五、控制面 vs 数据面（这条别含糊）

**MCP 是控制面，不出音频字节。** 上面三个工具的出参里刻意没有 path、没有文件、没有字节
（`tools.py` 的 `_hit_fields` 就是「刻意不含 path / 音频字节」的那一个函数）。
要真正听见声音，宿主必须自己用**进程内**的 adapter 走 `runtime.Executor`
（见 [`docs/25`](../../docs/25-怎么在你的项目里用vox-type.md) 的形态 3）——
毫秒级首音（命中即播，磁盘读）只在进程内兑现。

为什么不让 MCP 出音：多一跳网络 + 多一次工具往返，会把命中即播的毫秒级首音稀释成
"又一个 TTS"。分层口径见 [`docs/24`](../../docs/24-接入形态与评判.md) §三：
数据面（出音）= 进程内 adapter / 常驻服务，控制面 = CLI + Skill + MCP。

MCP 的正确用法是：**查**（这条话术铸了没有？）、**排**（这批 key 哪些没铸？）、
**验**（这个包还合规吗？），然后宿主拿结论去做播报决策。

## 六、`pack_dir` 的两种口径（**别记反**）

| 工具 | `pack_dir` 指哪 | 为什么 |
|---|---|---|
| `vox_lookup` / `vox_plan` / `vox_pack_check` | **铸出来的资产包**（有 `manifest.json`） | 它们查的是**音频资产** |
| `vox_plan_from_state` | **包源目录**（有 `trigger.json`） | 它查的是**规则**，而 `trigger.json` 不进资产包 |

`vox_lookup` / `vox_plan` / `vox_pack_check` 三条工具的 `pack_dir` **一律只认铸出来的资产包**
（目录里有 `manifest.json`）。`packs/<业务>/` 那种**包源目录**（有 `phrases.json`、
**没有** `manifest.json`）传进来会校验不过。

注意与 CLI 的差别：`vox pack check packs/heat_kefu`（**CLI**）是能吃包源目录的（走
`compiler` 的源格式校验），但 **MCP 工具不吃**——`adapters` 不允许 import `compiler`
（`adapters/AGENTS.md` §⑤），所以 MCP 这边**只加提示、不扩语义**。

`vox_pack_check` 拿到包源目录时，会在 `violations` **末尾追加**一条指路提示（原有违规项、
`passed`、`isError` 都不变，`isError` 仍为 `true`）：

```
这是包源目录（有 phrases.json、没有 manifest.json）；vox_pack_check 只认铸出来的资产包。
请先跑 `sh bin/vox pack build <包源目录> --out <输出目录>`，再把 <输出目录> 传进来。
```

既没有 `manifest.json` 也没有 `phrases.json` 的目录**不会**拿到这条提示（不是包源目录，
就只说 `缺少 manifest.json`）；正常资产包走通时 `violations` 仍是 `[]`。

## 七、`vox_plan_from_state`（T86）：给 `trigger/` 一条产品出口

**它是什么**：把 `trigger/` 的「状态快照 → 播报计划」接成 MCP 工具。
在此之前那条线**能力完整但没有任何出口**——`docs/24 §五#5` 的复跑判据
`grep -rn --include='*.py' "^from trigger" . | grep -v "^./trigger"` 只命中 `labs/ticket-source/`。

入参：`pack_dir`（**包源目录**，须含 `trigger.json`）、`state`（扁平快照，字段须在
`trigger.json` 的 `state_fields` 里声明）、可选 `turn_id`（参与变体稳定散列，便于重放）。

| 场景 | `result.content[0].text` 的字段 | `isError` |
|---|---|---|
| 命中规则 | `trigger_id`、`rule_id`、`plan:[{action,key,rate,variant,slots}]`、`keys`、`chars`、`budget_chars`、`plan_id` | `false` |
| `state` 含**未声明字段** | `error`（`StateError: state 含未声明字段: ['xxx']（…已声明的字段为 […]）`） | `true` |
| **无兜底规则且无规则匹配** | `error`（`TriggerError: …（trigger_id='…'）…`） | `true` |
| 单轮**超预算** | `error`（`BudgetError: …`，含实际字数与预算值） | `true` |
| 目录里**没有 `trigger.json`** | `error`（点名该路径，并说明资产包不含 `trigger.json`） | `true` |

**判定口径全部复用 `trigger/` 的公开 API**（`load_trigger` / `build_plan`），
本适配器**不复制任何规则匹配或预算逻辑**；依赖授权见 `adapters/AGENTS.md §⑨.5`。
同样**不出音频字节**——它只回答"这一轮该说哪些 key"，出音仍归进程内 adapter。

实测（仓库根，`packs/demo-brief`）：

```sh
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"vox_plan_from_state","arguments":{"pack_dir":"packs/demo-brief","state":{"ticket_id":"WB-DEMO-0001","ticket_status":"处理中","days_left":5,"overdue_days":0,"is_overdue":false,"assignee_confirmed":true}}}}' \
  | python3 -m adapters.mcp_vox
# → {"trigger_id": "demo-brief.ticket-reminder", "rule_id": "greeting",
#    "plan": [{"action": "SAY", "key": "greeting_ticket", "rate": "normal", "variant": 0, "slots": {}}],
#    "keys": ["greeting_ticket"], "chars": 11, "budget_chars": 60,
#    "plan_id": "demo-brief.ticket-reminder:None"}
```

> **已知小瑕疵**：不传 `turn_id` 时 `plan_id` 会是 `"<trigger_id>:None"`——这是
> `trigger.build_plan` 的既有派生口径（`plan_id = f"{trigger_id}:{turn_id}"`），
> 本工具**原样透传、不自行发明**。宿主若需要可关联的 `plan_id`，请显式传 `turn_id`。
