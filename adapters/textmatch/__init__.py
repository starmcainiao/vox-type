"""adapters.textmatch — 跨适配器共用的文本匹配层（T47 新建，扩展区）

职责：把「归一化 + 逐字命中判定」收敛成**唯一实现**，供 `framework_kefu`（宿主桥的钩子）
      与 `mcp_vox`（MCP 控制面查询）共同消费——同一句话在任何入口的命中结论必须一致。
      公开面只有 `normalize_text` 与 `find_hit` / `find_hit_sequence`（docs/10 §10.3 / §10.7）。

不负责：不做语义分析、不做近似匹配、不驱动播放、不产生事件、不读写任何文件。
      语义放行的唯一松弛是 docs/10 §10.3 冻结的四步归一化，不许加步。

公开面纪律：消费方一律 `from adapters.textmatch import ...`，
      **不得** `from adapters.textmatch.hit import ...` 走模块路径绕过本文件的 `__all__`。
"""

from .hit import (
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
from .normalize import normalize_text

__all__ = [
    # 归一化（docs/10 §10.3 四步）
    "normalize_text",
    # 命中查询（docs/10 §10.7 冻结判据）
    "find_hit",
    "find_hit_sequence",
    "HitResult",
    "SequenceHitResult",
    # 上游表示形态与原因码（报命中率必须带上游形态）
    "MODE_KEY",
    "MODE_TEXT",
    "REASON_TEXT_NOT_PREBAKED",
    # 命中查询的子步骤（消费方需要自建索引 / 自选候选时用）
    "build_text_index",
    "lookup_key",
    "pick_by_text",
    "confirm",
    "text_for_key",
]
