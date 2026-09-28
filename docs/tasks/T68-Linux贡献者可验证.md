# T68 · Linux/Windows 贡献者可验证（冻结区·待开批）

> 状态：**待开批**（`cli/` 是冻结区）
> 来源：T1 决策层「开发交付票」G2
> **本卡只起草。注意：这一批在更早的会话里被明确推迟过（见 §六），开批前请先读那段。**

## 一、问题（已复核）

全仓测试**需要 macOS 的 `say` 命令**。无 `say` 环境下 `cli` 根整根红。

模拟 Linux（最小 PATH 屏蔽 macOS 专有二进制）逐根实测：

| 根 | macOS ran | 无 say ran | 无 say failed |
|---|---|---|---|
| `adapters` | 335 | 335 | 0（skipped 14） |
| **`cli`** | **89** | **26** | **22** |
| 其余 9 根 | — | — | **0** |

报错原文：

```
RuntimeError: 夹具预铸失败: rc=4 stderr={
  "reason": "TtsError: say 命令不可用: [Errno 2] No such file or directory: 'say'"
```

**关键不对称**：`adapters` 根**有**守卫，所以在无 `say` 环境优雅跳过：

```python
# adapters/tests/test_conformance.py:137
@unittest.skipUnless(shutil.which("say"), "macOS say 命令不可用，跳过真实合成用例")
```

`cli` 根的预铸夹具**没有这层守卫**，直接 ERROR。
**`cli` 契约（89 条里的 63 条）在 Linux 上永远跑不到**——而 `cli` 恰恰是本仓
自称最对外的冻结契约（`docs/08` 的退出码 0/2/3/4/5）。

而 `CONTRIBUTING.md` 通篇**零平台说明**（`grep -c "macOS\|Linux"` → 0）。
Linux 贡献者按贡献指南跑一遍必然红，且指南没告诉他这是已知限制。

## 二、为什么必须开冻结区

守卫要加在 `cli/tests/` 的共用预铸夹具里，而 `cli/` 整目录是冻结区。
`.github/workflows/` 不是冻结区，但那部分只能加 CI 作业、加不了守卫。

## 三、做什么

1. `cli/tests/` 的共用预铸夹具加**与 adapters 同款**守卫：
   `@unittest.skipUnless(shutil.which("say"), "…")`。
   **同形照抄那一行**，不要发明新写法——三个夹具类漏抄了 adapters 早就做对的那一行。
2. `.github/workflows/tests.yml` 加第二个 job，`runs-on: ubuntu-latest`，
   **只跑已实测可过的 10 根**：
   `core rules assets compiler runtime eval tools trigger packs` + `adapters`。
   **不要**把 `cli` 放进 Linux 矩阵（那正是这张卡要解决的问题本身）。
3. 守卫跳过的用例**必须计入 `skipped` 并在汇总行可见**，不许静默消失。

## 四、验收（开批后执行）

- AC1：模拟无 `say` 环境跑全量 → `failed=0`（允许 `skipped` 上升），**给出汇总行原文**。
- AC2：macOS 上跑全量 → `failed=0` 且 **`skipped` 回到 0**（守卫不许在有 `say` 时误跳）。
- AC3：ubuntu-latest CI 作业真绿（**在 GitHub 上验，不许本机绿当 CI 绿**——
   本仓已因此栽过四次，见 `docs/tasks/01-接手指南`）。
- AC4：贡献指南里明写 `cli` 根的平台限制，**不许写「全平台全绿」**。
- AC5：`tools/run_all_tests.py` 汇总行的 `skipped` 字段口径不变（新增跳过必须可见）。

## 五、为什么 Linux 矩阵只跑 10 根而不是全跑

加完守卫之后 `cli` 根在 Linux 上应该也能过（只是大量 skipped）。
**但那会让「63 条 CLI 退出码契约在 Linux 上到底跑没跑」变成一个每次都要重新回答的问题。**
显式列出 10 根，是为了让「哪些契约在哪个平台被验证」这件事**写在文件里**而不是靠推理。
若开批后实测 `cli` 根在 Linux 也全绿，可考虑扩到 11 根——**但要在 CI 文件里写明**。

## 六、开批前必读：这个坑之前踩过

更早的会话里，你（维护者）就**明确拍板过**：「测试与 CI 的 say 依赖另设计」，
把「mac 基调去除」拆成三块，其中一块就是「测试与 CI 的 say 依赖」——
**当时是推迟，不是否决**。

推迟的理由记录在：调整「测试与 CI 的 say 依赖」的时机，
与「冻结区缺省适配器随接入形态定案」是同一件事的两面——
`bin/vox --help` 现在写着「缺省 `adapters.tts_macsay:MacSayTts`」，
**缺省适配器选什么还没定**，而守卫方案取决于那个选择：

- 若缺省改成「无引擎也能跑」（零依赖优先）→ 守卫的写法完全不同，
  `cli` 根可能根本不需要预铸夹具。
- 若缺省继续是 macsay → 本卡的写法成立。

**所以这张卡的正确顺序是：先定缺省适配器，再定守卫写法。**
在缺省适配器定案之前直接执行本卡，很可能白做。

## 七、待维护者决定

1. **缺省适配器是什么**（`bin/vox --help` 的默认值）？这是前置问题。
2. 缺省定了之后，Linux 矩阵要不要扩到 11 根？
3. `CONTRIBUTING.md` 里那句平台限制，是写在「前置条件」还是「已知限制」小节？
