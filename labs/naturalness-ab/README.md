# naturalness-ab · 预铸拼接自然度盲测（pilot）

> 立项五指标里最后一项未完成的**真人听测**（`docs/13 §五#1`：拼接自然度 = 回读 CER ✅ + **盲测 A/B ❌**）。
> 本目录是它的 pilot 实现；`docs/05 §5.3` 把「预铸拼接自然度评测协议」列为拟贡献资产——本批是那份协议的第一版落地。
> 定位：`labs/` 一次性实验区，**不进任何层契约**；音频落仓外，仓内只放脚本、台账与打分表。

## 一、这批测什么（口径）

三臂**同引擎**（macOS `say` / Tingting）、**同语速档**（normal），唯一变量是**接缝与来源**：

| 臂 | 怎么产 | 接缝 |
|---|---|---|
| ① `prebaked` | 走产品快路 `sh bin/vox run`（拆句资产按序拼接 + 句间静音垫 200ms + 段级淡入淡出 5ms） | 有（每条 1–2 处） |
| ② `runtime_synth` | 运行时逐句合成，用**同一套**拼接参数（`DuplexParams` 缺省 + `runtime.audio.concat_wavs`） | 有（结构与 ① 相同） |
| ③ `one_shot` | 同一段文本一次合成 | **无**（作为「拼接代价」的参照上界） |

- ① vs ③ 回答「**拼接损失多少自然度**」；① vs ② 回答「**预铸本身**是否额外损伤自然度」。
- **本批实测：① 与 ② 逐字节相同（20/20）**——同引擎、同参数、macOS `say` 确定性，预铸资产与现合成音频零差异（唯一差别在延迟）。
  因此 ② 在本批充当**听测一致性对照**：同一段音频以两个码各出现一次，逐条 |MOS(①)−MOS(②)| 就是**评分噪声下限**
  （`report.py` 会算它）；上面任何 MOS 差若小于它，就不能当结论。换成非确定性 TTS 时 ② 才有独立含义。
- `docs/05 §5.3` 的第三产线「端到端模型输出」**不在本批**，留第二批（复用 `labs/e2e-vs-cascade` 的 harness）。
- **条目 = 20 条**（14 条真实多分句 + 6 条构造双单元序列）。凑满 20 不是凑数：仓里的 `eval.stats.MIN_REPEATS = 20`，
  每臂样本 <20 时 `bootstrap_ci` 直接抛错、按 `eval/AGENTS.md §①` **不得出报告**——10 条那版只能给描述量。
  构造项在 `raw/arms.jsonl` 里标 `constructed=true`，报告 §一 有分型副表，不与真实多分句混读。
- 条目选择（确定性）：真实多分句取 `packs/heat_kefu` 的 14 个多分句源 key 全部，另加 6 条构造双单元序列
  （同一业务步骤的两句相邻话术），按预铸总时长升序排序后取前 20 条；规则写死在 `gen_samples.py`，同包重跑必得同选择。
  音频合计约 **10.8 分钟**（60 段）。

## 二、你要做的（约 25–30 分钟，含反复对比）

```sh
sh labs/naturalness-ab/run.sh          # 生成三臂音频 + 盲化 + 出两张空表（已跑过，可跳过）
open ~/vox-naturalness/listen/         # 逐条听
```

1. **听**：`listen/` 里每个条目 3 段（如 `opening-c1.wav` / `-c2` / `-c3`），顺序已随机，**码不含臂信息**；同一条目的 3 段连着听、可反复对比。
2. **填 `sheet_clips.csv`**（每段一行，60 行）：
   - `整段自然度MOS(1-5)`：1=很不自然 … 5=很自然（整数）；
   - `接缝可察觉(无/有但不影响/明显影响)`：三档**照抄**其一，不要写别的字；
   - `备注`：可留空（例如「接缝在第 2 句开头」）。
3. **填 `sheet_items.csv`**（每条一行，20 行）：`最自然的一段(c1/c2/c3/都一样)` 四选一照抄。
4. **出报告**：`python3 labs/naturalness-ab/report.py`（表没填齐会 fail-closed 报缺哪一格，不会出半截报告）。

> 两张表是 UTF-8 带 BOM 的 CSV，Numbers / Excel 直接双击打开即可；**填完请「导出/另存为 CSV」**（Numbers 默认存成 .numbers，report.py 只认这两个 CSV 路径），或直接用文本编辑器填。
> 填完就是「真人标注」工件（`labeled_by=human:*` 口径，见 `docs/11 §11.5`）。

## 三、复现链与产物

| 文件 | 说明 |
|---|---|
| `gen_samples.py` | 三臂样本生成（音频落 `--out-root`，缺省 `~/vox-naturalness`）；台账 `raw/arms.jsonl` 逐条记 sha256 + 时长 |
| `blind.py` | 盲化（固定种子，缺省 `20260923`）→ `listen/` + `raw/blinding_map.json` + 两张空表 |
| `sheet_clips.csv` / `sheet_items.csv` | **人工填**（原始标注工件，进仓） |
| `report.py` | 完整性门禁 → `report.json` + `report.md`（MOS 中位数 + 95% bootstrap 区间，`eval.stats` 口径） |
| `raw/plans/*.json` | ① 臂实际播出的 plan（每单元一个 key），可原样重跑 |

复现命令（同包同种子 → 同音频 sha256、同盲化）：

```sh
sh bin/vox pack build packs/heat_kefu --out packs/heat_kefu_build/heat-kefu-1   # 若构建产物不在
python3 labs/naturalness-ab/gen_samples.py
python3 labs/naturalness-ab/blind.py
python3 labs/naturalness-ab/report.py        # 表填好后
```

## 四、局限（写死在报告里，别摘掉）

- **单听测者（n=1）**：无跨人一致性 → pilot 级数字，**不得对外当基准**（`docs/16` 那条「对外叙事需自然度盲测背书」要 ≥3 位听测者）。
- 引擎是 macOS `say`（零第三方依赖前提）——结论不外推到其他 TTS。
- 本批只覆盖**句间接缝**（拆句铸入的 2–3 段）；**槽位接缝**（槽值现场合成后接在固定话术之后）与端到端臂未覆盖。
- 音频不进仓（`*.wav` 已在 `.gitignore`），仓内只有脚本、台账、打分表、报告。

## 五、下一批（可选）

1. 端到端臂（qwen-omni，复用 `labs/e2e-vs-cascade`）→ 补齐 `docs/05 §5.3` 的三产线；
2. 槽位接缝条目（用 `packs/heat_kefu` 的带槽话术 + `vox run` 的 slots）；
3. 听测者扩到 3 人 → 报告升格为可引用基准（含跨人一致性）。
