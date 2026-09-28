<!--
三段必填。缺一段的 PR 会被退回——不是走过场，是本仓的验收依据（跑批铁律：失败会被产物数量掩盖）。
本 PR 的改动范围与 docs/tasks/ 无强制关联：外部贡献者不需要开任务卡（见 CONTRIBUTING.md）。
-->

## 1. 环境

> 本仓对平台有真实依赖，不给环境就没法判「红」是谁造成的。

- **OS**：`uname -s` →
- **架构**：`uname -m` →
- **Python**：`python3 --version` →
- **有没有 `say`**：`command -v say` →
  （没有 `say` 时 `cli` 根会红，属**已知平台限制**，不是你的改动破坏了东西——详见
  `CONTRIBUTING.md` 的「平台前置条件」。缺 `say` 时改用 `--root <你改的那一层>` 绕开。）
- **有没有 `ffmpeg`**：`command -v ffmpeg` →（模型 TTS 主线需要，做 16 kHz 归一）
- **`VOX_TTS_ENDPOINT` 是否可达**：（不需要写地址，只写「可达 / 不可达 / 没测」）

## 2. 自证

> 把**汇总行原文**贴上来，不要写「我跑过了」。跑批铁律：失败会被产物数量掩盖，
> 所以只要一行汇总不够，也要能看出是哪些根红。

```
$ python3 tools/run_all_tests.py
== 汇总 ==
ran=… skipped=… executed=… failed=… failures=… errors=… roots=11
FAILED roots: …
```

- 退出码：`echo $?` →
- 如果**只跑了单根**（`--root <x>`），在此说明为什么没跑全量：
- 结构预算（`python3 tools/structure_budget/check.py --no-write`）是否仍全绿：

## 3. 冻结区勾选框

> 内核冻结 / 扩展分区（D9）。**业务侧只碰 `packs/`，永不改内核。**

本 PR 改到了以下**冻结区**目录吗？

- [ ] core/
- [ ] rules/
- [ ] compiler/
- [ ] assets/
- [ ] runtime/
- [ ] eval/
- [ ] cli/

- [ ] **没有改到任何冻结区**（改的是 `adapters/` `packs/` `trigger/` `labs/` `tools/` `examples/` `docs/` 等扩展区）

**如果勾了「有」：必须先开 issue 讨论版本化迁移方案，不许直接开 PR。**
勾「有」而没开 issue 的 PR 会被退回。对应登记项见 `docs/13 §五`。

---

## 附：自检

- [ ] 我在测试里**调的是产品 API**，没有在测试里复制被验逻辑（反空转纪律）
- [ ] 未命中 / 未知输入会**抛错或留痕计数**，没有静默降级（fail-closed 红线）
- [ ] 我改的 `packs/` 目录里**没有任何 Python 逻辑**（包是数据不是代码）
- [ ] 我写进对外文档的每个数字都能追到 `eval/` 或 `labs/*/report.json` 的可复现报告
      （测试数只引用 `python3 tools/run_all_tests.py` 的实跑末行，不抄文档）
- [ ] 本 PR **不含**用户录音、真实会话、内部地址、token / 密钥
      （本仓是公开仓。含敏感数据的改动一律走私密渠道，见 `SECURITY.md`）
