"""
adapters.asr_omlx.adapter — oMLX 本地 ASR 薄适配器

职责：把 oMLX（:10099）暴露的 OpenAI 形状 POST /v1/audio/transcriptions 接进来，
      做 wav → 转写文本（16 kHz 单声道中文；探针实测模型 Qwen3-ASR-0.6B-8bit）。
不负责：不含业务逻辑；不做拼接与判定（runtime/）；不做评测口径（eval/）。

层边界（adapters/AGENTS.md §⑤）：只依赖 core/ 公开接口与标准库；
      HTTP 一律标准库 urllib（本卡明令不得新增第三方依赖）。

失败纪律：fail-closed——连接失败 / 超时 / 传输中断 / 非 2xx / 响应非 JSON /
      缺 text 字段一律抛 AsrError，消息含阶段（连接/响应/读取）与具体原因；
      绝不返回空串或 None 冒充成功（空串是 ASR 链路里最危险的假成功——
      调用方会当成"用户一句话没说"）。

读阶段同样收口（T14c 修 4）：response.read() 在 _open 返回之后才执行，
      传输中断（IncompleteRead）/ 连接重置（ConnectionResetError）/ 读取超时
      （裸 TimeoutError）都发生在这里，同样必须归 AsrError，不逃出分类。
"""

import json
import math
import os
import socket
import ssl
import urllib.error
import urllib.request
from http.client import HTTPException as HttpLibError
from http.client import IncompleteRead as HttpIncompleteRead
from pathlib import Path
from typing import Any, Optional

FIELD_FILE: str = "file"
FIELD_MODEL: str = "model"
FIELD_TEXT: str = "text"
# 默认模型名（探针实测可用；调用方可覆盖，评测方按环境指定）
DEFAULT_MODEL: str = "Qwen3-ASR-0.6B-8bit"
# 响应 text 最大容许长度：ASR 转写不应比源音频长太多，超长多半是服务端串流，
# 按不完整处理而非照收（不静默接受垃圾转写）
_MAX_TEXT_CHARS: int = 100000
# 转写接口形状（写进报告 env 供复核）
TRANSCRIPTION_ENDPOINT: str = "/v1/audio/transcriptions"


class AsrError(RuntimeError):
    """ASR 转写失败统一异常。

    消息必须含失败原因与具体上下文（异常类型名 / 状态码 / 响应片段），
    不允许吞掉错误上下文，也不允许用空串代替。
    """


class OmlxAsr:
    """oMLX 本地 ASR 适配器（OpenAI 形状音频转写接口）。

    属性：
        name:           适配器名（报告 env.adapter.name）
        model_version:  模型版本（参与指纹；换模型 = 旧报告不可比）
        synthetic:      False——本适配器是真机引擎（与 OfflineTts 的标记对偶）
        voice:          None——ASR 无音色概念（报告字段形状留位，供 bench 统一取用）
    """

    name: str = "omlx-asr"
    model_version: str = DEFAULT_MODEL
    synthetic: bool = False
    voice: Optional[str] = None

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:10099",
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 120,
        opener: Optional[Any] = None,
    ) -> None:
        """初始化（参数校验失败即抛 ValueError，不做静默回落到默认值）。

        参数：
            base_url:        服务根地址（可覆盖；尾随斜杠被剥掉，避免拼出双斜杠）
            model:           请求体 model 字段值（可覆盖；不得写死，评测方按环境指定）
            timeout_seconds: 单次请求超时（秒）；超时必须抛错而非默默重试
            opener:          可注入的 URL 打开器（默认 urllib.request.urlopen）。
                             测试替身在此注入——不联网、不起服务也能跑负例

        异常：
            ValueError: base_url 非 http(s) / model 为空 / timeout 非有限正数
        """
        base = (base_url or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError(
                f"base_url 必须是 http(s) 地址，实际为 {base_url!r}"
                f"（oMLX 探针实测端点为 http://127.0.0.1:10099）"
            )
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"model 必须是非空字符串，实际为 {model!r}")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            # T14c 修 4：NaN 必须被拒——nan <= 0 是 False，校验会放过它，
            # 随后 urlopen(timeout=nan) 内部才炸裸 ValueError，逃出 AsrError 契约。
            raise ValueError(
                f"timeout_seconds 必须是有限正数，实际为 {timeout_seconds!r}"
            )
        self.base_url = base
        self.model = model
        self.timeout = timeout_seconds
        self._opener = opener

    def endpoint(self) -> str:
        """返回完整转写端点 URL（base_url + 协议路径）。

        边界：base_url 已在 __init__ 剥掉尾斜杠，因此不会出现双斜杠。
        """
        return self.base_url + TRANSCRIPTION_ENDPOINT

    def _build_body(self, wav_bytes: bytes, filename: str) -> tuple:
        """手工构造 multipart/form-data 请求体。

        WHY 手工拼而不是第三方库：本卡明令不得新增第三方依赖，
        且 multipart 格式很简单（边界行 + 两条 part）。

        参数：wav_bytes 为 wav 字节；filename 为 part 内文件名（服务端可能据此判格式）。
        返回：(请求体字节, Content-Type 头字符串)。
        """
        boundary = "----voxtypeasr" + os.urandom(8).hex()
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{FIELD_FILE}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode("utf-8")
        tail = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{FIELD_MODEL}"\r\n'
            f"\r\n{self.model}\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        return head + wav_bytes + b"\r\n" + tail, f"multipart/form-data; boundary={boundary}"

    def _open(self, req: "urllib.request.Request") -> Any:
        """发请求，把「连接层」与「协议层」故障统一收口到 AsrError。

        WHY 在这里统一处理 socket/urllib 异常：调用方只该关心 AsrError 一种类型，
        且报告里要能按类型统计；消息里保留异常类型名——排查时"超时"与"连接拒绝"
        是完全不同的原因。
        """
        # 测试替身入口：形状与 urllib.request.urlopen 一致（req -> http 响应对象）。
        # WHY 替身也放进同一个 try 块：适配器只承诺收口 urlopen 的异常形状，
        # 若替身走旁路，「注入 socket.timeout 却得到裸 TimeoutError」这条路径
        # 就等于测了个假东西——负例必须真的穿过本文件的异常分类逻辑。
        opener = self._opener if self._opener is not None else urllib.request.urlopen
        try:
            return opener(req, timeout=self.timeout)
        except socket.timeout as exc:
            raise AsrError(
                f"ASR 请求超时（socket.timeout，{self.timeout} 秒）"
                f"→ {self.endpoint()}: {exc}"
            ) from exc
        except ssl.SSLError as exc:
            raise AsrError(
                f"ASR TLS 错误（ssl.SSLError）→ {self.endpoint()}: {exc}"
            ) from exc
        except urllib.error.HTTPError as exc:
            raise AsrError(
                f"ASR 服务端返回 HTTP {exc.code}（urllib.error.HTTPError）"
                f"→ {self.endpoint()}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            # URLError.reason 在连接被拒时是 ConnectionRefusedError 实例——
            # 把原始 reason 一起带上，不吞上下文
            raise AsrError(
                f"ASR 无法连接（urllib.error.URLError）→ {self.endpoint()}: "
                f"{exc.reason!r}"
            ) from exc
        except HttpIncompleteRead as exc:
            # http.client.IncompleteRead：响应传了一半连接就断（服务端崩溃/网关掐线）。
            # 必须排在 HttpLibError 前面——它是 HTTPException 的子类，写反了就归不到这里。
            raise AsrError(
                f"ASR 响应传输中断（http.client.IncompleteRead）"
                f"→ {self.endpoint()}: {exc}"
            ) from exc
        except HttpLibError as exc:
            # http.client.HTTPException：响应头非法 / 连接中途断开等协议层故障
            raise AsrError(
                f"ASR 响应协议错误（http.client.HTTPException）"
                f"→ {self.endpoint()}: {exc}"
            ) from exc
        except OSError as exc:
            # 连接层其余故障（URLError 已单独归口，不会走到这里）：
            # 连接被重置（ConnectionResetError）、网络不可达、EINTR…
            # 一律按"无法连接"处理，不逃出 AsrError 契约。
            raise AsrError(
                f"ASR 无法连接（{type(exc).__name__}）→ {self.endpoint()}: {exc}"
            ) from exc

    def transcribe(self, wav_path: Any) -> str:
        """转写一个 wav 文件，返回识别文本（去首尾空白）。

        参数：wav_path 为 wav 文件路径（str 或 Path）。
        返回：转写文本；协议上非空——空串视为失败（见下）。

        边界（全部 fail-closed 抛 AsrError，绝不返回空串/None 假成功）：
            - 路径不是文件 / 0 字节：本地即失败，不发网络请求
            - 连接失败 / 超时 / HTTP 非 2xx：见 _open
            - 响应体不是合法 JSON：服务端形状不符（可能返回了 HTML 错误页）
            - 响应 JSON 不是对象 / 缺 text 字段 / text 不是字符串 / 为空或仅空白：
              一律按"未得到转写"处理
        """
        wav = Path(wav_path)
        if not wav.is_file():
            raise AsrError(f"ASR 输入文件不存在或不是文件: {wav}")
        wav_bytes = wav.read_bytes()
        if not wav_bytes:
            raise AsrError(f"ASR 输入文件为 0 字节: {wav}（空文件无内容可转写）")

        body, content_type = self._build_body(wav_bytes, wav.name)
        req = urllib.request.Request(
            self.endpoint(),
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )

        response = self._open(req)
        try:
            raw = response.read()
        except HttpIncompleteRead as exc:
            # T14c 修 4：传输中断在 read 阶段最容易发生（连接建立成功、响应传一半断）。
            # 必须在 transcribe 里再归口一次——_open 只收口 urlopen 自身的异常，
            # response.read() 是在 _open 返回之后才执行的。
            raise AsrError(
                f"ASR 响应读取中断（http.client.IncompleteRead）"
                f"→ {self.endpoint()}: {exc}"
            ) from exc
        except HttpLibError as exc:
            # 读阶段其余协议层故障（http.client.HTTPException 非 IncompleteRead 子类）。
            raise AsrError(
                f"ASR 响应读取协议错误（http.client.HTTPException）"
                f"→ {self.endpoint()}: {exc}"
            ) from exc
        except TimeoutError as exc:
            # socket.timeout 已随 _open 归口为「请求超时」；这里的裸 TimeoutError
            # 是 read 阶段的超时，同样必须收口成 AsrError。
            raise AsrError(
                f"ASR 响应读取超时（TimeoutError）→ {self.endpoint()}: {exc}"
            ) from exc
        except OSError as exc:
            # 连接被重置（ConnectionResetError）/ 网络中断等读取期故障。
            raise AsrError(
                f"ASR 响应读取失败（{type(exc).__name__}）→ {self.endpoint()}: {exc}"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            # 响应非 JSON：常见于网关返回 HTML 错误页——带上前 120 字节帮助定位
            snippet = raw[:120].decode("utf-8", errors="replace")
            raise AsrError(
                f"ASR 响应不是合法 JSON（{type(exc).__name__}）"
                f"→ {self.endpoint()}，响应前 120 字节: {snippet!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise AsrError(
                f"ASR 响应 JSON 不是对象（实际 {type(payload).__name__}）"
                f"→ {self.endpoint()}: {str(payload)[:120]!r}"
            )
        if FIELD_TEXT not in payload:
            raise AsrError(
                f"ASR 响应缺 {FIELD_TEXT!r} 字段（实际字段: {sorted(payload)}）"
                f"→ {self.endpoint()}"
            )
        text = payload[FIELD_TEXT]
        if not isinstance(text, str):
            raise AsrError(
                f"ASR 响应 {FIELD_TEXT} 字段不是字符串（实际 {type(text).__name__}）"
                f"→ {self.endpoint()}"
            )
        stripped = text.strip()
        if not stripped:
            # 空转写 = 未得到结果：报 AsrError 而不是返回空串，
            # 否则调用方（eval.readback）会把空串当成"识别为无声"继续算 CER
            raise AsrError(
                f"ASR 响应 {FIELD_TEXT!r} 为空字符串"
                f"（不返回空串冒充成功）→ {self.endpoint()}"
            )
        if len(stripped) > _MAX_TEXT_CHARS:
            raise AsrError(
                f"ASR 响应 {FIELD_TEXT} 超长（{len(stripped)} 字符 > "
                f"{_MAX_TEXT_CHARS}），疑似服务端串流，按不完整处理"
            )
        return stripped
