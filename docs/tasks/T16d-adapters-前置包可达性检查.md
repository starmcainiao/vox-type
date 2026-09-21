# T16d · adapters：前置包 key / 字段可达性检查（报告类 API）

## 背景（只写必需）

前置包有两类「声明了但触发器用不到」的死重量，目前没有任何工具能查：

1. **话术 key 没有被任何规则引用**——`load_trigger` 只校验反方向（规则引用的 key 必须在
   phrases.json 里存在，C3b 口径），不校验「phrases.json 里的 key 是否都被某条规则的
   units 引用过」。没人引用的 key 永远不会被前置包播出来；
2. **state 字段既不在任何 `when` 里、也不在任何 `slots` 里**——声明了却参与不了任何判定
   或拼接，属于配置死重量。

实测基准（2026-09-19 程序化核对 `packs/demo-brief`，验收按这三个数对）：
- phrases.json 共 **8** 个 key；trigger 规则引用 **6** 个；**未被任何规则引用的 key 恰为 2 个**：
  `offer_help`、`ticket_status`（它们被同包的 script.json 用到——双形态包合法，所以本检查是
  **报告不是报错**）；
- 已声明 state 字段 **6** 个；`overdue_days` 虽无任何 `when` 依赖，但被 `overdue` 规则的
  slots 引用，**算被引用**；既不在 when 也不在 slots 的字段**恰为 1 个**：`ticket_id`。

**语义裁定（写在卡里，执行方不得自行升级）**：本检查**只报告、不报错**——返回清单，
由调用方决定怎么处置。理由：双形态包（trigger.json + script.json 并存）里
「key 只被 script.json 用」是合法形态；fail-closed 红线管的是执行路径的静默降级，
不是资产清单的盘点报告。

规格依据：`docs/13-未完成清单.md` §四 第 3 条（跨批待办 3）、`docs/tasks/00-索引.md` T16 验收记录 ⑤。

## 目标（可验收的产物）

- 产物 1：`adapters/state_trigger/reachability.py` —— 公开 API：
  ```python
  @dataclass(frozen=True)
  class ReachabilityReport:
      unreferenced_keys: Tuple[str, ...]         # 排序后的 key 元组
      unreferenced_state_fields: Tuple[str, ...] # 排序后的字段名元组

  def check_reachability(trigger: Trigger) -> ReachabilityReport: ...
  ```
  判定口径：
  - `unreferenced_keys` = `trigger.phrase_keys` − 所有规则所有单元的 `key`；
  - `unreferenced_state_fields` = 已声明字段 − (所有 `when`（非 null）里的字段名 ∪ 所有单元 `slots` 里的名字)；
  - 两个元组**排序输出**（确定性）；不抛业务异常、不写盘、不做 I/O（输入就是已装载的 Trigger）；
- 产物 2：`adapters/state_trigger/tests/test_reachability.py` —— 对应测试；
- 产物 3：`adapters/state_trigger/__init__.py` **纯追加**导出 `ReachabilityReport` / `check_reachability`；
- 产物 4：模块 docstring 写清「报告不是报错」的语义裁定与判定口径（方法级中文注释：
  职责 / 参数 / 返回值 / 边界 / 调用方）。

## 允许修改的文件（白名单）

```
允许新增：adapters/state_trigger/reachability.py
允许新增：adapters/state_trigger/tests/test_reachability.py
允许修改：adapters/state_trigger/__init__.py（只允许追加导出，不得删改既有行）
禁止触碰：其他一切文件——尤其 format.py / trigger.py（本卡零改动）、packs/、docs/、cli/
```

## 禁止事项

- **不改 `format.py` / `trigger.py`**（装载期校验与运行时匹配都不动；本卡是独立的报告 API）
- **不做 CLI 接线**（`vox verify` / `pack check` 是否挂本检查属 cli/ 冻结区，另行开卡；本卡只交付库 API）
- 不得 import `format.py` / `trigger.py` 的私有符号（下划线开头的一律不许用；只依赖
  `Trigger` / `TriggerRule` / `PlanUnit` / `StateField` 公开结构）
- 不得读盘、不得 import compiler（Trigger 里已带 phrase_keys / state_fields / rules，够用）
- 不得把报告升级成报错（语义裁定见上，不得自行收紧）
- 不得新增第三方依赖；不得"顺手优化"

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿，测试数 **只增不减**；
2. **活体基准（demo 包）**：`check_reachability(load_trigger('packs/demo-brief'))` →
   `unreferenced_keys == ('offer_help', 'ticket_status')` 且
   `unreferenced_state_fields == ('ticket_id',)`——与卡内实测基准**逐个相等**；
3. **正例（全引用）**：合成 Trigger——每个 key 都被某单元引用、每个字段都被 when 或 slots 引用 →
   两个元组全空；
4. **只被 slots 引用算被引用**：合成 Trigger 里一个字段只出现在某单元 `slots`（不在任何 when）→
   它**不出现**在 `unreferenced_state_fields`（这是 `overdue_days` 的实际形态，必须为它立用例）；
   同理只出现在 when 的字段也不算未引用；
5. **报告不抛错**：对含未引用 key / 未引用字段的 Trigger，`check_reachability` 返回报告而**不抛异常**
   （报告语义的负向断言）；
6. **确定性**：同一 Trigger 调两次 → 两个报告相等（frozen dataclass 可比较）；
7. `python3 -c "from adapters.state_trigger import check_reachability, ReachabilityReport"` 可导入；
8. `grep -rn "from adapters.state_trigger.format import _\|from adapters.state_trigger.trigger import _" adapters/state_trigger/reachability.py` → 0 命中（私有符号禁令）；
9. `git status --porcelain -uall` 恰为白名单 3 个文件（2 新增 + `__init__.py` 追加），无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 测试必须调用产品 API `check_reachability`；不得在测试文件内自造「可达性判定」逻辑再拿它当期望值
  （合成 Trigger 的期望清单可以手写——那是测试数据，不是被验逻辑的复制）；
- 测试名与实际调用路径一致；正例与「报告不抛错」的负向断言都要有；
- 不得为通过测试而放宽判定口径（比如把「slots 引用也算」偷偷改成「只有 when 算」）。

## 回滚方式

`git clean -fd adapters/state_trigger/reachability.py adapters/state_trigger/tests/test_reachability.py`；
`git checkout -- adapters/state_trigger/__init__.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T16d-adapters-前置包可达性检查.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**——只涉及合成 fixture 与 demo 包。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T16d 节）
  - 验收方独立复跑：adapters 277 全绿（262→+15）；活体基准两元组逐个相等（`('offer_help','ticket_status')` / `('ticket_id',)`）；确定性两次相等、frozen 拦截改写 ✓；`__init__.py` +8/-0 纯追加；format.py/trigger.py/packs 零 diff。
  - **边界留档**：空规则集（`rules=()`）组合只经公开 dataclass 构造测试，未经 `load_trigger` 活体（装载器本就禁止空 rules，二者不冲突）；报告区分「key 只被 script.json 用」的合法形态需交叉读 script.json，属后续口子，当前实现刻意只盘点 trigger 侧。
