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
import os
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
# 两个阈值的**标定基线**（docs/06 §6.1.3 按 macOS 14 `say` 实测留余量钉的）：
#   头静音 实测 max 16.2ms → 100ms（约 6 倍余量）
#   尾静音 实测 max 31.2ms → 150ms（约 5 倍余量）
# ⚠️ 2026-10-10 实测漂移：macOS 27.0.1 的 `say` 尾静音 155–187ms（P50 179ms），
# 余量被吃光 → 全仓 22 条红（compiler/cli 夹具 fail-closed），**不是代码回归**。
# 处置见 `_env_ms`：默认不变（逐字节兼容），要跟随宿主机引擎行为就用环境变量显式覆盖。
TAIL_SILENCE_ENV: str = "VOX_MAX_TAIL_SILENCE_MS"
HEAD_SILENCE_ENV: str = "VOX_MAX_HEAD_SILENCE_MS"
PEAK_MIN_DBFS: float = -20.0      # 峰值带下限
PEAK_MAX_DBFS: float = -1.0       # 峰值带上限
SILENCE_THRESHOLD: int = 328     # 静音判据：|s| <= 328 视为静音（约 -40 dBFS）
CLIP_THRESHOLD: int = 32767      # 削波判据：|s| >= 32767 计为满幅样本
FULL_SCALE: int = 32768          # 16-bit 满幅参考值（dBFS 换算用）


def _env_ms(name: str, default: float) -> float:
    """读一个毫秒阈值的显式环境覆盖；缺省或不可解析 → 原默认值。

    存在的理由（2026-10-10 T79）：阈值是**按某台机器某个 OS 版本的 TTS 行为标定**的，
    而 TTS 引擎的静音填充会随 OS 升级变化——macOS 27 的 `say` 尾静音实测 155–187ms，
    把按 macOS 14（31.2ms）标定的 150ms 阈值顶穿。这类漂移此前只有两条出路：
    改代码里的常量（属契约变更、要开批），或让整条流水线 fail-closed（= 全仓测试红）。
    两者都太重，因此给一个**显式、可审计、默认不变**的覆盖口：
    不设 = 行为与从前逐字节一致；设了 = 报告里照样写明用的是哪套阈值。
    非法值不静默吞掉——抛错，让「配错了」和「没配」分得开。
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise QualityError(
            f"{name} 必须是数字毫秒，实际为 {raw!r}（不设该变量即用缺省 "
            f"{default:.0f} ms；不会静默忽略非法值）"
        ) from exc
    if not (value > 0) or math.isinf(value) or math.isnan(value):
        raise QualityError(f"{name} 必须是正有限毫秒数，实际为 {raw!r}")
    return value


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

    # 3. 头静音：超阈 → 拒（基线实测最大 16.2ms，缺省阈值留 6 倍余量）
    #    阈值经 `_env_ms` 取：缺省 = MAX_HEAD_SILENCE_MS，行为与从前逐字节一致。
    head_limit = _env_ms(HEAD_SILENCE_ENV, MAX_HEAD_SILENCE_MS)
    if stats.head_silence_ms > head_limit:
        issues.append(
            f"头静音超限: 判据=<= {head_limit:.0f} ms, "
            f"实际={stats.head_silence_ms:.1f} ms"
            + (f"（{HEAD_SILENCE_ENV} 覆盖；缺省 {MAX_HEAD_SILENCE_MS:.0f}）"
               if head_limit != MAX_HEAD_SILENCE_MS else "")
        )

    # 4. 尾静音：超阈 → 拒（基线实测最大 31.2ms，缺省阈值留约 5 倍余量）
    #    覆盖生效时消息里点名「用了哪套阈值」——阈值被改过这件事必须留在报告里，
    #    否则读报告的人会把「放宽后的绿」误当成「按基线口径的绿」。
    tail_limit = _env_ms(TAIL_SILENCE_ENV, MAX_TAIL_SILENCE_MS)
    if stats.tail_silence_ms > tail_limit:
        issues.append(
            f"尾静音超限: 判据=<= {tail_limit:.0f} ms, "
            f"实际={stats.tail_silence_ms:.1f} ms"
            + (f"（{TAIL_SILENCE_ENV} 覆盖；缺省 {MAX_TAIL_SILENCE_MS:.0f}）"
               if tail_limit != MAX_TAIL_SILENCE_MS else "")
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
