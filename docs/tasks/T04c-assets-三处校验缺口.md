# T04c · assets：三处校验缺口（源码级审计发现）

> 本卡来源：T04/T04b 验收通过后，按项目红线「禁止静默降级」调只读审查员做源码级审计，发现 1 条**阻塞级**校验缺口 + 2 条契约/边界缺口。三条都已由验收方实测复现。本卡逐条修。

## 数据分级（派发前置检查项）

**分级：公开。** 本卡内容只有缺陷复现命令与修复要求，**不含**用户录音、真实会话、个人身份信息、内网地址或 token。可派发免费模型执行。

## 缺陷清单（每一条都已实测复现，执行者请先各跑一遍）

### 缺陷 1（阻塞级）：重复身份条目 → 校验器判"健康"，但新条目永远查不到

包格式的寻址身份是 `(key, part_index, rate_key, variant)`。当 manifest 里出现**两条同一身份**的条目（编译器重铸时追加而非替换、或手工改包），当前实现的后果是：

```
$ PYTHONPATH=. python3 -c "
import json, tempfile, wave
from pathlib import Path
from assets import fingerprint, load_pack, validate_pack
with tempfile.TemporaryDirectory() as d:
    d = Path(d); (d/'audio').mkdir()
    for n in ('old','new'):
        with open(d/'audio'/f'{n}.wav','wb') as f:
            w = wave.open(f,'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b'\x00\x00'*80); w.close()
    def e(t, p): return {'key':'greet','part_index':0,'rate_key':'normal','variant':0,'text':t,
        'fingerprint':fingerprint(t,'Tingting','normal','macos-say'),'path':p,'duration_ms':100}
    man = {'pack_id':'p','pack_version':'1','protocol_version':'0.1','ruleset_version':'v1',
           'voice':'Tingting','model_version':'macos-say','created_at':'t',
           'assets':[e('旧话术','audio/old.wav'), e('新话术','audio/new.wav')]}
    (d/'manifest.json').write_text(json.dumps(man, ensure_ascii=False), encoding='utf-8')
    pack = load_pack(d)
    print('validate_pack:', validate_pack(d))          # -> []  判定通过
    print('lookup(新话术):', pack.lookup('greet',0,'normal',0,'新话术'))   # -> None
    print('lookup(旧话术):', '命中' if pack.lookup('greet',0,'normal',0,'旧话术') else 'None')  # -> 命中
"
validate_pack: []
lookup(新话术): None
lookup(旧话术): 命中
```

即：**包内明明有匹配条目却永远查不到，校验器还说"健康"**——正是项目红线里「本该命中却换了路径、且零留痕」的原型。指纹机制作为"保险丝"在这里被短路了。
**要求：`load_pack` 遇到重复身份必须抛 `AssetPackError`（消息含冲突的 key 与坐标），`validate_pack` 必须把它列进问题列表。**

### 缺陷 2（契约违约）：`validate_pack` 在最该回报问题的输入上自己抛异常

T04 卡对 `validate_pack` 的契约原文：「返回问题列表（空列表=通过）」。实测它对两类输入直接抛异常：

```
$ PYTHONPATH=. python3 -c "
import json, tempfile
from pathlib import Path
from assets import validate_pack
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d/'manifest.json').write_bytes('{\"pack_id\":\"中文\"}'.encode('gb18030'))
    try: print(validate_pack(d))
    except Exception as e: print('抛出', type(e).__name__, ':', e)
"
抛出 UnicodeDecodeError : 'utf-8' codec can't decode byte 0xd6 in position 12: invalid continuation byte
```

另一类（条目 `path` 不是字符串）：

```
抛出 TypeError : unsupported operand type(s) for /: 'PosixPath' and 'int'
```

**要求**：`validate_pack` 对这两类输入**返回问题列表**（不是抛异常）；`load_pack` 对同样输入抛 `AssetPackError`（消息含原因）。注意：非 UTF-8 的 manifest 是中文项目里的常见输入，不能让它把校验器打死。

### 缺陷 3（边界缺口）：`path` 未约束在包根内，可指向包外文件

```
$ PYTHONPATH=. python3 -c "
import json, tempfile
from pathlib import Path
from assets import fingerprint, load_pack, validate_pack
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    man = {'pack_id':'p','pack_version':'1','protocol_version':'0.1','ruleset_version':'v1',
           'voice':'v','model_version':'m','created_at':'t',
           'assets':[{'key':'k','part_index':0,'rate_key':'normal','variant':0,'text':'t',
           'fingerprint':fingerprint('t','v','normal','m'),'path':'/etc/hosts','duration_ms':1}]}
    (d/'manifest.json').write_text(json.dumps(man))
    load_pack(d); print('装载通过；validate_pack:', validate_pack(d))
"
装载通过；validate_pack: []
```

绝对路径 `/etc/hosts`（以及 `../../..` 这类逃逸路径）都被判"健康"。包一旦迁机/换包，资产会静默指向别的文件。
**要求：`path` 必须是**相对包根**的路径，且规范化后**仍落在包根内**；违反 → `load_pack` 抛 `AssetPackError`、`validate_pack` 报问题。**

## 目标（产物）

修改 `assets/pack.py` 与 `assets/tests/test_pack.py`，逐条修掉上面 3 条，并补测试。

## 允许修改的文件（白名单）

```
允许修改：assets/pack.py, assets/tests/test_pack.py
允许新增：无
禁止触碰：其余一切文件（含 core/**、assets/fingerprint.py、assets/__init__.py、assets/AGENTS.md、adapters/**、rules/**、docs/**、根 AGENTS.md、README.md）
```

## 禁止事项

- 不得新增第三方依赖（只用标准库）。
- **不得改 `assets/fingerprint.py`**（指纹算法一个字都不许动）。
- **不得放宽任何既有校验**：必填字段、类型检查、rate_key 合法性、用户数据黑名单、指纹一致性、音频文件存在性、`assets` 非空、`AssetPack.lookup` 方法语义（未命中/指纹不匹配/文件不存在 → 返回 `None`，不抛错）——全部保持。
- 不得删既有测试用例（当前 46 条）。
- 不得改 `lookup` 返回 `None` 的契约（调用方决定降级是设计决定，不属本卡）。
- 不得改动白名单外文件；不得顺手重构。

## 验收标准（我会逐条核对）

1. **缺陷 1**：上文复现脚本 → `load_pack` 抛 `AssetPackError`（消息含冲突 key 与坐标）、`validate_pack` 返回非空；且**合法包（身份唯一）不受影响**（原有用例仍全绿）；
2. **缺陷 2**：非 UTF-8 manifest → `validate_pack` **返回问题列表**（不抛异常）、`load_pack` 抛 `AssetPackError`；条目 `path` 非字符串 → 同上（我都会实测）；
3. **缺陷 3**：`path` 为绝对路径（`/etc/hosts`）→ `load_pack` 抛 `AssetPackError`、`validate_pack` 非空；`path` 含 `..` 逃逸（如 `../../../etc/hosts`）→ 同上；而**正常的相对路径（如 `audio/a.wav`）仍正常装载**（不许把合法包也拦了）；
4. `python3 -m unittest discover -s assets -v` 全绿，用例数 **≥ 46**（只增不减）；
5. **`pack.lookup` 方法仍可用**：命中返回 `AssetEntry`、`expected_text` 改一字 → `None`、删音频 → `None`（我复跑 T04b 的验收探针）；
6. 白名单之外无改动（我用 `git status --porcelain` 核对）。

## 反空转条款（必带）

- 新增测试**必须调用产品 API**（`load_pack` / `validate_pack` / `AssetPack.lookup`），不得在测试内重写校验逻辑；
- 负例必须断言**异常类型 + 消息含关键值**（例如重复身份的消息要含冲突 key），不得只写 `assertRaises(Exception)`；
- 断言必须**能失败**（不得恒真、不得 `except: pass`）；
- 不得用 `skipTest` 绕过任何一条。

## 回滚方式

```
cd （仓库根）
git checkout -- assets/pack.py assets/tests/test_pack.py
```

（两文件均已入库为**已跟踪**基线，`git checkout` 有效。）

## 执行方式

```
cd （仓库根）
opencode run -m opencode/mimo-v2.5-free "$(cat docs/tasks/T04c-assets-三处校验缺口.md)" --dir （仓库根）
```

**非交互环境**：请直接改代码，不要先写计划再等确认，不要写仓库外的计划文件（如 `.hermes/plans/*.md`），除白名单文件外不要修改或新建任何文件。

## 卡状态

- [x] 已派发（2026-09-17）→ [x] 已回收 → [x] 验收通过（提交 5b0a2da）
