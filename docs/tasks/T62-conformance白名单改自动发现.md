# T62 · conformance 套件从手工白名单改自动发现

> 状态：已执行（待验收）　｜　来源：T1 决策层「架构票」缺口 ② + 本人复核
> 分区：`adapters/`（扩展区）——**不碰任何冻结区**

## 一、为什么开这张卡

`adapters/tests/test_conformance.py` 的文件头写着「同一套断言跑所有 tts-* 适配器」「新增 tts-* 时只需在此追加」——这听起来正是「适配器不许偷偷违规」的机器保证。实际是**手工白名单，而且已经腐坏**。

复核证据：

```sh
$ sed -n '26,28p' adapters/tests/test_conformance.py
ADAPTER_CLASSES = [
    MacSayTts,
]

$ python3 -c "
from adapters.tts_macsay.adapter import MacSayTts
from adapters.tts_omlx import OmlxTts
for cls in (MacSayTts, OmlxTts):
    t=cls(); v=t.rate_value('normal')
    print(f'{t.name:12} {v!r} isint={isinstance(v,int)}')"
macsay       200 isint=True
omlx-tts     1.0 isint=False      # 会挂在第 98 行的 assertIsInstance
```

**2 个 TTS 适配器只注册了 1 个。** 没注册的那个违反了套件自己的断言（`rate_value` 返回 `float` 而套件要求 `int`），而处置方式是**把它留在白名单外**，于是套件持续绿灯、对它零覆盖。

这是本项目反复出现的那一类病：**判据没作用到自己的载体上**。判据（conformance 套件）只作用在手工填进去的那一个。

## 二、做什么

### A. 白名单改目录扫描

`ADAPTER_CLASSES` 从「手写类列表」改成「扫 `adapters/tts_*/` 自动收集」。

约束：
- 扫描必须**零第三方依赖**（本仓硬约束），用 `pathlib` + `importlib`。
- 扫不到任何适配器时**必须报错**，不许静默空跑变绿（空套件比没有套件更坏——这是 `docs/13` 的老教训）。
- 发现 `adapters/asr_*/` 目录时，若无对应套件，**打一行 stderr 提示**，不要假装覆盖了。

### B. 修 `rate_value` 断言

现在的断言（`test_known_rates_return_int`）要求返回 `int`。两个适配器一个返 `200`（wpm），一个返 `1.0`（倍率）——**这俩是不同的量纲，断言本身是错的**，不是 omlx 错了。

处理：把断言改成核「返回的是有限正数」+「`normal` 在三者中最接近 1 或在约定区间」，并在断言处写明**为什么不再核 `int`**。**不许**为了保住 `int` 而去改 `OmlxTts.rate_value` 的返回类型（那会改适配器对外行为，超出本卡范围）。

### C. CI 关卡：新增 tts-* 未进套件即红

`tools/structure_budget/check.py` 之后**新增一道**独立关卡，或并入 `tools/run_all_tests.py`。判据：目录里 `adapters/tts_*/adapter.py` 的数量 == 套件实际收集到的类数量，不等即失败。

**门禁分两类文本**（本项目的老教训）：自撰文本用 grep 判定，逐字引文不用。

## 三、文件白名单

- `adapters/tests/test_conformance.py`
- `adapters/tests/`（新增文件，如 `test_conformance_discovery.py`）
- `adapters/tts_omlx/` **只读不改**（除非你确信必须改且在报告里显式说明理由）
- `tools/run_all_tests.py`（仅在 C 需要挂载时）
- `docs/tasks/T62-conformance白名单改自动发现.md`

**禁止**：改 `core/` `runtime/` `compiler/` `assets/` `eval/` `cli/`；为了让测试变绿而删断言或加 `skipUnless`；改 `MacSayTts` 或 `OmlxTts` 的对外行为。

## 四、验收标准

- **AC1**：`ADAPTER_CLASSES` 不再是手写字面量；实跑确认**收集到 2 个**（macsay + omlx）。
- **AC2**：故意在 `adapters/` 下造一个假 tts 适配器目录（`tts_zzz/` + `adapter.py` 带三个必需成员）→ 套件**必须失败**并指名 `tts_zzz`；删掉后恢复绿。**造完必须清理干净，`git status -uall` 不得残留。**
- **AC3**：`omlx` 那一支在套件里**真的跑了**（不是被 skip、不是被排除）。给出 ran 计数证据。
- **AC4**：`rate_value` 断言已改，且注释写明**为什么不再核 `int`**。
- **AC5**：`python3 tools/run_all_tests.py` 全绿（`failed=0`），并给出 `ran=` 汇总行原文。新计数应 ≥ 旧计数 + 1。
- **AC6**：`python3 tools/structure_budget/check.py --no-write` 全绿。
- **AC7**：冻结区零改动；`git status --porcelain` 只含 §三 白名单。

## 五、回滚

`git checkout -- adapters/tests/ tools/run_all_tests.py docs/tasks/T62-conformance白名单改自动发现.md`（并删掉任何临时造的适配器目录）

## 六、给执行者的提醒

- AC2 的假适配器是**本卡最容易出事的地方**：造漏了会让 `git status -uall` 出现半成品。造完立刻删，并贴清理后的 `git status -uall` 输出。
- 目录扫描要考虑 `__pycache__` 与 `adapters/textmatch`、`adapters/mcp_vox`、`adapters/framework_kefu` 这些**不是 tts-** 的目录，别把它们误收。
- `OmlxTts` 的导入路径是 `adapters.tts_omlx`（包级导出），`MacSayTts` 是 `adapters.tts_macsay.adapter`——**两条路径不一样**，自动发现要处理。

## 七、执行记录（2026-09-28，执行者只记事实，达标判定归验收方）

产物：

- `adapters/tests/test_conformance.py` —— `ADAPTER_CLASSES` 改为 `discover_adapters()` 的返回值；
  `rate_value` 断言按 §二 B 改写；
- `adapters/tests/test_conformance_discovery.py`（新增）—— 自动发现 + §二 C 的 CI 关卡，
  标准库实现（`pathlib` / `importlib` / `sys` / `unittest`），无第三方依赖。

与卡上提醒不符的两处（执行中实测，供验收参考）：

1. **§六第三条描述的导入差异，实际比写的更小**。`OmlxTts` **定义**在
   `adapters/tts_omlx/adapter.py`（第 40 行），包级 `__init__.py` 只是 `from .adapter import`
   再导出。所以 `<pkg>.adapter` 一条导入路径对两个适配器都成立。
   发现逻辑因此**不按路径分叉**，改按「具备 `REQUIRED_MEMBERS = ("name","rate_map","rate_value")`
   三者的类」识别，两种布局都覆盖；`<pkg>.adapter` 导入不到时才回落到包级导入。
2. **两个适配器的 `TtsError` 不是同一个类**（`tts_macsay/adapter.py` 与
   `tts_omlx/transport.py` 各自定义，`core/` 无统一 TTS 异常）。原套件只 import macsay 那份，
   omlx 抛自己的 `TtsError` 时 `assertRaises` 永远匹配不上。
   执行方式是**每个适配器用它自己的异常类**（`adapter_error_type`），判据未放宽——
   仍要求是 `RuntimeError` 子类，并新增 `test_error_type_is_runtime_error_subclass` 固化这条
   fail-closed 契约。**未**为通过测试去改 `OmlxTts` / `MacSayTts` 的对外行为。

实测输出（判定归验收方）：

```
conformance 单跑：  Ran 16 tests ... OK            （基线 12 条）
发现结果：         count=2  MacSayTts / OmlxTts（含 omlx-tts=True）
rate 量纲实跑：    macsay {slow:150, normal:200, fast:300}   旧 assertIsInstance(int) 全过=True
                  omlx   {slow:0.85, normal:1.0, fast:1.25}  旧 assertIsInstance(int) 全过=False
AC2 假 tts_zzz：   conformance exit=1, FAILED (errors=6)，输出中 tts_zzz 出现 12 次；
                  错误为 AttributeError('synthesize') ×2 与「找不到 RuntimeError 子类的 TtsError」×4；
                  删掉后 tts_zzz 目录无残留
空扫描：           RuntimeError「拒绝按 0 个适配器跑一致性测试」
门禁负例：         AssertionError「TTS 适配器覆盖不全：目录里有 2 个（tts_macsay、tts_omlx），
                  套件实际收集到 1 个（tts_macsay）——未进入套件：tts_omlx」
全量：             ran=1626 skipped=0 executed=1626 failed=0 failures=0 errors=0 roots=11
```

环境依赖（影响可复现性，须知晓）：omlx 那支的
`TestSynthesizeWavFormat` 会真的打 `127.0.0.1:10099` 的 oMLX 服务并经 ffmpeg 归一，
单跑 conformance 约 8s。**服务不在时该两个用例会红**（未加 `skipUnless`，本卡禁止）。
既有 `adapters/tts_omlx/tests/test_adapter.py` 已有 `self._service_up()` 的同类保护，
本卡未沿用，理由见上——AC3 要求 omlx 那支「不是被 skip」。

工作树并非干净：`adapters/tts_omlx/{adapter.py,transport.py,tests/test_adapter.py}`、
`tools/check_no_machine_paths.py`、`docs/22-双分辨率语面.md`、`tools/check_import_direction.py`、
`tools/import_direction_baseline.txt` 的改动**不属于本卡**（来自并行的 T61/T63），
本执行**未触碰、未回滚**它们；因此 `git status --porcelain` 会包含白名单外的条目，
AC7 需按「本卡是否引入白名单外改动」而非「工作树是否全干净」判读。
