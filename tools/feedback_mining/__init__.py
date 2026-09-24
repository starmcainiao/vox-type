"""tools.feedback_mining — 回流闭环挖掘器（立项 M3「日志挖掘 → 字模建议」）。

职责（T23）：吃本仓自己的两份留痕——
  ① 轮级留痕 JSONL（T17，`trigger/ledger.py` 的产物：state 结构化层 + plan）
  ② 事件流 JSONL（T07，`runtime/events.py` 的产物：三态 + `live_text`）
取三态为 miss / fallback 的单元的 `live_text`（实际播出、本该预铸却未预铸的话术）
→ 归一化聚合 → `suggestions.json`（候选）+ `report.json`（口径 + provenance）。

三条硬边界（仓库红线，写代码时同样适用）：

1. **归一化与单句判据一律同源，不得复制**：
   聚合用的归一化 = `adapters.framework_kefu.normalize_text`（import）；
   单句判据 = `compiler.source._SENTENCE_TERMINATORS`（import）。
   复制一份就会出现「挖掘侧和编译侧对『一句』理解不同」的静默分叉。
2. **只形式条款机器化**：docs/14 的 A1/A2 是语义判断，脚本一律写 `"pending_human"`，
   不得让脚本猜语义。语义与准入归类留给人工（申报制）。
3. **拒收不得静默**：不合格候选进 `rejected_candidates` 并写明原因与规则编号，
   同 `corpus-harvest` 的 rejected_sources 纪律——丢弃不留痕 = 台账残缺。

无第三方依赖（仅标准库）。本目录属 `tools/` 扩展区，不进任何层契约，
不被 core/rules/compiler/assets/runtime/eval/cli 依赖（依赖方向只指向它们，不反向）。
"""

# WHY 不在包顶 re-export：`python3 -m tools.feedback_mining.miner` 会因包导入先行而触发
#     runpy 的 RuntimeWarning（"'tools.feedback_mining.miner' found in sys.modules after import of
#     package ..."），该警告混进 stderr 会掩盖真实报错——T20 在 eval/ 已定过同类缺陷并修过，
#     此处沿用同一处置：调用方走完整路径 `from tools.feedback_mining.miner import mine`。
