# T10 · CLI 骨架（`vox pack check/build` + `vox run`）＋ 退出码约定 ＋ 两笔欠账

## 数据分级（派发前置检查项）

**分级：公开。** 本卡只有命令行契约、退出码与字段名，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发。

## 背景

四个命令名在根 `AGENTS.md §四` 里**早已冻结**（`vox pack build` / `vox pack check` / `vox run` / `vox bench` / `vox verify`），但**一行都没实现**——现在每跑一次链路都要手敲 `python3 -c "..."`。

七层已全部验收（572 条测试），链路已经通：`compiler` 能出包、`runtime` 能播包、`eval` 能出报告、`packs/repair/` 是一个真实业务包。本卡把**最早断掉的那一段**（人→命令→各层 API）接上，并顺手收掉两笔已登记的欠账。

**本卡只做 T10 范围**（`pack check` / `pack build` / `run` + 退出码 + 两笔欠账）；`vox bench` 与 `vox verify` 留 T10b（目录结构要预留，但不实现）。

**必读**（不得修改）：`docs/08-CLI口径.md`（**本卡规格，逐条照做**）、根 `AGENTS.md §四`（命令名）、`packs/AGENTS.md`（包的形态与已登记的口子）、`docs/06 §6.1.2`（源格式）、`docs/07 §7.2/§7.3`（剧本源格式与判据）、`compiler/AGENTS.md`、`runtime/AGENTS.md`、`eval/AGENTS.md`。

依赖（只读其**公开** API，不得改）：`core.protocol`、`compiler`（`load_source`/`load_script`/`check_properties`/`prebake` 及其异常）、`assets`（`load_pack`/`validate_pack`/`AssetPackError`）、`runtime`（`Executor`/`DuplexParams`/`RuntimeMissError`/`DuplexError`）、`adapters.tts_macsay`（**只能动态解析，不得静态 import**）。

## 目标（产物）

```
cli/__init__.py                       导出 main
cli/__main__.py                       python3 -m cli 入口
cli/errors.py                         退出码常量（0/2/3/4/5）+ CLIError 体系
cli/main.py                           argparse 分派（预留 bench/verify 子命令位，未实现时明确报"未实现"→ 退出码 2）
cli/commands/__init__.py
cli/commands/pack_check.py            docs/08 §8.3 第 1 条
cli/commands/pack_build.py            docs/08 §8.3 第 2 条
cli/commands/run.py                   docs/08 §8.3 第 3 条
cli/tests/__init__.py
cli/tests/test_pack_check.py
cli/tests/test_pack_build.py
cli/tests/test_run.py
cli/tests/test_exit_codes.py          退出码矩阵（2 / 3 / 4 / 5 各至少一条）
bin/vox                               POSIX sh 薄壳（须 chmod +x，仓库内可直接跑，不依赖安装）

允许修改（仅为收欠账 1）：
compiler/source.py                    增加未知字段拒绝（白名单见 docs/08 §8.5）
compiler/tests/test_source.py         补未知字段负例
```

### 1. CLI 骨架（照 `docs/08`）

- 退出码与错误分类**逐条照 `docs/08 §8.2`**（0/2/3/4/5 的语义与"`3` 必须打印异常类型名"）；
- 三个命令的行为、参数、`--json` 输出字段**逐条照 `docs/08 §8.3`**（字段名一字不改）；
- `cli/commands/*.py` 里**只允许**：解析参数 → 调各层公开 API → 打印 → 返回退出码。**一行判定逻辑都不许写**；
- 适配器一律**动态解析**（`importlib`，格式 `<模块>:<类名>`，缺省 `adapters.tts_macsay:MacSayTts`；失败 → 退出码 2，**不得回落**）；
- `--json`：stdout **纯 JSON**（我这边的验收会 `json.loads(stdout)`），人读摘要走 stderr；不带 `--json` 反之。

### 2. 欠账 1：源格式未知字段拒绝（`compiler/source.py`）

- 白名单：
  - `pack.json` 允许 `{pack_id, pack_version, protocol_version, ruleset_version, voice, model_version, rates, locale, duplex}`；
  - `phrases[]` 每项允许 `{key, variants, rates}`；
- 未知字段 → `SourceError`，**消息列出未知字段名与位置**（例：`pack.json 未知字段: ['duplx']` / `phrases[0] 未知字段: ['varient']`）；
- **只增不减**：`packs/repair/`（用了 `locale`+`duplex`）与既有测试夹具必须照常通过。

### 3. 欠账 2：`--out` 越界拦截

- `pack build --out` 落在 `pack_dir` 内 → 退出码 2；`run --out` 落在 `--pack` 包目录内 → 退出码 2（判据：解析后的绝对路径是不是对方的子路径，**含 `..` 逃逸**）。

## 附：验收用的两份 plan（我和你都用这一份；自测时写到 `/tmp`，**不得进仓**）

`plan_hit.json`（纯命中，5 个全 key 单元 —— 期望 `hit_count=5 / miss_count=0 / tts_calls=0`）：

```json
[{"key": "greeting_welcome", "rate": "normal", "variant": "auto"},
 {"key": "ask_fault_type", "rate": "normal"},
 {"key": "ask_fault_detail", "rate": "normal"},
 {"key": "ask_fault_urgency", "rate": "normal"},
 {"key": "closing_thank_you", "rate": "normal"}]
```

`plan_live.json`（含一条 `SAY_LIVE` —— **不带** `--allow-fallback` 时期望退出码 `5`；带上时期望 `0` 且 `miss_count=1`）：

```json
[{"key": "greeting_welcome", "rate": "normal", "variant": "auto"},
 {"key": "ask_fault_type", "rate": "normal"},
 {"action": "SAY_LIVE", "text": "不好意思，刚才没听清，请您再说一次要报修的设备。", "rate": "normal"}]
```

（这两份 plan 的 key 都来自 `packs/repair/phrases.json`，所以要对 `packs/repair` **预铸出来的包**跑，不是对源目录跑。）

## 允许修改的文件（白名单）

```
允许新增：cli/__init__.py, cli/__main__.py, cli/errors.py, cli/main.py,
          cli/commands/__init__.py, cli/commands/pack_check.py, cli/commands/pack_build.py,
          cli/commands/run.py,
          cli/tests/__init__.py, cli/tests/test_pack_check.py, cli/tests/test_pack_build.py,
          cli/tests/test_run.py, cli/tests/test_exit_codes.py,
          bin/vox
允许修改：compiler/source.py, compiler/tests/test_source.py（**仅为**未知字段拒绝）
禁止触碰：其余一切文件（含 core/**、rules/**、assets/**、adapters/**、runtime/**、eval/**、
          packs/**、compiler 的其他文件、docs/**、各层 AGENTS.md、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库；`argparse` + `json` + `importlib` + `sys`/`os`/`pathlib`）。
- **不得实现任何判定逻辑**：命中判定、四属性、质检阈值、统计口径、指标计算一律调对应层；CLI 只做"解析→调用→打印→退出码"。
- 不得 import 任何以下划线开头的私有符号（如 `compiler.checks._check_c1`、`eval.bench._make_sample`）；只能用各层 `__init__` 导出的公开名。
- 不得改 `core.protocol`、`runtime`、`assets`、`eval` 的任何文件；`compiler/source.py` 的改动**只准**是"未知字段拒绝"这一项。
- 不得把 `bench`/`verify` 真正实现（`cli/main.py` 里出现这两个子命令时，一律"未实现" → 退出码 2；目录结构可预留）。
- 不得把任何产物写进 `packs/**` 或仓库内（测试一律 `tempfile`）。
- 不得"顺手优化"、不得改与本卡无关的格式、不得改卡。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s cli -v` **全绿**（我会贴计数）；`python3 -m unittest discover -s compiler` 仍全绿且 **165 条只增不减**；
2. **`packs/repair` 端到端（我手跑，命令名照 `docs/08`）**：
   - `bin/vox pack check packs/repair --json` → 退出码 **0**、stdout 是纯 JSON（我 `json.loads`）、`passed is true`、`skipped == ["key_not_prebaked"]`、`violations == []`、`phrases == 16`、`units == 17`；
   - `bin/vox pack build packs/repair --out <临时目录> --json` → 退出码 **0**、`total == 54`、`synthesized == 54`、`clean is true`、`validate_pack == []`；
   - `bin/vox pack check <临时目录>/…（已预铸包，含 manifest.json）--json` → 退出码 **0**、`pack_validate == []`；
   - `bin/vox run <我给的 plan.json> --pack <预铸包> --json`（纯命中的 5 个单元）→ 退出码 **0**、`hit_count == 5`、`miss_count == 0`、`tts_calls == 0`、`out_path` 存在；
   - 同一条链路上含 `SAY_LIVE` 的 plan **不带** `--allow-fallback` → 退出码 **5**，stderr 含该 key/reason；带 `--allow-fallback` → 退出码 0 且 `miss_count == 1`；
3. **退出码矩阵（我手跑）**：
   - 缺子命令/缺必填参数/路径不存在/`--duplex '{"patience_ms":500}'`/未知适配器 → 一律 **2**；
   - `pack check` 喂一个含违规的剧本 → **4**（`passed is false` 且 `violations` 非空）；
   - `pack build` 用一个会抛错的适配器 → **4**（`clean is false`，打印 `failed` 明细）；
   - **3 的路径存在且能打印异常类型名**（我看 `cli/tests/test_exit_codes.py` 里那条用例，并把它单独跑一遍）；
4. **未知字段拒绝生效**（我手跑）：把 `packs/repair/pack.json` 复制到临时目录后插入 `"duplx": {}` → `compiler.load_source` 抛 `SourceError` 且消息含 `duplx`；`phrases[0]` 插 `"varient": []` → 同样报错含 `varient`；**`packs/repair/` 原样仍通过**；
5. **`--out` 越界**（我手跑）：`pack build --out packs/repair/sub` → **2**；`run --out <包目录>/x.wav` → **2**；
6. **`bin/vox` 可直接跑**：`cd /tmp && （仓库根）/bin/vox pack check （仓库根）/packs/repair` → 退出码 0（证明不依赖 cwd、不依赖安装）；`ls -l bin/vox` 可见可执行位；
7. **没有重写判定**（我 grep 核对）：`cli/**` 里不出现 `orphan_branch`、`0.987`、`percentile`、`bootstrap` 这类判定/统计字样；`grep -nE "^from .* import .*_" cli/**/*.py` 无非法的私有符号导入；
8. `git status --porcelain -uall` 仅白名单文件；`core/`、`runtime/`、`assets/`、`eval/`、`packs/` **零 diff**；
9. 含方法级中文注释与关键步骤 WHY 注释（尤其：为何 `3` 要打印异常类型名、为何 CLI 不写判定、为何适配器只能动态解析）；
10. 报告如实写明：哪些退出码路径是**真跑过**的、哪些只是"读了代码/写了用例"（不许把"写了用例"说成"实测过"）。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`cli.main.main` / 三个命令函数 / `compiler.load_source`），不得在测试里复制判定逻辑或自己解析 CLI 参数；
- `--json` 的每一条断言必须真跑 `json.loads(stdout)`，不得只断言"含某个字符串"；
- 退出码用例必须断言**具体码值**（`assertEqual(rc, 2)`），不得只断言"非零"；
- 负例必须断言**异常类型 + 消息含关键值**（字段名/路径/参数值）；
- 不得 `assertTrue(True)`、不得 `except: pass`、不得用 `skipTest` 绕过本卡任何验收项；
- 不得为了让测试变绿而放宽 `compiler/source.py` 的既有校验（只增不减）。

## 回滚方式

```
cd （仓库根）
git checkout -- compiler/source.py compiler/tests/test_source.py
git clean -fd cli bin        # 本卡新增的目录（未跟踪）
chmod -x bin/vox 2>/dev/null; rm -f bin/vox
```

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`；`thoughtLevel` 必须是 `enabled`；**改过 agent 定义后需新开会话**）。

**回落**：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T10-cli-CLI骨架.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要往仓库里写计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，回落路径 `opencode run` / `sense-nova/sensenova-6.8-flash-lite`，**43 分 15 秒单轮完成**，无限流）→ [x] 已回收 → [x] **验收通过**（10/10；`cli` 64 条、`compiler` 165→**168**；退出码矩阵 2/3/4/5 全部经 `bin/vox` 实跑）
- **一处与文档的偏差、按实现写回**：`pack check <已预铸包>` 的 `skipped=["source_and_script_unavailable"]`（已铸产物里没有源文件，源级检查无输入可跑）→ 已补进 `docs/08 §8.3`。
