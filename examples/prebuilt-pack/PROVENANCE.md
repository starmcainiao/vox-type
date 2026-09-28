# examples/prebuilt-pack · 预铸自证包（来源可审计）

> 本目录是一份**已铸好的资产包**：clone 后**不需要任何 TTS 服务、不联网**就能听见
> 「命中即播」。它不是一个可编辑的业务包——**音频与 `manifest.json` 不得手改**。

## 一、包身份

| 字段 | 值 |
|---|---|
| `pack_id` | `heat-kefu-prebuilt-10`（**子集独立身份**，与全量包 `heat-kefu` 不通用、不撞号） |
| `pack_version` / `protocol_version` / `ruleset_version` | `1` / `0.1` / `v1` |
| `voice` | `default`（oMLX **默认音色，非克隆**） |
| `model_version` | `Qwen3-TTS-12Hz-0.6B-Base-bf16`（= `adapters.tts_omlx.OmlxTts.DEFAULT_MODEL`） |
| `created_at` | `2026-09-24T08:04:49+00:00`（以 `manifest.json` 为准） |
| 音频规格 | 16 kHz / 单声道 / 16-bit PCM（`runtime.audio` 契约） |
| 体积 | 音频 784,028 字节（10 个 wav）；整目录 **799,100 字节**（`du -sk` 804 KB，含清单 / 台账 / 本文件），远低于卡的 3 MB 上限 |

### 口径：小模型、非克隆、非 `say`

依卡 §四.1「**不得用 `say` 生成任何入库音频**」，本包**不使用** macOS `say`：

- **引擎**：`adapters.tts_omlx:OmlxTts` → oMLX `Qwen3-TTS-12Hz-0.6B-Base-bf16`。**小模型**。
- **音色**：`voice=default`（生成时 `VOX_TTS_VOICE` 未设，回落 `DEFAULT_VOICE="default"`）。
- **非克隆**：生成时 `VOX_TTS_REF` 与 `VOX_TTS_REF_TEXT` **均未设**（每次构建前显式 `unset`），
  因此 `OmlxTts.voice` 不走 `clone-<sha256>` 分支。
- **数据合规**：全部话术取自 `packs/heat_kefu/phrases.json`，该包为**公开级 · 自造 demo 文案**
  （无录音、无真实会话、无内网地址/token）。

### 为什么零引擎 run 必须带 `--adapter`

`runtime` 的引擎一致性闸在**任何包内查表之前**（`runtime/executor.py:421-427`）：

```python
if (self.pack.voice != self.adapter.voice
        or self.pack.model_version != self.adapter.model_version):
    return _UnitDecision(state=FALLBACK, reason=REASON_ENGINE_MISMATCH, ...)
```

CLI 缺省适配器冻结为 `adapters.tts_macsay:MacSayTts`（`cli/commands/__init__.py:34`，
身份 `Tingting`/`macos-say`），与本包身份不等 → `engine_mismatch` → fail-closed **rc=5**（实测）。

所以零引擎路径的命令**必须**显式带 `--adapter adapters.tts_omlx:OmlxTts`。
**「零引擎」的含义是「不需要任何 TTS 服务」**，不是「不需要 `--adapter`」：
`tts_calls=0` 意味着一次合成都没发生、没有网络请求，端点离线也能跑
（`selftest.sh` 注入死端点实测 rc=3 / PARTIAL——退出码按卡 §三.4 的
`PASS=0 / PARTIAL=3 / FAIL=4` 映射）。
这条闸是本仓「禁止静默换声音」的既有设计，不是缺陷。

## 二、生成环境（2026-09-24，本机 macOS arm64）

- **适配器**：`adapters.tts_omlx:OmlxTts`（OpenAI 兼容 `/v1/audio/speech` 客户端；
  走缺省基址 `http://127.0.0.1:10099`，适配器不写死地址）。
- **模型**：oMLX `Qwen3-TTS-12Hz-0.6B-Base-bf16`。**小模型、非克隆。**
- **环境**：`python3` 3.14.6、`ffmpeg`（`/opt/homebrew/bin/ffmpeg`；服务端 24 kHz →
  适配器内降到 16 kHz / 单声道 / 16-bit）。

### 生成命令（原样）

```sh
# 1) 造子集源包（不落仓，落 /tmp）
rm -rf /tmp/vox_prebuilt_src && mkdir -p /tmp/vox_prebuilt_src
#    从 packs/heat_kefu 一致裁剪 phrases.json / script.json / pack.json（规则见 §四）

# 2) 自校验子集源包（期望 rc=0）
sh bin/vox pack check /tmp/vox_prebuilt_src
#    → pack check: 通过（phrases=10 units=10 pack_validate=0 条） skipped=['key_not_prebaked']

# 3) 铸包（模型引擎；VOX_TTS_REF 不许设）
unset VOX_TTS_REF VOX_TTS_REF_TEXT VOX_TTS_VOICE
sh bin/vox pack build /tmp/vox_prebuilt_src \
    --out examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts
#    → pack build: 通过（total=10 synthesized=10 reused=0 clean=True）
```

`prebake_ledger.json` 记录：`total=10 synthesized=10 reused=0 tts_calls=10 clean=true`，
`pack_verify_issues=[]`。

> 注：构建 stderr 会出现 `[TTS 归一重试] 重试 1/5 … 归一后头静音仍超限`。这是
> `compiler` 的**有界重采**（重采样，不是降级），最终产物 `clean=true`、
> `quality_issues=[]`，属正常留痕。

## 三、10 句清单（含 `examples/plan.json` 的全部 4 个 key）

`★` = `examples/plan.json` 的 key（自证必需）；`farewell` 同时是 `script.json` 的
`terminal_keys`。`text` 逐字取自 `packs/heat_kefu/phrases.json`，未改一字。
`duration_ms` 是**本次采样值**（采样型引擎会漂移，见 §五）。

| # | key | text | `manifest.json` 的 `fingerprint` | `duration_ms`（本次采样） |
|---|---|---|---|---|
| 1 ★ | `opening__1` | 您好，我是供热智能客服小暖。 | `fc8097d3763973fd` | 2784 |
| 2 ★ | `opening__2` | 报修、查账单缴费、供暖政策咨询，都可以直接跟我说，比如“我要报修”或“查一下账单”。 | `84392223402c7a2b` | 8127 |
| 3 ★ | `transfer_ready` | 已为您转接人工坐席，请稍候。 | `299e94b7e394f5f2` | 2777 |
| 4 ★ | `farewell` | 好的，再见，祝您生活愉快！ | `7543de62dc32de91` | 2510 |
| 5 | `work_order_empty` | 暂无工单记录。 | `8ba5edbeb4a9a52c` | 1074 |
| 6 | `clarify_repair__1` | 您是要报修吗？ | `afcbcfbb17fbde7f` | 1152 |
| 7 | `repair_confirm_question__1` | 信息是否正确？ | `a31c484dde72bf9f` | 1389 |
| 8 | `chat_smalltalk__2` | 请问有什么可以帮您？ | `7a1f27a637aabda2` | 1566 |
| 9 | `repair_ask_natural_userNo__1` | 您的户号是多少？ | `ccba9d28fd5da785` | 1363 |
| 10 | `repair_ask_desc` | 请简单描述一下具体情况。 | `f806210d7ac8d416` | 1745 |

**关于「manifest 的 sha256 与音频逐一对应」**：资产格式冻结在 `assets/pack.py`，
清单里的指纹字段就叫 `fingerprint`，取
`assets.fingerprint.fingerprint(text, voice, rate_value, model_version)`——
**文本指纹**（SHA-256 截断 16 位 hex，`assets/fingerprint.py:19`），**不是音频文件哈希**。
`vox verify` 用它逐条重算（`assets/pack.py:773`）判定「清单与内容是否对应」，
这正是本包「不得手改音频或清单」可被机器执行的落地方式。
本包**不登记**音频 sha256：重铸后音频字节会变（见 §五），登记了就是假可复现。核验用：

```sh
sh bin/vox verify examples/prebuilt-pack     # rc=0 → 清单与音频内容逐条一致
```

## 四、裁剪规则（可审计）

子集源包在 `/tmp/vox_prebuilt_src` 生成，**从不落仓**（仓内不得留痕）。`packs/` 一行不改。

1. **`packs/heat_kefu` 无 `trigger.json`**（`find packs/heat_kefu -name 'trigger*'` 为空）——
   状态→计划触发器住在扩展区 `trigger/`，不属于业务包；本卡裁剪只动三份源文件。
2. **`phrases.json`**：保留上表 10 条，逐字不变，`phrases` 数组**保持原包声明顺序**。
3. **`script.json`**：`units` 过滤到同样 10 个 key，数组**保持原剧本顺序**（原剧本即
   `farewell` 收尾），顶层其余字段（`script_version` / `terminal_keys` /
   `live_whitelist` / `max_retry`）原样保留。
   - `terminal_keys == ["farewell"]` 非空且 `units[-1].key == "farewell"` → C1a/C1b 通过；
   - 子集内全是 key 单元、无 `text`/`reason`，故 `live_whitelist` 保持原值也不触发 C5。
4. **`pack.json`**：只改三处身份字段，其余（`pack_version` / `protocol_version` /
   `ruleset_version` / `rates` / `locale` / `duplex`）原样保留：
   - `pack_id`：`heat-kefu` → `heat-kefu-prebuilt-10`（**子集独立身份**；
     若沿用 `heat-kefu`，10 句子集与 69 句全量包将同 `pack_id` + 同 `pack_version`，
     两个不同产物撞同一身份，属静默隐患）；
   - `voice`：`Tingting` → `default`（对齐 oMLX 默认音色）；
   - `model_version`：`macos-say` → `Qwen3-TTS-12Hz-0.6B-Base-bf16`
     （**必须**等于 `OmlxTts.model_version` 的实际值，否则运行时 `engine_mismatch`）。
5. **自校验**：`sh bin/vox pack check /tmp/vox_prebuilt_src` → **rc=0**
   （`phrases=10 units=10 violations=0 pack_validate=0 passed=true`）。
   只裁 `phrases.json` 而漏裁 `script.json`，会立刻报 59 条 `key_not_in_library`（C3b）——
   四属性必须一致，缺一处即响。
6. **语速档**：全 `normal`（原包唯一档），10 条均单分片（`part_index=0`）、`variant=0`。

## 五、可复现口径（明示）

**本包非字节级可复现。** `Qwen3-TTS-12Hz-0.6B-Base-bf16` 是**采样型**神经 TTS 引擎：
同一文本、同一模型、同一音色，两次合成的音频字节**不会相同**；`compiler` 的头静音超限
有界重采也是采样过程。本包在 2026-09-24 一天内重铸过多次，10 条 `fingerprint`
**逐条不变**，而 `duration_ms` 与字节数在毫秒/字节级漂移——这正是本节的实证。

因此：

- **可复现的是结构与指纹，不是波形**。`text` / `voice` / `rate` / `model_version` 四要素
  决定 `fingerprint`（`assets/fingerprint.py:19`），重铸后指纹**逐条相同**。
- **不宣称字节级对比**。重铸验收口径是：`vox verify rc=0` +
  零引擎 `run` 的 `hit=4 miss=0 tts_calls=0` + 10 条指纹逐一相同。
  「重铸前后 `sha256sum` 逐字相同」**不是**本包的验收标准，也不应作为验收标准。
- **引擎/音色一变，指纹全变**（换音色 = 旧资产自动失效，这是设计，不是 bug）。

### 重铸命令

```sh
# 需要：本机 oMLX 起 TTS 服务（Qwen3-TTS-12Hz-0.6B-Base-bf16）+ ffmpeg
# 不要：macOS 的 say（本卡禁止用它生成入库音频）
rm -rf /tmp/vox_prebuilt_src examples/prebuilt-pack && mkdir -p /tmp/vox_prebuilt_src
#   ……按 §四 裁剪规则重建 phrases.json / script.json / pack.json……
sh bin/vox pack check /tmp/vox_prebuilt_src                 # 期望 rc=0
unset VOX_TTS_REF VOX_TTS_REF_TEXT VOX_TTS_VOICE
sh bin/vox pack build /tmp/vox_prebuilt_src \
    --out examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts                     # 期望 total=10 clean=True
sh bin/vox verify examples/prebuilt-pack                    # 期望 rc=0
sh bin/vox run examples/plan.json --pack examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts --out /tmp/r.wav    # 期望 hit=4 miss=0 tts_calls=0
```

重铸后请**同步更新本文件**的 `created_at`、体积与 §三 表格的 `duration_ms`——
它们是本次采样的留痕，不是承诺。换音色：`export VOX_TTS_VOICE=<音色名>`
并同步改子集源包 `pack.json` 的 `voice`，否则 `engine_mismatch`。

## 六、怎么用（不需要 TTS 服务）

```sh
# 听见「命中即播」——不合成任何声音，全部磁盘读，不发网络请求
sh bin/vox run examples/plan.json --pack examples/prebuilt-pack \
    --adapter adapters.tts_omlx:OmlxTts --out /tmp/prebuilt.wav
# → run: 通过（hit=4 miss=0 fallback=0 tts_calls=0）

sh bin/vox verify examples/prebuilt-pack    # 产物可质检，rc=0
```

`--adapter` 必带，理由见 §一。`tts_calls=0` 意味着一次合成都没发生，
**端点离线也能跑**——`examples/selftest.sh` 的 PARTIAL 模式已实测。
