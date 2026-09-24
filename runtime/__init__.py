# runtime/__init__.py
# 执行器公共接口导出（runtime/AGENTS.md §②：对外只暴露一个接缝）

from .audio import (
    SAMPLE_RATE,
    AudioError,
    apply_fade,
    concat_wavs,
    max_sample_jump,
    read_wav,
    silence,
)
from .duplex import DuplexError, DuplexParams
from .events import EventError, build_event
from .executor import (
    ExecutionResult,
    Executor,
    REASON_AUDIO_FILE_MISSING,
    REASON_ENGINE_MISMATCH,
    REASON_FINGERPRINT_MISMATCH,
    REASON_KEY_NOT_PREBAKED,
    REASON_SAY_LIVE_TEXT,
    RuntimeMissError,
)

__all__ = [
    # 音频工具
    "SAMPLE_RATE",
    "AudioError",
    "read_wav",
    "silence",
    "apply_fade",
    "concat_wavs",
    "max_sample_jump",
    # 双工参数
    "DuplexParams",
    "DuplexError",
    # 事件
    "build_event",
    "EventError",
    # 执行器
    "Executor",
    "ExecutionResult",
    "RuntimeMissError",
    # 未命中/降级原因常量（docs/06 §6.2.7 ②）
    "REASON_KEY_NOT_PREBAKED",
    "REASON_SAY_LIVE_TEXT",
    "REASON_FINGERPRINT_MISMATCH",
    "REASON_AUDIO_FILE_MISSING",
    "REASON_ENGINE_MISMATCH",
]
