"""
adapters.framework_kefu — kefu-agent 框架桥（T11：把预铸包插进真链路）

接缝位置（docs/10 §10.1）：
    用户音频 → ASR → brain → 「这一轮要说什么」 → [本层] → 包内音频(零合成) | 慢路合成

本包只做两件事：
    1. 确定性归一化（normalize.normalize_text，docs/10 §10.3 四步，不许加步）
    2. 命中判定 + 出路选择（bridge.KefuBridge：key 直查 / 归一化逐字相等；
       未命中默认 fail-closed，显式降级必须出 miss/fallback 事件）

KEFU 侧薄客户端（kefu_client.KefuClient）只走 kefu-agent 已有的公开接口：
    brain POST /chat/turn、voice worker 的 stdio 行协议（op=stt / op=tts）。
    不修改 kefu-agent 的任何文件或脚本。

WHY 这个适配器跨了 runtime/ 与 assets/（adapters/AGENTS.md §⑤ 只允许 tts-* 依赖 core/）：
    它是 `framework-*` 那一类——命中判定的口径（docs/10 §10.2）就钉在接缝上，
    必须能同时读资产包、驱动执行器。tts-* / asr-* 适配器仍只依赖 core/。
"""

from runtime import REASON_KEY_NOT_PREBAKED

from .bridge import (
    MATCH_MODE,
    MODE_KEY,
    MODE_TEXT,
    REASON_TEXT_NOT_PREBAKED,
    BridgeError,
    BridgeResult,
    KefuBridge,
)
from .hit_query import (
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
from .kefu_client import (
    KefuBrainError,
    KefuClient,
    KefuError,
    KefuWorkerError,
    extract_reply,
    extract_wav,
)
from .normalize import normalize_text

__all__ = [
    # 接缝
    "KefuBridge",
    "BridgeResult",
    "BridgeError",
    # 命中表示形态与原因码（对外报命中率必须带上游形态）
    "MATCH_MODE",
    "MODE_KEY",
    "MODE_TEXT",
    "REASON_TEXT_NOT_PREBAKED",
    "REASON_KEY_NOT_PREBAKED",
    # 归一化
    "normalize_text",
    # 命中查询公开 API（T29：为内嵌旁路钩子供数）
    "find_hit",
    "HitResult",
    "build_text_index",
    "lookup_key",
    "pick_by_text",
    "confirm",
    "text_for_key",
    # 序列命中（T33，docs/10 §10.7：整段 → 多段逐字覆盖）
    "find_hit_sequence",
    "SequenceHitResult",
    # kefu 侧客户端
    "KefuClient",
    "KefuError",
    "KefuBrainError",
    "KefuWorkerError",
    "extract_reply",
    "extract_wav",
]
