# T17d · adapters + labs：清掉指向已删私名的 5 处说明文字（微型卡）

## 背景（只写必需）

T17c 把 `_rule_matches` 更名为公开 `rule_matches` 并删除了私名。活代码里 import/调用/定义处已零引用，
但有 **5 处说明文字**仍指向已不存在的 `_rule_matches`——这正是本仓 T21e 教训点名的
「说明文字指向一个已经不存在的名字 = 新的假陈述」。其中前 3 处还是本批 T16c 刚写进去的：

1. `adapters/state_trigger/format.py:618` —— 注释「WHY：trigger._rule_matches 对 state 里缺值的字段…」
2. `adapters/state_trigger/tests/test_format.py:582` —— docstring「trigger._rule_matches 对 state 里缺值的字段…」
3. `adapters/state_trigger/tests/test_format.py:709` —— docstring「（涉及 _rule_matches 的语义）」
4. `labs/ticket-source/README.md:101、149` —— 两处方法说明
5. `labs/ticket-source/source_map.json:66` —— `verification` 字段里「用引擎的 _rule_matches」

修法：**只改名字**，`_rule_matches` → `rule_matches`，不改任何句子的其余内容、不改任何代码行为。

## 允许修改的文件（白名单）

```
允许修改：adapters/state_trigger/format.py（仅第 618 行附近那一处注释）
允许修改：adapters/state_trigger/tests/test_format.py（仅 582/709 两处 docstring）
允许修改：labs/ticket-source/README.md（仅 101/149 两处）
允许修改：labs/ticket-source/source_map.json（仅第 66 行 verification 串内的名字）
禁止触碰：其他一切文件——trigger.py / reachability.py / summary.json / 任何逻辑
```

## 禁止事项

- **不改任何可执行代码**：所有 diff 必须在注释 / docstring / JSON 字符串值内
- 不得改 labs 的 summary.json / raw/（它们由 run_e2e.py 重跑再生）
- 不得"顺手"改写句子其余部分

## 验收标准（逐条可判定）

1. `python3 -m unittest discover -s adapters` 全绿，测试数 **284 不变**（改名前基线）；
2. `grep -rn "_rule_matches" --include='*.py' adapters/` → **0 命中**；
3. `grep -n "_rule_matches" labs/ticket-source/README.md labs/ticket-source/source_map.json` → **0 命中**；
4. `python3 labs/ticket-source/run_e2e.py` → **真 rc 0（不接管道量）**，断言条数 148 不变；
   （source_map.json 的 verification 串只改名字，可达性/命中数等被断言字段一字不动）
5. `git diff` 逐行核对：全部改动落在注释 / docstring / JSON 字符串内，无一行可执行代码变更；
6. `git status --porcelain -uall` 恰为白名单 4 个文件（summary.json 若因重跑再生，允许出现）。

## 反空转条款

本卡不新增测试（纯文字修正）；不得为通过验收改断言或期望。

## 回滚方式

`git checkout -- adapters/state_trigger/format.py adapters/state_trigger/tests/test_format.py labs/ticket-source/`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。
**回落**：
```
cd （仓库根）
opencode run -m sense-nova/sensenova-6.8-flash-lite "$(cat docs/tasks/T17d-adapters-清理旧私名引用.md)" --dir （仓库根）
```

**数据分级：本卡为公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19；证据见 `00-索引.md` 第九批 T17d 节）
  - 验收方独立复跑：adapters 284 全绿不变；`._rule_matches` 属性引用全仓 0、labs 两文件 0；labs 重跑（我量 rc）rc 0、148 条不变；diff 逐行核对全在注释/docstring/JSON 字符串。
  - **裁定（判据措辞，我的卡文问题第二次同类）**：验收 2 的子串 grep 字面不可达（`test_rule_matches_*` 方法名含该子串）——按本意「指向已删私名的**属性/引用**残留 = 0」解释，已实测达成；连续两张卡的 grep 判据栽在子串上，记入收尾教训。
