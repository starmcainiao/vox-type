"""
adapters.framework_kefu.tests.test_bridge — 命中判定、零调用、事件与两档

只调产品 API：normalize_text / KefuBridge.run_turn（内部走 runtime.Executor）。
慢路合成器全部用可计数的假对象（CountingTts），不碰 say、不碰网络。

覆盖：
    key 档命中（tts_calls==0、事件 hit、match_mode=="key"、播的是包内音频）
    文本档逐字相等命中（含全角/空白/大小写差异）
    差一个字 / 同义句 → 未命中
    事件字段名全部来自 core.metrics_spec（只允许新增 match_mode）
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core.metrics_spec import FALLBACK, HIT, KEY, METRIC_FIELDS, MISS, REASON
from runtime import RuntimeMissError
from runtime.audio import read_wav

import base64

from adapters.framework_kefu import (
    MATCH_MODE,
    MODE_KEY,
    MODE_TEXT,
    REASON_TEXT_NOT_PREBAKED,
    BridgeError,
    KefuBridge,
    KefuClient,
    KefuWorkerError,
    extract_reply,
    extract_wav,
    normalize_text,
)
from adapters.framework_kefu.tests import CountingTts, FakeClient, build_pack, write_wav


# 自造话术（禁止使用 kefu 的真实业务文案）
GREET = "您好，这里是报修服务热线。"
GREET_V2 = "您好，报修服务为您服务。"
ASK = "请问您要报修的是什么设备？"
FULLWIDTH_FORM = "已为您登记Ａ０１号，请留意。"


def _peak(samples):
    """取绝对值峰值，用于区分"包内音频"与"慢路音频"。"""
    return max(abs(s) for s in samples)


class BridgeBase(unittest.TestCase):
    """公用夹具：一个临时资产包 + 假慢路合成器 + 假 brain/ASR。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-bridge-"))
        self.pack = build_pack(
            self.tmp / "pack",
            [
                ("greeting", GREET, 0),
                ("greeting", GREET_V2, 1),
                ("ask_fault_type", ASK, 0),
                ("note", FULLWIDTH_FORM, 0),
                ("closing", "谢谢您的来电，祝您生活愉快。", 0),
            ],
        )
        self.tts = CountingTts()
        self.client = FakeClient()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, **kw):
        return KefuBridge(self.pack, live_tts=self.tts, client=self.client, **kw)

    def out(self, name="turn.wav"):
        return self.tmp / name


class TestKeyModeHit(BridgeBase):
    """key 档：pack.lookup 直查 → 命中 → 零慢路调用。"""

    def test_hit_is_zero_tts_and_emits_hit_event(self):
        res = self.bridge().run_turn(session_id="s1", key="greeting", out_path=self.out())

        self.assertEqual(res.state, "hit")
        self.assertEqual(res.match_mode, MODE_KEY)
        self.assertEqual(res.tts_calls, 0, "命中必须零慢路调用")
        self.assertEqual(self.tts.calls, [], "命中路径不得调用慢路合成器")
        self.assertTrue(res.audio_path.is_file())

        ev = res.events[0]
        self.assertTrue(ev[HIT])
        self.assertNotIn(MISS, ev)
        self.assertNotIn(FALLBACK, ev)
        self.assertEqual(ev[KEY], "greeting")
        self.assertEqual(ev[MATCH_MODE], MODE_KEY)
        self.assertEqual(ev[REASON], "")
        self.assertEqual(res.live_text, GREET)

    def test_hit_plays_pack_audio_via_executor(self):
        """命中的音频就是包内那份（经 runtime.Executor 拼接 + 淡入淡出）。"""
        out = self.out()
        self.bridge().run_turn(session_id="s1", key="greeting", out_path=out)

        entry = [e for e in self.pack.assets
                 if e.key == "greeting" and e.variant == 0][0]
        pack_samples, _ = read_wav(self.pack.root / entry.path)
        out_samples, _ = read_wav(out)

        self.assertEqual(len(out_samples), len(pack_samples), "时长应等于包内资产")
        # 中段逐样本相同（淡入淡出只动首尾各 80 个样本）
        mid = len(pack_samples) // 2
        self.assertEqual(
            list(out_samples[mid - 50:mid + 50]), list(pack_samples[mid - 50:mid + 50])
        )
        # 执行器施加了 fade_ms：首样本被压到 0，而包内原文件不是
        self.assertEqual(out_samples[0], 0)
        self.assertNotEqual(pack_samples[0], 0)

    def test_hit_prefers_variant_zero(self):
        """同 key 多变体时，key 档取包内第一个可用变体（确定性、可复现）。"""
        res = self.bridge().run_turn(session_id="s1", key="greeting", out_path=self.out())
        self.assertEqual(res.live_text, GREET)
        self.assertEqual(res.events[0].get("variant"), 0)

    def test_second_variant_hits_via_text_mode(self):
        """第二个变体在文本档逐字相等时命中（说明包内各 variant 都参与比对）。"""
        self.client.replies = [GREET_V2]
        res = self.bridge().run_turn(session_id="s1", text="用户说了点什么", out_path=self.out())
        self.assertEqual(res.state, "hit")
        self.assertEqual(res.match_mode, MODE_TEXT)
        self.assertEqual(res.tts_calls, 0)
        self.assertEqual(res.events[0][KEY], "greeting")
        self.assertEqual(res.events[0].get("variant"), 1)


class TestTextModeHit(BridgeBase):
    """文本档：normalize_text(回复) 与包内归一化文本逐字相等 → 命中。"""

    def test_verbatim_hit_with_fullwidth_and_spacing_diff(self):
        """包内是全角字母数字 + 全角逗号，回复是半角 + 带空格 + 句末无句号前空格。"""
        self.client.replies = [" 已为您登记A01号,请留意。 "]
        res = self.bridge().run_turn(session_id="s1", text="用户的问题", out_path=self.out())

        self.assertEqual(res.state, "hit")
        self.assertEqual(res.match_mode, MODE_TEXT)
        self.assertEqual(res.tts_calls, 0)
        self.assertEqual(self.tts.calls, [])
        self.assertEqual(res.events[0][HIT], True)

    def test_brain_reply_is_the_live_text(self):
        self.client.replies = [GREET]
        res = self.bridge().run_turn(session_id="sess-42", text="用户的问题", out_path=self.out())
        self.assertEqual(res.live_text, GREET)
        self.assertEqual(self.client.asks, [("sess-42", "用户的问题")])

    def test_asr_leg_feeds_brain(self):
        """给了 wav_bytes 就走 ASR，识别结果再去问 brain。"""
        self.client.replies = [GREET]
        self.client.heard = "我要报修"
        res = self.bridge().run_turn(
            session_id="s1", wav_bytes=b"RIFF....WAVEfake-audio-bytes", out_path=self.out()
        )
        self.assertEqual(res.state, "hit")
        self.assertEqual(self.client.waves, [len(b"RIFF....WAVEfake-audio-bytes")])
        self.assertEqual(self.client.asks, [("s1", "我要报修")])


class TestMissIsNotFuzzy(BridgeBase):
    """未命中：差一个字、少一个标点、换同义词——一律不算命中（禁止模糊匹配）。"""

    def test_one_char_diff_is_miss(self):
        """逐字比对：改一个字就未命中（这是断言"能失败"的实证）。"""
        edits = [
            ("删一个词", "请问您要报修什么设备？"),            # 去掉"的是"
            ("换成同义词", "请问您要报修的是哪个设备？"),      # 什么→哪个
            ("多加一个词", "请问您今天要报修的是什么设备？"),  # 插入"今天"
            ("删句末标点", ASK[:-1]),                          # 去掉"？"
            ("改一个标点", ASK[:-1] + "！"),                   # ？→！
        ]
        for label, reply in edits:
            with self.subTest(label=label):
                self.client.replies = [reply]
                bridge = self.bridge()
                with self.assertRaises(RuntimeMissError) as ctx:
                    bridge.run_turn(session_id="s1", text="用户的问题", out_path=self.out())
                self.assertIn(REASON_TEXT_NOT_PREBAKED, str(ctx.exception))

    def test_punctuation_stripping_is_not_allowed(self):
        """若有人把"去标点后比较"塞进归一化，这条会变红。"""
        # 包内 "您好，这里是报修服务热线。" 与 "您好，这里是报修服务热线" 差一个句号
        self.assertNotEqual(
            normalize_text(GREET), normalize_text(GREET.rstrip("。"))
        )
        self.client.replies = [GREET.rstrip("。")]
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(session_id="s1", text="x", out_path=self.out())
        self.assertIn(REASON_TEXT_NOT_PREBAKED, str(ctx.exception))

    def test_edit_distance_close_is_still_miss(self):
        """只差一个字符（编辑距离 1）也必须未命中——不许阈值匹配。"""
        self.client.replies = [GREET + "！"]
        with self.assertRaises(RuntimeMissError):
            self.bridge().run_turn(session_id="s1", text="x", out_path=self.out())

    def test_key_not_in_pack_is_miss(self):
        with self.assertRaises(RuntimeMissError) as ctx:
            self.bridge().run_turn(
                session_id="s1", key="not_prebaked_key", out_path=self.out()
            )
        self.assertIn("key_not_prebaked", str(ctx.exception))
        self.assertIn("not_prebaked_key", str(ctx.exception))


class TestAllowFallback(BridgeBase):
    """显式降级：走慢路合成器，出 miss 事件且原因可区分。"""

    def test_text_miss_falls_back_with_distinct_reason(self):
        self.client.replies = ["这是一句全新的话，包里没有。"]
        res = self.bridge(allow_fallback=True).run_turn(
            session_id="s1", text="用户的问题", out_path=self.out()
        )

        self.assertEqual(res.state, "miss")
        self.assertEqual(res.match_mode, MODE_TEXT)
        self.assertEqual(res.tts_calls, 1)
        self.assertEqual(len(self.tts.calls), 1)
        self.assertEqual(self.tts.calls[0]["text"], "这是一句全新的话，包里没有。")
        self.assertTrue(res.audio_path.is_file())

        ev = res.events[0]
        self.assertTrue(ev[MISS])
        self.assertNotIn(HIT, ev)
        self.assertEqual(ev[REASON], REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(ev[KEY], None)      # 文本档没有 key 可记
        self.assertGreater(res.first_audio_ms, 0.0)

    def test_key_miss_falls_back_with_distinct_reason(self):
        res = self.bridge(allow_fallback=True).run_turn(
            session_id="s1", key="no_such_key", out_path=self.out()
        )

        self.assertEqual(res.state, "miss")
        self.assertEqual(res.match_mode, MODE_KEY)
        self.assertEqual(res.tts_calls, 1)
        self.assertEqual(res.events[0][REASON], "key_not_prebaked")
        self.assertEqual(res.events[0][KEY], "no_such_key")

    def test_text_reason_differs_from_key_reason(self):
        """两个原因码必须可区分（T13 预铸准入吃这个分布）。"""
        self.client.replies = ["包里没有的话。"]
        text_reason = self.bridge(allow_fallback=True).run_turn(
            session_id="a", text="x", out_path=self.out("a.wav")
        ).events[0][REASON]
        key_reason = self.bridge(allow_fallback=True).run_turn(
            session_id="b", key="no_such_key", out_path=self.out("b.wav")
        ).events[0][REASON]
        self.assertNotEqual(text_reason, key_reason)
        self.assertEqual(text_reason, REASON_TEXT_NOT_PREBAKED)
        self.assertEqual(key_reason, "key_not_prebaked")

    def test_fallback_audio_is_live_not_pack(self):
        """降级播的是慢路合成出来的音频，不是包内那份。"""
        self.client.replies = ["包里没有的话。"]
        out = self.out()
        self.bridge(allow_fallback=True).run_turn(
            session_id="s1", text="x", out_path=out
        )

        out_samples, _ = read_wav(out)
        entry = self.pack.assets[0]
        pack_samples, _ = read_wav(self.pack.root / entry.path)
        self.assertNotEqual(len(out_samples), len(pack_samples), "时长不同")
        self.assertNotEqual(_peak(out_samples), _peak(pack_samples), "峰值不同")


class TestEngineMismatch(BridgeBase):
    """引擎不一致：一律走慢路 + fallback/engine_mismatch，绝不播包内音频。"""

    def _mismatched(self, **kw):
        """把包的音色与慢路合成器的音色改成不同值。"""
        pack = build_pack(
            self.tmp / "pack-mm",
            [("greeting", GREET, 0)],
            voice="Tingting",
            peak=8000, ms=200,
        )
        tts = CountingTts(voice="Yao")       # 慢路合成器音色 ≠ 包音色
        return KefuBridge(pack, live_tts=tts, client=self.client, **kw), pack, tts

    def test_fallback_with_engine_mismatch_reason(self):
        bridge, pack, tts = self._mismatched(allow_fallback=True)
        self.client.replies = [GREET]
        out = self.out()
        res = bridge.run_turn(session_id="s1", text="x", out_path=out)

        self.assertEqual(res.state, "fallback")
        self.assertEqual(res.events[0][FALLBACK], True)
        self.assertEqual(res.events[0][REASON], "engine_mismatch")
        self.assertEqual(res.tts_calls, 1)
        self.assertEqual(len(tts.calls), 1)

    def test_pack_audio_is_never_played_on_mismatch(self):
        """验收第 6 条：用峰值与时长证明播的不是包内音频。"""
        bridge, pack, tts = self._mismatched(allow_fallback=True)
        self.client.replies = [GREET]
        out = self.out()
        bridge.run_turn(session_id="s1", text="x", out_path=out)

        out_samples, _ = read_wav(out)
        pack_entry = pack.assets[0]
        pack_samples, _ = read_wav(pack.root / pack_entry.path)
        self.assertNotEqual(_peak(out_samples), _peak(pack_samples), "峰值必须不同")
        self.assertNotEqual(len(out_samples), len(pack_samples), "时长必须不同")

    def test_mismatch_wins_even_when_key_would_hit(self):
        """key 本来会命中，但引擎不一致时"一律"不播包内音频（docs/10 §10.4）。"""
        bridge, pack, tts = self._mismatched(allow_fallback=True)
        res = bridge.run_turn(session_id="s1", key="greeting", out_path=self.out())
        self.assertEqual(res.state, "fallback")
        self.assertEqual(res.events[0][REASON], "engine_mismatch")
        self.assertEqual(res.match_mode, MODE_KEY)

    def test_mismatch_is_not_gated_by_allow_fallback(self):
        """engine_mismatch 是"一律"降级：默认开关下也走慢路并留痕，不算静默降级。"""
        bridge, pack, tts = self._mismatched()          # allow_fallback=False
        self.client.replies = [GREET]
        res = bridge.run_turn(session_id="s1", text="x", out_path=self.out())
        self.assertEqual(res.state, "fallback")
        self.assertEqual(res.events[0][REASON], "engine_mismatch")
        self.assertEqual(res.tts_calls, 1)
        self.assertTrue(res.audio_path.is_file())

    def test_model_version_mismatch_also_fallback(self):
        """模型版本不一致同样视为引擎不一致（与 runtime 语义一致）。"""
        pack = build_pack(self.tmp / "pack-mv", [("k", GREET, 0)], model_version="macos-say")
        tts = CountingTts(model_version="other-model")
        bridge = KefuBridge(pack, live_tts=tts, client=self.client, allow_fallback=True)
        self.client.replies = [GREET]
        res = bridge.run_turn(session_id="s1", text="x", out_path=self.out())
        self.assertEqual(res.state, "fallback")
        self.assertEqual(res.events[0][REASON], "engine_mismatch")


class TestEventsAndContract(BridgeBase):
    """事件字段与返回结构。"""

    def test_event_keys_come_from_metrics_spec(self):
        """字段名一律引 core.metrics_spec；唯一新增字段是 match_mode。"""
        self.client.replies = [GREET]
        hits = [
            self.bridge().run_turn(session_id="a", key="greeting",
                                   out_path=self.out("a.wav")).events[0],
            self.bridge().run_turn(session_id="b", text="x",
                                   out_path=self.out("b.wav")).events[0],
        ]
        for ev in hits:
            extra = set(ev) - METRIC_FIELDS - {MATCH_MODE}
            self.assertEqual(extra, set(), f"出现 metrics_spec 之外的字段: {sorted(extra)}")
            states = [s for s in (HIT, MISS, FALLBACK) if ev.get(s)]
            self.assertEqual(len(states), 1, f"事件必须恰好一个三态: {ev}")

    def test_fallback_event_keys_also_clean(self):
        pack = build_pack(self.tmp / "pack-ev", [("k", GREET, 0)], voice="Tingting")
        bridge = KefuBridge(pack, live_tts=CountingTts(voice="Yao"),
                            client=self.client, allow_fallback=True)
        self.client.replies = [GREET]
        ev = bridge.run_turn(session_id="s1", text="x", out_path=self.out()).events[0]
        extra = set(ev) - METRIC_FIELDS - {MATCH_MODE}
        self.assertEqual(extra, set(), f"出现 metrics_spec 之外的字段: {sorted(extra)}")

    def test_bridge_rejects_pack_without_contract(self):
        """pack 缺必需成员（voice / lookup …）→ BridgeError，本层不猜。"""
        class _NoContract:
            pass
        with self.assertRaises(BridgeError) as ctx:
            KefuBridge(_NoContract(), live_tts=self.tts, client=self.client)
        msg = str(ctx.exception)
        self.assertIn("voice", msg)
        self.assertIn("lookup", msg)

    def test_bridge_rejects_live_tts_without_voice(self):
        """慢路合成器不声明音色 → 无法判引擎一致性 → 拒绝装配（不静默）。"""
        class _NoVoice:
            model_version = "x"

            def synthesize(self, text, out_path, rate_key="normal"):
                pass
        with self.assertRaises(BridgeError) as ctx:
            KefuBridge(self.pack, live_tts=_NoVoice(), client=self.client)
        self.assertIn("voice", str(ctx.exception))

    def test_result_shape(self):
        self.client.replies = [GREET]
        res = self.bridge().run_turn(session_id="s1", text="x", out_path=self.out())
        self.assertIsInstance(res.audio_path, Path)
        self.assertIsInstance(res.tts_calls, int)
        self.assertIsInstance(res.first_audio_ms, float)
        self.assertIsInstance(res.events, list)
        self.assertGreaterEqual(res.first_audio_ms, 0.0)


class TestClientHelpers(unittest.TestCase):
    """kefu_client 的响应解析与入参护栏（不调网络、不起 worker）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kefu-client-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_reply_bare_string(self):
        self.assertEqual(extract_reply(" 您好。 "), "您好。")

    def test_extract_reply_known_shapes(self):
        self.assertEqual(extract_reply({"reply": "您好"}), "您好")
        self.assertEqual(extract_reply({"content": "您好"}), "您好")
        self.assertEqual(
            extract_reply({"choices": [{"message": {"content": "您好"}}]}), "您好"
        )
        self.assertEqual(extract_reply({"text": {"content": "您好"}}), "您好")
        self.assertEqual(extract_reply(["您", "好"]), "您好")

    def test_extract_reply_ignores_unknown_wrappers(self):
        """只认已知回复字段：未知包装字段返回 None（不猜、不静默降级）。"""
        self.assertIsNone(extract_reply({"outer": {"text": "您好"}}))
        self.assertIsNone(extract_reply({"prompt": "您好"}))
        self.assertIsNone(extract_reply({"sessionId": "s1"}))

    def test_extract_reply_returns_none_when_absent(self):
        """取不到回复文本返回 None（由调用方抛 KefuBrainError），不返回空串。"""
        self.assertIsNone(extract_reply({"reply": "   "}))
        self.assertIsNone(extract_reply(12345))
        self.assertIsNone(extract_reply(None))

    def test_extract_wav_from_base64(self):
        payload = b"RIFF\x00\x00\x00\x00WAVE"
        got = extract_wav({"wavB64": base64.b64encode(payload).decode("ascii")})
        self.assertEqual(got, payload)

    def test_extract_wav_from_path(self):
        wav = self.tmp / "from_worker.wav"
        write_wav(wav, peak=100, ms=10)
        self.assertEqual(extract_wav({"wavPath": str(wav)}), wav.read_bytes())

    def test_extract_wav_missing_raises(self):
        with self.assertRaises(KefuWorkerError) as ctx:
            extract_wav({"engine": "say", "ok": True})
        self.assertIn("没有音频", str(ctx.exception))
        self.assertIn("engine", str(ctx.exception))     # 报错要带现场字段，便于定位

    def test_client_timeout_must_be_positive(self):
        with self.assertRaises(TypeError) as ctx:
            KefuClient(timeout_s=0)
        self.assertIn("timeout_s", str(ctx.exception))

    def test_client_rejects_empty_input(self):
        client = KefuClient()
        with self.assertRaises(TypeError):
            client.transcribe(b"")
        with self.assertRaises(TypeError):
            client.synthesize_live("   ")
        with self.assertRaises(TypeError):
            client.ask_brain("", "您好")
        with self.assertRaises(TypeError):
            client.ask_brain("s1", "")

    def test_client_reports_missing_worker_script(self):
        """worker 脚本不存在 → 明确报路径，不静默返回空串。"""
        client = KefuClient(worker_script="/tmp/no-such-voice_worker.py")
        with self.assertRaises(KefuWorkerError) as ctx:
            client.transcribe(b"RIFF....WAVE")
        self.assertIn("no-such-voice_worker.py", str(ctx.exception))
        client.close()


if __name__ == "__main__":
    unittest.main()
