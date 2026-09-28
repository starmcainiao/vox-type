# T38 · `adapters/tts_omlx` 测试的 CI 环境依赖与**假绿**修复

> 层：`adapters/`（扩展区）
> 发现于：2026-09-23 快照 `06f7488b` 推送后的 CI（Python 3.12/3.13/3.14 三档全红）
> 性质：**T35 遗留缺陷**（我方 T35 验收漏检）+ 一处**假绿**（负例因错误原因通过）
> 归属：扩展区，**不需开批**

## 一、问题（CI 实测，非推测）

T35 新增的 `adapters/tts_omlx/tests/test_adapter.py` 里，跨机环境依赖处理错了：

```python
FFMPEG_BIN: str = shutil.which("ffmpeg") or "ffmpeg"   # 回落到裸名
```

适配器在合成前会 `shutil.which(self._ffmpeg)` 做**可用性检查**（`adapter.py:131-133`）。
CI runner 没装 ffmpeg → 裸名解析不到 → 抛 `TtsError`。后果分两种，**两种都是坏的**：

| 用例 | 本意 | CI 上的实际后果 |
|---|---|---|
| `test_output_is_16k_mono_16bit` | 断言产物是 16k/单声道/16-bit | **ERROR**（三档 Python 全红，CI 挂在这两条上） |
| `test_ffmpeg_invoked_with_16k_contract_args` | 断言命令行必须带 `-ar 16000` / `pcm_s16le` | **ERROR**（同上） |
| `test_bogus_output_format_raises` | 断言「ffmpeg 产出非契约格式 → 抛错」 | **假绿**——它期望 `TtsError`，而缺失 ffmpeg 也抛 `TtsError`，**因错误的原因通过**，待验的复核逻辑一行没跑到 |
| `test_ffmpeg_nonzero_exit_raises` | 断言「ffmpeg 非 0 退出 → 抛错且消息含退出码」 | **假绿**（同上） |

**本机为什么没暴露**：本机装了 ffmpeg，四条全绿。这正是仓内已记的教训「**本机绿 ≠ CI 绿**」的
又一次应验——而且这次更隐蔽：两条负例不是「红」，是「绿得没意义」。

## 二、目标

1. 四条**替身 runner** 用例在**没有真 ffmpeg 的机器上也能真的跑**（不靠 skip 躲开）；
2. 两条负例必须**因正确的原因**通过——即待验的复核逻辑真的被执行；
3. 真合成的冒烟用例维持 `skipUnless(真 ffmpeg)`（它本就需要真引擎）。

## 三、产物路径

| 文件 | 改什么 |
|---|---|
| `adapters/tts_omlx/tests/test_adapter.py` | `FFMPEG_BIN` 改为 `_stub_ffmpeg()`：取任一**存在的**可执行文件作占位（真执行被 `FakeRunner` 接管），并写清 WHY；`TestDownsampleShape` 加类级 `skipUnless(FFMPEG_BIN)` 兜底 |
| `docs/13-未完成清单.md` | §五#23 补第 ⑤ 条（本缺陷）；§八 登记本卡 |
| `docs/tasks/00-索引.md` | 第二十七批节补记本卡 |

## 四、文件白名单（越界即退）

```
adapters/tts_omlx/tests/test_adapter.py
docs/13-未完成清单.md
docs/tasks/00-索引.md
docs/tasks/T38-adapters-tts-omlx测试的CI环境依赖与假绿修复.md
```

## 五、禁止事项

1. **不动 `adapters/tts_omlx/adapter.py` 或 `postprocess.py`**——适配器行为正确（fail-closed），
   缺陷在测试的环境假设；
2. 不动冻结区；不改 CI workflow（本修复后 CI 无需装 ffmpeg）；
3. 不 `git add` / 不提交 / 不推送。

## 六、验收标准

1. **本机（有 ffmpeg）**：`adapters` 根 293 条全绿；
2. **模拟 CI（PATH 无 ffmpeg，例 `env PATH=/usr/bin:/bin <python>`）**：
   `adapters` 根仍全绿，且 `TestDownsampleShape` 的 **4 条是真跑（不是 skip）**——
   逐条 `-v` 输出可见 `ok`，不是 `skipped`；
3. **反空转**：把格式负例的替身产出改成「合规格式」→ 该用例必须**判红**（证明它真的在验复核逻辑）；
4. **真冒烟仍诚实 skip**：无 ffmpeg 时 `skipped` 计数含它，且不产生 ERROR；
5. 不改产品代码的前提下，`git diff` 只触及白名单文件。

## 七、回滚方式

`git checkout -- adapters/tts_omlx/tests/test_adapter.py`。

## 八、依赖

无。**但它是快照 `06f7488b` 的 CI 红因之一**——修完后需重推快照才能让公开 CI 恢复
（另一类红 = `docs/13 §五#22` 的 TTL 夹具，**在冻结区，需用户开批**，本卡不涉及）。

## 九、已知问题登记（不在本卡范围）

- **CI 仍会红**：`assets` 1 条 + `runtime` 3 条（墙钟越过写死的失效时刻）属冻结区缺陷
  `docs/13 §五#22`，需开批才能修；`README` 已如实披露该 4 条为红。
- 本仓**无「CI 环境 vs 本机」的自动差异检查**——本次靠人看 CI 才发现。是否加一道
  「在最小 PATH 下跑一遍」的本地预检，登记为后续考虑项。

## 十、验收记录

**验收方：AI 会话（策划兼验收，2026-09-23）**。**披露**：本卡由我方实现与自验；
**本缺陷是我方 T35 验收漏检**（只在本机验、没看 CI 环境差异），发现过程如实记在上面。

| 判据 | 结果 | 证据 |
|---|---|---|
| 1 本机全绿 | ✅ | `adapters` **293** OK |
| 2 模拟 CI 全绿且不躲 | ✅ | `env PATH=/usr/bin:/bin python3 -m unittest discover -s adapters` → **293 OK (skipped=1)**；`TestDownsampleShape -v` → **Ran 4 tests … OK**（`ok` 而非 `skipped`） |
| 3 反空转 | ✅ | 注入「替身产出改成合规格式」→ `test_bogus_output_format_raises` **FAIL**（非空转） |
| 4 真冒烟诚实 skip | ✅ | 无 ffmpeg 时全根 `skipped=1`（即真合成冒烟），无 ERROR |
| 5 白名单 | ✅ | `git diff --stat` 仅触及 `adapters/tts_omlx/tests/test_adapter.py` |

**过程留痕（一个差点误读的现象）**：反空转注入后我用 `cp` 原地还原文件，重跑仍报 FAILED——
**不是还原失败，是 `__pycache__` 陈旧**（原地替换文件后 mtime+size 未触发失效）；
`find adapters -name __pycache__ -exec rm -rf {} +` 后即恢复全绿。**教训：原地换文件做注入时，
先清字节码缓存，否则会把「缓存陈旧」误读成「代码没还原」。**

### 追加：本次修复**自己又踩了仓里已写明的坑**（如实记账）

第一版修复用了 `Optional[str]` 注解但**没加 `from typing import Optional`**。本机主解释器是
**3.14（PEP 649 延迟求值）→ 注解不求值、全绿**；CI 的 **3.12/3.13 在 import 时求值 → NameError**，
整个 `tts_omlx.tests.test_adapter` 模块**导入即失败**（`unittest.loader._FailedTest`，
adapters 根 293 → 255 条）。

**这正是 `docs/tasks/01-接手指南` 与 `docs/18` 都已写明的那条教训**——「涉及注解/新语法时要用
`~/miniforge3/bin/python3`（3.12）复跑关键根」。我读过它，没照做，于是同一类事故当场重演一次。

**追加验收（按规程补做，两个解释器 × 有无 ffmpeg）**：

| 条件 | 结果 |
|---|---|
| 3.14 本机 | `adapters` **293 OK** |
| 3.12（`~/miniforge3`，CI 档） | `adapters` **293 OK** |
| 3.12 + 最小 PATH（模拟 CI 无 ffmpeg） | `adapters` **293 OK（skipped=1）** |
| 3.12 全根复跑 | 仅 `assets` 1 条 + `runtime` 3 条红（**即 `§五#22` 的已知冻结区缺陷**），其余全绿 |

**新增过程纪律（本卡副产品，建议提升为仓级惯例）**：**凡新增/修改注解，提交前至少用 3.12 跑一遍改动的根**；
**凡新增依赖外部可执行文件的测试，用最小 PATH 跑一遍**。两条都是命令，写在这里以免再靠记。
