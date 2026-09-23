# adapters.tts_omlx.adapter — oMLX 本地神经 TTS 薄适配器（含零样本克隆重铸）。
# 服务出 24 kHz，适配器内用 ffmpeg 降到契约的 16 kHz/单声道/16-bit（runtime.audio 只认这个）。
# 两条路径都由 env 决定，代码里不写死任何参考音频：
#   a) 默认音色 —— 不发 ref_audio，带 voice="default"；
#   b) 零样本克隆 —— env VOX_TTS_REF（参考音频）+ VOX_TTS_REF_TEXT（转写），缺一即 fail-closed；
#      voice 取 clone-<参考音频 sha256 前 8 位>（指纹里不出现路径），
#      model_version 为 qwen3-tts-0.6b-base-clone（换音色即换指纹，旧资产自动失效）。
# 失败纪律 fail-closed：缺 ffmpeg / 空文本 / 未知语速档 / 连接与超时 / 非 2xx / 响应不是合法 WAV /
# 降采样失败 / 落盘失败一律抛 TtsError，绝不返回静音、空文件或假成功。
import base64
import hashlib
import json
import math
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import wave
from http.client import HTTPException as HttpLibError
from http.client import IncompleteRead as HttpIncompleteRead
from pathlib import Path
from typing import Any, Optional

from . import postprocess

BASE_URL = "http://127.0.0.1:10099"
SPEECH_ENDPOINT = "/v1/audio/speech"
DEFAULT_MODEL = "Qwen3-TTS-12Hz-0.6B-Base-bf16"
CLONE_MODEL_VERSION = "qwen3-tts-0.6b-base-clone"
DEFAULT_VOICE = "default"
DEFAULT_FFMPEG = "ffmpeg"
TARGET_FMT = (1, 2, 16000)
TARGET_RATE = TARGET_FMT[2]
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
CLONE_PREFIX = "clone-"
# 有界重试：oMLX 在模型装卸/换载窗口内会以 HTTP 500 拒绝请求
# （实测文案 "Model '…' is busy; cannot start work while unload is pending…"），
# 那是引擎瞬时态而非失败合成——重试而非报错，但必须有界，超限仍 fail-closed。
MAX_RETRIES = 5
RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0, 30.0)
BUSY_MARKERS = ("is busy;", "unload is pending", "cannot start work")
# 阶段故障 → 消息模板（表驱动：逐条 except 会把文件推过 adapters/AGENTS.md §② 的 150 行标准）
HTTP_STAGES = {socket.timeout: "TTS 请求超时（socket.timeout，{t} 秒）", ssl.SSLError: "TTS TLS 错误（ssl.SSLError）",
               urllib.error.URLError: "TTS 无法连接（urllib.error.URLError）",
               HttpLibError: "TTS 响应协议错误（http.client.HTTPException）"}
READ_STAGES = {HttpIncompleteRead: "TTS 响应读取中断（http.client.IncompleteRead）",
               HttpLibError: "TTS 响应读取协议错误（http.client.HTTPException）",
               TimeoutError: "TTS 响应读取超时（TimeoutError）"}


# TTS 合成失败统一异常：消息必须含失败原因与上下文，不允许用空文件冒充成功。
class TtsError(RuntimeError):
    pass


class OmlxTts:
    # oMLX 本地神经 TTS 适配器；voice / model_version 随 env 切换默认音色或克隆音色。
    # 适配器身份与契约声明（薄适配器不含业务逻辑）
    name, requires_core = "omlx-tts", "^0.1"
    DEFAULT_MODEL_VERSION = DEFAULT_MODEL
    rate_map = {"slow": 0.85, "normal": 1.0, "fast": 1.25}
    capabilities = {"zero_shot_clone": True, "streaming": False, "max_rate_hz": TARGET_RATE}

    def __init__(self, base_url: str = BASE_URL, model: Optional[str] = None, timeout_seconds: float = 600,
                 ffmpeg: Optional[str] = None, opener: Optional[Any] = None, runner: Optional[Any] = None) -> None:
        # 参数校验失败抛 ValueError，不静默回落到默认值；timeout 需有限正数（0.6B 冷启动实测 68 s）。
        base, model = (base_url or "").strip().rstrip("/"), (model or os.environ.get("VOX_TTS_MODEL") or DEFAULT_MODEL).strip()
        if not base.startswith(("http://", "https://")):
            raise ValueError(f"base_url 必须是 http(s) 地址，实际为 {base_url!r}（oMLX 实测端点为 {BASE_URL}）")
        if not model or not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(f"model/timeout_seconds 非法: model={model!r} timeout={timeout_seconds!r}（须非空且有限正数）")
        self.base_url, self.model, self.timeout, self._ffmpeg, self._opener, self._runner = (
            base, model, timeout_seconds, ffmpeg if ffmpeg is not None else (os.environ.get("VOX_FFMPEG") or DEFAULT_FFMPEG), opener, runner)

    def _clone_ref(self) -> Optional[Path]:
        # 无 VOX_TTS_REF 返回 None（走默认音色）；env 不完整或文件无效即 fail-closed。
        # WHY 不回落默认音色：回落表现为「突然换了个声音」，事后无法定位——正是本层要消除的静默降级。
        ref, ref_text = os.environ.get("VOX_TTS_REF"), os.environ.get("VOX_TTS_REF_TEXT")
        if not ref:
            return None
        ref_path = Path(ref).expanduser()
        if not (ref_text or "").strip():
            raise TtsError("克隆模式不完整：VOX_TTS_REF 已设置但 VOX_TTS_REF_TEXT 缺失或为空白（参考音频必须配转写）")
        if not ref_path.is_file() or ref_path.stat().st_size == 0:
            raise TtsError(f"参考音频不存在或为空: {ref}")
        return ref_path

    @property
    def voice(self) -> str:
        # 默认 "default"；克隆为 clone-<参考音频 sha256 前 8 位>，指纹里永不出现路径。
        ref = self._clone_ref()
        return DEFAULT_VOICE if ref is None else CLONE_PREFIX + hashlib.sha256(ref.read_bytes()).hexdigest()[:8]

    @property
    def model_version(self) -> str:
        # 克隆音色取 clone 专用版本，使资产指纹与默认音色互不通用。
        return CLONE_MODEL_VERSION if self.voice.startswith(CLONE_PREFIX) else self.DEFAULT_MODEL_VERSION

    def rate_value(self, rate_key: str) -> float:
        # 语义档位 → 服务端 speed；未知档位抛 TtsError 且消息含该档名，不回落。
        if rate_key not in self.rate_map:
            raise TtsError(f"未知语速档位: {rate_key!r}（仅支持: {', '.join(sorted(self.rate_map))}）")
        return self.rate_map[rate_key]

    def endpoint(self) -> str:
        # 完整合成端点 URL（base_url 已剥尾随斜杠，不会出现双斜杠）。
        return self.base_url + SPEECH_ENDPOINT

    def _payload(self, text: str, rate: float) -> dict:
        # 克隆请求体带 base64 ref_audio + ref_text，默认音色只带 voice（实现见 postprocess）。
        ref = self._clone_ref()
        return postprocess.build_payload(self.model, text, rate, None if ref is None else ref.read_bytes())

    def synthesize(self, text: str, out_path, rate_key: str = "normal") -> None:
        # 合成并落盘 16kHz/单声道/16-bit WAV；父目录自动创建，任何失败都抛 TtsError。
        if not text:  # 空文本不合成：产出静音 WAV 会被下游当成成功，等于静默降级
            raise TtsError("空文本无法合成语音（禁止合成静音）")
        out = Path(out_path)
        try:  # 父目录自动创建；目录已存在时给出可定位错误
            out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TtsError(f"创建输出目录失败: {out.parent} ({exc})") from exc
        if out.exists() and out.is_dir():
            raise TtsError(f"目标路径是已存在的目录而非文件: {out}")
        raw = self._fetch(self._payload(text, self.rate_value(rate_key)))
        # 定位 ffmpeg；解析不到即 fail-closed，不猜别的工具。
        ffmpeg = shutil.which(self._ffmpeg)
        if ffmpeg is None:
            raise TtsError(f"ffmpeg 不可用: {self._ffmpeg!r}（可在 PATH 提供或以 env VOX_FFMPEG 覆盖）——服务出 24 kHz，缺 ffmpeg 无法降到契约 16 kHz")
        with tempfile.TemporaryDirectory(prefix="vox_tts_omlx_") as tmp:
            src = Path(tmp) / "raw.wav"
            src.write_bytes(raw)
            # 一步归一：ffmpeg 降到契约 16k + 首尾静音裁剪 + 峰值归一（神经引擎产物
            # 不满足预铸质检阈值，归一是合成侧的活）。runner 沿用注入替身以保持可测性。
            normalized = postprocess.normalize_from(src, ffmpeg=ffmpeg, runner=self._runner)
            self._assert_contract(normalized)
            try:
                shutil.move(str(normalized), str(out))
            except OSError as exc:
                raise TtsError(f"落盘移动失败: {normalized} -> {out} ({exc})") from exc

    def _fetch(self, payload: dict) -> bytes:
        # POST 合成请求并读响应；引擎装卸窗口的 500 busy 与连接类故障做有界重试，
        # 超限仍 fail-closed（不静默返回空产物），末次错误带重试次数便于定位。
        req = urllib.request.Request(self.endpoint(), data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        attempt = 0
        while True:
            try:
                return self._fetch_once(req)
            except TtsError as exc:
                if not self._retryable(exc) or attempt >= MAX_RETRIES:
                    raise
                self._sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                attempt += 1

    def _retryable(self, exc: TtsError) -> bool:
        # 只对「引擎瞬时态」重试：装卸窗口的 500 busy、连接/超时类故障。
        # 4xx（未知 voice、缺 ref_text）与响应过大是确定性错误，不重试。
        text = str(exc)
        return ("HTTP 5" in text or any(m in text for m in BUSY_MARKERS)
                or any(s in text for s in ("无法连接", "超时", "TLS", "读取", "HTTPException")))

    def _sleep(self, seconds: float) -> None:
        import time
        time.sleep(seconds)

    def _fetch_once(self, req: urllib.request.Request) -> bytes:
        # 单次 POST 与响应读取；连接/超时/读取期故障按阶段表收口，HTTPError 带 body 片段。
        try:
            response = (self._opener if self._opener is not None else urllib.request.urlopen)(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                snippet = exc.read()[:200].decode("utf-8", "replace")
            except (OSError, HttpLibError):
                snippet = ""
            raise TtsError(f"TTS 服务端返回 HTTP {exc.code}（urllib.error.HTTPError）→ {self.endpoint()}: {exc.reason} {snippet!r}") from exc
        except BaseException as exc:
            raise self._classified(exc, HTTP_STAGES, "TTS 无法连接")
        try:
            raw = response.read()
        except BaseException as exc:
            raise self._classified(exc, READ_STAGES, "TTS 响应读取失败")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TtsError(f"TTS 响应过大（{len(raw)} 字节 > {MAX_RESPONSE_BYTES}），疑似服务端串流")
        return raw

    def _classified(self, exc: BaseException, stages: dict, stage_kind: str) -> Exception:
        # 按阶段表把故障归类为 TtsError（消息含阶段与端点）；未归类时按 OSError 兜底，其余原样返回。
        label = stages.get(type(exc))
        if label is not None:
            return TtsError(f"{label.format(t=self.timeout)} → {self.endpoint()}")
        if isinstance(exc, OSError):
            return TtsError(f"{stage_kind}（{type(exc).__name__}）→ {self.endpoint()}")
        return exc

    def _assert_contract(self, path: Path) -> None:
        # 用 wave 复验产物格式与非空——不靠信任 ffmpeg 或归一化步骤的输出。
        try:
            with wave.open(str(path), "rb") as wf:
                got = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes())
        except (wave.Error, EOFError, OSError) as exc:
            raise TtsError(f"产物不是合法 WAV: {path} ({exc})") from exc
        if got[:3] != TARGET_FMT or got[3] == 0:
            raise TtsError(f"产物格式不符（要求 16kHz/单声道/16-bit 且非空，实际 {got[2]}Hz/{got[0]}声道/{got[1] * 8}-bit/{got[3]} 帧）: {path}")
