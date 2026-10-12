# T87 · docs：`docs/22 §3` 的"真值输出"与实测不符——改成真值并补引擎身份约束

## 背景（只写必需）

实测（2026-10-12，见 `T84`）：照 `docs/22 §2` 用**模型适配器**铸 `packs/heat_kefu`（rc=0，69/69 clean=True，
实测 97 秒），紧接着照 `§3` 用**同一个适配器** `run` 它 —— **rc=5 `engine_mismatch`**：

```
run: 已按 fail-closed 中止（plan 单元 #1 未命中（key='opening__1'，reason=engine_mismatch））
已按 fail-closed 中止（退出码 5）
```

根因：`packs/heat_kefu/pack.json` 声明 `"voice": "Tingting"` / `"model_version": "macos-say"`，
而 `run` 的引擎一致性闸比的是**包清单声明的身份**与**适配器身份**（`runtime/executor.py`），两者不等即 fail-closed。
`pack build` 目前**不校验**这件事，所以错误延后到 `run` 才爆。

git 考古（`git log -- packs/heat_kefu/pack.json`）：该文件**只有一个提交碰过它**，从创建起就是 `say` 身份；
而 `docs/22 §3` 那段 `rc=0` 的"真值输出"自 2026-09-23 写下后**从未改过**。
⇒ **文档与包源从第一天起就不自洽**，不是回归。

**本卡只做"把文档改成真值 + 把约束写出来"，不改任何数据与代码。**

## 目标（可验收的产物）

- 产物 1：`docs/22-五分钟跑起来.md` 的两处**逐字替换**（见下「具体改法」）

## 允许修改的文件（白名单）

```
允许新增：无
允许修改：docs/22-五分钟跑起来.md
禁止触碰：其他一切文件（尤其不得改 packs/、cli/、runtime/、README.md、AGENTS.md）
```

## 具体改法（**两处逐字替换，不要自由发挥、不要顺带润色别处**）

### 替换一：`## 3. 播一句` 里那段"2 单元最小 plan 实跑真值输出"

**原文（连同代码块一起，逐字匹配）**：

```sh
sh bin/vox run examples/plan.json --pack /tmp/vox-demo/heat-kefu --adapter adapters.tts_omlx:OmlxTts --out /tmp/vox-demo/out.wav
```

**改为**：

```sh
# 路径 A（推荐先走这条，零引擎、不依赖 §2 铸没铸成功）：用仓里预铸好的自证包
sh bin/vox run examples/plan.json --pack examples/prebuilt-pack --adapter adapters.tts_omlx:OmlxTts --out /tmp/vox-demo/out.wav
```

**原文（紧随其后那段"真值输出"代码块，逐字匹配并整体替换）**：

```
run: 通过（hit=2 miss=0 fallback=0 tts_calls=0 first_audio_ms=0.5 out=/tmp/vox-demo/out.wav）
{ "command": "run", "hit_count": 2, "miss_count": 0, "fallback_count": 0,
  "tts_calls": 0, "first_audio_ms": 0.4657…, "total_duration_ms": 11021,
  "out_path": "/tmp/vox-demo/out.wav", "events": [ … ] }
```

**替换为**（这才是 2026-10-12 的实机真值）：

```
run: 通过（hit=4 miss=0 fallback=0 tts_calls=0 first_audio_ms=1.99 out=/tmp/vox-demo/out.wav）
```

**并在该代码块之后，新增下面这一段（逐字照抄，不要改写）**：

```markdown
> ⚠️ **用 §2 铸出来的 `packs/heat_kefu` 包，这里播不出来（rc=5）**——这是实测，不是意外：
>
> ```
> run: 已按 fail-closed 中止（plan 单元 #1 未命中（key='opening__1'，reason=engine_mismatch））
> 已按 fail-closed 中止（退出码 5）
> ```
>
> 原因：`packs/heat_kefu/pack.json` 声明的是 `"voice": "Tingting"` / `"model_version": "macos-say"`，
> 而 `run` 的引擎一致性闸比的是**包清单里声明的身份**与**适配器身份**，两者不等即 fail-closed。
> 该闸在任何包内查表**之前**生效，所以它拦得早、也不写输出文件。
>
> **要用模型引擎铸你自己的包，必须先把 `pack.json` 的 `voice`/`model_version` 改成该引擎的实际身份再铸。**
> `adapters.tts_omlx:OmlxTts` 的身份是 `default` / `mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit`。
> （`examples/prebuilt-pack` 就是按这个身份铸的，所以路径 A 能直接播。）
```

### 替换二：`## 1. 一次性准备` 里的 macOS `say` 脚注

**原文（逐字匹配）**：

```
> **macOS `say` 应急路径（脚注）**：已有 macOS 且暂不接 TTS 端点时，`pack build` 可省略
> `--adapter` 走系统自带 `say`；它只作应急，不作为跨平台主线。
```

**改为**：

```
> **macOS `say` 应急路径（脚注）**：已有 macOS 且暂不接 TTS 端点时，`pack build` 可省略
> `--adapter` 走系统自带 `say`；它只作应急，不作为跨平台主线。
>
> ⚠️ **在较新的 macOS 上，这条路会直接失败（实测 rc=4）**：质检门的静音阈值是按 **macOS 14** 的
> `say` 实测标定的（`compiler/quality.py`），而新系统的 `say` 尾静音更长，会把阈值顶穿 →
> `pack build: 不通过（clean=False … 尾静音超限: 判据=<= 150 ms, 实际=157.2 ms …）`。
> 处置（只放宽阈值、不改契约语义）：
>
> ```sh
> VOX_MAX_TAIL_SILENCE_MS=250 VOX_MAX_HEAD_SILENCE_MS=100 \
>   sh bin/vox pack build packs/demo-brief --out /tmp/vox-demo/demo-brief
> ```
>
> 不设 = 与从前逐字节一致；设了 = 明确接受本机引擎的静音行为。口径见 `docs/06 §6.1.3.1`
> 与 `docs/13 §五#32`。
```

## 禁止事项

- 不得新增第三方依赖
- 不得改动本卡白名单以外的任何文件
- **不得顺手改动别处的措辞、数字或结构**；本卡只允许上面两处替换
- 不得为了让文档"看起来一致"去改 `packs/` 里的任何文件（那是数据，属另一张待拍板的卡）
- 不得删除原有的"退出码"表、`selftest` 小节或任何既有内容

## 验收标准（逐条可判定，验收方逐条核对）

1. `git diff --stat -- docs/22-五分钟跑起来.md` **只显示这一个文件被改**；
2. 文件里**不再出现**字符串 `first_audio_ms=0.5`（旧假值）；
3. 文件里**出现**字符串 `first_audio_ms=1.99`（新真值）与 `reason=engine_mismatch`（约束说明）；
4. 文件里**出现**字符串 `VOX_MAX_TAIL_SILENCE_MS`（阈值处置）；
5. 文件里**出现**字符串 `mlx-community:Qwen3-TTS-12Hz-1.7B-Base-8bit`（引擎实际身份）；
6. **负例（防误改）**：`grep -c '^## ' docs/22-五分钟跑起来.md` 的章节数**与改动前相同**
   （既有的 0–8 共 9 个二级标题一个不少）；
7. 文档内所有相对链接仍指向存在的文件（验收方抽 3 条核对）。

## 反空转条款（每张卡必带，T01 教训）

- 不得只改"看起来相关"的一处就宣称完成——**两处替换都要做**，验收方逐条比对上列字符串；
- **不得编造新的实测数字**：本卡给出的 `1.99`、`rc=5`、`157.2 ms`、`269.2 ms` 都是 2026-10-12 的实测值，
  原文照抄即可；**任何本卡没给的新数字都不得写入**（本仓纪律 D6：数字必须有出处）；
- 改动不得触发 `tools/check_no_machine_paths.py`（不得写入本机用户名/主机名/绝对路径）。

## 回滚方式

`git checkout -- docs/22-五分钟跑起来.md`。

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`。
**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T87-*.md)" --dir （仓库根）`。
本卡是**逐字替换**、判据全是字符串存在性检查，**适合免费档**；走免费档须在报告里点名。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
