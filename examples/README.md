# examples · 示例文件

- **`plan.json`** —— 一份播报计划（`vox run` 的输入），4 个 `{"key": …}` 单元，全部落在 `packs/heat_kefu` 已铸资产里（纯命中路径，`tts_calls=0`）。
- **`bench-corpus.json`** —— `vox bench` 的示例语料（`{"units":[{"key","text","rate","variant"}]}`），`text` 逐字取自 `packs/heat_kefu/phrases.json`，与 plan 同一组 key。
- 逐条可跑命令（2026-09-24 实机实测，三条均 rc=0；macOS 缺省引擎 `say`）：

```sh
rm -rf /tmp/vox_t42_ex && mkdir -p /tmp/vox_t42_ex
sh bin/vox pack build packs/heat_kefu --out /tmp/vox_t42_ex/heat-kefu   # total=69 synthesized=69 clean=true passed=true
sh bin/vox run examples/plan.json --pack /tmp/vox_t42_ex/heat-kefu --out /tmp/vox_t42_ex/out.wav   # hit=4 miss=0 tts_calls=0
sh bin/vox bench /tmp/vox_t42_ex/heat-kefu --corpus examples/bench-corpus.json --out /tmp/vox_t42_ex/bench   # observed_hit_rate=1.0 gate passed
```

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

产物 sha256 `23a077c8…7e0675`（69 key 全部重合成，非缓存复用，故逐次可复现地得到同一内容；
绝对时间戳落在 `run` 的 `events[].ts` 里，不写入音频字节）。仓库的 `.gitignore` 只放行
`!docs/**/*.wav`，所以音频落在 `docs/media/` 才进得了库（`git check-ignore` 对
`docs/media/demo.wav` 返回 0，即**未被忽略**）。
