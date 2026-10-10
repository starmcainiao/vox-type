"""
adapters.tts_piper.normalize — Piper 产物归一（降采样 + 静音裁剪 + 峰值归一）

为什么需要：Piper 直出 22050 Hz、峰值贴 0 dBFS（偶带满幅样本）、尾静音可达 ~180 ms，
三项都不满足预铸质检阈值（16 kHz / 削波=0 / 峰值带 [-20,-1] dBFS / 尾静音 ≤150 ms）。
归一是「让产物合规」，属合成侧的活——质检门保持冻结不动，它不依赖具体引擎。

为什么自带实现而不复用 tts_omlx/postprocess：适配器之间不建立 import 耦合——
删掉一个不坏另一个；两份产物归一服务的引擎与失败语义本来就不同。

失败纪律 fail-closed：任一归一步骤或自检不满足即抛 NormalizeError，绝不产出坏件。
"""

import array
import math
import os
import subprocess
import tempfile
import wave
from pathlib import Path

TARGET_RATE = 16000                  # 契约采样率（与 compiler.quality 同值）
SILENCE_THRESHOLD = 328              # 静音判据 ≈ -40 dBFS（与 compiler.quality 同值）
HEAD_LIMIT_MS = 100.0                # 与 compiler.quality.MAX_HEAD_SILENCE_MS 同值
TAIL_LIMIT_MS = 150.0                # 与 compiler.quality.MAX_TAIL_SILENCE_MS 同值
PEAK_MIN_DBFS = -20.0                # 与 compiler.quality 峰值带下限同值
PEAK_MAX_DBFS = -1.0                 # 与 compiler.quality 峰值带上限同值
FULL_SCALE = 32768.0
CLIP_THRESHOLD = 32767
HEAD_PAD_MS = 5.0                    # 裁后保留 5 ms 头静音（100 ms 上限留 20 倍余量）
TAIL_PAD_MS = 5.0                    # 裁后保留 5 ms 尾静音（150 ms 上限留 30 倍余量）


class NormalizeError(RuntimeError):
    """归一失败；由适配器统一包装成 TtsError 抛出。"""


def normalize_from(src: Path, *, ffmpeg: str, timeout: float = 600.0) -> Path:
    """把 Piper 原始输出收进预铸质检阈值内，返回新的 WAV 文件路径。

    步骤：ffmpeg 降采样 → 峰值归一 → 首尾静音裁剪 → 按质检口径自检 → 落盘。
    **顺序不可换**（2026-10-10 容器实测）：piper 输出有数十毫秒的渐入斜坡——
    若先裁后归一，边界样本按未缩放幅值判定「非静音」，整体缩放后跌回阈值以下，
    105ms 处才重新越阈，自检以「头静音 159.8ms 超限」拒绝整批。
    先缩放再裁剪，阈值判定对象与最终产物同相，两个方向（缩放/裁剪）不再互相打架。
    任一判据不满足即抛 NormalizeError；不做 LUFS 归一（与编译器一致，明确推迟）。
    """
    src = Path(src)
    if not src.is_file() or src.stat().st_size == 0:
        raise NormalizeError(f"归一输入不存在或为空: {src}")
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "resampled.wav"
        _downsample(src, mid, ffmpeg, timeout)
        samples = _read_samples(mid)
        samples = _peak_normalize(samples)
        samples = _trim(samples)
        _self_check(samples)
        # mkstemp 的 fd 必须立即关闭（Windows/长跑进程下 fd 泄漏是硬故障）；
        # 文件保持存在，由调用方 move 走。
        fd, tmp_name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        out = Path(tmp_name)
        _write_wav(samples, out)
    return out


def _downsample(src: Path, dst: Path, ffmpeg: str, timeout: float) -> None:
    """ffmpeg 降到契约格式 16 kHz/单声道/16-bit。"""
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
           "-ar", str(TARGET_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                check=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise NormalizeError(f"ffmpeg 不可用: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise NormalizeError(f"ffmpeg 超时（{timeout} 秒）") from exc
    if result.returncode != 0:
        raise NormalizeError(
            f"ffmpeg 降采样失败（退出码 {result.returncode}）: "
            f"{result.stderr.strip()[:300]}"
        )


def _read_samples(path: Path) -> array:
    """读 16-bit 样本；格式不符契约 / 非法 WAV / 0 样本一律抛错（不信任 ffmpeg 输出）。"""
    try:
        with wave.open(str(path), "rb") as wf:
            fmt = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
            raw = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise NormalizeError(f"降采样产物不是合法 WAV: {path}（{exc}）") from exc
    if fmt != (1, 2, TARGET_RATE):
        raise NormalizeError(
            f"降采样产物格式不符（要求 {TARGET_RATE}Hz/单声道/16-bit）: "
            f"实际 {fmt[2]}Hz/{fmt[0]}声道/{fmt[1] * 8}-bit @ {path}"
        )
    samples = array.array("h")
    samples.frombytes(raw)
    if len(samples) == 0:
        raise NormalizeError(f"降采样产物 0 样本（空音频）: {path}")
    return samples


def _trim(samples: array) -> array:
    """把首尾静音裁到各 ≤ PAD 毫秒，且不切进语音。

    起点 = max(0, first - keep)：首静音 > keep 时裁到恰好 keep；否则保持不动。
    终点对称。这样新首（尾）静音严格 ≤ keep，任何情况下都不丢语音样本。
    """
    n = len(samples)
    first = next((i for i in range(n) if abs(samples[i]) > SILENCE_THRESHOLD), None)
    if first is None:
        raise NormalizeError("音频全静音，无法归一（拒绝产出静音资产）")
    last = next(i for i in range(n - 1, -1, -1) if abs(samples[i]) > SILENCE_THRESHOLD)
    head_keep = int(HEAD_PAD_MS / 1000.0 * TARGET_RATE)
    tail_keep = int(TAIL_PAD_MS / 1000.0 * TARGET_RATE)
    start = max(0, first - head_keep)
    end = min(n, last + 1 + tail_keep)
    if end <= start:
        raise NormalizeError(f"裁剪后区间为空（首非静音 {first} / 末 {last}）")
    return array.array("h", samples[start:end])


def _peak_normalize(samples: array) -> array:
    """线性增益把峰值压到峰值带的**几何**中心，两侧都留余量。

    目标电平用 10^((下+上)/2)：dB 值的平均要先转回线性域做几何平均，
    直接 10^((下+上)/20) 得到的是算术平均，会离中心偏半段。
    """
    peak = max(abs(s) for s in samples)
    if peak == 0:
        raise NormalizeError("峰值为 0（全静音），无法归一")
    target = FULL_SCALE * math.sqrt(10.0 ** ((PEAK_MIN_DBFS + PEAK_MAX_DBFS) / 20.0))
    gain = target / peak
    out = array.array("h")
    for s in samples:
        v = int(round(s * gain))
        out.append(max(-CLIP_THRESHOLD, min(CLIP_THRESHOLD, v)))
    return out


def _self_check(samples: array) -> None:
    """按 compiler.quality 完全相同的口径复算，不过就抛——不许把坏件交给质检门。"""
    n = len(samples)
    first = next((i for i in range(n) if abs(samples[i]) > SILENCE_THRESHOLD), None)
    if first is None:
        raise NormalizeError("归一后全静音（自检不过）")
    last = next(i for i in range(n - 1, -1, -1) if abs(samples[i]) > SILENCE_THRESHOLD)
    head_ms = first / TARGET_RATE * 1000
    tail_ms = (n - 1 - last) / TARGET_RATE * 1000
    peak = max(abs(s) for s in samples)
    peak_dbfs = 20.0 * math.log10(peak / FULL_SCALE)
    if head_ms > HEAD_LIMIT_MS:
        raise NormalizeError(f"归一后头静音仍超限: {head_ms:.1f} ms > {HEAD_LIMIT_MS:.0f} ms")
    if tail_ms > TAIL_LIMIT_MS:
        raise NormalizeError(f"归一后尾静音仍超限: {tail_ms:.1f} ms > {TAIL_LIMIT_MS:.0f} ms")
    if not (PEAK_MIN_DBFS <= peak_dbfs <= PEAK_MAX_DBFS):
        raise NormalizeError(
            f"归一后峰值仍越带: {peak_dbfs:.2f} dBFS（要求 {PEAK_MIN_DBFS}~{PEAK_MAX_DBFS}）"
        )
    if peak >= CLIP_THRESHOLD:
        raise NormalizeError(f"归一后仍有满幅样本（削波）: 峰值 {peak}")


def _write_wav(samples: array, path: Path) -> None:
    """按契约格式写 WAV（标准库 wave，不依赖 audioop）。"""
    try:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_RATE)
            wf.writeframes(samples.tobytes())
    except (wave.Error, OSError) as exc:
        raise NormalizeError(f"写 WAV 失败: {path}（{exc}）") from exc
