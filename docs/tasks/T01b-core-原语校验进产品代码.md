# T01b · core 修订：原语校验必须进产品代码（T01 退回项）

## 背景

T01（首次派发）产物已回收，验收结论 **部分通过、两条退回**：

- ❌ **缺陷 1（静默接受未知原语）**：`core/protocol.py` 里 `PRIMITIVES` 只是常量，**没有任何地方使用它**；实测 `parse_plan([{"key":"greet","action":"SING"}])` **未报错、解析通过**——未知原语被静默接受。违反本层纪律（未知原语必须报错，禁止静默降级）。
- ❌ **缺陷 2（测试空转）**：`core/tests/test_protocol.py` 里把校验函数 `_validate_primitive_or_raise` **定义在测试文件内部**，于是"测试"测的是测试自己写的辅助函数，**根本没有调用产品代码**（测试名还写着 `unknown_primitive_in_unit`，名实不符）。

本卡只修这两条。T01 已通过的其余部分（rate 校验 / key-text 互斥 / 类型校验 / 三档覆盖 / 指标字段）**不要改动**。

必读：`AGENTS.md`、`core/AGENTS.md`、`docs/tasks/T01-core-协议与schema.md`（原卡）。**不得修改这些文件。**

## 目标

1. **产品代码**：`core/protocol.py` 增加原语的语法位置与校验——
   - 播报单元支持**可选** `action` 字段（原语名，如 `"SAY"`）；
   - `action` **缺省**时按结构推断：有 `key` → `SAY`，有 `text` → `SAY_LIVE`（这是结构默认，不是静默回落，允许）；
   - `action` **显式给出时必须是 `PRIMITIVES` 成员**，否则抛 `ProtocolError`，**消息必须包含该非法原语名**；
   - 解析路径必须真的调用该校验（即 `parse_plan` 校验 `action`）；
   - 新增公开函数 `validate_primitive(name: str) -> str`（不在白名单则抛 `ProtocolError`）并从 `core/__init__.py` 导出。
2. **测试**：`core/tests/test_protocol.py` 中所有原语相关测试**必须通过产品 API 触发**——
   - 删除测试文件内部的 `_validate_primitive_or_raise` 辅助函数；
   - 负例改为走 `parse_plan([{"key": "greet", "action": "SING"}])`，断言抛 `ProtocolError` 且消息含 `"SING"`；
   - 另加一条负例：`validate_primitive("SING")` 直接调用产品函数，同样断言报错含名；
   - 加一条正例：`action` 缺省时按结构推断（key→SAY、text→SAY_LIVE）可断言。

## 允许修改的文件（白名单）

```
允许修改：core/protocol.py, core/tests/test_protocol.py, core/__init__.py
允许新增：无
禁止触碰：其余一切文件（含 core/metrics_spec.py、core/tests/test_metrics_spec.py、README.md、AGENTS.md、docs/**）
```

## 禁止事项

- **不得在测试文件内复制/重写任何校验逻辑**（这会再次变成"测试自测自"）。
- 不得放宽既有校验（rate / key-text 互斥 / 类型校验必须保持原强度）。
- 不得新增第三方依赖。
- 不得改动本卡白名单之外的文件；不得顺手重构。

## 验收标准（我会逐条核对，逐条要证据）

1. `python3 -m unittest discover -s core/tests -v` 全绿；
2. **负例（关键）**：`parse_plan([{"key":"greet","action":"SING"}])` → `ProtocolError`，消息含 `SING`；
3. `validate_primitive("SING")` → `ProtocolError`，消息含 `SING`；`validate_primitive("SAY")` → 返回 `"SAY"`；
4. `parse_plan([{"key":"greet"}])` 与 `parse_plan([{"text":"x"}])` 均解析成功，且推断出的 action 分别为 `SAY` / `SAY_LIVE`；
5. 测试文件中**不再存在**任何自定义校验辅助函数（我会 grep 核对）；
6. T01 已通过的用例数量不减少（原 40 条中与本次无关的必须全部保留通过）。

## 回滚方式

`git checkout -- core/`（本卡只改既有文件，不改结构）。

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T01b-core-原语校验进产品代码.md)" --dir （仓库根）
```

## 卡状态

- [x] 已派发（2026-09-17 00:32–00:36，`opencode/mimo-v2.5-free`，4 分 16 秒）→ [x] 已回收 → [x] **验收通过**
- 验收证据（我逐条核对，见 00-索引.md）：44/44 测试全绿；`parse_plan(action="SING")` 与 `validate_primitive("SING")` 均抛错且消息含名；`action` 缺省按结构推断（key→SAY / text→SAY_LIVE）；测试文件内无自造校验函数；原 40 条用例名逐条保留；`ProtocolError` 抛出点 8 → 9（只增未减，校验强度未放宽）。
