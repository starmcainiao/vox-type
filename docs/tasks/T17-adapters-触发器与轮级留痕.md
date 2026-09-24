# T17 · adapters：触发器 + 轮级留痕（`state` → plan，并留下可训练的配对）

## 背景（只写必需）

T16 定死了 `state` 快照格式与 `packs/<前置包>/trigger.json` 的映射格式（`adapters/state_trigger/format.py`）。
本卡把**行为**补上：**给定一份 state 快照，产出一份 plan**，并把**这一轮的 (状态, 打算说什么) 留下来**。

**为什么留痕要在这张卡里做**（不是"以后再说"）：`docs/12 §12.5` 定了微调弧线——前置包在运行中
顺带产数据，攒够了可以微调出更懂本业务的模型。而**现在不做，事后补要改已冻结的事件契约**。
两条通道（`docs/12 §12.10`）：

- **正样本「该说什么」** ← 本卡的**轮级留痕**（state 快照 + 产出的 plan）
- **负样本「哪里没覆盖」** ← 既有事件流的 `miss` + `reason`（运行时天然产出，**本卡不碰**）

**落点**：`adapters/`（扩展区）。理由见 `docs/12` 的「分层纪律」——`state → plan` 是"决定说什么"，
属接缝之上，不属于本层，因此不进 `runtime/`。

规格依据（执行前先读）：
- `docs/12 §12.5`（微调弧线与两条数据通道）、`§12.10`（留痕与存储：DB 还是日志）、`§12.11`（三个部件）
- T16 的产物：`adapters/state_trigger/format.py`（**本卡必须复用它的校验器，不得重写**）
- `core/metrics_spec.py`（`turn_id` / `plan_id` 字段名**必须引用它，不得自造**）
- `eval/AGENTS.md`（报告纪律：原始数据要落盘、可复查、不得美化）

## 目标（可验收的产物）

- 产物 1：`adapters/state_trigger/trigger.py` —— `build_plan(trigger, state) -> Plan`，**纯函数、确定性**
- 产物 2：`adapters/state_trigger/ledger.py` —— **轮级留痕**：`record_turn(...)`，JSONL 追加写
- 产物 3：`adapters/state_trigger/tests/test_trigger.py`、`adapters/state_trigger/tests/test_ledger.py`
- 产物 4：`adapters/state_trigger/README.md` —— 口径、复现命令、与 T16 格式的关系（**含一条"只提案不出声"的边界声明**）

### 产物 1 的硬要求

1. **只产出已审核的话术**：plan 里每个单元的 key 必须来自 `phrases.json`（T16 的校验器已保证，这里**再断言一次**——
   两道闸门不算冗余，因为 plan 是运行时产物）；
2. **确定性**：同一 `state` + 同一 `trigger.json` → plan **逐字段相同**（含顺序、`rate`、变体选择）；
   变体若用随机/散列选择，**必须**由 `turn_id` 派生（照 `runtime/executor.py` 对 `variant: auto` 的既有做法），
   使同一轮重跑得到同一结果；
3. **无条件命中 → fail-closed**：没有任何规则匹配该 state → **抛错**（`TriggerError`），
   **不得返回空 plan、不得回落到某条默认话术**（对齐仓库红线：禁止静默降级）；
4. **预算强制**：`build_plan` 内部必须调 T16 的 `check_budget`；超限抛错，不产出 plan。

### 产物 2 的硬要求

5. **留痕内容 = 结构化层 + plan**：一条记录含
   `turn_id` / `plan_id` / `ts` / `trigger_id`（或 `rule_id`，哪条规则触发的）/ `state`（**只放结构化层**）/ `plan`。
   字段名 `turn_id` / `plan_id` **必须引 `core.metrics_spec`**；
6. **敏感层绝不落盘**：留痕文件里**不得出现** state 敏感层的任何值（`docs/12 §12.10` 末的红线）；
7. **留痕可还原监督配对**：从一条记录必须能取出 `(state 结构化层, plan 引用的 key 列表)`——
   这是它作为训练数据的全部意义；
8. **落盘位置由参数指定**，缺省落**仓外**；**不得**把留痕写进仓库目录（防止私人数据入仓）；
9. **写入失败 fail-closed**：不得静默丢弃。抛 `LedgerError`，消息含**目标路径**与失败原因。
   （"留痕失败要不要阻断播报"是**调用方的决定**，本模块只负责把失败**响亮地**交出去。）

## 允许修改的文件（白名单）

```
允许新增：adapters/state_trigger/{trigger.py,ledger.py,README.md}
允许新增：adapters/state_trigger/tests/{test_trigger.py,test_ledger.py}
允许修改：adapters/state_trigger/__init__.py（只允许追加导出，不得删改既有导出）
允许修改：adapters/state_trigger/format.py —— **只允许改一处**：把 `from compiler.source import SourceError, load_source`
          改为从**公开面** import（`from compiler import SourceError, load_source`）。
          理由：`adapters/AGENTS.md §⑤` 禁止 import compiler 的**内部实现**；
          而 `load_source` / `SourceError` 都在 `compiler/__init__.py` 的 `__all__` 里，
          走 `compiler.source` 是内部模块路径（本卡第 ⑧ 节登记的例外条款第 1 条）。
          **除这一行外不得改动 format.py 的任何字符**（含注释与空行）。
禁止触碰：其他一切文件——尤其不得改 core/ rules/ compiler/ assets/ runtime/ eval/ cli/（冻结区）、
          不得改 docs/、不得改 README.md / AGENTS.md、不得改 packs/
```

**前置条件**：T16 已验收通过且**已提交入库**（否则本卡的"允许修改"没有可回滚的目标）。

## 禁止事项

- **不得新增第三方依赖**（只用标准库 + 仓库自身模块）
- **不得改冻结区任何文件**；若判断必须改，**停下来在报告里说明**
- **不得重写 T16 的校验器**（`format.py` 是既有产物，本卡复用）
- 不得"顺手优化"、"顺手重构"、不得改动与本卡无关的格式
- **不得在测试里联网**；**测试不得依赖真实时钟**（时间戳要能注入，否则测不了确定性）
- 不得为了让测试好写而放宽校验：无条件命中 / 超预算 / 未审核 key / 写入失败**一律抛错**

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿（**注意是 `-s adapters`**）；
   且基线不破：T16 之后是 N 条（含 T16 新增），本卡之后必须 **≥N 且全绿**；
2. **正例**：对 `packs/demo-brief/` 造一份合法 state → `build_plan` 产出 plan，
   其每个 key 都 ∈ `phrases.json`；调 `record_turn` 写出一条留痕，文件行数 +1；
3. **负例（无条件命中）**：造一份**没有任何规则匹配**的 state → 抛 `TriggerError`，
   **不得返回空 plan**（断言：没有产出任何 plan 对象、没有落任何留痕）；
4. **负例（超预算）**：把某条规则的 plan 撑到超过 `turn_budget_chars` → 抛错，消息含实际字数与预算；
5. **负例（敏感层落盘）**：state 带敏感层值 → 调用 `record_turn` 后
   **在留痕文件里 grep 该敏感值必须为 0 命中**（这条是硬红线，必须实测断言）；
6. **负例（写入失败）**：把留痕目标设成一个不可写路径（如指向只读目录或已存在的目录名）→
   抛 `LedgerError`，**消息含该路径**；
7. **确定性**：同一 `state` + 同 `trigger.json` 跑两次 → plan **逐字段相同**；
   变体选择若受 `turn_id` 影响，**同一 `turn_id` 重跑必须同结果**（写进断言）；
8. **留痕可还原配对**：从留痕文件读回一条记录，能取出 `(state 结构化层, plan key 列表)`，
   并断言**敏感层不在其中**；
9. **字段名合规**：`grep` 断言留痕的 `turn_id` / `plan_id` 字面量与 `core/metrics_spec` 的常量一致
   （不得自造字段名）；
10. `git status --porcelain -uall` 的新增/修改文件**全部**落在白名单内；
    冻结区零 diff（我会跑 `git diff --stat -- core rules compiler assets runtime eval cli` 应为空）；
11. **依赖改走公开面**：`grep -n 'from compiler.source' adapters/state_trigger/format.py` → **0 命中**；
    `grep -n 'from compiler import' adapters/state_trigger/format.py` → **≥1 命中**；
    且 `adapters/state_trigger/format.py` 除该行外**逐字节未变**（我会用 `git diff --numstat` 核对增删行数）；
12. 冻结区零 diff 之外，还要断言**内核没有反向依赖本模块**：
    `grep -rn 'state_trigger' runtime/ assets/ eval/ cli/ compiler/ core/` → **0 命中**。

## 反空转条款（每张卡必带，T01 教训）

- **测试必须调用产品 API**：必须 import `adapters.state_trigger` 的公开函数，
  不得在测试文件里复制/重写被验逻辑（违者整卡退回）；
- 测试名必须与实际调用路径一致；
- **正例与负例都要有**，且负例断言**错误消息包含具体非法值**（见第 3/4/6 条）；
- 不得为通过测试而放宽校验；既有校验强度只增不减。

## 回滚方式

新增文件 → `git clean -fd adapters/state_trigger/{trigger.py,ledger.py,README.md, tests/test_trigger.py, tests/test_ledger.py}`；
`__init__.py` 的追加导出用 `git checkout -- adapters/state_trigger/__init__.py` 单独还原。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T17-adapters-触发器与轮级留痕.md)" --dir （仓库根）
```

**数据分级：公开级**——只用 `packs/demo-brief/` 的公开 demo 数据，不含任何用户录音、真实会话、内网地址、token。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）
