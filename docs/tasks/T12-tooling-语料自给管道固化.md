# T12 · tooling：语料自给管道固化（fetch / derive / baseline 三件套 + 测试）

## 背景（只写必需）

`labs/corpus-harvest/` 已用一次性脚本跑通首轮：5 份公开语料落盘（`corpus.lock.json` 台账）、
`packs/fin-cs/` 派生完成（17 key / 102 资产 / `vox pack check|build|bench` 三关全过）、
逐字档与语义档实测出数（见 `labs/corpus-harvest/README.md`）。

**问题是这三件套现在还是一次的**：脚本在 `labs/`（实验区，不进任何层契约），没有测试、没有回归保护。
下一批（换业务语料、方言链路）还要用它，**必须先固化，否则每次都是重写一遍**。

规格依据：
- `docs/11-语料自给与语义提案-口径.md`（§11.3 许可台账与 fail-closed 规则、§11.5 golden 格式、§11.6 格式硬约束、§11.9 实测记录）
- 根 `AGENTS.md`（`labs/` = 实验区；`[扩]` 扩展区随便加）

## 目标（可验收的产物）

把三件套从 `labs/` 提升为**有测试、可复用**的工具。**留口子优先于大重构**——不重写逻辑，
只做「搬位置 + 加测试 + 补缺口」。

- 产物 1：`tools/corpus_fetch/{__init__.py,fetch.py,derive_golden.py,baseline_literal.py}`
  （由 `labs/corpus-harvest/` 的对应文件搬入并去硬编码；`sources.json` / `corpus.lock.json` **不搬**，
  它们是本批的数据，留在 `labs/`）
- 产物 2：`tools/tests/test_corpus_fetch.py` + `tools/tests/test_derive_golden.py`（对应测试）
- 产物 3：根 `AGENTS.md` 的目录清单**追加一行** `tools/ [扩] 仓外数据获取与派生（只写仓外，不进任何层契约）`
- 产物 4：`tools/README.md`（口径、复现命令、与本批 `labs/` 的关系）

**搬的时候要去掉的硬编码**（现有脚本里都有）：
- `CORPUS_ROOT = Path("~/corpus")` → 提为参数，缺省值可保留但必须可覆盖
- `derive_golden.py` 的 `STRATEGY_TO_KEY` → 提为**外部映射文件**（`--strategy-map <json>`），
  因为换业务时这张表一定不同；缺省值可内置，但必须能换
- `baseline_literal.py` 的 `REPO` 路径 → 用 `importlib` 从 `--pack` 推仓库根，或提为参数

## 允许修改的文件（白名单）

```
允许新增：tools/**（含 tools/tests/**）
允许修改：AGENTS.md（只允许在「三、目录与冻结状态」的代码块里追加 tools/ 那一行）
禁止触碰：其他一切文件——尤其不得改 core/ rules/ compiler/ assets/ runtime/ eval/ cli/ 下任何文件、
          不得改 docs/、不得改 labs/corpus-harvest/ 下任何文件（它要作为本批的历史证据原样留着）
```

## 禁止事项

- **不得新增第三方依赖**（`tools/` 下只允许标准库 + 仓库自身模块）
- 不得改动本卡白名单以外的任何文件
- 不得"顺手优化"、"顺手重构"、不得改动与本卡无关的格式
- **不得为了让测试好写而放宽任何校验**（许可判据、sha256 交叉核对、拒写仓库内路径，强度只增不减）
- **不得在测试里联网**——测试必须离线可跑（网络走不通用例用「已下载的假响应」或跳过并显式标注）

## 验收标准（逐条可判定，我会逐条核对）

1. `python3 -m unittest discover -s tools/tests -v` 全绿；
2. **负例（许可）**：构造一份 README front-matter 写 `apache-2.0` 而正文含 `NonCommercial` 的假来源
   → 必须抛 `LicenseRejected`，且**错误消息包含命中的具体词**（照 `docs/11 §11.9.2`）；
3. **负例（哈希）**：构造一份「平台声明 sha256 ≠ 实测」的假响应 → 必须抛错且消息含两个哈希值的前缀；
4. **负例（路径）**：`--out-root` 指向仓库内 → 必须抛错（`docs/11` 裁定 2），
   且**不得先建目录再报错**（断言目标目录仍不存在）；
5. **正例（端到端离线）**：用 fixture 假响应跑完 `fetch` 的「审计 → 下载 → 校验 → 写台账」全链，
   断言台账里每条的 `size_bytes` / `sha256` 与 fixture 一致、`verified_against_platform` 取值正确
   （平台未给哈希时必须是 `false`，不得填 `true`）；
6. **正例（派生）**：用一份 3~5 条的小 fixture 语料跑 `derive_golden`，断言
   ① 每条都带 `user_utterance` 与 `assistant_reply_free` **两个字段**（`docs/11 §11.9.3`）；
   ② 含占位符/超长/超短的用户话术被过滤；③ 策略 12「其它」产出 `expect_kind="unspecified"`；
7. **两条已在 `labs/` 版本里修掉的坑，搬过去不许退化**（见 `labs/corpus-harvest/README.md §8.1`）：
   - **`optional` 闸门**：缺省必须跳过 optional 来源，且**响亮打印**跳过声明（静默跳过 = 让人以为已经下了）；
     `--only <id>` 必须能显式拉单个 optional 项；
   - **大文件流式下载**：不得用「一次性读进内存」的写法；必须分块写盘 + 边下边算 sha256；
     断言：失败时**不留下**可被误认为完整语料的 `.part` 残留；
   - **台账原子写**：`corpus.lock.json` 的 read-modify-write 必须原子（临时文件 + rename），
     且检测到并发实例时必须拒绝启动——实测过一次并发写导致台账被陈旧任务覆盖（见 labs README §8.1 坑 3）；
8. **回归**：搬完后用真实语料重跑一次，数字必须与本批 `labs/` 记录**逐位一致**——
   - `fetch.py --recheck` → 异常 0 个
   - `baseline_literal.py` → `0/2433 = 0.00%`
   - `cross_check_template_rate.py` → 两份语料 1.8% / 0.2%
   （不一致 = 搬错了，整卡退回。这三个数是本批的回归基线）
9. 根 `AGENTS.md` 只多了一行 `tools/`，`git diff --stat AGENTS.md` 必须显示 **+1 行**。

## 反空转条款（每张卡必带，T01 教训）

- **测试必须调用产品 API**：不得在测试文件内定义/复制被验逻辑（违者整卡退回）。
  本卡的「产品 API」= `tools/corpus_fetch/` 下的公开函数；
- 测试名必须与实际调用路径一致（禁止名为 `*_in_unit` 却只测独立辅助函数）；
- **正例与负例都要有**，且负例断言**错误消息包含具体非法值**；
- 不得为通过测试而放宽校验；既有校验强度只增不减（验收方会比对异常抛出点数量）。

## 回滚方式

新增目录，回滚 = `git clean -fd tools/`；`AGENTS.md` 那一行用 `git checkout -- AGENTS.md` 单独还原。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（`~/.zcode/agents/vox-card-executor.md`）。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T12-tooling-语料自给管道固化.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**——语料是公开渠道的（MIT / Apache-2.0），不含用户录音、真实会话、内网地址、token。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T12 节）
  - 验收方独立复跑：tools 52 测试全绿、baseline 0/2433=0.00%、recheck 0 异常、cross_check 1.8%/0.2% 逐位一致；许可负例用自写探针复现（消息含命中词）；异常抛出点 24→31 只增不减。
  - **口径裁定（验收 8a 的形式冲突）**：卡面写的字面命令 `fetch.py --recheck` 无法对 labs 仓内台账执行——实现把「只写仓外」贯彻到了 `--lock`（仓内一律拒）。裁定**按实现的严格端收下**：tools/ 管道面向未来批次（台账与语料同址落仓外）；labs 历史台账的巡检走 public API `recheck()`（本次即此路径，数字达标）或 labs 原脚本。
