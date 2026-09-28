"""
adapters.framework_kefu.normalize — **re-export 薄壳**（T47：实现已迁入 adapters/textmatch）

职责：**只做转发**。归一化的唯一实现是 `adapters.textmatch.normalize.normalize_text`
      （docs/10 §10.3 四步），本文件不再持有任何实现——全仓对这个函数的**定义**
      只允许出现在 `adapters/textmatch/` 下（T47 验收第 2 条用全仓唯一性 grep 卡这一点：
      本壳若写出函数体，那条 grep 就会出现第二处命中）。

保留原因：既有 import 路径不断。大量消费方与测试从 `adapters.framework_kefu.normalize`
      （以及包根 `adapters.framework_kefu`）导入这个符号，删掉即破坏它们；
      同源断言（`tools/tests/test_feedback_mining.py` 的 `assertIs`）要求转发出去的是
      **同一个函数对象**，因此这里必须原样 re-export，不能包一层新函数。

不负责：不实现归一化、不加步、不加语义松弛。新增归一化行为请改
      `adapters/textmatch/normalize.py`（四步语义冻结，docs/10 §10.3）。
"""

from adapters.textmatch import normalize_text

__all__ = ["normalize_text"]
