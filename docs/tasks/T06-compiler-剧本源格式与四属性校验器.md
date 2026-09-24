# T06 · compiler：剧本源格式 + 四属性校验器（五项机器判据）

## 数据分级（派发前置检查项）

**分级：公开。** 本卡只有格式规格、判据与字段名，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。卡内示例话术是自造的公开 demo 文本。可派发。

## 背景

`compiler/` 已能把话术编译成资产包（T05 已验收）；`rules/` 已定义五类规则（T02 已验收）。缺的是**把规则变成机器判据的那一环**：新业务的剧本是否合格，目前只能人肉评审。

本卡把设计基础 §2.4 的**四属性**（无非法吸收态 / 全 key 可达且都被预铸 / 不夹带未审核文本 / 有界）**按本项目当前的剧本形态**落成可判定判据，并顺带把跨批待办 ①② 结项：

- **① `reason` 的落点**：裁定 = **不动 `core.protocol`**，`reason` 是**剧本源格式**的一等字段（作者意图），运行时的 `reason` 是**执行事实**，两层各自完整；
- **② R-3 的检查口径**：裁定 = 按「有出口 / 追问有上限 / 无孤立枝」三条可判定判据落地（C1a / C2a / C3a），不按"自环"字面判（线性 plan 无法表达循环）。

**两条裁定的完整理由、源格式定义、五项判据的公式与违规码，全部写在 `docs/07-剧本源格式与四属性校验.md`——那是本卡的规格，照抄，不得自创或改动。**

**必读**（不得修改）：`compiler/AGENTS.md`、`docs/07-剧本源格式与四属性校验.md`（**权威规格**）、`core/protocol.py`（原语集合与 plan 单元字段）、`rules/ruleset.v1.json`（R-1…R-5 原文）、`rules/skill.md`（输出格式）、`compiler/source.py`（话术库装载，`PackSource` 结构）。

依赖（只读其公开 API，不得修改）：`core/protocol.py`（`PRIMITIVES` / `VALID_RATES`）、`compiler/source.py`（`load_source` / `PackSource` / `SourceError`）、`assets/`（`load_pack` 与 `AssetPack.assets[].key`）。

## 目标（产物）

```
compiler/__init__.py                  追加导出：Script / ScriptError / Violation / CheckResult /
                                      load_script / check_properties
compiler/script.py                    剧本源格式装载与校验（docs/07 §7.2）
compiler/checks.py                    五项机器判据（docs/07 §7.3）
compiler/tests/test_script.py
compiler/tests/test_checks.py
```

### 1. `compiler/script.py`

```python
@dataclass(frozen=True)
class Script:
    script_version: int
    terminal_keys: tuple[str, ...]
    live_whitelist: tuple[str, ...]
    max_retry: int                 # 缺省 3
    units: tuple[dict, ...]        # 原始字典（不转 PlanUnit —— reason 要留着，见 docs/07 §7.5）

class ScriptError(Exception): ...

def load_script(root: Path) -> Script      # 读 <root>/script.json
```

**校验规则照 `docs/07` §7.2 逐条实现**（缺字段、类型不对、`script_version != 1`、`units` 为空、单元字段冲突、`reason` 出现在 key 单元上、**未知字段**……各自报 `ScriptError` 且**消息含文件名/字段名/单元序号**）。

**不得**用 `core.parse_plan` 来装载剧本（它是给运行时 plan 用的校验，会把 `reason` 静默丢弃 —— 这正是跨批待办 ① 要解决的问题）。单元级校验自己写，但**原语与 rate 的合法性必须调 `core.validate_primitive` / 复用 `core.protocol.VALID_RATES`**，不得复制一份白名单。

### 2. `compiler/checks.py`

```python
@dataclass(frozen=True)
class Violation:
    code: str                  # 判据码，照 docs/07 §7.3 的表
    unit_index: int            # 1-based
    key: Optional[str]
    message: str               # 含判据名 + 实际值 + 上限/期望值

@dataclass(frozen=True)
class CheckResult:
    violations: tuple[Violation, ...]
    skipped: tuple[str, ...]

def check_properties(script: Script, source, pack=None) -> CheckResult
```

判据 C1a / C1b / C2a / C2b / C3a / C3b / C3c / C4a / C4b / C4c / C5a / C5b **逐条照 `docs/07` §7.3 实现**（code 名一字不改）；判定顺序 C1 → C2 → C3 → C4 → C5；`pack is None` 时 C3c 跳过并记入 `skipped`（**不得默认判过**）。

`source` 用 `PackSource.phrases[].key` 取已审核话术库；`pack` 用 `assets` 包的 `assets[].key` 集合取已预铸集合。**不得**用文件系统扫目录代替这两个来源。

### 3. 明确不做（`docs/07` §7.4）

不得为「这句是不是追问」「换着 key 追问的累计次数」「跨轮死锁」「是否真转人工」写**任何启发式**（例如按问号猜问句）。这四条是登记在 `docs/07` §7.4 的**人工审查项**，既不是违规也不进返回值。

## 允许修改的文件（白名单）

```
允许新增：compiler/script.py, compiler/checks.py,
          compiler/tests/test_script.py, compiler/tests/test_checks.py
允许修改：compiler/__init__.py（只允许**追加**导出，不得删改既有导出与既有实现）
禁止触碰：其余一切文件（含 core/**、rules/**、assets/**、adapters/**、runtime/**、eval/**、
          compiler/AGENTS.md、compiler/source.py、compiler/quality.py、compiler/prebake.py、
          docs/**、AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- **不得改 `core/protocol.py`**（原语与 PlanUnit 字段本轮零改动）；不得为通过测试放宽任何既有校验。
- 不得用 `core.parse_plan` 装载剧本；不得自造原语/rate 白名单（必须引 `core`）。
- 不得实现语义分析（句意、意图、追问识别）；不得引任何模型/分词库。
- 不得把 `skipped` 当成通过；不得在 `pack=None` 时静默跳过 C3c 而不记 `skipped`。
- 不得往仓库里放业务样例数据（测试用 `tempfile` 自造源与临时包）。
- 不得"顺手优化"、不得改与本卡无关的格式；不得实现 CLI（T10）。

## 验收标准（我会逐条核对）

1. `python3 -m unittest discover -s compiler -v` **全绿**（T05 的 79 条只增不减）；
2. **三个已有正反例的判定（我手跑，这是本卡最硬的一条）** —— 把 `rules/examples/` 的现成单元按 `docs/07` §7.2 包成源格式喂进 `check_properties`（话术库取 `keylist.json` 的 7 个 key、临时包覆盖同样 7 个 key，避免 `key_not_prebaked` 误报）：
   - `ok_intake.json` 的单元 + 声明 `terminal_keys=["closing_thank_you"]` → **`violations == ()`**；
   - `bad_r3_deadlock.json` 的单元 → **含 `no_exit`**（末单元是追问文本）**＋ 3 条 `live_without_reason`**（序号 2/3/4 是自由文本且无 reason）；**不得含 `retry_unbounded`**（三句文本各不相同，不是"同一 key 连续跑"）；
   - `bad_r5_silent_fallback.json` 的单元 → **含 `unreviewed_text`**（`action="SAY"` 带 text，伪装命中）**＋ `no_exit`**（末单元不是终态）；**不得含 `live_without_reason`**（该单元动作是 `SAY` 不是 `SAY_LIVE`，走 C4a 不走 C5a）；
3. **C2a 真有牙**（我手跑）：构造同一 key 连续 4 次的剧本 → `retry_unbounded`，消息含 key、4 与上限 3；连续 3 次 → **不报**（3 是允许的，这是边界）；`max_retry: 5` → `max_retry_exceeds_rule`；
4. **C3 三条各自可失败**（我手跑）：终态之后的单元 → `orphan_branch`（消息含序号）；引用话术库里没有的 key → `key_not_in_library`；引用库里与包共有的 key 但**包里没有** → `key_not_prebaked`；
5. **`pack=None` 的纪律**（我手跑）：C3c 跳过 → `skipped` 含 `"key_not_prebaked"`，`violations` 里**没有** `key_not_prebaked`，且 `skipped` 非空这件事在返回值里可见；
6. **C5 两条**（我手跑）：SAY_LIVE 无 `reason` → `live_without_reason`；`reason` 不在 `live_whitelist` → `reason_not_whitelisted`（消息含实际值）；**`live_whitelist: []` 时任何 SAY_LIVE 都被拦**；
7. **源格式负例**（我手跑）：缺 `terminal_keys` / `units` 为空 / `script_version: 2` / 单元同时带 key 与 text / key 单元带 `reason` / 单元有未知字段 → 均 `ScriptError` 且消息含字段名或序号；
8. **不做启发式**（我会 grep 核对）：`checks.py`/`script.py` 里没有按标点/问号/关键词猜语义的代码，没有 `import re` 用于语义判断；
9. 含方法级中文注释与关键步骤 WHY 注释（尤其：为何不用 `parse_plan`、为何 `skipped` 不等于通过、为何不做语义启发式）；
10. 白名单之外无改动（我用 `git status --porcelain -uall` 核对）；`core/protocol.py`、`compiler/source.py` 零 diff。

## 反空转条款（必带）

- 测试必须调用**产品 API**（`load_script` / `check_properties`），不得在测试内复制判据逻辑或自造 `Violation` 判定；
- **不得只测正例**：每条判据（C1a/C1b/C2a/C2b/C3a/C3b/C3c/C4a/C4b/C4c/C5a/C5b）都要有**能失败的**负例用例，且断言 `code` **与** 消息里的关键值（序号/key/实际值/上限）；
- 正例不得用"空剧本"糊弄：正例必须至少 4 个单元、含 1 个槽位单元、含 1 个终态；
- 不得 `assertTrue(True)`、不得 `except: pass`、不得用 `skipTest` 绕过本卡任何验收项；
- 不得为过测试放宽校验（既有校验强度只增不减）。

## 回滚方式

```
cd （仓库根）
git checkout -- compiler/__init__.py
rm -f compiler/script.py compiler/checks.py compiler/tests/test_script.py compiler/tests/test_checks.py
```

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**（商汤 `sensenova-6.8-flash-lite`；注意其 `thoughtLevel` 必须是 `enabled`，且**改过 agent 定义后要新开会话**）。

**回落**（`vox-card-executor` 不可用时）：

```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T06-compiler-剧本源格式与四属性校验器.md)" --dir （仓库根）
```

这是**非交互环境**：请直接落地代码，不要先写计划再等确认，不要往仓库里写计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要新建或修改任何文件。

## 卡状态

- [x] 已派发（2026-09-17，回落路径 `opencode run` / `sense-nova/sensenova-6.8-flash-lite`，约 24 分钟，无限流）→ [x] 已回收 → [x] **验收通过**（10/10；`compiler` 79 → **165 条测试全绿**；跨批待办 ①② 在本卡结项，裁定见 `docs/07` §7.5/§7.6）
