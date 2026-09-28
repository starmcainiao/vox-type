# T03 · adapters：tts-macsay 薄适配器（接口一致性）

## 背景

`adapters/` 是**扩展区**：把外部能力接进来，要求"薄"（单适配器 ≤ 150 行，不含注释与测试）。
本卡只做**第一个 TTS 适配器**——macOS `say`（本机可跑、零依赖），用于后续预铸流水线（T05）与执行器（T07）。

依赖：`core/`（T01/T01b 已验收）。必读：`AGENTS.md`、`adapters/AGENTS.md`、`core/AGENTS.md`。**不得修改这些文件。**

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有接口规格与合成命令参数，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 目标（产物）

1. `adapters/tts_macsay/__init__.py` — 导出 `MacSayTts`
2. `adapters/tts_macsay/adapter.py` — 适配器实现（≤150 行，不含注释/空行）
3. `adapters/tts_macsay/tests/__init__.py` + `adapters/tts_macsay/tests/test_adapter.py` — 测试
4. `adapters/tests/test_conformance.py` — **接口一致性测试**（同一套测试将来跑所有 `tts-*`）

### 接口要求（必须逐项实现）

```python
class MacSayTts:
    name: str = "macsay"                        # 适配器名
    model_version: str = "macos-say"            # 参与资产指纹；换版本=旧资产失效
    voice: str = "Tingting"                     # 音色
    rate_map: dict[str, int]                    # 语义档位 → 物理参数（slow/normal/fast）
    requires_core: str = "^0.1"                 # 依赖的内核版本区间

    def rate_value(self, rate_key: str) -> int: ...      # 未知档位 → 抛错（不得回落）
    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None: ...
```

`synthesize` 行为要求：
- 调用 `say -v <voice> -r <rate> -o <tmp> --file-format=WAVE --data-format=LEI16@16000 <text>`，产出 **16kHz / 单声道 / 16-bit WAV**；
- **空文本 → 抛错**（不得合成静音）；`say` 非零退出 → 抛错并带 stderr 摘要；产物不存在或 0 字节 → 抛错；
- 产出目录不存在时自动创建；临时文件用 `tempfile`，不落仓。

### 口径澄清（策划 2026-09-17 增补，派发前实测确认）

- 本机（macOS 26.3.1 / Python 3.14.6）**实测通过**：`say -v Tingting -o <真实临时文件> --file-format=WAVE --data-format=LEI16@16000 "<文本>"` 退出码 0，产物为 **1 声道 / 16 bit / 16000 Hz** 的合法 WAV（`wave` 可读，39483 帧）。
  → **一条 `say` 命令直出即可**。**不要**引入 `afconvert`、重采样逻辑或任何"格式不达标就转换"的分支；Python 3.14 已移除 `audioop`，手写重采样只会把适配器撑爆 150 行上限。
- 已知易踩坑：`-o /dev/null`、`-o /dev/stdout` 会被 `say` 拒绝（`Speaking failed: -241` / `Opening output file failed: -54`），并可能伴随 `This synthesizer does not support data format specifications.` 的误导性报错。这是 `say` 对**特殊文件**的限制，**不是** data-format 不支持——产物必须写到普通临时文件路径。

### 错误类型

在 `adapter.py` 内定义 `TtsError(RuntimeError)`，并把上述失败统一抛 `TtsError`（消息含原因）。

## 允许修改的文件（白名单）

```
允许新增：adapters/tts_macsay/__init__.py, adapters/tts_macsay/adapter.py,
          adapters/tts_macsay/tests/__init__.py, adapters/tts_macsay/tests/test_adapter.py,
          adapters/tests/__init__.py, adapters/tests/test_conformance.py
允许修改：无
禁止触碰：其余一切文件（含 core/**、AGENTS.md、README.md、docs/**）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库 `subprocess` / `tempfile` / `wave` / `unittest`）。
- 不得包含业务逻辑（业务话术、剧本、命中判定一概不做）。
- **不得让测试依赖"必须真跑 `say`"**：一致性测试必须能在无 `say` 的环境降级为 skip（用 `shutil.which("say")` 判断），**但空文本抛错、未知档位抛错这两条必须无条件测试**（不依赖外部命令）。
- 不得改动白名单外文件。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s adapters -v` 全绿（有 `say` 时含真实合成用例；无 `say` 时相关用例 skip 而非失败）；
2. **负例（必须无条件测）**：`synthesize("", path)` → `TtsError`；`rate_value("very_slow")` → 抛错且消息含 `very_slow`；
3. 一致性测试 `test_conformance.py` 对 `MacSayTts` 断言：`rate_map` 含 `slow/normal/fast` 三档；`model_version` 非空；`requires_core` 非空；`name` 非空；**并留出"将来加入其他 tts-* 适配器时复用同一套断言"的结构**（如以参数化方式收集适配器类）；
4. 真实合成用例（有 `say` 时）：产出的 WAV 经 `wave` 读取后，`channels==1`、`sampwidth==2`、`framerate==16000`、帧数 > 0；
5. 适配器文件 ≤150 行（不含注释与空行）；我会实测行数；
6. 含方法级中文注释（职责/参数/返回/边界）。

## 反空转条款（必带）

- 测试必须调用适配器**产品 API**（`synthesize` / `rate_value`），不得在测试内复制其逻辑；
- 不得用 `assertTrue(True)` 之类占位断言；外部命令不可用时用 `skipTest`，**不得把失败改成通过**。

## 回滚方式

只新增文件 → `git clean -fd adapters/tts_macsay adapters/tests`。

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T03-adapters-tts-macsay.md)" --dir （仓库根）
```

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 退回（跨卷落盘裸 OSError → T03b 已修，经 T03c/T03d 后整体验收通过）
