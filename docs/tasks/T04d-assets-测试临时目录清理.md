# T04d · assets：测试临时目录清理（mkdtemp 泄漏修复，只动测试资源管理）

## 背景（只写必需）

`assets/tests/test_pack.py` 有 **2 处** `tempfile.mkdtemp()`（第 264 行、第 621 行附近）创建了临时目录
却**没有清理**——反复跑测试会在 `$TMPDIR`（及 `/tmp`）里累积残留目录。这是 2026-09-17 T04 时代
登记的「本批遗留」，至今未修（`docs/13-未完成清单.md` §五 第 2 条）。

本卡**只改测试的资源管理方式，不改任何断言与用例逻辑**——测试条数、测试名、期望值全部原样。

## 目标（可验收的产物）

把 2 处裸 `mkdtemp()` 改成**必然清理**的写法（两种任选其一，全文件统一）：
- `tempfile.TemporaryDirectory()` 上下文管理；或
- 保留 `mkdtemp` 但立刻 `self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)`。

（adapters 层 T03c 已有同款先例：`test_crossvolume` 的清理改成 `addCleanup`——照那个模式。）

## 允许修改的文件（白名单）

```
允许修改：assets/tests/test_pack.py（只允许改资源管理：临时目录的创建/清理方式、
          必要的 import（shutil 等）；不得改任何断言、用例名、测试方法数、期望值）
禁止触碰：其他一切文件——尤其 assets/pack.py / assets/fingerprint.py 等产品代码、
          其他层的测试、docs/
```

## 禁止事项

- **不改任何断言与期望值**（测试条数、用例名必须与改动前完全一致）
- 不得改产品代码（`assets/` 下非测试文件零 diff）
- 不得删测试或合并测试来"减少泄漏面"
- 不得新增第三方依赖；不得"顺手优化"

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s assets` 全绿，测试数与改动前**完全一致**（59 条，不多不少）；
2. **泄漏实测（验收方会亲测）**：记录运行前 `$TMPDIR` 下测试命名模式的临时目录数 → 跑全量
   assets 测试两遍 → 再数一遍，**新增残留 = 0**（改前这个数字会随跑测试增长）；
3. `grep -n "mkdtemp" assets/tests/test_pack.py` 的每一处调用点，其所在用例都有必然执行的清理
   （`addCleanup` 或上下文管理覆盖全部路径，包括断言失败抛异常的路径——`addCleanup` 天然覆盖，
   裸 `finally` 不算"必然"，除非写对了异常路径）；
4. `git diff assets/tests/test_pack.py` 里**不出现**任何断言行/期望值的增删改（只有资源管理相关行
   与 import 行的变动）；
5. `git status --porcelain -uall` 恰为白名单 1 个文件，无其他文件。

## 反空转条款（每张卡必带，T01 教训）

- 本卡不新增测试（改的是既有测试的资源管理）；**不得为了让"泄漏实测"好看而删用例或缩短用例**；
- 清理必须挂在 unittest 的正式机制上（`addCleanup` / `TemporaryDirectory` 上下文），
  不得用 `atexit`、模块级全局列表之类的旁门写法。

## 回滚方式

`git checkout -- assets/tests/test_pack.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T04d-assets-测试临时目录清理.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T04d 节）
  - 验收方独立复跑：assets 59 全绿不变；泄漏口径（我跑两遍）`find "$TMPDIR" -maxdepth 1 -type d -name "tmp*"` before=59 after=59 **delta=0**；两处 mkdtemp 均紧随 `addCleanup(shutil.rmtree, ...)`；diff 仅 7 行（import + 2×addCleanup + 注释），断言零改动。
  - **执行方加分项（记下）**：做了对照实验——`git stash` 回旧代码同口径实测 **+18 个泄漏目录**（9 调用 × 2 遍），证明该口径有灵敏度、不是恒零空测。这条测量方法已可复用。
