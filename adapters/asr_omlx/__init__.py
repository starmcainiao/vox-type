# adapters/asr_omlx/__init__.py
# oMLX 本地 ASR 适配器：导出 OmlxAsr / AsrError

from .adapter import AsrError, OmlxAsr

__all__ = ["OmlxAsr", "AsrError"]
