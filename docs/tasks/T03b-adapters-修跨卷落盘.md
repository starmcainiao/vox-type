# T03b · adapters：修 tts-macsay 跨卷落盘失败（附回归测试）

> 本卡是 T03 的**修订卡**：T03 的六条验收标准本身已全过，但验收时实测出一个**阻塞下游的真缺陷**（见「问题复现」），按纪律退回重做，不做"差不多过"。

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有缺陷复现命令与修复要求，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 背景

`adapters/tts_macsay/adapter.py` 的 `synthesize()` 采用「先写到 `tempfile`，再移动到目标路径」的实现，移动用了 `Path.replace()`（即 `os.replace`）。
`os.replace` **不能跨文件系统**。而本机：系统临时目录与仓库目录**分属两个文件系统**（跨卷）。

后果：适配器**写不进仓库所在卷**。而 T03 卡的背景已写明这个适配器是「用于后续预铸流水线（T05）与执行器（T07）」用的，T05 正是要往仓内 `assets/` 或业务包目录写音频（`.gitignore` 里 `assets/audio/` 的存在也说明音频预期落在仓内）。**这条不修，T05 必崩。**

## 问题复现（验收时实测，执行者必须先跑一遍确认）

```
cd （仓库根） && python3 -c "
from adapters.tts_macsay.adapter import MacSayTts
MacSayTts().synthesize('跨卷测试', '（仓库根）/.crossdev_probe.wav')
"
```

实测输出（现状）：

```
OSError: [Errno 18] Cross-device link: '/var/folders/gq/.../tmpXXXXXXXX.wav' -> '（仓库根）/.crossdev_probe.wav'
```

注意两点：
1. 抛的是**裸 `OSError`**，不是 `TtsError`——违反 T03 卡「把上述失败统一抛 `TtsError`」的要求；
2. 失败点在**落盘**阶段，`say` 本身是成功的（临时 WAV 已正常产出）。

（复现后请务必删掉 `/.crossdev_probe.wav`——正常修复后该文件会真的生成成功。）

## 目标（修复要求）

1. **跨卷也必须成功落盘**：目标路径与 `tempfile` 不同设备时，`synthesize()` 必须正常产出 WAV（内容为 16kHz/单声道/16-bit）。
2. **所有失败统一抛 `TtsError`**，消息含原因；**不得让裸 `OSError` 逃逸**。覆盖两条失败路径：
   - 产出目录创建失败（例如目标父路径上有一个同名的**普通文件**，`mkdir` 会失败）；
   - 落盘移动失败。
3. **既有行为一条都不许放宽**：空文本抛错、未知语速档位抛错且消息含 key、`say` 非零退出抛错（带 stderr 摘要）、产物不存在或 0 字节抛错、`say` 成功但临时文件清理（`finally`）、临时文件仍用 `tempfile`（不往仓里丢临时文件）。
4. 允许的实现方向（**不强制**，能用更少代码达到同样效果即可）：把 `tmp_path.replace(out)` 换成 `shutil.move(...)`（标准库，跨卷自动退化为复制+删除），并把该步包进 `try/except OSError` → `raise TtsError(...)`；`mkdir` 同样用 `try/except OSError` 包一层。
5. `adapter.py` 仍须 **≤150 行**（不含注释与空行；当前实现实测 91 行，预算充足）。方法级中文注释保留。

## 允许修改的文件（白名单）

```
允许新增：adapters/tests/test_crossvolume.py
允许修改：adapters/tts_macsay/adapter.py
禁止触碰：其余一切文件（含 core/**、adapters/AGENTS.md、core/AGENTS.md、rules/**、assets/**、docs/**、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- **不得把测试改成"跳过"来绕过**：跨卷用例在本机**必须真跑**（本机 TMPDIR 与仓库确实是两个设备，必然可跑），只有在确实找不到异设备目录时才允许 `skipTest`。
- 不得改动白名单外文件；不得顺手重构、不得改测试口径、不得删既有用例。
- 不得实现业务逻辑 / 拼接 / 命中判定。

## 验收标准（我会逐条核对）

1. **复现命令修复后成功**：把上文的 out 路径换成一个异设备目录下的 WAV，`synthesize()` 正常返回；我用 `wave` 读出的 `channels==1`、`sampwidth==2`、`framerate==16000`、帧数 > 0；
2. `python3 -m unittest discover -s adapters -v` **全绿**；且新增的跨卷用例**状态为 ok 而非 skipped**（我会看 `-v` 输出）；
3. **负例（我手跑）**：目标父路径上放一个同名普通文件 → `synthesize()` 抛 `TtsError`（不是 `OSError`/`FileExistsError`），消息含原因；
4. 既有 33 条测试**逐条仍在**（只增不减），且不得有任何断言被放宽（对照 T03 已提交的实现）；
5. `adapter.py` ≤150 行（不含注释与空行，我实测）；`synthesize` / `rate_value` 仍有方法级中文注释（职责/参数/返回/边界）；
6. 白名单之外无改动（我用 `git status --porcelain` 核对）。

## 反空转条款（必带）

- 新增测试**必须调用产品 API**（`MacSayTts.synthesize`），不得在测试内重写合成或移动逻辑；
- 跨卷用例必须**断言 WAV 参数**（`channels`/`sampwidth`/`framerate`/帧数>0），不得只断言"文件存在"；
- 负例必须断言**异常类型 + 消息含原因**，不得只写 `assertRaises(Exception)`；
- 跨卷用例必须在**异设备**目录下建临时目录做（`tempfile.mkdtemp(dir=<异设备目录>)`），`tearDown`/`addCleanup` 必须清理干净，**不得在仓里留下任何文件**；若 `os.stat(该目录).st_dev == os.stat(tempfile.gettempdir()).st_dev` 则 `skipTest`（**不得把失败改成通过**）。

## 回滚方式

```
cd （仓库根）
git checkout -- adapters/tts_macsay/adapter.py
rm -f adapters/tests/test_crossvolume.py
```

（`adapter.py` 已由 T03 提交为**已跟踪**基线，所以 `git checkout` 有效。）

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T03b-adapters-修跨卷落盘.md)" --dir （仓库根）
```

**非交互环境**：请直接落地代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要在仓库内新建任何文件。

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 验收通过（提交 e3e4d90）
