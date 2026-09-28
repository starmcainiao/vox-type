# T60 · 门禁被一个 NUL 字节静默绕过 + 速览表数字打架

> 开批：2026-09-28 · 性质：**一处判据修复 + 两处文档口径**（零冻结区契约）
> 起因：独立验收人对 `43cfb0b` 的**有条件通过**判决，三条必修项
> 前置：`43cfb0b` 已提交；本卡是其后的修订

## 一、三条必修项（全部已由主会话独立复现，不是转述）

### 必修 ①【严重】NUL 字节让整道门禁静默失效

`tools/check_no_machine_paths.py` 的 `scan_file`：

```python
if b"\x00" in raw:
    return [], "binary"        # ← 整文件跳过，且不影响 passed / 退出码
```

**主会话实测**：造 `_INJECT_bin.txt`，首字节 `\x00`、其后是本机家目录前缀路径：

```
passed= True   violation_count= 0
skipped= [{'file': '_INJECT_bin.txt', 'reason': 'binary'}]
rc=0
```

**一个字节绕过整道发布树红线。** 而 CI 关卡 4a 照样打印「PASS 机器绝对路径（零违规）」。

这正是 §五#31 记录的同一类失效——**判据正确，但没作用到它覆盖不到的那类输入上**。
基线里已有 11 个 wav 因 binary 被跳过；一旦有人把文本塞进任何带 NUL 的文件，
红线静默失效，**且没有任何信号**。

**处置**（只许更严，不许更松）：

- 判「二进制」不再看「有没有 NUL」，改为**看魔数**（`wav`/`png`/`jpeg`/`gif`/`pdf`/`zip`/`gzip`/
  `elf` 等文件头的真实签名）；
- **无魔数但含 NUL 的文件：照扫不误**——把 NUL 剥掉后按文本扫，
  违规照样计入 `violation_count`；
- 若仍判为真二进制（**须有魔数证据**），在 JSON 的 `skipped` 里带 `reason`，
  **并新增一个 `unscanned[]` 计数**，让「有多少内容没被检查」本身成为一个可被看见的数字
  （现在 `skipped` 是信息性的、不影响判定，**这正是无声的来源**）；
- **11 个 wav 必须仍然被正确跳过**（它们有 RIFF/WAVE 魔数，是真二进制）——
  改完跑一遍确认 `skipped` 条数与 `reason` 合理，不许把它们变成误报。

**验收的核心是反向的**：注入 NUL 文件后**必须 rc=1**；同时**11 个 wav 仍正常跳过**。
两个都成立才算修好。只满足前者会把 11 个 wav 变成噪声，只满足后者等于没修。

### 必修 ②`docs/13` 速览表与 §五 自相矛盾

- `:79` 速览行写「31 项（**16 项已关 ✅**；余 **15** 项：#4、#24、#27、#28、#29、#30、#31 中……）」
- `:139` §五 表头写「31 项：**19 项已关 ✅ / 12 项开放**」
- 按 §五 **自己刚补上的判据**复跑 = `31 19 12` → **`:79` 是过期数字**。

讽刺处：#31 刚以「一个没有判据的计数等于没有计数」为由给 §五 补了判据，
**却没回头改被判据管的那一行速览表**。

**处置**：改 `:79` 的数字与编号清单，与 §五 的判据对齐。
**同时**：速览表也补上同一判据的指引，免得下次再漂。

### 必修 ③`docs/13` §五#30 的口径描述少写了一半

#30 写「含 ≥8 字符的实质话术文本」，但独立验收人实测：**该判据跑在公开树上得 27
（22 整段 + 5 节选），22 只有加上「整段逐字」这条隐含约束才成立。**

**数字是对的，口径描述不完整**——而口径不完整的数字，下一个人复算必然得出 27。

**处置**：把「整段逐字」这条约束**写进口径本身**，
并把工作树口径（24 = 22 整段 + 2 节选）与公开树口径（27 = 22 + 5）分别写明，
**三个数各自标清自己在数什么**。不许含糊成一句「22 条」。

## 二、文件白名单

1. `tools/check_no_machine_paths.py` —— **必修 ①**（唯一允许改判据的地方，**只许更严**）
2. `docs/13-未完成清单.md` —— 必修 ②③
3. `docs/tasks/00-索引.md` —— 追加本卡小节

**禁止**：改 `tools/pack_phrase_audit.py` / `tools/README.md` / `.github/workflows/tests.yml`
（关卡 4a 调的就是第 1 个文件，**脚本接口不许变**——`--json` 输出结构与退出码语义必须保持，
否则 CI 会静默失效）；改任何冻结区（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）；
改 `packs/**` 与 `admission.md`；改 `docs/18` / `docs/20` / README / CHANGELOG。

**禁止 `git add` / `git commit` / 任何 remote 操作。禁止 `git add -A`。**

## 三、验收标准（可复跑）

```sh
# AC1 NUL 绕过已堵死（**本卡最关键的一条**）
# 注入：首字节 NUL + 本机家目录前缀路径 → 必须 rc=1 并指名该文件
# 恢复：删除后必须 rc=0，且整份 JSON 与注入前逐字节相同
# **注入后仍 rc=0 = 本卡直接判不通过。**

# AC2 真二进制仍被正确跳过（防止修过头把 11 个 wav 变成误报）
python3 tools/check_no_machine_paths.py --json | python3 -c \
  "import json,sys;d=json.load(sys.stdin);print('violations=',d['violation_count']);print('skipped=',len(d.get('skipped',[])))"
# 期望：violations=0；skipped 全部是 11 个 .wav（有魔数证据），且 unscanned 字段存在

# AC3 脚本接口没变（CI 不会静默失效）
python3 tools/check_no_machine_paths.py --json | python3 -c \
  "import json,sys;d=json.load(sys.stdin);print(sorted(d.keys()))"
# 期望：仍含 passed / violations / violation_count / violations_by_class / skipped，
#      **且新增 unscanned**；工具名 check_no_machine_paths 不变

# AC4 判据只更严没更松：门禁在真实仓上仍是 0 违规
python3 tools/check_no_machine_paths.py; echo "rc=$?"     # 期望 rc=0

# AC5 docs/13 速览与 §五 一致（无第二处数字打架）
python3 - <<'PY'
import re
t=open('docs/13-未完成清单.md',encoding='utf-8').read().split('\n')
i=next(k for k,l in enumerate(t) if l.startswith('## 五、'))
j=next(k for k in range(i+1,len(t)) if t[k].startswith('## 六'))
r=[l for l in t[i:j] if re.match(r'^\|\s*\d+\s*\|',l)]
c=[x for x in r if ('✅' in x.split('|')[2] or '~~' in x.split('|')[2])]
print('§五 =',len(r),len(c),len(r)-len(c))
PY
# 期望：31 19 12；且 :79 速览行写的数字与之相符（人肉核对，报告里贴两行原文）

# AC6 冻结区零改动
git status --porcelain core/ rules/ compiler/ assets/ runtime/ eval/ cli/     # 期望空

# AC7 全量测试仍全绿
python3 tools/run_all_tests.py 2>&1 | tail -2                                # 期望 failed=0

# AC8 结构预算（必须带 --no-write）
python3 tools/structure_budget/check.py --no-write 2>&1 | tail -1           # 期望：全部合规
```

## 四、禁止事项

- **禁止把门禁改弱**：不许为了跳过 11 个 wav 而放宽、不许调大阈值、不许加豁免。
- **禁止改脚本的对外接口**（JSON 键名、退出码语义）——CI 靠它。
- **禁止把「加 NUL 就跳过」换成另一个等价的绕过面**（例如按扩展名跳过 `.txt`）。
- **禁止顺手把 `docs/13` 其它未决项改成已完成**。
- **禁止 `git add` / `git commit` / `git push` / `git remote` / `git add -A`。**

## 五、回滚

3 个文件各自 cp 到 `/tmp/t60-baseline/` 留底，回滚用 cp（工作区有大量未提交存量，
`git checkout --` 会连带回滚）。

## 六、本卡完成后

§五#31 的「判据正确但没作用到载体上」这条记录，需要**补一句**：
本次发现的失效面是「判据没作用到它扫不到的文件类型上」——
**与原文记录的是同一类错误的另一个面**，不是两个问题。补这一句，不许改原文结论。
