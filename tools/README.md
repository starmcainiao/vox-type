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

测试：`tools/tests/test_corpus_fetch.py`、`tools/tests/test_derive_golden.py`、
`tools/tests/test_feedback_mining.py`（38 条，覆盖 T23 的验收 1–7 与负例实测）。

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
