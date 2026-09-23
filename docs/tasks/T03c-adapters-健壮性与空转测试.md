# T03c · adapters：tts-macsay 三处健壮性 + 两条空转测试（源码级审计发现）

> 本卡来源：T03/T03b 验收通过后，按项目红线「禁止静默降级」调只读审查员做源码级审计，发现 1 条卡内验收标准未满足 + 2 条反空转条款违约 + 2 条健壮性缺口。本卡逐条修。

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有缺陷复现命令与修复要求，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 缺陷清单（每一条都已由验收方实测复现，执行者请先各跑一遍）

### 缺陷 1（卡内验收标准未满足）：无 `say` 环境是 ERROR 而非 skip

T03 卡验收标准 1 原文：「`python3 -m unittest discover -s adapters -v` 全绿（有 `say` 时含真实合成用例；**无 `say` 时相关用例 skip 而非失败**）」。

实测（用 `PATH` 清空模拟无 `say`）：

```
$ cd （仓库根） && env PATH= /opt/homebrew/bin/python3 -m unittest discover -s adapters
FileNotFoundError: [Errno 2] No such file or directory: 'say'
Ran 35 tests in 0.003s
FAILED (errors=1, skipped=13)
```

根因：`synthesize()` 里 `subprocess.run` 抛出的 `FileNotFoundError` 没有归一成 `TtsError`，于是"环境缺 `say`"被算成测试失败。**要求：`say` 不存在时 `synthesize()` 必须抛 `TtsError`**（消息含原因），从而该用例能被 `skipUnless(shutil.which("say"))` 覆盖或走正常的断言路径。

### 缺陷 2（假成功）：目标路径是已存在目录时静默成功

```
$ PYTHONPATH=. python3 -c "
import os, tempfile
from adapters.tts_macsay.adapter import MacSayTts
with tempfile.TemporaryDirectory() as d:
    MacSayTts().synthesize('测试', d)      # d 是已存在的目录
    print('未抛错 → 假成功；实际落点:', os.listdir(d))
"
未抛错 → 假成功；实际落点: ['tmpj45uaa2z.wav']
```

`shutil.move(src, dst)` 在 `dst` 是目录时语义是「移进目录内」，函数于是正常返回（无异常=调用方判定成功），而契约路径 `out_path` 处**没有**音频文件——语音链路上这表现为"本该播这句、结果没播/播错"，且零留痕。
**要求：`out_path` 已存在且是目录 → 抛 `TtsError`**（消息含该路径与原因）。

### 缺陷 3（健壮性缺口）：`say` 调用无超时

`subprocess.run(cmd, capture_output=True, text=True, check=False)` **没有 `timeout=`**。`say` 卡死（音频服务挂起等）会让 `synthesize()` 无限阻塞，上层（未来的 runtime 双工链路）被整体挂住。
**要求：加 `timeout=`（用模块级常量或类属性，值取 60 秒），超时 → 抛 `TtsError`**（消息含超时秒数）。

### 缺陷 4（反空转违约）：空白文本用例把失败吃成通过

`adapters/tts_macsay/tests/test_adapter.py` 的 `test_whitespace_only_text_is_allowed`：

```python
        except TtsError:
            # say 可能失败（合理行为），但错误不应是"空文本"
            pass
```

`except` 分支里**没有任何断言**。也就是说：如果 adapter 真的对 `"  "` 抛了"空文本"错误（即用例要抓的 bug），它照样通过。这违反 T03 卡反空转条款「不得把失败改成通过」，且用例名与其实际断言不符。
**要求：改成真断言**——要么显式断言"抛出的不是空文本错误"（`assertNotIn("空文本", str(ctx.exception))`），要么断言成功产出。不得 `except: pass`。

### 缺陷 5（反空转违约）：临时文件泄漏用例恒真

同文件 `test_synthesize_uses_tempfile`：

```python
        temp_files = [f for f in Path(self.tmp_dir).iterdir() if f.name.startswith("tmp")]
        self.assertEqual(len(temp_files), 0, "不应有临时文件残留")
```

adapter 的临时文件建在**系统临时目录根部**（实测 `/var/folders/.../T/tmpXXXX.wav`），而用例扫的是 `self.tmp_dir`（一个子目录，里面只有 `test_output.wav`）——**断言恒真，永远测不出泄漏**。这属于「断言恒真/校验空转」。
**要求：改成**真正**能失败的断言**。可行做法（择一）：合成前后统计系统临时目录内 `tmp*.wav` 的数量差，或断言目标目录内除目标文件外无残留 + 系统临时目录计数不增。**改完后请自证该断言能失败**（例如临时把 adapter 的清理注释掉，看到用例 FAIL，再改回来）。

### 缺陷 6（测试卫生）：跨卷用例的临时目录清理失败被静默吞掉

`adapters/tests/test_crossvolume.py` 把临时目录建在**仓库根**（`tempfile.mkdtemp(dir=_REPO_ROOT)`），`tearDown` 用 `shutil.rmtree(..., ignore_errors=True)` —— 清理失败（进程中断、权限问题）不留任何痕迹，会在**仓库工作区里留下垃圾目录**并污染后续派发的基线。
**要求：清理不得静默吞失败**——改用 `addCleanup` 注册清理，或用不带 `ignore_errors` 的 `rmtree`，让清理失败显式暴露。

## 目标（产物）

修改 `adapters/tts_macsay/adapter.py` 与 `adapters/tts_macsay/tests/test_adapter.py`、`adapters/tests/test_crossvolume.py`，逐条修掉上面 6 条。

## 允许修改的文件（白名单）

```
允许修改：adapters/tts_macsay/adapter.py,
          adapters/tts_macsay/tests/test_adapter.py,
          adapters/tests/test_crossvolume.py,
          adapters/tests/test_conformance.py
允许新增：无
禁止触碰：其余一切文件（含 core/**、assets/**、rules/**、adapters/AGENTS.md、docs/**、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库 `subprocess` / `tempfile` / `pathlib` / `unittest` / `unittest.mock`）。
- **不得靠放宽断言来"修"测试**：缺陷 4/5 要的是让断言**真的有效**，不是删掉它。
- 不得删既有用例；不得新增 `skipTest` 来绕过（缺陷 1 的方向相反：要**归一异常**让环境缺失变成正常 skip，不是把失败改 skip）。
- 不得改动白名单外文件；不得顺手重构；不得把 `synthesize` 的既有正常路径行为改掉（空文本抛错、未知档位抛错带 key、say 非零退出抛错带 stderr、产物不存在或 0 字节抛错、临时文件用 tempfile 且异常路径清理、跨卷落盘成功）。
- 不得实现业务逻辑 / 拼接 / 命中判定。

## 验收标准（我会逐条核对）

1. **缺陷 1**：`env PATH= /opt/homebrew/bin/python3 -m unittest discover -s adapters` → **不再出现 ERROR**（应为 `skipped` 计数增加、`OK`）；`<带 say 的解释器> -m unittest discover -s adapters -v`（本机有 say）仍全绿；
2. **缺陷 2**：`synthesize('测试', <已存在目录>)` → 抛 `TtsError`（消息含该路径），且该目录内**没有**多出任何文件（我会实测）；
3. **缺陷 3**：`adapter.py` 里 `subprocess.run(...)` 带 `timeout=`；我用 `unittest.mock` 让 `subprocess.run` 抛 `subprocess.TimeoutExpired` → `synthesize()` 抛 `TtsError` 且消息含超时值；
4. **缺陷 4**：`test_whitespace_only_text_is_allowed`（或改名后的对应用例）的 except 分支里有**真断言**（我 grep 核对无 `except ...: pass`）；
5. **缺陷 5**：临时文件泄漏断言**可失败**——我会用临时改坏 adapter（把 `finally` 里的 `unlink` 注释掉）在副本上验证该用例会 FAIL，再还原。执行时请在卡里贴出你自证该断言能失败的方法（一句话即可）；
6. **缺陷 6**：`test_crossvolume.py` 的清理不再 `ignore_errors=True` 静默吞（改用 `addCleanup` 或不带 `ignore_errors`）；
7. 既有 35 条用例**逐条仍在**（只增不减），且既有断言未被放宽；
8. `adapter.py` 仍 **≤150 行**（不含注释与空行；当前 98 行，预算充足）；方法级中文注释保留；
9. 白名单之外无改动（我用 `git status --porcelain` 核对）。

## 反空转条款（必带）

- 测试必须调用产品 API（`MacSayTts.synthesize` / `rate_value`），不得在测试内重写合成逻辑；
- 新增/修改的断言必须**能失败**（不得恒真），必须断言**异常类型 + 消息含关键值**；
- 不得用 `skipTest` 把失败改成跳过（缺陷 1 的修法是归一异常，不是跳过）。

## 回滚方式

```
cd （仓库根）
git checkout -- adapters/tts_macsay/adapter.py adapters/tts_macsay/tests/test_adapter.py adapters/tests/test_crossvolume.py adapters/tests/test_conformance.py
```

（四个文件均已入库为**已跟踪**基线，`git checkout` 有效。）

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T03c-adapters-健壮性与空转测试.md)" --dir （仓库根）
```

**非交互环境**：请直接改代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要修改或新建任何文件。

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → 验收 8/9（标准 5 未过 → T03d 补做后通过）
