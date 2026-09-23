# adapters/tts_omlx/__init__.py
# oMLX 本地神经 TTS 薄适配器（含零样本克隆重铸）：导出 OmlxTts / TtsError

from .adapter import OmlxTts, TtsError

__all__ = ["OmlxTts", "TtsError"]
