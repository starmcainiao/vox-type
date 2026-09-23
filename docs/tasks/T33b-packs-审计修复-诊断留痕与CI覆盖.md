# T33b · packs + adapters + labs：T33 审计修复（诊断留痕 / CI 覆盖 / 报告溯源）

## 卡号 / 标题
`T33b · 审计修复：blocked_by 诊断留痕 + CI 真包覆盖 + report 溯源 + 三处卫生`

## 背景（只写必需）

- T33 已验收通过并提交（基线 **`d83b910`**）。随后 `blackiron-silent-failure-hunter` 审计：
  **2 P1 + 3 P2 + 3 建议**。其中 **P1-1（空归一化条目 → 覆盖循环空转/零留痕挂死）已由验收方
  在 `d83b910` 内修复**（两道护栏 + 3 条 SIGALRM 回归测试）——本卡不动它。
- **规格依据（先读，不得改）**：`docs/10-接入取景与命中口径.md §10.7`——其末节已于 2026-09-21
  更新为「序列档与 `find_hit` 的候选口径**有意不同**（序列档只认 `part_index==0` + `rate_key` 精确
  匹配 + `confirm` 通过）+ `SequenceHitResult` 带 **`blocked_by`** 诊断字段」。本卡按它实现。
- 本卡**不改命中数据**（`packs/heat_kefu/{phrases.json,script.json,admission.md}` 一律不动）。

## 目标（5 项，逐项可验收）

1. **P1-2**：`find_hit_sequence` 新增 `blocked_by` 诊断字段——未命中时记录「文本能匹配、但因
   `rate_key` / `part_index` 口径被剔除」的候选标识；**命中时恒为空元组**；**只进诊断、永不进命中**。
2. **P2-a**：CI 真包覆盖 =（a）`.github/workflows/tests.yml` 在全量测试前加一步构建 heat-kefu 包；
   （b）`adapters/framework_kefu/tests/test_hit_query.py` 加一组**不依赖 yaml、不依赖构建产物**的
   序列命中自洽断言（用本目录 `build_pack` 夹具造多句包）。
3. **P2-b**：修校验空转——`packs/heat_kefu/tests/test_source_of_truth.py` 里
   `if res.miss_reason is None:` 这种「条件不满足就整段跳过」的写法，改为对每条输入**显式断言**
   期望的 hit/miss（断言体不得可被整体跳过）。
4. **P2-c**：`labs/heat-kefu-seq/report.json` 加 `provenance`（`yaml_sha256` / `git_commit` /
   `manifest_sha256`），让报告能自证语料与代码版本。
5. **建议三条**：① `packs/heat_kefu/tests/test_source_of_truth.py` 末尾「跑法」注释已过期
   （称 `-s packs` 跑不到本文件，实测能跑到），改成实测事实；② `adapters/.../test_hit_query.py`
   里两段重复的 `pack_orig` 构造去重，并把注释里「位置 0 覆盖不上」改成真实成因
   （`confirm` 指纹复核把候选剔除）；③ `.gitignore` 加 `packs/*_build/`。

## 允许修改的文件（白名单）

```
允许修改：adapters/framework_kefu/hit_query.py
         adapters/framework_kefu/tests/test_hit_query.py
         packs/heat_kefu/tests/test_source_of_truth.py
         labs/heat-kefu-seq/run_seq_bench.py
         labs/heat-kefu-seq/README.md（仅在需要同步"结果/溯源"说明时）
         labs/heat-kefu-seq/report.json（重跑产物）
         .github/workflows/tests.yml
         .gitignore
禁止触碰：docs/**（规格由策划写；docs/10 §10.7 已更新，本卡不得再改）、
         compiler/** runtime/** core/** assets/** eval/** cli/**、
         packs/heat_kefu/{phrases.json,script.json,admission.md}（命中数据与申报不动）、
         packs/ 下其他包、<kefu 仓根>/**（另一仓）
```

## 实现规格

### 1. `blocked_by`（P1-2）

```python
@dataclass(frozen=True)
class SequenceHitResult:
    entries: Tuple[AssetEntry, ...]
    mode: str
    text: str
    miss_reason: Optional[str]
    uncovered: str
    blocked_by: Tuple[str, ...] = ()      # 新增（带默认值：既有构造点不必改）
```

- **元素格式（冻结）**：`"<key>@<rate_key>#<part_index>"`，例：`"greet@slow#0"`。
- **计算时机**：**只在未命中路径**（`best is None` / 空段保护触发时）。命中路径恒为 `()`。
- **筛选条件**：遍历 `pack.assets`，命中即收：该条目 `normalize_text(text)` **非空**、且是
  `norm[pos:]` 的**前缀**（消费长度 > 0），但被以下任一条件排除：
  `part_index != 0` 或 `rate_key != 传入的 rate_key`。
  （**不**收录"因 `confirm` 复核不过被剔除"的——那是指纹/文件问题，语义不同，别混进来。）
- **顺序与去重**：按 `pack.assets` 顺序；同一标识只记一次。
- 语义边界（写进 docstring）：`blocked_by` 是**诊断留痕**，对应 `docs/10 §10.7` 与
  `docs/17 §三` 的「near-miss 只进诊断、永不进命中」红线；它**不得**影响 `entries`/`miss_reason` 的判定。

### 2. CI 真包覆盖（P2-a）

2a. `.github/workflows/tests.yml`：在「全量测试」步之前加一步（macOS runner 有 `say`，可构建）：
```yaml
      - name: 构建 heat-kefu 包（packs 的真包断言依赖它；不构建则诚实 skip）
        run: sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1
```
（落点必须是这个路径：`packs/heat_kefu/tests/test_source_of_truth.py::_load_pack_dir` 的候选之一。）

2b. `adapters/framework_kefu/tests/test_hit_query.py` 新增一个测试类（**不读 yaml、不依赖构建产物**，
全部用 `build_pack` 造包），至少覆盖：
- 多句包（如 `[("s1","您好。",0),("s2","请问有什么可以帮您？",0),("s3","再见。",0)]`）：
  两段拼接 → 命中且 `len(entries)==2`；三段拼接 → `3`；
- 负例：拼接中**插入包外句** → `entries==()`、`miss_reason==REASON_TEXT_NOT_PREBAKED`、`uncovered` 从断点起；
- 段序：颠倒两段 → **仍命中**且段序 == 输入顺序（§10.7：不做段序约束）；
- 确定性：同输入连跑 5 次，段 key 序列一致；
- `blocked_by`：命中时 `== ()`。

### 3. 校验空转修正（P2-b）

把 `packs/heat_kefu/tests/test_source_of_truth.py` 里形如
```python
for k in ...:
    res = find_hit_sequence(pack, text)
    if res.miss_reason is None:          # ← 不满足就整段跳过，测试仍绿
        self.assertEqual(len(res.entries), N)
```
改成**无条件断言期望值**（命中用例断言 `entries` 段数与 `miss_reason is None`；未命用例断言
`entries == ()` 与 `miss_reason == REASON_TEXT_NOT_PREBAKED`）。改完**自己验证断言非空转**：
临时把 `find_hit_sequence` 的返回改成 miss（例如在测试内 monkeypatch 或在探针脚本里），
确认对应用例会红——把这条验证写进报告。

### 4. report 溯源（P2-c）

`labs/heat-kefu-seq/run_seq_bench.py` 的 report 增加：
```json
"provenance": {
  "yaml_sha256": "<yaml 文件 sha256>",
  "manifest_sha256": "<构建产物 manifest.json 的 sha256>",
  "git_commit": "<git rev-parse HEAD>"      // git 不可用时写 null，并在 env 里留痕（如 git_error）
}
```
README 的「结果」节补一句：报告自带 `provenance`，可自证语料（yaml sha256）、代码（commit）与
包产物（manifest sha256）版本。**不得**因此把绝对路径或 yaml 全文写进报告（沿用既有卫生纪律）。
`blocked_by` 或 provenance 变更后**重跑一次**让 `report.json` 落盘刷新。

### 5. 三处卫生（建议）

- `packs/heat_kefu/tests/test_source_of_truth.py` 末尾「跑法」注释：删掉「`discover -s packs`
  跑不到本文件」的过期说法，写明实测（`packs/__init__.py` 已入库后能跑到；真断言需先 build + 设 yaml）。
- `adapters/framework_kefu/tests/test_hit_query.py`：`pack_orig` 两段重复构造合并为一份；
  把「改一字后位置 0 覆盖不上」类注释改为真实成因（候选被 `confirm` 指纹复核剔除 → 整体 miss）。
- `.gitignore`：加 `packs/*_build/`（放在「本地数据」段），随后用
  `git check-ignore -v packs/heat_kefu_build/heat-kefu-1/manifest.json` 验证有输出。

## 禁止事项

- 不得改 `docs/**`；不得改 `packs/heat_kefu/` 的命中数据三件（phrases/script/admission）；
- 不得新增第三方依赖；不得"顺手优化/重构"白名单外文件；
- 不得放宽任何既有校验；`find_hit` 与既有函数行为**一律不变**（只加 `blocked_by` 与其计算）；
- 不得把本机绝对路径 / yaml 全文 / kefu 仓路径写进任何产物（report 里 yaml 只许 sha256）。

## 验收标准（逐条可判定，验收方会独立复跑）

1. `blocked_by` 字段存在且语义正确：造「只有 slow 档」的包 → `find_hit_sequence(rate_key="normal")`
   未命中且 `blocked_by == ("<key>@slow#0",)`；同包 `rate_key="slow"` → 命中且 `blocked_by == ()`。
2. **判定不变**：T33 既有全部断言仍绿（adapters 条数只增不减）；真包 14/14 序列命中、40/40 单句
   不受影响（需 yaml + build）。
3. CI workflow 含新增构建步（贴 diff）；构建步的命令与落点与 `_load_pack_dir` 候选一致。
4. **自洽断言真的不依赖环境**：`env -u KEFU_HEAT_YAML python3 -m unittest discover -s adapters`
   → rc 0 且**无 skip**（新测试类在其中跑掉）。
5. **空转修正有效**：给出你做的注入验证（把序列档打成全 miss → 对应用例必须红；还原后绿）。
6. `report.json` 含 `provenance` 三字段且值正确（`yaml_sha256` 与 `shasum -a 256 <yaml>` 一致）；
   重跑 rc 0；产物里无本机路径（机器盘位路径、家目录路径、宿主仓名、父目录名零命中——类别清单见 `docs/18 §一`）。
7. 三个测试根 rc 0 与条数：`packs`（**先 build + 设 `KEFU_HEAT_YAML`**）、`adapters`、`trigger`；
   并报十一根合计条数。**退出码单独确认**（`cmd >out 2>&1; echo $?`，不得接管道读 `$?`）。
8. `.gitignore` 验证：`git check-ignore -v packs/heat_kefu_build/heat-kefu-1/manifest.json` 有输出。

## 反空转条款

- 测试必须调用产品 API；不得在测试内复制被验逻辑；期望值不得写死与实现无关的常量；
- 第 5 条的「注入验证」是硬要求（证明修正后的断言能判红）；
- 正例与负例都要有；负例断言消息含具体值（`uncovered` 断点文本 / `blocked_by` 元素）。

## 回滚方式

`git checkout -- adapters/framework_kefu/hit_query.py adapters/framework_kefu/tests/test_hit_query.py
packs/heat_kefu/tests/test_source_of_truth.py labs/heat-kefu-seq/run_seq_bench.py
labs/heat-kefu-seq/report.json .github/workflows/tests.yml .gitignore`
（`labs/heat-kefu-seq/README.md` 若改动了同法回滚。）

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`（商汤 provider，`sensenova-6.8-flash-lite`）。
**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T33b-*.md)" --dir （仓库根）`。
数据分级 = **公开**（无录音/真实会话/内网地址/token）——两条路均可派。

**跑 packs 真断言的前置**（本机）：
```
sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1
KEFU_HEAT_YAML="<kefu 仓根>/organs/客服/brain/prompts/供热预设.yaml" python3 -m unittest discover -s packs
```

**执行方纪律**：非交互环境直接落地，不先出计划等确认；只回事实与自测输出，不做达标判定；
不提交 git；与卡冲突的事实停下上报，不自行改口径。

## 验收记录（2026-09-21，验收方逐条独立复核）

**结论：验收通过**。证据明细见 `00-索引.md` 第十九批「T33b」小节。

- 白名单核对：`git status --porcelain -uall` 仅 8 个白名单文件；`packs/heat_kefu/{phrases,script,admission}`
  与 `docs/**` 零改动。
- **验收 1（blocked_by）**：自写探针——slow-only 包 `normal` 查询 → miss + `blocked_by == ('greet@slow#0',)`；
  同包 `slow` 查询 → 命中 + `()`；同包 `find_hit(normal)` 对照命中（口径差异真实）。
  另实测 `load_pack` 在装载期拦下"文件缺失/指纹不符"的包（故该分支真实数据不可达，执行方以构造法覆盖）。
- **验收 2（判定不变）**：真包 14/14 整段序列命中、40/40 单句双档命中、命中路径 `blocked_by` 并集为空。
- **验收 3（CI）**：构建步 diff 已在正确位置；落点 == `_load_pack_dir` 候选 ③。
- **验收 4（自洽断言）**：`env -u KEFU_HEAT_YAML python3 -m unittest discover -s adapters` → **226 / OK / 0 skip**（亲跑）。
- **验收 5（空转修正）**：**亲自注入**（内存 patch 序列档为全 miss + 后导入测试模块）→ 该测试类
  **17 处判红 / 9 条测试**；还原后绿。断言非空转。
- **验收 6（provenance）**：三字段与 `shasum -a 256`（yaml、manifest）/ `git rev-parse HEAD` 逐项一致；
  产物路径扫描 0 命中。
- **验收 7（十一根）**：core 44 / rules 21 / assets 59 / adapters **226** / compiler 168 / runtime 118 /
  eval 208 / cli 85 / tools 84 / trigger 159 / packs 35（0 skip）= **1,207 全绿**（逐根确认 rc=0）。
- **验收 8（.gitignore）**：`git check-ignore -v packs/heat_kefu_build/heat-kefu-1/manifest.json` → 有输出。
- 执行方 5 条不确定项的裁定：① 命中路径 `blocked_by` 恒 `()`——**按卡面（即 §10.7）**，正确；
  ② 元素格式用候选自身 `rate_key`/`part_index`——**正确**（要标出候选被哪个口径挡掉）；
  ③ `pack_orig` 去重时删掉重复导入——**接受**（白名单内、行为不变）；④ report 的 `git_commit` 记重跑
  时刻 HEAD——**接受**（报告自证"由该提交的代码产出"）；⑤ packs 35 条 vs 单测内 14+40 循环——
  **正确理解**（循环内条目不计入 unittest 用例数）。
- 已知残留（不阻塞）：report 的 `git_commit` 指向产出它时的 HEAD；提交本卡后如需指向新提交，重跑一次 labs 即可。

## 卡状态
- [x] 已派发 → [x] 已回收 → [x] **验收通过**（证据见上）
