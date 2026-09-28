# tools/ · 仓外数据获取与派生

> **定位**：`[扩]` 扩展区。仓外语料的获取与派生工具，**只写仓外，不进任何层契约**。
> 规格依据：`docs/11-语料自给与语义提案-口径.md`（§11.3 许可台账、§11.5 golden 格式、§11.9 实测记录）。
> 出身：由 `labs/corpus-harvest/` 的三件套搬入并去硬编码（见 T12）。

---

## 一、为什么在 tools/ 而不是 labs/

`labs/corpus-harvest/` 里那三件套**跑通了但是一次性的**：脚本没有测试、
没有回归保护，下一批（换业务语料、方言链路）还得重写。T12 把它们固化为可复用的工具，
**留口子优先于大重构**——不重写逻辑，只做「搬位置 + 去硬编码 + 补测试」。

两条边界必须记住：

- **本目录的脚本不进任何层契约**（同 `labs/`），也不被 `core/ rules/ compiler/ assets/ runtime/ eval/ cli/` 依赖；
- **语料本身永不入仓**（`docs/11` 裁定 2）——脚本只写 `~/corpus/`，
  这个约束在代码里是**强制**的（`--out-root` 指向仓库内会直接抛错），不是靠自觉。

## 二、文件与职责

| 文件 | 职责 | 不改的东西 |
|---|---|---|
| `corpus_fetch/fetch.py` | 获取器：先验许可后下载、逐文件 sha256 交叉核对、拒写仓库内路径、`--recheck` 巡检、台账原子写 | 许可判据、`NONCOMMERCIAL_PATTERNS` 硬拒绝 |
| `corpus_fetch/derive_golden.py` | 从对话语料派生 golden set 对照集（`docs/11 §11.5` 的集合 A） | 两字段口径、`MIN/MAX_UTTER_LEN` |
| `corpus_fetch/baseline_literal.py` | 逐字档基线（`docs/11 §11.5` 的实验①） | 复用产品归一化，不另写一份 |
| `feedback_mining/miner.py` | 回流闭环挖掘（T23 / 立项 M3）：轮级留痕 + 事件流 → `suggestions.json` + `rejected.json` + `report.json` | 归一化与单句判据一律 **import** 产品代码，不复制；A1/A2 恒写 `pending_human` |
| `feedback_mining/make_demo_inputs.py` | 造挖掘器的演示输入（T17 留痕 + T07 事件流，全部自造话术） | 不读时钟、不随机 |
| `run_all_tests.py` | **「跑全量测试」的单一真源**（T48）：逐根跑十一个测试根、累计 ran/skipped/failures/errors、打印**分项**汇总行、任一根红即非零退出并点名失败根 | 零第三方依赖（纯标准库）；每根一个独立子进程（十一个 `tests` 包名全撞，同进程必串味） |
| `check_no_machine_paths.py` | **发布树绝对路径门禁**（T58）：扫「入库 + 未跟踪」全量文件，按本机家目录/外接卷挂载点/启动卷临时目录三类**推导出的**前缀判红，输出机器可读 JSON | 只读（不写任何文件）；判据自带豁免机制（`# scan-exempt: <理由>`）而不靠「悄悄不查」；前缀不写死 |
| `check_import_direction.py` | **import 方向闸门**（T63）：用 `ast` 解析真实语法树（不用正则——正则分不清 `import` 与字符串/注释里的 `import`）判两条：`frozen-import`（冻结区 `core/ rules/ compiler/ assets/ runtime/ eval/ cli/` 反向 import 扩展区 `adapters/ packs/ trigger/`）与 `private-name`（跨包 `from x import _y`）；例外走 `import_direction_baseline.txt` | 只读（不写任何文件）；判据与前缀只住在脚本里（写在文档里的判据会扫到写判据的那一行）；基线条目**必须带非空理由**且必须与真实违规配对，对不上（陈旧或凭空）直接判红，不靠「往基线里随便加一条」放水；`tests/` 目录内**不判**（判的是产品的依赖方向，而测试断言内部实现需要跨包引用私有名）；零第三方依赖 |
| `pack_phrase_audit.py` | **发布树话术比对门禁**（T58）：`admission.md` × 仓外 yaml 逐 key 机械比对，**分级**四类命中（凭据/内网地址 = 硬失败；逐字重合/结构化标签 = 只报告） | 只输出 key 名与类别，**绝不输出原文**；逐字类退出 0 并带 `policy="pending-business-decision"`（业务判断未裁定，见 `docs/20 §四`）；零第三方依赖（不 import PyYAML） |

测试：`tools/tests/test_corpus_fetch.py`、`tools/tests/test_derive_golden.py`、
`tools/tests/test_feedback_mining.py`（38 条，覆盖 T23 的验收 1–7 与负例实测）、
`tools/tests/test_run_all_tests.py`（T48：汇总口径分项、失败根点名、仓根入 `sys.path`、
`discover` 静默 0 条的堵漏、端到端判红/判绿）。

### run_all_tests（T48 · 「跑全量」的单一真源）

```bash
cd （仓库根）
python3 tools/run_all_tests.py            # 全量（十一个根），任一根红 → exit 1
python3 tools/run_all_tests.py --root core --root cli   # 只跑指定根
python3 tools/run_all_tests.py --json     # 额外打逐根明细 + 汇总（机器出处用）
```

末行汇总**必须分项**，不要只给一个总数：

```
ran=1649 skipped=0 executed=1649 failed=1 failures=1 errors=0 roots=11
FAILED roots: packs
```

（上面这行是 2026-09-28 在「yaml 文件可达 **且** 已装 PyYAML」的本机上的实跑值，
**样例而非契约**：条数随各层增删变化，真值一律由本命令当场产出——这正是「数字必须有出处」的意思。
那 1 条 `failed` 是 `packs` 根的同源指纹断言在按设计报警（仓外可选的 kefu yaml 变了），
不是测试坏了；yaml 不可达的环境（公开 CI）连跑都不跑它，`FAILED roots` 那行为 `(none)`。）
口径（写死在这一节，改这里等于改对外口径）：

- `ran` = 所有根 `TestResult.testsRun` 之和，**含** skip、**含**失败用例；
- `skipped` = `@unittest.skip` / `@unittest.skipUnless` / `skipTest` 的条数。
  **`skipped` 是环境相关量，不是契约**——同一个仓在不同的机器/环境下必然不同，
  任何对外数字都不许拿它当定值引用。真值只有一条出处：**本命令当场产出**。

  `packs` 根的同源断言要真跑，**两个**前置条件必须**同时**成立
  （T70 把判据从单条件扩成合取——单条件只覆盖了两个前置之一，
  于是「文件在、库不在」那一格掉进缝里炸成 error）：

  | ① yaml 文件 | ② PyYAML 可导入 | `skipped` | 说明 |
  |---|---|---|---|
  | 可达 | 已装 | **0** | 25 条同源断言全真跑（本机默认） |
  | 可达 | **未装** | **25** | 有仓无库：判据合取生效，诚实 skip 而非 error |
  | 不可达 | 已装 | **25** | 公开 CI 常态（无 kefu 仓） |
  | 不可达 | 未装 | **25** | 两者皆缺，skip 消息里报的是**先命中的那条** |

  四种组合的 **skip 数如上表**（2026-09-28，`ran=1649` 口径）。
  **`failed` 则随 yaml 可达性分岔**：不可达的两档是 `failed=0`（断言按设计 skip）；
  可达的两档当前是 `failed=1`——那是同源指纹断言发现**仓外** kefu yaml 变了，
  属门禁按设计报警，不是测试坏了。
  要复跑「文件不可达」那一档（可复现，不靠人记）：

  ```bash
  # 把 yaml 显式指到一个不存在的路径
  KEFU_HEAT_YAML=/nonexistent/供热预设.yaml python3 tools/run_all_tests.py
  ```

  **「未装 PyYAML」是测试期可选依赖，不是产品依赖**——产品仍是零第三方依赖
  （`README.md` 首屏口径）。想看这 25 条真跑，装上 PyYAML 即可（不装不影响 `failed=0`）。

  `packs` 之外各根也有自己的 `@unittest.skipUnless`，**与 yaml 无关**，按环境各自成立：

  | 条件 | 影响的根 |
  |---|---|
  | `shutil.which("say")` / macOS `say` 或可用音色不可用 | `adapters`（`test_conformance` / `tts_macsay`）、`runtime`、`compiler` |
  | `say` 可用**且** tmpdir 与仓库在不同设备 | `adapters`（`test_crossvolume`） |
  | `shutil.which("ffmpeg")` 不可用 | `adapters`（`tts_omlx` 真实合成冒烟） |
  | `packs/heat_kefu` 的构建产物不在仓内（公开 CI 常态） | `assets`、`adapters/framework_kefu` 的真包等价性断言 |

  即：`skipped` 的具体条数是**「哪几个环境前提在这台机器上不成立」的函数**。
  要引用就引用本命令的当场输出，并写清是在什么环境下跑的。
- `executed` = `ran - skipped`（**含**失败用例；失败数单列，不许从 executed 里抹掉）；
- `failed` = `failures + errors`，其中 `errors` 含**发现期异常**（根不存在、import 失败、包名冲突）。

**为什么分项**：`ran` 与 `executed` 的差恒等于 `skipped`，只报一个总数时无法自查，
仓内曾出现对外宣称 1,547、而任何实跑口径都给不出该数的悬案（T48 前）。

三条硬约束：

1. **每根一个独立子进程**：十一个根的 `tests/` **包名全叫 `tests`**，同一解释器内
   `sys.modules` 会串味（后一根静默拿到前一根的模块）。进程隔离是唯一不改动任何测试文件的隔离方式。
2. **失败必须显形**：任一根 `failed > 0` → 退出码非 0 + 逐个点名失败根。
   子进程退出码与回报计数**互相交叉校验**（任一为红都算红），哨兵行缺失记 error。
   依据 `AGENTS.md`「跑批铁律」：失败会被产物数量掩盖。
3. **零第三方依赖**：只用标准库。`python3 -m pytest` 装没装都不影响本脚本
   （仓根 `conftest.py` 只服务 pytest 的收集，全量口径走 unittest）。

### check_no_machine_paths · pack_phrase_audit（T58 · 两条发布树内容门禁）

这两个脚本是**成对的**，解决的是同一个毛病：**判据只以文字/正则的形式活在文档里，
于是它既不可复跑、也不可自动化**。T56 把 79 行绝对路径清到 2 行，但那 2 行删不得——
它们是判据自己的正则（**判据自指**：判据的正则会扫到写判据的那一行，
于是任何扫描恒非零，门禁事实上不存在）。T58 把判据收进代码，文档里只留调用。

```bash
cd （仓库根）
python3 tools/check_no_machine_paths.py ; echo "rc=$?"     # 0=零违规 1=有违规 2=用法/环境错
python3 tools/pack_phrase_audit.py      ; echo "rc=$?"     # 0=无硬失败 1=有硬失败 2=用法错
```

四条共同纪律（**违反任何一条都不叫门禁，叫装饰**）：

1. **只读**。两个脚本都不写任何文件；`--json` 只输出到 stdout。门禁改被查物，
   等于自证——本仓已经在 `structure_budget` 上踩过这个坑（故它必须带 `--no-write`）。
2. **前缀/正则只住在脚本里**。文档（尤其被扫描的文档）里只准出现调用。
   写在文档里的判据会扫到自己。
3. **豁免必须显式**。`check_no_machine_paths.py` 只认两种豁免：脚本自己，
   以及行内标了 `# scan-exempt: <理由>` 的行。没有「悄悄不查」的后门。
4. **假绿比红更坏**。取不到文件清单 → 退出 2 而不是 0；yaml 取不到 →
   `degraded=true` + stderr 响亮提示（要「取不到即失败」加 `--require-yaml`）。

`pack_phrase_audit.py` 的**分级**是刻意设计，不是妥协：凭据与内网地址进公开仓
没有「可公开」一说（硬失败，退出 1）；「某段话术能否对外发布」是**商业判断**，
处置路径截至 2026-09-28 全部未决（`docs/20 §四`），把它焊成 CI 硬门禁等于
用机器固化一个还没人做的决定。这两类只出报告、退出 0，并带
`policy="pending-business-decision"`——业务方拍板后**只改这一个字段**，
判据与 CI 都不必动。CI 关卡 4 因此只接前两类。

`pack_phrase_audit.py` 的两条阈值判据（写在代码里，可复跑，不靠人眼）：

| 类别 | 判据 | 阈值 |
|---|---|---|
| `verbatim-overlap` | §2/§3 表**逐行**取「yaml 文本（节选）」列，在**该行自己的 key** 的 yaml 值里找最长逐字连续重合 | ≥ 8 字 |
| `structured-label` | yaml 里**值类型非 `str`** 的 key（dict/list，键名表与覆盖表）及其下标量出现在文档里 | ≥ 4 字 |

**为什么逐字那一类必须「逐行 + 同 key」约束**：拿某 key 的话术去和整份
`admission.md` 求最长公共子串，会把两句话共有的客套话（8 个汉字的重合在中文里
是常事）算成逐字泄漏。本机实测：不加同 key 约束时命中 34 个 key，其中 12 个的
重合占比 ≤ 0.33，明显是碰巧；加上约束后命中数与 `docs/13 §五#29` 登记的
**24 条**逐条对上（其中 22 条整段逐字、2 条节选前缀），与 T57 在公开树上的
22 条也可对齐。**同一判据从两边跑出同一个数**——这正是 #29 验收标准第 ① 条要的。

### feedback_mining（T23 · 立项 M3「回流闭环」）

口径与边界全在 `tools/feedback_mining/README.md`。三条硬边界是仓库红线的落点，记在这里：

- **归一化与单句判据同源**：`adapters.framework_kefu.normalize_text` 与
  `compiler.source._SENTENCE_TERMINATORS` 一律 import，测试用 `assertIs` 钉住是同一对象。
- **只形式条款机器化**：`docs/14` 的 A1/A2 与禁入项 ⅰ–ⅳ 是语义判断，
  `a1a2_class` 字段恒为 `"pending_human"`——脚本不猜语义。
- **拒收不静默**：形式条款未过的候选进 `rejected.json` 并写明规则编号与原因
  （同 `corpus_fetch` 的 rejected_sources 纪律）。
- **确定性**：不读时钟、不随机；同输入两次跑产物逐字节一致。`--shuffle` 是注入开关，
  只供验证「确定性断言能判红」，生产不用。

```bash
# 造演示输入（自造话术，含占位符 / 超长 / 跨句三类负例）
python3 tools/feedback_mining/make_demo_inputs.py --out-dir /tmp/vox-feedback-demo
# 挖掘（含 provenance：输入 sha256 + 仓 commit）
python3 tools/feedback_mining/miner.py \
    --ledger /tmp/vox-feedback-demo/turns.jsonl \
    --events /tmp/vox-feedback-demo/events.jsonl \
    --out-dir /tmp/vox-feedback-demo
```

下一步是**人工**的：候选 → 判 A1/A2 与禁入项 → 写 `packs/<包>/admission.md`
（`docs/14 §三`）→ `bin/vox pack check` → `vox pack build`。`vox pack check` 的机器化
admission 校验是 `docs/14 §四` 登记的待办，本工具不接线（`cli/` 是冻结区）。

## 三、口径（照 docs/11，不自创）

1. **许可只信 README，不信平台标签**。判据在 `audit_license` 里：
   平台字段、README front-matter 必须**两边都给且一致**；README **正文**命中禁商用/禁演绎声明
   → 硬拒绝，**无逃生口**（`docs/11 §11.9.2` 的实测事故：front-matter 写 `apache-2.0`、正文写 `CC BY-NC-ND`）。
   `ALLOWED_LICENSES = {mit, apache-2.0, cc-by-4.0, cc0-1.0, bsd-3-clause}`。
2. **`verified_against_platform=false` = 平台没给这个文件的哈希**（HF 镜像只对 LFS 文件给 `lfs.oid`）。
   只记录了实测值——**不得把这个格子当作"已交叉核对"**。
3. **golden set 一条用例记两个字段**（`docs/11 §11.9.3`，首轮最重要的口径澄清）：
   - `assistant_reply_free`（助手自由文本）→ 实验① 逐字档的输入，问「LLM 生成的那句能不能逐字命中预铸 variant」；
   - `user_utterance`（用户话术）→ 实验② 语义档的输入，问「用户的自由说法能不能映射到正确的 key」。
   拿用户话术去逐字比客服 variant 是**范畴错误**，不是"命中率低"。
4. **`expect_kind = "unspecified"` 必须存在**（`docs/11 §11.5`）：策略12「其它」装不进任何固定功能，
   标成「这一轮**不该**命中任何预铸 key」，否则把所有轮次都算成"应该有 key"，命中率是虚高的。
5. **派生话术必须改写，不得逐字复制语料原文**（`docs/11 §11.7`）。本目录只产出 golden set 对照集，
   不产出 `phrases.json`。

## 四、复现命令

三个脚本都是**纯标准库**（规避本机 pypi 吞吐极低：实测 12 s 只拉到 363 KB / 46 MB）。

```bash
cd （仓库根）

# ① 只审计许可，不下载（先跑这个，别先跑下载）
python3 tools/corpus_fetch/fetch.py --dry-run \
        --sources labs/corpus-harvest/sources.json \
        --lock    ~/corpus/corpus.lock.json

# ② 下载 + 逐文件 sha256 交叉核对
python3 tools/corpus_fetch/fetch.py \
        --sources labs/corpus-harvest/sources.json \
        --lock    ~/corpus/corpus.lock.json

# ③ 事后巡检：重算 sha256 与台账比对
python3 tools/corpus_fetch/fetch.py --recheck --lock ~/corpus/corpus.lock.json

# ④ 派生 golden set（换业务必须传 --strategy-map）
python3 tools/corpus_fetch/derive_golden.py \
        --corpus ~/corpus/tongyi_dianjin__DianJin-CSC-Data/data/CSConv.json \
        --out    /tmp/golden_derived.jsonl

# ⑤ 逐字档基线（复用 adapters.framework_kefu.normalize.normalize_text，不另写一份）
python3 tools/corpus_fetch/baseline_literal.py \
        --pack packs/fin-cs --golden /tmp/golden_derived.jsonl --out /tmp/literal_baseline.json

# ⑥ 测试（离线可跑，不联网）
python3 -m unittest discover -s tools/tests -v
```

### 参数（去硬编码后新增的）

| 脚本 | 参数 | 缺省 | 说明 |
|---|---|---|---|
| `fetch.py` | `--sources` | 仓库根下的 `sources.json` | 来源声明。**本批的 `sources.json` 是数据，留在 `labs/`**，不搬 |
| | `--lock` | `--out-root/corpus.lock.json` | 台账路径，必须仓外 |
| | `--out-root` | `~/corpus` | 语料落盘根。**指向仓库内直接抛错**（`docs/11` 裁定 2） |
| | `--repo-root` | 自推（`Path(__file__).parents[2]`） | 用于拒绝写仓库内路径 |
| `derive_golden.py` | `--strategy-map` | 内置 DianJin-CSC 12 类映射 | **换业务必须传**——这张表是业务判断，不是语料自带的事实 |
| `baseline_literal.py` | `--repo-root` | 从 `--pack` 向上推 | 用于 `importlib` 导入产品归一化函数 |

## 五、搬的时候补的三个缺口（labs README §8.1 的三个坑，不许退化）

| 坑 | labs 版状态 | tools 版 |
|---|---|---|
| **坑 1 · `optional` 闸门被重写时丢掉** | 已修 | 缺省跳过并**响亮打印**跳过声明（静默跳过 = 让人以为已经下了）；`--only <id>` 可显式拉单个 optional 项 |
| **坑 2 · 大文件一次性读进内存** | 已修 | `http_download_to_file`：流式分块写盘 + 边下边算 sha256；先写 `.part` 再改名，失败删 `.part`（残留 = 会被误认成完整语料） |
| **坑 3 · 台账 read-modify-write 不是原子** | 记录为已知限制 | `write_lock`：临时文件 + `rename`；`_hold_lease` 用 `fcntl.flock` 拿写锁，**检测到并发实例直接拒绝启动** |

## 六、与 labs/corpus-harvest/ 的关系

- `labs/corpus-harvest/` **原样保留**，作为本批的历史证据（T12 白名单明确禁止改动它）。
- 搬的是三个脚本；**`sources.json` 与 `corpus.lock.json` 不搬**——它们是那一批的**数据**（具体来源声明与
  实测台账），留在 `labs/` 里跟那批的结论绑定。本目录的脚本通过 `--sources` / `--lock` 指向它们。
- `labs/` 里的 `semantic_probe*.py` / `cross_check_template_rate.py` / `raw/` **没有搬**（T12 只列了三个），
  仍是那批的一次性产物。
- 那批的 README（`labs/corpus-harvest/README.md`）仍是**数字的出处**：
  逐字档 `0/2433 = 0.00%`、语义档 top-1 `11.8%`、句式可复用率 `1.8%`——对外引用一律以那份为准，
  本目录不提供新的对外数字。

## 七、revision 缺省与并发口径（T12b 审计修复后）

**revision 缺省只有一个值**：`fetch.py` 里的模块级常量 `DEFAULT_REVISION`（现为 `main`），
许可审计、文件树取数、下载 URL 三处都引同一个常量。此前审计侧与下载侧缺省**取的不是同一个值**，
仓库同时有 master/main 两个分支时，**审计证据与实际入库内容会绑在不同版本**——等于静默绕过
「以 README 为准」。现在改任一处都必须改常量，不会再出现两份字面量缺省。

**台账的「读旧 → 合并 → 写」在同一个锁临界区内**：写入口是 `update_lock(lock_path, merge_fn)`，
它在持有 `_hold_lease` 的期间完成读旧台账、合并与原子写。此前的形态是读在锁外、`write_lock`
只盖写，于是 B 实例在 A 提交前读到陈旧台账、A 释放锁后 B 才写入 → **A 的条目被静默覆盖**，
B 照常打「台账已写」exit 0（2026-09-18 那次并发事故的形态）。现在临界区内的第二个实例拿不到
锁，直接 `FetchError` 拒绝启动——宁可硬失败，也不允许静默覆盖。

**锁随进程退出自动释放，没有也不需要显式「释放锁」接口**：`flock` 的锁按 open-file-description
归属，同一个文件在两个 fd 上各自持锁、互不干扰——在**新开的 fd** 上 `LOCK_UN` 什么都不会释放，
却恒返回成功。`fetch.py` 里曾有一个「显式释放写锁」的辅助函数，就是这个形态：调用它"成功"了，
锁一分都没松。T12c 已把它删掉（连同唯一的调用方测试）。正确口径是：`_hold_lease` 的 `finally` 在
同一 fd 上解锁、进程退出时内核再兜底；要"解锁"只有两种正确做法——正常跑完让临界区结束，或
杀掉持锁的进程。别相信任何"我已经释放了锁"的自我报告。

**旧台账坏 JSON 一律中止**：解析失败抛 `FetchError` 且不改动原文件字节（消息含台账路径与解析
错误摘要）。此前的行为是打一行警告就整体覆盖，旧条目会全部丢失、对应语料从此脱离 `--recheck`
巡检。要继续得人工备份/删除该台账后重跑。

**声明文件缺失一个都不下**：`sources.json` 里声明了具体路径（非 `*`）时，`select_files` 逐项核对
每个声明路径都在平台文件树里，有缺失就抛 `FetchError` 并列出缺失路径全名与来源 id。此前的行为是
只要还有 ≥1 个命中就放行，被改名/删除的声明文件被静默丢弃——不完整语料当完整入库，
`manifest_digest` 只覆盖现存文件。

**映射表外的策略标签必须留痕**：`derive_golden.py` 按标签 `Counter` 计数，结尾打印跳过清单；
总数 > 0 时退出码非 0（`4`）。此前的形态是 `if strategy not in mapping: continue`，
上一行注释写「一律跳过并留痕」而**实际没有任何计数或日志**——轮次静默消失、golden set 残缺、
命中率口径失真。
