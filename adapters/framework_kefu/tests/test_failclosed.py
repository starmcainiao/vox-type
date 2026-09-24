"""
adapters.framework_kefu.tests.test_failclosed — fail-closed 有牙（验收第 4 条 + 红线 4）

只调产品 API：KefuBridge.run_turn。慢路合成器是可计数的假对象。

要钉住的四件事：
    未命中 + 默认开关 → 抛 runtime.RuntimeMissError
    抛错时不落盘（输出文件不存在）
    抛错时不调慢路合成器（计数恒 0）
    报错消息带原因码与文本/key 片段（负例必须断言"异常类型 + 消息含关键值"）
    以及：默认开关是 False，不允许"看起来没开降级却走了慢路"
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from runtime import RuntimeMissError
from runtime.duplex import DuplexParams

from adapters.framework_kefu import (
    REASON_TEXT_NOT_PREBAKED,
    BridgeError,
    KefuBridge,
)
from adapters.framework_kefu.tests import CountingTts, FakeClient, build_pack


GREET = "您好，这里是报修服务热线。"
UNKNOWN = "这是一句包里没有的话。"


class FailClosedBase(unittest.TestCase):
    """默认开关（allow_fallback=False）下的夹具。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-failclosed-"))
        self.pack = build_pack(
            self.tmp / "pack",
            [("greeting", GREET, 0), ("ask", "请问您要报修的是什么设备？", 0)],
        )
        self.tts = CountingTts()
        self.client = FakeClient()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, **kw):
        return KefuBridge(self.pack, live_tts=self.tts, client=self.client, **kw)

    def out(self, name="turn.wav"):
        return self.tmp / name


class TestMissRaisesRuntimeMissError(FailClosedBase):
    """未命中 + 默认 → 抛 RuntimeMissError，且异常类型必须精确。"""

    def test_type_is_exactly_runtime_miss_error(self):
        self.client.replies = [UNKNOWN]
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertIs(type(ctx.exception), RuntimeMissError,
                      f"异常类型必须是 RuntimeMissError，实际为 {type(ctx.exception).__name__}")

    def test_message_contains_reason_code(self):
        self.client.replies = [UNKNOWN]
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertIn(REASON_TEXT_NOT_PREBAKED, str(ctx.exception),
                      "消息必须含原因码，便于按 reason 聚合")

    def test_message_contains_text_snippet(self):
        self.client.replies = [UNKNOWN]
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertIn(UNKNOWN[:12], str(ctx.exception),
                      "消息必须含文本片段，便于定位是哪句话没命中")
        self.assertIn("allow_fallback=False", str(ctx.exception))

    def test_key_miss_message_contains_key_name(self):
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(session_id="s1", key="no_such_key", out_path=self.out())
        self.assertIn("no_such_key", str(ctx.exception))
        self.assertIn("key_not_prebaked", str(ctx.exception))

    def test_miss_never_returns_a_result(self):
        """默认开关下，未命中必须抛错而不是返回"看起来正常"的结果。"""
        self.client.replies = [UNKNOWN]
        bridge = self.bridge()
        for i in range(3):
            self.client.replies = [UNKNOWN]
            with self.assertRaises(RuntimeMissError):
                bridge.run_turn(session_id=f"s{i}", text="x", out_path=self.out(f"{i}.wav"))


class TestMissWritesNothing(FailClosedBase):
    """fail-closed 时不落盘。"""

    def test_output_file_does_not_exist(self):
        self.client.replies = [UNKNOWN]
        out = self.out("must_not_exist.wav")
        self.assertFalse(out.exists())
        with self.assertRaises(RuntimeMissError):
            self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=out)
        self.assertFalse(out.exists(), "抛错后输出文件不得存在")

    def test_key_miss_also_writes_nothing(self):
        out = self.out("key_miss.wav")
        with self.assertRaises(RuntimeMissError):
            self.bridge().run_turn(session_id="s1", key="no_such_key", out_path=out)
        self.assertFalse(out.exists())


class TestMissCallsLiveTtsZeroTimes(FailClosedBase):
    """fail-closed 时慢路合成器一次都不许被调用。"""

    def test_tts_not_called_on_text_miss(self):
        self.client.replies = [UNKNOWN]
        with self.assertRaises(RuntimeMissError):
            self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertEqual(self.tts.calls, [], "fail-closed 不得调用慢路合成器")

    def test_tts_not_called_on_key_miss(self):
        with self.assertRaises(RuntimeMissError):
            self.bridge().run_turn(session_id="s1", key="no_such_key", out_path=self.out())
        self.assertEqual(self.tts.calls, [])

    def test_tts_not_called_when_pack_has_no_matching_variant(self):
        """包里有该 key 但没有本层语速档的资产 → 仍按未命中处理，不降级。"""
        slow_pack = build_pack(
            self.tmp / "pack-slow",
            [("greeting", GREET, 0)],
            rate="slow",
        )
        bridge = KefuBridge(slow_pack, live_tts=self.tts, client=self.client)
        with self.assertRaises(RuntimeMissError) as ctx:
            bridge.run_turn(session_id="s1", key="greeting", out_path=self.out())
        self.assertIn("key_not_prebaked", str(ctx.exception))
        self.assertEqual(self.tts.calls, [])


class TestUsageGuardrails(FailClosedBase):
    """输入表示与依赖的护栏：不猜、不静默跳过。"""

    def test_no_input_raises_bridge_error(self):
        with self.assertRaises(BridgeError) as ctx:
            self.bridge().run_turn(session_id="s1", out_path=self.out())
        self.assertIn("wav_bytes / text / key", str(ctx.exception))

    def test_two_inputs_raises_bridge_error(self):
        cases = [
            dict(text="用户说", wav_bytes=b"audio"),
            dict(text="用户说", key="greeting"),
            dict(wav_bytes=b"audio", key="greeting"),
        ]
        for i, kw in enumerate(cases):
            with self.subTest(**kw):
                with self.assertRaises(BridgeError) as ctx:
                    self.bridge().run_turn(session_id=f"s{i}", out_path=self.out(), **kw)
                self.assertIn("一种输入表示", str(ctx.exception))

    def test_three_inputs_raises_bridge_error(self):
        with self.assertRaises(BridgeError):
            self.bridge().run_turn(
                session_id="s1", out_path=self.out(),
                text="用户说", wav_bytes=b"audio", key="greeting",
            )

    def test_wav_without_client_raises_bridge_error(self):
        bridge = KefuBridge(self.pack, live_tts=self.tts, client=None)
        with self.assertRaises(BridgeError) as ctx:
            bridge.run_turn(session_id="s1", wav_bytes=b"audio", out_path=self.out())
        self.assertIn("ASR", str(ctx.exception))
        self.assertEqual(self.tts.calls, [])
        self.assertFalse(self.out().exists())

    def test_text_without_client_raises_bridge_error(self):
        bridge = KefuBridge(self.pack, live_tts=self.tts, client=None)
        with self.assertRaises(BridgeError):
            bridge.run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertEqual(self.tts.calls, [])

    def test_empty_session_id_raises_bridge_error(self):
        with self.assertRaises(BridgeError) as ctx:
            self.bridge().run_turn(session_id="   ", key="greeting", out_path=self.out())
        self.assertIn("session_id", str(ctx.exception))

    def test_allow_fallback_must_be_bool(self):
        with self.assertRaises(BridgeError) as ctx:
            KefuBridge(self.pack, live_tts=self.tts, allow_fallback="yes")
        self.assertIn("allow_fallback", str(ctx.exception))

    def test_default_is_fail_closed(self):
        """默认必须是 fail-closed，不允许默认就开降级。"""
        bridge = KefuBridge(self.pack, live_tts=self.tts, client=self.client)
        self.assertFalse(bridge.allow_fallback)

    def test_empty_brain_reply_is_miss_not_hit(self):
        """brain 返回空回复：按未命中处理（不猜、不凑、不播空音频）。"""
        self.client.replies = [""]
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())
        self.assertIn(REASON_TEXT_NOT_PREBAKED, str(ctx.exception))
        self.assertEqual(self.tts.calls, [])


class TestExplicitFallbackLeavesTrace(FailClosedBase):
    """开了降级必须留痕：事件三态 + 非空原因码，缺一不可。"""

    def test_fallback_switch_on_still_emits_event(self):
        self.client.replies = [UNKNOWN]
        res = self.bridge(allow_fallback=True).run_turn(
            session_id="s1", text="用户的问题", out_path=self.out()
        )
        ev = res.events[0]
        self.assertEqual(ev.get("miss"), True)
        self.assertTrue(ev.get("reason"), "降级必须带非空 reason")
        self.assertEqual(ev["reason"], REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(res.tts_calls, 1)

    def test_hit_still_zero_tts_with_fallback_enabled(self):
        """开了降级也不影响命中路径的零调用性质。"""
        self.client.replies = [GREET]
        res = self.bridge(allow_fallback=True).run_turn(
            session_id="s1", text="用户的问题", out_path=self.out()
        )
        self.assertEqual(res.state, "hit")
        self.assertEqual(res.tts_calls, 0)
        self.assertEqual(self.tts.calls, [])

    def test_duplex_params_are_accepted(self):
        """双工参数透传：非法值由 runtime 拒绝，不落到本层。"""
        bridge = KefuBridge(
            self.pack, live_tts=self.tts, client=self.client,
            duplex=DuplexParams(patience_ms=400),
        )
        res = bridge.run_turn(session_id="s1", key="greeting", out_path=self.out())
        self.assertEqual(res.state, "hit")


if __name__ == "__main__":
    unittest.main()
