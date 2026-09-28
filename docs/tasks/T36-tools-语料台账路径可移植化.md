# T36 · tools —— 语料台账路径可移植化（去本机绝对路径）

> 层：`tools/`（扩展区）+ `labs/corpus-harvest/`（实验区）+ `docs/`（口径）
> 发现于：2026-09-23 收尾盘点（发布树六类敏感关键词复扫）
> 性质：**发布前阻塞级**（不是功能缺陷，是红线泄漏）

## 一、问题（实测，非推测）

`labs/corpus-harvest/corpus.lock.json` 随仓进公开分发，里面 **25 处路径值**是**本机绝对路径**
（**本文档不复制那些字面值**——复制等于把同一处泄漏搬进卡面；形态如下）：

```
"local_path": "<本机盘位前缀>/corpus/<来源目录>"
"local_file": "<本机盘位前缀>/corpus/<来源目录>/<文件相对路径>"
```

**两条独立的问题**：

1. **泄漏**：路径值暴露本机盘位与用户名布局。发布树的既有红线是「机器盘位路径、家目录路径零命中」
   （`docs/18 §一` 条件 3、T33b 卡面同口径）。当前 HEAD 已非零——**下次推送会直接带出去**。
   `docs/20-合规自审记录.md` 的 §二 / §三 两处正文同样写了该路径（该文件也在发布树里）。
2. **不可用**：绝对路径对**任何别的机器**都是错的。台账的用途是白盒溯源（`docs/11 §11.3`），
   写死机器路径等于「换个机器就没法 `--recheck`」。

**成因**：收口批（`aae4507`）为满足 `docs/11 §11.3` 的「绝对路径」字面要求，把台账从
`~/corpus/…` 改成了实测绝对路径——**当时 6 条路径逐条核对确实存在，事实没错，但引入了泄漏**。
生成器 `tools/corpus_fetch/fetch.py` / `labs/corpus-harvest/fetch.py` 写的都是 `str(dest_dir)`，
**不改生成器，下次重跑还会长回来**。

## 二、目标

台账与生成器改为 **`${CORPUS_ROOT}` 锚定**：

- 写：`local_path` / `local_file` 一律写成 `${CORPUS_ROOT}/<相对路径>`；
- 读：`--recheck` 按 `--out-root` → 环境变量 `CORPUS_ROOT` → 缺省 `~/corpus` 的顺序解析回本机路径；
- 落盘路径**不在 CORPUS_ROOT 之下 → 抛 `FetchError`（fail-closed）**，不退化成绝对路径。

这样台账对任何机器都成立，且本机行为不变（本机把 `CORPUS_ROOT` 指向语料实际所在位置即可）。

## 三、产物路径

| 文件 | 改什么 |
|---|---|
| `tools/corpus_fetch/fetch.py` | 加 `ROOT_PLACEHOLDER` + `portable_path()` / `resolve_portable()`；4 处写入点改用前者；`recheck()` 改用后者；`_schema` → `vox-corpus-lock/2` 并加 `_note` |
| `labs/corpus-harvest/fetch.py` | 同上（该副本已被 `tools/` 取代，但 golden README 的复现步骤仍指向它——**不修就还能重新生成泄漏**）；**另修一处同源缺陷**：`REPO_ROOT` 原是被脱敏成占位符 `<仓库根>` 的，占位符让「拒绝写仓库内」两道守卫**静默失效**（任何路径都不被视为仓内）→ 改自推 `parents[2]` |
| `labs/corpus-harvest/corpus.lock.json` | 25 处路径值就地迁移为 `${CORPUS_ROOT}/…`；`_schema` 同步 |
| `docs/11-语料自给与语义提案-口径.md` | §11.3 的 `local_path` 行：口径由「绝对路径」改为「`${CORPUS_ROOT}` 锚定，**机器绝对路径不入库**」 |
| `docs/20-合规自审记录.md` | §二/§三 两处本机路径 → 改为「`CORPUS_ROOT` 指向的仓外位置」，**不写具体机器路径**；§三 的存疑项 ③ 随之关闭 |
| `labs/multi-industry-corpus/corpus_ledger.json` | `out_of_repo_note` 里「管道台账带绝对路径」的陈述随本卡失效 → 更正为锚定口径（**审查发现卡面漏列，已补入白名单**） |
| `bin/vox` | 第 6 行注释里的本机盘位路径示例改为中性表述 |
| `tools/tests/test_corpus_fetch.py` | `github-archive` 那条「`local_file` 逐字等于落盘位置」的断言改为「解析后逐字等于」（保留原意图：不许多拼一层 `.zip`）；另补两条：`portable_path` 越界必须抛错的守卫测试、`recheck` 对未锚定路径必须告警的留痕测试 |

## 四、文件白名单（越界即退）

```
tools/corpus_fetch/fetch.py
tools/tests/test_corpus_fetch.py
labs/corpus-harvest/fetch.py
labs/corpus-harvest/corpus.lock.json
labs/multi-industry-corpus/corpus_ledger.json
docs/11-语料自给与语义提案-口径.md
docs/20-合规自审记录.md
bin/vox
```

## 五、禁止事项

1. **不动冻结区**（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）——`cli/` 也不许碰；
2. **不改 `--recheck` 的退出码语义**（0 正常 / 1 有异常 / 2 台账缺失 / 3 台账坏 JSON）；
3. **不删除台账里任何条目**（旧条目指向的语料被删时必须留着，让 recheck 报「缺失」——既有纪律）；
4. **不把本机路径写进任何文档或注释**（含「举例」形式）；
5. 不 `git add` / 不提交（提交归验收方）。

## 六、验收标准

1. **迁移后 `--recheck` 在真机上仍能跑通**（**卡面更正 2026-09-23**：原写「`--lock` 直接指仓内台账」，
   实测该路径**必然 exit 3**——`--lock` 拒仓内是 T12 既有的既定裁定，本条命令原样跑不通，是**卡错不是实现错**。
   已实测可执行的形式 = **把台账复制到仓外再巡检**）：
   ```sh
   cp labs/corpus-harvest/corpus.lock.json /tmp/t36-lock.json
   CORPUS_ROOT=<语料实际根> python3 -m tools.corpus_fetch.fetch --lock /tmp/t36-lock.json --recheck
   ```
   → 巡检 **19** 个文件 / 异常 **0** / 退出码 **0**；**负例**：`CORPUS_ROOT` 指到空目录
   → 19 条「缺失」/ 退出码 **1**（不得静默通过）。
   （替代形式：`cd labs/corpus-harvest && CORPUS_ROOT=<根> python3 fetch.py --recheck`，同结果。）
2. **红线复扫归零**：白名单文件里**机器盘位路径与家目录路径 0 命中**（扫描类别清单见 `docs/18 §一`）。
   （`docs/tasks/**` 历史验收记录里作为**扫描项名称**出现的同类字面已一并改为类别描述，见 §九。）
3. **`tools` 测试根全绿**。**卡面更正（2026-09-23）**：原写「改动只应影响其中 1 条断言」，
   实测影响 **3 处**（`test_corpus_fetch.py:727, 1125` 两条 schema 断言 + `:1393` 的 github-archive 断言）
   ——前两条是 `_schema` 升 v2 的**必然伴随**，不是额外改动。基线 157 → 改动后 **159**（新增 2 条守卫测试）。
4. **注入验证（反空转）**：
   - 把 `resolve_portable` 改成忽略传入的 root → **负例必须转绿**（证明负例由 root 处理决定，不是空转）；
   - 把 `portable_path` 的越界分支改成「静默写绝对路径」（本卡明令禁止的形态）→
     **必须有测试判红**（`test_portable_path_anchors_and_refuses_to_escape_root`）。
5. **fail-closed 实测**：造一个落在 `CORPUS_ROOT` 之外的路径 → 必须抛 `FetchError`，
   **不得**降级写绝对路径。
6. **兼容分支必须可观测（审查补入）**：`recheck` 遇到非 `${CORPUS_ROOT}` 前缀的路径值时必须
   在 stderr 留痕告警，**但不得改退出码语义**（仓外 v1 旧台账是合法可读的）。
   ——理由：没有这条留痕，「写入侧退回绝对路径」在巡检侧完全不可见（照样打「异常 0 个」）。

## 七、回滚方式

改动集中在 7 个文件，且台账是**纯数据迁移**（可逐行还原）。回滚 = `git revert <提交>`；
若只回滚台账，`git checkout <提交>^ -- labs/corpus-harvest/corpus.lock.json` 即可。

## 八、依赖

- 无前置卡。`docs/11 §11.3` 的口径改动是本卡的**一部分**（不是前置条件）——原口径的
  「绝对路径」字面要求正是本次泄漏的成因，必须同步改，否则下一个人会照旧口径改回去。

## 九、已知问题登记（不在本卡范围）

- `labs/corpus-harvest/fetch.py` 与 `tools/corpus_fetch/fetch.py` **两份并存**（后者是 T12 的提升版，
  1339 行 vs 663 行）——本卡只保证两份在路径写法上一致，**不合并**（合并是另一件事，且 labs 那份属历史证据链）。
- **扫描项名称的字面写法**：`docs/tasks/**` 历史验收记录里作为「检查项名字」出现的盘位串/家目录串，
  本次**一并改为类别描述**（指向 `docs/18 §一`），不再保留字面。
  **裁定（2026-09-23）**：把字面写在仓内等于让扫描永远非零，且每次都要重新裁定「这是路径还是检查项的名字」——
  盘位/家目录前缀的字面**一律不出现在发布树**，类别口径以 `docs/18 §一` 为唯一定义处。
  代码注释里同类字面同样改为类别描述（审查补入）。
- `tools/` 与 `labs/` 均无本层 `AGENTS.md`（项目级规则说「每层另有自己的 AGENTS.md（固定七项）」）
  ——**既有缺口**，本卡不动，登记待办。
- `recheck` 对**仓外**旧台账（v1、裸绝对路径）仍照常读通——这是设计内的向后兼容（见验收 6 的留痕要求），
  不是「本卡没清干净」：红线只针对**进公开分发**的台账。

## 十、验收记录

**验收方：AI 会话（策划兼验收，2026-09-23）**——**披露**：本卡由我方撰写、实现、自验，
已另派两个**只读独立审查员**（`blackiron-silent-failure-hunter` / `blackiron-code-reviewer`）复核，
其发现已逐条处置（见下「审查处置」）。**未走执行器派发路径**。

| 判据 | 结果 | 证据 |
|---|---|---|
| 1 真机正例（仓外副本） | ✅ | `巡检 19 个文件，异常 0 个`，rc=0 |
| 1 负例（空根） | ✅ | `异常 19 个`，rc=1（非静默） |
| 2 红线复扫 | ✅ | 白名单 7 文件 + 卡面自身：机器盘位路径 / 家目录路径 **0** 命中；台账本体 25 处路径值 100% 锚定 |
| 3 测试根 | ✅ | `tools` **159** 条 OK（基线 157 +2）；全仓十一根 **1,545 → 1,547** |
| 4 注入 A（忽略 root） | ✅ | 负例转绿 rc=0；恢复后 rc=1 |
| 4 注入 B（越界改静默） | ✅ | 仅 `test_portable_path_anchors_and_refuses_to_escape_root` 判红；恢复后全绿 |
| 5 fail-closed | ✅ | `portable_path(根外路径, 根)` → `FetchError`（含路径与根，可定位） |
| 6 兼容分支留痕 | ✅ | `test_recheck_warns_on_unanchored_ledger_paths`：rc 仍 0、stderr 含告警 |
| 台账数据迁移纯净性 | ✅ | 逐条比对：除 `local_path`/`local_file`/`_schema`/`_note` 外**逐字相同**；6 条 id 与顺序不变 |

**审查处置（两个只读审查员的发现，逐条）**：

| 发现 | 裁定 | 处置 |
|---|---|---|
| 卡面验收 1 的字面命令必 exit 3 | **卡错**（实现对） | 已改卡面为「仓外副本」形式并注明原因 |
| 卡面验收 3 写「只影响 1 条断言」实为 3 处 | **卡错** | 已据实更正并注明两条 schema 断言是必然伴随 |
| `portable_path` 越界分支零测试覆盖（注入 B 全绿） | **真缺口** | 已补守卫测试；注入 B 复验判红 |
| `recheck` 兼容分支无任何提示 | **真缺口（可观测性）** | 已加 stderr 告警（不改退出码） |
| `labs/multi-industry-corpus/corpus_ledger.json` 陈述失真且不在白名单 | **卡面漏列** | 已更正该文件并补入白名单 |
| 代码注释含盘位/家目录字面（朴素扫描会命中） | **接受（改为更稳的写法）** | 注释改为指向 `docs/18 §一` 的类别描述 |
| `labs/.../fetch.py` 的 `REPO_ROOT` 未列入卡面 §三 | **卡面漏列** | 已补入 §三 |
| `labs/` 版 `resolve_portable` docstring 偏薄 | **接受** | 已补齐边界与调用方说明 |
| `recheck` 不校验台账是否在 `--out-root` 之下（既有，非本卡引入） | **登记为下一批** | 指错根时输出已可定位（逐条「缺失」+ rc 1），不是静默；改进留待后续卡 |
| `tools/` `labs/` 无本层 AGENTS.md（既有） | **登记** | 见 §九 |

**返工记录：无**（首轮即通过；审查发现的 4 条真缺口/卡错在验收前已处置完毕）。
