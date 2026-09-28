# 25 · 怎么在你的项目里用 vox-type

> 三种形态各一节：**借来执行（CLI）** / **接到 MCP 客户端** / **嵌进自己代码**。
> 每节给可直接复制的真实命令 + 期望输出 + 期望退出码。
> 形态怎么分、为什么这么分，见 [`docs/24`](24-接入形态与评判.md)。本文只回答「怎么开始用」。

---

## 0. 先读这三条（三种形态共用）

1. **本仓是前置包，不依附任何框架。** 没有 `pyproject.toml` / `setup.py` /
   `setup.cfg` / `requirements.txt`（`ls` 四项全部 `No such file or directory`），
   **不能 `pip install`**。跨进程用要靠 `PYTHONPATH` 或 `bin/vox` 自带的仓库根解析。
2. **没有第三方依赖。** 标准库 + 你机器上已有的 ffmpeg / oMLX 引擎端点。
   装 vox-type 不会往你环境里塞任何包。
3. **`<vox-type 仓库路径>`** 指含 `core/` `assets/` `runtime/` `adapters/` 四个
   顶层目录的那个目录。下文所有命令都从这个占位符出发，不含任何本机绝对路径。

---

## 1. 形态 1：借来执行（CLI）

**适合**：一次性播报、脚本 / cron、CI 里做验证、不想在自己进程里常驻任何东西。
**代价**：每轮一个 Python 进程（数十 ms 启动），宿主必须跟 vox-type 同机。

### 1.1 最小闭环

```sh
export VOX_ROOT="<vox-type 仓库路径>"

sh "$VOX_ROOT/bin/vox" run "$VOX_ROOT/examples/plan.json" \
    --pack "$VOX_ROOT/examples/prebuilt-pack" \
    --adapter adapters.tts_omlx:OmlxTts \
    --out /tmp/x.wav
# → run: 通过（hit=4 miss=0 fallback=0 tts_calls=0 first_audio_ms=0.8 out=/private/tmp/x.wav）
#   ← first_audio_ms 会漂，别引用这个数（同口径见 §3.1）
# 退出码 0
```

`--json` 时 stdout 只有纯 JSON、人读摘要走 stderr；不加则相反。
JSON 键是 `out_path`（注意与 Python 对象的 `output_path` 不同，见 §3.2）：

```sh
sh "$VOX_ROOT/bin/vox" run "$VOX_ROOT/examples/plan.json" \
    --pack "$VOX_ROOT/examples/prebuilt-pack" \
    --adapter adapters.tts_omlx:OmlxTts --out /tmp/x.wav --json 2>/dev/null \
  | python3 -c "import json,sys; print(sorted(json.load(sys.stdin).keys()))"
# → ['command', 'events', 'fallback_count', 'first_audio_ms', 'hit_count', 'miss_count', 'out_path', 'pack_dir', 'plan_id', 'total_duration_ms', 'tts_calls', 'turn_id']
```

`hit=4 miss=0 tts_calls=0`：plan 的 4 个单元全部命中预铸包，**一次 TTS 合成都没发生**，
全程磁盘读。产物是 16 kHz / 单声道 / 16-bit 的 WAV，约 16.8 s。

`bin/vox` 用脚本自身位置推导仓库根并塞进 `PYTHONPATH`（`bin/vox:24-32`），
所以**从任意 cwd 都能跑**，不需要先 cd：

```sh
cd /tmp && sh "$VOX_ROOT/bin/vox" verify "$VOX_ROOT/examples/prebuilt-pack"
# → verify: 通过（kind=asset_pack pack_dir=<vox-type 仓库路径>/examples/prebuilt-pack）
# 退出码 0
```

### 1.2 `--adapter` 为什么必须显式给

**不能省。** 缺省时 CLI 用的是 `adapters.tts_macsay:MacSayTts`
（`cli/commands/__init__.py:34`），而 `examples/prebuilt-pack` 的清单登记的引擎身份是
`default` / `Qwen3-TTS-12Hz-0.6B-Base-bf16`（oMLX 小模型）。身份不等 →
`engine_mismatch` → fail-closed。实测省略 `--adapter`：

```sh
sh "$VOX_ROOT/bin/vox" run "$VOX_ROOT/examples/plan.json" \
    --pack "$VOX_ROOT/examples/prebuilt-pack" \
    --out /tmp/x.wav
# → 已按 fail-closed 中止（退出码 5）
# 退出码 5
```

引擎一致性闸在**任何包内查表之前**（`runtime/executor.py`），所以它拦得早、不写输出文件。
「不需要 TTS 服务」≠「不需要 `--adapter`」：`tts_calls=0` 意味着一次合成都没发生，
但身份校验照跑。完整推导见
[`examples/prebuilt-pack/PROVENANCE.md`](../examples/prebuilt-pack/PROVENANCE.md)。

### 1.3 退出码

`0` 成功 / `2` 用法或参数错误 / `3` 运行期失败 / `4` 质检不通过 / `5` fail-closed 中止。
口径与错误分类见 [`docs/08`](08-CLI口径.md)。

```sh
sh "$VOX_ROOT/bin/vox" verify "$VOX_ROOT/examples/prebuilt-pack"     # 退出码 0
sh "$VOX_ROOT/bin/vox" run --help                                    # 退出码 0
sh "$VOX_ROOT/bin/vox" run "$VOX_ROOT/examples/plan.json"            # 缺 --pack → 退出码 2
```

### 1.4 更省事的方式

[`examples/README.md`](../examples/README.md) 有一节「30 秒：无引擎先听见命中即播」，
以及 `sh "$VOX_ROOT/examples/selftest.sh"` 一命令自证（环境自检 → 串真命令 →
期望值 vs 实际值 → 末行 `SELFTEST: PASS|PARTIAL|FAIL`，退出码 `0/3/4`）。

---

## 2. 形态 2：接到 MCP 客户端

**适合**：让 agent 在对话里**查**——"这句话铸了没有"、"这批 key 哪些没铸"、
"这个包还合规吗"。
**不适合**：让 agent 拿到音频去播。MCP 这边不出音频字节，原因见 §2.3。

适配器在 `adapters/mcp_vox/`（手写 stdio JSON-RPC 2.0，零第三方依赖），
细节与三个工具的完整出参表见
[`adapters/mcp_vox/README.md`](../adapters/mcp_vox/README.md)。

### 2.1 宿主配置

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

**`env.PYTHONPATH` 必须写**，否则宿主从自己的工作目录起子进程，拿到的错误是
`ModuleNotFoundError: No module named 'adapters'`——这句话**不指向真因**
（真因是仓库根不在 `sys.path`，不是包不存在）。

`args` 用 `["-m", "adapters.mcp_vox"]` 而不是 `["-m", "adapters.mcp_vox.server"]`：
后者会在 stderr 多吐一行 `RuntimeWarning: 'adapters.mcp_vox.server' found in
sys.modules after import of package 'adapters.mcp_vox'`（因 `adapters/mcp_vox/__init__.py`
先 import 了 `.server`）。协议不破、stdout 干净，但把 stderr 当错误判据的宿主会误判。

### 2.2 不依赖 cwd 的启动（也是本机验证命令）

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox 2>/tmp/mcp_stderr.txt
cat /tmp/mcp_stderr.txt
# → （空文件）
# 退出码 0
```

stdout 是：

```
{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "vox-type-mcp", "version": "1.0.0"}}}
```

必须含 `"protocolVersion"`。从别的目录跑也一样，只要 `PYTHONPATH` 指到仓库根：

```sh
export VOX_ROOT="<vox-type 仓库路径>"
export PYTHONPATH="${VOX_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd /tmp
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 -m adapters.mcp_vox
# 退出码 0；stderr 为空；stdout 含 "protocolVersion"
```

### 2.3 三个工具出什么、不出什么

`tools/call` 的结果包在 `result.content[0].text` 里，**那是又一个 JSON 字符串**
（宿主要再解析一次）。三个工具都是**只出元数据**，没有 path、没有文件、没有字节：

| 工具 | 出参 | 明确没有 |
|---|---|---|
| `vox_lookup(pack_dir, text\|key)` | 命中：`hit` `key` `rate_key` `variant` `duration_ms` `model_version`；未命中：`hit:false` `reason` | `path` / 音频字节 |
| `vox_plan(pack_dir, keys[])` | `plan[]`（元素字段同 `vox_lookup` 命中形态）`all_hit` `misses` | 时长汇总 / 音频字节 |
| `vox_pack_check(pack_dir)` | `passed` `keys` `violations` | —— |

工具内部错误 → `isError: true`（不崩、不吐栈）；未知方法 → `-32601`；
无 `id` 的通知静默忽略；协议范围只有 `initialize` / `tools/list` / `tools/call` 三个方法。

### 2.4 一条口径，别含糊

**MCP 是控制面，不出音频字节。** 上面三个工具的出参里刻意没有 path、没有文件、没有字节
——`adapters/mcp_vox/tools.py` 里那个出参构造函数就是「刻意不含 path / 音频字节」的那个函数。
要真正听见声音，宿主必须自己走进程内 adapter（形态 3）。

为什么不让 MCP 出音：多一跳网络 + 多一次工具往返，会把命中即播的**毫秒级首音**稀释成
"又一个 TTS"。分层口径见 [`docs/24`](24-接入形态与评判.md) §三：数据面（出音）
= 进程内 adapter / 常驻服务，控制面 = CLI + Skill + MCP。

---

## 3. 形态 3：嵌进自己代码

**适合**：语音 agent 框架、业务系统里**要毫秒级首音**的那条路。
**代价**：绑 Python；要自己处理仓库根的 import 路径（见 §3.2）。

完整可跑 demo 在 [`examples/embed_demo/`](../examples/embed_demo/)，
那份 README 把命名坑和负例复现都写全了。

### 3.1 最小闭环

```sh
export VOX_ROOT="<vox-type 仓库路径>"
export PYTHONPATH="${VOX_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 "$VOX_ROOT/examples/embed_demo/demo.py"
# → == vox-type embed_demo：一轮播报执行完成 ==
#   adapters 来源   = <vox-type 仓库路径>
#   hit             = 4   （plan 共 4 个单元）
#   miss            = 0   （未命中单元数）
#   tts_calls       = 0   （真实 TTS 调用次数；全命中且无槽位时应为 0）
#   first_audio_ms  = 1.000  ← 会漂，实测 0.4 ~ 5.0 ms，别引用这个数
#   output_path     = /tmp/embed_demo_out.wav
#   total_duration_ms = 16798
# 退出码 0
```

demo 只调四个公开 API，不含任何判定 / 拼接 / 播包逻辑：

```python
from assets import load_pack
from adapters.tts_omlx import OmlxTts
from core.protocol import parse_plan
from runtime import Executor

pack     = load_pack(Path(pack_dir))                        # assets/pack.py:826
plan     = parse_plan(json.loads(plan_text))                # core/protocol.py:183
executor = Executor(pack, OmlxTts(), allow_fallback=True)
result   = executor.execute(
    plan, plan_id="embed-demo", turn_id="1", out_path=Path("/tmp/x.wav"))
```

签名（照源码抄，核对过 `assets/__init__.py` / `runtime/__init__.py` /
`core/protocol.py` / `runtime/executor.py`）：

```python
assets.load_pack(root: Path) -> AssetPack                              # assets/pack.py:826
core.protocol.parse_plan(raw_plan: List[Any]) -> Plan                  # core/protocol.py:183
runtime.Executor.__init__(pack, adapter, *, duplex=None,
                          allow_fallback=False, policy_stream=False, now=None)   # executor.py:122
runtime.Executor.execute(self, plan, *, plan_id, turn_id, out_path) -> ExecutionResult  # executor.py:197
```

`Plan` 就是 `List[PlanUnit]` 的别名（`core/protocol.py:92`）。

`ExecutionResult` 字段：`events` `output_path` `hit_count` `miss_count`
`fallback_count` `tts_calls` `first_audio_ms` `total_duration_ms`。

### 3.2 两个必须先记住的坑

**坑 1：顶层包撞名，会静默吃掉你。**
cwd **优先于** `PYTHONPATH`。你的项目目录里只要有 `adapters/` `core/` `assets/`
`tools/` `cli/` 中任意一个同名目录，vox-type 的同名顶层包就被静默劫持。
最坏的是失败形态**不指向真因**：

```
ModuleNotFoundError: No module named 'adapters.textmatch'
```

——它暗示"装错了 vox-type"，真因是"你项目里有个 `adapters/` 目录"。
再隐蔽一层：如果那一份恰好也能 import 成功（比如只是个空目录的命名空间包），
代码会**照常跑起来**，但查的是另一份东西的索引——这正是 `AGENTS.md` 纪律 4
禁止的静默降级。

`examples/embed_demo/demo.py` 因此在 import 任何产品模块**之前**先判定一次落点，
命中撞名就退出码 2 并指出真因。负例复现（一条命令）：

```sh
mkdir -p /tmp/vox_neg/adapters
printf '# my own adapters package\n' > /tmp/vox_neg/adapters/__init__.py
cd /tmp/vox_neg
PYTHONPATH="<vox-type 仓库路径>" \
  python3 "<vox-type 仓库路径>/examples/embed_demo/demo.py"
# → embed_demo: 拒绝执行 —— vox-type 的顶层包被同名目录劫持了。
#   退出码 2
```

修法是把**你自己**的同名目录改名，别去改名 vox-type 的顶层包。

**坑 2：字段叫 `output_path`，CLI 的键叫 `out_path`。**

`ExecutionResult` 的字段名是 `output_path`（`runtime/executor.py:77`），
而 `vox run --json` 输出的键是 `out_path`（[`docs/08`](08-CLI口径.md)）。
写 `result.out_path`：

```
AttributeError: 'ExecutionResult' object has no attribute 'out_path'.
Did you mean: 'output_path'?
```

**`out_path` 是必填关键字，语义是「写盘」。** 内核里没有「返回音频」这个概念，
只有「写到一个路径」——所以 embed 形态是**写盘，宿主自己去读文件**，
**不是**「拿到字节直接播」。要改成推流 / 回传，属于形态 5 的前置件，
见 [`docs/24`](24-接入形态与评判.md) §五·附——那里明确写了那两件必然要动
冻结区 `runtime/executor.py`，不是「随便加」能排期的活。

### 3.3 引擎一致性照样拦

`Executor` 在**任何查表之前**比对包清单的 `voice` / `model_version` 与 adapter。
`examples/prebuilt-pack` 登记的是 `Qwen3-TTS-12Hz-0.6B-Base-bf16`，配 `OmlxTts` 才等；
换成 macsay 时，`allow_fallback=False` 抛 `RuntimeMissError`（`reason='engine_mismatch'`），
`allow_fallback=True` 则 4 个单元全走慢路（`fallback_count=4 tts_calls=4`）。
跟 §1.2 是同一条闸。

---

## 4. 三种形态怎么选

| 场景 | 用哪个 | 理由 |
|---|---|---|
| 脚本 / cron / CI 里播报或验证 | 形态 1 | 零常驻、退出码可判、调试透明 |
| agent 要查「铸了没有 / 合规吗」 | 形态 2 | 工具 schema 供模型直接消费；覆盖面最大 |
| 语音框架 / 业务系统要出音 | 形态 3 | **毫秒级首音只在这条路上兑现** |
| 多 agent 共用一套资产、远程宿主 | 形态 5（未落地） | 需要先在扩展区备齐三件，见 `docs/24` §五·附 |

一句话：**控制面走 CLI / MCP，数据面（出音）走进程内 adapter。**
MCP 与 CLI 都不进数据面——工具往返 + 音频回传会把 0.x ms 稀释成"又一个 TTS"。
