# T17c · adapters：规则匹配公开 API（`rule_matches` 更名公开 + labs 迁移）

## 背景（只写必需）

`trigger.py::_rule_matches` 是规则匹配的**唯一实现**（兜底语义、字段缺值不命中、区间比较都在里面）。
labs 的规则覆盖表（`labs/ticket-source/run_e2e.py`）已经在用它生成
`covered_by_ticket_id` —— 当时用的是**私有名**（下划线开头），这是 T21b 验收记录里明文登记的权宜：
「替代方案是在 labs 复制匹配逻辑，那违反『不复制校验逻辑』；但已登记待办：若长期要用，
应在 adapters/state_trigger 暴露公开匹配 API」。

覆盖表这类工具以后还会写（可达性检查、golden set 抽查），私有名不再是权宜而是长期依赖 →
按登记待办显式公开。**本卡不改任何匹配语义**，只做「更名公开 + 导出 + 调用点迁移」。

规格依据：`docs/13-未完成清单.md` §四 第 5 条；`docs/tasks/00-索引.md` T21b 验收记录裁定 ①。

## 目标（可验收的产物）

1. `trigger.py`：`_rule_matches` **更名为公开 `rule_matches`**（公开函数要带方法级中文注释块：
   职责 / 参数 / 返回值 / 边界 / 调用方），`build_plan` 内部调用点同步改名；
   **私有名一个不留**（不留 `_rule_matches = rule_matches` 这类别名影子——与行为不符的名字
   必须消失，T21e 的教训）；
2. `adapters/state_trigger/__init__.py`：**纯追加**导出 `rule_matches`（`__all__` 同步追加一行）；
3. `labs/ticket-source/run_e2e.py`：两处代码（`from adapters.state_trigger.trigger import _rule_matches`
   的 import 与唯一调用点）改用公开名；三处**说明文字**里出现的 `_rule_matches` 字样同步改为
   `rule_matches`（说明文字不许指向一个已经不存在的名字——否则就是新的假陈述）；
4. `tests/test_trigger.py`：**新增**公开名的直接单测 ≥3 条（兜底 when=None → True；
   字段缺值 → False；区间 {"gt"/"lte"} 两种各一条），既有用例一行不改。

## 允许修改的文件（白名单）

```
允许修改：adapters/state_trigger/trigger.py
允许修改：adapters/state_trigger/__init__.py（只允许追加导出，不得删改既有行）
允许修改：adapters/state_trigger/tests/test_trigger.py（只允许新增用例，不得删改既有用例）
允许修改：labs/ticket-source/run_e2e.py（只允许改名与上述说明文字，不得改判定逻辑）
禁止触碰：其他一切文件——尤其 format.py、ledger.py、packs/、docs/、summary.json / source_map.json
          （它们由 run_e2e.py 重跑再生，不许手改）
```

## 禁止事项

- **不改匹配语义**：兜底命中 / 字段缺值不命中 / TypeError 不命中 / 区间比较——一行逻辑都不许动
- 不留 `_rule_matches` 别名或任何下划线影子名
- 不得手改 `summary.json` / `source_map.json` / `raw/turns.jsonl`（它们是脚本产物，重跑再生）
- 不得新增第三方依赖；不得"顺手优化"

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s adapters` 全绿，测试数 **只增不减**（新增 ≥3 条公开名直测）；
2. `python3 -c "from adapters.state_trigger import rule_matches; print(rule_matches)"` 可导入；
3. **影子名清零**：`grep -rn "_rule_matches" --include='*.py' --include='*.json' --include='*.md' .`
   （仓库根起，排除 `.git`）→ **0 命中**；
4. **labs 重跑可再生**（验收方会亲自跑）：`python3 labs/ticket-source/run_e2e.py` →
   **真退出码 0（不许接管道量 `$?`）**，断言条数与改动前一致（148 条，只许多不许少），
   重跑后 `git diff --stat labs/ticket-source/` 里只允许出现本卡白名单内的文件
   与 `summary.json`（`generated_from` 字段随脚本再生而更新，属正常）；
5. **语义零变化（行为对照）**：`packs/demo-brief` 走 `build_plan`——构造条件规则全不匹配的
   state → 命中兜底 `closing`；构造 `is_overdue=true` → 命中 `overdue`。两条行为与改名前一致；
6. `git status --porcelain -uall` 恰为白名单 4 个文件的改动（+ 重跑再生的 `summary.json`），无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 新增测试必须直接调用公开的 `rule_matches`，不得绕道 `build_plan` 间接覆盖了事（间接覆盖已有，
  本卡要的是公开 API 的直接锚点）；
- 正例与负例都要有；不得为通过测试而放宽既有断言（既有用例一行不改是硬条件）。

## 回滚方式

前三个白名单文件：`git checkout -- adapters/state_trigger/`；
labs 文件：`git checkout -- labs/ticket-source/run_e2e.py`；
再生产物若残留：`git checkout -- labs/ticket-source/summary.json`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T17c-adapters-规则匹配公开API.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**——demo 包与合成 fixture。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T17c 节）
  - 验收方独立复跑：adapters 284 全绿（277→+7）；`from adapters.state_trigger import rule_matches` 可导入、`_rule_matches` 私有名已消失；demo 包语义对照——全不匹配 state → 兜底 `closing` ✓、`ticket_status=处理中` → `greeting` ✓（第一次探针误判是我自己的 state 构造落进了 `due_days_left` 区间，非产品问题）；labs 重跑（不接管道）rc 0、148 条断言不变。
  - **裁定 1（卡判据错，实现对）**：验收 3 的子串 grep 数学上不可达——会命中历史卡/文档、本卡自身、以及 `test_rule_matches_*` 新测试方法名（非影子名）。判据按本意解释为「**活代码零引用**」：产品代码 import/调用/定义处 `_rule_matches` = 0（已实测）。
  - **裁定 2（转 T17d）**：真实残留 5 处说明文字（format.py:618、test_format.py:582/709——本批 T16c 新写的注释、labs README:101/149、source_map.json:66 的 verification 串）指向已不存在的名字，按「不留假陈述」开 T17d 微型卡清理，不在本卡扩权。
  - **裁定 3（接受执行方取舍）**：新注释块不搬旧 docstring 的「或缺省」提法——实测 `when` 属性缺失抛 `AttributeError` 而非命中，旧话与行为不符，按 T21e 教训改写为「when 属性必须存在」。
