# 贡献指南（Contributing）

感谢关注 vox-type。当前处于**设计讨论 + 机制稳定**期，欢迎两类贡献：

## 提 Issue（最欢迎）
- 质疑协议与边界：plan 契约、准入判据（`docs/14`）、命中口径（`docs/10`）——负结果与反例同样欢迎；
- 报告复现问题：给出环境（Python 版本/OS）与完整命令输出。

## 提 PR 前
1. **先跑全量测试**：`for d in core rules assets adapters compiler runtime eval cli tools trigger packs; do python3 -m unittest discover -s $d; done`——全绿是底线；
2. **测试必须调用产品 API**（不得在测试内复制被验逻辑——反空转纪律）；
3. **fail-closed 红线**：未命中/未知输入必须抛错或留痕计数，禁止静默降级；
4. **包是数据不是代码**：`packs/` 内禁止任何 Python 逻辑；
5. **数字必须有出处**：对外口径引用可复现报告（`docs/08` CLI 口径、`docs/09` 对拍口径）；
6. **动冻结区**（core/rules/compiler/assets/runtime/eval/cli）需要先开 issue 说明版本化迁移方案（D9）；
7. 方法级中文注释（职责/参数/返回值/边界）+ 关键 WHY 注释。

## 层边界速查

| 目录 | 冻结状态 | 你能改什么 |
|---|---|---|
| core/ rules/ compiler/ assets/ runtime/ eval/ cli/ | 冻结 | 需版本化迁移（先开 issue） |
| adapters/ packs/ trigger/ | 扩展 | 自由新增；新增业务包 = 新增目录 |
| labs/ | 实验区 | 一次性实验，产物自带口径与原始数据 |

详见仓库根 [AGENTS.md](AGENTS.md)。
