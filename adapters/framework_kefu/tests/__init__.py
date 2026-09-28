"""
adapters.framework_kefu.tests — 测试夹具（不含任何命中判定/归一化逻辑）

这里只有三样东西，全部是"搭台"的，不是产品逻辑：
    build_pack(root, phrases)   在临时目录造一个 assets/ 格式的资产包（真音频文件）
    write_wav(path, peak, ms)   写 16kHz/单声道/16-bit WAV，波形参数可控
    CountingTts                 可计数的假慢路合成器（真测试里绝不调 say 或网络）
    FakeClient                  假 brain/ASR（返回预设文本，记录被调用情况）

WHY 自己造包而不直接用 packs/repair：
    1) packs/ 是业务目录，测试不该依赖它的真实话术（含真实业务文案，也不该被测试改）；
    2) 需要精确控制音频峰值与时长，才能断言"播的是包内音频还是慢路音频"。
指纹与包校验一律调 assets/ 的公开 API（fingerprint / load_pack），不复制算法。
"""

import json
import struct
import wave
from pathlib import Path

from assets import fingerprint, load_pack

# 夹具的固定身份：与真实引擎对齐（voice 是 macOS say 的音色名）
VOICE = "Tingting"
MODEL = "macos-say"
RATE = "normal"


def write_wav(path, *, peak=8000, ms=200, step=1):
    """写一段 16kHz/单声道/16-bit 的锯齿波。

    WHY 参数可控：包内资产与假慢路合成器要用**明显不同**的峰值和时长，
        才能断言"引擎不一致时没有播包内音频"（用峰值或时长区分，见验收第 6 条）。

    参数：
        path: 输出路径（父目录自动创建）
        peak: 波峰绝对值（越大峰值越高）
        ms:   时长（毫秒）
        step: 锯齿步长（决定波形形状，进一步区分来源）
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(ms * 16000 / 1000))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        for i in range(n):
            v = int(round(peak * ((i * step) % 1000) / 1000.0 - peak / 2.0))
            wf.writeframes(struct.pack("<h", max(-32768, min(32767, v))))
    return path


def build_pack(root, phrases, *, voice=VOICE, model_version=MODEL,
               rate=RATE, peak=8000, ms=200):
    """在 root 造一个可被 assets.load_pack 装载的资产包。

    参数：
        root:    包根目录（父目录自动创建）
        phrases: [(key, text, variant)] —— variant 是整数，part_index 恒 0
        voice / model_version / rate / peak / ms: 包级身份与音频波形参数

    返回：
        assets.AssetPack（已校验通过）

    异常：
        assets.AssetPackError: 构造出的包不合法（不该发生；发生说明夹具写错了）
    """
    root = Path(root)
    audio_dir = root / "audio"

    assets = []
    for key, text, variant in phrases:
        fp = fingerprint(text=text, voice=voice, rate_value=rate,
                         model_version=model_version)
        write_wav(audio_dir / f"{fp}.wav", peak=peak, ms=ms, step=1)
        assets.append({
            "key": key,
            "part_index": 0,
            "rate_key": rate,
            "variant": variant,
            "text": text,
            "fingerprint": fp,
            "path": f"audio/{fp}.wav",
            "duration_ms": ms,
        })

    manifest = {
        "pack_id": "kefu-bridge-test",
        "pack_version": "1",
        "protocol_version": "0.1",
        "ruleset_version": "v1",
        "voice": voice,
        "model_version": model_version,
        "created_at": "2026-09-18T00:00:00Z",
        "assets": assets,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load_pack(root)


class CountingTts:
    """可计数的假慢路合成器（runtime 适配器契约：synthesize / voice / model_version）。

    WHY 必须是假的：真实慢路要调 say 或 kefu worker，会联网/起子进程，
        既慢又不可复现；验收第 3/4/5 条全靠 calls 计数来断言"命中零调用""未调用"。
    WHY 默认波形与包内不同（peak=333 / ms=333）：让"播的是慢路音频"可被断言。
    """

    name = "fake-live"
    model_version = MODEL

    def __init__(self, *, voice=VOICE, model_version=MODEL, peak=333, ms=333):
        self.voice = voice
        self.model_version = model_version
        self.peak = peak
        self.ms = ms
        self.calls = []            # [{text, rate}]，每次成功合成追加一条

    def synthesize(self, text, out_path, rate_key="normal"):
        """落盘一段慢路音频，并记录一次调用。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"假慢路合成器拒绝空文本: {text!r}")
        self.calls.append({"text": text, "rate": rate_key})
        write_wav(out_path, peak=self.peak, ms=self.ms, step=7)


class FakeClient:
    """假 brain/ASR：ask_brain 按顺序吐预设回复，transcribe 返回固定文本。

    测试用它替换真实 kefu 上游——真链路只在验收第 7 条（人工联调）出现。
    """

    def __init__(self, replies=None, heard=""):
        self.replies = list(replies or [])
        self.heard = heard
        self.asks = []             # [(session_id, text)]
        self.waves = []            # 每次 transcribe 收到的字节数

    def ask_brain(self, session_id, text):
        self.asks.append((session_id, text))
        if not self.replies:
            raise AssertionError(
                f"FakeClient 没有剩余回复可吐（已用 {len(self.asks)} 次）"
            )
        return self.replies.pop(0)

    def transcribe(self, wav_bytes):
        self.waves.append(len(wav_bytes))
        return self.heard
