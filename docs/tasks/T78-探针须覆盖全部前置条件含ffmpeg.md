# T78 · adapters：`probe_availability` 必须覆盖**全部**前置条件（当前漏了 ffmpeg）

## 卡状态
- [x] 已写卡 → [x] 已派发 → [x] 已回收 → [x] 验收通过（附证据）/ 退回（附原因）

## 背景（只写必需）

推送前必跑的第 ④ 条「CI 代理跑一遍」，在**忠实还原 CI 条件**后仍撞出一盏红灯
（这是 T76、T77 之后的**第三盏**，属同一族「本机绿≠CI 绿」）。

CI 条件（三条同时成立，缺一不可）：

1. `macos-latest` runner，`actions/setup-python` 给 **3.12 / 3.13 / 3.14** 三档
   ——**不是**系统自带解释器。
2. **PATH 里没有 ffmpeg**（workflow 没有任何安装 ffmpeg 的步骤），但 `say` 在
   （`/usr/bin/say` 属系统自带）。
3. **没有本机 oMLX 服务**在跑。

在这三条下跑全量：**`ran=1645 skipped=28 executed=1617 failed=2 failures=0 errors=2`**，
两个 error 全在 `adapters` 根的 `TestSynthesizeWavFormat`，消息是：

```
adapters.tts_omlx.transport.TtsError: ffmpeg 不可用: 'ffmpeg'（可在 PATH 提供或以 env
VOX_FFMPEG 覆盖）——服务出 24 kHz，缺 ffmpeg 无法降到契约 16 kHz
```

3.12 与 3.14 实测**完全一致**（3.13 夹在两者之间）。

**为什么本机看不出来**：本机 PATH 里有 ffmpeg，且本机 oMLX 常在跑。
T77 加的 `probe_availability` 只探了**端点**，没探 **ffmpeg**——
于是「端点不可达」这一档被正确 skip 了，「端点可达但缺 ffmpeg」这一档照样 error。

**这就是 T77 那个缺陷的下一层**：T77 把「类整体跳过」修成「逐适配器跳过」，
但探针本身只回答了「服务在不在跑」。**只覆盖一半前置条件的探针比没有探针更坏**——
它报「可用」，让套件照跑，然后炸在另一个前置条件上。

## 目标（可验收的产物）

`OmlxTts.probe_availability()` 回答的是**「这个适配器此刻能不能真合成」**，
不是「端点通不通」。缺任一确定性前置条件都必须报不可用，并把**缺的是什么**说清楚。

## 允许修改的文件（白名单）

- `adapters/tts_omlx/adapter.py`
- `adapters/tests/test_conformance_probe.py`

**其余一律不许动**（含 `test_conformance.py`、含任何文档、含任何 `packs/`）。

## 设计口径（照做，不要另发明）

### 1. 前置条件共两条，与 `synthesize` 的检查顺序一致

- **前置一：ffmpeg**。`synthesize` 在**发任何请求之前**就要求它
  （缺 ffmpeg 属确定性失败，连一次服务端请求都不该发）。
  所以探针也**先查 ffmpeg**。
- **前置二：端点可达**。现有逻辑不变。

### 2. 消息必须是**单一真源**

`synthesize` 里那句 ffmpeg 缺失消息与探针要共用同一份文案，
否则两处各写一遍，改一处忘一处就又是一次「本机绿≠CI 绿」。
做法：抽成模块级常量（如 `FFMPEG_MISSING`，带 `{name}` 占位），
`synthesize` 与 `probe_availability` 都从它格式化。**不要新造第二份字符串。**

### 3. 两条都不满足时，**两个原因都要报**

`probe_availability` 返回 `(False, reason)`；`reason` 应把**所有**未满足的前置条件
都列出来（用可读的分隔），不是只报第一个——
只报第一个会让人补完 ffmpeg 再跑一遍才发现端点也不通，来回两趟。

### 4. ffmpeg 名的回落链要与 `__init__` 一致

`__init__` 里是 `ffmpeg 参数 → env VOX_FFMPEG → 常量 DEFAULT_FFMPEG`。
探针是**类方法、拿不到实例**，所以只能走后两档：`env VOX_FFMPEG → DEFAULT_FFMPEG`。
沿用文件里既有的WHY 口径（探针探的必须与「无参构造会用的」是同一个），
在注释里点明这一点。**刻意不用 `or` 判空回落那类写法时要说明理由**——
本文件 `__init__` 里对 `VOX_TTS_ENDPOINT` 已经有这条纪律（设了但为空要响亮失败）。

### 5. 绝不改动 `synthesize` 的失败语义

`synthesize` 在缺 ffmpeg 时**必须照旧抛 `TtsError`**——
那是 fail-closed 纪律（D4），不是可以「优化」掉的错误。
本卡只让**套件在调用前就知道**这件事，**不改变调用后的任何行为**。

## 禁止事项（这几条是本卡最容易做错的地方）

- **禁止在 `synthesize` 周围加 `try/except`**，也禁止把缺 ffmpeg 降级成别的行为。
- **禁止把 ffmpeg 缺失改成 warning 或回落**（例如「没有 ffmpeg 就原样输出 24 kHz」）——
  那会破坏 16 kHz 契约，是本仓最不能碰的那条线。
- **禁止改 `skip_unless_available`**（判定粒度与位置是 T77 已定的）。
- **禁止放宽既有断言**。
- **禁止在本卡里改任何文档**（`docs/23` 那处已由 T77 写好，本卡不重复改）。
- 禁止提交。

## 验收标准（逐条可判定，我会逐条核对）

| # | 标准 | 判定命令 |
|---|---|---|
| 1 | 缺 ffmpeg + 端点可达 → `probe_availability()` 返回 `(False, …)`，reason 里出现 `ffmpeg` | 新增用例 |
| 2 | ffmpeg 与端点**都**缺 → reason 里**两条原因都在** | 新增用例 |
| 3 | ffmpeg 可解析 + 端点可达 → `(True, "")` | 既有两条用例需适配 |
| 4 | `synthesize` 的 ffmpeg 缺失消息与探针**逐字同源**（同一常量格式化而来） | 读代码 + 新增用例 |
| 5 | `VOX_FFMPEG` 能让探针改探别的 ffmpeg 名（回落链第二档生效） | 新增用例 |
| 6 | 上述用例在**有 ffmpeg 与无 ffmpeg两种环境下都成立**（不许依赖宿主机恰好有 ffmpeg） | 两条命令各跑一遍 |
| 7 | `synthesize` 缺 ffmpeg 时仍抛 `TtsError`，未被改成别的东西 | 既有 tts_omlx 用例全绿 |

**第 6 条的跑法**（模拟 CI 的「无 ffmpeg」）：

```bash
env PATH="/usr/bin:/bin:/usr/sbin:/sbin" python3 -m unittest adapters.tests.test_conformance_probe -v
```

注意：上面这条**只保证这些用例自己**在两种环境下都对；
**不要**用它替代全量复跑（见下）。

## 反空转条款

- **测试必须调用产品的真实代码**：新测试 import 并调用 `OmlxTts.probe_availability`，
  不得另写一份探针逻辑。
- **新测试必须能在「无 ffmpeg」下证伪**：第 1/2 条要能在**本机有 ffmpeg** 的情况下
  造出「无 ffmpeg」（例如用 `VOX_FFMPEG` 指一个解析不到的名字），
  而不是靠「碰巧这台机器没装 ffmpeg」。**否则本机永远绿，等于没测。**
- **不得用「捕获异常后 skip」实现任何一条验收**。

## 回滚方式

```
git checkout -- adapters/tts_omlx/adapter.py adapters/tests/test_conformance_probe.py
```

## 执行方式

**首选**：ZCode 子智能体 `vox-card-executor`（SenseNova，自带 Write/Edit）。
**回落**：`opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T78-…md)"`。

**敏感数据自查（派发前必过）**：本卡**不含**用户录音、真实会话、内网地址、token，
也不含任何仓外仓库的路径或内容——可派发。

---

# 验收记录（2026-09-28，验收方=主会话）

## 结论：通过。CI 条件（无 ffmpeg）下 `adapters` 根由红转 `OK (skipped=3)`。

## 一、负控：同一棵树、同一条件，改前改后

条件 = CI 的第二条（**PATH 里没有 ffmpeg**），解释器用 3.14（CI 用 setup-python 3.12/3.13/3.14）。

| | `python3 -m unittest discover -s adapters` |
|---|---|
| 改前（HEAD 版 `adapter.py`） | `FAILED (failures=3, errors=3, skipped=1)` |
| 改后 | `OK (skipped=3)` |

`failures=3` 是附带的：新增的探针用例断言的正是 ffmpeg 前置条件，旧实现没有该行为。
**errors 那一栏才是本卡要消的那盏红灯。**

skip 消息**点名适配器与缺失的前置条件**，不是一句「跳过」：

```
每个适配器合成的 WAV 必须满足 … ... skipped "omlx-tts: ffmpeg 不可用: 'ffmpeg'
（可在 PATH 提供或以 env VOX_FFMPEG 覆盖）——服务出 24 kHz，缺 ffmpeg 无法降到契约 16 kHz"
```

即：缺什么、怎么补都写在 skip 里——**skip 不是静默降级**（D4）。

## 二、两种环境都要绿（验收第 6 条）

```
python3 -m unittest adapters.tests.test_conformance_probe                              → Ran 12 tests OK
env PATH="/usr/bin:/bin:/usr/sbin:/sbin" …/python3.14 -m unittest adapters.…probe       → Ran 12 tests OK
```

**关键**：新用例**不依赖宿主机恰好有没有 ffmpeg**——
「无 ffmpeg」一律用 `VOX_FFMPEG` 指向一个解析不到的名字造出来，
「有 ffmpeg」用 setUp 造的临时可执行文件挂到 PATH 前面。
否则本机（有 ffmpeg）永远绿，等于没测。

## 三、执行者的四处「我不确定」——验收方裁定

1. **`or` vs `os.environ.get(k, default)`：采纳执行者的 `or`，理由成立。**
   `__init__` 对 `VOX_FFMPEG` 用的就是 `os.environ.get(...) or DEFAULT_FFMPEG`，
   探针**逐字沿用**才能保证「探的就是无参构造会用的那个」。
   若探针改用另一套取法，`VOX_FFMPEG=""` 时会出现「探针查空名报不可用、构造其实能用」——
   那比现在更坏。与 `VOX_TTS_ENDPOINT` 那条纪律**有意不同**，且**代码注释里点明了理由**。
2. **多原因分隔符 `"; "`：采纳。** 单条原因时输出与 T77 原版**逐字相同**
   （既有那条 `assertEqual` 锁住了这点），故不破坏既有契约。
3. **未给「回落链第一档」单独立用例：接受。** 该行为本质依赖宿主机有无 ffmpeg，
   写无条件断言会违反验收第 6 条。它在无 ffmpeg 的实机跑里被隐式验证
   （skip 消息里点的是 `'ffmpeg'`，即常量值）。
4. **多改了一处既有断言（加了 `assertEqual(reason, …)`）：保留。** 只收紧不放宽，
   锁的正是第 2 条要保的「单条原因不带分隔符」兼容性。

## 四、执行者自报的越界写入事故——已核实无残留

执行者做基线核查时跑 `tools/structure_budget/check.py` **漏了 `--no-write`**，
回写了 `tools/structure_budget/LEDGER.md` 与 `snapshot.json`（白名单外），
自查发现后已 `git checkout --` 还原。

**验收方独立核实**：`git diff --stat -- tools/structure_budget/` 输出为空，
`git status -uall` 只有白名单那两个文件 + T78 卡本身（未跟踪）。**无残留漂移。**
（这条事故正是本仓记忆里「`check.py` 裸跑会重写台账」的复发，卡面没写、靠执行者自查拦下的。）

## 五、执行者顺带报的两件事（**本卡不处理，留档**）

- **系统 Python 3.9 下 `test_http_error_carries_status_code` 报 `KeyError: 'file'`**：
  3.9 的 tempfile 语义所致。**CI 用 setup-python 3.12/3.13/3.14，不受影响**，
  且本仓目标版本就是 3.12+。属「本机绿≠CI 绿」这族的另一种形态（比 CI 更旧的环境），
  值得另立卡，但**不在本次推送的阻塞面内**。
- **`adapter.py` 结构预算只剩 4 行余量（146/150）**：再给探针加前置条件就撞线。
  建议下一张卡把端点探活拆成模块级函数。
