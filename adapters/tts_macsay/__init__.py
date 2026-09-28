# adapters/tts_macsay/__init__.py
# macOS say 命令薄适配器：导出 MacSayTts

from .adapter import MacSayTts, TtsError

__all__ = ["MacSayTts", "TtsError"]
