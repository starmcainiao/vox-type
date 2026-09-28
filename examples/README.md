# examples · 示例文件

- **`plan.json`** —— 一份播报计划（`vox run` 的输入），4 个 `{"key": …}` 单元，全部落在 `packs/heat_kefu` 已铸资产里（纯命中路径，`tts_calls=0`）。
- **`bench-corpus.json`** —— `vox bench` 的示例语料（`{"units":[{"key","text","rate","variant"}]}`），`text` 逐字取自 `packs/heat_kefu/phrases.json`，与 plan 同一组 key。
- **`prebuilt-pack/`** —— **预铸好的自证包**：10 句 heat_kefu 子集（含 `plan.json` 全部 4 个 key），
  16 kHz/单声道/16-bit，整目录 799,100 字节（其中音频 784,028 字节 / 10 个 wav）。clone 后**零引擎、零 TTS 服务**就能听见「命中即播」。
  来源、10 句清单与重铸口径见 [`prebuilt-pack/PROVENANCE.md`](prebuilt-pack/PROVENANCE.md)。
- **`selftest.sh`** —— 一命令自证（POSIX sh，零第三方依赖）：环境自检 → 串真命令 →
  期望值 vs 实际值逐条 ✓/✗ → 末行 `SELFTEST: PASS|PARTIAL|FAIL`，退出码 `0/3/4`（卡 §三.4 钉死）。

## 30 秒：无引擎先听见「命中即播」

需要 oMLX / TTS 服务 / macOS 的 `say`？**不需要。** 这一步全程磁盘读，一次合成都没有发生：

```sh
sh bin/vox run examples/plan.json \
    --pack examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts \
    --out /tmp/prebuilt.wav
# → run: 通过（hit=4 miss=0 fallback=0 tts_calls=0 first_audio_ms=0.6）

sh bin/vox verify examples/prebuilt-pack     # 产物可质检 → rc=0
```

2026-09-24 实机实测：两条均 rc=0（`hit=4 miss=0 fallback=0 tts_calls=0`，
产物约 17 s 的 16 kHz 单声道 WAV；采样型引擎每次重铸毫秒数会漂移，字节数随之漂移，见 PROVENANCE §五）。

> **`--adapter adapters.tts_omlx:OmlxTts` 必须写**：`runtime` 的引擎一致性闸在**任何包内查表之前**
> （`runtime/executor.py:421-427`），本包清单如实登记真实引擎身份 `default` /
> `Qwen3-TTS-12Hz-0.6B-Base-bf16`（oMLX 小模型，非克隆），而 CLI 缺省适配器冻结为
> `adapters.tts_macsay:MacSayTts`（`Tingting`/`macos-say`，`cli/commands/__init__.py:34`）——
> 身份不等即 `engine_mismatch` → fail-closed（退出码 5），**省略 `--adapter` 实测 rc=5**。
> 「零引擎」的含义是**不需要任何 TTS 服务**，不是「不需要 `--adapter`」：
> `tts_calls=0` 意味着一次合成都没有发生、没有网络请求，端点离线也能跑。
> 完整推导（以及「为什么不能把清单改成 macsay 身份」）见
> [`prebuilt-pack/PROVENANCE.md`](prebuilt-pack/PROVENANCE.md)。

## 一命令自证

```sh
sh examples/selftest.sh
```

它依次做三件事：

1. **环境自检** —— Python ≥3.12、`ffmpeg`、macOS `say`、TTS 端点（`VOX_TTS_ENDPOINT`
   可达性）、`VOX_TTS_REF` 是否未设，并打印「你现在能走哪条路」；
2. **串真命令** —— `pack check packs/heat_kefu`（永远可跑）→ `verify examples/prebuilt-pack`
   → 零引擎 `vox run`（本卡核心自证）→ 有引擎则再 `pack build` 小包 + `bench`；
3. **期望 vs 实际** —— 每步打印期望值与实际值，逐条 ✓/✗。零引擎 `vox run` 要求
   `hit=4 miss=0 fallback=0 tts_calls=0`（带 `--adapter adapters.tts_omlx:OmlxTts`）。

末行结论与退出码：

| 结论 | 退出码 | 含义 |
|---|---|---|
| `SELFTEST: PASS` | `0` | 零引擎路径 + 完整链路全绿 |
| `SELFTEST: PARTIAL (…)` | `3` | 缺引擎（端点不可达 / 缺 ffmpeg），**但零引擎路径仍全部通过**；会打印要跑完整链路还缺什么 |
| `SELFTEST: FAIL (…)` | `4` | 有硬门没过（标 ✗ 的每一条都是） |

缺引擎**不等于 FAIL**——听见「命中即播」这一步不依赖任何 TTS 服务。
脚本不写任何临时文件到仓库内（全落 `/tmp`，退出时清理），也不联网（只做 TCP 探活）。

## 重铸自证包

自证包是**采样型**引擎的产物，**非字节级可复现**（同一文本两次合成，波形不同）。
可复现的是结构与指纹，验收口径是 `verify rc=0` + `hit=4 miss=0 tts_calls=0`，
**不是** `sha256sum` 逐字对比。完整口径、10 句清单与裁剪规则见
[`prebuilt-pack/PROVENANCE.md`](prebuilt-pack/PROVENANCE.md)。

```sh
# 需要：本机 oMLX 起 TTS 服务（Qwen3-TTS-12Hz-0.6B-Base-bf16）+ ffmpeg
# 不要：macOS 的 say（本卡禁止用它生成入库音频）、VOX_TTS_REF（本包口径禁用克隆）
rm -rf /tmp/vox_prebuilt_src examples/prebuilt-pack && mkdir -p /tmp/vox_prebuilt_src
#   ……按 PROVENANCE.md 的裁剪规则重建 phrases.json / script.json / pack.json……
sh bin/vox pack check /tmp/vox_prebuilt_src      # 期望 rc=0
unset VOX_TTS_REF VOX_TTS_REF_TEXT VOX_TTS_VOICE
sh bin/vox pack build /tmp/vox_prebuilt_src \
    --out examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts          # 期望 total=10 synthesized=10 clean=True
sh bin/vox verify examples/prebuilt-pack          # 期望 rc=0
sh bin/vox run examples/plan.json --pack examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts --out /tmp/r.wav
# 期望 hit=4 miss=0 fallback=0 tts_calls=0
```

子集源包**不落仓**（落 `/tmp`，用完即弃）。`packs/` 与冻区一行不改。

## 需要引擎的路径（完整链路）

逐条可跑命令（2026-09-24 实机实测，三条均 rc=0；macOS 缺省引擎 `say`）：

```sh
rm -rf /tmp/vox_t42_ex && mkdir -p /tmp/vox_t42_ex
sh bin/vox pack build packs/heat_kefu --out /tmp/vox_t42_ex/heat-kefu   # total=69 synthesized=69 clean=true passed=true
sh bin/vox run examples/plan.json --pack /tmp/vox_t42_ex/heat-kefu --out /tmp/vox_t42_ex/out.wav   # hit=4 miss=0 tts_calls=0
sh bin/vox bench /tmp/vox_t42_ex/heat-kefu --corpus examples/bench-corpus.json --out /tmp/vox_t42_ex/bench   # observed_hit_rate=1.0 gate passed
```

> `bench` 的 `--repeats` 下限是 `MIN_REPEATS=20`（`eval/stats.py`），低于它直接 rc=2。

> 对拍用替身合成器，`bench` 报告里的毫秒数**不得对外引用**（只比命中结构，不看时序）；`timing_metrics_meaningful=false`。

## 演示音频怎么来的

[`docs/media/demo.wav`](../docs/media/demo.wav) 就是 `vox run` 的原样产物——没有剪过、没有加音效、
没有二次转码：16k 采样 / 单声道 / 16-bit，271986 帧，16.999 s，是本 plan 4 个单元的
**命中即播拼接**（`hit=4 miss=0 tts_calls=0`，全是磁盘读，一次合成都没发生）。

来源包：`packs/heat_kefu`，**公开级 · 自造 demo 文案**——无录音、无真实会话、无内网地址/token
（分级与逐条声明见 [`packs/heat_kefu/admission.md`](../packs/heat_kefu/admission.md) 的数据分级一节；
其中 FAQ 条目标注「演示样例」）。本次实机跑出的音频**只含自造 demo 文案**，这一条是音频进仓的前提
（经用户拍板）。

复现（2026-09-24 实机实测，三条 rc 依次为 0 / 0 / 0；macOS 缺省引擎 `say`，
非 macOS 见 [`docs/22`](../docs/22-五分钟跑起来.md) 的模型 TTS 写法）：

```sh
rm -rf /tmp/vox_t42_media && mkdir -p /tmp/vox_t42_media
sh bin/vox pack build packs/heat_kefu --out /tmp/vox_t42_media/heat-kefu   # total=69 synthesized=69 clean=true passed=true
sh bin/vox run examples/plan.json --pack /tmp/vox_t42_media/heat-kefu --out /tmp/vox_t42_media/demo.wav   # hit=4 miss=0 tts_calls=0 first_audio_ms=0.8
cp /tmp/vox_t42_media/demo.wav docs/media/demo.wav
```

绝对时间戳落在 `run` 的 `events[].ts` 里，不写入音频字节。
产物哈希 `23a077c8…7e0675` 是 2026-09-24 那次 `say` 合成的快照，**不是**可复现承诺：
69 key 全部重合成而非缓存复用，同一引擎两次跑的字节并不相同；验收看
`hit=4 miss=0 tts_calls=0` 与指纹，不看 `sha256sum`。

音频要进库必须靠 `.gitignore` 的否定规则：`!docs/**/*.wav` 放行 `docs/media/`，
T45 起新增 `!examples/prebuilt-pack/**/*.wav` 放行自证包——两条是全部例外，
其余 `*.wav` 一律不进库。用 `git check-ignore <路径>` 返回 **1** 判定未被忽略。
