"""
compiler.quality — 质检门（纯函数，不依赖 TTS）

职责：读 WAV → 量出 AudioStats → 按 docs/06 §6.1.3 的判据逐条判定，返回问题列表。
      质检门是预铸流水线的第二道闸门：合成成功不等于资产合格，削波/头尾静音异常/
      响度越带的字模一律拒收，避免坏件进包后被运行时静默播出。
不负责：不做音频合成（adapters/）、不做差量调度（prebake.py）、不做 LUFS 归一（明确推迟）。

实现约束：只用标准库 wave + array（Python 3.14 已移除 audioop，不得依赖）。
"""

import array
import math
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

# ---------------------------------------------------------------------------
# 判据常量（docs/06 §6.1.3 的表——数值照抄，不得自创或改动）
# ---------------------------------------------------------------------------
SAMPLE_RATE: int = 16000          # 采样率：必须 16000 Hz
CHANNELS: int = 1                # 声道数：必须 1（单声道）
SAMPWIDTH: int = 2               # 位深：必须 2 字节（16-bit）
MAX_DURATION_MS: int = 30000     # 时长上限：> 30s 说明没按「一句」拆
MAX_HEAD_SILENCE_MS: float = 100.0   # 头静音上限
MAX_TAIL_SILENCE_MS: float = 150.0   # 尾静音上限
PEAK_MIN_DBFS: float = -20.0      # 峰值带下限
PEAK_MAX_DBFS: float = -1.0       # 峰值带上限
SILENCE_THRESHOLD: int = 328     # 静音判据：|s| <= 328 视为静音（约 -40 dBFS）
CLIP_THRESHOLD: int = 32767      # 削波判据：|s| >= 32767 计为满幅样本
FULL_SCALE: int = 32768          # 16-bit 满幅参考值（dBFS 换算用）


class QualityError(Exception):
    """质检度量异常——非 WAV / 打不开文件等无法度量的情况。

    消息必须包含文件路径与原因，以便定位是哪个字模出了问题。
    """


@dataclass(frozen=True)
class AudioStats:
    """一段 WAV 的可测属性（质检门的输入）。

    属性：
        path:              WAV 文件路径（字符串）
        frames:            帧数（采样点数）
        channels:          声道数
        sampwidth:         采样位宽（字节）
        framerate:         采样率（Hz）
        duration_ms:       时长（毫秒，整数）
        head_silence_ms:   头静音长度（毫秒）
        tail_silence_ms:   尾静音长度（毫秒）
        peak_dbfs:         峰值电平（dBFS，全静音时为 -inf）
        clipped_samples:   满幅样本数（|s| >= 32767）
    """
    path: str
    frames: int
    channels: int
    sampwidth: int
    framerate: int
    duration_ms: int
    head_silence_ms: float
    tail_silence_ms: float
    peak_dbfs: float
    clipped_samples: int


def measure(path: Union[str, Path]) -> AudioStats:
    """读 WAV 并量出 AudioStats。

    参数：
        path: WAV 文件路径

    返回：
        度量结果

    异常：
        QualityError: 非 WAV / 打不开（消息含路径与原因）
    """
    p = Path(path)

    try:
        wf = wave.open(str(p), "rb")
    except (wave.Error, OSError) as e:
        # wave.open 对非 WAV 会抛 wave.Error('unknown format')，缺文件抛 FileNotFoundError
        raise QualityError(f"无法读取 WAV 文件 {p}: {e}") from e

    with wf:
        frames = wf.getnframes()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(frames)

    # 解析样本：16-bit 有符号小端（macOS say 的 LEI16）。
    # 注意 array 按本机字节序解释，大端机器需要 byteswap。
    samples = array.array("h")
    if raw:
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
        if sys.byteorder != "little":
            samples.byteswap()

    # 时长（毫秒）：frames 是采样点数，除以采样率得秒
    duration_ms = int(round(frames / framerate * 1000)) if framerate > 0 else 0

    # 头/尾静音：从两端向内扫描，|s| <= 328（约 -40 dBFS）计为静音
    head = 0
    for s in samples:
        if abs(s) <= SILENCE_THRESHOLD:
            head += 1
        else:
            break
    tail = 0
    for s in reversed(samples):
        if abs(s) <= SILENCE_THRESHOLD:
            tail += 1
        else:
            break

    head_silence_ms = head / framerate * 1000 if framerate > 0 else 0.0
    tail_silence_ms = tail / framerate * 1000 if framerate > 0 else 0.0

    # 峰值电平：dBFS = 20 * log10(peak / 32768)；全静音时无峰值 → -inf
    peak_abs = max((abs(s) for s in samples), default=0)
    if peak_abs == 0:
        peak_dbfs = float("-inf")
    else:
        peak_dbfs = 20.0 * math.log10(peak_abs / FULL_SCALE)

    # 削波：满幅样本计数
    clipped = sum(1 for s in samples if abs(s) >= CLIP_THRESHOLD)

    return AudioStats(
        path=str(p),
        frames=frames,
        channels=channels,
        sampwidth=sampwidth,
        framerate=framerate,
        duration_ms=duration_ms,
        head_silence_ms=head_silence_ms,
        tail_silence_ms=tail_silence_ms,
        peak_dbfs=peak_dbfs,
        clipped_samples=clipped,
    )


def check_quality(stats: AudioStats) -> List[str]:
    """按 docs/06 §6.1.3 的判据逐条判定，返回问题列表（空列表 = 通过）。

    每条问题含判据名与实际值，便于业务侧直接看出「哪里超了、超多少」。

    参数：
        stats: measure() 产出的度量结果

    返回：
        问题列表（空列表表示全部判据通过）
    """
    issues: List[str] = []

    # 1. 采样率 / 声道 / 位深：资产层格式冻结，必须精确匹配
    if stats.framerate != SAMPLE_RATE:
        issues.append(
            f"采样率不符: 判据={SAMPLE_RATE} Hz, 实际={stats.framerate} Hz"
        )
    if stats.channels != CHANNELS:
        issues.append(
            f"声道数不符: 判据={CHANNELS}（单声道）, 实际={stats.channels}"
        )
    if stats.sampwidth != SAMPWIDTH:
        issues.append(
            f"位深不符: 判据={SAMPWIDTH} 字节(16-bit), 实际={stats.sampwidth} 字节"
        )

    # 2. 帧数与时长：时长 > 30s 说明没按「一句」拆
    if stats.frames <= 0:
        issues.append(f"帧数不合法: 判据=>0, 实际={stats.frames}")
    elif stats.duration_ms > MAX_DURATION_MS:
        issues.append(
            f"时长超限: 判据=<= {MAX_DURATION_MS} ms, 实际={stats.duration_ms} ms"
        )

    # 3. 头静音：超阈 → 拒（实测最大 16.2ms，阈值留 6 倍余量）
    if stats.head_silence_ms > MAX_HEAD_SILENCE_MS:
        issues.append(
            f"头静音超限: 判据=<= {MAX_HEAD_SILENCE_MS:.0f} ms, "
            f"实际={stats.head_silence_ms:.1f} ms"
        )

    # 4. 尾静音：超阈 → 拒（实测最大 31.2ms，阈值留约 5 倍余量）
    if stats.tail_silence_ms > MAX_TAIL_SILENCE_MS:
        issues.append(
            f"尾静音超限: 判据=<= {MAX_TAIL_SILENCE_MS:.0f} ms, "
            f"实际={stats.tail_silence_ms:.1f} ms"
        )

    # 5. 削波：满幅样本数必须为 0（否则是贴顶失真）
    if stats.clipped_samples != 0:
        issues.append(
            f"削波: 判据=满幅样本数=0, 实际={stats.clipped_samples}"
        )

    # 6. 峰值电平带：-20 dBFS <= 峰值 <= -1 dBFS
    #    下限拦住「太小听不见」，上限拦住「贴顶临界失真」
    if not (PEAK_MIN_DBFS <= stats.peak_dbfs <= PEAK_MAX_DBFS):
        issues.append(
            f"峰值越带: 判据={PEAK_MIN_DBFS:.0f} dBFS<=峰值<={PEAK_MAX_DBFS:.0f} dBFS, "
            f"实际={stats.peak_dbfs:.2f} dBFS"
        )

    return issues
