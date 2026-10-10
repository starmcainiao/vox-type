"""
adapters.tests.test_conformance_probe — T77：「逐适配器可用性 → skip」这处判定的自测；
T78：真适配器那条 hook 必须覆盖**全部**前置条件（ffmpeg + 端点）

被测对象是 `test_conformance.skip_unless_available` 本身：这里**直接 import 套件里那一份**
来调，不复制一份判定实现来测——复制出来的测试只能证明副本写对了，证明不了套件。

为什么需要这套测试：一致性套件的类级门是 `skipUnless(shutil.which("say"))`（按类整体跳），
循环却是 `for tts in self.adapters:` 逐适配器跑。守卫粒度比循环粒度粗，于是「本机有 say」
被当成了「所有适配器都能合成」；而 tts_omlx 的可用性取决于远端服务在不在跑，与 say 无关，
模拟 runner 条件（有 say、无 oMLX 服务）下必然连不上 → `errors=2` 把 CI 顶红。

按卡逐条覆盖（两个分支都要有）：
  1. 适配器自报不可用 → 套件 skip，且消息**点名**是哪个适配器、缺什么；
  2. 适配器**没有** `probe_availability` → 套件照旧真跑（不 skip）。
     第 2 条是「默认 = 可用」的负向证明：写反了（把「未实现」当成不可用）
     会让全部适配器永远 skip，套件变空还一路绿灯——正是本仓最忌讳的假绿。
补充守住判定的另两个前置约定：
  3. 自报可用 → 不 skip（三个状态各有处置，不是「非不可用即不可用」）；
  4. hook 按**类**查：实例上塞个同名属性不算实现。

以及真适配器那条 hook（`OmlxTts.probe_availability`）：探的端点必须与「无参构造会打的那个
端点」是同一个地址——探得通却说打不通（或反过来）就等于探了个寂寞。

T78 加的那一层：hook 回答的是「此刻能不能**真**合成」，前置条件共两条（ffmpeg 与端点），
漏一条就会「报可用、套件照跑、炸在另一个前置条件上」——比没有探针更坏。故：
  5. 缺 ffmpeg + 端点可达 → 不可用，reason 点名 ffmpeg；
  6. 两者都缺 → **两条原因都在**（只报第一个会让人补完再跑一遍，白来回一趟）；
  7. 缺 ffmpeg 时 synthesize 仍抛 TtsError 且消息与探针**逐字同源**（同一常量格式化）；
  8. VOX_FFMPEG 能让探针改探别的名字（回落链第二档真生效）。
这两条 ffmpeg 名的状态**不靠宿主机**：「有 ffmpeg」用临时目录里造的假 ffmpeg 挂到 PATH 前部，
「无 ffmpeg」用 VOX_FFMPEG 指一个解析不到的名字——本机装没装 ffmpeg，八条覆盖一模一样，
不靠「碰巧这台机器没装」。**否则本机永远绿，等于没测。**
env 一律就地还原（含「原本没设」）：漏还原会把后续用例悄悄指向别处。
"""

import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from adapters.tests.test_conformance import ADAPTER_CLASSES, skip_unless_available
from adapters.tts_omlx import OmlxTts, TtsError
import adapters.tts_omlx.adapter as omlx_adapter


# ---------------------------------------------------------------------------
# 测试替身（非产品件）：只需要「skipTest 被调 / 没被调」这一个观察点，
# 所以不真继承 TestCase。
# ---------------------------------------------------------------------------
class _SkipRecorder:
    """模拟 `unittest.TestCase.skipTest` 的真实语义：先记录，再抛 `unittest.SkipTest`。

    抛 `SkipTest` 而不是普通异常：套件里的用法是 `for tts in ...: self.skipTest(...)`
    直接把整个用例中止；替身不抛就等于没有中止，测不出「跳」这个动作本身。
    """

    def __init__(self):
        self.skipped = []

    def skipTest(self, reason):
        self.skipped.append(reason)
        raise unittest.SkipTest(reason)


class _StubUnavailableTts:
    """声明「不可用」的假适配器（只有 name 与 hook，不含任何产品逻辑）。"""

    name = "stub-dead"

    @classmethod
    def probe_availability(cls):
        return (False, "端点不可用：http://127.0.0.1:1")


class _StubAvailableTts:
    name = "stub-live"

    @classmethod
    def probe_availability(cls):
        return (True, "")


class _StubNoProbeTts:
    """不实现 hook 的假适配器 = 永远可用（tts_macsay 的形态）。"""

    name = "stub-noprobe"


# ---------------------------------------------------------------------------
# 判定本身（正例 + 负例）
# ---------------------------------------------------------------------------
class TestSkipUnlessAvailable(unittest.TestCase):
    """套件里那处逐适配器判定：三种自报状态各自该怎么处置。"""

    def test_unavailable_is_skipped_and_named(self):
        """自报不可用 → skip，且消息同时点名适配器与缺失原因（不许只写一句「跳过」）。"""
        rec = _SkipRecorder()
        with self.assertRaises(unittest.SkipTest) as ctx:
            skip_unless_available(rec, _StubUnavailableTts())
        message = str(ctx.exception)
        self.assertIn(
            "stub-dead", message,
            f"skip 消息必须点名是哪个适配器，实际 {message!r}",
        )
        self.assertIn(
            "端点不可用", message,
            f"skip 消息必须说明缺什么，实际 {message!r}",
        )
        self.assertIn(
            "127.0.0.1:1", message,
            "必须点到具体端点，否则读不出为什么跳",
        )
        self.assertEqual(rec.skipped, [message])

    def test_available_is_not_skipped(self):
        """自报可用 → 不 skip，用例照跑。"""
        rec = _SkipRecorder()
        skip_unless_available(rec, _StubAvailableTts())
        self.assertEqual(rec.skipped, [])

    def test_adapter_without_hook_is_not_skipped(self):
        """没有 probe_availability → 照旧真跑（默认 = 可用，不许写成默认跳过）。"""
        rec = _SkipRecorder()
        skip_unless_available(rec, _StubNoProbeTts())
        self.assertEqual(
            rec.skipped, [],
            "未实现 hook 的适配器被 skip = 空套件假绿灯",
        )

    def test_hook_is_a_class_level_lookup(self):
        """判定按类查 hook：实例上塞个同名属性不算实现（否则可用性会逐实例漂移）。"""
        tts = _StubNoProbeTts()
        tts.probe_availability = staticmethod(lambda: (False, "不该被看到"))
        rec = _SkipRecorder()
        skip_unless_available(rec, tts)
        self.assertEqual(rec.skipped, [])


# ---------------------------------------------------------------------------
# 默认分支必须落在**真**适配器上（不能只在替身上成立）
# ---------------------------------------------------------------------------
class TestDiscoveredAdaptersDefaultToAvailable(unittest.TestCase):
    """`discover_adapters()` 扫到的适配器逐个过那处判定：无 hook 的一律不得被 skip。

    替身只能证明判定写对了；这一条把它绑回真适配器——「默认 = 可用」必须真的
    作用于 tts_macsay，否则它照样会被整个跳过，而用例数看起来还是满的。
    """

    def test_adapters_without_hook_are_never_skipped(self):
        rec = _SkipRecorder()
        without_hook = []
        for cls in ADAPTER_CLASSES:
            if not hasattr(cls, "probe_availability"):
                without_hook.append(cls.__name__)
                skip_unless_available(rec, cls())
        self.assertEqual(rec.skipped, [], f"无 hook 的适配器被 skip：{rec.skipped}")
        self.assertIn(
            "MacSayTts", without_hook,
            "tts_macsay 靠「未实现 hook = 永远可用」被真跑；一旦给它加 hook，"
            "它就成了会 skip 的适配器，覆盖会悄悄变空",
        )


# ---------------------------------------------------------------------------
# 真适配器那条 hook：探的必须是「无参构造会打的那个端点」
# ---------------------------------------------------------------------------
def _fake_http_server(handler):
    """起一个最小 HTTP 服务，返回 `(base_url, stop)`；`handler(path) -> (status_line, body)`。

    为什么需要真的应答而不用裸 socket：T79 的探针会 `GET /v1/models`，裸 socket 连请求都
    读不走（探针会超时降级），那就测不出「清单里有没有那个模型」这一档了。
    只服务探针的这一个 GET，不实现 POST /v1/audio/speech——**不是**替身引擎，
    不参与任何合成断言（那条纪律见本仓「反空转」）。

    纯标准库 `http.server`，起在 127.0.0.1 的临时端口上，stop() 必定回收。
    """
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler 约定的命名
            status, body = handler(self.path)
            raw = body if isinstance(body, bytes) else str(body).encode()
            self.send_response(int(status.split()[1]))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):  # 静音：stderr 噪声会污染测试输出
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def stop():
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)

    return f"http://127.0.0.1:{srv.server_address[1]}", stop


def _fake_models_server(model_ids):
    """起一个只在 `GET /v1/models` 上返回给定模型清单的服务，返回 `(base_url, stop)`。"""
    payload = {"object": "list", "data": [{"id": mid, "object": "model"} for mid in model_ids]}

    def handler(path):
        if path.rstrip("/").endswith("/v1/models"):
            return "HTTP/1.1 200 OK", json.dumps(payload)
        return "HTTP/1.1 404 Not Found", "{}"

    return _fake_http_server(handler)


class _OmlxProbeTestBase(unittest.TestCase):
    """真适配器探针用例的公共装置：env 逐字还原 + 假 ffmpeg 挂在 PATH 前部。

    **只放装置，不放任何用例**——它不是 `unittest.TestCase` 的测试目标
    （名字以下划线开头，loadTestsFromModule 不会收它），子类各自定义自己的用例。
    这一点是刻意的：早先让 `TestOmlxTtsModelProbe` 直接继承 `TestOmlxTtsProbe`，
    结果父类 7 条用例在子类里被**重复跑了一遍**（子类 11 条 = 父类 7 + 新增 4），
    计数虚高且同一断言跑两遍。「共享装置」与「继承测试用例」必须分开。
    """

    def setUp(self):
        # 记下原值（None = 原本没设），tearDown 逐字还原。PATH 一并管起来：假 ffmpeg 靠
        # 临时目录挂在 PATH 前部才能被 shutil.which 解析到，漏还原会让同进程后续用例的
        # which() 结果漂移，等于把这条用例的状态泄漏给别的层。
        self._saved = {key: os.environ.get(key) for key in ("VOX_TTS_ENDPOINT", "VOX_FFMPEG", "PATH")}
        self.tmp_dir = tempfile.mkdtemp(prefix="vox_probe_")
        self.fake_ffmpeg = self._plant_fake_ffmpeg("vox-fake-ffmpeg")
        # 默认态：ffmpeg 前置条件**已显式满足**（挂到 PATH 前部的假 ffmpeg）。
        # 需要「无 ffmpeg」的用例在各自用例里把 VOX_FFMPEG 改成一个解析不到的名字。
        os.environ["VOX_FFMPEG"] = self.fake_ffmpeg
        os.environ["PATH"] = os.pathsep.join(filter(None, [self.tmp_dir, self._saved["PATH"]]))

    def tearDown(self):
        for key, saved in self._saved.items():
            if saved is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _plant_fake_ffmpeg(self, name):
        """在临时目录造一个**能被 shutil.which 解析到**的假 ffmpeg，返回它的名字。

        只要求 X_OK（shutil.which 的判据），内容是条空脚本：探针只问「解析得到吗」，
        从不执行它。用它而不是「指望本机装没装 ffmpeg」——本机有 ffmpeg 时也能造出
        可解析的状态，本机没装时也不会让「可用」这条用例变红。
        """
        exe = os.path.join(self.tmp_dir, name)
        with open(exe, "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(exe, 0o755)
        return name

    @staticmethod
    def _listening():
        """起一个临时回环监听，返回 (base_url, socket)；调用方在 finally 里 close。

        裸 socket **不**应答 HTTP：探针的 `GET /v1/models` 会读不到东西而降级为
        「不判不可用」。要测「清单里有没有那个模型」那一档，用 `_fake_models_server`。
        """
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        return f"http://127.0.0.1:{listener.getsockname()[1]}", listener

    @staticmethod
    def _dead_endpoint():
        """取一个此刻无人监听的回环端点（bind 拿号后立刻 close）。"""
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            port = holder.getsockname()[1]
        return f"http://127.0.0.1:{port}"


class TestOmlxTtsProbe(_OmlxProbeTestBase):
    """`OmlxTts.probe_availability`：与 __init__ 同一条 env 回落链，探打同一个端点。"""

    def test_reachable_endpoint_is_available(self):
        """连得上的端点 + 解析得到 ffmpeg → (True, "")。

        ffmpeg 那半由 setUp 的假 ffmpeg 显式安排（T78 起不再隐含依赖宿主机装了 ffmpeg），
        端点用临时监听而非固定端口，两个前置条件都与本机状态解耦。
        """
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            self.assertEqual(OmlxTts.probe_availability(), (True, ""))
        finally:
            listener.close()

    def test_unreachable_endpoint_is_unavailable_and_names_base_url(self):
        """连不上的端点（ffmpeg 前置条件已满足）→ (False, "端点不可用：<base_url>")。

        端口是自己刚 close 掉的，所以「此刻无人监听」是事实而非假设；
        万一有别的进程在毫秒级窗口里抢占了它，本用例会响亮失败，不会悄悄判绿。
        逐字相等守的是「单条原因不带分隔符」：加了两条原因后，别让格式噪声渗进单条场景。
        """
        base = self._dead_endpoint()
        os.environ["VOX_TTS_ENDPOINT"] = base
        ok, reason = OmlxTts.probe_availability()
        self.assertFalse(ok)
        self.assertIn("端点不可用", reason)
        self.assertIn(base, reason)
        self.assertEqual(reason, f"端点不可用：{base}")

    def test_probe_targets_same_endpoint_as_unargued_construction(self):
        """探活的地址与无参构造的地址是同一个（探与打不能各指一处）。

        ffmpeg 前置条件同样由 setUp 的假 ffmpeg 显式满足，不与本机是否装了 ffmpeg 耦合。
        """
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            self.assertEqual(OmlxTts().base_url, base)
            self.assertTrue(OmlxTts.probe_availability()[0])
        finally:
            listener.close()

    def test_missing_ffmpeg_is_unavailable_and_names_ffmpeg(self):
        """缺 ffmpeg + 端点可达 → (False, …)，reason 点名 ffmpeg 与那个解析不到的名字。

        这就是 T78 修的那个洞：只探端点的探针在这一档会报「可用」，让套件照跑然后炸在
        ffmpeg 上。本机装着 ffmpeg 也不影响本用例——VOX_FFMPEG 被显式指到解析不到的名字。
        """
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            os.environ["VOX_FFMPEG"] = "vox-no-such-ffmpeg"
            ok, reason = OmlxTts.probe_availability()
        finally:
            listener.close()
        self.assertFalse(
            ok,
            f"缺 ffmpeg 仍报可用 = 探针只覆盖了一半前置条件，实际 {reason!r}",
        )
        self.assertIn("ffmpeg", reason, f"reason 必须点名 ffmpeg，实际 {reason!r}")
        self.assertIn("vox-no-such-ffmpeg", reason, f"reason 必须点到那个名字，实际 {reason!r}")
        self.assertNotIn(
            "端点不可用", reason,
            f"端点明明可达，不该连带报端点（报多了等于把可用态判成不可用），实际 {reason!r}",
        )

    def test_missing_both_reports_both_reasons(self):
        """ffmpeg 与端点**都**缺 → 两条原因都在，且 ffmpeg 那条在前。

        只报第一个会让人补完 ffmpeg 再跑一遍才发现端点也不通，白来回一趟；
        ffmpeg 在前守的是「检查顺序与 synthesize 一致」（它也是先查 ffmpeg 再发请求）。
        """
        os.environ["VOX_TTS_ENDPOINT"] = self._dead_endpoint()
        os.environ["VOX_FFMPEG"] = "vox-no-such-ffmpeg"
        ok, reason = OmlxTts.probe_availability()
        self.assertFalse(ok)
        self.assertIn("ffmpeg", reason, f"两条都缺时必须报 ffmpeg，实际 {reason!r}")
        self.assertIn("端点不可用", reason, f"两条都缺时必须报端点，实际 {reason!r}")
        self.assertIn("vox-no-such-ffmpeg", reason, f"必须点到那个 ffmpeg 名，实际 {reason!r}")
        self.assertIn("127.0.0.1:", reason, f"必须点到那个端点，实际 {reason!r}")
        self.assertLess(
            reason.index("ffmpeg"), reason.index("端点不可用"),
            f"ffmpeg 必须排在端点之前（与 synthesize 的检查顺序一致），实际 {reason!r}",
        )

    def test_vox_ffmpeg_env_repoints_the_probe(self):
        """回落链第二档生效：VOX_FFMPEG 指定别的名字，探针探的就是**那个**名字。

        探针是类方法、拿不到实例参数，所以它只能读 env——本用例同时验两个方向：
        指定的名字解析得到时判可用，换成解析不到的名字时 reason 点的是那个名字而非写死的
        "ffmpeg"。任一方向改成读 DEFAULT_FFMPEG，本用例都会响亮失败。
        """
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            os.environ["VOX_FFMPEG"] = self.fake_ffmpeg
            self.assertEqual(OmlxTts.probe_availability(), (True, ""))
            os.environ["VOX_FFMPEG"] = "vox-another-missing-ffmpeg"
            ok, reason = OmlxTts.probe_availability()
            self.assertFalse(ok, f"env 指定的 ffmpeg 解析不到时必须判不可用，实际 {reason!r}")
            self.assertIn(
                "vox-another-missing-ffmpeg", reason,
                f"reason 必须点到 env 指定的那个名字（不能写死 DEFAULT_FFMPEG），实际 {reason!r}",
            )
        finally:
            listener.close()

    def test_synthesize_and_probe_share_one_ffmpeg_message(self):
        """synthesize 抛的缺 ffmpeg 消息与探针 reason **逐字同源**（同一个 FFMPEG_MISSING 常量）。

        两处若各写一遍文案，改一处忘一处就会重演「本机绿≠CI 绿」；这里把两侧都跟同一个
        常量比——任一处复制粘贴（哪怕只差一个字）都会被这条用例抓到。
        顺带守住「绝不改 synthesize 的失败语义」：端点是刚 close 掉的死端点，synthesize 仍抛
        **ffmpeg** 那条错而非连接错，说明它照旧在发任何请求之前就先查 ffmpeg。
        """
        name = "vox-no-such-ffmpeg"
        expected = omlx_adapter.FFMPEG_MISSING.format(name=name)
        self.assertIn("ffmpeg", expected, "FFMPEG_MISSING 文案本身被改跑，本用例即失去意义")
        with self.assertRaises(TtsError) as ctx:
            OmlxTts(base_url=self._dead_endpoint(), ffmpeg=name).synthesize(
                "接口一致性测试", Path(self.tmp_dir) / "unavailable.wav")
        self.assertEqual(
            str(ctx.exception), expected,
            "synthesize 的缺 ffmpeg 消息必须与 FFMPEG_MISSING 逐字同源",
        )
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            os.environ["VOX_FFMPEG"] = name
            ok, reason = OmlxTts.probe_availability()
            self.assertFalse(ok)
            self.assertEqual(
                reason, expected,
                "探针 reason 的 ffmpeg 那半必须与 synthesize 抛出的消息逐字同源",
            )
        finally:
            listener.close()


class TestOmlxTtsModelProbe(_OmlxProbeTestBase):
    """T79：探针必须连**模型名**一起探——端点通不代表模型在。

    这一族的缺陷是同一个失效面的第三层：T77 只探端点（漏 ffmpeg，T78 补），
    T78 补齐 ffmpeg 后仍漏模型。实测 2026-10-10 本机 oMLX 上默认模型已不在清单里，
    探针却报「可用」→ 套件照跑 → 3 条用例炸在 HTTP 404 上。

    继承 `_OmlxProbeTestBase`（只含装置的基类）而非 `TestOmlxTtsProbe`：
    后者带用例，继承它会让那 7 条在子类里重跑一遍、计数虚高。
    """

    def _serving(self, model_ids):
        """起一个**真的会应答 GET /v1/models** 的本地服务，返回 (base_url, stop)。"""
        return _fake_models_server(model_ids)

    def test_model_absent_is_unavailable_and_names_env(self):
        """清单里没有该模型 → (False, …)，reason 点名模型与 VOX_TTS_MODEL。"""
        base, stop = self._serving(["some-other-model"])
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            os.environ["VOX_TTS_MODEL"] = "vox-absent-model"
            ok, reason = OmlxTts.probe_availability()
        finally:
            stop()
        self.assertFalse(
            ok,
            f"模型不在清单仍报可用 = 探针只覆盖了一半前置条件，实际 {reason!r}",
        )
        self.assertIn("vox-absent-model", reason)
        self.assertIn("VOX_TTS_MODEL", reason, f"reason 必须点名修法，实际 {reason!r}")

    def test_model_present_is_available(self):
        """清单里有该模型 → (True, "")，不因新增的模型检查而误判。"""
        base, stop = self._serving(["vox-present-model", "other"])
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            os.environ["VOX_TTS_MODEL"] = "vox-present-model"
            ok, reason = OmlxTts.probe_availability()
        finally:
            stop()
        self.assertTrue(ok, f"模型在清单里应报可用，实际 {reason!r}")
        self.assertEqual(reason, "")

    def test_unlistable_service_is_not_judged_unavailable(self):
        """拿不到清单（老服务无 /v1/models / 返回非 JSON）→ **不判不可用**。

        方向与 ffmpeg/端点相反是有意的：探针只负责给出**确定的**不该跑，
        不负责替服务方猜测。这一档宁可放行去撞 synthesize 自己的响亮错误，
        也不能因为「查不到证据」就把一个能用的适配器判死。
        """
        for label, handler in (
            ("404 无此端点", lambda body: ("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n", b"")),
            ("非 JSON 垃圾", lambda body: ("HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello", b"")),
        ):
            with self.subTest(服务=label):
                base, stop = _fake_http_server(handler)
                try:
                    os.environ["VOX_TTS_ENDPOINT"] = base
                    os.environ["VOX_TTS_MODEL"] = "vox-anything"
                    ok, reason = OmlxTts.probe_availability()
                finally:
                    stop()
                self.assertTrue(
                    ok,
                    f"{label} 不该被判不可用（探针不能凭缺证据判死），实际 {reason!r}",
                )

    def test_negative_control_ffmpeg_still_judged_when_model_absent(self):
        """负控：模型不在**不会掩盖** ffmpeg 缺失——两条原因都要报出来。

        「只报第一个原因」会让人补完一个再跑一遍才发现另一个，白来回一趟。
        """
        base, stop = self._serving(["some-other-model"])
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            os.environ["VOX_TTS_MODEL"] = "vox-absent-model"
            os.environ["VOX_FFMPEG"] = "vox-no-such-ffmpeg"
            ok, reason = OmlxTts.probe_availability()
        finally:
            stop()
        self.assertFalse(ok)
        self.assertIn("ffmpeg", reason, f"模型原因不得掩盖 ffmpeg 原因，实际 {reason!r}")
        self.assertIn("VOX_TTS_MODEL", reason, f"模型原因必须仍在，实际 {reason!r}")
        self.assertIn("; ", reason, "两条原因应用 '; ' 分隔")


if __name__ == "__main__":
    unittest.main()
