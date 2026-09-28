"""
runtime.tests.test_duplex_policy — 开口策略四参数被消费（T19）

覆盖范围（四条可观测行为，每条至少一个可判红的测试）：
  1. patience_ms  → 事件流末尾追加等待窗口事件（LISTEN_MS），值随参数变；音频字节不变
  2. backchannel  → 累计播报时长 ≥ patience_ms 的单元事件带 backchannel_ok；off 恒 False
  3. barge_in     → 每条单元事件带 barge_in 值；confirm 时终态单元带 requires_confirm
  4. rate_band    → critical 单元强制 slow；缺 slow 档且带内 → 回落 + rate_fallback 留痕；
                    带外 → miss（fail-closed，不静默用别的档播出去）
  5. 音频不变性   → patience_ms / backchannel / barge_in 三参数任意取值下 WAV sha256 相同；
                    只有 rate_band（档位选择）改变音频
  6. 负例         → 四参数非法值 → DuplexError（消息含参数名与实际值）；
                    等待窗口事件构造负例 → EventError
  7. 字段纪律     → 新事件键全部来自 core.metrics_spec（ALL_EVENT_FIELDS）

纪律：
  - 既有测试文件一行未改（新增文件承载新断言）
  - 全部走产品 API：Executor.execute + 手搓真包 + 事件流读取
  - 不 import compiler/ 或 rules/（runtime/AGENTS.md §⑤ 层边界）
  - 复用 test_executor.py 的手搓夹具（make_pack / FakeTts / _write_wav / _level）
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

import core.metrics_spec as spec
from assets.fingerprint import fingerprint
from assets.pack import load_pack

from runtime.duplex import DuplexError, DuplexParams
from runtime.events import EventError, build_listen_event
from runtime.executor import (
    REASON_CRITICAL_RATE_OUT_OF_BAND,
    Executor,
    RuntimeMissError,
)
from runtime.tests.test_executor import FakeTts, SR, _level, _write_wav, make_pack


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _entries_long():
    """两条"够长"的预铸话术：各 1000ms，累计 2000ms > 最大耐心窗 1800ms。

    故意取长时长，保证 backchannel 判据在 patience_ms ∈ {400,900,1800} 三档下
    都能触达（patience_ms 只影响"标记挂在哪条"，不影响音频）。
    """
    return [
        {"key": "greeting", "text": "您好，请问需要什么帮助", "rate_key": "normal",
         "variant": 0, "path": "audio/greeting_v0.wav", "level": 20000, "ms": 1000},
        {"key": "goodbye", "text": "感谢您的来电，祝您生活愉快", "rate_key": "normal",
         "variant": 0, "path": "audio/goodbye_v0.wav", "level": 10000, "ms": 1000},
    ]


def _entries_critical():
    """critical 单元的三种包内形态：只有 normal 档（用于回落与超带判定）。"""
    return [
        {"key": "amount", "text": "您的缴费金额是三十元", "rate_key": "normal",
         "variant": 0, "path": "audio/amount_v0.wav", "level": 18000, "ms": 800},
    ]


def _entries_with_slow():
    """critical 单元有 slow 档（用于"选中 slow"的正例）。"""
    return [
        {"key": "amount", "text": "您的缴费金额是三十元", "rate_key": "normal",
         "variant": 0, "path": "audio/amount_n.wav", "level": 18000, "ms": 800},
        {"key": "amount", "text": "您的缴费金额是三十元", "rate_key": "slow",
         "variant": 0, "path": "audio/amount_s.wav", "level": 12000, "ms": 1100},
    ]


def _sha256(path):
    """输出 WAV 的字节指纹（音频不变性判据）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _Base(unittest.TestCase):
    """公共夹具：一个 tempfile 包根 + 假 TTS + 输出路径。"""

    entries = _entries_long()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack_root = Path(self.tmp.name) / "pack"
        self.pack_root.mkdir(parents=True)
        self.pack = make_pack(self.pack_root, self.entries)
        self.tts = FakeTts()
        self.out = Path(self.tmp.name) / "out.wav"
        self.policy = True  # 新消费者显式 opt in 双工策略事件流

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self):
        return [{"key": "greeting"}, {"key": "goodbye"}]

    def _unit_events(self, events):
        """从事件流里剔除末尾的等待窗口事件，只留单元事件。

        过滤键：单元事件必带 PART；等待窗口事件不带 PART（形态自描述）。
        这是按形态分流，不依赖事件顺序。
        """
        return [e for e in events if spec.PART in e]

    def _listen_event(self, events):
        """取出等待窗口事件；不存在则断言失败。"""
        matches = [e for e in events if spec.LISTEN_MS in e]
        self.assertEqual(
            len(matches), 1,
            f"事件流必须恰好一条等待窗口事件，实际 {len(matches)}",
        )
        return matches[0]


# ============================================================
# 1. patience_ms → 等待窗口事件（音频不变）
# ============================================================
class TestPatienceWindow(_Base):
    """patience_ms 唯一被消费的行为：事件流末尾追加等待窗口事件。"""

    def _run(self, duplex):
        return Executor(self.pack, self.tts, duplex=duplex,
                        policy_stream=self.policy).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    def test_listen_event_appended_at_tail(self):
        """事件流最后一条必须是等待窗口事件，值为 patience_ms。"""
        result = self._run(DuplexParams.default())
        events = result.events
        self.assertIn(spec.LISTEN_MS, events[-1],
                      "事件流末尾必须追加等待窗口事件")
        self.assertEqual(events[-1][spec.LISTEN_MS], 900)
        # 等待窗口事件不是单元事件：不带三态键
        self.assertFalse(any(s in events[-1] for s in (spec.HIT, spec.MISS, spec.FALLBACK)))

    def test_listen_ms_follows_parameter(self):
        """换 patience_ms（400/900/1800）→ 等待窗口事件里的时长值随之变。"""
        observed = {}
        for ms in (400, 900, 1800):
            result = self._run(DuplexParams(patience_ms=ms))
            observed[ms] = self._listen_event(result.events)[spec.LISTEN_MS]
        self.assertEqual(observed, {400: 400, 900: 900, 1800: 1800},
                         f"等待窗口时长必须随 patience_ms 变化，实际 {observed}")

    def test_listen_event_carries_plan_context(self):
        """等待窗口事件必须带 turn_id / plan_id / pack_version（可关联留痕）。"""
        result = self._run(DuplexParams.default())
        event = self._listen_event(result.events)
        self.assertEqual(event[spec.TURN_ID], "turn-1")
        self.assertEqual(event[spec.PLAN_ID], "plan-1")
        self.assertEqual(event[spec.PACK_VERSION], "1.0.0")
        self.assertNotIn(spec.PART, event, "等待窗口事件不属于任何 plan 单元")

    def test_patience_window_is_not_audio(self):
        """等待窗口只是事件语义，绝不产生音频（总时长不变）。"""
        for ms in (400, 900, 1800):
            result = self._run(DuplexParams(patience_ms=ms))
            self.assertEqual(
                result.total_duration_ms, 2200,
                f"patience_ms={ms} 不得改变音频总时长（1000+200 静音垫+1000）",
            )


# ============================================================
# 2. backchannel → 可发背景回应标记
# ============================================================
class TestBackchannel(_Base):
    """backchannel=on 时，累计播报时长 ≥ patience_ms 的单元事件带标记。"""

    def _run(self, duplex):
        return Executor(self.pack, self.tts, duplex=duplex,
                        policy_stream=self.policy).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    def test_on_marks_unit_reaching_patience(self):
        """on + patience_ms=900：单元 1（累计 1000ms ≥ 900）带 True，单元 2 亦 True。"""
        result = self._run(DuplexParams(patience_ms=900, backchannel="on"))
        units = self._unit_events(result.events)
        flags = [e[spec.BACKCHANNEL_OK] for e in units]
        self.assertEqual(flags, [True, True],
                         f"两条单元累计均已达 900ms，应都带标记，实际 {flags}")

    def test_on_marker_flips_between_units(self):
        """on + patience_ms=1800：单元 1（1000ms）未到窗 → False；单元 2（2000ms）→ True。"""
        result = self._run(DuplexParams(patience_ms=1800, backchannel="on"))
        flags = [e[spec.BACKCHANNEL_OK] for e in self._unit_events(result.events)]
        self.assertEqual(flags, [False, True],
                         f"累计达窗的那条应带标记，实际 {flags}")

    def test_off_always_false(self):
        """off 时标记恒 False（不看累计时长，也不看 patience_ms）。"""
        for ms in (400, 900, 1800):
            with self.subTest(patience_ms=ms):
                result = self._run(DuplexParams(patience_ms=ms, backchannel="off"))
                units = self._unit_events(result.events)
                self.assertTrue(units, "应有单元事件")
                for event in units:
                    self.assertIs(
                        event[spec.BACKCHANNEL_OK], False,
                        f"backchannel=off 时标记必须恒 False（patience_ms={ms}）",
                    )

    def test_flag_flips_with_switch(self):
        """同一 plan 换开关 → 标记翻转（可观测判据）。"""
        on = self._run(DuplexParams(patience_ms=1800, backchannel="on"))
        off = self._run(DuplexParams(patience_ms=1800, backchannel="off"))
        on_flags = [e[spec.BACKCHANNEL_OK] for e in self._unit_events(on.events)]
        off_flags = [e[spec.BACKCHANNEL_OK] for e in self._unit_events(off.events)]
        self.assertNotEqual(on_flags, off_flags, "开关必须改变标记")
        self.assertEqual(on_flags, [False, True])
        self.assertEqual(off_flags, [False, False])

    def test_spoken_ms_accumulates(self):
        """每条单元事件带已播累计时长（backchannel 判据的输入，必须可复核）。"""
        result = self._run(DuplexParams(patience_ms=900, backchannel="on"))
        spoken = [e[spec.SPOKEN_MS] for e in self._unit_events(result.events)]
        self.assertEqual(spoken, [1000, 2000],
                         f"累计时长必须按单元累加，实际 {spoken}")

    def test_patience_ms_redundant_in_unit_events(self):
        """单元事件冗余一份 patience_ms，便于按事件聚合。"""
        result = self._run(DuplexParams(patience_ms=400, backchannel="on"))
        for event in self._unit_events(result.events):
            self.assertEqual(event[spec.PATIENCE_MS], 400)


# ============================================================
# 3. barge_in → 字段值 + 需确认标记
# ============================================================
class TestBargeIn(_Base):
    """每条单元事件带 barge_in 值；confirm 时终态单元额外带 requires_confirm。"""

    def _run(self, duplex):
        return Executor(self.pack, self.tts, duplex=duplex,
                        policy_stream=self.policy).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    def test_every_unit_carries_barge_in_value(self):
        """每条单元事件都带 barge_in 字段，值与参数一致。"""
        for value in ("allow", "confirm"):
            with self.subTest(barge_in=value):
                result = self._run(DuplexParams(barge_in=value))
                units = self._unit_events(result.events)
                self.assertEqual(
                    [e[spec.BARGE_IN] for e in units], [value, value],
                    "每条单元事件必须带 barge_in 值",
                )

    def test_value_flips_with_parameter(self):
        """换参数 → 字段值变（可观测判据）。"""
        allow = self._run(DuplexParams(barge_in="allow"))
        confirm = self._run(DuplexParams(barge_in="confirm"))
        self.assertEqual([e[spec.BARGE_IN] for e in self._unit_events(allow.events)],
                         ["allow", "allow"])
        self.assertEqual([e[spec.BARGE_IN] for e in self._unit_events(confirm.events)],
                         ["confirm", "confirm"])

    def test_confirm_marks_last_unit_only(self):
        """confirm 下终态单元（plan 最后一个）带 requires_confirm=True，其余 False。"""
        result = self._run(DuplexParams(barge_in="confirm"))
        units = self._unit_events(result.events)
        flags = [e[spec.REQUIRES_CONFIRM] for e in units]
        self.assertEqual(flags, [False, True],
                         f"只有终态单元应带需确认标记，实际 {flags}")

    def test_allow_never_marks(self):
        """allow 下 requires_confirm 恒 False（任何单元都可被打断）。"""
        result = self._run(DuplexParams(barge_in="allow"))
        for event in self._unit_events(result.events):
            self.assertIs(event[spec.REQUIRES_CONFIRM], False,
                          "allow 下不得出现需确认标记")

    def test_terminal_keys_override_last_unit(self):
        """terminal_keys 命中时按 key 判终态，不受位置影响。"""
        entries = [
            {"key": "greeting", "text": "您好", "rate_key": "normal", "variant": 0,
             "path": "audio/g.wav", "level": 20000, "ms": 100},
            {"key": "amount", "text": "金额三十元", "rate_key": "normal", "variant": 0,
             "path": "audio/a.wav", "level": 12000, "ms": 100},
            {"key": "confirm_ask", "text": "请确认", "rate_key": "normal", "variant": 0,
             "path": "audio/c.wav", "level": 8000, "ms": 100},
        ]
        pack = make_pack(Path(self.tmp.name) / "pack3", entries)
        duplex = DuplexParams(barge_in="confirm", terminal_keys={"amount"})
        result = Executor(pack, self.tts, duplex=duplex,
                          policy_stream=True).execute(
            [{"key": "greeting"}, {"key": "amount"}, {"key": "confirm_ask"}],
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        units = self._unit_events(result.events)
        self.assertEqual(
            [e[spec.REQUIRES_CONFIRM] for e in units], [False, True, True],
            "terminal_keys 命中（amount）与最后一个单元都要带标记",
        )
        self.assertEqual([e[spec.KEY] for e in units],
                         ["greeting", "amount", "confirm_ask"])

    def test_non_terminal_unit_never_forces_fallback(self):
        """非 critical 单元不得触发档位回落（不顺手放宽）。"""
        result = self._run(DuplexParams(barge_in="confirm"))
        for event in self._unit_events(result.events):
            self.assertFalse(
                event.get(spec.RATE_FALLBACK, False),
                "非 critical 单元不得出现档位回落留痕",
            )


# ============================================================
# 4. rate_band → critical 单元强制 slow / 带内回落 / 带外 miss
# ============================================================
class TestRateBandCritical(_Base):
    """critical 单元：强制 slow；缺档按收敛带判回落；超带 fail-closed。"""

    def setUp(self):
        super().setUp()
        self.pack = make_pack(Path(self.tmp.name) / "pack-crit", _entries_critical())
        self.plan = [{"key": "amount", "rate": "normal", "critical": True}]

    def _run(self, duplex, plan=None, allow_fallback=False):
        return Executor(self.pack, self.tts, duplex=duplex,
                        allow_fallback=allow_fallback,
                        policy_stream=self.policy).execute(
            plan if plan is not None else self.plan,
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )

    def test_slow_asset_present_selects_slow(self):
        """包内有 slow 档 → 选中 slow（事件 rate 记 slow，requested_rate 不写）。"""
        pack = make_pack(Path(self.tmp.name) / "pack-slow", _entries_with_slow())
        result = Executor(pack, self.tts,
                          duplex=DuplexParams(rate_band=0.20),
                          policy_stream=True).execute(
            [{"key": "amount", "rate": "normal", "critical": True}],
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        event = self._unit_events(result.events)[0]
        self.assertIs(event[spec.HIT], True)
        self.assertEqual(event[spec.RATE], "slow",
                         "critical 单元必须强制 slow 档")
        self.assertEqual(event[spec.REQUESTED_RATE], "",
                         "没发生回落时 requested_rate 留空")
        self.assertFalse(event[spec.RATE_FALLBACK],
                         "没发生回落时不得留回落痕")
        self.assertEqual(result.hit_count, 1)

    def test_slow_missing_and_in_band_falls_back_with_trace(self):
        """包内只有 normal 档 + rate_band=0.20（≥相邻档差）→ 回落 normal，事件留痕。"""
        result = self._run(DuplexParams(rate_band=0.20), allow_fallback=True)
        event = self._unit_events(result.events)[0]
        self.assertIs(event[spec.HIT], True)
        self.assertEqual(event[spec.RATE], "normal",
                         "带内回落到最近可用档（normal）")
        self.assertEqual(event[spec.REQUESTED_RATE], "slow",
                         "必须留痕计划档位（slow）")
        self.assertIs(event[spec.RATE_FALLBACK], True,
                      "必须留痕发生了档位回落（不得静默）")
        self.assertEqual(result.hit_count, 1)

    def test_out_of_band_never_succeeds_even_with_fallback(self):
        """超带是更硬的 fail-closed：allow_fallback=True 也不得用别的档合出音频。

        WHY 连 allow_fallback 都不放行：那是"本该关键信息慢说却跑成常速"——
        静默降级，事后无法定位。留痕的"超带"结论只能以抛错形式给出。
        """
        with self.assertRaises(RuntimeMissError) as ctx:
            self._run(DuplexParams(rate_band=0.05), allow_fallback=True)
        message = str(ctx.exception)
        self.assertIn(REASON_CRITICAL_RATE_OUT_OF_BAND, message)
        self.assertIn("amount", message)
        self.assertFalse(self.out.exists(), "中止后不得留下输出文件")
        self.assertEqual(self.tts.calls, [], "中止前不得有任何合成调用")

    def test_out_of_band_message_reports_true_allow_fallback(self):
        """allow_fallback=True 仍被拦下时，消息必须如实写出 True（不得谎称 False）。"""
        with self.assertRaises(RuntimeMissError) as ctx:
            self._run(DuplexParams(rate_band=0.05), allow_fallback=True)
        message = str(ctx.exception)
        self.assertIn("allow_fallback=True", message,
                       f"消息必须如实写出实际取值，实际: {message!r}")
        self.assertNotIn("allow_fallback=False", message,
                         "不得谎称 allow_fallback=False")
        self.assertIn("0.05", message, "消息必须含实际收敛带取值")

    def test_out_of_band_is_fail_closed_by_default(self):
        """超带 + allow_fallback=False → 抛 RuntimeMissError，不写输出文件。"""
        with self.assertRaises(RuntimeMissError) as ctx:
            self._run(DuplexParams(rate_band=0.05))
        message = str(ctx.exception)
        self.assertIn("amount", message)
        self.assertIn(REASON_CRITICAL_RATE_OUT_OF_BAND, message)
        self.assertFalse(self.out.exists(), "中止后不得存在输出文件")

    def test_non_critical_unit_is_untouched(self):
        """非 critical 单元：同样缺档时走既有语义（不因为本卡而放宽/收紧）。"""
        result = self._run(DuplexParams(rate_band=0.20),
                           plan=[{"key": "amount"}], allow_fallback=True)
        event = self._unit_events(result.events)[0]
        self.assertIs(event[spec.HIT], True,
                      "非 critical 单元按既有语义命中 normal 档")
        self.assertFalse(event[spec.RATE_FALLBACK],
                         "非 critical 单元不得发生档位回落")
        self.assertEqual(event[spec.REQUESTED_RATE], "",
                         "非 critical 单元不记关键信息计划档")

    def test_out_of_band_produces_no_artifact(self):
        """带内回落有产物（播包内音频）；超带一律无产物（fail-closed）。"""
        self._run(DuplexParams(rate_band=0.20), allow_fallback=True)
        in_band_sha = _sha256(self.out)
        self.assertTrue(self.out.exists())
        self.assertGreater(len(in_band_sha), 0)
        for band in (0.05, 0.10, 0.15):
            with self.subTest(rate_band=band):
                victim = Path(self.tmp.name) / f"band_{band}.wav"
                with self.assertRaises(RuntimeMissError):
                    self._run(DuplexParams(rate_band=band),
                              allow_fallback=True)
                self.assertFalse(victim.exists(),
                                 "超带不得留下输出文件")


# ============================================================
# 5. 音频不变性（sha256）
# ============================================================
class TestAudioImmutability(_Base):
    """patience_ms / backchannel / barge_in 任意取值 → WAV 字节完全相同。"""

    def _run(self, duplex, path):
        return Executor(self.pack, self.tts, duplex=duplex,
                        policy_stream=self.policy).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=path,
        )

    def test_patience_ms_does_not_change_audio(self):
        """三档耐心窗的输出 sha256 相同。"""
        shas = {}
        for ms in (400, 900, 1800):
            p = Path(self.tmp.name) / f"p{ms}.wav"
            self._run(DuplexParams(patience_ms=ms), p)
            shas[ms] = _sha256(p)
        self.assertEqual(len(set(shas.values())), 1,
                         f"patience_ms 只影响事件流，WAV 必须字节不变：{shas}")

    def test_backchannel_does_not_change_audio(self):
        """开关切换的输出 sha256 相同。"""
        on = Path(self.tmp.name) / "bc_on.wav"
        off = Path(self.tmp.name) / "bc_off.wav"
        self._run(DuplexParams(backchannel="on"), on)
        self._run(DuplexParams(backchannel="off"), off)
        self.assertEqual(_sha256(on), _sha256(off),
                         "backchannel 只影响事件流，WAV 必须字节不变")

    def test_barge_in_does_not_change_audio(self):
        """打断策略切换的输出 sha256 相同。"""
        allow = Path(self.tmp.name) / "bi_allow.wav"
        confirm = Path(self.tmp.name) / "bi_confirm.wav"
        self._run(DuplexParams(barge_in="allow"), allow)
        self._run(DuplexParams(barge_in="confirm"), confirm)
        self.assertEqual(_sha256(allow), _sha256(confirm),
                         "barge_in 只影响事件流，WAV 必须字节不变")

    def test_three_event_only_params_all_combined(self):
        """三参数全部取遍组合，输出 sha256 仍然一致（组合不产生耦合）。"""
        shas = set()
        for ms in (400, 1800):
            for bc in ("on", "off"):
                for bi in ("allow", "confirm"):
                    p = Path(self.tmp.name) / f"c_{ms}_{bc}_{bi}.wav"
                    self._run(DuplexParams(patience_ms=ms, backchannel=bc,
                                           barge_in=bi), p)
                    shas.add(_sha256(p))
        self.assertEqual(len(shas), 1,
                         f"三参数 8 种组合应产出同一 WAV，实际 {len(shas)} 种")

    def test_rate_band_does_change_audio(self):
        """对照：只有 rate_band（档位选择）会改变音频。"""
        pack = make_pack(Path(self.tmp.name) / "pack-rb", _entries_critical())
        plan = [{"key": "amount", "rate": "normal", "critical": True}]

        bandy = Path(self.tmp.name) / "bandy.wav"
        tight = Path(self.tmp.name) / "tight.wav"
        Executor(pack, self.tts, duplex=DuplexParams(rate_band=0.20),
                 allow_fallback=True, policy_stream=True).execute(
            plan, plan_id="p", turn_id="t", out_path=bandy,
        )
        with self.assertRaises(RuntimeMissError):
            Executor(pack, self.tts, duplex=DuplexParams(rate_band=0.05),
                 policy_stream=True).execute(
                plan, plan_id="p", turn_id="t", out_path=tight,
            )
        self.assertTrue(bandy.exists())
        self.assertFalse(tight.exists(), "超带不得产出音频（对照不变性判据的反例）")
        # 与"有 slow 档"的包对比：档位不同 → 音频必然不同
        pack_slow = make_pack(Path(self.tmp.name) / "pack-slow", _entries_with_slow())
        slowy = Path(self.tmp.name) / "slowy.wav"
        Executor(pack_slow, self.tts, duplex=DuplexParams(rate_band=0.20),
                 policy_stream=True).execute(
            plan, plan_id="p", turn_id="t", out_path=slowy,
        )
        self.assertNotEqual(_sha256(bandy), _sha256(slowy),
                            "档位选择不同（normal vs slow）必须产出不同音频")


# ============================================================
# 6. 负例：校验强度只增不减
# ============================================================
class TestDuplexValidationNegative(unittest.TestCase):
    """四参数任一非法值 → DuplexError，消息含参数名与实际值。"""

    def _assert_duplex_error(self, kwargs, name, bad_value):
        with self.assertRaises(DuplexError) as ctx:
            DuplexParams(**kwargs)
        message = str(ctx.exception)
        self.assertIn(name, message,
                      f"消息必须含参数名 {name}，实际: {message!r}")
        self.assertIn(str(bad_value), message,
                      f"消息必须含实际值 {bad_value}，实际: {message!r}")
        return message

    def test_patience_ms_out_of_range(self):
        """patience_ms 只认 400/900/1800（不接受插值 1000）。"""
        self._assert_duplex_error({"patience_ms": 1000}, "patience_ms", "1000")

    def test_patience_ms_bool_rejected(self):
        """patience_ms=True → 拒绝（bool 不是 int）。"""
        self._assert_duplex_error({"patience_ms": True}, "patience_ms", "True")

    def test_rate_band_zero_rejected(self):
        """rate_band=0 → 拒绝（收敛带必须为正，否则任何跨档都判超带）。"""
        self._assert_duplex_error({"rate_band": 0}, "rate_band", "0")

    def test_rate_band_over_one_rejected(self):
        """rate_band=1.5 → 拒绝（超出 (0,1]）。"""
        self._assert_duplex_error({"rate_band": 1.5}, "rate_band", "1.5")

    def test_rate_band_negative_rejected(self):
        """rate_band=-0.1 → 拒绝。"""
        self._assert_duplex_error({"rate_band": -0.1}, "rate_band", "-0.1")

    def test_rate_band_bool_rejected(self):
        """rate_band=True → 拒绝（bool 是 int 子类，必须显式排除）。"""
        self._assert_duplex_error({"rate_band": True}, "rate_band", "True")

    def test_backchannel_invalid(self):
        """backchannel="maybe" → 拒绝。"""
        self._assert_duplex_error({"backchannel": "maybe"}, "backchannel", "maybe")

    def test_backchannel_case_sensitive(self):
        """backchannel="ON" → 拒绝（只认白名单，不做大小写归一）。"""
        self._assert_duplex_error({"backchannel": "ON"}, "backchannel", "ON")

    def test_barge_in_invalid(self):
        """barge_in="block" → 拒绝。"""
        self._assert_duplex_error({"barge_in": "block"}, "barge_in", "block")

    def test_barge_in_case_sensitive(self):
        """barge_in="Allow" → 拒绝。"""
        self._assert_duplex_error({"barge_in": "Allow"}, "barge_in", "Allow")

    def test_terminal_keys_non_string(self):
        """terminal_keys 含非字符串 → 拒绝。"""
        with self.assertRaises(DuplexError) as ctx:
            DuplexParams(terminal_keys={1, "a"})
        self.assertIn("terminal_keys", str(ctx.exception))

    def test_terminal_keys_empty_string_rejected(self):
        """terminal_keys 含空字符串 → 拒绝（会误标所有匿名单元）。"""
        with self.assertRaises(DuplexError) as ctx:
            DuplexParams(terminal_keys={"", "a"})
        self.assertIn("terminal_keys", str(ctx.exception))

    def test_terminal_keys_coerced_from_list(self):
        """传普通集合/列表也能用（frozen dataclass 在 __post_init__ 归一成 frozenset）。"""
        params = DuplexParams(terminal_keys=["amount"])
        self.assertEqual(params.terminal_keys, frozenset({"amount"}))


# ============================================================
# 7. 事件构造负例 + 字段纪律
# ============================================================
class TestListenEventValidation(_Base):
    """等待窗口事件构造的负例，以及新字段全部来自 metrics_spec。"""

    def test_negative_listen_ms_rejected(self):
        """listen_ms=-1 → EventError，消息含字段名与值。"""
        with self.assertRaises(EventError) as ctx:
            build_listen_event(
                turn_id="t", plan_id="p", listen_ms=-1,
            )
        message = str(ctx.exception)
        self.assertIn("listen_ms", message)
        self.assertIn("-1", message)

    def test_bool_listen_ms_rejected(self):
        """listen_ms=True → EventError（bool 是 int 子类，必须排除）。"""
        with self.assertRaises(EventError):
            build_listen_event(
                turn_id="t", plan_id="p", listen_ms=True,
            )

    def test_all_new_keys_come_from_metric_spec(self):
        """四参数产生的新键必须全部来自 core.metrics_spec.ALL_EVENT_FIELDS。"""
        pack = make_pack(Path(self.tmp.name) / "pack-all", _entries_with_slow())
        duplex = DuplexParams(
            patience_ms=400, backchannel="on", barge_in="confirm", rate_band=0.05,
        )
        result = Executor(pack, self.tts, duplex=duplex,
                          allow_fallback=True, policy_stream=True).execute(
            [{"key": "amount", "rate": "normal", "critical": True}],
            plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        for event in result.events:
            unknown = set(event.keys()) - spec.ALL_EVENT_FIELDS
            self.assertEqual(
                unknown, set(),
                f"事件出现了 metrics_spec 之外的键: {sorted(unknown)}",
            )

    def test_duplex_field_constants_are_strings(self):
        """双工策略字段常量必须都是字符串且互不重复。"""
        values = [spec.BARGE_IN, spec.REQUIRES_CONFIRM, spec.BACKCHANNEL_OK,
                  spec.PATIENCE_MS, spec.SPOKEN_MS, spec.REQUESTED_RATE,
                  spec.RATE_FALLBACK, spec.LISTEN_MS]
        for value in values:
            self.assertIsInstance(value, str, f"{value!r} 必须是字符串")
        self.assertEqual(len(set(values)), len(values), "字段常量不得重复")

    def test_metric_fields_unchanged(self):
        """METRIC_FIELDS 仍是 15 个（只增字段不得污染报告口径集合）。"""
        self.assertEqual(len(spec.METRIC_FIELDS), 15)
        for value in [spec.BARGE_IN, spec.REQUIRES_CONFIRM, spec.BACKCHANNEL_OK,
                      spec.PATIENCE_MS, spec.SPOKEN_MS, spec.REQUESTED_RATE,
                      spec.RATE_FALLBACK, spec.LISTEN_MS]:
            self.assertNotIn(value, spec.METRIC_FIELDS,
                             f"{value!r} 不得并入 METRIC_FIELDS")

    def test_listen_event_not_a_unit_event(self):
        """等待窗口事件缺三态键与 rate/key（形态自描述，便于按形态分流）。"""
        result = Executor(self.pack, self.tts,
                          duplex=DuplexParams.default(),
                          policy_stream=True).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        event = self._listen_event(result.events)
        self.assertNotIn(spec.RATE, event)
        self.assertNotIn(spec.VARIANT, event)
        self.assertNotIn(spec.FIRST_AUDIO_MS, event)


if __name__ == "__main__":
    unittest.main()


# ============================================================
# 8. 策略流开关：默认形状零变化（不静默降级）
# ============================================================
class TestPolicyStreamOptIn(_Base):
    """policy_stream 默认 False = 事件流形状与既往逐字节一致。

    这是"不静默降级"的落点：既有下游按位置/按字段集合消费事件流，
    默认打开新形态会让它们静默读到等待窗口事件。
    """

    def test_default_shape_is_one_event_per_unit(self):
        """默认（policy_stream=False）：N 个单元 → N 条事件，无等待窗口事件。"""
        result = Executor(self.pack, self.tts).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(len(result.events), 2,
                         "旧形状必须是一个单元恰好一条事件")
        self.assertEqual([e[spec.PART] for e in result.events], [1, 2])
        self.assertFalse(any(spec.LISTEN_MS in e for e in result.events))

    def test_default_shape_has_no_policy_fields(self):
        """默认形状下不得出现任何双工策略字段（字段集合与既往一致）。"""
        result = Executor(self.pack, self.tts).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        for event in result.events:
            unknown = set(event.keys()) - spec.METRIC_FIELDS
            self.assertEqual(unknown, set(),
                             f"默认形状不得出现策略字段: {sorted(unknown)}")

    def test_policy_stream_adds_listen_event(self):
        """显式 opt in：N 个单元 → N+1 条事件，末尾是等待窗口事件。"""
        result = Executor(self.pack, self.tts, policy_stream=True).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=self.out,
        )
        self.assertEqual(len(result.events), 3)
        self.assertIn(spec.LISTEN_MS, result.events[-1])
        self.assertEqual(result.events[-1][spec.LISTEN_MS], 900)

    def test_policy_stream_is_bool_only(self):
        """policy_stream 必须是 bool（禁止 "yes" 之类绕过）。"""
        with self.assertRaises(TypeError) as ctx:
            Executor(self.pack, self.tts, policy_stream="yes")
        self.assertIn("policy_stream", str(ctx.exception))

    def test_audio_identical_across_stream_shapes(self):
        """两种事件流形态下的音频字节完全相同（事件流不影响产物）。"""
        a = Path(self.tmp.name) / "legacy.wav"
        b = Path(self.tmp.name) / "policy.wav"
        Executor(self.pack, self.tts).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=a)
        Executor(self.pack, self.tts, policy_stream=True).execute(
            self._plan(), plan_id="plan-1", turn_id="turn-1", out_path=b)
        self.assertEqual(_sha256(a), _sha256(b),
                         "事件流形态不得影响音频字节")


if __name__ == "__main__":
    unittest.main()
