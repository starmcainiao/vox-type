#!/usr/bin/env python3
"""基频（F0）粗代理：纯标准库自相关法，用来粗看「克隆有没有跟上参考的调门」。

用途与边界（别当结论用）：
- 只给**中位 F0（Hz）+ 有声帧数 + RMS** 三个数，用于粗比参考 / 克隆 / 现有引擎三者的调门量级；
- 不是音色相似度，也不是 MOS 代理；音色好坏一律由人听（`labs/tts-clone-probe/README.md` 的对照套件）。
- 纯标准库（本机无 numpy），frame=800 / hop=800 / 步长 4，24kHz 下 6s 音频约数秒。

用法：python3 labs/tts-clone-probe/f0_proxy.py a.wav b.wav …
"""
from __future__ import annotations

import math
import sys
import wave
from array import array


def load(path: str) -> tuple[array, int]:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: 只支持 16-bit")
    samples = array("h")
    samples.frombytes(raw)
    if ch > 1:  # 取第一声道
        samples = array("h", samples[0::ch])
    return samples, sr


def f0_stats(samples: array, sr: int) -> tuple[float | None, int, float]:
    frame, hop = 800, 800
    lag_min, lag_max = int(sr / 400), int(sr / 70)
    f0s: list[float] = []
    energies: list[float] = []
    for start in range(0, len(samples) - frame, hop):
        seg = samples[start:start + frame]
        rms = math.sqrt(sum(v * v for v in seg) / frame) / 32768.0
        energies.append(rms)
        if rms < 0.01:
            continue
        mean = sum(seg) / frame
        x = [v - mean for v in seg]
        best_lag, best_val = 0, 0.0
        for lag in range(lag_min, min(lag_max, frame - 1)):
            n = frame - lag
            acc = 0.0
            e1 = 0.0
            e2 = 0.0
            for i in range(0, n, 2):
                a, b = x[i], x[i + lag]
                acc += a * b
                e1 += a * a
                e2 += b * b
            denom = math.sqrt(e1 * e2)
            val = acc / denom if denom > 0 else 0.0
            if val > best_val:
                best_val, best_lag = val, lag
        if best_lag and best_val > 0.5:      # 归一化互相关（[-1,1]）取 0.5 作有声判据
            f0s.append(sr / best_lag)
    f0s.sort()
    median = f0s[len(f0s) // 2] if f0s else None
    rms_all = math.sqrt(sum(e * e for e in energies) / len(energies)) if energies else 0.0
    return median, len(f0s), rms_all


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    print(f"{'文件':<46} {'中位F0':>8} {'有声帧':>6} {'RMS':>7} {'时长':>7}")
    for path in sys.argv[1:]:
        samples, sr = load(path)
        median, n, rms = f0_stats(samples, sr)
        dur = len(samples) / sr
        f0 = f"{median:6.1f}Hz" if median else "   n/a"
        print(f"{path.split('/')[-1]:<46} {f0:>8} {n:>6} {rms:>7.4f} {dur:>6.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
