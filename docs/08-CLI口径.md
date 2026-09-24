# 08 · CLI（`vox`）口径：目录、命令、退出码与错误分类

> **本文件是 T10 / T10b 卡的规格依据**，由策划补定；实现不得自创命令名、退出码或输出字段。
> 上游权威：根 `AGENTS.md §四`（四个命令名**已冻结**：`vox pack build/check`、`vox run`、`vox bench`、`vox verify`）、
> 各层 `AGENTS.md` 的公开 API、`docs/06`（预铸口径）、`docs/07`（剧本口径）、`eval/AGENTS.md`（报告口径）。
>
> **命令名冻结、行为未实现**——本轮把行为接上现成层 API，**不许在 CLI 里重写任何一层已实现的判定**。

---

## 8.1 目录与入口

- 新增顶层 `cli/` 包（**非冻结区**，薄壳）：
  ```
  cli/__init__.py        导出 main
  cli/__main__.py        python3 -m cli 的入口
  cli/main.py            参数解析 → 分派到各命令
  cli/commands/          pack_check.py / pack_build.py / run.py（T10）；bench.py / verify.py（T10b）
  cli/errors.py          退出码常量 + CLiError 体系
  cli/tests/
  bin/vox                POSIX sh 薄壳：定位仓库根 → exec python3 -m cli "$@"
  ```
- 用法：`vox <命令> [参数]`，等价于 `python3 -m cli <命令> [参数]`。`bin/vox` 必须 `chmod +x`，且**不依赖安装**（仓库内可直接跑）。
- 根 `AGENTS.md §三` 的目录清单要补 `cli/  [冻]? ` 一行 —— **`cli/` 定为「薄壳·随内核冻结」**（它只做分派与打印，不含判定逻辑；判定逻辑的改动一律发生在对应层）。

## 8.2 退出码（**冻结**，脚本化调用依赖它）

| 码 | 含义 | 什么时候用 |
|---|---|---|
| `0` | 成功 | 命令完成且结论为"通过/已完成" |
| `2` | **用法或参数错误**（配置期） | 缺子命令、缺必填参数、路径不存在、JSON 解析失败、语料/包源格式非法（`SourceError`/`ScriptError`）、未知适配器路径 |
| `3` | **运行期失败**（执行期） | 执行过程中抛出的异常：合成失败、写盘失败、内部缺陷（`TypeError`/`AttributeError`/`ValueError`…）——**必须打印异常类型名** |
| `4` | **质检不通过**（非错误） | `pack check`/`verify` 判定有违规、`prebake` 报 `clean=False`（含 `failed`/`quality_issues`） |
| `5` | **fail-closed 中止**（业务语义） | `run`：未命中且未开降级 → `RuntimeMissError` |

**纪律**：
- `3` 与 `2` 必须分得开（T08 审计 #2 的欠账）：参数/装载期的错 → `2`；执行期的错 → `3` 且 **stderr 必带 `type(exc).__name__`**（traceback 在 `--verbose` 时打印）。**不得**把内部缺陷折叠成 `2`——那会让人去查命令行参数。
- **不静默**：任何非零退出都要有 stderr 一行说明；`4`/`5` 属"预期内的业务结论"，措辞要中性（不是"错误"，是"不通过"/"已按 fail-closed 中止"）。
- `--json` 时把机器可读结果打到 **stdout**（纯 JSON，无其他输出），人读摘要打到 stderr；不带 `--json` 时摘要打 stdout。

## 8.3 命令（T10 三个）

### `vox pack check <pack_dir> [--json]`
接现成 API，**逐层往下**：
1. `compiler.load_source(<pack_dir>)` → 源格式与话术表（`SourceError` → 退出码 2）；
2. `compiler.load_script(<pack_dir>)` → 剧本源格式（`ScriptError` → 2）；
3. `compiler.check_properties(script, source, pack=None)` → 四属性（`pack=None` → `skipped` 里必有 `"key_not_prebaked"`，**报告里要如实写出 skipped，不得当成通过**）；
4. 若 `<pack_dir>` 下存在 `manifest.json`（即指向的是**已预铸包**），额外 `assets.load_pack` + `assets.validate_pack`。

- 有 `violations` 或 `validate_pack` 非空 → **退出码 4**，`--json` 输出：
  `{"command":"pack.check","pack_dir":"…","phrases":16,"units":17,"violations":[{"code","unit_index","key","message"}],"skipped":["key_not_prebaked"],"pack_validate":[],"passed":false}`
- 全过 → `0`，`passed=true`。
- **本命令负责接通「源格式未知字段拒绝」**（见 8.5）。

### `vox pack build <pack_dir> --out <dir> [--adapter <模块>:<类>] [--allow-partial] [--json]`
1. `compiler.load_source`；
2. `compiler.load_script` + `compiler.check_properties(script, source, pack=None)` —— **先检后铸**：源不合规直接退出码 4，**不得**铸出一半；
3. `compiler.prebake(source, adapter, <dir>)`；
4. `clean=False` → 退出码 4（打印 `failed`/`quality_issues` 明细）；
5. `clean=True` → 退出码 0，`--json` 输出 `{"command":"pack.build","total":54,"synthesized":54,"reused":0,"clean":true,"pack_dir":"…","validate_pack":[]}`。
- `--adapter` 缺省 `adapters.tts_macsay:MacSayTts`；解析失败 → 2（**不得回落别的适配器**）。
- `--out` 落在 `<pack_dir>` 内 → **参数错误（2）**（不许把产物写进源）。

### `vox run <plan.json> --pack <built_pack_dir> [--adapter …] [--out <wav>] [--allow-fallback] [--duplex <json>] [--json]`
1. `assets.load_pack(<built_pack_dir>)`（`AssetPackError` → 2）；
2. 读 plan JSON（`json.load` 失败 → 2；`core.ProtocolError` → 2）；
3. `runtime.DuplexParams(**duplex)`（`--duplex` 给的 JSON 对象；非法值 `DuplexError` → 2）；
4. `runtime.Executor(pack, adapter, duplex=…, allow_fallback=…)` + `execute(plan, plan_id, turn_id, out_path)`；
5. `RuntimeMissError` → **退出码 5**，stderr 打印原始消息（含 key 与原因）；
6. 成功 → `0`，`--json` 输出 `{"command":"run","hit_count":5,"miss_count":1,"fallback_count":0,"tts_calls":1,"first_audio_ms":…,"total_duration_ms":…,"out_path":"…","events":[…]}`（事件字段名照 `core.metrics_spec` 常量，**不得改名**）。

## 8.4 CLI 不许做的事（层边界）

- **不许实现任何判定**：命中判定、四属性、质检阈值、统计口径一律调对应层；CLI 只做"参数解析 → 调 API → 打印 → 定退出码"。
- 不许 import `cli` 之外的私有符号（`_` 开头）——只能用各层 `__init__` 导出的公开名。
- 不许新增第三方依赖（只用标准库 `argparse`/`json`/`importlib`/`sys`/`os`/`pathlib`）。
- 不许往 `packs/` 源目录里写任何东西（产物只能进 `--out`）。
- 不许把 `bench`/`verify` 塞进本卡实现（T10b 做），但**目录结构要预留**。

## 8.5 顺带要收的两笔欠账（都属本卡）

1. **源格式未知字段拒绝**（`packs/AGENTS.md` 已登记；实测 `load_source` 现在静默忽略 `typo_field`）：
   - `compiler/source.py` 增加字段白名单：`pack.json` 允许 `{pack_id, pack_version, protocol_version, ruleset_version, voice, model_version, rates, locale, duplex}`（后两个是**业务侧字段**，只校验"存在性允许"，其内部形状由 `runtime.DuplexParams` 负责）；`phrases[]` 每项允许 `{key, variants, rates}`；
   - 出现未知字段 → `SourceError`，**消息列出未知字段名与位置**（如 `pack.json 未知字段: ['duplx']`）；
   - 这是**校验强度只增不减**的改动：现有合法包（含 `packs/repair/`）必须照常通过——`packs/repair/pack.json` 正好用了 `locale`+`duplex`，是本改动的活体回归用例；
   - 口径补进 `docs/06 §6.1.2`（带日期）。
2. **`--out` 越界拦截**：`vox pack build --out` 与 `vox run --out` 落在包/源目录内 → 退出码 2（不许污染冻结区）。`vox bench --out` 的同名防护留给 T10b（它归 eval 的 `run_bench`/CLI 一起做）。

## 8.6 T10b：`vox bench` 与 `vox verify`（本节的字段名冻结）

### `vox bench <pack_dir> --corpus <corpus.json> --out <dir> [--adapter …] [--repeats N] [--seed N] [--required-hit-rate F] [--warmup N] [--json]`

- **必须复用 `eval.run_bench`**（`eval.bench.BenchConfig` + `run_bench` + `eval.report.write_report`）——**不得重写对拍逻辑**（统计口径属 `eval/` 契约，CLI 只传参）。
- `<pack_dir>` 是**已预铸包**（含 `manifest.json`）→ `assets.load_pack`；语料用 `eval.bench.load_corpus`。
- `--out` 落在 `<pack_dir>` 内 → **参数错误（2）**。
- 退出码：报告 `incomplete is False` → `0`；`incomplete is True` → **4**（"数据不完整"是结论不是崩溃，**报告照样写出**）；参数/装载错 → 2；执行期异常 → 3。
- `--json` 输出：`{"command":"bench","report_path":"…","incomplete":false,"repeats":20,"seed":…,"arms":{"fast":{…},"slow":{…}},"timing_metrics_meaningful":true}`（`arms` 直接透传 `eval` 报告里的同名块，**不得改名或重算**）。

### `vox verify <artifact> [--json]`（按产物类型分派，**不猜**）

| 产物形态（判据按顺序） | 走哪条 | 不过时 |
|---|---|---|
| 目录且有 `manifest.json` | `assets.load_pack` + `assets.validate_pack` | 退出码 4，`issues` 为 `validate_pack` 的返回 |
| 目录且有 `pack.json` 与 `phrases.json` | `compiler.load_source` + `load_script` + `check_properties(script, source, pack=None)` | 4，`violations` 为四属性返回 |
| 文件且 JSON 顶层 `schema_version == "vox-eval-report/1"` | `eval.report` 的完整性字段核对 + **raw 指纹核对**（见 §8.7） | 4，`issues` 列出缺失/不符项 |
| 其它 | —— | **2**，stderr 说明支持的三类产物形态 |

- 三类都过 → `0`；`--json` 输出 `{"command":"verify","kind":"asset_pack|pack_source|eval_report","passed":true,"issues":[],"violations":[]}`（未涉及的键给空列表）。
- **不许**对产物做任何修复或写入（只读）。

## 8.7 T10b 顺带要收的两笔欠账

1. **raw 值级篡改可见**（T08b 明确留给 T10 的那条）：
   - `eval/bench.py` 落盘 raw 时，把每份 raw 的 **`sha256` 与 `expected_lines`** 写进报告 `raw` 块（**只追加字段**，不改名不缺项）；
   - `eval/report.py` 的 `check_raw_on_disk` 增加**指纹核对**：报告里带指纹 → 逐文件比对 sha256 与行数（值级篡改因此可见）；报告里**不带**指纹（老报告）→ 退回原有的行数/index 核对（向后兼容，**不得**因此报错）；
   - `vox verify` 对 eval 报告走这条核对。
2. **eval 的 `--out` 越界防护**（T08 审计 #7）：`eval/bench.py` 建工作目录前检查 `out_dir` 是否落在 `pack.root` 内（含 `..` 逃逸）→ 命中即 `BenchReportError`（消息含两个路径）。

## 8.8 本卡不做

历史基线重跑、盲测/golden set（`eval/` 的后续卡）、`vox` 的 shell 补全与帮助美化。
