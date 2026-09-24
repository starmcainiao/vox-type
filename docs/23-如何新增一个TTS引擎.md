# 23 · 如何新增一个 TTS 引擎

> 面向「刚 clone 的开发者」。目标：不读内核、不看任务卡，照着这份就能加一个 `tts_*` 并跑绿全套测试。
> 最短实例是 `adapters/tts_macsay/`（一个包两个文件）。本文所有 `file:line` 都是可现查的。

**一句话概括边界**：`adapters/` 是扩展区，可以随便加；**内核一行不改**，你只需通过接口一致性测试。
（`adapters/AGENTS.md:47`）

---

## 1. 你要写的最少东西

```
adapters/tts_<你的引擎>/
├── __init__.py     # 导出类（3 行）
└── adapter.py      # 适配器本体（≤ 150 可执行行）
```

核心就一个方法：**`synthesize(text, rate_key) -> 16k wav`**。

契约在层文件里写死：「实现 `core/` 定义的接口（TTS：`synthesize(text, rate_key) -> 16k wav`；能力声明
`capabilities`）」（`adapters/AGENTS.md:20`）。

**一个必须先说的落差**：这句里「`core/` 定义的接口」在字面上不成立——`core/` 只有 `protocol.py`
（plan 协议）和 `metrics_spec.py`（指标口径），**没有** `synthesize` 的抽象基类（`core/protocol.py:1`
docstring 明写「不含 TTS（adapters/）」）。契约真正的载体是**鸭子类型**：谁读、谁查，看这两处——

- 运行时只认三个成员：`synthesize` / `voice` / `model_version`，缺一就抛
  `TypeError: adapter 缺少必需成员: [...]`（`runtime/executor.py:187-192`）；
- 实际调用形式是**位置参数带路径**：`self.adapter.synthesize(text, target, rate_key)`
  （`runtime/executor.py:249-254`）。所以你的签名必须是
  `synthesize(self, text: str, out_path, rate_key: str = "normal") -> None`——
  **写文件，不返回音频**（两个现有实例都返回 `None`：`adapters/tts_macsay/adapter.py:63`、
  `adapters/tts_omlx/adapter.py:108`）。

产物格式是**被下游硬读的常量**，不是建议：`16kHz / 单声道 / 16-bit`。
一致性测试逐字段断言这三项（`adapters/tests/test_conformance.py:149-170`）。

---

## 2. 必须声明的五项（以及不声明会怎样）

全部是**类属性**，不是 `__init__` 里的实例状态——一致性测试会直接构造 `cls()` 后读它们
（`adapters/tests/test_conformance.py:37-38`），所以它们必须在无参构造下就能取到。

| 项 | 例子（macsay） | 作用 | 不声明 / 声明错会怎样 |
|---|---|---|---|
| `name` | `"macsay"` | 适配器身份，参与资产索引 | 一致性测试断言非空（`test_conformance.py:40-44`） |
| `model_version` | `"macos-say"` | **资产指纹的四个输入之一** | 见下方详解——这是最危险的一项 |
| `requires_core` | `"^0.1"` | 依赖的内核版本区间 | 一致性测试断言非空（`test_conformance.py:55-62`）；见下方详解 |
| `rate_map` | `{"slow":150,"normal":200,"fast":300}` | 语义档位 → 物理参数 | 缺任一档 → 测试红；`rate_value()` 里还得自己拦未知档 |
| `capabilities` | `{"zero_shot_clone":True,...}` | 能力声明（**可选但建议写**） | 见下方详解——目前无人消费 |

出处：`requires_core` / `name` / `model_version` / `rate_map` 的强制清单在
`adapters/AGENTS.md:21`；`voice` 是第四项运行时必需成员（`runtime/executor.py:187`），虽然
AGENTS.md §② 没写它，但你照样得给。

### `model_version`：写错会「突然换了个声音」

指纹算法（冻结，唯一实现）是
`sha256(f"{text}\0{voice}\0{rate_value}\0{model_version}")[:16]`
（`assets/fingerprint.py:8-9`、`:41-42`）。`compiler` 铸造时把它喂进去
（`compiler/prebake.py:119-124`）。

预铸的**差量复用**门槛拿它做判据：

> 复用需要旧包的 voice/model_version 与当前引擎一致
> WHY：音色/模型版本换了，旧音频是「另一个声音」，复用 = 运行时突然换了个声音，
> 这是红线里点名的静默降级。

（`compiler/prebake.py:254-261`）

推论：**换模型版本或换音色，必须同时改 `model_version`**，否则旧音频会被当新的复用下去。
层文件把它写成变更纪律：「外部 SDK 升级导致行为变化（音色/语速映射改变）→ 必须**同时提升
`model_version`**，触发全库重铸」（`adapters/AGENTS.md:43`）。验收标准 3 也点名要有断言
（`adapters/AGENTS.md:28`）。

参考实现（克隆路径下模型版本随之变化）：`CLONE_MODEL_VERSION = "qwen3-tts-0.6b-base-clone"`
（`adapters/tts_omlx/adapter.py:32`），声明处注释「换音色即换指纹，旧资产自动失效」
（`adapters/tts_omlx/adapter.py:10`）。

### `requires_core`：契约要求强校验，但当前实现只有「非空」

层文件的验收标准是「声明了 `requires_core`，与内核大版本不匹配时**加载即报错**」
（`adapters/AGENTS.md:27`），内核侧也登记了版本对应关系「适配器声明 `requires_core: ^x.y`，
内核只允许**加能力不改语义**」（`core/AGENTS.md:80`）。

**如实说**：`tools/structure_budget/check.py` 里没有任何 `requires_core` / semver 解析
（全文件无此字样），全仓也没有「大版本不匹配即报错」的实现——现状是**只有**一致性测试断言它
非空（`adapters/tests/test_conformance.py:55-62`）。写 `"^0.1"` 是对的（与两个现有适配器一致），
但「不匹配即报错」目前是一个**尚未落地的验收条款**，不要以为声明了就有人替你校验。

### `capabilities`：写了不被读

AGENTS.md §② 把 `capabilities` 列进契约（`adapters/AGENTS.md:20`），但全仓只有 `tts_omlx`
声明了它（`adapters/tts_omlx/adapter.py:47`），且**只有一个测试在读它**——
`test_capabilities_declared`，断言 `zero_shot_clone` 为真、`streaming` 为假
（`adapters/tts_omlx/tests/test_adapter.py:304-308`）。`runtime/`、`compiler/`、`assets/`
都不消费它。所以它是**声明面，不是执行面**：写上它，方便将来的人知道你能干什么，
但别指望有代码替你把关。`tts_macsay` 就没有这一项，也不违规。

---

## 3. 边界纪律（三条，都是硬线）

### ① ≤ 150 可执行行

「单个适配器 ≤ 150 行（不含注释与测试）。超出即说明它干了不该干的活」
（`adapters/AGENTS.md:22`）。同一数字以机器可读形式登记在
`adapters/AGENTS.md:104`（「结构预算：可执行行数阈值 <= 150」）——
**那一行别加粗星号**，脚本正则会匹配失败，等于阈值消失（`adapters/AGENTS.md:106`）。

**自查命令**（只判定、不落盘）：

```sh
python3 tools/structure_budget/check.py --no-write
```

口径别自己数：「`tokenize` 后 `NEWLINE` token 计数（一条逻辑语句结束时的换行），
排除注释与 docstring；不含 `tests/` 与 `__pycache__`」（`adapters/AGENTS.md:112-115`）。
注意 **`NL` 不能用**，它是「视觉空白行」的 token，实测 `a = 1\nb = 2\n` 的 `NL` 计数为 0——
用错会把口径反转成「数空行数」（`tools/structure_budget/check.py:86-98`）。

超了怎么办：**先拆，不要申请豁免**。参考 T41 的做法——把「HTTP 传输 / 请求体 / 重试」拆到
`transport.py`，`adapter.py` 回到只做编排，实测 `adapter.py` 115 行、`transport.py` 71 行，
**未新增豁免**（`adapters/AGENTS.md:92-98`）。真要豁免，得两处同改：层 AGENTS.md 写理由
（`adapters/AGENTS.md:81-85`）+ 台账登记块加一行（`LEDGER.md:141-148`）——
未登记的超限一律判违规、非 0 退出（`tools/structure_budget/check.py:246-257`）。

### ② 不得 import 其他层内部

允许依赖「`core/`；外部库/SDK（自行管理）」；禁止「import `compiler/`、`runtime/`、`assets/` 的内部实现」
（`adapters/AGENTS.md:38-39`）。验收标准再强调一次：「不 import 其他层内部（只 import `core/` 公开接口）」
（`adapters/AGENTS.md:29`）。

实操含义：**不要** `from runtime.audio import read_wav` 这类跨层取实现——
`runtime` 在下一层，你往上取就是越界。`adapters/tts_macsay/adapter.py:8-12` 的全部 import 是
标准库（`shutil` / `subprocess` / `tempfile` / `wave` / `pathlib`），零本仓依赖——
**这是可以照抄的干净样板**。

### ③ 失败必须抛，不许返回静音 / 空文件

「失败必须抛出（不得返回静音/空文件）」（`adapters/AGENTS.md:30`）。

为什么这条是红线，`tts_omlx` 的注释说得最直白：
「空文本不合成：产出静音 WAV 会被下游当成成功，等于静默降级」
（`adapters/tts_omlx/adapter.py:110`）。

具体判据照抄 `tts_macsay`：

- 空文本 → 抛（`adapters/tts_macsay/adapter.py:77-78`），一致性测试无条件断言，
  连 `say` 命令不可用的机器上也跑（`adapters/tests/test_conformance.py:119-131`）；
- 外部命令非零退出 → 抛，消息带退出码 + stderr 前 500 字
  （`adapters/tts_macsay/adapter.py:121-125`）；
- **产物不存在或 0 字节 → 抛**（`adapters/tts_macsay/adapter.py:127-128`）——这条最容易被漏，
  写了文件路径就返回，等于给下游喂了一个假成功；
- 超时 → 抛，消息带超时秒数（`adapters/tts_macsay/adapter.py:116-119`）；
- 落盘移动失败 → 抛，消息带源→目标路径（`adapters/tts_macsay/adapter.py:136-139`）。

统一用一个 `TtsError(RuntimeError)` 承载，消息必须含失败原因、「不允许吞掉错误上下文」
（`adapters/tts_macsay/adapter.py:17-21`）。

---

## 4. 怎么验：接口一致性测试

测试文件头就写了扩展方式：「在 `ADAPTER_CLASSES` 列表中追加新适配器类即可，无需重写断言」
（`adapters/tests/test_conformance.py:11`）。注册表目前只有
`ADAPTER_CLASSES = [MacSayTts]`（`adapters/tests/test_conformance.py:26-28`）。

**第一步，把自己加进注册表**（编辑 `adapters/tests/test_conformance.py`）：

```python
from adapters.tts_yours.adapter import YourTts   # 顶部补 import
ADAPTER_CLASSES = [MacSayTts, YourTts]
```

**第二步，跑它**（本仓不装第三方依赖，用 `unittest`）：

```sh
python3 -m unittest discover -s adapters/tests -p "test_conformance.py"
```

本仓实测：`Ran 12 tests ... OK`，退出码 0。

**第三步，把这一层全跑一遍**：

```sh
python3 -m unittest discover -s adapters
```

本仓实测：`Ran 302 tests ... OK`，退出码 0。

**这 12 条断言会卡你什么**（`test_conformance.py`）：

| 组 | 断言 | 行号 |
|---|---|---|
| 属性完整性 | `name` / `model_version` / `requires_core` 非空 | 40-62 |
| 属性完整性 | `rate_map` 含 `slow` / `normal` / `fast` | 64-80 |
| `rate_value` | 已知三档返回 `int` | 92-98 |
| `rate_value` | 未知档抛 `TtsError` | 100-105 |
| `rate_value` | 错误消息**包含那个 key 名** | 107-113 |
| `synthesize` | 空文本抛 `TtsError`，消息提及「空」 | 125-131 |
| 真实合成 | `channels=1`、`sampwidth=2`、`framerate=16000`、帧数 > 0 | 149-170 |
| 真实合成 | 三档各自合成，格式均合法 | 172-181 |

两条容易忽略的：

- 「错误消息包含 key 名」是独立断言（`test_conformance.py:107-113`）——只抛 `TtsError` 不算过，
  消息里得真的出现 `"nonexistent_rate_xyz"`。
- 真实合成那组带 `@unittest.skipUnless(shutil.which("say"), ...)`（`test_conformance.py:137`），
  **没有 `say` 的命令会静默跳过**，绿≠真过。如果你的引擎靠外部命令，自己补一个等价探针。
  （非 macOS 的机器可以照 `README.md:107-118` 改走 `adapters.tts_omlx:OmlxTts`。）

---

## 5. 怎么接进使用面：`--adapter <模块>:<类名>`

### 解析在哪

`resolve_adapter()` 在 `cli/commands/__init__.py:94`；缺省规格常量
`DEFAULT_ADAPTER_SPEC = "adapters.tts_macsay:MacSayTts"` 在 `:34`。三个命令各调一次：
`run`（`cli/commands/run.py:123`）、`pack build`（`cli/commands/pack_build.py:133`）、
`bench`（`cli/commands/bench.py:145`）。flag 定义形如
「`--adapter`，适配器 `<模块>:<类名>`，缺省 `{DEFAULT_ADAPTER_SPEC}`」
（`cli/commands/run.py:46-50`）。

**你的引擎要能被 import 到**：路径就是 `adapters.tts_yours.adapter:YourTts`（模块名带点，
类名不带模块前缀）。因为 `resolve_adapter` 里是
`module = importlib.import_module(module_name)`（`cli/commands/__init__.py:124-130`），
所以 `adapters/` 必须是个能被解析的包目录——照 `tts_macsay` 抄个 `__init__.py` 就行
（`adapters/tts_macsay/__init__.py:1-6`）。

### 为什么用 importlib，不能静态 import

层内注释把原因写全了：

> **适配器只能动态解析**（importlib，格式 `<模块>:<类名>`），本包内**不得静态 import adapters**。
> WHY：`adapters/` 是扩展区（新增引擎不该改内核与 CLI）；静态 import 会把 CLI 与某个具体引擎焊死。
> 解析失败一律 → 退出码 2 且**绝不回落**——回落成默认引擎就是「本该用 A 引擎却换了 B」的
> 静默降级，事后无法定位。

（`cli/commands/__init__.py:8-13`；同一理由在实现处再复述一次：`cli/commands/__init__.py:124`）

对你的直接后果：**新增引擎不用改 `cli/`，一行都不用**。缺省引擎是写死的字符串而不是 import 出来的
（`cli/commands/__init__.py:90-93`），保持「CLI 不知道任何具体引擎」这条边界。

### 失败行为：不回落，退出码 2

`resolve_adapter` 的五种失败**全部**抛 `UsageError`、全部不回落
（`cli/commands/__init__.py:103-150`）：格式非法（`:110-114`）、模块导不进来（`:127-130`）、
类不存在（`:132-135`）、不是类（`:137-142`）、实例化失败（`:144-150`）。
最后一条值得留意：注释明写「不回落：解析成功但实例化失败同样是参数问题（写错类名/构造需要参数）」
（`cli/commands/__init__.py:146-147`）。

**推论**：如果你的引擎需要构造参数，别指望从 CLI 传进来——会直接失败。
现成的替代入口是 env，参考 `tts_omlx`：「`--adapter` 传不进构造参数，env 是服务器上唯一的入口」
（`adapters/AGENTS.md:10-12`）；`VOX_TTS_ENDPOINT` / `VOX_TTS_VOICE` 兜底链与
「给错值抛 `ValueError`，不静默回落」见 `adapters/tts_omlx/adapter.py:53-61`。

### 用你的引擎跑一遍

```sh
sh bin/vox run examples/plan.json \
  --pack /tmp/vox-demo/heat-kefu \
  --adapter adapters.tts_yours.adapter:YourTts \
  --out /tmp/vox-demo/out.wav
```

---

## 6. 照着抄的最小骨架

骨架自 `adapters/tts_macsay/adapter.py` 抽出，标注了每段来源。**先说清两处骨架化的取舍**：
macsay 的 `synthesize` 是 55 个可执行行里的主体，靠 tempfile 绕开
「say 对 `/dev/null`、`/dev/stdout` 等特殊文件会拒绝（-241/-54）」（`:89-92`）再 `shutil.move`
（`:135-139`）——那是 `say` 命令特有的坑，**不用抄**；下面换成直写目标路径，但保留了「产物
不存在或 0 字节即抛」（`:127-128`），这条要留。

**骨架可执行行 27**（`check.py` 口径实测，去掉了 `_call_engine` 里的 TODO 也是 26），远在 150 以内。

### `adapters/tts_yours/__init__.py`

```python
from .adapter import YourTts, TtsError

__all__ = ["YourTts", "TtsError"]
```

（照 `adapters/tts_macsay/__init__.py:4-6`）

### `adapters/tts_yours/adapter.py`

```python
"""adapters.tts_yours.adapter — <你的引擎> 薄适配器（16kHz/单声道/16-bit WAV）"""

from pathlib import Path

_TIMEOUT_SECONDS: int = 60                              # ← macsay:14


class TtsError(RuntimeError):                            # ← macsay:17-21
    """TTS 合成失败统一异常。消息必须包含失败原因，不允许吞掉错误上下文。"""


class YourTts:
    name: str = "yours"                                 # ← macsay:35
    model_version: str = "your-engine-v1"               # ← macsay:36（换引擎/换音色必须提升）
    voice: str = "default"                              # ← macsay:37
    rate_map: dict[str, int] = {                        # ← macsay:38-42
        "slow": 150,
        "normal": 200,
        "fast": 300,
    }
    requires_core: str = "^0.1"                         # ← macsay:43
    capabilities = {"streaming": False}                 # ← tts_omlx:47（可选）

    def rate_value(self, rate_key: str) -> int:         # ← macsay:45-61
        """语义档位 → 物理参数；未知档位抛错，消息里带上 key 名。"""
        if rate_key not in self.rate_map:
            raise TtsError(
                f"未知语速档位: {rate_key!r}（仅支持: {', '.join(sorted(self.rate_map))}）"
            )
        return self.rate_map[rate_key]

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        """合成 16kHz/单声道/16-bit WAV 落盘；任何失败都抛 TtsError。"""
        if not text:                                    # ← macsay:77-78
            raise TtsError("空文本无法合成语音（禁止合成静音）")

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)   # ← macsay:80-85
        rate = self.rate_value(rate_key)                # ← macsay:87

        self._call_engine(text, out, rate)              # ← macsay:95-125（见下）

        if not out.exists() or out.stat().st_size == 0: # ← macsay:127-128
            raise TtsError("引擎未产出有效文件（文件不存在或 0 字节）")

    def _call_engine(self, text: str, out: Path, rate: int) -> None:
        """调你的引擎。非零退出 / 超时 → 抛 TtsError，消息带可定位上下文。"""
        raise TtsError("TODO: 接你的引擎（非零退出与超时都必须抛，不许吞）")
```

三处**照抄不后悔**的细节：

1. `rate_value` 的错误消息用 `{rate_key!r}` 原样带出 key——一致性测试逐字匹配
   （`adapters/tests/test_conformance.py:107-113`）。
2. `synthesize` 的签名里 `rate_key` 是**第三个位置参数且带默认值**——
   `runtime` 是按位置调的（`runtime/executor.py:254`）。
3. 产物 0 字节判定放在合成之后、返回之前（`:127-128`）——没有这道闸，
   引擎悄悄写个空文件就被下游当成成功。

---

## 7. 提交前清单

```sh
# ① 契约齐全：一致性测试（本仓实测 Ran 12 tests OK，rc=0）
python3 -m unittest discover -s adapters/tests -p "test_conformance.py"

# ② 本层全绿（本仓实测 Ran 302 tests OK，rc=0）
python3 -m unittest discover -s adapters

# ③ 结构预算：只判定不落盘（本仓实测 rc=0，「结构预算：全部合规」）
python3 tools/structure_budget/check.py --no-write
```

对照层文件的验收条件逐条自查（`adapters/AGENTS.md:26-30`）：
① 接口一致性测试通过；② 声明了 `requires_core`；③ `model_version` 参与指纹；
④ 不 import 其他层内部；⑤ 失败必须抛出。

第 2 条如实写：**当前实现只校验非空，没有 semver 解析**
（见 §2「`requires_core`」）。

## 附：本文引用的「落差」清单

写文档时顺手核出来的，避免下一位读者踩：

| 落差 | 出处 |
|---|---|
| AGENTS.md 说「实现 `core/` 定义的接口」，但 `core/` 没有 TTS 接口定义 | `adapters/AGENTS.md:20` vs `core/protocol.py:5` |
| `synthesize` 签名在文档里是 `(text, rate_key)`，实际是 `(text, out_path, rate_key)` | `adapters/AGENTS.md:20` vs `runtime/executor.py:254` |
| `voice` 是运行时必需成员，AGENTS.md §② 未列入「必须声明」清单 | `runtime/executor.py:187` vs `adapters/AGENTS.md:21` |
| `requires_core` 的「加载即报错」未落地 | `adapters/AGENTS.md:27`（全仓无 semver 解析） |
| `capabilities` 无人消费，仅 `tts_omlx` 自测 | `adapters/AGENTS.md:20` vs `adapters/tts_omlx/adapter.py:47` |
