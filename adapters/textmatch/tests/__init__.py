"""
adapters.textmatch.tests — 测试夹具（只搭台，不含任何归一化/命中逻辑）

夹具与产品代码同源但独立：指纹与包校验一律调 `assets` 的公开 API，不复制算法。
WHY 不复用 `adapters.framework_kefu.tests.build_pack`：那是 kefu 桥自己的夹具，
    本层的测试不应跨适配器 import（`adapters/AGENTS.md §⑤` 的精神）。
"""

import json
import struct
import wave
from pathlib import Path

from assets import fingerprint, load_pack

VOICE = "Tingting"
MODEL = "macos-say"
RATE = "normal"


def write_wav(path, *, peak=8000, ms=200):
    """写一段 16kHz/单声道/16-bit 锯齿波（真 WAV，能过指纹/存在性校验）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(ms * 16000 / 1000))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        for i in range(n):
            v = int(round(peak * ((i * 1) % 1000) / 1000.0 - peak / 2.0))
            wf.writeframes(struct.pack("<h", max(-32768, min(32767, v))))
    return path


def build_pack(root, phrases, *, voice=VOICE, model_version=MODEL, rate=RATE):
    """在 root 造一个可被 assets.load_pack 装载的资产包。

    参数：
        root:    包根目录（父目录自动创建）
        phrases: [(key, text, variant)] —— part_index 恒 0

    返回：
        assets.AssetPack（已校验通过）
    """
    root = Path(root)
    audio_dir = root / "audio"

    entries = []
    for key, text, variant in phrases:
        fp = fingerprint(text=text, voice=voice, rate_value=rate,
                         model_version=model_version)
        write_wav(audio_dir / f"{fp}.wav", peak=8000, ms=200)
        entries.append({
            "key": key,
            "part_index": 0,
            "rate_key": rate,
            "variant": variant,
            "text": text,
            "fingerprint": fp,
            "path": f"audio/{fp}.wav",
            "duration_ms": 200,
        })

    manifest = {
        "pack_id": "textmatch-test",
        "pack_version": "1",
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": voice,
        "model_version": model_version,
        "created_at": "2026-09-28T00:00:00Z",
        "assets": entries,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load_pack(root)
