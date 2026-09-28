# T03d · adapters：失败路径的临时文件泄漏回归用例（T03c 验收标准 5 未通过）

> 本卡是 T03c 的**补卡**：T03c 的 9 条验收标准里 8 条通过，**第 5 条未通过**——按纪律退回补做，不允许"差不多过"。

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有测试设计说明与自证方法，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 背景：T03c 的缺陷 5 只修了一半

T03c 要求「临时文件泄漏断言**可失败**」，并指定了自证方式：临时把 adapter 的清理改坏，看用例是否 FAIL。验收方按这个方式实测，结论是**该用例仍然 OK，抓不住回归**。

原因是 adapter 的两条路径不对称：

| 路径 | 临时文件去向 | `finally` 里的 `unlink` 是否起作用 |
|---|---|---|
| 成功 | 被 `shutil.move` **搬到目标路径** | 此时已无临时文件可删，`unlink` 空转 |
| `say` 非零退出 / 超时 / 落盘失败 | 仍留在系统临时目录 | **全靠 `unlink` 兜底** |

T03c 改后的断言（合成前后统计系统临时目录内 `tmp*.wav` 数量）只走了**成功路径**，而成功路径上临时文件本来就被搬走了 → 断言恒真，**清理回归永远测不出来**。

验收方已实测确认「失败路径泄漏用例**能**失败」：把 `finally` 里的 `tmp_path.unlink(...)` 改成 `pass` 后，用 mock 让 `subprocess.run` 返回非零退出，系统临时目录内 `tmp*.wav` 数量 **0 → 1**（泄漏），此时断言会 FAIL。

## 目标（产物）

在 `adapters/tts_macsay/tests/test_adapter.py` 里**新增一条走失败路径的泄漏回归用例**，形如：

```python
def test_failure_path_leaves_no_tempfile(self):
    """失败路径（say 非零退出）也不得留下临时文件——finally 清理必须生效。"""
    # 1. 统计系统临时目录内 tmp*.wav 数量（before）
    # 2. 用 unittest.mock 让 subprocess.run 返回 returncode != 0（不要真的调 say）
    # 3. 断言 synthesize 抛 TtsError
    # 4. 统计 after，断言 after == before
```

要点：
- 覆盖**至少一条失败路径**（`say` 非零退出 即可；愿意再加超时/落盘失败更好）；
- 用 `unittest.mock.patch` 替换 `subprocess.run`，不要让用例真的调 `say`（这样它在无 `say` 环境也有意义）；
- 计数范围与现有成功路径用例保持一致（系统临时目录内 `tmp*.wav`，前后差值）。

## 允许修改的文件（白名单）

```
允许修改：adapters/tts_macsay/tests/test_adapter.py
允许新增：无
禁止触碰：其余一切文件（含 adapters/tts_macsay/adapter.py、adapters/tests/**、core/**、assets/**、rules/**、docs/**、根 AGENTS.md、README.md）
```

**注意：`adapter.py` 不在白名单内——本卡只补测试，产品代码一行都不许动。**

## 禁止事项

- 不得新增第三方依赖（`unittest.mock` 是标准库）。
- 不得修改产品代码（`adapter.py` 等一律不许动）。
- 不得删既有用例、不得放宽既有断言。
- 不得用 `skipTest` 绕过。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s adapters -v` 全绿，用例数 **≥ 36**（T03c 后为 35，只增不减）；
2. 新用例**走失败路径**：断言里能看到 `subprocess.run` 被 mock（我 grep 核对 `mock`/`patch` 与 `returncode`）；
3. **自证（必须做，且必须在回复里贴出命令与前后输出）**：临时把 `adapters/tts_macsay/adapter.py` 的 `finally` 里 `tmp_path.unlink(missing_ok=True)` 改成 `pass`，运行新用例 → **必须 FAIL**；随后**必须把 adapter.py 还原到与改动前逐字节一致**（用 md5 或 `git diff` 证明），再跑一次全量确认全绿。
   - 参考命令（验收方实测过，泄漏会表现为计数 0 → 1）：
     ```
     python3 -m unittest adapters.tts_macsay.tests.test_adapter.TestSynthesizePositive.test_failure_path_leaves_no_tempfile
     ```
   - **还原后必须提交证据**：`git diff --stat adapters/tts_macsay/adapter.py` 应为空。
4. 无 `say` 环境下 `env PATH= /opt/homebrew/bin/python3 -m unittest discover -s adapters` 仍为 `OK`（无 ERROR），新用例**不得**因为依赖真 `say` 而失败（用 mock 的意义就在这里）；
5. 白名单之外无改动（我用 `git status --porcelain` 核对；**特别是 `adapter.py` 必须无 diff**）。

## 反空转条款（必带）

- 新用例必须调用产品 API（`MacSayTts.synthesize`），不得在测试内重写合成逻辑；
- 断言必须**能失败**，且必须由执行者**实测证明**（标准 3），不接受"我认为它能失败"；
- 不得 `except: pass`、不得 `assertTrue(True)`。

## 回滚方式

```
cd （仓库根）
git checkout -- adapters/tts_macsay/tests/test_adapter.py
```

（该文件已入库为**已跟踪**基线。）

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T03d-adapters-失败路径泄漏用例.md)" --dir （仓库根）
```

**非交互环境**：请直接改代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要修改或新建任何文件。

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 验收通过（提交 eef6fd6）
