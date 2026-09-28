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
class TestOmlxTtsProbe(unittest.TestCase):
    """`OmlxTts.probe_availability`：与 __init__ 同一条 env 回落链，探打同一个地址。"""

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


if __name__ == "__main__":
    unittest.main()
