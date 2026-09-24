"""
runtime.audio — WAV 读取/拼接/静音垫/淡入淡出/click 判据

职责：把 16kHz/单声道/16-bit 的 PCM 片段读进内存、拼接、加段间静音垫与段级淡入淡出并写盘，
      以及提供 click（爆音）判据 max_sample_jump。
不负责：不做命中判定（executor）、不调用 TTS、不改写资产包。

格式红线（docs/02-protocols-draft / docs/06 §6.1.3）：
    只认 16000 Hz / 单声道 / 16-bit。非此格式一律抛 AudioError——
    不重采样、不静默接受（静默接受 = 下游按 16k 假设计算时长，事后无法定位）。
"""

import sys
import wave
from array import array
from pathlib import Path

# 冻结的音频口径
SAMPLE_RATE = 16000            # 采样率：必须 16000 Hz
CHANNELS = 1                   # 声道数：必须单声道
SAMPLE_WIDTH_BYTES = 2         # 采样位宽：16-bit = 2 字节


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class AudioError(Exception):
    """音频读取/格式/拼接异常。

    消息必须包含导致失败的具体值（采样率/声道/位宽/路径），
    不允许吞掉上下文。
    """


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def read_wav(path):
    """读取 WAV 为 16-bit 小端样本数组。

    参数：
        path: WAV 文件路径

    返回：
        (samples: array('h'), framerate: int)

    异常：
        AudioError: 文件不存在 / 不是合法 WAV / 格式不是 16kHz/单声道/16-bit / 0 样本
    """
    p = Path(path)
    if not p.is_file():
        raise AudioError(f"音频文件不存在: {p}")

    try:
        with wave.open(str(p), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioError(f"不是合法的 WAV 文件: {p} ({exc})") from exc

    # 格式校验：任一项不符就抛错，禁止重采样与静默接受
    if (channels, sampwidth, framerate) != (
        CHANNELS,
        SAMPLE_WIDTH_BYTES,
        SAMPLE_RATE,
    ):
        raise AudioError(
            f"音频格式不符（要求 {SAMPLE_RATE}Hz/单声道/16-bit，实际 "
            f"{framerate}Hz/{channels}声道/{sampwidth * 8}-bit）: {p}——"
            f"禁止重采样与静默接受"
        )

    # 数据长度必须是 2 字节样本的整数倍，否则 frombytes 会以 ValueError 泄漏
    if len(raw) % (SAMPLE_WIDTH_BYTES * CHANNELS) != 0:
        raise AudioError(f"音频数据长度不合法（{len(raw)} 字节）: {p}")

    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":      # WAV 是小端；大端平台需要换字节序
        samples.byteswap()

    if len(samples) == 0:
        raise AudioError(f"音频内容为空（0 样本）: {p}")

    return samples, framerate


# ---------------------------------------------------------------------------
# 静音垫与淡入淡出
# ---------------------------------------------------------------------------
def silence(ms: int, framerate: int) -> array:
    """生成 ms 长的静音片段（全零 16-bit 样本）。

    用于单元之间的句间静音垫（silence_pad_ms）与槽值前后微停顿（slot_pad_ms）。

    异常：
        AudioError: framerate 不是 16000 / ms 为负
    """
    if framerate != SAMPLE_RATE:
        raise AudioError(
            f"framerate 必须是 {SAMPLE_RATE}，实际为 {framerate}——禁止重采样"
        )
    if ms < 0:
        raise AudioError(f"ms 必须 >= 0，实际为 {ms!r}")
    return array("h", [0]) * int(round(ms * framerate / 1000))


def apply_fade(samples: array, fade_ms: int, framerate: int) -> array:
    """在片段首尾各施加一段线性淡入/淡出，返回新数组（不改入参）。

    淡入：前 fade 个样本乘以 i/fade（0 → 1）
    淡出：后 fade 个样本从尾部往回乘以 i/fade（0 → 1）

    参数：
        samples:   输入样本（array('h')）
        fade_ms:   淡入淡出时长（毫秒），0 表示关闭
        framerate: 采样率，必须 16000

    返回：
        施加淡入淡出后的新数组（长度不变）

    异常：
        AudioError: framerate 不是 16000 / fade_ms 为负
    """
    if framerate != SAMPLE_RATE:
        raise AudioError(
            f"framerate 必须是 {SAMPLE_RATE}，实际为 {framerate}——禁止重采样"
        )
    if fade_ms < 0:
        raise AudioError(f"fade_ms 必须 >= 0，实际为 {fade_ms!r}")

    out = array("h", samples)          # WHY：不改入参，拼接时同一片段可能被多次引用
    n = len(out)
    fade = int(round(fade_ms * framerate / 1000))

    # 片段比淡入淡出还短时，把淡入淡出压到 n//2，避免首尾重叠互相覆盖
    fade = min(fade, n // 2)
    if n == 0 or fade <= 0:
        return out

    for i in range(fade):
        # WHY：系数用 i/fade 而不是 (i+1)/fade——必须让首样本与尾样本落到 0，
        #      否则拼接边界那一次跳变丝毫没被消除，click 判据仍然会爆。
        gain = i / fade
        out[i] = int(round(out[i] * gain))
        out[n - 1 - i] = int(round(out[n - 1 - i] * gain))
    return out


# ---------------------------------------------------------------------------
# 拼接与写盘
# ---------------------------------------------------------------------------
def concat_wavs(segments, path, *, framerate: int, fade_ms: int) -> Path:
    """按顺序拼接片段并写盘（16kHz/单声道/16-bit）。

    参数：
        segments:  片段列表，每项为 array('h')（音频片段或 silence() 产出的静音垫）
        path:      输出 WAV 路径（父目录不存在时自动创建）
        framerate: 采样率，必须 16000
        fade_ms:   段级淡入淡出时长（毫秒），对每段音频施 0 = 关闭

    返回：
        输出路径（Path）

    异常：
        AudioError: framerate 不是 16000 / segments 为空 / 片段类型不对
    """
    if framerate != SAMPLE_RATE:
        raise AudioError(
            f"framerate 必须是 {SAMPLE_RATE}，实际为 {framerate}——禁止重采样"
        )
    if not segments:
        raise AudioError("segments 不能为空列表——禁止产出空音频")

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    merged = array("h")
    for seg in segments:
        if not isinstance(seg, array) or seg.typecode != "h":
            raise AudioError(
                f"片段必须是 array('h')，实际为 {type(seg).__name__}"
            )
        # WHY：全零片段（静音垫）跳过淡入淡出——一是数学上本就是恒等操作，
        #      二是让静音垫的样本数精确等于 pad 时长，下游按样本数核对
        #      "段间确有 silence_pad_ms 静音" 时才有确定的锚点。
        if fade_ms > 0 and len(seg) > 0 and max(abs(s) for s in seg) > 0:
            seg = apply_fade(seg, fade_ms, framerate)
        merged.extend(seg)

    try:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH_BYTES)
            wf.setframerate(framerate)
            wf.writeframes(merged.tobytes())
    except (wave.Error, OSError) as exc:
        raise AudioError(f"写 WAV 失败: {out_path} ({exc})") from exc

    return out_path


# ---------------------------------------------------------------------------
# click 判据
# ---------------------------------------------------------------------------
def max_sample_jump(samples: array) -> int:
    """相邻样本的最大绝对差——拼接 click（爆音）判据。

    拼接处若没有淡入淡出，电平跳变会集中成一次大跳；阈值由测试/eval 给定，
    本函数只给原始量，不内建阈值（避免把判据钉死在实现里）。

    参数：
        samples: 样本数组（array('h')）

    返回：
        最大绝对差（0 样本或 1 样本时返回 0）
    """
    peak = 0
    prev = None
    for s in samples:
        if prev is not None:
            d = s - prev
            if d < 0:
                d = -d
            if d > peak:
                peak = d
        prev = s
    return peak
