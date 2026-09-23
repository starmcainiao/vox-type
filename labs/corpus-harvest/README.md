# 语料自给与语义档实测 · 2026-09-18

> **实验区产物，不进任何层契约**（根 `AGENTS.md`：`labs/` = 一次性实验与外部对拍，产物自带口径与原始数据）。
> 规格依据：`docs/11-语料自给与语义提案-口径.md`。
> 本目录回答一个问题：**脱离公司数据，用公开语料能不能验证预铸这件事——验证结果是肯定的「能不能」，也是否定的「值不值得」。**

---

## 一、一句话结论

**语料拿到了、链路跑通了、闸门过了；但两条最关键的实测都是负面的，而且它们改变项目的对外表述：**

1. **逐字档在 2433 条真实语料上命中 0.00%** —— 与 `docs/10 §10.2` 的预判一致；
2. **语义档（embedding 检索）top-1 只有 11.8%、策略级 12.7%，随机基线 8.3%** —— 与项目门槛 **98.7%** 差 8 倍。**「让上游按 key 说话」不能靠从用户话术反推 key 来实现。**
3. **真实客诉对话里只有 1.8% 的客服话轮是句式可复用的**，75.7% 的电话一通里一句都预铸不了。

但这不是项目的坏消息——它**把预铸的适用边界钉死了**：预铸服务的是**流程决定的话轮**（问候/确认身份/告别/合规提示/请稍等/未听清重试，占话轮 **23%**），不是**内容决定的话轮**。见 §六。

---

## 二、拿到了什么语料（5 份，全部许可干净、可复现重下）

台账：`corpus.lock.json`（`size_bytes` / `sha256` 全部**实测**并与平台声明**交叉核对**）。
落盘位置：`~/corpus/<ns>__<name>/`（**仓外**，`docs/11` 裁定 2）。

| 来源 | 平台 | 许可 | 体量 | 内容 |
|---|---|---|---|---|
| `tongyi_dianjin/DianJin-CSC-Data` | ModelScope | **MIT** | 9.04 MB | **1855 通真实客户-坐席对话，50587 话轮**，客服话轮带 12 类策略标注、8 类业务域 |
| `QingshanAI/ecom-customer-service-synthetic` | ModelScope | Apache-2.0 | 0.19 MB | 电商客服合成对话，`scene/user/bot/tone` 配对 |
| `QingshanAI/ecom-after-sale-synthetic` | ModelScope | Apache-2.0 | 0.27 MB | 电商售后场景 |
| `QingshanAI/ecom-logistics-synthetic` | ModelScope | Apache-2.0 | 0.27 MB | 电商物流场景 |
| `tomsawyerhu/cantonese-dialect` | ModelScope | Apache-2.0 | 1.03 MB | 粤语转写（**无音频**，见 §五） |

**复现**（任意机器）：

```bash
cd labs/corpus-harvest
python3 fetch.py --dry-run    # 只审计许可，不下载
python3 fetch.py              # 下载 + 校验
python3 fetch.py --recheck    # 事后巡检：重算 sha256 与台账比对
```

`fetch.py` 纯标准库（规避本机 pypi 吞吐极低：实测 12 s 只拉到 363 KB / 46 MB）。

## 三、拦截记录：fail-closed 抓到的东西（**这批最该保留的教训**）

许可审计规则见 `docs/11 §11.3`。以下是它实际拦下的，**每一条都会污染一个要公开分发的仓**：

| 来源 | 判定 | 理由 |
|---|---|---|
| `MagicDataTech/magicdata-dialect-wu-chinese-tts-lite` | ❌ 拒 | front-matter 与平台 cardData **都写 `apache-2.0`**，README **正文**表格写 **CC BY-NC-ND 4.0**、并注明 *for non-commercial use only* → 以正文为准 |
| `MagicHub/magicdata-dialect-northeastern-chinese-tts-lite` | ❌ 拒 | 同数据、不同组织上传：front-matter 无 license，正文同样 CC BY-NC-ND → **「同名镜像等价」这个假设不成立** |
| `ASLP-lab/WenetSpeech-Yue` | ❌ 拒 | `cc-by-nc-4.0` |
| `TwinkStart/KeSpeech` | ❌ 拒 | 无许可声明（证据不足） |
| 粤语相关搜索首页 20 个 | ❌ 拒 | 许可字段**全部为 `None`** |

> **审计本身出过一次假通过**：第一版只扫 front-matter，于是 `MagicDataTech` 那份**被判通过了**。
> 是随后核对正文才发现 front-matter 与正文冲突。现在 `fetch.py` 有 `NONCOMMERCIAL_PATTERNS` **硬拒绝**（无人工放行口）。
> 这条记在这里的原因：**fail-closed 不是形式主义**——它唯一的价值就是在这种「两面说法不一致」时站出来。

## 四、预铸产物：`packs/fin-cs/`（17 key / 102 资产）

按 `docs/11 §11.6` 的格式硬约束派生（**一句一 variant**、无占位符、槽位不写在文本里）：

- `phrases.json`：17 个 key，对应语料 12 类策略 + 5 个保障类（重试/澄清/请稍等/合规/转人工）
- `script.json`：19 单元线性流程，含 2 个 `SAY_LIVE` 档（`reason` ∈ `live_whitelist`）
- `golden/corpus_derived.jsonl`：**2433 条**对照集（由 `derive_golden.py` 半自动派生）

**验收实测**（三关全过）：

```
vox pack check  → 通过（phrases=17 units=19 violations=0）
vox pack build  → total=102 synthesized=102 failed=0 quality_issues=0 clean=true
vox bench       → fast: hit_rate=1.0 tts_calls_p50=0.0  参考门槛 0.987 → passed=true
                  （timing_metrics_meaningful=false：缺省适配器是离线替身，时序数字不被报告背书）
```

> **bench 的 1.0 要读懂再引用**：它是**脚本驱动档**（key 由流程给定、不经检索）的回归闸门，
> 不是真实覆盖率。真实覆盖率看 §五。`eval/corpus/fin_cs_keydrive.json` 的 `notes` 里已写明这一点。

## 五、两条实测的负面结论（**本目录的核心产出**）

### 5.1 逐字档：0.00%（n=2433）

```bash
python3 baseline_literal.py --pack ../../packs/fin-cs --out raw/literal_baseline.json
```

| 臂 | 输入 | 命中率 |
|---|---|---|
| 实验①（正解） | 助手自由文本 `assistant_reply_free`（真实链路里 LLM 生成的那句） | **0/2433 = 0.00%** |
| 对照（范畴错误） | 用户话术 `user_utterance` | 0/2433 = 0.00% |

用**产品自己的** `adapters.framework_kefu.normalize.normalize_text`，不另写一份（防"测试测自己"）。
相似度分布解释了为什么是 0：即使最相近的 variant，中位相似度 **0.204**、P90 **0.360**。

> **一条必须写清的区分**：`docs/10 §10.2` 里的「逐字比对」，比的是**助手打算说的那句话**与包内 variant，
> **不是用户说的话**。所以拿用户话术去比客服 variant 是**范畴错误**，不是"命中率低"。
> golden set 因此一条用例记两个字段（`assistant_reply_free` / `user_utterance`），两个实验分开做。

### 5.2 语义档：top-1 11.8%（n=2064）

```bash
python3 semantic_probe.py --golden ../../packs/fin-cs/golden/corpus_derived.jsonl \
                          --pack ../../packs/fin-cs --out-jsonl raw/semantic_probe.jsonl
python3 semantic_probe_control.py --golden ../../packs/fin-cs/golden/corpus_derived.jsonl \
                                  --probe raw/semantic_probe.jsonl
```

模型：oMLX `bge-m3-mlx-fp16`（1024 维，**本机已常驻、零下载**）。**只提案，不出声**（`docs/11` 裁定 1）。

| 实验 | top-1 | top-3 | 随机基线 |
|---|---|---|---|
| 路线 A：匹配**预铸 variant 原文** | **11.8%** | 29.5% | 5.9%（1/17) |
| 路线 B：匹配 **key 功能描述** | 8.9% | 24.1% | 5.9% |
| **对照 1：策略级 12 类**（去掉我自拟的 key 设计） | **12.7%** | 32.5% | 8.3% |
| 对照 3：把语义相邻功能视为可用（宽松口径） | 27.5% | — | — |

**对照 1 是决定性的**：换成语料自带的 12 类标签、把我自拟的 key 体系整个拿掉，top-1 仍只有 12.7%
（随机的 1.5 倍）→ **是任务本身难，不是 key 设计的问题。**

**对照 2（混淆结构）**说明难在哪里——误差塌陷到两个「吸收态」：

```
  91x  期望 ask_feedback        → 预测 execute_solution
  80x  期望 verify_identity     → 预测 restate_issue
  76x  期望 probe_detail        → 预测 restate_issue
  64x  期望 inform_fact         → 预测 restate_issue
  62x  期望 empathy_hardship    → 预测 restate_issue
  61x  期望 restate_issue       → 预测 hold_notice
```

检索退化成几个泛化原型：**从「用户说了什么」反推「这轮该用哪个话轮功能」，信息本来就不足**——
同一位用户说「分两期的话每期要还多少」，客服可以答信息、也可以给建议，人说哪句都成立。

**结论**：`docs/10 §10.2` 说「若要提速，必须把固定话术改由 key 驱动」——现在有了下句：
**那个「改由 key 驱动」不能靠从用户话术做语义检索来实现**（差 8 倍）。它只能靠**业务流程本身产出 key**。

## 六、真实语料对「预铸准入」的实测（T13 的输入）

在 1855 通真实电话 / 25810 个客服话轮上（去实体值后比对句式骨架）：

| 口径 | 数字 |
|---|---|
| 出现 ≥5 次的可复用句式覆盖的话轮 | **1.8%** |
| 骨架压缩比（全局） | 1.03x（几乎不重复） |
| 策略 3/4/5/6/7/8/10 的骨架压缩比 | **1.00x**（零模板化） |
| 一通电话里一句都预铸不了的占比 | **75.7%** |
| 出现最多的 18 个可复用句式 | **全部是开场问候与结束语** |

**所以预铸准入的正确判据不是「能不能检索到 key」，而是「这轮的话术是不是由流程决定」**：

| 话轮类型 | 由谁决定说什么 | 占比 | 预铸可行性 |
|---|---|---|---|
| 仪式性/流程性（问候、确认身份、告别、合规提示、请稍等、未听清重试、转人工） | **流程** | **23.0%** | ✅ 可行——key 由流程给，命中率 100%（本批 bench 实测） |
| 内容性（信息传达、提供建议、解决实施、共情、细化问题） | **现场内容** | 77.0% | ❌ 不可行——每轮内容不同，句式 98%+ 不重复 |

**对外表述必须据此收紧**：不能说"让语音链路的固定部分零延迟"（听起来像大部分能固定），
要说"**让流程决定的那部分话术零延迟；公开语料实测：在事实密集型业务里这部分占 23% 的话轮，其余 77% 每轮不同**"。

### 6.1 跨语料交叉验证（换一份完全不同的语料，结论是否成立）

```bash
python3 cross_check_template_rate.py --out raw/cross_check.json
```

| 语料 | 话轮 | 骨架 | 压缩比 | 句式可复用率 |
|---|---|---|---|---|
| `DianJin-CSC`（**真实**，催收/账户服务） | 25810 | 25072 | 1.03x | **1.8%** |
| `QingshanAI/ecom-*`（**合成**，电商客服 3 场景合并） | 2976 | 2959 | 1.01x | **0.2%** |

**读法（重要，别读反）**：换一份来源、场景、生成方式都不同的语料，结论**没有翻转**。
但**这不构成"真实业务里可预铸率更低"的证据**——合成语料的措辞多样性是 LLM「造」出来的，
多样性被生成方式人为放大了。它只说明：**这份数据不构成反例的反面**。

**因此有一条结论必须留白**：「流程驱动型业务（报修登记、查询播报）的可预铸率显著更高」这个假设，
**本批没有验证，也无法用现有公开语料验证**——公开渠道没有「带真实转写的表单/IVR 流程语料」。
它只能靠：① 自建流程（`packs/repair` 就是这个形态，T11 实测 key 档 100% 命中）；
② 或未来的真实业务数据。**不要把它写成已验证的事实。**

## 七、方言这块的实况（**音频已到手，只差一个能读 parquet 的解释器**）

> **状态更正（2026-09-18 深夜）**：早先有一次后台下载**成功**取到了 Wu-Bench 的 `asr.parquet`
> （另有两次因 socket read 超时失败——同一份文件、网络波动，成败都有）。
> 也就是说**下载不再是阻塞项**，剩下的唯一阻塞是**读**它（见下）。
> 这是本目录里唯一一处"文档先说未下载、后被现实推翻"的地方，按纪律更正而不是把文件删掉重来。

| 候选 | 结果 |
|---|---|
| `ASLP-lab/WenetSpeech-Wu-Bench` | ✅ **可用，且文件已在盘上**。许可 apache-2.0（正文无 NC 条款，已全文核过）；`understanding/asr.parquet` **1,030,709,745 字节已下载，sha256 与平台 LFS oid 逐字相符**（`verified_against_platform=true`）。内容：吴语 ASR 9.75 小时（上海话/苏州话/普通话混说），**按 CER 评测**——正是方言链路需要的口径。**唯一阻塞：parquet 需 pyarrow，而本机主解释器是 Python 3.14、PyPI 与清华镜像都没有 3.14 wheel**（`from versions: none`） |
| `tomsawyerhu/cantonese-dialect` | ✅ 许可过，**但仓内只有转写清单、没有音频**（CSV 指向仓外的 Common Voice wav 路径，该目录不存在）→ 只能当「粤语说法文本」用，不能做 ASR 评测 |
| `MagicDataTech` / `MagicHub` 方言 TTS | ❌ 禁商用（见 §三） |
| 粤语相关 20 个数据集 | ❌ 全无许可声明 |
| `WenetSpeech-Yue` / `-Chuan` | ❌ CC-BY-NC 禁商用，且 TB 级 |

**批 3（方言链路）的前置条件现在是**：
1. **能读 parquet 的解释器**（`<内部设计仓>/organs/voice/.venv` 是 3.13.3，或 Miniforge 3.12）——**这是唯一的阻塞**；
2. （可选）补一个 **wav + txt 纯文件**的许可干净方言集，让链路不依赖 parquet 读取能力——本批搜到的不满足，需再找。

**另有一条工具纪律**：Wu-Bench 在 `sources.json` 里是 `optional: true`，**缺省跑不会碰它**
（1 GB 不该在例行跑里被拉）。要更新它用 `python3 fetch.py --only ASLP-lab/WenetSpeech-Wu-Bench`。

## 八、本目录文件清单

| 文件 | 作用 |
|---|---|
| `sources.json` | 来源声明（含 `rejected_sources`：被拒来源与理由，是台账的另一半） |
| `fetch.py` | 下载器：先验许可后下载、逐文件 sha256 交叉核对、拒写仓库内路径、`--recheck` 巡检 |
| `corpus.lock.json` | **台账**：id/平台/许可/许可证据 URL/体量/sha256/落盘路径 |
| `derive_golden.py` | 从语料派生 golden set 对照集（策略标签 → key，半自动标注） |
| `baseline_literal.py` | 逐字档基线（复用产品归一化函数） |
| `semantic_probe.py` | 语义档测量（oMLX embedding，两条路线，只提案不出声） |
| `semantic_probe_control.py` | 对照实验（策略级 + 混淆结构 + 宽松口径） |
| `raw/literal_baseline.json` | 逐字档逐条原始数据 |
| `raw/semantic_probe.jsonl` + `semantic_probe_summary.json` | 语义档逐条提案与汇总 |

> **分发排除（T37，2026-09-23）**：上表 `raw/semantic_probe.jsonl` **不进公开分发**——
> 它的每一条都携带语料的 `user_utterance` **逐字原文**（2433 条，实测 800 条 16 字窗口里命中 240 条），
> 属 `docs/11 §11.7` 排除清单第二条（`labs/**/raw/**` 中含第三方逐字原文的部分）。
> **不影响 11.8% 的可核验性**：复现走 `§十` 对账表的命令（重跑 `semantic_probe.py` 写 `/tmp/sp.jsonl`），
> 不依赖这份已提交的原始文件。执行点 = 仓根 `.gitattributes` 的 `export-ignore`（机器强制，不靠人记）。

### 8.1 下载器踩过的两个坑（**T12 提升为工具时不能丢**）

**坑 1：`optional` 闸门在重写时丢了。** 重写 `fetch.py` 时漏掉 optional 过滤，
结果例行跑会去下 `ASLP-lab/WenetSpeech-Wu-Bench` 那份**标着"本批不下载"的 1030.7 MB parquet**。
现在缺省跳过并**响亮打印**跳过声明（静默跳过等于让人以为"已经下了"）。
`--only <id>` 可显式拉单个 optional 项。

**坑 2：大文件不能一次性读进内存。** 原来的 `http_get(binary=True)` 是 `resp.read()`——
1 GB 文件就是 1 GB 内存，而且必然撞 socket read 超时，报错只有
`TimeoutError('The read operation timed out')`，**看不出是"体量太大"还是"网断了"**。
现已改为 `http_download_to_file`：流式分块 + 逐块写盘 + 边下边算 sha256 + 失败删 `.part`。

> 这两条都是**fail-closed 的方向**：坑 1 让"不该下的被下了"，坑 2 让"失败原因读不出来"。
> 前者靠显式闸门 + 响亮声明解决，后者靠流式 + 残留 `.part` 可识别解决。

**坑 3：台账写入不是原子的，并发跑会互相覆盖。** 实测：一个早先启动、后被判定为"陈旧"的后台下载
在我提交 5 条台账之后才真正结束，又把台账重写成了 6 条（它真的把 1 GB 的 Wu-Bench 下成功了，
而后来那次同命令因 socket 超时失败——同一份文件、网络波动，成败都有）。
**影响**：台账的 read-modify-write 没有加锁，两个实例并发跑会丢条目。
**当前处置**：单用户实验工具，记录为已知限制；**T12 提升为工具时应改为原子写（临时文件 + rename）
并在检测到并发时拒绝启动**，不要让两个实例同时写同一份台账。

## 九、边界与未做

- 本目录**不进任何层契约**；语义提案**不出声**、不接 `runtime/`、不改内核（`docs/11` 裁定 1）；
- 语料只覆盖**催收/账户服务**一个场景，**1.8% / 23% 这两个数不能外推到别的业务**——流程驱动型业务（报修登记、查询播报）的可预铸率应显著更高，但那需要另一个业务的语料来测；
- `fin-cs` 包的话术是**改写**的（`docs/11 §11.7` 禁止逐字复制语料原文）；
- 时序数字在本目录一概不报——bench 用的离线替身，`timing_metrics_meaningful=false`；真实链路时序见 `docs/09`。

## 十、回归对账（2026-09-19 已验证，验收方亲跑）

> 对外引用的四个数字全部**逐位复现**。任何人对这些数字有疑，照下表命令重跑即可；
> 期望值即表中数字，实测列是 2026-09-19 的对账结果（环境：主解释器 python3；嵌入走本机 oMLX bge-m3-mlx-fp16）。

| 数字 | 命令（在本目录内跑） | 期望值 | 2026-09-19 实测 |
|---|---|---|---|
| 真实语料句式可复用率 | `python3 cross_check_template_rate.py --out /tmp/cc.json` | **1.8%** | 1.7978% ✓（`reusable_turns=464 / n_turns=25810`） |
| 合成语料句式可复用率（对照） | 同上 | **0.2%** | 0.24% ✓ |
| 语义档路线 A top-1 | `python3 semantic_probe.py --golden ../../packs/fin-cs/golden/corpus_derived.jsonl --pack ../../packs/fin-cs --out-jsonl /tmp/sp.jsonl` | **11.8%** | 11.8% ✓ |
| 语义档路线 A top-3 | 同上 | **29.5%** | 29.5% ✓ |

注：语义路线本身已被实测否掉（`docs/13 §八#1`，top-1 与门槛差 8 倍）；本节存证的是**数字的出处可复现**，
不意味路线仍开放。脚本保留本目录原样（历史证据链，不固化进 tools/）。
