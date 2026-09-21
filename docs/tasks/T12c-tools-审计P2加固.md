# T12c · tools：T12 审计 P2 批量加固（6 条小修，依赖 T12b 验收通过）

## 背景（只写必需）

同一次 `blackiron-silent-failure-hunter` 审计的 **6 条 P2**（T12b 修阻塞与 P1，本卡收 P2）。
验收方已逐条读码核实：

1. **golden JSONL 半截写**（`derive_golden.py`）：中断/盘满留下半截 JSONL，下游按存活行计数
   静默变小。修法：写盘走 tmp + rename（照 `fetch.py::write_lock` 同款原子模式）。
2. **variant 归一化碰撞静默遮蔽**（`baseline_literal.py`）：两个不同 variant 归一化后相同，
   `setdefault` 让后者静默丢失、命中归属错 key。修法：碰撞即抛错（消息含 key 与两条原 variant）。
3. **4xx 也重试**（`fetch.py::http_get`）：`HTTPError` 是 `URLError` 子类，404/403 白等 2s+4s。
   修法：`except` 分支里单列 `urllib.error.HTTPError` → 不重试直接归入 FetchError
   （消息保留状态码）。
4. **非 UTF-8 README 用替换符顶上**（`fetch.py::http_get` 文本分支 `errors="replace"`）：
   乱码正文让许可关键词扫描空转"通过"。修法：文本分支 `decode("utf-8")` 严格解码，
   `UnicodeDecodeError` → `FetchError`（消息含 URL；审计正文必须可读）。`binary=True` 路径不动。
5. **`release_lock` 是 no-op**（`fetch.py`）：flock 按 open-file-description 归属，
   新开 fd 上的 `LOCK_UN` 什么也没释放却恒"成功"。修法：**删除该函数**；锁随进程退出自动释放，
   这句写进 `tools/README.md` 的并发口径段（T12b 已加的那段）。若测试引用了它，同步删测试。
6. **缺 `expect_license` 时核对④无声跳过**（`fetch.py::audit_license`）：事后无法区分
   「核对通过」与「根本没核对」。修法：缺声明时向 `audit_checks` 追加一条
   `"④ expect_license 未声明，跳过核对"` 记录（行为仍是跳过，但要留痕）。
7. **〔T12b 验收时补）审计侧 revision 缺省行为缺 fixture**（`fetch.py::audit_license`）：
   T12b 把审计侧缺省 revision 从 master 统一到 `DEFAULT_REVISION`（"main"），但没有离线
   fixture 钉住「无 revision 声明 + master/main README 不同」时审计读 main 的行为。
   修法：加一条离线用例（假平台 meta/README），断言审计取的是 DEFAULT_REVISION 一侧。

## 允许修改的文件（白名单）

```
允许修改：tools/corpus_fetch/fetch.py
允许修改：tools/corpus_fetch/derive_golden.py
允许修改：tools/corpus_fetch/baseline_literal.py
允许修改：tools/tests/（既有用例引用 release_lock 的可同步删改；其余不得删改既有断言）
允许修改：tools/README.md（只允许在并发口径段补锁随进程释放一句）
禁止触碰：其他一切文件——labs/、packs/、docs/、其他层
```

## 禁止事项

- 不得放宽任何既有校验；不得改台账 JSON 结构；不得新增第三方依赖
- 不得动 T12b 刚修的五处语义（select_files 逐项核对 / 临界区读改写 / 坏台账硬失败 /
  未知策略非零退出 / DEFAULT_REVISION）
- 不得"顺手优化"

## 验收标准（逐条可判定，验收方会逐条核对）

1. `python3 -m unittest discover -s tools/tests` 全绿，测试数**只增不减**；
2. **第 1 条**：测试注入「写 golden 中途抛错」→ 盘上**不留下**半截 JSONL（目标文件要么不存在要么完整）；
3. **第 2 条**：构造归一化后相同的两条 variant → 抛错且消息含 key 与两条原文；
4. **第 3 条**：假响应 404 → 抛 `FetchError` 且**不经历重试等待**（测试可断言调用次数 == 1）；
5. **第 4 条**：假响应返回非 UTF-8 字节（binary 路径拿 bytes）→ 文本模式抛 `FetchError` 含 URL；
6. **第 5 条**：`grep -n "release_lock" tools/` → 0 命中（函数与引用全清）；
7. **第 6 条**：缺 `expect_license` 的来源审计记录里含「④…跳过」字符串；
7b. **第 7 条**：无 revision 声明的来源，审计取的是 `DEFAULT_REVISION` 一侧（离线 fixture 断言，钉住 T12b 修 5 的审计侧行为）；
8. **回归三数字**：recheck 0 异常（仓外副本或 API，与 T12/T12b 同口径）/ baseline `0/2433=0.00%` / cross_check `1.8% / 0.2%`；
9. `git status --porcelain -uall` 恰为白名单文件。

## 反空转条款（每张卡必带，T01 教训）

- 新增测试必须调用产品 API；负例断言消息含具体非法值（URL / key / 状态码）；
- 不得为通过测试而放宽校验（尤其第 4 条——不许改回 `errors="replace"`）。

## 回滚方式

`git checkout -- tools/corpus_fetch/ tools/tests/ tools/README.md`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T12c-tools-审计P2加固.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T12c 节）
  - 验收方独立复跑：tools 75 全绿（62→+13 净增）；`grep release_lock tools/` = 0；回归（我跑）baseline 0/2433=0.00%、recheck 0 异常；T12b 五处语义零 diff。
  - **裁定（接受）**：`tools/corpus_fetch/__init__.py` 不在白名单逐文件列表——但它是删 `release_lock` 的必然伴随（导出面清理），不清则验收 6（0 命中）不可达，判授权伴随改动；仅删 import 与 `__all__` 两行。
  - **裁定（接受）**：验收 2 注错走 `dg.json.dumps`（写循环的真实依赖路径），非替身替换被测函数——覆盖真实路径，正当。`measure` 内部 index 结构变更为碰撞检测必然，输出 JSON 结构未变。
