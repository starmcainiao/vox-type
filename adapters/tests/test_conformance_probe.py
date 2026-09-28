"""
adapters.tests.test_conformance_probe — T77：「逐适配器可用性 → skip」这处判定的自测

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
env 一律就地还原（含「原本没设」）：漏还原会把后续用例悄悄指向别处。
"""

import os
import socket
import unittest

from adapters.tests.test_conformance import ADAPTER_CLASSES, skip_unless_available
from adapters.tts_omlx import OmlxTts


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
class TestOmlxTtsProbe(unittest.TestCase):
    """`OmlxTts.probe_availability`：与 __init__ 同一条 env 回落链，探打同一个地址。"""

    def setUp(self):
        # 记下原值（None = 原本没设），tearDown 逐字还原
        self._saved = os.environ.get("VOX_TTS_ENDPOINT")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("VOX_TTS_ENDPOINT", None)
        else:
            os.environ["VOX_TTS_ENDPOINT"] = self._saved

    @staticmethod
    def _listening():
        """起一个临时回环监听，返回 (base_url, socket)；调用方在 finally 里 close。"""
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

    def test_reachable_endpoint_is_available(self):
        """连得上的端点 → (True, "")。用临时监听而非固定端口，不与本机状态耦合。"""
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            self.assertEqual(OmlxTts.probe_availability(), (True, ""))
        finally:
            listener.close()

    def test_unreachable_endpoint_is_unavailable_and_names_base_url(self):
        """连不上的端点 → (False, "端点不可用：<base_url>")，reason 里带着那个端点。

        端口是自己刚 close 掉的，所以「此刻无人监听」是事实而非假设；
        万一有别的进程在毫秒级窗口里抢占了它，本用例会响亮失败，不会悄悄判绿。
        """
        base = self._dead_endpoint()
        os.environ["VOX_TTS_ENDPOINT"] = base
        ok, reason = OmlxTts.probe_availability()
        self.assertFalse(ok)
        self.assertIn("端点不可用", reason)
        self.assertIn(base, reason)

    def test_probe_targets_same_endpoint_as_unargued_construction(self):
        """探活的地址与无参构造的地址是同一个（探与打不能各指一处）。"""
        base, listener = self._listening()
        try:
            os.environ["VOX_TTS_ENDPOINT"] = base
            self.assertEqual(OmlxTts().base_url, base)
            self.assertTrue(OmlxTts.probe_availability()[0])
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
