# adapters/textmatch/ · 跨适配器共用的文本匹配层（扩展区）

> 建于 T47（2026-09-28）。本目录**不是** `tts-*` / `asr-*` / `framework-*` 那类适配器，
> 是 `adapters/` 家族内的**公共部件**：它不接任何外部能力，只把「归一化 + 逐字命中判定」
> 收敛成唯一实现，供家族内多个成员共同依赖。

## ① 职责 / 不负责什么

**职责**：预铸包命中判定的**唯一实现**——
- `normalize.py`：`normalize_text`（docs/10 §10.3 四步归一化，顺序冻结，纯函数）
- `hit.py`：`find_hit`（key 档 / 文本档）+ `find_hit_sequence`（整段逐字覆盖，docs/10 §10.7）
  及其子步骤（`build_text_index` / `lookup_key` / `pick_by_text` / `confirm` / `text_for_key`）

**不负责**：不接外部能力（不调 TTS / ASR / HTTP）；不驱动播放（`runtime/`）；不产生事件
（事件契约在冻结区）；不做语义分析；不做近似匹配；不读写任何文件；
不含业务逻辑（话术是 `packs/` 的数据，不是本层的判据）。

**存在的理由（T47 的问题陈述）**：`adapters/AGENTS.md §⑤` 禁止适配器之间直接 import，
所以「让 MCP 走 kefu 的归一化」这条路是**封死**的——`mcp_vox` 不能 import `framework_kefu`。
两侧都能依赖的第三方只能是同层公共模块，这才有本目录。此前 `mcp_vox/tools.py` 自己写了
一份裸 `entry.text == text`，实测分歧：包内 `价格①` / 传入 `价格1` 在 kefu 入口命中、
在 MCP 入口不命中，且**无任何留痕**——正是 `AGENTS.md` 纪律 4 禁止的静默降级。

## ② 输入 / 输出契约

- `normalize_text(text: str) -> str`：纯函数、确定性、同输入必得同输出。
  输入空串返回空串；**非 str 抛 `TypeError`**（禁止把 `None` / `bytes` 静默当空串
  ——那会让"没命中"伪装成"命中空话术"）。
- `find_hit(pack, *, key=None, text=None, rate_key="normal") -> HitResult`：
  **永不抛错、永不返回裸 None**——命中与否都从 `entry` / `miss_reason` 读。
  `key` 与 `text` **必须恰好给一种**，给 0 种或 2 种都抛 `ValueError`
  （禁止猜调用方想要哪一档）。
- `find_hit_sequence(pack, text, rate_key="normal") -> SequenceHitResult`：同上，永不抛错。
  `blocked_by` **只进诊断、永不进命中**（docs/17 §三）。
- 归一化**只准 docs/10 §10.3 的四步**，不许加步：去空白 → 全角↔半角 → NFKC → 小写。
  任何"同义改写 / 去语气词 / 去标点后比较"都属于语义操作，放进来等于自造命中。
- **薄度量化标准：单文件可执行行 ≤ 150**（引 `adapters/AGENTS.md §②` 与 §⑩ 的同一个数，
  本层**不新立阈值**）。实测：`hit.py` 129 行、`normalize.py` 16 行，均合规，
  **未新增豁免**（台账 `tools/structure_budget/LEDGER.md` 的豁免清单不变）。

## ③ 验收条件

1. **唯一实现可证**：`grep -rn "def normalize_text" --include='*.py' .` 与
   `grep -rn "def find_hit" --include='*.py' .` 的全部命中都在本目录下；
   `adapters/framework_kefu/normalize.py` 与 `hit_query.py` 只剩 import + `__all__`。
2. **同源可证**：`tools/tests/test_feedback_mining.py` 的
   `assertIs(fm.normalize_text, product_normalize)` 恒绿——转发出去的是**同一个函数对象**。
3. **两入口同判**：同一句话经 `framework_kefu.find_hit` 与 `mcp_vox` 的
   `vox_lookup` 工具，命中结论必须一致（含全角/半角、兼容字符这类归一化松弛）。
4. **注入验证**：`normalize.py` 的第 ③ 步 NFKC 删掉 → 「`价格①` / `价格1`」用例判红；
   改回即绿。判红判绿靠的是**真实现**，不是桩。
5. **公开面纪律**：消费方走 `from adapters.textmatch import ...`（本包 `__all__`），
   **不得** `from adapters.textmatch.hit import ...` 绕过 `__all__` 走模块路径。

## ④ 本层数据收集

无。命中率等统计由 `runtime/` 与 `eval/` 统一记录（不建第二份口径）。
本层只**报结论**（`HitResult` / `SequenceHitResult`），是否上报、怎么聚合由调用方决定。

## ⑤ 依赖边界

**依赖方向登记（T47 显式登记，供后续审计复核）**：

| 依赖方 | 依赖本层的方式 | 理由 |
| --- | --- | --- |
| `adapters/framework_kefu/` | `from adapters.textmatch import ...`；`normalize.py` / `hit_query.py` 保留为 **re-export 薄壳** | 让 kefu 桥与 MCP 消费**同一份**实现，消除"同一句两入口不同判" |
| `adapters/mcp_vox/` | `from adapters.textmatch import find_hit` | 同上；且这**不是** §⑤ 禁止的「跨适配器业务耦合」——本层是无业务逻辑的公共部件，mcp_vox 不 import `framework_kefu`、kefu 也不 import `mcp_vox`，家族成员之间仍然零依赖 |

**允许依赖**：`runtime` / `assets` 的**公开 `__all__`**（`REASON_KEY_NOT_PREBAKED` /
`AssetEntry`）——与 `adapters/AGENTS.md §⑨` 给 `framework_kefu` / `mcp_vox` 的
「core + runtime/assets 公开面」同款放宽。理由：命中判定要读资产包并做指纹复核，
必然触碰 `assets` 的公开面；`REASON_KEY_NOT_PREBAKED` 来自 `runtime` 的原因码表。
**边界仍硬**：不得 import 内部私有名（下划线），只走公开 `__all__`。

**禁止**：import 其他适配器（`framework_kefu` / `mcp_vox` / `tts_*` / `asr_*` 任一）；
import 冻结区内部实现；新增第三方依赖；把判定搬进 `runtime/` 或 `core/`（冻结区）。

## ⑥ 变更纪律

改动本层 = 改**跨入口命中口径**，影响面是全链路：先按冻结区流程评估，
再改，且必须**同批**验证两条消费路径（`framework_kefu` 与 `mcp_vox`）。
只改一侧、或只让一侧的测试变绿，都算未完成——本层存在的全部理由就是消灭"只对一边生效"。
`docs/10 §10.3` 与 §10.7 的判据本身是冻结的；改判据不是本层的自由。

## ⑦ 冻结状态

**[扩] 扩展区**。目录可增、不可减；本层的**语义**（四步归一化、逐字相等、逐字覆盖）
随 `docs/10` 冻结，不因"扩展区好改"而松动。`framework_kefu` 侧的两个 re-export 薄壳
是**过渡兼容层**，不是第二份实现——任何时候都不得在薄壳里写回函数体。
