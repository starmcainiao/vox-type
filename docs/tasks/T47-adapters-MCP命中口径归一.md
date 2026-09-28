# T47 · adapters：MCP 命中口径与规范实现对齐（消除「同一句两入口不同判」）

> 批次：第三十三批 · 拍板人：主会话（2026-09-28 T1 决策层四票 + 独立验收后开批）
> 性质：**扩展区改动，零开批**（`adapters/` = `[扩]`），不碰任何冻结区

---

## 一、为什么开这张卡（事实，不是判断）

`adapters/mcp_vox/tools.py:80-88` 的命中候选定位是裸比较：

```python
def _find_entry(pack, key=None, text=None):
    for entry in pack.assets:
        if key is not None and entry.key == key:
            return entry
        if text is not None and entry.text == text:   # ← 裸 ==，不走归一化
            return entry
    return None
```

而规范实现（`docs/10 §10.3` 冻结的四步归一化 + `docs/10 §10.7` 序列命中）在
`adapters/framework_kefu/normalize.py:48` 与 `adapters/framework_kefu/hit_query.py:164,323`。

**后果（已实测构造出分歧例）**：

```
包内 text='价格①'  查询传入 '价格1'
  裸比较   = False   → MCP 判未命中
  归一比较 = True    → kefu 判命中
```

即**同一句话在 kefu 入口命中、在 MCP 入口不命中**。这违反 `AGENTS.md` 纪律 4
「禁止静默降级：本该命中却换了路径必须报错或留痕（fail-closed）」——
发生在控制面，且**无任何留痕**。

独立验收人裁定：**该主张成立**（`python3 -c "from adapters.framework_kefu.normalize import
normalize_text; ..."` 实测 `'价格①'→'价格1'`，裸比较 False / 归一比较 True）。

---

## 二、目标

让「归一化 + 逐字命中判定」成为**唯一实现**，MCP 消费它而非重写一份。
`adapters/mcp_vox/` 内部不再存在第二份命中口径。

**明确的非目标**（不许做，做了即超范围）：
- ❌ 不动 `find_hit` / `find_hit_sequence` 的判定语义（`docs/10 §10.7` 已冻结且有意区分单条/序列候选口径）
- ❌ 不把判定搬进 `runtime/` 或 `core/`（冻结区，本卡零开批的立足点就是不动它们）
- ❌ 不改 `framework_kefu` 的现有行为（**双跑对齐 → 切 MCP → 再验 framework_kefu 未回归**，framework_kefu 本身不重写）
- ❌ 不新增第三方依赖
- ❌ 不做 HTTP/SSE 传输（`docs/24` 明确本轮只做 stdio）

---

## 三、落点选择（执行者按此实施，不要自选架构）

新建**扩展区**独立模块 `adapters/textmatch/`，作为「跨适配器共用的文本匹配层」，
理由：`adapters/AGENTS.md §⑤` 禁止适配器之间直接 import（`mcp_vox` 不能 import `framework_kefu`），
所以不能简单让 MCP 去 import kefu 的实现；必须有一层双方都能依赖的第三方扩展区模块。

- `adapters/textmatch/__init__.py` —— 公开面，照 `adapters/AGENTS.md` 的 `__all__` 纪律
- `adapters/textmatch/normalize.py` —— **从 `framework_kefu/normalize.py` 迁入**（内容与四步语义一字不改，只改归属与 docstring 里的 WHY 注释保留）
- `adapters/textmatch/hit.py` —— **从 `framework_kefu/hit_query.py` 迁入** `find_hit` / `find_hit_sequence` / 归一化调用点
- `adapters/textmatch/AGENTS.md` —— 按本仓七项模板新建

**迁入后 `framework_kefu` 必须改为 import 新层**（`framework_kefu` 属于适配器家族，
让 `adapters/` 家族内的一个成员依赖同层的公共模块不算 §⑤ 禁止的「跨适配器业务耦合」——
但必须在 `adapters/textmatch/AGENTS.md` 里把这条依赖方向显式写明并说明理由，供后续审计复核）。
为安全起见，`framework_kefu/normalize.py` 与 `framework_kefu/hit_query.py` **保留为 re-export 薄壳**，
保证既有 import 路径不断（大量测试从那里导入），re-export 只做转发，不留第二份实现。

---

## 四、文件白名单

**可改**：
```
adapters/textmatch/__init__.py       （新建）
adapters/textmatch/normalize.py     （新建）
adapters/textmatch/hit.py            （新建）
adapters/textmatch/AGENTS.md         （新建）
adapters/textmatch/tests/            （新建）
adapters/mcp_vox/tools.py            （改：_find_entry 改走 textmatch）
adapters/framework_kefu/normalize.py （改为 re-export 薄壳）
adapters/framework_kefu/hit_query.py （改为 re-export 薄壳）
adapters/textmatch/AGENTS.md 里的依赖方向登记
docs/tasks/T47-*.md                  （本卡的验收记录段）
```

**禁改**（越界即作废）：
```
core/  rules/  compiler/  assets/  runtime/  eval/  cli/   ← 冻结区，本卡一行不动
trigger/  packs/                                        ← 与本卡无关
labs/  tools/                                           ← 与本卡无关
docs/13  docs/20  README.md  CHANGELOG.md                ← 别的卡的活
.github/                                               ← 别的卡的活
任何 *.wav 任何 corpus 任何 admission.md
```

---

## 五、验收标准（逐条可复跑，命令原文 + 期望输出）

> **路径占位约定（T56 占位化，2026-09-28）**：本节命令一律用占位符书写，**不含本机绝对路径**（口径见 `docs/18 §一`「绝对路径一律 0」）。
> - `<本仓根>` = vox-type 仓根目录。**可执行的等价做法**：先 `cd` 到本仓根再执行本节命令；
>   命令里的 `<本仓根>/xxx` 写成相对路径 `xxx` 即可（`<本仓根>` 本身不是可执行路径，别直接复制）。

1. **MCP 侧归一化生效**（本卡的核心判据）
```sh
cd <本仓根>
python3 - <<'PY'
import sys, json, tempfile, pathlib
sys.path.insert(0, '.')
from adapters.textmatch import normalize_text
assert normalize_text('价格①') == normalize_text('价格1')
# 造一个含 '价格①' 的包，调用 mcp_vox 的 lookup 工具，断言 hit=True
from adapters.mcp_vox import tools
print('normalize ok')
PY
```
期望：`normalize ok`（断言不抛）。**必须补一个真正走 `tools` 分派入口的集成测试**，
断言「包内 text 为全角、传入半角 → 返回 `hit:true`」，而不是只测 `normalize_text` 本身。

2. **无第二份实现**
```sh
cd <本仓根>
grep -rn "def normalize_text" --include='*.py' . ; echo "---" ; grep -rn "def find_hit" --include='*.py' .
```
期望：`def normalize_text` 与 `def find_hit` / `def find_hit_sequence` **各只出现 1 处**
（都在 `adapters/textmatch/` 下）；`framework_kefu/normalize.py`、`framework_kefu/hit_query.py`
里不再有函数体（只允许 import + `__all__`）。

3. **MCP 的 `_find_entry` 不再裸比较**
```sh
grep -n "entry.text ==" <本仓根>/adapters/mcp_vox/tools.py
```
期望：**无输出**（rc=1）。

4. **既有行为零回归**（`framework_kefu` 改 re-export 后必须全绿）
```sh
cd <本仓根>
python3 -m unittest discover -s adapters 2>&1 | tail -3
```
期望：`OK`；且 `Ran` 条数 = 316 + `adapters/textmatch/tests/` 新增条数（基线 316，见 `docs/13` 口径）。
**注入验证（必做）**：把 `adapters/textmatch/normalize.py` 的第 ③ 步 NFKC 删掉一行
→ 上面那条「价格①/价格1」用例必须判红；改回即绿。不做注入验证视为未验收。

5. **适配器薄度不被破坏**
```sh
cd <本仓根>
python3 tools/structure_budget/check.py --no-write | tail -3
```
期望：`结构预算：全部合规`；且新增的 `adapters/textmatch/*` 各文件可执行行 ≤150
（`adapters/AGENTS.md §②` 的量化标准；`hit.py` 若超行，按 §⑨.1 登记豁免 + 写拆分留口子）。

6. **公开面纪律**
```sh
grep -rn "^from\|^import" <本仓根>/adapters/mcp_vox/tools.py
```
期望：不含任何下划线私有名 import；`adapters/textmatch` 的 import 走其 `__all__` 公开面。

---

## 六、回滚

```sh
cd <本仓根>
git revert --no-edit <T47 的提交 sha>
# 或：直接
git checkout HEAD~1 -- adapters/
```
本卡无数据迁移、无包格式变更、无协议版本变更 → **回滚零残留**。

---

## 七、执行者纪律

- 只按本卡干活，看不见其他票的结论
- 完成后在**本文件末尾**追加「## 验收记录」，逐条贴 5/6/7/9/11 条的实际输出（**不得写「已通过」而不贴输出**）
- 不提交（由验收方统一提交）
- 不做达标判定（判定归验收方）

---

## 验收记录（执行者：vox-card-executor，2026-09-28）

> 纪律：本节逐条贴**实际输出**，不写「已通过」而不贴输出。
> 判定归验收方；本节只回事实。

### 改了什么

| 文件 | 动作 |
| --- | --- |
| `adapters/textmatch/__init__.py` | 新建（公开面 + `__all__`，14 个符号） |
| `adapters/textmatch/normalize.py` | 新建（自 `framework_kefu/normalize.py` 迁入，四步语义一字未改，仅改归属与 WHY 注释） |
| `adapters/textmatch/hit.py` | 新建（自 `framework_kefu/hit_query.py` 迁入，判定语义一字未改；`find_hit` / `find_hit_sequence` / `confirm` / `lookup_key` / `pick_by_text` / `build_text_index` / `text_for_key` / `MODE_*` / `REASON_TEXT_NOT_PREBAKED` / `_sequence_candidates`） |
| `adapters/textmatch/AGENTS.md` | 新建（七项模板；§⑤ 含**依赖方向显式登记**，供审计复核） |
| `adapters/textmatch/tests/__init__.py` | 新建（夹具：临时包 + 真 WAV；只用 `assets` 公开 API 算指纹） |
| `adapters/textmatch/tests/test_normalize.py` | 新建（9 例；含注入靶子与「唯一实现」同源断言） |
| `adapters/textmatch/tests/test_mcp_parity.py` | 新建（9 例；**走 `adapters.mcp_vox.tools.call_tool` 分派入口**） |
| `adapters/framework_kefu/normalize.py` | 改为 re-export 薄壳（12 行；无函数体） |
| `adapters/framework_kefu/hit_query.py` | 改为 re-export 薄壳（32 行；无函数体） |
| `adapters/mcp_vox/tools.py` | `_find_entry` 改走 `adapters.textmatch.find_hit`；删 `_confirmed_hit`（`find_hit` 内部已 `confirm`，避免第二遍复核） |

冻结区（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/ trigger/ packs/ labs/ tools/ docs/13 docs/20 README.md CHANGELOG.md .github/`）**一行未动**。

### AC1 · MCP 侧归一化生效

卡里那段脚本（原样跑），末尾按卡要求**补了真正走 `tools` 分派入口**的调用：

```sh
cd <本仓根>
python3 - <<'PY'
import sys, json, tempfile, pathlib
sys.path.insert(0, '.')
from adapters.textmatch import normalize_text
assert normalize_text('价格①') == normalize_text('价格1')
from adapters.mcp_vox import tools
from adapters.textmatch.tests import build_pack
tmp = pathlib.Path(tempfile.mkdtemp(prefix='t47-ac1-'))
build_pack(tmp / 'pack', [('price', '价格①', 0)])
res = tools.call_tool('vox_lookup', {'pack_dir': str(tmp / 'pack'), 'text': '价格1'})
payload = json.loads(res['content'][0]['text'])
assert res.get('isError') is not True, payload
assert payload['hit'] is True, payload
print('payload =', json.dumps(payload, ensure_ascii=False))
print('normalize ok')
PY
```

实际输出：

```
payload = {"hit": true, "key": "price", "rate_key": "normal", "variant": 0, "duration_ms": 200, "model_version": "macos-say"}
normalize ok
```

走分派入口的集成测试（卡要求「不是只测 `normalize_text` 本身」）：

```sh
python3 -m unittest adapters.textmatch.tests.test_mcp_parity -v 2>&1 | grep -E "\.\.\. (ok|FAIL|ERROR)|^Ran|^OK|^FAILED"
```

```
test_genuine_miss_is_not_error (...) ... ok
test_halfwidth_query_hits_fullwidth_pack_entry (...) ... ok
test_identical_text_still_hits (...) ... ok
test_key_path_still_hits (...) ... ok
test_both_key_and_text_is_error_not_silent_pick (...) ... ok
test_neither_key_nor_text_is_error (...) ... ok
test_plan_hit_and_miss (...) ... ok
test_hit_agrees_with_textmatch (...) ... ok
test_miss_agrees_with_textmatch (...) ... ok
Ran 9 tests in 0.171s
OK
```

其中 `test_halfwidth_query_hits_fullwidth_pack_entry` 就是卡的原始分歧例：包内 `价格①` / 传入 `价格1` → `hit:true`。

### AC2 · 无第二份实现

```sh
cd <本仓根>
grep -rn "def normalize_text" --include='*.py' . ; echo "---" ; grep -rn "def find_hit" --include='*.py' .
```

实际输出：

```
adapters/textmatch/normalize.py:55:def normalize_text(text: str) -> str:
---
adapters/textmatch/hit.py:169:def find_hit(pack, *, key: Optional[str] = None,
adapters/textmatch/hit.py:328:def find_hit_sequence(pack, text: str, rate_key: str = "normal") -> SequenceHitResult:
```

`normalize_text` 1 处、`find_hit` 1 处、`find_hit_sequence` 1 处，**全部在 `adapters/textmatch/` 下**。
`framework_kefu/normalize.py` 与 `hit_query.py` 已无函数体（只 `import` + `__all__`）。

> 执行过程中的真实插曲（如实记）：首版薄壳的 docstring 里为了说明「本文件不再持有实现」，
> 原文照抄了卡里那两条 grep 的**模式串本身**，导致 AC2 自己撞枪（壳文件被判成第二处定义）。
> 改成不含该字面串的表述后复跑才干净。若验收方日后在别的文件里也照抄这两个模式串，
> 会重现同一现象——这是 grep 口径的固有噪声，不是第二份实现。

### AC3 · MCP 的 `_find_entry` 不再裸比较

```sh
grep -n "entry.text ==" <本仓根>/adapters/mcp_vox/tools.py
```

实际输出：**无输出，rc=1**。

```text
rc=1  （期望 rc=1 且无输出）
```

### AC4 · 既有行为零回归（含必做的注入验证）

```sh
cd <本仓根>
python3 -m unittest discover -s adapters 2>&1 | tail -3
```

实际输出：

```
Ran 335 tests in 24.607s

OK
```

`335 = 316（docs/13 基线）+ 19（本卡新增：`test_normalize` 10 + `test_mcp_parity` 9）`。

**注入验证（把第 ③ 步 NFKC 删掉 → 必须判红 → 改回即绿）**：

```sh
python3 - <<'PY'
from pathlib import Path
p = Path("adapters/textmatch/normalize.py")
src = p.read_text(encoding="utf-8")
target = '    s = unicodedata.normalize("NFKC", s)\n'
assert src.count(target) == 1
p.write_text(src.replace(target, "    s = s  # INJECTED: 第 ③ 步 NFKC 被删除\n"), encoding="utf-8")
print("injected: 第 ③ 步 NFKC 已删除")
PY
```

注入后实际输出：

```
injected: 第 ③ 步 NFKC 已删除
```

`textmatch` 归一化靶子（判红）：

```
FAIL: test_still_covers_more_compatibility_chars (adapters.textmatch.tests.test_normalize.TestStepThree.test_still_covers_more_compatibility_chars)
AssertionError: '2²' != '22'
...
AssertionError: '价格①' != '价格1'
- 价格①
?   ^
+ 价格1
?   ^

Ran 4 tests in 0.001s

FAILED (failures=3)
```

**MCP 分派入口集成用例（判红）**：

```
FAIL: test_halfwidth_query_hits_fullwidth_pack_entry (adapters.textmatch.tests.test_mcp_parity.TestMcpUsesNormalizedHit.test_halfwidth_query_hits_fullwidth_pack_entry)
AssertionError: False is not True : {'hit': False, 'reason': 'key_not_prebaked'}

Ran 9 tests in 0.235s
FAILED (failures=1)
```

卡里 AC1 那段脚本同样判红：

```
Traceback (most recent call last):
  File "<stdin>", line 4, in <module>
AssertionError
```

`adapters` 全量：`Ran 335 tests … FAILED (failures=9)`。

改回后：`Ran 19 tests … OK` / `Ran 335 tests … OK`。

**11 个测试根全量（对齐 `.github/workflows/tests.yml` 的跑法）**：

```sh
for d in core rules assets adapters compiler runtime eval cli tools trigger packs; do
  printf "%-10s " "$d"; python3 -m unittest discover -s "$d" 2>&1 | grep -E "^(OK|FAILED)" | tail -1
done
```

```
core       OK
rules      OK
assets     OK
adapters   OK
compiler   OK
runtime    OK
eval       OK
cli        OK
tools      OK
trigger    OK
packs      OK (skipped=24)
```

### AC5 · 适配器薄度不被破坏

```sh
cd <本仓根>
python3 tools/structure_budget/check.py --no-write | tail -3
```

实际输出：

```
cli       阈值 ≤150 （cli/AGENTS.md）  文件 10  可执行行   586  超限 0
合计 60 个 .py 文件 / 5528 可执行行
结构预算：全部合规
```

`adapters/textmatch/*` 逐文件可执行行（同一 tokenize 口径）：

```
4   adapters/textmatch/__init__.py
129 adapters/textmatch/hit.py
16  adapters/textmatch/normalize.py
35  adapters/textmatch/tests/__init__.py
75  adapters/textmatch/tests/test_mcp_parity.py
44  adapters/textmatch/tests/test_normalize.py
```

`hit.py` = 129 **≤150，未超限、未登记豁免、未新增拆分留口子**（台账豁免清单不变）。

### AC6 · 公开面纪律

```sh
grep -rn "^from\|^import" <本仓根>/adapters/mcp_vox/tools.py
```

实际输出：

```
20:import json
21:from pathlib import Path
23:from assets import load_pack, validate_pack
24:from runtime import REASON_KEY_NOT_PREBAKED
25:from adapters.textmatch import find_hit
```

无任何下划线私有名 import；`adapters.textmatch` 走包级 `find_hit`（在 `__init__.py` 的 `__all__` 里），
**未**出现 `from adapters.textmatch.hit import …` 这类绕过 `__all__` 的模块路径。

---

## 执行者如实报告的遗留项（无权限改，不粉饰）

1. **`adapters/AGENTS.md §⑨` 没有登记 `textmatch/`**。该节列了 `framework_kefu` 与 `mcp_vox`
   的依赖授权（`core + runtime/assets 公开面`），新目录 `textmatch/` 同样依赖
   `runtime.REASON_KEY_NOT_PREBAKED` 与 `assets.AssetEntry`，但**没有登记**。
   本卡把它写在了 `adapters/textmatch/AGENTS.md §⑤`（卡明确要求写在那里），
   `adapters/AGENTS.md` **不在本卡白名单内**，故未动。建议另卡补 §⑨ 的一条。
2. **`mcp_vox/tools.py` 有两处对外可见的行为变化**，均源于「不再自实现判定」，如实列出：
   - `vox_lookup` **同时**给 `key` 与 `text`（都非空）时：旧行为是静默优先 `key`；
     新行为是 `find_hit` 的冻结契约抛 `ValueError` → `isError:true`。
     inputSchema 本就写「与 key 二选一」，故按非法输入处理，但**确实是行为变化**。
   - 空串 `key`（`""`）配 `text`：`_lookup` 里按既有 `if not key and not text` 的同一口径
     把空串当作「没给」，只递 `text` 过去，与旧行为一致。
   - key 档现在经 `lookup_key`（`part_index == 0` + `rate_key` 匹配）。compiler 的准入判据是
     「一句一 variant」，实测 `examples/prebuilt-pack` 全部 `part_index=0 / rate_key=normal`，
     故对合规包**结论不变**；对手搓的多段包，MCP 的 key 档口径从此与 kefu 一致（这正是本卡要的）。
3. **`adapters/tts_macsay.tests.test_adapter.TestSynthesizePositive.test_synthesize_uses_tempfile`
   是既有 flake**，与本卡无关：单跑两次，一次 `FAILED (failures=1)`、一次 `OK`，同一份代码。
   该测试只 `import adapters.tts_macsay.adapter`，对 `textmatch` / `framework_kefu` / `mcp_vox`
   零引用（已 `grep` 确认）。本卡的 AC4 全量跑与 11 根全量跑**均未复现**（最后一次全量为全绿）。
4. **未提交**（`git commit` 归验收方）。工作区另有并行卡的未跟踪文件
   （`conftest.py`、`docs/tasks/T48-*.md`、`tools/run_all_tests.py` 等），本卡未触碰。
