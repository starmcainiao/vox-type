# 结构预算台账（R09 适应度函数）

本台账由 `tools/structure_budget/check.py` 生成，**不要手改数字**——手改会让台账与磁盘上的实际行数脱节（静默降级）。

## 行数口径

可执行行 = `tokenize` 后 type 为 `NEWLINE` 的 token 计数（一条逻辑语句结束时的换行，每个代码行恰好一个；注释行、纯 docstring 行归为 `NL` 不在此计数，**不要用 `NL`**——实测 `a = 1\nb = 2\n` 的 NEWLINE 是 2 而 NL 是 0，用错会把口径反转为「数空行数」）。与 `adapters/AGENTS.md §⑨` 的 145–150 口径同源；测试目录与 `__pycache__` 不计（量化标准明写「不含注释与测试」）。

## 阈值来源（脚本只从这里读，不另立一套）

| 层 | 阈值（可执行行） | 来源 |
| --- | --- | --- |
| `core` | 150 | `core/AGENTS.md` |
| `rules` | —（未登记，不参与判定） | `rules/AGENTS.md` |
| `compiler` | —（未登记，不参与判定） | `compiler/AGENTS.md` |
| `assets` | —（未登记，不参与判定） | `assets/AGENTS.md` |
| `runtime` | —（未登记，不参与判定） | `runtime/AGENTS.md` |
| `adapters` | 150 | `adapters/AGENTS.md` |
| `trigger` | —（未登记，不参与判定） | `trigger/AGENTS.md` |
| `eval` | 150 | `eval/AGENTS.md` |
| `cli` | 150 | `cli/AGENTS.md` |

## 当前各层行数快照

### `core`（阈值 ≤ 150）

| 文件 | 可执行行 |
| --- | --- |
| `core/protocol.py` | 72 |
| `core/metrics_spec.py` | 28 |
| `core/__init__.py` | 3 |
| **合计** | **103** |

### `rules`（未登记阈值，不参与判定）

（本层无 .py 文件）

### `compiler`（未登记阈值，不参与判定）

| 文件 | 可执行行 |
| --- | --- |
| `compiler/prebake.py` | 182 |
| `compiler/checks.py` | 143 |
| `compiler/script.py` | 137 |
| `compiler/source.py` | 136 |
| `compiler/quality.py` | 96 |
| `compiler/__init__.py` | 6 |
| **合计** | **700** |

### `assets`（未登记阈值，不参与判定）

| 文件 | 可执行行 |
| --- | --- |
| `assets/pack.py` | 325 |
| `assets/fingerprint.py` | 7 |
| `assets/__init__.py` | 3 |
| **合计** | **335** |

### `runtime`（未登记阈值，不参与判定）

| 文件 | 可执行行 |
| --- | --- |
| `runtime/executor.py` | 264 |
| `runtime/audio.py` | 95 |
| `runtime/duplex.py` | 76 |
| `runtime/events.py` | 56 |
| `runtime/__init__.py` | 5 |
| **合计** | **496** |

### `adapters`（阈值 ≤ 150）

| 文件 | 可执行行 |
| --- | --- |
| `adapters/framework_kefu/kefu_client.py` | 191 |
| `adapters/framework_kefu/bridge.py` | 155 |
| `adapters/tts_omlx/postprocess.py` | 133 |
| `adapters/framework_kefu/hit_query.py` | 129 |
| `adapters/tts_omlx/adapter.py` | 115 |
| `adapters/asr_omlx/adapter.py` | 106 |
| `adapters/tts_omlx/transport.py` | 73 |
| `adapters/tts_macsay/adapter.py` | 55 |
| `adapters/framework_kefu/normalize.py` | 16 |
| `adapters/framework_kefu/__init__.py` | 7 |
| `adapters/asr_omlx/__init__.py` | 2 |
| `adapters/tts_macsay/__init__.py` | 2 |
| `adapters/tts_omlx/__init__.py` | 2 |
| **合计** | **986** |

### `trigger`（未登记阈值，不参与判定）

| 文件 | 可执行行 |
| --- | --- |
| `trigger/format.py` | 393 |
| `trigger/ledger.py` | 147 |
| `trigger/plan.py` | 120 |
| `trigger/reachability.py` | 32 |
| `trigger/__init__.py` | 5 |
| **合计** | **697** |

### `eval`（阈值 ≤ 150）

| 文件 | 可执行行 |
| --- | --- |
| `eval/duplex_quality.py` | 461 |
| `eval/bench.py` | 379 |
| `eval/readback.py` | 254 |
| `eval/report.py` | 179 |
| `eval/offline_tts.py` | 72 |
| `eval/stats.py` | 65 |
| `eval/cer.py` | 42 |
| `eval/_io.py` | 33 |
| `eval/__init__.py` | 5 |
| **合计** | **1490** |

### `cli`（阈值 ≤ 150）

| 文件 | 可执行行 |
| --- | --- |
| `cli/commands/verify.py` | 109 |
| `cli/commands/__init__.py` | 100 |
| `cli/commands/bench.py` | 77 |
| `cli/main.py` | 74 |
| `cli/commands/run.py` | 62 |
| `cli/commands/pack_build.py` | 61 |
| `cli/commands/pack_check.py` | 55 |
| `cli/errors.py` | 39 |
| `cli/__init__.py` | 5 |
| `cli/__main__.py` | 4 |
| **合计** | **586** |

全仓合计（54 个 .py 文件）：**5393** 可执行行

## 违规

（无）

## 豁免登记

超限但**已在各层 AGENTS.md 说明理由**的文件在此显式登记；未登记的超限一律判违规。豁免不是永久豁票：新增功能前先看能不能先拆。

<!-- BEGIN exemptions -->
- adapters: framework_kefu/bridge.py, 与 kefu HTTP 宿主桥接（见 adapters/AGENTS.md §⑨.1）
- adapters: framework_kefu/kefu_client.py, kefu HTTP 传输层（见 adapters/AGENTS.md §⑨.1）
- eval: bench.py, 离线对拍 harness 本体（见 eval/AGENTS.md ⑧）
- eval: duplex_quality.py, 双工质量 harness（见 eval/AGENTS.md ⑧）
- eval: readback.py, 回读 CER harness（见 eval/AGENTS.md ⑧）
- eval: report.py, 报告落盘与质检（见 eval/AGENTS.md ⑧）
<!-- END exemptions -->
