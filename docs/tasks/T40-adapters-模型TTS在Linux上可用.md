# T40 · 让「模型 TTS」在 Linux/服务器上可用（去 macOS `say` 依赖）

> 层：`adapters/`（扩展区）+ 文档（非冻结区）
> 起因：用户 2026-09-23 拍板方向——「macOS 的 `say` 现在不合适，用模型来产生更合适，因为大部分服务器都 Linux」
> 性质：**可用性缺陷**（不是新功能）：预铸的缺省引擎绑死 macOS，服务器上第一条命令就失败

## 一、事实（实测）

1. 缺省引擎 `DEFAULT_ADAPTER_SPEC = "adapters.tts_macsay:MacSayTts"`（`cli/commands/__init__.py:34`），
   在 Linux 上第一次 `vox pack build` 即以 `say 命令不可用: [Errno 2]` 失败；
2. **但模型路径其实早已存在**：`adapters/tts_omlx/adapter.py:31` 的 `SPEECH_ENDPOINT = "/v1/audio/speech"`
   ——**它本来就是「OpenAI 兼容 speech 客户端」**，只是被指向了本机 oMLX（macOS 应用）。
   同一个协议在 Linux 上是 vLLM-Omni / SGLang-Omni / 任意云服务都在说的。
3. **真正的堵点有两个**（都在 `OmlxTts.__init__`）：
   - `base_url: str = BASE_URL` —— **没有 env 兜底**，而 CLI 的 `--adapter` 只传「模块:类名」、
     传不进构造参数 → Linux 用户**无法把地址指到自己的服务**；
   - 音色只有 `DEFAULT_VOICE = "default"` / 克隆前缀 ——**没有 `VOX_TTS_VOICE`**，
     服务端命名的音色选不了。
   （`model` / `ffmpeg` 有 env 兜底，是既有正确先例：`VOX_TTS_MODEL` / `VOX_FFMPEG`。）
4. **行数预算极紧**：该文件现 **146 可执行行**，`adapters/AGENTS.md §②` 阈值 150 → **只有 4 行余量**
   （T35 验收时用 `tools/structure_budget/check.py` 判定过）。

## 二、目标

**让模型 TTS 这条路径在没有 macOS、没有 `say` 的机器上可用**，且不引入静默换引擎。

具体：把 `OmlxTts` 从「oMLX 专用」正名为「**OpenAI 兼容 `/v1/audio/speech` 客户端**」，
并把 **基址 / 音色** 接上 env（与既有 `VOX_TTS_*` 家族同名同风格）。

**不改变**：协议（`/v1/audio/speech`）、16k 契约、克隆语义、fail-closed 纪律、重试上限。

## 三、产物路径

| 文件 | 改什么 |
|---|---|
| `adapters/tts_omlx/adapter.py` | `base_url` 补 `VOX_TTS_ENDPOINT` 兜底；音色补 `VOX_TTS_VOICE` 兜底；docstring 说清「OpenAI 兼容客户端，可指向 oMLX / vLLM-Omni / SGLang-Omni / 云服务」 |
| `adapters/tts_omlx/tests/test_adapter.py` | 新增：env 兜底生效 / env 缺省时仍回落常量 / 非法 env（非 http）仍抛 `ValueError` |
| `adapters/AGENTS.md` | 登记定位（消除「叫 omlx 但不是 omlx 专用」的认知债）+ 记 §⑨.1 式豁免若变动 |
| `README.md` | Quick Start 的「两个前提」改写：Linux/服务器怎么指端点（一条命令） |
| `docs/22-五分钟跑起来.md` | §1 的警告改写 + §5 缺口行更新（那两行要跟着现实走） |

## 四、文件白名单（越界即退）

```
adapters/tts_omlx/adapter.py
adapters/tts_omlx/tests/test_adapter.py
adapters/AGENTS.md
README.md
docs/22-五分钟跑起来.md
```

## 五、禁止事项

1. **不动冻结区**（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）——包括
   `cli/commands/__init__.py` 的 `DEFAULT_ADAPTER_SPEC`（**见 §九，那是另一批**）；
2. **不新增第三方依赖**（纯标准库）；
3. **不得超过结构预算**：改后 `python3 tools/structure_budget/check.py` 必须仍判「全部合规」；
   若 4 行余量不够，**停下上报**，不得自行放宽阈值或加豁免；
4. **不改协议与语义**：端点路径、16k 契约、克隆前缀、`rate_map`、重试上限一律不动；
5. **不允许静默换引擎**：env 缺失时回落常量（有序、可预期、可测），**不得**回落成另一个引擎；
6. 不 `git add` / 不提交 / 不推送。

## 六、验收标准

1. **env 兜底真的生效**：`VOX_TTS_ENDPOINT=http://127.0.0.1:10099` + `VOX_TTS_VOICE=<某音色>` 时，
   `OmlxTts().base_url` / `.voice` 取到 env 值；不设 env 时取常量（`http://127.0.0.1:10099`）；
2. **非法 env 仍 fail-closed**：`VOX_TTS_ENDPOINT=not-a-url` → 抛 `ValueError`（不得静默回落）；
3. **真机端到端（本机能做的最强证据）**：用 oMLX 端点铸一个**小包**成功，且产物 manifest 的
   `model_version` 证明走的是**模型引擎**（不是 `macos-say`）——即「无 `say` 也能铸」这条路径成立；
4. **零回归 + 双档**：`adapters` 根在 **3.14 与 3.12** 下均全绿（3.12 用 `~/miniforge3/bin/python3`）；
5. **结构预算**：`python3 tools/structure_budget/check.py` 退出 0（`adapters` 超限数不得增加）；
6. **注入/反空转**：把 env 兜底那行改回常量 → 第 1 条断言必须判红（证明测试不是空转）；
7. **文档与现实一致**：README 与 `docs/22` 里的 Linux 命令**逐字照抄能跑通**（工作流内实跑一次）。

## 七、回滚方式

`git checkout -- adapters/tts_omlx/ README.md docs/22-五分钟跑起来.md adapters/AGENTS.md`
（纯源码 + 文档改动，无产物迁移）。

## 八、依赖

无前置卡。**它不改缺省引擎**——「把缺省从 `say` 换掉」见 §九，需用户开批。

## 九、已知问题登记（不在本卡范围）

- **缺省引擎仍是 `say`**（`cli/commands/__init__.py:34`，cli/ 冻结区）。本卡交付后，Linux 用户仍需
  显式写 `--adapter adapters.tts_omlx:OmlxTts`。**要免掉这个 flag 得改冻结区**，建议的解析顺序：
  `VOX_TTS_ADAPTER` env → 有 `VOX_TTS_ENDPOINT` 则用该适配器 → 否则 `say`；
  且**必须把选中的引擎打印出来**（否则就是静默换引擎，撞本仓红线）。**待开批。**
- **本机无 Linux，无法在真 Linux 上实跑**：本卡的证据是「协议同源 + 本机用模型引擎铸包成功 +
  路径不依赖 `say`」，**不等于「已在 Linux 验证」**——对外必须如实这么说。
- `adapters/tts_omlx/` 的目录名仍是 omlx（改名会破坏 T35 的验收记录）——本卡只改文档定位，不改路径。

## 十、验收记录

**验收方：AI 会话（2026-09-23）**。**披露**：本卡经工作流（`dwfrun-2e471ec6`）执行到一半被用户暂停、
随后工作流工具掉线，**余下部分由验收方直接做完**；独立复核由 `blackiron-silent-failure-hunter` 只读完成。

| 判据 | 结果 | 证据 |
|---|---|---|
| 1 env 兜底生效 | ✅ | 测试 4 条；`VOX_TTS_ENDPOINT=http://127.0.0.1:8123/` → `base_url` 去尾斜杠、`endpoint()` 正确、`payload["voice"]` 带上 env 音色 |
| 2 非法 env fail-closed | ✅ | 5 档（`not-a-url`/`ftp://`/裸 host/纯空白/**空串**）全抛 `ValueError`；CLI 层 `rc=2`。**空串这一档是复核员发现后补的**（见下） |
| 3 **真机铸小包成功** | ❌ **不成立（改判）** | `demo-brief` 三次分别失败 1 / 2 / 3 条（合计 3/96），失败 key 每次不同；同包走 `say` 48/48。**根因：模型是采样型，输出不确定**（同文本 8 次 sha256 全不同）→ 撞「预铸成功率必须 100%」这条纪律。**已开 T41 承接**，本卡不修 |
| 4 双档全绿 | ✅ | `adapters` 根：3.14 **297** OK / 3.12 **297** OK（+4 条新测试） |
| 5 结构预算 | ✅ | `check.py` 退出 0、`violations: []`；`tts_omlx/adapter.py` **148 ≤ 150**（改动净 +2 可执行行） |
| 6 注入反空转 | ✅ | 把 env 兜底改回常量 → 该文件 5 条 FAIL；单独把 `get(k,默认)` 改回 `or` → **只有 `endpoint=''` 那档判红**（牙精确） |
| 7 文档逐字可跑 | ✅ / ⚠️ | 命令本身可跑（`docs/22 §1` 的 `VOX_TTS_ENDPOINT=… --adapter …` 实测可用）；但**整包会因判据 3 随机失败**——`docs/22 §5` 已如实加一行标明 |
| 冻结区 | ✅ | `git diff --name-only -- core rules compiler assets runtime eval cli` 为空 |

**独立复核处置（`silent-failure-hunter`，只读）**：

| 发现 | 裁定 | 处置 |
|---|---|---|
| `VOX_TTS_ENDPOINT=`（**设了但为空**）静默回落本机地址——与三处文档「写错会响亮抛错，不会悄悄换地址」不符 | **真缺陷（本卡引入）** | 已修：`or 默认值` → `get(k, 默认值)`（空串落到既有的 http 校验上抛错）；补测试档位；注入验证判红 |
| `docs/22` 引 `adapter.py:31`，但改动后 `SPEECH_ENDPOINT` 已漂到 34 行 | **真缺陷（引用失效）** | 已修：**改为引符号名而非行号**（行号必然漂，符号不会） |
| 「不是本卡引入」的判断 | **证实** | 复核员把 HEAD 版单独装载对比：无 env 时请求体/`endpoint`/`voice`/`model_version` **逐字段相同**（`build_payload` 本来就写 `voice="default"`） |
| 归一自检的报错归因可能误导（`_trim` 的承诺会被 `_peak_normalize` 静默改写） | **真（既有代码，非本卡引入）** | 登记进 T41 §九，不在本卡修 |
| `voice` 属性会重复读参考音频（一次铸包 3 次） | **既有（T35），本卡未加重** | 登记，不修 |

**返工**：无（判据 1/2/4/5/6/7 首轮即过）。**判据 3 如实改判为不成立**——卡面原写的「铸一个小包成功」
在采样型引擎上做不到，这是本卡最有价值的产出（把问题从「能不能连上」推进到「能不能稳定铸包」）。
