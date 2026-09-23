# adapters.tts_omlx.adapter — OpenAI 兼容 /v1/audio/speech 客户端（薄适配器，含零样本克隆重铸）。
# 定位：说的是 OpenAI speech 协议，谁实现这个协议就适配谁——本机 oMLX、Linux 上的
#   vLLM-Omni / SGLang-Omni，或任意云服务。目录名沿用 tts_omlx 只是历史（不专用 oMLX）。
#   基址与音色都不写死：VOX_TTS_ENDPOINT（基址）/ VOX_TTS_VOICE（音色）可覆盖，缺省回落常量。
# 服务出 24 kHz，适配器内用 ffmpeg 降到契约的 16 kHz/单声道/16-bit（runtime.audio 只认这个）。
# 两条路径都由 env 决定，代码里不写死任何参考音频：
#   a) 默认音色 —— 不发 ref_audio，带 voice=env VOX_TTS_VOICE 或 "default"；
#   b) 零样本克隆 —— env VOX_TTS_REF（参考音频）+ VOX_TTS_REF_TEXT（转写），缺一即 fail-closed；
#      voice 取 clone-<参考音频 sha256 前 8 位>（指纹里不出现路径；克隆优先于 VOX_TTS_VOICE），
#      model_version 为 qwen3-tts-0.6b-base-clone（换音色即换指纹，旧资产自动失效）。
# 失败纪律 fail-closed：缺 ffmpeg / 空文本 / 未知语速档 / 连接与超时 / 非 2xx / 响应不是合法 WAV /
# 降采样失败 / 落盘失败一律抛 TtsError，绝不返回静音、空文件或假成功。
# 拆分（T41 方案 A 铺路）：HTTP 传输与请求体在 transport.OmlxTtsTransport（有界重试也在那一层），
#   本文件只留编排——参数校验、音色/指纹决策、ffmpeg 归一、落盘与契约复验。
import hashlib
import math
import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Optional

from . import postprocess
from .transport import (MAX_RETRIES, NORMALIZE_FAIL_MARKER, OmlxTtsTransport,
                        RETRY_DELAYS, TtsError)

BASE_URL = "http://127.0.0.1:10099"
SPEECH_ENDPOINT = "/v1/audio/speech"
DEFAULT_MODEL = "Qwen3-TTS-12Hz-0.6B-Base-bf16"
CLONE_MODEL_VERSION = "qwen3-tts-0.6b-base-clone"
DEFAULT_VOICE = "default"
DEFAULT_FFMPEG = "ffmpeg"
TARGET_FMT = (1, 2, 16000)
TARGET_RATE = TARGET_FMT[2]
CLONE_PREFIX = "clone-"


class OmlxTts:
    # OpenAI 兼容 /v1/audio/speech 客户端（oMLX / vLLM-Omni / SGLang-Omni / 云服务同一协议）；
    # voice / model_version 随 env 切换默认音色或克隆音色。
    # 适配器身份与契约声明（薄适配器不含业务逻辑）
    name, requires_core = "omlx-tts", "^0.1"
    DEFAULT_MODEL_VERSION = DEFAULT_MODEL
    rate_map = {"slow": 0.85, "normal": 1.0, "fast": 1.25}
    capabilities = {"zero_shot_clone": True, "streaming": False, "max_rate_hz": TARGET_RATE}

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None, timeout_seconds: float = 600,
                 ffmpeg: Optional[str] = None, opener: Optional[Any] = None, runner: Optional[Any] = None) -> None:
        # 参数校验失败抛 ValueError，不静默回落到默认值；timeout 需有限正数（0.6B 冷启动实测 68 s）。
        # 基址兜底顺序：参数 → env VOX_TTS_ENDPOINT → 常量 BASE_URL（有序可测）；给错值仍 ValueError，不静默回落。
        # WHY 用 get(k, 默认值) 而非 `env or 默认值`：**设了但为空**的 env（`export VOX_TTS_ENDPOINT="$TTS_HOST"`
        # 而 TTS_HOST 未定义、或 systemd `Environment=VOX_TTS_ENDPOINT=`）在 `or` 下会被当成"未设"，
        # 于是请求**悄悄打到本机默认地址**——正是本仓要消除的静默换地址。空串交给下面那道 http 校验响亮拦下。
        base = (base_url if base_url is not None else os.environ.get("VOX_TTS_ENDPOINT", BASE_URL)).strip().rstrip("/")
        model = (model or os.environ.get("VOX_TTS_MODEL") or DEFAULT_MODEL).strip()
        if not base.startswith(("http://", "https://")):
            raise ValueError(f"base_url 必须是 http(s) 地址，实际为 {base!r}（env VOX_TTS_ENDPOINT / 常量 {BASE_URL}）")
        if not model or not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(f"model/timeout_seconds 非法: model={model!r} timeout={timeout_seconds!r}（须非空且有限正数）")
        self.base_url, self.model, self.timeout, self._ffmpeg, self._opener, self._runner = (
            base, model, timeout_seconds, ffmpeg if ffmpeg is not None else (os.environ.get("VOX_FFMPEG") or DEFAULT_FFMPEG), opener, runner)
        self._transport = OmlxTtsTransport(opener=opener, timeout=timeout_seconds, endpoint=self.endpoint())
        self._normalize_retries = 0  # 归一失败的累计重采次数（供构建侧/测试观测；每次重试必写 stderr）

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
        # 默认音色取 env VOX_TTS_VOICE（缺省回落 DEFAULT_VOICE）；克隆优先：clone-<参考音频 sha256 前 8 位>，指纹里永不出现路径。
        ref = self._clone_ref()
        return (os.environ.get("VOX_TTS_VOICE") or DEFAULT_VOICE) if ref is None else CLONE_PREFIX + hashlib.sha256(ref.read_bytes()).hexdigest()[:8]

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
        # 克隆请求体带 base64 ref_audio + ref_text 且不带 voice；默认音色只带 voice，
        # 并以 self.voice 为准（env VOX_TTS_VOICE 由此真的到达服务端，不是发了等于没发）。
        ref = self._clone_ref()
        payload = postprocess.build_payload(self.model, text, rate, None if ref is None else ref.read_bytes())
        return {**payload, "voice": self.voice} if ref is None else payload

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
        # 定位 ffmpeg；解析不到即 fail-closed，不猜别的工具。放请求之前：缺 ffmpeg 属确定性
        # 失败，连一次服务端请求都不该发（否则白白消耗采样型引擎的算力，还把重试语义弄混）。
        ffmpeg = shutil.which(self._ffmpeg)
        if ffmpeg is None:
            raise TtsError(f"ffmpeg 不可用: {self._ffmpeg!r}（可在 PATH 提供或以 env VOX_FFMPEG 覆盖）——服务出 24 kHz，缺 ffmpeg 无法降到契约 16 kHz")
        payload = self._payload(text, self.rate_value(rate_key))
        with tempfile.TemporaryDirectory(prefix="vox_tts_omlx_") as tmp:
            src = Path(tmp) / "raw.wav"
            normalized = self._normalize_with_retries(src, ffmpeg, payload, text)
            self._assert_contract(normalized)
            try:
                shutil.move(str(normalized), str(out))
            except OSError as exc:
                raise TtsError(f"落盘移动失败: {normalized} -> {out} ({exc})") from exc

    def _normalize_with_retries(self, src: Path, ffmpeg: str, payload: dict, text: str) -> Path:
        # 采样型引擎的归一失败是随机态：同一条文本重采会得到**不同的新产物**，所以重试必须
        # 重新向服务端请求一次合成——只对同一份原始字节重复归一是确定性操作，重试一万次也不会变。
        # 有界：与传输层重试共用 transport.MAX_RETRIES / RETRY_DELAYS，超限仍 fail-closed。
        # 留痕：每次重采都打一行 stderr 警告（格式固定，供门禁按「重试」计数）；成功的那条
        # 不静默通过——第 1 次警告就是它的痕迹。确定性失败在这里一次都不重试，消息原样上抛。
        attempt = 0
        while True:
            src.write_bytes(self._transport.fetch(payload))
            try:
                return postprocess.normalize_from(src, ffmpeg=ffmpeg, runner=self._runner)
            except TtsError as exc:
                if not self._transport.is_normalization_failure(exc):
                    raise
                if attempt >= MAX_RETRIES:
                    # 耗尽仍失败时把重试次数并进消息：它会随 compiler/prebake.py 的
                    # failed.reason 自动进构建报告（那边是 f"{type(e).__name__}: {e}"）。
                    raise TtsError(f"{exc}（归一失败重试已耗尽：共重试 {attempt} 次，上限 {MAX_RETRIES}）") from exc
                self._sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                attempt += 1
                self._normalize_retries += 1
                self._warn_normalize_retry(text, attempt, str(exc))

    @staticmethod
    def _sleep(seconds: float) -> None:
        import time
        time.sleep(seconds)

    @staticmethod
    def _warn_normalize_retry(text: str, attempt: int, reason: str) -> None:
        # 固定格式（门禁按「重试」行数计数，不可改字段顺序）：
        #   [TTS 归一重试] 重试 N/MAX: <文本摘要> | <归一失败原因>
        # 文本摘要取前 24 个 Unicode 码点（CJK 一个汉字就是一个码点），换行压平防伪造第二条记录。
        # 用 sys.stderr.write 而非 print(file=sys.stderr)：print 在调用点绑定 stderr 对象，
        # 测试替身（contextlib.redirect_stderr 或 mock.patch 替换 sys.stderr）都抓不到它——
        # 而这条警告是唯一的重试留痕，必须可被观测。
        sample = "".join(c for c in text if c != "\n")[:24] or "<空文本>"
        sys.stderr.write(f"[TTS 归一重试] 重试 {attempt}/{MAX_RETRIES}: {sample} | {reason}\n")

    def _assert_contract(self, path: Path) -> None:
        # 用 wave 复验产物格式与非空——不靠信任 ffmpeg 或归一化步骤的输出。
        try:
            with wave.open(str(path), "rb") as wf:
                got = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes())
        except (wave.Error, EOFError, OSError) as exc:
            raise TtsError(f"产物不是合法 WAV: {path} ({exc})") from exc
        if got[:3] != TARGET_FMT or got[3] == 0:
            raise TtsError(f"产物格式不符（要求 16kHz/单声道/16-bit 且非空，实际 {got[2]}Hz/{got[0]}声道/{got[1] * 8}-bit/{got[3]} 帧）: {path}")
