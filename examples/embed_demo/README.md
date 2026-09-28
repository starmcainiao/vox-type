# embed_demo · 形态 3：从仓外 import 执行一轮

形态速查见 [`docs/25`](../../docs/25-怎么在你的项目里用vox-type.md)。本目录示范
**把 vox-type 嵌进自己的进程**（`python3 -c "from runtime import Executor"` 这条路），
而不是通过 CLI 或 MCP 出进程。

三个文件：

| 文件 | 作用 |
|---|---|
| `demo.py` | 调 `assets.load_pack` → `core.protocol.parse_plan` → `runtime.Executor`，执行一轮并把结果打出来。**不含任何判定 / 拼接 / 播包逻辑** |
| `run.sh` | 自解析仓库根 → 塞进 `PYTHONPATH` → `exec python3 demo.py`。与 `bin/vox` 同一范式，从任意 cwd 都能跑 |
| `README.md` | 本文件 |

---

## 30 秒：跑一轮

```sh
sh examples/embed_demo/run.sh
# → == vox-type embed_demo：一轮播报执行完成 ==
#   adapters 来源  = <vox-type 仓库路径>
#   hit             = 4   （plan 共 4 个单元）
#   miss            = 0   （未命中单元数）
#   tts_calls       = 0   （真实 TTS 调用次数；全命中且无槽位时应为 0）
#   first_audio_ms  = 0.913  （命中即播，走磁盘读；每次跑都会漂，见下）
#   output_path     = /tmp/embed_demo_out.wav   （写盘路径，不是返回值——宿主自己读这个文件）
#   total_duration_ms = 16798
# 退出码 0
```

`hit=4 miss=0 tts_calls=0`：plan 的 4 个单元全命中预铸包，**一次 TTS 合成都没发生**，
全程磁盘读。产物写到 `/tmp/embed_demo_out.wav`（16 kHz / 单声道 / 16-bit，约 16.8 s）。
`first_audio_ms` 每次跑都会漂（命中即播只是磁盘读，冷缓存 / 热缓存差别就在毫秒级），
本机连跑实测区间 `0.4` ~ `5.0` ms，别把它当稳定数字引用。

**不用 wrapper、直接设 PYTHONPATH**（两条等价，`run.sh` 只是把这两步写下来）：

```sh
export VOX_ROOT="<vox-type 仓库路径>"
export PYTHONPATH="${VOX_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 examples/embed_demo/demo.py
# 退出码 0；输出同上
```

---

## 为什么必须先设 PYTHONPATH

仓里**没有** `pyproject.toml` / `setup.py` / `setup.cfg` / `requirements.txt`
（`ls pyproject.toml setup.py setup.cfg requirements.txt` → 四项全部
`No such file or directory`）。所以**不能 `pip install`**，`PYTHONPATH` 是唯一安装方式。
`demo.py` 不会替你补环境——它在第一步就检查这个，缺了直接退出码 2 并打印
设置命令，而不是静默把仓库根塞进 `sys.path` 掩盖配置错误。

```sh
python3 examples/embed_demo/demo.py    # 未设 PYTHONPATH
# → embed_demo: vox-type 仓根不在 PYTHONPATH 上，无法 import。
#   本仓没有 pyproject.toml / setup.py / setup.cfg / requirements.txt，
#   不能 pip install，PYTHONPATH 是唯一安装方式：
#     export VOX_ROOT="<vox-type 仓库路径>"
#     export PYTHONPATH="${VOX_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
#     python3 examples/embed_demo/demo.py
#   退出码 2。
```

---

## 顶层包撞名：最容易被吃掉的一次事故

**cwd 优先于 `PYTHONPATH`。** 你的项目目录里只要有 `adapters/` `core/` `assets/`
`tools/` `cli/` 中任意一个同名目录，vox-type 的同名顶层包就会被**静默**劫持。

最坏的部分是**失败形态不指向真因**。以 `adapters/` 为例，你拿到的是：

```
ModuleNotFoundError: No module named 'adapters.textmatch'
```

——这句话暗示"装错了 vox-type"，而真因是"你项目里有个 `adapters/` 目录"。
再隐蔽一层：如果那一份 `adapters/__init__.py` 恰好也能 import 成功
（比如只是命名空间包、没 `__init__.py`），代码会**照常跑起来**，
但查的是另一份包里的索引——这正是 `AGENTS.md` 纪律 4 禁止的静默降级。

所以 `demo.py` 在 import 任何产品模块**之前**先判定一次落点。复现负例：

```sh
mkdir -p /tmp/vox_neg && mkdir -p /tmp/vox_neg/adapters
printf '# my own adapters package\n' > /tmp/vox_neg/adapters/__init__.py
cd /tmp/vox_neg
PYTHONPATH="<vox-type 仓库路径>" python3 <vox-type 仓库路径>/examples/embed_demo/demo.py
# → embed_demo: 拒绝执行 —— vox-type 的顶层包被同名目录劫持了。
#   会解析到（cwd 优先于 PYTHONPATH）：
#     adapters -> /tmp/vox_neg/adapters
#   期望落在 <vox-type 仓库路径> 之下
#   ...
#   退出码 2。
```

退出码 **2**（用法或参数错误），不是 3。修法是把**你自己**的同名目录改名，
别去改名 vox-type 的顶层包。

> 判据只用 `isdir()` 探测、不 `import`——避免执行你项目里那份 `__init__.py`。

---

## 两个命名坑（会咬人，先记住）

**1. 字段叫 `output_path`，CLI 的 JSON 键叫 `out_path`。**

结果对象的字段名是 `output_path`（`runtime/executor.py` 的 `ExecutionResult`），
而 `vox run --json` 输出的键是 `out_path`（`docs/08`）。写成 `result.out_path`：

```
AttributeError: 'ExecutionResult' object has no attribute 'out_path'.
Did you mean: 'output_path'?
```

**2. `out_path` 是必填关键字，语义是「写盘」。**

`execute(self, plan, *, plan_id, turn_id, out_path)` 里 `out_path` 必填，
**不是**可选。内核里没有「返回音频」这个概念，只有「写到一个路径」——
所以 embed 形态是**写盘，宿主自己去读文件**，不是「拿到字节直接播」。
要改成推流 / 回传，属于形态 5 的前置件，见 [`docs/24`](../../docs/24-接入形态与评判.md)
§五·附（明确写了那两件必然要动冻结区 `runtime/executor.py`）。

---

## 一轮里发生了什么（只有四个 API 调用）

```python
pack     = load_pack(Path(pack_dir))                      # assets.load_pack
plan     = parse_plan(json.loads(plan_text))              # core.protocol.parse_plan
executor = Executor(pack, OmlxTts(), allow_fallback=True) # runtime.Executor
result   = executor.execute(
    plan, plan_id="embed-demo", turn_id="1", out_path=OUT_PATH)
```

判定（这条单元该走命中还是慢路）、拼接（多段 wav 怎么接、要不要淡入淡出）、
写盘（16 kHz 单声道 16-bit 容器怎么落）**全在 `runtime.Executor` 里**，
demo 里没有一行判定或拼接代码——`demo.py` 只有一轮编排 + 两道防呆与报错文案
（行数给命令不给数字：`wc -l examples/embed_demo/demo.py`）。

四个值得宿主记住的口径：

- `turn_id` 参与 `variant auto` 的**稳定散列**，同一句话换个 `turn_id` 可能挑到不同变体。
- `allow_fallback=True` 是 fail-open：未命中走慢路（现场合成）并留痕。
  缺省是 `False` = fail-closed，直接抛 `RuntimeMissError`，**不写输出文件**。
- **引擎一致性**仍然拦，而且拦在任何查表**之前**：包清单的 `voice` / `model_version`
  必须和 adapter 对上。本 demo 用 `OmlxTts` 配 `examples/prebuilt-pack`
  （清单登记 `Qwen3-TTS-12Hz-0.6B-Base-bf16`）。换适配器（比如 macsay）时，
  `allow_fallback=False` 抛 `RuntimeMissError`，`reason='engine_mismatch'`；
  `allow_fallback=True` 则 4 个单元全走慢路（`fallback_count=4 tts_calls=4`）。
  这跟 CLI 那边 `--adapter` 必须显式给是同一条闸——省略时 `vox run` 实测退出码 5。
- `hit` / `miss` / `tts_calls` / `first_audio_ms` 就是宿主侧要盯的四个值；
  `total_duration_ms` 与 `output_path` 跟着给。

---

## 与其他两种形态怎么配

| 想做什么 | 用哪个 |
|---|---|
| 一次性播报、脚本 / cron、CI 里验证 | 形态 1（CLI，`sh bin/vox run …`） |
| 让 agent 查「这句话铸了没有」「这批 key 哪些没铸」 | 形态 2（MCP，控制面，不出音频） |
| 语音 agent 框架 / 业务系统里要**毫秒级首音** | 形态 3（本目录） |

命中即播的毫秒级首音**只在进程内兑现**。多一跳网络或多一次工具往返都会把它稀释掉，
所以形态 2 是控制面、形态 3 才是数据面。分层口径见 [`docs/24`](../../docs/24-接入形态与评判.md) §三。
