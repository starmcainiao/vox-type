# adapters/ · 引擎与框架适配器（扩展区）

## ① 职责 / 不负责什么

**职责**：**薄**适配器，把外部能力接进本系统。
- `tts-*`：TTS 引擎（如 `tts-macsay`、后续 `tts-qwen3`、`tts-cloud`）
  - **`tts_omlx/` = OpenAI 兼容 `/v1/audio/speech` 客户端**（T40 登记，消除「名字叫 omlx 却不是
    omlx 专用」的认知债）：它说的是 **OpenAI speech 协议**，谁实现这个协议就适配谁——本机 oMLX、
    Linux 上的 vLLM-Omni / SGLang-Omni，或任意云服务；**目录名里的 `omlx` 是历史，不是专用**。
    基址与音色都由 env 决定（`--adapter` 传不进构造参数，env 是服务器上唯一的入口）：
    `VOX_TTS_ENDPOINT`（缺省 `http://127.0.0.1:10099`）、`VOX_TTS_VOICE`（缺省 `default`）；
    给错值（非 http/https 的基址）抛 `ValueError`，**不静默回落**。克隆路径优先于 `VOX_TTS_VOICE`。
- `asr-*`：（后置）语音识别引擎
- `framework-*`：（后置）框架插件（pipecat / livekit / 自研 organs/voice）

**不负责**：不含业务逻辑；不改内核；不做拼接与判定（`runtime/`）；不做预铸调度（`compiler/`）。

## ② 输入 / 输出契约

- 实现 `core/` 定义的接口（TTS：`synthesize(text, rate_key) -> 16k wav`；能力声明 `capabilities`）。
- 必须声明：`requires_core: ^x.y`、`name`、`model_version`（进指纹）、`rate_map`（语义档位 → 物理参数）。
- **薄**的量化标准：单个适配器 ≤ 150 行（不含注释与测试）。超出即说明它干了不该干的活。

## ③ 验收条件

1. **接口一致性测试**通过（同一套测试跑所有 tts-* 适配器）；
2. 声明了 `requires_core`，与内核大版本不匹配时**加载即报错**；
3. `model_version` 参与指纹（换模型版本 → 旧资产自动失效，测试断言）；
4. 不 import 其他层内部（只 import `core/` 公开接口）；
5. 失败必须抛出（不得返回静音/空文件）。

## ④ 本层数据收集

引擎侧统计由 `runtime/` 统一记录（本层不自建埋点，避免双份口径）。

## ⑤ 依赖边界

- 允许依赖：`core/`；外部库/SDK（自行管理）。
- 禁止：import `compiler/`、`runtime/`、`assets/` 的内部实现。

## ⑥ 变更纪律

外部 SDK 升级导致行为变化（音色/语速映射改变）→ 必须**同时提升 `model_version`**，触发全库重铸（指纹机制自动生效）。

## ⑦ 冻结状态

**[扩] 扩展区**。可以无限新增；新增不需要改内核，只需通过接口一致性测试。

---

## ⑧ `state_trigger/` 已迁出（2026-09-19 → `trigger/`，**历史记录，不再具约束力**）

本节原为「已知形态例外：`state_trigger/`（2026-09-19 登记，**是权宜不是先例**）」。
原条款要点如下，**保留备查，但自迁出之日起对本层不再构成约束**：

- `adapters/state_trigger/` 是本层里的**非引擎形态**：前置包的 **`state` 快照格式 + `trigger.json`
  映射格式 + 校验器**（T16 产物），**不是** §① 列的 `tts-*` / `asr-*` / `framework-*`，因此
  **不适用** §② 的实现清单与 §② 的 ≤150 行量化标准（同类是 `compiler/source.py` 312 行、
  `compiler/script.py` 373 行；本模块去注释 663 行）——**登记于此是为了不出现"静默越界"**，
  不是为了开后门；
- 暂住的原因是**架构缺口，不是设计选择**：`core/` 只定义协议（§① 明确不含校验实现）、`compiler/`
  是**编译期**，而 `trigger.json` 是**运行期每轮**读的输入；本仓当时的五个 Python 层里没有
  「**运行期输入协议 + 其校验器**」这一类的归属；
- 暂住期间的四条边界纪律（现已原文转移到 `trigger/AGENTS.md §⑤`，对迁出后的模块继续生效）：
  ① 只许依赖 `core/` 与 `compiler/` 的公开 API，不得走内部模块路径；② 不含业务逻辑；
  ③ 不做 I/O 之外的副作用（只读声明的包源文件，不写盘、不发网络请求、不起进程）；
  ④ 不得被 `runtime/` / `assets/` / `eval/` / `cli/` import，内核不得反向依赖它。

**迁出原因与结果（2026-09-19 用户拍板，`docs/13 §八#15`）**：`state → plan` 是「决定说什么」，
属**接缝之上**（`docs/10 §10.1`），不属 adapters 的引擎形态；扩展区「随便加」的纪律适合它的成长期。
故升为**独立扩展区 `trigger/`**（自带七项 AGENTS.md），由 T28 整体迁入——文件格式与行为零变化，
仅改 import 路径与落点说明。**本节的暂住条款与「不得再往 `adapters/` 里加第二个同类模块」的禁令
随之解除**；同类模块从此落 `trigger/`，不再进入本层。

---

## ⑨ 已知形态例外：`framework_kefu/` 宿主桥（2026-09-19 登记losure，结构审计补账）

结构审计（2026-09-19）发现两处**未登记的既成事实**，按 §⑧ 先例（「登记于此是为了不出现静默越界」）补登记：

1. **行数豁免**：`framework_kefu/bridge.py`（可执行 231 行）、`kefu_client.py`（236 行）、
   `asr_omlx/adapter.py`（176 行）超 §② 的 ≤150 行——它们是与外部宿主（kefu HTTP / oMLX 服务）
   的**协议桥**，同类是 `compiler/source.py`（312 行）；豁免理由与 `trigger/AGENTS.md §②` 同口径。
   **拆分留口子**：asr_omlx 拆「HTTP 传输/转写协议」两层即可自然回 150 内，不必长期靠豁免；
   kefu_client 的 HTTP 传输层同理。**豁免不是永久豁票**：再进新功能前先拆。
2. **依赖授权**：`framework_kefu` 依赖 `runtime` / `assets` 的**公开 API**（`Executor` / `AssetEntry` /
   `REASON_KEY_NOT_PREBAKED` 等，均在各自 `__all__`）——§⑤「允许依赖：core/」对本子目录放宽为
   「core + runtime/assets 公开面」。理由：framework_kefu 是把本仓**接进宿主链路**的桥（docs/10 §10.5、
   T11 验收登记），它必然要驱动 runtime 播包；这与「业务适配器只吃 core」的普通适配器不同。
   **边界仍硬**：不得 import 内部私有名（下划线）；只走公开 `__all__`。

3. **实现变更（T41，2026-09-23）：`tts_omlx/` 拆出 `transport.py`**——按 §⑨.1 对 asr_omlx 写下的
   同一思路（「拆传输/协议两层可自然回 150 内，不必长期靠豁免」），把「HTTP 传输 / 请求体 /
   传输层有界重试」移到 `adapters/tts_omlx/transport.py`，`adapter.py` 回到只做编排
   （音色 / 指纹 / 克隆 / 落盘 / 契约复验）。实测：`adapter.py` **115** 可执行行、
   `transport.py` **71**（`tools/structure_budget/check.py` 口径），均 ≤150——**未新增豁免**，
   §⑨.1 的豁免清单不变。**重试纪律**：归一自检失败的有界重试沿用 `transport.MAX_RETRIES` /
   `RETRY_DELAYS`，每次重采写 stderr 留痕，超限仍 fail-closed（确定性失败一次都不重试）。

本节登记经第十五批后全仓结构审计（三审计员）发现并补账；原 §⑤ 字面对 framework_kefu 不再单独适用。

## ⑩ 结构预算（T21 登记，机器可读）

结构预算：可执行行数阈值 <= 150

（机器可读标记行，与 §② 的量化标准是同一个数；**不要在这行加任何修饰符**——加粗星号会让正则匹配失败，等于阈值消失。）

与 §② 的量化标准是**同一个数**（150），
本节只是把它写成 `tools/structure_budget/check.py` 能读的机器可读形式。
**阈值出处不变，仍是 §②**；本节不新立阈值。

口径：`tokenize` 后 `NEWLINE` token 计数（一条逻辑语句结束时的换行），
排除注释与 docstring；不含 `tests/` 与 `__pycache__`。§⑨.1 记的
231 / 236 / 176 与本口径同算法（实测 `asr_omlx/adapter.py` = 106，
落在 T14 裁定的 145–150 带内）。

超限登记见 §⑨.1（`framework_kefu/bridge.py`、`kefu_client.py`、`asr_omlx/adapter.py`）；
机器可读的豁免清单在 `tools/structure_budget/LEDGER.md` 的「豁免登记」块。
