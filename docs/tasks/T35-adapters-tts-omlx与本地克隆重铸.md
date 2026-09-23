# T35 · adapters/tts-omlx：本地神经 TTS 适配器（扩展区）+ 本地克隆重铸

> 来源：`labs/tts-clone-probe/`（2026-09-23 实测：oMLX `:10099` 支持零样本克隆；0.6B 可用 / 1.7B 退化；
> 产出 24 kHz 而项目契约是 16 kHz）+ 人工评估（选定对照套件的 B 整句 / D 16k 契约版音色）。
> 定位：**扩展区新增，不动内核**；克隆重铸属**本地构建**（产物落 `packs/*_build/`，`.gitignore` 已覆盖）。

## 一、目标

1. **新增 `adapters/tts_omlx/`**（薄适配器，接口与 `adapters/tts_macsay` 同形）：
   - 实现既有 conformance 接口：`name` / `model_version` / `requires_core` / `rate_map` / `rate_value(rate_key)` /
     `synthesize(text, out_path, rate_key)`（另可提供 `voice` / `capabilities`）；
   - 内部 POST oMLX `:10099/v1/audio/speech`（标准库 `urllib`，零第三方依赖）；产出**16 kHz/单声道/16-bit WAV**
     （服务出 24 kHz → 适配器内降采样；ffmpeg 路径以 env `VOX_FFMPEG` 覆盖，缺失即 **fail-closed** 抛 `TtsError`）；
   - 两条路径：
     a) **默认音色**（不发 `ref_audio`，`voice="default"`）——一致性测试与冒烟走这条，**不依赖任何私人录音**；
     b) **零样本克隆**（`VOX_TTS_REF` 参考音频 + `VOX_TTS_REF_TEXT` 转写，缺一即 fail-closed）；
        `voice` 属性取参考音频 sha256 前 8 位（`clone-<sha8>`），`model_version` 形如 `qwen3-tts-0.6b-base-clone`；
   - 模型以 env `VOX_TTS_MODEL` 覆盖，缺省 `Qwen3-TTS-12Hz-0.6B-Base-bf16`（实测可用档；1.7B 档退化，见 §四）。
2. **一致性测试**：`python3 -m unittest discover -s adapters` 全绿（新适配器过既有 conformance 套件 + 新测试）。
3. **本地克隆重铸（不入仓）**：`sh bin/vox pack build packs/heat_kefu --adapter adapters.tts_omlx:OmlxTts --out packs/heat_kefu_build/heat-kefu-clone`
   rc 0；资产 key 集合与源包一致；抽 3 条资产 ASR 回读与原文一致；每类资产 `runtime.audio.read_wav` 能读。
4. **登记**：`docs/13 §五` 追加一条（神经 TTS 接入的两处已知问题）+ §一 计数行同步。
5. **选型备忘**：`mlx-audio` 直跑 + 音色向量缓存的可行性与代价（**只侦察与结论，不安装任何包**），落
   `labs/tts-clone-probe/README.md`。

## 二、文件白名单

- 新增：`adapters/tts_omlx/__init__.py`、`adapters/tts_omlx/adapter.py`、`adapters/tts_omlx/tests/**`、`docs/tasks/T35-*.md`（本卡）
- 修改：`docs/13-未完成清单.md`（§五 + §一）、`adapters/AGENTS.md`（仅当需要登记行数豁免）、`labs/tts-clone-probe/README.md`（选型备忘）
- 构建产物（**不提交**）：`packs/heat_kefu_build/heat-kefu-clone/**`

## 三、禁止

- 不动冻结区（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）；
- **不改 `packs/heat_kefu/` 包源**（含 `pack.json` 的 `voice`/`model_version`）——克隆音色的包身份**不入公开仓**；
- **任何录音、参考音频、克隆音频不入仓**；仓内文本不得出现本机绝对路径与用户名（参考音频只经 env 传入）；
- 不 `git add`、不 commit、不 push。

## 四、验收标准

1. `python3 -m unittest discover -s adapters` 全绿；新适配器**可执行行数 ≤150**（超限须在 `adapters/AGENTS.md §⑨` 登记豁免）；
2. **冒烟（默认音色，无需参考）**：合成一句 → 16 kHz/单声道/16-bit WAV，且 `runtime.audio.read_wav` 能读；
3. **克隆路径（本地）**：env 就位时重铸 rc 0、资产 key 集合一致、抽 3 条 ASR 回读一致；
   `rate` 档实测（slow/normal/fast 三次比较时长）：若服务端 `speed` 无效则只声明 `normal` 并在卡内如实登记；
4. `docs/13 §五` 新条目含**证据**（实测数字 + 复现命令），且不泄露参考路径；
5. `git status -uall` 只出现白名单路径（无音频、无参考、无绝对路径）。

## 五、回滚

删 `adapters/tts_omlx/` 与 `packs/heat_kefu_build/heat-kefu-clone/` 即可；冻结区与包源零改动。

## 六、依赖

- 本机 oMLX `:10099` 可用（`/v1/audio/speech`、`/v1/audio/transcriptions`）；
- `ffmpeg` 可用（`/opt/homebrew/bin/ffmpeg` 实测；env 可覆盖）；
- `labs/tts-clone-probe/`：接口实测、坑与延迟数字（`README.md` §一/§二/§五）。

## 七、已知问题（本卡登记的，不修）

| # | 问题 | 证据 |
|---|---|---|
| 1 | `Qwen3-TTS-12Hz-1.7B-Base-8bit` 产出退化 | 同参考同参数下 ASR 回读乱码、把 `ref_text` 混入输出；中位 F0 272.7 Hz vs 参考 192.0 Hz |
| 2 | 流式产物是**无界 WAV**（`RIFF…ffffffff`） | `ffprobe`/播放器正常，`runtime.audio.read_wav` 会拒；适配器暂只走非流式 |
