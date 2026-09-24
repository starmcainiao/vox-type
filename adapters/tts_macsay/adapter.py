"""
adapters.tts_macsay.adapter — macOS say 命令薄适配器

职责：把 macOS 原生 say 命令接进 TTS 适配器接口，合成 16kHz/单声道/16-bit WAV。
不负责：不含业务逻辑；不做拼接与判定；不做预铸调度。
"""

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

_TIMEOUT_SECONDS: int = 60


class TtsError(RuntimeError):
    """TTS 合成失败统一异常。

    消息必须包含失败原因，不允许吞掉错误上下文。
    """


class MacSayTts:
    """macOS say 命令 TTS 适配器。

    属性：
        name:           适配器名（参与资产索引）
        model_version:  模型版本（参与资产指纹；换版本 = 旧资产失效）
        voice:          音色（macOS say -v 参数）
        rate_map:       语义档位 → 物理参数（语速 wpm）
        requires_core:  依赖的内核版本区间（semver range）
    """

    name: str = "macsay"
    model_version: str = "macos-say"
    voice: str = "Tingting"
    rate_map: dict[str, int] = {
        "slow": 150,
        "normal": 200,
        "fast": 300,
    }
    requires_core: str = "^0.1"

    def rate_value(self, rate_key: str) -> int:
        """把语义档位映射为物理语速值（wpm）。

        参数：
            rate_key: 语义档位（slow/normal/fast 或其他已配置的 key）

        返回：
            对应的物理语速值（整数，单位 wpm）

        异常：
            TtsError: 未知档位（不回落，直接报错且消息含该 key）
        """
        if rate_key not in self.rate_map:
            raise TtsError(
                f"未知语速档位: {rate_key!r}（仅支持: {', '.join(sorted(self.rate_map))}）"
            )
        return self.rate_map[rate_key]

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        """调用 macOS say 合成语音，产出 16kHz/单声道/16-bit WAV。

        参数：
            text:     待合成文本（不得为空）
            out_path: 输出文件路径（str 或 Path；父目录不存在时自动创建）
            rate_key: 语速档位（缺省 normal）

        返回：
            无（成功时文件已落盘）

        异常：
            TtsError: 空文本 / say 命令失败 / 产物不存在或 0 字节
        """
        if not text:
            raise TtsError("空文本无法合成语音（禁止合成静音）")

        out = Path(out_path)

        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise TtsError(f"创建输出目录失败: {out.parent} ({e})") from e

        rate = self.rate_value(rate_key)

        # say 对 /dev/null、/dev/stdout 等特殊文件会拒绝（-241/-54），
        # 因此始终写到 tempfile 生成的普通文件路径，再移动到目标。
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            cmd = [
                "say",
                "-v", self.voice,
                "-r", str(rate),
                "-o", str(tmp_path),
                "--file-format=WAVE",
                "--data-format=LEI16@16000",
                text,
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_TIMEOUT_SECONDS,
                )
            except FileNotFoundError as e:
                raise TtsError(
                    f"say 命令不可用: {e}"
                ) from e
            except subprocess.TimeoutExpired as e:
                raise TtsError(
                    f"say 命令超时（{_TIMEOUT_SECONDS} 秒）"
                ) from e

            if result.returncode != 0:
                stderr_snippet = result.stderr.strip()[:500]
                raise TtsError(
                    f"say 命令失败（退出码 {result.returncode}）: {stderr_snippet}"
                )

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise TtsError("say 命令未产出有效文件（文件不存在或 0 字节）")

            if out.exists() and out.is_dir():
                raise TtsError(
                    f"目标路径是已存在的目录而非文件: {out}"
                )

            # shutil.move 自动处理跨设备：先尝试 rename，失败则复制+删除
            try:
                shutil.move(str(tmp_path), str(out))
            except OSError as e:
                raise TtsError(f"落盘移动失败: {tmp_path} -> {out} ({e})") from e
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
