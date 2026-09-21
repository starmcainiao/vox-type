# T01 · core：协议与 schema（原语集合 / plan 解析 / 参数覆盖 / 指标字段）

## 背景

这是整个项目的**最底层内核**（冻结区）。后续所有层（规则、资产包、执行器、评测）都依赖这里定义的协议与字段名。
本卡只做**协议与语义**，不做任何音频、不做业务话术。

必读（仓库内）：`AGENTS.md`（项目纪律）、`core/AGENTS.md`（本层七项）。**不得修改这两个文件。**

## 目标（可验收的产物）

- `core/__init__.py` — 导出公共接口
- `core/protocol.py` — 协议与语义：原语集合、plan 数据结构、解析与校验、参数覆盖优先级
- `core/metrics_spec.py` — 指标字段名常量（冻结定义）
- `core/tests/test_protocol.py` — 正例 + 负例测试
- `core/tests/test_metrics_spec.py` — 字段名钉住测试

### 具体要求

1. **原语集合（封闭白名单）**：`SAY` / `SLOT` / `SAY_LIVE` / `PAD` / `LISTEN` / `PRELOAD` / `END`。
   - 解析时遇到未知原语 → 抛 `ProtocolError`，**错误信息必须包含该原语名**。
2. **plan 结构**：一份 plan 是「一个或多个播报单元」的序列；每个单元形如
   `{"key": "<话术key>", "rate": "slow|normal|fast", "variant": <int|"auto">, "slots": {"<槽位名>": "<值>"}}`，
   或自由文本单元 `{"text": "<文本>", "rate": ...}`。
   - `key` 与 `text` 二选一，**都缺或都给 → 报错**。
   - `rate` 只允许 `slow` / `normal` / `fast`；其他值 → 报错（**不得静默回落到 normal**）。
   - `variant` 允许整数或 `"auto"`。
3. **参数覆盖优先级**（三档，后者覆盖前者）：**业务默认 < 会话覆盖 < 剧本显式**。
   - 提供函数 `resolve_params(business_default: dict, session_override: dict, utterance_explicit: dict) -> dict`，
     合并顺序必须可测。
4. **指标字段定义**（`metrics_spec.py`，命名为常量导出，本卡冻结）：
   `SKIP` 不存在——必须包含至少：`HIT` / `MISS` / `FALLBACK` / `REASON` / `KEY` / `PART` / `RATE` / `VARIANT` /
   `FIRST_AUDIO_MS` / `PACK_VERSION` / `TS` / `TURN_ID` / `PLAN_ID`，以及比率字段
   `HIT_RATE` / `PRECAST_RATIO`。每个常量附一句中文注释说明口径。

## 允许修改的文件（白名单）

```
允许新增：core/__init__.py, core/protocol.py, core/metrics_spec.py,
          core/tests/__init__.py, core/tests/test_protocol.py, core/tests/test_metrics_spec.py
允许修改：无
禁止触碰：其余一切文件（尤其 README.md / AGENTS.md / docs/** / 其他层目录）
```

## 禁止事项

- **不得新增第三方依赖**（只用 Python 标准库：`json` / `dataclasses` / `unittest` / `typing`）。
- 不得实现音频、TTS、资产包、校验器（那是 T03/T04/T05/T06 的活）。
- 不得"顺手优化"或改动白名单外文件；不得改动本卡以外的任何格式。
- 不得为通过测试而放宽校验（校验必须严格，测试必须反映真实约束）。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s core/tests -v` **全绿**；
2. **负例 1**：未知原语（如 `SING`）→ `ProtocolError` 且消息含 `SING`；
3. **负例 2**：`rate="very_slow"` → 报错（不回落）；
4. **负例 3**：单元同时缺 `key`/`text`，或同时给两者 → 报错；
5. **正例**：合法 plan 解析成功，字段可访问；
6. 覆盖优先级三档用例各一（业务默认 < 会话覆盖 < 剧本显式）；
7. 指标字段以常量导出；测试**钉住字段名**（改名字必须导致测试失败）；
8. 代码含**方法级中文注释**（职责/参数/返回/边界）与关键步骤 WHY 注释。

## 回滚方式

本卡只新增文件，未改动既有文件 → 回滚 = `git clean -fd core`（并删除空目录）。

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T01-core-协议与schema.md)"
```

## 卡状态

- [x] 已派发（2026-09-17 00:0x，`opencode/mimo-v2.5-free`，21 分钟）→ [x] 已回收 → [ ] 验收通过 / [x] **退回（部分）**
- 退回项（2026-09-17，我源码级核对）：
  1. **未知原语被静默接受**：`PRIMITIVES` 只是常量、解析路径从不调用它；实测 `parse_plan([{"key":"greet","action":"SING"}])` 未报错 → 违反"未知原语必须报错"；
  2. **测试空转**：校验函数 `_validate_primitive_or_raise` 定义在测试文件内部，测试测的是自己写的辅助函数，从未调用产品代码。
- 其余 5 项（rate 校验 / key-text 互斥 / 类型校验 / 三档覆盖 / 指标字段+注释）**验收通过**。
- → 修订卡 `T01b-core-原语校验进产品代码.md` 已派发并**验收通过**（见 00-索引.md 验收记录）。
