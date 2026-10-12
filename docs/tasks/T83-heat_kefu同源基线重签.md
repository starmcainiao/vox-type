# T83 · packs：heat_kefu 同源基线重签（kefu yaml 提交 099394a）

## 卡状态
- [x] 已写卡 → [x] 已派发 → [x] 已回收 → [x] **验收通过（附证据）** / 退回（附原因）

### 验收记录（2026-10-11，验收方独立复算）

| # | 验收项 | 期望 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | 重签前对账 | 36 条中唯一红 = 指纹 | `Ran 36 tests FAILED (failures=1)`，唯一红 `test_yaml_fingerprint_unchanged` | ✅ |
| 2 | 重签后全绿 | 36 条 `OK` | `Ran 36 tests OK` | ✅ |
| 3 | 全量零红 | `failed=0` | `ran=1679 skipped=4 executed=1675 failed=0 roots=11`（T82 同批）；Linux 容器 `ran=1679 skipped=44 failed=0 roots=11` | ✅ |
| 4 | 旧值零命中 | 无输出 | `grep -rn "d893fee8" packs/heat_kefu/` → rc 1；新值在 tests 常量与 admission §0 各 1 处 | ✅ |
| 5 | 审计门禁无回归 | rc 0 / `passed=true` | rc 0、`passed=true`、`policy=pending-business-decision`、`verbatim-overlap=24`（与 `docs/13 §五#29` 登记数一致，未增减） | ✅ |
| 6 | 数字复算 | 见下 | **发现卡一处数字错误，已按实测更正并留档** | ⚠️→✅ |

**验收 6 复算明细**（`packs/AGENTS.md §⑥.5` 第 3 步要求「先对账后重签」，顺序未反）：

| 项 | 卡 | 实测 | 判定 |
|---|---|---|---|
| sha256 | `efd2acc0…` | 同 | ✅ |
| 大小 | 23850 bytes | 23850 | ✅ |
| top-level key | 78 | 78 | ✅ |
| scalar/dict 拆分 | 69 / 9 | str 68 + int 1 = 69；dict 8 + list 1 = 9 | ✅ |
| diff 体积 | +3 / −0 | `git show 099394a --stat` = 3 insertions | ✅ |
| **行数** | **332** | **331**（`wc -l`） | ❌ **卡错** |

**卡错处置（按 `docs/tasks/01-接手指南` §五·12「在卡内更正并注明，不改实现迁就卡」）**：
`admission.md` §0 写**实测值 331 行**。根因是**旧值本就多算 1 行**——T53 记的 `329 行`
对应的旧文件（`099394a~1`）实为 **328 行**，`wc -l` 独立复核。
更正与根因已写入 `admission.md §0.3.2`（本卡白名单内），并附**行数一律以 `wc -l` 为准**的口径。

**门禁自证（`packs/AGENTS.md §⑥.5` 第 4 步，必做）**：向 `phrases.json` 的 `farewell`
variant 尾部注入一个句号 → `FAILED (failures=6)`，判红用例含
`test_one_char_tamper_is_detected` / `test_variant_is_byte_identical_to_yaml` /
`test_all_40_variants_equal_yaml_verbatim` 等；`git diff` 确认还原干净。
**注入后仍报红 = 门禁的判红能力未被削弱**，非靠弱化断言通过。

**留挂未处置（按卡面处置口径，非遗漏）**：新 key `transfer_ready_again` **未铸入**，
登记为未覆盖第 2 条（§0.1 / §0.3.2 / §1.5 三处同步）。铸入需连带改
`phrases.json` 与 `script.json`，二者均不在本卡白名单 → 越界比少做更严重。
建议后续单独开「补铸」内容卡，把 `vague_slot_clarify` 与 `transfer_ready_again` 一并处理。

## 前置拍板（已由维护者拍板，2026-10-11）

**接受新的同源基线**：kefu 侧 yaml 已被上游改过（提交 `099394a`，BK#108「重复请求转人工」）。
本卡执行「重签」= 把新 sha256 写入两处（tests 常量 + admission §0），
并把新增 key `transfer_ready_again` **登记为未覆盖留挂（不铸入）**——
处置口径同 T53 对 `vague_slot_clarify` 的先例。补铸另开内容卡。

## 背景（只写必需）

`packs/heat_kefu/tests/test_source_of_truth.py::test_yaml_fingerprint_unchanged` 按设计变红：
实测 sha256 `efd2acc0…` ≠ 基线 `d893fee8…`。逐字节对账结论（已实测）：

- diff = **+3 行 / −0 行**（2 行注释 + 1 个新 key）；
- 新增 key：`transfer_ready_again`（33 字 / 1 句末标点 / 0 槽位——按判据属**可铸单句**，本卡不铸）；
- **既有 77 个 top-level key 全部逐字节未变**（BAKED/SEQ/SLOT 清单零失配）；
- 重签前 `python3 -m unittest discover -s packs` → 36 条中**唯一**失败 = 指纹断言（预期红）。

**顺序纪律（T53 §三.3）**：必须先完成对账（复跑取得「唯一红=指纹」的原始输出）、后重签；
先改指纹 = 抹掉门禁。

## 目标（可验收的产物）

1. `packs/heat_kefu/tests/test_source_of_truth.py`——`YAML_SHA256` 常量更新 + 注释块补记（T83 两行）。
2. `packs/heat_kefu/admission.md`——§0 表 3 处、§0.1 表（标题 + 2 行 + ⚠️ 注）、§1.5 表 2 行、新增 §0.3 重对账记录。

## 允许修改的文件（白名单）

```
允许修改：packs/heat_kefu/tests/test_source_of_truth.py
          packs/heat_kefu/admission.md
禁止触碰：其他一切文件（尤其 packs/heat_kefu/phrases.json、script.json、packs/heat_kefu_build/**、docs/**、cli/**）
```

## 具体改动（逐字照抄，不要自由发挥）

### 改动 1：`packs/heat_kefu/tests/test_source_of_truth.py`（两处）

(a) 注释块：在行 `#   下次上游再改仍需按 T53 的同一顺序重做；方案 A（拆句从 yaml 现算）未做。`
之后追加两行：

```python
# 2026-10-11 T83 重对账：上游 099394a（BK#108）再 +1 key（transfer_ready_again，未铸留挂）；
#   既有 77 key 零失配，增量极小 → 直接重签（明细见 admission.md §0.3）。
```

(b) 常量替换：

```python
原：YAML_SHA256 = "d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652"
新：YAML_SHA256 = "efd2acc0e90958f785c2ecb91a607fc157b95fa66084ba98a4854dbdb7568a2b"
```

### 改动 2：`packs/heat_kefu/admission.md`（六处）

(a) §0 表三行替换：

```
| sha256 | `d893fee86625b26c1e327236146e3d02c07dafae3ec3e3f18cb4ee1a2c9fc652` |
| 大小 | 23561 bytes / 329 行 |
| top-level key | 77（scalar 68 + dict 9） |
```
替换为
```
| sha256 | `efd2acc0e90958f785c2ecb91a607fc157b95fa66084ba98a4854dbdb7568a2b` |
| 大小 | 23850 bytes / 332 行 |
| top-level key | 78（scalar 69 + dict 9） |
```

(b) §0.1 标题：`## 0.1 对账表（yaml 77 top-level key 全覆盖）` → `## 0.1 对账表（yaml 78 top-level key 全覆盖）`

(c) 「未覆盖话术」行：

```
| **未覆盖话术（T53 新发现，待补）** | **1** | `vague_slot_clarify`——上游 9-24 新增的多分句话术，本包**尚未铸入**，见 §0.2.3 |
```
替换为
```
| **未覆盖话术（T53/T83 新发现，待补）** | **2** | `vague_slot_clarify`（T53，多分句）、`transfer_ready_again`（T83，单句）——上游新增、本包**尚未铸入**，见 §0.2.3 / §0.3 |
```

(d) 「合计」行：

```
| **合计** | **77** | = yaml top-level 实际 key 数，无遗漏无越界（40 单句 + 14 整段 + 13 带槽 + 13 非话术 + 1 未覆盖；29 拆句是这 14 整段的派生，不占新的 top-level key） |
```
替换为
```
| **合计** | **78** | = yaml top-level 实际 key 数，无遗漏无越界（40 单句 + 14 整段 + 13 带槽 + 13 非话术 + 2 未覆盖；29 拆句是这 14 整段的派生，不占新的 top-level key） |
```

(e) ⚠️ 注（§0.1 表下方）：

```
⚠️ **本表原口径「无遗漏」已不成立**：T53 对账发现上游新增了 1 条**话术** key（`vague_slot_clarify`），
本包未铸入。补铸需要同时改 `script.json`（`test_script_covers_all_baked_keys` 断言
「剧本覆盖全部包内 key」），而 `script.json` **不在 T53 白名单**内 → 留挂，详见 §0.2.3。
```
替换为
```
⚠️ **本表原口径「无遗漏」已不成立**：T53/T83 对账累计发现上游新增 **2 条话术** key（`vague_slot_clarify`、`transfer_ready_again`），
本包均未铸入。补铸需要同时改 `script.json`（`test_script_covers_all_baked_keys` 断言
「剧本覆盖全部包内 key」），而 `script.json` **不在 T53 / T83 卡白名单**内 → 留挂，详见 §0.2.3 / §0.3。
```

(f) 新增 §0.3 节 + §1.5 表两行：

在 §0.2.4 段末（`全量清扫建议另开卡。`）与 `## 1. 必铸 40（逐字铸入，一个标点不改）` 之间插入：

```markdown
## 0.3 T83 重对账记录（2026-10-11）

上游在 T53 基线（`e16a777`）之后再改 1 次本 yaml：**`099394a`**（2026-09-28，BK#108
「重复请求转人工」），sha256 已随之改变（现值见 §0）。对账结论：**增量极小，重签即可，无需重铸**。

### 0.3.1 变更面（逐字节对账）

| 项 | 结果 |
|---|---|
| diff 体积 | +3 行 / −0 行（2 行注释 + 1 个 key） |
| 新增 key | `transfer_ready_again`（33 字 / 1 句末标点 / 0 槽位——按判据属**可铸单句**） |
| 既有 key | 77 个 top-level 全部逐字节未变（BAKED/SEQ/SLOT 清单零失配） |

### 0.3.2 处置

- **指纹重签**：`YAML_SHA256` 更新为 `efd2acc0…`（tests 常量与 §0 同源更新；顺序=先对账后重签）。
- **新 key 留挂**：`transfer_ready_again` **不铸入**（登记为未覆盖第 2 条；铸入需同时改
  `phrases.json` 与 `script.json`（均不在 T83 卡白名单），处置口径同 §0.2.3）。
  建议后续单独开「补铸」内容卡，把 `vague_slot_clarify` 与 `transfer_ready_again` 一并处理。

### 0.3.3 复跑证据

- 重签前：`python3 -m unittest discover -s packs` → 36 条中**唯一**失败 = 指纹断言（预期红）。
- 重签后：同命令 → 36 条全绿（`OK`）。
```

§1.5「对账（刷新）」表两行替换：

```
| 未覆盖话术（T53 留挂，见 §0.2.3） | 1 |
| **合计** | **77**（= yaml top-level key 数，守恒） |
```
替换为
```
| 未覆盖话术（T53/T83 留挂，见 §0.2.3 / §0.3） | 2 |
| **合计** | **78**（= yaml top-level key 数，守恒） |
```

## 禁止事项

- **不得把 yaml 正文 / 话术原文写进仓库**（发布树红线）：只允许 key 名、字数、标点计数、sha、字节数等元数据；
- 不得改 `BAKED_KEYS` / `SEQ_KEYS` / `SLOT_KEYS` / `MULTI_CLAUSE_KEYS` 任何清单（本卡不铸入、不排除）；
- 不得改 `phrases.json` / `script.json` / 剧本 / 包内容（补铸另开卡）；
- 不得改任何测试断言逻辑（只允许增/改注释与常量值）；
- 不得提交；不得动白名单外文件。

## 验收标准（逐条可判定，我会逐条核对）

1. **重签前对账证据（先做，留原始输出）**：

   ```sh
   python3 -m unittest discover -s packs
   ```

   期望：`Ran 36 tests` + **唯一失败** = `test_yaml_fingerprint_unchanged`（这就是负控：门禁在拦）。

2. **重签后全绿**：

   ```sh
   python3 -m unittest discover -s packs
   ```

   期望：`Ran 36 tests` + `OK`（0 失败 0 错误）。

3. **全量零红**：

   ```sh
   VOX_MAX_TAIL_SILENCE_MS=250 VOX_MAX_HEAD_SILENCE_MS=100 python3 tools/run_all_tests.py
   ```

   期望 `failed=0`（需 T82 同批完成；若未完成，则允许 cli 根红、`packs` 必须转绿）。

4. **旧值零命中**：

   ```sh
   grep -rn --include='*.py' --include='*.md' --include='*.json' "d893fee8" packs/heat_kefu/
   ```

   期望：无输出（rc 1）；新值成对出现（tests 常量 + admission §0）。
   （只扫源码/文本；`__pycache__/*.pyc` 是旧编译缓存，会残留旧字符串，不算证据。）

5. **审计门禁无回归**：

   ```sh
   python3 tools/pack_phrase_audit.py
   ```

   期望 rc 0、`"passed": true`（policy 仍为 `pending-business-decision`，属既有口径）。

6. **数字复算一致**（防手滑）：sha256 = `efd2acc0…`；23850 bytes；`len(yaml.safe_load(...))` = 78。

## 反空转条款（T01 教训）

- 指纹断言的正/负控天然成立：重签前必红（见验收 1）、重签后必绿（验收 2）；
- `test_one_char_tamper_is_detected` 等篡改检出用例必须保持全绿（不容许靠弱化断言通过）；
- 不许把指纹测试改成 skip 或提高容差。

## 回滚方式

```sh
git checkout -- packs/heat_kefu/tests/test_source_of_truth.py packs/heat_kefu/admission.md
```

## 执行方式

**首选**（2026-09-17 起）：ZCode 子智能体 **`vox-card-executor`**（跑在「商汤」provider，
`sensenova-6.8-flash-lite`，自带 Write/Edit 直接落地）。定义：`~/.zcode/agents/vox-card-executor.md`；
**新建/改动 agent 定义后需新开会话才注册**。

**回落**（`vox-card-executor` 不可用时）：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T83-heat_kefu同源基线重签.md)" --dir （仓库根）
```

## 卡状态
- [x] 已派发 → [x] 已回收 → [x] 验收通过（附证据）/ 退回（附原因）
