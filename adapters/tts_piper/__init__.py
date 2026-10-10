# adapters/tts_piper/__init__.py
# Piper（rhasspy/piper）本地神经 TTS 薄适配器：导出 PiperTts

from .adapter import PiperTts, TtsError

__all__ = ["PiperTts", "TtsError"]
