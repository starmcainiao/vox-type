# tts-clone-probe · 本地神经 TTS 音色克隆探针（labs 一次性实验区）

> 为什么有它：盲测套件暴露了「引擎是 macOS `say`（共振峰合成）→ 机械感」这件事；
> 本目录验证「换成本地神经 TTS + 音色克隆」这条路**在本机能不能走通、代价多大**，并产出供人评估的对照套件。
> 定位：`labs/` 实验区，**不进任何层契约**；**参考音频与全部产物一律落仓外**（本仓是公开预备仓，真人录音永不入库）。

## 一、接口实测（2026-09-23，oMLX `127.0.0.1:10099`）

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `POST /v1/audio/speech` | TTS / 零样本克隆 | `model`、`input`、**`ref_audio`（base64，不是路径）**、`ref_text`、`voice`、`instructions`、`speed`、`response_format=wav`、`stream` |
| `POST /v1/audio/transcriptions` | ASR（出 `ref_text` 用；项目 readback 已在用） | `file`（multipart）、`model`（`Qwen3-ASR-0.6B-8bit` / `mlx-community:VibeVoice-ASR-4bit`） |
| `GET /v1/audio/voices?model=` | 音色清单 | 当前 Base 模型返回 `[]`（**无预置音色名**；音色靠 `ref_audio` 克隆或 VoiceDesign 类模型） |

可用 TTS 模型（`/v1/models` 实测）：`Qwen3-TTS-12Hz-0.6B-Base-bf16`、`mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit`、
`Voxtral-4B-TTS-2603-mlx-4bit`、`mlx-community--Breeze-TTS-2-mlx`、`openbmb:VoxCPM2`。
（Base 档 = 克隆变体；另需音色设计时看 VoiceStudio 缓存里的 `Qwen3-TTS-VoiceDesign-1.7B`。）

**延迟实测**（关键：决定「预铸 vs 实时」）：0.6B 冷启动 68 s（模型加载）、**热 3.86 s 出 4.80 s 音频（≈0.8× 实时）**；
1.7B 首次 88 s。对照预铸命中路径**首字节 0.1 ms**（T24）——神经音色现场生成是「秒级/句」，预铸把它提前到离线。

**输出格式**：24 kHz / 16-bit / mono WAV —— **不是**项目契约的 16 kHz（`runtime/audio.py: SAMPLE_RATE = 16000`，`read_wav` 硬校验）。

## 二、踩过的坑（复用请照做）

1. **`ref_audio` 必须 base64**：传文件路径直接 400 `Invalid base64 encoding in 'ref_audio' field`。
2. **无参考音频时必须显式给 `voice`**（如 `"default"`）：只给 `model`+`input` 会 400。
3. **`ref_text` 质量直接决定克隆**：小 ASR（`Qwen3-ASR-0.6B-8bit`）会把英文技术词转糊
   （实测「Unstructured.io」→「OnStructure.io」），**换 `VibeVoice-ASR-4bit` 明显更准**——先用它出 `ref_text`。
4. **挑参考段先验证再切**：`ffmpeg silencedetect` 会把**音乐/垫音**当语音（实测 30–40 s 段切出来全是「嗯嗯」）；
   先用 ASR 转写验证该段是真人语音，再切 8–10 s 连续语音。
5. **模型档位差异极大（本机实测，同一参考 + 同一 `ref_text`，对照组条目 1）**：

   | 条件 | ASR 回读 | 中位 F0 |
   |---|---|---|
   | 参考原声（8.6 s） | 「Unstructured.io 是全能的…」基本正确 | **192.0 Hz** |
   | A 现状 macOS say / Tingting | 完全正确 | 254.0 Hz（与参考差 62 Hz） |
   | B 克隆·整句（0.6B） | 正确（仅一处停顿位置偏） | **196.7 Hz**（跟得住参考） |
   | C 克隆·逐句 + 16k 拼接 | 正确（多一个虚字） | 200.0 Hz |
   | D 克隆·16k 契约版 | 同 B | 197.5 Hz |
   | E 克隆·语速 1.15 | 正确（多一个虚字） | 184.6 Hz |
   | F 克隆·1.7B-8bit | **乱码**（还把 `ref_text` 混进输出） | **272.7 Hz**（偏离 81 Hz） |

   结论：**0.6B 可用、1.7B 退化——大不等于好，换模型前必须逐条 ASR 回读验证**。
   （F0 只是粗代理，工具 `f0_proxy.py`；音色好坏一律以人听为准。）
6. **音质契约是 16 kHz**：即便神经 TTS 出 24 kHz，现有资产契约（冻结区 `SAMPLE_RATE`）会把它降到电话音质；
   要保住音色就得动冻结区（对照套件里的 D 条件就是为量这个代价而存在）；
   且 `runtime.audio.read_wav` **明确拒绝重采样**（fail-closed），真流水线必须在预铸端先降采样。

## 三、复现

```sh
# 0) 参考音频（仓外）：任意 8–10 s 干净语音 WAV
ffmpeg -ss <起> -t <长> -i <源.m4a> -ac 1 -ar 24000 -c:a pcm_s16le -y ~/vox-naturalness/clone-probe/ref.wav

# 1) 出 ref_text（更强 ASR）
python3 labs/tts-clone-probe/gen.py --transcribe ~/vox-naturalness/clone-probe/ref.wav \
    --asr-model mlx-community:VibeVoice-ASR-4bit

# 2) 单条克隆
python3 labs/tts-clone-probe/gen.py --ref-audio …/ref.wav --ref-text "…" \
    --text "您好，我是供热智能客服小暖。" --out …/clone-test.wav

# 3) 整套对照（A 现状 / B 克隆整句 / C 克隆拆句拼接 / D 16kHz 契约版 / E 语速 1.15 / F 0.6B 小模型）
python3 labs/tts-clone-probe/build_comparison.py --ref-audio …/ref.wav --ref-text "…" \
    --out-dir ~/vox-naturalness/clone-probe/comparison
open ~/vox-naturalness/clone-probe/comparison/index.html
```

## 五、实时性实测（2026-09-23，回答「能不能实时对话 / 是不是内存不够」）

**同句同模型（0.6B）背靠背对照**：

| 条件 | 总耗时 | 音频时长 | 相对实时 |
|---|---|---|---|
| 不带参考（默认音色） | 2.60 / 1.91 s | 3.04 / 2.32 s | **1.17× / 1.21×（比实时快）** |
| 带 8.6 s 参考（克隆） | 8.19 / 10.75 s | 2.40 / 3.28 s | 0.29× / 0.31× |
| 带 3.0 s 参考（克隆） | 7.69 / 7.23 s | 2.48 / 2.40 s | ~0.32× |

→ **克隆比默认音色每次多花约 5 秒，且缩短参考只省 1–3 秒**（开销不随参考长度线性 → 是条件化本身，不是"重编码长音频"）。
oMLX 侧**没有音色注册/缓存端点**（`/v1/audio/voices` 只读、`/v1/audio/process` 是通用处理）→ 每次调用重付。

**流式（`stream: true`）**：首字节 **0.61 / 0.77 / 0.87 s**（三次稳定），产物是**无界 WAV**（`RIFF…ffffffff`，
Python `wave` 模块解析不了，`ffprobe`/播放器正常；curl 会报 `transfer closed…`）。持续产出速率 ≈ 0.3–0.5× 实时
（克隆下），**播放会中途断粮**；不带参考的 1.2× 实时档则能连续。

**内存事实（24 GB 机器）**：oMLX 当前驻留 4.8 GB、内存压缩器 1.5 GB、已换出 1.8 GB；`loaded_count` 实测 4→3 波动
（模型互相挤）；加载速率 **29.9 s/GB** → 0.6B（驻留 1.79 GB）冷加载约 **68 s**（实测有一次 88 s 的调用）。
→ **内存决定「能同时常驻几个模型」（TTS 被挤出就每次白等一分钟），每句慢的主因是算力（参考条件化），加内存不会变快。**

**结论（三条路，按代价排序）**：
1. **固定话术走预铸**——把 B/D 音色铸进包，命中路径 0.1 ms，这一段**已经是实时**；
2. **变化话术**要实时，得让克隆摆脱「每次重付参考条件化」：绕开 oMLX 用 `mlx-audio` 在进程内跑并**缓存音色向量**，
   或改用自带 speaker-embedding 缓存的 TTS 档（VoiceStudio 缓存里的 Chatterbox 一类）；
3. 系统侧：**pin 住 TTS 模型** + 少同时驻留大模型；ASR 侧同样考虑流式。

## 六、mlx-audio 直跑与音色缓存：选型备忘（只侦察，未安装）

> 侦察口径：**未安装任何包、未改构建、未 git add/commit**；除一次误触启动 VoiceStudio 后端（已 kill，见 §六.1 注）外，
> 全程只读。结论一律标【实测】/【推断】。凡涉及本机位置一律写「仓外」，不含绝对路径与用户名。

### 六.1 本机有没有现成可跑的 mlx-audio？——**有，但只在一处**【实测】

**系统侧：全无。** `import mlx_audio` 在 5 个解释器上全部 `ModuleNotFoundError`
（`which -a python3` → `/opt/homebrew/bin/python3`(3.14.6)、`/usr/bin/python3`、`/usr/local/bin/python3`、
`/Library/Frameworks/Python.framework/.../python3`(3.13)、`~/miniforge3/bin/python`(3.12.11)）；
`~/miniforge3` 只有 base 环境（`conda env list` 一行 base，`envs/` 空目录），`pip list | grep -i mlx` 无命中。
`mlx` / `mlx_lm` 在系统解释器上同样 `ModuleNotFoundError`。

**唯一可用运行时：仓外 VoiceStudio 的 `.venv`**（仓外 `VoiceStudio/project/.venv`，1.9 GB）：

```
$ .venv/bin/python -c "import mlx_audio; print(mlx_audio.__file__)"
mlx_audio OK -> .../site-packages/mlx_audio/__init__.py
```
`pip list` 命中：`mlx-audio 0.5.0`、`mlx 0.32.1`、`mlx-metal 0.32.1`、`mlx-whisper 0.4.3`、
`parakeet-mlx 0.5.2`、`kittentts 0.8.1`、`openai 3.3.1`、`omnivoice 0.5.1`。
该 venv 的 python 是 uv 托管的 cpython-3.11.15（`uv-python/`，63 MB，仓外）。

**VoiceStudio 包内的两个东西是什么形态【实测】**：
- `Contents/MacOS/uv` = **Mach-O 64-bit arm64 单体二进制，47.8 MB**，`--version` → `uv 0.11.7`，
  完整子命令（run/sync/tool/pip/venv/cache…）；本机 `~/.local/bin/uv` 是 `0.11.9`，**能力相同且更新**，不需要复用包内的。
- `Contents/Resources/_up_/_up_/` = **不是压缩归档，是解压后的明文源码树**（37 MB）：`backend/`、`frontend/`、
  `omnivoice/`、`pyproject.toml`、`uv.lock`(1.4 MB)、`CHANGELOG.md`。`file` 判定为 UTF-8 文本。
  应用启动时把它 `Synced` 到仓外工作目录（日志原文：`Synced omnivoice/ from bundle`、`Synced backend/ from bundle`），
  即**每次启动都会覆盖外部工作目录**。

**能否被外部复用？** 结论【实测 + 推断】：
- **可复用**：那个 venv 本身（1.9 GB、依赖齐全、python 3.11.15）。仓外已有 Qwen3-TTS 的 MLX 权重可用：
  `Qwen3-TTS-12Hz-0.6B-Base-bf16`(2.3 GB)、`1.7B-Base-8bit`(2.9 GB)、`1.7B-Base-bf16`(8.5 GB)、
  `1.7B-VoiceDesign-4bit`(2.2 GB)。
- **不可当作依赖复用**：`_up_` 是**易失的镜像**——应用一启动就用包内版本覆盖外部副本，外部改的东西下次启动就没了。
- **注意**：包内**没有 Python 解释器**（`find VoiceStudio.app -name "python*"` 零命中），
  解释器在包**外**的 `uv-python/`；且 `omnivoice` 是以 editable 装的
  （指向仓外工作目录），意味着**源码树与 venv 有绑定关系**。
- **附带发现**：仓外 VoiceStudio 自带一个 `speaker_clone` / 「pooled speaker clones」/
  `resolve_consistent_ref` 的服务端克隆管线（`backend/api/routers/dub_generate.py`、`services/speaker_clone.py`）——
  那是**配音流水线**（按 speaker_id 挑参考段），**不是**我们要的「音色向量缓存」，别混。
- **未查到**：`omlx` 包本体不在盘上（`find` 只命中 dmg 与模型目录），**未查到 oMLX 侧公开的音色注册/向量缓存接口**
  （`mlx_audio/server.py` 路由只有 `/v1/models`、`/v1/audio/speech`、`/v1/audio/voices`(只读)、
  `/v1/audio/transcriptions`、`/v1/audio/separations`、`/v1/realtime`、`/v1/audio/transcriptions/realtime`）。

> **注（如实记录）**：侦察中我曾对 `omnivoice-studio -h` 执行了帮助命令，**误触启动了其后端**（拉起 uvicorn 到
> 127.0.0.1:3900 与 sidecar 3902）。已 `pkill` 清理并复验：`curl` 3900 → `http=000`（不可达）、`pgrep` 无残留。
> 副作用：应用把 `omnivoice/ backend/ frontend/` 重新 Sync 到仓外工作目录，并写入 `~/Library/Logs/OmniVoice/`。
> 属仓外、未触碰本仓与任何层契约；本仓无改动。

### 六.2 若要在本机装：成本【实测，未安装】

- `uv 0.11.9` 与 `pip`（系统 `pip 26.1.2` / conda `pip 25.3`）均可用。
- **PyPI 可达**：`curl -sSI --max-time 6 https://pypi.org/simple/mlx-audio/` → `HTTP/2 200`；
  `curl https://github.com` → `HTTP/2 200`。JSON API 可读：`mlx-audio` 最新 **0.5.5**，`requires_python >=3.10`。
- **分发形态**：**只有 sdist，没有 wheel**（`urls` 长度 2，两个都是 `sdist`，共 3.6 MB；sdist `1.7 MB`）。
  →【推断】**首次安装要现场编译 `miniaudio` / `sounddevice` / `scipy` / `numpy`**，不是解压即用。
- **依赖体量（0.5.5 的 `requires_dist` 原文）**：核心 = `huggingface_hub>=1.0`、`miniaudio>=1.61`、
  `mlx>=0.31.1`、`numpy>=1.26.4`、`scipy>=1.10`、`sounddevice>=0.5.3`、`tqdm>=4.67.1`、`transformers>=5.14.0`；
  克隆（ICL）路径还吃 `sentencepiece`。`[stt]`/`[server]`/`[sts]` extra 会再拉 `mlx-lm`、`fastapi`、`uvicorn`、`webrtcvad`。
- **已有缓存可省的部分**：`~/.cache/uv` **2.6 GB**，且**已含 mlx-audio 的构建产物**
  （`wheels-v6/pypi/mlx-audio`、`archive-v0/.../mlx_audio-0.5.3.dist-info`）。
  【推断】走 uv 装 0.5.3 可命中缓存、跳过重编译；但 0.5.5 的锁内容不同，**预计仍会重编译一部分**。
- **总代价【推断，非实测】**：新增体积约 1–2 GB（`transformers`+`scipy`+`numpy`+`huggingface_hub` 是大头），
  时间取决于本机是否有可用的 C/Cython 工具链——**未实测**（本次不装）。权重另计：
  0.6B-Base 2.3 GB、1.7B-Base-8bit 2.9 GB（仓外已有，**复用即可、不必再下**）。
- **内存是更硬的约束**（§五）：0.6B 驻留 1.79 GB、加载 29.9 s/GB。直跑 = 模型常驻本进程，
  换来的是**跳过 oMLX 进程边界**，但不会减少 Metal 算力开销。

### 六.3 关键假设的证据：是「条件化」不是「重编码」；音色能否缓存成向量

**假设成立，且能定位到代码【实测，源码级】。** §五的「3s 参考只省 1–3 秒」指向条件化本身，源码确认了机制：

1. **参考音频被整体送进前缀，前缀参与整段生成**——`_prepare_icl_generation_inputs`
   里 `ref_codes = self.speech_tokenizer.encode(ref_audio)`（**参考的整条波形编码成 16 层 codec**），
   然后 `icl_input_embed = concat(text_with_codec_pad, codec_with_text_pad)`，最后
   `input_embeds = concat([role_embed, combined_prefix, icl_input_embed])`。
   → 参考越长，**prefill 越长**（这是 §五里「缩短参考能省 1–3 秒」的那部分）。
2. **但省不干净，因为还有一次固定的 x-vector 抽取**——同一条路径注释明写
   `# 8. Speaker embedding (ICL still uses x-vector)`，随后
   `speaker_embed = self.extract_speaker_embedding(audio_for_spk)`。
   这是**与参考长度基本无关**的常数项（Res2Net/Resemblyzer 家族 encoder：`TimeDelayNetBlock` +
   `SqueezeExcitationRes2NetBlock` + `AttentiveStatisticsPooling`）。
   → **常数项 = 那 ~5 秒里砍不掉的部分**，正好解释「3s 参考省不掉剩下那几秒」。

**能否缓存成向量？——部分能，但默认入口拿不到【实测】。**

- **已有缓存，但只缓存了 codec，没缓存 x-vector**：`_icl_cache` 的
  `cache_key = (ref_text, (ref_audio.size, float(ref_audio.sum())))`，
  存的是 `(ref_codes, ref_text_ids)`——**省掉参考编码**这一步。
  **`speaker_embedding` 不在缓存键里，且第 746 行无条件重抽**（`extract_speaker_embedding(audio_for_spk)`）。
  → 即使进程内热缓存命中，x-vector 那笔仍每句重付。
- **可缓存的形态已经存在**：`extract_speaker_embedding(audio, sr=24000) -> mx.array`，
  形状 `[1, enc_dim]`（0.6B → `enc_dim: 1024`；1.7B → `enc_dim: 2048`，实测自模型 `config.json`）。
  注入点是 `generate()` 内 `use_icl = ref_audio is not None and ref_text is not None and has_encoder`
  → 走 `_generate_icl`，**不走** `_prepare_generation_inputs` 里那个干净的 `speaker_embed` 形参分支。
- **没有公开注入口**：`generate()` 签名**不接受预计算的 `speaker_embed`**，
  只接受 `ref_audio: Optional[Union[str, mx.array]]` + `ref_text`；`mlx_audio/server.py` 也没有
  「上传音色→返回 embedding」的端点。
  → **【未查到公开接口】要缓存向量，须子类化/猴补丁**（把 `_icl_cache` 扩到同时存 `speaker_embedding`，
  并给 `extract_speaker_embedding` 套一层按音频指纹 memoize），**不是调参数**。
- **可绕开「编码整段参考音频」的另一条路**：`_prepare_generation_inputs`（第 383 行）
  在「有 `ref_audio` 且 `speaker_encoder` 存在」时抽 x-vector，
  再把 `speaker_embed.reshape(1,1,-1)` 拼进 codec 前缀——**这是纯 x-vector 条件化，不做 speech-tokenizer 编码**。
  但当前 `use_icl` 判定会**优先路由到 ICL 路径**，要走到这条干净分支需绕过该判定。【推断：可行性待实测】

**换档：哪些模型自带 speaker-embedding？**【实测，仓库内】
- **Chatterbox**（`mlx_audio/tts/models/chatterbox/`）：`prepare_conditionals(ref_wav, ref_sr) -> Conditionals`，
  其中 `Conditionals(t3: T3Cond, gen: dict)`，`T3Cond` 含 `speaker_emb / cond_prompt_speech_tokens / emotion_adv`，
  `gen` 含 `prompt_token / prompt_feat / embedding` 等。注释明写「**Prepare conditioning from a reference audio clip**」
  —— **条件化被封装成一个可持有、可复用的对象**，天然可缓存。
  代价：`DEC_COND_LEN` 截 10s、`ENC_COND_LEN` 截 6s，且要做 **24k→16k 两次重采样**，
  再跑 `s3gen.embed_ref` + `ve.embeds_from_wavs`（**两套条件器**，比 Qwen3 的单 x-vector 更重）。
- **Qwen3 的 `Base` 变体才带 speaker encoder**（实测 `config.json`）：
  `0.6B-Base-bf16` → `has speaker_encoder_config: True, enc_dim 1024`；
  `1.7B-Base-8bit` / `1.7B-Base-bf16` → `True, enc_dim 2048`；
  而 `1.7B-VoiceDesign-4bit` → **`has speaker_encoder_config: False`**（与 README §一 的口径一致：Base = 克隆变体）。
- **其他带 encoder 的档**：`zonos2/speaker_encoder.py`、`spark/modules/speaker/speaker_encoder.py`
  （`SpeakerEncoder.get_indices(mels) -> codes`）、`pocket_tts/conditioners.py`。
  【未实测】它们的端到端克隆质量与延迟未验证。

### 六.4 结论：下一步最小实验

**目标：把克隆档从 0.3× 实时拉回 ≥1.0× 实时**（当前基线见 §五：带参考 0.29×/0.31×、3s 参考 ~0.32×；
不带参考 1.17×/1.21×——**天花板已存在，只是被参考条件化挡在门外**）。

最小实验（复用仓外现成 venv 与权重，**不新建环境**；先只读 spike，确认后再决定是否落库）：

1. **基线对齐**：用仓外 VoiceStudio venv + `mlx_audio 0.5.0` 直接进程内加载
   `Qwen3-TTS-12Hz-0.6B-Base-bf16`（仓外 2.3 GB，勿再下载），
   同句 + 同 8.6s 参考，量出进程内首句/次句延迟。
   *成功判据：次句延迟 ≈ oMLX 热路径的 8.19 s（若明显更低，说明进程边界本身有开销，值得继续）。*
2. **给 x-vector 加缓存**：按音频指纹 memoize `extract_speaker_embedding`，
   把 `speaker_embedding` 一并塞进 `_icl_cache` 的值（现为 `(ref_codes, ref_text_ids)` 二元组）。
   *成功判据：同一参考的**次句**克隆延迟 ≤ 不带参考基线的 **1.35 s**（2.6 s 基线 + 30% 容差）。*
3. **消融：走纯 x-vector 分支**（`_prepare_generation_inputs` 的 `speaker_embed` 拼接），
   跳过 `speech_tokenizer.encode(ref_audio)`，与第 2 步同条件对照。
   *成功判据：延迟更优**且** ASR 回读 + F0 代理（`f0_proxy.py`）不劣于 ICL 路径。*
4. **换档对照**：用 Chatterbox 的 `prepare_conditionals()` 把 `Conditionals` 算一次、之后复用，
   量「条件化一次 + 生成 N 句」的单句摊销。
   *成功判据：单句 ≥ 1.0× 实时，且人听音色不掉（以人听为准，F0 只是粗代理）。*
5. 全程**参考音频与产物落仓外**（本仓是公开预备仓）；本实验只允许写在 `labs/` 下。

**不做这个实验对产品的影响【推断，依据 §五 实测数字】**：
- **固定话术无影响**——预铸命中路径 0.1 ms（T24），这一段已是实时；克隆音色铸进资产包即可。
- **变化话术无解**——现场克隆每句 7.2–10.8 s，比实时慢 3–4 倍；流式也救不了，
  持续产出只有 0.3–0.5× 实时（§五），**播放会中途断粮**。
- 因此「实时对话 + 自定义音色」这个组合，**只能退化成二选一**：
  要么固定音色做到实时（默认音色 1.2× 实时，走预铸），要么自定义音色但放弃实时（改走预铸/预生成）。
- **内存侧不会解这个题**：加内存只能提高同时常驻的模型数（避免 TTS 被挤出后每次白等 68 s），
  每句慢的主因是 Metal 算力上的参考条件化。

## 四、下一步（不在本目录）

1. **写 `adapters/tts_omlx`**（形态照 `adapters/asr_omlx`：HTTP 薄适配器，扩展区，不动内核）；预铸端做 24k→16k 降采样。
2. **差量重铸 `packs/heat_kefu`**（指纹含 `voice`/`model_version`，换引擎必然重铸——正是「差量重铸」的设计用例）；
   顺手把 `variants[]` / `rates[]` 从 1×normal 扩到多套，让固定话术不呆。
3. **用 `labs/naturalness-ab` 复测**：换引擎后 ① 与 ② 不再逐字节相同（神经采样有噪声），② 变成真臂；
   再加一臂「神经 TTS 实时合成」，即可量出**神经音色下「预铸 vs 实时」的自然度差与延迟差**。
4. **（待拍板）音质契约**：要不要把 16 kHz 提到 24 kHz —— 属冻结区改动，须走版本 + 迁移期。
