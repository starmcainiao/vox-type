# 贡献指南（Contributing）

感谢关注 vox-type。当前处于**设计讨论 + 机制稳定**期，欢迎两类贡献：

## 提 Issue（最欢迎）
- 质疑协议与边界：plan 契约、准入判据（`docs/14`）、命中口径（`docs/10`）——负结果与反例同样欢迎；
- 报告复现问题：给出环境（Python 版本/OS）与完整命令输出。

## 提 PR 前

**外部贡献者不需要开任务卡**：`docs/tasks/` 里的 60+ 张卡（`T43`、`T55`、`T63`…）是维护者内部的
派发与验收机制，**不是外部贡献者的义务**，也不应该为一次贡献新建一张卡。
提 PR 直接走下面 8 条 + 仓库根 `AGENTS.md`，卡会由维护者补。

1. **先跑全量测试**：`python3 tools/run_all_tests.py`——全绿（`exit=0`）是底线。
   它逐根跑十一个测试根（core rules assets adapters compiler runtime eval cli tools trigger packs），
   末行打印**分项**汇总 `ran=… skipped=… executed=… failed=0`，
   任一根红则**非零退出并点名失败根**（跑批铁律：失败会被产物数量掩盖）。
   **不要再手抄逐根 `unittest discover` 的 shell 循环**：那份循环的唯一副本在
   `tools/run_all_tests.py`，CI 与本文档都调它（T48）；数字口径见 `tools/README.md`。
   只想改一处、不想等全量时，**只跑那一根**：

   ```sh
   python3 tools/run_all_tests.py --root cli        # 单根（本机约 30 秒量级）
   python3 tools/run_all_tests.py --root cli --root tools   # 指定多根
   python3 tools/run_all_tests.py                   # 全量十一个根（本机约 1 分钟量级）
   ```

   时间量级随机器与负载漂移，**不是承诺值**；要精确数字请自己跑。
   但 PR 合入前**必须**跑一次全量。
2. **平台前置条件（`cli` 根是平台相关的，别被它误导）**：`cli` 根的测试会走
   macOS 自带的 `say`（缺省适配器 `adapters.tts_macsay:MacSayTts`，见
   `cli/commands/__init__.py` 的 `DEFAULT_ADAPTER_SPEC`）。所以：

   - **macOS**：`cli` 根全绿（本机 2026-09-28 实跑 `ran=89 skipped=0 failed=0`）。
   - **Linux / CI**：`cli` 根在缺 `say` 的环境下会**红**——T64 卡实测模拟无 `say` 环境时
     `cli` 根 `ran 89→26、failed 22`，其余十个根不受影响。这是**已知平台限制，不是你的改动破坏了东西**。
     CI 因此固定在 `runs-on: macos-latest`（T49 有意不动它；解它要动缺省适配器即冻结区，见
     `docs/13 §五#27`）。
   - 在 Linux 上验自己的改动时，改用 `--root <你改的那一层>` 绕开 `cli` 根，
     或先跑 `sh examples/selftest.sh` 看零引擎路径。

   **本仓当前不宣称「全平台全绿」**——`skipped=0` 与「全绿」都只对**本机**成立。
3. **测试必须调用产品 API**（不得在测试内复制被验逻辑——反空转纪律）；
4. **fail-closed 红线**：未命中/未知输入必须抛错或留痕计数，禁止静默降级；
5. **包是数据不是代码**：`packs/` 内禁止任何 Python 逻辑；
6. **数字必须有出处**：对外口径引用可复现报告（`docs/08` CLI 口径、`docs/09` 对拍口径、
   `labs/*/report.json`）；测试数只引用 `python3 tools/run_all_tests.py` 的实跑末行，不抄文档；
7. **动冻结区**（core/rules/compiler/assets/runtime/eval/cli）需要先开 issue 说明版本化迁移方案（D9）；
8. 方法级中文注释（职责/参数/返回值/边界）+ 关键 WHY 注释。

> PR 提交时请填 `.github/PULL_REQUEST_TEMPLATE.md` 的三段：**环境**（OS / Python 版本 / 有没有 `say`）、
> **自证**（`python3 tools/run_all_tests.py` 的**汇总行原文**贴上来，不是「我跑过了」）、
> **冻结区勾选框**（是否改到 core/ rules/ compiler/ assets/ runtime/ eval/ cli/ ——
> 勾了「是」就必须先开 issue，不许直接开 PR）。

## 层边界速查

| 目录 | 冻结状态 | 你能改什么 |
|---|---|---|
| core/ rules/ compiler/ assets/ runtime/ eval/ cli/ | 冻结 | 需版本化迁移（先开 issue） |
| adapters/ packs/ trigger/ | 扩展 | 自由新增；新增业务包 = 新增目录 |
| labs/ | 实验区 | 一次性实验，产物自带口径与原始数据 |

详见仓库根 [AGENTS.md](AGENTS.md)。

## 安全

安全漏洞**不要**开公开 issue，走 [SECURITY.md](SECURITY.md) 的私密渠道。
