# T82 · cli：测试夹具去 macOS `say` 耦合（平台无关替身引擎）

## 卡状态
- [x] 已写卡 → [x] 已派发 → [x] 已回收 → [x] **验收通过（附证据）** / 退回（附原因）

### 验收记录（2026-10-11，验收方独立实跑）

| # | 验收项 | 期望 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | macOS `cli` 根 | `ran=89 failed=0` | `ran=89 skipped=0 executed=89 failed=0 roots=1`，`FAILED roots: (none)`（1.49s） | ✅ |
| 2 | **Linux 容器全量（主验收）** | `ran=1679 skipped=44 executed=1635 failed=0 roots=11` | **逐字一致**（`docker run --rm -v ~/vox-linux-test:/repo -w /repo vox-piper:local`） | ✅ |
| 3 | macOS 全量无新红 | 两卡同批 → `failed=0` | `ran=1679 skipped=4 executed=1675 failed=0 roots=11`（**首次全绿**） | ✅ |
| 4 | 负例 1（反静默降级） | 6 条全过 | `Ran 6 tests OK`（未知模块/未知类/坏格式 rc 2 不回落；抛错适配器 rc 4 含异常类型名） | ✅ |
| 5 | 负例 2（反空转） | 改坏类名 → 整片红 | `failed=22`（**4 failures + 18 errors**，与卡记的 Linux 修复前症状同数同型）→ 改回复跑 89 全绿 | ✅ |
| 6 | 三道门禁零回归 | 全 rc 0 | 结构预算 rc=0 / import 方向 rc=0（scanned 191→**192**，因新增 `fixture_tts.py`）/ 绝对路径 rc=0（`violation_count=0`） | ✅ |

**冻结区合规**：`cli/commands/**` 与 `cli/main.py` **零改动**（`git status` 只有 `cli/tests/**` 四改一增）；
`DEFAULT_ADAPTER_SPEC` 仍为 `adapters.tts_macsay:MacSayTts`；命令名/退出码/`--json` 字段集未动。
**零新增 skip**：89 → 89 条，用例数未下降（对比修复前 89 条中 22 条红，现全绿）。

**验收方一处方法论留档**（非卡缺陷）：批量跑门禁时用了 `python3 tools/$g`（zsh **默认不做词分割**），
四个脚本被当成单个参数传入而集体报 rc=2；改用 `${=g}` 后四道全 rc=0。
与 `docs/13 §五#13` 是同一类测量错误——**量的是谁的退出码必须先确认**。

## 背景（只写必需）

`docs/13 §五#27`：cli 测试夹具走产品缺省适配器 `adapters.tts_macsay:MacSayTts` 预铸，
而 `say` 只在 macOS 存在 → **Linux 容器上 `cli` 根 22 条红**（18 个 setUpClass error + 4 个 fail），
是「11 根全绿 / Linux 贡献者可验证（T68）」的最后一块拼图。

**本卡只改测试夹具**（`cli/tests/**`），产品缺省适配器保持 `tts_macsay` 不动（T52 定案）。
做法：新增一个**平台无关替身**（子类化仓库既有的 `eval.offline_tts.OfflineTts`，零参可构造），
夹具预铸缺省走它；替身的 `voice/model_version` 动态取自 `TINY_PACK_JSON`，
运行时引擎一致性检查照常生效——**替身不绕过任何产品判定，只替换「谁来合成」**。

**冻结区说明**：`cli/` 是冻结区（命令名与退出码一字不改；内部实现可换）。
本卡只碰 `cli/tests/**`（夹具与注释），不动任何命令面（命令名 / 退出码 / `--json` 字段集）。
用户已开批（本链路修复请求即批件，2026-10-11）。

## 目标（可验收的产物）

1. `cli/tests/fixture_tts.py`（新增）——`FixtureTts`：零参可构造、平台无关、产物确定性（同输入同字节）。
2. `cli/tests/__init__.py`——新增 `FIXTURE_ADAPTER_SPEC` 常量；`build_tiny_pack` 缺省适配器改走它；模块 docstring 两处同步。
3. `cli/tests/test_run.py`——**唯一需要现场合成**的用例（降级合成 `--allow-fallback`）显式传 `--adapter FIXTURE_ADAPTER_SPEC`；import 同步。
4. `cli/tests/test_bench.py` / `cli/tests/test_verify.py`——各 1 行注释同步（"真机 `say`" → "平台无关替身"）。
5. `labs/piper-linux/README.md`——§一 表「全量测试（对照）」行更新为 T82 后复跑结果。

## 允许修改的文件（白名单）

```
允许新增：cli/tests/fixture_tts.py
允许修改：cli/tests/__init__.py
          cli/tests/test_run.py
          cli/tests/test_bench.py
          cli/tests/test_verify.py
          labs/piper-linux/README.md（仅 §一 表「全量测试（对照）」一行）
禁止触碰：其他一切文件（尤其 cli/commands/**、cli/main.py、runtime/**、compiler/**、adapters/**、eval/**、docs/**、packs/**）
```

## 具体改动（逐字照抄，不要自由发挥）

### 改动 1：新增 `cli/tests/fixture_tts.py`

```python
"""
cli.tests.fixture_tts — cli 测试夹具专用的平台无关 TTS 替身（非产品件）

职责：给 cli 测试的极小包夹具提供**不依赖 macOS `say`、不依赖任何外部命令**的
      合成引擎，使 cli 根测试在 Linux/CI 上也能跑通（docs/13 §五#27 的解耦落点）。
不负责：不是产品代码；不进入任何产品路径；不代表任何真实引擎；不做判定。

身份口径：voice/model_version 动态取自 `cli.tests.TINY_PACK_JSON` 的声明值——
      运行时引擎一致性检查（runtime/executor.py）按这两个字符串逐字比对，
      与包源声明不一致会被判 engine_mismatch（这是被测行为，不是要绕过的）。
实现：子类化 `eval.offline_tts.OfflineTts`（仓库既有的确定性离线替身），
      零参可构造（`cli.commands.resolve_adapter` 以 `cls()` 实例化）。
"""

from cli.tests import TINY_PACK_JSON
from eval.offline_tts import OfflineTts


class FixtureTts(OfflineTts):
    """零参可构造的夹具引擎：身份与 TINY_PACK_JSON 声明一致，产物平台无关。"""

    def __init__(self) -> None:
        super().__init__(
            voice=TINY_PACK_JSON["voice"],
            model_version=TINY_PACK_JSON["model_version"],
        )
```

### 改动 2：`cli/tests/__init__.py`（三处）

(a) 模块 docstring：把

```
  - 极小包夹具只有 2 条资产（2 phrase × 1 variant × 1 rate），一条 `say` 调用一条——
    `packs/repair` 是 54 条，单测里跑它太慢；夹具只钉契约，不钉体量。
    端到端体量回归由 `packs/repair` 的手跑验收覆盖。
```

替换为

```
  - 极小包夹具只有 2 条资产（2 phrase × 1 variant × 1 rate），一条资产一次替身合成调用——
    `packs/repair` 是 54 条，单测里跑它太慢；夹具只钉契约，不钉体量。
    端到端体量回归由 `packs/repair` 的手跑验收覆盖。
  - 预铸缺省走**平台无关替身**（FIXTURE_ADAPTER_SPEC，见 fixture_tts.py）：macOS `say`
    只在 macOS 存在，夹具不得按平台分叉（docs/13 §五#27）；真机引擎的可用性由其自身
    测试与 CI 预铸步骤覆盖，不在这里。
```

(b) 在 `PACKS_REPAIR: Path = REPO_ROOT / "packs" / "repair"` 之后插入：

```python

# 夹具预铸的缺省适配器：平台无关替身（定义见 cli/tests/fixture_tts.py）。
# WHY 不用产品缺省（adapters.tts_macsay:MacSayTts）：`say` 只在 macOS 存在，
# Linux/CI 上整片红（docs/13 §五#27）；替身身份与 TINY_PACK_JSON 声明一致，
# 运行时引擎一致性检查照常生效（替身不是绕过检查，只是换个能合成的引擎）。
FIXTURE_ADAPTER_SPEC: str = "cli.tests.fixture_tts:FixtureTts"
```

(c) `build_tiny_pack` 签名与 docstring：把

```python
def build_tiny_pack(
    src: Path,
    out: Path,
    *,
    adapter: Optional[str] = None,
    allow_partial: bool = False,
    as_json: bool = True,
) -> Tuple[int, str, str]:
    """用产品 CLI 预铸极小包，返回 (退出码, stdout, stderr)。"""
```

替换为

```python
def build_tiny_pack(
    src: Path,
    out: Path,
    *,
    adapter: Optional[str] = FIXTURE_ADAPTER_SPEC,
    allow_partial: bool = False,
    as_json: bool = True,
) -> Tuple[int, str, str]:
    """用产品 CLI 预铸极小包，返回 (退出码, stdout, stderr)。

    adapter 缺省 = FIXTURE_ADAPTER_SPEC（平台无关替身，Linux/CI 可跑）；
    显式传 "" 表示不带 --adapter（走产品缺省适配器，仅 macOS 可合成）；
    负例用例传具体坏 spec。
    """
```

### 改动 3：`cli/tests/test_run.py`（两处）

(a) import 块顶部加一行（位于 `CliTestBase,` 之前）：

```python
from cli.tests import (
    FIXTURE_ADAPTER_SPEC,
    CliTestBase,
    assert_json_stdout,
    assert_not_json,
    build_tiny_pack,
    make_tiny_pack,
    run_cli,
    write_json,
)
```

(b) `test_run_say_live_with_fallback_returns_0`：把

```python
    def test_run_say_live_with_fallback_returns_0(self):
        """开 --allow-fallback：rc 0，miss_count=1，现场合成 1 次。"""
        plan = self.write_plan(PLAN_LIVE)
        out = self.tmp / "out" / "fallback.wav"

        rc, stdout, _ = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir), "--out", str(out),
             "--allow-fallback", "--json"]
        )
```

替换为

```python
    def test_run_say_live_with_fallback_returns_0(self):
        """开 --allow-fallback：rc 0，miss_count=1，现场合成 1 次（替身引擎，平台无关）。"""
        plan = self.write_plan(PLAN_LIVE)
        out = self.tmp / "out" / "fallback.wav"

        rc, stdout, _ = run_cli(
            ["run", str(plan), "--pack", str(self.pack_dir), "--out", str(out),
             "--allow-fallback", "--adapter", FIXTURE_ADAPTER_SPEC, "--json"]
        )
```

### 改动 4：两处注释

- `cli/tests/test_bench.py`（`BenchCliBase` docstring 第二行）：
  原 `    包与语料按类建一次（每条用例都会预铸一次真机 \`say\`，逐条重建太慢）；`
  新 `    包与语料按类建一次（预铸走平台无关替身，逐条重建太慢）；`
- `cli/tests/test_verify.py`（`VerifyBase` docstring）：
  原 `    """共享夹具：极小包源 + 它的已铸产物（预铸一次真机 say，类级复用）。"""`
  新 `    """共享夹具：极小包源 + 它的已铸产物（预铸一次夹具替身，类级复用）。"""`

### 改动 5：`labs/piper-linux/README.md`（仅一行）

原（§一 表「全量测试（对照）」行）：

```
| 全量测试（对照） | `python3 tools/run_all_tests.py` | 11 根里 10 根过；`cli` 根 22 条失败**全部**是夹具依赖 macOS `say`（`docs/13 §五#27` 的已知耦合，非本实验引入） |
```

新（复跑数按执行实测填；应为 11 根全过）：

```
| 全量测试（对照） | `python3 tools/run_all_tests.py` | **11 根全过**（T82 后复跑，`failed=0`；此前 `cli` 根 22 条红系夹具依赖 macOS `say` 的耦合 `docs/13 §五#27`，已解耦） |
```

## 禁止事项

- 不得新增 / 修改任何 `skip` / `skipUnless` / `skipIf`——**不许用跳过冒充通过**，修复前 22 条红必须真跑真绿；
- 不得放宽、删除、弱化任何既有断言；cli 根用例数不得下降；
- 不得动产品代码：`cli/commands/**`、`cli/main.py` 一行不改；`DEFAULT_ADAPTER_SPEC` 保持 `adapters.tts_macsay:MacSayTts`；
- 不得改 `TINY_PACK_JSON` 的 `voice`/`model_version`（替身身份与其一致，是引擎一致性检查通过的前提）；
- 不得新增第三方依赖（替身仅用标准库 + 仓库内 `eval.offline_tts`）；
- 不得提交（提交由验收方做）；
- 不得"顺手优化"白名单外的任何文件。

## 验收标准（逐条可判定，我会逐条核对）

1. **macOS（本机）cli 根全绿**（先 cd 到本仓根）：

   ```sh
   python3 tools/run_all_tests.py --root cli
   ```

   期望末两行：

   ```
   ran=89 skipped=0 executed=89 failed=0 failures=0 errors=0 roots=1
   FAILED roots: (none)
   ```

2. **Linux 容器全量全绿**（本卡主验收；须来自无 `say` 环境）：

   ```sh
   # 先同步副本（保留 .git：tools 根 provenance 断言需要），再从容器副本跑
   rsync -a --delete --exclude '__pycache__' "<本仓根>/" "$HOME/vox-linux-test/"
   docker run --rm -v "$HOME/vox-linux-test":/repo -w /repo vox-piper:local python3 tools/run_all_tests.py
   ```

   （`docker run` 按本机 colima / Docker Desktop 实际环境写；镜像与模型准备见 `labs/piper-linux/README.md §二/§三`。）

   期望末两行：

   ```
   ran=1679 skipped=44 executed=1635 failed=0 failures=0 errors=0 roots=11
   FAILED roots: (none)
   ```

   （与修复前对照：同一命令下 `failed=22 roots=cli`——22 条红全部来自 cli 根。）

3. **macOS 全量无新红**：

   ```sh
   VOX_MAX_TAIL_SILENCE_MS=250 VOX_MAX_HEAD_SILENCE_MS=100 python3 tools/run_all_tests.py
   ```

   期望：两卡（T82+T83）同批完成时 `failed=0`；若 T83 未完成，唯一允许的红是 `packs` 的同源指纹 1 条。

4. **负例 1（反静默降级，两平台）**：

   ```sh
   python3 -m unittest cli.tests.test_pack_build.TestPackBuildAdapterResolution cli.tests.test_pack_build.TestPackBuildFailingAdapter -v
   ```

   期望：全过——未知模块 / 未知类 / 坏格式一律 rc 2 不回落；会抛错的适配器 rc 4 且 failed 明细含异常类型名。

5. **负例 2（反空转，必做一次）**：临时把 `FIXTURE_ADAPTER_SPEC` 的值改成
   `"cli.tests.fixture_tts:NoSuchClass"`，跑 `python3 tools/run_all_tests.py --root cli`：
   必须出现整片红（类 setUpClass 失败），**随即改回**并复跑标准 1 全绿——
   这一步证明夹具真的经由产品 `--adapter` 路径被使用（不是空跑）。

6. **三道门禁零回归**（依次跑，均要求 rc 0 / passed=true）：

   ```sh
   python3 tools/structure_budget/check.py --no-write
   python3 tools/check_import_direction.py
   python3 tools/check_no_machine_paths.py --json
   ```

## 反空转条款（T01 教训）

- 测试必须调用产品 API（`run_cli` → `cli.main.main`）；本卡夹具是**注入到产品 CLI 的引擎替身**，
  不是绕过 CLI 的旁路——预铸仍经 `pack build` 全流水线（质检门照跑）；
- 修复前的 22 条红（18 error + 4 fail）必须逐条转绿，不许转成 skip；
- 负例 1 / 负例 2 必须真跑（见验收 4 / 5）。

## 回滚方式

```sh
git checkout -- cli/tests/__init__.py cli/tests/test_run.py cli/tests/test_bench.py cli/tests/test_verify.py labs/piper-linux/README.md
rm cli/tests/fixture_tts.py
```

## 执行方式

**首选**（2026-09-17 起）：ZCode 子智能体 **`vox-card-executor`**（跑在「商汤」provider，
`sensenova-6.8-flash-lite`，自带 Write/Edit 直接落地）。定义：`~/.zcode/agents/vox-card-executor.md`；
**新建/改动 agent 定义后需新开会话才注册**。

**回落**（`vox-card-executor` 不可用时）：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T82-cli测试夹具去say耦合.md)" --dir （仓库根）
```

## 卡状态
- [x] 已派发 → [x] 已回收 → [x] 验收通过（附证据）/ 退回（附原因）
