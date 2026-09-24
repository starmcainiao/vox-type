"""
eval.offline_tts — 离线替身 TTS 适配器（**非产品件**）

职责：在无网、无 `say` 命令、PATH 被清空的环境里，给 eval/ 提供一个 TTS 接口形状完全一致的
      替身适配器，使离线也能跑出结构完整的报告（eval/AGENTS.md §③.4「离线可跑」的技术前提）。
不负责：不是产品代码，不代表任何真实 TTS 引擎；不产生真实合成耗时。

接口一致性：synthesize/voice/model_version 的成员名与参数名、参数顺序照
      adapters/tts_macsay/adapter.py 的 MacSayTts，不得自创签名（否则运行时判定逻辑走不到）。

关键标记：synthetic = True。
      本适配器按「字符数 × ms_per_char」造音频时长，耗时是人工构造的，不是合成耗时。
      因此用它跑出的时序数字不得对外引用——bench/ 会把 timing_metrics_meaningful 置 False，
      并在人读摘要里打显式警告（不得美化，eval/AGENTS.md §③.3）。

确定性：同样输入 → 逐字节相同的 WAV。
      ① 样本值由 sha256(text) 派生的纯 LCG 递推算出（无 RNG 模块、无时间戳、无随机种子），
         与进程启动顺序、PYTHONHASHSEED、当前时间全部无关；
      ② 音频内容只依赖 text 与长度，不依赖 out_path 与调用次数；
      ③ WAV 头不含任何时间字段。
"""

import hashlib
import wave
from array import array
from pathlib import Path
from typing import Any, Dict, Optional

# 冻结的音频口径：与 runtime.audio 及包内资产一致（否则 read_wav 拒读）
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
SAMPLE_WIDTH: int = 2

# 语速档位 → 时长系数（slow 更慢=更长，fast 更快=更短）
DEFAULT_RATE_FACTOR: Dict[str, float] = {"slow": 1.3, "normal": 1.0, "fast": 0.75}

# 纯 LCG 参数（Numerical Recipes 经典取值；确定性，非 RNG 库）
_LCG_A: int = 1664525
_LCG_C: int = 1013904223
_LCG_MASK: int = 0xFFFFFFFF

# 峰值振幅（16-bit 安全范围；留余量给淡入淡出与拼接）
_PEAK: int = 5000


def _deterministic_samples(text: str, frames: int) -> array:
    """由 text 确定性地生成 frames 个 16-bit 样本（同 text + 同 frames → 同数组）。"""
    # WHY：用 sha256 而不是内建 hash()——hash() 的字符串种子随进程变化（PYTHONHASHSEED），
    #      那样"同输入 → 同字节"就不成立了，评测重放会失效。
    state = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16) & _LCG_MASK
    if state == 0:
        state = 1

    out = array("h")
    for _ in range(frames):
        state = (state * _LCG_A + _LCG_C) & _LCG_MASK
        out.append(int(((state >> 16) - 32768) * 0.15))
    return out


class OfflineTts:
    """离线替身 TTS 适配器：产出合法 16kHz/单声道/16-bit WAV，时长由文本长度决定。

    属性：
        name:           适配器名（会显示在报告 env.adapter.name）
        synthetic:      True——关键标记，报告据此把 timing_metrics_meaningful 置 False
        voice:          音色（参与运行时与包的引擎一致性比对，须与包 manifest 一致）
        model_version:  模型版本（同上）
        rate_map:       语义档位 → 物理语速值（wpm），与 MacSayTts 形状一致
        rate_factor:    语义档位 → 时长系数（本替身专用）
        ms_per_char:    每字符基准时长（毫秒）
    """

    name: str = "offline-synthetic"
    synthetic: bool = True
    rate_map: Dict[str, int] = {"slow": 150, "normal": 200, "fast": 300}

    def __init__(
        self,
        *,
        voice: str,
        model_version: str,
        ms_per_char: float = 70.0,
        rate_factor: Optional[Dict[str, float]] = None,
    ) -> None:
        """初始化替身适配器（音色与模型版本必须与资产包 manifest 一致，否则运行时按 engine_mismatch 降级）。

        异常：
            ValueError: voice/model_version 为空 / ms_per_char 非正 / rate_factor 含非正系数或缺档位
        """
        if not isinstance(voice, str) or not voice:
            raise ValueError(f"voice 必须是非空字符串，实际为 {voice!r}")
        if not isinstance(model_version, str) or not model_version:
            raise ValueError(f"model_version 必须是非空字符串，实际为 {model_version!r}")
        if (
            not isinstance(ms_per_char, (int, float))
            or isinstance(ms_per_char, bool)
            or ms_per_char <= 0
        ):
            raise ValueError(
                f"ms_per_char 必须是正数，实际为 {ms_per_char!r}"
            )
        if rate_factor is None:
            factors = dict(DEFAULT_RATE_FACTOR)
        else:
            factors = dict(rate_factor)
            for key in DEFAULT_RATE_FACTOR:
                if key not in factors:
                    raise ValueError(
                        f"rate_factor 缺档位 {key!r}，实际为 {sorted(factors)}"
                    )
            for key, value in factors.items():
                if not isinstance(value, (int, float)) or value <= 0:
                    raise ValueError(
                        f"rate_factor[{key!r}] 必须是正数，实际为 {value!r}"
                    )

        self.voice = voice
        self.model_version = model_version
        self.ms_per_char = float(ms_per_char)
        self.rate_factor = factors

    def rate_value(self, rate_key: str) -> int:
        """语义档位 → 物理语速值（wpm）。未知档位直接报错，不回落。"""
        if rate_key not in self.rate_map:
            raise ValueError(
                f"未知语速档位: {rate_key!r}（仅支持: {', '.join(sorted(self.rate_map))}）"
            )
        return self.rate_map[rate_key]

    def synthesize(self, text: str, out_path: Any, rate_key: str = "normal") -> Path:
        """合成一段音频并落盘为 16kHz/单声道/16-bit WAV。

        参数：
            text:     待合成文本（不得为空；禁止合成静音）
            out_path: 输出文件路径（str 或 Path；父目录不存在时自动创建）
            rate_key: 语速档位（slow / normal / fast）

        返回：
            输出 Path（WAV 已落盘）

        异常：
            ValueError: 空文本 / 未知语速档位
        """
        if text is None or len(text) == 0:
            # WHY：与适配器层同规矩——空文本一律报错，禁止用静音文件冒充产物
            #      （静默降级在语音链路里表现为"突然静音"，事后无法定位）。
            raise ValueError("空文本无法合成语音（禁止合成静音）")
        if rate_key not in self.rate_factor:
            raise ValueError(
                f"未知语速档位: {rate_key!r}（仅支持: {', '.join(sorted(self.rate_factor))}）"
            )

        out = Path(out_path)
        # 时长 = max(1, 字符数) × 每字符毫秒 × 档位系数；下限 1ms 保证至少 1 个样本
        duration_ms = max(1, len(text)) * self.ms_per_char * float(
            self.rate_factor[rate_key]
        )
        frames = max(1, int(round(duration_ms * SAMPLE_RATE / 1000.0)))

        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(_deterministic_samples(text, frames).tobytes())
        return out
