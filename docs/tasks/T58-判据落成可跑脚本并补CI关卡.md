# T58 · 把三条判据落成可跑脚本 + 补 CI 关卡 4 + 修 tools/README 口径

> 开批：2026-09-28 · 性质：**新增一个只读工具 + 一处 CI + 两处文档**（零冻结区契约）
> 起因：独立验收人终审的 D-2 残留、C-2、C-5、⑧

## 一、背景：三条判据现在只存在于文字里

### ① 绝对路径判据自指（AC2 不可达）

T56 把 79 行绝对路径清到 2 行，**但这 2 行删不得**——它们是 T51 的验收命令本身：

```
git diff … | grep -nE "<一段含本机路径前缀的正则>"    ← 判据的正则字面量
```

> **T59 补记**：本卡初稿在上面这个代码块里**把那段正则原样抄了出来**——
> 于是这张「消灭自指」的卡自己又制造了一次自指。**只描述、不书写**：
> 那段正则与它的前缀现在只存在于 `tools/check_no_machine_paths.py` 里，
> 文档侧一律改写为 `python3 tools/check_no_machine_paths.py`。

判据的正则会扫到写判据的那一行。**这不是泄漏，是判据自指**；
但后果是任何扫描都恒非零，于是这条门禁事实上无法自动化。

**解法：把判据落成脚本**，文档里只留 `python3 tools/check_no_machine_paths.py`，正则藏在脚本里。

### ② T55 的「12 条可公开 / 12 条含内部字样」不可复现

`docs/13 §五#29` 自己的验收标准第 ① 条要求「四类字样的判据与计数写进仓内**可复跑脚本或卡面**（不接受『人工看过』）」。

**现状：只在文字里。** 独立验收人独立复跑得到 **13 敏感 / 11 公开**，与 T55 的 12/12 不符，
分歧项 `repair_ask_natural_userNo` 被 T55 判为「可公开」，
但它命中的正是 T55 自己用来判别人敏感的第 ② 类字样（指向产品登录态/入口的文案）。
**同一判据，一边敏感一边公开——定性表不是从它自己声明的判据机械可复现的。**

### ③ `tools/README.md` 的 `skipped` 口径段已作废

仍写「packs 根常态 24 条」，而 T51 已修掉「路径写死就 skip」的机制；
T55 加守卫后 yaml 缺失时是 **25 条**。**口径段的错误比样例行更危险。**

## 二、本卡动作

### A. 新增 `tools/check_no_machine_paths.py`（**只读**）

- 扫描**全部入库文件 + 未跟踪文件**（覆盖 `AGENTS.md §三` 提到的发布树红线）；
- 判据：本机家目录前缀、外部卷挂载前缀、启动卷 tmp 前缀；
- **前缀字面量由环境变量或脚本内常量提供，不写在被扫描的文档里**；
- 输出机器可读 JSON（`passed` / `violations[]` / 扫描文件数）；
- **退出码**：`0` 通过 / `1` 有违规。**必须断言计数 + 非零退出**（`docs/13 §八#22`：假绿比红更坏）；
- **自指豁免**：脚本自己、以及显式标注了 `# scan-exempt: <理由>` 的行豁免——
  这样 T51 的验收命令可以改写成「`python3 tools/check_no_machine_paths.py`」，
  正则不再出现在被扫文档里。

**零第三方依赖**（纯标准库），与本仓其余 `tools/` 一致。

### B. 改 T51 的验收命令，指向脚本

把 `T51:133` 与 `T51:288` 那两行**含本机路径前缀的 grep 正则字面量**改成调用脚本。
**目标：全仓绝对路径归零（AC2 真正可达），而不是「归零到只剩判据自己」。**

> **T59 补记**：本卡初稿在这行又抄了一次那段正则，是**同一处自指的复发**
> （§一那段与这一段指向同一个字面量）。改法同上：只描述、不书写。

### C. 新增 CI 关卡 4：发布树卫生的加强版

现有 `.github/workflows/tests.yml` 关卡 3 只查「应被 `export-ignore` 排除的路径没漏出去」。
**新增关卡 4**：调 `tools/check_no_machine_paths.py`，**任一违规 → 整步非零退出**；
并调 `tools/pack_phrase_audit.py` 的**硬失败两类**（`credential` / `internal-endpoint`）。
**`verbatim-overlap` 不进门禁**——理由见 §二D。

**必须在 workflow 里留一条约束注释**（独立验收人 ⑧ 的建议）：

> 关卡 2 是全 workflow 唯一 `set +e` 的步骤，靠手工累加 `failed` 变量收口。
> **将来任何人新增断言，必须落在 `expect()` 内或两处 `failed=1` 累积点内，否则失败会被无声吞掉。**

### D. 把比对判据落成可复跑脚本（**分级，不是一刀切**）

新增 `tools/pack_phrase_audit.py`（或并入 A 的模式，二选一，**但不许只留文字**）：

- 输入：`packs/heat_kefu/admission.md` + 仓外 kefu yaml（**只读，路径走 env/占位**）；
- 输出：逐条 key 的命中类别与计数，**只输出 key 名与类别，不输出任何原文**；
- **必须处理 T57 新发现的口径差异**：把比对阈值降到「任意长度」会多出
  `faq_overrides` / `slot_labels` 两个 key——**它们是结构化标签不是话术正文**。
  脚本必须显式区分这两类并给出可复跑的判定依据。

> **本卡初版此处要求「有含内部字样命中 → 退出码 1」，已改为分级。**
>
> 理由：那段话术**能否对外发布只有业务方能判断**（`docs/20 §四` 记录了处置三路径，
> 截至 2026-09-28 全部未决）。把一个未经裁定的商业判断焊成 CI 硬门禁，
> 等于用机器固化一个还没人做的决定；业务方一旦裁定「本来就打算公开」，
> 门禁就成了需要绕过 CI 才能修的障碍。
>
> **分级规则**：
>
> | 类别 | 判据（可机械复现） | 退出码 |
> |---|---|---|
> | `credential` | 命中 token / 密钥 / 口令特征 | **`1`** |
> | `internal-endpoint` | 命中内网地址、私有仓绝对路径、内网服务名 | **`1`** |
> | `verbatim-overlap` | 与仓外 yaml 逐字相同且长度 ≥ 阈值 | `0`（**只报告**） |
> | `structured-label` | 同上但属 `faq_overrides` / `slot_labels` 一类 | `0`（只报告） |
>
> 前两类**无论业务方怎么裁定都必须失败**——凭据与内网地址进公开仓没有「可公开」一说。
> 后两类出报告，退出 `0`，并在输出里带上策略字段
> `policy="pending-business-decision"`；业务方拍板后改这一个字段即可切换成硬失败，
> **不需要改判据、不需要改 CI**。
>
> CI 关卡 4 因此**只接前两类**；`verbatim-overlap` 走人工复核，不进自动门禁。

### E. 修 `tools/README.md` 的 `skipped` 口径段

- 删「packs 根常态 24 条」，改为「yaml 可达时 0 条；不可达时 25 条」并写明触发条件；
- 补上 `adapters` 那几类 `@unittest.skipUnless` 条件；
- 明确「`skipped` 是环境相关量，不是契约」。

## 三、文件白名单

1. `tools/check_no_machine_paths.py` —— **新增**
2. `tools/pack_phrase_audit.py` —— **新增**（若与 A 合并则只建一个文件，说明理由）
3. `tools/README.md` —— 补两个新工具的说明 + 修 E
4. `.github/workflows/tests.yml` —— **仅新增关卡 4** + 加那条约束注释（**不得改动关卡 1/2/3 任何一行**）
5. `docs/tasks/T51-packs-yaml路径探测.md` —— 仅改那两行验收命令
6. `docs/13-未完成清单.md` —— 更新 #29 的验收标准指向新脚本
7. `docs/tasks/00-索引.md` —— 追加本批小节

**禁止**：改任何冻结区文件（`core/ rules/ compiler/ assets/ runtime/ eval/ cli/`）；
改 `packs/**` 任何文件；改 `admission.md`；改 `docs/18` / `docs/20` / README / CHANGELOG（T57/T56 已定）；
改 workflow 的关卡 1/2/3。
**禁止 `git add` / `git commit` / 任何 remote 操作。**

## 四、验收标准（可复跑）

```sh
# AC1 脚本存在、只读、零第三方依赖、退出码正确
python3 tools/check_no_machine_paths.py; echo "rc=$?"
# 期望：rc=0，JSON 里 passed=true

# AC2 绝对路径判据现在真正可达（全仓归零，含 T51 那两行改写后）
python3 tools/check_no_machine_paths.py --json | python3 -c "import json,sys; print('violations=', json.load(sys.stdin)['violations'])"
# 期望：violations=[]

# AC3 脚本自己会红（注入自证：造一个含绝对路径的临时文件 → 必须 rc=1 并指名该文件）
# 恢复后必须 rc=0。**注入后仍 rc=0 = 本卡直接判不通过。**

# AC4 比对判据可复跑，且分级正确（见 §二D）
python3 tools/pack_phrase_audit.py; echo "rc=$?"
# 期望：rc=0（本仓现状无 credential / internal-endpoint 命中）；
#      输出含 policy="pending-business-decision"、verbatim-overlap 与 structured-label 分列计数

# AC4b 分级自证：注入 credential 特征 → 必须 rc=1；移除后必须 rc=0
# **注入后仍 rc=0 = 本卡直接判不通过**

# AC5 CI 新增关卡 4，且关卡 1/2/3 零改动
python3 -c "import yaml;d=yaml.safe_load(open('.github/workflows/tests.yml'));print(len(d['jobs']['unittest']['steps']),'steps')"
git diff .github/workflows/tests.yml | grep -c "^-"    # 期望：只含 --- 那行
# 期望：steps 数 = 8（原 7 + 新增 1），且无删除行

# AC6 workflow 里有关卡 2 的约束注释
grep -n "expect()" .github/workflows/tests.yml
# 期望：命中，且注释里写明「新增断言必须落在 expect() 或两处 failed=1 累积点内」

# AC7 tools/README 口径已修
grep -n "常态 24 条" tools/README.md
# 期望：零命中

# AC8 冻结区零改动
git status --porcelain core/ rules/ compiler/ assets/ runtime/ eval/ cli/ labs/
# 期望：空

# AC9 全量测试仍全绿（新增脚本不应影响任何现有断言）
python3 tools/run_all_tests.py 2>&1 | tail -2
# 期望：failed=0
```

## 五、禁止事项

- **禁止把判据的正则字面量留在被扫描的文档里**——那正是本卡要消灭的自指。
- **禁止新脚本引入第三方依赖**（无 pip、无 pytest）。
- **禁止新脚本写任何文件**（只读工具；`--json` 只输出到 stdout）。
- **禁止脚本输出话术原文**——只输出 key 名与类别。
- **禁止把 `verbatim-overlap` 接进 CI 硬门禁**——商业判断未裁定（`docs/20 §四`），
  只有 `credential` 与 `internal-endpoint` 两类可以。
- **禁止改动 workflow 关卡 1/2/3**——那是 T49 交付并经独立验收人验证过的。
- **禁止让新脚本读写的路径写死成机器绝对路径**（否则它自己就违反判据）。

## 六、回滚

新增文件直接删除；`workflow` 与文档 `git checkout --`。
**注意：回滚本卡会让 AC2 重新不可达**（绝对路径判据退回自指状态）。
