"""
adapters.tts_piper.adapter — Piper（rhasspy/piper）本地神经 TTS 薄适配器

职责：把 piper 本地可执行文件接进 TTS 适配器接口，合成 16kHz/单声道/16-bit WAV。
      产物经 normalize 收进预铸质检阈值（piper 直出 22050 Hz、峰值贴顶、尾静音超限）。
不负责：不含业务逻辑；不做拼接与判定；不做预铸调度。

配置（env 是唯一入口——`--adapter` 传不进构造参数，容器/服务器只有环境变量）：
  VOX_PIPER_MODEL   .onnx 模型路径（必填；模型不进仓，由部署方自备）
  VOX_PIPER_BIN     piper 可执行文件（缺省 "piper"，走 PATH）
  VOX_FFMPEG        ffmpeg 可执行文件（缺省 "ffmpeg"，与 tts_omlx 同约定）

引擎一致性：voice/model_version 由模型文件名推导——换模型 = 换指纹 = 旧资产失效。
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .normalize import NormalizeError, normalize_from

_TIMEOUT_SECONDS = 300


class TtsError(RuntimeError):
    """TTS 合成失败统一异常。消息必须包含失败原因，不允许吞掉错误上下文。"""


class PiperTts:
    """Piper 本地神经 TTS 适配器。

    属性：
        name:           适配器名（参与资产索引）
        model_version:  模型版本（= `piper-<模型文件名>`，参与资产指纹）
        voice:          音色（= 模型文件名 stem；piper 一个模型一个音色）
        rate_map:       语义档位 → 语速倍率（>1 更快；调用时换算为 --length-scale 倒数）
        requires_core:  依赖的内核版本区间（semver range）
    """

    name: str = "piper"
    requires_core: str = "^0.1"
    rate_map: dict[str, float] = {
        "slow": 0.85,
        "normal": 1.0,
        "fast": 1.25,
    }

    def __init__(self) -> None:
        self._model: str = (os.environ.get("VOX_PIPER_MODEL") or "").strip()
        self._binary: str = (os.environ.get("VOX_PIPER_BIN") or "piper").strip()
        self._ffmpeg: str = (os.environ.get("VOX_FFMPEG") or "ffmpeg").strip()

    @property
    def voice(self) -> str:
        stem = Path(self._model).stem if self._model else ""
        return stem or "default"

    @property
    def model_version(self) -> str:
        stem = Path(self._model).stem if self._model else ""
        return f"piper-{stem}" if stem else "piper"

    def rate_value(self, rate_key: str) -> float:
        """把语义档位映射为语速倍率。

        参数：
            rate_key: 语义档位（slow/normal/fast）

        返回：
            语速倍率（>1 更快）

        异常：
            TtsError: 未知档位（不回落，直接报错且消息含该 key）
        """
        if rate_key not in self.rate_map:
            raise TtsError(
                f"未知语速档位: {rate_key!r}（仅支持: {', '.join(sorted(self.rate_map))}）"
            )
        return self.rate_map[rate_key]

    @classmethod
    def probe_availability(cls) -> tuple[bool, str]:
        """按 synthesize 的检查顺序探全部前置条件：模型 → piper → ffmpeg。

        三条都不满足时**全部原因都报**（只报第一条会让人补完一个再来一趟）。
        本方法走 env 回落链、与无参构造同构——探的就是会用的。
        """
        reasons = []
        model = (os.environ.get("VOX_PIPER_MODEL") or "").strip()
        if not model:
            reasons.append("未设置 VOX_PIPER_MODEL（.onnx 模型路径，模型不进仓）")
        elif not Path(model).is_file():
            reasons.append(f"模型文件不存在: {model}")
        binary = (os.environ.get("VOX_PIPER_BIN") or "piper").strip()
        if shutil.which(binary) is None:
            reasons.append(f"piper 不可用: {binary!r}（可在 PATH 提供或以 env VOX_PIPER_BIN 覆盖）")
        ffmpeg = (os.environ.get("VOX_FFMPEG") or "ffmpeg").strip()
        if shutil.which(ffmpeg) is None:
            reasons.append(
                f"ffmpeg 不可用: {ffmpeg!r}（22050Hz→16kHz 降采样必需，可设 env VOX_FFMPEG）"
            )
        return (not reasons, "; ".join(reasons))

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        """调 piper 合成语音，产出 16kHz/单声道/16-bit WAV。

        参数：
            text:     待合成文本（不得为空）
            out_path: 输出文件路径（str 或 Path；父目录不存在时自动创建）
            rate_key: 语速档位（缺省 normal）

        返回：
            无（成功时文件已落盘）

        异常：
            TtsError: 空文本 / 模型未配置或不存在 / piper 失败或超时 / 归一失败
        """
        if not text:
            raise TtsError("空文本无法合成语音（禁止合成静音）")
        if not self._model:
            raise TtsError("未配置 Piper 模型：请设 env VOX_PIPER_MODEL 指向 .onnx 文件")
        model = Path(self._model)
        if not model.is_file():
            raise TtsError(f"Piper 模型文件不存在: {model}")

        speed = self.rate_value(rate_key)
        out = Path(out_path)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise TtsError(f"创建输出目录失败: {out.parent}（{e}）") from e

        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "raw.wav"
            cmd = [
                self._binary,
                "--model", str(model),
                "--output_file", str(raw),
                # piper --length-scale 是时长倍率（越大越慢）；档位语义是语速倍率，取倒数。
                "--length-scale", f"{1.0 / speed:.4f}",
            ]
            try:
                result = subprocess.run(
                    cmd, input=text, capture_output=True, text=True,
                    check=False, timeout=_TIMEOUT_SECONDS,
                )
            except FileNotFoundError as e:
                raise TtsError(f"piper 命令不可用: {e}") from e
            except subprocess.TimeoutExpired as e:
                raise TtsError(f"piper 命令超时（{_TIMEOUT_SECONDS} 秒）") from e
            if result.returncode != 0:
                raise TtsError(
                    f"piper 命令失败（退出码 {result.returncode}）: "
                    f"{result.stderr.strip()[:500]}"
                )
            if not raw.is_file() or raw.stat().st_size == 0:
                raise TtsError("piper 未产出有效文件（文件不存在或 0 字节）")
            try:
                normalized = normalize_from(raw, ffmpeg=self._ffmpeg)
            except NormalizeError as e:
                raise TtsError(f"产物归一失败: {e}") from e
            try:
                shutil.move(str(normalized), str(out))
            except OSError as e:
                raise TtsError(f"落盘移动失败: {normalized} -> {out}（{e}）") from e
