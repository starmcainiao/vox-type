"""
adapters.framework_kefu.hit_query — **re-export 薄壳**（T47：实现已迁入 adapters/textmatch）

职责：**只做转发**。命中判定的唯一实现是 `adapters.textmatch.hit`
      （`find_hit` / `find_hit_sequence` 及其子步骤，docs/10 §10.2 裁定 1 + §10.7），
      本文件不再持有任何实现——全仓对这两个函数的**定义**只允许出现在
      `adapters/textmatch/` 下（T47 验收第 2 条用全仓唯一性 grep 卡这一点：
      本壳若写出函数体，那条 grep 就会出现第二处命中）。

保留原因：既有 import 路径不断。`framework_kefu/__init__.py`、`bridge.py`（真源注释：
      mode / reason 常量下沉到本模块）与大量测试都从这里导入；常量同名断言
      （`test_hit_query.py::test_constants_are_single_source` 的 `assertIs`）要求转发出去的
      是**同一批对象**，因此必须原样 re-export。

WHY 转发私有名 `_sequence_candidates`：既有白盒测试从这里取它做候选索引断言；
      它是**本壳的历史接口的一部分**，不是本壳新增的实现——实现仍在 textmatch 一侧。
      新代码禁止依赖它（对外公开面只有 `find_hit` / `find_hit_sequence` 等，见
      `adapters/textmatch/__init__.py` 的 `__all__`）。

不负责：不实现命中判定、不改 docs/10 §10.7 的算法判据。要改判定语义请改
      `adapters/textmatch/hit.py`（并按冻结区流程走）。
"""

from adapters.textmatch import (
    MODE_KEY,
    MODE_TEXT,
    REASON_TEXT_NOT_PREBAKED,
    HitResult,
    SequenceHitResult,
    build_text_index,
    confirm,
    find_hit,
    find_hit_sequence,
    lookup_key,
    pick_by_text,
    text_for_key,
)
from adapters.textmatch.hit import _sequence_candidates

__all__ = [
    "MODE_KEY",
    "MODE_TEXT",
    "REASON_TEXT_NOT_PREBAKED",
    "HitResult",
    "SequenceHitResult",
    "build_text_index",
    "lookup_key",
    "pick_by_text",
    "confirm",
    "text_for_key",
    "find_hit",
    "find_hit_sequence",
]
