# T12b · tools：T12 审计修复（2 阻塞 + 3 P1——静默失败猎手查出，验收方已逐条核实）

## 背景（只写必需）

T12 验收通过后按固定动作调 `blackiron-silent-failure-hunter` 扫 `tools/corpus_fetch/`，
报 **2 阻塞 + 3 P1 + 6 P2**。验收方已逐条读码核实本卡 5 条（P2 另立 T12c）。逐条事实：

1. **〔阻塞〕声明文件缺失被静默吞**（`fetch.py::select_files`）：`files` 声明了具体路径时，
   某个文件在平台树里缺失/被改名，只要还命中 ≥1 个就不报错——缺失文件被静默丢弃，
   不完整语料当完整入库，`manifest_digest` 只覆盖现存文件。`if not picked` 只拦全空。
2. **〔阻塞〕台账读在锁外（丢失更新）**（`fetch.py` main 写台账段）：`old = json.loads(read_text)`
   在锁外执行，`write_lock` 只盖写。B 实例在 A 提交前读 `old`、A 写完释放锁后 B 才写入
   → B 用陈旧 `old` 做 merge，**A 的条目被静默覆盖**，B 照常打「台账已写」exit 0。
   这正是 2026-09-18 那次并发事故的形态——锁没堵住它，只是把窗口从「整段」缩到「读→写间隙」。
3. **〔P1〕旧台账损坏 → 警告一行就整体覆盖**（同段 `except json.JSONDecodeError`）：
   旧条目全部丢失、对应语料脱管（recheck 不再巡检），exit 0。与本文件自身纪律
   「宁可硬失败也不静默覆盖」相抵触。
4. **〔P1〕未知策略静默跳过且注释撒谎**（`derive_golden.py`）：`if strategy not in mapping: continue`
   上一行注释写「一律跳过并留痕」，实际**没有任何计数或日志**——轮次静默消失，
   golden set 静默残缺、命中率口径失真。（「注释说做了实际没做」正是本仓 hunted 的假陈述。）
5. **〔P1〕revision 双缺省（master vs main）**：许可审计读 `src.get("revision", "master")`（:389），
   tree/下载用 `src.get("revision", "main")`（:482、:625）。仓库同时有 master/main 两分支时，
   **审计证据与实际入库内容绑在不同版本**，静默绕过「以 README 为准」。

## 目标（可验收的产物）

- 修 1：`select_files`——当 `pats` 不是 `["*"]` 时，**逐项核对**每个声明路径（非 `.` 开头的）
  都在 tree 里；有缺失 → 抛 `FetchError`，消息**列出缺失路径全名**与来源 id，一个都不下。
- 修 2：把「读旧台账 → merge → 写」整段挪进锁临界区（如给 `write_lock` 传 merge 回调，
  或新增 `_update_lock(lock_path, merge_fn)` 在持有 lease 期间完成读改写）。
  单实例行为与现在完全一致（回归：真实台账 recheck 数字不变）。
  顺带：main 里 `merge_lock(old, entries)` 被算两遍（write 参数与 print 各一次）——合并为算一次。
- 修 3：旧台账 JSON 解析失败 → **抛 `FetchError` 中止**（消息含路径与解析错误摘要），
  删掉「警告后覆盖」。要继续就让人手工裁决后重跑。
- 修 4：`derive_golden` 收集被跳过的未知策略 `Counter`，结尾**打印**（含标签与条数）；
  且未知策略总数 > 0 → **非零退出**（跑批铁律：断言成功数 + 非零退出码）。
  注释同步改成与行为一致。
- 修 5：新增模块级常量 `DEFAULT_REVISION = "main"`，三处（:389/:482/:625）全引它，
  不得再出现两份字面量缺省。

## 允许修改的文件（白名单）

```
允许修改：tools/corpus_fetch/fetch.py
允许修改：tools/corpus_fetch/derive_golden.py
允许修改：tools/tests/test_corpus_fetch.py（只允许新增用例；既有用例不得删改）
允许修改：tools/tests/test_derive_golden.py（同上）
允许修改：tools/README.md（只允许追加一段「revision 缺省与并发口径」说明）
禁止触碰：其他一切文件——尤其 baseline_literal.py（T12c 才动）、labs/、packs/、docs/
```

## 禁止事项

- 不得放宽任何既有校验（审计判据、sha256、路径闸门强度只增不减）
- 不得改台账 JSON 结构（entries 形状不变，recheck 兼容旧台账）
- 不得新增第三方依赖；不得"顺手优化"本卡 5 条之外的代码
- 修 2 不得用「先写临时文件再全量替换」替代真正的临界区（那是换个地方丢更新）

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s tools/tests` 全绿，测试数**只增不减**（基线 52）；
2. **修 1 负例**：声明 files=[a.txt, 缺失.txt] 而树里只有 a.txt → `FetchError` 且消息含 `缺失.txt`；
   声明全部命中 → 正常（正例）；`["*"]` 行为不变；
3. **修 2 注入实测**（验收方会做）：在「读旧台账后、写前」注入第二实例写入 → 本次运行必须
   **检测到冲突并失败**（或在临界区内完成读改写使丢失更新不可发生）——执行方自选机制，
   但必须给一条测试证明读在锁内（如：锁被持有时调更新入口 → 阻塞或报错，绝不静默用旧值）；
4. **修 3 负例**：旧台账内容为坏 JSON → 抛 `FetchError`，且**原文件字节未被改动**；
5. **修 4 实测**：语料含映射表外的策略标签 → 结尾打印跳过清单且进程退出码非 0；
   全部已知 → 打印不含跳过项、退出码 0；
6. **修 5**：`grep -n '"master"\|"main"' tools/corpus_fetch/fetch.py` → 除 `DEFAULT_REVISION`
   定义行外 0 命中；
7. **回归（三个数字逐位一致）**：`recheck(真实 labs 台账)` → 0 异常；
   `baseline_literal.py --pack packs/fin-cs` → `0/2433 = 0.00%`；
   labs 的 `cross_check_template_rate.py` → `1.8% / 0.2%`；
8. `git status --porcelain -uall` 恰为白名单文件，无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 新增测试必须调用产品 API（`select_files` / 台账更新入口 / `derive_golden` 主入口）；
- 负例断言错误消息包含具体非法值（缺失文件名 / 坏 JSON 路径 / 未知策略标签）；
- 不得为通过测试而放宽校验。

## 回滚方式

`git checkout -- tools/corpus_fetch/fetch.py tools/corpus_fetch/derive_golden.py tools/tests/ tools/README.md`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T12b-tools-审计修复.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T12b 节）
  - 验收方独立复跑：tools 62 全绿（52→+10）；自写探针——select_files 缺失声明拦截（消息含缺失名）、坏台账 FetchError 且文件字节未动、`"master"/"main"` 字面量仅剩 DEFAULT_REVISION 定义行；回归三数字逐位一致（recheck 0 异常 / 0/2433 / 1.8%·0.2%）。
  - **裁定 1（接受）**：`derive()` 返回值改为 `(List, Counter)` 二元组，既有测试 8 处调用点机械适配（`cases, _unk = ...`）——卡文「既有用例不得删改」与修 4「必须暴露计数」冲突；断言语义与用例数未变，返回二元组优于出参，按修法需求收下，卡内注明。
  - **裁定 2（接受）**：验收 7a 的 recheck 在仓外 `/tmp` 副本上跑（`cmp` 逐字节一致）——与 T12 的「tools 只写仓外」裁定同口径。
  - **裁定 3（接受 + 转卡）**：DEFAULT_REVISION 取 "main"；「无 revision 声明 + master/main README 不同」缺 fixture 钉住 → 已补进 T12c 验收 7b。
  - **边界留档**：点文件声明（如 `.gitattributes`）走「筛选结果为空」报错分支，不算「声明文件不存在」——语义成立，不改。
