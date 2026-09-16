# adapters/ · 引擎与框架适配器（扩展区）

## ① 职责 / 不负责什么

**职责**：**薄**适配器，把外部能力接进本系统。
- `tts-*`：TTS 引擎（如 `tts-macsay`、后续 `tts-qwen3`、`tts-cloud`）
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
