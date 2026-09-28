# adapters.tts_omlx.transport — HTTP 传输与请求体层（从 adapter.py 拆出，T41 方案 A 铺路）。
# 定位：只管「把 JSON POST 给 /v1/audio/speech、读回字节、按阶段归类故障、做有界重试」，
#   不碰音色/指纹/克隆语义/落盘/契约复验——那些是 adapter.py 的编排职责。
# 与 adapter 的关系：本模块不 import adapter（若那样，TtsError 的权威定义会漂移到传输层，
#   并让 postprocess 里 `from .adapter import TtsError` 的懒导入变成真循环）——import 方向单向
#   adapter → transport，无环。端点路径在本层自持常量（与 adapter.SPEECH_ENDPOINT 同值、无耦合）。
# 失败纪律 fail-closed：非 2xx / 响应过大 / 超限仍抛 TtsError，绝不返回空字节冒充成功。
import errno
import json
import socket
import ssl
import urllib.error
import urllib.request
from http.client import HTTPException as HttpLibError
from http.client import IncompleteRead as HttpIncompleteRead
from typing import Any, Optional


# /v1/audio/speech 的路径常量（端点拼装的权威源是 adapter.endpoint()，这里只承载路径段）。
SPEECH_ENDPOINT = "/v1/audio/speech"


# TTS 合成失败统一异常：消息必须含失败原因与上下文，不允许用空文件冒充成功。
class TtsError(RuntimeError):
    pass

# 有界重试：oMLX 在模型装卸/换载窗口内会以 HTTP 500 拒绝请求
# （实测文案 "Model '…' is busy; cannot start work while unload is pending…"），
# 那是引擎瞬时态而非失败合成——重试而非报错，但必须有界，超限仍 fail-closed。
MAX_RETRIES = 5
RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0, 30.0)
BUSY_MARKERS = ("is busy;", "unload is pending", "cannot start work")
# 阶段故障 → 消息模板（表驱动：逐条 except 会把文件推过 adapters/AGENTS.md §② 的 150 行标准）。
# 文案冻结（对外消息，测试可能断言）：重试判定**不**靠这些字符串，走原异常类型（见 _retryable）。
HTTP_STAGES = {socket.timeout: "TTS 请求超时（socket.timeout，{t} 秒）", ssl.SSLError: "TTS TLS 错误（ssl.SSLError）",
               urllib.error.URLError: "TTS 无法连接（urllib.error.URLError）",
               HttpLibError: "TTS 响应协议错误（http.client.HTTPException）"}
READ_STAGES = {HttpIncompleteRead: "TTS 响应读取中断（http.client.IncompleteRead）",
               HttpLibError: "TTS 响应读取协议错误（http.client.HTTPException）",
               TimeoutError: "TTS 响应读取超时（TimeoutError）"}
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
# 归一自检失败的两个标记：`产物归一失败: ` 是 postprocess.normalize_from 对**所有**归一内失败的
# 统一包装（ffmpeg 退出码非零 / 产物不是合法 WAV / 全静音这类**确定性**失败也带它）；
# 真正由采样浮动引起的随机失败只有首尾「静音仍超限」（T40 实测 24k 原始首静音 4.0–666.2 ms 浮动）。
# 所以重采判据必须**双条件**——只认前缀会把确定性失败也空转 5 次退避
# （T41 收尾批实测：垃圾响应 / ffmpeg 非零退出 / 伪造格式三条用例各真睡 67 秒，docs/13 §五#26）。
NORMALIZE_FAIL_MARKER = "产物归一失败"
NORMALIZE_RETRY_MARKER = "静音仍超限"

# T61：连接期**瞬时态**的 errno 集合。判定按 errno 而非中文文案——errno 是 OS 给的分类，
# 与本机 locale / 文案无关。ECONNREFUSED / EHOSTDOWN / ENOTFOUND 这类**确定性**故障不在此列：
# 同一个错端点重试 5 次的结果与重试 1 次完全相同，那只是让终端静默 67 秒。
# EISCONN 用 getattr 兜底（BSD 系有、Linux 没有），保持零平台依赖。
RETRYABLE_ERRNOS = {
    errno.ECONNRESET,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
    errno.EPIPE,
    getattr(errno, "EISCONN", errno.ECONNRESET),
}
# 首抛（不重试）时附带的修复指引。文案固定：用户照这一句就能自己改对，不必猜。
# 必须提到 VOX_TTS_ENDPOINT——那是 adapter 端点的唯一 env 入口（构造参数传不进 CLI）。
FIX_HINT = ("端点不可用；请确认服务已在跑（或换一个在跑的端点），"
            "再以 env 指定：export VOX_TTS_ENDPOINT=http://<host>:<port>")


class OmlxTtsTransport:
    # /v1/audio/speech 的 HTTP 传输：POST 请求体 → 读响应字节 → 有界重试。
    # opener / timeout 由 adapter 注入（base_url 不复制过来，端点单一权威源在 adapter）。
    def __init__(self, opener: Optional[Any] = None, timeout: float = 600, endpoint: str = SPEECH_ENDPOINT) -> None:
        self._opener, self._timeout, self._endpoint = opener, timeout, endpoint

    @property
    def timeout(self) -> float:
        return self._timeout

    def endpoint(self) -> str:
        return self._endpoint

    @staticmethod
    def _errno_of(exc: BaseException) -> Optional[int]:
        # 取 errno：URLError 常把 errno 挂在 .reason 上（自身 errno 位为 None），两条路都试。
        for obj in (exc, getattr(exc, "reason", None)):
            value = getattr(obj, "errno", None)
            if isinstance(value, int):
                return value
        return None

    def _retryable(self, exc: BaseException) -> bool:
        # 只对**瞬时态**重试（T61）：装卸窗口的 HTTP 5xx / busy、超时、读中断、连接期瞬时 errno。
        # 判定必须基于**异常类型**（isinstance），不基于 str(exc) 里的中文子串——HTTP_STAGES 已把
        # 原异常翻译成中文文案，走文案匹配会让判定绑死在措辞上。两条调用路径都要认：
        #   · fetch() 传进来的是 TtsError，原异常挂在 __cause__（_classified 用 `raise ... from exc`）；
        #   · _classified() 传进来的是**原异常本身**，没有 __cause__。
        # 确定性故障一次都不重试：4xx（voice 拼错 / 缺 ref_text / 端点 404）、ECONNREFUSED、
        # DNS 失败、TLS 证书错误——重试一万次也不会变。
        cause = getattr(exc, "__cause__", None) or exc
        if isinstance(cause, urllib.error.HTTPError):
            return cause.code >= 500  # 5xx = 引擎瞬时态；4xx = 确定性
        if any(marker in str(exc) for marker in BUSY_MARKERS):
            # 服务端把「忙」写进响应体（有的实现连 2xx 都带，无法用类型判定）——保留文本判定。
            return True
        if isinstance(cause, (socket.timeout, TimeoutError)):
            return True  # 等待服务端响应超时：瞬时态
        if isinstance(cause, ssl.SSLError):
            return False  # TLS 握手 / 证书错误：确定性，首抛
        if isinstance(cause, HttpLibError):
            return True  # 读期协议错误 / 半截读（IncompleteRead 是其子类）：重试
        if isinstance(cause, urllib.error.URLError):
            # https:// 握手失败会被 urllib 包成 URLError(reason=SSLError)：按 reason 的真实类型判，
            # 不落到 errno 兜底（TLS 的 errno 位常是 0/None，判据不可靠）。
            if isinstance(getattr(cause, "reason", None), ssl.SSLError):
                return False
            return self._errno_of(cause) in RETRYABLE_ERRNOS
        if isinstance(cause, OSError):
            return self._errno_of(cause) in RETRYABLE_ERRNOS  # 连接期瞬时态才重试
        return False

    @staticmethod
    def is_normalization_failure(exc: BaseException) -> bool:
        # 只对「归一自检里随机类的静音超限」返回 True（编排层据此决定是否重新向服务端请求合成）。
        # 双条件缺一不可：在「产物归一失败」包装之内、且是「静音仍超限」——postprocess 的
        # ffmpeg 退出码非零 / 产物不是合法 WAV / 全静音等确定性失败带同一包装，
        # 重试一万次也不会变，不得重采（docs/13 §五#26 的实测教训）。
        text = str(exc)
        return NORMALIZE_FAIL_MARKER in text and NORMALIZE_RETRY_MARKER in text

    def _sleep(self, seconds: float) -> None:
        import time
        time.sleep(seconds)

    def _classified(self, exc: BaseException, stages: dict, stage_kind: str) -> Exception:
        # 按阶段表把故障归类为 TtsError（消息含阶段与端点）；未归类时按 OSError 兜底，其余原样返回。
        # 只有**连接阶段的确定性**故障才附修复指引：读阶段的确定性故障不该指用户去改端点，
        # 瞬时态（超时 / 5xx / busy）本来就会重试、同样不该指端点。判定用的还是原异常类型。
        # 修复指引只挂在「连不上服务端」这一类确定性故障上：读阶段的确定性故障不该指用户改端点，
        # 瞬时态（超时 / 5xx / busy）本来就会重试、同样不该指端点。判定仍走原异常类型。
        connection_stage = stages is HTTP_STAGES
        hint = "" if (not connection_stage or self._retryable(exc)) else f"——{FIX_HINT}"
        label = stages.get(type(exc))
        if label is not None:
            return TtsError(f"{label.format(t=self._timeout)}{hint} → {self._endpoint}")
        if isinstance(exc, OSError):
            return TtsError(f"{stage_kind}（{type(exc).__name__}）{hint} → {self._endpoint}")
        return exc

    def fetch(self, payload: dict) -> bytes:
        # POST 合成请求并读响应；引擎装卸窗口的 500 busy 与**瞬时**连接类故障做有界重试，
        # 超限仍 fail-closed（不静默返回空产物），末次错误带重试次数便于定位。
        req = urllib.request.Request(self._endpoint, data=json.dumps(payload).encode("utf-8"),
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

    def _fetch_once(self, req: urllib.request.Request) -> bytes:
        # 单次 POST 与响应读取；连接/超时/读取期故障按阶段表收口，HTTPError 带 body 片段。
        try:
            response = (self._opener if self._opener is not None else urllib.request.urlopen)(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            try:
                snippet = exc.read()[:200].decode("utf-8", "replace")
            except (OSError, HttpLibError):
                snippet = ""
            raise TtsError(f"TTS 服务端返回 HTTP {exc.code}（urllib.error.HTTPError）→ {self._endpoint}: {exc.reason} {snippet!r}") from exc
        except BaseException as exc:
            # 必须 `from exc` 挂住原异常：_retryable 靠 __cause__ 里的**类型**判可重试，
            # 不挂就退化成「看中文文案」——那正是本卡要消掉的判据。
            raise self._classified(exc, HTTP_STAGES, "TTS 无法连接") from exc
        try:
            raw = response.read()
        except BaseException as exc:
            raise self._classified(exc, READ_STAGES, "TTS 响应读取失败") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TtsError(f"TTS 响应过大（{len(raw)} 字节 > {MAX_RESPONSE_BYTES}），疑似服务端串流")
        return raw
