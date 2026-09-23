# adapters.tts_omlx.postprocess — 神经 TTS 产物的预铸质检归一（降采样 + 首尾静音裁剪 + 峰值归一）。
# 为什么在适配器做：compiler.quality 的判据（头静音 <= 100 ms、尾静音 <= 150 ms、
#   峰值 -20<=peak<=-1 dBFS、削波=0）是给系统说音引擎留了余量的阈值，神经引擎的
#   呼吸停顿与输出电平不满足它——本次克隆首铸 29/69 因此被拒。归一是「让产物合规」，
#   属于合成侧的活；质检门保持冻结不动（它不依赖具体引擎）。
# 失败纪律 fail-closed：任何一步不通过都抛 TtsError，绝不返回静音、空文件或假成功。
import array
import base64
import math
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

TARGET_RATE, TARGET_CHANNELS, TARGET_SAMPWIDTH = 16000, 1, 2
SILENCE_THRESHOLD = 328       # 与 compiler.quality 同值（约 -40 dBFS）；裁切与自检同一阈值
HEAD_LIMIT_MS = 100.0         # 与 compiler.quality.MAX_HEAD_SILENCE_MS 同值
TAIL_LIMIT_MS = 150.0         # 与 compiler.quality.MAX_TAIL_SILENCE_MS 同值
PEAK_MIN_DBFS = -20.0         # 与 compiler.quality 峰值带下限同值
PEAK_MAX_DBFS = -1.0          # 与 compiler.quality 峰值带上限同值
HEAD_PAD_MS = 5               # 裁后保留 5 ms 头静音（100 ms 上限留 20 倍余量）
TAIL_PAD_MS = 5               # 裁后保留 5 ms 尾静音（150 ms 上限留 30 倍余量）
FULL_SCALE = 32768.0
CLIP_THRESHOLD = 32767


class _TtsError(RuntimeError):
    """归一内部失败；由对外函数统一包装成 TtsError 抛出。"""


def build_payload(model: str, text: str, speed: float, ref_audio_bytes: Optional[bytes]) -> dict:
    """组装合成请求体。

    克隆（给了参考音频字节）：带 base64 ref_audio + env 里的 ref_text，不带 voice。
    默认音色（无参考音频）：只带 voice。指纹里永不出现任何路径。
    """
    payload = {"model": model, "input": text, "response_format": "wav", "speed": speed}
    if ref_audio_bytes is not None:
        payload["ref_audio"] = base64.b64encode(ref_audio_bytes).decode("ascii")
        payload["ref_text"] = os.environ["VOX_TTS_REF_TEXT"]
    else:
        payload["voice"] = "default"
    return payload


def _run(runner, cmd, timeout, label):
    """跑外部进程（ffmpeg）：不可用/超时/非 0 一律抛错，带 stderr 片段便于定位。"""
    try:
        r = runner(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise _TtsError(f"{label}: 进程不可用（FileNotFoundError）: {cmd[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise _TtsError(f"{label}: 超时（{timeout} 秒）") from exc
    if r.returncode != 0:
        raise _TtsError(f"{label}: 退出码 {r.returncode}: {r.stderr.strip()[:300]!r}")
    return r


def _downsample(src: Path, dst: Path, ffmpeg: str, runner, timeout) -> None:
    """ffmpeg 降到契约格式 16kHz/单声道/16-bit；命令形状与适配器降级链路一致。"""
    _run(runner, [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
                  "-ar", str(TARGET_RATE), "-ac", str(TARGET_CHANNELS),
                  "-c:a", "pcm_s16le", str(dst)], timeout, "ffmpeg 降采样")


def _read_samples(path: Path) -> array:
    """读 16-bit 小端样本；格式不符契约 / 非法 WAV / 0 样本一律抛错（不信任 ffmpeg 输出）。

    注意：Python 的 wave 模块写出的 fmt 块在 WAVE 之后**没有 4 字节对齐填充**，
    所以不能固定从 44 字节处取样本——按 chunk 逐个定位 data 块，两种布局都能读。
    """
    raw = path.read_bytes()
    if not raw.startswith(b"RIFF") or len(raw) < 12:
        raise _TtsError(f"降采样产物不是合法 WAV（{len(raw)} 字节）: {path}")
    channels, sampwidth, rate, data_off = (0, 0, 0, -1)
    idx = 12
    while idx + 8 <= len(raw):
        chunk_id, chunk_size = raw[idx:idx + 4], struct.unpack("<I", raw[idx + 4:idx + 8])[0]
        body = raw[idx + 8:idx + 8 + chunk_size]
        if chunk_id == b"fmt ":
            # fmt 块字段偏移（相对块体）：AudioFormat(0,2B) NumChannels(2,2B)
            # SampleRate(4,4B) ByteRate(8,4B) BlockAlign(12,2B) BitsPerSample(14,2B)
            channels = struct.unpack("<H", body[2:4])[0]
            rate = struct.unpack("<I", body[4:8])[0]
            bits = struct.unpack("<H", body[14:16])[0]
            sampwidth = bits // 8
        elif chunk_id == b"data":
            data_off = idx + 8
        idx += 8 + chunk_size
    if (channels, sampwidth, rate) != (TARGET_CHANNELS, TARGET_SAMPWIDTH, TARGET_RATE):
        raise _TtsError(f"降采样产物格式不符（要求 {TARGET_RATE}Hz/单声道/16-bit，"
                        f"实际 {rate}Hz/{channels}声道/{sampwidth * 8}-bit）: {path}")
    if data_off < 0:
        raise _TtsError(f"降采样产物缺 data 块: {path}")
    samples = array.array("h")
    body = raw[data_off:]
    samples.frombytes(body[: len(body) - (len(body) % 2)])
    if len(samples) == 0:
        raise _TtsError(f"降采样产物 0 样本（空音频）: {path}")
    return samples


def _trim(samples: array, head_keep: int, tail_keep: int) -> array:
    """把首尾静音各裁到 head_keep / tail_keep 样本（单位是样本，不是毫秒）。

    实现说明：起点取 max(keep, 首静音) —— keep < 首静音时裁到 keep（keep 处必然
    仍是静音，不会切进语音）；keep >= 首静音时保持 0（不裁也不丢语音）。
    终点镜像同理。这样产物首尾静音严格 <= 保留量。
    """
    n = len(samples)
    first = next((i for i in range(n) if abs(samples[i]) > SILENCE_THRESHOLD), None)
    if first is None:
        raise _TtsError("音频全静音，无法归一（拒绝产出静音资产）")
    last = next((i for i in range(n - 1, -1, -1) if abs(samples[i]) > SILENCE_THRESHOLD), n - 1)
    start = max(head_keep, first)
    end = min(n - tail_keep, last + 1)
    if end <= start:
        raise _TtsError(f"裁剪后区间为空（首非静音 {first} / 末 {last}），拒绝产出")
    return array.array("h", bytes(samples[start:end]))


def _peak_normalize(samples: array) -> array:
    """线性增益把峰值压到峰值带的**几何**中心，两侧都留余量。

    目标电平用 10^((下+上)/2)：dB 值的平均要先转回线性域做几何平均，
    直接 10^((下+上)/20) 得到的是算术平均（-10.5 dBFS），会离中心偏半段。
    """
    peak = max(abs(s) for s in samples)
    if peak == 0:
        raise _TtsError("峰值为 0（全静音），无法归一")
    target = FULL_SCALE * math.sqrt(10.0 ** ((PEAK_MIN_DBFS + PEAK_MAX_DBFS) / 20.0))
    gain = target / peak
    out = array.array("h")
    for s in samples:
        v = int(round(s * gain))
        out.append(max(-CLIP_THRESHOLD, min(CLIP_THRESHOLD, v)))
    return out


def _write_wav(samples: array) -> bytes:
    """按契约格式重打包 WAV（标准库实现，不依赖 audioop）。"""
    body = struct.pack(f"<{len(samples)}h", *tuple(samples))
    header = b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, TARGET_CHANNELS, TARGET_RATE,
                                    TARGET_RATE * TARGET_CHANNELS * 2, TARGET_CHANNELS * 2, 16)
    header += b"data" + struct.pack("<I", len(body))
    return header + body


def normalize_from(src: Path, *, ffmpeg: str, runner=None, timeout: float = 600) -> Path:
    """把神经 TTS 原始输出收进预铸质检阈值内，返回新的 WAV 文件路径。

    步骤：ffmpeg 降采样 → 首尾静音裁剪 → 峰值归一 → 重打包 WAV。
    任一判据不满足即抛 TtsError（fail-closed）；不做 LUFS 归一（与编译器一致，明确推迟）。
    """
    from .adapter import TtsError
    if not src.is_file() or src.stat().st_size == 0:
        raise TtsError(f"归一输入不存在或为空: {src}")
    runner = runner if runner is not None else subprocess.run
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fd:
            dst = Path(fd.name)
        _downsample(src, dst, ffmpeg, runner, timeout)
        samples = _read_samples(dst)
        samples = _trim(samples, int(HEAD_PAD_MS / 1000.0 * TARGET_RATE),
                        int(TAIL_PAD_MS / 1000.0 * TARGET_RATE))
        samples = _peak_normalize(samples)
        # 自检：按 compiler.quality 完全相同的口径复算，不过就抛——不许把坏件交给质检门。
        head = next((i for i, s in enumerate(samples) if abs(s) > SILENCE_THRESHOLD), len(samples))
        tail = next((i for i, s in enumerate(reversed(samples)) if abs(s) > SILENCE_THRESHOLD), len(samples))
        head_ms, tail_ms = head / TARGET_RATE * 1000, tail / TARGET_RATE * 1000
        peak = max(abs(s) for s in samples)
        peak_dbfs = 20.0 * math.log10(peak / FULL_SCALE)
        if head_ms > HEAD_LIMIT_MS:
            raise _TtsError(f"归一后头静音仍超限: {head_ms:.1f} ms > {HEAD_LIMIT_MS:.0f} ms")
        if tail_ms > TAIL_LIMIT_MS:
            raise _TtsError(f"归一后尾静音仍超限: {tail_ms:.1f} ms > {TAIL_LIMIT_MS:.0f} ms")
        if not (PEAK_MIN_DBFS <= peak_dbfs <= PEAK_MAX_DBFS):
            raise _TtsError(f"归一后峰值仍越带: {peak_dbfs:.2f} dBFS（要求 {PEAK_MIN_DBFS}~{PEAK_MAX_DBFS}）")
        if peak >= CLIP_THRESHOLD:
            raise _TtsError(f"归一后仍有满幅样本（削波）: 峰值 {peak}")
        dst.write_bytes(_write_wav(samples))
    except _TtsError as exc:
        raise TtsError(f"产物归一失败: {exc}") from exc
    return dst
