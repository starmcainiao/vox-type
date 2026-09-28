# T21d · labs：把文字陈述的自校验从「一段」扩到「整个目录」（**本线最后一张清理卡**）

## 背景（只写必需）

T21c 把 `source_map.json#rule_reachability` 那段错陈述改对了，并加了自校验断言——**但扫描范围只限那一段**。
结果：同一个文件里**另一处仍然写着错的因果**，我去查了原文并程序化核实：

`source_map.json#_why_is_as_of_still_needed` 里写着
> "…但**没有任何一条规则的 when 依赖它们**（`days_left` / `is_overdue` / `overdue_days`）…"

我程序化核对 `packs/demo-brief/trigger.json` 的结果（实测）：

| 那句声称 | 事实 |
|---|---|
| 没有规则依赖 `days_left` | ❌ **假**——`due_tonight`、`due_days_left` 两条规则的 `when` 依赖它 |
| 没有规则依赖 `is_overdue` | ❌ **假**——`overdue`、`due_tonight`、`due_days_left` 三条依赖它 |
| 没有规则依赖 `overdue_days` | ✓ 真（确实 0 条） |

同一句里的第二个子句（"`days_left` 的 `min=0` 使负值无法表示"）是**成立**的——
它讲的是日期运算的约束，与"规则是否依赖"是两件事。

**为什么这张卡不是"再改一句话"**：那一段的错陈述之所以还在，是因为**自校验只盯了一段**。
只改这一句，下一句错陈述还会溜过去。所以本卡要做的是**把机制覆盖面扩到整个目录**，
而不是继续打地鼠。**本卡做完，本目录的文字陈述即由自校验托管，不再单开清理卡。**

顺带记录一个同类发现（本卡一并向同一套机制纳管）：`overdue_days` 被声明为 `state_field`
但**没有任何规则引用它**——与之前登记的"`phrases.json` 里有 key 只被 `script.json` 引用"同类。

## 目标（可验收的产物）

- 产物 1：`labs/ticket-source/` 下**所有文字陈述**做一次贯通审计：
  逐条列出**可判定的事实陈述**，标注它"由什么验证"；与实际不符的**改正**
- 产物 2：`labs/ticket-source/run_e2e.py` —— **一致性/禁语扫描从单段扩到整个目录**
  （`source_map.json` / `README.md` / `tickets.source.json#_fixture_meta` / `run_e2e.py` 自身的说明文字）
- 产物 3：`labs/ticket-source/README.md` —— 补一节「本目录的文字陈述由什么守着」
- 产物 4：重跑产物 `summary.json` / `raw/turns.jsonl`

## 允许修改的文件（白名单）

```
允许修改：labs/ticket-source/source_map.json
允许修改：labs/ticket-source/README.md
允许修改：labs/ticket-source/run_e2e.py
允许修改：labs/ticket-source/tickets.source.json（**仅限 `_fixture_meta` 等注解文字**，
          不得改任何一行 fixture 数据）
允许修改：labs/ticket-source/raw/turns.jsonl、summary.json（重跑产物）
禁止触碰：其他一切文件——不得改 packs/ adapters/ core/ rules/ compiler/ assets/ runtime/ eval/ cli/ docs/
```

## 禁止事项

- **不得改 `packs/demo-brief/`**（本卡只改 labs 的文字与断言）
- **不得改 `source_map.json` 里 `state_fields` / `sensitive_fields` / `derived_fields` / `as_of` 的数据语义**
  （只改说明文字）
- **不得删既有断言**（T21c 后是 109 条，只增不减）
- 不得放宽 T16 任何校验；不得改 `adapters/`；不得联网；不得新增依赖
- **不要 `git add` / `git commit`**；**不要做达标判定**
- **不得把"错的陈述"改成模糊表述来规避**（例如把"没有任何规则依赖"改成"这些字段用得不多"）——
  必须改成**与实测一致的具体陈述**（写明哪几条规则依赖它）

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 labs/ticket-source/run_e2e.py` → **rc 0**；断言数 **≥109**（只增不减）；
2. **那条错陈述已改正且可核**：`_why_is_as_of_still_needed` 里对"规则是否依赖字段"的表述，
   必须与 `trigger.json` 的实际 `when` 引用一致（`days_left` 2 条、`is_overdue` 3 条、`overdue_days` 0 条）；
   断言要**从 `trigger.json` 现算**引用关系再与文字比对，**不得在断言里硬编码这三组数字**；
3. **扫描覆盖面已扩到整个目录**（本卡核心）：断言必须扫
   `source_map.json` + `README.md` + `tickets.source.json` 的注解文字 + `run_e2e.py` 的说明文字，
   而不再是单段；且给出"扫了几个文件、扫了多少字符"的可核对信息；
4. **扩面后的自校验必须能被打破**（三种状态实测并贴输出）：
   - 改前 rc 0；
   - 在 **README.md**（不是 source_map.json）里写入一句被禁的旧因果 → **rc != 0** 且消息**指名是哪个文件**；
   - 还原后 rc 0；
5. **`overdue_days` 无规则引用这件事被如实记录下来**（写进 README 或 `summary.json` 的一个明确字段），
   **不得**为了让"全部字段都被引用"去改包；并说明它是否属于"前置包的 key/字段可达性检查"待办（跨批待办 3）；
6. **逐条事实陈述清单**落盘（`summary.json` 里一节即可）：每条含"陈述出处（文件）+ 判定方式 + 结论"；
   凡举不出判定方式的陈述，**要么删掉、要么改成可判定**；
7. **可复现**：两次运行 `summary.json` 逐字段相同（除时间戳类）＋ `raw/turns.jsonl` 逐字节相同；
8. `git status --porcelain -uall` 的改动**全部**在白名单内；
   `git diff --stat -- core rules compiler assets runtime eval cli adapters packs docs` **为空**；
9. `python3 -m unittest discover -s adapters` → 仍 **241 条全绿**。

## 反空转条款

- 断言必须能被打破（见验收第 4 条，**必须在 README 里**制造一次，证明覆盖面真的扩了，不是只改了个变量名）；
- **不得把断言写成"扫了自己刚写的那句话"**——扫描目标必须是文件全文，且清单里要能看出扫了哪些文件；
- 正例与负例都要有；负例断言消息含**具体文件名与被禁短语**。

## 回滚方式

`git checkout -- labs/ticket-source/`（T21c 的产物已入库，可精确回滚）。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T21d-labs-文字陈述自校验扩面.md)" --dir （仓库根）
```

**数据分级：公开级**。可派发。

## 卡状态

- [ ] 已派发 → [ ] 已回收 → [ ] 验收通过（附证据）/ 退回（附原因）

> **本卡是本线的收尾卡。** 做完之后：`labs/ticket-source/` 的文字陈述由自校验托管，
> **不再为"某句话与事实不符"单开卡片**——那属于机制没覆盖到位，而不是新问题。
