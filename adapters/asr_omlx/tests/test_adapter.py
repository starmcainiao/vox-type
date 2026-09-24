"""
adapters.asr_omlx.tests.test_adapter — oMLX ASR 适配器（全离线）

覆盖（对应 T14 验收 3 与反空转条款）：
  1. 五条负例**各一条**：连接拒绝 / 超时 / HTTP 500 / 响应非 JSON / 缺 text 字段
     → 全部抛 AsrError，消息含具体原因，**无一返回空串或 None 假成功**；
  2. 正例：假响应注入 → 返回去首尾空白的转写文本；
  3. base_url / model / timeout 必须可覆盖（不写死）；请求体确实带上 model 与 wav 字节；
  4. 配置期负例：base_url 非 http(s) / model 空 / timeout 非正 → ValueError；
  5. 本地文件负例：路径不存在 / 0 字节 → AsrError（不发网络请求）。

纪律（反空转）：全部调用产品 API（OmlxAsr.transcribe / OmlxAsr.__init__），
  用「假响应」替身注入 opener——**不联网、不起 oMLX 服务**；
  替身不是产品件，也不自造编辑距离或分位数。
"""

import tempfile
import unittest
from pathlib import Path

from adapters.asr_omlx import AsrError, OmlxAsr

WAV_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 36
TEXT = "今天下午三点提醒你开会。"


# ---------------------------------------------------------------------------
# 假响应替身（测试专用，非产品件）
# ---------------------------------------------------------------------------
def json_bytes(obj) -> bytes:
    """把字典编码成 UTF-8 JSON 字节（替身响应体）。"""
    import json

    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class FakeResponse:
    """urllib 响应形状的替身：只实现 transcribe 用到的 read()。"""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def read(self) -> bytes:
        return self.body


# 预设故障 → urllib.request.urlopen 真实抛出的异常类型。
# WHY 由替身转换而不是直接抛目标异常：适配器的失败路径靠 `except urllib.error.*`
#   收口（对齐 T03 的 TtsError 口径），替身必须模拟 urlopen 的抛出形状，
#   否则测的是「直接抛 AsrError」而绕过了适配器的异常分类逻辑。
def refused_error() -> "urllib.error.URLError":
    """连接拒绝：urlopen 抛 URLError，reason 是 ConnectionRefusedError 实例。"""
    import socket
    import urllib.error

    return urllib.error.URLError(
        reason=ConnectionRefusedError(61, "Connection refused")
    )


def http_incomplete_read(partial: bytes) -> "http.client.IncompleteRead":
    """传输中断：http.client 在 read() 读到一半连接断了就抛 IncompleteRead。"""
    import http.client

    return http.client.IncompleteRead(partial, expected=4096)


def http_error(code: int, msg: str) -> "urllib.error.HTTPError":
    """HTTP 非 2xx：urlopen 抛 HTTPError（reason = HTTP 状态文本）。"""
    import urllib.error

    return urllib.error.HTTPError(
        url="http://127.0.0.1:10099/v1/audio/transcriptions",
        code=code, msg=msg, hdrs=None, fp=None,
    )


class Timeout:
    """超时故障的替身记号：弹出时由 StubOpener 抛 socket.timeout（urlopen 的真实类型）。"""


class Http500:
    """HTTP 500 故障的替身记号：弹出时由 StubOpener 抛 urllib.error.HTTPError。"""


class TruncatedResponse:
    """响应传一半就断的替身记号：弹出时返回一个 read() 抛 IncompleteRead 的响应对象。

    WHY 用替身记号而不是直接把 IncompleteRead 塞进 outcomes：传输中断发生在
    `response.read()`，即 `_open` 返回**之后**——直接塞进 outcomes 会测的是
    urlopen 阶段的中断，绕过了 transcribe 里的读阶段分类逻辑（与 Timeout 记号的
    处理方式一致）。
    """


class ReadResetResponse:
    """读取期连接被重置的替身记号：read() 抛 ConnectionResetError。"""


class ReadTimeoutResponse:
    """读取期超时的替身记号：read() 抛裸 TimeoutError（read 阶段，非 socket.timeout）。"""


class ReadProtoResponse:
    """读取期协议层故障的替身记号：read() 抛裸 http.client.HTTPException。"""


def _response_that_raises(exc) -> "FakeResponse":
    """构造一个 read() 时抛 exc 的响应替身。"""

    class Broken:
        status = 200

        def read(self) -> bytes:
            raise exc

    return Broken()


class StubOpener:
    """opener 替身：按调用次序弹出预设响应；记录收到的 Request。

    记录 Request 是为断言「请求确实带上了 model 与 wav 字节」——
    不读请求体就等于没测过这个适配器有没有在发对东西。
    """

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, req, timeout=None):
        """形状与 urllib.request.urlopen 一致（含 timeout 关键字）。"""
        self.requests.append(req)
        if not self.outcomes:
            raise AssertionError("StubOpener 的预设响应已用尽（测试构造错误）")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, Timeout):
            import socket

            raise socket.timeout("timed out")
        if isinstance(outcome, Http500):
            raise http_error(500, "Internal Server Error")
        if isinstance(outcome, TruncatedResponse):
            return _response_that_raises(
                http_incomplete_read(b'{"text": "hello,')
            )
        if isinstance(outcome, ReadResetResponse):
            return _response_that_raises(
                ConnectionResetError(104, "Connection reset by peer")
            )
        if isinstance(outcome, ReadTimeoutResponse):
            return _response_that_raises(TimeoutError("timed out reading response"))
        if isinstance(outcome, ReadProtoResponse):
            import http.client

            return _response_that_raises(http.client.HTTPException("bad response header"))
        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            raise outcome()
        return outcome


def make_adapter(outcomes, wav_dir, **kwargs) -> tuple:
    """构造一个带 StubOpener 的适配器，返回 (适配器, opener)。"""
    opener = StubOpener(outcomes)
    adapter = OmlxAsr(opener=opener, **kwargs)
    return adapter, opener, wav_dir


def wav_file(dir_path: Path, name: str = "a.wav") -> Path:
    """落一个最小 wav 占位文件（内容是占位字节，不校验音频格式）。"""
    path = dir_path / name
    path.write_bytes(WAV_BYTES)
    return path


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------
class TranscribeSuccessTest(unittest.TestCase):
    """假响应注入下 transcribe 返回去首尾空白的转写文本。"""

    def test_returns_stripped_text(self):
        """200 + {"text": "..."} → 返回 strip 后的文本。"""
        with tempfile.TemporaryDirectory() as tmp:
            wav = wav_file(Path(tmp))
            body = json_bytes({"text": " " + TEXT + " "})
            adapter, opener, _ = make_adapter([FakeResponse(body)], Path(tmp))

            result = adapter.transcribe(wav)

            self.assertEqual(result, TEXT, "应返回去掉首尾空白的转写文本")
            self.assertIsInstance(result, str)
            self.assertNotEqual(result, "")

    def test_request_carries_model_and_wav_bytes(self):
        """请求体必须带 model 字段值与 wav 原始字节（否则引擎收到的是空请求）。"""
        with tempfile.TemporaryDirectory() as tmp:
            wav = wav_file(Path(tmp))
            model = "Qwen3-ASR-0.6B-8bit"
            adapter, opener, _ = make_adapter(
                [FakeResponse(json_bytes({"text": TEXT}))], Path(tmp), model=model
            )

            adapter.transcribe(wav)

            req = opener.requests[0]
            self.assertEqual(req.get_method(), "POST")
            body = req.data
            self.assertIsInstance(req.get_header("Content-type"), str)
            self.assertIn("multipart/form-data; boundary=", req.get_header("Content-type"))
            self.assertIn(model.encode("utf-8"), body, "请求体必须含 model 字段值")
            self.assertIn(WAV_BYTES[:8], body, "请求体必须含 wav 文件字节")
            self.assertEqual(body.count(b'form-data; name="model"'), 1)

    def test_base_url_and_timeout_overridable(self):
        """base_url / model / timeout 全部可覆盖（卡明令不许写死）。"""
        adapter = OmlxAsr(
            base_url="http://10.0.0.5:9999/", model="other-model", timeout_seconds=7.5
        )
        self.assertEqual(adapter.base_url, "http://10.0.0.5:9999")
        self.assertEqual(adapter.model, "other-model")
        self.assertEqual(adapter.timeout, 7.5)
        self.assertEqual(
            adapter.endpoint(), "http://10.0.0.5:9999/v1/audio/transcriptions"
        )


class AdapterConfigTest(unittest.TestCase):
    """构造期参数校验：全部抛 ValueError，消息含实际值。"""

    def test_rejects_non_http_base_url(self):
        with self.assertRaises(ValueError) as cm:
            OmlxAsr(base_url="ftp://127.0.0.1:10099")
        self.assertIn("ftp://127.0.0.1:10099", str(cm.exception))
        self.assertIn("base_url", str(cm.exception))

    def test_rejects_empty_model(self):
        with self.assertRaises(ValueError) as cm:
            OmlxAsr(model="  ")
        self.assertIn("model", str(cm.exception))

    def test_rejects_non_positive_timeout(self):
        with self.assertRaises(ValueError) as cm:
            OmlxAsr(timeout_seconds=0)
        self.assertIn("timeout_seconds", str(cm.exception))
        self.assertIn("0", str(cm.exception))

    def test_synthetic_marker_is_false(self):
        """真机适配器必须显式声明 synthetic=False（T08b 审计 #2：忘打标记的替身会被误认为真机）。"""
        self.assertIs(OmlxAsr().synthetic, False)
        self.assertEqual(OmlxAsr().name, "omlx-asr")


class LocalFileErrorTest(unittest.TestCase):
    """本地输入文件错误：不发网络请求即抛 AsrError。"""

    def test_missing_file_raises(self):
        opener = StubOpener([])
        adapter = OmlxAsr(opener=opener)
        with self.assertRaises(AsrError) as cm:
            adapter.transcribe("/definitely/not/here.wav")
        self.assertIn("/definitely/not/here.wav", str(cm.exception))
        self.assertEqual(opener.requests, [], "文件不存在时不得发网络请求")

    def test_zero_byte_file_raises_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.wav"
            path.write_bytes(b"")
            opener = StubOpener([])
            adapter = OmlxAsr(opener=opener)
            with self.assertRaises(AsrError) as cm:
                adapter.transcribe(path)
            self.assertIn("0 字节", str(cm.exception))
            self.assertEqual(opener.requests, [], "本地即失败时不得发网络请求")


# ---------------------------------------------------------------------------
# 五条失败路径（验收 3，各一条）
# ---------------------------------------------------------------------------
class FailurePathTest(unittest.TestCase):
    """五种服务端/网络故障 → 一律 AsrError，消息含具体原因，无一返回空串或 None。"""

    def _assert_asr_error(self, outcomes, keyword_checks):
        """统一断言：抛 AsrError、消息含所有关键字、结果不是空串/None。"""
        with tempfile.TemporaryDirectory() as tmp:
            wav = wav_file(Path(tmp))
            adapter, opener, _ = make_adapter(outcomes, Path(tmp))
            with self.assertRaises(AsrError) as cm:
                adapter.transcribe(wav)
            msg = str(cm.exception)
            for keyword in keyword_checks:
                self.assertIn(
                    keyword, msg,
                    f"AsrError 消息缺少关键上下文 {keyword!r}（实际消息: {msg}）",
                )
            # 关键：异常路径不得「返回」空串或 None 冒充成功
            self.assertNotEqual(msg, "")
            self.assertNotIsInstance(msg, type(None))
            return msg

    def test_connection_refused(self):
        """连接拒绝（URLError + ConnectionRefusedError.reason）。"""
        msg = self._assert_asr_error([refused_error()], ["URLError", "Connection refused"])
        self.assertIn("无法连接", msg)

    def test_timeout(self):
        """超时（socket.timeout）→ 消息含超时秒数。"""
        msg = self._assert_asr_error([Timeout()], ["socket.timeout", "超时"])
        self.assertIn("120", msg, "消息应含配置的超时秒数，便于定位是慢还是挂了")

    def test_http_500(self):
        """HTTP 500（HTTPError）→ 消息含状态码。"""
        msg = self._assert_asr_error([Http500()], ["HTTP 500", "HTTPError"])
        self.assertIn("Internal Server Error", msg)

    def test_response_not_json(self):
        """响应非 JSON（网关返回 HTML 错误页）→ 消息含响应片段。"""
        body = b"<html><body><h1>502 Bad Gateway</h1></body></html>"
        msg = self._assert_asr_error([FakeResponse(body)], ["不是合法 JSON", "502 Bad Gateway"])

    def test_missing_text_field(self):
        """响应 JSON 缺 text 字段 → 消息含实际字段列表。"""
        msg = self._assert_asr_error(
            [FakeResponse(json_bytes({"error": {"message": "model not found"}}))],
            ["text", "error"],
        )

    def test_empty_text_is_not_a_success(self):
        """text 为空串 → 抛 AsrError（绝不返回空串冒充「识别为无声」）。"""
        body = json_bytes({"text": "   "})
        with tempfile.TemporaryDirectory() as tmp:
            wav = wav_file(Path(tmp))
            adapter, _, _ = make_adapter([FakeResponse(body)], Path(tmp))
            with self.assertRaises(AsrError) as cm:
                adapter.transcribe(wav)
            msg = str(cm.exception)
            self.assertIn("text", msg)
            self.assertIn("空", msg)
            self.assertNotIn("None", msg)

    def test_non_string_text(self):
        """text 字段不是字符串（如数字）→ 抛 AsrError。"""
        msg = self._assert_asr_error([FakeResponse(json_bytes({"text": 42}))], ["text", "字符串"])

    def test_json_not_object(self):
        """响应 JSON 是数组而非对象 → 抛 AsrError。"""
        msg = self._assert_asr_error([FakeResponse(json_bytes(["今天下午"]))], ["不是对象", "list"])


class ReadStageFailureTest(unittest.TestCase):
    """T14c 修 4：读阶段异常必须归 AsrError，不逃出分类契约。

    读阶段 = `response.read()`，发生在 `_open` 返回之后——所以替身必须返回
    「read() 时抛错的响应对象」，而不是把异常塞给 opener（后者测的是 urlopen 阶段，
    会绕过 transcribe 里的读阶段分类逻辑）。
    """

    def _assert_asr_error(self, outcomes, keyword_checks):
        with tempfile.TemporaryDirectory() as tmp:
            wav = wav_file(Path(tmp))
            adapter, opener, _ = make_adapter(outcomes, Path(tmp))
            with self.assertRaises(AsrError) as cm:
                adapter.transcribe(wav)
            msg = str(cm.exception)
            for keyword in keyword_checks:
                self.assertIn(
                    keyword, msg,
                    f"AsrError 消息缺少关键上下文 {keyword!r}（实际消息: {msg}）",
                )
            return msg

    def test_incomplete_read_becomes_asr_error(self):
        """响应传一半就断（http.client.IncompleteRead）→ AsrError，不裸抛。"""
        msg = self._assert_asr_error(
            [TruncatedResponse()], ["IncompleteRead", "读取中断"]
        )
        self.assertNotIn("None", msg)

    def test_connection_reset_becomes_asr_error(self):
        """读取期连接被重置（ConnectionResetError）→ AsrError，消息含阶段。"""
        msg = self._assert_asr_error(
            [ReadResetResponse()], ["ConnectionResetError", "读取失败"]
        )

    def test_bare_timeout_error_in_read_becomes_asr_error(self):
        """读取期裸 TimeoutError（非 socket.timeout）→ AsrError。"""
        msg = self._assert_asr_error(
            [ReadTimeoutResponse()], ["TimeoutError", "读取超时"]
        )

    def test_bare_http_exception_in_read_becomes_asr_error(self):
        """读取期裸 http.client.HTTPException（非 IncompleteRead）→ AsrError。"""
        msg = self._assert_asr_error(
            [ReadProtoResponse()], ["HTTPException", "协议错误"]
        )

    def test_incomplete_read_message_mentions_stage(self):
        """消息必须点明是读阶段——连接阶段的 IncompleteRead 与读阶段是两种原因。"""
        msg = self._assert_asr_error([TruncatedResponse()], ["IncompleteRead"])
        self.assertIn("响应读取中断", msg)
        self.assertNotIn("响应协议错误", msg)


class TimeoutValidationTest(unittest.TestCase):
    """T14c 修 4：timeout 校验必须拒绝 NaN / inf——非有限正数不得漏进 urlopen。

    修前 nan <= 0 为 False，校验放过 nan，随后 urlopen(timeout=nan) 内部才炸裸
    ValueError（逃出 AsrError 契约）。这里断言构造期即被拦下，且请求永远发不出去。
    """

    def test_rejects_nan_timeout_at_construction(self):
        with self.assertRaises(ValueError) as cm:
            OmlxAsr(timeout_seconds=float("nan"))
        self.assertIn("timeout_seconds", str(cm.exception))
        self.assertIn("nan", str(cm.exception).lower())

    def test_rejects_infinite_timeout_at_construction(self):
        with self.assertRaises(ValueError) as cm:
            OmlxAsr(timeout_seconds=float("inf"))
        self.assertIn("timeout_seconds", str(cm.exception))

    def test_rejects_negative_infinite_timeout_at_construction(self):
        with self.assertRaises(ValueError) as cm:
            OmlxAsr(timeout_seconds=float("-inf"))
        self.assertIn("timeout_seconds", str(cm.exception))

    def test_nan_never_reaches_opener(self):
        """构造期就报错，所以根本不会有请求发出去。"""
        opener = StubOpener([])
        with self.assertRaises(ValueError):
            OmlxAsr(opener=opener, timeout_seconds=float("nan"))
        self.assertEqual(opener.requests, [], "构造期报错时不得发任何请求")

    def test_finite_timeout_still_accepted(self):
        """校验只许更严不许误伤：有限正数照常可用。"""
        for value in (120, 7.5, 0.001, 1e9):
            adapter = OmlxAsr(timeout_seconds=value)
            self.assertEqual(adapter.timeout, value)
