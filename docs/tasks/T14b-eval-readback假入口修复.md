# T14b · eval：readback 模块入口缺失（`python3 -m` 静默假成功，微型修复卡）

## 背景（只写必需）

T14 真机验收时发现：`eval/readback.py` 定义了 `run_readback_cli()`，但**没有
`if __name__ == "__main__":` 块**——`python3 -m eval.readback --manifest … --asr …`
会**导入模块后静默退出 rc 0，一行不执行、一个文件不写**。对照：`eval/bench.py:1022` 有这个入口。
这正是本仓 hunted 的「假成功」：命令"跑过了"、退出码 0、什么都没发生。

修法（两行，照 bench.py 同款）：

```python
if __name__ == "__main__":
    sys.exit(run_readback_cli())
```

（`sys` 需按文件既有 import 风格引入；若 `run_readback_cli` 已返回 int，直接作退出码。）

## 允许修改的文件（白名单）

```
允许修改：eval/readback.py（只在文件末尾追加 __main__ 块 + 必要的 import）
允许修改：eval/tests/test_readback.py（只允许新增 1 条：子进程跑
          `python3 -m eval.readback --manifest <不存在路径> …` 必须非 0 退出
          且 stderr 有报错——防入口再次丢失的回归锚点）
禁止触碰：其他一切文件
```

## 验收标准

1. `python3 -m unittest discover -s eval` 全绿（191 → 192，只增）；
2. **负例（我的原探针）**：`python3 -m eval.readback --manifest /tmp/不存在.jsonl --asr x:y --out /tmp/xxx`
   → **退出码非 0** 且 stderr 非空（修前此命令 rc 0 静默无输出）；
3. **入口存在**：`grep -n "__main__" eval/readback.py` ≥ 1 命中；
4. 其余行为零变化：`git diff` 除入口块与新增测试外无改动。

## 回滚方式

`git checkout -- eval/readback.py eval/tests/test_readback.py`。

## 执行方式

**首选**：ZCode 子智能体 **`vox-card-executor`**。**数据分级：公开级**。可派发。

## 卡状态

- [x] 已派发 → [x] 已回收 → [x] **验收通过**（2026-09-19）
  - 验收方独立复跑：eval 192 全绿（191→+1）；**模块级真机闭环修后重跑成功**——`python3 -m eval.readback` 实际执行（修前静默 rc 0），3 条 fin-cs say 合成 → oMLX 回读 → `cer_p50/p99/mean = 0.0`、`incomplete=False`、raw 指纹齐备、rc 0。
  - **边界留档**：回归锚点用 `--asr x:y`（导入失败分支）锚定「入口执行到了」；cwd 反推仓库根在当前树形下成立，挪目录需同步。
